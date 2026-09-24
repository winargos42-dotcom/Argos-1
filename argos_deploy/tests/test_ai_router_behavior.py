from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src import ai_router as module


@pytest.fixture(autouse=True)
def isolated_router(monkeypatch):
    for name in list(module.os.environ):
        if any(word in name for word in ('API_KEY','TOKEN','GEMINI','DEEPSEEK','OLLAMA','GCP_URL')):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(module, '_provider_state', {})
    monkeypatch.setattr(module, 'HAS_COST_OPT', False)


def test_provider_failure_falls_through_and_cooldown_expires(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda:clock[0])
    router = module.AIRouter()
    router.PROVIDERS = ['groq','ollama']
    first = Mock(side_effect=RuntimeError('synthetic outage'))
    second = Mock(return_value='local answer')
    monkeypatch.setattr(router, '_ask_groq', first)
    monkeypatch.setattr(router, '_ask_ollama', second)
    assert router.ask('literal question','literal system') == 'local answer'
    assert router.ask('second') == 'local answer'
    assert first.call_count == 1
    assert module._is_available('groq') is False
    clock[0] += module._COOLDOWN + 1
    first.side_effect = None
    first.return_value = 'recovered'
    assert router.ask('third') == 'recovered'
    assert 'groq' not in module._provider_state
    assert second.call_count == 2


def test_cache_hit_skips_providers_and_miss_stores_answer(monkeypatch):
    router = module.AIRouter()
    router.PROVIDERS = ['ollama']
    provider = Mock(return_value='fresh')
    store = Mock()
    monkeypatch.setattr(router, '_ask_ollama', provider)
    monkeypatch.setattr(module, 'HAS_COST_OPT', True)
    monkeypatch.setattr(module, 'get_cached', Mock(side_effect=['cached',None]))
    monkeypatch.setattr(module, 'get_tier_and_model', lambda prompt:('simple','fixture'))
    monkeypatch.setattr(module, 'store_cached', store)
    assert router.ask('first') == 'cached'
    provider.assert_not_called()
    assert router.ask('second') == 'fresh'
    store.assert_called_once_with('second','fresh')


def test_exhaustion_and_unknown_provider_return_none(monkeypatch):
    router = module.AIRouter()
    router.PROVIDERS = ['unknown']
    assert router.ask('question') is None


@pytest.mark.parametrize('provider,key', [('groq','GROQ_API_KEY'),('deepseek','DEEPSEEK_API_KEY'),('xai','XAI_API_KEY')])
def test_http_provider_preserves_roles_and_returns_response(monkeypatch, provider, key):
    import requests
    monkeypatch.setenv(key,'synthetic-key')
    post = Mock(return_value=SimpleNamespace(status_code=200,json=lambda:{'choices':[{'message':{'content':'answer'}}]}))
    monkeypatch.setattr(requests,'post',post)
    router = module.AIRouter()
    assert getattr(router,'_ask_'+provider)('literal question','literal system') == 'answer'
    assert post.call_args.kwargs['json']['messages'] == [{'role':'system','content':'literal system'},{'role':'user','content':'literal question'}]
    assert post.call_args.kwargs['headers']['Authorization'] == 'Bearer synthetic-key'


@pytest.mark.parametrize('provider', ['groq','deepseek','xai','watsonx','gigachat','yandexgpt'])
def test_missing_credentials_or_core_does_not_request(monkeypatch,provider):
    import requests
    post = Mock(side_effect=AssertionError('must not request'))
    monkeypatch.setattr(requests,'post',post)
    assert getattr(module.AIRouter(),'_ask_'+provider)('question','system') is None
    post.assert_not_called()


@pytest.mark.parametrize('provider', ['gigachat','yandexgpt','watsonx'])
def test_core_provider_arguments_and_failure(monkeypatch,provider):
    monkeypatch.setenv('WATSONX_API_KEY','synthetic')
    callback = Mock(return_value='answer')
    router = module.AIRouter(SimpleNamespace(**{'_ask_'+provider:callback}))
    assert getattr(router,'_ask_'+provider)('question','system') == 'answer'
    callback.assert_called_once_with('system','question')
    callback.side_effect = ValueError('synthetic')
    with pytest.raises(RuntimeError):
        getattr(router,'_ask_'+provider)('question','system')


def test_key_pool_rotation_exhaustion_expiry_and_reload(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY_0','synthetic-a')
    monkeypatch.setenv('GEMINI_API_KEY1','synthetic-b')
    monkeypatch.setenv('GEMINI_API_KEY','synthetic-a')
    clock = [1000.0]
    monkeypatch.setattr(module.time,'time',lambda:clock[0])
    monkeypatch.setattr(module.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    pool = module._GeminiKeyPool()
    pool.MAX_RPM = 1
    pool.WAIT_SEC = 2
    assert pool.available()
    assert pool.get_key() == (0,'synthetic-a')
    assert pool.get_key() == (1,'synthetic-b')
    assert pool.get_key() is None
    assert 'key_0: 1/1' in pool.status()
    clock[0] += 60
    assert pool.get_key() == (0,'synthetic-a')
    pool.mark_rate_limited(1)
    assert pool.get_key() is None
    monkeypatch.setenv('GEMINI_API_KEY_0','synthetic-new')
    pool.reload()
    assert pool.get_key() == (0,'synthetic-new')


def test_ollama_payload_and_transport_failure(monkeypatch):
    import requests
    monkeypatch.setenv('OLLAMA_HOST','http://fixture.invalid:11434')
    monkeypatch.setenv('OLLAMA_MODEL','fixture-model')
    post = Mock(return_value=SimpleNamespace(json=lambda:{'response':'answer'}))
    monkeypatch.setattr(requests,'post',post)
    router = module.AIRouter()
    assert router._ask_ollama('question','system') == 'answer'
    assert post.call_args.kwargs['json'] == dict(model='fixture-model',prompt='question',system='system',stream=False)
    post.side_effect = requests.Timeout('synthetic')
    with pytest.raises(RuntimeError,match='Ollama'):
        router._ask_ollama('question','system')
