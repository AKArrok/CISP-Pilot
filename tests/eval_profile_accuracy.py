"""用户画像准确度评测：能力推断蒙特卡洛 + 人工题目标签集。"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from quiz.stats import _beta_p_below


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests/fixtures/profile_accuracy_cases.json"
DEFAULT_REPORT = ROOT / "data/profile_accuracy_report.json"


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _predict(correct: int, attempts: int) -> tuple[bool, float]:
    probability = float(_beta_p_below(
        correct + config.BETA_PRIOR,
        attempts - correct + config.BETA_PRIOR,
        config.MASTERY_WEAK_THRESHOLD,
    ))
    return attempts >= 2 and probability > config.MASTERY_WEAK_PROB, probability


def evaluate_inference(cases: dict) -> dict:
    by_budget: dict[int, dict[str, float]] = {}
    for budget in cases["attempt_budgets"]:
        totals = defaultdict(float)
        for persona in cases["personas"]:
            abilities = persona["abilities"]
            true_weak = {d for d, p in abilities.items() if p <= cases["weak_max"]}
            for seed in range(cases["simulation_seeds"]):
                rng = random.Random(f"{cases['sampling_seed']}:{persona['id']}:{budget}:{seed}")
                predictions = {}
                probabilities = {}
                for domain, ability in abilities.items():
                    correct = sum(rng.random() < ability for _ in range(budget))
                    predictions[domain], probabilities[domain] = _predict(correct, budget)

                scored_domains = {
                    d for d, p in abilities.items()
                    if p <= cases["weak_max"] or p >= cases["strong_min"]
                }
                predicted_weak = {d for d in scored_domains if predictions[d]}
                truth = true_weak & scored_domains
                totals["tp"] += len(predicted_weak & truth)
                totals["fp"] += len(predicted_weak - truth)
                totals["fn"] += len(truth - predicted_weak)
                totals["tn"] += len(scored_domains - predicted_weak - truth)
                totals["profiles"] += 1
                totals["exact"] += predicted_weak == truth
                for domain in scored_domains:
                    target = 1.0 if domain in truth else 0.0
                    totals["brier_sum"] += (probabilities[domain] - target) ** 2
                    totals["brier_n"] += 1
                if truth:
                    ranked = sorted(abilities, key=lambda d: probabilities[d], reverse=True)
                    top = set(ranked[:len(truth)])
                    totals["top_recall_sum"] += len(top & truth) / len(truth)
                    totals["top_recall_n"] += 1

        tp, fp, fn, tn = (int(totals[k]) for k in ("tp", "fp", "fn", "tn"))
        precision = _ratio(tp, tp + fp)
        recall = _ratio(tp, tp + fn)
        by_budget[budget] = {
            "precision": precision,
            "recall": recall,
            "f1": _ratio(2 * precision * recall, precision + recall),
            "false_positive_rate": _ratio(fp, fp + tn),
            "exact_profile_rate": _ratio(totals["exact"], totals["profiles"]),
            "top_k_weak_recall": _ratio(totals["top_recall_sum"], totals["top_recall_n"]),
            "brier_score": _ratio(totals["brier_sum"], totals["brier_n"]),
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        }
    return {"by_attempt_budget": by_budget}


def evaluate_knowledge_tags(cases: dict) -> dict:
    points = {
        point["id"]: point
        for point in json.loads((ROOT / "data/knowledge_taxonomy.json").read_text("utf-8"))
    }
    tags = json.loads((ROOT / "data/question_knowledge_tags.json").read_text("utf-8"))
    active_primary = defaultdict(set)
    active_names = {
        point["name"] for point in points.values() if point.get("status") == "active"
    }
    manual_questions = set()
    for tag in tags:
        point = points.get(tag["knowledge_point_id"])
        if point and point.get("status") == "active" and tag.get("is_primary"):
            active_primary[tag["question_id"]].add(point["name"])
        if tag.get("tag_source") == "manual":
            manual_questions.add(tag["question_id"])

    correct = 0
    scorable = 0
    covered = 0
    supported_gold = 0
    missing = []
    errors = []
    for case in cases["knowledge_tag_cases"]:
        actual = active_primary.get(case["question_id"], set())
        expected_prediction = case.get("predicted")
        if expected_prediction is not None and expected_prediction not in actual:
            missing.append(case["question_id"] + ":" + expected_prediction)
        if not case["scorable"]:
            continue
        scorable += 1
        covered += bool(actual)
        supported_gold += bool(set(case["acceptable"]) & active_names)
        # 评分必须读取当前构建产物的主标签；fixture 中的 ``predicted`` 只用于
        # 检测预期输出是否仍存在，不能自己给自己打分。
        matched = len(actual) == 1 and next(iter(actual)) in case["acceptable"]
        correct += matched
        if not matched:
            errors.append({
                "question_id": case["question_id"],
                "predicted": sorted(actual),
                "expected_prediction": expected_prediction,
                "acceptable": case["acceptable"],
            })
    total = len(cases["knowledge_tag_cases"])
    return {
        "sample_size": total,
        "scorable": scorable,
        "unscorable": total - scorable,
        "manual_reviewed_cases": len(manual_questions & {
            case["question_id"] for case in cases["knowledge_tag_cases"]
        }),
        "parse_contamination_rate": _ratio(total - scorable, total),
        "primary_tag_coverage": _ratio(covered, scorable),
        "gold_taxonomy_coverage": _ratio(supported_gold, scorable),
        "accuracy_on_taxonomy_supported_gold": _ratio(correct, supported_gold),
        "conditional_accuracy": _ratio(correct, covered),
        "primary_tag_accuracy": _ratio(correct, scorable),
        "correct": correct,
        "missing_current_predictions": missing,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--enforce", action="store_true",
                        help="质量门槛未通过时返回非零状态")
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text("utf-8"))
    inference = evaluate_inference(cases)
    knowledge_tags = evaluate_knowledge_tags(cases)
    at_12 = inference["by_attempt_budget"].get(12)
    gates = cases["quality_gates"]
    gate_results = {
        "inference_12_f1": at_12["f1"] >= gates["inference_12_f1_min"],
        "inference_12_exact_profile": (
            at_12["exact_profile_rate"] >= gates["inference_12_exact_profile_min"]
        ),
        "primary_tag_accuracy": (
            knowledge_tags["primary_tag_accuracy"] >= gates["primary_tag_accuracy_min"]
        ),
        "parse_contamination": (
            knowledge_tags["parse_contamination_rate"] <= gates["parse_contamination_max"]
        ),
    }
    report = {
        "dataset": {
            "schema_version": cases["schema_version"],
            "dataset_role": cases.get("dataset_role", "unspecified"),
            "personas": len(cases["personas"]),
            "simulation_seeds": cases["simulation_seeds"],
            "attempt_budgets": cases["attempt_budgets"],
            "knowledge_tag_cases": len(cases["knowledge_tag_cases"]),
            "weak_max": cases["weak_max"],
            "strong_min": cases["strong_min"],
        },
        "inference": inference,
        "knowledge_tags": knowledge_tags,
        "quality_gates": {
            "thresholds": gates,
            "results": gate_results,
            "passed": all(gate_results.values()),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"报告: {args.output}")
    return 1 if args.enforce and not report["quality_gates"]["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
