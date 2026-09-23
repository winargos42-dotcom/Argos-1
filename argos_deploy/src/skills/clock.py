"""
clock.py — время и дата без обращения к языковой модели
═══════════════════════════════════════════════════════
Модель не знает текущего времени и выдумывает его; этот навык отвечает
по системным часам (часовой пояс и NTP настраиваются в системе).
═══════════════════════════════════════════════════════
"""

from __future__ import annotations

import re
from datetime import datetime

SKILL_DESCRIPTION = "Текущее время, дата и день недели по системным часам"

_WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря")

_TIME = r"(который час|сколько (сейчас )?времени|какое (сейчас )?время|время сейчас|текущее время)"
_DATE = r"(какое (сегодня |сейчас )?число|какая (сегодня |сейчас )?дата|сегодняшн\w* дат\w*|какой сегодня день|какой день недели|день недели сегодня)"


def _now() -> datetime:
    return datetime.now().astimezone()


def _date_text(now: datetime) -> str:
    return f"{now.day} {_MONTHS[now.month - 1]} {now.year}, {_WEEKDAYS[now.weekday()]}"


def handle(text: str, core=None) -> str | None:
    """Ответ о времени или дате; None, если запрос не об этом."""
    t = text.lower()
    wants_time, wants_date = re.search(_TIME, t), re.search(_DATE, t)
    if not (wants_time or wants_date):
        return None
    now = _now()
    offset = now.strftime("%z")
    zone = f"UTC{offset[:3]}" + (f":{offset[3:]}" if offset[3:] != "00" else "")
    if wants_time and wants_date:
        return f"🕒 Сейчас {now:%H:%M}, {_date_text(now)} ({zone})."
    if wants_time:
        return f"🕒 Сейчас {now:%H:%M} ({zone})."
    return f"📅 Сегодня {_date_text(now)}."
