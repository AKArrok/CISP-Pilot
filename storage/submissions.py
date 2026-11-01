"""Durable receipts and a transaction that serializes each learner thread."""
from contextlib import contextmanager

from sqlalchemy import select, text

from storage import db
from storage.models import Thread


class SubmissionConflict(ValueError):
    """A submission key was already used with a different answer payload."""


@contextmanager
def answer_transaction(thread_id: str):
    with db.session() as session:
        try:
            lock_thread(session, thread_id)
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


def lock_thread(session, thread_id: str) -> None:
    """Acquire the common learner write anchor before reading mutable state."""
    transaction = session.get_transaction()
    if session.info.get("thread_lock_transaction") is not transaction:
        session.info["locked_threads"] = set()
    locked = session.info.setdefault("locked_threads", set())
    if thread_id in locked:
        return
    if session.get_bind().dialect.name == "sqlite":
        # Legacy internal SQLite fixtures may have no Thread row. The database
        # writer lock still serializes all thread mutations before any read.
        if session.in_transaction():
            raise RuntimeError("Thread lock must precede SQLite transaction reads")
        session.execute(text("BEGIN IMMEDIATE"))
    else:
        thread = session.scalars(
            select(Thread).where(Thread.id == thread_id).with_for_update()
        ).first()
        if thread is None:
            raise ValueError("Thread transaction requires an existing thread")
    locked.add(thread_id)
    session.info["thread_lock_transaction"] = session.get_transaction()


@contextmanager
def thread_transaction(thread_id: str, *, session=None):
    """Reuse a caller transaction or own the commit for a learner mutation."""
    if session is not None:
        lock_thread(session, thread_id)
        yield session
    else:
        with answer_transaction(thread_id) as owned:
            yield owned
