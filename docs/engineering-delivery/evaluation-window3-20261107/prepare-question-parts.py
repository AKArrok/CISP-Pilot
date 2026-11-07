"""Freeze same8 original scenarios and first per-question-part protocol batch."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from agents.sources import source_preview  # noqa: E402
from scripts.answer_unit_contract import question_parts  # noqa: E402
from scripts.eval_nist_batch import request_content  # noqa: E402
import tiktoken  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


old = ROOT / "evaluation/nist-public-http-scenarios-complete-20261107"
old_run = ROOT / "docs/engineering-delivery/nist-public-http-scenarios-complete-20261107-live"
packet = ROOT / "evaluation/nist-public-http-question-parts-v1-20261107"
statefile = HERE / "continuation.json"
state = read(statefile)
budget = state["budget"]
assert not budget["pending_allocations"] and not state["pending_batches"]
assert budget["accounted_with_reservations_usd"]+.12 <= budget["approved_total_usd"] == 3
assert sha(ROOT / "data/cisp_qa.db") == state["default_db_sha256"]
packet.mkdir(exist_ok=False)
for name in ("cases.json", "acceptance.json", "chunks.json", "prompts.json", "source-units.json", "translations.json", "translation-review.json"):
    shutil.copyfile(old/name, packet/name)
shutil.copyfile(ROOT/"scripts/answer_unit_contract.py", packet/"contract.py")
selector = (
    "你是来源证据选择器。宿主已给出原问题question_parts，每个片段的ID、原文和位置不可改写。"
    "逐片段判断，背景和开放式说明问题用explain。一个片段的未知不能变成另一个片段的否定。"
    "只依据sources.units，选齐该片段所需的原文、适用条件、前文指代及参数定义；单元并非独立事实。"
    "仅输出JSON：{\"parts\":[{\"part_id\":\"宿主ID\",\"decision\":\"类型\",\"unit_ids\":[\"原始单元ID\"]}]}。"
    "每个宿主片段必须恰好出现一次，不能新增、遗漏、重复片段或提供自由文字。"
    "decision仅可为affirm（原文支持该片段所问命题）、deny（原文给出与该命题相反的要求）、"
    "undetermined（本次资料不足以确定该片段所问事项）、explain（以原文列举、比较或说明该片段）。"
    "除undetermined可用空数组外，每片段必须选择直接支持判断的原始unit_ids，片段内不得重复。"
    "资料未说明某事项，不能推出禁止或允许；已经存在的动作要求与待赋值参数须分开保留。"
    "不要生成解释、引文、问题文字或额外字段。"
)
write(packet/"card-selector.json", {"evidence_select_system": selector})
controller = (ROOT/"docs/engineering-delivery/evaluation-window2-20261107/dispatch-scenarios-complete.py").read_text(encoding="utf-8")
controller = controller.replace("nist-public-http-scenarios-complete-20261107", packet.name).replace(
    "scenarios-complete-console.log", "question-parts-v1-console.log")
controller_file = HERE/"dispatch-question-parts-v1.py"
controller_file.write_text(controller, encoding="utf-8")
dependencies = read(old/"runtime-source-hashes.json")
dependencies[controller_file.relative_to(ROOT).as_posix()] = "pending"
write(packet/"runtime-source-hashes.json", {name: sha(ROOT/name) for name in dependencies})
encoding = tiktoken.get_encoding("cl100k_base")
chunks, cases = read(packet/"chunks.json"), read(packet/"cases.json")
sources = read(packet/"source-units.json")["sources"]
preflight = []
for case, row in zip(cases, read(old_run/"results.json"), strict=True):
    assert case["id"] == row["id"] and case["question"] == row["question"]
    previews = next(event["contexts"] for event in row["events"] if event["type"] == "contexts")
    contexts = [next(chunk for chunk in chunks if source_preview(chunk) == preview) for preview in previews]
    content = json.loads(request_content("evidence_select", {"question": case["question"], "unit_sources": sources},
        {"contexts": contexts}, "", {"mode": "answer_card_pipeline"}))
    content["question_parts"] = question_parts(case["question"])
    tokens = len(encoding.encode(selector)) + len(encoding.encode(json.dumps(content, ensure_ascii=False)))
    assert tokens <= 5400
    preflight.append({"id": case["id"], "parts": content["question_parts"], "selector_input_tokens": tokens,
                      "scope": "Old actual retrieval contexts only; new real HTTP still required"})
write(packet/"input-preflight.json", {"cases": preflight, "max_selector_input_tokens": max(r["selector_input_tokens"] for r in preflight)})
write(packet/"funding-preflight.json", {"prior_accounted_usd": budget["accounted_with_reservations_usd"],
    "unknown_reservations": budget["unknown_fee_reservations"], "max_calls": 16, "allocation_usd": .12,
    "max_accounted_usd": budget["accounted_with_reservations_usd"]+.12, "standing_authorization_usd": 3,
    "scope": "Only pinned public NIST, same8 questions; Flash nonthinking1024/no retries; no private fragments"})
write(packet/"delta.json", {"same_cases_sha256": sha(old/"cases.json"), "same_acceptance_sha256": sha(old/"acceptance.json"),
    "same_chunks_sha256": sha(old/"chunks.json"), "same_source_units_and_translations": True,
    "change": "Host question-part IDs plus per-part decisions and host-scoped uncertainty; no model-written prose. Protocol/prompt/renderer bundle, not single-variable prompt comparison.",
    "old7of8_and_all_failures_retained": True, "default_changed": False, "human_gold": False})
manifest = read(old/"manifest.json")
manifest.update(answer_card_protocol="question_parts", executor_sha256=sha(ROOT/"scripts/evaluate_public_http.py"),
    role="Same frozen8 original cases/criteria/source; per-question-part protocol diagnostic, no human gold or default promotion.",
    base_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    original_cases_sha256=sha(old/"cases.json"))
manifest["frozen_sha256"] = {path.name: sha(path) for path in packet.iterdir() if path.is_file()}
write(packet/"manifest.json", manifest)
write(packet/"authorization.json", {"max_calls": 16, "budget_usd": .12, "manifest_sha256": sha(packet/"manifest.json")})
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(),
    "output": f"docs/engineering-delivery/{packet.name}-live", "status": "frozen_question_parts_v1",
    "max_calls": 16, "budget_usd": .12}
state["pending_batches"] = [state["active_experiment"]]
budget["pending_allocations"] = [{"batch": packet.name, "max_calls": 16, "budget_usd": .12}]
write(statefile, state)
print(json.dumps({"cases": len(cases), "max_calls": 16, "allocation_usd": .12,
                  "max_input": max(row["selector_input_tokens"] for row in preflight),
                  "max_accounted_usd": budget["accounted_with_reservations_usd"]+.12}))
