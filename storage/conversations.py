"""Committed conversation pairs are the source of chat history."""
import time

from sqlalchemy import select

from storage import db
from storage.models import Conversation
from storage.submissions import thread_transaction


def save_round(thread_id: str, question: str, answer: str, intent: str) -> None:
    from agents.budget import current_budget
    budget = current_budget.get()
    if budget is not None:
        budget.check()
    with thread_transaction(thread_id) as session:
        now = time.time()
        for role, content in (("user", question), ("assistant", answer)):
            session.add(Conversation(thread_id=thread_id, role=role, content=content,
                                     intent=intent if role == "assistant" else None,
                                     created_at=now))
            session.flush()
        if budget is not None:
            budget.check()


def load_history(thread_id: str, limit: int = 50) -> list[dict]:
    with db.session() as session:
        rows = session.scalars(select(Conversation).where(Conversation.thread_id == thread_id)
                               .order_by(Conversation.id.desc()).limit(limit)).all()
        return [{"role": row.role, "content": row.content, "intent": row.intent,
                 "created_at": row.created_at} for row in reversed(rows)]
