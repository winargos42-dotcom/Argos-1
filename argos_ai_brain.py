"""
🧠 ARGOS AI BRAIN SYSTEM
Распределённая система интеллектуальных агентов с Azure AI

Компоненты:
- Azure OpenAI интеграция
- Локальное кэширование и память
- Агент-управление
- Распределённое принятие решений
- Обучение и адаптация
"""

import os
import json
import asyncio
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
from abc import ABC, abstractmethod
import sqlite3
from enum import Enum

# Azure SDK
# [FIX-BRAIN-1] Правильный пакет — `openai` (>=1.0), а не несуществующий `azure-ai-openai`.
# Класс AzureOpenAI живёт в пакете openai, а не в azure.ai.openai.
try:
    from openai import AzureOpenAI
    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False
    print("⚠️  openai SDK не установлена. Установите: pip install 'openai>=1.0'")


class AgentRole(Enum):
    """Роли агентов в системе"""
    MASTER = "master"           # Главный координатор
    ANALYST = "analyst"         # Анализ данных
    OPTIMIZER = "optimizer"     # Оптимизация
    MONITOR = "monitor"         # Мониторинг
    EXECUTOR = "executor"       # Исполнение команд


class AgentState(Enum):
    """Состояния агента"""
    IDLE = "idle"
    THINKING = "thinking"
    WORKING = "working"
    WAITING = "waiting"
    ERROR = "error"


@dataclass
class AgentConfig:
    """Конфигурация агента"""
    name: str
    role: AgentRole
    model: str = "gpt-4"
    temperature: float = 0.7
    max_tokens: int = 2000
    timeout: int = 30
    local_id: Optional[str] = None
    node_id: Optional[str] = None
    
    def to_dict(self):
        return {
            'name': self.name,
            'role': self.role.value,
            'model': self.model,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
            'timeout': self.timeout,
            'local_id': self.local_id,
            'node_id': self.node_id
        }


@dataclass
class Memory:
    """Память агента"""
    agent_id: str
    timestamp: str
    context: Dict[str, Any]
    result: str
    success: bool
    tokens_used: int = 0


def _chat_completion_kwargs(model_name: str, max_n: int, temperature: float) -> Dict[str, Any]:
    """[FIX-BRAIN-4] Возвращает набор kwargs для chat.completions.create в зависимости
    от семейства модели.

    - Reasoning/GPT-5 семейство (o1/o3/o4/gpt-5.x) требует max_completion_tokens
      вместо max_tokens, и не поддерживает кастомный temperature (фиксирован=1).
    - Старые модели (gpt-3.5/4/4-turbo/4o) используют max_tokens и принимают temperature.
    """
    m = (model_name or "").lower()
    is_reasoning = any(m.startswith(p) for p in ("o1", "o3", "o4", "gpt-5", "gpt-6"))
    if is_reasoning:
        return {"max_completion_tokens": max_n}
    return {"max_tokens": max_n, "temperature": temperature}


