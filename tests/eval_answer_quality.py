# -*- coding: utf-8 -*-
"""回答层质量评测：忠实度 / 引用真实性 / 要素完整性（LLM-as-judge）。

检索评测（eval_retrieval_gold）证明"上下文里有答案页"，本脚本测量最后一公里：
完整跑 route → rewrite → retrieve → answer 链路，对答案做三层检查——
1. 程序化：出处行是否存在、引用的《文件》第M页是否真的在检索上下文里、
   是否命中 gold 证据页；
2. LLM-judge（独立于回答模型的 deepseek-chat）：忠实度 / 完整性 / 引用支撑；
3. 相关性门限误拒统计（领域内问题被拒答 = 误拒）。

评测期间 persist 节点被替换为空操作，不污染真实记忆库与画像表。
运行: python tests/eval_answer_quality.py [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, ".")

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from tests.eval_retrieval_gold import load_json

HOLDOUT_PATH = "tests/fixtures/retrieval_holdout.json"

REPORT_PATH = "data/answer_quality_report.json"
CITATION_RE = re.compile(r"【出处】(.+)")
CITATION_ITEM_RE = re.compile(r"《(.+?)》第(\d+)页")


def parse_citations(answer: str) -> list[dict]:
    """解析答案末尾的【出处】行 → [{raw, file, page}]；无出处行返回 []。"""
    match = CITATION_RE.search(answer or "")
    if not match:
        return []
    citations = []
    for part in match.group(1).split("；"):
        m = CITATION_ITEM_RE.search(part.strip())
        if m:
            citations.append({"raw": part.strip(), "file": m.group(1), "page": int(m.group(2))})
    return citations


def citation_stats(citations: list[dict], contexts: list[dict],
                   evidence: list[dict]) -> dict:
    """出处三项程序化检查：数量、引用页在上下文中、命中 gold 证据页。"""
    ctx_pages = {(c["source"], c["page"]) for c in contexts}
    gold_pages = {(e["source"], e["page"]) for e in evidence}
    in_context = 0
    hits_gold = False
    for c in citations:
        if any(src.endswith(c["file"]) or c["file"] in src for src, page in ctx_pages
               if page == c["page"]):
            in_context += 1
        if any((src.endswith(c["file"]) or c["file"] in src) and page == c["page"]
               for src, page in gold_pages):
            hits_gold = True
    return {"count": len(citations), "in_context": in_context, "hits_gold": hits_gold}


async def noop_persist(state) -> dict:
    return {}


class JudgeOut(BaseModel):
    faithfulness: int = Field(description="忠实度 0-2：2=答案完全可由资料推出；1=有少量资料外补充但无事实错误；0=含资料中不存在或矛盾的内容")
    completeness: int = Field(description="完整性 0-2：2=覆盖问题的全部关键要素；1=覆盖主要但缺部分；0=答非所问或大量缺失")
    citation_support: int = Field(description="引用支撑 0-2：2=列出的出处页确实支撑答案内容；1=部分支撑或支撑较弱；0=出处与答案无关")
    issues: list[str] = Field(default_factory=list, description="发现的具体问题，没有则空列表")


async def judge_answer(simple_llm, question: str, answer: str, contexts: list[dict]):
    from llms import invoke_structured

    context_text = "\n\n".join(
        f"<chunk id={i} 来源《{c['source']}》第{c['page']}页>\n{c['text'][:600]}\n</chunk>"
        for i, c in enumerate(contexts, 1)
    )
    return await asyncio.to_thread(
        invoke_structured, simple_llm, JudgeOut,
        [SystemMessage(content=(
            "你是严格的质量评审员。给定用户问题、助手回答、以及回答所依据的资料片段，"
            "按三维独立打分（0-2 整数），只在资料可支撑时给高分；答案中出现资料无法支撑的具体事实要扣分。"
            "只输出结构化结果。")),
         HumanMessage(content=f"用户问题: {question}\n\n资料片段:\n{context_text}\n\n助手回答:\n{answer}")],
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只评前 N 题（0=全部）")
    parser.add_argument("--only", default="", help="只评指定 case id（逗号分隔），用于修复前后 A/B")
    parser.add_argument("--output", default=REPORT_PATH)
    args = parser.parse_args()

    import agents.graph as graph_module
    from agents.graph import NO_ANSWER, run

    # 评测隔离：persist 置空，避免污染长期记忆/画像表（图在首次 get 时才编译）
    graph_module.persist = noop_persist

    cases = load_json(HOLDOUT_PATH)
    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[:args.limit]

    from llms import get_simple_llm
    simple = get_simple_llm()

    details, gate_rejected = [], 0
    for index, case in enumerate(cases, 1):
        question = case["question"]
        try:
            result = await run(question, thread_id=f"ansq-eval-{index}")
        except Exception as exc:
            details.append({"id": case["id"], "question": question, "error": str(exc)[:200]})
            print(f"[{index:03d}/{len(cases)}] ERROR {case['id']}")
            continue
        answer = result.get("answer", "")
        contexts = result.get("contexts", [])
        citations = parse_citations(answer)
        cstats = citation_stats(citations, contexts, case["evidence"])
        refused = answer.strip() == NO_ANSWER
        gate_rejected += int(refused)
        verdict = None
        if not refused:
            try:
                verdict = await judge_answer(simple, question, answer, contexts)
            except Exception as exc:
                print(f"[{index:03d}] judge 失败: {exc}")
        details.append({
            "id": case["id"], "question": question, "intent": result.get("intent"),
            "refused": refused, "answer": answer, "citations": citations,
            "citation_stats": cstats, "n_contexts": len(contexts),
            "faithfulness": getattr(verdict, "faithfulness", None),
            "completeness": getattr(verdict, "completeness", None),
            "citation_support": getattr(verdict, "citation_support", None),
            "issues": getattr(verdict, "issues", None) or [],
            "usage": result.get("usage", {}),
        })
        f = getattr(verdict, "faithfulness", "-")
        c = getattr(verdict, "completeness", "-")
        s = getattr(verdict, "citation_support", "-")
        mark = "REFUSED" if refused else "OK"
        print(f"[{index:03d}/{len(cases)}] {mark} F={f} C={c} S={s} "
              f"cites={cstats['count']}/{cstats['in_context']} {case['id']}", flush=True)

    judged = [d for d in details if d.get("faithfulness") is not None]
    answered = [d for d in details if not d.get("refused") and not d.get("error")]
    with_cites = [d for d in answered if d["citation_stats"]["count"]]
    summary = {
        "count": len(details),
        "judged": len(judged),
        "gate_rejected": gate_rejected,
        "faithfulness_avg": round(mean(d["faithfulness"] for d in judged), 3) if judged else None,
        "completeness_avg": round(mean(d["completeness"] for d in judged), 3) if judged else None,
        "citation_support_avg": round(mean(d["citation_support"] for d in judged), 3) if judged else None,
        "faithfulness_0_count": sum(1 for d in judged if d["faithfulness"] == 0),
        "citation_rate": round(len(with_cites) / len(answered), 3) if answered else None,
        "citation_in_context_rate": round(
            mean(d["citation_stats"]["in_context"] / d["citation_stats"]["count"] for d in with_cites), 3) if with_cites else None,
        "citation_hits_gold_rate": round(
            mean(1.0 if d["citation_stats"]["hits_gold"] else 0.0 for d in with_cites), 3) if with_cites else None,
    }
    report = json.dumps(
        {"summary": summary, "details": details},
        ensure_ascii=False,
        indent=2,
    )
    await asyncio.to_thread(Path(args.output).write_text, report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"报告: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
