"""判分逻辑单测（纯函数，无需 API/索引）。运行: pytest tests/test_grader.py -q"""

import pytest

from quiz.grader import normalize_choice, grade


class FakeStore:
    """替换 store.record_attempt 避免写库。"""

    def __init__(self):
        self.records = []

    def record_attempt(self, **kw):
        self.records.append(kw)
        return len(self.records)


def test_normalize_choice():
    assert normalize_choice("a") == "A"
    assert normalize_choice("BA") == "AB"
    assert normalize_choice("C ") == "C"
    assert normalize_choice("E") == ""


def test_grade_single(monkeypatch):
    import quiz.grader as g

    fake = FakeStore()
    monkeypatch.setattr(g.store, "record_attempt", fake.record_attempt)
    q = {
        "id": "t1",
        "source": "test",
        "domain": "信息安全管理",
        "stem": "题干",
        "options": {"A": "x", "B": "y", "C": "z", "D": "w"},
        "answer": "B",
        "analysis": "因为B",
    }
    result = grade(q, "b", include_related=False)  # 小写也算对
    assert result["correct"] is True
    assert result["attempt_id"] == 1
    assert result["answer"] == "B"
    assert "B" in result["analysis"]
    result = grade(q, "A", include_related=False)
    assert result["correct"] is False
    assert len(fake.records) == 2
    assert fake.records[0]["correct"] is True
    assert fake.records[1]["correct"] is False


@pytest.mark.parametrize("choice", ["", "Z9", "AA"])
def test_grade_rejects_invalid_choice_before_recording(monkeypatch, choice):
    import quiz.grader as g

    fake = FakeStore()
    monkeypatch.setattr(g.store, "record_attempt", fake.record_attempt)
    q = {
        "id": "t2",
        "source": "test",
        "domain": "信息安全管理",
        "stem": "题干",
        "options": {"A": "x", "B": "y", "C": "z", "D": "w"},
        "answer": "B",
    }

    with pytest.raises(ValueError, match="选项必须"):
        grade(q, choice)
    assert fake.records == []


def test_grade_can_skip_related_retrieval(monkeypatch):
    import quiz.grader as g

    fake = FakeStore()
    monkeypatch.setattr(g.store, "record_attempt", fake.record_attempt)
    monkeypatch.setattr(g, "related_chunks", lambda _question: pytest.fail("不应执行关联课件检索"))
    q = {
        "id": "t3",
        "source": "test",
        "domain": "信息安全保障",
        "stem": "题干",
        "options": {"A": "x", "B": "y"},
        "answer": "A",
    }

    result = grade(q, "A", include_related=False)
    assert result["related_chunks"] == []
