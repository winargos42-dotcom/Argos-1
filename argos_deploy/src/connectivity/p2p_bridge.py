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
from typing import Optional

from src.connectivity.redis_bus import RedisBus

# ── КОНСТАНТЫ ─────────────────────────────────────────────
P2P_PORT = int(os.getenv("ARGOS_P2P_PORT", "55771"))  # Порт для P2P связи
BROADCAST_PORT = int(os.getenv("ARGOS_P2P_BROADCAST_PORT", "55772"))  # Порт для UDP-обнаружения
HEARTBEAT_SEC = 15  # Пульс каждые N секунд
NODE_TIMEOUT = 45  # Нода считается мёртвой через N секунд
VERSION = "1.0.0"
NETWORK_SECRET = os.getenv("ARGOS_NETWORK_SECRET", "argos_default_secret")
UDP_SOCKET_TIMEOUT = 0.1  # Таймаут recvfrom — держит цикл отзывчивым


def p2p_bind_host() -> str:
    """Адрес для bind UDP- и TCP-сокетов P2P (ARGOS_P2P_BIND).

    Пусто (по умолчанию) — все интерфейсы, как раньше. "127.0.0.1" — только
    локально (например, связь с KolibriOS в QEMU через slirp/hostfwd).
    """
    return (os.getenv("ARGOS_P2P_BIND", "") or "").strip()


