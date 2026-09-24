import json

import pytest

from src.skills import light_vision as lv

CH = [f"switch.wiif_ble_switch_4gang_switch_{i}" for i in range(1, 5)]
# Замеры 2026-09-24 на X230: канал 1 — основной свет комнаты, 2 — вне кадра, 3 и 4 — слабее
CAL = {"time": "2026-09-24 03:25", "dark": 7.0,
       "channels": {CH[0]: 130.0, CH[1]: 0.5, CH[2]: 20.0, CH[3]: 22.0}}


@pytest.fixture(autouse=True)
def calib_path(tmp_path, monkeypatch):
    lv._NAMES.clear()
    monkeypatch.setenv("ARGOS_LIGHT_CALIBRATION", str(tmp_path / "cal.json"))
    monkeypatch.delenv("ARGOS_LIGHT_SWITCHES", raising=False)


class FakeHome:
    """Лампы влияют на яркость аддитивно, как в калибровке."""

    def __init__(self, states, fail_on=None):
        self.states = dict(states)
        self.calls = []
        self.fail_on = fail_on

    def state(self, e):
        return self.states[e]

    def switch(self, e, on):
        self.calls.append((e, on))
        self.states[e] = "on" if on else "off"

    def measure(self, core=None):
        if self.fail_on and self.states.get(self.fail_on) == "on":
            raise RuntimeError("камера отвалилась")
        return CAL["dark"] + sum(CAL["channels"][e] for e, s in self.states.items() if s == "on")


def test_calibration_measures_each_channel_and_restores():
    home = FakeHome({CH[0]: "off", CH[1]: "off", CH[2]: "on", CH[3]: "off"})
    cal = lv.calibrate(measure=home.measure, state=home.state, switch=home.switch, sleep=lambda s: None)
    assert cal["dark"] == 7.0
    assert cal["channels"] == {CH[0]: 130.0, CH[1]: 0.5, CH[2]: 20.0, CH[3]: 22.0}
    assert home.states == {CH[0]: "off", CH[1]: "off", CH[2]: "on", CH[3]: "off"}
    assert lv.load_calibration()["channels"][CH[0]] == 130.0
    text = lv.format_calibration(cal)
    assert "канал 1: +130 — видно в кадре" in text and "канал 2: +0 — света нет" in text


def test_calibration_restores_on_failure():
    home = FakeHome({CH[0]: "off", CH[1]: "off", CH[2]: "on", CH[3]: "off"}, fail_on=CH[1])
    with pytest.raises(RuntimeError):
        lv.calibrate(measure=home.measure, state=home.state, switch=home.switch, sleep=lambda s: None)
    assert home.states == {CH[0]: "off", CH[1]: "off", CH[2]: "on", CH[3]: "off"}


def test_calibration_refuses_offline_switch():
    home = FakeHome({CH[0]: "off", CH[1]: "unavailable", CH[2]: "on", CH[3]: "off"})
    with pytest.raises(RuntimeError, match="канал 2"):
        lv.calibrate(measure=home.measure, state=home.state, switch=home.switch, sleep=lambda s: None)
    assert home.calls == []  # ничего не переключали


def states(*on):
    return {e: ("on" if i + 1 in on else "off") for i, e in enumerate(CH)}


def test_assess_confirms_light():
    text = lv.assess(137.0 + 20.0, states(1, 3), CAL)
    assert "✅ Камера подтверждает: горит канал 1, канал 3." in text


def test_assess_detects_dead_lamp():
    text = lv.assess(27.0, states(1, 3), CAL)  # канал 1 «включён», но кадр как при одном канале 3
    assert "канал 1 включён, но камера этого света не видит" in text
    assert "канал 3" not in text.split("❗")[1]


def test_assess_dark_and_off():
    assert "✅ Видимый свет выключен, в кадре темно" in lv.assess(8.0, states(), CAL)


def test_assess_light_from_elsewhere():
    assert "другой источник" in lv.assess(90.0, states(), CAL)


def test_assess_hidden_and_offline_channels():
    st = states(2)
    st[CH[3]] = "unavailable"
    text = lv.assess(7.5, st, CAL)
    assert "Включено вне поля зрения камеры: канал 2" in text
    assert "Нет данных от: канал 4" in text


def test_handle_routes(monkeypatch):
    assert lv.handle("status") is None
    assert lv.handle("свет на кухне включен?") is None
    assert lv.handle("горит ли свет?") is None  # это вопрос к home_assistant
    assert "калибровка света" in lv.handle("свет по камере")  # без калибровки — подсказка
    with open(lv._calibration_path(), "w", encoding="utf-8") as fh:
        json.dump(CAL, fh)
    monkeypatch.setattr(lv, "ha_state", lambda e: states(1)[e])
    monkeypatch.setattr(lv, "measure_brightness", lambda core=None: 137.0)
    assert "горит канал 1" in lv.handle("Аргос, проверь свет камерой")
    called = {}
    monkeypatch.setattr(lv, "calibrate", lambda core=None: called.setdefault("cal", CAL))
    assert lv.handle("калибровка света").startswith("💡 Калибровка света") and called


def test_room_names_from_ha(monkeypatch):
    monkeypatch.setattr(lv, "_ha_request", lambda m, p, d=None: {
        "state": "on", "attributes": {"friendly_name": "Свет кухня"}})
    assert lv.ha_state(CH[0]) == "on"
    assert "горит кухня" in lv.assess(137.0, states(1), CAL)
