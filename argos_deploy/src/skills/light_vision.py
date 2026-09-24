"""
light_vision.py — ARGOS проверяет свет камерой ноутбука
═══════════════════════════════════════════════════════
«калибровка света» — по очереди включает каналы выключателя (только по этой команде),
  измеряет, насколько каждый освещает кадр, и возвращает всё как было;
«свет по камере» / «проверь свет» — без переключений сверяет яркость кадра с
  состояниями выключателей в Home Assistant: видно ли, что включённая лампа горит,
  нет ли света, когда всё выключено.
Кадры не сохраняются — используется только средняя яркость.
Настройки: ARGOS_LIGHT_SWITCHES (через запятую; по умолчанию каналы 1–4 выключателя wiif 4-gang),
ARGOS_LIGHT_CALIBRATION (data/light_calibration.json), ARGOS_HA_URL, ARGOS_HA_TOKEN_FILE.
═══════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import os
import re
import statistics
import time

SKILL_DESCRIPTION = "Проверка освещения камерой: калибровка каналов и сверка с Home Assistant"

_CALIBRATE = r"(калибр\w*\W+(свет\w*|освещени\w*)|(свет\w*|освещени\w*)\W+калибр\w*|откалибруй\W+свет)"
# «горит ли свет?» остаётся навыку home_assistant (список выключателей); здесь — проверка камерой
_CHECK = (r"(свет\w*\W+(по|через)\W+камер\w*|камер\w*\W+(\w+\W+)?свет\w*|"
          r"провер\w*\W+свет\w*|видишь\W+ли\W+свет)")

VISIBLE_DELTA = 8.0   # на сколько (из 255) канал должен поднимать яркость, чтобы считаться видимым
SETTLE_S = 2.0        # лампе и автоэкспозиции камеры нужно время после переключения


def _switches() -> list[str]:
    raw = os.getenv("ARGOS_LIGHT_SWITCHES", "")
    if raw.strip():
        return [s.strip() for s in raw.split(",") if s.strip()]
    return [f"switch.wiif_ble_switch_4gang_switch_{i}" for i in range(1, 5)]


def _calibration_path() -> str:
    return os.getenv("ARGOS_LIGHT_CALIBRATION", "data/light_calibration.json")


# ── ввод-вывод (подменяется в тестах) ─────────────────────
def _ha_request(method: str, path: str, payload: dict | None = None):
    import requests

    url = os.getenv("ARGOS_HA_URL", "http://127.0.0.1:8123").rstrip("/")
    with open(os.getenv("ARGOS_HA_TOKEN_FILE", "/etc/argos/ha-token"), encoding="utf-8") as fh:
        token = fh.read().strip()
    resp = requests.request(method, f"{url}/api/{path}", json=payload, timeout=10,
                            headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    return resp.json()


_NAMES: dict[str, str] = {}  # entity_id → название из HA («Свет кухня» → «кухня»)


def ha_state(entity: str) -> str:
    data = _ha_request("GET", f"states/{entity}")
    name = (data.get("attributes") or {}).get("friendly_name") or ""
    if name and not re.search(r"switch|gang", name, re.I):
        _NAMES[entity] = re.sub(r"^свет\s+", "", name, flags=re.I)
    return data["state"]


def ha_switch(entity: str, on: bool) -> None:
    _ha_request("POST", f"services/switch/turn_{'on' if on else 'off'}", {"entity_id": entity})


def measure_brightness(core=None) -> float:
    """Средняя яркость (0–255) по трём кадрам после прогрева камеры."""
    import cv2

    vision = getattr(core, "vision", None)
    if vision is None or not hasattr(vision, "grab_frames"):
        from src.vision.argos_vision import ArgosVision

        vision = ArgosVision()
    frames = vision.grab_frames(count=3, interval=0.2)
    return statistics.mean(float(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).mean()) for f in frames)


def _short(entity: str) -> str:
    if entity in _NAMES:
        return _NAMES[entity]
    match = re.search(r"(\d+)$", entity)
    return f"канал {match.group(1)}" if match else entity


# ── калибровка ────────────────────────────────────────────
def calibrate(core=None, measure=measure_brightness, state=ha_state, switch=ha_switch, sleep=time.sleep) -> dict:
    """Все выкл → фон; затем каждый канал отдельно. Исходные состояния восстанавливаются всегда."""
    entities = _switches()
    original = {e: state(e) for e in entities}
    offline = [e for e, s in original.items() if s not in ("on", "off")]
    if offline:
        raise RuntimeError("недоступны: " + ", ".join(_short(e) for e in offline))
    result = {"time": time.strftime("%Y-%m-%d %H:%M"), "channels": {}}
    try:
        for e in entities:
            switch(e, False)
        sleep(SETTLE_S)
        base = measure(core)
        result["dark"] = round(base, 1)
        for e in entities:
            switch(e, True)
            sleep(SETTLE_S)
            lit = measure(core)
            switch(e, False)
            sleep(SETTLE_S)
            result["channels"][e] = round(lit - base, 1)
    finally:
        for e, s in original.items():
            switch(e, s == "on")
        # Tuya отвечает через облако: ждём, пока HA подтвердит исходные состояния (до ~10 с)
        for _ in range(10):
            if all(state(e) == s for e, s in original.items()):
                break
            sleep(1.0)
    path = _calibration_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    return result


def load_calibration() -> dict | None:
    try:
        with open(_calibration_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def format_calibration(cal: dict) -> str:
    lines = [f"💡 Калибровка света по камере ({cal['time']}), фон в темноте {cal['dark']:.0f}/255:"]
    for e, delta in cal["channels"].items():
        seen = "видно в кадре" if delta >= VISIBLE_DELTA else "света нет: лампы нет, она перегорела или вне кадра"
        lines.append(f"  • {_short(e)}: +{max(0, round(delta))} — {seen}")
    lines.append("Свет возвращён в исходное состояние.")
    return "\n".join(lines)


# ── сверка без переключений ───────────────────────────────
def assess(brightness: float, states: dict[str, str], cal: dict) -> str:
    dark, deltas = cal["dark"], cal["channels"]
    visible = {e: d for e, d in deltas.items() if d >= VISIBLE_DELTA}
    on_visible = [e for e in visible if states.get(e) == "on"]
    expected = dark + sum(visible[e] for e in on_visible)
    lines = [f"📷 Яркость кадра {brightness:.0f}/255 (в темноте {dark:.0f}, ожидаю {expected:.0f})."]
    offline = [e for e, s in states.items() if s not in ("on", "off")]
    if offline:
        lines.append("⚠️ Нет данных от: " + ", ".join(_short(e) for e in offline))
    problems = []
    for e in on_visible:
        others = expected - visible[e]
        if brightness < others + visible[e] * 0.5:  # без этой лампы кадр был бы примерно таким
            problems.append(f"❗ {_short(e)} включён, но камера этого света не видит — лампа не горит?")
    if not on_visible and brightness > dark + max(VISIBLE_DELTA, min(visible.values(), default=VISIBLE_DELTA) * 0.5):
        problems.append("❗ Видимые каналы выключены, но в кадре светло — другой источник (день, экран, другая лампа).")
    if on_visible and not problems:
        lines.append("✅ Камера подтверждает: горит " + ", ".join(_short(e) for e in on_visible) + ".")
    elif not on_visible and not problems:
        lines.append("✅ Видимый свет выключен, в кадре темно — совпадает.")
    hidden_on = [e for e in deltas if e not in visible and states.get(e) == "on"]
    if hidden_on:
        lines.append("ℹ️ Включено вне поля зрения камеры: " + ", ".join(_short(e) for e in hidden_on) + ".")
    return "\n".join(lines + problems)


def handle(text: str, core=None) -> str | None:
    t = (text or "").lower()
    try:
        if re.search(_CALIBRATE, t):
            return format_calibration(calibrate(core))
        if re.search(_CHECK, t):
            cal = load_calibration()
            if cal is None:
                return "💡 Сначала скажи «калибровка света»: я по очереди включу каналы и запомню, что видит камера."
            states = {}
            for e in cal["channels"]:
                try:
                    states[e] = ha_state(e)
                except Exception:
                    states[e] = "unavailable"
            return assess(measure_brightness(core), states, cal)
    except Exception as e:
        return f"❌ Свет по камере: {e}"
    return None
