"""
coral_protocol.py — подпись запросов к узлу ускорения зрения (Coral Edge TPU)
═══════════════════════════════════════════════════════
X230 шлёт кадр (JPEG) на узел argos-coral по HTTP в локальной сети. HMAC-SHA256
подтверждает подлинность запроса и ответа и защищает от повтора; содержимое
не шифруется, поэтому сервис слушает только LAN и кадры нигде не сохраняются.

Файл самодостаточен (только стандартная библиотека): на узел копируется вместе с
coral_accel_server.py без остального пакета src.
═══════════════════════════════════════════════════════
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import stat
import threading
import time
import uuid

VERSION = "argos-coral-v1"
TTL = 30  # секунд: окно допустимого расхождения часов и жизни nonce
H_TS, H_NONCE, H_MAC = "X-Argos-Ts", "X-Argos-Nonce", "X-Argos-Mac"
_WEAK = (b"argos_default_secret", b"change_me", b"changeme")


def load_key(path: str | None = None) -> bytes:
    """Ключ из ARGOS_CORAL_SECRET_FILE (закрытый файл владельца сервиса), иначе ARGOS_CORAL_SECRET."""
    path = (os.getenv("ARGOS_CORAL_SECRET_FILE", "") if path is None else path).strip()
    if path:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("Ключ Coral: файл должен быть закрытым (600) и принадлежать пользователю сервиса")
            key = stream.read(4097).strip()
    else:
        key = os.getenv("ARGOS_CORAL_SECRET", "").encode()
    if not 32 <= len(key) <= 4096 or key.lower() in _WEAK:
        raise ValueError("Coral: нужен случайный секрет длиной не меньше 32 байт")
    return key


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _request_mac(key: bytes, method: str, target: str, ts: str, nonce: str, body: bytes) -> str:
    msg = "\n".join((VERSION, method.upper(), target, ts, nonce, _digest(body))).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _response_mac(key: bytes, nonce: str, status: int, body: bytes) -> str:
    msg = "\n".join((VERSION + "-resp", nonce, str(int(status)), _digest(body))).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def sign_request(key: bytes, method: str, target: str, body: bytes = b"", clock=time.time) -> dict:
    """Заголовки для запроса; target — путь с query-строкой, как он уйдёт на сервер."""
    ts, nonce = f"{clock():.3f}", uuid.uuid4().hex
    return {H_TS: ts, H_NONCE: nonce, H_MAC: _request_mac(key, method, target, ts, nonce, body)}


def sign_response(key: bytes, nonce: str, status: int, body: bytes) -> str:
    return _response_mac(key, nonce, status, body)


def verify_response(key: bytes, nonce: str, status: int, body: bytes, mac: str | None) -> bool:
    return bool(mac) and hmac.compare_digest(_response_mac(key, nonce, status, body), mac)


class Verifier:
    """Проверка подписи входящих запросов с кэшем nonce (защита от повтора)."""

    def __init__(self, key: bytes, clock=time.time, cache_size: int = 4096):
        if len(key) < 32:
            raise ValueError("Слабый ключ Coral")
        self.key, self.clock, self.limit = key, clock, cache_size
        self.seen: dict[str, float] = {}
        self.lock = threading.Lock()

    def verify(self, method: str, target: str, headers, body: bytes) -> str:
        """Возвращает nonce запроса или бросает ValueError."""
        ts, nonce, mac = (headers.get(h) or "" for h in (H_TS, H_NONCE, H_MAC))
        if not re.fullmatch(r"\d{1,12}(\.\d{1,6})?", ts) or not re.fullmatch(r"[0-9a-f]{32}", nonce) \
                or not re.fullmatch(r"[0-9a-f]{64}", mac):
            raise ValueError("Неверные заголовки подписи")
        now, when = self.clock(), float(ts)
        if not math.isfinite(when) or abs(now - when) > TTL:
            raise ValueError("Запрос устарел или часы расходятся")
        if not hmac.compare_digest(_request_mac(self.key, method, target, ts, nonce, body), mac):
            raise ValueError("Неверная подпись")
        with self.lock:
            self.seen = {k: until for k, until in self.seen.items() if until >= now}
            if nonce in self.seen or len(self.seen) >= self.limit:
                raise ValueError("Повтор запроса или переполнен кэш nonce")
            self.seen[nonce] = when + TTL
        return nonce
