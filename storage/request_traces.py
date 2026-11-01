"""Persist bounded observations and authorize reads before loading payloads."""
import json
import math
import time
from uuid import UUID

from sqlalchemy import select, update as sql_update

from storage import db
from storage.models import RequestTrace, Thread

MAX_TRACE_BYTES = 64_000
STATUSES = {"running", "completed", "failed", "cancelled"}


def recovery_state(record: dict) -> dict:
    """Read-only observation assessment, never a liveness or rollback verdict."""
    if record["status"] != "running":
        return {"state": "recorded_terminal", "reason": "terminal_observed",
                "next_action": "refresh_history", "execution_stopped": None}
    deadline = record["trace"].get("observation_deadline_at")
    valid = (isinstance(deadline, (int, float)) and not isinstance(deadline, bool) and deadline > 0)
    try:
        valid = valid and math.isfinite(deadline)
    except OverflowError:
        valid = False
    now = time.time()
    if not valid:
        reason = "deadline_unavailable"
    elif now < record["created_at"]:
        reason = "clock_before_record"
    elif now >= deadline:
        reason = "observation_deadline_elapsed"
    else:
        return {"state": "awaiting_observation", "reason": "within_observation_window",
                "next_action": "wait_or_refresh_history", "execution_stopped": None}
    return {"state": "unconfirmed", "reason": reason,
            "next_action": "refresh_history_before_retry", "execution_stopped": None}


def _request_id(value: str) -> str:
    canonical = str(UUID(value))
    if canonical != value:
        raise ValueError("request_id must be a canonical UUID")
    return canonical


def _payload(trace: dict, status: str) -> str:
    if status not in STATUSES:
        raise ValueError("invalid request trace status")
    if not isinstance(trace, dict):
        raise ValueError("request trace must be an object")
    payload = json.dumps(trace, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(payload.encode("utf-8")) > MAX_TRACE_BYTES:
        raise ValueError("request trace exceeds byte limit")
    return payload


def _record(row: RequestTrace) -> dict:
    return {
        "request_id": row.request_id,
        "thread_id": row.thread_id,
        "status": row.status,
        "trace": json.loads(row.trace_json),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def create(request_id: str, thread_id: str, trace: dict, *, status: str = "running") -> dict:
    request_id = _request_id(request_id)
    payload = _payload(trace, status)
    with db.session() as session:
        if session.get(Thread, thread_id) is None:
            raise ValueError("unknown thread")
        now = time.time()
        row = RequestTrace(request_id=request_id, thread_id=thread_id, status=status,
                           trace_json=payload, created_at=now, updated_at=now)
        session.add(row)
        session.commit()
        return _record(row)


def update(request_id: str, trace: dict, *, status: str) -> bool:
    request_id = _request_id(request_id)
    payload = _payload(trace, status)
    with db.session() as session:
        result = session.execute(sql_update(RequestTrace)
                                 .where(RequestTrace.request_id == request_id)
                                 .values(status=status, trace_json=payload, updated_at=time.time()))
        session.commit()
        return result.rowcount == 1


def get(request_id: str, *, owner_user_id: str) -> dict | None:
    with db.session() as session:
        row = session.scalar(
            select(RequestTrace).join(Thread, RequestTrace.thread_id == Thread.id).where(
                RequestTrace.request_id == request_id,
                Thread.owner_user_id == owner_user_id,
            )
        )
        return _record(row) if row is not None else None
