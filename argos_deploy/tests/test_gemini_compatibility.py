import ast
import re
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROUTER_PATHS = ["src/ai_router.py", "argos_deploy/src/ai_router.py"]
CORE_PATHS = ["src/core.py", "argos_deploy/src/core.py"]
DEFAULT_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]


def _load_definitions(relative_path, environment, **namespace):
    path = PROJECT_ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    names = {
        "_env_flag", "_env_disabled", "_split_gemini_model_list",
        "_gemini_model_candidates", "_GeminiResponse", "_GeminiCompatClient",
        "AIRouter", "_GEMINI_DEFAULT_MODELS", "_GEMINI_DEPRECATED_PREFIXES",
    }
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in names for target in node.targets
        ):
            nodes.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "ArgosCore":
            nodes.extend(
                method for method in node.body
                if isinstance(method, ast.FunctionDef) and method.name == "_ask_gemini"
            )
    namespace.update({
        "os": SimpleNamespace(getenv=lambda name, default=None: environment.get(name, default)),
        "re": re,
        "log": Mock(),
    })
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Gemini regression tests must not use a real transport")

    monkeypatch.setattr("requests.sessions.Session.request", reject)
    monkeypatch.setattr("socket.socket.connect", reject)
    monkeypatch.setattr("socket.getaddrinfo", reject)


@pytest.fixture(params=ROUTER_PATHS, ids=["root-router", "deployment-router"])
def router(request):
    environment = {}
    pool = Mock()
    pool.available.return_value = True
    pool.get_key.side_effect = [(0, "fake-gemini-key-0"), (1, "fake-gemini-key-1")]
    namespace = _load_definitions(request.param, environment, _GEMINI_POOL=pool)
    return namespace["AIRouter"](), environment, pool


@pytest.fixture(params=CORE_PATHS, ids=["root-core", "deployment-core"])
def core_adapter(request):
    environment = {}
    models = SimpleNamespace(
        list=Mock(side_effect=AssertionError("Constructors must not list remote models")),
        generate_content=Mock(return_value=SimpleNamespace(text="offline response")),
    )
    sdk = SimpleNamespace(
        Client=Mock(return_value=SimpleNamespace(models=models)),
        types=SimpleNamespace(HttpOptions=SimpleNamespace(model_fields={"timeout": object(), "client_args": object()})),
    )
    namespace = _load_definitions(request.param, environment, genai_sdk=sdk)
    return namespace, environment, sdk, models


def _response(status=200, body=None):
    if body is None:
        body = {"candidates": [{"content": {"parts": [{"text": "offline response"}]}}]}
    return SimpleNamespace(status_code=status, json=lambda: body)


def test_router_defaults_use_supported_models(router):
    instance, _, _ = router
    assert instance._gemini_model_candidates() == DEFAULT_MODELS


def test_router_preserves_and_normalizes_explicit_models(router):
    instance, environment, _ = router
    environment.update({
        "GEMINI_MODEL": " models/gemini-custom-primary ",
        "GEMINI_MODEL_CANDIDATES": "models/gemini-custom-primary; models/gemini-2.0-flash gemini-custom-backup",
    })
    assert instance._gemini_model_candidates() == [
        "gemini-custom-primary", "gemini-2.0-flash", "gemini-custom-backup", *DEFAULT_MODELS,
    ]


@pytest.mark.parametrize("flag", ["1", " TRUE ", "да"])
def test_disabled_router_does_not_load_keys_or_call_proxy(router, monkeypatch, flag):
    instance, environment, pool = router
    environment.update({"ARGOS_DISABLE_GEMINI": flag, "ARGOS_GCP_URL": "https://proxy.invalid"})
    post = Mock(return_value=_response())
    monkeypatch.setattr(requests, "post", post)

    assert instance._ask_gemini("prompt", "system") is None

    post.assert_not_called()
    assert pool.method_calls == []


def test_router_direct_rest_uses_header_auth_and_bounded_timeout(router, monkeypatch):
    instance, _, pool = router
    post = Mock(return_value=_response())
    monkeypatch.setattr(requests, "post", post)

    assert instance._ask_gemini("prompt", "system") == "offline response"

    post.assert_called_once()
    args, kwargs = post.call_args
    assert args == ("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",)
    assert kwargs["headers"]["x-goog-api-key"] == "fake-gemini-key-0"
    assert 0 < kwargs["timeout"] <= 30
    assert kwargs["json"] == {"contents": [{"parts": [{"text": "system\n\nprompt"}]}]}
    pool.mark_rate_limited.assert_not_called()


