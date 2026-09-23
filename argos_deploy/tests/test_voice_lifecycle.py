"""Voice lifecycle regressions; pipes and transport doubles, no audio devices."""
import ast
import logging
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.interface import offline_voice as ov


def voice_methods():
    """Compile the actual narrow core methods, avoiding the src/core package shadow."""
    path = Path(__file__).resolve().parents[1] / 'src/core.py'
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ArgosCore')
    names = {'_init_voice', 'start_wake_word', 'stop_wake_word', 'listen'}
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in methods} == names
    namespace = {'os': os, 'threading': threading, 'log': logging.getLogger('voice-test'),
                 'PYTTSX3_OK': False, 'SR_OK': False, 'sr': None}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


def test_entered_autostart_cannot_publish_after_stop(monkeypatch):
    methods = voice_methods()
    timers = []
    class Timer:
        def __init__(self, delay, callback):
            self.callback = callback
            timers.append(self)
        def start(self):
            pass
        def cancel(self):
            pass  # A callback already entered Timer.run cannot be recalled.
    monkeypatch.setenv('ARGOS_VOICE', 'on')
    monkeypatch.setattr(threading, 'Timer', Timer)
    monkeypatch.setattr(ov, 'get_tts', lambda: SimpleNamespace(available=True, backend='test'))
    started = threading.Event()
    gate_attempted = threading.Event()
    lock = threading.RLock()
    class ObservedLock:
        def __enter__(self):
            if threading.current_thread() is not threading.main_thread():
                gate_attempted.set()
            lock.acquire()
        def __exit__(self, *args):
            lock.release()
    core = SimpleNamespace(_wake=None, voice_on=False,
                           start_wake_word=MagicMock(side_effect=lambda *args: started.set()))
    methods['_init_voice'](core)
    core._voice_lifecycle_lock = ObservedLock()
    callback = threading.Thread(target=timers[0].callback, daemon=True)
    with lock:
        callback.start()
        # New code reaches the lock; old code calls start immediately.
        for _ in range(100):
            if gate_attempted.wait(0.005) or started.is_set():
                break
        methods['stop_wake_word'](core)
    callback.join(1)
    assert not callback.is_alive()
    core.start_wake_word.assert_not_called()


def test_stop_cancels_pending_autostart(monkeypatch):
    core_methods = voice_methods()
    timers = []
    class Timer:
        def __init__(self, delay, callback):
            self.delay, self.callback, self.cancelled = delay, callback, False
            timers.append(self)
        def start(self):
            pass
        def cancel(self):
            self.cancelled = True
        def fire(self):
            if not self.cancelled:
                self.callback()
    monkeypatch.setenv('ARGOS_VOICE', 'on')
    monkeypatch.setenv('ARGOS_VOICE_AUTOSTART_DELAY', '20')
    monkeypatch.setattr(threading, 'Timer', Timer)
    monkeypatch.setattr(ov, 'get_tts', lambda: SimpleNamespace(available=True, backend='test'))
    core = SimpleNamespace(_wake=None, voice_on=False, start_wake_word=MagicMock())
    core_methods['_init_voice'](core)
    assert len(timers) == 1 and timers[0].delay == 20
    core_methods['stop_wake_word'](core)
    timers[0].fire()
    core.start_wake_word.assert_not_called()
    assert timers[0].cancelled


def test_silent_recorder_read_has_short_deadline():
    read_fd, write_fd = os.pipe()
    mic = ov.MicStream(highpass=False)
    stream = os.fdopen(read_fd, 'rb', buffering=0)
    mic._proc = SimpleNamespace(stdout=stream)
    # Watchdog bounds the old blocking implementation so RED cannot hang pytest.
    watchdog = threading.Timer(2, lambda: os.close(write_fd))
    watchdog.start()
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            mic.read()
        assert time.monotonic() - started < 1.5
    finally:
        watchdog.join(3)
        stream.close()


def test_empty_local_stt_never_falls_back_to_cloud_without_opt_in(monkeypatch):
    core_methods = voice_methods()
    monkeypatch.delenv('ARGOS_VOICE_CLOUD_ENABLED', raising=False)
    monkeypatch.setattr(ov, 'get_stt', lambda: SimpleNamespace(available=True, listen=lambda **kw: ''))
    remote = MagicMock()
    remote.Recognizer.return_value.recognize_google.return_value = 'cloud transcription'
    core_methods.update(SR_OK=True, sr=remote)
    assert core_methods['listen'](SimpleNamespace()) == ''
    remote.Recognizer.assert_not_called()
