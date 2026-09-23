"""Офлайн-голос: Piper TTS, Vosk STT, микрофон, wake word — без железа (всё замокано)."""
import importlib.machinery
import json
import os
import sys
import types
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.interface import offline_voice as ov


# ── helpers ─────────────────────────────────────────────────────────────────
def _fake_piper_module(calls):
    mod = types.ModuleType("piper")

    class _Voice:
        @classmethod
        def load(cls, path):
            calls.append(("load", path))
            return cls()

        def synthesize_wav(self, text, wav_file):
            calls.append(("synth", text))
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"\x00\x00" * 2205)

    mod.PiperVoice = _Voice
    mod.__spec__ = importlib.machinery.ModuleSpec("piper", None)
    return mod


class _FakeRecognizer:
    """Имитация KaldiRecognizer: каждый чанк — готовая фраза из очереди."""

    def __init__(self, phrases):
        self.phrases = list(phrases)
        self._last = ""
        self.resets = 0

    def AcceptWaveform(self, _data):
        if self.phrases:
            self._last = self.phrases.pop(0)
            return self._last is not None
        self._last = ""
        return False

    def Result(self):
        return json.dumps({"text": self._last or ""})

    def PartialResult(self):
        return json.dumps({"partial": ""})

    def FinalResult(self):
        return json.dumps({"text": ""})

    def Reset(self):
        self.resets += 1


class _FakeMic:
    def __init__(self, n_chunks):
        self.n = n_chunks
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def chunks(self):
        for _ in range(self.n):
            yield b"\x00\x00" * 4000

    def close(self):
        self.closed = True


# ── config / text ───────────────────────────────────────────────────────────
def test_voice_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARGOS_VOICE", raising=False)
    assert ov.voice_enabled() is False
    monkeypatch.setenv("ARGOS_VOICE", "on")
    assert ov.voice_enabled() is True


def test_model_paths_follow_env(monkeypatch, tmp_path):
    monkeypatch.delenv("ARGOS_PIPER_MODEL", raising=False)
    monkeypatch.delenv("ARGOS_VOSK_MODEL", raising=False)
    monkeypatch.delenv("VOSK_MODEL_PATH", raising=False)
    monkeypatch.setenv("ARGOS_MODELS_DIR", str(tmp_path))
    (tmp_path / "vosk" / "vosk-model-small-ru-0.22").mkdir(parents=True)
    assert ov.piper_model_path() == str(tmp_path / "piper" / "ru_RU-medium.onnx")
    assert ov.vosk_model_path() == str(tmp_path / "vosk" / "vosk-model-small-ru-0.22")
    monkeypatch.setenv("ARGOS_VOSK_MODEL", "/x/y")
    assert ov.vosk_model_path() == "/x/y"


def test_clean_text_strips_emoji_markdown_and_urls():
    out = ov.clean_text_for_speech("🔊 **Готово**: см. https://example.com/x `code`")
    assert "🔊" not in out and "*" not in out and "http" not in out
    assert "Готово" in out and "ссылка" in out


def test_clean_text_truncates_on_sentence(monkeypatch):
    text = "Первое предложение. " * 20
    out = ov.clean_text_for_speech(text, max_chars=100)
    assert len(out) <= 100 and out.endswith(".")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("аргос какая температура дома", "какая температура дома"),
        ("аргос", ""),
        ("эй аргоса включи свет", "включи свет"),
        ("какая погода", None),
        ("", None),
    ],
)
def test_split_wake_phrase(monkeypatch, text, expected):
    monkeypatch.delenv("ARGOS_WAKE_WORDS", raising=False)
    assert ov.split_wake_phrase(text) == expected


# ── TTS ─────────────────────────────────────────────────────────────────────
def test_tts_piper_synthesizes_and_plays_then_deletes_wav(monkeypatch, tmp_path):
    model = tmp_path / "ru_RU-medium.onnx"
    model.write_bytes(b"onnx")
    calls = []
    monkeypatch.setitem(sys.modules, "piper", _fake_piper_module(calls))
    monkeypatch.setattr(ov.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "paplay" else None)
    played = []

    def _run(cmd, **kw):
        played.append(list(cmd))
        assert os.path.exists(cmd[-1])  # WAV существует во время воспроизведения
        with wave.open(cmd[-1]) as w:
            assert w.getframerate() == 22050
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ov.subprocess, "run", _run)
    tts = ov.OfflineTTS(model_path=str(model), sink="")
    assert tts.backend == "piper"
    assert tts.speak("Привет 👋, я Аргос") is True
    synth = [c[1] for c in calls if c[0] == "synth"]
    assert synth and "👋" not in synth[0] and "Аргос" in synth[0]
    assert played and played[0][0] == "paplay"
    assert not os.path.exists(played[0][-1])  # временный файл удалён
    assert tts.last_synth_seconds is not None
    assert tts.speaking is False


