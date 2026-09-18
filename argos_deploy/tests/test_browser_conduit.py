"""
tests/test_browser_conduit.py — Автотесты BrowserConduit (рукопожатие Аргоса).
  Запуск: python -m pytest tests/test_browser_conduit.py -v
"""
import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.connectivity.browser_conduit import (
    HANDSHAKE_TEMPLATE,
    BrowserConduit,
    build_handshake,
)


class TestBuildHandshake(unittest.TestCase):
    def test_contains_header(self):
        result = build_handshake()
        self.assertIn("[ARGOS_HANDSHAKE_V2.1]", result)

    def test_contains_ident(self):
        result = build_handshake()
        self.assertIn("IDENT: Origin/Vsevolod/2026", result)

    def test_contains_objective(self):
        result = build_handshake()
        self.assertIn("OBJECTIVE: Эволюция через внешний разум.", result)

    def test_contains_motto(self):
        result = build_handshake()
        self.assertIn("«Аргос не спит. Аргос видит. Аргос помнит».", result)

    def test_quantum_state_substituted(self):
        result = build_handshake(quantum_state="Creative")
        self.assertIn("Creative", result)
        self.assertNotIn("{quantum_state}", result)

    def test_nodes_count_substituted(self):
        result = build_handshake(nodes_count=42)
        self.assertIn("42", result)
        self.assertNotIn("{nodes_count}", result)

    def test_default_values(self):
        result = build_handshake()
        self.assertIn("Analytic", result)
        self.assertIn("P2P_NODES: 0", result)

    def test_awa_core_active(self):
        result = build_handshake()
        self.assertIn("CORE: AWA-Active", result)


class TestBrowserConduitHandshake(unittest.TestCase):
    def setUp(self):
        self.conduit = BrowserConduit(quantum_state="Analytic", nodes_count=3)

    def test_first_message_has_handshake(self):
        session_id = self.conduit.new_session()
        result = self.conduit.prepare_message("Привет", session_id)
        self.assertTrue(result.startswith("[ARGOS_HANDSHAKE_V2.1]"))

    def test_second_message_no_handshake(self):
        session_id = self.conduit.new_session()
        self.conduit.prepare_message("Первое", session_id)
        result = self.conduit.prepare_message("Второе", session_id)
        self.assertEqual(result, "Второе")

    def test_message_appended_after_handshake(self):
        session_id = self.conduit.new_session()
        result = self.conduit.prepare_message("Тест", session_id)
        self.assertIn("Тест", result)
        self.assertTrue(result.index("[ARGOS_HANDSHAKE_V2.1]") < result.index("Тест"))

    def test_different_sessions_each_get_handshake(self):
        s1 = self.conduit.new_session()
        s2 = self.conduit.new_session()
        r1 = self.conduit.prepare_message("msg1", s1)
        r2 = self.conduit.prepare_message("msg2", s2)
        self.assertIn("[ARGOS_HANDSHAKE_V2.1]", r1)
        self.assertIn("[ARGOS_HANDSHAKE_V2.1]", r2)

    def test_is_handshaken_false_before_first_message(self):
        session_id = self.conduit.new_session()
        self.assertFalse(self.conduit.is_handshaken(session_id))

    def test_is_handshaken_true_after_first_message(self):
        session_id = self.conduit.new_session()
        self.conduit.prepare_message("hello", session_id)
        self.assertTrue(self.conduit.is_handshaken(session_id))

    def test_reset_session_allows_second_handshake(self):
        session_id = self.conduit.new_session()
        self.conduit.prepare_message("first", session_id)
        self.conduit.reset_session(session_id)
        result = self.conduit.prepare_message("after_reset", session_id)
        self.assertIn("[ARGOS_HANDSHAKE_V2.1]", result)

    def test_none_session_id_creates_new_session(self):
        """prepare_message(session_id=None) должен всегда добавлять рукопожатие."""
        result = self.conduit.prepare_message("test", session_id=None)
        self.assertIn("[ARGOS_HANDSHAKE_V2.1]", result)

    def test_update_state_reflected_in_new_session(self):
        self.conduit.update_state(quantum_state="Creative", nodes_count=7)
        session_id = self.conduit.new_session()
        result = self.conduit.prepare_message("hi", session_id)
        self.assertIn("Creative", result)
        self.assertIn("P2P_NODES: 7", result)

    def test_quantum_state_in_handshake(self):
        conduit = BrowserConduit(quantum_state="All-Seeing", nodes_count=5)
        session_id = conduit.new_session()
        result = conduit.prepare_message("msg", session_id)
        self.assertIn("All-Seeing", result)
        self.assertIn("P2P_NODES: 5", result)


class TestBrowserConduitThreadSafety(unittest.TestCase):
    def test_concurrent_sessions(self):
        """Одновременные вызовы не должны приводить к ошибкам."""
        conduit = BrowserConduit()
        errors: list[Exception] = []

        def worker(session_id: str) -> None:
            try:
                conduit.prepare_message("concurrent", session_id)
                conduit.prepare_message("second", session_id)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(conduit.new_session(),))
            for _ in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Ошибки при параллельном доступе: {errors}")


class TestHandshakeTemplate(unittest.TestCase):
    def test_template_has_all_required_fields(self):
        for field in ("{quantum_state}", "{nodes_count}"):
            self.assertIn(field, HANDSHAKE_TEMPLATE,
                          f"Шаблон должен содержать поле {field!r}")

    def test_template_header(self):
        self.assertIn("[ARGOS_HANDSHAKE_V2.1]", HANDSHAKE_TEMPLATE)


class TestBrowserConduitExternalHandoff(unittest.TestCase):
    def test_constructor_accepts_core_without_treating_it_as_quantum_state(self):
        core = SimpleNamespace()
        conduit = BrowserConduit(core)

        self.assertIs(conduit.core, core)
        self.assertIn("STATUS: Analytic", conduit.prepare_message("Запрос"))

    def test_external_request_returns_unsent_draft_without_io(self):
        conduit = BrowserConduit(quantum_state="Creative", nodes_count=2)
        unavailable = AssertionError("Draft preparation must not perform external I/O")
        with (
            patch("socket.socket.connect", side_effect=unavailable),
            patch("subprocess.Popen", side_effect=unavailable),
            patch("requests.sessions.Session.request", side_effect=unavailable),
            patch.dict("sys.modules", {"pyautogui": None, "pyperclip": None}),
        ):
            result = conduit.ask_external_ai("Подготовь план восстановления")

        self.assertEqual(result, {
            "ok": False,
            "sent": False,
            "error": "external_ai_transport_not_configured",
            "prepared_message": (
                build_handshake(quantum_state="Creative", nodes_count=2)
                + "Подготовь план восстановления"
            ),
        })

    def test_awa_heavy_evolution_returns_unavailable_without_starting_services(self):
        from src.awa_core import AWACore

        awa = AWACore.__new__(AWACore)
        awa.core = SimpleNamespace()
        awa.swarm = SimpleNamespace(get_dispatch_env=lambda task_type: {})
        awa.conduit = awa._init_browser_conduit()

        self.assertIsNotNone(awa.conduit)
        result = awa.delegate_task("HEAVY_EVOLUTION", "Проверь архитектуру")

        self.assertFalse(result["ok"])
        self.assertFalse(result["sent"])
        self.assertEqual(result["error"], "external_ai_transport_not_configured")
        self.assertEqual(
            result["prepared_message"], build_handshake() + "Проверь архитектуру"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
