"""quiz 门面：出题 → 作答 → 统计的对外接口（server.py 调用）。"""
from __future__ import annotations

import logging

from quiz import store
from quiz.selector import next_question, QuestionBank
from quiz.grader import grade
from quiz.stats import summary
import config

logger = logging.getLogger(__name__)

store.init_db()


def get_next_question(mode: str = "random", domain: str | None = None,
                      anchor_question_id: str | None = None,
                      thread_id: str = "default") -> dict:
    """抽题；weak 模式可带锚点题定向生成同类题（脱敏：不含答案）。"""
    q = None
    calibration_meta = None
    if mode in {"random", "weak"}:
        calibration_allowed = True
        if mode == "weak":
            # Calibration is useful only after the normal FSRS contract has
            # been honored.  A pending hypothesis must never steal a due card.
            try:
                from quiz import review
                calibration_allowed = not bool(review.due_rows(thread_id, domain, limit=1))
            except Exception:
                calibration_allowed = True
        try:
            from quiz import calibration
            selected = (
                calibration.next_question(
                    thread_id, QuestionBank.get(), domain=domain,
                )
                if calibration_allowed else None
            )
            if selected is not None:
                q, calibration_meta = selected
        except Exception:
            # 校准是旁路能力；数据库/旧库异常不应让普通刷题不可用。
            logger.exception("校准题调度失败，回退普通抽题")
    # "再练一道同类题"：以指定题（通常是刚答错的）为锚点，围绕同一考点定向生成
    if q is None and anchor_question_id:
        anchor_q = QuestionBank.get().get_by_id(anchor_question_id)
        if anchor_q is not None:
            wrong_choice = _last_wrong_choice(thread_id, anchor_question_id)
            try:
                from quiz.generator import _anchor_from_question, generate_question
                q = generate_question(anchor_q["domain"],
                                      anchor=_anchor_from_question(anchor_q, wrong_choice))
            except Exception:
                logger.exception("锚点定向出题失败，回退常规抽题")
    if q is None:
        q = next_question(mode=mode, domain=domain, thread_id=thread_id)

    # AI 补充出题条件：指定域无题可抽，或域内可做题极少
    if q is None and domain is not None:
        bank = QuestionBank.get()
        if len(bank.available(domain, thread_id=thread_id)) < config.QUIZ_AI_MIN_BANK:
            try:
                from quiz.generator import generate_question
                generated = generate_question(domain)
            except Exception:
                generated = None
            if generated:
                q = generated

    if q is None:
        raise ValueError(f"该知识域没有可用题目: {domain or '全部'}")
    # 脱敏返回
    result = {
        "id": q["id"],
        "source": q["source"],
        "domain": q["domain"],
        "stem": q["stem"],
        "options": q["options"],
    }
    if calibration_meta is not None:
        result.update({
            "calibration_reason": calibration_meta["calibration_reason"],
            "calibration_progress": calibration_meta["calibration_progress"],
        })
    return result


def _last_wrong_choice(thread_id: str, question_id: str) -> str | None:
    """该题最近一次答错的选项（定向出题的干扰项依据）。"""
    try:
        from storage import repos
        return repos.last_wrong_choice(thread_id, question_id)
    except Exception:
        return None


def submit_answer(question_id: str, choice: str, thread_id: str = "default") -> dict:
    """作答判分。真题从内存题库取，AI 题从 SQLite 取。"""
    question = QuestionBank.get().get_by_id(question_id)
    if question is None:
        raise KeyError(f"题目不存在: {question_id}")
    from storage import repos
    attribution = None
    calibration_hypothesis = None
    try:
        attribution = repos.effective_question_attribution(thread_id, question_id)
        calibration_hypothesis = repos.calibration_for_question(thread_id, question_id)
    except Exception:
        logger.exception("题目归因读取失败，按未验证证据处理")
    attribution_status = (
        attribution.get("attribution_status") if attribution else "unverified"
    )
    point_id = attribution.get("candidate_point_id") if attribution else None
    point_ids = [point_id] if point_id and attribution_status == "confirmed" else []
    profile_domain = (
        (attribution.get("candidate_domain") if attribution else None)
        or question.get("domain")
    )
    # 校准题本身的标签已 confirmed，但证据在假设解决前暂存，避免一题改变画像。
    profile_applied = attribution_status == "confirmed" and calibration_hypothesis is None
    result = grade(
        question, choice, thread_id=thread_id, profile_domain=profile_domain,
        attribution_status=attribution_status,
        profile_applied=profile_applied,
        calibration_id=calibration_hypothesis["id"] if calibration_hypothesis else None,
        attribution_point_id=point_id if attribution_status == "confirmed" else None,
    )
    if point_ids and calibration_hypothesis is None:
        repos.update_learner_knowledge_states(
            thread_id, point_ids, bool(result["correct"])
        )
    elif calibration_hypothesis is not None:
        from quiz import calibration
        calibration_result = calibration.record_answer(
            thread_id,
            question_id,
            bool(result["correct"]),
            attempt_id=result.get("attempt_id"),
        )
    else:
        calibration_result = None
        if (
            config.ENABLE_CALIBRATION
            and attribution_status == "probable"
            and not result["correct"]
            and point_id
        ):
            repos.create_calibration_hypothesis(
                thread_id=thread_id,
                knowledge_point_id=point_id,
                trigger_question_id=question_id,
                trigger_correct=bool(result["correct"]),
                calibration_reason=(
                    f"题目归因待校准：{attribution.get('reason', '推测中的知识点弱点')}"
                ),
            )
    response = {
        "correct": result["correct"],
        "answer": result["answer"],
        "analysis": result["analysis"],
        "related_chunks": [
            {
                "source": c["source"], "page": c["page"],
                "domain": c["domain"], "text": c["text"][:300],
            }
            for c in result["related_chunks"]
        ],
    }
    response["attribution_status"] = attribution_status
    if attribution:
        response["attribution_reason"] = attribution.get("reason", "")
        response["candidate_point_id"] = attribution.get("candidate_point_id")
        response["candidate_domain"] = attribution.get("candidate_domain")
    if calibration_hypothesis is not None:
        response["calibration_reason"] = calibration_hypothesis["calibration_reason"]
        response["calibration_progress"] = (
            calibration_result or calibration_hypothesis
        ).get("calibration_progress") or {
            "answered": calibration_hypothesis["evidence_count"],
            "required": config.ATTRIBUTION_CALIBRATION_MIN_EVIDENCE,
            "status": calibration_hypothesis["status"],
        }
    return response


def get_stats(thread_id: str) -> dict:
    return summary(thread_id)
