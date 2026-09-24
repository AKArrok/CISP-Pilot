"""结构化父子切片的纯函数测试。"""
from data_ingest.chunking import build_fixed_chunks, build_structured_chunks, deduplicate_chunks


def _pages(*texts: str, source: str = "业务连续性_V4.2") -> list[dict]:
    return [
        {
            "domain": "业务连续性",
            "source": source,
            "page": index,
            "kind": "courseware",
            "text": text,
        }
        for index, text in enumerate(texts, start=1)
    ]


def test_structured_chunks_keep_parent_and_retrieval_child():
    chunks = build_structured_chunks(_pages(
        "业务影响分析\n定义：分析业务中断造成的影响。\n"
        "目标：确定关键业务和恢复优先级。\n"
        "步骤：识别业务流程；评估影响；确定RTO和RPO。"
    ), target_chars=45, max_chars=90, overlap_units=1)

    assert len(chunks) >= 2
    assert len({chunk["parent_id"] for chunk in chunks}) == 1
    assert all(chunk["title"] == "业务影响分析" for chunk in chunks)
    assert all("知识域：业务连续性" in chunk["embedding_text"] for chunk in chunks)
    assert all(chunk["child_text"] in chunk["text"] for chunk in chunks)


def test_repeated_headers_and_page_numbers_are_removed():
    chunks = build_structured_chunks(_pages(
        "CISP培训课件\n业务影响分析\n正文第一页。\n1",
        "CISP培训课件\n恢复策略\n正文第二页。\n2",
        "CISP培训课件\n应急响应\n正文第三页。\n3",
    ))

    assert chunks
    assert all("CISP培训课件" not in chunk["text"] for chunk in chunks)
    assert all(not chunk["text"].rstrip().endswith(str(chunk["page"])) for chunk in chunks)


def test_ids_are_stable_and_change_with_content():
    original = build_structured_chunks(_pages("访问控制\n访问控制用于限制主体对客体的访问。"))
    repeated = build_structured_chunks(_pages("访问控制\n访问控制用于限制主体对客体的访问。"))
    changed = build_structured_chunks(_pages("访问控制\n访问控制用于限制主体对资源的访问。"))

    assert original[0]["id"] == repeated[0]["id"]
    assert original[0]["id"] != changed[0]["id"]


def test_deduplication_prefers_v42_over_v41():
    text = "风险评估用于识别资产、威胁和脆弱性，并分析风险发生的可能性与影响。" * 4
    old = build_structured_chunks(_pages(text, source="信息安全评估_v4.1"))[0]
    new = build_structured_chunks(_pages(text, source="信息安全评估_V4.2"))[0]

    chunks, removed = deduplicate_chunks([old, new])

    assert removed == 1
    assert len(chunks) == 1
    assert "V4.2" in chunks[0]["source"]


def test_continuation_page_inherits_previous_title():
    chunks = build_structured_chunks(_pages(
        "业务影响分析\n定义：分析业务中断造成的影响。",
        "目标：确定关键业务和恢复优先级。\n步骤：识别业务流程；评估影响。",
    ))

    continued = [c for c in chunks if "恢复优先级" in c["child_text"]]
    assert continued
    assert all(c["title"] == "业务影响分析" for c in continued)
    assert all("知识点：业务影响分析" in c["embedding_text"] for c in continued)


def test_fixed_chunks_have_char_overlap_and_no_parent_hierarchy():
    text = "第一句。第二句。第三句。" * 30
    chunks = build_fixed_chunks(_pages(text), target_chars=40, max_chars=200, overlap_chars=15)

    assert len(chunks) >= 5
    assert all(c["parent_id"] == c["id"] for c in chunks)  # 自身即父，检索不去重
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a["text"][-15:] in b["text"] or b["text"][-15:] in a["text"]  # 有字符重叠
