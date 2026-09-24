"""
ha_camera_vision.py — ARGOS смотрит через камеры Home Assistant и распознаёт на узле Coral
═══════════════════════════════════════════════════════
«камеры дома» / «какие камеры» — список камер в Home Assistant;
«что на камере <название>» / «камера <название> что видит» — снимок камеры HA → детекция объектов;
«лица на камере <название>» — снимок → детекция лиц;
«что за предмет на камере <название>» — снимок → классификация.

Снимок берётся из HA (/api/camera_proxy/<entity_id>), уходит на узел Coral по LAN с
подписью HMAC и нигде не сохраняется. Если камера одна — название можно не указывать.
Настройки: ARGOS_HA_URL, ARGOS_HA_TOKEN_FILE (как у навыка home_assistant),
ARGOS_CORAL_URL, ARGOS_CORAL_SECRET_FILE (как у навыка coral).
═══════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import re

SKILL_DESCRIPTION = "Распознавание по камерам Home Assistant на узле Coral (объекты, лица, предметы)"

_CAM = r"камер\w*"
_LIST = rf"(как\w*\W+{_CAM}|список\W+{_CAM}|{_CAM}\W+дома|дома\W+{_CAM}|сколько\W+{_CAM})"
# «что на камере X», «камера X что видит», «лица/предмет на камере X»
_ON_CAM = rf"(на\W+{_CAM}|{_CAM}[ае]?)\W*"
_FACES = rf"лиц\w*\W+{_ON_CAM}"
_CLASSIFY = rf"(что\W+за\W+предмет|классифиц\w*)\W+{_ON_CAM}"
_LOOK = rf"(что\W+(на|видит|видно)\W+{_ON_CAM}|{_ON_CAM}\W*(что\W+вид\w*|кто\W+в\W+кадре|объекты))"


def _ha() -> tuple[str, str]:
    url = os.getenv("ARGOS_HA_URL", "http://127.0.0.1:8123").rstrip("/")
    try:
        with open(os.getenv("ARGOS_HA_TOKEN_FILE", "/etc/argos/ha-token"), encoding="utf-8") as f:
            return url, f.read().strip()
    except OSError:
        return url, ""


def list_cameras() -> list[dict]:
    """[{entity_id, name, state}] всех камер HA. Пустой список, если их нет или HA недоступен."""
    import requests

    url, token = _ha()
    if not token:
        return []
    try:
        resp = requests.get(f"{url}/api/states", headers={"Authorization": f"Bearer {token}"}, timeout=5)
        resp.raise_for_status()
        states = resp.json()
    except Exception:
        return []
    cams = []
    for s in states:
        if s["entity_id"].startswith("camera."):
            cams.append({"entity_id": s["entity_id"],
                         "name": s.get("attributes", {}).get("friendly_name") or s["entity_id"],
                         "state": s.get("state", "")})
    return cams


def pick_camera(cams: list[dict], query: str) -> dict | None:
    """Выбор камеры по названию из фразы; если камера одна — берём её без названия."""
    if not cams:
        return None
    if len(cams) == 1:
        return cams[0]
    q = query.lower()
    for c in cams:  # по имени или по хвосту entity_id (camera.dvor → «двор»)
        name = c["name"].lower()
        ent = c["entity_id"].split(".", 1)[-1].replace("_", " ").lower()
        if name in q or ent in q or any(w in q for w in name.split() if len(w) > 3):
            return c
    return None


def snapshot(entity_id: str) -> bytes:
    """JPEG-снимок камеры HA. Бросает RuntimeError при ошибке."""
    import requests

    url, token = _ha()
    if not token:
        raise RuntimeError("Home Assistant не настроен")
    resp = requests.get(f"{url}/api/camera_proxy/{entity_id}",
                        headers={"Authorization": f"Bearer {token}"}, timeout=10)
    if resp.status_code != 200 or not resp.content:
        raise RuntimeError(f"камера не дала снимок (HTTP {resp.status_code})")
    return resp.content


def _coral():
    from src.vision.coral_client import CoralClient

    client = CoralClient.from_env()
    if client is None:
        raise RuntimeError("узел Coral не настроен (ARGOS_CORAL_URL)")
    return client


def _format_list(cams: list[dict]) -> str:
    if not cams:
        return "📷 В Home Assistant нет камер. Добавь камеру (телефон через приложение HA, IP/Tuya-камеру) — и я смогу смотреть через неё."
    lines = [f"📷 Камеры в Home Assistant ({len(cams)}):"]
    for c in cams:
        mark = "🟢" if c["state"] not in ("unavailable", "unknown", "") else "⚪"
        lines.append(f"  {mark} {c['name']}")
    return "\n".join(lines)


def handle(text: str, core=None) -> str | None:
    t = (text or "").lower()
    if not re.search(_CAM, t):
        return None
    try:
        if re.search(_LIST, t):
            return _format_list(list_cameras())
        # запросы распознавания требуют слова про камеру + действие
        wants = re.search(_FACES, t) or re.search(_CLASSIFY, t) or re.search(_LOOK, t)
        if not wants:
            return None
        cams = list_cameras()
        if not cams:
            return _format_list(cams)
        cam = pick_camera(cams, t)
        if cam is None:
            names = ", ".join(c["name"] for c in cams)
            return f"📷 Какая камера? Есть: {names}."
        img = snapshot(cam["entity_id"])
        client = _coral()
        if re.search(_FACES, t):
            r = client.detect(img, model="faces", threshold=0.5)
            n = len(r.get("objects") or [])
            return f"🙂 {cam['name']}: лиц в кадре — {n} ({r.get('inference_ms', '?')} мс)."
        if re.search(_CLASSIFY, t):
            from src.vision.coral_client import ru_label

            r = client.classify(img)
            labels = r.get("labels") or []
            if not labels:
                return f"🔎 {cam['name']}: предмет не определён ({r.get('inference_ms', '?')} мс)."
            top = ", ".join(f"{ru_label(o['label'])} ({o['score']:.0%})" for o in labels[:3])
            return f"🔎 {cam['name']}: {top} ({r.get('inference_ms', '?')} мс на TPU)."
        from src.vision.coral_client import summarize

        r = client.detect(img, model="objects")
        return f"{cam['name']} — {summarize(r)}"
    except Exception as e:  # HA/камера/узел недоступны — честно, без выдумок
        return f"❌ Камера HA: {e}"
    return None
