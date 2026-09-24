# -*- coding: utf-8 -*-
"""多轮追问检索评测：指代消解改写后的检索命中率。

真实使用的常见模式是"先问概念 A，再追问 B"（"它和RTO什么关系？""怎么防？"）。
本脚本在真实对话线程上顺序执行两轮，检验追问轮**改写后的检索**是否命中
追问目标的知识页（Top5）。回答走 flash，检索为现网特征序；persist 已隔离。
运行: python tests/eval_multiturn.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

REPORT_PATH = "data/multiturn_report.json"

# (首轮问题, 追问问题, 追问目标证据页 [(source, page), ...])
PAIRS = [
    ("什么是业务影响分析BIA？", "它和RTO是什么关系？",
     [("业务连续性_V4.2", 11), ("业务连续性_V4.1", 11)]),
    ("什么是Kerberos协议？", "它获取TGT时AS会返回哪些信息？",
     [("信息安全支撑技术_V4.1", 47), ("信息安全支撑技术_V4.2", 50)]),
    ("什么是SQL注入攻击？", "应该怎么防御它？",
     [("计算环境安全_v4.1", 50), ("计算环境安全_v4.2", 44)]),
    ("什么是信息安全应急响应？", "它的响应过程分为哪六个阶段？",
     [("业务连续性_V4.2", 39), ("业务连续性_V4.1", 38)]),
    ("什么是网络安全等级保护？", "定级备案的一般流程是什么？",
     [("网络安全监管_v4.2", 52)]),
    ("什么是信息安全风险评估？", "定性和定量的方法有什么区别？",
     [("信息安全评估_V4.2", 37), ("信息安全评估_V4.2", 33)]),
    ("什么是SSE-CMM模型？", "它的能力级别是怎么划分的？",
     [("安全工程与运营_V4.2", 41), ("安全工程与运营_V4.2", 20)]),
    ("防火墙的作用是什么？", "状态检测技术是怎么工作的？",
     [("物理与网络通信安全_V4.2", 46), ("物理与网络通信安全_V4.1", 54)]),
    ("恶意代码是通过什么方式传播的？", "针对它有哪些预防技术？",
     [("计算环境安全_v4.1", 34), ("计算环境安全_v4.2", 32)]),
    ("什么是数字签名？", "PKI体系是怎么支撑它的？",
     [("信息安全支撑技术_V4.2", 24), ("信息安全支撑技术_V4.1", 18)]),
]


async def main() -> None:
    import agents.graph as graph_module
    from agents.graph import run

    async def noop_persist(state) -> dict:
        return {}

    graph_module.persist = noop_persist  # 隔离记忆库

    rows, fails = [], []
    for index, (q1, q2, evidence) in enumerate(PAIRS, 1):
        thread = f"mt-eval-{index}"
        await run(q1, thread_id=thread)
        r2 = await run(q2, thread_id=thread)
        rewritten = r2.get("search_query", q2)
        ctx_pages = [(c["source"], c["page"]) for c in r2.get("contexts", [])][:5]
        ctx_set = set(ctx_pages)
        hit = any((s, p) in ctx_set for s, p in evidence)
        rows.append({"pair": index, "q1": q1, "q2": q2, "rewritten": rewritten,
                     "evidence": [f"{s} p{p}" for s, p in evidence],
                     "ctx_top5": [f"{s} p{p}" for s, p in ctx_pages],
                     "hit": hit})
        mark = "HIT" if hit else "MISS"
        print(f"[{index:02d}/{len(PAIRS)}] {mark} 追问: {q2} | 改写: {rewritten[:40]}", flush=True)
        if not hit:
            fails.append(rows[-1])

    summary = {"pairs": len(PAIRS), "hit_rate": round(
        sum(1 for r in rows if r["hit"]) / len(rows), 3), "failures": fails}
    report = json.dumps(
        {"summary": summary, "rows": rows},
        ensure_ascii=False,
        indent=2,
    )
    await asyncio.to_thread(Path(REPORT_PATH).write_text, report, encoding="utf-8")
    print(json.dumps({"pairs": summary["pairs"], "hit_rate": summary["hit_rate"]},
                     ensure_ascii=False))
    for f in fails:
        print("MISS:", f["q2"], "| 改写:", f["rewritten"][:50], "| 期望:", f["evidence"])
    print(f"报告: {REPORT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