class AzureAIClient:
    """Клиент для работы с Azure OpenAI"""

    def __init__(self):
        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.api_key = os.getenv("AZURE_OPENAI_KEY")
        self.api_version = os.getenv("AZURE_OPENAI_VERSION", "2024-02-15-preview")
        self.model_name = os.getenv("AZURE_OPENAI_MODEL", "gpt-4-turbo")
        self.deployment_name = os.getenv("AZURE_DEPLOYMENT_NAME", "argos-gpt4")
        
        self.client = None
        self.initialized = False
        
        if AZURE_AVAILABLE and self.endpoint and self.api_key:
            try:
                self.client = AzureOpenAI(
                    api_key=self.api_key,
                    api_version=self.api_version,
                    azure_endpoint=self.endpoint
                )
                self.initialized = True
                print("✅ Azure OpenAI клиент инициализирован")
            except Exception as e:
                print(f"❌ Ошибка инициализации Azure: {e}")
    
    async def think(self, prompt: str, config: AgentConfig) -> Dict[str, Any]:
        """Запрос к Azure OpenAI"""
        
        if not self.initialized:
            return {
                'success': False,
                'error': 'Azure OpenAI не инициализирован',
                'fallback': True
            }
        
        try:
            response = await asyncio.to_thread(
                self._make_request,
                prompt,
                config
            )
            return response
        except Exception as e:
            print(f"❌ Ошибка при запросе к Azure: {e}")
            return {
                'success': False,
                'error': str(e),
                'fallback': True
            }
    
    def _make_request(self, prompt: str, config: AgentConfig) -> Dict[str, Any]:
        """Синхронный запрос к API"""
        
        try:
            # [FIX-BRAIN-3] В openai>=1.0 Azure-клиент принимает ТОЛЬКО model=<deployment_name>,
            # параметр deployment_id удалён и ронял запрос с TypeError. На Azure endpoint
            # параметр model трактуется как имя deployment-а, а не модели.
            # [FIX-BRAIN-4] max_tokens / temperature определяются через helper, т.к.
            # reasoning-модели (gpt-5.x, o-series) используют max_completion_tokens
            # и игнорируют temperature. Имя модели берём из env (AZURE_OPENAI_MODEL).
            extra_kwargs = _chat_completion_kwargs(
                self.model_name, config.max_tokens, config.temperature
            )
            response = self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {
                        "role": "system",
                        "content": f"Ты {config.name} - {config.role.value} агент в распределённой системе ARGOS. Твоя задача: {self._get_system_prompt(config.role)}"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                **extra_kwargs,
            )
            
            return {
                'success': True,
                'response': response.choices[0].message.content,
                'tokens': response.usage.total_tokens,
                'model': config.model
            }
        except Exception as e:
            raise e
    
    @staticmethod
    def _get_system_prompt(role: AgentRole) -> str:
        """Системные подсказки для разных ролей"""
        
        prompts = {
            AgentRole.MASTER: "Координировать работу других агентов, принимать стратегические решения, управлять ресурсами",
            AgentRole.ANALYST: "Анализировать данные, находить закономерности, генерировать insights",
            AgentRole.OPTIMIZER: "Оптимизировать процессы, улучшать производительность, решать задачи оптимизации",
            AgentRole.MONITOR: "Мониторить систему, выявлять аномалии, сообщать о проблемах",
            AgentRole.EXECUTOR: "Выполнять задачи, запускать процессы, контролировать выполнение"
        }
        
        return prompts.get(role, "Выполнять свои обязанности в системе ARGOS")


