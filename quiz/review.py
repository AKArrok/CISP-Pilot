"""题目级 FSRS 复习调度（py-fsrs）：每道答过的题一张卡，答错=Again、答对=Good。

域级遗忘曲线（quiz/stats.py）保留做域级展示；本模块把复习调度下沉到题级，
weak 抽题时优先消耗到期卡。参考 open-spaced-repetition/py-fsrs 的做法：
DSR 模型（难度 D / 稳定性 S / 可提取性 R）按每次作答更新，替代固定半衰期。
当前作答只有对错两态：correct→Good、wrong→Again（未采集作答时长，
之后可按时长细分 Hard/Good/Easy）。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fsrs import Card, Rating, Scheduler, State
from sqlalchemy import func, select

from storage import db
from storage.models import LearnerReviewCard

logger = logging.getLogger(__name__)

# 默认参数已是大规模基准拟合结果；积累足够本地复习数据后可换个人化拟合
_scheduler = Scheduler()

RATING_BY_CORRECT = {True: Rating.Good, False: Rating.Again}


def _to_dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _row_to_card(row: LearnerReviewCard) -> Card:
    return Card(
        state=State(row.state), step=row.step,
        stability=row.stability, difficulty=row.difficulty,
        due=_to_dt(row.due),
        last_review=_to_dt(row.last_review) if row.last_review else None,
    )


def update_card(thread_id: str, question_id: str, domain: str | None, correct: bool,
                now: float | None = None) -> None:
    """作答后更新（或新建）该题的 FSRS 复习卡。"""
    now = now if now is not None else time.time()
    rating = RATING_BY_CORRECT[bool(correct)]
    with db.session() as s:
        row = s.get(LearnerReviewCard, (thread_id, question_id))
        card = _row_to_card(row) if row else Card()
        try:
            card, _log = _scheduler.review_card(card, rating, _to_dt(now))
        except Exception:
            logger.exception("FSRS 调度失败（question_id=%s）", question_id)
            return
        if row is None:
            row = LearnerReviewCard(
                thread_id=thread_id, question_id=question_id, domain=domain,
                state=0, step=None, stability=0.0, difficulty=0.0,
                due=now, last_review=None, reps=0, lapses=0,
            )
            s.add(row)
        row.domain = domain or row.domain
        row.state = int(card.state)
        row.step = card.step
        row.stability = float(card.stability)
        row.difficulty = float(card.difficulty)
        row.due = card.due.timestamp()
        row.last_review = card.last_review.timestamp() if card.last_review else now
        row.reps += 1
        row.lapses += 0 if correct else 1
        s.commit()


def due_rows(
    thread_id: str, domain: str | None = None, limit: int = 50,
) -> list[LearnerReviewCard]:
    """到期复习卡（due <= now），按最久未复习优先。"""
    now = time.time()
    with db.session() as s:
        stmt = (select(LearnerReviewCard)
                .where(LearnerReviewCard.thread_id == thread_id,
                       LearnerReviewCard.due <= now)
                .order_by(LearnerReviewCard.due.asc())
                .limit(limit))
        if domain:
            stmt = stmt.where(LearnerReviewCard.domain == domain)
        return list(s.scalars(stmt).all())


def due_count(thread_id: str, domain: str | None = None) -> int:
    with db.session() as s:
        stmt = select(func.count()).select_from(LearnerReviewCard).where(
            LearnerReviewCard.thread_id == thread_id,
            LearnerReviewCard.due <= time.time(),
        )
        if domain:
            stmt = stmt.where(LearnerReviewCard.domain == domain)
        return s.scalar(stmt) or 0


def card_count(thread_id: str) -> int:
    with db.session() as s:
        return s.scalar(
            select(func.count()).select_from(LearnerReviewCard).where(
                LearnerReviewCard.thread_id == thread_id
            )
        ) or 0
