#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
budding_manager.py — Менеджер почкования узлов Аргоса.

Отвечает за создание дочерних узлов (почек) на удалённых хостах в LAN.
Алгоритм:
  1. Периодически сканирует ARP-таблицу, собирая активные хосты.
  2. Для каждого нового хоста проверяет «плодородность»:
     - открыт ли порт для приёма почек (parent.port + 1000)
     - нет ли там уже узла Argos (parent.port)
  3. Если хост подходит — сериализует код и состояние и отправляет TCP-посылку.
  4. На принимающей стороне другой BuddingManager распаковывает почку
     и запускает новый процесс WhisperNode.

БЕЗОПАСНОСТЬ (почкование — это выполнение чужого кода, поэтому по умолчанию всё выключено):
  • Сеть включается только при ARGOS_BUDDING=on. Без этого объект создаётся,
    но не открывает портов и не сканирует сеть (безопасно для тестов и импорта).
  • Нужен сильный общий секрет ARGOS_NETWORK_SECRET (>=32 символов, не «argos_default_secret»),
    иначе слушатель и отправка почек не запускаются.
  • Принимаются ТОЛЬКО ГОСТ-запечатанные почки (шифр + HMAC-Стрибог). Незашифрованный
    pickle из сети никогда не десериализуется и не выполняется (это был RCE).
  • Слушатель привязан к ARGOS_BUDDING_BIND (по умолчанию 127.0.0.1), а не ко всем интерфейсам.
  • Авто-поиск «плодородной земли» и авто-рассылка почек — только при ARGOS_BUDDING_AUTOSPREAD=on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from typing import Optional

log = logging.getLogger("argos.budding")

import numpy as np

_ON = ("1", "true", "on", "yes", "да")
_WEAK_SECRETS = ("argos_default_secret", "change_me", "changeme", "secret", "password")