class AgentMemoryDB:
    """База данных памяти агентов"""
    
    def __init__(self, db_path: str = "~/.argos/agent_memory.db"):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """Инициализация БД"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT,
                    timestamp TEXT,
                    context TEXT,
                    result TEXT,
                    success BOOLEAN,
                    tokens_used INTEGER,
                    created_at TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_stats (
                    agent_id TEXT PRIMARY KEY,
                    tasks_completed INTEGER DEFAULT 0,
                    tasks_failed INTEGER DEFAULT 0,
                    avg_response_time REAL DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    last_activity TEXT
                )
            """)
            
            conn.commit()
    
    def save_memory(self, memory: Memory):
        """Сохранить память"""
        memory_id = f"{memory.agent_id}_{datetime.now().timestamp()}"
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO memories 
                (id, agent_id, timestamp, context, result, success, tokens_used, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory_id,
                memory.agent_id,
                memory.timestamp,
                json.dumps(memory.context),
                memory.result,
                memory.success,
                memory.tokens_used,
                datetime.now().isoformat()
            ))
            conn.commit()
    
    def get_agent_memories(self, agent_id: str, limit: int = 10) -> List[Dict]:
        """Получить памяти агента"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT * FROM memories 
                WHERE agent_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (agent_id, limit))
            
            columns = [description[0] for description in cursor.description]
            memories = []
            
            for row in cursor.fetchall():
                memory_dict = dict(zip(columns, row))
                memory_dict['context'] = json.loads(memory_dict['context'])
                memories.append(memory_dict)
            
            return memories
    
    def update_agent_stats(self, agent_id: str, success: bool, tokens: int, response_time: float):
        """Обновить статистику агента"""
        with sqlite3.connect(self.db_path) as conn:
            # Получить текущие значения
            cursor = conn.execute(
                "SELECT * FROM agent_stats WHERE agent_id = ?",
                (agent_id,)
            )
            
            row = cursor.fetchone()
            
            if row:
                # Обновить существующую запись
                tasks_completed = row[1] + (1 if success else 0)
                tasks_failed = row[2] + (0 if success else 1)
                
                conn.execute("""
                    UPDATE agent_stats 
                    SET tasks_completed = ?, 
                        tasks_failed = ?, 
                        total_tokens = total_tokens + ?,
                        last_activity = ?
                    WHERE agent_id = ?
                """, (
                    tasks_completed,
                    tasks_failed,
                    tokens,
                    datetime.now().isoformat(),
                    agent_id
                ))
            else:
                # Создать новую запись
                conn.execute("""
                    INSERT INTO agent_stats 
                    (agent_id, tasks_completed, tasks_failed, total_tokens, last_activity)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    agent_id,
                    1 if success else 0,
                    0 if success else 1,
                    tokens,
                    datetime.now().isoformat()
                ))
            
            conn.commit()
    
    def get_agent_stats(self, agent_id: str) -> Dict:
        """Получить статистику агента"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT * FROM agent_stats WHERE agent_id = ?",
                (agent_id,)
            )
            
            row = cursor.fetchone()
            
            if row:
                columns = [description[0] for description in cursor.description]
                return dict(zip(columns, row))
            
            return {}


class ARGOSAgent:
    """Интеллектуальный агент ARGOS"""
    
    def __init__(self, config: AgentConfig, azure_client: Optional[AzureAIClient] = None):
        self.config = config
        self.azure_client = azure_client or AzureAIClient()
        self.memory_db = AgentMemoryDB()
        self.state = AgentState.IDLE
        self.id = f"{config.role.value}_{datetime.now().timestamp()}"
        self.tasks_queue: asyncio.Queue = asyncio.Queue()
        self.last_activity = None
    
    async def think(self, prompt: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Основной метод мышления агента"""
        
        self.state = AgentState.THINKING
        context = context or {}
        start_time = datetime.now()
        
        try:
            # Получить памяти предыдущих задач
            recent_memories = self.memory_db.get_agent_memories(self.id, limit=5)
            
            # Расширить контекст историей
            enhanced_prompt = self._enhance_prompt(prompt, recent_memories)
            
            # Запросить Azure OpenAI
            response = await self.azure_client.think(enhanced_prompt, self.config)
            
            # Обработать результат
            if response.get('success'):
                self.state = AgentState.IDLE
                
                # Сохранить в память
                memory = Memory(
                    agent_id=self.id,
                    timestamp=datetime.now().isoformat(),
                    context=context,
                    result=response['response'],
                    success=True,
                    tokens_used=response.get('tokens', 0)
                )
                self.memory_db.save_memory(memory)
                
                # Обновить статистику
                elapsed_time = (datetime.now() - start_time).total_seconds()
                self.memory_db.update_agent_stats(
                    self.id,
                    success=True,
                    tokens=response.get('tokens', 0),
                    response_time=elapsed_time
                )
                
                self.last_activity = datetime.now()
                
                return {
                    'success': True,
                    'agent_id': self.id,
                    'response': response['response'],
                    'tokens': response.get('tokens', 0),
                    'model': response.get('model'),
                    'thinking_time': elapsed_time
                }
            else:
                # Fallback на локальную обработку
                self.state = AgentState.ERROR
                return await self._fallback_think(prompt, context)
        
        except Exception as e:
            self.state = AgentState.ERROR
            print(f"❌ Ошибка в think(): {e}")
            
            # Сохранить ошибку в память
            memory = Memory(
                agent_id=self.id,
                timestamp=datetime.now().isoformat(),
                context=context,
                result=str(e),
                success=False
            )
            self.memory_db.save_memory(memory)
            
            return {
                'success': False,
                'agent_id': self.id,
                'error': str(e),
                'fallback': True
            }
    
    async def _fallback_think(self, prompt: str, context: Dict) -> Dict[str, Any]:
        """Локальная обработка если Azure недоступен"""
        
        # Простая локальная обработка на основе правил
        response = self._local_reasoning(prompt, context)
        
        memory = Memory(
            agent_id=self.id,
            timestamp=datetime.now().isoformat(),
            context=context,
            result=response,
            success=True,
            tokens_used=0
        )
        self.memory_db.save_memory(memory)
        self.memory_db.update_agent_stats(self.id, success=True, tokens=0, response_time=0)
        
        return {
            'success': True,
            'agent_id': self.id,
            'response': response,
            'fallback': True,
            'local_reasoning': True
        }
    
    def _local_reasoning(self, prompt: str, context: Dict) -> str:
        """Локальное рассуждение агента"""
        
        # Базовая обработка на основе роли
        if self.config.role == AgentRole.MONITOR:
            return self._monitor_reasoning(prompt, context)
        elif self.config.role == AgentRole.ANALYST:
            # [FIX-BRAIN-2] было AgentRole.ANALYZER — такого имени в enum нет, только ANALYST.
            return self._analyzer_reasoning(prompt, context)
        elif self.config.role == AgentRole.OPTIMIZER:
            return self._optimizer_reasoning(prompt, context)
        else:
            return f"[{self.config.name}] Обработано локально: {prompt[:100]}..."
    
    def _monitor_reasoning(self, prompt: str, context: Dict) -> str:
        """Логика мониторинга"""
        if 'status' in prompt.lower():
            return "✅ Все системы в норме. Нет аномалий."
        elif 'error' in prompt.lower():
            return "⚠️  Обнаружены потенциальные проблемы. Требуется дополнительное исследование."
        return "📊 Мониторинг активен. Система работает нормально."
    
    def _analyzer_reasoning(self, prompt: str, context: Dict) -> str:
        """Логика анализа"""
        return f"📈 Анализ: На основе предоставленных данных система показывает положительный тренд."
    
    def _optimizer_reasoning(self, prompt: str, context: Dict) -> str:
        """Логика оптимизации"""
        return "⚡ Рекомендации по оптимизации: 1) Кэширование 2) Параллелизм 3) Batch processing"
    
    @staticmethod
    def _enhance_prompt(prompt: str, memories: List[Dict]) -> str:
        """Расширить промпт историей"""
        
        if not memories:
            return prompt
        
        memory_context = "Предыдущие задачи:\n"
        for mem in memories[:3]:
            memory_context += f"- {mem.get('result', '')[:100]}\n"
        
        return f"{memory_context}\nТекущая задача: {prompt}"
    
    def get_status(self) -> Dict[str, Any]:
        """Получить статус агента"""
        stats = self.memory_db.get_agent_stats(self.id)
        
        return {
            'id': self.id,
            'name': self.config.name,
            'role': self.config.role.value,
            'state': self.state.value,
            'last_activity': self.last_activity,
            'stats': stats,
            'config': self.config.to_dict()
        }


