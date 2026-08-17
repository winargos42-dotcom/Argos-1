from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import requests

from src.core import _load_argos_core_class

ArgosCore = _load_argos_core_class()
core_module = sys.modules[ArgosCore.__module__]


def _dummy_core(ollama_url: str = "http://localhost:11434/api/generate"):
    dummy = SimpleNamespace(
        ollama_url=ollama_url,
        _ollama_status="unknown",
        _ollama_last_error=None,
        _ollama_last_checked_at=None,
        _ollama_unavailable_until=0.0,
        _ollama_unavailable_permanent=False,
    )
    for name in (
        "_set_ollama_unavailable",
        "_set_ollama_available",
        "_ensure_ollama_running",
    ):
        setattr(dummy, name, getattr(ArgosCore, name).__get__(dummy, type(dummy)))
    return dummy


def test_missing_ollama_binary_opens_permanent_circuit(monkeypatch):
    dummy = _dummy_core()
    ping_calls: list[str] = []
    spawn_calls: list[list[str]] = []

    def offline(url, **_kwargs):
        ping_calls.append(url)
        raise requests.ConnectionError("offline")

    def spawn(args, **_kwargs):
        spawn_calls.append(args)
        raise AssertionError("missing executable must be detected before Popen")

    monkeypatch.setattr(ArgosCore, "_ollama_start_lock", threading.Lock())
    monkeypatch.setattr(core_module.requests, "get", offline)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(subprocess, "Popen", spawn)

    assert dummy._ensure_ollama_running() is False
    assert dummy._ensure_ollama_running() is False

    assert len(ping_calls) == 1
    assert spawn_calls == []
    assert dummy._ollama_unavailable_permanent is True
    assert dummy._ollama_last_error == "binary_not_found"


def test_remote_ollama_is_never_started_as_a_local_process(monkeypatch):
    dummy = _dummy_core("http://inference.internal:11434/api/generate")
    spawn_calls: list[list[str]] = []

    monkeypatch.setattr(ArgosCore, "_ollama_start_lock", threading.Lock())
    monkeypatch.setattr(
        core_module.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            requests.ConnectionError("remote offline")
        ),
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda args, **_kwargs: spawn_calls.append(args),
    )

    assert dummy._ensure_ollama_running() is False
    assert spawn_calls == []
    assert dummy._ollama_last_error == "remote_unreachable"


def test_custom_cloud_fallback_uses_configured_openai_endpoint(monkeypatch):
    monkeypatch.setenv("ARGOS_INFERENCE_URL", "https://inference.example/v1")
    monkeypatch.setenv("ARGOS_INFERENCE_MODEL", "argos-cloud")
    monkeypatch.setenv("ARGOS_INFERENCE_API_KEY", "top-secret")

    dummy = SimpleNamespace(
        context=SimpleNamespace(get_prompt_context=lambda: ""),
        _is_provider_temporarily_disabled=lambda _name: False,
        _is_host_reachable=lambda *_args, **_kwargs: True,
        _disable_provider_temporarily=lambda *_args, **_kwargs: None,
        _provider_disabled_until={},
        _provider_disable_reason={},
    )
    response = MagicMock()
    response.ok = True
    response.status_code = 200
    response.json.return_value = {
        "choices": [{"message": {"content": "cloud answer"}}]
    }
    captured: dict = {}

    def post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["payload"] = kwargs["json"]
        return response

    monkeypatch.setattr(requests, "post", post)

    answer = ArgosCore._ask_openai_compat(
        dummy, "system", "question", provider_name="CloudFallback"
    )

    assert answer == "cloud answer"
    assert captured["url"] == "https://inference.example/v1/chat/completions"
    assert captured["payload"]["model"] == "argos-cloud"
    assert captured["headers"]["Authorization"] == "Bearer top-secret"


def test_custom_cloud_fallback_is_before_ollama_in_auto_mode(monkeypatch):
    for name in (
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "GROK_API_KEY",
        "GROQ_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_0",
        "GIGACHAT_API_KEY",
        "YANDEX_IAM_TOKEN",
        "KIMI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARGOS_INFERENCE_URL", "https://inference.example/v1")
    monkeypatch.setenv("ARGOS_INFERENCE_MODEL", "argos-cloud")

    no = lambda: False
    dummy = SimpleNamespace(
        _has_openclaw_config=no,
        _has_openclaw_cli=no,
        _is_provider_temporarily_disabled=lambda _name: False,
        _has_gigachat_config=no,
        _has_yandexgpt_config=no,
        _has_kimi_config=no,
        _has_watsonx_config=no,
        _ask_openai_compat=lambda *_args, **_kwargs: None,
        _ask_ollama=lambda *_args, **_kwargs: None,
        model=None,
        auto_collab_max_models=8,
    )

    providers = ArgosCore._auto_providers(dummy)
    names = [name for name, _fn in providers]

    assert names[-2:] == ["CloudFallback", "Ollama (Argoss)"]


def test_inference_health_never_returns_credentials(monkeypatch):
    monkeypatch.setenv("ARGOS_INFERENCE_URL", "https://inference.example/v1")
    monkeypatch.setenv("ARGOS_INFERENCE_MODEL", "argos-cloud")
    monkeypatch.setenv("ARGOS_INFERENCE_API_KEY", "top-secret")

    no = lambda: False
    dummy = SimpleNamespace(
        ai_mode="auto",
        ollama_url="http://localhost:11434/api/generate",
        _ollama_status="unavailable",
        _ollama_last_error="binary_not_found",
        _ollama_last_checked_at=1.0,
        _ollama_unavailable_until=float("inf"),
        _ollama_unavailable_permanent=True,
        _provider_disabled_until={},
        _provider_disable_reason={},
        _provider_disabled_permanent={},
        _has_gigachat_config=no,
        _has_yandexgpt_config=no,
        _has_kimi_config=no,
    )

    payload = ArgosCore.inference_health(dummy)
    serialized = repr(payload)

    assert payload["providers"]["CloudFallback"]["configured"] is True
    assert "top-secret" not in serialized
    assert "api_key" not in serialized.lower()
