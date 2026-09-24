# -*- coding: utf-8 -*-
"""问法鲁棒性评测：模拟真实用户的提问风格，测量检索相对规范问法的退化。

真实用户不会按教科书句式提问。本脚本用 LLM 把 holdout 题改写成三种真实风格变体
（口语随意 / 带错别字 / 极简省略），在默认生产配置（融合精排）下检索，
与规范问法的成绩对比，量化"问法鲁棒性"。

变体与检索分全部落盘缓存（data/embedding_cache.json / data/rerank_cache.json），
重复运行零 API 消耗。
运行: python tests/eval_query_robustness.py [--per-domain N] [--regen]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from statistics import mean

sys.path.insert(0, ".")

import config
from tests.eval_retrieval_gold import load_json, resolve_relevant_parent_ids

HOLDOUT_PATH = "tests/fixtures/retrieval_holdout.json"
VARIANTS_PATH = "data/query_variants.json"

STYLES = {
    "colloquial": "口语随意地问，像平时聊天那样，可以加语气词，但意思不变",
    "typo": "故意带 2-3 个错别字或形近字错误，像拼音输入法打错的样子，意思不变",
    "terse": "极简省略地问，只留核心词，像搜索框里敲的短语，但考点不变",
}


async def generate_variants(cases: list[dict], per_domain: int) -> list[dict]:
    """每个知识域抽 per_domain 题，每题生成 3 种风格变体。"""
    from llms import get_simple_llm, invoke_structured
    from langchain_core.messages import HumanMessage, SystemMessage
    from pydantic import BaseModel, Field

    class Variants(BaseModel):
        colloquial: str = Field(description="口语随意版")
        typo: str = Field(description="带错别字版")
        terse: str = Field(description="极简省略版")

    # 每域均匀抽样
    by_domain: dict[str, list[dict]] = {}
    for case in cases:
        by_domain.setdefault(case["primary_domain"], []).append(case)
    sampled = []
    for _domain, items in sorted(by_domain.items()):
        sampled.extend(items[:per_domain])

    simple = get_simple_llm()
    out = []
    for case in sampled:
        prompt = "把下面这个问题改写成三种问法变体，保持考点与答案不变，只改问法。\n"
        for _key, desc in STYLES.items():
            prompt += f"- {desc}\n"
        prompt += f"\n原问题: {case['question']}"
        try:
            v = await asyncio.to_thread(
                invoke_structured, simple, Variants,
                [SystemMessage(content="只输出结构化结果。"),
                 HumanMessage(content=prompt)],
            )
            variants = {"colloquial": v.colloquial, "typo": v.typo, "terse": v.terse}
        except Exception as exc:
            print(f"  变体生成失败 {case['id']}: {exc}")
            continue
        out.append({"id": case["id"], "question": case["question"],
                    "primary_domain": case["primary_domain"], "variants": variants})
        print(f"  {case['id']}: {variants['terse'][:30]}", flush=True)
    return out


def evaluate_variants(variants_data: list[dict], cases: list[dict], chunks: list[dict]) -> dict:
    """变体查询在默认生产配置下检索，统计 R1/R5 与相对规范问法的退化。"""
    from retrieval.hybrid import HybridRetriever

    retriever = HybridRetriever.get()
    case_by_id = {c["id"]: c for c in cases}
    rows = []
    for entry in variants_data:
        case = case_by_id[entry["id"]]
        relevant = resolve_relevant_parent_ids(chunks, case)
        for style, query in entry["variants"].items():
            results = retriever.retrieve(query, top_k=10)
            parent_ids = [r.get("parent_id", r["id"]) for r in results]
            rank = next((i + 1 for i, pid in enumerate(parent_ids) if pid in relevant), None)
            rows.append({"id": entry["id"], "style": style, "query": query,
                         "first_relevant_rank": rank,
                         "recall_at_1": 1.0 if rank == 1 else 0.0,
                         "recall_at_5": 1.0 if rank and rank <= 5 else 0.0})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-domain", type=int, default=3)
    parser.add_argument("--regen", action="store_true", help="强制重新生成变体")
    args = parser.parse_args()

    cases = load_json(HOLDOUT_PATH)
    chunks = load_json(config.KB_CHUNKS_PATH)

    if args.regen:
        variants_data = []
    else:
        try:
            variants_data = load_json(VARIANTS_PATH)
        except FileNotFoundError:
            variants_data = []
    if not variants_data:
        print("生成问法变体（LLM）...")
        variants_data = asyncio.run(generate_variants(cases, args.per_domain))
        with open(VARIANTS_PATH, "w", encoding="utf-8") as fp:
            json.dump(variants_data, fp, ensure_ascii=False, indent=2)
        print(f"变体已存 {VARIANTS_PATH}: {len(variants_data)} 题 × 3 风格")

    print("变体检索评测（默认生产配置，缓存命中零消耗）...")
    rows = evaluate_variants(variants_data, cases, chunks)

    # 规范问法基线：同一批题在 v11 报告中的成绩
    baseline = {}
    try:
        v11 = load_json("data/retrieval_holdout_report_v11_100.json")
        for d in v11["details"]:
            baseline[d["id"]] = (d["recall_at_1"], d["recall_at_5"])
    except FileNotFoundError:
        pass

    by_style: dict[str, list[dict]] = {}
    for row in rows:
        by_style.setdefault(row["style"], []).append(row)

    print(f"\n{'问法':<12} {'R1':>8} {'R5':>8} {'n':>4}")
    print("-" * 36)
    for style, items in by_style.items():
        print(f"{style:<12} {mean(i['recall_at_1'] for i in items):>8.3f} "
              f"{mean(i['recall_at_5'] for i in items):>8.3f} {len(items):>4}")
    if baseline:
        ids = {e["id"] for e in variants_data}
        base_rows = [baseline[i] for i in ids if i in baseline]
        print(f"{'规范问法':<10} {mean(b[0] for b in base_rows):>8.3f} "
              f"{mean(b[1] for b in base_rows):>8.3f} {len(base_rows):>4}")

    failures = [r for r in rows if not r["recall_at_5"]]
    print(f"\n变体未进 Top5: {len(failures)}")
    for f in failures[:10]:
        print(f"  [{f['style']}] {f['query'][:40]} -> rank={f['first_relevant_rank'] or '不在Top10'} ({f['id']})")

    with open("data/query_robustness_report.json", "w", encoding="utf-8") as fp:
        json.dump({"rows": rows, "variants_data": variants_data}, fp, ensure_ascii=False, indent=2)
    print("报告: data/query_robustness_report.json")


if __name__ == "__main__":
    main()
