"""构建知识点候选树和题目多标签。

用法:
  python scripts/build_taxonomy.py
  python scripts/build_taxonomy.py --import-db

产物默认写入 data/，可重复运行；候选知识点需要审校后才应改为 active。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from data_ingest.attribution import classify_questions
from data_ingest.knowledge_catalog import apply_curated_catalog
from data_ingest.taxonomy import build_semantic_question_tags, build_taxonomy
from data_ingest.taxonomy_review import apply_manual_review
from data_ingest.tag_review import apply_manual_tag_review


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--import-db", action="store_true",
                        help="同时将候选点和题目标签写入当前数据库")
    parser.add_argument("--with-dense", action="store_true",
                        help="尝试加载配置的 dense 后端；失败会显式降级为不可用")
    parser.add_argument(
        "--db-only", action="store_true",
        help="仅写入数据库，不重写 taxonomy/tag/attribution JSON 产物",
    )
    args = parser.parse_args()
    if args.db_only and not args.import_db:
        parser.error("--db-only 必须与 --import-db 一起使用")
    with open(config.KB_CHUNKS_PATH, encoding="utf-8") as fp:
        chunks = json.load(fp)
    with open(config.QUESTIONS_PATH, encoding="utf-8") as fp:
        questions = json.load(fp)
    points, _legacy_tags = build_taxonomy(chunks, questions)
    apply_manual_review(points)
    apply_curated_catalog(points)
    tags = build_semantic_question_tags(points, questions)
    apply_manual_tag_review(points, tags)
    embedder = None
    if args.with_dense:
        try:
            from llms import get_embeddings
            embedder = get_embeddings()
        except Exception as exc:
            print(f"dense 后端不可用，显式降级: {type(exc).__name__}")
    attributions = classify_questions(points, questions, embedder=embedder)
    # 既有人工审校标签是冻结 active 资产；不新增题号级覆盖，只把已有
    # 审校结果作为归因快照中的 confirmed 来源记录下来。
    manual_by_question = {
        tag["question_id"]: tag for tag in tags if tag.get("tag_source") == "manual"
    }
    points_by_id = {point["id"]: point for point in points}
    for result in attributions:
        tag = manual_by_question.get(result["question_id"])
        if tag is None:
            continue
        point = points_by_id.get(tag["knowledge_point_id"], {})
        candidate = {
            "knowledge_point_id": tag["knowledge_point_id"],
            "point_id": tag["knowledge_point_id"],
            "domain": point.get("domain"), "name": point.get("name"),
            "score": 1.0,
        }
        result["candidate"] = candidate
        result["candidate_point_id"] = candidate["knowledge_point_id"]
        result["candidate_domain"] = candidate["domain"]
        result["attribution_status"] = result["status"] = "confirmed"
        result["agreement_count"] = 3
        result["reason"] = "已有人工审校 active 主标签"
        result["evidence"]["manual_review"] = tag.get("review", {})
    if not args.db_only:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(config.KNOWLEDGE_TAXONOMY_PATH, "w", encoding="utf-8") as fp:
            json.dump(points, fp, ensure_ascii=False, indent=1)
        with open(config.QUESTION_TAGS_PATH, "w", encoding="utf-8") as fp:
            json.dump(tags, fp, ensure_ascii=False, indent=1)
        with open(config.QUESTION_ATTRIBUTIONS_PATH, "w", encoding="utf-8") as fp:
            json.dump(attributions, fp, ensure_ascii=False, indent=1)
    print(f"知识点候选: {len(points)}，建议首批审校: "
          f"{sum(bool(p.get('recommended')) for p in points)}，题目标签: {len(tags)}")
    print("审校结果: " + "，".join(
        f"{status}={sum(p.get('status') == status for p in points)}"
        for status in ("active", "rejected", "candidate")
    ))
    if not args.db_only:
        print(f"候选树 → {config.KNOWLEDGE_TAXONOMY_PATH}")
        print(f"题目标签 → {config.QUESTION_TAGS_PATH}")
        print(f"保守归因 → {config.QUESTION_ATTRIBUTIONS_PATH}")
    print("归因状态: " + "，".join(
        f"{status}={sum(row['attribution_status'] == status for row in attributions)}"
        for status in ("confirmed", "probable", "unverified")
    ))
    if args.import_db:
        from storage import db, repos
        db.create_all()
        print(f"数据库知识点: {repos.upsert_knowledge_points(points)}")
        tag_count = repos.replace_question_knowledge_points(
            tags, question_ids=[q["id"] for q in questions])
        print(f"数据库题目标签: {tag_count}")
        for result in attributions:
            repos.upsert_question_attribution(result)
        print(f"数据库题目归因: {len(attributions)}")


if __name__ == "__main__":
    main()
