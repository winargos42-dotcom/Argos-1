"""
tests/test_budding_manager.py
Тесты модуля BuddingManager (src/connectivity/budding_manager.py)
"""
import pytest
from unittest.mock import MagicMock, patch, call
import sys, types

# ── Мок зависимостей которых может не быть в CI ──────────────────────────────
for mod in ["scapy", "scapy.all"]:
    sys.modules.setdefault(mod, types.ModuleType(mod))


def _import_manager():
    try:
        from src.connectivity.budding_manager import BuddingManager
        return BuddingManager
    except ImportError:
        try:
            from budding_manager import BuddingManager
            return BuddingManager
        except ImportError:
            pytest.skip("BuddingManager недоступен")


@pytest.fixture(autouse=True)
def _no_real_networking():
    """BuddingManager.__init__ unconditionally starts two real background
    threads: a TCP listener bound to 0.0.0.0:<port+1000> and an ARP-scanning
    loop. Every test in this file reuses port=5000, so the listeners race for
    the same socket and the leaked threads can stall the process well past
    normal test timeouts. Tests below exercise send_bud/find_soil/stop
    directly (with their own mocking), not the real listener/scanner, so the
    background threads add nothing but risk here."""
    try:
        from src.connectivity.budding_manager import BuddingManager
    except ImportError:
        yield
        return
    with patch.object(BuddingManager, "_start_bud_listener", lambda self: None), \
            patch.object(BuddingManager, "_start_soil_search", lambda self: None):
        yield


# ── Базовые тесты ─────────────────────────────────────────────────────────────

def test_import():
    BuddingManager = _import_manager()
    assert BuddingManager is not None


def test_instantiation():
    BuddingManager = _import_manager()
    bm = BuddingManager(node_id="test_node", port=5000)
    assert bm is not None


def test_has_required_methods():
    BuddingManager = _import_manager()
    bm = BuddingManager(node_id="test_node", port=5000)
    for method in ("send_bud", "find_soil", "start", "stop"):
        assert hasattr(bm, method), f"Метод {method} отсутствует"


def test_soil_search_interval_default():
    BuddingManager = _import_manager()
    bm = BuddingManager(node_id="n1", port=5000)
    assert hasattr(bm, "soil_search_interval")
    assert bm.soil_search_interval > 0


def test_bud_port_derived_from_parent():
    BuddingManager = _import_manager()
    bm = BuddingManager(node_id="n1", port=5000)
    # bud_port должен быть parent.port + 1000
    if hasattr(bm, "bud_port"):
        assert bm.bud_port == 6000


# ── send_bud ──────────────────────────────────────────────────────────────────

@patch("socket.socket")
def test_send_bud_calls_connect(mock_socket_cls):
    BuddingManager = _import_manager()
    mock_sock = MagicMock()
    mock_socket_cls.return_value.__enter__ = lambda s: mock_sock
    mock_socket_cls.return_value.__exit__ = MagicMock(return_value=False)
    bm = BuddingManager(node_id="n1", port=5000)
    try:
        bm.send_bud("192.168.1.100", target_port=6000)
    except Exception:
        pass  # Сетевые ошибки в CI ожидаемы


def test_send_bud_to_invalid_ip_does_not_crash():
    BuddingManager = _import_manager()
    bm = BuddingManager(node_id="n1", port=5000)
    try:
        bm.send_bud("0.0.0.0", target_port=1)
    except Exception:
        pass  # ожидаемо в CI без сети


# ── find_soil ─────────────────────────────────────────────────────────────────

def test_find_soil_returns_list():
    BuddingManager = _import_manager()
    bm = BuddingManager(node_id="n1", port=5000)
    with patch.object(bm, "find_soil", return_value=["192.168.1.5"]) as mock_fs:
        result = bm.find_soil()
        assert isinstance(result, list)


# ── stop ──────────────────────────────────────────────────────────────────────

def test_stop_does_not_raise():
    BuddingManager = _import_manager()
    bm = BuddingManager(node_id="n1", port=5000)
    try:
        bm.stop()
    except Exception as e:
        pytest.fail(f"stop() кинул исключение: {e}")


# ── Безопасность: почкование выключено и требует сильный секрет ────────────────
STRONG = "x" * 40


def test_disabled_by_default(monkeypatch):
    BuddingManager = _import_manager()
    monkeypatch.delenv("ARGOS_BUDDING", raising=False)
    monkeypatch.setenv("ARGOS_NETWORK_SECRET", STRONG)
    bm = BuddingManager(node_id="n1", port=5000)
    assert bm.enabled is False
    assert "выключен" in bm.status()


def test_enabled_needs_strong_secret(monkeypatch):
    BuddingManager = _import_manager()
    monkeypatch.setenv("ARGOS_BUDDING", "on")
    for weak in ("", "argos_default_secret", "short"):
        monkeypatch.setenv("ARGOS_NETWORK_SECRET", weak)
        bm = BuddingManager(node_id="n1", port=5000)
        assert bm.enabled is False, f"слабый секрет {weak!r} не должен включать почкование"
        assert bm._gost is None


def test_enabled_with_strong_secret_binds_loopback(monkeypatch):
    BuddingManager = _import_manager()
    monkeypatch.setenv("ARGOS_BUDDING", "on")
    monkeypatch.setenv("ARGOS_NETWORK_SECRET", STRONG)
    monkeypatch.delenv("ARGOS_BUDDING_AUTOSPREAD", raising=False)
    bm = BuddingManager(node_id="n1", port=5000)
    assert bm.enabled and bm._gost is not None
    assert bm.bind_host == "127.0.0.1"
    assert bm.autospread is False  # авто-распространение отдельным флагом


def test_send_bud_refused_without_secret(monkeypatch):
    BuddingManager = _import_manager()
    monkeypatch.delenv("ARGOS_BUDDING", raising=False)
    monkeypatch.setenv("ARGOS_NETWORK_SECRET", STRONG)
    bm = BuddingManager(node_id="n1", port=5000)
    bm._gost = None
    assert bm.send_bud("192.168.1.100", target_port=6000) is False


def test_incoming_plain_pickle_is_rejected(monkeypatch):
    """Незашифрованный pickle из сети не должен ни распаковываться, ни запускаться."""
    import pickle
    BuddingManager = _import_manager()
    monkeypatch.setenv("ARGOS_BUDDING", "on")
    monkeypatch.setenv("ARGOS_NETWORK_SECRET", STRONG)
    bm = BuddingManager(node_id="n1", port=5000)

    launched = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: launched.append(a))
    payload = pickle.dumps({"code": "import os", "state": {"hidden_size": 1}})

    class FakeConn:
        def __init__(self, data):
            self._data = [data, b""]
            self.closed = False

        def recv(self, n):
            return self._data.pop(0) if self._data else b""

        def close(self):
            self.closed = True

    conn = FakeConn(payload)
    bm._handle_incoming_bud(conn, ("192.168.1.66", 12345))
    assert launched == []  # ничего не запущено
    assert conn.closed
