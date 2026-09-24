"""保守三路归因的状态、证据和显式降级回归。"""


def _question(stem="下列哪项正确？", correct="A"):
    return {
        "id": "q1", "domain": "信息安全管理", "stem": stem,
        "options": {"A": correct, "B": "无关干扰项"}, "answer": "A",
        "analysis": "解析没有额外线索", "needs_review": False,
    }


def test_unique_exact_hit_survives_dense_unavailable_as_probable():
    from data_ingest.attribution import classify_question_attribution

    points = [
        {"id": "risk", "domain": "信息安全管理", "name": "风险处理", "status": "active"},
        {"id": "audit", "domain": "信息安全管理", "name": "安全审计", "status": "active"},
    ]
    result = classify_question_attribution(
        _question(correct="风险处理"), points, embedder=None,
    )

    assert result["candidate_point_id"] == "risk"
    assert result["attribution_status"] == "probable"
    assert result["evidence"]["dense"]["available"] is False
    assert result["evidence"]["exact"]["candidates"][0]["evidence"]["correct_option"] >= 0.95


def test_same_name_across_domains_is_unverified():
    from data_ingest.attribution import classify_question_attribution

    points = [
        {"id": "mac-support", "domain": "信息安全支撑技术", "name": "MAC", "status": "active"},
        {"id": "mac-compute", "domain": "计算环境安全", "name": "MAC", "status": "active"},
    ]
    result = classify_question_attribution(
        _question(stem="哪项模型使用 MAC？", correct="MAC"), points,
    )

    assert result["attribution_status"] == "unverified"
    assert "同名" in result["reason"]


def test_failed_dense_backend_is_not_counted_as_three_route_agreement():
    from data_ingest.attribution import classify_question_attribution

    class BrokenEmbedder:
        def embed_documents(self, _documents):
            raise RuntimeError("embedding unavailable")

    points = [{"id": "rto", "domain": "业务连续性", "name": "恢复时间目标",
               "aliases": ["RTO"], "status": "active"}]
    result = classify_question_attribution(
        {**_question(stem="RTO 是什么？", correct="恢复时间目标"),
         "domain": "业务连续性"}, points, BrokenEmbedder(),
    )

    assert result["evidence"]["dense"]["available"] is False
    assert result["agreement_count"] < 3
    assert result["attribution_status"] != "confirmed"


def test_empty_evidence_is_unverified():
    from data_ingest.attribution import classify_question_attribution

    result = classify_question_attribution(
        _question(stem="下列说法正确的是？", correct="没有匹配概念"),
        [{"id": "rto", "domain": "业务连续性", "name": "恢复时间目标", "status": "active"}],
    )

    assert result["attribution_status"] == "unverified"
    assert result["candidate_point_id"] is None


def test_exact_evidence_survives_dense_top_five_miss():
    from data_ingest.attribution import classify_question_attribution

    class DenseMissEmbedder:
        def embed_documents(self, texts):
            # The target is deliberately outside the dense top five and below
            # the dense acceptance threshold; exact evidence still scans all
            # active points and should retain it as a probable candidate.
            if len(texts) > 1:
                return [
                    [0.4, 0.9165] if "目标知识点" not in text else [0.0, 1.0]
                    for text in texts
                ]
            return [[1.0, 0.0]]

    points = [
        {"id": f"noise-{index}", "domain": "信息安全管理", "name": f"干扰概念{index}",
         "status": "active"}
        for index in range(6)
    ] + [{"id": "target", "domain": "信息安全管理", "name": "目标知识点",
          "status": "active"}]
    result = classify_question_attribution(
        _question(stem="这道题考查什么？", correct="目标知识点"),
        points, DenseMissEmbedder(),
    )

    assert result["candidate_point_id"] == "target"
    assert result["attribution_status"] == "probable"
    assert all(
        item["knowledge_point_id"] != "target"
        for item in result["evidence"]["dense"]["candidates"]
    )


def test_route_conflict_is_explicitly_rejected():
    from data_ingest.attribution import AttributionThresholds, _aggregate

    def candidate(point_id, domain, score):
        return {"knowledge_point_id": point_id, "domain": domain, "score": score}

    routes = {
        "dense": {"available": True, "candidates": [candidate("p1", "域一", 0.91)],
                  "reason": None},
        "tfidf": {"available": True, "candidates": [candidate("p2", "域二", 0.91)],
                  "reason": None},
        "exact": {"available": True, "candidates": [candidate("p1", "域一", 0.96)],
                  "reason": None},
    }
    result = _aggregate(_question(), routes, AttributionThresholds())

    assert result["attribution_status"] == "unverified"
    assert result["evidence"]["conflict"] is True
    assert "冲突" in result["reason"]


def test_batch_classifier_embeds_questions_in_one_call():
    from data_ingest.attribution import classify_questions

    class CountingEmbedder:
        def __init__(self):
            self.calls = []

        def embed_documents(self, texts):
            self.calls.append(list(texts))
            return [[1.0, float(index + 1)] for index, _ in enumerate(texts)]

    embedder = CountingEmbedder()
    points = [
        {"id": "p1", "domain": "域一", "name": "概念一", "status": "active"},
        {"id": "p2", "domain": "域二", "name": "概念二", "status": "active"},
    ]
    questions = [
        {**_question(stem="问题一", correct="概念一"), "id": "q1"},
        {**_question(stem="问题二", correct="概念二"), "id": "q2"},
    ]

    results = classify_questions(points, questions, embedder)

    assert len(results) == 2
    assert len(embedder.calls) == 2
    assert [len(call) for call in embedder.calls] == [2, 2]


def test_negative_question_does_not_treat_excluded_option_as_target():
    from data_ingest.attribution import classify_question_attribution

    question = _question(
        stem="数字签名应具有的性质不包括哪一项？",
        correct="数据机密性",
    )
    points = [
        {"id": "signature", "domain": "密码", "name": "数字签名", "status": "active"},
        {"id": "confidentiality", "domain": "密码", "name": "数据机密性", "status": "active"},
    ]

    result = classify_question_attribution(question, points)

    assert result["candidate_point_id"] == "signature"
    assert result["attribution_status"] == "probable"
    assert result["evidence"]["negative_question"] is True
    assert result["evidence"]["exact"]["correct_option_used"] is False
