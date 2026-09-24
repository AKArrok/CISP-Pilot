"""非 holdout 开发集：比较 TF-IDF 与 dense embedding 标签召回。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_ingest.dense_taxonomy import (
    build_dense_question_tags,
    predict_dense_question_domains,
)
from data_ingest.tag_review import MANUAL_PRIMARY
from data_ingest.taxonomy import build_semantic_question_tags


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "tests/fixtures/profile_accuracy_dev.json"
REPORT_PATH = ROOT / "data/profile_accuracy_dev_report.json"


def _score(
    tags: list[dict], points: list[dict], cases: list[dict], *, include_errors: bool = True,
) -> dict:
    names = {point["id"]: point["name"] for point in points}
    point_domains = {point["id"]: point["domain"] for point in points}
    predicted = {
        tag["question_id"]: names[tag["knowledge_point_id"]]
        for tag in tags if tag.get("is_primary") and tag["knowledge_point_id"] in names
    }
    predicted_domains = {
        tag["question_id"]: tag.get(
            "predicted_domain", point_domains.get(tag["knowledge_point_id"])
        )
        for tag in tags if tag.get("is_primary")
    }
    correct = sum(predicted.get(case["question_id"]) in case["acceptable"] for case in cases)
    covered = sum(case["question_id"] in predicted for case in cases)
    active_domains = {}
    for point in points:
        if point.get("status") == "active":
            active_domains.setdefault(point["name"], set()).add(point["domain"])
    taxonomy_supported = sum(
        any(name in active_domains for name in case["acceptable"]) for case in cases
    )
    same_domain_supported = sum(
        any(
            case.get("expected_domain", case["domain"]) in active_domains.get(name, set())
            for name in case["acceptable"]
        )
        for case in cases
    )
    domain_correct = sum(
        predicted_domains.get(case["question_id"])
        == case.get("expected_domain", case["domain"])
        for case in cases
    )
    result = {
        "sample_size": len(cases), "covered": covered, "correct": correct,
        "coverage": round(covered / len(cases), 4),
        "accuracy": round(correct / len(cases), 4),
        "conditional_accuracy": round(correct / covered, 4) if covered else 0.0,
        "taxonomy_supported": taxonomy_supported,
        "same_domain_supported": same_domain_supported,
        "domain_constraint_ceiling": round(same_domain_supported / len(cases), 4),
        "domain_correct": domain_correct,
        "domain_accuracy": round(domain_correct / len(cases), 4),
        "domain_accuracy_when_tagged": round(domain_correct / covered, 4) if covered else 0.0,
    }
    if include_errors:
        result["errors"] = [
            {"question_id": case["question_id"], "predicted": predicted.get(case["question_id"]),
             "acceptable": case["acceptable"],
             "predicted_domain": predicted_domains.get(case["question_id"]),
             "expected_domain": case.get("expected_domain", case["domain"])}
            for case in cases if predicted.get(case["question_id"]) not in case["acceptable"]
        ]
    return result


def _score_domains(predictions: dict[str, str], cases: list[dict]) -> dict:
    correct = sum(
        predictions.get(case["question_id"]) == case.get("expected_domain", case["domain"])
        for case in cases
    )
    return {
        "sample_size": len(cases),
        "correct": correct,
        "accuracy": round(correct / len(cases), 4),
    }


def validate_dev(cases: dict, questions: list[dict]) -> dict:
    question_by_id = {question["id"]: question for question in questions}
    case_ids = [case["question_id"] for case in cases["knowledge_tag_cases"]]
    excluded = set(MANUAL_PRIMARY)
    for path in ("profile_accuracy_cases.json", "profile_accuracy_holdout.json"):
        fixture = json.loads((ROOT / "tests/fixtures" / path).read_text("utf-8"))
        excluded |= {case["question_id"] for case in fixture["knowledge_tag_cases"]}
    eligible = [
        question for question in questions
        if question["id"] not in excluded and question.get("domain")
        and not question.get("needs_review") and not question.get("dup_of")
    ]
    expected = []
    for domain in sorted({question["domain"] for question in eligible}):
        domain_questions = [question for question in eligible if question["domain"] == domain]
        ranked = sorted(
            domain_questions,
            key=lambda question: hashlib.sha256(
                f"dev-v1:{question['id']}".encode("utf-8")
            ).hexdigest(),
        )
        expected.extend(question["id"] for question in ranked[:cases["sample_per_domain"]])
    return {
        "sample_size": len(case_ids),
        "unique": len(case_ids) == len(set(case_ids)),
        "selection_exact": case_ids == expected,
        "missing": sorted(set(case_ids) - question_by_id.keys()),
        "overlap": sorted(set(case_ids) & excluded),
        "domain_counts": dict(sorted(Counter(case["domain"] for case in cases["knowledge_tag_cases"]).items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    cases = json.loads(CASES_PATH.read_text("utf-8"))
    questions = json.loads((ROOT / "data/questions.json").read_text("utf-8"))
    points = json.loads((ROOT / "data/knowledge_taxonomy.json").read_text("utf-8"))
    case_ids = {case["question_id"] for case in cases["knowledge_tag_cases"]}
    dev_questions = [question for question in questions if question["id"] in case_ids]
    tfidf = _score(
        build_semantic_question_tags(points, dev_questions), points, cases["knowledge_tag_cases"]
    )

    from llms import get_embeddings
    embedder = get_embeddings()
    stored_experiments = []
    for threshold in (0.30, 0.35, 0.40, 0.45, 0.50):
        for margin in (0.0, 0.02, 0.04):
            tags = build_dense_question_tags(
                points, dev_questions, embedder,
                min_similarity=threshold, min_margin=margin,
            )
            score = _score(
                tags, points, cases["knowledge_tag_cases"], include_errors=False,
            )
            stored_experiments.append({
                "domain_mode": "stored", "stored_domain_prior": 0.0,
                "min_similarity": threshold, "min_margin": margin, **score,
            })
    inferred_experiments = []
    inferred_domain_scores = []
    for prior in (0.0, 0.02, 0.05, 0.08):
        predicted_domains = predict_dense_question_domains(
            points, dev_questions, embedder, stored_domain_prior=prior,
        )
        inferred_domain_scores.append({
            "stored_domain_prior": prior,
            **_score_domains(predicted_domains, cases["knowledge_tag_cases"]),
        })
        for threshold in (0.30, 0.35, 0.40, 0.45, 0.50):
            for margin in (0.0, 0.02, 0.04):
                tags = build_dense_question_tags(
                    points, dev_questions, embedder,
                    min_similarity=threshold, min_margin=margin,
                    domain_mode="inferred", stored_domain_prior=prior,
                    predicted_domains=predicted_domains,
                )
                score = _score(
                    tags, points, cases["knowledge_tag_cases"], include_errors=False,
                )
                inferred_experiments.append({
                    "domain_mode": "inferred", "stored_domain_prior": prior,
                    "min_similarity": threshold, "min_margin": margin, **score,
                })
    experiments = stored_experiments + inferred_experiments
    best = max(
        experiments,
        key=lambda row: (row["accuracy"], row["conditional_accuracy"], row["coverage"],
                         row["min_similarity"], row["min_margin"]),
    )
    best_tags = build_dense_question_tags(
        points, dev_questions, embedder,
        min_similarity=best["min_similarity"], min_margin=best["min_margin"],
        domain_mode=best["domain_mode"],
        stored_domain_prior=best["stored_domain_prior"],
    )
    best = {
        **best,
        "errors": _score(best_tags, points, cases["knowledge_tag_cases"])["errors"],
    }
    active_names = {
        point["name"] for point in points if point.get("status") == "active"
    }
    report = {
        "dataset": {"role": cases["dataset_role"], "sample_size": len(case_ids)},
        "integrity": validate_dev(cases, questions),
        "taxonomy_missing": [
            {
                "question_id": case["question_id"],
                "expected_domain": case.get("expected_domain", case["domain"]),
                "acceptable": case["acceptable"],
            }
            for case in cases["knowledge_tag_cases"]
            if not any(name in active_names for name in case["acceptable"])
        ],
        "embedding_model": embedder.model_identity,
        "stored_domain_baseline": _score_domains(
            {case["question_id"]: case["domain"] for case in cases["knowledge_tag_cases"]},
            cases["knowledge_tag_cases"],
        ),
        "tfidf": tfidf,
        "dense_best": best,
        "dense_stored_best": max(
            stored_experiments,
            key=lambda row: (row["accuracy"], row["conditional_accuracy"], row["coverage"]),
        ),
        "dense_inferred_best": max(
            inferred_experiments,
            key=lambda row: (row["accuracy"], row["conditional_accuracy"], row["coverage"]),
        ),
        "inferred_domain_grid": inferred_domain_scores,
        "dense_grid": experiments,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
