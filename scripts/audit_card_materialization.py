"""Replay a pinned host contract; never accept raw selector JSON as user output."""
import hashlib
import importlib.util
import json
from pathlib import Path

from agents.sources import source_preview

ROOT = Path(__file__).resolve().parents[1]


def load_contract(packet, manifest):
    path = packet / "contract.py"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if manifest["frozen_sha256"].get("contract.py") != digest:
        raise ValueError("Host contract snapshot is not pinned")
    dependencies = json.loads((packet / "runtime-source-hashes.json").read_text(encoding="utf-8"))
    for name in ("scripts/answer_output_contract.py", "scripts/inline_oscal_parameters.py", "agents/sources.py"):
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != dependencies.get(name):
            raise ValueError("Restore the declared host-contract dependency before replay")
    spec = importlib.util.spec_from_file_location("frozen_card_" + digest[:16], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected_answers(packet, rows, receipts, manifest):
    contract = load_contract(packet, manifest)
    chunks = json.loads((packet / "chunks.json").read_text(encoding="utf-8"))
    sources = json.loads((packet / "source-units.json").read_text(encoding="utf-8"))["sources"]
    translations = json.loads((packet / "translations.json").read_text(encoding="utf-8"))
    answer_rows = [row for row in rows if any(call.get("stage") == "answer" for call in row["trace"]["trace"]["model_calls"])]
    answer_receipts = [receipt for receipt in receipts if receipt["stage"] == "answer"]
    if len(answer_rows) != len(answer_receipts):
        raise ValueError("Missing selector receipt or answer trace")
    answers = []
    for row, receipt in zip(answer_rows, answer_receipts, strict=True):
        raw = receipt["response"]["generations"][0][0]["message"]["content"]
        observations = row["trace"]["trace"]["events"]
        rendered = any(event["stage"] == "answer_source_cards" and event["outcome"] == "rendered" for event in observations)
        fallback = any(event["stage"] == "answer_source_cards" and event["outcome"] == "fallback_unmapped_source" for event in observations)
        if rendered:
            previews = next(event["contexts"] for event in row["events"] if event["type"] == "contexts")
            contexts = []
            for preview in previews:
                matches = [chunk for chunk in chunks if source_preview(chunk) == preview]
                if not matches or len({hashlib.sha256(chunk["text"].encode()).hexdigest() for chunk in matches}) != 1:
                    raise ValueError("Unknown or ambiguous original source preview")
                contexts.append(matches[0])
            selected = contract.materialize_card_selection(raw, contract.build_catalog(contexts, sources), contexts)
            answer = contract.render_card_answer(selected, contexts, translations, question=row["question"])
        elif fallback:
            answer = raw
        else:
            raise ValueError("Card strategy did not run or declare its original-answer fallback")
        if row.get("persisted_answer_sha256") != hashlib.sha256(answer.encode()).hexdigest():
            raise ValueError("Persisted answer does not match pinned host materialization")
        answers.append(answer)
    return answers
