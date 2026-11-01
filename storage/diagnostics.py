"""Read diagnostic summaries for an already authorized learner thread."""
import json

from sqlalchemy import select

from storage import db
from storage.models import DiagnosticSession


def list_diagnostics(thread_id: str, limit: int = 20, offset: int = 0) -> dict:
    with db.session() as session:
        rows = session.scalars(
            select(DiagnosticSession)
            .where(DiagnosticSession.thread_id == thread_id)
            .order_by(DiagnosticSession.created_at.desc(), DiagnosticSession.id.desc())
            .offset(offset).limit(limit + 1)
        ).all()
        items = [{
            "session_id": row.id,
            "status": row.status,
            "selected_domains": json.loads(row.selected_domains),
            "progress": {"answered": len(json.loads(row.responses)),
                         "max_questions": row.max_questions},
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        } for row in rows[:limit]]
    has_more = len(rows) > limit
    return {"items": items, "has_more": has_more,
            "next_offset": offset + limit if has_more else None}
