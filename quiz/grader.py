"""判分与解析组装（确定性比对，不走 LLM）。"""
from __future__ import annotations

import logging

from quiz import store

logger = logging.getLogger(__name__)


def normalize_choice(choice: str) -> str:
    return "".join(sorted(c.upper() for c in choice if c.upper() in "ABCD"))


def validate_choice(choice: str) -> str:
    """返回规范化选项；非法字符、空答案和重复选项不得写入学习记录。"""
    compact = "".join(c for c in choice if not c.isspace())
    normalized = normalize_choice(choice)
    if (not compact or any(c.upper() not in "ABCD" for c in compact)
            or len(set(normalized)) != len(normalized)):
        raise ValueError("选项必须是 A-D 中不重复的字母组合")
    return normalized


def grade(
    question: dict,
    choice: str,
    *,
    include_related: bool = True,
    thread_id: str = "default",
    profile_domain: str | None = None,
    attribution_status: str = "confirmed",
    profile_applied: bool = True,
    calibration_id: int | None = None,
    attribution_point_id: str | None = None,
) -> dict:
    """判分并保存原始证据。

    只有 ``confirmed`` 且 ``profile_applied`` 的作答才更新 FSRS；Beta/Elo
    由调用方在确认归因后更新。这样 probable/unverified 仍可审计，却不会
    污染长期画像。
    """
    if attribution_status not in {"confirmed", "probable", "unverified"}:
        raise ValueError("无效的题目归因状态")
    profile_applied = bool(profile_applied and attribution_status == "confirmed")
    normalized_choice = validate_choice(choice)
    correct = normalized_choice == normalize_choice(question["answer"])
    attempt_id = store.record_attempt(
        thread_id=thread_id,
        question_id=question["id"],
        question_source=question.get("source", "unknown"),
        domain=profile_domain or question.get("domain"),
        stem=question["stem"],
        choice=normalized_choice,
        correct=correct,
        attribution_status=attribution_status,
        profile_applied=profile_applied,
        calibration_id=calibration_id,
        attribution_point_id=attribution_point_id,
    )
    if profile_applied:
        try:
            from quiz import review
            review.update_card(
                thread_id, question["id"], profile_domain or question.get("domain"), correct,
            )
        except Exception:
            logger.exception("FSRS 复习卡更新失败（不影响判分）")
    return {
        "correct": correct,
        "answer": question["answer"],
        "analysis": question.get("analysis") or "题库未附解析。",
        "related_chunks": related_chunks(question) if include_related else [],
        "attempt_id": attempt_id,
    }


def related_chunks(question: dict, top_k: int = 2) -> list[dict]:
    """用题干在该题知识域内检索关联课件片段（答错时帮助回看原文）。"""
    try:
        from retrieval.hybrid import HybridRetriever

        return HybridRetriever.get().retrieve(
            question["stem"], top_k=top_k, domain=question.get("domain"))
    except Exception:
        return []