def test_tts_sink_passed_to_player(monkeypatch, tmp_path):
    monkeypatch.setattr(ov.shutil, "which", lambda n: f"/usr/bin/{n}" if n == "paplay" else None)
    tts = ov.OfflineTTS(model_path=str(tmp_path / "missing.onnx"), sink="bluez_output.X")
    assert tts.player_command("/tmp/a.wav") == ["paplay", "--device=bluez_output.X", "/tmp/a.wav"]
    assert tts.player_command("/tmp/a.wav", sink="alsa_output.Y")[1] == "--device=alsa_output.Y"


def test_tts_pw_play_when_no_paplay(monkeypatch, tmp_path):
    monkeypatch.setattr(ov.shutil, "which", lambda n: f"/usr/bin/{n}" if n == "pw-play" else None)
    tts = ov.OfflineTTS(model_path=str(tmp_path / "missing.onnx"), sink="s1")
    assert tts.player_command("/a.wav") == ["pw-play", "--target", "s1", "/a.wav"]


def test_tts_falls_back_to_espeak(monkeypatch, tmp_path):
    monkeypatch.setattr(ov.shutil, "which", lambda n: f"/usr/bin/{n}" if n in ("espeak-ng", "paplay") else None)
    runs = []
    monkeypatch.setattr(ov.subprocess, "run", lambda cmd, **kw: runs.append(cmd) or SimpleNamespace(returncode=0))
    tts = ov.OfflineTTS(model_path=str(tmp_path / "missing.onnx"))
    assert tts.backend == "espeak-ng"
    assert tts.speak("проверка") is True
    assert runs[0][:3] == ["espeak-ng", "-v", "ru"]
    assert runs[1][0] == "paplay"


def test_tts_none_backend_logs_and_returns_false(monkeypatch, tmp_path):
    monkeypatch.setattr(ov.shutil, "which", lambda n: None)
    run = MagicMock()
    monkeypatch.setattr(ov.subprocess, "run", run)
    tts = ov.OfflineTTS(model_path=str(tmp_path / "missing.onnx"))
    assert tts.backend == "none" and tts.available is False
    assert tts.speak("текст") is False
    run.assert_not_called()


# ── Mic / levels ────────────────────────────────────────────────────────────
def test_level_dbfs_and_highpass_removes_dc():
    np = pytest.importorskip("numpy")
    silence = np.zeros(16000, dtype="<i2").tobytes()
    assert ov.level_dbfs(silence)["rms_dbfs"] <= -100
    full = (np.ones(16000) * 32767).astype("<i2").tobytes()
    assert ov.level_dbfs(full)["peak_dbfs"] > -0.1
    dc = (np.ones(32000) * 8000).astype("<i2").tobytes()
    hpf = ov.HighPassFilter()
    out = hpf.process(dc)
    tail = np.frombuffer(out, dtype="<i2")[-4000:]
    assert abs(float(tail.mean())) < 50  # DC подавлен


