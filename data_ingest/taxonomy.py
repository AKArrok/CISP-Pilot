"""从结构化课件标题建立可审校的知识点候选和题目多标签。

这里故意不调用 LLM：候选树是离线资产，必须可重复、可 diff。启发式标签只是
``candidate``，上线前可由人工/后续 LLM 审校；没有标签的题仍按原 domain 工作。
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict


GENERIC_TITLES = {
    "课程内容", "总结", "目录", "前言", "概述", "知识点串讲", "复习", "练习",
    "技术", "标准", "基本概念",
}


def _canonical(value: str) -> str:
    return re.sub(r"[\s\-—_:：/（）()·•]+", "", value or "").strip()


def _candidate_title(title: str, domain: str) -> tuple[str, str | None] | None:
    raw = (title or "").strip()
    if not raw or raw in GENERIC_TITLES or raw == domain:
        return None
    if raw.startswith("知识子域：") or raw.startswith("知识子域:"):
        parent = re.sub(r"^知识子域[：:]", "", raw).strip()
        return ("", parent) if parent else None
    if "知识点串讲" in raw or re.match(r"^第[一二三四五六七八九十\d]+[章节部分]", raw):
        return None
    if len(_canonical(raw)) < 2:
        return None
    return raw, None


def _point_id(domain: str, name: str) -> str:
    digest = hashlib.sha1(f"{domain}\x1f{_canonical(name)}".encode("utf-8")).hexdigest()[:20]
    return f"kp_{digest}"


def _match_score(text: str, name: str) -> int:
    """返回匹配长度；短英文缩写要求边界，避免把 MAC 命中在乱码中。"""
    if not name:
        return 0
    variants = {name, _canonical(name)}
    best = 0
    for variant in variants:
        if len(variant) < 2:
            continue
        if variant.isascii() and variant.replace("_", "").isalnum():
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(variant)}(?![A-Za-z0-9])", text,
                         flags=re.IGNORECASE):
                best = max(best, len(variant))
        elif variant in text:
            best = max(best, len(variant))
    return best


def _point_variants(point: dict) -> list[str]:
    variants = {point["name"], *(point.get("aliases") or [])}
    original = (point.get("review") or {}).get("original_name")
    if original:
        variants.add(original)
    return sorted((value for value in variants if _canonical(value)), key=lambda value: (-len(value), value))


def _exact_variant_score(text: str, variants: list[str], base: float) -> float:
    canonical_text = _canonical(text).lower()
    best = 0.0
    for variant in variants:
        canonical_variant = _canonical(variant).lower()
        if len(canonical_variant) < 2:
            continue
        raw_variant = variant.strip().lower()
        if raw_variant.isascii():
            matched = bool(re.search(
                rf"(?<![a-z0-9]){re.escape(raw_variant)}(?![a-z0-9])",
                text, flags=re.IGNORECASE,
            ))
        else:
            matched = canonical_variant in canonical_text
        if matched:
            position = canonical_text.rfind(canonical_variant) if not canonical_variant.isascii() else 0
            # 考题常先给背景、后给被问概念；轻量位置先验用于拆解两个都被提及的概念。
            position_bonus = 0.08 * max(0, position) / max(1, len(canonical_text))
            best = max(best, min(
                0.99, base + min(0.10, len(canonical_variant) / 100) + position_bonus
            ))
    return best


def build_semantic_question_tags(points: list[dict], questions: list[dict]) -> list[dict]:
    """同域字符语义候选召回，再以别名证据、相似度和间隔约束确定唯一主标签。"""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    by_domain: dict[str, list[dict]] = defaultdict(list)
    for point in points:
        point["question_count"] = 0
        if point.get("status") == "active":
            by_domain[point["domain"]].append(point)

    indexes = {}
    for domain, domain_points in by_domain.items():
        domain_points.sort(key=lambda point: point["id"])
        documents = [" ".join(_point_variants(point)) for point in domain_points]
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
        indexes[domain] = (domain_points, vectorizer, vectorizer.fit_transform(documents))

    tags = []
    for question in questions:
        domain = question.get("domain")
        if domain not in indexes or not question.get("stem") or question.get("needs_review"):
            continue
        domain_points, vectorizer, matrix = indexes[domain]
        stem = question["stem"]
        analysis = question.get("analysis", "")
        option_map = question.get("options") or {}
        answer_letters = set(question.get("answer", ""))
        answer_options = " ".join(
            text for letter, text in option_map.items() if letter in answer_letters
        )
        distractor_options = " ".join(
            text for letter, text in option_map.items() if letter not in answer_letters
        )
        options = " ".join(option_map.values())
        query = " ".join((stem, analysis, answer_options))
        similarities = cosine_similarity(vectorizer.transform([query]), matrix)[0]
        ranked = sorted(
            range(len(domain_points)), key=lambda index: (-similarities[index], domain_points[index]["id"])
        )[:5]
        # 语义召回的 top-k 不能漏掉题干、解析或选项中的强词面证据。
        direct = {
            index for index, point in enumerate(domain_points)
            if any(
                _exact_variant_score(text, _point_variants(point), 0.60)
                for text in (stem, analysis, options)
            )
        }
        ranked = sorted(set(ranked) | direct)

        scored = []
        for index in ranked:
            point = domain_points[index]
            variants = _point_variants(point)
            stem_exact = _exact_variant_score(stem, variants, 0.88)
            analysis_exact = _exact_variant_score(analysis, variants, 0.82)
            answer_exact = _exact_variant_score(answer_options, variants, 0.80)
            distractor_exact = _exact_variant_score(distractor_options, variants, 0.55)
            semantic = 0.38 + 0.65 * float(similarities[index])
            score = max(stem_exact, analysis_exact, answer_exact, distractor_exact, semantic)
            scored.append((score, bool(stem_exact or analysis_exact or answer_exact), point))
        scored.sort(key=lambda item: (-item[0], item[2]["id"]))
        if not scored or scored[0][0] < 0.52:
            continue
        best_score, has_direct_evidence, best_point = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if not has_direct_evidence and best_score - runner_up < 0.035:
            continue
        best_point["question_count"] += 1
        tags.append({
            "question_id": question["id"],
            "knowledge_point_id": best_point["id"],
            "confidence": round(min(0.99, best_score), 3),
            "is_primary": True,
            "tag_source": "heuristic",
            "tag_method": "domain_constrained_char_tfidf_v1",
        })
    tags.sort(key=lambda tag: (tag["question_id"], tag["knowledge_point_id"]))
    return tags


def build_taxonomy(chunks: list[dict], questions: list[dict]) -> tuple[list[dict], list[dict]]:
    """返回 ``(knowledge_points, question_tags)``，结果顺序稳定。"""
    points: dict[tuple[str, str], dict] = {}
    current_parent: dict[tuple[str, str], str | None] = {}
    ordered_chunks = sorted(chunks, key=lambda c: (
        c.get("domain", ""), c.get("source", ""), c.get("page", 0), c.get("id", "")))
    for chunk in ordered_chunks:
        domain = chunk.get("domain")
        title_info = _candidate_title(chunk.get("title", ""), domain or "")
        if title_info is None:
            continue
        name, parent = title_info
        source_key = (domain or "", chunk.get("source", ""))
        if parent is not None:
            current_parent[source_key] = parent
            continue
        key = (domain or "", _canonical(name))
        point = points.setdefault(key, {
            "id": _point_id(*key), "domain": domain, "name": name,
            "parent_name": current_parent.get(source_key),
            "description": None, "evidence_count": 0, "question_count": 0,
            "status": "candidate",
        })
        point["evidence_count"] += 1

    by_domain: dict[str, list[dict]] = defaultdict(list)
    for point in points.values():
        by_domain[point["domain"]].append(point)
    tags: list[dict] = []
    for question in questions:
        if not question.get("domain") or not question.get("stem"):
            continue
        text = " ".join([
            question.get("stem", ""),
            question.get("analysis", ""),
            " ".join((question.get("options") or {}).values()),
        ])
        matches = []
        for point in by_domain.get(question["domain"], []):
            score = _match_score(text, point["name"])
            if score >= 2:
                matches.append((score, point))
        matches.sort(key=lambda item: (-item[0], item[1]["id"]))
        for index, (score, point) in enumerate(matches[:3]):
            point["question_count"] += 1
            tags.append({
                "question_id": question["id"],
                "knowledge_point_id": point["id"],
                "confidence": round(min(0.98, 0.65 + score / 100), 3),
                "is_primary": index == 0,
                "tag_source": "heuristic",
            })
    result = sorted(points.values(), key=lambda p: (p["domain"], p["name"], p["id"]))
    for point in result:
        # 低频版式标题仍保留供人工审校，但默认不进入诊断候选池。
        point["recommended"] = bool(
            point["evidence_count"] >= 2 or point["question_count"] >= 1
        )
    tags.sort(key=lambda t: (t["question_id"], not t["is_primary"], t["knowledge_point_id"]))
    return result, tags
