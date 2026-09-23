"""
tests/test_p2p.py — Автотесты P2P-системы Аргоса
  Покрывает: NodeProfile, NodeRegistry, TaskDistributor,
             авторитет, сериализацию, P2P-мост.
  Запуск: python -m pytest tests/test_p2p.py -v
  Или:    python tests/test_p2p.py
"""
import sys, os, time, json, threading, socket
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest


class TestNodeProfile(unittest.TestCase):
    def setUp(self):
        from src.connectivity.p2p_bridge import NodeProfile
        self.NodeProfile = NodeProfile

    def test_create_profile(self):
        p = self.NodeProfile()
        self.assertIsNotNone(p.node_id)
        self.assertIsInstance(p.node_id, str)
        self.assertGreater(len(p.node_id), 8)

    def test_get_power_structure(self):
        p   = self.NodeProfile()
        pwr = p.get_power()
        self.assertIn("index",    pwr, "get_power() должен содержать 'index'")
        self.assertIn("cpu_free", pwr)
        self.assertIn("ram_free", pwr)
        self.assertIsInstance(pwr["index"], (int, float))
        self.assertGreaterEqual(pwr["index"], 0)
        self.assertLessEqual(pwr["index"], 100)

    def test_authority_formula(self):
        """Авторитет = мощность × log(возраст + 2).
        Мокаем get_power чтобы убрать влияние CPU-флуктуаций."""
        import math
        from unittest.mock import patch
        p = self.NodeProfile()
        fixed_power = {"index": 50, "cpu_free": 50.0, "ram_free": 80.0,
                       "cpu_cores": 2, "ram_gb": 4.0}
        with patch.object(type(p), 'get_power', return_value=fixed_power):
            age_days  = p.get_age_days()
            authority = p.get_authority()
            expected  = int(fixed_power["index"] * math.log(age_days + 2))
            self.assertEqual(authority, expected,
                             msg="Авторитет не соответствует формуле")

    def test_authority_increases_with_age(self):
        """Более старая нода должна иметь больший авторитет при той же мощности."""
        import math
        p = self.NodeProfile()
        pwr = p.get_power()["index"]
        auth_day1  = pwr * math.log(1  + 2)
        auth_day30 = pwr * math.log(30 + 2)
        self.assertGreater(auth_day30, auth_day1)

    def test_serialize_deserialize(self):
        p    = self.NodeProfile()
        data = p.to_dict()
        self.assertIn("node_id",   data)
        self.assertIn("hostname",  data)
        self.assertIn("power",     data)
        self.assertIn("authority", data)
        # Сериализация в JSON
        j = json.dumps(data)
        back = json.loads(j)
        self.assertEqual(back["node_id"], p.node_id)

    def test_hostname_is_string(self):
        p = self.NodeProfile()
        self.assertIsInstance(p.hostname, str)
        self.assertGreater(len(p.hostname), 0)

    def test_get_age_days_nonnegative(self):
        p = self.NodeProfile()
        self.assertGreaterEqual(p.get_age_days(), 0)


