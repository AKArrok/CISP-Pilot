"""Freeze unchanged context/unknown/alternative focus questions for scope grouping."""
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


source = ROOT/"evaluation/nist-public-http-question-parts-missing-v2-20261107"
original40 = ROOT/"evaluation/nist-public-http-question-parts-regression40-v2-20261107"
packet = ROOT/"evaluation/nist-public-http-question-groups-focus-v3-20261107"
statefile = HERE/"continuation.json"
state = read(statefile)
b = state["budget"]
assert not b["pending_allocations"] and not state["pending_batches"]
assert b["accounted_with_reservations_usd"]+.08 <= b["approved_total_usd"] == 3
packet.mkdir(exist_ok=False)
for name in ("chunks.json", "prompts.json", "source-units.json", "translations.json", "translation-review.json"):
    shutil.copyfile(source/name, packet/name)
shutil.copyfile(ROOT/"scripts/answer_unit_contract.py", packet/"contract.py")
selector = (
    "你是来源证据选择器。宿主question_parts是按标点分出的原文片段，不等于独立事实。"
    "先将相邻片段合为完整的待回答命题，把背景条件与它限定的提问放在一起判断；不要孤立肯定用户背景或方案。"
    "可确定事项与未知事项分别归组，不能把局部未知泛化成整题缺证据。"
    "只能引用原ID，全部原片段须恰好覆盖一次，组内相邻且保原序；不删除限定词、不改写原问题。"
    "仅输出JSON：{\"parts\":[{\"part_ids\":[\"相邻原片段ID\"],\"decision\":\"类型\",\"unit_ids\":[\"原单元ID\"]}]}。"
    "decision仅为context（纯用户设定背景，不作为资料结论验证，unit_ids必须为空）、"
    "affirm（资料支持分组后的完整命题）、deny（完整命题与原文要求相反）、"
    "undetermined（本次资料不足以确定该完整提问）、"
    "not_specified（相关资料可见但未给所询额外规定或具体赋值，须选相关原文范围）、"
    "explain（列举、比较、二选一或开放式说明，以原文回答）。"
    "只有context和undetermined可用空unit_ids；其他类型必须选择直接相关原文及必要前提、指代和参数定义。"
    "同组不得重复单元ID。未说明不等于禁止或允许，参数值待定不表示动作可选。"
    "不输出自由解释、引文、问题文字或额外字段。"
)
write(packet/"card-selector.json", {"evidence_select_system": selector})
ids = {"scenario-01", "scenario-03", "scenario-07", "ac-8-new-01"}
cases = [case for case in read(original40/"cases.json") if case["id"] in ids]
assert len(cases) == 4
write(packet/"cases.json", cases)
write(packet/"acceptance.json", {"inherited": read(original40/"acceptance.json"),
    "scope": "Same source/condition/answer criteria; user context must not be falsely endorsed, unknown scope retained. Original4 case dictionaries unchanged.",
    "no_automatic_enum_gold": True, "no_new_human_gold": True})
controller = (HERE/"dispatch-question-parts-v1.py").read_text(encoding="utf-8").replace(
    "nist-public-http-question-parts-v1-20261107", packet.name).replace(
    "question-parts-v1-console.log", "question-groups-focus-v3-console.log").replace("16", "8").replace(".12", ".08")
controller_file = HERE/"dispatch-question-groups-v3.py"
controller_file.write_text(controller, encoding="utf-8")
dependencies = read(source/"runtime-source-hashes.json")
dependencies[controller_file.relative_to(ROOT).as_posix()] = "pending"
write(packet/"runtime-source-hashes.json", {name: sha(ROOT/name) for name in dependencies})
write(packet/"funding-preflight.json", {"prior_accounted_usd": b["accounted_with_reservations_usd"],
    "unknown_reservations": b["unknown_fee_reservations"], "max_calls": 8, "allocation_usd": .08,
    "max_accounted_usd": b["accounted_with_reservations_usd"]+.08, "standing_authorization_usd": 3,
    "scope": "Only unchanged public focus4, Flash disabledthinking1024/no retries"})
write(packet/"delta.json", {"change": "Adjacent original-part groups plus explicit user-context role; no standalone background source endorsement required.",
    "host_guards": "Exact full original part coverage, adjacency/order/nonoverlap, no model prose, no citations for user context",
    "same_original_case_dicts_source_units_translations": True, "old39of40_and_all_prior_failures_retained": True,
    "default_promoted": False, "semantic_grouping_still_requires_real_review": True})
m = read(source/"manifest.json")
m.update(max_calls=8, budget_usd=.08, answer_card_protocol="question_groups", executor_sha256=sha(ROOT/"scripts/evaluate_public_http.py"),
    role="Same four focus questions after original40 semantic39/40; grouping/control role change, no grading edits.",
    original_cases_sha256=sha(original40/"cases.json"), base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())
m["frozen_sha256"] = {path.name: sha(path) for path in packet.iterdir() if path.is_file()}
write(packet/"manifest.json", m)
write(packet/"authorization.json", {"max_calls": 8, "budget_usd": .08, "manifest_sha256": sha(packet/"manifest.json")})
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(),
    "output": f"docs/engineering-delivery/{packet.name}-live", "status": "frozen_question_groups_v3", "max_calls": 8, "budget_usd": .08}
state["pending_batches"] = [state["active_experiment"]]
b["pending_allocations"] = [{"batch": packet.name, "max_calls": 8, "budget_usd": .08}]
write(statefile, state)
print(json.dumps({"cases": 4, "max_calls": 8, "allocation_usd": .08, "max_accounted_usd": b["accounted_with_reservations_usd"]+.08}))
