"""题目级 FSRS 复习调度测试 — 独立临时 SQLite，不碰真实数据。"""
import sys
import time

import pytest
from sqlalchemy import create_engine

sys.path.insert(0, ".")
THREAD = "review-test"


@pytest.fixture()
def tmp_db(tmp_path):
    from storage import db
    from storage.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db.override_engine(engine)
    yield engine
    engine.dispose()


def test_wrong_answer_creates_due_card(tmp_db):
    """答错建卡：Again 后短时间内到期，lapses 记 1。"""
    import quiz.review as review

    now = time.time() - 3600  # 一小时前答错
    review.update_card(THREAD, "q1", "信息安全管理", correct=False, now=now)
    rows = review.due_rows(THREAD)
    assert [r.question_id for r in rows] == ["q1"]
    card = rows[0]
    assert card.lapses == 1 and card.reps == 1
    assert card.stability > 0


def test_correct_answer_pushes_due_forward(tmp_db):
    """答对（Good）后到期时间相对上次作答推后（新卡先过学习步骤，间隔短是正常的）。"""
    import quiz.review as review

    now = time.time()
    review.update_card(THREAD, "q1", "信息安全管理", correct=True, now=now - 86400)
    from storage import db
    from storage.models import LearnerReviewCard
    with db.session() as s:
        row = s.get(LearnerReviewCard, (THREAD, "q1"))
    assert row.reps == 1 and row.lapses == 0
    assert row.stability > 0
    assert row.due > row.last_review        # 间隔为正，且比 Again 的分钟级更长
    assert row.due - row.last_review > 60   # 均为 epoch 秒


def test_stability_grows_across_reviews(tmp_db):
    """同一张卡多次答对，稳定性单调增长（间隔越拉越长）。"""
    import quiz.review as review
    from sqlalchemy import select
    from storage import db
    from storage.models import LearnerReviewCard

    base = time.time() - 86400 * 30
    for at in [base, base + 86400, base + 86400 * 5, base + 86400 * 20]:
        review.update_card(THREAD, "q1", "信息安全管理", correct=True, now=at)
    with db.session() as s:
        row = s.scalars(select(LearnerReviewCard).where(
            LearnerReviewCard.thread_id == THREAD,
            LearnerReviewCard.question_id == "q1",
        )).one()
    assert row.reps == 4
    assert row.stability > 2.3              # 高于首次 Good 的初始稳定性


def test_due_rows_orders_most_overdue_first(tmp_db):
    """到期队列按最久未复习优先。"""
    import quiz.review as review

    now = time.time()
    review.update_card(THREAD, "q_new", "信息安全管理", correct=False, now=now - 3600)
    review.update_card(THREAD, "q_old", "计算环境安全", correct=False, now=now - 86400 * 3)
    due_ids = [r.question_id for r in review.due_rows(THREAD)]
    assert due_ids.index("q_old") < due_ids.index("q_new")


def test_due_count_with_domain_filter(tmp_db):
    import quiz.review as review

    now = time.time()
    review.update_card(THREAD, "q1", "信息安全管理", correct=False, now=now - 86400)
    review.update_card(THREAD, "q2", "软件安全开发", correct=False, now=now - 86400)
    assert review.due_count(THREAD) == 2
    assert review.due_count(THREAD, domain="信息安全管理") == 1


def test_review_cards_are_isolated_by_thread(tmp_db):
    import quiz.review as review

    now = time.time() - 86400
    review.update_card("learner-a", "same-question", "信息安全管理", False, now=now)
    assert review.due_count("learner-a") == 1
    assert review.due_count("learner-b") == 0
