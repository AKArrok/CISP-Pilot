"""Offline controls and integration tests; no business DB or external calls."""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from agents import evidence, graph
import config


def chunk(name="RTO", page=1):
    return {"source": "synthetic.txt", "page": page, "text": name,
            "domain": "业务连续性"}


def search_decision(*queries):
    return {"action": "search", "searches": [
        {"question": q, "query": q} for q in queries]}


class ScriptedModel:
    def __init__(self, *decisions):
        self.decisions = iter(decisions)
        self.messages = []

    async def ainvoke(self, messages):
        self.messages.append(list(messages))
        return AIMessage(content=json.dumps(next(self.decisions)), usage_metadata={
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15})


def run(model, search, **kwargs):
    return asyncio.run(evidence.run_evidence_agent(
        "RTO与RPO区别", model=model, search=search, **kwargs))


def test_mixed_pdf_and_oscal_evidence_keeps_locations_and_original_text():
    original = [chunk(), {"source": "NIST", "locator": "cp-9", "domain": "业务连续性",
                         "text": "Original backup control", "embedding_text": "Not evidence"}]

    async def search(query):
        return original

    model = ScriptedModel(search_decision("备份"), {"action": "finish"})
    result = run(model, search)
    assert result["evidence_report"]["status"] == "completed"
    assert len(result["contexts"]) == 2
    payload = json.loads(model.messages[1][-1].content)
    assert payload["evidence"][1]["locator"] == "cp-9"
    assert payload["evidence"][0]["page"] == 1
    assert "Not evidence" not in model.messages[1][-1].content


def test_agent_can_search_inspect_and_supplement():
    queries = []

    async def search(query):
        queries.append(query)
        return [chunk(query, len(queries))]

    model = ScriptedModel(search_decision("RTO"), search_decision("RPO"),
                          {"action": "finish", "gaps": ["缺少恢复案例"]})
    result = run(model, search)
    assert queries == ["RTO", "RPO"]
    assert result["evidence_report"]["status"] == "completed"
    assert result["evidence_report"]["gaps"] == ["缺少恢复案例"]
    assert result["evidence_report"]["searches"][1]["chunk_ids"] == [2]
    assert result["evidence_report"]["usage"] == {
        "prompt_tokens": 30, "completion_tokens": 15}
    assert "remaining_search_rounds" in model.messages[1][-1].content


def test_hard_search_and_model_budgets_and_deduplication():
    queries = []

    async def search(query):
        queries.append(query)
        return [chunk(), chunk()]

    model = ScriptedModel(search_decision("a", "b", "c"),
                          search_decision("d", "e", "f"), search_decision("g"))
    result = run(model, search)
    assert queries == list("abcdef")
    assert len(model.messages) == 3
    assert len(result["contexts"]) == 1
    assert result["evidence_report"]["status"] == "budget_exhausted"
    assert all(set(r["chunk_ids"]) == {1} for r in result["evidence_report"]["searches"])


@pytest.mark.parametrize("decision", ["bad json", {"action": "search"},
                                           search_decision("a", "b", "c", "d")])
def test_invalid_decision_discards_evidence(decision):
    async def search(query):
        raise AssertionError("must not search")

    result = run(ScriptedModel(decision), search)
    assert result["contexts"] == []
    assert result["evidence_report"]["status"] == "fallback"
    assert result["evidence_report"]["usage"]["prompt_tokens"] == 10


def test_tool_failure_discards_partial_evidence():
    async def search(query):
        if query == "fail":
            raise RuntimeError("retriever unavailable")
        return [chunk()]

    result = run(ScriptedModel(search_decision("ok", "fail")), search)
    assert result["contexts"] == []
    assert result["evidence_report"]["reason"] == "invalid_output_or_tool_error"


def test_timeout_cancels_model_and_returns_fallback():
    class Slow:
        async def ainvoke(self, messages):
            await asyncio.sleep(1)

    result = run(Slow(), None, deadline_seconds=0.01)
    assert result["contexts"] == []
    assert result["evidence_report"]["reason"] == "timeout"


def test_empty_evidence_and_repeat_query_fall_back():
    async def search(query):
        return []

    for second in ({"action": "finish"}, search_decision("RTO")):
        result = run(ScriptedModel(search_decision("RTO"), second), search)
        assert result["evidence_report"]["status"] == "fallback"


@pytest.mark.parametrize("question, expected", [
    ("什么是RTO", False), ("RTO和RPO区别", True),
    ("备份案例分析", True), ("RTO是什么？RPO是什么？", True)])
def test_experimental_gate(question, expected):
    assert evidence.needs_evidence_agent(question) is expected


