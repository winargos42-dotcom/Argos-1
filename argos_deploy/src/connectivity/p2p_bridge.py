"""
p2p_bridge.py — P2P Сеть Аргоса
  Ноды находят друг друга в локальной сети и интернете.
  Объединяют вычислительную мощь, обмениваются навыками.
  Задачи распределяются по мощности и возрасту ноды.
"""

import os
import json
import socket
import threading
import time
import uuid
import hashlib
import platform
import psutil
import datetime
import requests
import ipaddress
import http.client
import re
import logging
from .p2p_auth import Auth, load_key, send_frame, recv_frame, canonical
from typing import Optional

from src.connectivity.redis_bus import RedisBus

# ── КОНСТАНТЫ ─────────────────────────────────────────────
P2P_PORT = int(os.getenv("ARGOS_P2P_PORT", "55771"))  # Порт для P2P связи
BROADCAST_PORT = int(os.getenv("ARGOS_P2P_BROADCAST_PORT", "55772"))  # Порт для UDP-обнаружения
HEARTBEAT_SEC = 15  # Пульс каждые N секунд
NODE_TIMEOUT = 45  # Нода считается мёртвой через N секунд
VERSION = "1.0.0"
NETWORK_SECRET = None  # No legacy/default credential; loaded privately on explicit start/client call.
UDP_SOCKET_TIMEOUT = 0.1  # Таймаут recvfrom — держит цикл отзывчивым

log = logging.getLogger("argos.p2p")


def _env(*names: str) -> str:
    """Первое непустое значение из списка переменных окружения (обрезанное)."""
    for name in names:
        value = (os.getenv(name, "") or "").strip()
        if value:
            return value
    return ""


def p2p_bind_host() -> str:
    """Адрес bind TCP- и UDP-сокетов P2P.

    ARGOS_P2P_BIND_HOST (основное имя) или ARGOS_P2P_BIND (совместимый алиас).
    По умолчанию — 127.0.0.1: слушать все интерфейсы без явной настройки
    небезопасно; адрес должен лежать внутри ARGOS_P2P_ALLOWED_SUBNET.
    """
    return _env("ARGOS_P2P_BIND_HOST", "ARGOS_P2P_BIND") or "127.0.0.1"


def parse_p2p_peers(raw: Optional[str] = None) -> list:
    """Разбирает ARGOS_P2P_PEERS: "host:port,host:port" -> [(host, port), ...].

    Порт по умолчанию — BROADCAST_PORT. Некорректные элементы пропускаются.
    Отправка идёт только адресатам внутри ARGOS_P2P_ALLOWED_SUBNET (см. ArgosBridge).
    """
    if raw is None:
        raw = os.getenv("ARGOS_P2P_PEERS", "")
    peers = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        host, sep, port = item.rpartition(":")
        if not sep:
            host, port = item, str(BROADCAST_PORT)
        host = host.strip().strip("[]")
        try:
            port_num = int(port)
        except ValueError:
            continue
        if host and 0 < port_num < 65536:
            peers.append((host, port_num))
    return peers


def _is_loopback(host: str) -> bool:
    return host.startswith("127.") or host in ("localhost", "::1")


def p2p_protocol_roadmap() -> str:
    """Статус протокола и дорожная карта миграции на libp2p + ZKP."""
    return (
        "🛰️ P2P ПРОТОКОЛ ARGOS\n"
        "Текущий транспорт: UDP discovery + TCP JSON (custom).\n"
        "\n"
        "🎯 Рекомендуемый target: libp2p (совместимость с dHT, pubsub, secure transports).\n"
        "Этапы миграции:\n"
        "1) Discovery: mDNS/Kademlia вместо широковещательного UDP.\n"
        "2) Transport Security: Noise/TLS + peer identity keys.\n"
        "3) Messaging: gossipsub для событий, request-response для RPC.\n"
        "4) Data exchange: protobuf-сообщения и версионирование протокола.\n"
        "\n"
        "🔐 ZKP roadmap (перспектива):\n"
        "- Phase A: selective disclosure (минимизация персональных полей).\n"
        "- Phase B: proof-of-attribute (подтверждение факта без раскрытия значения).\n"
        "- Phase C: proof-of-policy (валидность данных/правил между нодами).\n"
        "\n"
        "Примечание: в текущей версии ZKP не активирован, это roadmap для следующей итерации."
    )


