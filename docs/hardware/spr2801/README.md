# SPR2801MCB (Gyrfalcon GTI2801 + Xilinx Artix-7) — что известно и что восстановлено

Сводка собрана 2026-09-23 из журнала `AGENTS.md`, заметок Obsidian, памяти ARGOS (MemPalace)
и репозиториев GitHub после пожара 03.08.2026. Исходники Linux-shim от 26.06 не найдены ни на диске,
ни в одной ветке GitHub — ниже их точная спецификация для пересборки.

## Железо

| Параметр | Значение |
|---|---|
| Плата | SPR2801MCB, M.2 PCIe, «нейросетевая карта» |
| NPU | Gyrfalcon SPR2801S (GTI2801); узлы вендорского драйвера `/dev/gti2800-0`, `/dev/gti2803-0` |
| FPGA | Xilinx Artix-7 **XC7A35T-CSG325**, мост PCIe → XDMA |
| PCI ID | `VEN_10EE&DEV_7022&SUBSYS_28011E00&REV_00` (Windows instance `…\6&375FA39C&0&0038020B`) |
| Flash | MX25U3235 (1.8 В), дамп `spr_bios_dump_20260609_153010.bin` (4 МБ) снимался 09.06 — файл не найден на X230 |
| JTAG | пады SPR2801S — **1.8 В**. Segger J-Link на 3.3 В плату не повредил, но напряжение всегда мерить заранее |

Состояние на 10.06.2026: плата исправна, заводская прошивка enumerates (DONE горит).
На Windows XDMA падал с Code 10 — ошибка заводского битстрима (Address=0, нет MMIO), а не повреждение.
04.06 выяснено, что драйвер XDMA фактически не привязан (служба `XDMA` отсутствует; реальный INF — oem57.inf).

## Код в репозитории

- `src/connectivity/xilinx_fpga.py` — класс `XilinxFPGA`: обнаружение устройства, `dma_probe()` (чтение
  XDMA Identifier `0x1fc0xxxx` из `\control` offset 0), `dma_read(node, offset, length)` для
  `control|user|c2h_0|h2c_0`; доступно через MCP tool `xilinx_fpga` (actions `dma_test`, `dma_probe`).
  Путь к устройству сейчас Windows-specific (SetupAPI по GUID `{74c7e4a9-6d5d-4a70-bc0d-20691dff9e9d}`).
- `src/fpga_api.py` — FastAPI: `/api/fpga/status|registers|read|infer|bitstream|memory|pipeline|parameters`.
- `src/skills/fpga/` — навык ARGOS.
- `docs/hardware/spr2801/xdma_ring_trace_logger.py` — кольцевой логгер трассировки DMA/ioctl
  (backend `deque` и zero-alloc `raw`, ~60 нс на запись); восстановлен из `winargos42-dotcom/argos@main`.
- `docs/hardware/spr2801/ARGOS_XDMA_DMA_Integration_2026-06-04.md` — журнал интеграции XDMA (P1–P3).

## Linux-shim (26.06.2026) — спецификация утраченных исходников

Назначение: запускать вендорскую `libGTILibrary.so` на Linux без вендорского драйвера, направляя её
обращения к реальному NPU через стандартный Xilinx XDMA.

Устройство (по журналу сессии):
- `gti2800_xdma_shim.c` — `LD_PRELOAD`-библиотека, перехватывает `open/ioctl/mmap/close` для
  `/dev/gti2800-0` и `/dev/gti2803-0`; `mmap` — теневой буфер в RAM; запись/чтение данных —
  через `/dev/xdma0_h2c_0` (host→card), `/dev/xdma0_c2h_0` (card→host), регистры — `/dev/xdma0_user`.
- `spr2801_vendor_runner.cpp` — тестовый раннер: `GtiCreateModel` → `GtiEvaluate` на реальной модели.
- `run_vendor_shim_dryrun.sh` — прогон без железа (все обращения пишутся в лог, в устройство — ничего).
- `run_vendor_shim_live.sh` + `LIVE_CHECKLIST.md` — боевой режим, включается **только** переменной
  `SPR2801_SHIM_LIVE=I_ACCEPT_LINUX_XDMA_LIVE`.
- `tests/test_spr2801_gti_shim_artifact.py` — 3 теста артефактов.

Результат dry-run через настоящую `libGTILibrary.so`: `GtiCreateModel=ok`, `GtiEvaluate=ok`,
**17 ioctl, 1461 записей H2C (2.96 МБ), 16 чтений C2H (32 КБ)**. Live-запуск на bare-metal Linux не выполнялся.

Для пересборки нужны: `libGTILibrary.so` (вендорский SDK Gyrfalcon, официальные источники закрыты),
коды ioctl вендорского драйвера (восстанавливаются трассировкой dry-run — для этого и нужен ring logger)
и Linux-драйвер Xilinx XDMA (`dma_ip_drivers`).

## Что дальше

1. Найти копии: старый ПК «Orion»/диски, чаты ChatGPT/Codex за 26.06, Obsidian-хранилище —
   файлы `artifacts/spr2801_gti_shim/*`, `reports/spr2801_gti_shim_latest.md`, дамп flash, `libGTILibrary.so`.
2. Хост с M.2/PCIe (у X230 M.2 нет) + Linux + `xdma.ko` → `dma_probe` должен вернуть сигнатуру XDMA.
3. Пересобрать shim по спецификации выше, сначала dry-run, затем live по чек-листу.
4. Идентифицировать битстрим (Vivado HW Manager) — без карты AXI-адресов user BAR осмысленна только DMA-часть.

## Google Coral

По Google Coral (Edge TPU) данных не найдено: ни в коде и истории git, ни на GitHub (6 аккаунтов/форков),
ни в истории Codex, датасетах HF и памяти ARGOS. Устройство (USB `1a6e:089a`/`18d1:9302` или PCIe `1ac1:089a`)
к X230 не подключено.
