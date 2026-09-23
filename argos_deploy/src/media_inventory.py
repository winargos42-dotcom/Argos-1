"""Read-only media metadata. No capture, playback, inference or device toggles.

Two local metadata commands, an existing ADB server query and an HA HTTP worker, each
limited to three seconds. A 30-second cache avoids repeated discovery commands.
"""
import copy
import http.client
import json
import os
from pathlib import Path
import re
import selectors
import socket
import stat
import subprocess
import sys
import threading
import time

LIMIT = 2 * 1024 * 1024
COMMANDS = (('pactl', '-f', 'json', 'list', 'sources'),
            ('pactl', '-f', 'json', 'list', 'sinks'))
SOURCE_NAMES = ('pulse_sources', 'pulse_sinks', 'v4l2', 'adb', 'home_assistant')


def clean(value, fallback='unknown'):
    if not isinstance(value, str):
        return fallback
    text = ''.join(ch for ch in value if ch.isprintable()).strip()[:160]
    if '://' in text or re.search(r'(?:token|password|secret)\s*[=:]', text, re.I):
        return fallback
    return text or fallback


def device(source, ident, kind, name, state):
    return {'source': source, 'id': clean(ident), 'kind': kind, 'name': clean(name),
            'state': clean(state), 'capture_verified': False, 'playback_verified': False}


