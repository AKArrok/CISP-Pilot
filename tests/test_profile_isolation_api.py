"""用户画像 API 必须按 thread_id 隔离，禁止返回全局学习记录。"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine


def test_stats_api_is_thread_scoped(tmp_path):
    from quiz import store
    from server import app
    from storage import db
    from storage.models import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'profile-api.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db.override_engine(engine)
    try:
        for index in range(3):
            store.record_attempt(
                "api-a", f"a-{index}", "test", "信息安全管理", "题目", "A", False,
            )
            store.record_attempt(
                "api-b", f"b-{index}", "test", "信息安全管理", "题目", "B", True,
            )

        client = TestClient(app)
        learner_a = client.get("/api/stats", params={"thread_id": "api-a"})
        learner_b = client.get("/api/stats", params={"thread_id": "api-b"})
        assert learner_a.status_code == learner_b.status_code == 200
        assert learner_a.json()["total_accuracy"] == 0.0
        assert learner_b.json()["total_accuracy"] == 1.0
        assert learner_a.json()["weak_domains"] == ["信息安全管理"]
        assert learner_b.json()["weak_domains"] == []
        assert learner_a.json()["profile_scope"] == {
            "type": "thread", "thread_id": "api-a",
            "legacy_global_history_included": False,
        }
        assert client.get("/api/stats").status_code == 422
    finally:
        engine.dispose()


def test_quiz_endpoints_require_thread_id():
    from server import app

    client = TestClient(app)
    assert client.post("/quiz/next", json={"mode": "random"}).status_code == 422
    assert client.post(
        "/quiz/answer", json={"question_id": "anything", "choice": "A"},
    ).status_code == 422
