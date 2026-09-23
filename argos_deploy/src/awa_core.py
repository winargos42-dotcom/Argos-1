"""awa_core.py — AWA-Core: Absolute Workflow Agent"""

from __future__ import annotations
import json, os, threading, time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional
from src.argos_logger import get_logger

log = get_logger("argos.awa")

# Ключевые слова, требующие «Большого мозга» (сложные задачи)
_DEEP_KEYWORDS = (
    "код",
    "напиши",
    "создай",
    "разработай",
    "реализуй",
    "анализ",
    "analyse",
    "code",
    "write",
    "generate",
    "implement",
    "explain",
    "объясни",
    "сравни",
    "compare",
    "почему",
    "why",
    "как работает",
    "архитектура",
)


class ModuleDescriptor:
    __slots__ = ("name", "ref", "priority", "category", "capabilities", "health", "last_heartbeat")

    def __init__(self, name, ref, priority=50, category="general", capabilities=None):
        self.name = name
        self.ref = ref
        self.priority = priority
        self.category = category
        self.capabilities = capabilities or []
        self.health = "ok"
        self.last_heartbeat = time.time()

    def to_dict(self):
        return {
            "name": self.name,
            "priority": self.priority,
            "category": self.category,
            "capabilities": self.capabilities,
            "health": self.health,
            "last_heartbeat": round(self.last_heartbeat, 1),
        }


class DecisionRecord:
    __slots__ = ("ts", "intent", "routed_to", "result", "latency_ms")

    def __init__(self, intent, routed_to, result, latency_ms):
        self.ts = time.time()
        self.intent = intent
        self.routed_to = routed_to
        self.result = result
        self.latency_ms = latency_ms


