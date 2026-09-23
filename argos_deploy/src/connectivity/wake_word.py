"""
wake_word.py -- Wake Word "Argos" voice activation.
Backends: porcupine > vosk (офлайн, parecord/PipeWire) > speech_recognition > simulation.

WakeWordListener(core, admin, flasher) — полный голосовой контур для ArgosCore:
«Аргос» → команда (Vosk) → core.process_logic → ответ голосом (Piper).
Работает только при ARGOS_VOICE=on: без явного включения микрофон не слушается.
"""

import os, threading, time
from typing import Callable, Optional
from src.argos_logger import get_logger

log = get_logger("argos.wakeword")

try:
    import pvporcupine as _porc  # type: ignore

    PORCUPINE_OK = True
except ImportError:
    _porc = None
    PORCUPINE_OK = False

try:
    from vosk import KaldiRecognizer, Model as VoskModel  # type: ignore

    VOSK_OK = True
except ImportError:
    KaldiRecognizer = VoskModel = None
    VOSK_OK = False

try:
    import speech_recognition as _sr  # type: ignore

    SR_OK = True
except ImportError:
    _sr = None
    SR_OK = False

try:
    import sounddevice as _sd  # type: ignore

    AUDIO_OK = True
except ImportError:
    _sd = None
    AUDIO_OK = False

WAKE_WORDS = ["аргос", "argos", "привет аргос", "эй аргос", "аргос слушай"]


class WakeWordDetector:
    """
    Детектор wake word «Аргос».
    Бэкенд выбирается автоматически по доступным библиотекам.
    """

    def __init__(self, on_detected: Optional[Callable] = None, wake_words: Optional[list] = None):
        self._cb = on_detected
        self._words = [w.lower() for w in (wake_words or WAKE_WORDS)]
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._count = 0
        self._backend = self._pick_backend()
        log.info("WakeWord: backend=%s words=%s", self._backend, self._words[:2])

    @staticmethod
    def _pick_backend() -> str:
        if PORCUPINE_OK and os.getenv("PICOVOICE_ACCESS_KEY"):
            return "porcupine"
        if VOSK_OK and _offline_vosk_ready():
            return "vosk"
        if SR_OK:
            return "sr_google"
        return "simulation"

    @property
    def backend(self) -> str:
        return self._backend

    def start(self) -> str:
        if self._running:
            return "Wake Word: уже запущен."
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="argos-ww")
        self._thread.start()
        return f"Wake Word активен (backend={self._backend}). Скажи «Аргос»."

    def stop(self) -> str:
        self._running = False
        loop = getattr(self, "_vosk_loop", None)
        if loop is not None:
            loop.stop()
        return "Wake Word остановлен."

    def _loop(self):
        {
            "porcupine": self._loop_porcupine,
            "vosk": self._loop_vosk,
            "sr_google": self._loop_sr,
            "simulation": self._loop_sim,
        }.get(self._backend, self._loop_sim)()

    def _loop_porcupine(self):
        try:
            import struct, pyaudio  # type: ignore

            key = os.getenv("PICOVOICE_ACCESS_KEY", "")
            p = _porc.create(access_key=key, keywords=["porcupine"])
            pa = pyaudio.PyAudio()
            stream = pa.open(
                rate=p.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=p.frame_length,
            )
            while self._running:
                pcm = stream.read(p.frame_length, exception_on_overflow=False)
                if p.process(struct.unpack_from("h" * p.frame_length, pcm)) >= 0:
                    self._fire("porcupine")
            stream.close()
            pa.terminate()
            p.delete()
        except Exception as e:
            log.warning("Porcupine: %s -> sr_google", e)
            self._backend = "sr_google"
            self._loop_sr()

    def _loop_vosk(self):
        from src.interface.offline_voice import VoskWakeLoop, get_stt

        stt = get_stt()
        if not stt.available:
            log.warning("Vosk model not found: %s -> simulation", stt.model_path)
            self._backend = "simulation"
            self._loop_sim()
            return
        # Детектор только сигнализирует о wake word; команды обрабатывает WakeWordListener.
        self._vosk_loop = VoskWakeLoop(
            stt,
            on_command=lambda _cmd: self._fire("vosk"),
            on_wake=lambda: self._fire("vosk"),
        )
        self._vosk_loop.start()
        while self._running and self._vosk_loop.running:
            time.sleep(0.5)
        self._vosk_loop.stop()

    def _loop_sr(self):
        if not SR_OK:
            self._backend = "simulation"
            self._loop_sim()
            return
        recognizer = _sr.Recognizer()
        recognizer.energy_threshold = int(os.getenv("ARGOS_SR_ENERGY", "300"))
        while self._running:
            try:
                with _sr.Microphone() as src:
                    recognizer.adjust_for_ambient_noise(src, duration=0.3)
                    audio = recognizer.listen(src, timeout=5, phrase_time_limit=4)
                try:
                    txt = recognizer.recognize_google(audio, language="ru-RU").lower()
                    if any(w in txt for w in self._words):
                        self._fire("sr_google")
                except _sr.UnknownValueError:
                    pass
                except Exception as e:
                    time.sleep(2)
            except Exception:
                time.sleep(1)

    def _loop_sim(self):
        interval = float(os.getenv("ARGOS_WAKE_SIM_INTERVAL", "0"))
        if interval <= 0:
            while self._running:
                time.sleep(1)
            return
        while self._running:
            time.sleep(interval)
            if self._running:
                self._fire("simulation")

    def _fire(self, src: str = "?"):
        self._count += 1
        log.info("WAKE WORD #%d (src=%s)", self._count, src)
        if self._cb:
            try:
                self._cb()
            except Exception as e:
                log.error("Wake cb: %s", e)

    def trigger(self):
        self._fire("manual")

    def status(self) -> str:
        return (
            f"Wake Word: running={self._running}  backend={self._backend}  "
            f"detected={self._count}"
        )


