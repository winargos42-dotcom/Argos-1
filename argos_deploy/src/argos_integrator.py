"""
argos_integrator.py — Унифицированный интегратор всех подсистем ARGOS

Авто-обнаруживает и подключает:
  • Security (security/*) → защита, шифрование, root
  • Connectivity (connectivity/*) → мосты, P2P, IoT, протоколы
  • Factory (factory/*) → прошивки, репликация
  • Knowledge (knowledge/*) → векторное хранилище, Grist
  • Mind (mind/*) → сознание, эволюция, сновидения
  • Quantum (quantum/*) → квантовые вычисления
  • Vision (vision/*) → компьютерное зрение
  • Skills (skills/*) → навыки через SkillLoader
  • Modules (modules/*) → модули через ModuleLoader
  • Claude Templates (claude-code-templates/*) → агенты, команды, MCP

Использование:
  from src.argos_integrator import ArgosIntegrator
  integrator = ArgosIntegrator(core)
  integrator.integrate_all()
"""

import os
import sys
import importlib
import pkgutil
import inspect
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass, field

from src.argos_logger import get_logger
from src.event_bus import get_bus, Events

log = get_logger("argos.integrator")
bus = get_bus()


@dataclass
class IntegrationResult:
    """Результат интеграции подсистемы."""
    name: str
    loaded: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    
    @property
    def ok(self) -> bool:
        return len(self.loaded) > 0 or len(self.failed) == 0
    
    def __str__(self) -> str:
        status = "✅" if self.ok else "❌"
        lines = [f"{status} {self.name}: {len(self.loaded)} loaded"]
        if self.failed:
            lines.append(f"   ⚠ {len(self.failed)} failed")
        return "\n".join(lines)


