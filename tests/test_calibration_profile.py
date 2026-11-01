"""probable/unverified 不污染画像，校准需两条独立一致证据。"""

import pytest
from sqlalchemy import create_engine


@pytest.fixture()
def calibration_db(tmp_path, monkeypatch):
    from storage import db
    from storage.models import Base, KnowledgePoint, QuestionAttribution

    engine = create_engine(
        f"sqlite:///{tmp_path / 'calibration.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db.override_engine(engine)
    points = [
        KnowledgePoint(
            id="kp-risk",
            domain="信息安全管理",
            name="风险处理",
            status="active",
            evidence_count=2,
            question_count=2,
        )
    ]
    questions = [
        {
            "id": "trigger",
            "source": "test",
            "domain": "信息安全管理",
            "stem": "触发题",
            "options": {"A": "错", "B": "对"},
            "answer": "B",
            "analysis": "分析",
        },
        {
            "id": "cal-1",
            "source": "test",
            "domain": "信息安全管理",
            "stem": "可靠校准题一",
            "options": {"A": "错", "B": "对"},
            "answer": "B",
            "analysis": "分析",
        },
        {
            "id": "cal-2",
            "source": "test",
            "domain": "信息安全管理",
            "stem": "可靠校准题二",
            "options": {"A": "错", "B": "对"},
            "answer": "B",
            "analysis": "分析",
        },
    ]
    with db.session() as session:
        session.add_all(points)
        for question_id, status in (
            ("trigger", "probable"),
            ("cal-1", "confirmed"),
            ("cal-2", "confirmed"),
        ):
            session.add(
                QuestionAttribution(
                    question_id=question_id,
                    candidate_point_id="kp-risk",
                    candidate_domain="信息安全管理",
                    attribution_status=status,
                    agreement_count=2,
                    evidence_json='{"routes": {}}',
                    reason="test",
                    classifier_version="test",
                    created_at=1,
                    updated_at=1,
                )
            )
        session.commit()

    class FakeBank:
        def all(self, domain=None):
            return [q for q in questions if domain is None or q["domain"] == domain]

        def available(self, domain=None, *, thread_id="default"):
            return self.all(domain)

        def get_by_id(self, question_id):
            return next((q for q in questions if q["id"] == question_id), None)

    import quiz

    monkeypatch.setattr(quiz.QuestionBank, "get", classmethod(lambda cls: FakeBank()))
    monkeypatch.setattr("quiz.grader.related_chunks", lambda question: [])
    monkeypatch.setattr("config.CALIBRATION_MAX_INSERTIONS", 3)
    monkeypatch.setattr("config.CALIBRATION_MIN_ATTEMPT_GAP", 1)
    yield engine
    engine.dispose()


def test_probable_wrong_answer_waits_for_two_consistent_confirmed_questions(calibration_db):
    import quiz
    from storage import repos

    thread = "calibration-thread"
    first = quiz.submit_answer("trigger", "A", thread_id=thread)
    assert first["correct"] is False
    assert repos.learner_knowledge_states(thread) == []

    q1 = quiz.get_next_question(thread_id=thread)
    assert q1["id"] == "cal-1"
    assert "calibration_reason" in q1
    quiz.submit_answer("cal-1", "A", thread_id=thread)
    assert repos.learner_knowledge_states(thread) == []

    q2 = quiz.get_next_question(thread_id=thread)
    assert q2["id"] == "cal-2"
    result = quiz.submit_answer("cal-2", "A", thread_id=thread)
    assert result["calibration_progress"]["status"] == "confirmed"
    state = repos.learner_knowledge_states(thread, ["kp-risk"])[0]
    assert state["attempts"] == 2 and state["correct"] == 0
    assert repos.pending_calibrations(thread) == []


def test_duplicate_calibration_submission_applies_only_first_evidence_attempt(
    calibration_db,
):
    import quiz
    from sqlalchemy import select
    from storage import db, repos
    from storage.models import LearnerAttempt

    thread = "duplicate-calibration-thread"
    quiz.submit_answer("trigger", "A", thread_id=thread)
    first_question = quiz.get_next_question(thread_id=thread)
    assert first_question["id"] == "cal-1"
    quiz.submit_answer("cal-1", "A", thread_id=thread)
    calibration_id = repos.pending_calibrations(thread)[0]["id"]
    public_calibration = repos.pending_calibrations(thread)[0]
    public_cal_1 = next(
        item for item in public_calibration["evidence"] if item["question_id"] == "cal-1"
    )
    assert "attempt_id" not in public_cal_1
    # A retried/conflicting request remains raw audit evidence, but must not become
    # a second profile event. The calibration evidence keeps the first answer.
    quiz.submit_answer("cal-1", "B", thread_id=thread)
    assert quiz.get_next_question(thread_id=thread)["id"] == "cal-2"
    result = quiz.submit_answer("cal-2", "A", thread_id=thread)

    assert result["calibration_progress"]["status"] == "confirmed"
    state = repos.learner_knowledge_states(thread, ["kp-risk"])[0]
    assert state["attempts"] == 2 and state["correct"] == 0
    assert repos.profile_attempt_count(thread) == 2
    with db.session() as session:
        cal_1_attempts = list(
            session.scalars(
                select(LearnerAttempt)
                .where(
                    LearnerAttempt.thread_id == thread,
                    LearnerAttempt.question_id == "cal-1",
                )
                .order_by(LearnerAttempt.id)
            )
        )
    assert [row.profile_applied for row in cal_1_attempts] == [True, False]
    assert [row.correct for row in cal_1_attempts] == [False, True]
    with db.session() as session:
        from storage.models import CalibrationHypothesis

        calibration = session.get(CalibrationHypothesis, calibration_id)
        import json

        evidence = json.loads(calibration.evidence_json)
    cal_1_evidence = next(item for item in evidence if item["question_id"] == "cal-1")
    applied_cal_1 = next(row for row in cal_1_attempts if row.profile_applied)
    assert cal_1_evidence["attempt_id"] == applied_cal_1.id

    # Retried confirmation must be a no-op as well.
    resolved = repos.confirm_calibration_evidence(calibration_id)
    assert resolved is not None and resolved["_transitioned"] is False
    state = repos.learner_knowledge_states(thread, ["kp-risk"])[0]
    assert state["attempts"] == 2 and repos.profile_attempt_count(thread) == 2


def test_calibration_confirmation_rolls_back_as_one_transaction(
    calibration_db,
    monkeypatch,
):
    import quiz
    from sqlalchemy import select
    from storage import db, repos
    from storage.models import LearnerAttempt

    thread = "atomic-calibration-thread"
    quiz.submit_answer("trigger", "A", thread_id=thread)
    quiz.get_next_question(thread_id=thread)
    quiz.submit_answer("cal-1", "A", thread_id=thread)
    quiz.get_next_question(thread_id=thread)

    original_update = repos._increment_learner_knowledge_state

    def fail_profile_update(*args, **kwargs):
        raise RuntimeError("injected profile write failure")

    monkeypatch.setattr(repos, "_increment_learner_knowledge_state", fail_profile_update)
    with pytest.raises(RuntimeError, match="injected profile write failure"):
        quiz.submit_answer("cal-2", "A", thread_id=thread)

    assert repos.calibration_status_counts(thread) == {
        "pending": 1,
        "confirmed": 0,
        "retracted": 0,
        "inconclusive": 0,
    }
    assert repos.profile_attempt_count(thread) == 0
    assert repos.learner_knowledge_states(thread) == []
    with db.session() as session:
        applied = list(
            session.scalars(
                select(LearnerAttempt.profile_applied).where(
                    LearnerAttempt.thread_id == thread,
                    LearnerAttempt.calibration_id.is_not(None),
                )
            )
        )
    assert applied and not any(applied)

    monkeypatch.setattr(repos, "_increment_learner_knowledge_state", original_update)
    result = quiz.submit_answer("cal-2", "A", thread_id=thread)
    assert result["calibration_progress"]["status"] == "confirmed"
    assert repos.profile_attempt_count(thread) == 2
    state = repos.learner_knowledge_states(thread, ["kp-risk"])[0]
    assert state["attempts"] == 2 and state["correct"] == 0


def test_review_card_failure_rolls_back_entire_confirmation(
    calibration_db,
    monkeypatch,
):
    import quiz
    from storage import repos

    thread = "review-card-failure-thread"
    quiz.submit_answer("trigger", "A", thread_id=thread)
    quiz.get_next_question(thread_id=thread)
    quiz.submit_answer("cal-1", "A", thread_id=thread)
    quiz.get_next_question(thread_id=thread)
    before = repos.pending_calibrations(thread)[0]
    original_update = quiz.review.update_card

    def fail_review_update(*args, **kwargs):
        raise RuntimeError("injected review-card failure")

    monkeypatch.setattr("quiz.review.update_card", fail_review_update)
    with pytest.raises(RuntimeError, match="injected review-card failure"):
        quiz.submit_answer("cal-2", "A", thread_id=thread, submission_id="failed-confirmation")
    assert repos.pending_calibrations(thread)[0] == before
    assert repos.profile_attempt_count(thread) == 0
    assert repos.learner_knowledge_states(thread, ["kp-risk"]) == []
    monkeypatch.setattr("quiz.review.update_card", original_update)
    result = quiz.submit_answer("cal-2", "A", thread_id=thread, submission_id="failed-confirmation")
    assert result["calibration_progress"]["status"] == "confirmed"
    assert repos.profile_attempt_count(thread) == 2
    state = repos.learner_knowledge_states(thread, ["kp-risk"])[0]
    assert state["attempts"] == 2 and state["correct"] == 0


def test_create_calibration_reuses_pending_for_same_thread_and_point(calibration_db):
    from storage import repos

    first = repos.create_calibration_hypothesis(
        "reuse-thread",
        "kp-risk",
        "trigger",
        False,
        "first",
    )
    second = repos.create_calibration_hypothesis(
        "reuse-thread",
        "kp-risk",
        "another-trigger",
        False,
        "retry",
    )

    assert second["id"] == first["id"]
    assert second["trigger_question_id"] == "trigger"
    assert len(repos.pending_calibrations("reuse-thread")) == 1


def test_concurrent_calibration_creation_returns_one_pending(calibration_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from storage import repos

    barrier = Barrier(2)

    def create(trigger):
        barrier.wait(timeout=5)
        return repos.create_calibration_hypothesis(
            "concurrent-reuse-thread",
            "kp-risk",
            trigger,
            False,
            trigger,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, ["trigger-a", "trigger-b"]))

    assert len({result["id"] for result in results}) == 1
    pending = repos.pending_calibrations("concurrent-reuse-thread")
    assert len(pending) == 1
    assert pending[0]["id"] == results[0]["id"]


def test_concurrent_calibration_presentations_reuse_one_outstanding_question(
    calibration_db,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import quiz
    from storage import repos

    thread = "concurrent-presentation-thread"
    quiz.submit_answer("trigger", "A", thread_id=thread)
    barrier = Barrier(2)

    def get_question():
        barrier.wait(timeout=5)
        return quiz.get_next_question(thread_id=thread)

    with ThreadPoolExecutor(max_workers=2) as pool:
        questions = list(pool.map(lambda _: get_question(), range(2)))

    assert [question["id"] for question in questions] == ["cal-1", "cal-1"]
    pending = repos.pending_calibrations(thread)
    assert len(pending) == 1
    assert pending[0]["presentation_count"] == 1
    assert "attempt_id" not in pending[0]["evidence"][0]
    assert [item["question_id"] for item in pending[0]["evidence"]] == ["cal-1"]
    assert pending[0]["evidence"][0]["correct"] is None


def test_concurrent_conflicting_answers_keep_exact_first_attempt(calibration_db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import quiz
    from sqlalchemy import select
    from storage import db, repos
    from storage.models import CalibrationHypothesis, LearnerAttempt

    thread = "concurrent-answer-thread"
    quiz.submit_answer("trigger", "A", thread_id=thread)
    assert quiz.get_next_question(thread_id=thread)["id"] == "cal-1"
    calibration_id = repos.pending_calibrations(thread)[0]["id"]
    barrier = Barrier(2)
    def submit(choice):
        barrier.wait(timeout=5)
        return quiz.submit_answer("cal-1", choice, thread_id=thread)

    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(
            pool.map(
                submit,
                ["A", "B"],
            )
        )

    assert {answer["correct"] for answer in answers} == {False, True}
    with db.session() as session:
        hypothesis = session.get(CalibrationHypothesis, calibration_id)
        attempts = list(
            session.scalars(
                select(LearnerAttempt)
                .where(
                    LearnerAttempt.thread_id == thread,
                    LearnerAttempt.calibration_id == calibration_id,
                    LearnerAttempt.question_id == "cal-1",
                )
                .order_by(LearnerAttempt.id)
            )
        )
    import json

    evidence = json.loads(hypothesis.evidence_json)
    cal_1_evidence = next(item for item in evidence if item["question_id"] == "cal-1")
    winning_attempt = next(row for row in attempts if row.id == cal_1_evidence["attempt_id"])
    assert winning_attempt.correct is cal_1_evidence["correct"]
    assert len(attempts) == 2
    assert not any(row.profile_applied for row in attempts)

    assert quiz.get_next_question(thread_id=thread)["id"] == "cal-2"
    winning_attempt_id = winning_attempt.id
    choice = "B" if cal_1_evidence["correct"] else "A"
    result = quiz.submit_answer("cal-2", choice, thread_id=thread)
    assert result["calibration_progress"]["status"] == "confirmed"
    with db.session() as session:
        attempts_after = list(
            session.scalars(
                select(LearnerAttempt).where(
                    LearnerAttempt.thread_id == thread,
                    LearnerAttempt.calibration_id == calibration_id,
                    LearnerAttempt.question_id == "cal-1",
                )
            )
        )
    assert (
        next(row for row in attempts_after if row.id == winning_attempt_id).profile_applied is True
    )
    assert sum(row.profile_applied for row in attempts_after) == 1
    assert repos.learner_knowledge_states(thread, ["kp-risk"])[0]["attempts"] == 2


def test_contradictory_confirmed_evidence_does_not_upgrade(calibration_db):
    import quiz
    from storage import repos

    thread = "contradiction-thread"
    quiz.submit_answer("trigger", "A", thread_id=thread)
    quiz.get_next_question(thread_id=thread)
    quiz.submit_answer("cal-1", "B", thread_id=thread)
    quiz.get_next_question(thread_id=thread)
    result = quiz.submit_answer("cal-2", "A", thread_id=thread)

    assert repos.learner_knowledge_states(thread) == []
    assert result["calibration_progress"]["status"] == "pending"
    pending = repos.pending_calibrations(thread)
    assert len(pending) == 1
    assert pending[0]["consistent_evidence_count"] == 1

    # 两道可靠题结果矛盾且已无剩余题源；下一次无筛选调度必须终结假进度。
    quiz.get_next_question(thread_id=thread)
    assert repos.pending_calibrations(thread) == []
    assert repos.calibration_status_counts(thread) == {
        "pending": 0,
        "confirmed": 0,
        "retracted": 0,
        "inconclusive": 1,
    }
    assert repos.learner_knowledge_states(thread) == []


def test_budget_exhaustion_still_restores_unanswered_question(
    calibration_db,
    monkeypatch,
):
    import quiz
    from storage import repos

    thread = "outstanding-thread"
    monkeypatch.setattr("config.CALIBRATION_MAX_INSERTIONS", 1)
    quiz.submit_answer("trigger", "A", thread_id=thread)
    first = quiz.get_next_question(thread_id=thread)
    repeated = quiz.get_next_question(thread_id=thread)

    assert first["id"] == repeated["id"] == "cal-1"
    pending = repos.pending_calibrations(thread)
    assert pending[0]["presentation_count"] == 1
    assert "attempt_id" not in pending[0]["evidence"][0]
    assert pending[0]["status"] == "pending"


def test_missing_reliable_questions_close_as_inconclusive(calibration_db):
    import quiz
    from storage import db, repos
    from storage.models import KnowledgePoint

    thread = "no-evidence-thread"
    with db.session() as session:
        session.add(
            KnowledgePoint(
                id="kp-empty",
                domain="法律法规",
                name="无可靠题知识点",
                status="active",
                evidence_count=0,
                question_count=0,
            )
        )
        session.commit()
    repos.create_calibration_hypothesis(
        thread,
        "kp-empty",
        "trigger",
        False,
        "no reliable questions",
    )

    quiz.get_next_question(thread_id=thread)

    assert repos.pending_calibrations(thread) == []
    assert repos.calibration_status_counts(thread)["inconclusive"] == 1
    assert repos.learner_knowledge_states(thread) == []


def test_weak_mode_keeps_due_review_priority_over_calibration(calibration_db):
    import time

    import quiz
    from storage import db
    from storage.models import LearnerReviewCard

    thread = "due-priority-thread"
    with db.session() as session:
        session.add(
            LearnerReviewCard(
                thread_id=thread,
                question_id="trigger",
                domain="信息安全管理",
                state=0,
                step=None,
                stability=0.0,
                difficulty=0.0,
                due=time.time() - 60,
                last_review=time.time() - 120,
                reps=1,
                lapses=1,
            )
        )
        session.commit()

    # A pending calibration exists, but weak mode must let the due FSRS card
    # win before it considers inserting another calibration question.
    from storage import repos

    repos.create_calibration_hypothesis(
        thread,
        "kp-risk",
        "trigger",
        False,
        "test",
        now=time.time(),
    )
    question = quiz.get_next_question(mode="weak", thread_id=thread)

    assert question["id"] == "trigger"
