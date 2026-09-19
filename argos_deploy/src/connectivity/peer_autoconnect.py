"""
peer_autoconnect.py — Auto-connect Argos P2P mesh on startup.

Reads config/peers.json, skips own IP, tries private IP first then public IP.
Runs in a background thread so it never blocks boot.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time

from src.argos_logger import get_logger

log = get_logger("argos.p2p.autoconnect")

PEERS_CONFIG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "config", "peers.json"
)

CONNECT_DELAY_SEC  = 30   # wait after orchestrator init before first connect
RETRY_INTERVAL_SEC = 120  # retry disconnected peers every 2 min
MAX_RETRIES        = 5    # give up after N consecutive failures per peer
TCP_PROBE_TIMEOUT  = 3    # seconds for port reachability check


def _own_ips() -> set[str]:
    ips: set[str] = {"127.0.0.1", "localhost"}
    try:
        hostname = socket.gethostname()
        ips.add(socket.gethostbyname(hostname))
    except Exception:
        pass
    try:
        import psutil
        for iface in psutil.net_if_addrs().values():
            for addr in iface:
                if addr.family == socket.AF_INET:
                    ips.add(addr.address)
    except Exception:
        pass
    env_ip = os.getenv("ARGOS_MY_IP", "").strip()
    if env_ip:
        ips.add(env_ip)
    return ips


def _load_peers() -> list[dict]:
    try:
        with open(PEERS_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("peers", [])
    except FileNotFoundError:
        log.warning("[AutoConnect] config/peers.json not found")
        return []
    except Exception as e:
        log.error("[AutoConnect] Failed to load peers.json: %s", e)
        return []


def _port_open(ip: str, port: int) -> bool:
    """Quick TCP probe: returns True if port is reachable."""
    try:
        with socket.create_connection((ip, port), timeout=TCP_PROBE_TIMEOUT):
            return True
    except Exception:
        return False


def _best_ip(peer: dict, p2p_port: int) -> str | None:
    """Try private IP first, fall back to public IP."""
    priv = peer.get("private_ip", "").strip()
    pub  = peer.get("public_ip",  "").strip()

    if priv and _port_open(priv, p2p_port):
        log.debug("[AutoConnect] %s reachable via private IP %s", peer.get("name"), priv)
        return priv
    if pub and _port_open(pub, p2p_port):
        log.debug("[AutoConnect] %s reachable via public IP %s", peer.get("name"), pub)
        return pub
    return None


class PeerAutoConnect:
    def __init__(self, bridge):
        from src.connectivity.p2p_bridge import P2P_PORT

        self.bridge = bridge
        self._own_ips = _own_ips()
        self._peers = _load_peers()
        self._p2p_port = P2P_PORT
        self._failures: dict[str, int] = {}
        self._connected: set[str] = set()
        self._running = False

    def start(self):
        self._running = True
        t = threading.Thread(
            target=self._connect_loop,
            daemon=True,
            name="ArgosP2PAutoConnect"
        )
        t.start()
        log.info("[AutoConnect] Started — %d static peers", len(self._peers))
        return t

    def stop(self):
        self._running = False

    def _connect_loop(self):
        log.info("[AutoConnect] Waiting %ds before first connect ...", CONNECT_DELAY_SEC)
        time.sleep(CONNECT_DELAY_SEC)
        while self._running:
            self._connect_all()
            time.sleep(RETRY_INTERVAL_SEC)

    def _connect_all(self):
        for peer in self._peers:
            name = peer.get("name", "?")
            pub  = peer.get("public_ip",  "").strip()
            priv = peer.get("private_ip", "").strip()

            # Skip if this is our own node
            if pub in self._own_ips or priv in self._own_ips:
                continue
            if not pub and not priv:
                continue

            key = pub or priv
            if self._failures.get(key, 0) >= MAX_RETRIES:
                continue

            ip = _best_ip(peer, self._p2p_port)
            if ip is None:
                self._failures[key] = self._failures.get(key, 0) + 1
                log.warning("[AutoConnect] %s — port %d unreachable (%d/%d)",
                            name, self._p2p_port,
                            self._failures[key], MAX_RETRIES)
                self._connected.discard(key)
                continue

            result = self.bridge.connect_to(ip)
            if result.startswith("✅"):
                if key not in self._connected:
                    log.info("[AutoConnect] Connected: %s via %s", name, ip)
                self._connected.add(key)
                self._failures[key] = 0
            else:
                self._failures[key] = self._failures.get(key, 0) + 1
                log.warning("[AutoConnect] %s — %d/%d: %s",
                            name, self._failures[key], MAX_RETRIES, result)
                self._connected.discard(key)

    def status(self) -> str:
        lines = ["🌐 PEER AUTOCONNECT STATUS:"]
        for peer in self._peers:
            pub  = peer.get("public_ip",  "")
            priv = peer.get("private_ip", "")
            name = peer.get("name", pub)
            key  = pub or priv
            if pub in self._own_ips or priv in self._own_ips:
                state = "🏠 THIS NODE"
            elif key in self._connected:
                state = "🟢 connected"
            elif self._failures.get(key, 0) >= MAX_RETRIES:
                state = f"🔴 gave up ({MAX_RETRIES} failures)"
            else:
                fails = self._failures.get(key, 0)
                state = f"🟡 retrying ({fails}/{MAX_RETRIES})" if fails else "⏳ pending"
            lines.append(f"  {name:22s} pub={pub:18s} priv={priv or 'n/a':14s} {state}")
        return "\n".join(lines)


def start_autoconnect(bridge) -> "PeerAutoConnect | None":
    peers = _load_peers()
    if not peers:
        return None
    ac = PeerAutoConnect(bridge)
    ac.start()
    return ac
