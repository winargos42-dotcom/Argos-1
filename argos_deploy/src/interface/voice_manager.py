"""
voice_manager.py — Унифицированный голосовой менеджер для Android/desktop.

TTS приоритет:
  1) Android TextToSpeech через pyjnius
  2) Piper / espeak-ng офлайн через PipeWire (Linux desktop, offline_voice)
  3) plyer.tts
  4) pyttsx3 (desktop fallback)

STT приоритет:
  1) Android SpeechRecognizer / RecognizerIntent (pyjnius)
  2) Vosk офлайн (Linux desktop, offline_voice)
  3) speech_recognition (Google Speech)
"""

from __future__ import annotations

import os
import threading
from typing import Optional, Tuple

# ── Optional dependencies ------------------------------------------------------
try:
    from jnius import autoclass, cast, PythonJavaClass, java_method  # type: ignore

    JNIUS_OK = True
except Exception:
    autoclass = cast = PythonJavaClass = java_method = None  # type: ignore
    JNIUS_OK = False

try:
    from plyer import tts as plyer_tts  # type: ignore

    PLYER_OK = True
except Exception:
    plyer_tts = None
    PLYER_OK = False

try:
    import pyttsx3  # type: ignore

    PYTTSX3_OK = True
except Exception:
    pyttsx3 = None  # type: ignore
    PYTTSX3_OK = False

try:
    import speech_recognition as _sr  # type: ignore

    SR_OK = True
except Exception:
    _sr = None  # type: ignore
    SR_OK = False


IS_ANDROID = (
    "ANDROID_ARGUMENT" in os.environ
    or "ANDROID_ARGUMENTS" in os.environ
    or "ANDROID_ROOT" in os.environ
)
# Energy threshold for speech_recognition microphone calibration
# (higher = less sensitive to ambient noise). 300 matches SR defaults.
DEFAULT_ENERGY_THRESHOLD = 300
DEFAULT_LANG = os.getenv("ARGOS_VOICE_LANGUAGE", os.getenv("ARGOS_VOICE_LANG", "ru-RU"))


if JNIUS_OK:

    class _RecognizerListener(PythonJavaClass):  # type: ignore[misc]
        """Listener для Android SpeechRecognizer."""

        __javainterfaces__ = ["android/speech/RecognitionListener"]
        __javacontext__ = "app"

    def __init__(self, intent_class):
        super().__init__()
        self.text: Optional[str] = None
        self.error: Optional[str] = None
        self._event = threading.Event()
        self._intent = intent_class

        # Все методы интерфейса (большинство — заглушки)
        @java_method("(Landroid/os/Bundle;)V")
        def onReadyForSpeech(self, _params):  # noqa: N802
            pass

        @java_method("()V")
        def onBeginningOfSpeech(self):  # noqa: N802
            pass

        @java_method("(F)V")
        def onRmsChanged(self, _rms):  # noqa: N802
            pass

        @java_method("([B)V")
        def onBufferReceived(self, _buffer):  # noqa: N802
            pass

        @java_method("()V")
        def onEndOfSpeech(self):  # noqa: N802
            if not self._event.is_set():
                self._event.set()

        @java_method("(I)V")
        def onError(self, error):  # noqa: N802
            self.error = f"Recognizer error {error}"
            self._event.set()

        @java_method("(Landroid/os/Bundle;)V")
        def onResults(self, bundle):  # noqa: N802
            try:
                results = bundle.getStringArrayList(
                    autoclass("android.speech.RecognizerIntent").EXTRA_RESULTS
                )
                if results and results.size() > 0:
                    self.text = str(results.get(0))
            except Exception as exc:  # pragma: no cover - Android only
                self.error = str(exc)
            self._event.set()

        @java_method("(Landroid/os/Bundle;)V")
        def onPartialResults(self, bundle):  # noqa: N802
            try:
                results = bundle.getStringArrayList(
                    autoclass("android.speech.RecognizerIntent").EXTRA_RESULTS
                )
                if results and results.size() > 0 and not self.text:
                    self.text = str(results.get(0))
            except Exception:
                pass

        @java_method("(ILandroid/os/Bundle;)V")
        def onEvent(self, _event_type, _params):  # noqa: N802
            pass

        def wait_result(self, timeout: float = 10.0) -> Tuple[Optional[str], Optional[str]]:
            self._event.wait(timeout)
            return self.text, self.error

else:

    class _RecognizerListener:
        """Заглушка для не-Android окружений."""

        def __init__(self, *_args, **_kwargs):
            self.text: Optional[str] = None
            self.error: Optional[str] = "STT backend unavailable"

        def wait_result(self, timeout: float = 0.0) -> Tuple[Optional[str], Optional[str]]:
            return self.text, self.error


