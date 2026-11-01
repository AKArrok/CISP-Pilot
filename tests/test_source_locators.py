import asyncio

from langchain_core.messages import AIMessage

from agents import graph
from agents.sources import source_preview
from scripts.audit_flash_answers import citation_checks


def test_answer_accepts_mixed_pdf_and_oscal_without_index_metadata(monkeypatch):
    contexts = [
        {"source": "原课件", "page": 7, "domain": "信息安全管理", "text": "原课件内容。"},
        {"source": "NIST OSCAL", "locator": "ac-8", "domain": "实验补充资料",
         "text": "System usage may be monitored, recorded, and subject to audit;",
         "embedding_text": "索引词不能冒充原文", "search_metadata": {"zh": "索引词不能冒充原文"}},
    ]
    captured = []

    async def invoke(llm, messages):
        captured.extend(messages)
        return AIMessage(content="离线协议回放。\n【出处】[资料1]《原课件》第7页；[资料2]《NIST OSCAL》定位【ac-8】")

    monkeypatch.setattr(graph, "get_answer_llm", lambda: object())
    monkeypatch.setattr(graph, "llm_ainvoke_retry", invoke)
    result = asyncio.run(graph.answer({"question": "使用告知", "contexts": contexts}))
    prompt = captured[1].content
    assert "来源《原课件》第7页" in prompt
    assert "来源《NIST OSCAL》定位【ac-8】" in prompt
    assert contexts[1]["text"] in prompt
    assert "索引词不能冒充原文" not in prompt
    assert not citation_checks(result["answer"], contexts)["errors"]


def test_correct_locator_does_not_prove_same_topic_claim():
    contexts = [{"source": "NIST OSCAL", "locator": "cp-9", "text": "Protect the integrity of backup information."}]
    answer = "备份必须每小时一次。[资料1]\n【出处】[资料1]《NIST OSCAL》定位【cp-9】"
    check = citation_checks(answer, contexts)
    assert check["parsed_citations"] == 1
    assert not check["errors"]
    assert check["semantic_citation_support"] == "not_scored"


def test_wrong_control_and_invented_pdf_page_are_rejected():
    contexts = [{"source": "NIST OSCAL", "locator": "ac-8", "text": "notification"}]
    for answer in ("[资料1]《NIST OSCAL》定位【cp-9】", "[资料1]《NIST OSCAL》第8页"):
        assert citation_checks(answer, contexts)["errors"]


def test_source_previews_keep_distinct_locators_without_index_metadata():
    rows = [source_preview({"source": "NIST", "locator": locator, "domain": "实验", "text": "Original.",
                            "source_url": "https://example.test/catalog.json", "embedding_text": "secret alias"})
            for locator in ("ac-8", "cp-9")]
    assert len({(r["source"], r["locator"]) for r in rows}) == 2
    assert all(r["page"] is None and "embedding_text" not in r for r in rows)
