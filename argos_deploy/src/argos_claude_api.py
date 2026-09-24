"""
argos_claude_api.py — Унифицированный API для работы с Claude Templates в ARGOS

Предоставляет программный интерфейс для:
  • Поиска и вызова Claude агентов
  • Выполнения Claude команд
  • Управления интеграцией

Использование:
    from src.argos_claude_api import ArgosClaudeAPI
    api = ArgosClaudeAPI(core)
    
    # Поиск агента
    agent = api.find_agent("создай python api")
    
    # Получить подсказку для LLM
    prompt = api.get_agent_prompt(agent["name"])
    
    # Выполнить команду
    result = api.execute_command("generate-tests", "src/main.py")
"""

from __future__ import annotations

import os
import json
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass

from src.claude_templates_integrator import (
    ClaudeTemplatesIntegrator,
    ClaudeTemplatesLoader,
    ClaudeComponent
)
from src.argos_logger import get_logger

log = get_logger("argos.claude-api")


@dataclass
class AgentMatch:
    """Результат подбора агента."""
    name: str
    category: str
    description: str
    confidence: float
    prompt: str


@dataclass  
class CommandResult:
    """Результат выполнения команды."""
    success: bool
    command: str
    output: str
    error: Optional[str] = None


class ArgosClaudeAPI:
    """
    Унифицированный API для Claude Templates в ARGOS.
    """
    
    def __init__(self, core=None, auto_init: bool = True):
        self.core = core
        self._integrator: Optional[ClaudeTemplatesIntegrator] = None
        self._agent_cache: Dict[str, ClaudeComponent] = {}
        self._command_cache: Dict[str, Dict] = {}
        
        if auto_init:
            self._init_integrator()
    
    def _init_integrator(self):
        """Инициализация интегратора."""
        try:
            self._integrator = ClaudeTemplatesIntegrator(self.core)
            self._integrator.integrate()
            
            # Кешируем агентов
            for agent in self._integrator.loader.list_by_type("agent"):
                self._agent_cache[agent.name] = agent
            
            # Кешируем команды
            for cmd in self._integrator.loader.list_by_type("command"):
                self._command_cache[cmd.name] = {
                    "component": cmd,
                    "metadata": self._integrator.adapter.adapt_command_to_tool(cmd)
                }
                
            log.info(f"ArgosClaudeAPI: кешировано {len(self._agent_cache)} агентов, {len(self._command_cache)} команд")
        except Exception as e:
            log.error(f"Failed to initialize Claude integrator: {e}")
            self._integrator = None
    
    def is_ready(self) -> bool:
        """Проверка готовности API."""
        return self._integrator is not None
    
    # ═══════════════════════════════════════════════════════
    # Работа с агентами
    # ═══════════════════════════════════════════════════════
    
    def find_agent(self, task: str, min_confidence: float = 0.5) -> Optional[AgentMatch]:
        """
        Находит лучшего агента для задачи.
        
        Args:
            task: Описание задачи
            min_confidence: Минимальный порог уверенности (0-1)
            
        Returns:
            AgentMatch или None
        """
        if not self._integrator:
            return None
        
        component = self._integrator.find_agent_for_task(task)
        if not component:
            return None
        
        # Вычисляем confidence простым способом
        confidence = 0.7  # Базовое значение
        
        return AgentMatch(
            name=component.name,
            category=component.category,
            description=component.description[:200] + "..." if len(component.description) > 200 else component.description,
            confidence=confidence,
            prompt=self._format_agent_prompt(component)
        )
    
    def get_agent_prompt(self, agent_name: str) -> Optional[str]:
        """Получает системный промпт агента."""
        if agent_name in self._agent_cache:
            return self._agent_cache[agent_name].content
        
        # Поиск по частичному совпадению
        for name, comp in self._agent_cache.items():
            if agent_name.lower() in name.lower() or name.lower() in agent_name.lower():
                return comp.content
        
        return None
    
    def list_agents(self, category: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """
        Список агентов с фильтрацией.
        
        Args:
            category: Фильтр по категории
            limit: Максимальное количество
            
        Returns:
            Список словарей с информацией об агентах
        """
        if not self._integrator:
            return []
        
        agents = self._integrator.loader.list_by_type("agent")
        
        if category:
            agents = [a for a in agents if category.lower() in a.category.lower()]
        
        return [
            {
                "name": a.name,
                "category": a.category,
                "description": a.description[:150] + "..." if len(a.description) > 150 else a.description,
                "tools": a.tools[:5] if a.tools else [],
            }
            for a in agents[:limit]
        ]
    
    def search_agents(self, query: str) -> List[Dict]:
        """Поиск агентов по запросу."""
        if not self._integrator:
            return []
        
        results = self._integrator.loader.search(query)
        return [
            {
                "name": r.name,
                "category": r.category,
                "description": r.description[:150] + "..." if len(r.description) > 150 else r.description,
            }
            for r in results[:20]
        ]
    
    def _format_agent_prompt(self, component: ClaudeComponent) -> str:
        """Форматирует промпт агента для использования в LLM."""
        lines = [
            f"# Agent: {component.name}",
            f"Category: {component.category}",
            f"Tools: {', '.join(component.tools[:10]) if component.tools else 'None'}",
            "",
            "## System Prompt",
            component.content[:1500] + "\n..." if len(component.content) > 1500 else component.content,
        ]
        return "\n".join(lines)
    
    # ═══════════════════════════════════════════════════════
    # Работа с командами
    # ═══════════════════════════════════════════════════════
    
    def list_commands(self, category: Optional[str] = None) -> List[Dict]:
        """Список доступных команд."""
        if not self._command_cache:
            return []
        
        commands = list(self._command_cache.values())
        
        if category:
            commands = [c for c in commands if category.lower() in c["component"].category.lower()]
        
        return [
            {
                "name": c["component"].name,
                "category": c["component"].category,
                "description": c["metadata"]["description"],
                "allowed_tools": c["metadata"]["allowed_tools"],
            }
            for c in commands[:50]
        ]
    
    def get_command_info(self, command_name: str) -> Optional[Dict]:
        """Получает информацию о команде."""
        if not self._command_cache:
            return None
        
        # Точное совпадение
        if command_name in self._command_cache:
            cmd = self._command_cache[command_name]
            return {
                "name": cmd["component"].name,
                "description": cmd["metadata"]["description"],
                "content": cmd["component"].content,
                "category": cmd["component"].category,
            }
        
        # Поиск по частичному совпадению
        for name, cmd in self._command_cache.items():
            if command_name.lower() in name.lower():
                return {
                    "name": cmd["component"].name,
                    "description": cmd["metadata"]["description"],
                    "content": cmd["component"].content,
                    "category": cmd["component"].category,
                }
        
        return None
    
    def execute_command(self, command_name: str, *args, **kwargs) -> CommandResult:
        """
        Выполняет команду Claude Templates.
        
        Это заглушка - реальное выполнение требует интеграции с Claude Code.
        """
        info = self.get_command_info(command_name)
        if not info:
            return CommandResult(
                success=False,
                command=command_name,
                output="",
                error=f"Command '{command_name}' not found"
            )
        
        return CommandResult(
            success=False,
            command=command_name,
            output=f"Шаблон команды '{command_name}' найден, выполнение не настроено.\nОписание: {info['description'][:100]}",
            error="execution_not_configured",
        )
    
    # ═══════════════════════════════════════════════════════
    # Интеграция с Skill Loader
    # ═══════════════════════════════════════════════════════
    
    def register_as_skill(self, agent_name: str) -> bool:
        """
        Регистрирует Claude агента как навык ARGOS.
        """
        if not self.core or not self._integrator:
            return False
        
        if not hasattr(self.core, 'skill_loader'):
            log.warning("Core has no skill_loader")
            return False
        
        component = self._agent_cache.get(agent_name)
        if not component:
            log.warning(f"Agent {agent_name} not found")
            return False
        
        try:
            adapted = self._integrator.adapter.adapt_agent_to_skill(component)
            self._integrator._register_as_skill(adapted, component)
            return True
        except Exception as e:
            log.error(f"Failed to register agent as skill: {e}")
            return False
    
    # ═══════════════════════════════════════════════════════
    # Статистика и отчёты
    # ═══════════════════════════════════════════════════════
    
    def get_stats(self) -> Dict[str, Any]:
        """Возвращает статистику интеграции."""
        if not self._integrator:
            return {"error": "Not initialized"}
        
        return {
            "agents": {
                "total": len(self._agent_cache),
                "categories": len(set(a.category for a in self._agent_cache.values()))
            },
            "commands": {
                "total": len(self._command_cache),
                "categories": len(set(c["component"].category for c in self._command_cache.values()))
            },
            "ready": self.is_ready()
        }
    
    def generate_report(self) -> str:
        """Генерирует текстовый отчёт."""
        stats = self.get_stats()
        
        lines = [
            "=" * 60,
            "ARGOS Claude Templates API Report",
            "=" * 60,
            f"Status: {'✅ Ready' if stats['ready'] else '❌ Not Ready'}",
            "",
            f"Agents: {stats['agents']['total']} in {stats['agents']['categories']} categories",
            f"Commands: {stats['commands']['total']} in {stats['commands']['categories']} categories",
            "",
            "Top Categories:",
        ]
        
        # Top categories
        if self._agent_cache:
            from collections import Counter
            cats = Counter(a.category for a in self._agent_cache.values())
            for cat, count in cats.most_common(10):
                lines.append(f"  {cat:30s}: {count}")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════
# Фасад для быстрого доступа
# ═══════════════════════════════════════════════════════

_global_api: Optional[ArgosClaudeAPI] = None

def get_claude_api(core=None) -> ArgosClaudeAPI:
    """Получает глобальный экземпляр API."""
    global _global_api
    if _global_api is None:
        _global_api = ArgosClaudeAPI(core)
    return _global_api


def quick_find_agent(task: str) -> Optional[AgentMatch]:
    """Быстрый поиск агента для задачи."""
    api = get_claude_api()
    return api.find_agent(task)


def quick_list_agents(category: Optional[str] = None) -> List[Dict]:
    """Быстрый список агентов."""
    api = get_claude_api()
    return api.list_agents(category=category)
