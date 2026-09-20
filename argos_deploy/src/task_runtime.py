from __future__ import annotations

import os
import queue
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from src.execution_outcome import classify_execution
from src.task_control import ExecutionControl, TaskCancelled, request_scope


TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})


class TaskRunner:
    def __init__(self, path, execute, *, max_pending=64, autostart=True):
        if not isinstance(max_pending, int) or not 1 <= max_pending <= 256:
            raise ValueError("max_pending must be between 1 and 256")
        self.path = Path(path)
        self.execute = execute
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._queue = queue.Queue(maxsize=max_pending)
        self._controls = {}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        created = not self.path.exists()
        if created:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        with self._db() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "argos_tasks" not in tables:
                raise ValueError("Task database contains unrelated data")
            db.execute("""CREATE TABLE IF NOT EXISTS argos_tasks (
                id TEXT PRIMARY KEY, text TEXT NOT NULL, status TEXT NOT NULL,
                answer TEXT NOT NULL DEFAULT '', partial_answer TEXT NOT NULL DEFAULT '',
                execution_status TEXT NOT NULL DEFAULT 'unverified',
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1
            )""")
            db.execute("""UPDATE argos_tasks SET status='interrupted',
                answer='Выполнение прервано перезапуском сервиса. Проверьте результат перед повтором.',
                updated_at=?, version=version+1 WHERE status IN ('queued','running','cancelling')""", (time.time(),))
        self._thread = None
        if autostart:
            self._thread = threading.Thread(target=self._worker, name="argos-task-worker", daemon=True)
            self._thread.start()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _record(row):
        if row is None:
            return None
        result = dict(row)
        result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    def get(self, task_id):
        with self._lock, self._db() as db:
            return self._record(db.execute("SELECT * FROM argos_tasks WHERE id=?", (task_id,)).fetchone())

    def list(self, limit=50):
        limit = max(1, min(int(limit), 200))
        with self._lock, self._db() as db:
            return [self._record(row) for row in db.execute(
                "SELECT * FROM argos_tasks ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            )]

    def _update(self, task_id, **values):
        values["updated_at"] = time.time()
        with self._lock, self._db() as db:
            db.execute(
                "UPDATE argos_tasks SET " + ",".join(f"{key}=?" for key in values) + ",version=version+1 WHERE id=?",
                (*values.values(), task_id),
            )

    def submit(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 24000:
            raise ValueError("Введите запрос длиной от 1 до 24000 символов")
        with self._lock:
            if self._stopping.is_set():
                raise RuntimeError("Сервис задач останавливается")
            if self._queue.full():
                raise RuntimeError("Очередь заполнена. Дождитесь завершения текущих задач")
            task_id = uuid.uuid4().hex
            now = time.time()
            with self._db() as db:
                db.execute("INSERT INTO argos_tasks(id,text,status,created_at,updated_at) VALUES(?,?,'queued',?,?)", (task_id, text, now, now))
                db.execute("""DELETE FROM argos_tasks WHERE status IN ('completed','failed','cancelled','interrupted')
                    AND id NOT IN (SELECT id FROM argos_tasks ORDER BY created_at DESC LIMIT 1000)""")
            self._queue.put_nowait(task_id)
            return self.get(task_id)

    def cancel(self, task_id):
        with self._lock:
            task = self.get(task_id)
            if task is None or task["status"] in TERMINAL:
                return task
            if task["status"] == "queued":
                self._update(task_id, status="cancelled", cancel_requested=1, answer="Задача отменена до выполнения.")
            else:
                self._update(task_id, status="cancelling", cancel_requested=1)
                self._controls[task_id].set()
            return self.get(task_id)

    def _worker(self):
        while not self._stopping.is_set():
            try:
                task_id = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                with self._lock:
                    task = self.get(task_id)
                    if task is None or task["status"] != "queued":
                        continue
                    cancel_event = threading.Event()
                    self._controls[task_id] = cancel_event
                    self._update(task_id, status="running")

                def on_chunk(text):
                    with self._lock:
                        partial = self.get(task_id)["partial_answer"] + text
                        if len(partial) > 65536:
                            raise ValueError("Ответ превысил допустимый размер")
                        self._update(task_id, partial_answer=partial)

                try:
                    with request_scope(ExecutionControl(cancel_event, on_chunk)):
                        result = self.execute(task["text"])
                    answer, outcome = classify_execution(result)
                    if len(answer) > 65536:
                        answer, outcome = "Ошибка: ответ превысил допустимый размер и не был принят.", "failed"
                    self._update(task_id, status="failed" if outcome == "failed" else "completed",
                                 answer=answer, execution_status=outcome)
                except TaskCancelled:
                    self._update(task_id, status="cancelled", answer="Обработка остановлена. Уже выполненные действия не отменяются.")
                except Exception:
                    self._update(task_id, status="failed", answer="Ошибка выполнения задачи. Подробности доступны в локальном журнале.", execution_status="failed")
                    import logging
                    logging.getLogger(__name__).exception("Task execution failed: %s", task_id)
                finally:
                    with self._lock:
                        self._controls.pop(task_id, None)
            finally:
                self._queue.task_done()

    def close(self, timeout=2):
        with self._lock:
            self._stopping.set()
            with self._db() as db:
                active = db.execute("SELECT id FROM argos_tasks WHERE status IN ('queued','running','cancelling')").fetchall()
            for task in active:
                self.cancel(task["id"])
        if self._thread is not None:
            self._thread.join(timeout=timeout)
