"""Synthetic-only stage timings; never record SQL, arguments, tokens or IDs."""
from contextvars import ContextVar
from functools import wraps
import json
from pathlib import Path
from threading import Lock
import time

from sqlalchemy import event
from sqlalchemy.engine import Engine

current = ContextVar("quiz_stage_profile", default=None)
file_lock = Lock()


def install(output):
    import quiz
    from quiz import grader
    from storage import submissions

    def timed(target, attribute, label):
        original = getattr(target, attribute)

        @wraps(original)
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                row = current.get()
                if row is not None:
                    row["stages"].append({"stage": label, "seconds": time.perf_counter() - started})
        setattr(target, attribute, wrapper)

    timed(grader, "related_chunks", "related_retrieval")
    timed(quiz, "_apply_answer", "apply_answer")
    timed(submissions, "lock_thread", "thread_write_lock")

    @event.listens_for(Engine, "before_cursor_execute")
    def before(connection, cursor, statement, parameters, context, executemany):
        if current.get() is not None:
            context._quiz_profile_started = time.perf_counter()

    @event.listens_for(Engine, "after_cursor_execute")
    def after(connection, cursor, statement, parameters, context, executemany):
        row = current.get()
        if row is not None and hasattr(context, "_quiz_profile_started"):
            # Whitelist just the SQL operation, never retain its body/parameters.
            verb = statement.lstrip().split(None, 1)[0].upper()
            row["sql"].append({"verb": verb if verb in {"SELECT", "INSERT", "UPDATE", "DELETE", "BEGIN", "PRAGMA"} else "OTHER",
                               "seconds": time.perf_counter() - context._quiz_profile_started})

    original = quiz.submit_answer

    @wraps(original)
    def submit(*args, **kwargs):
        row = {"stages": [], "sql": [], "status": "failed"}
        token = current.set(row)
        started = time.perf_counter()
        try:
            result = original(*args, **kwargs)
            row["status"] = "completed"
            return result
        finally:
            row["total_seconds"] = time.perf_counter() - started
            current.reset(token)
            with file_lock, (Path(output) / "quiz-stages.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row) + "\n")
    quiz.submit_answer = submit