class ArgosIntegrator:
    """
    Центральный интегратор ARGOS.
    Подключает все подсистемы к ядру через единый интерфейс.
    """
    
    # Карта подсистем: package -> (klass_name, setup_method)
    SUBSYSTEMS = {
        "security": (None, None),  # Авто-обнаружение классов
        "connectivity": ("ArgosBridge", None),  # Основной P2P мост
        "factory": ("Replicator", "setup"),
        "knowledge": ("VectorStore", None),
        "mind": ("Dreamer", "setup"),
        "vision": ("ShadowVision", None),
    }
    
    def __init__(self, core=None):
        self.core = core
        self._registry: Dict[str, Any] = {}
        self._results: List[IntegrationResult] = []
        self._allow_stubs = os.getenv("ARGOS_ALLOW_STUBS", "1").lower() in {"1", "true", "on"}

    def _stub(self, cls_name: str, reason: str) -> Any:
        """Создаёт минимальный stub-объект вместо падения интеграции."""
        log.warning("Stub for %s: %s", cls_name, reason)
        return SimpleNamespace(__name__=cls_name, stub=True, reason=reason)

    def _instantiate_or_stub(self, cls, key: str, *args, **kwargs):
        try:
            return cls(*args, **kwargs)
        except Exception as e:
            if self._allow_stubs:
                return self._stub(cls.__name__, str(e))
            raise
        
    def integrate_all(self) -> Dict[str, Any]:
        """
        Запускает полную интеграцию всех подсистем.
        Возвращает реестр подключенных компонентов.
        """
        log.info("╔" + "═" * 58 + "╗")
        log.info("║" + " ARGOS UNIVERSAL INTEGRATOR v3.0 ".center(58) + "║")
        log.info("╚" + "═" * 58 + "╝")
        
        # 1. Security (первая, критична)
        self._integrate_security()
        
        # 2. Connectivity (P2P, мосты, IoT)
        self._integrate_connectivity()
        
        # 3. Knowledge (хранилища)
        self._integrate_knowledge()
        
        # 4. Factory (прошивки)
        self._integrate_factory()
        
        # 5. Mind (сознание)
        self._integrate_mind()
        
        # 6. Quantum (квантовые модули)
        self._integrate_quantum()
        
        # 7. Vision (зрение)
        self._integrate_vision()
        
        # 8. Skills (через SkillLoader)
        self._integrate_skills()
        
        # 9. Modules (через ModuleLoader)
        self._integrate_modules()
        
        # 10. Interface компоненты
        self._integrate_interfaces()
        
        # 11. Claude Templates (агенты, команды, MCP)
        self._integrate_claude_templates()
        
        # Отчёт
        self._print_summary()
        
        return self._registry
    
    def _safe_import(self, module_path: str, class_name: Optional[str] = None):
        """Безопасный импорт с подробным логированием."""
        try:
            mod = importlib.import_module(module_path)
            if class_name:
                cls = getattr(mod, class_name, None)
                if cls is None:
                    # Ищем по похожим именам
                    candidates = [name for name in dir(mod) 
                                 if not name.startswith('_') and inspect.isclass(getattr(mod, name))]
                    if candidates:
                        cls = getattr(mod, candidates[0])
                        log.debug(f"Auto-selected {candidates[0]} from {module_path}")
                return cls
            return mod
        except Exception as e:
            log.debug(f"Import failed {module_path}: {e}")
            return None
    
    def _auto_discover_classes(self, package: str, suffixes: tuple = ()) -> List[type]:
        """Авто-обнаружение классов в пакете."""
        classes = []
        try:
            pkg = importlib.import_module(package)
            for _, name, _ in pkgutil.iter_modules(pkg.__path__):
                if name.startswith('_'):
                    continue
                if suffixes and not name.endswith(suffixes):
                    continue
                full_path = f"{package}.{name}"
                mod = self._safe_import(full_path)
                if mod:
                    for obj_name in dir(mod):
                        obj = getattr(mod, obj_name)
                        if inspect.isclass(obj) and obj.__module__ == full_path:
                            classes.append(obj)
        except Exception as e:
            log.warning(f"Auto-discover failed for {package}: {e}")
        return classes
    
    def _integrate_security(self) -> IntegrationResult:
        """Интеграция модуля безопасности."""
        result = IntegrationResult("security")
        
        components = [
            ("src.security.encryption", "ArgosShield"),
            ("src.security.git_guard", "GitGuard"),
            ("src.security.bootloader_manager", "BootloaderManager"),
            ("src.security.lazarus_protocol", "LazarusProtocol"),
        ]
        # Опциональные тяжёлые/ключевые модули включаем только при явном разрешении,
        # чтобы не валить интеграцию в продуктиве, где нет ключей/секретов.
        if os.getenv("ARGOS_ENABLE_GOST", "").lower() in {"1", "true", "on"}:
            components.append(("src.security.gost_cipher", "GOSTCipher"))
        if os.getenv("ARGOS_ENABLE_ZKP", "").lower() in {"1", "true", "on"}:
            components.append(("src.security.zkp", "ZKPManager"))
        
        for module, cls_name in components:
            cls = self._safe_import(module, cls_name)
            if cls:
                try:
                    instance = self._instantiate_or_stub(cls, cls_name)
                    key = cls_name.lower().replace("manager", "").replace("argos", "")
                    self._registry[f"security.{key}"] = instance
                    result.loaded.append(cls_name)
                    
                    # Публикуем событие
                    bus.publish(Events.COMPONENT_LOADED, {
                        "type": "security",
                        "name": cls_name,
                        "instance": instance
                    })
                except Exception as e:
                    result.failed.append(f"{cls_name}: {e}")
                    
        self._results.append(result)
        log.info(str(result))
        return result
    
    def _integrate_connectivity(self) -> IntegrationResult:
        """Интеграция connectivity (P2P, мосты, IoT)."""
        result = IntegrationResult("connectivity")
        
        # Основные мосты
        bridges = [
            ("src.connectivity.p2p_bridge", "ArgosBridge", "p2p"),
            ("src.connectivity.telegram_bot", "ArgosTelegram", "telegram"),
            ("src.connectivity.whatsapp_bridge", "WhatsAppBridge", "whatsapp"),
            ("src.connectivity.slack_bridge", "SlackBridge", "slack"),
            ("src.connectivity.email_bridge", "EmailBridge", "email"),
            ("src.connectivity.websocket_bridge", "WebSocketBridge", "websocket"),
            ("src.connectivity.orangepi_bridge", "OrangePiBridge", "orangepi"),
            ("src.connectivity.max_bridge", "MaxBridge", "max"),
            ("src.connectivity.xen_argo_transport", "XenArgoTransport", "xen"),
        ]
        if os.getenv("ARGOS_ENABLE_IOT", "").lower() in {"1", "true", "on"}:
            bridges.extend(
                [
                    ("src.connectivity.iot_bridge", "IoTBridge", "iot"),
                    ("src.connectivity.mesh_network", "MeshNetwork", "mesh"),
                    ("src.connectivity.gost_p2p", "GOSTP2P", "gost_p2p"),
                    ("src.connectivity.home_assistant", "HomeAssistantBridge", "ha"),
                    ("src.connectivity.sensor_bridge", "ArgosSensorBridge", "sensor"),
                ]
            )
        
        for module, cls_name, key in bridges:
            cls = self._safe_import(module, cls_name)
            if cls:
                try:
                    if self.core is None and key in {"telegram", "xen", "p2p"}:
                        # Пропускаем мосты, требующие core/admin, при автономном прогоне интегратора
                        result.failed.append(f"{key}: skipped (no core)")
                        continue
                    instance = (
                        self._instantiate_or_stub(cls, key, self.core)
                        if self.core
                        else self._instantiate_or_stub(cls, key)
                    )
                    self._registry[f"connectivity.{key}"] = instance
                    result.loaded.append(key)

                    if hasattr(instance, "handle_event"):
                        bus.subscribe(Events.MESSAGE_RECEIVED, instance.handle_event)
                except Exception as e:
                    result.failed.append(f"{key}: {e}")
        
        # Industrial protocols
        protocols = [
            ("src.connectivity.protocols.modbus_bridge", "ModbusBridge", "modbus"),
            ("src.connectivity.protocols.ble_bridge", "BLEBridge", "ble"),
            ("src.connectivity.protocols.lora_bridge", "LoRaBridge", "lora"),
            ("src.connectivity.protocols.zigbee_bridge", "ZigbeeBridge", "zigbee"),
            ("src.connectivity.protocols.nfc_bridge", "NFCBridge", "nfc"),
        ]
        
        for module, cls_name, key in protocols:
            cls = self._safe_import(module, cls_name)
            if cls:
                try:
                    instance = cls()
                    self._registry[f"protocol.{key}"] = instance
                    result.loaded.append(f"proto:{key}")
                except Exception as e:
                    result.failed.append(f"{key}: {e}")
        
        self._results.append(result)
        log.info(str(result))
        return result
    
    def _integrate_knowledge(self) -> IntegrationResult:
        """Интеграция knowledge (векторные хранилища)."""
        result = IntegrationResult("knowledge")
        
        components = [
            ("src.knowledge.vector_store", "VectorStore", "vector"),
            ("src.knowledge.grist_storage", "GristStorage", "grist"),
        ]
        grist_disabled = os.getenv("ARGOS_DISABLE_GRIST", "").lower() in {"1", "true", "on"}
        if not grist_disabled and os.getenv("GRIST_API_KEY") and (
            os.getenv("GRIST_DOC_ID") or os.getenv("GIST_ID")
        ):
            components.append(("src.knowledge.grist_git_sync", "GristGitSync", "grist_sync"))
        
        for module, cls_name, key in components:
            cls = self._safe_import(module, cls_name)
            if cls:
                try:
                    instance = self._instantiate_or_stub(cls, key)
                    self._registry[f"knowledge.{key}"] = instance
                    result.loaded.append(key)
                except Exception as e:
                    result.failed.append(f"{key}: {e}")
                    
        self._results.append(result)
        log.info(str(result))
        return result
    
    def _integrate_factory(self) -> IntegrationResult:
        """Интеграция factory (прошивки, репликация)."""
        result = IntegrationResult("factory")
        
        components = [
            ("src.factory.flasher", "AirFlasher", "flasher"),
            ("src.factory.replicator", "Replicator", "replicator"),
            ("src.factory.firmware_tools", "FirmwareTools", "firmware"),
        ]
        
        for module, cls_name, key in components:
            cls = self._safe_import(module, cls_name)
            if cls:
                try:
                    instance = cls()
                    self._registry[f"factory.{key}"] = instance
                    result.loaded.append(key)
                    
                    # Если у ядра есть flasher - привязываем
                    if self.core and key == "flasher":
                        self.core.flasher = instance
                except Exception as e:
                    result.failed.append(f"{key}: {e}")
                    
        self._results.append(result)
        log.info(str(result))
        return result
    
    def _integrate_mind(self) -> IntegrationResult:
        """Интеграция mind (сознание, сновидения, эволюция)."""
        result = IntegrationResult("mind")
        
        components = [
            ("src.mind.dreamer", "Dreamer", "dreamer"),
            ("src.mind.evolution_engine", "EvolutionEngine", "evolution"),
            ("src.mind.self_model_v2", "SelfModelV2", "self_model"),
        ]

        for module, cls_name, key in components:
            cls = self._safe_import(module, cls_name)
            if cls:
                try:
                    if self.core is None:
                        if self._allow_stubs:
                            instance = self._stub(cls_name, "no core")
                        else:
                            result.failed.append(f"{key}: skipped (no core)")
                            continue
                    else:
                        instance = self._instantiate_or_stub(cls, key, self.core)
                    self._registry[f"mind.{key}"] = instance
                    result.loaded.append(key)
                    
                    # Привязываем к ядру, если есть атрибут
                    if self.core:
                        if key == "dreamer" and hasattr(self.core, 'dreamer'):
                            self.core.dreamer = instance
                        if key == "evolution" and hasattr(self.core, 'evolution'):
                            self.core.evolution = instance
                        if key == "self_model" and hasattr(self.core, 'self_model'):
                            self.core.self_model = instance
                except Exception as e:
                    result.failed.append(f"{key}: {e}")
                    
        self._results.append(result)
        log.info(str(result))
        return result
    
    def _integrate_quantum(self) -> IntegrationResult:
        """Интеграция quantum (квантовые вычисления)."""
        result = IntegrationResult("quantum")
        
        components = [
            ("src.quantum.ibm_bridge", "IBMCredentialManager", "ibm"),
            ("src.quantum.watson_bridge", "WatsonBridge", "watson"),
            ("src.quantum.logic", "ArgosQuantum", "logic"),
            ("src.quantum.oracle", "QuantumOracle", "oracle"),
        ]
        
        for module, cls_name, key in components:
            cls = self._safe_import(module, cls_name)
            if cls:
                try:
                    instance = cls()
                    self._registry[f"quantum.{key}"] = instance
                    result.loaded.append(key)
                    
                    if self.core and key == "logic":
                        self.core.quantum = instance
                except Exception as e:
                    result.failed.append(f"{key}: {e}")
                    
        self._results.append(result)
        log.info(str(result))
        return result
    
    def _integrate_vision(self) -> IntegrationResult:
        """Интеграция vision (компьютерное зрение)."""
        result = IntegrationResult("vision")
        
        from src.vision import ArgosVision, ShadowVision
        try:
            current = getattr(self.core, "vision", None) if self.core else None
            vision = current if current is not None else ArgosVision()
            self._registry["vision.vision"] = vision
            self._registry["vision.shadow"] = ShadowVision(self.core)
            if self.core is not None and current is None:
                self.core.vision = vision
            result.loaded.extend(["vision", "shadow"])
        except Exception as error:
            result.failed.append("vision: " + type(error).__name__)

        self._results.append(result)
        log.info(str(result))
        return result
    
    def _integrate_skills(self) -> IntegrationResult:
        """Интеграция через SkillLoader."""
        result = IntegrationResult("skills")
        
        try:
            from src.skill_loader import SkillLoader
            loader = SkillLoader(core=self.core)
            skills = loader.discover()
            
            for skill_name in skills:
                try:
                    loaded = loader.load(skill_name, core=self.core)  # Pass core!
                    if loaded:
                        self._registry[f"skill.{skill_name}"] = loaded
                        result.loaded.append(skill_name)
                except Exception as e:
                    result.failed.append(f"{skill_name}: {e}")
                    
            self._skill_loader = loader
        except Exception as e:
            result.errors.append(str(e))
            
        self._results.append(result)
        log.info(str(result))
        return result
    
    def _integrate_modules(self) -> IntegrationResult:
        """Интеграция через ModuleLoader."""
        result = IntegrationResult("modules")
        
        try:
            from src.modules.module_loader import ModuleLoader
            loader = ModuleLoader()
            report = loader.load_all(core=self.core)
            
            for mod_id, instance in loader.modules.items():
                self._registry[f"module.{mod_id}"] = instance
                result.loaded.append(mod_id)
                
            self._module_loader = loader
        except Exception as e:
            result.errors.append(str(e))
            
        self._results.append(result)
        log.info(str(result))
        return result
    
    def _integrate_interfaces(self) -> IntegrationResult:
        """Интеграция UI интерфейсов."""
        result = IntegrationResult("interfaces")

        is_android = bool(
            os.getenv("ANDROID_ROOT")
            or os.getenv("ANDROID_ARGUMENT")
            or os.path.exists("/system/build.prop")
        )
        force_mobile_ui = os.getenv("ARGOS_ENABLE_MOBILE_UI", "").strip().lower() in {
            "1", "true", "on", "yes"
        }

        interfaces = [("src.interface.gui", "ArgosGUI", "gui")]
        if is_android or force_mobile_ui:
            interfaces.append(("src.interface.mobile_ui", "ArgosMobileUI", "mobile"))
        interfaces.extend(
            [
                ("src.interface.web_engine", "ArgosWebEngine", "web"),
                ("src.interface.fastapi_dashboard", "FastAPIDashboard", "fastapi"),
                ("src.interface.argos_shell", "ArgosShell", "shell"),
            ]
        )
        enable_streamlit = os.getenv("ARGOS_ENABLE_STREAMLIT_INTERFACE", "0").strip().lower() in {
            "1", "true", "on", "yes"
        }
        if enable_streamlit:
            interfaces.append(("src.interface.streamlit_dashboard", "StreamlitDashboard", "streamlit"))
        
        for module, cls_name, key in interfaces:
            cls = self._safe_import(module, cls_name)
            if cls:
                self._registry[f"interface.{key}"] = cls  # Класс, не инстанс
                result.loaded.append(key)
                
        self._results.append(result)
        log.info(str(result))
        return result
    
    def get(self, path: str) -> Optional[Any]:
        """Получить компонент по пути: 'security.shield', 'connectivity.p2p'."""
        return self._registry.get(path)
    
    def list_all(self) -> Dict[str, List[str]]:
        """Список всех загруженных компонентов по категориям."""
        result: Dict[str, List[str]] = {}
        for key in self._registry:
            cat, _, name = key.partition(".")
            result.setdefault(cat, []).append(name)
        return result
    
    def get_claude_agent(self, task_description: str) -> Optional[Dict]:
        """Найти подходящего Claude агента для задачи."""
        if hasattr(self, '_claude_integrator') and self._claude_integrator:
            agent = self._claude_integrator.find_agent_for_task(task_description)
            if agent:
                return {
                    "name": agent.name,
                    "category": agent.category,
                    "description": agent.description,
                    "tools": agent.tools
                }
        return None
    
    def list_claude_agents(self) -> List[Dict]:
        """Список всех доступных Claude агентов."""
        if hasattr(self, '_claude_integrator') and self._claude_integrator:
            return self._claude_integrator.list_available_agents()
        return []
    
    def _integrate_claude_templates(self) -> IntegrationResult:
        """Интеграция шаблонов Claude Code."""
        result = IntegrationResult("claude-templates")
        
        try:
            from src.claude_templates_integrator import ClaudeTemplatesIntegrator
            
            claude_integrator = ClaudeTemplatesIntegrator(self.core)
            claude_stats = claude_integrator.integrate()
            
            # Регистрируем в реестре
            self._registry["claude.agents"] = claude_stats.get("adapters", {})
            self._registry["claude.commands"] = claude_stats.get("commands", {})
            self._registry["claude.hooks"] = claude_stats.get("hooks", 0)
            
            # Сохраняем референс для доступа
            self._claude_integrator = claude_integrator
            
            result.loaded.append("claude-templates")
            result.loaded.append(f"{len(claude_stats.get('adapters', {}))} adapters")
            result.loaded.append(f"{len(claude_stats.get('commands', {}))} commands")
            
        except Exception as e:
            result.errors.append(str(e))
            log.warning(f"Claude Templates integration failed: {e}")
            
        self._results.append(result)
        return result

    def _print_summary(self):
        """Печать итоговой сводки."""
        total_ok = sum(1 for r in self._results if r.ok)
        total_failed = sum(len(r.failed) for r in self._results)

        log.info("\n" + "═" * 60)
        log.info(" ИНТЕГРАЦИЯ ЗАВЕРШЕНА ".center(60, "═"))
        log.info("═" * 60)

        for res in self._results:
            icon = "✅" if res.ok else "⚠️"
            log.info(f"{icon} {res.name:12s} — {len(res.loaded):2d} loaded" +
                     (f" | {len(res.failed)} failed" if res.failed else ""))
            if res.failed:
                for item in res.failed:
                    log.warning("  ↳ %s fail: %s", res.name, item)
            if res.errors:
                for item in res.errors:
                    log.warning("  ↳ %s error: %s", res.name, item)

        log.info("─" * 60)
        log.info(f"Итого: {total_ok}/{len(self._results)} подсистем OK, {total_failed} ошибок")
        log.info("═" * 60)
        
        # Публикуем событие
        bus.publish(Events.CORE_READY, {
            "registry": self._registry,
            "results": [
                {"name": r.name, "loaded": len(r.loaded), "failed": len(r.failed)}
                for r in self._results
            ]
        })


# ═══════════════════════════════════════════════════════════════════
# Фасад для быстрого доступа
# ═════════════════════════════════════════════════════════════════==

def quick_integrate(core=None) -> Dict[str, Any]:
    """Быстрая интеграция всех подсистем."""
    integrator = ArgosIntegrator(core)
    return integrator.integrate_all()


# Синглтон для глобального доступа
_global_integrator: Optional[ArgosIntegrator] = None

def get_integrator() -> ArgosIntegrator:
    """Получить глобальный интегратор."""
    global _global_integrator
    if _global_integrator is None:
        _global_integrator = ArgosIntegrator()
    return _global_integrator