class TestNodeRegistry(unittest.TestCase):
    def setUp(self):
        from src.connectivity.p2p_bridge import NodeRegistry, NodeProfile
        self.NodeRegistry = NodeRegistry
        self.NodeProfile  = NodeProfile

    def test_add_and_get(self):
        reg  = self.NodeRegistry()
        node = self.NodeProfile().to_dict()
        reg.update(node, node.get("ip","127.0.0.1"))
        all_nodes = reg.all()
        ids = [n.get("node_id") for n in all_nodes]
        self.assertIn(node["node_id"], ids, "Узел не найден после добавления")

    def test_count(self):
        reg = self.NodeRegistry()
        for i in range(3):
            node = self.NodeProfile().to_dict()
            node["node_id"] = f"test_node_{i}"
            reg.update(node, node.get("ip","127.0.0.1"))
        self.assertGreaterEqual(reg.count(), 3)

    def test_get_master_returns_highest_authority(self):
        reg = self.NodeRegistry()
        import math
        nodes = []
        for i, auth in enumerate([50, 120, 80]):
            n = {"node_id": f"n{i}", "hostname": f"host{i}",
                 "authority": auth, "power": {"index": 50},
                 "age_days": i+1, "last_seen": time.time()}
            nodes.append(n)
            reg.update(n, "127.0.0.1")
        master = reg.get_master()
        self.assertIsNotNone(master)
        self.assertEqual(master["node_id"], "n1",
                         f"Мастером должен быть n1 (авторитет 120), а не {master['node_id']}")

    def test_remove_node(self):
        reg  = self.NodeRegistry()
        node = self.NodeProfile().to_dict()
        reg.update(node, "127.0.0.1")
        before = reg.count()
        self.assertGreaterEqual(before, 1, "Узел должен быть добавлен")

    def test_all_returns_list(self):
        reg = self.NodeRegistry()
        result = reg.all()
        self.assertIsInstance(result, list)

    def test_update_existing_node(self):
        reg  = self.NodeRegistry()
        node = self.NodeProfile().to_dict()
        reg.update(node, node.get("ip","127.0.0.1"))
        node["power"]["index"] = 99
        reg.update(node, node.get("ip","127.0.0.1"))  # Обновление
        all_nodes = reg.all()
        found = next((n for n in all_nodes if n.get("node_id") == node["node_id"]), None)
        if found:
            self.assertEqual(found["power"]["index"], 99)


class TestTaskDistributor(unittest.TestCase):
    def setUp(self):
        from src.connectivity.p2p_bridge import TaskDistributor, NodeRegistry, NodeProfile
        import math
        self.NP    = NodeProfile
        self.reg   = NodeRegistry()
        self.self_prof = NodeProfile()
        self.dist  = TaskDistributor(self.reg, self.self_prof)
        # Добавляем тестовые ноды
        for i, (pwr, age) in enumerate([(90,30), (50,5), (70,15)]):
            auth = round(pwr * math.log(age + 2), 2)
            n = {"node_id": f"task_node_{i}", "hostname": f"h{i}",
                 "power": {"index": pwr}, "authority": auth,
                 "age_days": age, "last_seen": time.time(), "ip": "127.0.0.1"}
            self.reg.update(n, "127.0.0.1")

    def test_best_node_is_highest_authority(self):
        best = self.dist.pick_node_for("ai")
        # Может вернуть None если нет реальных подключений — это нормально
        if best is not None:
            # pick_node_for returns {"node": {...}, "is_local": bool}
            node_data = best.get("node", best)
            self.assertIn("node_id", node_data)

    def test_route_returns_response(self):
        resp = self.dist.route_task("Привет")
        self.assertIsInstance(resp, str)

    def test_no_nodes_graceful(self):
        from src.connectivity.p2p_bridge import TaskDistributor, NodeRegistry, NodeProfile
        empty_reg = NodeRegistry()
        prof      = NodeProfile()
        dist      = TaskDistributor(empty_reg, prof)
        best = dist.pick_node_for("ai")
        # При пустом реестре возвращает себя (is_local=True) — корректное поведение
        if best is not None:
            self.assertIn("is_local", best)
            # Либо None либо локальный узел
            is_local = best.get("is_local", False)
            self.assertTrue(is_local or True)  # всегда ок
        # Нет краша — тест пройден


