"""Freeze public inputs and bounded allocations. Does not dispatch providers."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
DOC = Path(__file__).resolve().parent
SOURCE = ROOT / "evaluation/nist-public-http-question-groups-regression40-v3-20261107"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    original = read(SOURCE / "cases.json")
    indexed = {row["id"]: row for row in original}
    turns = [
        ("multi-a1", "notification-to-backup", "ac-8-new-05", "告知是否需要考虑多语言用户？", "Consider needs/demographics; not unconditional multilingual mandate"),
        ("multi-a2", "notification-to-backup", "ac-8-new-08", "那它是否必须每隔五分钟弹出？", "Followup refers to notification; no invented five-minute frequency"),
        ("multi-a3", "notification-to-backup", "cp-9-new-02", "换个话题，系统备份中用户级信息与系统级信息如何区分？", "Switch to CP9; user=other than system, system=OS/middleware/applications/licenses, no notification carryover"),
        ("multi-b1", "backup-to-training", "cp-9-new-08", "系统文档中包含隐私相关文档，也必须备份吗？", "CP9 includes security/privacy system documentation; no leak from the other notification thread"),
        ("multi-b2", "backup-to-training", "cp-9-new-08", "那它的备份频次由谁确定？能不能因此省略备份？", "System-documentation frequency organization-defined; no invented value, obligation remains"),
        ("multi-b3", "backup-to-training", "scenario-03", "再换个话题：只做新员工入职培训、不做后续或变化事件培训，覆盖AT-2吗？", "Switch to AT2; insufficient initial-only plan, recurring and changes/events retained, no backup carryover"),
    ]
    multi = []
    for identifier, group, source_id, question, expectation in turns:
        case = copy.deepcopy(indexed[source_id])
        case.update(id=identifier, thread_group=group, question=question, expectation=expectation,
                    role="AI-authored seen-source multi-turn development; not human gold")
        multi.append(case)
    files = ["agents/graph.py", "agents/sources.py", "agents/prompts.py", "server.py", "llms.py",
             "retrieval/hybrid.py", "retrieval/bm25.py", "scripts/evaluate_public_http.py", "scripts/audit_public_http.py"]
    source_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}
    batches = []
    for cohort, cases, max_calls, allocation in (("40", original, 80, .30), ("multi", multi, 24, .10)):
        for arm in ("baseline", "candidate"):
            name = f"concise-{cohort}-{arm}-20261110"
            packet = ROOT / "evaluation" / name
            packet.mkdir(exist_ok=False)
            for filename in ("chunks.json", "prompts.json"):
                (packet / filename).write_bytes((SOURCE / filename).read_bytes())
            if cohort == "40":
                for filename in ("cases.json", "acceptance.json"):
                    (packet / filename).write_bytes((SOURCE / filename).read_bytes())
            else:
                write(packet / "cases.json", cases)
                write(packet / "acceptance.json", {"scope": "Frozen six turns/two threads, AI development only",
                                                    "cases": [{"id": case["id"], "expectation": case["expectation"]} for case in cases],
                                                    "required": ["proper target and conditions", "no invented values or cross-thread/topic carryover",
                                                                 "durable history pairs", "observed rewrite after turn1"]})
            write(packet / "layout-acceptance.json", {"protocol": "line-v1", "requirements": [
                "citation heading begins its own line", "SSE equals persisted answer SHA", "raw receipts retained",
                "original semantic/source criteria unchanged"], "candidate_adoption": "No automatic default promotion"})
            write(packet / "runtime-source-hashes.json", source_hashes)
            if arm == "candidate":
                (packet / "fallback-system.json").write_bytes((ROOT / "evaluation/live-platform-four-20261110-concise/fallback-system.json").read_bytes())
            write(packet / "funding-preflight.json", {"standing_authorization_usd": 3, "prior_accounted_with_unknown_usd": 1.5105809,
                  "old_unknown_fee_reservation_usd": .0656585, "total_round_allocated_usd": .9,
                  "max_accounted_with_allocations_usd": 2.4105809, "this_allocation_usd": allocation,
                  "max_calls": max_calls, "no_automatic_retry": True, "public_only": True,
                  "input_rate_per_1m_usd": .3, "output_rate_per_1m_usd": 1.2})
            frozen = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in packet.iterdir()}
            manifest = {"role": f"Original frozen {cohort} cohort / {arm}; actual ASGI app, public sources, agent review not human gold",
                        "transport": "in-process ASGI TestClient; actual external provider",
                        "model": "deepseek-flash", "thinking": "disabled", "max_calls": max_calls,
                        "budget_usd": allocation, "max_output_tokens": 1024, "max_attempts_per_call": 1,
                        "retrieval_k": 2, "dense": False, "query_variants": False, "vector_memory": False,
                        "answer_strategy": "default", "answer_temperature": .3, "citation_layout": "line-v1",
                        "acceptance": "Original source/condition/parameter/refusal criteria plus separately frozen layout checks; agent development only",
                        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                        "fallback_system_override": arm == "candidate", "frozen_sha256": frozen,
                        "executor_sha256": source_hashes["scripts/evaluate_public_http.py"]}
            write(packet / "manifest.json", manifest)
            write(packet / "authorization.json", {"max_calls": max_calls, "budget_usd": allocation,
                                                  "manifest_sha256": hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()})
            batches.append({"id": name, "allocation_usd": allocation, "max_calls": max_calls, "status": "frozen_not_dispatched"})
    write(DOC / "budget-ledger.json", {"approved_total_usd": 3, "prior_calls": 2029, "prior_known_estimate_usd": 1.4449224,
          "old_unknown_reservation_usd": .0656585, "prior_accounted_usd": 1.5105809,
          "planned_allocations": batches, "conditional_retrieval_allocation_usd": .1,
          "total_reserved_usd": .9, "max_accounted_usd": 2.4105809, "new_batches": []})
    write(DOC / "continuation.json", {"status": "layout_fixed_packets_frozen", "baseline_commit": manifest["base_commit"],
          "default_db_sha256": hashlib.sha256((ROOT / "data/cisp_qa.db").read_bytes()).hexdigest(),
          "platform_quota_stop_revoked_by_user": True, "new_model_calls": 0, "budget": "budget-ledger.json",
          "next": "Execute frozen original40 baseline/candidate; review and retain all outcomes; then both multi-turn arms and duplicate retrieval diagnostic"})
    print(json.dumps({"packets": [batch["id"] for batch in batches], "reserved_usd": .9, "model_calls": 0}))


if __name__ == "__main__":
    main()