def bounded_command(argv):
    """Hard output cap while reading, with a total three-second deadline."""
    worker = [sys.executable, str(Path(__file__).resolve()), '--ha-states']
    if tuple(argv) not in COMMANDS and argv != worker:
        raise ValueError('unsupported_metadata_command')
    deadline = time.monotonic() + 3
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
    raw = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(child.stdout.fileno(), False)
            selector.register(child.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError('metadata_command_timeout')
                try:
                    chunk = os.read(child.stdout.fileno(), min(65536, LIMIT + 1 - len(raw)))
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > LIMIT:
                    raise ValueError('metadata_output_too_large')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('metadata_command_timeout')
        if child.wait(timeout=remaining) != 0:
            raise ValueError('metadata_command_failed')
        return bytes(raw)
    finally:
        if child.poll() is None:
            child.kill()
        try:
            child.wait(timeout=0.5)
        finally:
            child.stdout.close()


def existing_adb_devices():
    """Query only an existing loopback ADB server; never start a daemon."""
    deadline = time.monotonic() + 3

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError('adb_metadata_timeout')
        return seconds

    with socket.create_connection(('127.0.0.1', 5037), timeout=remaining()) as connection:
        def receive(size):
            raw = bytearray()
            while len(raw) < size:
                connection.settimeout(remaining())
                chunk = connection.recv(size - len(raw))
                if not chunk:
                    raise ValueError('incomplete_adb_metadata')
                raw.extend(chunk)
            return bytes(raw)

        connection.settimeout(remaining())
        connection.sendall(b'000ehost:devices-l')
        if receive(4) != b'OKAY':
            raise ValueError('adb_metadata_failed')
        length = receive(4)
        if not re.fullmatch(rb'[0-9a-fA-F]{4}', length):
            raise ValueError('invalid_adb_metadata_length')
        # Four hex digits inherently cap the server response at 65535 bytes.
        return b'List of devices attached\n' + receive(int(length, 16))


def ha_allowlist(rows):
    if not isinstance(rows, list):
        raise ValueError('invalid_ha_schema')
    result = []
    allowed_states = {'idle', 'recording', 'streaming', 'off', 'on', 'paused', 'playing',
                      'standby', 'buffering', 'unavailable', 'unknown'}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ident = row.get('entity_id', '')
        if not isinstance(ident, str) or not re.fullmatch(r'(camera|media_player)\.[a-z0-9_]{1,120}', ident):
            continue
        attributes = row.get('attributes')
        if not isinstance(attributes, dict):
            attributes = {}
        state = row.get('state')
        result.append({'entity_id': ident, 'state': state if isinstance(state, str) and state in allowed_states else 'unknown',
                       'attributes': {'friendly_name': clean(attributes.get('friendly_name'), ident)}})
        if len(result) >= 512:
            break
    return result


def ha_worker():
    """Only invoked in the parent-bounded worker; credentials never enter argv/stdout."""
    path = Path('/etc/argos/ha-token')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('private_ha_credential_required')
    with path.open() as stream:
        token = stream.read(8193).strip()
    if not token or len(token) > 8192 or '\n' in token or '\r' in token:
        raise ValueError('invalid_ha_credential')
    # HTTPConnection neither reads proxy environment nor follows redirects.
    connection = http.client.HTTPConnection('127.0.0.1', 8123, timeout=3)
    try:
        connection.request('GET', '/api/states', headers={'Authorization': 'Bearer ' + token})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('ha_metadata_http_error')
        raw = response.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('ha_metadata_too_large')
        return ha_allowlist(json.loads(raw))
    finally:
        connection.close()


class MediaInventory:
    def __init__(self, *, command_reader=None, ha_reader=None, adb_reader=None,
                 video_root=Path('/sys/class/video4linux')):
        self.command_reader = command_reader or bounded_command
        self.ha_reader = ha_reader or self._ha
        self.adb_reader = adb_reader or existing_adb_devices
        self.video_root = Path(video_root)
        self._cache = None
        self._cached_at = 0
        self._lock = threading.Lock()

    @staticmethod
    def _ha():
        return json.loads(bounded_command([sys.executable, str(Path(__file__).resolve()), '--ha-states']))

    def _pulse(self, direction):
        rows = json.loads(self.command_reader(['pactl', '-f', 'json', 'list', direction]))
        if not isinstance(rows, list) or len(rows) > 512:
            raise ValueError('invalid_pulse_schema')
        devices = []
        source = 'pulse_' + direction
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('name'), str):
                raise ValueError('invalid_pulse_device')
            name = row['name']
            kind = 'audio_output' if direction == 'sinks' else 'audio_input'
            if direction == 'sources' and (name.endswith('.monitor') or row.get('monitor_of_sink') not in (None, '', 4294967295)):
                kind = 'audio_monitor'
            devices.append(device(source, name, kind, row.get('description', name), row.get('state', 'unknown')))
        return devices

    def _video(self):
        if not self.video_root.is_dir():
            raise FileNotFoundError('video_metadata_unavailable')
        devices = []
        for node in sorted(self.video_root.iterdir())[:64]:
            if not re.fullmatch(r'video\d+', node.name):
                continue
            with (node / 'name').open() as stream:
                name = stream.read(256).strip()
            # video0/video1 may describe the same physical camera; IDs are endpoints.
            devices.append(device('v4l2', node.name, 'video_input', name, 'present; capture not tested'))
        return devices

    def _adb(self):
        raw = self.adb_reader()
        if len(raw) > LIMIT:
            raise ValueError('adb_metadata_too_large')
        lines = raw.decode('utf-8', errors='strict').splitlines()
        if not lines or lines[0].strip() != 'List of devices attached':
            raise ValueError('invalid_adb_schema')
        result = []
        for line in lines[1:513]:
            fields = line.split()
            if not fields:
                continue
            if len(fields) < 2 or fields[1] not in ('device', 'offline', 'unauthorized', 'recovery', 'sideload', 'bootloader'):
                raise ValueError('invalid_adb_device')
            name = next((field[6:].replace('_', ' ') for field in fields[2:] if field.startswith('model:')), fields[0])
            # ADB reachability is not an available camera/microphone stream.
            result.append(device('adb', fields[0], 'android_endpoint', name, fields[1]))
        return result

    def _home_assistant(self):
        return [device('home_assistant', row['entity_id'],
                       'video_input' if row['entity_id'].startswith('camera.') else 'audio_output',
                       row['attributes']['friendly_name'], row['state']) for row in ha_allowlist(self.ha_reader())]

    def snapshot(self):
        now = time.monotonic()
        if self._cache is not None and now - self._cached_at < 30:
            result = copy.deepcopy(self._cache)
            result['cached'] = True
            return result
        if not self._lock.acquire(blocking=False):
            if self._cache is not None:
                result = copy.deepcopy(self._cache)
                result.update(cached=True, stale=True)
                return result
            return {'generated_at': time.time(), 'cached': False, 'devices': [],
                    'sources': {name: {'status': 'unknown', 'reason': 'refresh_in_progress'} for name in SOURCE_NAMES}}
        try:
            result = {'generated_at': time.time(), 'cached': False, 'devices': [], 'sources': {},
                      'note': 'Metadata discovery only; endpoint entries may share one physical device.'}
            readers = [('pulse_sources', lambda: self._pulse('sources')), ('pulse_sinks', lambda: self._pulse('sinks')),
                       ('v4l2', self._video), ('adb', self._adb), ('home_assistant', self._home_assistant)]
            for name, reader in readers:
                if time.monotonic() - now >= 11:
                    result['sources'][name] = {'status': 'unknown', 'reason': 'discovery_budget_exhausted'}
                    continue
                try:
                    rows = reader()
                    result['devices'].extend(rows)
                    result['sources'][name] = {'status': 'ok', 'count': len(rows)}
                except Exception:
                    result['sources'][name] = {'status': 'unknown', 'reason': 'metadata_probe_failed'}
            self._cached_at = time.monotonic()
            self._cache = copy.deepcopy(result)
            return result
        finally:
            self._lock.release()


if __name__ == '__main__':
    try:
        if sys.argv[1:] != ['--ha-states']:
            raise ValueError('invalid_metadata_worker')
        print(json.dumps(ha_worker(), ensure_ascii=False))
    except Exception:
        raise SystemExit(1)
