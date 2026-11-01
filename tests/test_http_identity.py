"""Authentication and ownership are checked before any personal business data access."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, update

import config
import server
from http_auth_helpers import isolated_database
from storage import auth, cache, db, repos
from storage.http_auth import COOKIE, validate_origin_config
from storage.models import Base, DiagnosticSession, LearnerAttempt, DiagnosticEvent, WebSession


PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'http.sqlite'}")
    Base.metadata.create_all(engine)
    client_cache = cache._MemClient()
    client_cache._data = {}
    monkeypatch.setattr(cache, "_client", client_cache)
    monkeypatch.setattr(config, "CISP_PUBLIC_ORIGIN", "http://127.0.0.1:9528")
    with isolated_database(engine, ready=True):
        users, clients, threads = [], [], []
        for name in ("alice", "bob"):
            user = auth.register(name, PASSWORD)
            login = auth.login(name, PASSWORD)
            client = TestClient(server.app, base_url=config.CISP_PUBLIC_ORIGIN)
            client.cookies.set(COOKIE, login["session_token"])
            client.headers.update({"Origin": config.CISP_PUBLIC_ORIGIN,
                                   "X-CSRF-Token": login["csrf_token"]})
            users.append(user)
            clients.append(client)
            threads.append(repos.create_thread(user["id"])["id"])
        with db.session() as session:
            session.add(DiagnosticSession(id="alice-diagnostic", thread_id=threads[0],
                                          created_at=1.0, updated_at=1.0))
            session.commit()
        yield users, clients, threads
        for client in clients:
            client.close()
    engine.dispose()


def _thread_requests(thread_id):
    return [
        ("GET", f"/api/diagnostics?thread_id={thread_id}", None),
        ("POST", "/chat/stream", {"thread_id": thread_id, "query": "private query"}),
        ("POST", "/quiz/next", {"thread_id": thread_id}),
        ("POST", "/quiz/answer", {"thread_id": thread_id, "question_id": "q", "choice": "A", "submission_id": "ownership-probe"}),
        ("POST", "/diagnostic/start", {"thread_id": thread_id, "selected_domains": ["domain"], "start_id": "ownership-probe"}),
        ("POST", "/api/attributions/q/correction", {"thread_id": thread_id, "corrected_point_id": "kp"}),
        *[("GET", f"/api/{path}?thread_id={thread_id}", None) for path in (
            "stats", "history", "attribution-review", "attribution-corrections", "attribution-observability",
        )],
    ]


def test_cross_account_and_legacy_thread_requests_are_denied_without_side_effects(accounts, monkeypatch):
    _users, clients, threads = accounts
    from agents.graph import SessionStore
    monkeypatch.setattr(SessionStore, "get", lambda *_: pytest.fail("unauthorized graph access"))
    with db.session() as session:
        before = [session.scalar(select(func.count()).select_from(model))
                  for model in (LearnerAttempt, DiagnosticEvent)]
    for thread_id in (threads[0], "legacy-unowned", "nonexistent"):
        for method, url, payload in _thread_requests(thread_id):
            response = clients[1].request(method, url, json=payload)
            assert response.status_code == 404, (method, url, response.text)
    for method, path, payload in (
        ("POST", "next", None),
        ("POST", "answer", {"question_id": "q", "choice": "A"}),
        ("GET", "report", None),
    ):
        response = clients[1].request(method, f"/diagnostic/alice-diagnostic/{path}", json=payload)
        assert response.status_code == 404
    with db.session() as session:
        after = [session.scalar(select(func.count()).select_from(model))
                 for model in (LearnerAttempt, DiagnosticEvent)]
    assert after == before


def test_missing_session_is_denied_on_every_protected_route(accounts):
    _users, _clients, threads = accounts
    client = TestClient(server.app, base_url=config.CISP_PUBLIC_ORIGIN)
    requests = _thread_requests(threads[0]) + [
        ("GET", path, None) for path in (
            "/api/auth/me", "/api/threads", "/api/metrics", "/api/attributions",
            "/api/knowledge-points", "/api/domains", "/api/document?source=x&page=1",
            "/api/readiness", "/diagnostic/alice-diagnostic/report",
        )
    ] + [
        ("POST", "/api/threads", None), ("POST", "/api/auth/logout", None),
        ("POST", "/diagnostic/alice-diagnostic/next", None),
        ("POST", "/diagnostic/alice-diagnostic/answer", {"question_id": "q", "choice": "A"}),
    ]
    for method, url, payload in requests:
        assert client.request(method, url, json=payload).status_code == 401, url
    assert client.get("/api/health").json() == {"status": "ok"}
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
    client.close()


def test_csrf_origin_logout_and_expiry(accounts):
    _users, clients, _threads = accounts
    client = clients[0]
    token = client.cookies.get(COOKIE)
    headers = dict(client.headers)
    for origin, csrf in (("https://evil.invalid", headers["x-csrf-token"]),
                         (config.CISP_PUBLIC_ORIGIN, "wrong"), ("", "")):
        client.headers.update({"Origin": origin, "X-CSRF-Token": csrf})
        assert client.post("/api/threads").status_code == 403
    client.headers.update(headers)
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    client.cookies.set(COOKIE, token)
    assert client.get("/api/auth/me").status_code == 401
    with db.session() as session:
        session.execute(update(WebSession).values(expires_at=datetime.now() - timedelta(days=1)))
        session.commit()
    assert clients[1].get("/api/auth/me").status_code == 401


def test_login_cookie_and_pre_auth_origin_and_limits(accounts, monkeypatch):
    _users, _clients, _threads = accounts
    client = TestClient(server.app, base_url=config.CISP_PUBLIC_ORIGIN)
    payload = {"username": "alice", "password": PASSWORD}
    assert client.post("/api/auth/login", json=payload).status_code == 403
    assert client.post("/api/auth/register", json=payload).status_code == 403
    response = client.post("/api/auth/login", json=payload, headers={"Origin": config.CISP_PUBLIC_ORIGIN})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Max-Age=86400" in cookie
    assert "session_token" not in response.json() and "password_hash" not in response.text
    assert response.headers["cache-control"] == "no-store"
    # Limiter denies before password computation.
    monkeypatch.setattr(cache, "rate_limit", lambda *args: (False, 31))
    monkeypatch.setattr(auth, "login", lambda *_: pytest.fail("rate limited password work"))
    assert client.post("/api/auth/login", json=payload,
                       headers={"Origin": config.CISP_PUBLIC_ORIGIN}).status_code == 429
    client.close()


def test_thread_creation_lists_and_metrics_are_owner_scoped(accounts):
    users, clients, threads = accounts
    from agents import metrics
    metrics.record(threads[0], "alice secret", "knowledge", 1, 2, 3, 4, 5, 6)
    metrics.record(threads[1], "bob secret", "chat", 10, 20, 30, 40, 50, 60)
    metrics.record("legacy", "orphan", "legacy", 100, 200, 300, 400, 500, 600)
    for index, client in enumerate(clients):
        listing = client.get("/api/threads").json()
        assert {row["id"] for row in listing} == {threads[index]}
        summary = client.get("/api/metrics").json()
        assert summary["total_requests"] == 1
        assert summary["total_prompt_tokens"] == (5 if index == 0 else 50)
    response = clients[1].post("/api/threads", json={"id": threads[0]})
    assert response.status_code == 201
    row = response.json()
    assert row["id"] != threads[0] and row["owner_user_id"] == users[1]["id"]
    assert repos.get_thread_owner(threads[0]) == users[0]["id"]


def test_owned_history_and_long_memory_do_not_mix_accounts(accounts, tmp_path, monkeypatch):
    _users, clients, threads = accounts
    from agents import memory

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "MEMORY_VECTORS_PATH", str(tmp_path / "memory.npz"))
    monkeypatch.setattr(memory, "_embed_text", lambda text: (memory._fallback_embed(text), "test-hash"))
    for index, name in enumerate(("alice", "bob")):
        memory.save_round(threads[index], "same public question", f"{name} private context", "knowledge")
    for index, name in enumerate(("alice", "bob")):
        response = clients[index].get(f"/api/history?thread_id={threads[index]}")
        assert response.status_code == 200
        assert [row["content"] for row in response.json()] == [
            "same public question", f"{name} private context",
        ]
        assert memory.load_short_context(threads[index])[-1]["content"] == f"{name} private context"
        recalled = memory.search_long_memory("same public question", thread_id=threads[index])
        assert len(recalled) == 1 and recalled[0]["answer"] == f"{name} private context"


def test_public_readiness_redacts_details_and_https_origin_requires_secure_cookie(accounts, monkeypatch):
    _users, clients, _threads = accounts
    with monkeypatch.context() as readiness_patch:
        readiness_patch.setattr(server, "_readiness_payload", lambda: ({"error": "private path"}, False))
        response = clients[0].get("/api/ready")
        assert response.status_code == 503 and response.json() == {"status": "unavailable"}
    monkeypatch.setattr(config, "CISP_PUBLIC_ORIGIN", "http://public.example")
    with pytest.raises(ValueError, match="HTTPS"):
        validate_origin_config()
    monkeypatch.setattr(config, "CISP_PUBLIC_ORIGIN", "https://demo.example")
    validate_origin_config()
    client = TestClient(server.app, base_url=config.CISP_PUBLIC_ORIGIN)
    response = client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD},
                           headers={"Origin": config.CISP_PUBLIC_ORIGIN})
    assert response.status_code == 200 and "Secure" in response.headers["set-cookie"]
    client.close()
