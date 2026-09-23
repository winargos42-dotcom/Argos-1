"""Offline inventory parser/transport boundary tests: no device or network access."""
import copy
import importlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def inventory_class():
    module = importlib.import_module('src.media_inventory')
    assert hasattr(module, 'MediaInventory'), 'Missing metadata-only production inventory'
    return module.MediaInventory


def fixture_inventory(tmp_path):
    video = tmp_path / 'video4linux'
    (video / 'video0').mkdir(parents=True)
    (video / 'video0/name').write_text('Integrated Camera\n')
    (video / 'video1').mkdir()
    (video / 'video1/name').write_text('Integrated Camera\n')
    calls = []
    def command(argv):
        calls.append(argv)
        if argv == ['pactl', '-f', 'json', 'list', 'sources']:
            return json.dumps([{'name': 'alsa_input.builtin', 'description': 'Built-in Microphone',
                                'state': 'SUSPENDED', 'properties': {'private': 'do-not-copy'}}]).encode()
        if argv == ['pactl', '-f', 'json', 'list', 'sinks']:
            return json.dumps([{'name': 'bluez_output.XinYi', 'description': 'XinYi', 'state': 'IDLE'}]).encode()
        assert argv == ['adb', 'devices', '-l']
        return b'List of devices attached\n\n'
    def ha():
        return [{'entity_id': 'camera.front', 'state': 'idle',
                 'attributes': {'friendly_name': 'Front', 'entity_picture': '/api/camera_proxy/secret',
                                'access_token': 'never-output', 'stream_source': 'rtsp://user:password@host/'}},
                {'entity_id': 'media_player.room', 'state': 'off', 'attributes': {'friendly_name': 'Room'}},
                {'entity_id': 'sensor.secret', 'state': 'private', 'attributes': {}}]
    obj = inventory_class()(command_reader=command, ha_reader=ha, video_root=video,
                            adb_reader=lambda: obj.command_reader(['adb', 'devices', '-l']))
    return obj, calls


def test_metadata_only_safe_schema_and_allowlisted_ha(tmp_path):
    obj, calls = fixture_inventory(tmp_path)
    result = obj.snapshot()
    assert all(row['status'] == 'ok' for row in result['sources'].values())
    assert len(result['devices']) == 6
    assert all(row['capture_verified'] is False for row in result['devices'])
    assert {row['kind'] for row in result['devices']} == {'audio_input', 'audio_output', 'video_input'}
    output = json.dumps(result)
    assert all(secret not in output for secret in ('never-output', 'rtsp:', 'camera_proxy', 'do-not-copy', 'sensor.secret'))
    assert len(calls) == 3


def test_cached_snapshot_is_defensive_copy(tmp_path):
    obj, calls = fixture_inventory(tmp_path)
    one = obj.snapshot()
    one['devices'].clear()
    assert len(obj.snapshot()['devices']) == 6
    assert len(calls) == 3


def test_failure_means_unknown_not_no_devices(tmp_path):
    def fail(*args):
        raise RuntimeError('private-token-or-url')
    obj = inventory_class()(command_reader=fail, ha_reader=fail, adb_reader=fail,
                            video_root=tmp_path / 'absent')
    result = obj.snapshot()
    assert all(row['status'] == 'unknown' for row in result['sources'].values())
    assert result['devices'] == []
    assert 'private-token-or-url' not in json.dumps(result)


def test_adb_requires_authorized_device_and_does_not_claim_camera(tmp_path):
    obj, _ = fixture_inventory(tmp_path)
    original = obj.command_reader
    def command(argv):
        if argv[0] == 'adb':
            return b'List of devices attached\nphone1 unauthorized\nphone2 device product:x model:Phone device:y transport_id:1\n'
        return original(argv)
    obj.command_reader = command
    rows = [row for row in obj.snapshot()['devices'] if row['source'] == 'adb']
    assert len(rows) == 2
    assert {row['state'] for row in rows} == {'unauthorized', 'device'}
    assert all(row['kind'] == 'android_endpoint' and row['capture_verified'] is False for row in rows)


def test_explicit_dynamic_module_commands_only():
    mod = importlib.import_module('src.modules.media_devices_module').MediaDevicesModule()
    for text in ('аудио видео устройства', 'медиа устройства', 'камеры и микрофоны'):
        assert mod.can_handle(text, text)
    assert not mod.can_handle('включи камеру', 'включи камеру')


def test_malformed_pulse_response_is_unknown(tmp_path):
    obj, _ = fixture_inventory(tmp_path)
    original = obj.command_reader
    obj.command_reader = lambda argv: b'{broken' if argv[0] == 'pactl' else original(argv)
    result = obj.snapshot()
    assert result['sources']['pulse_sources']['status'] == 'unknown'
    assert result['sources']['pulse_sinks']['status'] == 'unknown'


def test_output_cap_kills_and_reaps_producer_before_timeout(monkeypatch):
    import subprocess
    import time
    module = importlib.import_module('src.media_inventory')
    real_popen = subprocess.Popen
    children = []
    def producer(argv, **kwargs):
        child = real_popen([sys.executable, '-c',
                           'import os,time; os.write(1,b"x"*(2*1024*1024+1)); time.sleep(10)'], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(module.subprocess, 'Popen', producer)
    started = time.monotonic()
    with pytest.raises(ValueError, match='metadata_output_too_large'):
        module.bounded_command(['pactl', '-f', 'json', 'list', 'sources'])
    assert time.monotonic() - started < 2.5
    assert children and all(child.poll() is not None for child in children)


def test_existing_adb_protocol_reads_metadata_without_starting_daemon(monkeypatch):
    import socket
    import threading
    module = importlib.import_module('src.media_inventory')
    assert hasattr(module, 'existing_adb_devices'), 'Existing-server metadata reader required'
    client, server = socket.socketpair()
    payload = b'phone1\tdevice product:x model:Phone device:y transport_id:1\n'
    seen = []
    def serve():
        try:
            seen.append(server.recv(64))
            server.sendall(b'OKAY' + ('%04x' % len(payload)).encode() + payload)
        finally:
            server.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    def connected(address, timeout):
        assert address == ('127.0.0.1', 5037)
        assert timeout <= 3
        return client
    monkeypatch.setattr(module.socket, 'create_connection', connected)
    def forbidden(*args, **kwargs):
        pytest.fail('ADB metadata must never launch a process')
    monkeypatch.setattr(module.subprocess, 'Popen', forbidden)
    result = module.existing_adb_devices()
    thread.join(1)
    assert not thread.is_alive()
    assert seen == [b'000ehost:devices-l']
    assert result == b'List of devices attached\n' + payload


def test_missing_adb_server_is_unknown_without_autostart(monkeypatch, tmp_path):
    module = importlib.import_module('src.media_inventory')
    assert hasattr(module, 'existing_adb_devices'), 'Existing-server metadata reader required'
    def refused(*args, **kwargs):
        raise ConnectionRefusedError()
    monkeypatch.setattr(module.socket, 'create_connection', refused)
    obj, _ = fixture_inventory(tmp_path)
    obj.adb_reader = module.existing_adb_devices
    result = obj.snapshot()
    assert result['sources']['adb']['status'] == 'unknown'
