"""Offline only: fake sockets/threads, no actual listener or provider call."""
import json
import pytest
from src.connectivity import p2p_bridge as p

class Profile:
    node_id = 'local-node'
    hostname = 'test'
    def to_dict(self):
        return {'node_id': self.node_id, 'hostname': self.hostname, 'age_days': 1,
                'authority': 1, 'skills': [], 'power': {'index': 1}, 'role': 'worker'}
    def get_age_days(self): return 1
    def get_power(self): return {'index': 1}
    def get_authority(self): return 1
    def get_skills(self): return []

@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setattr(p, 'NodeProfile', Profile)
    monkeypatch.setattr(p.ArgosBridge, '_get_local_ip', lambda self: '127.0.0.1')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('REDIS_HOST', raising=False)
    monkeypatch.setenv('ARGOS_NETWORK_SECRET', '0123456789abcdef'*4)
    monkeypatch.setenv('ARGOS_P2P_BIND_HOST', '127.0.0.1')
    monkeypatch.setenv('ARGOS_P2P_ALLOWED_SUBNET', '127.0.0.0/24')
    monkeypatch.setenv('ARGOS_P2P_DISCOVERY_BIND', '127.0.0.1')
    monkeypatch.setenv('ARGOS_P2P_DISCOVERY_TARGET', '127.0.0.1')
    monkeypatch.delenv('ARGOS_NETWORK_SECRET_FILE', raising=False)
    # conftest обнуляет ARGOS_P2P_BROADCAST_PORT; здесь сокеты фейковые, нужен валидный порт.
    monkeypatch.setenv('ARGOS_P2P_PORT', '55771')
    monkeypatch.setenv('ARGOS_P2P_BROADCAST_PORT', '55772')
    for alias in ('ARGOS_P2P_BIND', 'ARGOS_P2P_PEERS'):
        monkeypatch.delenv(alias, raising=False)
    return p.ArgosBridge()

class Conn:
    def __init__(self, raw): self.raw, self.sent, self.closed = raw, b'', False
    def recv(self, n):
        result, self.raw = self.raw[:n], self.raw[n:]
        return result
    def sendall(self, raw): self.sent += raw
    def settimeout(self, n): pass
    def close(self): self.closed = True

def test_legacy_plain_secret_is_rejected(bridge):
    connection = Conn(json.dumps({'action': 'status', 'secret': 'argos_default_secret'}).encode())
    bridge._handle_client(connection, '127.0.0.2')
    assert b'local-node' not in connection.sent
    assert connection.closed

def test_start_rejects_missing_secret_before_threads(bridge, monkeypatch):
    monkeypatch.delenv('ARGOS_NETWORK_SECRET', raising=False)
    class NoThread:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
    monkeypatch.setattr(p.threading, 'Thread', NoThread)
    with pytest.raises(ValueError): bridge.start()
    assert bridge._running is False

def test_skill_download_never_writes(bridge):
    assert 'unsupported' in bridge.sync_skills_from_network().lower()

def test_envelope_auth_direction_replay_expiry_and_reply_binding():
    from src.connectivity.p2p_auth import Auth
    key = b'0123456789abcdef'*4
    now = [100.0]
    sender, receiver = Auth(key, clock=lambda: now[0]), Auth(key, clock=lambda: now[0])
    request = sender.pack('request', {'action': 'status'})
    assert key.decode() not in json.dumps(request)
    assert receiver.verify(request, 'request')['action'] == 'status'
    with pytest.raises(ValueError): receiver.verify(request, 'request')
    response = receiver.pack('response', {'ok': True}, reply_to=request['nonce'])
    with pytest.raises(ValueError): sender.verify(response, 'request')
    with pytest.raises(ValueError): sender.verify(response, 'response', reply_to='wrong')
    assert sender.verify(response, 'response', reply_to=request['nonce']) == {'ok': True}
    old = sender.pack('discovery', {'node_id': 'peer'})
    now[0] += 31
    with pytest.raises(ValueError): receiver.verify(old, 'discovery')

