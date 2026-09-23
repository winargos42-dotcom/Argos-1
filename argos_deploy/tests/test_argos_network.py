import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PATH = Path(__file__).resolve().parents[1] / 'src' / 'argos_network.py'
spec = importlib.util.spec_from_file_location('network_under_test', PATH)
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)
BOOT = 'ef59f687-c833-44c0-9749-5ebbc0b90ef7'
AUTH = {'Authorization': 'Bearer network-test-key'}


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('ARGOS_NETWORK_KEY', 'network-test-key')
    monkeypatch.setenv('ARGOS_MCP_API_KEY', 'owner-test-key')
    clock = [100.0]
    monkeypatch.setattr(network, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    app = FastAPI()
    network.install_network_routes(app, health_provider=lambda: {
        'ready': True, 'uptime_seconds': 12, 'error': 'must-not-leak', 'secret': 'private'})
    with TestClient(app) as client:
        yield client, clock


def registration(client, **changes):
    body = {'node_id': 'argos-x230-primary', 'boot_id': BOOT,
            'status': {'ready': True, 'uptime_seconds': 4}}
    body.update(changes)
    return client.post('/network/register', headers=AUTH, json=body)


def heartbeat(client, challenge, **changes):
    body = {'node_id': 'argos-x230-primary', 'boot_id': BOOT,
            'challenge_id': challenge, 'status': {'ready': True, 'uptime_seconds': 5}}
    body.update(changes)
    return client.post('/network/heartbeat', headers=AUTH, json=body)


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer wrong'},
    {'Authorization': 'Bearer owner-test-key'},
    [('Authorization', 'Bearer network-test-key'), ('Authorization', 'Bearer network-test-key')]])
def test_auth_rejected(setup, headers):
    client, _ = setup
    assert client.get('/network/status', headers=headers).status_code == 401


def test_missing_key_fails_closed(setup, monkeypatch):
    client, _ = setup
    monkeypatch.delenv('ARGOS_NETWORK_KEY')
    assert client.get('/network/status', headers=AUTH).status_code == 503


def test_confirmation_replay_expiry_offline(setup):
    client, clock = setup
    registered = registration(client).json()
    assert registered['confirmed'] is False
    primary = client.get('/network/status', headers=AUTH).json()['peers'][0]
    assert not primary['online'] and not primary['confirmed']
    first = registered['challenge_id']
    reply = heartbeat(client, first)
    assert reply.status_code == 200
    assert reply.json()['confirmed'] is True
    assert heartbeat(client, first).status_code == 409
    status = client.get('/network/status', headers=AUTH).json()
    assert status['peers'][0]['online'] and status['peers'][0]['confirmed']
    assert status['cloud']['role'] == 'secondary'
    assert 'must-not-leak' not in str(status) and 'private' not in str(status)
    clock[0] += 45
    assert heartbeat(client, reply.json()['challenge_id']).status_code == 409
    clock[0] += 15
    assert not client.get('/network/status', headers=AUTH).json()['peers'][0]['online']


def test_identity_boot_and_reregistration(setup):
    client, _ = setup
    assert registration(client, node_id='attacker').status_code == 403
    assert registration(client, boot_id='invalid').status_code == 422
    first = registration(client).json()['challenge_id']
    other = '7bfc93e5-fad0-4ea2-8f96-681f9c718c75'
    assert heartbeat(client, first, boot_id=other).status_code == 409
    second = registration(client, boot_id=other).json()['challenge_id']
    assert heartbeat(client, first).status_code == 409
    assert heartbeat(client, second, boot_id=other).status_code == 200


@pytest.mark.parametrize('status', [
    {'ready': 'yes', 'uptime_seconds': 1}, {'ready': True, 'uptime_seconds': -1},
    {'ready': True, 'uptime_seconds': True}, {'ready': True, 'uptime_seconds': '3'},
    {'ready': True, 'uptime_seconds': 1, 'command': 'shell'},
])
def test_status_allowlist(setup, status):
    assert registration(setup[0], status=status).status_code == 422


def test_new_server_rejects_old_session(setup):
    client, _ = setup
    old = registration(client).json()['challenge_id']
    app = FastAPI()
    network.install_network_routes(app, health_provider=lambda: {'ready': False})
    with TestClient(app) as restarted:
        assert heartbeat(restarted, old).status_code == 409
        challenge = registration(restarted).json()['challenge_id']
        assert heartbeat(restarted, challenge).status_code == 200


def test_unauthorized_registration_does_not_reset_session(setup):
    client, _ = setup
    challenge = registration(client).json()['challenge_id']
    response = client.post('/network/register', json={
        'node_id': 'argos-x230-primary', 'boot_id': BOOT,
        'status': {'ready': True, 'uptime_seconds': 1}})
    assert response.status_code == 401
    assert heartbeat(client, challenge).status_code == 200


def test_unknown_challenge_and_expiry_recovery(setup):
    client, clock = setup
    challenge = registration(client).json()['challenge_id']
    assert heartbeat(client, 'eba22358-531f-4c44-ab25-276810f3ed04').status_code == 409
    clock[0] += 46
    assert heartbeat(client, challenge).status_code == 409
    new = registration(client).json()['challenge_id']
    assert new != challenge and heartbeat(client, new).status_code == 200


def test_cloud_callback_failure_is_sanitized(setup):
    def broken():
        raise ValueError('private-provider-diagnostic')
    app = FastAPI()
    network.install_network_routes(app, health_provider=broken)
    with TestClient(app) as client:
        response = client.get('/network/status', headers=AUTH)
        assert response.status_code == 200
        assert response.json()['cloud']['ready'] is False
        assert 'private-provider-diagnostic' not in response.text
