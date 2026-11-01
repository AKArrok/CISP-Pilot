"""Create original synthetic demo assets and migrate a new, explicit SQLite target.

No course/exam inputs, users, historical attempts or evaluation labels are imported.
Vectors are deterministic protocol fixtures, not semantic model embeddings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TOPICS = [
    ("信息安全保障", "纵深防御", "在多个层面设置相互补充的控制", "取消入口以外的所有安全控制"),
    ("信息安全监管", "合规证据", "保留适用要求与控制措施的对应记录", "只保存口头承诺而不保存记录"),
    ("信息安全支撑技术", "哈希完整性检查", "比较摘要以检查内容是否发生变化", "把哈希当成可逆加密来解密"),
    ("物理与网络通信安全", "网络分段", "限制不同区域之间的通信范围", "让所有区域无条件相互访问"),
    ("计算环境安全", "最小权限", "只授予完成任务所需的权限", "为所有普通账号永久授予管理员权限"),
    ("软件安全开发", "参数化查询", "将查询结构与外部输入参数分离", "直接把外部输入拼接为查询结构"),
    ("信息安全评估", "风险评估", "结合资产、威胁和脆弱性识别风险", "只按设备外观判断风险"),
    ("安全工程与运营", "变更管理", "执行前评估影响并准备回退方案", "直接修改关键配置且不留记录"),
    ("业务连续性", "恢复演练", "验证备份和恢复步骤是否实际可用", "从不恢复验证而仅统计备份文件数量"),
    ("信息安全管理", "安全意识", "说明并练习报告可疑事件的流程", "要求员工发现异常后保持沉默"),
]
LICENSE = """MIT License — original synthetic demo data only

Copyright (c) 2026 CISP synthetic demo data contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this data and associated documentation files (the "Data"), to deal
in the Data without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Data, and to permit persons to whom the Data is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Data.

THE DATA IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE DATA OR THE USE OR OTHER DEALINGS IN THE
DATA.
"""


def fixture_vector(text: str) -> list[float]:
    """Fixed 1024-dimensional test vector; no semantic or provider claim."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [(digest[i % len(digest)] - 127.5) / 127.5 for i in range(1024)]