# ═══════════════════════════════════════════════════════════
# ПРОФИЛЬ НОДЫ — мощность, возраст, навыки
# ═══════════════════════════════════════════════════════════
class NodeProfile:
    def __init__(self):
        self.node_id = self._load_or_create_id()
        self.birth = self._load_or_create_birth()
        self.version = VERSION
        self.os_type = platform.system()
        self.hostname = socket.gethostname()
        self.role = self._resolve_role()

    def _load_or_create_id(self) -> str:
        path = "config/node_id"
        if os.path.exists(path):
            return open(path).read().strip()
        nid = str(uuid.uuid4())
        os.makedirs("config", exist_ok=True)
        open(path, "w").write(nid)
        return nid

    def _load_or_create_birth(self) -> str:
        path = "config/node_birth"
        if os.path.exists(path):
            return open(path).read().strip()
        birth = datetime.datetime.now().isoformat()
        os.makedirs("config", exist_ok=True)
        open(path, "w").write(birth)
        return birth

    def get_power(self) -> dict:
        """Вычислительная мощность ноды (0–100)."""
        cpu_free = 100 - 0.0
        ram = psutil.virtual_memory()
        ram_free = (ram.available / ram.total) * 100
        cpu_cores = psutil.cpu_count(logical=False) or 1

        # Итоговый индекс мощности
        power_index = int((cpu_free * 0.5) + (ram_free * 0.3) + min(cpu_cores * 5, 20))
        return {
            "index": power_index,
            "cpu_free": round(cpu_free, 1),
            "ram_free": round(ram_free, 1),
            "cpu_cores": cpu_cores,
            "ram_gb": round(ram.total / (1024**3), 1),
        }

    def get_age_days(self) -> float:
        """Возраст ноды в днях."""
        try:
            birth = datetime.datetime.fromisoformat(self.birth)
            return (datetime.datetime.now() - birth).total_seconds() / 86400
        except Exception:
            return 0.0

    def get_authority(self) -> int:
        """Авторитет ноды = мощность × log(возраст+1). Старые и мощные — главные."""
        import math

        age = self.get_age_days()
        power = self.get_power()["index"]
        return int(power * math.log(age + 2))

    def _resolve_role(self) -> str:
        env_role = (os.getenv("ARGOS_NODE_ROLE", "") or "").strip().lower()
        if env_role in {"gateway", "worker", "server"}:
            return env_role

        power = self.get_power()
        if power.get("cpu_cores", 1) <= 2 or power.get("ram_gb", 1.0) < 2.5:
            return "gateway"
        if power.get("cpu_cores", 1) >= 8 and power.get("ram_gb", 0.0) >= 16:
            return "server"
        return "worker"

    def get_skills(self) -> list:
        try:
            return [
                f[:-3]
                for f in os.listdir("src/skills")
                if f.endswith(".py") and not f.startswith("__")
            ]
        except Exception:
            return []

    def to_dict(self) -> dict:
        power = self.get_power()
        return {
            "node_id": self.node_id,
            "birth": self.birth,
            "age_days": round(self.get_age_days(), 2),
            "authority": self.get_authority(),
            "version": self.version,
            "os": self.os_type,
            "hostname": self.hostname,
            "role": self.role,
            "power": power,
            "skills": self.get_skills(),
        }