def test_tamper_and_cache_capacity_fail_closed():
    from src.connectivity.p2p_auth import Auth
    a, b = Auth(b'A'*32), Auth(b'A'*32, cache_size=1)
    first = a.pack('request', {'action': 'status'})
    broken = dict(first, payload={'action': 'query'})
    with pytest.raises(ValueError): b.verify(broken, 'request')
    b.verify(first, 'request')
    with pytest.raises(ValueError): b.verify(a.pack('request', {'action': 'status'}), 'request')

def test_split_framing_and_length_rejected():
    from src.connectivity.p2p_auth import send_frame, recv_frame
    sock = Conn(b'')
    send_frame(sock, {'ok': True})
    class Split(Conn):
        def recv(self, n): return super().recv(min(n, 1))
    assert recv_frame(Split(sock.sent)) == {'ok': True}
    with pytest.raises(ValueError): recv_frame(Conn(b'\xff'*4))

def test_udp_must_authenticate_before_registry(bridge):
    bridge._ensure_auth()
    with pytest.raises(ValueError):
        bridge._receive_discovery(json.dumps({'type':'ARGOS_HELLO', 'profile': {'node_id':'forged'}}).encode(), '127.0.0.2')
    assert bridge.registry.count() == 0
    packet = bridge._auth.pack('discovery', Profile().to_dict() | {'node_id':'peer'})
    bridge._receive_discovery(json.dumps(packet).encode(), '127.0.0.2')
    assert bridge.registry.count() == 1
    with pytest.raises(ValueError): bridge._receive_discovery(json.dumps(packet).encode(), '127.0.0.2')

def test_bind_failure_propagates_and_closes_partial_sockets(bridge, monkeypatch):
    opened = []
    class BindFail:
        def __init__(self, *args): self.closed=False; opened.append(self)
        def setsockopt(self,*args): pass
        def settimeout(self,*args): pass
        def bind(self,*args): raise OSError('occupied')
        def close(self): self.closed=True
    monkeypatch.setattr(p.socket, 'socket', BindFail)
    with pytest.raises(OSError): bridge.start()
    assert not bridge._running
    assert opened and all(s.closed for s in opened)

def test_authenticated_request_gets_bound_response_without_raw_secret(bridge):
    from src.connectivity.p2p_auth import Auth, send_frame, recv_frame
    remote = Auth(b'0123456789abcdef'*4)
    request = remote.pack('request', {'action':'status'})
    output = Conn(b'')
    send_frame(output, request)
    connection = Conn(output.sent)
    bridge._handle_client(connection, '127.0.0.2')
    response = recv_frame(Conn(connection.sent))
    assert remote.verify(response, 'response', reply_to=request['nonce'])['node_id'] == 'local-node'
    assert b'0123456789abcdef' not in connection.sent
    assert connection.closed

def test_start_idempotent_and_stop_closes_and_joins(bridge, monkeypatch):
    sockets, threads = [], []
    class FakeSocket:
        def __init__(self, *args): self.closed=False; sockets.append(self)
        def setsockopt(self,*args): pass
        def settimeout(self,*args): pass
        def bind(self,*args): pass
        def listen(self,*args): pass
        def close(self): self.closed=True
    class FakeThread:
        def __init__(self, target, daemon, name):
            self.name, self.ident, self.live = name, None, False
            threads.append(self)
        def start(self): self.ident=1; self.live=True
        def join(self, timeout): self.live=False
        def is_alive(self): return self.live
    monkeypatch.setattr(p.socket, 'socket', FakeSocket)
    monkeypatch.setattr(p.threading, 'Thread', FakeThread)
    assert 'running' in bridge.start()
    assert 'external_peer_count=0' in bridge.start()
    assert len(sockets) == 2 and len(threads) == 2
    bridge.stop()
    assert all(s.closed for s in sockets)
    assert all(not t.live for t in threads)
    assert 'stopped' in bridge.network_status()