WakeWord = WakeWordDetector


def _offline_vosk_ready() -> bool:
    try:
        from src.interface.offline_voice import get_stt
        import shutil

        recorder = any(shutil.which(p) for p in ("parecord", "pw-record", "arecord"))
        return recorder and get_stt().available
    except Exception:
        return False


class WakeWordListener:
    """
    Голосовой контур ArgosCore (интерфейс, который ожидает core.start_wake_word):
      WakeWordListener(core, admin, flasher).start() / .stop() / .status()
    «Аргос» → (короткое «Слушаю») → команда → core.process_logic → ответ голосом.
    """

    def __init__(self, core, admin=None, flasher=None, loop_factory=None):
        self.core = core
        self.admin = admin
        self.flasher = flasher
        self._loop_factory = loop_factory
        self._loop = None
        self.last_command: Optional[str] = None
        self.last_answer: Optional[str] = None

    @property
    def running(self) -> bool:
        return bool(self._loop and self._loop.running)

    def _tts(self):
        tts = getattr(self.core, "_offline_tts", None)
        if tts is None:
            from src.interface.offline_voice import get_tts

            tts = get_tts()
        return tts

    def _on_wake(self) -> None:
        try:
            self._tts().speak("Слушаю")
        except Exception as e:
            log.debug("wake ack: %s", e)

    def _on_command(self, text: str) -> None:
        self.last_command = text
        log.info("Голосовая команда: %s", text)
        answer = ""
        try:
            result = self.core.process_logic(text, self.admin, self.flasher)
            answer = result.get("answer", "") if isinstance(result, dict) else str(result or "")
        except Exception as e:
            answer = f"Ошибка обработки команды: {e}"
            log.error("Voice command: %s", e)
        self.last_answer = answer
        # process_logic сам вызывает core.say() при voice_on; иначе озвучиваем явно.
        if answer and not getattr(self.core, "voice_on", False):
            self._tts().speak(answer)

    def start(self) -> str:
        from src.interface.offline_voice import VoskWakeLoop, get_stt, voice_enabled

        if not voice_enabled():
            return (
                "🔇 Голосовой контур выключен (ARGOS_VOICE=off). "
                "Включи ARGOS_VOICE=on в argos.env и перезапусти сервис."
            )
        if self.running:
            return "Wake Word: уже запущен."
        stt = get_stt()
        if not stt.available:
            return f"❌ Wake Word: нет модели Vosk ({stt.model_path}) или пакета vosk."
        factory = self._loop_factory or VoskWakeLoop
        self._loop = factory(stt, on_command=self._on_command, on_wake=self._on_wake, tts=self._tts())
        self._loop.start()
        return "👂 Wake Word активен (Vosk, офлайн). Скажи «Аргос» и команду."

    def stop(self) -> str:
        if self._loop is not None:
            self._loop.stop()
        return "Wake Word остановлен, микрофон закрыт."

    def status(self) -> str:
        detections = getattr(self._loop, "detections", 0) if self._loop else 0
        return f"Wake Word: running={self.running} backend=vosk detected={detections}"
