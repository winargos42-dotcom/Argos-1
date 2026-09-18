"""Local handshake and session formatting for browser handoffs."""

import threading
import uuid


HANDSHAKE_TEMPLATE = (
    "[ARGOS_HANDSHAKE_V2.1]\n"
    "STATUS: {quantum_state} | CORE: AWA-Active | P2P_NODES: {nodes_count}\n"
    "IDENT: Origin/Vsevolod/2026\n"
    "OBJECTIVE: Эволюция через внешний разум.\n"
    "«Аргос не спит. Аргос видит. Аргос помнит».\n"
)


def build_handshake(quantum_state: str = "Analytic", nodes_count: int = 0) -> str:
    return HANDSHAKE_TEMPLATE.format(
        quantum_state=quantum_state, nodes_count=nodes_count
    )


class BrowserConduit:
    def __init__(
        self, core=None, *, quantum_state: str = "Analytic", nodes_count: int = 0
    ):
        self.core = core
        self.quantum_state = quantum_state
        self.nodes_count = nodes_count
        self._handshaken_sessions: set[str] = set()
        self._lock = threading.Lock()

    def new_session(self) -> str:
        return uuid.uuid4().hex

    def prepare_message(self, message: str, session_id: str | None = None) -> str:
        with self._lock:
            if session_id is not None and session_id in self._handshaken_sessions:
                return message
            prepared = build_handshake(self.quantum_state, self.nodes_count) + message
            if session_id is not None:
                self._handshaken_sessions.add(session_id)
            return prepared

    def is_handshaken(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._handshaken_sessions

    def ask_external_ai(self, payload: object) -> dict[str, object]:
        """Prepare a local draft; no external transport is configured."""
        return {
            "ok": False,
            "sent": False,
            "error": "external_ai_transport_not_configured",
            "prepared_message": self.prepare_message(str(payload)),
        }

    def reset_session(self, session_id: str) -> None:
        with self._lock:
            self._handshaken_sessions.discard(session_id)

    def update_state(
        self, quantum_state: str | None = None, nodes_count: int | None = None
    ) -> None:
        with self._lock:
            if quantum_state is not None:
                self.quantum_state = quantum_state
            if nodes_count is not None:
                self.nodes_count = nodes_count