# ═══════════════════════════════════════════════════════════
# ИЗВЕСТНЫЕ НОДЫ — реестр живых участников сети
# ═══════════════════════════════════════════════════════════
class NodeRegistry:
    def __init__(self):
        self._nodes: dict[str, dict] = {}  # node_id → profile + last_seen
        self._lock = threading.Lock()

    def update(self, profile: dict, addr: str):
        nid = profile.get("node_id")
        if not nid:
            return
        with self._lock:
            self._nodes[nid] = {
                **profile,
                "addr": addr,
                "last_seen": time.time(),
            }

    def remove_dead(self):
        now = time.time()
        with self._lock:
            dead = [nid for nid, n in self._nodes.items() if now - n["last_seen"] > NODE_TIMEOUT]
            for nid in dead:
                del self._nodes[nid]

    def all(self) -> list:
        with self._lock:
            return list(self._nodes.values())

    def count(self) -> int:
        return len(self._nodes)

    def get_master(self) -> Optional[dict]:
        """Нода с наибольшим авторитетом — главная."""
        nodes = self.all()
        if not nodes:
            return None
        return max(nodes, key=lambda n: n.get("authority", 0))

    def total_power(self) -> int:
        """Суммарная мощность всей сети."""
        return sum(n.get("power", {}).get("index", 0) for n in self.all())

    def report(self, self_profile: dict) -> str:
        nodes = self.all()
        master = self.get_master()
        total = self.total_power() + self_profile.get("power", {}).get("index", 0)

        lines = [
            f"🌐 ARGOS NETWORK — {len(nodes) + 1} нод(а) онлайн",
            f"   Суммарная мощность: {total}/100",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"👁️ ЭТА НОДА:",
            f"   ID:        {self_profile['node_id'][:8]}...",
            f"   Возраст:   {self_profile['age_days']:.1f} дней",
            f"   Мощность:  {self_profile['power']['index']}/100",
            f"   Авторитет: {self_profile['authority']}",
            f"   Навыки:    {len(self_profile['skills'])}",
        ]

        if master:
            is_master = master["node_id"] == self_profile["node_id"]
            lines.append(f"\n👑 МАСТЕР: {'ЭТА НОДА ✅' if is_master else master['hostname']}")

        if nodes:
            lines.append(f"\n📡 СОСЕДНИЕ НОДЫ:")
            for n in sorted(nodes, key=lambda x: -x.get("authority", 0)):
                age = n.get("age_days", 0)
                pw = n.get("power", {}).get("index", 0)
                auth = n.get("authority", 0)
                host = n.get("hostname", "unknown")
                addr = n.get("addr", "?")
                sk = len(n.get("skills", []))
                lines.append(
                    f"   🔹 {host} ({addr})\n"
                    f"      Возраст: {age:.1f}д | Мощность: {pw}/100 | Авторитет: {auth} | Навыки: {sk}"
                )

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# РАСПРЕДЕЛИТЕЛЬ ЗАДАЧ
# ═══════════════════════════════════════════════════════════
class TaskDistributor:
    """Выбирает лучшую ноду для выполнения задачи."""

    HEAVY_KEYWORDS = (
        "vision",
        "камер",
        "изображ",
        "скрин",
        "compile",
        "компиля",
        "build",
        "прошив",
        "firmware",
        "video",
        "render",
        "train",
    )

    def __init__(self, registry: NodeRegistry, self_profile: NodeProfile):
        self.registry = registry
        self.me = self_profile
        self.request_peer = None
        self.query_local = None

    def _infer_task_type(self, prompt: str) -> str:
        low = (prompt or "").lower()
        if any(k in low for k in self.HEAVY_KEYWORDS):
            return "heavy"
        return "ai"

    def _score_node(self, node: dict, task_type: str) -> float:
        power = float(node.get("power", {}).get("index", 0.0))
        auth = float(node.get("authority", 0.0))
        ram_gb = float(node.get("power", {}).get("ram_gb", 0.0))
        role = str(node.get("role", "worker"))

        if task_type == "heavy":
            role_bonus = 22.0 if role == "server" else (8.0 if role == "worker" else -25.0)
            return (auth * 0.55) + (power * 0.45) + role_bonus + min(ram_gb, 64.0) * 0.4

        if task_type == "old":
            return float(node.get("age_days", 0.0)) * 10.0 + auth

        return (auth * 0.5) + (power * 0.5)

    def pick_node_for(self, task_type: str = "ai") -> dict:
        """
        task_type:
          'ai'    — нужна максимальная мощность CPU/RAM
          'store' — нужно место на диске
          'old'   — нужен авторитет (старая нода)
        """
        nodes = self.registry.all()
        me = self.me.to_dict()
        all_ = [me] + nodes

        if task_type == "heavy":
            candidates = [
                n
                for n in all_
                if n.get("role", "worker") != "gateway"
                and n.get("power", {}).get("index", 0) >= 45
                and n.get("power", {}).get("ram_gb", 0) >= 4
            ]
            if not candidates:
                candidates = all_
            best = max(candidates, key=lambda n: self._score_node(n, "heavy"))
        elif task_type == "ai":
            best = max(all_, key=lambda n: self._score_node(n, "ai"))
        elif task_type == "old":
            best = max(all_, key=lambda n: n.get("age_days", 0))
        else:
            best = max(all_, key=lambda n: self._score_node(n, task_type))

        is_me = best["node_id"] == me["node_id"]
        return {"node": best, "is_local": is_me, "task_type": task_type}

    def route_task(self, prompt: str, core=None, task_type: str = None) -> str:
        """Направляет AI-запрос на лучшую ноду. Если локальная — выполняет сам."""
        resolved_type = task_type or self._infer_task_type(prompt)
        decision = self.pick_node_for(resolved_type)
        node = decision["node"]

        if decision["is_local"]:
            if self.query_local is None:
                return "[LOCAL] Bounded inference adapter unavailable"
            result = self.query_local(prompt)
            return f"[LOCAL:{resolved_type}] " + str(result.get('answer', result.get('error', 'No answer')))

        if self.request_peer is None:
            return "[ROUTE FAIL] Authenticated transport unavailable"
        try:
            data = self.request_peer(node.get("addr", ""), {"action": "query", "prompt": prompt},
                                     port=node.get("port", P2P_PORT))
            if "answer" not in data:
                return "[ROUTE FAIL] " + str(data.get("error", "No answer"))
            return f"[{node.get('hostname', 'peer')}:{resolved_type}] {data['answer']}"
        except Exception:
            return "[ROUTE FAIL] Authenticated peer unavailable"


