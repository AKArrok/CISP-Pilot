"""薄弱点画像：Beta 掌握度 + Elo 评级 + 遗忘曲线（结果经 Redis 缓存，答题时失效）。

参考成熟做法（轻量、无需训练）：
- Beta 后验置信区间：样本不足时不确定能力高低，用 P(能力<阈值) 判薄弱，避免小样本误判。
- Elo 评级（Pelánek 2016）：按时间顺序 replay attempts，答对/答错按期望概率增量更新。
- 遗忘曲线（Duolingo HLR / Ebbinghaus）：记忆强度随间隔指数衰减，到期进入复习队列。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from quiz.store import stats_cache_key
from storage import cache, repos


def _beta_mean(a: float, b: float) -> float:
    """Beta 分布均值（后验掌握度）。"""
    return a / (a + b) if a + b else 0.5


def _beta_p_below(a: float, b: float, threshold: float) -> float:
    """P(X < threshold)，X ~ Beta(a, b)。用不完整 Beta 函数（正则化）精确计算。"""
    from scipy.special import betainc
    return betainc(a, b, threshold)


def _confidence(a: float, b: float) -> float:
    """置信度 = 1 - 90% 区间宽度（样本越多越窄 → 越接近 1）。"""
    from scipy.stats import beta
    lo, hi = beta.ppf([0.05, 0.95], a, b)
    return max(0.0, min(1.0, 1.0 - (hi - lo)))


def _memory_strength(days: float) -> float:
    """遗忘曲线：2^(-天数/半衰期)。"""
    if days <= 0:
        return 1.0
    return 2.0 ** (-days / config.MASTERY_HALF_LIFE_DAYS)


def _elo_expected(rating: float, opponent: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-(rating - opponent) / 400.0))


def domain_profiles(thread_id: str, now: float | None = None) -> dict[str, dict]:
    """{域: 画像}，由 attempts 实时聚合（融合问答侧提问信号）。

    画像含 Beta 后验参数、Elo、遗忘曲线指标；asks 为该域的提问次数
    （用户反复追问 = "哪里不会"的先验信号，与答题对错互补）。
    """
    now = now if now is not None else time.time()
    # rows: (domain, correct, answered_at) 按时间排序
    rows = sorted(
        repos.attempt_rows(thread_id), key=lambda r: r[2]  # answered_at
    )
    ask_rows = {d: (n, last) for d, n, last in repos.signal_rows(thread_id)}
    by_domain: dict[str, dict] = {}

    for domain, correct, answered_at in rows:
        p = by_domain.setdefault(domain, {
            "attempts": 0, "correct": 0, "wrong": 0,
            "last_answered_at": 0.0, "streak": 0, "max_streak": 0,
            "rating": config.ELO_INIT, "rating_history": [],
        })
        p["attempts"] += 1
        p["correct"] += 1 if correct else 0
        p["wrong"] += 0 if correct else 1
        p["last_answered_at"] = max(p["last_answered_at"], answered_at)
        p["streak"] = p["streak"] + 1 if correct else 0
        p["max_streak"] = max(p["max_streak"], p["streak"])

        # Elo 增量更新：对手难度 = 该域历史正确率
        opp_guess = (p["correct"] / p["attempts"]) if p["attempts"] else 0.5
        opponent = 1500 + (opp_guess - 0.5) * 800
        expected = _elo_expected(p["rating"], opponent)
        outcome = 1.0 if correct else 0.0
        p["rating"] += config.ELO_K * (outcome - expected)
        p["rating_history"].append(p["rating"])

    profiles: dict[str, dict] = {}
    # 只有提问、还没答过题的域也建画像（attempts=0，供"高频提问未验证"提示与抽题加权）
    ask_only = {d: v for d, v in ask_rows.items() if d not in by_domain and d in set(config_domains())}
    for d, (_n, _last) in ask_only.items():
        by_domain[d] = {
            "attempts": 0, "correct": 0, "wrong": 0,
            "last_answered_at": 0.0, "streak": 0, "max_streak": 0,
            "rating": config.ELO_INIT, "rating_history": [],
        }
    for domain, p in by_domain.items():
        a = p["correct"] + config.BETA_PRIOR
        b = p["wrong"] + config.BETA_PRIOR
        mastery = _beta_mean(a, b)
        days_since = (now - p["last_answered_at"]) / 86400.0 if p["last_answered_at"] else None
        strength = _memory_strength(days_since) if days_since is not None else 1.0
        p_below = float(_beta_p_below(a, b, config.MASTERY_WEAK_THRESHOLD))
        asks = ask_rows.get(domain, (0, 0.0))[0]
        profiles[domain] = {
            **p,
            "mastery": round(mastery, 4),
            "confidence": round(float(_confidence(a, b)), 4),
            "p_below_threshold": round(p_below, 4),
            "beta_a": round(a, 2), "beta_b": round(b, 2),
            "last_seen_days": round(days_since, 1) if days_since is not None else None,
            "memory_strength": round(float(strength), 4),
            "due_for_review": bool(strength <= config.REVIEW_DUE_STRENGTH),
            "asks": asks,
            # 薄弱 = 后验概率超过阈值（样本不足时概率自然不高，不会误判）
            "weak": bool(p["attempts"] >= 2 and p_below > config.MASTERY_WEAK_PROB),
            "insufficient": bool(p["attempts"] < 2),
            # 高频提问但答题未验证 → 优先引导做题验证
            "ask_unverified": bool(asks >= config.ASK_SIGNAL_MIN and p["attempts"] < 2),
        }
    return profiles


def config_domains() -> list[str]:
    from domains import DOMAINS
    return DOMAINS


def weak_domains(thread_id: str) -> list[str]:
    return sorted(
        d for d, p in domain_profiles(thread_id).items() if p["weak"]
    )


def review_due_domains(thread_id: str) -> list[str]:
    return sorted(
        d for d, p in domain_profiles(thread_id).items()
        if not p["insufficient"] and p["due_for_review"]
    )


def _knowledge_point_profiles(thread_id: str) -> list[dict]:
    states = repos.learner_knowledge_states(thread_id)
    points = {
        point["id"]: point
        for point in repos.load_knowledge_points([
            state["knowledge_point_id"] for state in states
        ])
    } if states else {}
    rows = []
    for state in states:
        point = points.get(state["knowledge_point_id"], {})
        a, b = state["beta_a"], state["beta_b"]
        p_below = float(_beta_p_below(a, b, config.MASTERY_WEAK_THRESHOLD))
        rows.append({
            "id": state["knowledge_point_id"],
            "name": point.get("name", state["knowledge_point_id"]),
            "domain": point.get("domain"),
            "attempts": state["attempts"],
            "correct": state["correct"],
            "mastery": round(_beta_mean(a, b), 4),
            "confidence": round(float(_confidence(a, b)), 4),
            "p_below_threshold": round(p_below, 4),
            "weak": bool(
                state["attempts"] >= 2 and p_below > config.MASTERY_WEAK_PROB
            ),
            "insufficient": state["attempts"] < 2,
            "last_seen": state["last_seen"],
            # learner state 只能由 confirmed 归因产生；字段显式化，避免把
            # 掌握度 confidence 与题目归因可信度混在一起。
            "attribution_status": "confirmed",
            "attribution_evidence_count": None,
            "attribution_reason": "知识点归因已确认",
        })
    return sorted(rows, key=lambda row: (
        not row["weak"], row["mastery"], -row["attempts"], row["name"]
    ))


def summary(thread_id: str) -> dict:
    cache_key = stats_cache_key(thread_id)
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached
    profiles = domain_profiles(thread_id)
    domains_detail = [
        {
            "domain": d,
            "attempts": p["attempts"],
            "correct": p["correct"],
            "accuracy": round(p["correct"] / p["attempts"], 4) if p["attempts"] else None,
            "mastery": p["mastery"],
            "confidence": p["confidence"],
            "weak": p["weak"],
            "insufficient": p["insufficient"],
            "due_for_review": p["due_for_review"],
            "last_seen_days": p["last_seen_days"],
            "rating": round(p["rating"], 1),
            "streak": p["streak"],
            "asks": p["asks"],
            "ask_unverified": p["ask_unverified"],
        }
        for d, p in sorted(profiles.items())
    ]
    total_attempts = sum(p["attempts"] for p in profiles.values())
    total_correct = sum(p["correct"] for p in profiles.values())
    weak = weak_domains(thread_id)
    point_profiles = _knowledge_point_profiles(thread_id)
    system_attribution_rows = repos.question_attribution_rows()
    attribution_rows = repos.apply_thread_attribution_corrections(
        [dict(row) for row in system_attribution_rows], thread_id,
    )
    attribution_counts = {status: 0 for status in ("confirmed", "probable", "unverified")}
    for row in system_attribution_rows:
        status = row["attribution_status"]
        if status in attribution_counts:
            attribution_counts[status] += 1
    try:
        pending_calibrations = repos.pending_calibrations(thread_id)
        correction_counts = repos.attribution_correction_channel_counts(thread_id)
        calibration_counts = repos.calibration_status_counts(thread_id)
    except Exception:
        pending_calibrations = []
        correction_counts = {
            "events": 0,
            "profile_replays": 0,
            "calibration_replacements": 0,
            "calibrations_retracted": 0,
        }
        calibration_counts = {
            "pending": 0, "confirmed": 0, "retracted": 0, "inconclusive": 0,
        }
    try:
        from quiz import review
        due_cards = review.due_count(thread_id)
    except Exception:
        due_cards = 0
    result = {
        "profile_scope": {
            "type": "thread",
            "thread_id": thread_id,
            "legacy_global_history_included": False,
        },
        "legacy_applied_attempts_without_point": sum(
            repos.legacy_applied_attempt_counts(thread_id).values()
        ),
        "total_attempts": total_attempts,
        "total_accuracy": round(total_correct / total_attempts, 4) if total_attempts else None,
        "domains": domains_detail,
        "knowledge_points": point_profiles,
        "attributions": attribution_rows,
        "attribution_status_counts": attribution_counts,
        "attribution_evidence_channels": {
            "system_candidates": attribution_counts,
            "user_corrections": correction_counts,
            "calibration_outcomes": calibration_counts,
        },
        "pending_calibrations": pending_calibrations,
        "weak_domains": weak,
        "weak_knowledge_points": [
            point["id"] for point in point_profiles if point["weak"]
        ],
        "review_due_domains": review_due_domains(thread_id),
        "ask_unverified_domains": sorted(
            d for d, p in profiles.items() if p["ask_unverified"]),
        "review_due_cards": due_cards,
        "model": {
            "type": "beta_elo_forgetting_fsrs",
            "half_life_days": config.MASTERY_HALF_LIFE_DAYS,
            "weak_prob": config.MASTERY_WEAK_PROB,
            "knowledge_point_evidence": "primary_tags_only",
            "attribution_status_meaning": {
                "confirmed": "题目到知识点证据足够强，可进入长期画像",
                "probable": "推测归因，仅进入待校准队列，不改变长期画像",
                "unverified": "证据冲突/不足，仅保留审计，不改变长期画像",
            },
        },
    }
    cache.set_json(cache_key, result, ttl=config.STATS_CACHE_TTL)
    return result


if __name__ == "__main__":
    import json
    print(json.dumps(summary("default"), ensure_ascii=False, indent=1))
