"""题目归因和校准的可重复评测口径。

未知样本永远不被当作正确；没有金标/用户纠正的样本只参与覆盖、拒绝和
路由可用性统计，不参与 precision 或 observed misattribution。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


STATUSES = ("confirmed", "probable", "unverified")


def _metric(value: float | None, sample_size: int, minimum: int = 1) -> dict:
    return {
        "value": value,
        "sample_size": sample_size,
        "status": "ok" if sample_size >= minimum else "insufficient",
    }


def _gold_map(gold: Any) -> dict[str, dict]:
    if gold is None:
        return {}
    if isinstance(gold, dict):
        rows = gold.get(
            "questions",
            gold.get("labels", gold.get("knowledge_tag_cases", gold)),
        )
        if isinstance(rows, dict):
            rows = [{"question_id": key, **(value if isinstance(value, dict) else {
                "acceptable_knowledge_points": value,
            })} for key, value in rows.items()]
    else:
        rows = gold
    result = {}
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("question_id"):
            continue
        accepted = row.get("acceptable_knowledge_points")
        if accepted is None:
            accepted = row.get(
                "acceptable",
                row.get("knowledge_point_ids", row.get("knowledge_point_id", [])),
            )
        if isinstance(accepted, str):
            accepted = [accepted]
        point_ids = set(accepted or [])
        domain = row.get(
            "adjudicated_domain", row.get("expected_domain", row.get("domain")),
        )
        if not point_ids and not domain:
            continue
        result[row["question_id"]] = {"point_ids": point_ids, "domain": domain}
    return result


def _correct(result: dict, label: dict) -> bool:
    candidate = result.get("candidate") or {}
    point_id = result.get("candidate_point_id") or candidate.get("knowledge_point_id")
    if label["point_ids"]:
        # Gold may identify a frozen point by id or by its human-readable name;
        # accepting both keeps the evaluator compatible with the existing
        # hand-review fixtures without changing runtime attribution records.
        return bool({point_id, candidate.get("name")} & label["point_ids"])
    if label.get("domain"):
        return (result.get("candidate_domain") or (result.get("candidate") or {}).get("domain")) == label["domain"]
    return False


def _candidate_domain(result: dict) -> str | None:
    candidate = result.get("candidate") or {}
    return result.get("candidate_domain") or candidate.get("domain")


def _has_candidate(result: dict) -> bool:
    return bool(result.get("candidate_point_id") or result.get("candidate"))


def _route_top(result: dict, name: str) -> dict | None:
    evidence = result.get("evidence") or {}
    routes = evidence.get("routes", evidence)
    route = routes.get(name) if isinstance(routes, dict) else None
    if not isinstance(route, dict) or not route.get("available"):
        return None
    candidates = route.get("candidates") or []
    return candidates[0] if candidates else None


def _domain_for(result: dict, labels: dict[str, dict]) -> str:
    label = labels.get(result.get("question_id"), {})
    return (
        label.get("domain")
        or result.get("candidate_domain")
        or (result.get("candidate") or {}).get("domain")
        or "未分类"
    )


def evaluate_attributions(
    results: list[dict],
    *,
    gold=None,
    user_corrections=None,
    calibration_hypotheses: list[dict] | None = None,
    dataset_role: str = "development",
    minimum_slice_size: int = 5,
) -> dict:
    """计算固定口径的归因报告。

    ``gold`` 与 ``user_corrections`` 都只被视为已知标签来源；两者以纠正
    优先合并。外部 holdout 不在这里自动产生，调用方需显式传入且自行通过
    双人盲审质量门。
    """
    rows = list(results or [])
    labels = _gold_map(gold)
    labels.update(_gold_map(user_corrections))
    total = len(rows)
    counts = {status: sum(row.get("attribution_status", row.get("status")) == status
                          for row in rows) for status in STATUSES}
    accepted = counts["confirmed"] + counts["probable"]
    route_disagreements = 0
    dense_unavailable = 0
    for row in rows:
        tops = [_route_top(row, name) for name in ("dense", "tfidf", "exact")]
        tops = [item.get("knowledge_point_id") for item in tops if item]
        route_disagreements += int(len(tops) >= 2 and len(set(tops)) > 1)
        dense = (row.get("evidence") or {}).get("dense", {})
        dense_unavailable += int(not dense.get("available", False))

    scorable = [row for row in rows if row.get("question_id") in labels]
    misattributed = sum(
        _has_candidate(row) and not _correct(row, labels[row["question_id"]])
        for row in scorable
    )
    candidate_scorable = [row for row in scorable if _has_candidate(row)]
    domain_scorable = [
        row for row in candidate_scorable
        if labels[row["question_id"]].get("domain")
    ]
    domain_misattributed = sum(
        _candidate_domain(row) != labels[row["question_id"]]["domain"]
        for row in domain_scorable
    )
    jointly_misattributed = sum(
        not _correct(row, labels[row["question_id"]])
        or (
            bool(labels[row["question_id"]].get("domain"))
            and _candidate_domain(row) != labels[row["question_id"]]["domain"]
        )
        for row in candidate_scorable
    )

    precision = {}
    domain_accuracy = {}
    joint_precision = {}
    for status in ("confirmed", "probable"):
        status_rows = [
            row for row in scorable
            if row.get("attribution_status", row.get("status")) == status
        ]
        correct = sum(_correct(row, labels[row["question_id"]]) for row in status_rows)
        precision[status] = _metric(
            correct / len(status_rows) if status_rows else None,
            len(status_rows), minimum_slice_size,
        )
        status_domain_rows = [
            row for row in status_rows if labels[row["question_id"]].get("domain")
        ]
        domain_correct = sum(
            _has_candidate(row)
            and _candidate_domain(row) == labels[row["question_id"]]["domain"]
            for row in status_domain_rows
        )
        domain_accuracy[status] = _metric(
            domain_correct / len(status_domain_rows) if status_domain_rows else None,
            len(status_domain_rows), minimum_slice_size,
        )
        joint_correct = sum(
            _has_candidate(row)
            and _correct(row, labels[row["question_id"]])
            and (
                not labels[row["question_id"]].get("domain")
                or _candidate_domain(row) == labels[row["question_id"]]["domain"]
            )
            for row in status_rows
        )
        joint_precision[status] = _metric(
            joint_correct / len(status_rows) if status_rows else None,
            len(status_rows), minimum_slice_size,
        )

    entered = 0
    resolved = 0
    calibration_outcomes: dict[str, int] = {}
    for hypothesis in calibration_hypotheses or []:
        evidence = hypothesis.get("answered_evidence", hypothesis.get("evidence", []))
        opportunities = hypothesis.get("opportunities", hypothesis.get("max_opportunities", 0))
        status = hypothesis.get("status") or "unknown"
        calibration_outcomes[status] = calibration_outcomes.get(status, 0) + 1
        if "entered" in hypothesis:
            entered_this = bool(hypothesis["entered"])
        elif hypothesis.get("presentation_count") is not None:
            entered_this = hypothesis["presentation_count"] > 0
        else:
            entered_this = bool(evidence)
        if entered_this:
            entered += 1
        if entered_this and status in {"confirmed", "retracted"} and (
            not opportunities or len(evidence) <= opportunities
        ):
            resolved += 1

    domain_rows: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        domain_rows[_domain_for(row, labels)].append(row)
    slices = {}
    for domain, domain_results in sorted(domain_rows.items()):
        domain_labels = [row for row in domain_results if row.get("question_id") in labels]
        domain_confirmed = [row for row in domain_labels
                            if row.get("attribution_status", row.get("status")) == "confirmed"]
        domain_probable = [row for row in domain_labels
                           if row.get("attribution_status", row.get("status")) == "probable"]
        rejection = _metric(
            sum(row.get("attribution_status", row.get("status")) == "unverified"
                for row in domain_results) / len(domain_results)
            if domain_results else None,
            len(domain_results), minimum_slice_size,
        )
        domain_confirmed_precision = _metric(
            sum(_correct(row, labels[row["question_id"]]) for row in domain_confirmed)
            / len(domain_confirmed) if domain_confirmed else None,
            len(domain_confirmed), minimum_slice_size,
        )
        domain_probable_precision = _metric(
            sum(_correct(row, labels[row["question_id"]]) for row in domain_probable)
            / len(domain_probable) if domain_probable else None,
            len(domain_probable), minimum_slice_size,
        )
        slices[domain] = {
            "sample_size": len(domain_results),
            "scorable": len(domain_labels),
            "rejection_rate": rejection["value"],
            "rejection_rate_detail": rejection,
            "confirmed_precision": domain_confirmed_precision["value"],
            "confirmed_precision_detail": domain_confirmed_precision,
            "probable_precision": domain_probable_precision["value"],
            "probable_precision_detail": domain_probable_precision,
        }

    metrics = {
        "rejection_rate": _metric(
            counts["unverified"] / total if total else None, total, minimum_slice_size,
        ),
        "accepted_coverage": _metric(
            accepted / total if total else None, total, minimum_slice_size,
        ),
        "observed_misattribution_rate": _metric(
            misattributed / len(scorable) if scorable else None,
            len(scorable), minimum_slice_size,
        ),
        "observed_domain_misattribution_rate": _metric(
            domain_misattributed / len(domain_scorable) if domain_scorable else None,
            len(domain_scorable), minimum_slice_size,
        ),
        "candidate_domain_accuracy": _metric(
            (len(domain_scorable) - domain_misattributed) / len(domain_scorable)
            if domain_scorable else None,
            len(domain_scorable), minimum_slice_size,
        ),
        "joint_observed_misattribution_rate": _metric(
            jointly_misattributed / len(candidate_scorable) if candidate_scorable else None,
            len(candidate_scorable), minimum_slice_size,
        ),
        "three_route_disagreement_rate": _metric(
            route_disagreements / total if total else None, total, minimum_slice_size,
        ),
        "dense_unavailable_rate": _metric(
            dense_unavailable / total if total else None, total, minimum_slice_size,
        ),
        "calibration_resolution_rate": _metric(
            resolved / entered if entered else None, entered,
        ),
    }

    report = {
        "dataset_role": dataset_role,
        "dataset_role_note": "开发集报告是开发适配结果，不是新题泛化准确率。"
        if dataset_role == "development" else dataset_role,
        "sample_size": total,
        "known_label_sample_size": len(scorable),
        "status_counts": counts,
        "rejection_rate": metrics["rejection_rate"]["value"],
        "rejection_rate_detail": metrics["rejection_rate"],
        "accepted_coverage": metrics["accepted_coverage"]["value"],
        "accepted_coverage_detail": metrics["accepted_coverage"],
        "observed_misattribution_rate": metrics["observed_misattribution_rate"]["value"],
        "observed_misattribution_rate_detail": metrics["observed_misattribution_rate"],
        "observed_domain_misattribution_rate": metrics["observed_domain_misattribution_rate"]["value"],
        "observed_domain_misattribution_rate_detail": metrics["observed_domain_misattribution_rate"],
        "candidate_domain_accuracy": metrics["candidate_domain_accuracy"]["value"],
        "candidate_domain_accuracy_detail": metrics["candidate_domain_accuracy"],
        "joint_observed_misattribution_rate": metrics["joint_observed_misattribution_rate"]["value"],
        "joint_observed_misattribution_rate_detail": metrics["joint_observed_misattribution_rate"],
        "confirmed_precision": precision["confirmed"]["value"],
        "confirmed_precision_detail": precision["confirmed"],
        "probable_precision": precision["probable"]["value"],
        "probable_precision_detail": precision["probable"],
        "confirmed_domain_accuracy": domain_accuracy["confirmed"]["value"],
        "confirmed_domain_accuracy_detail": domain_accuracy["confirmed"],
        "probable_domain_accuracy": domain_accuracy["probable"]["value"],
        "probable_domain_accuracy_detail": domain_accuracy["probable"],
        "confirmed_joint_precision": joint_precision["confirmed"]["value"],
        "confirmed_joint_precision_detail": joint_precision["confirmed"],
        "probable_joint_precision": joint_precision["probable"]["value"],
        "probable_joint_precision_detail": joint_precision["probable"],
        "three_route_disagreement_rate": metrics["three_route_disagreement_rate"]["value"],
        "three_route_disagreement_rate_detail": metrics["three_route_disagreement_rate"],
        "dense_unavailable_rate": metrics["dense_unavailable_rate"]["value"],
        "dense_unavailable_rate_detail": metrics["dense_unavailable_rate"],
        "calibration_resolution_rate": metrics["calibration_resolution_rate"]["value"],
        "calibration_resolution_rate_detail": metrics["calibration_resolution_rate"],
        "calibration": {
            "entered": entered,
            "resolved_within_opportunities": resolved,
            "outcomes": dict(sorted(calibration_outcomes.items())),
        },
        "domain_slices": slices,
        "metric_definitions": {
            "rejection_rate": "unverified / 全部待归因题",
            "accepted_coverage": "(confirmed + probable) / 全部题",
            "observed_misattribution_rate": "兼容旧字段：有知识点金标时按知识点判错、无知识点金标时按领域判错；候选错误数 / 全部有金标样本（无候选不计错）",
            "observed_domain_misattribution_rate": "有审校领域金标的有候选样本中，候选领域缺失或与金标不同的比例",
            "candidate_domain_accuracy": "有审校领域金标且有候选的样本中，候选领域与金标一致的比例",
            "joint_observed_misattribution_rate": "有金标且有候选的样本中，知识点判错或候选领域与领域金标不符（含缺失）的比例",
            "calibration_resolution_rate": "规定机会数内解决(probable→confirmed/retracted)的假设 / 已实际进入校准的假设",
            "precision": "只在相应状态且有确定金标/用户纠正的题上计算；存在知识点金标时表示知识点精度，领域准确性单独报告",
            "confirmed_domain_accuracy": "confirmed 且有领域金标的样本中，预测领域与金标一致的比例；无候选/无候选领域视为错误",
            "probable_domain_accuracy": "probable 且有领域金标的样本中，预测领域与金标一致的比例；无候选/无候选领域视为错误",
            "confirmed_joint_precision": "confirmed 金标样本中知识点正确且（若有领域金标）领域正确的比例",
            "probable_joint_precision": "probable 金标样本中知识点正确且（若有领域金标）领域正确的比例",
        },
        "external_holdout": {
            "status": "optional_blocked",
            "counts_toward_runtime_or_ci": False,
            "note": "缺少两名独立人工盲审者时保持 blocked，不阻塞产品运行。",
        },
    }
    return report
