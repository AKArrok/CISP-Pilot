"""Freeze the original8, source-only unit catalog and evidence-first workflow."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.answer_unit_contract import build_catalog  # noqa: E402
from scripts.eval_nist_batch import request_content  # noqa: E402
import tiktoken  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


prior = ROOT / "evaluation/nist-native-ownership-noaid-20261107"
packet = ROOT / "evaluation/nist-unit-evidence-first-v1-20261107"
packet.mkdir(exist_ok=False)
shutil.copyfile(prior / "chunks.json", packet / "chunks.json")
shutil.copyfile(prior / "cases.json", packet / "original-cases.json")
shutil.copyfile(ROOT / "docs/engineering-delivery/evaluation-resume-20261106/literal-source-units.json", packet / "source-units.json")
shutil.copyfile(ROOT / "scripts/answer_unit_contract.py", packet / "contract.py")
cases = read(prior / "cases.json")
sources = read(packet / "source-units.json")["sources"]
for case in cases:
    keys = {(source["source_url"], source["locator"], hashlib.sha256(source["text"].encode()).hexdigest())
            for source in case["fixed_contexts"]}
    case["unit_sources"] = [source for source in sources
        if (source["source_url"], source["locator"], source["canonical_text_sha256"]) in keys]
write(packet / "cases.json", cases)
old_prompts = read(prior / "prompts.json")
head, _, tail = old_prompts["answer_structured_system"].partition("\n4. ")
_, _, tail = tail.partition("\n5. ")
rule = ("只输出JSON，字段仅answer和unit_ids。answer是完整中文解释（非逐字原文）；unit_ids是实际支持解释的所选原文单元ID字符串数组，"
        "不能引用selected_evidence以外的单元或自行输出引文/出处。程序按ID复制原文与来源。selected_evidence.insufficient为true时须限定"
        "具体证据缺口，未获支持的部分不补造；没有支持单元时unit_ids为空。parameter_definitions是所选原生参数的原始定义，"
        "referenced_unit_ids指明实际引用它的所选动作，未赋值参数不得当作已确定的值。")
prompts = {"answer_baseline_system": old_prompts["answer_baseline_system"],
    "evidence_select_system": "你是原文证据选择器，不生成答案。仅从sources.units选择直接回答问题所需的全部原文单元，并保留必须的适用条件、前文指代和原生参数定义。单元并非独立事实，必要时应一起选择；不能将邻句前提移用到其他陈述。只输出JSON，字段仅unit_ids（原始ID字符串数组，无重复）和insufficient（布尔值）。证据不足时如实标记，仍可选择支持已知部分的单元；完全没有支持时数组为空。不要添加原文没有的定义、条件、数字或组织参数值。",
    "answer_candidate_system": head + "\n4. " + rule + "\n5. " + tail}
write(packet / "prompts.json", prompts)
dependencies = ["scripts/answer_unit_contract.py", "scripts/inline_oscal_parameters.py",
                "scripts/answer_output_contract.py", "scripts/answer_grade_contract.py", "agents/sources.py"]
write(packet / "runtime-source-hashes.json", {name: sha(ROOT / name) for name in dependencies})
state = read(Path(__file__).with_name("continuation.json"))
budget = state["budget"]
assert not budget["pending_allocations"] and not state["pending_batches"]
held = budget["known_estimate_usd"] + sum(row["additional_reservation_usd"] for row in budget["unknown_fee_reservations"])
assert abs(held - budget["accounted_with_reservations_usd"]) < 1e-9 and held + .12 <= budget["approved_total_usd"]
write(packet / "funding-preflight.json", {"known_estimate_usd": budget["known_estimate_usd"],
    "unknown_reservations": budget["unknown_fee_reservations"], "prior_accounted_usd": held,
    "new_allocation_usd": .12, "max_accounted_usd": held + .12, "approved_total_usd": 3,
    "authorization": "standing user3USD; new24call batch; no old-lock retry or reset"})
manifest = {"mode": "answer_unit_pipeline", "evidence_protocol": "answer_source_lines",
    "guidance_segments": True, "native_parameter_bindings": True, "answer_temperature": .3,
    "selector_temperature": 0, "max_estimated_input_tokens": 5500,
    "executor_sha256": sha(ROOT / "scripts/eval_nist_batch.py"),
    "paid_proposal": {"calls": 24, "budget_usd": .12, "model": "deepseek-flash", "thinking": "disabled",
        "max_output_tokens": 1024, "max_retries": 0, "scope": "Public pinned NIST only, original8, baseline+unit selection+answer"},
    "acceptance": "Original8 frozen semantic/completeness/condition/parameter/terminology/citation criteria, strict unit contract. All failures retained. Repeat/negative/full32/mixed4 before default promotion; AI diagnostic not human gold."}
encoding = tiktoken.get_encoding("cl100k_base")
estimates = []
for case, original in zip(cases, read(packet / "original-cases.json"), strict=True):
    assert {key: value for key, value in case.items() if key != "unit_sources"} == original
    row = {"contexts": case["fixed_contexts"]}
    catalog = build_catalog(row["contexts"], case["unit_sources"])
    row["evidence_select"] = json.dumps({"unit_ids": list(catalog), "insufficient": False})
    for stage in ("answer_baseline", "evidence_select", "answer_candidate"):
        content = request_content(stage, case, row, "", manifest)
        tokens = len(encoding.encode(content)) + len(encoding.encode(prompts[stage + "_system"]))
        estimates.append({"id": case["id"], "stage": stage, "tokens": tokens,
                          "candidate_preflight": "all selectable units, including parameter closure" if stage == "answer_candidate" else "exact request"})
assert max(row["tokens"] for row in estimates) <= 5500
write(packet / "input-preflight.json", {"estimates": estimates, "max_input": max(row["tokens"] for row in estimates),
    "all_candidate_units_preflight": True, "same_questions_sources_and_labels": True, "model_calls": 0})
manifest["frozen_sha256"] = {path.name: sha(path) for path in packet.iterdir() if path.is_file()}
write(packet / "manifest.json", manifest)
write(packet / "authorization.json", {"max_calls": 24, "budget_usd": .12, "manifest_sha256": sha(packet / "manifest.json")})
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(),
    "output": "docs/engineering-delivery/nist-unit-evidence-first-v1-20261107-live", "status": "frozen_preflight_passed", "max_calls": 24, "budget_usd": .12}
state["pending_batches"] = [state["active_experiment"]]
budget["pending_allocations"] = [{"batch": packet.name, "max_calls": 24, "budget_usd": .12}]
write(Path(__file__).with_name("continuation.json"), state)
print(json.dumps({"packet": packet.name, "calls": 24, "ceiling": .12,
    "max_input": max(row["tokens"] for row in estimates), "accounted_max": held + .12}))
