"""把解析产物（JSON）导入数据库（MySQL 主 / SQLite 降级），并迁移旧 SQLite 历史数据。

用法:
  python scripts/import_to_db.py                # 导入 questions.json + kb_chunks.json
  python scripts/import_to_db.py --legacy       # 额外迁移旧 cisp_qa.db 的 attempts/AI题/对话/指标
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from storage import db, repos


def import_json() -> None:
    with open(config.QUESTIONS_PATH, encoding="utf-8") as fp:
        questions = json.load(fp)
    with open(config.KB_CHUNKS_PATH, encoding="utf-8") as fp:
        chunks = json.load(fp)
    n_q = repos.upsert_questions(questions)
    n_c = repos.upsert_chunks(chunks)
    print(f"questions 表: 累计 {repos.count_questions()} 条（本次扫描 {n_q}）")
    print(f"kb_chunks 表: 累计 {repos.count_chunks()} 条（本次扫描 {n_c}）")
    if os.path.exists(config.QUESTION_ATTRIBUTIONS_PATH):
        with open(config.QUESTION_ATTRIBUTIONS_PATH, encoding="utf-8") as fp:
            attributions = json.load(fp)
        for result in attributions:
            repos.upsert_question_attribution(result)
        print(f"question_attributions 表: 本次导入 {len(attributions)} 条")


def import_legacy(legacy_path: str) -> None:
    """从旧 SQLite（data/cisp_qa.db）迁移历史数据到当前后端。"""
    import sqlite3

    from storage.models import Attempt, Conversation, Metric, Question

    conn = sqlite3.connect(legacy_path)
    conn.row_factory = sqlite3.Row
    with db.session() as s:
        for r in conn.execute("SELECT * FROM ai_questions"):
            s.add(Question(
                id=r["id"], source="AI生成", num=0, stem=r["stem"], options=r["options"],
                answer=r["answer"], analysis=r["analysis"] or "", domain=r["domain"],
                needs_review=False))
        for r in conn.execute("SELECT * FROM attempts"):
            s.add(Attempt(question_id=r["question_id"], question_source=r["question_source"],
                          domain=r["domain"], stem=r["stem"], choice=r["choice"],
                          correct=bool(r["correct"]), answered_at=r["answered_at"]))
        for r in conn.execute("SELECT * FROM conversations"):
            s.add(Conversation(thread_id=r["thread_id"], role=r["role"], content=r["content"],
                               intent=r["intent"], created_at=r["created_at"]))
        if _inspect(conn, "metrics"):
            for r in conn.execute("SELECT * FROM metrics"):
                s.add(Metric(thread_id=r["thread_id"], question=r["question"], intent=r["intent"],
                             route_ms=r["route_ms"], retrieval_ms=r["retrieval_ms"],
                             first_token_ms=r["first_token_ms"], total_ms=r["total_ms"],
                             prompt_tokens=r["prompt_tokens"],
                             completion_tokens=r["completion_tokens"], created_at=r["created_at"]))
        s.commit()
    conn.close()
    print(f"旧 SQLite 历史数据已迁移（后端={db.backend()}）")


def _inspect(conn, table: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def main() -> None:
    db.create_all()
    print(f"存储后端: {db.backend()}")
    if "--reset" in sys.argv:
        from storage.models import KBChunk, Question
        with db.session() as s:
            n1 = s.query(Question).delete()
            n2 = s.query(KBChunk).delete()
            s.commit()
        print(f"已清空旧数据: questions {n1} 条, kb_chunks {n2} 条")
    import_json()
    if "--legacy" in sys.argv and os.path.exists(config.DB_PATH):
        import_legacy(config.DB_PATH)


if __name__ == "__main__":
    main()
