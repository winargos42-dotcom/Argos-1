#!/usr/bin/env bash
# ARGOS home-contour bring-up: first diagnostic pass on the X230.
#
# Run this ON THE PHYSICAL X230 (not in any CI/cloud sandbox), as the
# normal user is fine for most of it — only a handful of steps (docker,
# reading serial devices) may need root/group membership already set up
# by scripts/workstation-setup-x230.sh.
#
# Goal: find out, in one pass, what's already there before doing anything
# with the ESP32-S3-Touch-LCD-7, the Tuya protector, or Home Assistant:
#   - is an ESP32 attached, and on which /dev/ttyACM*|ttyUSB*
#   - is a Home Assistant / MQTT broker already running (do NOT stand up
#     a second HA instance if one already exists)
#   - is Docker available and what containers already exist
#   - is the Redmi phone visible over ADB
#   - is esptool available to talk to the ESP32
#
# Nothing here touches the household wiring/breaker box. Low-voltage only.

set -u

echo "===== SYSTEM ====="
uname -a
df -h / /home

echo
echo "===== USB ====="
lsusb

echo
echo "===== SERIAL ====="
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || echo "(no /dev/ttyACM* or /dev/ttyUSB* found)"

echo
echo "===== NETWORK ====="
ip -br a

echo
echo "===== HOME ASSISTANT / MQTT ====="
ss -lntp | grep -E ':8123|:1883' || echo "(nothing listening on 8123/1883)"

echo
echo "===== DOCKER ====="
docker ps -a 2>/dev/null || echo "(docker not available or not running)"

echo
echo "===== ADB ====="
adb devices -l 2>/dev/null || echo "(adb not installed)"

echo
echo "===== ESPTOOL ====="
esptool version 2>/dev/null || esptool.py version 2>/dev/null || echo "(esptool not installed)"