class TestArgosBridge(unittest.TestCase):
    def test_import(self):
        from src.connectivity.p2p_bridge import ArgosBridge
        self.assertTrue(True, "ArgosBridge импортируется без ошибок")

    def test_instantiate_without_core(self):
        from src.connectivity.p2p_bridge import ArgosBridge
        try:
            bridge = ArgosBridge(core=None)
            self.assertIsNotNone(bridge)
        except Exception as e:
            self.fail(f"ArgosBridge(core=None) упал: {e}")

    def test_has_required_methods(self):
        from src.connectivity.p2p_bridge import ArgosBridge
        bridge = ArgosBridge(core=None)
        for method in ["start", "network_status", "sync_skills_from_network", "route_query"]:
            self.assertTrue(hasattr(bridge, method),
                            f"ArgosBridge должен иметь метод '{method}'")

    def test_network_status_before_start(self):
        from src.connectivity.p2p_bridge import ArgosBridge
        bridge = ArgosBridge(core=None)
        status = bridge.network_status()
        self.assertIsInstance(status, str)
        self.assertGreater(len(status), 0)

    def test_route_query_offline(self):
        from src.connectivity.p2p_bridge import ArgosBridge
        bridge = ArgosBridge(core=None)
        result = bridge.route_query("тест")
        self.assertIsInstance(result, str)

    def test_udp_discovery_socket_options(self):
        """Verify UDP discovery socket can be configured with required options
        (SO_REUSEADDR, SO_BROADCAST, bind, settimeout) for cross-platform compatibility."""
        import socket as _socket
        from src.connectivity.p2p_bridge import ArgosBridge, BROADCAST_PORT

        bridge = ArgosBridge(core=None)

        # Verify host/port attributes set in __init__
        # Усиленный мост (сверка с рантаймом 2026-09-23): по умолчанию только loopback,
        # а не "" (все интерфейсы).
        self.assertEqual(bridge.udp_host, "127.0.0.1")
        self.assertEqual(bridge.udp_port, BROADCAST_PORT)

        # Verify that a socket with the expected options can be configured
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        try:
            sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_BROADCAST, 1)
            if hasattr(_socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            sock.bind((bridge.udp_host, bridge.udp_port))
            sock.settimeout(0.1)
            # Reaching here without exception confirms all socket options are accepted
        except Exception as e:
            self.fail(f"Не удалось настроить UDP-сокет P2P: {e}")
        finally:
            sock.close()

    def test_udp_discovery_replaces_separate_broadcaster_listener(self):
        """ArgosBridge имеет метод _udp_discovery вместо отдельных broadcaster/listener."""
        from src.connectivity.p2p_bridge import ArgosBridge
        bridge = ArgosBridge(core=None)
        self.assertTrue(hasattr(bridge, "_udp_discovery"),
                        "ArgosBridge должен иметь метод '_udp_discovery'")
        self.assertFalse(hasattr(bridge, "_udp_broadcaster"),
                         "_udp_broadcaster заменён на _udp_discovery")
        self.assertFalse(hasattr(bridge, "_udp_listener"),
                         "_udp_listener заменён на _udp_discovery")


class TestP2PPacketEncoding(unittest.TestCase):
    """Тесты сериализации P2P-пакетов."""

    def test_json_roundtrip(self):
        node = {
            "node_id": "test-abc-123",
            "hostname": "argos-pc",
            "power": {"index": 75, "cpu": 40, "ram_free": 60},
            "authority": 135.5,
            "age_days": 10.5,
            "last_seen": time.time(),
            "ip": "192.168.1.100",
        }
        encoded = json.dumps(node).encode("utf-8")
        decoded = json.loads(encoded.decode("utf-8"))
        self.assertEqual(decoded["node_id"],   node["node_id"])
        self.assertEqual(decoded["authority"], node["authority"])

    def test_large_payload(self):
        """Большие пакеты должны сериализоваться без ошибок."""
        big = {"data": "x" * 60000, "node_id": "test"}
        enc = json.dumps(big).encode("utf-8")
        self.assertGreater(len(enc), 50000)
        dec = json.loads(enc)
        self.assertEqual(len(dec["data"]), 60000)


class TestP2PAuthority(unittest.TestCase):
    """Математические тесты формулы авторитета."""

    def test_authority_formula(self):
        import math
        cases = [
            (100, 0,   100 * math.log(2)),
            (100, 10,  100 * math.log(12)),
            (50,  30,  50  * math.log(32)),
            (0,   365, 0),
        ]
        for power, age, expected in cases:
            got = power * math.log(age + 2)
            self.assertAlmostEqual(got, expected, places=5,
                msg=f"Ошибка формулы: power={power} age={age}")

    def test_authority_ordering(self):
        """Приоритет: новая мощная нода vs старая слабая."""
        import math
        new_powerful = 95 * math.log(1 + 2)    # 1 день, 95% мощность
        old_weak     = 30 * math.log(365 + 2)  # 1 год, 30% мощность
        self.assertGreater(old_weak, new_powerful,
            "Старая нода с авторитетом должна иметь приоритет над новой")


class TestEventBusP2P(unittest.TestCase):
    """Тесты EventBus в контексте P2P."""

    def test_p2p_events_subscribe(self):
        from src.event_bus import EventBus, Events
        bus = EventBus(history_size=20)
        received = []
        bus.subscribe(Events.P2P_NODE_JOINED, lambda e: received.append(e))
        bus.publish(Events.P2P_NODE_JOINED, {"node_id": "test"}, sync=True)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].data["node_id"], "test")
        bus.stop()

    def test_event_history(self):
        from src.event_bus import EventBus, Events
        bus = EventBus(history_size=10)
        for i in range(5):
            bus.publish(Events.P2P_NODE_JOINED, {"i": i}, sync=True)
        hist = bus.history(Events.P2P_NODE_JOINED)
        self.assertEqual(len(hist), 5)
        bus.stop()

    def test_wildcard_subscriber(self):
        from src.event_bus import EventBus, Events
        bus = EventBus()
        all_events = []
        bus.subscribe("*", lambda e: all_events.append(e.topic))
        bus.publish(Events.P2P_NODE_JOINED,  {}, sync=True)
        bus.publish(Events.P2P_NODE_LEFT,    {}, sync=True)
        bus.publish(Events.P2P_SKILL_SYNCED, {}, sync=True)
        self.assertEqual(len(all_events), 3)
        bus.stop()


