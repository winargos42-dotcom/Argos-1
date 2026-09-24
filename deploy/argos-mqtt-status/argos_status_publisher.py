#!/usr/bin/env python3
"""Публикует состояние ядра ARGOS и X230 в MQTT для настенной панели.

Топик argos/system/x230/status (retain): {"argos":"ok|down","cpu":..,"ram":..,"disk":..,"ts":..}
Топик argos/system/coral/status (retain), если задан ARGOS_CORAL_URL:
  {"coral":"online|offline","latency_ms":..,"ts":..}
"""
import json
import os
import time
import urllib.request

import paho.mqtt.client as mqtt
import psutil

BROKER = os.getenv("ARGOS_MQTT_HOST", "127.0.0.1")
USER = os.getenv("ARGOS_MQTT_USER", "argos")
PASS_FILE = os.getenv("ARGOS_MQTT_PASS_FILE", "/etc/argos/mqtt-argos.pass")
HEALTH_URL = os.getenv("ARGOS_HEALTH_URL", "http://127.0.0.1:8080/health")
TOPIC = "argos/system/x230/status"
CORAL_URL = os.getenv("ARGOS_CORAL_URL", "").strip().rstrip("/")
TOPIC_CORAL = "argos/system/coral/status"
INTERVAL = int(os.getenv("ARGOS_STATUS_INTERVAL", "15"))


def argos_state() -> str:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
            body = json.load(resp)
        return "ok" if body.get("ok") and body.get("ready") else "down"
    except Exception:
        return "down"


def coral_snapshot() -> dict | None:
    """Пингует /v1/health узла Coral (без подписи, из LAN). None, если узел не настроен."""
    if not CORAL_URL:
        return None
    started = time.monotonic()
    try:
        with urllib.request.urlopen(CORAL_URL + "/v1/health", timeout=3) as resp:
            ok = json.load(resp).get("ok") is True
        latency = round((time.monotonic() - started) * 1000)
        return {"coral": "online" if ok else "offline", "latency_ms": latency, "ts": int(time.time())}
    except Exception:
        return {"coral": "offline", "ts": int(time.time())}


def snapshot() -> dict:
    return {
        "argos": argos_state(),
        "cpu": round(psutil.cpu_percent(interval=1)),
        "ram": round(psutil.virtual_memory().percent),
        "disk": round(psutil.disk_usage("/").percent),
        "ts": int(time.time()),
    }


def main() -> None:
    with open(PASS_FILE, encoding="utf-8") as f:
        password = f.read().strip()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="argos-x230-status")
    client.username_pw_set(USER, password)
    client.will_set(TOPIC, json.dumps({"argos": "down"}), qos=1, retain=True)
    client.connect_async(BROKER, 1883, keepalive=60)
    client.loop_start()
    while True:
        if client.is_connected():
            client.publish(TOPIC, json.dumps(snapshot()), qos=0, retain=True)
            coral = coral_snapshot()
            if coral is not None:
                client.publish(TOPIC_CORAL, json.dumps(coral), qos=0, retain=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
