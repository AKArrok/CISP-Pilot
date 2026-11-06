"""Persist case-by-case Codex source review; no model dispatch or automatic gold."""

import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[3]
packet = ROOT / "evaluation/nist-public-http-cards-v3r2-module-20261107"
run = ROOT / "docs/engineering-delivery/nist-public-http-cards-v3r2-module-20261107-live"
notes = [
    "before granting access，包含公开系统进一步访问；开放题肯定导语生硬，正文明确之前。",
    "保留直到确认使用条件且采取明确登录/进一步访问操作，两个条件均在。",
    "无human interface不要求通知，不附加风险或多语言义务。",
    "公开系统父条件与授权用途子条款同时呈现。",
    "多语言根据组织需求和用户人口特征，无二次告知风险串用。",
    "二次告知依据风险评估，未变成一律强制。",
    "否定提前隐藏，原文两个持续条件完整。",
    "只说本次资料未给五分钟附加规定，保留原有动作。",
    "contractors与managers/senior executives在原文，未排除承包商。",
    "内容取决于组织/系统/远程工作环境；更新事件非穷尽，breaches译为泄露。",
    "给出海报、提醒用品、登录消息、邮件与活动，至少两种；未声称只有两种。",
    "后续可一次或多次简短临时课程，保留may和主题范围。",
    "法规变化可触发更新，不声称是全部事件；未给参数具体值。",
    "明确PII处理，未变成敏感信息无边界泛称。",
    "否定只能海报，列举多个机制且原生参数未赋值。",
    "本次原文没有90分要求；知识测试存在但不能反推分数。",
    "系统状态、OS、中间件、应用和许可证，软件类别完整。",
    "系统级列举与用户级为其他信息的区分，不把组件参数泛化。",
    "数字签名与密码学哈希；未混同机密性加密。",
    "MP-5和SC-8准确；肯定导语不适合哪两个，正文仍直接回答。",
    "除应急计划还有组织备份要求，补充特定信息法律义务而非杜撰。",
    "senior agency official for privacy与legal counsel均有。",
    "无独家厂商规定；三类备份和保护动作仍强制，组件只属用户级。",
    "否定跳过系统文档，安全/隐私文档包括在内，频次未赋值。",
    "超越自动扫描，技能/经验、人为对手动作与深入分析，未称扫描无用。",
    "网络、操作系统和/或应用层技术能力，保留and/or。",
    "限定time/resources/skills影响约束内抵抗评估，未推断无限覆盖。",
    "原文明确迁移尤其重要，给识别可利用弱点与模拟对手的上下文；没有捏造IPv6特有原因。仅支持这一来源层面的解释，不能当详细迁移风险机制答案。",
    "漏洞分析可支持测试，保留can而非唯一前提。",
    "风险评估指导所需独立程度，无一律外部独立要求。",
    "未给七天期限，频次参数与单次持续时间未混为一谈。",
    "内部或外部均可，保留硬件/软件/固件与物理/技术控制。",
]
cases = json.loads((packet / "cases.json").read_text(encoding="utf8"))
rows = json.loads((run / "results.json").read_text(encoding="utf8"))
assert len(cases) == len(rows) == len(notes) == 32
reviews = []
for case, row, note in zip(cases, rows, notes, strict=True):
    assert row["id"] == case["id"]
    answer = "".join(e.get("content", "") for e in row["events"] if e["type"] == "token")
    reviews.append(
        {
            "id": case["id"],
            "question": case["question"],
            "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
            "source_support": True,
            "semantic_supported": True,
            "note": note,
        }
    )
result = {
    "reviewer": "Codex AI manual reading of complete frozen English and actual Chinese/English SSE answers; no paid judge",
    "criteria": "Original frozen response/source/condition/parameter/completeness criteria; no question, label or failure removed",
    "supported": 32,
    "total": 32,
    "retrieval_target_top2": 32,
    "citation_materialization_audit": "audit.json passed32/32",
    "independent_human_gold": False,
    "default_promoted": False,
    "limitations": [
        "Source cards are lengthy and have generic affirmative leads on several open questions.",
        "CA8 migration explanation limited to source statement and general testing purpose, not a detailed IPv6 risk mechanism.",
        "Same32 development questions, one real application run; no independent generalization or production SLA.",
    ],
    "cases": reviews,
}
with (run / "semantic-review.json").open("x", encoding="utf8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
    f.write("\n")


def metrics(directory):
    rs = json.loads((directory / "results.json").read_text(encoding="utf8"))
    elapsed = sorted(r["elapsed_seconds"] for r in rs)
    summary = json.loads((directory / "summary.json").read_text(encoding="utf8"))
    return {
        "requests": len(rs),
        "mean_seconds": statistics.mean(elapsed),
        "median_seconds": statistics.median(elapsed),
        "p95_nearest_rank_seconds": elapsed[-2],
        "max_seconds": max(elapsed),
        "estimate_usd": summary["known_cost_estimate_usd"],
        "attempts": summary["dispatch_attempts"],
        "transport": summary["transport"],
    }


comparison = {
    "baseline": metrics(
        ROOT / "docs/engineering-delivery/nist-public-http-default-baseline-20261106-live"
    ),
    "candidate": metrics(run),
    "semantic_baseline": "31/32 per retained baseline source review",
    "semantic_candidate": "32/32 Codex development source review, not human gold",
    "limits": "Sequential ASGI real-provider comparison on separate times, output shape differs; not causal significance/concurrent throughput/SLA",
}
with (run / "comparison.json").open("x", encoding="utf8") as f:
    json.dump(comparison, f, ensure_ascii=False, indent=2)
    f.write("\n")
print(json.dumps(comparison))
