# Coral Edge TPU на узле argos-coral

Фаза 2 плана `checks/PLAN-argos-coral-node-20260924.md`. Узел: Gigabyte B75-DS3V, i3-2120,
Coral PCIe `1ac1:089a` (один TPU), Debian 12 на SSD.

```
X230 (ARGOS, камера)  ── JPEG + HMAC ──▶  argos-coral:8770 argos-vision-accel ──▶ /dev/apex_0
      навык coral.py  ◀── JSON + HMAC ──
```

| Файл | Где | Что |
|---|---|---|
| `argos_deploy/src/vision/coral_protocol.py` | оба | подпись HMAC-SHA256 запроса и ответа, защита от повтора (30 с) |
| `argos_deploy/src/vision/coral_accel_server.py` | узел | HTTP-сервис: `/v1/health`, `/v1/status`, `/v1/detect`; `--benchmark N` |
| `argos_deploy/src/vision/coral_client.py` | X230 | клиент, `CoralUnavailable` при сбое |
| `argos_deploy/src/skills/coral.py` | X230 | «корал статус», «что видит корал», «корал лица» |
| `install-coral-debian12.sh` | узел | `--check` (по умолчанию) / `--install` |
| `fetch-models.sh` | узел | модели SSD MobileNet v2 (COCO, лица) + SHA256SUMS |

## Порядок

1. **Ключ (X230)** — один общий ключ для обеих сторон:
   `head -c 48 /dev/urandom | base64 > /etc/argos/coral.key && chmod 600 /etc/argos/coral.key`
   Владелец на X230 — пользователь сервиса ARGOS. На узел — по SSH, владелец `argos-vision`, режим 600.
2. **Узел:** `./install-coral-debian12.sh` (проверки) → `./install-coral-debian12.sh --install`.
   Скрипт ставит заголовки ровно под текущее ядро, `gasket-dkms` + `libedgetpu1-std`, udev-правило
   (группа `apex`), venv с `tflite-runtime==2.14.0` и прогоняет бенчмарк от имени сервиса.
   Если gasket не собрался — скрипт остановится; собирать DKMS из официального
   `google/gasket-driver` на закреплённом коммите, ядро не менять наугад.
3. `systemctl enable --now argos-vision-accel`; `curl http://192.168.1.93:8770/v1/health`.
4. **nftables на узле:** порт 8770/tcp только с 192.168.1.240.
5. **X230:** в окружение ARGOS добавить
   `ARGOS_CORAL_URL=http://192.168.1.93:8770` и `ARGOS_CORAL_SECRET_FILE=/etc/argos/coral.key`,
   перезапустить ARGOS, спросить «корал статус», затем «что видит корал».

## Критерии

- Бенчмарк `objects` на TPU: медиана < 20 мс (ожидаемо ~6–10 мс для SSD MobileNet v2 на PCIe).
- «что видит корал» отвечает меньше чем за 1 с вместе со снимком кадра.
- Неподписанный запрос → 401, чужой адрес → 403; кадры на диск не пишутся.

## РАБОЧАЯ СВЯЗКА (проверено на узле 192.168.1.94, 2026-09-24)

**Замер: 7.66 мс/кадр на TPU** (SSD MobileNet v2, медиана из 100), детекция верная
(на grace_hopper — «человек», «галстук»). Цель фазы 2 (<20 мс) достигнута.

Особенность узла: CPU **Intel i3-2120 (Sandy Bridge): есть AVX, нет AVX2/FMA**. Из-за этого:
- колёса `tflite_runtime`/`ai_edge_litert`, собранные с AVX2 (в т.ч. **feranick 2.17.1**), падают с
  **Illegal instruction** уже при создании интерпретатора;
- **Docker это НЕ обходит** — тот же AVX2-бинарник в контейнере упадёт так же (SIGILL — про CPU, не про ОС);
- стоковый `libedgetpu` 16.0 (Google, 2021) несовместим по ABI с рантаймами для Python 3.11
  (`tflite 2.14` → SystemError, `ai_edge_litert` → segfault).

Рабочее сочетание (только AVX-рантайм + согласованный libedgetpu):

| Компонент | Что ставить | Почему |
|---|---|---|
| Драйвер | `gasket-dkms` (Coral apt) — уже стоит | модуль apex грузится |
| libedgetpu | **feranick `libedgetpu1-std_16.0tf2.15.1-1.bookworm_amd64.deb`** | ABI под новый рантайм; ставится поверх стокового |
| Рантайм | **PyPI `tflite-runtime==2.14.0`** (не feranick!) | собран только под AVX → работает на Sandy Bridge |

```bash
# на узле, HTTPS работает (ICMP закрыт)
cd /root/coral-feranick
curl -fsSL -O https://github.com/feranick/libedgetpu/releases/download/v16.0TF2.15.1-1/libedgetpu1-std_16.0tf2.15.1-1.bookworm_amd64.deb
dpkg -i libedgetpu1-std_16.0tf2.15.1-1.bookworm_amd64.deb
python3 -m venv --system-site-packages /opt/argos-vision/venv
/opt/argos-vision/venv/bin/pip install "tflite-runtime==2.14.0"      # именно PyPI
/opt/argos-vision/venv/bin/python coral_accel_server.py --benchmark 100   # проверка ~8 мс
```

`coral_accel_server.py` пробует `tflite_runtime → ai_edge_litert → tensorflow.lite`, поэтому код
менять не нужно. Если однажды поставить процессор с AVX2 — подойдут и полностью согласованные
пакеты feranick одной версии TF.
