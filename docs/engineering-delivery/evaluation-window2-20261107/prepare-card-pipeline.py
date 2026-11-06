"""Freeze a typed source-card candidate without changing the original8 criteria."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.answer_unit_contract import build_catalog, materialize_card_selection, render_card_answer  # noqa: E402
from scripts.eval_nist_batch import request_content  # noqa: E402
from scripts.inline_oscal_parameters import PARAMETER  # noqa: E402
import tiktoken  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


prior = ROOT / "evaluation/nist-unit-evidence-first-v1-20261107"
packet = ROOT / "evaluation/nist-unit-cards-v1-20261107"
packet.mkdir(exist_ok=False)
for name in ("cases.json", "chunks.json", "original-cases.json", "source-units.json"):
    shutil.copyfile(prior / name, packet / name)
shutil.copyfile(Path(__file__).with_name("bilingual-units-r2.json"), packet / "translations.json")
shutil.copyfile(Path(__file__).with_name("translation-review-r2.json"), packet / "translation-review.json")
shutil.copyfile(ROOT / "scripts/answer_unit_contract.py", packet / "contract.py")
translations = read(packet / "translations.json")
units = {unit["id"]: unit for source in read(packet / "source-units.json")["sources"] for unit in source["units"]}
for key, value in translations["units"].items():
    assert hashlib.sha256(units[key]["text"].encode()).hexdigest() == value["english_sha256"]
    assert sorted(PARAMETER.findall(units[key]["text"])) == sorted(PARAMETER.findall(value["reading_translation"]))
prompts = {"answer_baseline_system": read(prior / "prompts.json")["answer_baseline_system"],
    "evidence_select_system": "你是来源证据选择器。只依据给定sources.units，选择直接回答问题所需的全部原文及必要前提、前文指代、参数定义。单元不一定是独立事实，不得把邻句的前提移给其他陈述。仅输出JSON，字段为unit_ids（无重复的原始ID字符串数组）和answer_type。answer_type仅可为：affirm（资料支持被问命题，保留适用条件）；deny（资料给出与被问命题相反的要求）；not_specified（资料未作出所询规定或具体取值）；explain（以所选原文列举、比较或说明）；insufficient（资料不足以回答该问题）。除insufficient外，须选择支持判断的原文。不要生成中文解释、引文或额外字段。"}
write(packet / "prompts.json", prompts)
dependencies = ["scripts/answer_unit_contract.py", "scripts/inline_oscal_parameters.py", "scripts/answer_output_contract.py",
                "scripts/answer_grade_contract.py", "agents/sources.py"]
write(packet / "runtime-source-hashes.json", {name: sha(ROOT / name) for name in dependencies})
state = read(Path(__file__).with_name("continuation.json"))
b = state["budget"]
assert not b["pending_allocations"] and not state["pending_batches"]
held = b["known_estimate_usd"] + sum(x["additional_reservation_usd"] for x in b["unknown_fee_reservations"])
assert abs(held-b["accounted_with_reservations_usd"]) < 1e-9 and held+.08 <= b["approved_total_usd"]
write(packet / "funding-preflight.json", {"known_estimate_usd": b["known_estimate_usd"], "unknown_reservations": b["unknown_fee_reservations"],
    "prior_accounted_usd": held, "new_allocation_usd": .08, "max_accounted_usd": held+.08, "approved_total_usd": 3,
    "authorization": "standing user3USD; new16calls, not old lock retry"})
manifest = {"mode": "answer_card_pipeline", "evidence_protocol": "answer_source_lines", "guidance_segments": True,
    "native_parameter_bindings": True, "answer_temperature": .3, "selector_temperature": 0,
    "max_estimated_input_tokens": 5500, "executor_sha256": sha(ROOT / "scripts/eval_nist_batch.py"),
    "paid_proposal": {"calls": 16, "budget_usd": .08, "model": "deepseek-flash", "thinking": "disabled",
        "max_output_tokens": 1024, "max_retries": 0, "scope": "Public NIST only, original8 baseline+typed selection; host cards zero generation calls"},
    "acceptance": "Original8 factual/completeness/condition/parameter/terminology/citation criteria unchanged; typed lead must answer actual question, selected translations+original must cover it. No format-only pass. Repeat/negative/full32/mixed4 required before promotion.",
    "limitations": "Bounded evidence response candidate with reduced free-form teaching; not a replacement of full project scope or independent human gold. No default change."}
encoding = tiktoken.get_encoding("cl100k_base")
estimates = []
for case, original in zip(read(packet / "cases.json"), read(packet / "original-cases.json"), strict=True):
    assert {key: value for key, value in case.items() if key != "unit_sources"} == original
    row = {"contexts": case["fixed_contexts"]}
    catalog = build_catalog(row["contexts"], case["unit_sources"])
    # Offline only: materialize all primary-source units to validate full translation coverage.
    primary = [key for key, unit in catalog.items() if unit["source_id"] == 1]
    selected = materialize_card_selection(json.dumps({"unit_ids": primary, "answer_type": "explain"}), catalog, row["contexts"])
    render_card_answer(selected, row["contexts"], translations)
    for stage in ("answer_baseline", "evidence_select"):
        content = request_content(stage, case, row, "", manifest)
        estimates.append({"id": case["id"], "stage": stage,
            "tokens": len(encoding.encode(content)) + len(encoding.encode(prompts[stage + "_system"]))})
assert max(x["tokens"] for x in estimates) <= 5500
write(packet / "input-preflight.json", {"estimates": estimates, "max_input": max(x["tokens"] for x in estimates),
    "same_questions_sources_labels": True, "all69translated_units_bound": True, "parameter_tokens_preserved": True,
    "translation_review": "Codex AI source-only review, not independent human gold", "model_calls": 0})
manifest["frozen_sha256"] = {path.name: sha(path) for path in packet.iterdir() if path.is_file()}
write(packet / "manifest.json", manifest)
write(packet / "authorization.json", {"max_calls": 16, "budget_usd": .08, "manifest_sha256": sha(packet / "manifest.json")})
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(),
    "output": "docs/engineering-delivery/nist-unit-cards-v1-20261107-live", "status": "frozen_preflight_passed", "max_calls": 16, "budget_usd": .08}
state["pending_batches"] = [state["active_experiment"]]
b["pending_allocations"] = [{"batch": packet.name, "max_calls": 16, "budget_usd": .08}]
write(Path(__file__).with_name("continuation.json"), state)
print(json.dumps({"packet": packet.name, "max_input": max(x["tokens"] for x in estimates), "accounted_max": held+.08}))
