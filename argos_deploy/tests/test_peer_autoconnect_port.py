import ipaddress
import json

import pytest

from src.connectivity import p2p_bridge, peer_autoconnect


@pytest.mark.parametrize("bridge_port", [55771, 55881])
def test_autoconnect_probes_the_port_used_by_bridge(monkeypatch, tmp_path, bridge_port):
    peer_ip = "192.0.2.10"
    config = tmp_path / "peers.json"
    config.write_text(json.dumps({
        "p2p_port": 8000,
        "peers": [{"name": "test-peer", "public_ip": peer_ip}],
    }), encoding="utf-8")
    monkeypatch.setattr(peer_autoconnect, "PEERS_CONFIG", str(config))
    monkeypatch.setattr(peer_autoconnect, "_own_ips", lambda: {"127.0.0.1"})
    monkeypatch.setattr(p2p_bridge, "P2P_PORT", bridge_port)

    class PeerSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def connect(self, address):
            if address != (peer_ip, bridge_port):
                raise ConnectionRefusedError(address)

        def settimeout(self, timeout):
            pass

        def close(self):
            pass

    def create_connection(address, timeout):
        sock = PeerSocket()
        sock.connect(address)
        return sock

    monkeypatch.setattr(peer_autoconnect.socket, "create_connection", create_connection)
    # Мост после сверки с рантаймом говорит только HMAC-фреймами (_request_peer);
    # старый plaintext-обмен {"action": "status"} больше не поддерживается,
    # поэтому здесь подменяется аутентифицированный запрос, а не сырой сокет.
    requests = []

    def request_peer(address, message, port=None):
        requests.append((address, message))
        return {"node_id": "remote-node", "hostname": "test-peer"}

    bridge = p2p_bridge.ArgosBridge.__new__(p2p_bridge.ArgosBridge)
    bridge.registry = p2p_bridge.NodeRegistry()
    bridge.profile = type("Profile", (), {"node_id": "local-node"})()
    bridge.port = bridge_port
    bridge.bind_host = "192.0.2.1"
    bridge._running = False
    bridge.allowed_network = ipaddress.ip_network("192.0.2.0/24")
    bridge._request_peer = request_peer
    autoconnect = peer_autoconnect.PeerAutoConnect(bridge)

    autoconnect._connect_all()

    nodes = bridge.registry.all()
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == "remote-node"
    assert nodes[0]["addr"] == peer_ip
    assert "🟢 connected" in autoconnect.status()
    assert requests == [(peer_ip, {"action": "status"})]
