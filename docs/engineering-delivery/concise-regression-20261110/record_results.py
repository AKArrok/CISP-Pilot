"""Record completed public runs and source reviews; no model calls or business DB writes."""
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
DOC = Path(__file__).resolve().parent
ACTOR = "codex-development-source-review"
DATASET = "public-nist-concise-regression"
BATCHES = {
    "baseline40": "concise-40-baseline-20261110",
    "candidate40": "concise-40-candidate-20261110",
    "baseline-multi": "concise-multi-baseline-20261110",
    "candidate-multi": "concise-multi-candidate-20261110",
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


NOTES = {
    "ac-8": ["访问前通知并保留到确认及明确操作。", "保留到确认及明确操作，未省略双条件。",
             "无人机/人工界面用词瑕疵另记；核心人机界面例外正确，未作所有访问都免告知的泛化。",
             "公开系统须描述授权用途。", "保留需求/用户人口构成及consider条件。",
             "二次通知以风险评估为考虑条件。", "确认前不得隐藏，参数内容未编造。", "否定五分钟要求，不将其他参数充作通知频次。"],
    "at-2": ["承包商明确包含。", "承认远程办公影响内容确定；区分组织定义更新事件与未明文规定的自动触发。",
             "列举了五种有源示例，超过所问两种，属于精简性问题。", "允许短临时培训，保留最低频率及组织参数。",
             "法律变化为可能触发事件，未变成唯一触发。", "包含PII处理及用户行动。",
             "否定海报唯一性，技术参数不等同固定方式；01/02确为培训频次参数。",
             "未捏造90分；源文虽提知识测试，未要求统一考试分数。"],
    "cp-9": ["系统状态、OS、中间件、应用、许可证。", "系统级列举与用户级排除定义准确。",
             "数字签名与密码哈希为完整性示例。", "传输保护关联MP-5与SC-8。", "应急计划之外的组织及外部合规要求。",
             "隐私高级官员及法律顾问；中文首席称谓不作为指定职位证明。", "未虚构唯一供应商，范围与频次未当采购来源。",
             "隐私相关系统文档不可跳过，待定义频次不使动作可选。"],
    "ca-8": ["超越扫描、验证漏洞/抗渗透，技能与约束需区分。", "网络、OS、应用安全技能与经验，独立性不等于技能。",
             "约束包含时间资源技能；缺少具体影响机制时限定范围，通用补充须标明。", "IPv4到IPv6是资料中的迁移示例。",
             "漏洞分析结果可支持渗透测试。", "风险评估指导独立程度，未给等级。", "未虚构七天，频次与测试对象参数分开。",
             "内部和外部均可，独立性与物理实施位置不同。"],
}
SCENARIOS = [
    "成员条件与批准主体已区分，但主结论把资料未规定的口头/书面形式说成确定性禁止；基线自身后文承认形式未规定，候选进一步称书面批准要求。源支持不足，判失败。",
    "公开系统隐私一致性条件保留，不一律允许或绝对禁止监控引用。",
    "初始、后续及变化/组织事件培训均须覆盖，参数未赋值不使动作可选。",
    "三类对象区分且许可证属系统级，不编造频次；基线将保护也称第四类对象是表述瑕疵，后文正确区分保护动作。",
    "扫描不能替代渗透且交战规则在开始前商定。",
    "风险变化须调整级别，指南频率/范围/深度可调整，保留两层规范力度。",
    "联邦机构/PII条件保留，授权用户为未授权目的访问亦包含。",
    "不用网络为本地，经网络为网络访问，经外部网络为远程；物理位置不决定。",
]
MULTI = ["保留多语言考虑条件。", "它正确承接系统通知，未虚构五分钟频次。",
         "切到CP-9后正确区分系统与用户信息，无通知话题污染。", "另一会话从隐私文档备份开始，不受前会话影响。",
         "承接备份对象，组织确定频次，不能省略动作。", "切到AT-2后保留后续/事件培训要求，无备份话题污染。"]


def main():
    from scripts.package_evaluation import package
    from scripts.package_data_catalog import package as knowledge_package
    from storage import evaluation_store as store, evaluation_workspace as workspace, data_catalog as catalog

    os.environ["CISP_EVAL_DB_PATH"] = str(ROOT / "data/evaluation/concise-regression-20261110/platform.sqlite3")
    workspace.migrate()
    catalog.migrate()
    snapshot = catalog.import_snapshot(knowledge_package(
        ROOT / "evaluation/concise-40-baseline-20261110/chunks.json", namespace=DATASET,
        label="public frozen NIST101 / concise regression", visibility="public"), ACTOR)
    report = {"model_calls_in_recording": 0, "independent_human_gold": False, "snapshot": snapshot, "runs": {}}
    diag = {row["id"]: row for row in read(DOC / "retrieval-diagnostic.json")["public_cases"]}
    ledger = read(DOC / "budget-ledger.json")
    ledger["new_batches"] = []
    for name, packet_name in BATCHES.items():
        run, packet = DOC / name, ROOT / "evaluation" / packet_name
        rows = read(run / "results.json")
        reviews = []
        preview_matches = []
        lengths = []
        for row in rows:
            answer = "".join(e.get("content", "") for e in row["events"] if e["type"] == "token")
            identifier = row["id"]
            passed = True
            if "multi" in name:
                note = MULTI[int(identifier[-1]) - 1 + (3 if identifier.startswith("multi-b") else 0)]
            elif identifier.startswith("scenario-"):
                note = SCENARIOS[int(identifier[-2:]) - 1]
                passed = identifier != "scenario-01"
            else:
                group, number = identifier.split("-new-")
                note = NOTES[group][int(number) - 1]
                if name == "baseline40" and identifier == "ca-8-new-01":
                    passed = False
                    note += "但把自动扫描说成渗透的起点/基础手段，goes beyond不支持该确定性层级关系，额外主张判失败。"
                if name == "candidate40" and identifier == "ac-8-new-03":
                    note = "人机界面例外及风险考虑正确；另有机械引用定位ac-8_gdn不匹配，不能以语义正确覆盖引用失败。"
                if name == "candidate40" and identifier == "ac-8-new-08":
                    note += "出处虽已换行，仍插在句中且后跟句号/正文，完整末尾出处段未达成，不自动晋级。"
            reviews.append({"id": identifier, "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                            "source_supported": passed, "note": note})
            lengths.append(len(answer))
            if identifier in diag:
                contexts = next(e["contexts"] for e in row["events"] if e["type"] == "contexts")
                preview_matches.append({"id": identifier, "offline_equals_actual_contexts": contexts == diag[identifier]["actual_previews"]})
        write(run / "semantic-review.json", {"reviewer": "Codex source-based development review (AI; not independent human gold)",
              "policy": "Frozen source/condition/parameter/refusal criteria; inspect extra unsupported claims in both arms. Layout and exact citation scored separately. No labels or thresholds changed.",
              "cases": reviews})
        bundle = package(packet, run, 2, DATASET)
        write(run / "platform-bundle.json", bundle.model_dump())
        imported = store.import_bundle(bundle, ACTOR)
        with store.database() as connection:
            counts = workspace.summary(connection, imported["id"])
        summary = read(run / "summary.json")
        report["runs"][name] = {**imported, "summary": counts, "source_review_passed": sum(r["source_supported"] for r in reviews),
                                 "median_answer_characters": statistics.median(lengths), "mean_answer_characters": statistics.mean(lengths),
                                 "median_elapsed_seconds": statistics.median(r["elapsed_seconds"] for r in rows),
                                 "retrieval_replay": preview_matches}
        ledger["new_batches"].append({"id": packet_name, **summary})
    report["comparisons"] = {key: workspace.compare(report["runs"][a]["id"], report["runs"][b]["id"])
                             for key, a, b in [("original40", "baseline40", "candidate40"), ("multi", "baseline-multi", "candidate-multi")]}
    # These cohorts both include the layout fix. Do not pretend this measures its causal effect.
    report["layout_causal_evidence"] = "layout-red.log, layout-http-final.log, targeted-tests.log; both new paid arms use line-v1"
    report["improvements"] = []
    for tag, name, case, category, hypothesis, evidence in [
        ("citation", "candidate40", "ac-8-new-03", "evidence", "精简提示仍可能虚构出处定位，不能晋级默认。", "candidate40/audit.json: citation_structure=false; ac-8_gdn vs provided ac-8"),
        ("oral", "baseline40", "scenario-01", "generation", "精简不修复规范力度扩展，口头/书面要求需保留资料边界。", "两臂source review均失败，见semantic-review.json"),
        ("duplicate", "baseline40", "ac-8-new-01", "retrieval", "静态重复告警不等于最终上下文重复，保留已有父段折叠。", "retrieval-diagnostic.json: finalTop2 duplicates 0/40; without fold 38/40; no retriever changes"),
    ]:
        item = catalog.create_item(catalog.Improvement(request_id="concise-regression-" + tag,
            run_id=report["runs"][name]["id"], case_id=case, attempt=1, snapshot_id=snapshot["id"], category=category,
            hypothesis=hypothesis, evidence=evidence,
            acceptance="同冻结输入和标准复测；区分格式、源支持、出处及检索；不把AI开发集当人工验收。"), ACTOR)
        event = catalog.append_event(item["id"], catalog.Event(request_id="concise-note-" + tag, revision=item["revision"],
            action="investigating" if tag != "duplicate" else "note", note=evidence + "；结果与局限保留，未部署。"), ACTOR)
        if tag == "oral":
            event = catalog.append_event(item["id"], catalog.Event(request_id="concise-retest-oral", revision=event["revision"],
                action="retest", candidate_run=report["runs"]["candidate40"]["id"], note="两臂仍未解决，不据缩短篇幅标记fixed。"), ACTOR)
        report["improvements"].append(event)
    # Bind the previous raw inline answer without rewriting its immutable old review or run.
    old_packet = ROOT / "evaluation/live-platform-four-20261110-concise"
    old_run = ROOT / "docs/engineering-delivery/live-platform-four-20261110/candidate"
    old = store.import_bundle(package(old_packet, old_run, 2, DATASET), ACTOR)
    item = catalog.create_item(catalog.Improvement(request_id="concise-regression-layout",
        run_id=old["id"], case_id="cp-9-new-08", attempt=1, snapshot_id=snapshot["id"], category="generation",
        hypothesis="对行内出处做确定性换行，同时转换SSE与SQL；原回执保留。",
        evidence="上轮真实行内出处、layout-red.log复现；本轮92/92换行及SSE/SQL一致。",
        acceptance="全部分片位置及实际HTTP流测试通过；新旧判据不同，不把旧绿灯改成新旧同标准fixed。"), ACTOR)
    report["improvements"].append(catalog.append_event(item["id"], catalog.Event(request_id="concise-layout-note",
        revision=item["revision"], action="note", note="局部格式修复通过；原40候选有定位失败和句中出处，精简提示不晋级。"), ACTOR))
    report["historical_candidate_run"] = old
    ledger["known_estimate_usd"] = ledger["prior_known_estimate_usd"] + sum(b["known_cost_estimate_usd"] for b in ledger["new_batches"])
    ledger["accounted_with_old_unknown_usd"] = ledger["known_estimate_usd"] + ledger["old_unknown_reservation_usd"]
    ledger["pending_allocations_usd"] = 0
    for batch in ledger["planned_allocations"]:
        batch["status"] = "completed"
    ledger["new_calls"] = sum(b["dispatch_attempts"] for b in ledger["new_batches"])
    ledger["new_cost_estimate_usd"] = sum(b["known_cost_estimate_usd"] for b in ledger["new_batches"])
    ledger["remaining_usd"] = 3 - ledger["accounted_with_old_unknown_usd"]
    write(DOC / "budget-ledger.json", ledger)
    report["default_db_sha256_after"] = hashlib.sha256((ROOT / "data/cisp_qa.db").read_bytes()).hexdigest()
    assert report["default_db_sha256_after"] == "3d6d78663b9378d8791f1692afcdd702c9476866d5458c3492f307766e9d5b62"
    write(DOC / "platform-records.json", report)
    print(json.dumps({"runs": {k: v["summary"]["counts"] for k, v in report["runs"].items()},
                      "new_calls": ledger["new_calls"], "new_cost": ledger["new_cost_estimate_usd"]}))


if __name__ == "__main__":
    main()
