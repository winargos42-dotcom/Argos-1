#!/usr/bin/env python3
"""Публикует состояние ядра ARGOS и X230 в MQTT для настенной панели.

Топик argos/system/x230/status (retain): {"argos":"ok|down","cpu":..,"ram":..,"disk":..,"ts":..}
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
INTERVAL = int(os.getenv("ARGOS_STATUS_INTERVAL", "15"))


def argos_state() -> str:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
            body = json.load(resp)
        return "ok" if body.get("ok") and body.get("ready") else "down"
    except Exception:
        return "down"


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
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
