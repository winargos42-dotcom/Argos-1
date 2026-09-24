"""
coral_client.py — клиент ARGOS к узлу argos-vision-accel (Coral Edge TPU)
═══════════════════════════════════════════════════════
ARGOS_CORAL_URL=http://192.168.1.93:8770 и общий ключ ARGOS_CORAL_SECRET_FILE.
Если URL не задан или узел недоступен — CoralUnavailable, вызывающий код
откатывается на локальный анализ X230.
═══════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections import Counter
from urllib.parse import urlencode, urlsplit

from src.vision import coral_protocol as proto


RU = {
    "person": "человек", "bicycle": "велосипед", "car": "машина", "motorcycle": "мотоцикл",
    "bus": "автобус", "truck": "грузовик", "cat": "кошка", "dog": "собака", "bird": "птица",
    "bottle": "бутылка", "cup": "кружка", "chair": "стул", "couch": "диван", "bed": "кровать",
    "dining table": "стол", "tv": "телевизор", "laptop": "ноутбук", "mouse": "мышь",
    "keyboard": "клавиатура", "cell phone": "телефон", "book": "книга", "clock": "часы",
    "potted plant": "растение", "remote": "пульт", "scissors": "ножницы", "backpack": "рюкзак",
    "handbag": "сумка", "knife": "нож", "spoon": "ложка", "bowl": "миска", "sink": "раковина",
    "refrigerator": "холодильник", "microwave": "микроволновка", "oven": "духовка",
    "toilet": "унитаз", "umbrella": "зонт", "vase": "ваза", "teddy bear": "игрушка", "face": "лицо",
}


class CoralUnavailable(RuntimeError):
    """Узел не настроен, недоступен или ответ не прошёл проверку."""


class CoralClient:
    def __init__(self, url: str, key: bytes, timeout: float = 3.0):
        parts = urlsplit(url)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError("ARGOS_CORAL_URL должен быть вида http://IP:порт")
        self.base, self.key, self.timeout = url.rstrip("/"), key, timeout

    @classmethod
    def from_env(cls) -> "CoralClient | None":
        url = os.getenv("ARGOS_CORAL_URL", "").strip()
        if not url:
            return None
        return cls(url, proto.load_key(), float(os.getenv("ARGOS_CORAL_TIMEOUT", "3")))

    def _call(self, method: str, target: str, body: bytes = b"", signed: bool = True) -> dict:
        headers = {"Content-Type": "application/octet-stream"} if body else {}
        nonce = None
        if signed:
            signature = proto.sign_request(self.key, method, target, body)
            nonce = signature[proto.H_NONCE]
            headers.update(signature)
        request = urllib.request.Request(self.base + target, data=body or None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status, data, mac = response.status, response.read(1 << 20), response.headers.get(proto.H_MAC)
        except urllib.error.HTTPError as e:
            status, data, mac = e.code, e.read(1 << 20), e.headers.get(proto.H_MAC)
        except (urllib.error.URLError, OSError) as e:
            raise CoralUnavailable(f"узел Coral недоступен: {getattr(e, 'reason', e)}") from e
        if signed and not proto.verify_response(self.key, nonce, status, data, mac):
            raise CoralUnavailable(f"ответ узла Coral не прошёл проверку подписи (HTTP {status})")
        try:
            payload = json.loads(data)
        except ValueError as e:
            raise CoralUnavailable("узел Coral вернул не JSON") from e
        if status != 200:
            raise CoralUnavailable(f"узел Coral: {payload.get('error', status)}")
        return payload

    def health(self) -> bool:
        try:
            return bool(self._call("GET", "/v1/health", signed=False).get("ok"))
        except CoralUnavailable:
            return False

    def status(self) -> dict:
        return self._call("GET", "/v1/status")

    def detect(self, image: bytes, model: str = "objects", threshold: float = 0.4, top_k: int = 20) -> dict:
        if not image:
            raise ValueError("пустой кадр")
        query = urlencode({"model": model, "threshold": f"{threshold:.2f}", "top_k": int(top_k)})
        return self._call("POST", f"/v1/detect?{query}", image)

    def classify(self, image: bytes, model: str = "classify", threshold: float = 0.1, top_k: int = 5) -> dict:
        if not image:
            raise ValueError("пустой кадр")
        query = urlencode({"model": model, "threshold": f"{threshold:.2f}", "top_k": int(top_k)})
        return self._call("POST", f"/v1/classify?{query}", image)


def summarize(result: dict) -> str:
    objects = result.get("objects") or []
    where = "TPU" if result.get("tpu") else "CPU"
    timing = f"{result.get('inference_ms', '?')} мс на {where}"
    if not objects:
        return f"👁 Coral: в кадре ничего не распознано ({timing})."
    counts = Counter(RU.get(o["label"], o["label"]) for o in objects)
    parts = [f"{name} ×{n}" if n > 1 else name for name, n in counts.most_common()]
    best = max(objects, key=lambda o: o["score"])
    return (f"👁 Coral видит: {', '.join(parts)} "
            f"(уверенность до {best['score']:.0%}, {timing}).")