def test_query_only_dedicated_bounded_adapter(bridge):
    called = []
    bridge.distributor.query_local = lambda prompt: called.append(prompt) or {'answer':'safe'}
    assert bridge.route_query('hello') == '[LOCAL:ai] safe'
    assert called == ['hello']

@pytest.mark.parametrize('answer', ['hello', '', '   '])
def test_local_query_http_is_loopback_bounded_and_closed(bridge, monkeypatch, answer):
    monkeypatch.setenv('ARGOS_P2P_OLLAMA_MODEL', 'test:tiny')
    records = {}
    class Socket:
        def settimeout(self, value): records['timeout'] = value
        def shutdown(self, how): pass
        def close(self): records['socket_closed'] = True
    class HTTP:
        def __init__(self, host, port, timeout):
            records['endpoint'] = (host, port, timeout)
            self.sock = Socket()
        def connect(self): pass
        def request(self, method, path, body, headers):
            records['request'] = (method, path, json.loads(body))
        def getresponse(self): return self
        status = 200
        def read(self, maximum):
            records['max_read'] = maximum
            return json.dumps({'response':answer, 'done':True}).encode()
        def close(self): records['closed'] = True
    class Timer:
        def __init__(self, seconds, target): records['deadline'] = seconds
        def start(self): pass
        def cancel(self): records['cancelled'] = True
        def join(self, seconds): records['joined'] = True
    monkeypatch.setattr(p.http.client, 'HTTPConnection', HTTP)
    monkeypatch.setattr(p.threading, 'Timer', Timer)
    result = bridge._query_local('say hello')
    if answer.strip():
        assert result['answer'] == 'hello'
    else:
        assert result == {'error':'local_inference_incomplete'}
    assert records['endpoint'] == ('127.0.0.1', 11434, 1)
    assert records['request'] == ('POST', '/api/generate', {'model':'test:tiny', 'prompt':'say hello', 'stream':False, 'options':{'num_predict':128}})
    assert records['deadline'] == 8 and records['max_read'] == 32769
    assert records['closed'] and records['cancelled'] and records['joined']
    assert not bridge._clients

def test_private_key_loader_rejects_default_and_public_file(monkeypatch, tmp_path):
    from src.connectivity.p2p_auth import load_key
    monkeypatch.delenv('ARGOS_NETWORK_SECRET_FILE', raising=False)
    monkeypatch.setenv('ARGOS_NETWORK_SECRET', 'argos_default_secret')
    with pytest.raises(ValueError): load_key()
    path = tmp_path/'key'
    path.write_bytes(b'0123456789abcdef'*4)
    path.chmod(0o644)
    monkeypatch.setenv('ARGOS_NETWORK_SECRET_FILE', str(path))
    with pytest.raises(ValueError): load_key()
    path.chmod(0o600)
    assert load_key() == b'0123456789abcdef'*4

def test_malformed_nested_udp_does_not_kill_discovery_loop(bridge):
    """First pre-auth packet exhausts JSON depth; the next signed peer still registers."""
    bridge._ensure_auth()
    valid = json.dumps(bridge._auth.pack('discovery', Profile().to_dict() | {'node_id':'peer'})).encode()
    nested = b'['*2000+b'0'+b']'*2000
    packets = [nested, valid]
    class Datagram:
        def sendto(self, *args): pass
        def recvfrom(self, maximum):
            if packets:
                return packets.pop(0), ('127.0.0.2', 55772)
            bridge._stop.set()
            raise p.socket.timeout()
    bridge._udp = Datagram()
    bridge._udp_discovery()  # synchronous fake socket; no thread or network
    assert not packets
    assert bridge.registry.count() == 1
    assert bridge.registry.all()[0]['node_id'] == 'peer'