class VoiceManager:
    """Универсальный помощник для TTS/STT."""

    def __init__(self, lang: Optional[str] = None):
        self.lang = lang or DEFAULT_LANG
        self.tts_enabled = True
        self._last_error: Optional[str] = None
        self._offline_tts = None
        self._backend = self._init_tts_backend()
        self._sr_recognizer = _sr.Recognizer() if SR_OK else None
        if self._sr_recognizer:
            self._sr_recognizer.energy_threshold = DEFAULT_ENERGY_THRESHOLD

    # ── TTS ------------------------------------------------------------------
    def _init_tts_backend(self) -> str:
        """Инициализирует доступный TTS backend."""
        if IS_ANDROID and JNIUS_OK:
            try:
                self._PythonActivity = autoclass("org.kivy.android.PythonActivity")
                self._TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
                self._Locale = autoclass("java.util.Locale")
                self._android_ctx = self._PythonActivity.mActivity
                self._android_tts = self._TextToSpeech(self._android_ctx, None)
                try:
                    locale = self._Locale.forLanguageTag(self.lang)
                    self._android_tts.setLanguage(locale)
                except Exception:
                    pass
                return "android"
            except Exception as exc:
                self._last_error = f"Android TTS init: {exc}"

        if not IS_ANDROID:
            try:
                from src.interface.offline_voice import get_tts

                tts = get_tts()
                if tts.available:
                    self._offline_tts = tts
                    return tts.backend  # "piper" | "espeak-ng"
            except Exception as exc:
                self._last_error = f"offline TTS init: {exc}"

        if PLYER_OK:
            return "plyer"

        if PYTTSX3_OK:
            try:
                self._pyttsx = pyttsx3.init()
                return "pyttsx3"
            except Exception as exc:
                self._last_error = f"pyttsx3 init: {exc}"

        return "none"

    def speak(self, text: str) -> None:
        """Озвучивает текст, если TTS включён."""
        if not self.tts_enabled or not text:
            return
        if self._backend == "android":
            try:
                self._android_tts.speak(text, self._TextToSpeech.QUEUE_ADD, None, "argos-tts")
                return
            except Exception as exc:  # pragma: no cover - Android only
                self._last_error = f"TTS android: {exc}"
        if self._backend in ("piper", "espeak-ng"):
            if self._offline_tts.speak(text):
                return
            self._last_error = f"TTS {self._backend}: {self._offline_tts.last_error}"
            return
        if self._backend == "plyer":
            try:
                plyer_tts.speak(text=text, language=self.lang)
                return
            except Exception as exc:
                self._last_error = f"TTS plyer: {exc}"
        if self._backend == "pyttsx3":
            try:
                self._pyttsx.say(text)
                self._pyttsx.runAndWait()
                return
            except Exception as exc:
                self._last_error = f"TTS pyttsx3: {exc}"

    # ── STT ------------------------------------------------------------------
    def listen(self, timeout: float = 8.0, phrase_limit: float = 6.0) -> str:
        """
        Распознаёт речь и возвращает строку.
        На Android использует SpeechRecognizer, иначе — speech_recognition.
        """
        text = ""
        if IS_ANDROID and JNIUS_OK:
            text = self._listen_android(timeout)
        if not text and not IS_ANDROID:
            text = self._listen_vosk(timeout, phrase_limit)
        if not text and SR_OK:
            text = self._listen_speech_recognition(timeout, phrase_limit)
        return text

    def _listen_android(self, timeout: float) -> str:
        """STT через Android SpeechRecognizer (RecognizerIntent)."""
        try:
            SpeechRecognizer = autoclass("android.speech.SpeechRecognizer")
            Intent = autoclass("android.content.Intent")
            RecognizerIntent = autoclass("android.speech.RecognizerIntent")
            if not hasattr(self, "_PythonActivity"):
                self._PythonActivity = autoclass("org.kivy.android.PythonActivity")
            ctx = self._PythonActivity.mActivity
            sr = SpeechRecognizer.createSpeechRecognizer(ctx)
            intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            intent.putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
            )
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, self.lang)
            intent.putExtra(RecognizerIntent.EXTRA_PROMPT, "ARGOS — слушаю команду...")
            listener = _RecognizerListener(RecognizerIntent)
            sr.setRecognitionListener(listener)
            sr.startListening(intent)
            text, err = listener.wait_result(timeout)
            try:
                sr.stopListening()
                sr.cancel()
                sr.destroy()
            except Exception:
                pass
            if err:
                self._last_error = err
            return text or ""
        except Exception as exc:  # pragma: no cover - Android only
            self._last_error = f"Android STT: {exc}"
            return ""

    def _listen_vosk(self, timeout: float, phrase_limit: float) -> str:
        """Офлайн STT через Vosk (микрофон PipeWire)."""
        try:
            from src.interface.offline_voice import get_stt

            stt = get_stt()
            if not stt.available:
                return ""
            text = stt.listen(timeout=timeout, phrase_limit=phrase_limit)
            if not text and stt.last_error:
                self._last_error = stt.last_error
            return text
        except Exception as exc:
            self._last_error = f"Vosk: {exc}"
            return ""

    def _listen_speech_recognition(self, timeout: float, phrase_limit: float) -> str:
        """
        STT через speech_recognition (Google Speech API).
        phrase_limit — максимальная длительность фразы в секундах.
        """
        recognizer = self._sr_recognizer or _sr.Recognizer()
        if not self._sr_recognizer:
            recognizer.energy_threshold = DEFAULT_ENERGY_THRESHOLD
        try:
            with _sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.3)
                audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            return recognizer.recognize_google(audio, language=self.lang)
        except Exception as exc:
            self._last_error = f"SpeechRecognition: {exc}"
            return ""

    # ── Helpers --------------------------------------------------------------
    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def tts_backend(self) -> str:
        return self._backend
