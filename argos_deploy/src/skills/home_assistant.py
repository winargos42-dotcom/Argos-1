"""
home_assistant.py — ARGOS читает состояние дома из Home Assistant
═══════════════════════════════════════════════════════
Отвечает без обращения к языковой модели (мгновенно):
  • электричество: напряжение, ток, мощность, энергия, частота
  • выключатели и свет: что включено
  • климат: температура, влажность
  • двери и датчики движения
  • «что дома» — общая сводка

Только чтение: навык никогда не вызывает сервисы HA и ничего не переключает.
Настройки: ARGOS_HA_URL (http://127.0.0.1:8123), ARGOS_HA_TOKEN_FILE (/etc/argos/ha-token).
═══════════════════════════════════════════════════════
"""

from __future__ import annotations

import os
import re

import requests

from src.argos_logger import get_logger

SKILL_DESCRIPTION = "Состояние дома из Home Assistant: электричество, свет, климат, датчики (только чтение)"

log = get_logger("argos.home_assistant")

_UNITS_OUT = {"kW": ("Вт", 1000.0), "W": ("Вт", 1.0), "V": ("В", 1.0), "A": ("А", 1.0),
              "kWh": ("кВт·ч", 1.0), "Hz": ("Гц", 1.0), "°C": ("°C", 1.0), "%": ("%", 1.0)}
_OFFLINE = ("unavailable", "unknown")
_SKIP_PREFIXES = ("sensor.sun_", "sensor.argos_panel", "binary_sensor.argos_panel", "sensor.backup")

# Намерения: регулярные выражения по границам слов (чтобы «поток» не совпадал с «ток»)
_INTENTS = {
    "energy": r"\b(напряжени\w*|ток\w?|мощност\w*|потреблени\w*|электричеств\w*|энерги\w*|счётчик\w*|счетчик\w*|киловатт\w*)\b",
    "switches": r"\b(свет\w*|выключател\w*|розетк\w*|включен\w*)\b",
    "climate": r"\b(температур\w*|влажност\w*|тепло|холодно|климат\w*)\b",
    "security": r"\b(двер\w*|открыт\w*|движени\w*|датчик\w*)\b",
    "summary": r"(что (у нас )?дома|состояние дома|как дела дома|статус дома|сводка по дому|дом статус)",
}
_HOME_CONTEXT = r"\b(дом\w*|квартир\w*|коридор\w*|кухн\w*|комнат\w*|сейчас|у нас)\b"


def _config() -> tuple[str, str]:
    url = os.getenv("ARGOS_HA_URL", "http://127.0.0.1:8123").rstrip("/")
    token_file = os.getenv("ARGOS_HA_TOKEN_FILE", "/etc/argos/ha-token")
    try:
        with open(token_file, encoding="utf-8") as f:
            return url, f.read().strip()
    except OSError:
        return url, ""


def _fetch_states() -> list[dict] | None:
    url, token = _config()
    if not token:
        return None
    try:
        resp = requests.get(f"{url}/api/states", headers={"Authorization": f"Bearer {token}"}, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning("Home Assistant недоступен: %s", e)
        return None


def _name(state: dict) -> str:
    return state.get("attributes", {}).get("friendly_name") or state["entity_id"]


def _value(state: dict) -> str:
    raw, unit = state.get("state", ""), state.get("attributes", {}).get("unit_of_measurement", "")
    out_unit, factor = _UNITS_OUT.get(unit, (unit, 1.0))
    try:
        number = float(raw) * factor
        text = f"{number:.0f}" if factor != 1.0 or number >= 100 else f"{number:g}"
    except (TypeError, ValueError):
        text = raw
    return f"{text} {out_unit}".strip()


def _live(states: list[dict]) -> list[dict]:
    return [s for s in states if s.get("state") not in _OFFLINE and not s["entity_id"].startswith(_SKIP_PREFIXES)]


def _by_class(states: list[dict], domain: str, classes: tuple[str, ...]) -> list[dict]:
    return [s for s in _live(states) if s["entity_id"].startswith(domain + ".")
            and s.get("attributes", {}).get("device_class") in classes]


def _energy(states: list[dict]) -> str:
    rows = _by_class(states, "sensor", ("voltage", "current", "power", "energy", "frequency"))
    if not rows:
        return "⚡ Данных об электричестве нет (датчики недоступны)."
    lines = ["⚡ Электричество:"] + [f"  • {_name(s)}: {_value(s)}" for s in rows]
    return "\n".join(lines)


def _switches(states: list[dict]) -> str:
    rows = [s for s in _live(states) if s["entity_id"].split(".")[0] in ("switch", "light")
            and "child_lock" not in s["entity_id"]]
    if not rows:
        return "💡 Выключатели недоступны (устройства не в сети)."
    on = [_name(s) for s in rows if s["state"] == "on"]
    off = [_name(s) for s in rows if s["state"] == "off"]
    lines = ["💡 Выключатели и свет:"]
    lines += [f"  • ВКЛ: {n}" for n in on] + [f"  • выкл: {n}" for n in off]
    return "\n".join(lines)


def _climate(states: list[dict]) -> str:
    rows = _by_class(states, "sensor", ("temperature", "humidity"))
    if not rows:
        return "🌡 Датчиков температуры и влажности в сети нет."
    return "\n".join(["🌡 Климат:"] + [f"  • {_name(s)}: {_value(s)}" for s in rows])


def _security(states: list[dict]) -> str:
    rows = _by_class(states, "binary_sensor", ("door", "window", "opening", "motion", "occupancy"))
    if not rows:
        return "🚪 Датчиков дверей и движения в сети нет."
    return "\n".join(["🚪 Датчики:"] + [f"  • {_name(s)}: {'сработал' if s['state'] == 'on' else 'спокойно'}"
                                        for s in rows])


def _summary(states: list[dict]) -> str:
    devices = [s for s in states if not s["entity_id"].startswith(_SKIP_PREFIXES)
               and s["entity_id"].split(".")[0] in ("sensor", "switch", "light", "binary_sensor", "climate")]
    offline = sum(1 for s in devices if s.get("state") in _OFFLINE)
    head = f"🏠 Дом: в сети {len(devices) - offline} из {len(devices)} сущностей"
    return "\n\n".join([head, _energy(states), _switches(states), _climate(states), _security(states)])


def detect_intent(text: str) -> str | None:
    t = text.lower()
    if re.search(_INTENTS["summary"], t):
        return "summary"
    for intent in ("energy", "climate", "security", "switches"):
        if re.search(_INTENTS[intent], t):
            # «свет»/«датчик»/«открыт» слишком общие — требуем домашний контекст или вопрос
            if intent in ("switches", "security") and not (re.search(_HOME_CONTEXT, t) or "?" in t):
                continue
            return intent
    return None


def handle(text: str, core=None) -> str | None:
    """Ответ о состоянии дома или None, если запрос не про дом."""
    intent = detect_intent(text)
    if not intent:
        return None
    states = _fetch_states()
    if states is None:
        return "🏠 Home Assistant сейчас недоступен — не могу получить состояние дома."
    return {"energy": _energy, "switches": _switches, "climate": _climate,
            "security": _security, "summary": _summary}[intent](states)