class ARGOSBrain:
    """Главный мозг системы ARGOS"""
    
    def __init__(self, node_id: str = "local"):
        self.node_id = node_id
        self.agents: Dict[str, ARGOSAgent] = {}
        self.azure_client = AzureAIClient()
        self.coordinator_task = None
        self.running = False
        
        print(f"🧠 ARGOS Brain инициализирован на узле: {node_id}")
    
    def create_agent(self, name: str, role: AgentRole, **kwargs) -> ARGOSAgent:
        """Создать нового агента"""
        
        config = AgentConfig(
            name=name,
            role=role,
            node_id=self.node_id,
            **kwargs
        )
        
        agent = ARGOSAgent(config, self.azure_client)
        self.agents[agent.id] = agent
        
        print(f"✅ Агент создан: {name} ({role.value}) - ID: {agent.id}")
        
        return agent
    
    async def think(self, query: str, role: AgentRole = AgentRole.MASTER, context: Dict = None) -> Dict:
        """Главный запрос мышления"""
        
        # Найти подходящего агента
        suitable_agent = self._find_agent(role)
        
        if not suitable_agent:
            # Создать нового агента если не найден
            suitable_agent = self.create_agent(
                f"agent_{role.value}",
                role
            )
        
        return await suitable_agent.think(query, context)
    
    async def coordinate(self, task: str, agents: List[AgentRole] = None) -> Dict:
        """Координировать работу нескольких агентов"""
        
        agents = agents or [
            AgentRole.ANALYST,
            AgentRole.OPTIMIZER,
            AgentRole.EXECUTOR
        ]
        
        results = {}
        
        for agent_role in agents:
            result = await self.think(task, agent_role)
            results[agent_role.value] = result
        
        return {
            'task': task,
            'agent_results': results,
            'timestamp': datetime.now().isoformat()
        }
    
    def _find_agent(self, role: AgentRole) -> Optional[ARGOSAgent]:
        """Найти агента по роли"""
        for agent in self.agents.values():
            if agent.config.role == role and agent.state != AgentState.ERROR:
                return agent
        return None
    
    async def start(self):
        """Запустить мозг"""
        self.running = True
        print("🚀 ARGOS Brain запущен!")
    
    async def stop(self):
        """Остановить мозг"""
        self.running = False
        print("🛑 ARGOS Brain остановлен")
    
    def get_status(self) -> Dict[str, Any]:
        """Получить полный статус системы"""
        return {
            'node_id': self.node_id,
            'running': self.running,
            'agents_count': len(self.agents),
            'agents': {
                agent_id: agent.get_status() 
                for agent_id, agent in self.agents.items()
            },
            'timestamp': datetime.now().isoformat()
        }


