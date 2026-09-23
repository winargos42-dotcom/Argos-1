"""
offline_voice.py — офлайн-голос Аргоса для Linux-десктопа (PipeWire/PulseAudio).

TTS:  Piper (piper-tts, модель ru_RU-medium.onnx)  → fallback espeak-ng → лог.
      Воспроизведение: paplay / pw-play в sink по умолчанию (или ARGOS_AUDIO_SINK).
STT:  Vosk (vosk-model-small-ru) — полностью офлайн.
Mic:  parecord / pw-record (16 кГц, mono, s16le) с программным ФВЧ (срезает
      инфранизкий гул встроенного микрофона ALC269).

Никаких сетевых вызовов: аудио не покидает машину.

Переменные окружения:
  ARGOS_VOICE=off|on           — главный выключатель голосового контура (по умолчанию off)
  ARGOS_MODELS_DIR             — корень моделей (/home/.argos-storage/models)
  ARGOS_PIPER_MODEL            — путь к .onnx голосу Piper
  ARGOS_VOSK_MODEL / VOSK_MODEL_PATH — каталог модели Vosk
  ARGOS_AUDIO_SINK             — имя sink для воспроизведения (пусто = default)
  ARGOS_AUDIO_SOURCE           — имя source для записи (пусто = default)
  ARGOS_WAKE_WORDS             — через запятую, по умолчанию «аргос,аргус»
  ARGOS_TTS_MAX_CHARS          — максимум символов на одну фразу (600)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from typing import Callable, Iterator, Optional

try:
    from src.argos_logger import get_logger

    log = get_logger("argos.voice")
except Exception:  # pragma: no cover - standalone usage
    import logging

    log = logging.getLogger("argos.voice")

_ON_VALUES = {"1", "true", "on", "yes", "да", "вкл"}

DEFAULT_MODELS_DIR = "/home/.argos-storage/models"
SAMPLE_RATE = 16000
DEFAULT_WAKE_WORDS = ("аргос", "аргус")


# ── Конфигурация ────────────────────────────────────────────────────────────
def voice_enabled() -> bool:
    """Главный выключатель: ARGOS_VOICE=on. По умолчанию микрофон не слушается."""
    return os.getenv("ARGOS_VOICE", "off").strip().lower() in _ON_VALUES


def models_dir() -> str:
    return os.getenv("ARGOS_MODELS_DIR", DEFAULT_MODELS_DIR).strip() or DEFAULT_MODELS_DIR


def piper_model_path() -> str:
    explicit = os.getenv("ARGOS_PIPER_MODEL", "").strip()
    if explicit:
        return explicit
    return os.path.join(models_dir(), "piper", "ru_RU-medium.onnx")


def vosk_model_path() -> str:
    explicit = os.getenv("ARGOS_VOSK_MODEL", "").strip() or os.getenv("VOSK_MODEL_PATH", "").strip()
    if explicit:
        return explicit
    base = os.path.join(models_dir(), "vosk")
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if name.startswith("vosk-model") and os.path.isdir(os.path.join(base, name)):
                return os.path.join(base, name)
    return os.path.join(base, "vosk-model-small-ru-0.22")


def wake_words() -> list[str]:
    raw = os.getenv("ARGOS_WAKE_WORDS", "").strip()
    words = [w.strip().lower() for w in raw.split(",") if w.strip()] if raw else list(DEFAULT_WAKE_WORDS)
    return words


def _audio_env() -> dict:
    env = dict(os.environ)
    if not env.get("XDG_RUNTIME_DIR"):
        candidate = f"/run/user/{os.getuid()}"
        if os.path.isdir(candidate):
            env["XDG_RUNTIME_DIR"] = candidate
    return env


_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF⬀-⯿️‍]+"
)


def clean_text_for_speech(text: str, max_chars: Optional[int] = None) -> str:
    """Убирает эмодзи/markdown/URL, чтобы синтезатор не читал мусор."""
    if not text:
        return ""
    limit = max_chars or int(os.getenv("ARGOS_TTS_MAX_CHARS", "600") or 600)
    t = str(text)
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"https?://\S+", " ссылка ", t)
    t = _EMOJI_RE.sub(" ", t)
    t = re.sub(r"[*_`#>|\[\]{}<>•]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        cut = t[:limit]
        dot = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        t = cut[: dot + 1] if dot > limit // 2 else cut
    return t


# ── TTS ─────────────────────────────────────────────────────────────────────
class OfflineTTS:
    """Офлайн-синтез речи: Piper → espeak-ng. Воспроизведение через PipeWire."""

    def __init__(self, model_path: Optional[str] = None, sink: Optional[str] = None):
        self.model_path = model_path or piper_model_path()
        self.sink = sink if sink is not None else os.getenv("ARGOS_AUDIO_SINK", "").strip()
        self._voice = None
        self._load_lock = threading.Lock()
        self._speak_lock = threading.Lock()
        self._speaking = threading.Event()
        self.last_error: Optional[str] = None
        self.last_synth_seconds: Optional[float] = None
        self.backend = self._detect_backend()
        if self.backend == "none":
            log.warning(
                "TTS: нет офлайн-движка (pip install piper-tts + модель %s, или espeak-ng)",
                self.model_path,
            )

    # -- backend detection
    def _detect_backend(self) -> str:
        try:
            import importlib.util

            if importlib.util.find_spec("piper") and os.path.isfile(self.model_path):
                return "piper"
        except Exception:
            pass
        if shutil.which("espeak-ng"):
            return "espeak-ng"
        return "none"

    @property
    def available(self) -> bool:
        return self.backend != "none"

    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()

    def player_command(self, wav_path: str, sink: Optional[str] = None) -> list[str]:
        target = sink if sink is not None else self.sink
        if shutil.which("paplay"):
            cmd = ["paplay"]
            if target:
                cmd.append(f"--device={target}")
            return cmd + [wav_path]
        if shutil.which("pw-play"):
            cmd = ["pw-play"]
            if target:
                cmd += ["--target", target]
            return cmd + [wav_path]
        if shutil.which("aplay"):
            return ["aplay", "-q", wav_path]
        return []

    def _load_piper(self):
        with self._load_lock:
            if self._voice is None:
                from piper import PiperVoice  # type: ignore

                started = time.monotonic()
                self._voice = PiperVoice.load(self.model_path)
                log.info("TTS: Piper загружен за %.1f с (%s)", time.monotonic() - started, self.model_path)
        return self._voice

    def synthesize_to_file(self, text: str, wav_path: str) -> bool:
        """Синтезирует текст в WAV. Возвращает True при успехе."""
        text = clean_text_for_speech(text)
        if not text:
            return False
        started = time.monotonic()
        try:
            if self.backend == "piper":
                voice = self._load_piper()
                with wave.open(wav_path, "wb") as wav_file:
                    voice.synthesize_wav(text, wav_file)
            elif self.backend == "espeak-ng":
                subprocess.run(
                    ["espeak-ng", "-v", "ru", "-w", wav_path, text],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
            else:
                self.last_error = "TTS недоступен: нет Piper-модели и espeak-ng"
                log.warning(self.last_error)
                return False
        except Exception as exc:
            self.last_error = f"TTS {self.backend}: {exc}"
            log.warning(self.last_error)
            if self.backend == "piper" and shutil.which("espeak-ng"):
                self.backend = "espeak-ng"
                return self.synthesize_to_file(text, wav_path)
            return False
        self.last_synth_seconds = time.monotonic() - started
        return True

    def play_file(self, wav_path: str, sink: Optional[str] = None, timeout: float = 120.0) -> bool:
        cmd = self.player_command(wav_path, sink)
        if not cmd:
            self.last_error = "Нет плеера (paplay/pw-play/aplay)"
            log.warning(self.last_error)
            return False
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=timeout, env=_audio_env())
            return True
        except Exception as exc:
            self.last_error = f"Воспроизведение: {exc}"
            log.warning(self.last_error)
            return False

    def speak(self, text: str, sink: Optional[str] = None) -> bool:
        """Синтез + воспроизведение (блокирующе). Возвращает True, если прозвучало."""
        if not text or not self.available:
            if not self.available:
                log.info("TTS пропущен (нет движка): %s", str(text)[:80])
            return False
        with self._speak_lock:
            fd, wav_path = tempfile.mkstemp(prefix="argos-tts-", suffix=".wav")
            os.close(fd)
            self._speaking.set()
            try:
                if not self.synthesize_to_file(text, wav_path):
                    return False
                return self.play_file(wav_path, sink)
            finally:
                self._speaking.clear()
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    def speak_async(self, text: str, sink: Optional[str] = None) -> threading.Thread:
        th = threading.Thread(target=self.speak, args=(text, sink), daemon=True, name="argos-tts")
        th.start()
        return th


# ── Микрофон ────────────────────────────────────────────────────────────────
class HighPassFilter:
    """Однополюсный ФВЧ (~80 Гц) для s16le: убирает DC и инфранизкий гул."""

    def __init__(self, cutoff_hz: float = 80.0, rate: int = SAMPLE_RATE):
        import math

        rc = 1.0 / (2 * math.pi * cutoff_hz)
        dt = 1.0 / rate
        self.alpha = rc / (rc + dt)
        self._prev_x = 0.0
        self._prev_y = 0.0

    def process(self, pcm: bytes) -> bytes:
        try:
            import numpy as np
        except ImportError:  # pragma: no cover
            return pcm
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        if x.size == 0:
            return pcm
        y = np.empty_like(x)
        a = self.alpha
        px, py = self._prev_x, self._prev_y
        # y[n] = a*(y[n-1] + x[n] - x[n-1]); 4000 отсчётов на чанк — цикл дешёвый.
        try:
            from scipy.signal import lfilter  # type: ignore

            # DF2T-состояние: z = a*y[n-1] - a*x[n-1]
            y, _ = lfilter([a, -a], [1.0, -a], x, zi=[a * (py - px)])
        except Exception:
            for i in range(x.size):
                py = a * (py + x[i] - px)
                px = x[i]
                y[i] = py
        self._prev_x = float(x[-1])
        self._prev_y = float(y[-1])
        return np.clip(y, -32768, 32767).astype("<i2").tobytes()


def level_dbfs(pcm: bytes) -> dict:
    """RMS/peak/DC в dBFS для s16le PCM."""
    import math

    try:
        import numpy as np

        x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        if x.size == 0:
            return {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "dc": 0.0}
        rms = float(np.sqrt(np.mean(x**2)))
        peak = float(np.max(np.abs(x)))
        dc = float(np.mean(x))
    except ImportError:  # pragma: no cover
        import array

        arr = array.array("h", pcm)
        if not arr:
            return {"rms_dbfs": -120.0, "peak_dbfs": -120.0, "dc": 0.0}
        rms = math.sqrt(sum(v * v for v in arr) / len(arr))
        peak = float(max(abs(v) for v in arr))
        dc = sum(arr) / len(arr)
    to_db = lambda v: round(20 * math.log10(max(v, 1e-9) / 32768.0), 1)
    return {"rms_dbfs": to_db(rms), "peak_dbfs": to_db(peak), "dc": round(dc, 1)}


class MicStream:
    """Поток PCM с микрофона через parecord/pw-record. Всегда закрывается в __exit__."""

    CHUNK_BYTES = 8000  # 0.25 с при 16 кГц s16 mono

    def __init__(self, source: Optional[str] = None, rate: int = SAMPLE_RATE, highpass: bool = True):
        self.source = source if source is not None else os.getenv("ARGOS_AUDIO_SOURCE", "").strip()
        self.rate = rate
        self._proc: Optional[subprocess.Popen] = None
        self._hpf = HighPassFilter(rate=rate) if highpass else None

    def command(self) -> list[str]:
        if shutil.which("parecord"):
            cmd = ["parecord", "--raw", "--format=s16le", f"--rate={self.rate}", "--channels=1",
                   "--latency-msec=100"]
            if self.source:
                cmd.append(f"--device={self.source}")
            return cmd
        if shutil.which("pw-record"):
            cmd = ["pw-record", "--format", "s16", "--rate", str(self.rate), "--channels", "1"]
            if self.source:
                cmd += ["--target", self.source]
            return cmd + ["-"]
        if shutil.which("arecord"):
            return ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(self.rate), "-c", "1"]
        raise RuntimeError("Нет программы записи (parecord/pw-record/arecord)")

    def __enter__(self) -> "MicStream":
        self._proc = subprocess.Popen(
            self.command(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_audio_env()
        )
        return self

    def read(self, nbytes: int = CHUNK_BYTES) -> bytes:
        if not self._proc or not self._proc.stdout:
            return b""
        data = self._proc.stdout.read(nbytes)
        if data and self._hpf:
            data = self._hpf.process(data)
        return data or b""

    def chunks(self) -> Iterator[bytes]:
        while True:
            data = self.read()
            if not data:
                return
            yield data

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass

    def __exit__(self, *exc) -> None:
        self.close()


def record_seconds(seconds: float, source: Optional[str] = None, highpass: bool = True) -> bytes:
    """Записывает N секунд с микрофона (s16le 16 кГц mono) и гарантированно гасит рекордер."""
    need = int(seconds * SAMPLE_RATE) * 2
    buf = bytearray()
    with MicStream(source=source, highpass=highpass) as mic:
        while len(buf) < need:
            data = mic.read(min(MicStream.CHUNK_BYTES, need - len(buf)))
            if not data:
                break
            buf.extend(data)
    return bytes(buf)


# ── STT ─────────────────────────────────────────────────────────────────────
class VoskSTT:
    """Офлайн-распознавание речи (Vosk)."""

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path or vosk_model_path()
        self._model = None
        self._lock = threading.Lock()
        self.last_error: Optional[str] = None

    @property
    def available(self) -> bool:
        try:
            import importlib.util

            return bool(importlib.util.find_spec("vosk")) and os.path.isdir(self.model_path)
        except Exception:
            return False

    def model(self):
        with self._lock:
            if self._model is None:
                import vosk  # type: ignore

                vosk.SetLogLevel(-1)
                started = time.monotonic()
                self._model = vosk.Model(self.model_path)
                log.info("STT: Vosk загружен за %.1f с (%s)", time.monotonic() - started, self.model_path)
        return self._model

    def recognizer(self, rate: int = SAMPLE_RATE, grammar: Optional[list[str]] = None):
        import vosk  # type: ignore

        if grammar:
            return vosk.KaldiRecognizer(self.model(), rate, json.dumps(grammar, ensure_ascii=False))
        return vosk.KaldiRecognizer(self.model(), rate)

    def transcribe_pcm(self, pcm: bytes, rate: int = SAMPLE_RATE) -> str:
        rec = self.recognizer(rate)
        step = 8000
        parts: list[str] = []
        for i in range(0, len(pcm), step):
            if rec.AcceptWaveform(pcm[i : i + step]):
                parts.append(json.loads(rec.Result()).get("text", ""))
        parts.append(json.loads(rec.FinalResult()).get("text", ""))
        return " ".join(p for p in parts if p).strip()

    def transcribe_wav(self, wav_path: str) -> str:
        """Распознаёт WAV (mono s16, любая частота — Vosk ресэмплирует сам)."""
        try:
            with wave.open(wav_path, "rb") as wf:
                if wf.getsampwidth() != 2:
                    raise ValueError("нужен 16-битный PCM WAV")
                rate = wf.getframerate()
                channels = wf.getnchannels()
                pcm = wf.readframes(wf.getnframes())
            if channels == 2:
                import numpy as np

                arr = np.frombuffer(pcm, dtype="<i2").reshape(-1, 2).mean(axis=1)
                pcm = arr.astype("<i2").tobytes()
            return self.transcribe_pcm(pcm, rate)
        except Exception as exc:
            self.last_error = f"Vosk WAV: {exc}"
            log.warning(self.last_error)
            return ""

    def listen(
        self,
        timeout: float = 8.0,
        phrase_limit: float = 8.0,
        mic_factory: Callable[[], MicStream] = MicStream,
    ) -> str:
        """Слушает микрофон до конца фразы / таймаута. Рекордер всегда гасится."""
        if not self.available:
            self.last_error = f"Vosk недоступен (модель {self.model_path})"
            return ""
        rec = self.recognizer()
        started = time.monotonic()
        speech_started_at: Optional[float] = None
        try:
            with mic_factory() as mic:
                for chunk in mic.chunks():
                    now = time.monotonic()
                    if rec.AcceptWaveform(chunk):
                        text = json.loads(rec.Result()).get("text", "").strip()
                        if text:
                            return text
                    elif speech_started_at is None and json.loads(rec.PartialResult()).get("partial"):
                        speech_started_at = now
                    if speech_started_at is None and now - started > timeout:
                        break
                    if speech_started_at is not None and now - speech_started_at > phrase_limit:
                        break
        except Exception as exc:
            self.last_error = f"Vosk mic: {exc}"
            log.warning(self.last_error)
            return ""
        return json.loads(rec.FinalResult()).get("text", "").strip()


# ── Wake word ───────────────────────────────────────────────────────────────
def split_wake_phrase(text: str, words: Optional[list[str]] = None) -> Optional[str]:
    """
    Если в тексте есть wake word — возвращает остаток после него ("" если только имя),
    иначе None.  «аргос какая температура дома» → «какая температура дома».
    """
    tokens = (text or "").lower().split()
    words = words or wake_words()
    for i, tok in enumerate(tokens):
        if any(tok == w or (len(w) >= 4 and tok.startswith(w)) for w in words):
            return " ".join(tokens[i + 1 :]).strip()
    return None


class VoskWakeLoop:
    """
    Непрерывное прослушивание: ждёт «Аргос», затем команду.
    on_command(text) вызывается в потоке слушателя.
    Во время собственной речи (tts.speaking) аудио отбрасывается (полудуплекс).
    """

    def __init__(
        self,
        stt: VoskSTT,
        on_command: Callable[[str], None],
        on_wake: Optional[Callable[[], None]] = None,
        tts: Optional[OfflineTTS] = None,
        command_timeout: float = 6.0,
        mic_factory: Callable[[], MicStream] = MicStream,
    ):
        self.stt = stt
        self.on_command = on_command
        self.on_wake = on_wake
        self.tts = tts
        self.command_timeout = command_timeout
        self.mic_factory = mic_factory
        self.detections = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mic: Optional[MicStream] = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, daemon=True, name="argos-wake-vosk")
        self._thread.start()

    def stop(self, join_timeout: float = 3.0) -> None:
        self._stop.set()
        mic = self._mic
        if mic is not None:
            mic.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(join_timeout)

    def _handle_text(self, text: str) -> Optional[str]:
        rest = split_wake_phrase(text)
        if rest is None:
            return None
        self.detections += 1
        log.info("WAKE WORD «%s» (#%d)", text, self.detections)
        return rest

    def run(self) -> None:
        words = wake_words()
        try:
            rec = self.stt.recognizer()
        except Exception as exc:
            log.warning("Wake(Vosk): модель не загружена: %s", exc)
            return
        while not self._stop.is_set():
            pending_command: Optional[str] = None
            try:
                with self.mic_factory() as mic:
                    self._mic = mic
                    awaiting_until: Optional[float] = None
                    for chunk in mic.chunks():
                        if self._stop.is_set():
                            break
                        if self.tts is not None and self.tts.speaking:
                            if hasattr(rec, "Reset"):
                                rec.Reset()
                            continue
                        if not rec.AcceptWaveform(chunk):
                            if awaiting_until and time.monotonic() > awaiting_until:
                                awaiting_until = None
                            continue
                        text = json.loads(rec.Result()).get("text", "").strip()
                        if not text:
                            continue
                        if awaiting_until is not None:
                            pending_command = text
                            break
                        rest = self._handle_text(text)
                        if rest is None:
                            continue
                        if rest:
                            pending_command = rest
                            break
                        if self.on_wake:
                            self.on_wake()
                        awaiting_until = time.monotonic() + self.command_timeout
            except Exception as exc:
                log.warning("Wake(Vosk) mic: %s", exc)
                self._stop.wait(2.0)
            finally:
                self._mic = None
            if pending_command and not self._stop.is_set():
                # Микрофон закрыт на время обработки команды — Аргос не слушает сам себя.
                try:
                    self.on_command(pending_command)
                except Exception as exc:
                    log.error("Wake command: %s", exc)
                if hasattr(rec, "Reset"):
                    rec.Reset()
            elif not self._stop.is_set():
                self._stop.wait(0.5)
        log.info("Wake(Vosk): остановлен (words=%s)", words)


# ── Синглтоны ───────────────────────────────────────────────────────────────
_TTS: Optional[OfflineTTS] = None
_STT: Optional[VoskSTT] = None
_SINGLETON_LOCK = threading.Lock()


def get_tts() -> OfflineTTS:
    global _TTS
    with _SINGLETON_LOCK:
        if _TTS is None:
            _TTS = OfflineTTS()
        return _TTS


def get_stt() -> VoskSTT:
    global _STT
    with _SINGLETON_LOCK:
        if _STT is None:
            _STT = VoskSTT()
        return _STT


def speak(text: str, sink: Optional[str] = None) -> bool:
    """Модульный API: озвучить текст офлайн (блокирующе)."""
    return get_tts().speak(text, sink)


def listen(timeout: float = 8.0, phrase_limit: float = 8.0) -> str:
    """Модульный API: распознать одну фразу с микрофона (офлайн)."""
    return get_stt().listen(timeout=timeout, phrase_limit=phrase_limit)


def status() -> dict:
    tts = get_tts()
    stt = get_stt()
    return {
        "voice_enabled": voice_enabled(),
        "tts_backend": tts.backend,
        "piper_model": tts.model_path,
        "stt_backend": "vosk" if stt.available else "none",
        "vosk_model": stt.model_path,
        "recorder": next((p for p in ("parecord", "pw-record", "arecord") if shutil.which(p)), None),
        "player": next((p for p in ("paplay", "pw-play", "aplay") if shutil.which(p)), None),
    }