class BuddingManager:
    """
    Менеджер почкования узлов.

    Параметры
    ---------
    parent_node : WhisperNode
        Родительский узел (должен иметь атрибуты: node_id, port, host,
        hidden_size, hidden_state, light_mode, rnn).
    soil_search_interval : int
        Интервал в секундах между циклами поиска «плодородной земли».
    """

    def __init__(self, parent_node=None, soil_search_interval: int = 60, node_id: str = None, port: int = None):
        # Backward compatibility: accept node_id/port for tests
        if node_id is not None and port is not None:
            # Create mock parent node
            class MockNode:
                def __init__(self, node_id, port):
                    self.node_id = node_id
                    self.port = port
                    self.host = "127.0.0.1"
                    self.hidden_size = 128
                    self.hidden_state = None
                    self.light_mode = False
                    self.rnn = None
            parent_node = MockNode(node_id, port)
        self.parent = parent_node
        self.bud_port = parent_node.port + 1000 if parent_node else (port or 5000) + 1000
        self.soil_search_interval = soil_search_interval
        self.running = True
        self.known_hosts: set = set()
        self.sent_buds: dict = defaultdict(float)  # host -> timestamp

        # Всё сетевое — только по явному согласию; почка = выполнение чужого кода
        self.enabled = os.getenv("ARGOS_BUDDING", "").strip().lower() in _ON
        self.autospread = os.getenv("ARGOS_BUDDING_AUTOSPREAD", "").strip().lower() in _ON
        self.bind_host = os.getenv("ARGOS_BUDDING_BIND", "127.0.0.1").strip() or "127.0.0.1"

        # ГОСТ-безопасность для шифрования почек: только с сильным общим секретом
        self._gost = None
        secret = os.getenv("ARGOS_NETWORK_SECRET", "").strip()
        if not self.enabled:
            log.info("BuddingManager создан, но выключен (ARGOS_BUDDING=on — включить)")
        elif len(secret) < 32 or secret.lower() in _WEAK_SECRETS:
            log.error("BuddingManager: нужен ARGOS_NETWORK_SECRET >=32 символов и не по умолчанию — сеть не запущена")
            self.enabled = False
        else:
            try:
                from src.connectivity.gost_p2p import GostP2PSecurity

                self._gost = GostP2PSecurity(secret=secret)
            except Exception as e:
                log.error("BuddingManager: ГОСТ недоступен (%s) — сеть не запущена", e)
                self.enabled = False

        if self.enabled and self._gost:
            self._start_bud_listener()
            if self.autospread:
                self._start_soil_search()
            else:
                log.info("BuddingManager: авто-распространение выключено (ARGOS_BUDDING_AUTOSPREAD=on — включить)")

    # ── TCP-сервер для приёма почек ──────────────────
    def _start_bud_listener(self):
        def listener():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Привязка к конкретному адресу (по умолчанию loopback), не ко всем интерфейсам
            sock.bind((self.bind_host, self.bud_port))
            sock.listen(5)
            sock.settimeout(0.5)
            while self.running:
                try:
                    conn, addr = sock.accept()
                    threading.Thread(
                        target=self._handle_incoming_bud,
                        args=(conn, addr),
                        daemon=True,
                    ).start()
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        log.warning("%s BudListener: %s", self.parent.node_id, e)
            sock.close()

        t = threading.Thread(target=listener, daemon=True)
        t.start()
        self._listener_thread = t

    def _handle_incoming_bud(self, conn, addr):
        """Получает TCP-посылку с кодом и состоянием, запускает новый узел.
        Принимает только ГОСТ-запечатанные почки (ARGOS-BUD-GOST-1); всё остальное отвергается.
        """
        try:
            chunks = []
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)

            # Принимаем ТОЛЬКО ГОСТ-запечатанные почки (шифр + HMAC). Незашифрованный
            # pickle из сети — это удалённое выполнение кода, поэтому отвергаем сразу.
            if not (self._gost and data.startswith(b"ARGOS-BUD-GOST-1")):
                log.warning("%s Почка без ГОСТ-подписи от %s — отклонена", self.parent.node_id, addr[0])
                conn.close()
                return
            try:
                pkg = self._gost.open_bud(data)
                log.info("%s ГОСТ-почка принята от %s", self.parent.node_id, addr[0])
            except Exception as e:
                log.warning("%s ГОСТ проверка почки: %s", self.parent.node_id, e)
                conn.close()
                return

            code = pkg["code"]
            state = pkg["state"]
            target_port = pkg.get("target_port", self.parent.port + 1)

            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                script_path = f.name

            new_id = f"{self.parent.node_id}_bud_{hashlib.md5(code.encode()).hexdigest()[:4]}"
            cmd = [
                sys.executable,
                script_path,
                "--node-id",
                new_id,
                "--port",
                str(target_port),
                "--hidden-size",
                str(state["hidden_size"]),
            ]
            if state.get("light_mode"):
                cmd.append("--light-mode")

            hidden = state["hidden_state"]
            if isinstance(hidden, np.ndarray):
                hidden = hidden.tolist()
            cmd += [
                "--initial-state",
                json.dumps(hidden),
                "--initial-weights",
                json.dumps(
                    {
                        "W_h": state["W_h"],
                        "W_i": state["W_i"],
                        "b": state["b"],
                    }
                ),
            ]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log.info("%s Bud launched: %s from %s", self.parent.node_id, new_id, addr[0])
        except Exception as e:
            log.error("%s Bud handle: %s", self.parent.node_id, e)
        finally:
            conn.close()

    # ── Отправка почки ───────────────────────────────
    def send_bud(
        self,
        target_ip: str,
        target_port: Optional[int] = None,
        target_bud_port: Optional[int] = None,
    ) -> bool:
        """
        Сериализует код родителя и отправляет его TCP-посылкой на
        target_ip:target_bud_port. Новый узел будет слушать на target_port.
        """
        # Без общего ГОСТ-секрета почку не шлём (иначе получатель её и не примет)
        if not self._gost:
            log.error("%s Отправка почки без ГОСТ-секрета запрещена", self.parent.node_id)
            return False

        if target_bud_port is None:
            target_bud_port = self.bud_port

        # Не отправляем на один хост чаще чем раз в 5 минут
        if time.time() - self.sent_buds.get(target_ip, 0) < 300:
            return False

        # Код для самовоспроизведения
        try:
            script = os.path.join(os.path.dirname(__file__), "whisper_node.py")
            if os.path.exists(script):
                with open(script, encoding="utf-8") as f:
                    code = f.read()
            else:
                import inspect
                from src.connectivity import whisper_node as _wm

                code = inspect.getsource(_wm)
        except Exception as e:
            log.error("%s Cannot get source: %s", self.parent.node_id, e)
            return False

        # Состояние родителя
        state = {
            "hidden_size": self.parent.hidden_size,
            "hidden_state": self.parent.hidden_state.tolist(),
            "W_h": self.parent.rnn.W_h.tolist(),
            "W_i": self.parent.rnn.W_i.tolist(),
            "b": self.parent.rnn.b.tolist(),
            "light_mode": self.parent.light_mode,
        }
        bud_pkg = {
            "code": code,
            "state": state,
            "target_port": target_port or (self.parent.port + 1),
        }

        # Сериализация только с ГОСТ-шифрованием (проверка секрета — в начале метода)
        pkg = self._gost.seal_bud(bud_pkg)
        log.info("%s Почка зашифрована ГОСТ Кузнечик-CTR + HMAC-Стрибог", self.parent.node_id)

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((target_ip, target_bud_port))
            sock.sendall(pkg)
            sock.close()
            self.sent_buds[target_ip] = time.time()
            log.info("%s Bud sent → %s:%s", self.parent.node_id, target_ip, target_bud_port)
            return True
        except Exception as e:
            log.warning("%s Bud send failed: %s", self.parent.node_id, e)
            return False

    # ── Поиск «плодородной земли» ────────────────────
    def _start_soil_search(self):
        def loop():
            while self.running:
                try:
                    self.find_soil()
                except Exception as e:
                    log.warning("%s Soil search: %s", self.parent.node_id, e)
                time.sleep(self.soil_search_interval)

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        self._search_thread = t

    def find_soil(self):
        """Ищет хосты в LAN, подходящие для почкования."""
        for host in self._get_local_hosts():
            if host in self.known_hosts:
                continue
            if self._is_soil_suitable(host):
                log.info("%s Suitable soil at %s", self.parent.node_id, host)
                free_port = self._find_free_port(host, start=5001)
                if free_port:
                    self.send_bud(host, target_port=free_port)
                self.known_hosts.add(host)
                break  # по одной почке за цикл

    def _get_local_hosts(self) -> set:
        """Возвращает активные IPv4-хосты из ARP-таблицы."""
        hosts = set()
        try:
            output = subprocess.check_output(["arp", "-a"], text=True, timeout=5)
            for ip in re.findall(r"(\d+\.\d+\.\d+\.\d+)", output):
                if ip != self.parent.host and not ip.endswith(".255"):
                    hosts.add(ip)
        except Exception as e:
            log.warning("%s ARP error: %s", self.parent.node_id, e)
        return hosts

    def _is_soil_suitable(self, ip: str) -> bool:
        """Хост подходит, если принимает почки, но ещё не запустил Argos."""
        if not self._is_port_open(ip, self.bud_port, timeout=1):
            return False
        if self._is_port_open(ip, self.parent.port, timeout=1):
            return False  # Argos уже запущен на этом хосте
        return True

    def _is_port_open(self, ip: str, port: int, timeout: float = 1.0) -> bool:
        """Возвращает True если TCP-порт открыт."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _find_free_port(self, ip: str, start: int = 5001, end: int = 6000) -> Optional[int]:
        """Ищет свободный TCP-порт в диапазоне [start, end)."""
        for port in range(start, end):
            if not self._is_port_open(ip, port, timeout=0.2):
                return port
        return None

    # ── Управление жизненным циклом ──────────────────
    def start(self) -> str:
        """Запускает менеджер почкования (потоки уже запущены в __init__)."""
        self.running = True
        return "✅ BuddingManager запущен"

    def stop(self) -> str:
        """Останавливает менеджер почкования."""
        self.running = False
        return "🛑 BuddingManager остановлен"

    def status(self) -> str:
        """Возвращает статус менеджера почкования."""
        if not getattr(self, "enabled", False):
            return "🌿 BuddingManager выключен (ARGOS_BUDDING=on + сильный ARGOS_NETWORK_SECRET)"
        state = "активен" if self.running else "остановлен"
        node_id = self.parent.node_id if self.parent else "—"
        spread = "авто" if getattr(self, "autospread", False) else "ручное"
        return (f"🌿 BuddingManager [{state}] | узел: {node_id} | {self.bind_host}:{self.bud_port} | "
                f"ГОСТ-шифр | распространение: {spread}")