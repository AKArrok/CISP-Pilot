"""Replay both real failure variants with explicit native requirements; no API."""
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
origins = [ROOT / "docs/engineering-delivery/nist-unit-cards-negatives-v1-20261107-live",
           ROOT / "docs/engineering-delivery/nist-unit-cards-actionguard-v2-20261107-live"]
packet = ROOT / "evaluation/nist-unit-cards-native-renderer-v3r2-20261107"
output = ROOT / "docs/engineering-delivery/nist-unit-cards-native-renderer-v3r2-20261107-replay"
packet.mkdir(exist_ok=False)
output.mkdir(exist_ok=False)
before = sha(ROOT / "data/cisp_qa.db")
for name in ("cases.json", "chunks.json", "source-units.json", "translations.json", "translation-review.json",
             "original-positive-cases.json", "negative-acceptance.json", "prompts.json"):
    shutil.copyfile(parent / name, packet / name)
recorded = [{**row, "source_batch": origin.relative_to(ROOT).as_posix()}
            for origin in origins for row in read(origin / "results.json")]
write(packet / "recorded-results.json", recorded)
shutil.copyfile(ROOT / "scripts/answer_unit_contract.py", packet / "contract.py")
write(packet / "manifest.json", {"mode": "offline_card_replay", "new_provider_calls": 0,
    "source_receipts_sha256": {origin.relative_to(ROOT).as_posix(): sha(origin / "receipts.jsonl") for origin in origins},
    "renderer_sha256": sha(ROOT / "scripts/answer_unit_contract.py"),
    "delta": "Native ancestors preserved. Statement-role notice keeps actions binding despite unassigned values; not_specified lead limits missing information to extra rules/assigned values, not existing action requirements. No model prompt change from v2.",
    "acceptance": "Same original8 and same8 boundary criteria; judge complete actual output, not enum alone. All older failures retained; no semantic autoaccept.",
    "frozen_sha256": {path.name: sha(path) for path in packet.iterdir() if path.is_file()}})
cases = {case["id"]: case for case in read(packet / "cases.json")}
translations = read(packet / "translations.json")
rows = []
for original in recorded:
    case = cases[original["id"]]
    assert original["question"] == case["question"] and original["contexts"] == [
        {key: source[key] for key in ("source", "locator", "source_url", "text")} for source in case["fixed_contexts"]]
    selected = materialize_card_selection(original["evidence_select"], build_catalog(original["contexts"], case["unit_sources"]), original["contexts"])
    text = render_card_answer(selected, original["contexts"], translations, question=case["question"])
    rows.append({"id": original["id"], "source_batch": original["source_batch"], "question": case["question"],
        "answer_type": selected["answer_type"], "ancestor_unit_ids": selected["ancestor_unit_ids"],
        "original_selector_sha256": hashlib.sha256(original["evidence_select"].encode()).hexdigest(),
        "rendered_candidate": text, "candidate_sha256": hashlib.sha256(text.encode()).hexdigest()})
write(output / "results.json", rows)
after = sha(ROOT / "data/cisp_qa.db")
assert before == after
write(output / "default-db.json", {"before": before, "after": after, "unchanged": True})
write(output / "summary.json", {"rendered_observations": len(rows), "unique_questions": len({row["id"] for row in rows}),
    "new_provider_calls": 0, "new_model_cost_usd": 0,
    "source_model_attempts_already_accounted": sum(read(origin / "summary.json")["provider_calls"] for origin in origins),
    "source_model_cost_already_accounted_usd": sum(read(origin / "summary.json")["cost_usd_conservative"] for origin in origins),
    "semantic_acceptance": "pending_original_source_review", "default_db_unchanged": True,
    "limit": "Offline real-selector replay, not new model/HTTP results; two old ambiguity failures remain in their original records"})
sf = Path(__file__).with_name("continuation.json")
state = read(sf)
state["active_experiment"] = {"packet": packet.relative_to(ROOT).as_posix(), "output": output.relative_to(ROOT).as_posix(),
    "status": "offline_replay24_16unique_pending_AI_review", "new_provider_calls": 0}
write(sf, state)
print(json.dumps({"observations": len(rows), "unique_questions": 16, "new_provider_calls": 0}))
