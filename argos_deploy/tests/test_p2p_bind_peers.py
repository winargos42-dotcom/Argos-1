"""
tests/test_p2p_bind_peers.py — ARGOS_P2P_BIND и ARGOS_P2P_PEERS для P2P-моста.

ARGOS_P2P_BIND  — адрес bind для UDP (55772) и TCP (55771); пусто = все интерфейсы.
ARGOS_P2P_PEERS — "host:port,..." — unicast-адресаты ARGOS_HELLO (кроме broadcast).
"""

import json
import socket
import threading
import time

import pytest

from src.connectivity import p2p_bridge
from src.connectivity.p2p_bridge import ArgosBridge, parse_p2p_peers


def _free_port(kind=socket.SOCK_DGRAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _RecordingSocket(socket.socket):
    """socket.socket, запоминающий адреса bind()."""

    binds = []

    def bind(self, address):
        _RecordingSocket.binds.append((self.type, address))
        return super().bind(address)


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("ARGOS_P2P_BIND", raising=False)
    monkeypatch.delenv("ARGOS_P2P_PEERS", raising=False)
    return monkeypatch


def test_defaults_keep_old_behavior(clean_env):
    bridge = ArgosBridge(core=None)
    assert bridge.udp_host == ""
    assert bridge.tcp_host == ""
    assert bridge.unicast_peers == []


def test_bind_env_applies_to_udp_and_tcp(clean_env):
    clean_env.setenv("ARGOS_P2P_BIND", " 127.0.0.1 ")
    bridge = ArgosBridge(core=None)
    assert bridge.udp_host == "127.0.0.1"
    assert bridge.tcp_host == "127.0.0.1"


def test_parse_peers(monkeypatch):
    monkeypatch.setattr(p2p_bridge, "BROADCAST_PORT", 55772)  # conftest может обнулять порт
    assert parse_p2p_peers("") == []
    assert parse_p2p_peers("127.0.0.1:55773") == [("127.0.0.1", 55773)]
    assert parse_p2p_peers(" 10.0.0.5:1 , host.lan , bad:port, :7, x:70000") == [
        ("10.0.0.5", 1),
        ("host.lan", 55772),
    ]


def test_peers_env(clean_env):
    clean_env.setenv("ARGOS_P2P_PEERS", "127.0.0.1:55773,127.0.0.2:9")
    bridge = ArgosBridge(core=None)
    assert bridge.unicast_peers == [("127.0.0.1", 55773), ("127.0.0.2", 9)]


def test_hello_is_sent_unicast_and_peer_hello_registered(clean_env):
    """Нода на loopback шлёт ARGOS_HELLO пиру и регистрирует его ответный HELLO."""
    node_port, peer_port = _free_port(), _free_port()
    clean_env.setenv("ARGOS_P2P_BIND", "127.0.0.1")
    clean_env.setenv("ARGOS_P2P_PEERS", f"127.0.0.1:{peer_port}")
    bridge = ArgosBridge(core=None)
    bridge.udp_port = node_port

    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", peer_port))
    peer.settimeout(5)
    bridge._running = True
    t = threading.Thread(target=bridge._udp_discovery, daemon=True)
    t.start()
    try:
        data, addr = peer.recvfrom(65536)
        msg = json.loads(data.decode())
        assert msg["type"] == "ARGOS_HELLO"
        assert msg["profile"]["node_id"] == bridge.profile.node_id
        assert addr == ("127.0.0.1", node_port)  # отправлено с сокета, привязанного к loopback

        hello = {"type": "ARGOS_HELLO",
                 "profile": {"node_id": "kolibri-test", "hostname": "kolibri"},
                 "sign": "unsigned"}
        peer.sendto(json.dumps(hello).encode(), ("127.0.0.1", node_port))
        deadline = time.time() + 5
        while time.time() < deadline and not bridge.registry.all():
            time.sleep(0.05)
        nodes = bridge.registry.all()
        assert [n["node_id"] for n in nodes] == ["kolibri-test"]
        assert nodes[0]["addr"] == "127.0.0.1"
    finally:
        bridge._running = False
        t.join(timeout=2)
        peer.close()


def test_tcp_server_binds_to_configured_host(clean_env, monkeypatch):
    clean_env.setenv("ARGOS_P2P_BIND", "127.0.0.1")
    tcp_port = _free_port(socket.SOCK_STREAM)
    monkeypatch.setattr(p2p_bridge, "P2P_PORT", tcp_port)
    monkeypatch.setattr(p2p_bridge.socket, "socket", _RecordingSocket)
    _RecordingSocket.binds = []
    bridge = ArgosBridge(core=None)
    bridge._running = True
    t = threading.Thread(target=bridge._tcp_server, daemon=True)
    t.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not _RecordingSocket.binds:
            time.sleep(0.05)
        assert (socket.SOCK_STREAM, ("127.0.0.1", tcp_port)) in _RecordingSocket.binds
    finally:
        bridge._running = False
        t.join(timeout=4)
