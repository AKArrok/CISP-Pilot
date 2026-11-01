"""线程隔离的 probable 弱点校准调度。

校准题必须是已经确认归因的题目。这里的作答结果在假设解决前只作证据，
不会直接写入 learner_knowledge_states；避免用一题或未确认标签反向证明模型。
"""

from __future__ import annotations

import time
import logging

import config
from storage import cache, repos
from storage.submissions import thread_transaction


def _progress(hypothesis: dict) -> dict:
    return {
        "answered": hypothesis["evidence_count"],
        "presented": hypothesis["presentation_count"],
        "consistent": hypothesis["consistent_evidence_count"],
        "required": config.ATTRIBUTION_CALIBRATION_MIN_EVIDENCE,
        "status": hypothesis["status"],
    }


def _valid_question(question: dict) -> bool:
    return bool(
        question.get("answer")
        and not question.get("needs_review")
        and not question.get("dup_of")
        and question.get("id")
    )


def _unanswered_question(hypothesis: dict, bank) -> dict | None:
    # Refreshing before answering must return the same question and not inflate
    # presentation_count or create a second evidence slot.
    outstanding = next(
        (item["question_id"] for item in hypothesis["evidence"] if item.get("correct") is None),
        None,
    )
    if outstanding:
        return bank.get_by_id(outstanding)
    return None


def next_question(
    thread_id: str,
    bank,
    domain: str | None = None,
) -> tuple[dict, dict] | None:
    """返回 ``(题目, calibration_meta)``；没有可靠题目时返回 None。"""
    if not config.ENABLE_CALIBRATION:
        return None
    budget_exhausted = (
        repos.calibration_presentation_count(thread_id) >= config.CALIBRATION_MAX_INSERTIONS
    )
    for hypothesis in repos.pending_calibrations(
        thread_id,
        limit=config.CALIBRATION_MAX_PENDING,
    ):
        if domain and hypothesis.get("candidate_domain") not in {None, domain}:
            continue
        same = _unanswered_question(hypothesis, bank)
        if same is not None:
            return same, {
                "calibration_id": hypothesis["id"],
                "calibration_reason": hypothesis["calibration_reason"],
                "calibration_progress": _progress(hypothesis),
            }
        if budget_exhausted:
            repos.resolve_calibration(
                hypothesis["id"],
                "inconclusive",
                now=time.time(),
            )
            continue
        if hypothesis["presentation_count"]:
            last = hypothesis.get("last_presented_at")
            if repos.attempt_count(thread_id, since=last) < config.CALIBRATION_MIN_ATTEMPT_GAP:
                continue
        used_ids = {
            hypothesis["trigger_question_id"],
            *(item.get("question_id") for item in hypothesis["evidence"]),
        }
        reliable_ids = (
            repos.confirmed_question_ids_for_point(hypothesis["knowledge_point_id"]) - used_ids
        )
        candidates = [
            question
            for question in bank.all()
            if _valid_question(question)
            and question["id"] in reliable_ids
            and (not domain or question.get("domain") == domain)
        ]
        if not candidates:
            # 指定域可能暂时过滤掉可靠题；只有无筛选调度才能判定题源耗尽。
            # 不能用未审校 AI 题填坑，也不能留下永远不会再出题的 pending。
            if domain is None:
                repos.resolve_calibration(
                    hypothesis["id"],
                    "inconclusive",
                    now=time.time(),
                )
            continue
        question = sorted(candidates, key=lambda item: item["id"])[0]
        updated = repos.mark_calibration_presented(
            hypothesis["id"],
            question["id"],
            now=time.time(),
        )
        if updated is None or updated.get("status") != "pending":
            continue
        assigned_id = updated.get("_presented_question_id") or question["id"]
        assigned_question = bank.get_by_id(assigned_id)
        if assigned_question is None:
            continue
        return assigned_question, {
            "calibration_id": updated["id"],
            "calibration_reason": updated["calibration_reason"],
            "calibration_progress": _progress(updated),
        }
    return None


def record_answer(
    thread_id: str,
    question_id: str,
    correct: bool,
    attempt_id: int | None = None,
    *, session=None,
) -> dict | None:
    """记录校准证据；两条不同题且结果一致才解决假设。"""
    with thread_transaction(thread_id, session=session) as transaction:
        hypothesis = repos.calibration_for_question(thread_id, question_id, session=transaction)
        if hypothesis is None:
            return None
        updated = repos.record_calibration_evidence(
            hypothesis["id"],
            question_id,
            correct,
            attempt_id=attempt_id,
            now=time.time(),
            session=transaction,
        )
        if updated is None:
            return None
        answered = updated["answered_evidence"]
        distinct_question_ids = {item.get("question_id") for item in answered}
        if (
            len(distinct_question_ids) >= config.ATTRIBUTION_CALIBRATION_MIN_EVIDENCE
            and updated["consistent_evidence_count"] >= config.ATTRIBUTION_CALIBRATION_MIN_EVIDENCE
        ):
            resolved = repos.confirm_calibration_evidence(
                updated["id"],
                now=time.time(),
                session=transaction,
            )
            if resolved is not None:
                updated = resolved
                if resolved.get("_transitioned"):
                    _update_confirmed_review_cards(thread_id, resolved, session=transaction)
        updated["calibration_progress"] = _progress(updated)
    if session is None:
        try:
            cache.delete_keys(f"stats:summary:{thread_id}")
        except Exception:
            logging.getLogger(__name__).warning("Calibration committed; summary cache invalidation failed", exc_info=True)
    return updated


def _update_confirmed_review_cards(thread_id: str, hypothesis: dict, *, session=None) -> None:
    """为本次原子确认的新证据更新题目级复习卡。"""
    applied_ids = set(hypothesis.get("_applied_question_ids", []))
    from quiz import review

    for item in hypothesis["answered_evidence"]:
        question = item["question_id"]
        if question not in applied_ids:
            continue
        domain = repos.question_attribution(question, session=session)
        review.update_card(
            thread_id,
            question,
            domain.get("candidate_domain") if domain else None,
            bool(item["correct"]),
            now=item.get("answered_at"),
            session=session,
        )
