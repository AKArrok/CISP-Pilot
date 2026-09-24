"""真正的 dense embedding 知识点召回；与生产 TF-IDF 标签器并行离线评测。"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from data_ingest.taxonomy import _exact_variant_score, _point_variants


def _normalized(vectors: list[list[float]]) -> np.ndarray:
    array = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    return array / np.maximum(norms, 1e-12)


def _eligible(points: list[dict], questions: list[dict]) -> tuple[list[dict], list[dict]]:
    active_points = sorted(
        (point for point in points if point.get("status") == "active"),
        key=lambda point: point["id"],
    )
    eligible_questions = [
        question for question in questions
        if question.get("domain") and question.get("stem") and not question.get("needs_review")
    ]
    return active_points, eligible_questions


def _point_document(point: dict) -> str:
    return (
        f"知识域：{point['domain']}。知识点：{point['name']}。同义表达："
        + "、".join(_point_variants(point))
    )


def _question_document(question: dict, *, include_stored_domain: bool) -> str:
    options = question.get("options") or {}
    answer_letters = set(question.get("answer", ""))
    correct = " ".join(
        text for letter, text in options.items() if letter in answer_letters
    )
    prefix = f"知识域：{question['domain']}。" if include_stored_domain else ""
    return (
        f"{prefix}题目：{question['stem']}。正确选项：{correct}。"
        f"解析：{question.get('analysis', '')}"
    )


def _domain_indexes(active_points: list[dict]) -> dict[str, list[int]]:
    indexes: dict[str, list[int]] = defaultdict(list)
    for index, point in enumerate(active_points):
        indexes[point["domain"]].append(index)
    return indexes


def _question_evidence(question: dict) -> tuple[str, str]:
    option_map = question.get("options") or {}
    answer_letters = set(question.get("answer", ""))
    answer_options = " ".join(
        text for letter, text in option_map.items() if letter in answer_letters
    )
    distractors = " ".join(
        text for letter, text in option_map.items() if letter not in answer_letters
    )
    return answer_options, distractors


def _direct_score(question: dict, point: dict) -> float:
    """正确选项是分类证据，强度高于题干中的背景概念。"""
    answer_options, distractors = _question_evidence(question)
    variants = _point_variants(point)
    return max(
        _exact_variant_score(answer_options, variants, 0.95),
        _exact_variant_score(question["stem"], variants, 0.88),
        _exact_variant_score(question.get("analysis", ""), variants, 0.82),
        _exact_variant_score(distractors, variants, 0.55),
    )


def _strong_answer_domain(question: dict, active_points: list[dict]) -> str | None:
    """正确选项唯一强命中某一知识域时，允许纠正 dense 域推断。"""
    answer_options, _ = _question_evidence(question)
    matches = []
    for point in active_points:
        score = _exact_variant_score(answer_options, _point_variants(point), 0.95)
        if score >= 0.95:
            matches.append((score, point["domain"]))
    if not matches:
        return None
    best = max(score for score, _ in matches)
    domains = {domain for score, domain in matches if score == best}
    return next(iter(domains)) if len(domains) == 1 else None


def _select_domain(
    similarities: np.ndarray,
    indexes: dict[str, list[int]],
    stored_domain: str,
    stored_domain_prior: float,
) -> str:
    """按每域 top-3 聚合，避免知识点数量多的域天然占优。"""
    scored = []
    for domain, point_indexes in indexes.items():
        values = np.sort(similarities[point_indexes])[-3:]
        score = 0.7 * float(values[-1]) + 0.3 * float(values.mean())
        if domain == stored_domain:
            score += stored_domain_prior
        scored.append((score, domain))
    return max(scored, key=lambda item: (item[0], item[1]))[1]


def predict_dense_question_domains(
    points: list[dict], questions: list[dict], embedder,
    *, stored_domain_prior: float = 0.0,
) -> dict[str, str]:
    """不把原始域写入查询，使用 dense 语义并允许软原始域先验。"""
    active_points, eligible_questions = _eligible(points, questions)
    if not active_points or not eligible_questions:
        return {}
    point_vectors = _normalized(embedder.embed_documents([
        _point_document(point) for point in active_points
    ]))
    question_vectors = _normalized(embedder.embed_documents([
        _question_document(question, include_stored_domain=False)
        for question in eligible_questions
    ]))
    indexes = _domain_indexes(active_points)
    return {
        question["id"]: _select_domain(
            point_vectors @ query_vector,
            indexes,
            question["domain"],
            stored_domain_prior,
        )
        for question, query_vector in zip(eligible_questions, question_vectors, strict=True)
    }


def build_dense_question_tags(
    points: list[dict], questions: list[dict], embedder,
    *, min_similarity: float = 0.40, min_margin: float = 0.02,
    domain_mode: str = "stored", stored_domain_prior: float = 0.0,
    predicted_domains: dict[str, str] | None = None,
) -> list[dict]:
    """dense top-5 候选 + 知识域约束 + 通用题目证据分类。"""
    if domain_mode not in {"stored", "inferred"}:
        raise ValueError("domain_mode must be 'stored' or 'inferred'")
    active_points, eligible_questions = _eligible(points, questions)
    if not active_points or not eligible_questions:
        return []

    point_documents = [_point_document(point) for point in active_points]
    question_documents = [
        _question_document(question, include_stored_domain=domain_mode == "stored")
        for question in eligible_questions
    ]

    point_vectors = _normalized(embedder.embed_documents(point_documents))
    question_vectors = _normalized(embedder.embed_documents(question_documents))
    by_domain = _domain_indexes(active_points)
    if domain_mode == "inferred" and predicted_domains is None:
        predicted_domains = {
            question["id"]: _select_domain(
                point_vectors @ query_vector,
                by_domain,
                question["domain"],
                stored_domain_prior,
            )
            for question, query_vector in zip(
                eligible_questions, question_vectors, strict=True
            )
        }

    tags = []
    for question, query_vector in zip(eligible_questions, question_vectors, strict=True):
        selected_domain = (
            (predicted_domains or {}).get(question["id"])
            if domain_mode == "inferred" else question["domain"]
        )
        selected_domain = _strong_answer_domain(question, active_points) or selected_domain
        candidate_indexes = by_domain.get(selected_domain, [])
        if not candidate_indexes:
            continue
        similarities = point_vectors[candidate_indexes] @ query_vector
        top_local = set(np.argsort(-similarities, kind="stable")[:5])
        # dense top-k 不能丢掉题干/解析/正确选项中的强词面证据。
        top_local |= {
            local_index
            for local_index, point_index in enumerate(candidate_indexes)
            if _direct_score(question, active_points[point_index]) > 0
        }
        scored = []
        for local_index in top_local:
            point_index = candidate_indexes[int(local_index)]
            point = active_points[point_index]
            similarity = float(similarities[int(local_index)])
            direct = _direct_score(question, point)
            score = max(similarity, direct)
            scored.append((score, similarity, point))
        scored.sort(key=lambda item: (-item[0], item[2]["id"]))
        best_score, best_similarity, best_point = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if best_similarity < min_similarity and best_score == best_similarity:
            continue
        if best_score - runner_up < min_margin:
            continue
        tags.append({
            "question_id": question["id"],
            "knowledge_point_id": best_point["id"],
            "confidence": round(min(0.99, best_score), 3),
            "is_primary": True,
            "tag_source": "heuristic",
            "tag_method": f"{domain_mode}_domain_constrained_dense_v3",
            "predicted_domain": selected_domain,
        })
    return sorted(tags, key=lambda tag: (tag["question_id"], tag["knowledge_point_id"]))
