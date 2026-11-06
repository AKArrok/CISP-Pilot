import asyncio
from functools import partial
import json

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
import pytest

from agents import graph
from scripts.experimental_card_node import card_answer
from test_answer_unit_contract import fixture
from test_answer_card_contract import translation_map
from scripts.answer_unit_contract import build_catalog


def setup(monkeypatch):
    contexts, sources = fixture()
    translations = translation_map(build_catalog(contexts, sources), contexts)
    message = AIMessage(content='{"unit_ids":["s1:l1:p2"],"answer_type":"affirm"}',
        usage_metadata={"input_tokens": 10, "output_tokens": 8, "total_tokens": 18})
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return FakeMessagesListChatModel(responses=[message], disable_streaming=kwargs["disable_streaming"])

    monkeypatch.setattr(graph, "get_answer_llm", factory)

    async def fallback(state):
        return {"answer": "Original fallback", "messages": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

    node = partial(card_answer, sources=sources, translations=translations, selector_prompt="Synthetic selector", fallback=fallback)
    state = {"question": "Should languages be considered?", "contexts": contexts,
             "messages": [HumanMessage(content="Should languages be considered?")], "thread_id": "synthetic"}
    return node, state, calls, message


def test_card_node_preserves_raw_selector_receipt_and_returns_host_materialization(monkeypatch):
    node, state, calls, original = setup(monkeypatch)
    result = asyncio.run(node(state))
    assert "Consider languages" in result["answer"] and "risk" not in result["answer"]
    assert result["messages"][0].content == result["answer"]
    assert json.loads(original.content)["answer_type"] == "affirm"
    assert result["usage"] == {"prompt_tokens": 10, "completion_tokens": 8}
    assert calls[0]["disable_streaming"] and calls[0]["temperature"] == 0
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert calls[0]["max_tokens"] == 1024 and calls[0]["max_retries"] == 0


def test_registered_chinese_page_without_url_runs_selector_and_verbatim_card(monkeypatch):
    import hashlib
    from scripts.compile_nist_source_units import compile_units

    context = {"source": "Synthetic course", "page": 7, "text": "检测并确认事件。"}
    compiled = compile_units([{**context, "source_url": None, "locator": None}])["sources"]
    compiled[0]["page"] = 7
    digest = hashlib.sha256(context["text"].encode()).hexdigest()
    translations = {"units": {f"{digest}:1:1": {"english_sha256": digest,
        "reading_translation": context["text"], "original_language": "zh"}}, "parameters": {}}

    def factory(**kwargs):
        return FakeMessagesListChatModel(responses=[AIMessage(content='{"unit_ids":["s1:l1:p1"],"answer_type":"explain"}')])

    async def forbidden_fallback(state):
        raise AssertionError("Registered original must not fall back")

    monkeypatch.setattr(graph, "get_answer_llm", factory)
    result = asyncio.run(card_answer({"question": "检测？", "contexts": [context], "messages": []},
        sources=compiled, translations=translations, selector_prompt="Synthetic selector", fallback=forbidden_fallback))
    assert "> 检测并确认事件。" in result["answer"] and "第7页" in result["answer"]
    assert "source_url" not in context and "辅助译文：" not in result["answer"]


def test_unmapped_course_source_uses_original_answer_without_a_selector_call(monkeypatch):
    node, state, calls, _ = setup(monkeypatch)
    state["contexts"] = [{"source": "Synthetic course", "page": 1, "text": "Not NIST"}]
    assert asyncio.run(node(state))["answer"] == "Original fallback" and not calls


def test_changed_known_source_fails_closed_instead_of_using_unreviewed_translation(monkeypatch):
    node, state, calls, _ = setup(monkeypatch)
    state["contexts"][0]["text"] += "Changed."
    with pytest.raises(ValueError, match="source text"):
        asyncio.run(node(state))
    assert not calls


def test_low_relevance_keeps_original_gate_before_selector(monkeypatch):
    node, state, calls, _ = setup(monkeypatch)
    monkeypatch.setattr(graph, "_below_relevance_floor", lambda contexts: True)
    assert asyncio.run(node(state))["answer"] == "Original fallback" and not calls


def test_graph_events_do_not_stream_internal_selection_json(monkeypatch):
    from langgraph.graph import END, START, StateGraph
    from agents.state import AgentState
    node, state, _, _ = setup(monkeypatch)
    builder = StateGraph(AgentState)
    builder.add_node("answer", node)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    workflow = builder.compile()

    async def events():
        return [event async for event in workflow.astream_events(state, version="v2")]

    output = asyncio.run(events())
    streamed = [event for event in output if event["event"] == "on_chat_model_stream"]
    assert not streamed
    final = next(event["data"]["output"] for event in output if event["event"] == "on_chain_end"
                 and event["name"] == "answer")
    assert "辅助译文" in final["answer"] and '"unit_ids"' not in final["answer"]


def test_real_sse_and_sql_persist_host_card_without_internal_selector_json(tmp_path, monkeypatch):
    import sqlite3
    from sqlalchemy import create_engine
    import config
    import server
    from agents import memory
    from http_auth_helpers import authenticated_http, isolated_database
    from storage import cache
    from storage.models import Base

    node, state, _, _ = setup(monkeypatch)
    state["contexts"][0]["domain"] = "安全管理"
    monkeypatch.setattr(server, "warmup", lambda: None)
    monkeypatch.setattr(config, "ENABLE_QUERY_VARIANTS", False)
    monkeypatch.setattr(cache, "_client", cache._MemClient())
    monkeypatch.setattr(cache, "_live", False)
    monkeypatch.setattr(memory, "_save_vector_round", lambda *_: None)
    monkeypatch.setattr(graph, "route", lambda state: {"intent": "knowledge", "domain": None})
    monkeypatch.setattr(graph, "retrieve", lambda state_: {"contexts": state["contexts"], "evidence_report": None})
    monkeypatch.setattr(graph, "recall_memory", lambda state: {"memories": []})
    monkeypatch.setattr(graph, "answer", node)
    database = tmp_path / "card-http.sqlite"
    engine = create_engine(f"sqlite:///{database.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    try:
        with isolated_database(engine, ready=True):
            http = authenticated_http(server.app)
            thread = http.own_thread()
            with http.client as client:
                response = client.post("/chat/stream", json={"query": state["question"], "thread_id": thread})
                assert response.status_code == 200
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                assert events[-1]["type"] == "done" and not any(event["type"] == "error" for event in events)
                rendered = "".join(event.get("content", "") for event in events if event["type"] == "token")
                assert "辅助译文" in rendered and '"unit_ids"' not in rendered
                history = client.get("/api/history", params={"thread_id": thread}).json()
                assert history[-1]["content"] == rendered
                trace = client.get("/api/traces/" + response.headers["x-request-id"]).json()
                assert trace["status"] == "completed" and trace["trace"]["conversation_commit"] == "confirmed"
                assert trace["trace"]["model_calls"][0]["usage"] == {"prompt_tokens": 10, "completion_tokens": 8}
            engine.dispose()
            with sqlite3.connect("file:" + database.as_posix() + "?mode=ro", uri=True) as connection:
                assert connection.execute("select content from conversations where thread_id=? and role='assistant'", (thread,)).fetchone()[0] == rendered
    finally:
        engine.dispose()