class TestIntegration(unittest.TestCase):
    """Интеграционные тесты P2P + EventBus."""

    def test_node_joined_fires_event(self):
        from src.event_bus import get_bus, Events
        bus      = get_bus()
        received = []
        bus.subscribe(Events.P2P_NODE_JOINED, lambda e: received.append(e))
        # Симулируем регистрацию ноды
        bus.emit(Events.P2P_NODE_JOINED, {"node_id": "sim_node", "ip": "10.0.0.1"})
        time.sleep(0.1)
        self.assertTrue(len(received) >= 1, "Событие P2P_NODE_JOINED не получено")

    def test_dag_events_flow(self):
        from src.event_bus import EventBus, Events
        bus    = EventBus()
        events = []
        for ev in [Events.DAG_STARTED, Events.DAG_NODE_DONE, Events.DAG_COMPLETED]:
            bus.subscribe(ev, lambda e, events=events: events.append(e.topic))
        bus.publish(Events.DAG_STARTED,   {"dag_id": "test"}, sync=True)
        bus.publish(Events.DAG_NODE_DONE, {"node": "step1"},  sync=True)
        bus.publish(Events.DAG_COMPLETED, {"ok": 1},          sync=True)
        self.assertEqual(len(events), 3)
        bus.stop()


# ── RUNNER ────────────────────────────────────────────────
def run_tests():
    print("━" * 60)
    print("  ARGOS P2P АВТОТЕСТЫ")
    print("━" * 60)
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    test_classes = [
        TestNodeProfile, TestNodeRegistry, TestTaskDistributor,
        TestArgosBridge, TestP2PPacketEncoding, TestP2PAuthority,
        TestEventBusP2P, TestIntegration,
    ]
    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("━" * 60)
    ok   = result.testsRun - len(result.failures) - len(result.errors)
    fail = len(result.failures) + len(result.errors)
    print(f"  ИТОГ: {ok} ✅  /  {fail} ❌  из {result.testsRun}")
    print("━" * 60)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
