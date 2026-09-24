from datetime import datetime, timedelta, timezone

import pytest

from src.skills import clock

FIXED = datetime(2026, 9, 23, 16, 5, tzinfo=timezone(timedelta(hours=10)))


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(clock, "_now", lambda: FIXED)


@pytest.mark.parametrize("text", ["Который час?", "сколько сейчас времени", "Аргос, какое время"])
def test_time(text):
    assert clock.handle(text) == "🕒 Сейчас 16:05 (UTC+10)."


@pytest.mark.parametrize("text", ["какое сегодня число?", "какая дата", "какой сегодня день"])
def test_date(text):
    assert clock.handle(text) == "📅 Сегодня 23 сентября 2026, среда."


def test_time_and_date():
    assert clock.handle("который час и какое число?") == "🕒 Сейчас 16:05, 23 сентября 2026, среда (UTC+10)."


@pytest.mark.parametrize("text", ["время работы системы", "расскажи о времени и пространстве", "поток сознания", "дата-центр"])
def test_unrelated(text):
    assert clock.handle(text) is None


def test_half_hour_offset(monkeypatch):
    monkeypatch.setattr(clock, "_now", lambda: FIXED.replace(tzinfo=timezone(timedelta(hours=5, minutes=30))))
    assert clock.handle("который час") == "🕒 Сейчас 16:05 (UTC+05:30)."
