"""
coral.py — зрение через узел argos-coral (Coral Edge TPU) без языковой модели
═══════════════════════════════════════════════════════
«корал статус» — состояние узла и задержки TPU;
«что видит корал» / «корал посмотри» — объекты в кадре камеры X230;
«корал лица» — сколько лиц в кадре.
Кадр снимается локально, уходит на узел по LAN с подписью HMAC и нигде не сохраняется.
Настройка: ARGOS_CORAL_URL, ARGOS_CORAL_SECRET_FILE (см. deploy/coral/README.md).
═══════════════════════════════════════════════════════
"""

from __future__ import annotations

import re

from src.vision.coral_client import summarize  # noqa: F401  (ответ навыка)

SKILL_DESCRIPTION = "Детекция объектов и лиц на Coral Edge TPU (узел argos-coral)"

_CORAL = r"(корал\w*|coral)"
_STATUS = rf"({_CORAL}\W+(статус|status|состояние)|(статус|состояние)\W+{_CORAL})"
_FACES = rf"{_CORAL}\W+(\w+\W+)?лиц\w*"
_LOOK = rf"(что\W+(видит|видишь)\W+{_CORAL}|{_CORAL}\W+(что\W+видишь|посмотри|кто\W+в\W+кадре|камера|объекты))"


def _client():
    from src.vision.coral_client import CoralClient

    client = CoralClient.from_env()
    if client is None:
        raise RuntimeError("узел Coral не настроен (ARGOS_CORAL_URL)")
    return client


def _frame_jpeg(core=None) -> bytes:
    import cv2

    vision = getattr(core, "vision", None)
    if vision is None or not hasattr(vision, "grab_frames"):
        from src.vision.argos_vision import ArgosVision

        vision = ArgosVision()
    frame = vision.grab_frames(count=1)[0]
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise RuntimeError("не удалось сжать кадр")
    return buf.tobytes()


def _status_text(client) -> str:
    info = client.status()
    lines = [f"🧠 Узел Coral работает, аптайм {info.get('uptime_s', 0) // 60} мин."]
    for name, st in (info.get("models") or {}).items():
        avg = f"{st['avg_ms']} мс" if st.get("avg_ms") is not None else "ещё не вызывалась"
        lines.append(f"  • {name}: {'TPU' if st.get('tpu') else 'CPU'}, вызовов {st.get('calls', 0)}, среднее {avg}")
    return "\n".join(lines)


def handle(text: str, core=None) -> str | None:
    t = (text or "").lower()
    if not re.search(_CORAL, t):
        return None
    try:
        if re.search(_STATUS, t):
            return _status_text(_client())
        if re.search(_FACES, t):
            result = _client().detect(_frame_jpeg(core), model="faces", threshold=0.5)
            n = len(result.get("objects") or [])
            return f"🙂 Coral: лиц в кадре — {n} ({result.get('inference_ms', '?')} мс)."
        if re.search(_LOOK, t):
            return summarize(_client().detect(_frame_jpeg(core), model="objects"))
    except Exception as e:  # узел недоступен/не настроен — честно говорим, модель не выдумывает
        return f"❌ Coral: {e}"
    return None
