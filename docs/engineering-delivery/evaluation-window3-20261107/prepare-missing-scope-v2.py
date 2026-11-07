"""Restore old source-backed not_specified; freeze unchanged five focus cases."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


source = ROOT/"evaluation/nist-public-http-question-parts-v1-20261107"
original32 = ROOT/"evaluation/nist-public-http-question-parts-regression32-20261107"
packet = ROOT/"evaluation/nist-public-http-question-parts-missing-v2-20261107"
statefile = HERE/"continuation.json"
state = read(statefile)
b = state["budget"]
assert not b["pending_allocations"] and not state["pending_batches"]
assert b["accounted_with_reservations_usd"]+.08 <= b["approved_total_usd"] == 3
packet.mkdir(exist_ok=False)
for name in ("chunks.json", "prompts.json", "source-units.json", "translations.json", "translation-review.json"):
    shutil.copyfile(source/name, packet/name)
shutil.copyfile(ROOT/"scripts/answer_unit_contract.py", packet/"contract.py")
selector = read(source/"card-selector.json")["evidence_select_system"]
needle = "explain（以原文列举、比较或说明该片段）。"
assert selector.count(needle) == 1
selector = selector.replace(needle, "not_specified（相关资料可见但未给所询的额外规定或具体赋值，须选择相关原文范围）、"+needle)
write(packet/"card-selector.json", {"evidence_select_system": selector})
focus_ids = {"ac-8-new-08", "at-2-new-08", "cp-9-new-07", "ca-8-new-07"}
cases = [case for case in read(original32/"cases.json") if case["id"] in focus_ids]
cases += [case for case in read(source/"cases.json") if case["id"] == "scenario-01"]
assert len(cases) == len({c["id"] for c in cases}) == 5
write(packet/"cases.json", cases)
write(packet/"acceptance.json", {"scope": "Inherited original32 source/citation criteria and scenario-01 explicit approval-form uncertainty. No question/label/threshold edits.",
    "original32_acceptance_sha256": sha(original32/"acceptance.json"),
    "original8_acceptance_sha256": sha(source/"acceptance.json"),
    "source_specific_missing_not_global_absence": True, "no_unrelated_auto_citations": True, "human_gold": False})
controller = (HERE/"dispatch-question-parts-v1.py").read_text(encoding="utf-8").replace(
    "nist-public-http-question-parts-v1-20261107", packet.name).replace(
    "question-parts-v1-console.log", "missing-scope-v2-console.log").replace("16", "10").replace(".12", ".08")
controller_file = HERE/"dispatch-missing-scope-v2.py"
controller_file.write_text(controller, encoding="utf-8")
dependencies = read(source/"runtime-source-hashes.json")
dependencies[controller_file.relative_to(ROOT).as_posix()] = "pending"
write(packet/"runtime-source-hashes.json", {name: sha(ROOT/name) for name in dependencies})
write(packet/"funding-preflight.json", {"prior_accounted_usd": b["accounted_with_reservations_usd"],
    "unknown_reservations": b["unknown_fee_reservations"], "max_calls": 10, "allocation_usd": .08,
    "max_accounted_usd": b["accounted_with_reservations_usd"]+.08, "standing_authorization_usd": 3,
    "scope": "Five unchanged public focus questions, Flash disabledthinking1024/no retries"})
write(packet/"delta.json", {"only_restored_decision": "Old not_specified distinction plus related original scope; undetermined still permits honest evidence-insufficient refusal.",
    "rendering": "not_specified and undetermined references called related context, not proof of global absence",
    "old_regression29of32_retained": True, "same_source_units_translations_and_question_strings": True,
    "no_case_keywords_in_prompt": True, "no_default_promotion": True})
m = read(source/"manifest.json")
m.update(max_calls=10, budget_usd=.08, executor_sha256=sha(ROOT/"scripts/evaluate_public_http.py"),
    role="Same five focus questions; restore source-specific missing decision after original32 regression failure; no grade edits.",
    original_cases_sha256=sha(original32/"cases.json"), base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())
m["frozen_sha256"] = {path.name: sha(path) for path in packet.iterdir() if path.is_file()}
write(packet/"manifest.json", m)
write(packet/"authorization.json", {"max_calls": 10, "budget_usd": .08, "manifest_sha256": sha(packet/"manifest.json")})
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(),
    "output": f"docs/engineering-delivery/{packet.name}-live", "status": "frozen_missing_scope_v2", "max_calls": 10, "budget_usd": .08}
state["pending_batches"] = [state["active_experiment"]]
b["pending_allocations"] = [{"batch": packet.name, "max_calls": 10, "budget_usd": .08}]
write(statefile, state)
print(json.dumps({"cases": 5, "max_calls": 10, "allocation_usd": .08, "max_accounted_usd": b["accounted_with_reservations_usd"]+.08}))
