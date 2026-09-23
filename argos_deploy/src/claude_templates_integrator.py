"""
claude_templates_integrator.py — Интеграция шаблонов Claude Code Templates в ARGOS

Загружает и адаптирует агентов, команды, хуки, MCP и навыки из
claude-code-templates для использования в экосистеме ARGOS.
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field

# Graceful import для yaml
try:
    import yaml
    _YAML_OK = True
except ImportError:
    _YAML_OK = False
    yaml = None

from src.argos_logger import get_logger
from src.event_bus import get_bus, Events

log = get_logger("argos.claude-templates")
bus = get_bus()

# Путь к шаблонам Claude Code
TEMPLATES_ROOT = Path("C:/Users/AvA/debug/argoss/claude-code-templates")
COMPONENTS_ROOT = TEMPLATES_ROOT / "cli-tool" / "components"


@dataclass
class ClaudeComponent:
    """Унифицированное представление компонента Claude Code."""
    name: str
    component_type: str  # agent, command, hook, mcp, setting, skill
    category: str
    description: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    tools: List[str] = field(default_factory=list)
    source_path: Path = field(default_factory=Path)
    
    @property
    def id(self) -> str:
        return f"{self.component_type}:{self.category}/{self.name}"


class ClaudeTemplatesLoader:
    """Загрузчик компонентов Claude Code Templates."""
    
    SUPPORTED_TYPES = ["agents", "commands", "hooks", "mcps", "settings", "skills"]
    
    def __init__(self, templates_path: Optional[Path] = None):
        self.root = templates_path or COMPONENTS_ROOT
        self._components: Dict[str, ClaudeComponent] = {}
        self._categories: Dict[str, List[str]] = {}
        
    def discover(self) -> Dict[str, int]:
        """Обнаруживает все компоненты по типам. Возвращает статистику."""
        stats = {}
        for comp_type in self.SUPPORTED_TYPES:
            type_path = self.root / comp_type
            if not type_path.exists():
                continue
                
            count = 0
            for category_dir in type_path.iterdir():
                if not category_dir.is_dir():
                    continue
                for file_path in category_dir.glob("*.md"):
                    try:
                        comp = self._parse_component(file_path, comp_type, category_dir.name)
                        self._components[comp.id] = comp
                        self._categories.setdefault(comp_type, []).append(comp.id)
                        count += 1
                    except Exception as e:
                        log.warning(f"Failed to parse {file_path}: {e}")
                        
            stats[comp_type] = count
        return stats
    
    def _parse_component(self, path: Path, comp_type: str, category: str) -> ClaudeComponent:
        """Парсит markdown файл с YAML frontmatter."""
        content = path.read_text(encoding="utf-8")
        
        # Извлекаем YAML frontmatter
        metadata = {}
        markdown_content = content
        
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                yaml_content = parts[1].strip()
                markdown_content = parts[2].strip()
                
                # Пытаемся распарсить YAML
                if _YAML_OK:
                    try:
                        metadata = yaml.safe_load(yaml_content) or {}
                    except yaml.YAMLError:
                        metadata = self._parse_broken_yaml(yaml_content)
                else:
                    metadata = self._parse_broken_yaml(yaml_content)
            else:
                markdown_content = content
        
        # Fallback-значения
        name = metadata.get("name", path.stem)
        description = metadata.get("description", "")
        
        # Инструменты
        tools = metadata.get("tools", [])
        if isinstance(tools, str):
            tools = [t.strip() for t in tools.split(",") if t.strip() and t.strip() != "*"]
        elif tools is None:
            tools = []
            
        return ClaudeComponent(
            name=name,
            component_type=comp_type.rstrip("s"),
            category=category,
            description=description,
            content=markdown_content,
            metadata=metadata,
            tools=tools,
            source_path=path
        )
    
    def _parse_broken_yaml(self, yaml_content: str) -> dict:
        """Ручной парсинг для сломанного YAML."""
        result = {}
        lines = yaml_content.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line or line.startswith('#'):
                i += 1
                continue
            
            if ':' in line and not line.startswith('-'):
                key, sep, val = line.partition(':')
                key = key.strip()
                val = val.strip()
                
                # Если значение пустое - ищем следующие строки
                if not val:
                    val_lines = []
                    i += 1
                    while i < len(lines):
                        next_line = lines[i]
                        if not next_line.strip() or (':' in next_line and not next_line.strip().startswith('-')):
                            break
                        if next_line.startswith('  ') or next_line.startswith('\t'):
                            val_lines.append(next_line.strip())
                        else:
                            break
                        i += 1
                    val = '\n'.join(val_lines)
                
                result[key] = val
            i += 1
        
        return result
    
    def get(self, component_id: str) -> Optional[ClaudeComponent]:
        """Получить компонент по ID."""
        return self._components.get(component_id)
    
    def list_by_type(self, comp_type: str) -> List[ClaudeComponent]:
        """Список компонентов по типу."""
        ids = self._categories.get(comp_type + "s", [])
        return [self._components[i] for i in ids if i in self._components]
    
    def search(self, query: str) -> List[ClaudeComponent]:
        """Поиск компонентов по названию или описанию."""
        query = query.lower()
        results = []
        for comp in self._components.values():
            if (query in comp.name.lower() or 
                query in comp.description.lower() or
                query in comp.category.lower()):
                results.append(comp)
        return results


class ArgosClaudeAdapter:
    """Адаптер компонентов Claude Code → ARGOS Skills/Modules."""
    
    def __init__(self, loader: ClaudeTemplatesLoader):
        self.loader = loader
        
    def adapt_agent_to_skill(self, component: ClaudeComponent) -> Dict[str, Any]:
        """Адаптирует агента Claude в навык ARGOS."""
        return {
            "name": f"claude_{component.name}",
            "version": component.metadata.get("version", "1.0.0"),
            "entry": "skill.py",
            "author": "claude-templates",
            "description": component.description,
            "category": component.category,
            "claude_agent": {
                "original_name": component.name,
                "tools": component.tools,
                "system_prompt": component.content,
                "model": component.metadata.get("model", "sonnet")
            },
            "permissions": ["network"] if "network" in component.content.lower() else []
        }
    
    def adapt_command_to_tool(self, component: ClaudeComponent) -> Dict[str, Any]:
        """Адаптирует команду Claude в инструмент ARGOS."""
        return {
            "name": f"cmd_{component.name}",
            "type": "command",
            "description": component.description,
            "allowed_tools": component.metadata.get("allowed-tools", ""),
            "argument_hint": component.metadata.get("argument-hint", ""),
            "script": component.content,
            "source": str(component.source_path)
        }
    
    def adapt_hook_to_trigger(self, component: ClaudeComponent) -> Dict[str, Any]:
        """Адаптирует хук Claude в триггер ARGOS."""
        return {
            "name": f"hook_{component.name}",
            "type": "trigger",
            "description": component.description,
            "hooks": component.metadata.get("hooks", {}),
            "config": component.content
        }
    
    def adapt_mcp_to_bridge(self, component: ClaudeComponent) -> Dict[str, Any]:
        """Адаптирует MCP Claude в мост ARGOS."""
        return {
            "name": f"mcp_{component.name}",
            "type": "bridge",
            "description": component.description,
            "server_config": component.metadata.get("mcpServers", {}),
            "connection": component.metadata.get("connection", {})
        }


class ClaudeTemplatesIntegrator:
    """Унифицированный интегратор шаблонов Claude Code в ARGOS."""
    
    def __init__(self, core=None):
        self.core = core
        self.loader = ClaudeTemplatesLoader()
        self.adapter = ArgosClaudeAdapter(self.loader)
        self._adapters: Dict[str, Any] = {}
        self._commands: Dict[str, Any] = {}
        self._hooks: List[Callable] = []
        
    def integrate(self) -> Dict[str, Any]:
        """Запускает полную интеграцию шаблонов."""
        log.info("╔" + "═" * 58 + "╗")
        log.info("║" + " CLAUDE TEMPLATES INTEGRATOR ".center(58) + "║")
        log.info("╚" + "═" * 58 + "╝")
        
        # 1. Обнаружение
        stats = self.loader.discover()
        total = sum(stats.values())
        log.info(f"📦 Обнаружено компонентов: {total}")
        for ctype, count in stats.items():
            log.info(f"  └─ {ctype}: {count}")
            
        # 2. Адаптация агентов → навыки
        self._integrate_agents()
        
        # 3. Адаптация команд → инструменты
        self._integrate_commands()
        
        # 4. Адаптация хуков → триггеры
        self._integrate_hooks()
        
        # 5. Адаптация MCP → мосты
        self._integrate_mcps()
        
        # Отчёт
        self._print_summary()
        
        return {
            "adapters": self._adapters,
            "commands": self._commands,
            "hooks": len(self._hooks)
        }
    
    def _integrate_agents(self):
        """Интеграция агентов как навыков."""
        agents = self.loader.list_by_type("agent")
        log.info(f"🤖 Адаптация агентов: {len(agents)}")
        
        for agent in agents:
            try:
                adapted = self.adapter.adapt_agent_to_skill(agent)
                self._adapters[agent.id] = adapted
                
                # Регистрируем как навык в ARGOS
                if self.core:
                    self._register_as_skill(adapted, agent)
                    
            except Exception as e:
                log.warning(f"  ⚠ {agent.name}: {e}")
                
    def _register_as_skill(self, adapted: Dict, component: ClaudeComponent):
        """Регистрирует адаптированного агента как навык ARGOS."""
        # Создаём виртуальный навык
        skill_id = adapted["name"]
        
        # Если у ядра есть skill_loader - регистрируем
        if hasattr(self.core, 'skill_loader') and self.core.skill_loader:
            try:
                # Создаём модуль навыка динамически
                skill_module = type(
                    f"ClaudeSkill_{component.name}",
                    (),
                    {
                        "_claude_component": component,
                        "_system_prompt": component.content,
                        "handle": lambda text, core: self._invoke_claude_agent(component, text)
                    }
                )
                self._adapters[f"skill:{skill_id}"] = skill_module
                log.debug(f"  ✓ Зарегистрирован навык: {skill_id}")
            except Exception as e:
                log.warning(f"  ✗ Ошибка регистрации {skill_id}: {e}")
    
    def _invoke_claude_agent(self, component: ClaudeComponent, query: str) -> str:
        """Вызов агента Claude через ARGOS."""
        return f"[execution_not_configured] Claude Agent: {component.name}\nВызов не выполнен: подключение к исполнителю не настроено.\n{component.description}"
    
    def _integrate_commands(self):
        """Интеграция команд как инструментов."""
        commands = self.loader.list_by_type("command")
        log.info(f"⌨️ Адаптация команд: {len(commands)}")
        
        for cmd in commands:
            try:
                adapted = self.adapter.adapt_command_to_tool(cmd)
                self._commands[cmd.name] = adapted
                log.debug(f"  ✓ {cmd.name}")
            except Exception as e:
                log.warning(f"  ⚠ {cmd.name}: {e}")
                
    def _integrate_hooks(self):
        """Интеграция хуков как триггеров."""
        hooks = self.loader.list_by_type("hook")
        log.info(f"🪝 Адаптация хуков: {len(hooks)}")
        
        for hook in hooks:
            try:
                adapted = self.adapter.adapt_hook_to_trigger(hook)
                self._hooks.append(adapted)
            except Exception as e:
                log.warning(f"  ⚠ {hook.name}: {e}")
                
    def _integrate_mcps(self):
        """Интеграция MCP как мостов."""
        mcps = self.loader.list_by_type("mcp")
        log.info(f"🔗 Адаптация MCP: {len(mcps)}")
        
        for mcp in mcps:
            try:
                adapted = self.adapter.adapt_mcp_to_bridge(mcp)
                self._adapters[mcp.id] = adapted
            except Exception as e:
                log.warning(f"  ⚠ {mcp.name}: {e}")
    
    def find_agent_for_task(self, task_description: str) -> Optional[ClaudeComponent]:
        """Находит подходящего агента для задачи с улучшенным алгоритмом."""
        agents = self.loader.list_by_type("agent")
        if not agents:
            return None
            
        task_lower = task_description.lower()
        
        # Расширенная карта ключевых слов
        keywords_map = {
            # AI/ML
            "ai": ["ai", "ml", "machine learning", "model", "llm", "neural"],
            "prompt": ["prompt", "prompt engineering", "prompts"],
            "llm": ["llm", "large language model", "gpt", "claude"],
            "data": ["data", "dataset", "analytics", "pandas", "numpy"],
            
            # Frontend
            "frontend": ["frontend", "react", "vue", "angular", "ui", "css", "html", "dom", "browser"],
            "react": ["react", "reactjs", "nextjs", "jsx", "hooks"],
            "vue": ["vue", "vuejs", "nuxt"],
            
            # Backend
            "backend": ["backend", "api", "rest", "graphql", "server", "database", "db"],
            "python": ["python", "django", "flask", "fastapi", "asyncio"],
            "nodejs": ["node", "nodejs", "express", "nestjs", "javascript server"],
            "database": ["database", "sql", "nosql", "schema", "postgres", "mysql", "mongodb"],
            
            # DevOps
            "devops": ["devops", "docker", "kubernetes", "k8s", "infra", "infrastructure"],
            "docker": ["docker", "container", "dockerfile", "compose"],
            "k8s": ["kubernetes", "k8s", "helm", "kubectl", "pods"],
            "cicd": ["ci/cd", "cicd", "pipeline", "github actions", "gitlab ci"],
            
            # Security
            "security": ["security", "secure", "audit", "vulnerability", "pentest", "auth"],
            "crypto": ["cryptography", "encryption", "cipher", "hash", "oauth", "jwt"],
            
            # Testing
            "test": ["test", "testing", "jest", "pytest", "coverage", "e2e", "unit test"],
            "qa": ["qa", "quality assurance", "test automation", "cypress", "playwright"],
            
            # Mobile
            "mobile": ["mobile", "android", "ios", "flutter", "react native", "swift"],
            
            # Desktop
            "desktop": ["desktop", "electron", "tauri", "qt", "gui"],
            
            # Cloud
            "cloud": ["cloud", "aws", "gcp", "azure", "serverless", "lambda"],
            
            # Docs/Content
            "docs": ["documentation", "docs", "readme", "markdown", "docusaurus"],
            "blog": ["blog", "article", "content", "writing", "copywriting"],
            
            # Design
            "design": ["design", "ui/ux", "figma", "prototype", "mockup"],
            
            # Code quality
            "refactor": ["refactor", "clean", "optimize", "improve", "structure"],
            "review": ["code review", "review code", "pull request", "pr review"],
        }
        
        # Вычисляем score для каждого агента
        agent_scores = []
        
        for agent in agents:
            score = 0
            desc_lower = agent.description.lower()
            cat_lower = agent.category.lower()
            name_lower = agent.name.lower()
            
            # Проверяем соответствие ключевым словам
            for keyword, terms in keywords_map.items():
                if any(t in task_lower for t in terms):
                    # Увеличиваем score за каждое совпадение
                    if any(t in desc_lower for t in terms):
                        score += 3
                    if any(t in cat_lower for t in terms):
                        score += 2
                    if any(t in name_lower for t in terms):
                        score += 2
            
            # Дополнительные бонусы
            if score > 0:
                # Предпочитаем специализированные агенты, а не generalist
                if len(agent.category.split('-')) > 1:
                    score += 1
                
                # Чем больше tools, тем лучше
                score += min(len(agent.tools) * 0.1, 2)
            
            agent_scores.append((agent, score))
        
        # Сортируем по убыванию score
        agent_scores.sort(key=lambda x: x[1], reverse=True)
        
        # Возвращаем лучший результат или None если нет совпадений
        best_agent, best_score = agent_scores[0] if agent_scores else (None, 0)
        
        if best_score > 0:
            log.debug(f"Found agent {best_agent.name} with score {best_score} for task: {task_description}")
            return best_agent
        
        # Fallback: возвращаем первого подходящего по категории
        return agents[0] if agents else None
    
    def list_available_agents(self) -> List[Dict]:
        """Возвращает список доступных агентов с описаниями."""
        agents = self.loader.list_by_type("agent")
        return [
            {
                "name": a.name,
                "category": a.category,
                "description": a.description,
                "tools": a.tools
            }
            for a in agents[:20]  # Ограничиваем для UI
        ]
    
    def _print_summary(self):
        """Печать итоговой сводки."""
        log.info("\n" + "═" * 60)
        log.info(" ИНТЕГРАЦИЯ ШАБЛОНОВ ЗАВЕРШЕНА ".center(60, "═"))
        log.info("═" * 60)
        log.info(f"🤖 Агенты:    {len([k for k in self._adapters if k.startswith('agent:')])}")
        log.info(f"⌨️ Команды:   {len(self._commands)}")
        log.info(f"🪝 Хуки:      {len(self._hooks)}")
        log.info(f"🔗 MCP:       {len([k for k in self._adapters if k.startswith('mcp:')])}")
        log.info("═" * 60)


def quick_claude_integrate(core=None) -> ClaudeTemplatesIntegrator:
    """Быстрая интеграция шаблонов Claude."""
    integrator = ClaudeTemplatesIntegrator(core)
    integrator.integrate()
    return integrator


# Синглтон
_global_claude_integrator: Optional[ClaudeTemplatesIntegrator] = None

def get_claude_integrator(core=None) -> ClaudeTemplatesIntegrator:
    """Получить глобальный интегратор шаблонов Claude."""
    global _global_claude_integrator
    if _global_claude_integrator is None:
        _global_claude_integrator = ClaudeTemplatesIntegrator(core)
    return _global_claude_integrator
