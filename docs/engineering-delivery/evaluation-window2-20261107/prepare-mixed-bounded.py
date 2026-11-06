"""One ablation removes optional elaboration permission, no case-specific rules."""

import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(p):
    return json.loads(p.read_text(encoding="utf8"))


def write(p, v):
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + "\n", encoding="utf8")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


old = ROOT / "data/review_packets/nist-mixed-cards-regression-20261107-r2"
packet = ROOT / "data/review_packets/nist-mixed-cards-bounded-20261107-r2"
statefile = HERE / "continuation.json"
state = read(statefile)
b = state["budget"]
assert not b["pending_allocations"] and b["accounted_with_reservations_usd"] + 0.08 <= 3
packet.mkdir(exist_ok=False)
excluded = {
    "manifest.json",
    "authorization.json",
    "dispatch.lock",
    "funding-preflight.json",
    "runtime-source-hashes.json",
}
for p in old.iterdir():
    if p.is_file() and p.name not in excluded:
        shutil.copyfile(p, packet / p.name)
system = read(old / "prompts.json")["ANSWER_SYSTEM"]
before = "可给出资料能够支持的记忆点或易混淆点。"
assert system.count(before) == 1
system = system.replace(
    before, "只回答用户所问，不主动添加记忆点、易混淆点或资料未解释的术语定义。"
)
supplement = '可基于通用安全常识简短补充但须标注"（通用补充）"。'
assert system.count(supplement) == 1
system = system.replace(supplement, "")
write(packet / "fallback-system.json", {"system": system})
dependencies = read(old / "runtime-source-hashes.json")
write(packet / "runtime-source-hashes.json", {name: sha(ROOT / name) for name in dependencies})
write(
    packet / "funding-preflight.json",
    {
        "prior_accounted_usd": b["accounted_with_reservations_usd"],
        "unknown_reservations": b["unknown_fee_reservations"],
        "max_calls": 8,
        "allocation_usd": 0.08,
        "max_accounted_usd": b["accounted_with_reservations_usd"] + 0.08,
        "total_authorization_usd": 3,
    },
)
write(
    packet / "delta.json",
    {
        "change": "Remove optional memory/common-knowledge elaboration permission; retain original source/definition/parameter/conditions criteria. No control IDs, expected answers, case keywords or labels added.",
        "same_cases_sha256": sha(old / "cases.json"),
        "same_chunks_sha256": sha(old / "chunks.json"),
        "baseline_system_sha256": hashlib.sha256(
            read(old / "prompts.json")["ANSWER_SYSTEM"].encode()
        ).hexdigest(),
        "private_scope": "Previously approved same4 full fragments, scope-proof unchanged; no additional private material",
    },
)
m = read(old / "manifest.json")
m.update(
    fallback_system_override=True,
    executor_sha256=sha(ROOT / "scripts/evaluate_public_http.py"),
    role="Single bounded elaboration ablation on same4 approvedmixed, original3/4 retained; no defaultprompt change.",
    acceptance="Same original core/source/definition attribution criteria, exactsame approvedTop2; no label/question edits. Must pass strictaudit plus Codexsourcecheck; not independentgold.",
)
m["frozen_sha256"] = {p.name: sha(p) for p in packet.iterdir() if p.is_file()}
write(packet / "manifest.json", m)
write(
    packet / "authorization.json",
    {"max_calls": 8, "budget_usd": 0.08, "manifest_sha256": sha(packet / "manifest.json")},
)
output = "docs/engineering-delivery/evaluation-window2-20261107/operator-private/mixed-cards-bounded-live"
state["active_experiment"] = {
    "packet": packet.relative_to(ROOT).as_posix(),
    "output": output,
    "status": "frozen_single_bounded_ablation",
    "max_calls": 8,
    "budget_usd": 0.08,
}
state["pending_batches"] = [state["active_experiment"]]
b["pending_allocations"] = [{"batch": packet.name, "max_calls": 8, "budget_usd": 0.08}]
write(statefile, state)
controller = (
    (HERE / "dispatch-mixed-regression-r2.py")
    .read_text(encoding="utf8")
    .replace("nist-mixed-cards-regression-20261107-r2", "nist-mixed-cards-bounded-20261107-r2")
    .replace("mixed-cards-regression-r2-live", "mixed-cards-bounded-live")
    .replace("mixed-cards-r2-console.log", "mixed-cards-bounded-console.log")
)
(HERE / "dispatch-mixed-bounded.py").write_text(controller, encoding="utf8")
write(
    HERE / "mixed-bounded-preflight.json",
    {
        "max_calls": 8,
        "budget_usd": 0.08,
        "prior_accounted_usd": b["accounted_with_reservations_usd"],
        "max_accounted_usd": b["accounted_with_reservations_usd"] + 0.08,
        "same_private_scope": True,
        "default_prompt_changed": False,
        "cases_sha256": sha(packet / "cases.json"),
        "chunks_sha256": sha(packet / "chunks.json"),
        "delta_sha256": sha(packet / "delta.json"),
    },
)
print(
    json.dumps({"max_calls": 8, "max_accounted_usd": b["accounted_with_reservations_usd"] + 0.08})
)