def test_router_preserves_proxy_without_forwarding_direct_key(router, monkeypatch):
    instance, environment, pool = router
    environment.update({
        "ARGOS_GCP_URL": "https://proxy.invalid/",
        "GEMINI_MODEL": "models/gemini-custom-primary",
    })
    post = Mock(return_value=_response())
    monkeypatch.setattr(requests, "post", post)

    assert instance._ask_gemini("prompt", "") == "offline response"

    args, kwargs = post.call_args
    assert args == ("https://proxy.invalid/proxy/gemini/v1/models/gemini-custom-primary:generateContent",)
    assert "x-goog-api-key" not in kwargs.get("headers", {})
    assert 0 < kwargs["timeout"] <= 30
    assert pool.method_calls == []


def test_router_proxy_failure_falls_back_to_direct_rest(router, monkeypatch):
    instance, environment, _ = router
    environment["ARGOS_GCP_URL"] = "https://proxy.invalid"
    post = Mock(side_effect=[_response(503, {"error": "unavailable"}), _response()])
    monkeypatch.setattr(requests, "post", post)

    assert instance._ask_gemini("prompt", "") == "offline response"

    assert post.call_count == 2
    assert post.call_args.kwargs["headers"]["x-goog-api-key"] == "fake-gemini-key-0"


def test_router_explicit_retired_model_404_uses_supported_fallback(router, monkeypatch):
    instance, environment, _ = router
    environment["GEMINI_MODEL"] = "models/gemini-2.0-flash"
    post = Mock(side_effect=[_response(404, {"error": {"status": "NOT_FOUND"}}), _response()])
    monkeypatch.setattr(requests, "post", post)

    assert instance._ask_gemini("prompt", "") == "offline response"

    assert post.call_count == 2
    assert "/models/gemini-2.0-flash:" in post.call_args_list[0].args[0]
    assert "/models/gemini-2.5-flash:" in post.call_args_list[1].args[0]


def test_router_rotates_key_once_after_model_quotas(router, monkeypatch):
    instance, _, pool = router
    quota = _response(429, {"error": {"status": "RESOURCE_EXHAUSTED"}})
    post = Mock(side_effect=[quota, quota, _response()])
    monkeypatch.setattr(requests, "post", post)

    assert instance._ask_gemini("prompt", "") == "offline response"

    assert post.call_count == 3
    pool.mark_rate_limited.assert_called_once_with(0)
    assert pool.get_key.call_count == 2
    assert [call.kwargs["headers"]["x-goog-api-key"] for call in post.call_args_list] == [
        "fake-gemini-key-0", "fake-gemini-key-0", "fake-gemini-key-1",
    ]
    assert all("fake-gemini-key" not in call.args[0] for call in post.call_args_list)


def test_router_auth_error_does_not_try_more_models_or_keys(router, monkeypatch):
    instance, _, pool = router
    post = Mock(return_value=_response(403, {"error": {"message": "API key invalid"}}))
    monkeypatch.setattr(requests, "post", post)

    with pytest.raises(RuntimeError, match="403"):
        instance._ask_gemini("prompt", "")

    post.assert_called_once()
    assert pool.get_key.call_count == 1
    pool.mark_rate_limited.assert_not_called()


def test_router_transport_error_omits_header_secret_and_traceback_context(router, monkeypatch):
    instance, _, _ = router
    secret = "fake-gemini-header-secret"
    error = requests.exceptions.InvalidHeader(f"Invalid x-goog-api-key header: {secret!r}")
    monkeypatch.setattr(requests, "post", Mock(side_effect=error))

    with pytest.raises(RuntimeError) as raised:
        instance._ask_gemini("prompt", "")

    assert secret not in str(raised.value)
    assert "InvalidHeader" in str(raised.value)
    assert secret not in "".join(traceback.format_exception(raised.type, raised.value, raised.tb))
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


def test_router_outer_logger_omits_transport_header_secret(router, monkeypatch):
    instance, _, _ = router
    secret = "fake-gemini-header-secret"
    error = requests.exceptions.InvalidHeader(f"Invalid x-goog-api-key header: {secret!r}")
    monkeypatch.setattr(requests, "post", Mock(side_effect=error))
    namespace = instance._try_provider.__globals__
    namespace["_mark_failed"] = Mock()

    assert instance._try_provider("gemini", "prompt", "") is None

    warnings = " ".join(str(call) for call in namespace["log"].warning.call_args_list)
    assert secret not in warnings
    assert "InvalidHeader" in warnings
    namespace["_mark_failed"].assert_called_once_with("gemini")


def test_router_quota_exhaustion_stops_after_second_key(router, monkeypatch):
    instance, _, pool = router
    post = Mock(return_value=_response(429, {"error": {"status": "RESOURCE_EXHAUSTED"}}))
    monkeypatch.setattr(requests, "post", post)

    with pytest.raises(RuntimeError, match="Gemini"):
        instance._ask_gemini("prompt", "")

    assert post.call_count == 4
    assert pool.get_key.call_count == 2


