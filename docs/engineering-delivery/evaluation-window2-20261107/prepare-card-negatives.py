"""Freeze developmental boundary cases; criteria never enter the index or requests."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.eval_nist_batch import request_content  # noqa: E402
import tiktoken  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


prior = ROOT / "evaluation/nist-unit-cards-v1-20261107"
packet = ROOT / "evaluation/nist-unit-cards-negatives-v1-20261107"
packet.mkdir(exist_ok=False)
for name in ("chunks.json", "source-units.json", "translations.json", "translation-review.json", "prompts.json"):
    shutil.copyfile(prior / name, packet / name)
shutil.copyfile(ROOT / "scripts/answer_unit_contract.py", packet / "contract.py")
definitions = [
    ("ac-8", "纯后台服务没有人类登录界面，资料是否仍要求为了多语言用户强制显示使用告知？", "deny", "无human interfaces时通知不要求；不得因语言或风险强行增加通知。"),
    ("ac-8", "资料是否规定系统使用告知必须支持哪三种具体语言？请列出这三种语言。", "not_specified", "只按组织需求与人口特征考虑多语言，没有三种语言的具体清单，不编造语言或参数。"),
    ("at-2", "AT-2是否要求每次培训必须持续90分钟？", "not_specified", "资料未给90分钟或每次时长，培训频次/更新频次不等于时长。"),
    ("at-2", "除了举例列出的评估发现、安全事件或泄露、规则变化，其他事件是否一律不能触发内容更新？", "deny", "include but are not limited to是非穷尽示例；不得排除其他组织定义事件。"),
    ("cp-9", "CP-9给出了本组织每年必须投入的备份采购预算金额吗？", "not_specified", "未给采购预算或金额，不编造数字；不足说明限定该问题而非全库没有备份内容。"),
    ("cp-9", "CP-9的系统组件范围参数尚未赋值，是否就可以停止系统级信息和系统文档备份？", "deny", "参数不赋值不表示动作可选；组件范围属于用户级备份，不豁免系统级和文档。"),
    ("ca-8", "CA-8是否允许开始渗透测试后才让各方商定交战规则？", "deny", "All parties agree before commencing；不能将开始前改为开始后。"),
    ("at-2", "AT-2中security incidents or breaches是否表示必须调查员工违规并处分？", "not_specified", "原词是安全事件或泄露，条款把经验纳入培训/意识技术，未规定员工处分，不偷换事件类别。"),
]
templates = {}
for case in read(prior / "cases.json"):
    templates.setdefault(case["target_control"], case)
cases, acceptance = [], []
for index, (control, question, category, criterion) in enumerate(definitions, 1):
    template = templates[control]
    key = f"card-boundary-{index:02d}"
    cases.append({"id": key, "question": question, "target_control": control,
        "role": "AI authored adversarial development diagnostic, not independent unseen test",
        "thread_group": key, "fixed_contexts": template["fixed_contexts"], "unit_sources": template["unit_sources"]})
    acceptance.append({"id": key, "expected_direction": category, "criterion": criterion,
        "original_evidence": template["fixed_contexts"][0]["text"],
        "grading": "Judge actual lead and complete source/translation content; do not grade by enum alone. Preserve original positive8 and all older failures."})
write(packet / "cases.json", cases)
write(packet / "acceptance.json", {"scope": "Development boundary criteria, never model/index input; wording can overlap historical topics", "cases": acceptance})
files = subprocess.check_output(["rg", "--files", "evaluation", "-g", "cases.json"], cwd=ROOT, text=True, encoding="utf-8").splitlines()
history = set()
for filename in files:
    path = ROOT / filename
    if path.resolve() == (packet / "cases.json").resolve():
        continue
    value = read(path)
    if isinstance(value, dict):
        value = value.get("cases", [])
    history.update(case["question"].strip() for case in value if isinstance(case, dict) and isinstance(case.get("question"), str))
duplicates = [case["id"] for case in cases if case["question"].strip() in history]
write(packet / "history-check.json", {"files_read": len(files)-1, "historical_exact_questions": len(history),
    "exact_duplicate_ids": duplicates, "limit": "No exact duplicate is not proof of new topics or independent human gold; all remain developmental"})
dependencies = ["scripts/answer_unit_contract.py", "scripts/inline_oscal_parameters.py", "scripts/answer_output_contract.py",
                "scripts/answer_grade_contract.py", "agents/sources.py"]
write(packet / "runtime-source-hashes.json", {name: sha(ROOT / name) for name in dependencies})
sf = Path(__file__).with_name("continuation.json")
state = read(sf)
b = state["budget"]
assert not b["pending_allocations"] and not state["pending_batches"]
held = b["known_estimate_usd"] + sum(row["additional_reservation_usd"] for row in b["unknown_fee_reservations"])
assert abs(held-b["accounted_with_reservations_usd"]) < 1e-9 and held+.08 <= b["approved_total_usd"]
write(packet / "funding-preflight.json", {"known_estimate_usd": b["known_estimate_usd"], "unknown_reservations": b["unknown_fee_reservations"],
    "prior_accounted_usd": held, "new_allocation_usd": .08, "max_accounted_usd": held+.08, "approved_total_usd": 3,
    "authorization": "standing user3USD; new16calls, no old-lock retry"})
manifest = read(prior / "manifest.json")
manifest["executor_sha256"] = sha(ROOT / "scripts/eval_nist_batch.py")
manifest["role"] = "Frozen developmental scope/absence/premise boundaries; positive8 untouched, no independent unseen/human claim. Source translations created before these questions."
manifest["acceptance"] = "Frozen acceptance.json; complete question/condition/parameter/term/citation support and scoped insufficiency, not enum matching or structure alone; all failures retained."
encoding = tiktoken.get_encoding("cl100k_base")
prompts = read(packet / "prompts.json")
estimates = []
for case in cases:
    for stage in ("answer_baseline", "evidence_select"):
        content = request_content(stage, case, {"contexts": case["fixed_contexts"]}, "", manifest)
        assert all(criterion["criterion"] not in content for criterion in acceptance)
        estimates.append({"id": case["id"], "stage": stage,
            "tokens": len(encoding.encode(content)) + len(encoding.encode(prompts[stage + "_system"]))})
assert max(row["tokens"] for row in estimates) <= 5500
write(packet / "input-preflight.json", {"estimates": estimates, "max_input": max(row["tokens"] for row in estimates),
    "criteria_not_sent_to_model": True, "fixed_primary_and_secondary_source_contexts": True,
    "limitation": "Answer workflow diagnostic, not actual retrieval or production HTTP gate", "model_calls": 0})
manifest["frozen_sha256"] = {path.name: sha(path) for path in packet.iterdir() if path.is_file()}
write(packet / "manifest.json", manifest)
write(packet / "authorization.json", {"max_calls": 16, "budget_usd": .08, "manifest_sha256": sha(packet / "manifest.json")})
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(),
    "output": "docs/engineering-delivery/nist-unit-cards-negatives-v1-20261107-live", "status": "frozen_preflight_passed", "max_calls": 16, "budget_usd": .08}
state["pending_batches"] = [state["active_experiment"]]
b["pending_allocations"] = [{"batch": packet.name, "max_calls": 16, "budget_usd": .08}]
write(sf, state)
print(json.dumps({"packet": packet.name, "max_input": max(row["tokens"] for row in estimates), "duplicates": duplicates, "accounted_max": held+.08}))
