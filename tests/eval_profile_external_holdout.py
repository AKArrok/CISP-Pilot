"""外部双盲 holdout 的硬门控验收；材料不全时禁止评分。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_ingest.dense_taxonomy import build_dense_question_tags
from data_ingest.taxonomy import build_semantic_question_tags
from tests.eval_profile_dev import _score


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests/fixtures/profile_accuracy_external_holdout.json"
DEFAULT_REPORT = ROOT / "data/profile_accuracy_external_holdout_report.json"
REQUIRED_GATES = {
    "primary_tag_coverage_min": 0.80,
    "primary_tag_accuracy_min": 0.80,
    "review_label_overlap_min": 0.80,
}
FINAL_ANNOTATION_FIELDS = {
    "review_labels", "reviewed_domains", "adjudicated_domain", "acceptable",
    "adjudication_note",
}


def _normalized_stem(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    value = re.sub(r"^\s*\d+\s*[.\u3001:)\uff09\uff0e-]\s*", "", value)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def _ngrams(value: str, size: int = 4) -> set[str]:
    return {value[index:index + size] for index in range(max(1, len(value) - size + 1))}


def _near_duplicate(left: str, right: str) -> float:
    left_grams, right_grams = _ngrams(left), _ngrams(right)
    union = left_grams | right_grams
    return len(left_grams & right_grams) / len(union) if union else 1.0


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _content_overlaps(external: list[dict], current: list[dict]) -> list[dict]:
    current_stems = [(row["id"], _normalized_stem(row.get("stem", ""))) for row in current]
    overlaps = []
    for row in external:
        stem = _normalized_stem(row.get("stem", ""))
        for current_id, current_stem in current_stems:
            if not stem or not current_stem:
                continue
            score = 1.0 if stem == current_stem else _near_duplicate(stem, current_stem)
            if score >= 0.88:
                overlaps.append({
                    "external_id": row.get("id"), "current_id": current_id,
                    "similarity": round(score, 4),
                })
                break
    return overlaps


def validate_external_holdout(
    fixture: dict,
    current_questions: list[dict] | None = None,
    taxonomy: list[dict] | None = None,
) -> dict:
    source = fixture.get("source", {})
    reviewers = fixture.get("reviewers", [])
    questions = fixture.get("questions", [])
    reviewers_are_rows = isinstance(reviewers, list) and all(
        isinstance(row, dict) for row in reviewers
    )
    reviewer_ids = [row.get("reviewer_id", "") for row in reviewers] if reviewers_are_rows else []
    valid_reviewer_ids = (
        len(reviewer_ids) == 2
        and all(isinstance(value, str) and value.strip() for value in reviewer_ids)
    )
    adjudication = fixture.get("adjudication", {})
    adjudication = adjudication if isinstance(adjudication, dict) else {}
    source_questions = [
        {key: value for key, value in question.items() if key not in FINAL_ANNOTATION_FIELDS}
        for question in questions
    ]
    source_questions_payload = json.dumps(
        source_questions, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    actual_source_questions_hash = (
        hashlib.sha256(source_questions_payload).hexdigest() if questions else ""
    )
    source_questions_hash_matches = (
        bool(actual_source_questions_hash)
        and actual_source_questions_hash == adjudication.get("source_questions_sha256")
    )
    if current_questions is None:
        current_questions = json.loads(
            (ROOT / "data/questions.json").read_text("utf-8")
        )
    current_ids = {question["id"] for question in current_questions}
    question_ids = [question.get("id") for question in questions]
    normalized = [_normalized_stem(question.get("stem", "")) for question in questions]
    internal_duplicate_stems = sorted({stem for stem in normalized if stem and normalized.count(stem) > 1})
    content_overlaps = _content_overlaps(questions, current_questions)
    if taxonomy is None:
        taxonomy = json.loads(
            (ROOT / "data/knowledge_taxonomy.json").read_text("utf-8")
        )
    active_by_name: dict[str, set[str]] = {}
    for point in taxonomy:
        if point.get("status") == "active":
            active_by_name.setdefault(point["name"], set()).add(point["domain"])
    payload = json.dumps(
        questions, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    actual_hash = hashlib.sha256(payload).hexdigest() if questions else ""
    label_overlap = []
    complete_labels = True
    valid_labels = True
    valid_questions = True
    for question in questions:
        labels = question.get("review_labels", {})
        left = set(labels.get(reviewer_ids[0], [])) if valid_reviewer_ids else set()
        right = set(labels.get(reviewer_ids[1], [])) if valid_reviewer_ids else set()
        acceptable = set(question.get("acceptable", []))
        domain = question.get("adjudicated_domain")
        complete_labels = complete_labels and bool(left) and bool(right) and bool(acceptable)
        valid_labels = valid_labels and all(
            label in active_by_name and domain in active_by_name[label]
            for label in left | right | acceptable
        )
        options = question.get("options")
        answer = set(question.get("answer", ""))
        valid_questions = valid_questions and bool(
            question.get("id") and question.get("stem") and isinstance(options, dict)
            and len(options) >= 2 and answer and answer <= set(options)
        )
        label_overlap.append(bool(left & right))
    overlap_rate = round(sum(label_overlap) / len(label_overlap), 4) if label_overlap else 0.0
    reviewer_packet_hashes = adjudication.get("review_packet_sha256", {})
    expected_packet_ids = set(reviewer_ids) if valid_reviewer_ids else set()
    reviewer_hashes_valid = (
        isinstance(reviewer_packet_hashes, dict)
        and set(reviewer_packet_hashes) == expected_packet_ids
        and all(_valid_sha256(value) for value in reviewer_packet_hashes.values())
    )
    review_provenance_bound = (
        adjudication.get("schema_version") == 2
        and _valid_sha256(adjudication.get("source_questions_sha256"))
        and source_questions_hash_matches
        and adjudication.get("reviewer_ids") == reviewer_ids
        and reviewer_hashes_valid
        and reviewers_are_rows
        and all(
            row.get("review_packet_sha256") == reviewer_packet_hashes.get(row.get("reviewer_id"))
            for row in reviewers
        )
    )
    checks = {
        "schema_version_2": fixture.get("schema_version") == 2,
        "external_source_documented": all(source.get(key) for key in (
            "name", "url", "license_or_permission", "permission_evidence_url",
            "content_sha256", "acquired_at",
        )) and source.get("kind") == "external",
        "two_distinct_blind_reviewers": (
            reviewers_are_rows and valid_reviewer_ids
            and len({value.casefold() for value in reviewer_ids}) == 2
            and all(row.get("reviewed_at") for row in reviewers)
            and all(
                row.get("blinded_to_predictions") is True
                and row.get("blinded_to_other_reviewer") is True
                and row.get("did_not_access_development_sets") is True
                and row.get("independent_human_reviewer") is True
                for row in reviewers
            )
        ),
        "minimum_100_questions": len(questions) >= 100,
        "unique_external_ids": len(question_ids) == len(set(question_ids)),
        "external_ids_only": bool(questions) and not ({q.get("id") for q in questions} & current_ids),
        "external_content_only": not content_overlaps,
        "no_internal_duplicate_stems": not internal_duplicate_stems,
        "valid_question_schema": valid_questions and bool(questions),
        "complete_double_labels": complete_labels and bool(questions),
        "labels_in_frozen_taxonomy": valid_labels and bool(questions),
        "adjudication_recorded": all(adjudication.get(key) for key in (
            "adjudicator_id", "adjudicated_at", "method",
        )),
        "review_provenance_bound": review_provenance_bound,
        "quality_gates_not_weakened": all(
            fixture.get("quality_gates", {}).get(key, 0) >= minimum
            for key, minimum in REQUIRED_GATES.items()
        ),
        "frozen_cases_unchanged": bool(actual_hash) and actual_hash == fixture.get("frozen_cases_sha256"),
        "source_questions_hash_matches": source_questions_hash_matches,
        "review_label_overlap": overlap_rate,
    }
    return {"checks": checks, "ready": all(
        value for key, value in checks.items() if key != "review_label_overlap"
    ), "actual_hash": actual_hash, "content_overlaps": content_overlaps,
        "internal_duplicate_stems": internal_duplicate_stems}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--method", choices=("tfidf", "dense"), default="dense")
    args = parser.parse_args()
    if not args.cases.exists():
        template = ROOT / "tests/fixtures/profile_accuracy_external_holdout.template.json"
        fixture = json.loads(template.read_text("utf-8"))
    else:
        fixture = json.loads(args.cases.read_text("utf-8"))
    integrity = validate_external_holdout(fixture)
    overlap_min = fixture["quality_gates"]["review_label_overlap_min"]
    if not integrity["ready"] or integrity["checks"]["review_label_overlap"] < overlap_min:
        report = {
            "status": "blocked", "reason": "外部来源或双人盲审材料不完整，禁止评分",
            "integrity": integrity,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    points = json.loads((ROOT / "data/knowledge_taxonomy.json").read_text("utf-8"))
    questions = [{
        "id": row["id"], "domain": row["adjudicated_domain"], "stem": row["stem"],
        "options": row["options"], "answer": row["answer"],
        "analysis": row.get("analysis", ""), "needs_review": False,
    } for row in fixture["questions"]]
    cases = [{
        "question_id": row["id"], "domain": row["adjudicated_domain"],
        "acceptable": row["acceptable"],
    } for row in fixture["questions"]]
    if args.method == "dense":
        from llms import get_embeddings
        params = json.loads((ROOT / "data/profile_accuracy_dev_report.json").read_text("utf-8"))[
            "dense_best"
        ]
        tags = build_dense_question_tags(
            points, questions, get_embeddings(),
            min_similarity=params["min_similarity"], min_margin=params["min_margin"],
            domain_mode=params.get("domain_mode", "stored"),
            stored_domain_prior=params.get("stored_domain_prior", 0.0),
        )
    else:
        tags = build_semantic_question_tags(points, questions)
    score = _score(tags, points, cases)
    gates = fixture["quality_gates"]
    passed = (
        score["coverage"] >= gates["primary_tag_coverage_min"]
        and score["accuracy"] >= gates["primary_tag_accuracy_min"]
    )
    report = {"status": "passed" if passed else "failed", "method": args.method,
              "integrity": integrity, "score": score, "quality_gates": gates}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
