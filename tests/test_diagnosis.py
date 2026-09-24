"""入门诊断闭环：自评先验、跨域校准、可恢复状态和报告。"""
import pytest
from sqlalchemy import create_engine


@pytest.fixture()
def diagnostic_db(tmp_path, monkeypatch):
    from storage import db
    from storage.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'diagnostic.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db.override_engine(engine)

    domains = ["信息安全保障", "信息安全监管", "业务连续性"]
    questions = []
    for domain in domains:
        for i in range(1, 8):
            questions.append({
                "id": f"{domain}-{i}", "source": "test", "domain": domain,
                "stem": f"{domain} 题目 {i}",
                "options": {"A": "错", "B": "对", "C": "干扰", "D": "干扰"},
                "answer": "B", "analysis": "test",
            })

    class FakeBank:
        def all(self, domain=None):
            return [q for q in questions if domain is None or q["domain"] == domain]

        def get_by_id(self, question_id):
            return next((q for q in questions if q["id"] == question_id), None)

    import quiz.diagnosis as diagnosis
    monkeypatch.setattr(diagnosis.QuestionBank, "get", classmethod(lambda cls: FakeBank()))
    monkeypatch.setattr(diagnosis, "grade", lambda question, choice, **_kwargs: {
        "correct": False, "answer": question["answer"], "analysis": "test",
    })
    yield engine
    engine.dispose()


def test_diagnostic_covers_focus_domains_and_control(diagnostic_db):
    from quiz.diagnosis import answer, next_question, start_session

    started = start_session(
        "thread-1", ["信息安全保障", "信息安全监管"],
        {"goal": "冲刺", "domain_status": {"信息安全保障": "概念混淆"}},
        max_questions=6,
    )
    sid = started["session_id"]
    selected = []
    for _ in range(6):
        current = next_question(sid)
        assert current["status"] == "active"
        q = current["question"]
        selected.append(q["domain"])
        result = answer(sid, q["id"], "A", confidence=1)
        if result["status"] == "completed":
            break

    assert selected[:4] == ["信息安全保障", "信息安全监管",
                            "信息安全保障", "信息安全监管"]
    assert "业务连续性" in selected  # 第 5 题左右插入未自评控制域

    from quiz.diagnosis import report
    output = report(sid)
    assert output["status"] == "completed"
    assert set(output["weak_domains"]) >= {"信息安全保障", "信息安全监管"}
    assert output["self_report"]["goal"] == "冲刺"
    assert output["domains"][0]["evidence"] in {"diagnostic", "self_report"}


def test_next_is_idempotent_before_answer(diagnostic_db):
    from quiz.diagnosis import next_question, start_session

    sid = start_session("thread-2", ["业务连续性"], max_questions=6)["session_id"]
    first = next_question(sid)
    second = next_question(sid)
    assert first["question"]["id"] == second["question"]["id"]


def test_answer_persists_knowledge_point_state(diagnostic_db):
    from quiz.diagnosis import answer, next_question, report, start_session
    from storage import db, repos
    from storage.models import KnowledgePoint, QuestionKnowledgePoint

    sid = start_session("thread-state", ["业务连续性"], max_questions=6)["session_id"]
    current = next_question(sid)
    question_id = current["question"]["id"]
    with db.session() as session:
        session.add(KnowledgePoint(
            id="kp-state", domain="业务连续性", name="状态持久化测试点",
            evidence_count=1, question_count=1,
        ))
        session.add(QuestionKnowledgePoint(
            question_id=question_id, knowledge_point_id="kp-state",
            confidence=1.0, is_primary=True, tag_source="test",
        ))
        session.commit()

    answer(sid, question_id, "A", confidence=3, reason="我确信这个选项正确")
    states = repos.learner_knowledge_states("thread-state", ["kp-state"])
    assert states[0]["attempts"] == 1
    assert states[0]["correct"] == 0
    output = report(sid)
    point = output["knowledge_points"][0]
    assert point["session_attempts"] == 1
    assert point["insufficient"] is True
    assert point["session_high_confidence_errors"] == 1
    assert output["misconception_evidence"][0]["reason"] == "我确信这个选项正确"


def test_confidence_signals_distinguish_misconception_and_fragile_knowledge():
    from quiz.diagnosis import _domain_stats

    responses = [
        {"domain": "信息安全管理", "correct": False, "confidence": 3},
        {"domain": "信息安全管理", "correct": True, "confidence": 0},
    ]
    row = _domain_stats(responses, "信息安全管理")
    assert row["high_confidence_errors"] == 1
    assert row["low_confidence_correct"] == 1
    assert row["calibration_signal"] == "misconception_risk"


