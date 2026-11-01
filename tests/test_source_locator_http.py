from types import SimpleNamespace

from agents import graph
from test_chat_sql_recovery import events
from test_http_identity import accounts as accounts


def test_sse_preserves_pdf_and_distinct_oscal_locations(accounts, monkeypatch):
    contexts = [{"source": "课件", "page": 7, "domain": "信息安全管理", "text": "Original PDF."}]
    contexts += [{"source": "NIST", "locator": locator, "source_url": "https://example.test/catalog.json",
                  "domain": "实验", "text": "Original control.", "embedding_text": "private search aliases"}
                 for locator in ("ac-8", "cp-9")]

    class ReplayGraph:
        async def astream_events(self, *args, **kwargs):
            yield {"event": "on_chain_end", "name": "retrieve", "metadata": {"langgraph_node": "retrieve"},
                   "data": {"output": {"contexts": contexts}}}

        async def aget_state(self, config):
            return SimpleNamespace(values={"answer": "offline protocol replay", "usage": {}})

    monkeypatch.setattr(graph.SessionStore, "get", lambda *_: ReplayGraph())
    _, clients, threads = accounts
    output = events(clients[0].post("/chat/stream", json={"thread_id": threads[0], "query": "test"}))
    assert output[-1] == {"type": "done"}
    sources = next(e["contexts"] for e in output if e["type"] == "contexts")
    assert sources[0] == {"source": "课件", "page": 7, "domain": "信息安全管理", "text": "Original PDF."}
    assert [c["locator"] for c in sources[1:]] == ["ac-8", "cp-9"]
    assert all(c["page"] is None and c["source_url"].startswith("https:") for c in sources[1:])
    assert all("embedding_text" not in c for c in sources)
