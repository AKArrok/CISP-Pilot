"""data/manifest.json — 数据资产版本清单，防止"改了解析/分块但索引过期"的错配。

各脚本写入自己负责的字段，加载方（HybridRetriever / server 启动）校验一致性:
- parse_courses  → kb_schema_version, chunk_count
- parse_questions→ questions_schema_version, question_count
- build_index    → vector_count, embedding_model（向量维度/模型身份）
"""
from __future__ import annotations

import json
import os

import config

MANIFEST_PATH = os.path.join(config.DATA_DIR, "manifest.json")

# structured_v1 只新增可选字段，legacy JSON 仍可读取，保持向后兼容。
KB_SCHEMA_VERSION = 1
QUESTIONS_SCHEMA_VERSION = 2


def read_manifest() -> dict:
    if not os.path.exists(MANIFEST_PATH):
        return {}
    with open(MANIFEST_PATH, encoding="utf-8") as fp:
        return json.load(fp)


def update_manifest(**fields) -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    manifest = read_manifest()
    manifest.update(fields)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as fp:
        json.dump(manifest, fp, ensure_ascii=False, indent=1)


def _active_question_count() -> int:
    """活动数据源的题库计数：数据库表非空用 DB，否则用 JSON。"""
    from storage import repos
    # AI 生成题会在运行期合法增长，不计入 manifest 基线校验
    n = repos.count_questions(exclude_ai=True)
    if n:
        return n
    with open(config.QUESTIONS_PATH, encoding="utf-8") as fp:
        return len(json.load(fp))


def _active_chunk_count() -> int:
    # 向量索引直接由 JSON 构建；这里也必须校验同一个规范数据源。
    with open(config.KB_CHUNKS_PATH, encoding="utf-8") as fp:
        return len(json.load(fp))


def validate_index() -> None:
    """启动自检：四件数据资产齐全且相互一致，否则给出明确的修复指令。"""
    problems = []
    if not os.path.exists(config.KB_CHUNKS_PATH):
        problems.append("缺 data/kb_chunks.json → 运行 python data_ingest/parse_courses.py")
    if not os.path.exists(config.QUESTIONS_PATH):
        problems.append("缺 data/questions.json → 运行 python data_ingest/parse_questions.py")
    if not os.path.exists(config.VECTORS_PATH):
        problems.append("缺 data/vectors.npz → 运行 python data_ingest/build_index.py")
    if problems:
        raise FileNotFoundError("数据资产不完整:\n  " + "\n  ".join(problems))

    manifest = read_manifest()
    if not manifest:
        problems.append("缺 data/manifest.json → 重跑三个 data_ingest 脚本")
    else:
        n_chunks = _active_chunk_count()
        n_questions = _active_question_count()
        meta = json.load(open(config.VECTORS_PATH + ".meta.json", encoding="utf-8"))
        if manifest.get("chunk_count") != n_chunks:
            problems.append(
                f"知识块数与清单不符（manifest={manifest.get('chunk_count')}, 实际={n_chunks}）"
                " → 重新运行 parse_courses.py、build_index.py、scripts/import_to_db.py")
        if manifest.get("question_count") != n_questions:
            problems.append(
                f"题库数与清单不符（manifest={manifest.get('question_count')}, 实际={n_questions}）"
                " → 重新运行 parse_questions.py、scripts/import_to_db.py")
        if manifest.get("vector_count") != len(meta):
            problems.append(
                f"向量索引过期（manifest={manifest.get('vector_count')}, 索引={len(meta)}）"
                " → 重新运行 build_index.py")
        if manifest.get("kb_schema_version") != KB_SCHEMA_VERSION:
            problems.append("kb_chunks schema 版本过期 → 重建索引")
    if problems:
        raise RuntimeError("数据资产校验失败:\n  " + "\n  ".join(problems))
