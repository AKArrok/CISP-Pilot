"""解析产物自检。分两层:
- fixtures 子集: 任何环境（含 CI）都可跑
- 本地全量数据: data/ 不入库，存在时才跑（本地回归用）
"""
from collections import Counter
import json
import os

import pytest

import config
from domains import DOMAINS

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

REAL_DATA = (
    os.path.exists(config.QUESTIONS_PATH) and os.path.exists(config.KB_CHUNKS_PATH)
)


def _load(path):
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def _fixture_questions():
    return _load(os.path.join(FIXTURE_DIR, "questions_sample.json"))


def _fixture_chunks():
    return _load(os.path.join(FIXTURE_DIR, "kb_chunks_sample.json"))


# ── 通用断言（fixtures 与本地全量共用）────────────────────────────────────

def check_questions(questions):
    for q in questions:
        assert q["id"] and q["source"] and q["stem"].strip(), q["id"]
        if q["answer"]:
            assert set(q["answer"]) <= set("ABCD"), q["id"]
            if not q.get("needs_review"):
                for letter in q["answer"]:
                    assert letter in q["options"], q["id"]
        if q["answer"] and not set(q["answer"]) <= set(q["options"]):
            assert q.get("needs_review"), q["id"]
        assert q["domain"] in DOMAINS or q.get("needs_review"), q["id"]


def check_chunks(chunks):
    for c in chunks:
        assert c["id"] and c["source"] and c["page"] >= 1 and c["text"].strip(), c["id"]
        assert c["domain"] in DOMAINS, c["id"]


# ── fixtures 层（CI 可跑）────────────────────────────────────────────────

def test_fixture_questions():
    check_questions(_fixture_questions())


def test_fixture_chunks():
    check_chunks(_fixture_chunks())



def test_fixture_structure_boundaries_and_provenance():
    questions = _fixture_questions()
    chunks = _fixture_chunks()
    provenance = _load(os.path.join(FIXTURE_DIR, "parser_samples.provenance.json"))
    source = "原创合成解析结构样本"
    question_keys = {
        "analysis", "answer", "domain", "id", "needs_review",
        "num", "options", "source", "stem",
    }
    chunk_keys = {"domain", "id", "kind", "page", "source", "text"}

    assert len(questions) == 24
    assert len(chunks) == 20
    assert len(DOMAINS) == 10
    assert all(set(q) == question_keys for q in questions)
    assert all(set(c) == chunk_keys for c in chunks)
    for records, prefix in (
        (questions, "parser-demo-question-"),
        (chunks, "parser-demo-chunk-"),
    ):
        ids = [record["id"] for record in records]
        assert len(set(ids)) == len(records)
        assert all(record_id.startswith(prefix) for record_id in ids)
        assert all(record["source"] == source for record in records)

    assert set(DOMAINS) <= {q["domain"] for q in questions}
    assert Counter(c["domain"] for c in chunks) == Counter({d: 2 for d in DOMAINS})
    kind_counts = Counter(c["kind"] for c in chunks)
    assert kind_counts == {"pptx": 7, "courseware": 13}
    assert sum(
        not q["needs_review"]
        and len(q["answer"]) == 1
        and set(q["options"]) == set("ABCD")
        for q in questions
    ) > len(questions) // 2

    boundary_counts = {
        "multiple_answer_letters": sum(len(set(q["answer"])) > 1 for q in questions),
        "empty_answer": sum(not q["answer"] for q in questions),
        "review_unresolved_domain": sum(
            q["needs_review"] and q["domain"] not in DOMAINS for q in questions
        ),
        "review_missing_answer_option": sum(
            q["needs_review"] and bool(set(q["answer"]) - set(q["options"]))
            for q in questions
        ),
    }
    assert all(count > 0 for count in boundary_counts.values())
    assert provenance["schema_version"] == "parser-samples-v1"
    assert provenance["source_marker"] == source
    assert provenance["counts"] == {
        "questions": len(questions),
        "chunks": len(chunks),
        "domains": len(DOMAINS),
        "chunk_kinds": dict(kind_counts),
    }
    assert set(provenance["schemas"]["questions"]) == question_keys
    assert set(provenance["schemas"]["chunks"]) == chunk_keys
    assert set(provenance["boundary_categories"]) == set(boundary_counts)
    assert provenance["creation_method"] == (
        "AI-authored original synthetic structure data created in this task"
    )
    assert provenance["data_license_proposal"] == (
        "MIT data only; pending owner/publication confirmation"
    )


# ── 本地全量层（缺 data/ 时 skip）─────────────────────────────────────────

@pytest.mark.skipif(not REAL_DATA, reason="本地 data/ 不存在（CI 环境）")
class TestLocalData:
    def setup_method(self):
        self.questions = _load(config.QUESTIONS_PATH)
        self.chunks = _load(config.KB_CHUNKS_PATH)

    def test_question_bank_nonempty(self):
        assert len(self.questions) >= 1000

    def test_questions_valid(self):
        check_questions(self.questions)

    def test_kb_chunks_valid_and_cover_all_domains(self):
        check_chunks(self.chunks)
        covered = {c["domain"] for c in self.chunks}
        for d in DOMAINS:
            assert d in covered, d
