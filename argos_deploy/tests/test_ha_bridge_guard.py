import pytest

from src.connectivity import home_assistant as ha


class Resp:
    ok, status_code, text = True, 200, "[]"

    def json(self):
        return [{"entity_id": "switch.x", "state": "on"}]


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    token = tmp_path / "ha-token"
    token.write_text("secret-token\n")
    monkeypatch.delenv("HA_TOKEN", raising=False)
    monkeypatch.delenv("ARGOS_HA_WRITE", raising=False)
    monkeypatch.delenv("ARGOS_HA_WRITE_DENY", raising=False)
    monkeypatch.setenv("ARGOS_HA_TOKEN_FILE", str(token))
    calls = []
    monkeypatch.setattr(ha.requests, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(ha.requests, "post", lambda url, **k: calls.append(url) or Resp())
    b = ha.HomeAssistantBridge()
    b.calls = calls
    return b


def test_token_from_file_enables_read(bridge):
    assert bridge.enabled and bridge.token == "secret-token"
    assert bridge.health().startswith("✅")
    assert "switch.x = on" in bridge.list_states()


def test_services_off_by_default(bridge):
    assert "выключено" in bridge.call_service("switch", "turn_on", {"entity_id": "switch.lamp"})
    assert "выключен" in bridge.publish_mqtt("argos/x", "on")
    assert bridge.calls == []


@pytest.mark.parametrize("domain,data,why", [
    ("homeassistant", {"entity_id": "switch.lamp"}, "Домен"),
    ("switch", {"entity_id": "switch.multi_function_protector_switch"}, "Запрещено"),
    ("switch", {"entity_id": "switch.lamp,switch.multi_function_protector_switch"}, "Запрещено"),
    ("switch", {"entity_id": "all"}, "Запрещено"),
    ("switch", {}, "entity_id"),
])
def test_dangerous_calls_blocked_even_when_enabled(bridge, monkeypatch, domain, data, why):
    monkeypatch.setenv("ARGOS_HA_WRITE", "on")
    assert why in bridge.call_service(domain, "turn_off", data)
    assert bridge.calls == []


def test_allowed_call_when_enabled(bridge, monkeypatch):
    monkeypatch.setenv("ARGOS_HA_WRITE", "on")
    assert bridge.call_service("switch", "turn_on", {"entity_id": "switch.lamp"}).startswith("✅")
    assert bridge.calls == ["http://localhost:8123/api/services/switch/turn_on"]