def prepare(output_dir: Path) -> dict:
    target = output_dir.resolve()
    protected = (ROOT / "data").resolve()
    if target == protected or protected in target.parents or target in protected.parents:
        raise ValueError("demo output must not contain or be inside the default data directory")
    if target.exists() and any(target.iterdir()):
        raise ValueError("demo output must be new or empty; existing files are never replaced")
    target.mkdir(parents=True, exist_ok=True)
    import numpy as np

    questions, chunks, points, attributions = [], [], [], []
    for number, (domain, concept, fact, wrong) in enumerate(TOPICS, 1):
        point_id = f"demo-kp-{number:02}"
        chunks.append({"id": f"demo-chunk-{number:02}", "domain": domain,
                       "source": "合成工程演示资料", "page": number, "kind": "summary",
                       "text": f"合成演示定义：{concept}的目标是{fact}。本例仅用于工程演示。"})
        points.append({"id": point_id, "domain": domain, "name": concept, "description": fact,
                       "evidence_count": 1, "question_count": 2, "status": "active"})
        for variant in range(2):
            qid = f"demo-q-{number:02}-{variant + 1}"
            answer = "A" if variant == 0 else "B"
            questions.append({"id": qid, "source": "合成工程演示", "num": len(questions) + 1,
                              "stem": f"根据合成演示定义，{concept}应采用哪种做法？（情景{variant + 1}）",
                              "options": {"A": fact if variant == 0 else wrong,
                                          "B": wrong if variant == 0 else fact,
                                          "C": "跳过所有确认步骤", "D": "不记录任何结果"},
                              "answer": answer, "analysis": f"本演示定义为：{fact}。", "domain": domain,
                              "needs_review": False})
            attributions.append({"question_id": qid, "candidate_point_id": point_id,
                                 "candidate_domain": domain, "attribution_status": "confirmed",
                                 "agreement_count": 0, "classifier_version": "synthetic-demo-v1",
                                 "reason": "定义式合成fixture映射，不是人工审阅或评测金标",
                                 "evidence": {"synthetic_demo": True, "human_reviewed": False}})
    manifest = {"kb_schema_version": 1, "questions_schema_version": 2, "chunk_count": len(chunks),
                "question_count": len(questions), "vector_count": len(chunks),
                "embedding_model": "synthetic-sha256-protocol-fixture-1024",
                "synthetic_demo": True, "license": "MIT", "human_reviewed": False,
                "notice": "Not exam material, evaluation labels, semantic embeddings or accuracy evidence."}
    for filename, value in {"questions.json": questions, "kb_chunks.json": chunks,
                            "knowledge_taxonomy.json": points, "question_knowledge_tags.json": [],
                            "question_attributions.json": attributions, "manifest.json": manifest,
                            "vectors.npz.meta.json": chunks}.items():
        with (target / filename).open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    with (target / "vectors.npz").open("xb") as handle:
        np.savez_compressed(handle, vectors=np.asarray([fixture_vector(c["text"]) for c in chunks], dtype=np.float32))
    with (target / "LICENSE.demo.txt").open("x", encoding="utf-8") as handle:
        handle.write(LICENSE)
    with (target / "NOTICE.demo.txt").open("x", encoding="utf-8") as handle:
        handle.write("仅新生成的合成数据采用上述许可，不改变项目代码或第三方依赖许可。\n"
                     "定义映射不是人类评审；向量是固定协议fixture，不是语义embedding。\n"
                     "普通演示必须关闭dense；接真实provider前请重建向量，不用于效果/泛化评分。\n")
    database = target / "cisp_qa.db"
    with database.open("xb"):
        pass
    env = {**os.environ, "CISP_DB_PATH": str(database), "DATABASE_URL": "", "MYSQL_PASSWORD": "",
           "PYTHON_DOTENV_DISABLED": "1", "PYTHONUTF8": "1"}
    subprocess.run([sys.executable, "-B", "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executemany("INSERT INTO questions (id,source,num,stem,options,answer,analysis,domain,needs_review) VALUES (?,?,?,?,?,?,?,?,?)",
            [(q["id"], q["source"], q["num"], q["stem"], json.dumps(q["options"], ensure_ascii=False), q["answer"], q["analysis"], q["domain"], False) for q in questions])
        connection.executemany("INSERT INTO kb_chunks (id,domain,source,page,kind,text) VALUES (?,?,?,?,?,?)",
            [(c["id"], c["domain"], c["source"], c["page"], c["kind"], c["text"]) for c in chunks])
        connection.executemany("INSERT INTO knowledge_points (id,domain,name,description,evidence_count,question_count,status) VALUES (?,?,?,?,?,?,?)",
            [(p["id"], p["domain"], p["name"], p["description"], 1, 2, "active") for p in points])
        connection.executemany("INSERT INTO question_attributions (question_id,candidate_point_id,candidate_domain,attribution_status,agreement_count,evidence_json,reason,classifier_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(a["question_id"], a["candidate_point_id"], a["candidate_domain"], "confirmed", 0, json.dumps(a["evidence"]), a["reason"], a["classifier_version"], 0, 0) for a in attributions])
        connection.executemany("INSERT INTO question_attribution_events (question_id,attribution_status,payload,classifier_version,created_at) VALUES (?,?,?,?,?)",
            [(a["question_id"], "confirmed", json.dumps(a, ensure_ascii=False), a["classifier_version"], 0) for a in attributions])
    return {"output_dir": str(target), "questions": len(questions), "chunks": len(chunks),
            "synthetic_demo": True, "human_reviewed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output_dir), ensure_ascii=False))
