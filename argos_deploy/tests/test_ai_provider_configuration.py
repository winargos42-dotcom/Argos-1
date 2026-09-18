import socket

import pytest

from src import ai_providers


GIGACHAT_ENV_NAMES = (
    "GIGACHAT_API_KEY",
    "GIGACHAT_ACCESS_TOKEN",
    "GIGACHAT_CLIENT_ID",
    "GIGACHAT_CLIENT_SECRET",
)


@pytest.fixture(autouse=True)
def isolated_gigachat_configuration(monkeypatch):
    for name in GIGACHAT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        ai_providers, "AI_PROVIDERS", {"gigachat": ai_providers.AI_PROVIDERS["gigachat"]}
    )

    def reject_network(*args, **kwargs):
        raise AssertionError("Configuration detection must not contact a provider")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket, "getaddrinfo", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)


@pytest.mark.parametrize("values", [
    {"GIGACHAT_API_KEY": "test-api-key"},
    {"GIGACHAT_ACCESS_TOKEN": "test-access-token"},
    {"GIGACHAT_CLIENT_ID": "test-client", "GIGACHAT_CLIENT_SECRET": "test-secret"},
    {"GIGACHAT_CLIENT_ID": " test-client ", "GIGACHAT_CLIENT_SECRET": " test-secret "},
])
def test_supported_gigachat_credentials_are_detected(monkeypatch, values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    assert ai_providers.available_providers() == ["gigachat"]


@pytest.mark.parametrize("values", [
    {},
    {"GIGACHAT_CLIENT_ID": "test-client"},
    {"GIGACHAT_CLIENT_SECRET": "test-secret"},
    {"GIGACHAT_API_KEY": "  "},
    {"GIGACHAT_CLIENT_ID": "test-client", "GIGACHAT_CLIENT_SECRET": "  "},
])
def test_incomplete_gigachat_credentials_are_not_detected(monkeypatch, values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    assert ai_providers.available_providers() == []


@pytest.mark.parametrize("placeholder", [
    "your_key_here", "your_token_here", "None", "NULL", "changeme",
])
def test_placeholder_credentials_are_not_configuration(monkeypatch, placeholder):
    monkeypatch.setenv("GIGACHAT_API_KEY", placeholder)
    monkeypatch.setenv("GIGACHAT_ACCESS_TOKEN", placeholder)
    monkeypatch.setenv("GIGACHAT_CLIENT_ID", "test-client")
    monkeypatch.setenv("GIGACHAT_CLIENT_SECRET", placeholder)

    assert ai_providers.available_providers() == []


def test_status_describes_configuration_without_claiming_working_api(monkeypatch):
    monkeypatch.setenv("GIGACHAT_CLIENT_ID", "test-client")
    monkeypatch.setenv("GIGACHAT_CLIENT_SECRET", "test-secret")

    status = ai_providers.providers_status()

    assert "✅ GigaChat" in status
    assert "1/1" in status
    assert "GIGACHAT_CLIENT_ID + GIGACHAT_CLIENT_SECRET" in status
    assert "Работа API и генерация ответа не проверялись" in status
    assert "Активных провайдеров" not in status
    assert "test-client" not in status
    assert "test-secret" not in status
