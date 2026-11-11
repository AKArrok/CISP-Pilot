"""Layout is deterministic across model chunk boundaries and persisted graph messages."""
import asyncio
import json
from pathlib import Path

from langchain_core.messages import AIMessage
import pytest

from agents import graph
from test_http_identity import accounts as accounts


def saved_inline_answer():
    root = Path(__file__).resolve().parents[1]
    rows = json.loads((root / "docs/engineering-delivery/live-platform-four-20261110/candidate/results.json").read_text(encoding="utf-8"))
    row = next(item for item in rows if item["id"] == "cp-9-new-08")
    return "".join(event.get("content", "") for event in row["events"] if event["type"] == "token")


def test_saved_inline_answer_is_formatted_in_graph_and_message(monkeypatch):
    raw = saved_inline_answer()
    assert "\n【出处】" not in raw

    async def invoke(*_):
        return AIMessage(content=raw)

    monkeypatch.setattr(graph, "get_answer_llm", lambda: object())
    monkeypatch.setattr(graph, "llm_ainvoke_retry", invoke)
    result = asyncio.run(graph.answer({"question": "备份", "contexts": [
        {"source": "NIST SP 800-53 5.2.0 OSCAL", "locator": "cp-9", "domain": "实验", "text": "Public backup source"}]}))
    expected = raw.replace("【出处】", "\n【出处】")
    assert result["answer"] == expected
    assert result["messages"][0].content == expected


@pytest.mark.parametrize("raw,expected", [
    ("", ""), ("正文", "正文"), ("正文【出", "正文【出"),
    ("【出处】[资料1]", "【出处】[资料1]"),
    ("正文\n【出处】[资料1]", "正文\n【出处】[资料1]"),
    ("正文【出处】[资料1]", "正文\n【出处】[资料1]"),
    ("甲【出处】A乙【出处】B", "甲\n【出处】A乙\n【出处】B"),
])
def test_all_chunk_splits_preserve_content_and_idempotence(raw, expected):
    from agents.sources import CitationLineFormatter, format_citation_lines

    assert format_citation_lines(raw) == expected
    assert format_citation_lines(expected) == expected
    for split in range(len(raw) + 1):
        formatter = CitationLineFormatter()
        assert formatter.feed(raw[:split]) + formatter.feed(raw[split:]) + formatter.finish() == expected
    formatter = CitationLineFormatter()
    assert "".join(formatter.feed(char) for char in raw) + formatter.finish() == expected


def test_saved_provider_text_is_unchanged_except_newline_at_every_split():
    from agents.sources import CitationLineFormatter

    raw = saved_inline_answer()
    expected = raw.replace("【出处】", "\n【出处】")
    for split in range(len(raw) + 1):
        formatter = CitationLineFormatter()
        assert formatter.feed(raw[:split]) + formatter.feed(raw[split:]) + formatter.finish() == expected


@pytest.mark.parametrize("bad_citation", [False, True, "selected"])
def test_actual_http_model_stream_and_sql_history_agree(accounts, monkeypatch, bad_citation):
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from agents import memory, sources
    from test_chat_sql_recovery import events

    raw = saved_inline_answer()
    expected = raw.replace("【出处】", "\n【出处】")
    if bad_citation == "selected":
        raw = "依据[资料1]，需要备份。"
        expected = raw + "\n\n【出处】[资料1]《NIST SP 800-53 5.2.0 OSCAL》定位【cp-9】"
    elif bad_citation:
        raw = "仅依据所给片段，无法确定。"
        expected = raw + "\n\n" + sources.CITATION_WARNING

    async def route(_):
        return {"intent": "knowledge", "domain": None}

    async def rewrite(state):
        return {"search_query": state["question"], "query_variants": []}

    async def retrieve(_):
        return {"contexts": [{"source": "NIST SP 800-53 5.2.0 OSCAL", "locator": "cp-9", "domain": "实验", "text": "Public backup source"}]}

    async def memories(_):
        return {"memories": []}

    async def invoke(model, messages):
        # The fake model does not honor ChatOpenAI's streaming=True; explicitly exercise its real stream API.
        parts = [part.content async for part in model.astream(messages)]
        return AIMessage(content="".join(parts))

    fed = []
    original_feed = sources.CitationLineFormatter.feed

    def feed(self, content):
        fed.append(content)
        return original_feed(self, content)

    monkeypatch.setattr(sources.CitationLineFormatter, "feed", feed)
    monkeypatch.setattr(graph, "route", route)
    monkeypatch.setattr(graph, "rewrite", rewrite)
    monkeypatch.setattr(graph, "retrieve", retrieve)
    monkeypatch.setattr(graph, "recall_memory", memories)
    monkeypatch.setattr(graph, "get_answer_llm", lambda: GenericFakeChatModel(messages=iter([AIMessage(content=raw)])))
    monkeypatch.setattr(graph, "llm_ainvoke_retry", invoke)
    monkeypatch.setattr(memory, "_save_vector_round", lambda *_: None)
    _, clients, threads = accounts
    output = events(clients[0].post("/chat/stream", json={"thread_id": threads[0], "query": "系统文档需要备份吗？"}))
    assert output[-1]["type"] == "done"
    streamed = "".join(event.get("content", "") for event in output if event["type"] == "token")
    assert streamed == expected
    assert len(fed) > 1  # Graph normalization plus actual model stream, not only final-answer fallback.
    saved = clients[0].get("/api/history", params={"thread_id": threads[0]}).json()
    assert saved[-1]["content"] == streamed


@pytest.mark.parametrize("fault,check", [
    ("inline", "citation_heading_own_line"),
    ("saved_hash", "stream_equals_saved_hash"),
])
def test_saved_real_layout_audit_rejects_regressions(tmp_path, fault, check):
    import re
    import shutil
    from scripts.audit_public_http import audit

    root = Path(__file__).resolve().parents[1]
    packet = root / "evaluation/concise-40-baseline-20261110"
    run = root / "docs/engineering-delivery/concise-regression-20261110/baseline40"
    for name in ("results.json", "summary.json", "default-db.json", "receipts.jsonl", "executor.py"):
        shutil.copyfile(run / name, tmp_path / name)
    rows = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    if fault == "inline":
        token = next(event for event in rows[0]["events"] if event["type"] == "token")
        token["content"] = re.sub(r"\n+(?=【出处】)", "", token["content"])
    else:
        rows[0]["persisted_answer_sha256"] = "0" * 64
    (tmp_path / "results.json").write_text(json.dumps(rows), encoding="utf-8")
    result = audit(packet, tmp_path)
    assert not result["passed"]
    assert result["checks"][0]["checks"][check] is False
