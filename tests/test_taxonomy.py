"""知识点候选树构建的确定性和题目多标签测试。"""


def test_build_taxonomy_is_deterministic_and_tags_exact_matches():
    from data_ingest.taxonomy import build_taxonomy

    chunks = [
        {"id": "2", "domain": "信息安全支撑技术", "source": "x", "page": 2,
         "title": "知识子域：密码学"},
        {"id": "1", "domain": "信息安全支撑技术", "source": "x", "page": 1,
         "title": "数字签名"},
        {"id": "3", "domain": "信息安全支撑技术", "source": "x", "page": 3,
         "title": "哈希函数"},
        {"id": "4", "domain": "信息安全支撑技术", "source": "x", "page": 4,
         "title": "总结"},
    ]
    questions = [{
        "id": "q1", "domain": "信息安全支撑技术", "stem": "数字签名和哈希函数的区别？",
        "options": {"A": "x"}, "analysis": "数字签名使用私钥。",
    }]
    points_a, tags_a = build_taxonomy(chunks, questions)
    points_b, tags_b = build_taxonomy(list(reversed(chunks)), questions)

    assert [p["id"] for p in points_a] == [p["id"] for p in points_b]
    assert len(tags_a) == len(tags_b) == 2
    assert all(t["tag_source"] == "heuristic" for t in tags_a)
    assert {p["name"] for p in points_a} == {"数字签名", "哈希函数"}


def test_short_ascii_match_requires_token_boundary():
    from data_ingest.taxonomy import build_taxonomy

    chunks = [{"id": "1", "domain": "信息安全支撑技术", "source": "x", "page": 1,
               "title": "CA"}]
    questions = [{"id": "q1", "domain": "信息安全支撑技术", "stem": "LOCALACHE 不是认证机构。",
                 "options": {}, "analysis": ""}]
    _points, tags = build_taxonomy(chunks, questions)
    assert tags == []


def test_generic_titles_do_not_become_high_frequency_knowledge_points():
    from data_ingest.taxonomy import build_taxonomy

    chunks = [
        {"id": "1", "domain": "信息安全监管", "source": "x", "page": 1,
         "title": "标准"},
        {"id": "2", "domain": "信息安全保障", "source": "y", "page": 1,
         "title": "技术"},
        {"id": "3", "domain": "信息安全管理", "source": "z", "page": 1,
         "title": "基本概念"},
    ]
    questions = [{"id": "q1", "domain": "信息安全监管", "stem": "标准化技术要求",
                  "options": {}, "analysis": ""}]

    points, tags = build_taxonomy(chunks, questions)
    assert points == []
    assert tags == []


def test_manual_review_covers_every_recommended_point():
    from data_ingest.taxonomy_review import apply_manual_review

    points = [
        {"domain": "软件安全开发", "name": "SDL", "recommended": True,
         "status": "candidate"},
        {"domain": "软件安全开发", "name": "灵活安排自己的“组合”",
         "recommended": True, "status": "candidate"},
        {"domain": "软件安全开发", "name": "低频标题", "recommended": False,
         "status": "candidate"},
    ]
    reviewed = apply_manual_review(points)

    assert reviewed[0]["status"] == "active"
    assert reviewed[1]["status"] == "rejected"
    assert reviewed[2]["status"] == "candidate"
    assert all(point.get("review") for point in reviewed if point["recommended"])


def test_manual_review_repairs_name_and_parent():
    from data_ingest.taxonomy_review import apply_manual_review

    point = {"domain": "业务连续性", "name": "第一阶段 — 准备",
             "parent_name": "业务连续性管理", "recommended": True,
             "status": "candidate"}
    apply_manual_review([point])

    assert point["name"] == "应急响应：准备"
    assert point["parent_name"] == "信息安全事件响应流程"
    assert point["status"] == "active"


def test_curated_catalog_adds_points_and_merges_aliases_idempotently():
    from data_ingest.knowledge_catalog import apply_curated_catalog

    points = [{
        "id": "existing", "domain": "业务连续性", "name": "业务连续性计划",
        "status": "active", "aliases": ["BCP"],
    }]
    apply_curated_catalog(points)
    apply_curated_catalog(points)

    existing = next(point for point in points if point["id"] == "existing")
    assert "业务持续性计划" in existing["aliases"]
    assert len([point for point in points if point["name"] == "业务连续性计划"]) == 1


def test_semantic_tagger_uses_alias_and_domain_constraint():
    from data_ingest.taxonomy import build_semantic_question_tags

    points = [
        {"id": "business-rto", "domain": "业务连续性", "name": "恢复时间目标",
         "aliases": ["RTO"], "status": "active"},
        {"id": "support-rto", "domain": "信息安全支撑技术", "name": "实时令牌操作",
         "aliases": ["RTO"], "status": "active"},
    ]
    questions = [{"id": "q1", "domain": "业务连续性", "stem": "在BCP中，RTO指什么？",
                  "options": {}, "analysis": "", "needs_review": False}]

    tags = build_semantic_question_tags(points, questions)
    assert [tag["knowledge_point_id"] for tag in tags] == ["business-rto"]


def test_semantic_tagger_prefers_stem_evidence_over_option_noise():
    from data_ingest.taxonomy import build_semantic_question_tags

    points = [
        {"id": "treat", "domain": "信息安全管理", "name": "风险处理",
         "aliases": ["风险处置"], "status": "active"},
        {"id": "assess", "domain": "信息安全管理", "name": "风险评估",
         "aliases": [], "status": "active"},
    ]
    questions = [{"id": "q1", "domain": "信息安全管理", "stem": "风险处置可以采用哪些措施？",
                  "options": {"A": "重新进行风险评估"}, "analysis": "", "needs_review": False}]

    tags = build_semantic_question_tags(points, questions)
    assert [tag["knowledge_point_id"] for tag in tags] == ["treat"]


def test_semantic_tagger_uses_correct_option_not_distractors():
    from data_ingest.taxonomy import build_semantic_question_tags

    points = [
        {"id": "access", "domain": "计算环境安全", "name": "数据库访问控制",
         "aliases": ["访问控制粒度"], "status": "active"},
        {"id": "backup", "domain": "计算环境安全", "name": "数据库备份",
         "aliases": ["数据库大小"], "status": "active"},
    ]
    questions = [{"id": "q1", "domain": "计算环境安全", "stem": "哪项与数据库安全直接相关？",
                  "options": {"A": "访问控制粒度", "B": "数据库大小"}, "answer": "A",
                  "analysis": "", "needs_review": False}]
    tags = build_semantic_question_tags(points, questions)
    assert [tag["knowledge_point_id"] for tag in tags] == ["access"]


def test_semantic_tagger_abstains_without_sufficient_evidence():
    from data_ingest.taxonomy import build_semantic_question_tags

    points = [{"id": "rto", "domain": "业务连续性", "name": "恢复时间目标",
               "aliases": ["RTO"], "status": "active"}]
    questions = [{"id": "q1", "domain": "业务连续性", "stem": "下列说法正确的是？",
                  "options": {}, "analysis": "", "needs_review": False}]
    assert build_semantic_question_tags(points, questions) == []
