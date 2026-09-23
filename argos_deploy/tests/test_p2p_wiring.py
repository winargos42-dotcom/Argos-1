"""Extract only wiring functions: no cloud import, core initialization or threads."""
import ast
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from threading import Lock
import pytest

ROOT = Path(__file__).resolve().parents[1]

def extract(path, name, namespace, class_name=None):
    tree = ast.parse((ROOT/path).read_text())
    body = tree.body
    if class_name:
        body = next(n.body for n in body if isinstance(n, ast.ClassDef) and n.name == class_name)
    node = next(n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]

class Bridge:
    def __init__(self, core=None): self.starts=0; self.stops=0
    def start(self): self.starts+=1; return 'running'
    def stop(self): self.stops+=1

def test_core_start_reuses_existing_bridge():
    old = Bridge()
    core = SimpleNamespace(p2p=old)
    method = extract('src/core.py', 'start_p2p', {'ArgosBridge':Bridge, 'log':SimpleNamespace(info=lambda *a:None)}, 'ArgosCore')
    assert method(core) == 'running'
    assert core.p2p is old and old.starts == 1

def test_core_failed_bind_does_not_publish_unstarted_bridge():
    class Failed(Bridge):
        def start(self): raise OSError('occupied')
    core = SimpleNamespace(p2p=None)
    method = extract('src/core.py', 'start_p2p', {'ArgosBridge':Failed, 'log':SimpleNamespace(info=lambda *a:None)}, 'ArgosCore')
    with pytest.raises(OSError): method(core)
    assert core.p2p is None

def test_p2p_startup_gate_and_state_ownership(monkeypatch):
    import os
    function = extract('cloud_entry.py', '_start_p2p_if_enabled', {'os':os})
    app = SimpleNamespace(state=SimpleNamespace(p2p_lifecycle_lock=Lock(), p2p_closing=False))
    core = SimpleNamespace(p2p=None)
    calls = []
    def start(): core.p2p=Bridge(); calls.append(1)
    core.start_p2p=start
    monkeypatch.delenv('ARGOS_P2P_AUTOSTART', raising=False)
    function(core, app)
    assert not calls
    monkeypatch.setenv('ARGOS_P2P_AUTOSTART','true')
    function(core, app)
    assert app.state.p2p is core.p2p and calls == [1]
    app.state.p2p_closing = True
    function(core, app)
    assert calls == [1]

def test_lifespan_always_stops_bridge_even_with_body_exception():
    calls = []
    async def to_thread(func): calls.append(func.__name__); func()
    life = extract('cloud_entry.py','lifespan',{'asynccontextmanager':asynccontextmanager, 'asyncio':SimpleNamespace(to_thread=to_thread)})
    bridge=Bridge()
    app=SimpleNamespace(state=SimpleNamespace(p2p_lifecycle_lock=Lock(), p2p_closing=False, p2p=bridge, task_runner=SimpleNamespace(close=lambda:None)))
    async def scenario():
        with pytest.raises(ValueError):
            async with life(app): raise ValueError('test')
    asyncio.run(scenario())
    assert bridge.stops == 1
    assert calls == ['stop', '<lambda>']

def test_lifespan_stops_voice_even_without_p2p():
    calls=[]
    async def to_thread(func): calls.append(func.__name__); func()
    life=extract('cloud_entry.py','lifespan',{'asynccontextmanager':asynccontextmanager,'asyncio':SimpleNamespace(to_thread=to_thread)})
    def stop_wake_word(): calls.append('voice_stopped')
    app=SimpleNamespace(state=SimpleNamespace(p2p_lifecycle_lock=Lock(),p2p_closing=False,
                                             core=SimpleNamespace(stop_wake_word=stop_wake_word),p2p=None))
    async def scenario():
        async with life(app): pass
    asyncio.run(scenario())
    assert calls==['stop_wake_word','voice_stopped']
