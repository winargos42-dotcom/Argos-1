# ╔══════════════════════════════════════════════════════════════════════╗
# ║  ARGOS UNIVERSAL OS — Dockerfile                                    ║
# ║  Все драйверы: GPIO, I2C, Serial, UART, Modbus, MQTT, Ollama,      ║
# ║  Whisper, gTTS, ffmpeg, pyserial, smbus2, gpiod                    ║
# ╚══════════════════════════════════════════════════════════════════════╝

FROM python:3.11-slim-bookworm AS base

LABEL maintainer="Vsevolod / Argos Project"
LABEL description="Argos Universal OS — автономная ИИ-система"
LABEL version="2.1.0"

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DEBIAN_FRONTEND=noninteractive

# ── SYSTEM PACKAGES ───────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget git ca-certificates build-essential gcc g++ \
    libffi-dev libssl-dev libsqlite3-dev \
    ffmpeg portaudio19-dev \
    i2c-tools libi2c-dev \
    libserial-dev \
    lm-sensors usbutils \
    net-tools iputils-ping \
    htop procps \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# ── OPENAI CODEX CLI ──────────────────────────────────────────────────────────
ENV CODEX_INSTALL_DIR=/usr/local/bin
ENV CODEX_NON_INTERACTIVE=1
RUN curl -fsSL https://chatgpt.com/codex/install.sh | sh && \
    codex --version

WORKDIR /app
COPY requirements.txt ./

RUN pip install --upgrade pip setuptools wheel

# ── CORE + AI ─────────────────────────────────────────────────────────────────
RUN pip install --no-cache-dir \
    python-dotenv>=1.0.0 psutil>=5.9.0 requests>=2.31.0 \
    python-telegram-bot>=21.0.0 \
    google-genai>=0.8.0 ollama>=0.3.0 \
    scikit-learn>=1.4.0 numpy>=1.26.0 \
    aiosqlite>=0.20.0 GitPython>=3.1.40

# ── AUDIO ─────────────────────────────────────────────────────────────────────
RUN pip install --no-cache-dir \
    gTTS>=2.5.0 pyttsx3>=2.90 \
    SpeechRecognition>=3.10.0 PyAudio>=0.2.13 \
    faster-whisper>=1.0.0

# ── HARDWARE DRIVERS ──────────────────────────────────────────────────────────
RUN pip install --no-cache-dir \
    smbus2>=0.4.3 pyserial>=3.5 gpiod>=1.5.0 \
    pymodbus>=3.6.0 paho-mqtt>=2.0.0

RUN pip install --no-cache-dir OPi.GPIO>=0.5.0 || \
    echo "[WARN] OPi.GPIO: только для Orange Pi (пропущено)"

# ── WEB / VISION ──────────────────────────────────────────────────────────────
RUN pip install --no-cache-dir \
    beautifulsoup4>=4.12.0 lxml>=5.0.0 \
    duckduckgo-search>=5.0.0 Pillow>=10.0.0

# ── MCP API (Cloud Run) ───────────────────────────────────────────────────────
RUN pip install --no-cache-dir \
    aiohttp>=3.9.0 \
    fastapi>=0.110.0 \
    "uvicorn[standard]>=0.29.0"

# ── INDUSTRIAL PROTOCOLS (graceful) ──────────────────────────────────────────
RUN pip install --no-cache-dir xknx>=3.1.0 opcua>=0.98.13 || \
    echo "[WARN] Промышленные протоколы: частично недоступны"

# ── APP ───────────────────────────────────────────────────────────────────────
COPY . .

RUN mkdir -p data/ollama_trainer data/argos_model logs config/gateways config/dags

# Пользователь с доступом к serial, i2c, gpio, audio
RUN groupadd -r argos 2>/dev/null || true && \
    useradd -r -g argos argos 2>/dev/null || true && \
    usermod -aG dialout,i2c,audio argos 2>/dev/null || true && \
    chown -R argos:argos /app

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python3 -c "import psutil; assert psutil.cpu_percent() >= 0" || exit 1

# ── RUNTIME STAGE (required by docker workflow --target runtime) ──────────────
FROM base AS runtime

ENV CODEX_HOME=/codex-home
USER argos
EXPOSE 8080
CMD ["sh", "-c", "if [ \"${ARGOS_ENV:-local}\" = \"cloud\" ]; then python3 cloud_entry.py; else python3 main.py --no-gui; fi"]
