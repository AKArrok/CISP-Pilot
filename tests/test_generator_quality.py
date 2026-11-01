"""AI 出题质量闭环测试：结构校验、近似查重、自校验不过标 needs_review。"""
import sys

sys.path.insert(0, ".")


# ── 结构校验 ────────────────────────────────────────────────────────────

def _gen(stem="题干", a="选项A", b="选项B", c="选项C", d="选项D", answer="B"):
    from quiz.generator import GeneratedQuestion
    return GeneratedQuestion(stem=stem, option_a=a, option_b=b,
                             option_c=c, option_d=d, answer=answer, analysis="解析")


def test_structural_ok_passes_valid_question():
    from quiz.generator import _structural_ok
    assert _structural_ok(_gen()) is True


def test_structural_ok_rejects_duplicate_options():
    from quiz.generator import _structural_ok
    assert _structural_ok(_gen(a="相同", b="相同")) is False


def test_structural_ok_rejects_bad_answer():
    from quiz.generator import _structural_ok
    assert _structural_ok(_gen(answer="E")) is False
    assert _structural_ok(_gen(stem="  ")) is False


# ── 近似查重 ────────────────────────────────────────────────────────────

class FakeBank:
    def __init__(self, stems, domain="测试域"):
        self._stems = stems
        self._domain = domain

    def all(self, domain=None):
        if domain and domain != self._domain:
            return []
        return [{"stem": s, "domain": self._domain} for s in self._stems]

    def reload(self):
        pass


def test_looks_duplicate_exact_prefix(monkeypatch):
    import quiz.generator as g
    monkeypatch.setattr(g.QuestionBank, "get", classmethod(lambda cls: FakeBank(
        ["防火墙的主要功能是什么？请选择最准确的一项。"])))
    monkeypatch.setattr(g.store, "existing_ai_stems", lambda: set())
    assert g._looks_duplicate("防火墙的主要功能是什么？请选择最准确的一项。", "测试域") is True


def test_looks_duplicate_catches_reworded(monkeypatch):
    """同域题干换说法（相似度>0.85）也要拦住。"""
    import quiz.generator as g
    base = "下列关于防火墙的说法中正确的是哪一项，请从以下四个选项中进行选择"
    monkeypatch.setattr(g.QuestionBank, "get", classmethod(lambda cls: FakeBank([base])))
    monkeypatch.setattr(g.store, "existing_ai_stems", lambda: set())
    reworded = "下列关于防火墙的说法中正确的是哪一项，请你从下面四个选项当中选择"
    assert g._looks_duplicate(reworded, "测试域") is True
    assert g._looks_duplicate("密钥管理中密钥的生命周期包括哪些阶段", "测试域") is False


def test_looks_duplicate_scoped_to_domain(monkeypatch):
    """同题干但不同域不算重复。"""
    import quiz.generator as g
    stem = "PKI 的核心组成部分包括哪些？"
    monkeypatch.setattr(g.QuestionBank, "get", classmethod(
        lambda cls: FakeBank([stem], domain="信息安全支撑技术")))
    monkeypatch.setattr(g.store, "existing_ai_stems", lambda: set())
    assert g._looks_duplicate(stem, "信息安全支撑技术") is True
    assert g._looks_duplicate(stem, "业务连续性") is False


# ── 自校验闭环 ──────────────────────────────────────────────────────────

class FakeRetriever:
    def retrieve(self, query, top_k=3, domain=None):
        return [{"text": "防火墙部署在网络边界，依据预定义规则过滤流量。",
                 "source": "课件", "page": 1, "domain": domain or "物理与网络通信安全"}]


class FakeLLM:
    pass


def _patch_generation(monkeypatch, verify_valid: bool):
    """固定生成结果 + 可控校验结果，隔离 LLM 与检索。"""
    import quiz.generator as g

    calls = {"inserted": []}
    monkeypatch.setattr(
        g.QuestionBank, "get", classmethod(lambda cls: FakeBank([])),
    )

    def fake_invoke(llm, output_class, messages):
        if output_class.__name__ == "GeneratedQuestion":
            return _gen(stem="防火墙依据什么过滤流量？", answer="B")
        from quiz.generator import VerifyOut
        return VerifyOut(valid=verify_valid, reason="test")

    monkeypatch.setattr(g, "get_answer_llm", lambda temperature=0.5: FakeLLM())
    monkeypatch.setattr(g, "get_simple_llm", lambda: FakeLLM())
    monkeypatch.setattr(g, "invoke_structured", fake_invoke)
    monkeypatch.setattr(g.store, "insert_ai_question",
                        lambda stem, options, answer, analysis, domain, needs_review=False:
                        calls["inserted"].append({"needs_review": needs_review}) or "ai_x")
    return calls


def test_generate_question_passes_verify(monkeypatch):
    import quiz.generator as g

    calls = _patch_generation(monkeypatch, verify_valid=True)
    monkeypatch.setattr("retrieval.hybrid.HybridRetriever.get",
                        classmethod(lambda cls: FakeRetriever()), raising=False)
    q = g.generate_question("物理与网络通信安全")
    assert q is not None
    assert q["id"] == "ai_x"
    assert len(calls["inserted"]) == 1
    assert calls["inserted"][0]["needs_review"] is False


def test_generate_question_failed_verify_marked_needs_review(monkeypatch):
    """自校验不过：不出题给用户，但落库留档 needs_review=True。"""
    import quiz.generator as g

    calls = _patch_generation(monkeypatch, verify_valid=False)
    monkeypatch.setattr("retrieval.hybrid.HybridRetriever.get",
                        classmethod(lambda cls: FakeRetriever()), raising=False)
    q = g.generate_question("物理与网络通信安全")
    assert q is None
    assert len(calls["inserted"]) == 1
    assert calls["inserted"][0]["needs_review"] is True


def test_anchor_query_uses_wrong_stem(monkeypatch):
    """有锚点时检索 query 带上错题题干（定向出题）。"""
    import quiz.generator as g
    captured = {}

    class CapturingRetriever(FakeRetriever):
        def retrieve(self, query, top_k=3, domain=None):
            captured["query"] = query
            return super().retrieve(query, top_k, domain)

    monkeypatch.setattr("retrieval.hybrid.HybridRetriever.get",
                        classmethod(lambda cls: CapturingRetriever()), raising=False)
    monkeypatch.setattr(g, "get_answer_llm", lambda temperature=0.5: FakeLLM())

    def fail_gen(llm, output_class, messages):
        captured["prompt"] = messages[-1].content
        raise RuntimeError("stop")

    monkeypatch.setattr(g, "invoke_structured", fail_gen)
    anchor = {"stem": "RTO 和 RPO 的区别是什么？", "wrong_choice": "PRIVATE-WRONG-CHOICE",
              "answer": "B", "analysis": "RTO 关注恢复时间"}
    try:
        g.generate_question("业务连续性", anchor=anchor)
    except RuntimeError:
        pass
    assert "RTO 和 RPO" in captured["query"]
    assert captured["query"].startswith("业务连续性")
    assert "PRIVATE-WRONG-CHOICE" not in captured["prompt"]
    assert "用户刚做错" not in captured["prompt"]