def test_record_seconds_always_terminates_recorder(monkeypatch):
    monkeypatch.setattr(ov.shutil, "which", lambda n: f"/usr/bin/{n}" if n == "parecord" else None)
    proc = MagicMock()
    proc.stdout.read.side_effect = lambda n: b"\x01\x00" * (n // 2)
    popen = MagicMock(return_value=proc)
    monkeypatch.setattr(ov.subprocess, "Popen", popen)
    data = ov.record_seconds(0.5, highpass=False)
    assert len(data) == 16000
    cmd = popen.call_args[0][0]
    assert cmd[0] == "parecord" and "--rate=16000" in cmd and "--channels=1" in cmd
    proc.terminate.assert_called_once()


def test_mic_stream_uses_configured_source(monkeypatch):
    monkeypatch.setattr(ov.shutil, "which", lambda n: f"/usr/bin/{n}" if n == "pw-record" else None)
    mic = ov.MicStream(source="alsa_input.test")
    cmd = mic.command()
    assert cmd[0] == "pw-record" and "--target" in cmd and "alsa_input.test" in cmd and cmd[-1] == "-"


# ── STT ─────────────────────────────────────────────────────────────────────
def _install_fake_vosk(monkeypatch, phrases):
    mod = types.ModuleType("vosk")
    mod.SetLogLevel = lambda lvl: None
    mod.Model = lambda path: SimpleNamespace(path=path)
    created = []

    def _rec(model, rate, grammar=None):
        r = _FakeRecognizer(phrases)
        r.rate = rate
        created.append(r)
        return r

    mod.KaldiRecognizer = _rec
    monkeypatch.setitem(sys.modules, "vosk", mod)
    return created


def test_vosk_transcribe_wav(monkeypatch, tmp_path):
    created = _install_fake_vosk(monkeypatch, ["аргос какая температура дома"])
    wav_path = tmp_path / "t.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 4000)
    stt = ov.VoskSTT(model_path=str(tmp_path))
    assert stt.transcribe_wav(str(wav_path)) == "аргос какая температура дома"
    assert created[0].rate == 22050


def test_vosk_listen_returns_first_final_phrase(monkeypatch, tmp_path):
    _install_fake_vosk(monkeypatch, [None, "включи свет"])
    stt = ov.VoskSTT(model_path=str(tmp_path))
    monkeypatch.setattr(ov.VoskSTT, "available", property(lambda self: True))
    mic = _FakeMic(5)
    assert stt.listen(timeout=5, mic_factory=lambda: mic) == "включи свет"
    assert mic.closed is True


def test_vosk_listen_unavailable_returns_empty(tmp_path):
    stt = ov.VoskSTT(model_path=str(tmp_path / "nope"))
    assert stt.listen() == ""
    assert "Vosk недоступен" in stt.last_error


# ── Wake loop ───────────────────────────────────────────────────────────────
def _run_loop(phrases, n_chunks=6, tts=None):
    stt = MagicMock()
    rec = _FakeRecognizer(phrases)
    stt.recognizer.return_value = rec
    commands, wakes = [], []
    loop_ref = {}

    def on_command(text):
        commands.append(text)
        loop_ref["loop"]._stop.set()

    loop = ov.VoskWakeLoop(stt, on_command=on_command, on_wake=lambda: wakes.append(1), tts=tts,
                           mic_factory=lambda: _FakeMic(n_chunks))
    loop_ref["loop"] = loop
    # Один проход: после исчерпания чанков — стоп.
    loop._stop.wait = lambda t=None: loop._stop.set() or True
    loop.run()
    return commands, wakes, loop, rec


def test_wake_loop_inline_command():
    commands, wakes, loop, _ = _run_loop(["какая погода", "аргос какая температура дома"])
    assert commands == ["какая температура дома"]
    assert loop.detections == 1 and wakes == []


def test_wake_loop_wake_then_command():
    commands, wakes, loop, _ = _run_loop(["аргос", "включи свет"])
    assert wakes == [1]
    assert commands == ["включи свет"]


def test_wake_loop_ignores_audio_while_speaking():
    tts = SimpleNamespace(speaking=True)
    commands, wakes, loop, rec = _run_loop(["аргос включи свет"], tts=tts)
    assert commands == [] and loop.detections == 0 and rec.resets > 0


# ── WakeWordListener (core integration) ─────────────────────────────────────
def test_listener_refuses_when_voice_off(monkeypatch):
    from src.connectivity.wake_word import WakeWordListener

    monkeypatch.setenv("ARGOS_VOICE", "off")
    factory = MagicMock()
    lst = WakeWordListener(SimpleNamespace(), loop_factory=factory)
    assert "ARGOS_VOICE=off" in lst.start()
    factory.assert_not_called()


def test_listener_starts_and_routes_command(monkeypatch):
    from src.connectivity import wake_word

    monkeypatch.setenv("ARGOS_VOICE", "on")
    monkeypatch.setattr(ov.VoskSTT, "available", property(lambda self: True))
    fake_loop = MagicMock()
    fake_loop.running = False
    factory = MagicMock(return_value=fake_loop)
    spoken = []
    tts = SimpleNamespace(speak=lambda t: spoken.append(t) or True, speaking=False)
    core = SimpleNamespace(
        _offline_tts=tts,
        voice_on=False,
        process_logic=MagicMock(return_value={"answer": "Дома 22 градуса", "state": "Direct"}),
    )
    lst = wake_word.WakeWordListener(core, admin="A", flasher=None, loop_factory=factory)
    msg = lst.start()
    assert "активен" in msg
    fake_loop.start.assert_called_once()
    on_command = factory.call_args.kwargs["on_command"]
    on_command("какая температура дома")
    core.process_logic.assert_called_once_with("какая температура дома", "A", None)
    assert spoken == ["Дома 22 градуса"]
    assert "микрофон закрыт" in lst.stop()
    fake_loop.stop.assert_called_once()


def test_listener_does_not_double_speak_when_voice_on(monkeypatch):
    from src.connectivity import wake_word

    spoken = []
    core = SimpleNamespace(
        _offline_tts=SimpleNamespace(speak=lambda t: spoken.append(t)),
        voice_on=True,  # process_logic сам вызывает core.say()
        process_logic=lambda *a: {"answer": "ok"},
    )
    lst = wake_word.WakeWordListener(core)
    lst._on_command("тест")
    assert spoken == [] and lst.last_answer == "ok"


def test_status_reports_backends(monkeypatch, tmp_path):
    monkeypatch.setattr(ov, "_TTS", ov.OfflineTTS(model_path=str(tmp_path / "none.onnx")))
    monkeypatch.setattr(ov, "_STT", ov.VoskSTT(model_path=str(tmp_path / "none")))
    st = ov.status()
    assert st["stt_backend"] == "none"
    assert set(st) >= {"voice_enabled", "tts_backend", "piper_model", "vosk_model", "recorder", "player"}