# ═══════════════════════════════════════════════════════════
# P2P МОСТ — сервер + клиент + пульс
# ═══════════════════════════════════════════════════════════
class ArgosBridge:
    """LAN-only authenticated status/discovery and bounded local-Ollama inference.

    Shared-key membership is not per-node identity or encryption. No skill transfer,
    arbitrary commands, remote Python imports, Redis auto-start, or WAN discovery.
    """
    def __init__(self, core=None):
        self.core = core
        self.profile = NodeProfile()
        self.registry = NodeRegistry()
        self.distributor = TaskDistributor(self.registry, self.profile)
        self.distributor.request_peer = self._request_peer
        self.distributor.query_local = self._query_local
        self._running = False
        self._auth = None
        self._lifecycle = threading.RLock()
        self._stop = threading.Event()
        self._threads = []
        self._clients = set()
        self._client_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(4)
        self._outbound_slots = threading.BoundedSemaphore(4)
        self._tcp = self._udp = None
        # ARGOS_P2P_BIND / ARGOS_P2P_PEERS (коммит 78ec305) — совместимые алиасы
        # поверх усиленных имён ARGOS_P2P_BIND_HOST / ARGOS_P2P_DISCOVERY_*.
        self.bind_host = p2p_bind_host()
        self.port = int(os.getenv('ARGOS_P2P_PORT', str(P2P_PORT)))
        self.udp_host = _env('ARGOS_P2P_DISCOVERY_BIND') or self.bind_host
        self.udp_port = int(os.getenv('ARGOS_P2P_BROADCAST_PORT', str(BROADCAST_PORT)))
        self.allowed_network = ipaddress.ip_network(os.getenv('ARGOS_P2P_ALLOWED_SUBNET', '127.0.0.0/24'))
        self.discovery_target = _env('ARGOS_P2P_DISCOVERY_TARGET') or self.bind_host
        # Дополнительные unicast-адресаты подписанного discovery (например, нода
        # в QEMU за slirp, куда broadcast не доходит). Адреса вне разрешённой
        # подсети молча пропускаются при отправке.
        self.unicast_peers = parse_p2p_peers()
        self._local_ip = self.bind_host
        self.redis_bus = None  # Legacy unsigned Redis registry path deliberately unavailable.

    @property
    def tcp_host(self):
        """Совместимость с 78ec305: TCP-сервер слушает bind_host."""
        return self.bind_host

    def _get_local_ip(self):
        return self.bind_host

    def _discovery_targets(self):
        targets = [(self.discovery_target, self.udp_port)]
        for peer in self.unicast_peers:
            if self._allowed(peer[0]) and peer not in targets:
                targets.append(peer)
        return targets

    def _ensure_auth(self):
        if self._auth is None:
            self._auth = Auth(load_key())
        return self._auth

    def _allowed(self, address):
        try:
            ip = ipaddress.ip_address(address)
            return ip.version == 4 and ip in self.allowed_network
        except ValueError:
            return False

    def _config_guard(self):
        if not self.allowed_network.is_private or self.allowed_network.prefixlen < 16:
            raise ValueError('P2P requires a narrow private IPv4 subnet')
        if not self._allowed(self.bind_host) or not self._allowed(self.discovery_target):
            raise ValueError('P2P endpoint outside allowed LAN')
        if self.udp_host not in ('0.0.0.0', self.bind_host):
            raise ValueError('Unexpected discovery bind')
        if not (1 <= self.port <= 65535 and 1 <= self.udp_port <= 65535):
            raise ValueError('Invalid P2P port')

    def start(self):
        with self._lifecycle:
            if self._running:
                return self.network_status()
            # Validate current provisioning even when a client initialized auth earlier.
            try:
                self._auth = Auth(load_key())
            except (OSError, ValueError) as exc:
                self._auth = None
                log.warning("P2P disabled: ARGOS_NETWORK_SECRET(_FILE) is unset or weak (%s); "
                            "networked P2P actions stay off", exc)
                raise
            self._config_guard()
            opened = []
            try:
                tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                opened.append(tcp)
                tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                tcp.bind((self.bind_host, self.port))
                tcp.listen(4)
                tcp.settimeout(.2)
                udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                opened.append(udp)
                udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                udp.bind((self.udp_host, self.udp_port))
                udp.settimeout(.2)
                self._tcp, self._udp = tcp, udp
                self._stop.clear()
                self._running = True
                self._threads = [threading.Thread(target=target, daemon=True, name=name)
                                 for target, name in ((self._tcp_server, 'ArgosP2P-TCP'),
                                                      (self._udp_discovery, 'ArgosP2P-UDP'))]
                for thread in self._threads:
                    thread.start()
            except BaseException:
                self._running = False
                self._stop.set()
                for sock in opened:
                    sock.close()
                for thread in self._threads:
                    if thread.ident is not None:
                        thread.join(1)
                self._tcp = self._udp = None
                raise
            return self.network_status()

    def stop(self):
        with self._lifecycle:
            self._running = False
            self._stop.set()
            for sock in (self._tcp, self._udp):
                if sock:
                    sock.close()
            with self._client_lock:
                clients = list(self._clients)
            for sock in clients:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
            end = time.monotonic()+12
            # Stop accept/discovery first, then snapshot workers: no late worker escapes join.
            for thread in list(self._threads):
                if thread.name in ('ArgosP2P-TCP', 'ArgosP2P-UDP') and thread.ident is not None:
                    thread.join(max(0, end-time.monotonic()))
            for thread in list(self._threads):
                if thread is not threading.current_thread() and thread.ident is not None:
                    thread.join(max(0, end-time.monotonic()))
            if any(t.is_alive() for t in self._threads):
                raise RuntimeError('P2P shutdown incomplete')
            self._threads = []
            self._tcp = self._udp = None

    def _accept_profile(self, data, address):
        if not isinstance(data, dict) or not self._allowed(address):
            raise ValueError('Invalid peer')
        node_id = data.get('node_id')
        if not isinstance(node_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', node_id):
            raise ValueError('Invalid node ID')
        if node_id == self.profile.node_id:
            return
        if self.registry.count() >= 128 and node_id not in {n['node_id'] for n in self.registry.all()}:
            raise ValueError('Peer registry full')
        power = data.get('power', {})
        if not isinstance(power, dict):
            raise ValueError('Invalid power metadata')
        clean = {'node_id': node_id, 'hostname': str(data.get('hostname', 'peer'))[:64],
                 'role': data.get('role') if data.get('role') in ('worker','server','gateway') else 'worker',
                 'skills': [], 'port': data.get('port', self.port)}
        if type(clean['port']) is not int or not 1 <= clean['port'] <= 65535:
            raise ValueError('Invalid peer port')
        for key in ('authority', 'age_days'):
            value = data.get(key, 0)
            if type(value) not in (int, float) or not 0 <= value <= 1000000:
                raise ValueError('Invalid peer metric')
            clean[key] = value
        clean['power'] = {}
        for key in ('index', 'ram_gb'):
            value = power.get(key, 0)
            if type(value) not in (int, float) or not 0 <= value <= 1000000:
                raise ValueError('Invalid peer metric')
            clean['power'][key] = value
        self.registry.update(clean, address)

    def _own_profile(self):
        return {**self.profile.to_dict(), 'skills': [], 'port': self.port}

    def _receive_discovery(self, raw, address):
        if len(raw) > 8192 or not self._allowed(address):
            raise ValueError('Invalid discovery source/size')
        profile = self._ensure_auth().verify(json.loads(raw), 'discovery')
        self._accept_profile(profile, address)

    def _udp_discovery(self):
        sock, next_send = self._udp, 0
        while not self._stop.is_set():
            if time.monotonic() >= next_send:
                try:
                    packet = canonical(self._auth.pack('discovery', self._own_profile()))
                    if len(packet) <= 8192:
                        for target in self._discovery_targets():
                            try:
                                sock.sendto(packet, target)
                            except OSError:
                                pass
                except (OSError, ValueError):
                    pass
                next_send = time.monotonic()+HEARTBEAT_SEC
                self.registry.remove_dead()
            try:
                raw, address = sock.recvfrom(8193)
                self._receive_discovery(raw, address[0])
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                pass

    def _tcp_server(self):
        sock = self._tcp
        while not self._stop.is_set():
            try:
                conn, address = sock.accept()
            except OSError:
                continue
            if self._stop.is_set() or not self._allowed(address[0]) or not self._slots.acquire(blocking=False):
                conn.close()
                continue
            with self._client_lock:
                self._clients.add(conn)
            def worker(connection=conn, host=address[0]):
                try:
                    self._handle_client(connection, host)
                finally:
                    with self._client_lock:
                        self._clients.discard(connection)
                    self._slots.release()
            thread = threading.Thread(target=worker, daemon=True, name='ArgosP2P-client')
            self._threads = [t for t in self._threads if t.name != 'ArgosP2P-client' or t.is_alive()]
            self._threads.append(thread)
            try:
                thread.start()
            except Exception:
                conn.close()
                with self._client_lock:
                    self._clients.discard(conn)
                self._slots.release()
                self._threads.remove(thread)

    def _query_local(self, prompt):
        """No tools/commands: one bounded text-only local Ollama request."""
        model = os.getenv('ARGOS_P2P_OLLAMA_MODEL', os.getenv('OLLAMA_MODEL', ''))
        if not isinstance(prompt, str) or not 1 <= len(prompt) <= 4096:
            return {'error': 'invalid_prompt'}
        if not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', model):
            return {'error': 'local_model_unconfigured'}
        connection = http.client.HTTPConnection('127.0.0.1', 11434, timeout=1)
        socket_holder = []
        def abort():
            for sock in socket_holder:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
        timer = None
        try:
            connection.connect()
            socket_holder.append(connection.sock)
            with self._client_lock:
                self._clients.add(connection.sock)
            if self._stop.is_set():
                return {'error': 'stopping'}
            timer = threading.Timer(8, abort)
            timer.daemon = True
            timer.start()
            connection.sock.settimeout(8)
            body = canonical({'model': model, 'prompt': prompt, 'stream': False,
                              'options': {'num_predict': 128}})
            connection.request('POST', '/api/generate', body=body, headers={'Content-Type':'application/json'})
            response = connection.getresponse()
            raw = response.read(32769)
            if response.status != 200 or len(raw) > 32768:
                return {'error': 'local_inference_failed'}
            data = json.loads(raw)
            answer = data.get('response')
            if not isinstance(answer, str) or not answer.strip() or not data.get('done') or len(answer) > 8192:
                return {'error': 'local_inference_incomplete'}
            return {'answer': answer, 'node_id': self.profile.node_id, 'backend': 'local_ollama'}
        except Exception:
            return {'error': 'local_inference_failed'}
        finally:
            if timer:
                timer.cancel()
                timer.join(1)
            with self._client_lock:
                for sock in socket_holder:
                    self._clients.discard(sock)
            connection.close()

    def _handle_client(self, conn, addr):
        try:
            if not self._allowed(addr):
                return
            packet = recv_frame(conn)
            message = self._ensure_auth().verify(packet, 'request')
            action = message.get('action')
            if action == 'status':
                result = self._own_profile()
            elif action == 'query':
                result = self._query_local(message.get('prompt'))
            else:
                result = {'error': 'unsupported_action'}
            conn.settimeout(2)
            send_frame(conn, self._auth.pack('response', result, reply_to=packet['nonce']))
        except Exception:
            pass  # Fail closed; never reflect raw exceptions or unauthenticated input.
        finally:
            conn.close()

    def _request_peer(self, address, message, port=None):
        if not self._allowed(address):
            raise ValueError('Peer outside allowed LAN')
        auth = self._ensure_auth()
        packet = auth.pack('request', message)
        if not self._outbound_slots.acquire(blocking=False):
            raise ValueError('Outbound P2P connection limit')
        sock = None
        try:
            sock = socket.create_connection((address, port or self.port), timeout=2)
            with self._client_lock:
                self._clients.add(sock)
            if self._stop.is_set():
                raise ValueError('P2P is stopping')
            sock.settimeout(2)
            send_frame(sock, packet)
            response = recv_frame(sock, seconds=12)
        finally:
            if sock is not None:
                with self._client_lock:
                    self._clients.discard(sock)
                sock.close()
            self._outbound_slots.release()
        return auth.verify(response, 'response', reply_to=packet['nonce'])

    def network_status(self):
        self.registry.remove_dead()
        count = len([n for n in self.registry.all() if n['node_id'] != self.profile.node_id])
        return (f'P2P transport={"running" if self._running else "stopped"}; '
                f'bind={self.bind_host}:{self.port}; external_peer_count={count}; '
                'peer_basis=authenticated_recent; persistent_sessions=0; '
                'authentication=HMAC-SHA256; encryption=none; skill_transfer=unsupported')

    def route_query(self, prompt, task_type=None):
        self.registry.remove_dead()
        return self.distributor.route_task(prompt, self.core, task_type=task_type)

    def sync_skills_from_network(self):
        return 'unsupported: automatic peer skill transfer and execution are disabled'

    def connect_to(self, ip):
        try:
            data = self._request_peer(ip, {'action': 'status'})
            self._accept_profile(data, ip)
            if data.get('node_id') == self.profile.node_id:
                return 'P2P self connection ignored; ' + self.network_status()
            return '✅ Authenticated peer registered; ' + self.network_status()
        except Exception:
            return '❌ Authenticated peer connection failed'