def parse_p2p_peers(raw: Optional[str] = None) -> list:
    """Разбирает ARGOS_P2P_PEERS: "host:port,host:port" -> [(host, port), ...].

    Порт по умолчанию — BROADCAST_PORT. Некорректные элементы пропускаются.
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
            if core:
                res = (
                    core._ask_gemini("Ты Аргос.", prompt)
                    or core._ask_ollama("Ты Аргос.", prompt)
                    or "Нет ответа от ИИ."
                )
                return f"[LOCAL:{resolved_type}] {res}"
            return "[LOCAL] Ядро не подключено."

        # Запрос к удалённой ноде через TCP JSON протокол
        addr = node.get("addr", "")
        try:
            sock = socket.socket()
            sock.settimeout(20)
            sock.connect((addr, P2P_PORT))
            sock.sendall(
                json.dumps(
                    {
                        "action": "query",
                        "prompt": prompt,
                        "task_type": resolved_type,
                        "secret": NETWORK_SECRET,
                    }
                ).encode()
            )
            raw = sock.recv(65536)
            sock.close()
            data = json.loads(raw.decode() or "{}")
            answer = data.get("answer", "Нет ответа")
            return f"[{node['hostname']}:{resolved_type}] {answer}"
        except Exception as e:
            return f"[ROUTE FAIL] {e}"


# ═══════════════════════════════════════════════════════════
# P2P МОСТ — сервер + клиент + пульс
# ═══════════════════════════════════════════════════════════
class ArgosBridge:
    def __init__(self, core=None):
        self.core = core
        self.profile = NodeProfile()
        self.registry = NodeRegistry()
        self.distributor = TaskDistributor(self.registry, self.profile)
        self._running = False
        self._local_ip = self._get_local_ip()
        # Unified UDP discovery socket params (SO_REUSEADDR + SO_BROADCAST + bind + timeout)
        self.udp_host = p2p_bind_host()  # "" = все интерфейсы (ARGOS_P2P_BIND)
        self.tcp_host = self.udp_host  # TCP-сервер слушает тот же адрес
        self.udp_port = BROADCAST_PORT  # UDP discovery port
        # Дополнительные unicast-адресаты ARGOS_HELLO (ARGOS_P2P_PEERS),
        # например нода в QEMU, куда broadcast не доходит.
        self.unicast_peers = parse_p2p_peers()
        # ГОСТ P2P безопасность
        try:
            from src.connectivity.gost_p2p import GostP2PSecurity

            self._gost = GostP2PSecurity(secret=NETWORK_SECRET)
        except Exception:
            self._gost = None

        # Optional Redis pub/sub transport
        self.redis_url = os.getenv("REDIS_URL", "").strip()
        self.redis_host = os.getenv("REDIS_HOST", "").strip()
        self.redis_port = int(os.getenv("REDIS_PORT", "6379") or "6379")
        self.redis_password = os.getenv("REDIS_PASSWORD", "") or None
        self.redis_prefix = os.getenv("REDIS_CHANNEL_PREFIX", "argos")
        self.redis_bus = None
        if self.redis_url or self.redis_host:
            try:
                self.redis_bus = RedisBus(
                    redis_url=self.redis_url or None,
                    host=self.redis_host,
                    port=self.redis_port,
                    password=self.redis_password,
                    prefix=self.redis_prefix,
                )
                self.redis_bus.register("state", self._on_redis_state)
            except Exception:
                self.redis_bus = None

    def _get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _sign(self, data: dict) -> str:
        """ГОСТ HMAC-Стрибог-256 подпись (замена SHA-256)."""
        if self._gost:
            return self._gost.sign(data)
        # Fallback: SHA-256 если ГОСТ недоступен
        raw = json.dumps(data, sort_keys=True) + NETWORK_SECRET
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _verify_sign(self, data: dict, signature: str) -> bool:
        """Проверяет ГОСТ HMAC-Стрибог-256 подпись."""
        if self._gost:
            return self._gost.verify(data, signature)
        # Fallback: воссоздаём SHA-256
        raw = json.dumps(data, sort_keys=True) + NETWORK_SECRET
        return hashlib.sha256(raw.encode()).hexdigest()[:16] == signature

    def start(self) -> str:
        self._running = True
        threading.Thread(target=self._udp_discovery, daemon=True).start()
        threading.Thread(target=self._tcp_server, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        if self.redis_bus:
            try:
                self.redis_bus.start()
                threading.Thread(target=self._redis_heartbeat_loop, daemon=True).start()
            except Exception:
                self.redis_bus = None
        return (
            f"🌐 P2P-мост запущен\n"
            f"   IP:       {self._local_ip}:{P2P_PORT}\n"
            f"   UDP:      {self._local_ip}:{self.udp_port}\n"
            f"   Нода ID:  {self.profile.node_id[:8]}...\n"
            f"   Возраст:  {self.profile.get_age_days():.2f} дней\n"
            f"   Мощность: {self.profile.get_power()['index']}/100\n"
            f"   Авторитет:{self.profile.get_authority()}"
        )

    def stop(self):
        self._running = False
        if self.redis_bus:
            try:
                self.redis_bus.stop()
            except Exception:
                pass

    # ── UDP ОБНАРУЖЕНИЕ (broadcast send + receive) ────────
    def _udp_discovery(self):
        """Combined UDP discovery: broadcasts presence and listens for peers.

        Uses a single socket with SO_REUSEADDR + SO_BROADCAST + bind + short
        timeout so the same socket can both send and receive on BROADCAST_PORT.
        This is the canonical "out-of-the-box" pattern that works across Linux,
        macOS and Windows without extra privileges.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # SO_REUSEPORT lets multiple app instances share the same UDP port
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        try:
            sock.bind((self.udp_host, self.udp_port))
        except Exception as e:
            print(f"[P2P UDP]: Не удалось открыть порт {self.udp_port}: {e}")
            sock.close()
            return
        sock.settimeout(UDP_SOCKET_TIMEOUT)

        last_broadcast = 0
        while self._running:
            now = time.time()
            # ── Broadcast heartbeat ──────────────────────────────
            if now - last_broadcast >= HEARTBEAT_SEC:
                last_broadcast = now
                try:
                    profile_data = self.profile.to_dict()
                    payload = json.dumps(
                        {
                            "type": "ARGOS_HELLO",
                            "profile": profile_data,
                            "sign": self._sign(profile_data),
                        }
                    ).encode()
                except Exception:
                    payload = None
                if payload is not None:
                    # broadcast с loopback-сокета невозможен — только unicast
                    if not _is_loopback(self.udp_host):
                        try:
                            sock.sendto(payload, ("<broadcast>", self.udp_port))
                        except Exception:
                            pass
                    for peer in self.unicast_peers:
                        try:
                            sock.sendto(payload, peer)
                        except Exception:
                            pass
            # ── Receive incoming discovery packets ───────────────
            try:
                data, addr = sock.recvfrom(4096)
                msg = json.loads(data.decode())
                if msg.get("type") != "ARGOS_HELLO":
                    continue
                profile = msg.get("profile", {})
                if profile.get("node_id") == self.profile.node_id:
                    continue  # Игнорируем себя
                self.registry.update(profile, addr[0])
            except socket.timeout:
                pass  # Dead-peer cleanup is handled by _heartbeat_loop
            except Exception:
                pass
        sock.close()

    # ── TCP СЕРВЕР — принимает запросы от других нод ──────
    def _tcp_server(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((self.tcp_host, P2P_PORT))
            srv.listen(10)
        except Exception as e:
            print(f"[P2P TCP]: Не удалось открыть порт {P2P_PORT}: {e}")
            return
        srv.settimeout(2)
        while self._running:
            try:
                conn, addr = srv.accept()
                threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr[0]),
                    daemon=True,
                ).start()
            except socket.timeout:
                pass
            except Exception:
                pass
        srv.close()

    def _handle_client(self, conn: socket.socket, addr: str):
        try:
            raw = conn.recv(65536)
            msg = json.loads(raw.decode())

            # Проверка секрета — поддерживает и ГОСТ-подпись, и plain secret
            incoming_secret = msg.get("secret", "")
            gost_sig = msg.get("gost_sign", "")
            if gost_sig and self._gost:
                # Новый путь: ГОСТ HMAC-Стрибог проверка
                check_data = {k: v for k, v in msg.items() if k not in ("gost_sign",)}
                if not self._gost.verify(check_data, gost_sig):
                    conn.sendall(json.dumps({"error": "Unauthorized (GOST HMAC)"}).encode())
                    return
            elif incoming_secret != NETWORK_SECRET:
                conn.sendall(json.dumps({"error": "Unauthorized"}).encode())
                return

            action = msg.get("action", "query")

            if action == "query":
                prompt = msg.get("prompt", "")
                if self.core:
                    answer = (
                        self.core._ask_gemini("Ты Аргос.", prompt)
                        or self.core._ask_ollama("Ты Аргос.", prompt)
                        or "Нет ответа от ИИ."
                    )
                else:
                    answer = "Ядро не подключено на этой ноде."
                conn.sendall(
                    json.dumps(
                        {
                            "answer": answer,
                            "node_id": self.profile.node_id,
                            "host": self.profile.hostname,
                        }
                    ).encode()
                )

            elif action == "sync_skills":
                # Запрашивающая нода хочет получить список наших навыков
                skills = self.profile.get_skills()
                conn.sendall(json.dumps({"skills": skills}).encode())

            elif action == "get_skill":
                # Передаём файл навыка
                skill_name = msg.get("skill", "")
                path = f"src/skills/{skill_name}.py"
                if os.path.exists(path) and not skill_name.startswith(".."):
                    code = open(path, encoding="utf-8").read()
                    conn.sendall(json.dumps({"code": code, "name": skill_name}).encode())
                else:
                    conn.sendall(json.dumps({"error": "Skill not found"}).encode())

            elif action == "status":
                conn.sendall(json.dumps(self.profile.to_dict()).encode())

        except Exception as e:
            try:
                conn.sendall(json.dumps({"error": str(e)}).encode())
            except Exception:
                pass
        finally:
            conn.close()

    # ── ПУЛЬС — периодические задачи ─────────────────────
    def _heartbeat_loop(self):
        while self._running:
            time.sleep(HEARTBEAT_SEC)
            self.registry.remove_dead()

    # ── REDIS HEARTBEAT / STATE SYNC ─────────────────────
    def _redis_heartbeat_loop(self):
        while self._running and self.redis_bus:
            try:
                payload = {
                    "profile": self.profile.to_dict(),
                    "ts": time.time(),
                }
                self.redis_bus.publish("state", payload)
            except Exception:
                pass
            time.sleep(HEARTBEAT_SEC)

    def _on_redis_state(self, obj: dict):
        try:
            profile = obj.get("profile") or {}
            if not profile:
                profile = {
                    "node_id": obj.get("node_id"),
                    "role": obj.get("role"),
                    "power": obj.get("power", {}),
                    "skills": obj.get("skills", []),
                    "age_days": obj.get("age_days", 0),
                }
            profile["ts"] = obj.get("ts", time.time())
            self.registry.update(profile, addr=str(profile.get("node_id", "redis")))
        except Exception:
            pass

    # ── ПУБЛИЧНЫЙ API ─────────────────────────────────────
    def network_status(self) -> str:
        return self.registry.report(self.profile.to_dict())

    def route_query(self, prompt: str, task_type: str = None) -> str:
        """Отправляет AI-запрос на наиболее мощную ноду в сети."""
        return self.distributor.route_task(prompt, self.core, task_type=task_type)

    def sync_skills_from_network(self) -> str:
        """Загружает навыки от всех нод в сети."""
        nodes = self.registry.all()
        synced = []
        errors = []

        for node in nodes:
            addr = node.get("addr")
            if not addr:
                continue
            try:
                # 1. Получаем список навыков удалённой ноды
                sock = socket.socket()
                sock.settimeout(5)
                sock.connect((addr, P2P_PORT))
                sock.sendall(
                    json.dumps({"action": "sync_skills", "secret": NETWORK_SECRET}).encode()
                )
                raw = sock.recv(65536)
                sock.close()
                remote = json.loads(raw).get("skills", [])

                my_skills = set(self.profile.get_skills())

                # 2. Загружаем отсутствующие навыки
                for skill in remote:
                    if skill in my_skills or skill == "evolution":
                        continue
                    try:
                        s2 = socket.socket()
                        s2.settimeout(8)
                        s2.connect((addr, P2P_PORT))
                        s2.sendall(
                            json.dumps(
                                {
                                    "action": "get_skill",
                                    "skill": skill,
                                    "secret": NETWORK_SECRET,
                                }
                            ).encode()
                        )
                        data = json.loads(s2.recv(65536))
                        s2.close()

                        if "code" in data:
                            path = f"src/skills/{skill}.py"
                            with open(path, "w", encoding="utf-8") as f:
                                f.write(data["code"])
                            synced.append(f"{skill} ← {node['hostname']}")
                    except Exception as e:
                        errors.append(f"{skill}: {e}")

            except Exception as e:
                errors.append(f"{node.get('hostname', addr)}: {e}")

        result = [f"🔄 СИНХРОНИЗАЦИЯ НАВЫКОВ:"]
        if synced:
            result.append(f"  Загружено: {len(synced)}")
            for s in synced:
                result.append(f"    ✅ {s}")
        else:
            result.append("  Нет новых навыков для загрузки.")
        if errors:
            result.append(f"  Ошибки: {len(errors)}")
        return "\n".join(result)

    def connect_to(self, ip: str) -> str:
        """Вручную подключиться к известной ноде по IP."""
        try:
            sock = socket.socket()
            sock.settimeout(5)
            sock.connect((ip, P2P_PORT))
            sock.sendall(
                json.dumps(
                    {
                        "action": "status",
                        "secret": NETWORK_SECRET,
                    }
                ).encode()
            )
            data = json.loads(sock.recv(65536))
            sock.close()
            self.registry.update(data, ip)
            return (
                f"✅ Подключён к ноде:\n"
                f"   Хост:     {data.get('hostname')}\n"
                f"   ID:       {data.get('node_id','?')[:8]}...\n"
                f"   Возраст:  {data.get('age_days', 0):.1f} дней\n"
                f"   Мощность: {data.get('power',{}).get('index',0)}/100\n"
                f"   Навыки:   {len(data.get('skills',[]))}"
            )
        except Exception as e:
            return f"❌ Не удалось подключиться к {ip}: {e}"
