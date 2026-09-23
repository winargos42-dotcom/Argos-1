"""
tests/test_p2p_bind_peers.py — ARGOS_P2P_BIND и ARGOS_P2P_PEERS для P2P-моста.

ARGOS_P2P_BIND  — совместимый алиас ARGOS_P2P_BIND_HOST: адрес bind для UDP и TCP.
                  По умолчанию теперь 127.0.0.1 (раньше "" — все интерфейсы).
ARGOS_P2P_PEERS — "host:port,..." — дополнительные unicast-адресаты подписанного
                  discovery-пакета; адреса вне ARGOS_P2P_ALLOWED_SUBNET пропускаются.

После сверки с живым рантаймом (усиленный мост Codex) неподписанный ARGOS_HELLO
больше не регистрирует пира — только HMAC-конверт argos-p2p-v2.
"""

import json
import socket
import threading
import time

import pytest

from src.connectivity import p2p_bridge
from src.connectivity.p2p_auth import Auth, canonical, recv_frame, send_frame
from src.connectivity.p2p_bridge import ArgosBridge, parse_p2p_peers

KEY = b"0123456789abcdef" * 4


def _free_port(kind=socket.SOCK_DGRAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("ARGOS_P2P_BIND", "ARGOS_P2P_PEERS", "ARGOS_P2P_BIND_HOST",
                 "ARGOS_P2P_DISCOVERY_BIND", "ARGOS_P2P_DISCOVERY_TARGET",
                 "ARGOS_P2P_ALLOWED_SUBNET", "ARGOS_NETWORK_SECRET_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARGOS_NETWORK_SECRET", KEY.decode())
    return monkeypatch


def test_defaults_are_loopback_only(clean_env):
    bridge = ArgosBridge(core=None)
    assert bridge.udp_host == "127.0.0.1"
    assert bridge.tcp_host == "127.0.0.1"
    assert bridge.unicast_peers == []


def test_bind_alias_applies_to_udp_and_tcp(clean_env):
    clean_env.setenv("ARGOS_P2P_BIND", " 127.0.0.5 ")
    bridge = ArgosBridge(core=None)
    assert bridge.bind_host == "127.0.0.5"
    assert bridge.udp_host == "127.0.0.5"
    assert bridge.tcp_host == "127.0.0.5"
    assert bridge.discovery_target == "127.0.0.5"


def test_canonical_bind_host_wins_over_alias(clean_env):
    clean_env.setenv("ARGOS_P2P_BIND", "127.0.0.5")
    clean_env.setenv("ARGOS_P2P_BIND_HOST", "127.0.0.9")
    assert ArgosBridge(core=None).bind_host == "127.0.0.9"


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


def test_peers_outside_allowed_subnet_are_not_targeted(clean_env):
    clean_env.setenv("ARGOS_P2P_PEERS", "127.0.0.2:9,192.168.1.10:9,host.lan:9")
    bridge = ArgosBridge(core=None)
    targets = bridge._discovery_targets()
    assert ("127.0.0.2", 9) in targets
    assert all(host.startswith("127.") for host, _ in targets)


def test_signed_discovery_sent_unicast_and_only_signed_peer_registered(clean_env):
    """Нода шлёт подписанный discovery пиру из ARGOS_P2P_PEERS; неподписанный
    ARGOS_HELLO игнорируется, подписанный профиль регистрируется."""
    node_port, peer_port = _free_port(), _free_port()
    clean_env.setenv("ARGOS_P2P_BIND", "127.0.0.1")
    clean_env.setenv("ARGOS_P2P_PEERS", f"127.0.0.1:{peer_port}")
    clean_env.setenv("ARGOS_P2P_BROADCAST_PORT", str(node_port))
    bridge = ArgosBridge(core=None)
    bridge._auth = Auth(KEY)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", node_port))
    udp.settimeout(0.1)
    bridge._udp = udp

    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", peer_port))
    peer.settimeout(5)
    t = threading.Thread(target=bridge._udp_discovery, daemon=True)
    t.start()
    try:
        data, addr = peer.recvfrom(65536)
        remote = Auth(KEY)
        profile = remote.verify(json.loads(data), "discovery")
        assert profile["node_id"] == bridge.profile.node_id
        assert addr == ("127.0.0.1", node_port)

        hello = {"type": "ARGOS_HELLO",
                 "profile": {"node_id": "unsigned-test", "hostname": "kolibri"},
                 "sign": "unsigned"}
        peer.sendto(json.dumps(hello).encode(), ("127.0.0.1", node_port))
        signed = remote.pack("discovery", {"node_id": "kolibri-test", "hostname": "kolibri"})
        peer.sendto(canonical(signed), ("127.0.0.1", node_port))
        deadline = time.time() + 5
        while time.time() < deadline and not bridge.registry.all():
            time.sleep(0.05)
        nodes = bridge.registry.all()
        assert [n["node_id"] for n in nodes] == ["kolibri-test"]
        assert nodes[0]["addr"] == "127.0.0.1"
    finally:
        bridge._stop.set()
        t.join(timeout=2)
        udp.close()
        peer.close()


def test_start_binds_tcp_and_udp_to_configured_host(clean_env):
    clean_env.setenv("ARGOS_P2P_BIND", "127.0.0.1")
    clean_env.setenv("ARGOS_P2P_PORT", str(_free_port(socket.SOCK_STREAM)))
    clean_env.setenv("ARGOS_P2P_BROADCAST_PORT", str(_free_port()))
    bridge = ArgosBridge(core=None)
    try:
        assert "running" in bridge.start()
        assert bridge._tcp.getsockname() == ("127.0.0.1", bridge.port)
        assert bridge._udp.getsockname() == ("127.0.0.1", bridge.udp_port)
    finally:
        bridge.stop()


def test_start_refuses_without_secret_and_logs(clean_env, caplog):
    clean_env.delenv("ARGOS_NETWORK_SECRET")
    bridge = ArgosBridge(core=None)
    with caplog.at_level("WARNING", logger="argos.p2p"):
        with pytest.raises(ValueError):
            bridge.start()
    assert bridge._running is False and bridge._tcp is None
    assert "P2P disabled" in caplog.text


def test_default_secret_is_refused(clean_env):
    clean_env.setenv("ARGOS_NETWORK_SECRET", "argos_default_secret")
    with pytest.raises(ValueError):
        ArgosBridge(core=None).start()
    assert "failed" in ArgosBridge(core=None).connect_to("127.0.0.1")


def test_unknown_actions_such_as_get_skill_are_refused(clean_env):
    """Удалённой выдачи/загрузки кода навыков нет: get_skill → unsupported_action."""

    class Conn:
        def __init__(self, raw):
            self.raw, self.sent = raw, b""

        def recv(self, n):
            out, self.raw = self.raw[:n], self.raw[n:]
            return out

        def sendall(self, raw):
            self.sent += raw

        def settimeout(self, n):
            pass

        def close(self):
            pass

    bridge = ArgosBridge(core=None)
    remote = Auth(KEY)
    request = remote.pack("request", {"action": "get_skill", "name": "a/../../x"})
    out = Conn(b"")
    send_frame(out, request)
    conn = Conn(out.sent)
    bridge._handle_client(conn, "127.0.0.2")
    reply = remote.verify(recv_frame(Conn(conn.sent)), "response", reply_to=request["nonce"])
    assert reply == {"error": "unsupported_action"}
    assert "unsupported" in bridge.sync_skills_from_network()