def test_regular_answer_can_update_knowledge_point_state(diagnostic_db, monkeypatch):
    import quiz
    from storage import db, repos
    from storage.models import KnowledgePoint, QuestionKnowledgePoint

    question_id = "业务连续性-1"
    with db.session() as session:
        session.add_all([
            KnowledgePoint(
                id="kp-regular", domain="信息安全管理", name="普通刷题状态测试点",
                evidence_count=1, question_count=1,
            ),
            KnowledgePoint(
                id="kp-secondary", domain="业务连续性", name="次要关联点",
                evidence_count=1, question_count=1,
            ),
            QuestionKnowledgePoint(
                question_id=question_id, knowledge_point_id="kp-regular",
                confidence=1.0, is_primary=True, tag_source="test",
            ),
            QuestionKnowledgePoint(
                question_id=question_id, knowledge_point_id="kp-secondary",
                confidence=0.7, is_primary=False, tag_source="test",
            ),
        ])
        session.commit()
    captured = {}

    def fake_grade(question, choice, **kwargs):
        captured.update(kwargs)
        return {"correct": True, "answer": question["answer"],
                "analysis": "test", "related_chunks": []}

    monkeypatch.setattr(quiz, "grade", fake_grade)

    quiz.submit_answer(question_id, "B", thread_id="thread-regular")
    states = repos.learner_knowledge_states("thread-regular")
    assert [state["knowledge_point_id"] for state in states] == ["kp-regular"]
    assert states[0]["attempts"] == 1 and states[0]["correct"] == 1
    assert captured["thread_id"] == "thread-regular"
    assert captured["profile_domain"] == "信息安全管理"


def test_taxonomy_rebuild_removes_stale_heuristic_tags(diagnostic_db):
    from storage import db, repos
    from storage.models import QuestionKnowledgePoint

    with db.session() as session:
        session.add_all([
            QuestionKnowledgePoint(
                question_id="q-stale", knowledge_point_id="kp-old",
                confidence=0.7, is_primary=True, tag_source="heuristic",
            ),
            QuestionKnowledgePoint(
                question_id="q-stale", knowledge_point_id="kp-human",
                confidence=1.0, is_primary=False, tag_source="manual",
            ),
        ])
        session.commit()

    assert repos.replace_question_knowledge_points([], ["q-stale"]) == 0
    tags = repos.question_knowledge_points("q-stale")
    assert [tag["knowledge_point_id"] for tag in tags] == ["kp-human"]


def test_taxonomy_rebuild_can_replay_manual_tags(diagnostic_db):
    from storage import repos

    tag = {
        "question_id": "q-manual", "knowledge_point_id": "kp-human",
        "confidence": 1.0, "is_primary": True, "tag_source": "manual",
    }

    assert repos.replace_question_knowledge_points([tag]) == 1
    assert repos.replace_question_knowledge_points([tag]) == 1
    assert repos.question_knowledge_points("q-manual") == [tag]


def test_question_import_updates_existing_rows(diagnostic_db):
    from storage import repos

    original = {
        "id": "q-import", "source": "source", "num": 1, "stem": "污染题干",
        "options": {"A": "旧", "B": "旧"}, "answer": "A", "analysis": "",
        "domain": "业务连续性", "needs_review": False,
    }
    repaired = {
        **original, "stem": "修复后的题干", "options": {"A": "新", "B": "新"},
        "answer": "B", "needs_review": True,
    }

    repos.upsert_questions([original])
    repos.upsert_questions([repaired])
    row = next(question for question in repos.load_questions() if question["id"] == "q-import")

    assert row["stem"] == "修复后的题干"
    assert row["answer"] == "B"
    assert row["needs_review"] is True


def test_diagnosis_only_uses_reviewed_active_heuristic_tags(diagnostic_db):
    from storage import db, repos
    from storage.models import KnowledgePoint, QuestionKnowledgePoint

    with db.session() as session:
        session.add_all([
            KnowledgePoint(id="kp-active", domain="业务连续性", name="已审校",
                           evidence_count=1, question_count=1, status="active"),
            KnowledgePoint(id="kp-candidate", domain="业务连续性", name="未审校",
                           evidence_count=1, question_count=1, status="candidate"),
            QuestionKnowledgePoint(question_id="q-reviewed", knowledge_point_id="kp-active",
                                   confidence=0.9, is_primary=True,
                                   tag_source="heuristic"),
            QuestionKnowledgePoint(question_id="q-reviewed", knowledge_point_id="kp-candidate",
                                   confidence=0.9, is_primary=False,
                                   tag_source="heuristic"),
        ])
        session.commit()

    tags = repos.question_knowledge_points("q-reviewed")
    assert [tag["knowledge_point_id"] for tag in tags] == ["kp-active"]


def test_invalid_domain_is_rejected(diagnostic_db):
    from quiz.diagnosis import start_session
    from domains import DOMAINS

    with pytest.raises(ValueError, match="未知知识域"):
        start_session("thread-3", ["不存在的域"])
    with pytest.raises(ValueError, match="最多选择 4"):
        start_session("thread-3", DOMAINS[:5])
