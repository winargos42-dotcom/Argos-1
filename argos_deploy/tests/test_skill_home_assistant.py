import pytest

from src.skills import home_assistant as ha


def state(entity_id, value, name, unit="", device_class=None):
    attrs = {"friendly_name": name}
    if unit:
        attrs["unit_of_measurement"] = unit
    if device_class:
        attrs["device_class"] = device_class
    return {"entity_id": entity_id, "state": value, "attributes": attrs}


STATES = [
    state("sensor.protector_voltage", "236.4", "Защита Напряжение", "V", "voltage"),
    state("sensor.protector_current", "0.71", "Защита Ток", "A", "current"),
    state("sensor.protector_power", "0.168", "Защита Мощность", "kW", "power"),
    state("switch.protector_switch", "on", "Защита Выключатель"),
    state("switch.protector_child_lock", "off", "Защита Блокировка"),
    state("switch.dom_switch_1", "unavailable", "дом Switch 1"),
    state("sensor.th_temperature", "unavailable", "Датчик Температура", "°C", "temperature"),
    state("binary_sensor.dver_door", "on", "Дверь", device_class="door"),
    state("sensor.sun_next_dawn", "2026-09-24T06:00:00+00:00", "Sun"),
]


@pytest.fixture(autouse=True)
def fake_ha(monkeypatch):
    monkeypatch.setattr(ha, "_fetch_states", lambda: STATES)


@pytest.mark.parametrize(("text", "intent"), [
    ("Какое напряжение сейчас?", "energy"),
    ("сколько потребляем электричества", "energy"),
    ("что у нас дома", "summary"),
    ("какая температура дома", "climate"),
    ("свет в коридоре включён?", "switches"),
    ("дверь открыта?", "security"),
])
def test_intents(text, intent):
    assert ha.detect_intent(text) == intent


@pytest.mark.parametrize("text", [
    "поток сознания", "расскажи анекдот", "светлое будущее искусственного интеллекта",
    "Вычисли 2+2", "открыт ли музей",
])
def test_unrelated_text_is_not_intercepted(text):
    assert ha.detect_intent(text) is None
    assert ha.handle(text) is None


def test_energy_converts_kw_to_watts():
    answer = ha.handle("какое напряжение?")
    assert "236 В" in answer and "0.71 А" in answer and "168 Вт" in answer


def test_switches_skip_offline_and_child_lock():
    answer = ha.handle("что со светом дома?")
    assert "ВКЛ: Защита Выключатель" in answer
    assert "Блокировка" not in answer and "Switch 1" not in answer


def test_summary_reports_offline_counts_and_skips_sun():
    answer = ha.handle("что дома")
    assert "в сети" in answer and "Sun" not in answer
    assert "Дверь: сработал" in answer


def test_unreachable_home_assistant(monkeypatch):
    monkeypatch.setattr(ha, "_fetch_states", lambda: None)
    assert "недоступен" in ha.handle("какое напряжение")


def test_skill_is_read_only():
    source = open(ha.__file__, encoding="utf-8").read()
    assert "/api/services" not in source and "requests.post" not in source
