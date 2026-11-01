"""答题记录与 AI 题存储 — 使用 storage ORM（MySQL 或 SQLite 显式选择）。

AI 生成题与真题统一存 questions 表（source='AI生成'）；
历史函数签名保持不变，调用方（quiz/__init__、grader、selector）无需感知后端。
"""
from __future__ import annotations

import json
import time

from sqlalchemy import select

from storage import cache, db
from storage.models import LearnerAttempt, Question
from storage.submissions import thread_transaction

STATS_CACHE_KEY = "stats:summary"


def stats_cache_key(thread_id: str) -> str:
    return f"{STATS_CACHE_KEY}:{thread_id}"


def init_db() -> None:
    db.create_all()


# ── AI 生成题（写 questions 表）─────────────────────────────────────────

def insert_ai_question(stem: str, options: dict, answer: str,
                       analysis: str, domain: str,
                       needs_review: bool = False) -> str:
    qid = f"ai_{int(time.time() * 1000)}"
    with db.session() as s:
        s.add(Question(id=qid, source="AI生成", num=0, stem=stem,
                       options=json.dumps(options, ensure_ascii=False),
                       answer=answer, analysis=analysis, domain=domain,
                       needs_review=needs_review))
        s.commit()
    return qid


def existing_ai_stems() -> set[str]:
    with db.session() as s:
        return set(s.scalars(select(Question.stem).where(Question.source == "AI生成")))


# ── 答题记录 ────────────────────────────────────────────────────────────

def record_attempt(thread_id: str, question_id: str, question_source: str,
                   domain: str | None, stem: str, choice: str, correct: bool,
                   attribution_status: str = "confirmed",
                   profile_applied: bool = True,
                   calibration_id: int | None = None,
                   attribution_point_id: str | None = None,
                   *, session=None) -> int | None:
    with thread_transaction(thread_id, session=session) as s:
        row = LearnerAttempt(
            thread_id=thread_id, question_id=question_id,
            question_source=question_source, domain=domain, stem=stem[:500],
            choice=choice, correct=correct, answered_at=time.time(),
            attribution_status=attribution_status,
            profile_applied=profile_applied,
            calibration_id=calibration_id,
            attribution_point_id=attribution_point_id,
        )
        s.add(row)
        s.flush()
        attempt_id = row.id
    if session is None:
        cache.delete_keys(stats_cache_key(thread_id))
    return attempt_id


def attempted_ids(thread_id: str, correct_only: bool = False) -> set[str]:
    stmt = select(LearnerAttempt.question_id).where(
        LearnerAttempt.thread_id == thread_id
    ).distinct()
    if correct_only:
        stmt = stmt.where(LearnerAttempt.correct.is_(True))
    with db.session() as s:
        return set(s.scalars(stmt))
