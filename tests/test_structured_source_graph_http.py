"""Real HTTP, graph, sparse retrieval, SQL history; external model transport mocked."""
import json
from pathlib import Path

from langchain_core.messages import AIMessage
import numpy as np

import config
from agents import graph, memory
from retrieval.hybrid import HybridRetriever
from test_chat_sql_recovery import events
from test_http_identity import accounts as accounts


def test_structured_source_full_graph_followup_and_ownership(accounts, monkeypatch, tmp_path):
    chunks = json.loads((Path(__file__).resolve().parents[1] /
                         "evaluation/nist-development-v1/chunks.json").read_text(encoding="utf-8"))
    from data_ingest import manifest
    # Real asset validation and vector-file load, with explicit protocol-fixture
    # vectors used only to exercise startup; dense retrieval remains disabled.
    assets = tmp_path / "nist-v1"
    assets.mkdir()
    (assets / "kb_chunks.json").write_text(json.dumps(chunks), encoding="utf-8")
    (assets / "questions.json").write_text("[]", encoding="utf-8")
    (assets / "vectors.npz.meta.json").write_text(json.dumps([{"id": c["id"]} for c in chunks]), encoding="utf-8")
    np.savez_compressed(assets / "vectors.npz", vectors=np.eye(len(chunks), dtype=np.float32))
    (assets / "manifest.json").write_text(json.dumps({"kb_schema_version": 1, "chunk_count": len(chunks),
                                                    "question_count": 0, "vector_count": len(chunks)}), encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", str(assets))
    monkeypatch.setattr(config, "KB_CHUNKS_PATH", str(assets / "kb_chunks.json"))
    monkeypatch.setattr(config, "QUESTIONS_PATH", str(assets / "questions.json"))
    monkeypatch.setattr(config, "VECTORS_PATH", str(assets / "vectors.npz"))
    monkeypatch.setattr(manifest, "MANIFEST_PATH", str(assets / "manifest.json"))
    retriever = HybridRetriever()
    monkeypatch.setattr(HybridRetriever, "get", lambda: retriever)
    monkeypatch.setattr(config, "ENABLE_DENSE_RETRIEVAL", False)
    monkeypatch.setattr(config, "ENABLE_QUERY_REWRITE", False)
    monkeypatch.setattr(config, "ENABLE_QUERY_VARIANTS", True)
    monkeypatch.setattr(config, "ENABLE_LEARNED_RANKING", False)
    monkeypatch.setattr(config, "ENABLE_RERANKING", False)
    monkeypatch.setattr(config, "ENABLE_EVIDENCE_SUBAGENT", False)
    monkeypatch.setattr(config, "RETRIEVER_K", 1)
    monkeypatch.setattr(graph, "get_simple_llm", lambda: "simple")
    monkeypatch.setattr(graph, "get_answer_llm", lambda: "answer")
    stages = []

    def structured(llm, schema, messages):
        stages.append(schema.__name__)
        if schema is graph.RouteOut:
            return graph.RouteOut(intent="knowledge", domain="业务连续性")
        return graph.VariantsOut(variants=["系统备份 频率", "backup frequency"])

    async def invoke(llm, messages, **kwargs):
        if llm == "simple":
            stages.append("followup_rewrite")
            return AIMessage(content="系统备份的频率如何确定？")
        stages.append("answer")
        assert "定位【cp-9】" in messages[1].content
        assert "search_metadata" not in messages[1].content
        return AIMessage(content="备份频率待组织确定。\n【出处】[资料1]《NIST SP 800-53 5.2.0 OSCAL》定位【cp-9】")

    monkeypatch.setattr(graph, "invoke_structured", structured)
    monkeypatch.setattr(graph, "llm_ainvoke_with_retry", invoke)
    monkeypatch.setattr(graph, "llm_ainvoke_retry", invoke)
    monkeypatch.setattr(memory, "search_long_memory", lambda *args: [])
    monkeypatch.setattr(memory, "_save_vector_round", lambda *args: None)
    _, clients, threads = accounts
    for query in ("系统备份频率如何确定？", "那它的频率呢？"):
        output = events(clients[0].post("/chat/stream", json={"thread_id": threads[0], "query": query}))
        assert output[-1] == {"type": "done"}
        contexts = next(x["contexts"] for x in output if x["type"] == "contexts")
        assert contexts[0]["locator"] == "cp-9"
        assert contexts[0]["page"] is None
        assert "embedding_text" not in contexts[0]
    assert stages.count("RouteOut") == 2
    assert stages.count("VariantsOut") == 2
    assert stages.count("followup_rewrite") == 1
    assert stages.count("answer") == 2
    assert len(clients[0].get("/api/history", params={"thread_id": threads[0]}).json()) == 4
    assert clients[1].get("/api/history", params={"thread_id": threads[0]}).status_code == 404