def test_core_client_constructs_without_io_and_sets_sdk_timeout(core_adapter):
    namespace, _, sdk, models = core_adapter

    instance = namespace["_GeminiCompatClient"]("fake-sdk-key")

    sdk.Client.assert_called_once()
    options = sdk.Client.call_args.kwargs["http_options"]
    assert options["timeout"] == 30_000
    assert options["client_args"]["trust_env"] is False
    assert "client" not in options
    models.list.assert_not_called()
    models.generate_content.assert_not_called()
    assert instance.model_name == "gemini-2.5-flash"


@pytest.mark.parametrize("schema_available", [True, False])
def test_core_legacy_sdk_receives_supported_timeout_options(core_adapter, schema_available):
    namespace, _, sdk, models = core_adapter
    if schema_available:
        sdk.types.HttpOptions.model_fields = {"timeout": object()}
    else:
        del sdk.types

    def construct_legacy_client(*, api_key, http_options):
        assert api_key == "fake-sdk-key"
        assert http_options == {"timeout": 30_000}
        return SimpleNamespace(models=models)

    sdk.Client.side_effect = construct_legacy_client

    instance = namespace["_GeminiCompatClient"]("fake-sdk-key")

    sdk.Client.assert_called_once()
    models.list.assert_not_called()
    models.generate_content.assert_not_called()
    assert instance.model_name == "gemini-2.5-flash"


def test_core_candidate_defaults_and_explicit_models_match_router(core_adapter):
    namespace, environment, _, _ = core_adapter
    adapter = namespace["_GeminiCompatClient"]
    assert adapter._gemini_model_candidates() == DEFAULT_MODELS
    environment.update({
        "GEMINI_MODEL": "models/gemini-custom-primary",
        "GEMINI_MODEL_CANDIDATES": "models/gemini-custom-primary; models/gemini-2.0-flash gemini-custom-backup",
    })
    assert adapter._gemini_model_candidates() == [
        "gemini-custom-primary", "gemini-2.0-flash", "gemini-custom-backup", *DEFAULT_MODELS,
    ]


def test_core_explicit_retired_model_falls_back_after_404(core_adapter):
    namespace, environment, _, models = core_adapter
    environment["GEMINI_MODEL"] = "models/gemini-2.0-flash"
    models.generate_content.side_effect = [RuntimeError("404 NOT_FOUND"), SimpleNamespace(text="fallback response")]
    instance = namespace["_GeminiCompatClient"]("fake-sdk-key")

    assert instance.generate_content(["system", "prompt"]).text == "fallback response"

    assert [call.kwargs["model"] for call in models.generate_content.call_args_list] == [
        "gemini-2.0-flash", "gemini-2.5-flash",
    ]
    assert models.generate_content.call_args.kwargs["contents"] == "system\n\nprompt"
    assert instance.model_name == "gemini-2.5-flash"
    models.list.assert_not_called()


def test_core_auth_error_is_not_retried_as_missing_model(core_adapter):
    namespace, _, _, models = core_adapter
    models.generate_content.side_effect = RuntimeError("403 API key invalid")
    instance = namespace["_GeminiCompatClient"]("fake-sdk-key")

    with pytest.raises(RuntimeError, match="403"):
        instance.generate_content("prompt")

    models.generate_content.assert_called_once()
    models.list.assert_not_called()


def test_core_disable_prevents_existing_client_and_proxy_use(core_adapter):
    namespace, environment, _, _ = core_adapter
    environment.update({"ARGOS_DISABLE_GEMINI": "true", "ARGOS_GCP_URL": "https://proxy.invalid"})
    core = SimpleNamespace(
        model=Mock(),
        context=Mock(),
        _gemini_limiter=Mock(),
        _is_provider_temporarily_disabled=Mock(return_value=False),
        _ask_gemini_via_gcp_proxy=Mock(return_value="unexpected proxy response"),
    )

    assert namespace["_ask_gemini"](core, "context", "prompt") is None
    assert core._last_gemini_rate_limited is False
    core.model.generate_content.assert_not_called()
    core._gemini_limiter.allow.assert_not_called()

    core.model = None
    assert namespace["_ask_gemini"](core, "context", "prompt") is None
    core._ask_gemini_via_gcp_proxy.assert_not_called()


@pytest.mark.parametrize("allow", [False, True])
def test_deprecated_override_keeps_existing_opt_in_after_normalization(core_adapter, allow):
    namespace, environment, _, _ = core_adapter
    environment["GEMINI_MODEL"] = "models/gemini-1.5-flash"
    environment["ARGOS_ALLOW_DEPRECATED_GEMINI_MODELS"] = "true" if allow else "false"
    candidates = namespace["_GeminiCompatClient"]._gemini_model_candidates()
    assert ("gemini-1.5-flash" in candidates) is allow
