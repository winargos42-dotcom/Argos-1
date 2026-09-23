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

## ПОДТВЕРЖДЁННЫЙ БЛОКЕР (2026-09-24): рантайм ↔ libedgetpu

Проверено на самом узле (192.168.1.94, Debian 12, Python 3.11.2):
- ОС на SSD, `/dev/apex_0` есть, модули `apex`+`gasket`, `gasket-dkms 1.0-18`, `libedgetpu1-std 16.0` — всё стоит;
- делегат `libedgetpu.so.1` **грузится** и в `tflite_runtime`, и в `ai_edge_litert`;
- но создание интерпретатора с моделью **падает**: `tflite-runtime==2.14.0` → `SystemError`,
  `ai_edge_litert` → **segfault**. Причина: стоковый `libedgetpu` 16.0 (сборка июля 2021) несовместим
  по ABI с рантаймами для Python 3.11. Рабочая пара для 16.0 — `tflite_runtime 2.5.0`, но её колёс под
  cp311 нет (только cp39), а pip под cp311 отдаёт лишь `tflite-runtime 2.14.0`. Python 3.9, Docker и
  `pycoral` на узле отсутствуют.

**Решение (одно из):**
1. **Matched-пакеты feranick** (стандартный фикс этой проблемы): поставить на узел `libedgetpu1-std` и
   `tflite_runtime` (при желании `pycoral`) из релизов github.com/feranick — они собраны согласованно
   под Python 3.10–3.12 и новый TF. Заменяет стоковый libedgetpu, поэтому это осознанное системное
   изменение узла; после установки прогнать `coral_accel_server.py --benchmark`.
2. **Docker-контейнер Coral** — образ с уже согласованными libedgetpu + tflite; тогда сервис зрения
   запускать в контейнере с пробросом `/dev/apex_0`.

До этого сервис `argos-vision-accel` работать не будет. `coral_accel_server.py` уже пробует
`tflite_runtime → ai_edge_litert → tensorflow.lite`, так что после установки рабочей пары код менять
не придётся.
