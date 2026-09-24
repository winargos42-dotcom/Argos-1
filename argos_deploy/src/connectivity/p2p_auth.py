"""Authenticated, bounded P2P v2 envelopes. HMAC authenticates; it does not encrypt."""
import hashlib
import hmac
import json
import math
import os
import re
import stat
import struct
import threading
import time
import uuid

MAX_FRAME = 65536
TTL = 30


def load_key():
    path = os.getenv('ARGOS_NETWORK_SECRET_FILE', '').strip()
    if path:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError('P2P key file must be private and owned by service user')
            key = stream.read(4097).strip()
    else:
        key = os.getenv('ARGOS_NETWORK_SECRET', '').encode()
    if not 32 <= len(key) <= 4096 or key.lower() in (b'argos_default_secret', b'change_me', b'changeme'):
        raise ValueError('P2P requires a provisioned random secret of at least 32 bytes')
    return key


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


class Auth:
    def __init__(self, key, clock=time.time, cache_size=1024):
        if len(key) < 32:
            raise ValueError('Weak P2P key')
        self.key, self.clock, self.limit = key, clock, cache_size
        self.seen = {}
        self.lock = threading.Lock()

    def pack(self, direction, payload, reply_to=''):
        if direction not in ('request', 'response', 'discovery') or not isinstance(payload, dict):
            raise ValueError('Invalid envelope')
        body = dict(protocol='argos-p2p-v2', direction=direction, timestamp=self.clock(),
                    nonce=uuid.uuid4().hex, reply_to=reply_to, payload=payload)
        body['mac'] = hmac.new(self.key, canonical(body), hashlib.sha256).hexdigest()
        return body

    def verify(self, packet, direction, reply_to=''):
        fields = {'protocol','direction','timestamp','nonce','reply_to','payload','mac'}
        if not isinstance(packet, dict) or set(packet) != fields:
            raise ValueError('Invalid envelope')
        now, ts = self.clock(), packet['timestamp']
        if (packet['protocol'] != 'argos-p2p-v2' or packet['direction'] != direction
                or packet['reply_to'] != reply_to or not isinstance(packet['payload'], dict)
                or type(ts) not in (int, float) or not math.isfinite(ts) or abs(now-ts) > TTL
                or not isinstance(packet['nonce'], str) or not re.fullmatch(r'[0-9a-f]{32}', packet['nonce'])
                or not isinstance(packet['mac'], str) or not re.fullmatch(r'[0-9a-f]{64}', packet['mac'])):
            raise ValueError('Invalid envelope')
        body = {k: v for k,v in packet.items() if k != 'mac'}
        expected = hmac.new(self.key, canonical(body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, packet['mac']):
            raise ValueError('Unauthenticated envelope')
        with self.lock:
            self.seen = {k: until for k,until in self.seen.items() if until >= now}
            if packet['nonce'] in self.seen or len(self.seen) >= self.limit:
                raise ValueError('Replayed envelope or replay cache full')
            # Future-dated packets remain cached until their entire validity window expires.
            self.seen[packet['nonce']] = ts+TTL
        return packet['payload']


def send_frame(sock, packet):
    body = canonical(packet)
    if not 0 < len(body) <= MAX_FRAME:
        raise ValueError('Frame too large')
    sock.sendall(struct.pack('!I', len(body))+body)


def recv_frame(sock, seconds=5):
    until = time.monotonic()+seconds
    def exact(size):
        out = bytearray()
        while len(out) < size:
            remaining = until-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Frame deadline')
            sock.settimeout(remaining)
            chunk = sock.recv(size-len(out))
            if not chunk:
                raise ValueError('Incomplete frame')
            out.extend(chunk)
        return bytes(out)
    size = struct.unpack('!I', exact(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError('Invalid frame length')
    packet = json.loads(exact(size))
    if not isinstance(packet, dict):
        raise ValueError('Invalid frame object')
    return packet
