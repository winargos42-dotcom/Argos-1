"""Связка ArgosCore ↔ офлайн-голос/камера (без железа)."""
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.core import ArgosCore


def test_src_vision_exports_full_argos_vision():
    # Регрессия: пакет src/vision/ раньше отдавал ShadowVision под именем ArgosVision.
    from src.vision import ArgosVision, ShadowVision

    for name in ("look_through_camera", "look_at_screen", "analyze_image", "analyze_file", "camera_report"):
        assert hasattr(ArgosVision, name), name
    assert ShadowVision is None or ArgosVision is not ShadowVision


def test_wake_word_listener_importable_from_core_path():
    from src.connectivity.wake_word import WakeWordListener  # то, что импортирует core.start_wake_word

    assert callable(WakeWordListener)


def test_say_prefers_offline_tts():
    tts = MagicMock()
    dummy = SimpleNamespace(voice_on=True, _offline_tts=tts, _tts_engine=None, _tts_lock=threading.Lock())
    ArgosCore.say(dummy, "Привет")
    tts.speak_async.assert_called_once_with("Привет")


def test_say_silent_when_voice_off():
    tts = MagicMock()
    dummy = SimpleNamespace(voice_on=False, _offline_tts=tts, _tts_engine=None, _tts_lock=threading.Lock())
    ArgosCore.say(dummy, "Привет")
    tts.speak_async.assert_not_called()


def test_start_wake_word_respects_argos_voice_off(monkeypatch):
    monkeypatch.setenv("ARGOS_VOICE", "off")
    dummy = SimpleNamespace(_wake=None)
    msg = ArgosCore.start_wake_word(dummy, None, None)
    assert "ARGOS_VOICE=off" in msg


def test_stop_wake_word_when_not_started():
    assert "не запущен" in ArgosCore.stop_wake_word(SimpleNamespace(_wake=None))


def test_voice_services_report_mentions_offline_backends(monkeypatch):
    from src.interface import offline_voice as ov

    monkeypatch.setattr(ov, "status", lambda: {
        "voice_enabled": False, "tts_backend": "piper", "stt_backend": "vosk",
        "piper_model": "p", "vosk_model": "v", "recorder": "parecord", "player": "paplay",
    })
    dummy = SimpleNamespace(voice_on=False, _tts_engine=None, _wake=None)
    text = ArgosCore.voice_services_report(dummy)
    assert "[piper]" in text and "vosk (офлайн)" in text
    assert "ARGOS_VOICE=off" in text
