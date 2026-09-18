import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPACE_URL = "https://huggingface.co/spaces/AvaSiG/sentence-transformers-all-MiniLM-L6-v2"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


@pytest.fixture(params=["argos_deploy/src/skills/huggingface_ai.py", "src/skills/huggingface_ai.py"])
def hf_factory(request):
    path = PROJECT_ROOT / request.param
    tree = ast.parse(path.read_text(encoding="utf-8"))
    definitions = [
        node for node in tree.body
        if (
            isinstance(node, ast.FunctionDef)
            and node.name in {"_parse_model_ref", "_resolve_model_env", "_resolve_embed_env"}
        ) or (isinstance(node, ast.ClassDef) and node.name == "HuggingFaceAI")
    ]

    def create(environment, model=None):
        calls = []

        class OfflineClient:
            def __init__(self, **kwargs):
                pass

            def text_generation(self, prompt, **kwargs):
                calls.append(("text_generation", kwargs["model"]))
                return "offline response"

            def feature_extraction(self, text, model):
                calls.append(("feature_extraction", model))
                return [0.1, 0.2]

        namespace = {
            "os": SimpleNamespace(getenv=lambda key, default=None: environment.get(key, default)),
            "Any": Any,
            "urlparse": urlparse,
            "InferenceClient": OfflineClient,
            "DEFAULT_EMBED_MODEL": environment.get("HUGGINGFACE_EMBED_MODEL", EMBED_MODEL),
            "DEFAULT_TEXT_MODEL": environment.get("HUGGINGFACE_TEXT_MODEL", "").strip(),
            "_HF_POOL": SimpleNamespace(
                reload=lambda: None,
                available=lambda: False,
                status=lambda: "offline fixture",
                _tokens=[],
                MAX_RPM=30,
            ),
        }
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), "exec"), namespace)
        return namespace["HuggingFaceAI"](token="offline-fixture", model=model), calls

    return create


@pytest.mark.parametrize("legacy_model", [None, "legacy/text-model", SPACE_URL])
def test_text_model_in_status_is_used_for_generation(hf_factory, legacy_model):
    environment = {"HUGGINGFACE_TEXT_MODEL": "example/chat-model"}
    if legacy_model is not None:
        environment["HUGGINGFACE_MODEL"] = legacy_model
    hf, calls = hf_factory(environment)

    assert "Text generation: example/chat-model" in hf.run()
    assert hf.ask("hello") == "offline response"
    assert calls == [("text_generation", "example/chat-model")]


def test_text_model_url_is_normalized_before_generation(hf_factory):
    hf, calls = hf_factory({"HUGGINGFACE_TEXT_MODEL": "https://huggingface.co/example/chat-model"})

    assert hf.ask("hello") == "offline response"
    assert calls == [("text_generation", "example/chat-model")]


@pytest.mark.parametrize("model_variable", ["HUGGINGFACE_MODEL", "HUGGINGFACE_TEXT_MODEL"])
def test_space_config_cannot_be_used_as_text_generation_model(hf_factory, model_variable):
    hf, calls = hf_factory({model_variable: SPACE_URL})

    with pytest.raises(RuntimeError, match="для text generation укажи модель"):
        hf.ask("hello")
    assert calls == []


def test_legacy_model_is_preserved_when_text_model_is_empty(hf_factory):
    hf, calls = hf_factory({"HUGGINGFACE_MODEL": "legacy/text-model", "HUGGINGFACE_TEXT_MODEL": "  "})

    assert hf.ask("hello") == "offline response"
    assert calls == [("text_generation", "legacy/text-model")]


def test_space_reference_can_fall_back_to_a_configured_text_model(hf_factory):
    hf, calls = hf_factory({"HUGGINGFACE_TEXT_MODEL": "example/chat-model"}, model=SPACE_URL)

    assert hf.ask("hello") == "offline response"
    assert calls == [("text_generation", "example/chat-model")]


def test_explicit_model_overrides_environment(hf_factory):
    hf, calls = hf_factory({"HUGGINGFACE_TEXT_MODEL": "example/chat-model"}, model="constructor/model")

    assert hf.ask("hello") == "offline response"
    assert hf.ask("hello", model="request/model") == "offline response"
    assert calls == [("text_generation", "constructor/model"), ("text_generation", "request/model")]


@pytest.mark.parametrize("space", [None, SPACE_URL])
def test_text_model_does_not_replace_embedding_configuration(hf_factory, space):
    environment = {
        "HUGGINGFACE_TEXT_MODEL": "example/chat-model",
        "HUGGINGFACE_EMBED_MODEL": "example/embedding-model",
    }
    if space is not None:
        environment["HUGGINGFACE_MODEL_SPACE"] = space
    hf, calls = hf_factory(environment)

    assert hf.embed("hello") == [0.1, 0.2]
    assert calls == [("feature_extraction", "example/embedding-model")]
