"""保守、可审计的题目到知识点归因。

这个模块只回答一个问题：题目是否足够可信地归属于哪个冻结的 active
知识点。它不读取用户答题记录，也不计算掌握度；后者由 ``quiz.stats`` 和
线程隔离的 learner state 负责。

三路候选故意保持独立：dense embedding、字符 TF-IDF、以及题干/正确选项/
解析上的精确证据。聚合器是 precision-first，无法证明时拒绝归因。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import config
from data_ingest.dense_taxonomy import _normalized, _point_document
from data_ingest.taxonomy import _exact_variant_score, _point_variants


ROUTES = ("dense", "tfidf", "exact")
NEGATIVE_STEM_PATTERN = re.compile(
    r"不包括|不属于|不正确|错误的是|说法错误|理解错误|不需要|无需|"
    r"不能|不应当|不恰当|不符合|不具备|不可以|不是"
)


@dataclass(frozen=True)
class AttributionThresholds:
    dense_top_k: int = config.ATTRIBUTION_DENSE_TOP_K
    dense_min_score: float = config.ATTRIBUTION_DENSE_MIN_SCORE
    dense_min_margin: float = config.ATTRIBUTION_DENSE_MIN_MARGIN
    tfidf_top_k: int = config.ATTRIBUTION_TFIDF_TOP_K
    tfidf_min_score: float = config.ATTRIBUTION_TFIDF_MIN_SCORE
    tfidf_min_margin: float = config.ATTRIBUTION_TFIDF_MIN_MARGIN
    exact_strong_score: float = config.ATTRIBUTION_EXACT_STRONG_SCORE
    exact_min_margin: float = config.ATTRIBUTION_EXACT_MIN_MARGIN
    exact_stem_score: float = config.ATTRIBUTION_EXACT_STEM_SCORE
    exact_correct_option_score: float = config.ATTRIBUTION_EXACT_CORRECT_OPTION_SCORE
    exact_analysis_score: float = config.ATTRIBUTION_EXACT_ANALYSIS_SCORE

    def as_dict(self) -> dict[str, float | int]:
        return {
            "dense_top_k": self.dense_top_k,
            "dense_min_score": self.dense_min_score,
            "dense_min_margin": self.dense_min_margin,
            "tfidf_top_k": self.tfidf_top_k,
            "tfidf_min_score": self.tfidf_min_score,
            "tfidf_min_margin": self.tfidf_min_margin,
            "exact_strong_score": self.exact_strong_score,
            "exact_min_margin": self.exact_min_margin,
            "exact_stem_score": self.exact_stem_score,
            "exact_correct_option_score": self.exact_correct_option_score,
            "exact_analysis_score": self.exact_analysis_score,
        }


def _thresholds(value: AttributionThresholds | dict[str, Any] | None) -> AttributionThresholds:
    if value is None:
        return AttributionThresholds()
    if isinstance(value, AttributionThresholds):
        return value
    fields = AttributionThresholds.__dataclass_fields__
    return AttributionThresholds(**{
        name: value.get(name, getattr(AttributionThresholds(), name))
        for name in fields
    })


def _active_points(points: list[dict]) -> list[dict]:
    return sorted(
        (point for point in points if point.get("status") == "active" and point.get("id")),
        key=lambda point: point["id"],
    )


def _candidate(point: dict, score: float, **extra: Any) -> dict:
    result = {
        "knowledge_point_id": point["id"],
        "point_id": point["id"],
        "domain": point.get("domain"),
        "name": point.get("name", point["id"]),
        "score": round(float(score), 6),
    }
    result.update(extra)
    return result


def _empty_route(reason: str = "unavailable") -> dict:
    return {"available": False, "candidates": [], "reason": reason}


def _correct_option_text(question: dict) -> str:
    options = question.get("options") or {}
    answer = set(question.get("answer", ""))
    return " ".join(text for letter, text in options.items() if letter in answer)


def _is_negative_question(question: dict) -> bool:
    return bool(NEGATIVE_STEM_PATTERN.search(question.get("stem", "")))


def _question_query(question: dict) -> str:
    correct = "" if _is_negative_question(question) else _correct_option_text(question)
    return "。".join((question.get("stem", ""), correct, question.get("analysis", "")))


def _attribution_question_document(question: dict) -> str:
    correct = "" if _is_negative_question(question) else _correct_option_text(question)
    return (
        f"题目：{question.get('stem', '')}。正确选项：{correct}。"
        f"解析：{question.get('analysis', '')}"
    )


def _attribution_point_document(point: dict) -> str:
    """归因专用 dense 文档；父级只作语义上下文，不作知识域硬约束。"""
    document = _point_document(point)
    parent = point.get("parent_name")
    return f"{document}。父级主题：{parent}" if parent else document


def _attribution_point_terms(point: dict) -> str:
    terms = list(_point_variants(point))
    if point.get("parent_name"):
        terms.append(point["parent_name"])
    return " ".join(terms)


def _prepare_tfidf(points: list[dict]):
    """建立字符索引；异常时由调用方显式记录为不可用。"""
    from sklearn.feature_extraction.text import TfidfVectorizer

    vectorizer = TfidfVectorizer(
        analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform([
        _attribution_point_terms(point) for point in points
    ])
    return vectorizer, matrix


def _tfidf_candidates(
    question: dict, points: list[dict], vectorizer, matrix, thresholds: AttributionThresholds,
) -> dict:
    from sklearn.metrics.pairwise import cosine_similarity

    query = vectorizer.transform([_question_query(question)])
    scores = cosine_similarity(query, matrix)[0]
    ranked = sorted(range(len(points)), key=lambda i: (-float(scores[i]), points[i]["id"]))
    candidates = [
        _candidate(points[index], float(scores[index]), evidence={"cosine": float(scores[index])})
        for index in ranked[:max(1, thresholds.tfidf_top_k)]
        if float(scores[index]) >= thresholds.tfidf_min_score
    ]
    return {"available": True, "candidates": candidates, "reason": None}


def _exact_candidates(
    question: dict, points: list[dict], thresholds: AttributionThresholds,
) -> dict:
    stem = question.get("stem", "")
    negative_question = _is_negative_question(question)
    correct = "" if negative_question else _correct_option_text(question)
    analysis = question.get("analysis", "")
    scored = []
    for point in points:
        variants = _point_variants(point)
        evidence = {
            "stem": round(_exact_variant_score(
                stem, variants, thresholds.exact_stem_score,
            ), 6),
            "correct_option": round(_exact_variant_score(
                correct, variants, thresholds.exact_correct_option_score,
            ), 6),
            "analysis": round(_exact_variant_score(
                analysis, variants, thresholds.exact_analysis_score,
            ), 6),
        }
        score = max(evidence.values())
        if score > 0:
            scored.append(_candidate(
                point, score, evidence=evidence,
                evidence_fields=[name for name, item in evidence.items() if item > 0],
            ))
    scored.sort(key=lambda item: (-item["score"], item["knowledge_point_id"]))
    # Exact evidence is intentionally not restricted to a top-k: a unique exact
    # hit in the correct option must survive a dense top-k miss.
    return {
        "available": True, "candidates": scored, "reason": None,
        "correct_option_used": not negative_question,
    }


def _dense_candidates(
    question: dict, points: list[dict], embedder, thresholds: AttributionThresholds,
    point_vectors=None, question_vector=None,
) -> dict:
    if embedder is None:
        return _empty_route("dense_backend_not_configured")
    try:
        if point_vectors is None:
            point_vectors = _normalized(embedder.embed_documents([
                _attribution_point_document(point) for point in points
            ]))
        if question_vector is None:
            question_vector = _normalized(embedder.embed_documents([
                _attribution_question_document(question)
            ]))[0]
        scores = point_vectors @ question_vector
        ranked = sorted(range(len(points)), key=lambda i: (-float(scores[i]), points[i]["id"]))
        candidates = [
            _candidate(points[index], float(scores[index]), evidence={"cosine": float(scores[index])})
            for index in ranked[:max(1, thresholds.dense_top_k)]
            if float(scores[index]) >= thresholds.dense_min_score
        ]
        return {"available": True, "candidates": candidates, "reason": None}
    except Exception as exc:
        # A missing/failed dense backend is a first-class state, never silently
        # relabelled as agreement between the other two routes.
        return _empty_route(f"dense_backend_error:{type(exc).__name__}")


def _route_margin(route: dict) -> float | None:
    candidates = route.get("candidates") or []
    if not candidates:
        return None
    runner = candidates[1]["score"] if len(candidates) > 1 else 0.0
    return float(candidates[0]["score"] - runner)


def _exact_ambiguous(route: dict, thresholds: AttributionThresholds) -> bool:
    candidates = route.get("candidates") or []
    if len(candidates) < 2:
        return False
    best = candidates[0]["score"]
    # Two active points with the same name/alias in different domains are not
    # made safe by a tie-break on id; keep them unverified.
    return all(
        abs(float(item["score"]) - float(best)) <= thresholds.exact_min_margin
        for item in candidates[1:]
        if float(item["score"]) >= thresholds.exact_strong_score
    ) and any(
        item.get("domain") != candidates[0].get("domain")
        for item in candidates[1:]
        if float(item["score"]) >= thresholds.exact_strong_score
    )


def _aggregate(
    question: dict, routes: dict[str, dict], thresholds: AttributionThresholds,
) -> dict:
    tops = {
        name: route["candidates"][0]
        for name, route in routes.items()
        if route.get("available") and route.get("candidates")
    }
    all_candidates = {
        item["knowledge_point_id"]: item
        for route in routes.values()
        for item in route.get("candidates", [])
    }
    exact_top = tops.get("exact")
    exact_strong = bool(
        exact_top and exact_top["score"] >= thresholds.exact_strong_score
    )
    exact_ambiguous = _exact_ambiguous(routes["exact"], thresholds)
    top_ids = [item["knowledge_point_id"] for item in tops.values()]
    counts = Counter(top_ids)
    best_id = None
    if counts:
        best_id = max(
            counts,
            key=lambda point_id: (
                counts[point_id],
                sum(item["score"] for item in tops.values()
                    if item["knowledge_point_id"] == point_id),
                point_id,
            ),
        )
    candidate = all_candidates.get(best_id) if best_id else None
    if exact_strong and not exact_ambiguous:
        candidate = exact_top
        best_id = exact_top["knowledge_point_id"]

    meaningful_ids = {
        name: item["knowledge_point_id"]
        for name, item in tops.items()
        if (
            name == "exact" and item["score"] >= thresholds.exact_strong_score
        ) or (
            name == "dense" and item["score"] >= thresholds.dense_min_score
        ) or (
            name == "tfidf" and item["score"] >= thresholds.tfidf_min_score
        )
    }
    conflict = len(set(meaningful_ids.values())) > 1
    margins_ok = True
    for name, route in routes.items():
        margin = _route_margin(route)
        if margin is None:
            continue
        minimum = {
            "dense": thresholds.dense_min_margin,
            "tfidf": thresholds.tfidf_min_margin,
            "exact": thresholds.exact_min_margin,
        }[name]
        if margin < minimum and len(route.get("candidates", [])) > 1:
            margins_ok = False
    available_count = sum(bool(route.get("available")) for route in routes.values())
    agreement_count = counts.get(best_id, 0) if best_id else 0
    all_three_agree = (
        available_count == 3 and len(tops) == 3 and len(set(top_ids)) == 1
        and not conflict and not exact_ambiguous and margins_ok
    )
    if all_three_agree:
        status = "confirmed"
        reason = "dense、字符TF-IDF、精确证据三路一致且间隔充足"
    elif exact_ambiguous:
        status = "unverified"
        reason = "精确证据命中多个知识域同名/同义知识点，拒绝猜测"
    elif conflict or not margins_ok:
        status = "unverified"
        reason = "候选路由冲突或首选间隔不足，拒绝归因"
    elif exact_strong and not conflict:
        status = "probable"
        reason = "冻结 active 知识点在正确选项/题干/解析中唯一强命中"
    elif agreement_count >= 2 and not conflict and margins_ok:
        status = "probable"
        reason = f"{agreement_count} 路候选一致，证据不足以确认"
    elif not meaningful_ids:
        status = "unverified"
        reason = "三路均无达到阈值的候选"
    elif any(
        not route.get("available") for route in routes.values()
    ):
        status = "unverified"
        reason = "部分归因后端不可用，不能伪装成三路一致"
    else:
        status = "unverified"
        reason = "只有单一路由或证据不足，保守拒绝归因"

    if status == "unverified" and any(
        (route.get("reason") or "").startswith("dense_backend_")
        for route in routes.values()
    ):
        reason = f"{reason}；dense不可用"

    return {
        "question_id": question.get("id"),
        "candidate": candidate,
        "candidate_point_id": candidate["knowledge_point_id"] if candidate else None,
        "candidate_domain": candidate.get("domain") if candidate else None,
        "agreement_count": int(agreement_count),
        "attribution_status": status,
        "status": status,
        "reason": reason,
        "classifier_version": config.ATTRIBUTION_CLASSIFIER_VERSION,
        "evidence": {
            "question_id": question.get("id"),
            "thresholds": thresholds.as_dict(),
            "routes": routes,
            "dense": routes["dense"],
            "tfidf": routes["tfidf"],
            "exact": routes["exact"],
            "available_route_count": available_count,
            "meaningful_top_candidates": meaningful_ids,
            "conflict": conflict,
            "exact_ambiguous": exact_ambiguous,
            "margins_ok": margins_ok,
            "negative_question": _is_negative_question(question),
        },
    }


def classify_question_attribution(
    question: dict,
    points: list[dict],
    embedder=None,
    *,
    thresholds: AttributionThresholds | dict[str, Any] | None = None,
) -> dict:
    """对一题产生完整、可 JSON 序列化的归因结果。"""
    points = _active_points(points)
    threshold = _thresholds(thresholds)
    if not points:
        routes = {name: _empty_route("no_active_points") for name in ROUTES}
    else:
        routes = {
            "dense": _dense_candidates(question, points, embedder, threshold),
            "tfidf": _empty_route("not_prepared"),
            "exact": _exact_candidates(question, points, threshold),
        }
        try:
            vectorizer, matrix = _prepare_tfidf(points)
            routes["tfidf"] = _tfidf_candidates(
                question, points, vectorizer, matrix, threshold,
            )
        except Exception as exc:
            routes["tfidf"] = _empty_route(f"tfidf_error:{type(exc).__name__}")
    return _aggregate(question, routes, threshold)


def classify_questions(
    points: list[dict], questions: list[dict], embedder=None,
    *,
    thresholds: AttributionThresholds | dict[str, Any] | None = None,
) -> list[dict]:
    """批量归因；dense 和 TF-IDF 索引各建一次，输出按题目 id 稳定排序。"""
    active = _active_points(points)
    threshold = _thresholds(thresholds)
    if not active:
        return [classify_question_attribution(q, [], embedder, thresholds=threshold)
                for q in sorted(questions, key=lambda item: item.get("id", ""))]

    try:
        vectorizer, matrix = _prepare_tfidf(active)
    except Exception:
        vectorizer = matrix = None
    ordered_questions = sorted(questions, key=lambda item: item.get("id", ""))
    point_vectors = question_vectors = None
    dense_failure = None
    if embedder is not None:
        try:
            point_vectors = _normalized(embedder.embed_documents([
                _attribution_point_document(point) for point in active
            ]))
            question_vectors = _normalized(embedder.embed_documents([
                _attribution_question_document(question)
                for question in ordered_questions
            ]))
        except Exception as exc:
            dense_failure = _empty_route(f"dense_backend_error:{type(exc).__name__}")

    results = []
    for index, question in enumerate(ordered_questions):
        dense = dense_failure or _dense_candidates(
            question, active, embedder, threshold,
            point_vectors, question_vectors[index] if question_vectors is not None else None,
        )
        if vectorizer is None:
            tfidf = _empty_route("tfidf_unavailable")
        else:
            try:
                tfidf = _tfidf_candidates(question, active, vectorizer, matrix, threshold)
            except Exception as exc:
                tfidf = _empty_route(f"tfidf_error:{type(exc).__name__}")
        exact = _exact_candidates(question, active, threshold)
        results.append(_aggregate(
            question, {"dense": dense, "tfidf": tfidf, "exact": exact}, threshold,
        ))
    return results


# Descriptive aliases used by import/build scripts.
build_attribution_results = classify_questions
classify_attribution = classify_question_attribution
