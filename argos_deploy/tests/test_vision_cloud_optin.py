"""Constructor-only source extraction; no SDK import, model or camera access."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import pytest

@pytest.mark.parametrize('flag,expected', [(None,False),('false',False),('garbage',False),('true',True)])
def test_cloud_vision_requires_explicit_optin_even_with_key(monkeypatch, flag, expected):
    if flag is None:
        monkeypatch.delenv('ARGOS_VISION_CLOUD_ENABLED',raising=False)
    else:
        monkeypatch.setenv('ARGOS_VISION_CLOUD_ENABLED',flag)
    path=Path(__file__).resolve().parents[1]/'src/vision/argos_vision.py'
    cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='ArgosVision')
    init=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
    calls=[]
    def factory(**kwargs): calls.append(True); return object()
    namespace={'os':os,'_ON':{'1','true','on','yes','да','вкл'},'_gemini_disabled':lambda:False,
               'GEMINI_OK':True,'genai_sdk':SimpleNamespace(Client=factory),
               'log':SimpleNamespace(info=lambda *args:None)}
    exec(compile(ast.Module(body=[init],type_ignores=[]),str(path),'exec'),namespace)
    instance=SimpleNamespace(ollama_model='local')
    namespace['__init__'](instance,api_key='test-not-a-real-key')
    assert bool(calls) is expected
    assert (instance._client is not None) is expected
