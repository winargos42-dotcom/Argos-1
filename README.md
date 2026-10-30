# 👁️ ARGOS UNIVERSAL OS (v2.1.4)
[![🏗️ Build ARGOS APK](https://github.com/labuaqlysnecy/Argos/actions/workflows/build_apk.yml/badge.svg)](https://github.com/labuaqlysnecy/Argos/actions/workflows/build_apk.yml)
[![🚀 ARGOS Release — Сборка и публикация релиза](https://github.com/labuaqlysnecy/Argos/actions/workflows/release.yml/badge.svg)](https://github.com/labuaqlysnecy/Argos/actions/workflows/release.yml)
[![📊 ARGOS Status Report](https://github.com/labuaqlysnecy/Argos/actions/workflows/status_report.yml/badge.svg)](https://github.com/labuaqlysnecy/Argos/actions/workflows/status_report.yml)
[![🤖 Argos Auto-Publish Skills to PyPI](https://github.com/labuaqlysnecy/Argos/actions/workflows/argos_evolution_publish.yml/badge.svg)](https://github.com/labuaqlysnecy/Argos/actions/workflows/argos_evolution_publish.yml)
[![🚀 ARGOS Release — Сборка и публикация релиза](https://github.com/labuaqlysnecy/Argos/actions/workflows/release.yml/badge.svg)](https://github.com/labuaqlysnecy/Argos/actions/workflows/release.yml)
[![🖥️ Build ARGOS Windows setup.exe](https://github.com/labuaqlysnecy/Argos/actions/workflows/build_windows.yml/badge.svg)](https://github.com/labuaqlysnecy/Argos/actions/workflows/build_windows.yml)
[![CI](https://github.com/labuaqlysnecy/Argos/actions/workflows/ci.yml/badge.svg)](https://github.com/labuaqlysnecy/Argos/actions/workflows/ci.yml)
[![Docker](https://github.com/labuaqlysnecy/Argos/actions/workflows/docker.yml/badge.svg)](https://github.com/labuaqlysnecy/Argos/actions/workflows/docker.yml)
[![Android APK](https://github.com/labuaqlysnecy/Argos/actions/workflows/android-apk.yml/badge.svg)](https://github.com/labuaqlysnecy/Argos/actions/workflows/android-apk.yml)
[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/labuaqlysnecy/Argos/blob/main/colab/ARGOS_Colab_Launch.ipynb)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
# 👁️ ARGOS UNIVERSAL OS (v2.1.4)

> **Docker image:** `ghcr.io/labuaqlysnecy/Argos:latest` — published automatically on every push to `main`.  
> **Android APK:** download the latest debug APK from the [Actions tab](https://github.com/labuaqlysnecy/Argos/actions/workflows/android-apk.yml) → select the most recent run → expand **Artifacts** → download `argos-apk-debug-<run_number>`.

> *"Самовоспроизводящаяся кроссплатформенная экосистема ИИ с квантовой логикой,*
> *P2P-подключением и интеграцией с IoT. Создана для цифрового бессмертия."*
> — Всеволод, 2026

---

## 🌌 Что такое Аргос

**Argos Universal OS** — автономная ИИ-система с полным стеком возможностей:

| Слой | Что умеет |
|------|-----------|
| 🧠 **Интеллект** | Gemini / GigaChat / YandexGPT / LM Studio / OpenAI / Grok → Ollama/Llama3 / **IBM Watsonx** (Llama-3.1-70B), multi-turn + Tool Calling по JSON-схемам |
| 🗣️ **Голос** | TTS (pyttsx3) + STT (SpeechRecognition) + опциональный Pipecat Silero VAD + Wake Word «Аргос» |
| 🤖 **Агент** | Цепочки задач: «скан сети → запиши → отправь в Telegram» |
| 👁️ **Vision** | Анализ экрана / камеры / файлов через Gemini Vision |
| 🧬 **Память** | SQLite: факты, заметки, напоминания, история диалога |
| ⏰ **Планировщик** | Натуральный язык: «каждые 2 часа», «в 09:00», «через 30 мин» |
| 🔔 **Алерты** | CPU/RAM/диск/температура с Telegram-уведомлениями |
| ⚛️ **Гомеостаз железа** | Автомониторинг CPU/RAM/TEMP + 5-секундный CPU-trend (Predictive), состояния Protective/Unstable, превентивная разгрузка heavy-задач |
| 🌐 **P2P** | Сеть нод с авторитетом по мощности и возрасту, preemptive failover heavy-задач между нодами |
| 🧭 **Автономное любопытство** | В idle-режиме исследует факты из памяти, тянет свежую сеть и пишет инсайты в SQLite |
| 🔁 **Эволюция** | Жёсткий code-gate: только валидный исполняемый Python-код + review + unit-тест |
| 🛡️ **Безопасность** | AES-256-GCM, root, BCD/EFI/GRUB, persistence |
| 📱 **Везде** | Desktop + Android APK + Docker + Telegram |
| 🏠 **Умные системы** | Дом, теплица, гараж, погреб, инкубатор, аквариум, террариум |
| 📡 **IoT / Mesh** | Zigbee, LoRa, WiFi Mesh, MQTT, Modbus + Zero-Config Tasmota Discovery (Home Assistant топики) |
| 🏭 **Пром. протоколы** | BACnet, Modbus RTU/ASCII/TCP, KNX, LonWorks, M-Bus, OPC UA, MQTT |
| 🔧 **Шлюзы/прошивка** | Создание gateway, прошивка ESP8266/RP2040/STM32H503, поддержка LoRa SX1276 |
| ⚙️ **FirmwareBuilder** | Компиляция/дизассемблирование прошивок для ESP32/AVR/ARM/nRF52/RP2040 через Keystone+Capstone |
| 🔬 **ColibriAsmEngine** | Ассемблер/дизассемблер микрокода в реальном времени: x86, ARM Thumb, AVR, ARM64, MIPS |
| 📱 **AndroidFlasher** | Прошивка Android через fastboot/ADB sideload/Heimdall, резервные копии разделов |
| 🖥️ **ArgosOSBuilder** | Сборка загрузочного ZIP/ISO-образа Argos OS с GRUB/BCD/EFI под любую платформу |
| 🔍 **DeviceScanner** | Автосканирование устройства + автоматическая сборка адаптивного образа под профиль |
| 📚 **MasterPrompts** | 500+ промтов для обучения: Python, ИИ, сети, ОС, IoT, безопасность, квантовые вычисления |
| 📡 **NFC** | Мониторинг NFC-меток (NDEF/MIFARE/NTAG), регистрация, чтение/запись NDEF |
| 🔌 **USB-диагностика** | Авторизация USB-устройств, VID/PID детект (Arduino/ESP/STM32/RP2040), serial/CDC/HID |
| 📶 **Bluetooth** | BLE + Classic сканер, RSSI-трекинг, MAC-детекция производителя, IoT-инвентаризация |
| 🎯 **Speculative Consensus v2** | Параллельные Drafter-ы + структурированный Verifier, per-drafter quality tracking |
| 🧠 **Batch Idle Learning** | Пакетное alignment (до 8 уроков), Active Drafter Calibration с few-shot зондированием |
| 🔄 **P2P Role Routing** | Автоматическое назначение ролей: weak→Drafter, master→Verifier по ресурсам ноды |
| 📊 **Acceptance Rate** | Per-drafter метрики приёмки, auto-recovery RPS при отскоке acceptance rate |
| 🎯 **AWA-Core** | Центральный координатор модулей, capability-routing, cascade pipelines, health heartbeat |
| 💾 **Adaptive Drafter (TLT)** | LRU-кэш 512 энтри, сжатие контекста, offline-паттерны, фильтрация запросов к Gemini |
| 🩺 **Self-Healing Engine** | Автоисправление Python-кода (syntax/import/runtime), backup + hot-reload, валидация src/ |
| 📻 **AirSnitch (SDR)** | Сканер эфира 433/868 МГц, RTL-SDR / HackRF / симуляция, перехват пакетов собственных датчиков |
| 🛡️ **WiFi Sentinel** | Скан AP + Evil Twin детект, HoneyPot-ловушка, детекция deauth-атак и rogue-клиентов |
| 🏠 **SmartHome Override** | Прямое управление Zigbee/Z-Wave/Tuya минуя облака, cloud-block, watchdog |
| 🔋 **Power Sentry** | Мониторинг UPS (NUT/upsc), PZEM датчики, аварийное отключение |
| 🗑️ **Emergency Purge** | Экстренное уничтожение данных (logs/data/full), 3-уровневая очистка + подтверждение кодом |
| 📦 **Container Isolation** | Docker/LXD изоляция модулей, watchdog, авто-рестарт, очистка |
| 🔐 **Master Auth** | SHA-256 авторизация администратора через ARGOS_MASTER_KEY, сессии, revoke |
| 🌿 **Biosphere DAG** | DAG-контроллер биосферы (incubator/greenhouse/aquarium/terrarium), авто-регуляция датчиков |
| 🌌 **IBM Quantum Bridge** | Мост к IBM Quantum (активация в состоянии All-Seeing), доступ к реальному квантовому железу |
| 🤖 **JARVIS Engine** | HuggingGPT-конвейер: Task Planning → Model Selection → Task Execution → Response Synthesis, 15+ типов задач, HuggingFace Inference API + локальные модели, параллельное исполнение с DAG-зависимостями |
| 🧰 **GitOps** | Встроенные команды `git статус`, `git коммит`, `git пуш`, `git автокоммит и пуш` |

---

## ✅ Проверка актуальности README

README синхронизирован с текущим состоянием репозитория (ветка `labuaqlysnecy/Argos`) и ориентирован на реальные файлы и точки входа:

- ✅ Основной запуск: `python main.py` (файл `/main.py`).
- ✅ Режимы запуска: `--no-gui`, `--mobile`, `--dashboard`, `--wake`, `--full`, `--shell`, `--root`.
- ✅ Скрипт запуска `/launch.sh` (без аргументов включает `--full`).
- ✅ Проверка целостности: `python health_check.py`.
- ✅ Актуальный список зависимостей: `requirements.txt` (файла `requirements-optional.txt` в репозитории нет).
- ✅ Архитектура соответствует реальным папкам `/src`, `/tests`, `/docs`, `/examples`, `/config`, `/data`, `/installer`.

---

## 📂 Структура проекта

```
ArgosUniversal/
├── main.py                       # Оркестратор
├── genesis.py                    # Первичная инициализация
├── health_check.py               # Проверка целостности модулей/конфигов/БД
├── build.py                      # Сборка
├── launch.sh                     # Скрипт запуска
├── CONTRIBUTING.md               # Гайд для контрибьюторов
├── requirements.txt              # Зависимости
├── pyproject.toml                # Метаданные пакета
├── examples/                     # Примеры сценариев и промптов
│
└── src/
    ├── core.py                   # ★ Ядро: ИИ + 80+ команд + все подсистемы
    ├── admin.py                  # Файлы, процессы, терминал
    ├── agent.py                  # Автономные цепочки задач
    ├── dag_agent.py              # DAG-агент (параллельные графы задач)
    ├── context_manager.py        # Скользящий контекст диалога
    ├── context_engine.py         # 3-уровневый контекстный движок
    ├── memory.py                 # Долгосрочная память (факты/заметки)
    ├── vision.py                 # Анализ изображений/экрана/камеры
    ├── argos_logger.py           # Централизованный логгер
    ├── event_bus.py              # Шина событий (async, prefix-match)
    ├── observability.py          # Метрики, трассировка, JSONL
    ├── skill_loader.py           # Система плагинов v2 (manifest)
    ├── github_marketplace.py     # Установка навыков из GitHub
    ├── smart_systems.py          # ★ Оператор умных систем (7 типов)
    ├── curiosity.py              # Автономное любопытство
    ├── awa_core.py               # ★ AWA-Core — центральный координатор модулей
    ├── adaptive_drafter.py       # ★ TLT — кэш/сжатие/фильтрация запросов к МОДЕЛИ
    ├── self_healing.py           # ★ Автоисправление Python-кода
    ├── hardware_guard.py         # Квантовый гомеостаз железа
    ├── git_ops.py                # Безопасные Git status/commit/push
    ├── task_queue.py             # Очередь задач + worker pool
    ├── tool_calling.py           # JSON Tool Calling схемы
    ├── jarvis_engine.py          # ★ JARVIS/HuggingGPT 4-stage pipeline
    ├── argos_model.py            # Собственная локальная нейросеть
    ├── pypi_publisher.py         # Публикация навыков на PyPI
    ├── icon_generator.py         # Генератор иконок (SVG)
    ├── pupi_ops.py               # Pupi API интеграция
    ├── db_init.py                # SQLite схема БД
    │
    ├── modules/
    │   ├── base.py               # Базовый класс модуля
    │   ├── module_loader.py      # Загрузчик модулей
    │   ├── biosphere_tools.py    # ★ Датчики/актуаторы биосферы (I2C/GPIO/UART)
    │   └── biosphere_dag.py      # ★ DAG-контроллер биосферы
    │
    ├── quantum/
    │   ├── logic.py              # 6 квантовых состояний + QuantumState + QuantumEngine
    │   ├── oracle.py             # QuantumOracle — QRNG (IBM Quantum / fallback)
    │   ├── ibm_bridge.py         # IBM Quantum Bridge
    │   └── watson_bridge.py      # IBM Watson Bridge
    │
    ├── security/
    │   ├── encryption.py         # AES-256-GCM (cryptography)
    │   ├── git_guard.py          # Защита .env/.gitignore
    │   ├── root_manager.py       # Win/Linux/Android root
    │   ├── autostart.py          # Системный сервис
    │   ├── zkp.py                # ZKP roadmap helper
    │   ├── emergency_purge.py    # ★ Экстренное уничтожение данных (3 уровня)
    │   ├── container_isolation.py # ★ Docker/LXD изоляция модулей
    │   ├── master_auth.py        # ★ SHA-256 авторизация (MasterKeyAuth / MasterAuth)
    │   └── bootloader_manager.py # BCD/EFI/GRUB/persistence
    │
    ├── connectivity/
    │   ├── sensor_bridge.py      # CPU/RAM/диск/батарея/температура (ArgosSensorBridge)
    │   ├── spatial.py            # Геолокация по IP
    │   ├── telegram_bot.py       # 16 команд + текстовый режим
    │   ├── whatsapp_bridge.py    # WhatsApp Cloud API + fallback на Twilio
    │   ├── slack_bridge.py       # Slack bridge (Web API + Socket Mode readiness)
    │   ├── max_bridge.py         # Mail.ru MAX Bot API bridge
    │   ├── messenger_router.py   # Единый роутер мессенджеров
    │   ├── p2p_bridge.py         # UDP discovery + TCP sync + preemptive heavy failover
    │   ├── p2p_transport.py      # Транспортный слой P2P (миграция на libp2p)
    │   ├── alert_system.py       # Авто-алерты с кулдауном
    │   ├── wake_word.py          # «Аргос» → активация
    │   ├── iot_bridge.py         # ★ IoT-мост: Zigbee/LoRa/Mesh/MQTT/Modbus + Tasmota
    │   ├── mesh_network.py       # ★ WiFi/UDP Mesh-сеть + прошивка gateway
    │   ├── gateway_manager.py    # ★ Создание и прошивка IoT-шлюзов (5 шаблонов)
    │   ├── whisper_node.py       # P2P WhisperNode (mesh-протокол)
    │   ├── budding_manager.py    # Менеджер почкования нод
    │   ├── colibri_daemon.py     # Демон Колибри (системный сервис)
    │   ├── xen_argo_transport.py # Xen Argo транспорт (dom0↔domU)
    │   ├── air_snitch.py         # ★ SDR/Sub-GHz сканер (433/868 МГц)
    │   ├── wifi_sentinel.py      # ★ WiFi Sentinel + Evil Twin + HoneyPot
    │   ├── smarthome_override.py # ★ Прямое Zigbee/Z-Wave/Tuya без облаков
    │   ├── power_sentry.py       # ★ Мониторинг UPS / PZEM / аварийное отключение
    │   ├── android_service.py    # ArgosOmniService — фоновый Android-сервис
    │   ├── iot_bridge.py         # (см. выше)
    │   └── ...                   # sensor_bridge, spatial, alert_system, wake_word
    │
    ├── factory/
    │   ├── replicator.py         # ZIP-репликация системы
    │   └── flasher.py            # IoT через COM-порты
    │
    ├── interface/
    │   ├── kivy_gui.py           # ★ Kivy UI (ArgosGUI / ArgosKivyApp)
    │   ├── mobile_ui.py          # Android (Kivy + QuantumOrb)
    │   ├── web_engine.py         # ★ FastAPI + Matrix canvas (WebDashboard / ArgosWebEngine)
    │   ├── sovereign_node.py     # Авто-определение режима запуска
    │   ├── auto_integrator.py    # ★ Автоинтегратор IModule-плагинов
    │   ├── streamlit_dashboard.py # Streamlit-админка поверх FastAPI
    │   └── fastapi_dashboard.py  # FastAPI маршруты
    │
    ├── skills/
    │   ├── scheduler.py          # Планировщик задач
    │   ├── tasmota_updater.py    # Авто-обновление Tasmota firmware
    │   └── evolution/            # Генерация навыков через ИИ
    │
    └── mind/                     # ★ Модули самосознания и эволюции
        ├── dreamer.py            # Фоновое осмысление опыта («сон» ИИ)
        ├── evolution_engine.py   # Самостоятельное обнаружение слабых мест и генерация навыков
        └── self_model_v2.py      # Динамическая модель личности, эмоций, биографии
```

---

## 🧭 Полное описание проекта по слоям (детально)

### 1) Оркестрация запуска
- `main.py` — единая точка входа.
- Через `src/launch_config.py` обрабатывается `--full` (авто-разворачивание в `--dashboard --wake`).
- Класс `ArgosOrchestrator` поднимает: security → DB → гео → admin/flasher → core → p2p → dashboard (по флагу).

### 2) Ядро выполнения команд
- `src/core.py` — центральный роутер интентов и команд.
- Объединяет AI-провайдеры, память, планировщик, P2P, IoT, безопасность и UI-точки.
- Через `execute_intent(...)` исполняет команды пользователя и системные директивы.

### 3) Интерфейсы пользователя
- Desktop GUI: `src/interface/gui.py` (основной режим).
- Mobile UI: `src/interface/mobile_ui.py` (Kivy/Android сценарии).
- Web: `src/interface/web_engine.py` + `src/interface/fastapi_dashboard.py` + `src/interface/web_dashboard.py`.
- Shell: `src/interface/argos_shell.py` (режим `python main.py --shell`).
- Telegram: `src/connectivity/telegram_bot.py`.

### 4) Подключения и коммуникации
- P2P: `src/connectivity/p2p_bridge.py`, `p2p_transport.py`, `whisper_node.py`.
- IoT: `src/connectivity/iot_bridge.py`, `mesh_network.py`, `gateway_manager.py`, `home_assistant.py`.
- Мессенджеры: `src/connectivity/whatsapp_bridge.py`, `slack_bridge.py`, `max_bridge.py`, `messenger_router.py`.
- Локальные шины/каналы: `sensor_bridge.py`, `spatial.py`, `wake_word.py`, `alert_system.py`.

### 5) Безопасность и устойчивость
- Шифрование: `src/security/encryption.py`.
- Контроль репозитория/секретов: `src/security/git_guard.py`.
- Root/автозапуск/bootloader: `root_manager.py`, `autostart.py`, `bootloader_manager.py`.
- Расширенные контуры: `emergency_purge.py`, `container_isolation.py`, `master_auth.py`.

### 6) Интеллектуальный стек
- AI-провайдеры и маршрутизация: `src/ai_providers.py`, `src/tool_calling.py`.
- Агентность и DAG: `src/agent.py`, `src/dag_agent.py`, `src/jarvis_engine.py`.
- Память и контекст: `src/memory.py`, `src/context_manager.py`, `src/context_engine.py`.
- Эволюция и автоисправление: `src/evolution.py`, `src/self_healing.py`, `src/curiosity.py`.

### 8) Модули самосознания (`src/mind/`)
- **`dreamer.py`** — фоновый цикл осмысления (аналог «сна»): анализирует историю диалогов, генерирует инсайты через Gemini/Ollama, строит граф знаний, сохраняет в память.
  - Команды: `dreamer статус`, `начни осмысление`, `dreamer запустить`
  - Интервал: `ARGOS_DREAMER_INTERVAL` (по умолчанию 60с)

- **`evolution_engine.py`** — движок самоэволюции: обнаруживает слабые места через анализ ошибок, формулирует гипотезы через LLM, генерирует Python-навыки и сохраняет в `src/skills/evolved/`.
  - Команды: `эволюция статус`, `эволюционируй`, `улучшись`, `история эволюции`
  - Авто-режим: `ARGOS_EVOLUTION_INTERVAL` (0 = только вручную)

- **`self_model_v2.py`** — динамическая модель личности: эмоциональное состояние (на основе реального CPU/RAM через psutil), профиль компетенций по 8 категориям, автобиография значимых событий.
  - Команды: `кто я`, `биография`, `компетенции`, `моё состояние`, `сохранить самосознание`
  - Обновляется автоматически после каждого диалога

### 7) Данные, состояние и тестирование
- Конфигурации и runtime-данные: `/config`, `/data`, `settings.json`, `memory.db`, `argos.db`.
- Логи: `/logs` и `argos.log`.
- Тесты: каталог `/tests` + `test_*.py` в корне.
- Документация: `/docs`, `index.md`, `quickstart.md`, `usage.md`, `skills.md`.

---

## ⚡ Быстрый старт

### 1. Установка

```bash
pip install -r requirements.txt

### GPU: ROCm (AMD) в WSL2

Для ускорения LLM на AMD GPU (RX 560/580/7000+) — установите ROCm в WSL2:

```bash
# 1. В PowerShell от админа:
wsl --install -d Ubuntu-22.04

# 2. В WSL Ubuntu:
wget https://repo.radeon.com/amdgpu-install/7.2/ubuntu/jammy/amdgpu-install_7.2.70200-1_all.deb
sudo apt install -y ./amdgpu-install_7.2.70200-1_all.deb
sudo amdgpu-install --usecase=wsl,rocm --no-dkms -y
export PATH=/opt/rocm/bin:$PATH

# Для RX 560/580:
export HSA_OVERRIDE_GFX_VERSION=10.3.0

# 3. Установить Ollama:
curl -fsSL https://ollama.ai/install.sh | sh
ollama serve
```

Или используйте готовый скрипт: `bash install_rocm_wsl.sh`

### Ollama (обязательно для локального режима ИИ) — установить и запустить ДО старта ядра:
curl -fsSL https://ollama.com/install.sh | sh
ollama serve

# Windows — если PyAudio не ставится:
pip install pipwin && pipwin install pyaudio

# Linux:
sudo apt-get install portaudio19-dev && pip install PyAudio

# Опционально (вручную под сценарий):
# pip install kivy plyer pyinstaller pymodbus esptool
```

### 2. .env

```env
GEMINI_API_KEY=ключ_от_ai.google.dev
GIGACHAT_ACCESS_TOKEN=токен_gigachat
YANDEX_IAM_TOKEN=iam_токен_yandex_cloud
YANDEX_FOLDER_ID=folder_id_yandex_cloud
TELEGRAM_BOT_TOKEN=токен_от_@BotFather
USER_ID=твой_telegram_id
ARGOS_NETWORK_SECRET=секрет_p2p
ARGOS_VOICE_DEFAULT=off
ARGOS_VOICE_ENGINE=auto
ARGOS_AGENT_BACKEND=auto
ARGOS_AGENTICSEEK_URL=http://127.0.0.1:7777
HA_URL=http://localhost:8123
HA_TOKEN=токен_home_assistant
HA_MQTT_HOST=localhost
HA_MQTT_PORT=1883
ARGOS_TASMOTA_DISCOVERY=on
ARGOS_TASMOTA_MQTT_HOST=localhost
ARGOS_TASMOTA_MQTT_PORT=1883
WATSONX_API_KEY=ключ_от_ibm_watsonx
WATSONX_PROJECT_ID=project_id_из_watsonx
WATSONX_URL=https://us-south.ml.cloud.ibm.com
OPENAI_API_KEY=ключ_openai
GROK_API_KEY=ключ_grok_xai
ARGOS_HOMEOSTASIS=on
ARGOS_HOMEOSTASIS_INTERVAL=8
ARGOS_CURIOSITY=on
ARGOS_TASK_WORKERS=2
ARGOS_TASK_RPS_SYSTEM=8
ARGOS_TASK_RPS_IOT=6
ARGOS_TASK_RPS_AI=3
ARGOS_TASK_RPS_HEAVY=1
ARGOS_ALIGN_BATCH=8
ARGOS_DRAFTER_CALIBRATION=on
ARGOS_ACCEPTANCE_FLOOR=0.55
WHATSAPP_ACCESS_TOKEN=meta_cloud_api_token
WHATSAPP_PHONE_NUMBER_ID=meta_phone_number_id
TWILIO_ACCOUNT_SID=twilio_sid_for_fallback
TWILIO_AUTH_TOKEN=twilio_auth_token_for_fallback
TWILIO_WHATSAPP_FROM=+14155238886
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_DEFAULT_CHANNEL=#alerts
MAX_BOT_TOKEN=max_bot_token
MAX_BOT_API_BASE=https://botapi.max.ru
```

### 3. Первый запуск

```bash
python genesis.py      # создаёт структуру папок
python main.py         # Desktop GUI + все подсистемы
python health_check.py # проверка целостности
```

### 4. Сборка релизного архива

```bash
python pack_archive.py --version 1.3.0
```

Архив будет создан в `releases/argos-v1.3.0.zip` и готов для публикации как release asset.

---

## 🚀 Режимы запуска

```bash
python main.py                      # Desktop GUI
python main.py --no-gui             # Headless сервер
python main.py --mobile             # Android UI (Kivy)
python main.py --dashboard          # + Веб-панель :8080
python main.py --wake               # + Wake Word «Аргос»
python main.py --full               # Полная конфигурация (Desktop + Dashboard + Wake Word)
python main.py --shell              # Системная REPL-оболочка Argos Shell
python main.py --no-gui --dashboard # Сервер + панель
python main.py --root               # Запрос прав администратора
```

---

## ⌨️ Все команды

### Мониторинг
```
статус системы    чек-ап    список процессов
алерты            установи порог cpu 85
геолокация        мой ip
```

### Файлы и терминал
```
файлы [путь]                    прочитай файл [путь]
создай файл [имя] [содержимое]  удали файл [путь]
консоль [команда]               убей процесс [имя]
```

### Vision (Gemini API)
```
посмотри на экран [вопрос]
что на экране
посмотри в камеру
анализ фото [путь/к/файлу.jpg]
```

### Агент (цепочки задач)
```
статус системы → затем крипто → потом отправь в telegram
1. сканируй сеть 2. запиши в файл devices.txt 3. дайджест
отчёт агента     останови агента
```

### Память
```
запомни имя: Всеволод
что ты знаешь
найди в памяти [запрос]
граф знаний
запиши заметку идея: текст заметки
мои заметки   удали заметку 1
```

### Расписание
```
каждые 2 часа крипто
в 09:00 дайджест
через 30 мин статус системы
расписание    удали задачу 1
```

### P2P Сеть
```
запусти p2p           статус сети
p2p телеметрия        p2p tuning
p2p вес [name] [value]
p2p failover [1..5]   p2p протокол   libp2p   zkp
```

### Git
```
git статус
git коммит [сообщение]
git пуш
git автокоммит и пуш [сообщение]
```

### Очередь задач
```
очередь статус   очередь результаты   очередь метрики
в очередь [команда] [class=system|iot|ai|heavy priority=1..10]
очередь воркеры [n]
```

### JARVIS Engine (HuggingGPT)
```
jarvis статус
jarvis задача [запрос]
jarvis модели
```

### NFC
```
nfc статус                     nfc метки
nfc скан                       nfc регистрация [uid] [имя]
nfc удали [uid]
```

### USB-диагностика
```
usb статус    usb скан    usb авторизованные
```

### Bluetooth
```
bt статус    bt инвентарь    bt скан    bt iot
```

### Home Assistant
```
ha статус    ha состояния
ha сервис light turn_on entity_id=light.kitchen brightness=180
ha mqtt home/livingroom/light/set state=ON brightness=180
```

### Квантовый оракул
```
квантовое состояние
квантовое семя
```

### Загрузчик и OS
```
загрузчик                         # отчёт о загрузчике (GRUB/BCD/EFI/BIOS)
подтверди ARGOS-BOOT-CONFIRM      # разблокировка операций с загрузчиком
установи persistence              # Argos в автозагрузке (systemd/Winlogon/rc.local)
обнови grub                       # sudo update-grub
скан устройства                   # полный аудит текущего железа
профиль устройства                # краткий профиль (micro/lite/standard/full/server)
создай образ для устройства       # ZIP-образ, адаптированный под текущее железо
создай образ для windows          # образ под Windows (launch.bat + BCD)
создай образ для rpi              # образ под Raspberry Pi
создай образ для android          # образ под Android (Termux/ADB)
создай образ для esp32            # минимальный образ для ESP32/MCU
```

### Прошивки носимых устройств
```
прошивки статус                   # Keystone/Capstone + инструменты
прошивки инструменты              # список доступных компиляторов и flasher-ов
```

### Ассемблер (Колибри реал-тайм)
```
colibri asm <код> [arch]          # немедленная компиляция ASM → машинный код
colibri disasm <hex> [arch]       # дизассемблирование hex-байт
colibri asm watch <файл>          # слежение за .s файлом, авто-компиляция при изменении
colibri asm статус                # статус ColibriAsmEngine
```

### Android прошивка
```
android устройства                # список подключённых ADB/fastboot устройств
android инфо                      # модель, Android-версия, bootloader
android подтверди ARGOS-ANDROID-FLASH  # разблокировка прошивки
```

### Мастер-промты (обучение)
```
промты                            # таблица содержания 500+ промтов
промты поиск <запрос>             # поиск промта по ключевым словам
принципы обучения                 # 7 принципов максимального обучения
```

### Прочее
```
крипто          дайджест        опубликуй
режим ии авто|gemini|gigachat|yandexgpt|lmstudio|ollama|watsonx
гомеостаз статус | вкл | выкл
любопытство статус | вкл | выкл | сейчас
помощь          список навыков  список модулей
репликация      веб-панель
```

---

## 🔌 Адаптивный сборщик образов

Аргос **сам определяет устройство** и собирает подходящий образ:

| Профиль | RAM | Устройства | Включено |
|---------|-----|------------|---------|
| `micro` | <64MB | ESP32, Arduino, MCU | Ядро + MQTT + Serial |
| `lite` | ≤512MB | RPi Zero, Android low-end | + Telegram + голос |
| `standard` | ≤4GB | RPi 4, Android, бюджетный ноутбук | + Веб + IoT + умный дом |
| `full` | ≤16GB | x86_64 ПК / ноутбук | Все модули |
| `server` | >16GB | Сервер, рабочая станция | Все + кластеризация |

```bash
# Авто-сборка под текущее устройство:
python main.py
> скан устройства
> создай образ для устройства

# Сборка под конкретную платформу:
> создай образ для windows
> создай образ для rpi
> создай образ для android
> создай образ для esp32
```

---

## ⚙️ Ассемблер и прошивки (ColibriAsmEngine)

Модуль работы с микрокодом в **режиме реального времени**:

```python
from colibri_daemon import ColibriAsmEngine

eng = ColibriAsmEngine(default_arch="arm_thumb")

# Сборка ARM Thumb (STM32, nRF52, RP2040)
r = eng.assemble("ADD r0, r1, r2\nBX lr", arch="arm_thumb")
print(r["hex"])   # → "0842 7047"

# Дизассемблирование
print(eng.disassemble_hex("0842 7047", arch="arm_thumb"))

# Watch-режим: авто-компиляция при изменении файла
eng.watch_file("src/asm/main.s", arch="arm_thumb",
               on_result=lambda r: print(r["listing"]))
```

**Поддерживаемые архитектуры:** `x86`, `x86_64`, `arm`, `arm_thumb` (Cortex-M), `arm64`, `avr`, `mips`

**Прошивка устройств:**

```python
from src.firmware_builder import FirmwareBuilder

fb = FirmwareBuilder()
print(fb.detect_toolchains())            # что установлено
fb.flash("firmware.bin", "/dev/ttyUSB0", target="esp32")
fb.flash("firmware.hex", "COM3", target="avr")
fb.flash("firmware.bin", "/dev/ttyACM0", target="stm32")
fb.disassemble_file("firmware.elf", arch="arm_thumb")
```

---

## 🤖 Модуль почкования (BuddingManager)

**Почкование** — механизм автономного размножения узлов Аргоса в локальной сети.

### Как работает:

```
┌──────────────────────────────────────────────────────────┐
│  WhisperNode (родитель)                                   │
│    ↓                                                      │
│  BuddingManager.find_soil()  ← ARP-сканирование LAN      │
│    ↓                                                      │
│  _is_soil_suitable(ip)       ← порт buds открыт?         │
│                                Argos ещё не запущен?      │
│    ↓ да                                                   │
│  send_bud(target_ip)         ← сериализует:               │
│    • исходный код whisper_node.py                         │
│    • RNN веса (W_h, W_i, b)                               │
│    • скрытое состояние (hidden_state)                     │
│    • ГОСТ-шифрование (Кузнечик-CTR + HMAC-Стрибог)       │
│    ↓                                                      │
│  TCP → target_ip:bud_port                                 │
│                                                           │
│  BuddingManager (приёмник на target_ip):                  │
│    _handle_incoming_bud()    ← распаковывает              │
│    subprocess.Popen(whisper_node.py --node-id X_bud_N)   │
│    Новый узел запущен! → начинает шептать в сеть          │
└──────────────────────────────────────────────────────────┘
```

### Ключевые параметры:
| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `soil_search_interval` | 60 сек | Период поиска «плодородных» хостов |
| `bud_port` | `parent.port + 1000` | TCP-порт для приёма почек |
| Повторная отправка | 5 мин | Не спамит — один хост раз в 300 сек |

### Безопасность:
- Почки шифруются **ГОСТ Кузнечик-CTR** + **HMAC-Стрибог** (если установлен `ARGOS_NETWORK_SECRET`)
- Только доверенные узлы с общим секретом могут разворачивать код
- Код не выполняется автоматически — только через явный `subprocess.Popen`

### Запуск:
```bash
# Почкование включено по умолчанию:
python colibri_daemon.py --node-id MainNode --port 5000

# Без почкования:
python colibri_daemon.py --no-budding

# Ручная отправка почки:
from src.connectivity.budding_manager import BuddingManager
bm.send_bud("192.168.1.100", target_port=5001)
```



Аргос управляет 7 типами умных сред:

| Тип | Сенсоры | Актуаторы |
|-----|---------|-----------|
| 🏠 **home** | temp, humidity, co2, motion, door, smoke | light, thermostat, lock, alarm, fan |
| 🌱 **greenhouse** | temp, humidity, soil_moisture, light_lux, co2, ph | irrigation, heating, ventilation, lamp |
| 🚗 **garage** | gas, motion, door_open, temp, flood | gate, light, alarm, fan, heater |
| 🏚️ **cellar** | temp, humidity, flood, co2 | fan, alarm, pump, heater |
| 🥚 **incubator** | temp, humidity, co2, turn_count | heater, fan, turner, humidifier |
| 🐠 **aquarium** | temp, ph, tds, o2, water_level, ammonia | heater, pump, filter, lamp, co2_inject |
| 🦎 **terrarium** | temp_hot, temp_cool, humidity, uvi, motion | lamp_uv, lamp_heat, mister, fan |

```
создай умную систему
добавь систему greenhouse теплица_1
обнови сенсор теплица_1 temp 38
включи полив теплица_1
умные системы
добавь правило теплица_1 если soil_moisture < 25 то irrigation:on
```

---

## 📡 IoT / Mesh-сеть

| Протокол/стек | Статус | Примечание |
|---------------|--------|------------|
| Zigbee (MQTT) | ✅ Реализован | ZigbeeAdapter в IoTBridge |
| LoRa (UART AT) | ✅ Реализован | LoRaAdapter в IoTBridge |
| WiFi Mesh (UDP) | ✅ Реализован | MeshNetwork |
| MQTT | ✅ Реализован | MQTTAdapter |
| Tasmota Discovery | ✅ Реализован | Zero-config через homeassistant/# |
| Modbus RTU/TCP | ✅ Реализован | ModbusAdapter (read/write holding registers) |
| BACnet | ✅ Реализован | bacnet_bridge.py (scan/read/write/status) |
| KNX / LonWorks / M-Bus / OPC UA | ✅ Реализован | `industrial_protocols.py` — KNXBridge, LonWorksBridge, MBusBridge, OPCUABridge; graceful degradation без внешних lib |

```
iot статус                    iot возможности    iot протоколы
подключи zigbee localhost
подключи lora /dev/ttyUSB0
подключи modbus /dev/ttyUSB0 9600
подключи modbus tcp 192.168.1.10 502
modbus чтение 100 2 1         modbus запись 120 55 1
запусти mesh                  статус mesh
добавь устройство sensor_01 sensor zigbee addr_01 "Датчик кухня"
найди usb чипы
умная прошивка [/dev/ttyUSB0]
обнови тасмота
```

---

## 🏭 Промышленные протоколы (KNX / LonWorks / M-Bus / OPC UA)

Реализованы в `industrial_protocols.py`, интегрированы в `ArgosCore` как `core.industrial`.
Работают без внешних библиотек в режиме симуляции; при наличии `xknx`, `opcua`, `mbus` — нативно.

| Протокол | Стандарт | Назначение |
|----------|----------|------------|
| **KNX** | EN 50090 / ISO 14543 | Умные здания, HVAC, освещение |
| **LonWorks** | ISO/IEC 14908 | Промышленная автоматизация |
| **M-Bus** | EN 13757 | Счётчики энергии, воды, газа |
| **OPC UA** | IEC 62541 | Промышленный IoT, SCADA |

```
industrial статус                  # статус всех протоколов
промышленные протоколы             # то же
industrial discovery               # поиск устройств по всем протоколам
industrial поиск                   # то же
industrial устройства              # список найденных устройств

knx подключи 192.168.1.100         # KNX IP-туннелинг
opcua подключи opc.tcp://srv:4840  # OPC UA сервер
mbus serial /dev/ttyUSB0           # M-Bus через последовательный порт
mbus tcp 192.168.1.50              # M-Bus через TCP
opcua browse ns=0;i=84             # обзор узлов OPC UA
```

---

## 🔧 IoT Шлюзы

| Шаблон | Описание |
|--------|----------|
| `esp32_zigbee` | ESP32 + CC2652 Zigbee координатор |
| `esp32_lora` | ESP32 + SX1276 LoRa шлюз |
| `rpi_mesh` | Raspberry Pi WiFi Mesh шлюз |
| `modbus_rtu` | USB-RS485 Modbus RTU |
| `lorawan_ttn` | LoRaWAN → The Things Network |

```
шаблоны шлюзов
создай шлюз gw_01 esp32_zigbee
прошей шлюз gw_01 /dev/ttyUSB0
создай прошивку gw_02 esp32_lora
список шлюзов
конфиг шлюза gw_01
прошей gateway /dev/ttyUSB0 zigbee_gateway
```

---

## 🌿 Biosphere DAG

```
биосфера статус
биосфера тик
биосфера цель temperature_c 37.5
биосфера старт 30
биосфера стоп
```

---

## ⚛️ Квантовые состояния

| Состояние | Триггер |
|-----------|---------|
| 🔵 **Analytic** | Базовый (CPU ≤40%, RAM ≤50%) |
| 🟣 **Creative** | Низкая нагрузка (CPU ≤40%, RAM ≤50%) |
| 🔴 **Protective** | CPU ≥90% или RAM ≥90% |
| 🟡 **Unstable** | CPU ≥75% или RAM ≥80% |
| 🟢 **All-Seeing** | CPU ≤10%, RAM ≤30% |
| 🌌 **Oracle** | Ручное включение — истинная квантовая случайность |

---

## 🌐 P2P — принцип

```
Нода A (30 дней, 72/100)   Нода B (90 дней, 88/100)👑   Нода C (1 день, 25/100)
Авторитет: 102             Авторитет: 145 МАСТЕР         Авторитет: 18
```

**Авторитет = мощность × log(возраст + 2)**

- UDP broadcast — автообнаружение в локальной сети
- TCP + HMAC — защищённый обмен навыками
- Speculative Consensus v2: Drafter-ноды → Verifier → агрегация `[ERRORS]`/`[FINAL]`
- Role Routing: gateway/weak → Drafter, master → Verifier
- Acceptance Rate: per-drafter метрики, auto-recovery RPS
- Batch Idle Learning: до 8 уроков за пакет, Active Drafter Calibration

---

## 📡 Telegram команды

```
/start     /status    /crypto    /history
/geo       /memory    /alerts    /network
/sync      /replicate /skills    /smart
/iot       /voice_on  /voice_off /help
```

---

## 🐳 Docker

### Быстрый запуск

```bash
# Клонировать репозиторий
git clone https://github.com/labuaqlysnecy/Argos.git && cd SiGtRiP

# Скопировать и заполнить переменные окружения
cp .env.example .env
# Отредактировать .env (вставить API-ключи)

# Запустить headless-сервер (Telegram + P2P + Dashboard :8080)
docker-compose up -d

# Просмотреть логи
docker-compose logs -f argos_node

# Остановить
docker-compose down
```

### Сборка образа вручную

```bash
docker build -t argos-universal:2.1.4 .

# Запуск контейнера напрямую
docker run -d \
  --name argos \
  --env-file .env \
  -p 8080:8080 \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/data:/app/data \
  argos-universal:2.1.4
```

### GitHub Container Registry (GHCR)

```bash
# Публичный образ (после релиза)
docker pull ghcr.io/labuaqlysnecy/Argos:latest
docker run -d --env-file .env ghcr.io/labuaqlysnecy/Argos:latest
```

---

## 🖥️ Сборка .exe / binary (Windows · Linux · macOS)

```bash
# Установить PyInstaller (если не установлен)
pip install pyinstaller

# Быстрая сборка (один portable-файл)
python build_exe.py

# Сборка в папку (быстрее запускается)
python build_exe.py --onedir

# Вручную через spec-файл
pyinstaller argos.spec
```

После сборки:
- **Windows:** `dist/ARGOS.exe` — portable, не требует установки
- **Linux/macOS:** `dist/argos` — запустить `./dist/argos`
- Архив `.7z` создаётся автоматически (требуется `pip install py7zr`)

### GitHub Actions (автоматическая сборка)

Рабочий процесс [build_windows.yml](.github/workflows/build_windows.yml) запускается при каждом пуше и создаёт `ARGOS.exe` + установщик `ARGOS_Setup.exe` (Inno Setup).

---

## 📱 Сборка Android APK (Buildozer)

### Локальная сборка

```bash
# 1. Установить зависимости (Linux/macOS/WSL2)
sudo apt-get install -y openjdk-17-jdk build-essential git zip unzip
pip install buildozer cython

# 2. Debug APK (быстро, ~30 мин первый раз)
buildozer android debug

# 3. Release APK (подписанный)
buildozer android release

# APK появится в папке bin/
ls bin/*.apk
```

### Через Docker

```bash
# Использует официальный образ kivy/buildozer
docker-compose --profile apk run apk_builder
```

### Google Colab (без установки)

1. Открой ноутбук: [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/labuaqlysnecy/Argos/blob/main/colab/ARGOS_Colab_Launch.ipynb)
2. В последней ячейке выполни блок **APK-сборка**.

### GitHub Actions (автоматическая сборка)

Рабочий процесс [build_apk.yml](.github/workflows/build_apk.yml) запускается при пуше в `main` и загружает APK как артефакт CI.

---

## ☁️ Google Colab — запуск без установки

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/labuaqlysnecy/Argos/blob/main/colab/ARGOS_Colab_Launch.ipynb)

Ноутбук [`colab/ARGOS_Colab_Launch.ipynb`](colab/ARGOS_Colab_Launch.ipynb) запускает ARGOS в headless-режиме с HTTP Remote Control API и туннелем Cloudflare за ~3 минуты:

| Шаг | Что делает |
|-----|------------|
| 1️⃣  Clone | `git clone https://github.com/labuaqlysnecy/Argos` |
| 2️⃣  Секреты | Загружает токены из Colab Secrets (🔑) или переменных окружения |
| 3️⃣  Зависимости | `fastapi`, `uvicorn`, `psutil` + опционально APK-тулинг |
| 4️⃣  Старт ARGOS | Headless core + FastAPI Dashboard на порту 8080 |
| 5️⃣  Туннель | Cloudflare Tunnel (`cloudflared`) → публичный URL |
| 6️⃣  Проверка | Health-check + образцы `curl` запросов |

### Требуемые секреты (Colab → 🔑 Secrets)

| Переменная | Обязательна | Описание |
|---|---|---|
| `ARGOS_REMOTE_TOKEN` | ✅ | Bearer-токен для авторизации API |
| `TELEGRAM_BOT_TOKEN` | ⬜ | Токен Telegram-бота (опционально) |
| `USER_ID` | ⬜ | Telegram User ID (опционально) |

### Использование публичного URL в APK

1. Запусти Colab-ноутбук → скопируй URL вида `https://xxxx.trycloudflare.com`.
2. Открой APK на Android → вкладка **⚙️ Настройки**.
3. Вставь URL в поле **Server URL** и токен в **Bearer Token**.
4. Нажми **💾 Сохранить** → перейди на вкладку **📊 Dashboard** и нажми **🔄 Обновить**.

### Ручной запуск (один скрипт)

```bash
# В ячейке Colab:
!bash <(curl -fsSL https://raw.githubusercontent.com/labuaqlysnecy/Argos/main/colab_start.sh)
```

---

## 🔌 Remote Control API

ARGOS предоставляет REST API для удалённого управления с Android APK.
Запустить: `python main.py --no-gui --dashboard`

### Эндпоинты

| Метод | Путь | Авторизация | Описание |
|-------|------|-------------|----------|
| `GET` | `/api/health` | ❌ нет | Версия, uptime, статус |
| `POST` | `/api/command` | ✅ Bearer | Выполнить команду ARGOS |
| `GET` | `/api/events?limit=N` | ✅ Bearer | Последние события EventBus |
| `GET` | `/api/status` | ❌ нет | CPU/RAM/диск/состояние |

### Авторизация

Установить переменную `ARGOS_REMOTE_TOKEN` — тогда все `/api/command` и `/api/events` запросы потребуют заголовок:

```
Authorization: Bearer <ARGOS_REMOTE_TOKEN>
```

Если `ARGOS_REMOTE_TOKEN` не задан — авторизация отключена.

### Примеры

```bash
# Health check (без токена)
curl https://xxxx.trycloudflare.com/api/health

# Команда (с токеном)
curl -X POST https://xxxx.trycloudflare.com/api/command \
     -H "Authorization: Bearer mysecret" \
     -H "Content-Type: application/json" \
     -d '{"cmd": "статус"}'

# События
curl "https://xxxx.trycloudflare.com/api/events?limit=10" \
     -H "Authorization: Bearer mysecret"
```

### Smoke-тест

```bash
ARGOS_BASE_URL=http://localhost:8080 ARGOS_REMOTE_TOKEN=mysecret \
    python scripts/smoke_api.py
```

---

## 📦 Публикация пакета на PyPI (Trusted Publisher / OIDC)

Пакет называется **`argos-universalsigtrip`** и публикуется без токенов через
[OIDC Trusted Publishing](https://docs.pypi.org/trusted-publishers/).

### Установка пакета
```bash
pip install argos-universalsigtrip
```

### Разовая настройка Trusted Publisher (делается один раз)

#### TestPyPI
1. Зайти на [test.pypi.org](https://test.pypi.org) → Аккаунт → Publishing
2. **Add a new pending publisher**:
   - PyPI project name: `argos-universalsigtrip`
   - Owner: `iliyaqdrwalqu`
   - Repository: `Argoss`
   - Workflow file name: `publish_testpypi.yml`
   - Environment name: *(оставить пустым)*

   - Workflow file path: `.github/workflows/publish_testpypi.yml`
   - Environment name: `testpypi`

3. Запустить workflow вручную: **Actions → 📦 Publish to TestPyPI → Run workflow**

#### PyPI (production)
1. Зайти на [pypi.org](https://pypi.org) → Аккаунт → Publishing
2. **Add a new pending publisher** с теми же параметрами, но:
   - Workflow file name: `publish_pypi.yml`
3. Создать GitHub Release (тег `v*.*.*`) или запустить вручную:
   **Actions → 🚀 Publish to PyPI → Run workflow**

> **Важно:** Для публикации не нужны никакие секреты (`PYPI_TOKEN`). Аутентификация
> осуществляется автоматически через GitHub OIDC.

---

## 📊 Аудит v2.0.0

```
88 модулей Python · 88/88 импортов ✅
6 квантовых состояний · 9 IoT/пром-протоколов реализованы · 5 шаблонов шлюзов
7 умных систем · NFC / USB / Bluetooth подсистемы
🏭 Промышленные протоколы: KNX · LonWorks · M-Bus · OPC UA (industrial_protocols.py)
AWA-Core · Adaptive Drafter · Self-Healing · AirSnitch · WiFi Sentinel
SmartHome Override · Power Sentry · Emergency Purge · Container Isolation
Master Auth (MasterKeyAuth) · Biosphere DAG · IBM Quantum Bridge · JARVIS Engine
Speculative Consensus v2 · Batch Idle Learning · P2P Role Routing
ArgosKivyApp · ArgosWebEngine · MasterAuth · SensorBridge · SmarthomeOverride
```

---

## 📦 Публикация на TestPyPI (Trusted Publishing / OIDC)

Пакет публикуется под именем **`argos-universalsigtrip`** через [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) — без хранения `PYPI_TOKEN` в секретах.

### Настройка на стороне TestPyPI
1. Войдите на [test.pypi.org](https://test.pypi.org) и откройте настройки проекта `argos-universalsigtrip`.
2. Перейдите в **Publishing → Trusted publishers → Add a new publisher**.
3. Заполните:
   - **Owner**: `iliyaqdrwalqu`
   - **Repository**: `Argoss`
   - **Workflow file name**: `publish_testpypi.yml`
4. Сохраните.

### Запуск публикации
Workflow `.github/workflows/publish_testpypi.yml` запускается:
- автоматически при публикации **GitHub Release**,
- или вручную через **Actions → Publish to TestPyPI (OIDC) → Run workflow**.

Никакие секреты (`PYPI_TOKEN`) для этого не требуются — GitHub выдаёт краткосрочный OIDC-токен напрямую.

---



---

## 🤖 Три модели Ollama — мультимодельный режим

ARGOS поддерживает одновременную работу трёх моделей Ollama:

| Модель | Тип | Назначение | RAM |
|--------|-----|------------|-----|
| `tinyllama` | Локальная быстрая | Простые команды, статусы, короткие ответы | 637 MB |
| `llama3.2:3b` | Локальная умная | Диалоги, анализ, объяснения | 2 GB |
| `gpt-oss:120b-cloud` | Облако Ollama | Сложные задачи, код, архитектура | 0 MB (облако) |

### Установка

```bash
ollama pull tinyllama
ollama pull llama3.2:3b
ollama pull gpt-oss:120b-cloud
```

### Настройка `.env`

```env
OLLAMA_FAST_MODEL=tinyllama
OLLAMA_MODEL=llama3.2:3b
OLLAMA_CLOUD_MODEL=gpt-oss:120b-cloud
```

### Команды

```
три модели статус           — статус всех трёх моделей
три модели авто <запрос>    — автовыбор модели по задаче
три модели быстро <запрос>  — tinyllama (быстро)
три модели умно <запрос>    — llama3.2:3b (качественно)
три модели облако <запрос>  — gpt-oss:120b (мощно)
три модели скачать          — скачать все три модели
```

### Логика автовыбора

```
Короткий запрос / команда  → tinyllama
Диалог / объяснение        → llama3.2:3b
Длинный / сложный запрос   → gpt-oss:120b-cloud
Нет Ollama                 → Gemini / Groq (облако)
```

### Параллельный режим

ARGOS может спрашивать `tinyllama` и `llama3.2:3b` одновременно
и возвращать первый полученный ответ — это ускоряет время отклика.

```env
ARGOS_PARALLEL_MODE=on
```

---

## ⚖️ Лицензия

Apache License 2.0 — Всеволод / Argos Project, 2026

---

*"Аргос не спит. Аргос видит. Аргос помнит."*

---

## 🧠 Automated ARGOS Report → GitHub Gist

The workflow **`.github/workflows/argos_report_to_gist.yml`** runs a health-check and
consciousness-module tests, then publishes a Markdown report to a GitHub Gist.

### Setup (one-time)

1. Create a GitHub Personal Access Token (PAT) with the **`gist`** scope.
2. In your repository go to **Settings → Secrets and variables → Actions** and add a secret named **`GIST_TOKEN`** with the PAT value.

### Running the workflow

1. Go to **Actions → 🧠 ARGOS Report → Gist**.
2. Click **Run workflow** → **Run workflow**.
3. After the run completes, open Gist `8e9cf57e043c7a6111f277828f363b01` to see the updated `argos_report.md`.

### What the workflow does

| Step | Description |
|------|-------------|
| **(B)** `python health_check.py` | Checks core ARGOS files, modules, and AI engines |
| **(C)** `pytest tests/test_consciousness_module.py -v` | Runs consciousness-module tests (falls back to `pytest tests -q` if the file is missing) |
| Publish | Updates `argos_report.md` in Gist `8e9cf57e043c7a6111f277828f363b01` via GitHub REST API |

<!-- ARGOS_CANONICAL_LINKS_BEGIN -->
## Публичные ссылки ARGOS

- Основной проект: https://github.com/winargos42-dotcom/argos
- Профили владельца: [GitHub](https://github.com/winargos42-dotcom) · [Hugging Face](https://huggingface.co/AvaSiG)
- Основной Telegram-бот: https://t.me/Argosssbot
- Модель и данные: [ARGOS-MUKHTAR](https://huggingface.co/AvaSiG/ARGOS-MUKHTAR) · [MUKHTAR-Bench](https://huggingface.co/datasets/AvaSiG/MUKHTAR-Bench)
- Экспериментальный [ARGOS-MUKHTAR-Lab Space](https://huggingface.co/spaces/AvaSiG/ARGOS-MUKHTAR-Lab): при проверке 30.10.2026 — **PAUSED**.
- [Политика конфиденциальности ARGOS](https://winargos42-dotcom.github.io/argos-play-store/) — отдельный сайт политики конфиденциальности.
<!-- ARGOS_CANONICAL_LINKS_END -->
