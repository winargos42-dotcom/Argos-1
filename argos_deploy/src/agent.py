"""
agent.py — ArgosAgent: автономный агент цепочек задач и напоминаний.

Объединяет:
  - Выполнение цепочек задач по тексту (execute_plan / _parse_steps)
  - Асинхронное выполнение очереди задач (run_chain)
  - Управление напоминаниями (check_reminders)
  - Субагентства (SubAgencyManager)
  - Отчёт о состоянии агента
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from src.argos_logger import get_logger
from src.agent_guard import AgentGuard
from src.execution_outcome import classify_execution

log = get_logger("argos.agent")

STEP_SEPARATORS = [" затем ", " потом ", " после этого ", " → ", "->", " и затем ", " далее "]



def _filter_agent_response(text: str) -> str:
    """Фильтруем мусор из ответа агента."""
    if not text:
        return text
    
    lines = text.splitlines()
    filtered = []
    skip_count = 0
    
    for line in lines:
        # Пропускаем длинные таблицы markdown (README)
        if line.startswith("|") and line.count("|") > 3:
            skip_count += 1
            continue
        # Пропускаем блоки кода из README
        if line.startswith("```") and len(filtered) == 0:
            skip_count += 1
            continue
        # Пропускаем строки с заголовками README
        if line.startswith("##") and "ARGOS" in line and len(filtered) < 3:
            skip_count += 1
            continue
        filtered.append(line)
    
    result = "\n".join(filtered).strip()
    
    # Если ответ слишком длинный и похож на README
    if len(result) > 2000 and ("##" in result or "```bash" in result):
        # Берём только первые 500 символов до первого блока кода
        idx = result.find("```")
        if idx > 100:
            result = result[:idx].strip()
        else:
            result = result[:500] + "..."
    
    return result


def _filter_agent_response(text: str) -> str:
    """Фильтруем мусор из ответа агента."""
    if not text:
        return text

    lines = text.splitlines()
    filtered = []
    skip_count = 0

    for line in lines:
        # Пропускаем длинные таблицы markdown (README)
        if line.startswith("|") and line.count("|") > 3:
            skip_count += 1
            continue
        # Пропускаем блоки кода из README
        if line.startswith("```") and len(filtered) == 0:
            skip_count += 1
            continue
        # Пропускаем строки с заголовками README
        if line.startswith("##") and "ARGOS" in line and len(filtered) < 3:
            skip_count += 1
            continue
        filtered.append(line)

    result = "\n".join(filtered).strip()

    # Если ответ слишком длинный и похож на README
    if len(result) > 2000 and ("##" in result or "```bash" in result):
        # Берём только первые 500 символов до первого блока кода
        idx = result.find("```")
        if idx > 100:
            result = result[:idx].strip()
        else:
            result = result[:500] + "..."

    return result


def _filter_agent_response(text: str) -> str:
    """Фильтруем мусор из ответа агента (README, длинные таблицы)."""
    if not text:
        return text

    lines = text.splitlines()
    filtered = []
    for line in lines:
        if line.startswith("|") and line.count("|") > 3:
            continue
        if line.startswith("```") and len(filtered) == 0:
            continue
        if line.startswith("##") and "ARGOS" in line and len(filtered) < 3:
            continue
        filtered.append(line)

    result = "\n".join(filtered).strip()

    if len(result) > 2000 and ("##" in result or "```bash" in result):
        idx = result.find("```")
        if idx > 100:
            result = result[:idx].strip()
        else:
            result = result[:500] + "..."

    return result


class ArgosAgent:
    """
    Автономный агент Аргоса.

    Возможности:
      - Разбор сложной команды на шаги и последовательное выполнение (execute_plan)
      - Асинхронный запуск очереди задач в отдельном потоке (run_chain)
      - Управление напоминаниями с поддержкой повторяющихся событий
      - Поддержка суб-агентств (SubAgencyManager)
    """

    def __init__(self, core):
        self.core = core
        self._guard = AgentGuard()
        # --- состояние execute_plan ---
        self._running = False
        self._results: List[dict] = []
        self._sub_agency = None

        # --- состояние run_chain ---
        self._thread: Optional[threading.Thread] = None
        self._task_chain: List[str] = []
        self._chain_results: List[dict] = []
        self._report: Dict[str, Any] = {
            "started_at": None,
            "tasks_done": 0,
            "errors": 0,
            "unverified": 0,
            "last_task": None,
        }

        # --- напоминания ---
        self._reminders: List[Dict[str, Any]] = []
        self._reminder_lock = threading.Lock()

    # ──────────────────────────────────────────────────────────────
    # СУБАГЕНТСТВА
    # ──────────────────────────────────────────────────────────────

    def set_sub_agency(self, manager) -> None:
        """Подключить SubAgencyManager к агенту."""
        self._sub_agency = manager
        log.info("ArgosAgent: SubAgencyManager подключён (%d субагентств)", len(manager._agents))

    def _execute_step(self, step: str, admin, flasher) -> object:
        """Выполнить один шаг плана: сначала через суб-агентство, затем через core."""
        if self._sub_agency is not None:
            sub_result = self._sub_agency.dispatch(step)
            if sub_result is not None:
                return sub_result
        return self.core.process_logic(step, admin, flasher)

    # ──────────────────────────────────────────────────────────────
    # EXECUTE_PLAN — синхронный парсинг текстовой цепочки
    # ──────────────────────────────────────────────────────────────

    def execute_plan(self, plan: str, admin, flasher) -> Optional[str]:
        """
        Разбирает текстовую команду на шаги и выполняет последовательно.
        Возвращает None если это не агентная задача (шаг один).
        """
        steps = self._parse_steps(plan)
        if len(steps) <= 1:
            return None

        log.info("Агент: %d шагов", len(steps))
        self._results = []
        self._running = True

        agency_tag = " [суб-агентства]" if self._sub_agency else ""
        results = [f"🤖 АГЕНТ АКТИВИРОВАН{agency_tag} — {len(steps)} шагов:\n"]

        for i, step in enumerate(steps, 1):
            if not self._running:
                results.append(f"\n⛔ Выполнение прервано на шаге {i}.")
                break

            step = step.strip()
            if not step:
                continue

            decision = self._guard.validate_step(step)
            if not decision.allowed:
                results.append(f"\n📍 Шаг {i}/{len(steps)}: BLOCKED [{decision.reason}]")
                self._results.append({"step": step, "result": f"BLOCKED:{decision.reason}", "ok": False, "status": "failed"})
                continue
            step = decision.sanitized

            results.append(f"\n📍 Шаг {i}/{len(steps)}: {step}")
            log.info("Шаг %d: %s", i, step)

            try:
                answer, status = classify_execution(self._execute_step(step, admin, flasher))
                answer = answer[:300]
                icon = {"succeeded": "✅", "failed": "❌", "unverified": "❔"}[status]
                results.append(f"   {icon} {answer or 'Результат отсутствует'}")
                self._results.append({"step": step, "result": answer, "ok": status == "succeeded", "status": status})
            except Exception as e:
                err = str(e)
                results.append(f"   ❌ Ошибка: {err}")
                self._results.append({"step": step, "result": err, "ok": False, "status": "failed"})
                log.error("Шаг %d ошибка: %s", i, err)

            time.sleep(0.5)

        self._running = False
        ok_count = sum(1 for r in self._results if r["ok"])
        fail_count = sum(r["status"] == "failed" for r in self._results)
        unverified_count = sum(r["status"] == "unverified" for r in self._results)

        results.append(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━")
        label = "ПЛАН ВЫПОЛНЕН" if ok_count == len(steps) else "ИТОГ ПЛАНА"
        results.append(f"🤖 {label}: ✅ {ok_count} / ❌ {fail_count} / ❔ {unverified_count}")
        return "\n".join(results)

    def _parse_steps(self, text: str) -> list:
        """Разбивает текст на шаги по разделителям."""
        result = [text]
        for sep in STEP_SEPARATORS:
            new_result = []
            for part in result:
                new_result.extend(part.split(sep))
            result = new_result
        numbered = re.split(r"\d+\.\s+", text)
        if len(numbered) > 2:
            return [s.strip() for s in numbered if s.strip()]
        return [s.strip() for s in result if s.strip()]

    def last_report(self) -> str:
        if not self._results:
            return "📭 Агент ещё не запускался."
        lines = ["📋 ПОСЛЕДНИЙ ОТЧЁТ АГЕНТА:"]
        for i, r in enumerate(self._results, 1):
            icon = "✅" if r["ok"] else ("❔" if r.get("status") == "unverified" else "❌")
            lines.append(f"  {icon} Шаг {i}: {r['step'][:50]}")
            lines.append(f"      → {r['result'][:100]}")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────
    # RUN_CHAIN — асинхронная очередь задач
    # ──────────────────────────────────────────────────────────────

    def run_chain(
        self, tasks: List[str], callback: Optional[Callable[[str, str], None]] = None
    ) -> str:
        """
        Запустить цепочку задач асинхронно в отдельном потоке.

        :param tasks:    Список команд для последовательного выполнения.
        :param callback: fn(task, result) вызывается после каждой задачи.
        :return: Статус запуска.
        """
        if self._running:
            return "⚠️ Агент уже выполняет цепочку задач. Подожди или останови."

        self._task_chain = list(tasks)
        self._chain_results = []
        self._running = True
        self._report["started_at"] = time.time()
        self._report["tasks_done"] = 0
        self._report["errors"] = 0
        self._report["unverified"] = 0

        self._thread = threading.Thread(
            target=self._execute_chain,
            args=(callback,),
            daemon=True,
            name="argos-agent-chain",
        )
        self._thread.start()
        return f"🤖 Агент запущен: {len(tasks)} задач в цепочке."

    def _execute_chain(self, callback: Optional[Callable[[str, str], None]]) -> None:
        """Внутренний метод — последовательное выполнение цепочки."""
        for task in self._task_chain:
            if not self._running:
                break
            try:
                self._report["last_task"] = task
                result = self.core.process(task)
                answer, status = classify_execution(result)
                self._chain_results.append({"task": task, "result": answer, "ok": status == "succeeded", "status": status})
                counter = {"succeeded": "tasks_done", "failed": "errors", "unverified": "unverified"}[status]
                self._report[counter] += 1
            except Exception as e:
                err = str(e)
                self._chain_results.append({"task": task, "result": err, "ok": False, "status": "failed"})
                self._report["errors"] += 1
                answer = f"❌ {err}"
            if callback:
                try:
                    callback(task, answer)
                except Exception:
                    log.warning("Не удалось передать результат задачи обработчику")
            time.sleep(0.3)

        self._running = False

    def stop(self) -> str:
        """Остановить любое выполнение (execute_plan или run_chain)."""
        if not self._running:
            return "ℹ️ Агент не запущен."
        self._running = False
        return "🛑 Агент остановлен."

    # ──────────────────────────────────────────────────────────────
    # НАПОМИНАНИЯ
    # ──────────────────────────────────────────────────────────────

    def add_reminder(
        self, text: str, fire_at: float, repeat_seconds: Optional[float] = None
    ) -> str:
        """
        Добавить напоминание.

        :param text:           Текст напоминания.
        :param fire_at:        Unix-timestamp когда сработать.
        :param repeat_seconds: Интервал повтора (None = однократно).
        :return: ID напоминания.
        """
        rid = f"rem_{int(time.time() * 1000)}"
        with self._reminder_lock:
            self._reminders.append(
                {
                    "id": rid,
                    "text": text,
                    "fire_at": fire_at,
                    "repeat_seconds": repeat_seconds,
                    "fired": False,
                }
            )
        return rid

    def check_reminders(self) -> List[str]:
        """
        Проверить напоминания и вернуть список сработавших текстов.
        Вызывается планировщиком или вручную.
        """
        now = time.time()
        fired_texts: List[str] = []

        with self._reminder_lock:
            for rem in self._reminders:
                if rem["fired"] and rem["repeat_seconds"] is None:
                    continue

                if now >= rem["fire_at"]:
                    fired_texts.append(rem["text"])
                    rem["fired"] = True

                    if rem["repeat_seconds"]:
                        rem["fire_at"] = now + rem["repeat_seconds"]
                        rem["fired"] = False

            self._reminders = [
                r for r in self._reminders if not (r["fired"] and r["repeat_seconds"] is None)
            ]

        for text in fired_texts:
            self._dispatch_reminder(text)

        return fired_texts

    def _dispatch_reminder(self, text: str) -> None:
        """Отправить напоминание через доступные каналы."""
        try:
            if hasattr(self.core, "event_bus") and self.core.event_bus:
                self.core.event_bus.publish("agent.reminder", {"text": text, "ts": time.time()})
            if hasattr(self.core, "tg") and self.core.tg:
                self.core.tg.send_message(f"⏰ Напоминание: {text}")
            if hasattr(self.core, "logger"):
                self.core.logger.info("Reminder fired: %s", text)
        except Exception:
            pass

    def list_reminders(self) -> str:
        """Вернуть список активных напоминаний в виде строки."""
        with self._reminder_lock:
            active = [r for r in self._reminders if not r["fired"]]
        if not active:
            return "📭 Активных напоминаний нет."
        lines = ["⏰ НАПОМИНАНИЯ:"]
        for i, r in enumerate(active, 1):
            fire_dt = datetime.fromtimestamp(r["fire_at"]).strftime("%d.%m %H:%M")
            repeat = f" (каждые {int(r['repeat_seconds'])}с)" if r["repeat_seconds"] else ""
            lines.append(f"  {i}. [{fire_dt}]{repeat} {r['text']}")
        return "\n".join(lines)

    def remove_reminder(self, index: int) -> str:
        """Удалить напоминание по номеру (1-based)."""
        with self._reminder_lock:
            active = [r for r in self._reminders if not r["fired"]]
            if index < 1 or index > len(active):
                return f"❌ Напоминание #{index} не найдено."
            rid = active[index - 1]["id"]
            self._reminders = [r for r in self._reminders if r["id"] != rid]
        return f"✅ Напоминание #{index} удалено."

    # ──────────────────────────────────────────────────────────────
    # ОТЧЁТ
    # ──────────────────────────────────────────────────────────────

    def report(self) -> str:
        """Вернуть полный отчёт о состоянии агента."""
        status = "🟢 Работает" if self._running else "⚫ Простаивает"
        elapsed = ""
        if self._report["started_at"]:
            secs = int(time.time() - self._report["started_at"])
            elapsed = f" | Время: {secs}с"

        lines = [
            "🤖 АГЕНТ АРГОСА",
            f"  Статус: {status}{elapsed}",
            f"  Задач выполнено: {self._report['tasks_done']}",
            f"  Ошибок: {self._report['errors']}",
            f"  Без подтверждения: {self._report['unverified']}",
            f"  Последняя задача: {self._report['last_task'] or '—'}",
        ]

        if self._chain_results:
            lines.append("  Результаты (последние 5):")
            for r in self._chain_results[-5:]:
                icon = "✅" if r["ok"] else ("❔" if r.get("status") == "unverified" else "❌")
                lines.append(f"    {icon} {r['task'][:40]} → {r['result'][:60]}")

        with self._reminder_lock:
            active_rem = sum(1 for r in self._reminders if not r["fired"])
        lines.append(f"  Активных напоминаний: {active_rem}")

        return "\n".join(lines)

    def status(self) -> str:
        """Краткий статус для встраивания в другие отчёты."""
        s = "работает" if self._running else "простаивает"
        return (
            f"Агент: {s} | "
            f"задач: {self._report['tasks_done']} | "
            f"ошибок: {self._report['errors']}"
        )
