"""Real graph persistence and SSE completion use committed SQL history."""
import json

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from agents import graph, memory
from storage import cache, conversations, db
from test_http_identity import accounts as accounts


def events(response):
    assert response.status_code == 200
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def local_chat(accounts, monkeypatch):
    observed = []

    async def route(state):
        return {"intent": "chitchat", "domain": None}

    async def answer(state):
        observed.append([message.content for message in state["messages"]])
        content = "reply:" + state["question"]
        return {"answer": content, "messages": [AIMessage(content=content)], "usage": {}}

    monkeypatch.setattr(graph, "route", route)
    monkeypatch.setattr(graph, "chitchat", answer)
    monkeypatch.setattr(memory, "_save_vector_round", lambda *_: None)
    yield accounts, observed


def test_reopen_without_cache_or_vector_restores_real_graph_context(local_chat, monkeypatch):
    (_, clients, threads), observed = local_chat
    client = clients[0]
    first = events(client.post("/chat/stream", json={"thread_id": threads[0], "query": "first"}))
    assert first[-1] == {"type": "done"}
    old_engine = db.get_engine()
    new_engine = create_engine(old_engine.url)
    old_engine.dispose()
    db.override_engine(new_engine)
    monkeypatch.setattr(cache, "get_json", lambda *_: (_ for _ in ()).throw(AssertionError("cache is not history")))
    monkeypatch.setattr(memory, "_load_store", lambda: (_ for _ in ()).throw(AssertionError("vector is not history")))
    try:
        history = client.get("/api/history", params={"thread_id": threads[0]})
        assert history.headers["cache-control"] == "no-store"
        assert [row["content"] for row in history.json()] == ["first", "reply:first"]
        second = events(client.post("/chat/stream", json={"thread_id": threads[0], "query": "second"}))
        assert second[-1] == {"type": "done"}
        assert observed == [["first"], ["first", "reply:first", "second"]]
        assert clients[1].get("/api/history", params={"thread_id": threads[0]}).status_code == 404
        assert clients[1].get("/api/history", params={"thread_id": threads[1]}).json() == []
    finally:
        new_engine.dispose()


@pytest.mark.parametrize("stage", ["assistant_insert", "commit"])
def test_storage_failure_rolls_back_pair_and_withholds_done(local_chat, stage):
    (_, clients, threads), observed = local_chat
    client = clients[0]
    engine = db.get_engine()

    def insert_fault(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO conversations") and "assistant" in parameters:
            raise OperationalError("private-sql", {}, Exception("private-password"))

    def commit_fault(session):
        raise OperationalError("private-commit", {}, Exception("private-password"))

    target, name, fault = (engine, "before_cursor_execute", insert_fault) if stage == "assistant_insert" else (
        Session, "before_commit", commit_fault)
    event.listen(target, name, fault)
    try:
        output = events(client.post("/chat/stream", json={"thread_id": threads[0], "query": "failed"}))
    finally:
        event.remove(target, name, fault)
    assert output[-1]["type"] == "error"
    assert not any(item["type"] == "done" for item in output)
    assert "private-password" not in json.dumps(output)
    assert conversations.load_history(threads[0]) == []
    retry = events(client.post("/chat/stream", json={"thread_id": threads[0], "query": "retry"}))
    assert retry[-1] == {"type": "done"}
    assert observed == [["failed"], ["retry"]]
    assert [row["content"] for row in conversations.load_history(threads[0])] == ["retry", "reply:retry"]


def test_done_observes_commit_despite_optional_memory_failure(local_chat, monkeypatch):
    (_, clients, threads), _ = local_chat
    committed = []

    def cache_fault(*args, **kwargs):
        committed.append([row["content"] for row in conversations.load_history(threads[0])])
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(cache, "set_json", cache_fault)
    monkeypatch.setattr(memory, "_save_vector_round", lambda *_: (_ for _ in ()).throw(RuntimeError("vector unavailable")))
    output = events(clients[0].post("/chat/stream", json={"thread_id": threads[0], "query": "saved"}))
    assert output[-1] == {"type": "done"}
    assert committed == [["saved", "reply:saved"]]


def test_history_storage_failure_is_safe_503(local_chat, monkeypatch):
    (_, clients, threads), _ = local_chat

    def fault(*_, **__):
        raise OperationalError("private-query", {}, Exception("private-password"))

    monkeypatch.setattr(conversations, "load_history", fault)
    response = clients[0].get("/api/history", params={"thread_id": threads[0]})
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "private-password" not in response.text
