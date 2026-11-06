"""Freeze/replay original16 selectors with native-role rendering; zero API calls."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.answer_unit_contract import build_catalog, materialize_card_selection, render_card_answer  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


parent = ROOT / "evaluation/nist-unit-cards-actionguard-v2-20261107"
origin = ROOT / "docs/engineering-delivery/nist-unit-cards-actionguard-v2-20261107-live"
packet = ROOT / "evaluation/nist-unit-cards-native-renderer-v3-20261107"
output = ROOT / "docs/engineering-delivery/nist-unit-cards-native-renderer-v3-20261107-replay"
packet.mkdir(exist_ok=False)
output.mkdir(exist_ok=False)
before = sha(ROOT / "data/cisp_qa.db")
for name in ("cases.json", "chunks.json", "source-units.json", "translations.json", "translation-review.json",
             "original-positive-cases.json", "negative-acceptance.json", "prompts.json"):
    shutil.copyfile(parent / name, packet / name)
shutil.copyfile(origin / "results.json", packet / "recorded-results.json")
shutil.copyfile(ROOT / "scripts/answer_unit_contract.py", packet / "contract.py")
write(packet / "manifest.json", {"mode": "offline_card_replay", "new_provider_calls": 0,
    "source_batch": origin.relative_to(ROOT).as_posix(), "source_receipts_sha256": sha(origin / "receipts.jsonl"),
    "renderer_sha256": sha(ROOT / "scripts/answer_unit_contract.py"),
    "delta": "Only host materialization: actual native statement ancestors copied; required-action/undefined-parameter policy shown for statement units, not advisory guidance. Same16 questions/rawselectors/source/translation/criteria.",
    "acceptance": "Unchanged complete answer, condition, action, parameter, terminology, citation criteria; enum alone not graded; no semantic autoaccept.",
    "frozen_sha256": {path.name: sha(path) for path in packet.iterdir() if path.is_file()}})
cases = {case["id"]: case for case in read(packet / "cases.json")}
translations = read(packet / "translations.json")
rows = []
for original in read(packet / "recorded-results.json"):
    case = cases[original["id"]]
    assert original["question"] == case["question"] and original["contexts"] == [
        {key: source[key] for key in ("source", "locator", "source_url", "text")} for source in case["fixed_contexts"]]
    selected = materialize_card_selection(original["evidence_select"], build_catalog(original["contexts"], case["unit_sources"]), original["contexts"])
    rendered = render_card_answer(selected, original["contexts"], translations, question=case["question"])
    rows.append({"id": original["id"], "question": case["question"], "answer_type": selected["answer_type"],
        "original_selector_sha256": hashlib.sha256(original["evidence_select"].encode()).hexdigest(),
        "ancestor_unit_ids": selected["ancestor_unit_ids"], "rendered_candidate": rendered,
        "candidate_sha256": hashlib.sha256(rendered.encode()).hexdigest()})
write(output / "results.json", rows)
after = sha(ROOT / "data/cisp_qa.db")
assert before == after
write(output / "default-db.json", {"before": before, "after": after, "unchanged": True})
write(output / "summary.json", {"rendered": len(rows), "new_provider_calls": 0, "new_model_cost_usd": 0,
    "source_model_attempts": 32, "source_model_cost_usd_conservative": read(origin / "summary.json")["cost_usd_conservative"],
    "semantic_acceptance": "pending_original_source_review", "default_db_unchanged": True,
    "limit": "Frozen real selector replay, not16new model answers or fullHTTP; old15of16failure retained"})
statefile = Path(__file__).with_name("continuation.json")
state = read(statefile)
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(), "output": output.relative_to(ROOT).as_posix(),
    "status": "offline_replay16_pending_AI_review", "new_provider_calls": 0}
write(statefile, state)
print(json.dumps({"offline_rendered": len(rows), "new_provider_calls": 0, "default_db_unchanged": True}))
