"""
home_assistant.py — Интеграция Аргоса с Home Assistant
  Поддержка:
  - REST API (states/services)
  - MQTT publish (опционально)

Токен: HA_TOKEN или файл ARGOS_HA_TOKEN_FILE (по умолчанию /etc/argos/ha-token), адрес:
HA_URL или ARGOS_HA_URL. Чтение (статус, состояния) работает сразу. Вызов сервисов и
MQTT publish — только при ARGOS_HA_WRITE=on; сущности из ARGOS_HA_WRITE_DENY
(по умолчанию реле питания switch.multi_function_protector_switch) и домены
homeassistant/hassio/script/shell_command/python_script/automation недоступны никогда.
"""

import json
import os
from typing import Any

import requests

from src.argos_logger import get_logger

log = get_logger("argos.ha")

_ON = ("1", "true", "on", "yes", "да")
_DENY_DOMAINS = {"homeassistant", "hassio", "script", "shell_command", "python_script", "automation",
                 "backup", "recorder", "system_log", "logger"}
_DEFAULT_DENY = "switch.multi_function_protector_switch"


def _read_token() -> str:
    token = os.getenv("HA_TOKEN", "").strip()
    if token:
        return token
    path = os.getenv("ARGOS_HA_TOKEN_FILE", "/etc/argos/ha-token")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


class HomeAssistantBridge:
    def __init__(self, core=None):
        self.core = core
        self.base_url = (os.getenv("HA_URL") or os.getenv("ARGOS_HA_URL") or "http://localhost:8123").rstrip("/")
        self.token = _read_token()
        self.mqtt_host = os.getenv("HA_MQTT_HOST", "localhost").strip()
        self.mqtt_port = int(os.getenv("HA_MQTT_PORT", "1883"))
        self._mqtt = None

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    @property
    def write_enabled(self) -> bool:
        return os.getenv("ARGOS_HA_WRITE", "").strip().lower() in _ON

    def _write_denied(self, domain: str, data: dict[str, Any]) -> str | None:
        if not self.write_enabled:
            return "🔒 Управление домом через «ha сервис» выключено (ARGOS_HA_WRITE=on, чтобы включить)."
        if domain.lower() in _DENY_DOMAINS:
            return f"🔒 Домен {domain} запрещён для ARGOS."
        deny = {e.strip() for e in os.getenv("ARGOS_HA_WRITE_DENY", _DEFAULT_DENY).split(",") if e.strip()}
        target = data.get("entity_id", "")
        targets = target if isinstance(target, list) else str(target).split(",")
        if not any(str(t).strip() for t in targets):
            return "🔒 Укажи entity_id: сервис без цели затронул бы все устройства."
        blocked = [t.strip() for t in map(str, targets) if t.strip() in deny or t.strip() == "all"]
        if blocked:
            return "🔒 Запрещено трогать: " + ", ".join(blocked)
        return None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def health(self) -> str:
        if not self.enabled:
            return "⚠️ Home Assistant: не настроен (нужен токен: ARGOS_HA_TOKEN_FILE или HA_TOKEN)."
        try:
            r = requests.get(f"{self.base_url}/api/", headers=self._headers(), timeout=5)
            if r.ok:
                return f"✅ Home Assistant API: {self.base_url}"
            return f"❌ Home Assistant API: HTTP {r.status_code}"
        except Exception as e:
            return f"❌ Home Assistant API: {e}"

    def list_states(self, limit: int = 20) -> str:
        if not self.enabled:
            return "⚠️ Home Assistant: не настроен."
        try:
            r = requests.get(f"{self.base_url}/api/states", headers=self._headers(), timeout=10)
            if not r.ok:
                return f"❌ HA states HTTP {r.status_code}"
            states = r.json()[: max(1, min(limit, 100))]
            lines = [f"🏠 Home Assistant состояния ({len(states)}):"]
            for s in states:
                lines.append(f"  • {s.get('entity_id','?')} = {s.get('state','?')}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ HA states: {e}"

    def call_service(self, domain: str, service: str, data: dict[str, Any] | None = None) -> str:
        if not self.enabled:
            return "⚠️ Home Assistant: не настроен."
        payload = data or {}
        denied = self._write_denied(domain, payload)
        if denied:
            log.warning("HA service отклонён: %s.%s %s", domain, service, payload)
            return denied
        try:
            r = requests.post(
                f"{self.base_url}/api/services/{domain}/{service}",
                headers=self._headers(),
                data=json.dumps(payload, ensure_ascii=False),
                timeout=10,
            )
            if r.ok:
                return f"✅ HA service: {domain}.{service}"
            return f"❌ HA service HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return f"❌ HA service: {e}"

    def _connect_mqtt(self):
        if self._mqtt:
            return True, ""
        try:
            import paho.mqtt.client as mqtt

            client = mqtt.Client()
            client.connect(self.mqtt_host, self.mqtt_port, 60)
            client.loop_start()
            self._mqtt = client
            return True, ""
        except Exception as e:
            return False, str(e)

    def publish_mqtt(self, topic: str, payload: dict[str, Any] | str) -> str:
        if not self.write_enabled:
            return "🔒 MQTT publish через ARGOS выключен (ARGOS_HA_WRITE=on, чтобы включить)."
        ok, err = self._connect_mqtt()
        if not ok:
            return f"❌ HA MQTT: {err}"
        try:
            msg = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            self._mqtt.publish(topic, msg)
            return f"✅ HA MQTT → {topic}: {str(msg)[:80]}"
        except Exception as e:
            return f"❌ HA MQTT publish: {e}"