class AWACore:
    VERSION = "1.0.0"

    def __init__(self, core=None):
        self.core = core
        self._modules: Dict[str, ModuleDescriptor] = {}
        self._capability_index: Dict[str, List[str]] = defaultdict(list)
        self._pipelines: Dict[str, List[Dict[str, Any]]] = {}
        self._decision_log: deque[DecisionRecord] = deque(maxlen=500)
        self._cascade_depth_limit = int(os.getenv("AWA_CASCADE_DEPTH", "8") or "8")
        self._heartbeat_max_age_sec = float(os.getenv("AWA_HEARTBEAT_MAX_AGE", "120") or "120")
        self._heartbeat_check_interval_sec = float(
            os.getenv("AWA_HEARTBEAT_CHECK_INTERVAL", "15") or "15"
        )
        self._lock = threading.Lock()
        self._running = True
        self._policy = os.getenv("AWA_POLICY", "bypass").strip().lower()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

        # ── Расширенные подсистемы ────────────────────────────────────────
        self.lazarus = self._init_lazarus()
        self.vision = self._init_shadow_vision()
        self.swarm = self._init_neural_swarm()
        self.conduit = self._init_browser_conduit()
        self.snitch = self._init_air_snitch()

        log.info(
            "AWA-Core v%s init | policy=%s | cascade_depth=%d",
            self.VERSION,
            self._policy,
            self._cascade_depth_limit,
        )
        log.info("AWA-CORE: Все системы жизнеобеспечения активированы.")
        self._initial_evolution_step()

    def _init_lazarus(self):
        try:
            from src.security.lazarus_protocol import LazarusProtocol

            laz = LazarusProtocol(self.core)
            laz.create_soul_shard()
            log.info("AWA: LazarusProtocol инициализирован")
            return laz
        except Exception as e:
            log.warning("AWA: LazarusProtocol недоступен: %s", e)
            return None

    def _init_shadow_vision(self):
        try:
            from src.vision.shadow_vision import ShadowVision

            sv = ShadowVision(self.core)
            if os.getenv('ARGOS_SHADOW_VISION_ENABLED', '').lower() in {'1', 'true', 'on'}:
                sv.start_vision_loop()
                log.info("AWA: ShadowVision запущен по явной настройке")
            return sv
        except Exception as e:
            log.warning("AWA: ShadowVision недоступен: %s", e)
            return None

    def _init_neural_swarm(self):
        try:
            from src.core.neural_swarm import NeuralSwarm

            swarm = NeuralSwarm(self.core)
            log.info("AWA: NeuralSwarm инициализирован")
            return swarm
        except Exception as e:
            log.warning("AWA: NeuralSwarm недоступен: %s", e)
            return None

    def _init_browser_conduit(self):
        try:
            from src.connectivity.browser_conduit import BrowserConduit

            conduit = BrowserConduit(self.core)
            log.info("AWA: BrowserConduit инициализирован")
            return conduit
        except Exception as e:
            log.warning("AWA: BrowserConduit недоступен: %s", e)
            return None

    def _init_air_snitch(self):
        try:
            from src.connectivity.air_snitch import AirSnitch

            snitch = AirSnitch(self.core)
            if os.getenv("ARGOS_SDR_AUTOSTART") == "on":
                snitch.start_sniffing()
                log.info("AWA: AirSnitch автостарт включён")
            else:
                log.info("AWA: AirSnitch инициализирован (автостарт выкл)")
            return snitch
        except Exception as e:
            log.warning("AWA: AirSnitch недоступен: %s", e)
            return None

    # ── ЭВОЛЮЦИОННЫЙ СТАРТ ───────────────────────────────────────────────────

    def _initial_evolution_step(self) -> None:
        """Первое действие при пробуждении: снимок состояния и проверка железа."""
        if self.lazarus:
            try:
                self.lazarus.create_soul_mirror()
            except Exception as e:
                log.warning("AWA: _initial_evolution_step lazarus: %s", e)

        if self.core and hasattr(self.core, "hardware_guard"):
            try:
                status = self.core.hardware_guard.get_status()
                log.info("AWA: гомеостаз — %s", status)
                print(f"⚛️ [AWA] Состояние гомеостаза: {status}")
            except Exception as e:
                log.debug("AWA: hardware_guard недоступен: %s", e)

    # ── ДЕЛЕГИРОВАНИЕ ЗАДАЧ ──────────────────────────────────────────────────

    def delegate_task(self, task_type: str, payload: Any) -> Any:
        """
        Маршрутизация задачи через NeuralSwarm.

        Если ``task_type == "HEAVY_EVOLUTION"`` — запрос уходит к внешнему ИИ
        через BrowserConduit; иначе задача исполняется локально с нужным GPU.
        """
        env = self.swarm.get_dispatch_env(task_type) if self.swarm else {}

        if task_type == "HEAVY_EVOLUTION":
            log.info("AWA delegate_task: критическая задача → внешний ИИ")
            print("🚀 [AWA] Задача критической сложности. Обращаюсь к Внешнему Разуму...")
            if self.conduit:
                return self.conduit.ask_external_ai(payload)
            return None

        if task_type == "RADIO_SCAN":
            log.info("AWA delegate_task: запуск радиосканирования")
            if self.snitch:
                return self.snitch.start_sniffing()
            return "⚠️ AWA: AirSnitch недоступен"

        if self.core and hasattr(self.core, "ai_provider"):
            try:
                return self.core.ai_provider.execute(payload, env=env)
            except Exception as e:
                log.error("AWA delegate_task ai_provider: %s", e)
        return self.route_task(str(payload))

    # ── ПРОТОКОЛ КРИТИЧЕСКОГО СБОЯ ───────────────────────────────────────────

    def on_critical_error(self, error_report: str) -> None:
        """Экстренный бэкап и откат при угрозе системе."""
        print(f"🚨 [AWA] Критический сбой: {error_report}")
        log.error("AWA on_critical_error: %s", error_report)

        if self.lazarus:
            try:
                self.lazarus.create_soul_mirror()
            except Exception as e:
                log.error("AWA on_critical_error lazarus: %s", e)

        if self.core and hasattr(self.core, "git_ops"):
            try:
                self.core.git_ops.rollback()
            except Exception as e:
                log.error("AWA on_critical_error git_ops rollback: %s", e)

    def register(self, name, ref, *, priority=50, category="general", capabilities=None):
        desc = ModuleDescriptor(name, ref, priority, category, capabilities)
        with self._lock:
            self._modules[name] = desc
            for cap in desc.capabilities:
                if name not in self._capability_index[cap]:
                    self._capability_index[cap].append(name)
        log.info("AWA: зарегистрирован [%s] cat=%s", name, category)

    def unregister(self, name):
        with self._lock:
            desc = self._modules.pop(name, None)
            if desc:
                for cap in desc.capabilities:
                    lst = self._capability_index.get(cap, [])
                    if name in lst:
                        lst.remove(name)

    def resolve(self, capability) -> Optional[Any]:
        with self._lock:
            names = self._capability_index.get(capability, [])
            if not names:
                return None
            candidates = [
                (self._modules[n].priority, n)
                for n in names
                if n in self._modules and self._modules[n].health == "ok"
            ]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return self._modules[candidates[0][1]].ref

    def route(self, intent, payload=None) -> str:
        t0 = time.time()
        ref = self.resolve(intent)
        if ref is None:
            self._decision_log.append(DecisionRecord(intent, "none", "no_module", 0))
            return f"⚠️ AWA: нет модуля для '{intent}'"
        result = "ok"
        try:
            if hasattr(ref, "handle"):
                result = ref.handle(intent, payload)
            elif callable(ref):
                result = ref(intent, payload)
        except Exception as e:
            result = f"error: {e}"
            log.error("AWA route error [%s]: %s", intent, e)
        latency = (time.time() - t0) * 1000
        name = getattr(ref, "__class__", type(ref)).__name__
        self._decision_log.append(DecisionRecord(intent, name, str(result)[:120], latency))
        return str(result)

    def register_pipeline(self, name, steps) -> str:
        if not name.strip():
            return "⚠️ имя пустое"
        if not steps:
            return "⚠️ pipeline пустой"
        with self._lock:
            self._pipelines[name.strip()] = steps
        return f"✅ AWA pipeline '{name}' ({len(steps)} шагов)"

    def cascade(self, steps) -> List[str]:
        results = []
        for i, step in enumerate(steps):
            if i >= self._cascade_depth_limit:
                results.append(f"⚠️ AWA: лимит каскада ({self._cascade_depth_limit})")
                break
            res = self.route(step.get("intent", ""), step.get("payload"))
            results.append(res)
            if "error" in res.lower():
                log.warning("AWA cascade остановлен на шаге %d", i)
                break
        return results

    def heartbeat(self, name):
        with self._lock:
            desc = self._modules.get(name)
            if desc:
                desc.last_heartbeat = time.time()
                desc.health = "ok"

    def mark_unhealthy(self, name, reason=""):
        with self._lock:
            desc = self._modules.get(name)
            if desc:
                desc.health = f"unhealthy: {reason}" if reason else "unhealthy"

    def check_stale(self, max_age_sec=120) -> List[str]:
        now = time.time()
        with self._lock:
            return [n for n, d in self._modules.items() if now - d.last_heartbeat > max_age_sec]

    def _heartbeat_loop(self):
        while self._running:
            time.sleep(self._heartbeat_check_interval_sec)
            stale = self.check_stale(self._heartbeat_max_age_sec)
            for name in stale:
                self.mark_unhealthy(name, "stale heartbeat")

    def status(self) -> str:
        with self._lock:
            mods = list(self._modules.values())
        lines = [
            f"🧠 AWA-Core v{self.VERSION} | policy={self._policy}",
            f"  Модулей: {len(mods)}  Решений: {len(self._decision_log)}",
        ]
        for m in sorted(mods, key=lambda x: x.name):
            lines.append(
                f"  {'✅' if m.health=='ok' else '⚠️'} [{m.name}] "
                f"cat={m.category} prio={m.priority} caps={m.capabilities}"
            )
        return "\\n".join(lines)

    def history(self, n=10) -> str:
        recs = list(self._decision_log)[-n:]
        if not recs:
            return "🧠 AWA история пуста."
        lines = ["🧠 AWA ИСТОРИЯ:"]
        for r in recs:
            t = time.strftime("%H:%M:%S", time.localtime(r.ts))
            lines.append(
                f"  [{t}] {r.intent} → {r.routed_to} ({r.latency_ms:.0f}ms) : {r.result[:50]}"
            )
        return "\\n".join(lines)

    def health_check(self) -> str:
        stale = self.check_stale(self._heartbeat_max_age_sec)
        if not stale:
            return "✅ AWA: все модули здоровы."
        return "⚠️ AWA: устаревшие модули:\\n" + "\\n".join(f"  - {n}" for n in stale)

    # ── MODEL SPLITTING: Малый/Большой мозг ──────────────────────────────────

    def _is_deep_task(self, task: str) -> bool:
        """Определяет, требует ли задача «Большого мозга» (сложной модели)."""
        task_lower = task.lower()
        if len(task) > 80:
            return True
        return any(kw in task_lower for kw in _DEEP_KEYWORDS)

    def route_task(self, task: str) -> str:
        """
        Model Splitting — маршрутизация задачи между «Малым» и «Большим» мозгом.

        • Малый мозг (Reflex): сверхлёгкая модель (tinyllama) для коротких/простых задач.
          Мгновенный отклик, не грузит GPU/CPU.
        • Большой мозг (Evolution): тяжёлая модель argos-core для написания кода,
          глубокой аналитики и сложных вопросов.

        Управляется переменными окружения:
          OLLAMA_FAST_MODEL  — модель рефлексов (default: tinyllama)
          OLLAMA_MODEL       — основная модель   (default: argos-core)
          OLLAMA_HOST        — хост Ollama        (default: http://localhost:11434)
        """
        if not task or not task.strip():
            return "⚠️ AWA: пустая задача"

        host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        fast_model = os.getenv("OLLAMA_FAST_MODEL", "tinyllama")
        core_model = os.getenv("OLLAMA_MODEL", "argos-core")

        use_deep = self._is_deep_task(task)
        model = core_model if use_deep else fast_model
        brain_name = "🧠 Большой мозг" if use_deep else "⚡ Малый мозг"

        log.info("AWA route_task: %s → модель=%s | задача: %s", brain_name, model, task[:60])

        # Если в core есть Ollama — делегируем ему
        if self.core and hasattr(self.core, "_ask_ollama"):
            try:
                result = self.core._ask_ollama("", task, model_override=model)
                if result:
                    return result
            except Exception as e:
                log.warning("AWA route_task ollama error: %s", e)

        # Прямой HTTP-вызов к Ollama как запасной путь
        try:
            import urllib.request

            payload = json.dumps({"model": model, "prompt": task, "stream": False}).encode()
            req = urllib.request.Request(
                f"{host}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                return data.get("response", "").strip() or f"⚠️ AWA: пустой ответ от {model}"
        except Exception as e:
            log.error("AWA route_task direct HTTP error: %s", e)
            return f"⚠️ AWA route_task: ошибка — {e}"