# Пример использования
if __name__ == "__main__":
    async def main():
        # Инициализировать мозг
        brain = ARGOSBrain(node_id="local-pc")
        
        # Создать агентов
        brain.create_agent("Аналитик", AgentRole.ANALYST)
        brain.create_agent("Оптимизатор", AgentRole.OPTIMIZER)
        brain.create_agent("Монитор", AgentRole.MONITOR)
        
        # Запустить
        await brain.start()
        
        # Примеры запросов
        print("\n" + "="*50)
        print("🧠 ARGOS AI BRAIN - ПРИМЕРЫ")
        print("="*50)
        
        # 1. Простой запрос
        result = await brain.think(
            "Какова текущая производительность системы?",
            AgentRole.MONITOR
        )
        print(f"\n✅ Результат мониторинга:\n{json.dumps(result, indent=2, ensure_ascii=False)}")
        
        # 2. Анализ
        result = await brain.think(
            "Проанализируй тренды использования ресурсов",
            AgentRole.ANALYST
        )
        print(f"\n📊 Результат анализа:\n{json.dumps(result, indent=2, ensure_ascii=False)}")
        
        # 3. Координация
        results = await brain.coordinate(
            "Оптимизировать производительность P2P сети"
        )
        print(f"\n🔗 Результаты координации:\n{json.dumps(results, indent=2, ensure_ascii=False)}")
        
        # Статус
        status = brain.get_status()
        print(f"\n📈 Статус системы:\n{json.dumps(status, indent=2, ensure_ascii=False)}")
        
        await brain.stop()
    
    # Запустить
    asyncio.run(main())