def test_graph_switch_bypass_fallback_and_report_reset(monkeypatch):
    from retrieval.hybrid import HybridRetriever

    class Retriever:
        def retrieve(self, *args):
            return [chunk("baseline")]

    monkeypatch.setattr(HybridRetriever, "get", lambda: Retriever())
    calls = []

    async def agent(question):
        calls.append(question)
        return {"contexts": [], "evidence_report": {
            "status": "fallback", "usage": {"prompt_tokens": 10}}}

    monkeypatch.setattr(evidence, "run_evidence_agent", agent)
    monkeypatch.setattr(config, "ENABLE_EVIDENCE_SUBAGENT", False)
    state = {"search_query": "RTO与RPO区别", "evidence_report": {"status": "old"}}
    result = asyncio.run(graph.retrieve(state))
    assert result["evidence_report"] is None and not calls
    monkeypatch.setattr(config, "ENABLE_EVIDENCE_SUBAGENT", True)
    result = asyncio.run(graph.retrieve(state))
    assert result["contexts"][0]["text"] == "baseline"
    assert result["evidence_report"]["status"] == "fallback"
    assert len(calls) == 1
    result = asyncio.run(graph.retrieve({"search_query": "什么是RTO"}))
    assert result["evidence_report"] is None and len(calls) == 1


def test_answer_accounts_for_subagent_and_reports_gaps(monkeypatch):
    class Answer:
        async def ainvoke(self, messages):
            assert "缺少案例" in messages[-1].content
            return AIMessage(content="资料不足", usage_metadata={
                "input_tokens": 20, "output_tokens": 7, "total_tokens": 27})

    monkeypatch.setattr(graph, "get_answer_llm", lambda: Answer())
    report = {"status": "completed", "searches": [], "gaps": ["缺少案例"],
              "conflicts": [], "usage": {"prompt_tokens": 30, "completion_tokens": 15}}
    state = {"contexts": [chunk()], "question": "区别", "evidence_report": report}
    result = asyncio.run(graph.answer(state))
    assert result["usage"] == {"prompt_tokens": 50, "completion_tokens": 22}
    state["contexts"] = []
    result = asyncio.run(graph.answer(state))
    assert result["usage"] == report["usage"]


def test_experimental_model_does_not_change_shared_factory(monkeypatch):
    from llms import get_simple_llm, reset_llms

    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    reset_llms()
    original = get_simple_llm()
    model = evidence.make_model()
    payload = model._get_request_payload([])
    assert payload["extra_body"]["max_tokens"] == 700
    assert model.max_retries == 0
    assert get_simple_llm() is original
    reset_llms()


def test_sse_never_publishes_subagent_model_tokens(tmp_path, monkeypatch):
    import server
    from agents import memory, metrics
    from storage import cache
    from storage.models import Base
    from sqlalchemy import create_engine
    from http_auth_helpers import authenticated_http, isolated_database
    from types import SimpleNamespace

    class FakeGraph:
        async def aget_state(self, config_):
            return SimpleNamespace(values={"messages": [AIMessage(content="history")],
                                          "answer": "public", "usage": {}})

        async def astream_events(self, *args, **kwargs):
            for node, text in (("retrieve", "PRIVATE_PLANNING"), ("answer", "public")):
                yield {"event": "on_chat_model_stream", "metadata": {"langgraph_node": node},
                       "data": {"chunk": AIMessage(content=text)}}
            yield {"event": "on_chain_end", "metadata": {"langgraph_node": "retrieve"},
                   "data": {"output": {"contexts": [chunk()], "evidence_report": {
                       "status": "completed", "rounds": 1}}}}

    monkeypatch.setattr(config, "ENABLE_EVIDENCE_SUBAGENT", True)
    monkeypatch.setattr(server.SessionStore, "get", lambda thread: FakeGraph())
    monkeypatch.setattr(cache, "rate_limit", lambda *args: (True, 1))
    monkeypatch.setattr(memory, "load_short_context", lambda *args, **kwargs: [])
    monkeypatch.setattr(metrics, "record", lambda *args: None)

    engine = create_engine(
        f"sqlite:///{tmp_path / 'evidence.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with isolated_database(engine, ready=True):
        http = authenticated_http(server.app)
        thread = http.own_thread("test")

        async def collect():
            response = await server.chat_stream(
                server.ChatRequest(query="RTO与RPO区别", thread_id=thread), user=http.user,
            )
            return "".join([item async for item in response.body_iterator])

        output = asyncio.run(collect())
    engine.dispose()
    assert "PRIVATE_PLANNING" not in output
    assert '"type": "token", "content": "public"' in output
    assert '"type": "progress"' in output and '"type": "evidence"' in output
