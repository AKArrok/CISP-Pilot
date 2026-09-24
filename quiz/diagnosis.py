"""知识域级入门诊断。

这不是普通 ``weak`` 抽题：它先把用户自评当作先验，再插入未选择域的校准题，
根据当前诊断结果决定下一题，并把每一步保存成可回放的事件。题目还没有知识点
多标签时，诊断粒度明确限制在知识域；后续补齐标签后可替换选题函数而不改变 API。
"""
from __future__ import annotations

import json
import time
import uuid

from domains import DOMAINS
from quiz.grader import grade
from quiz.selector import QuestionBank
from storage import repos

MIN_QUESTIONS = 6
DEFAULT_MAX_QUESTIONS = 12
MAX_QUESTIONS = 20
MAX_SELECTED_DOMAINS = 4


def _loads(value: str, default):
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _session(session_id: str) -> dict:
    row = repos.get_diagnostic_session(session_id)
    if row is None:
        raise KeyError(f"诊断会话不存在: {session_id}")
    row["selected_domains"] = _loads(row["selected_domains"], [])
    row["self_report"] = _loads(row["self_report"], {})
    row["question_ids"] = _loads(row["question_ids"], [])
    row["responses"] = _loads(row["responses"], [])
    return row


def _valid_domains(domains: list[str]) -> list[str]:
    seen = set()
    result = []
    for domain in domains:
        if domain not in DOMAINS:
            raise ValueError(f"未知知识域: {domain}")
        if domain not in seen:
            result.append(domain)
            seen.add(domain)
    return result


def start_session(thread_id: str, selected_domains: list[str],
                  self_report: dict | None = None,
                  max_questions: int = DEFAULT_MAX_QUESTIONS) -> dict:
    """创建诊断会话；自评只作为先验保存，不直接判定薄弱。"""
    domains = _valid_domains(selected_domains)
    if not domains:
        raise ValueError("至少选择一个自认为薄弱的知识域")
    if len(domains) > MAX_SELECTED_DOMAINS:
        raise ValueError(f"一次最多选择 {MAX_SELECTED_DOMAINS} 个薄弱知识域")
    if not 1 <= len(thread_id) <= 64:
        raise ValueError("thread_id 长度必须在 1 到 64 之间")
    if not MIN_QUESTIONS <= max_questions <= MAX_QUESTIONS:
        raise ValueError(f"max_questions 必须在 {MIN_QUESTIONS} 到 {MAX_QUESTIONS} 之间")
    report = self_report if isinstance(self_report, dict) else {}
    try:
        daily_minutes = int(report.get("daily_minutes")) if report.get("daily_minutes") is not None else None
    except (TypeError, ValueError):
        daily_minutes = None
    if daily_minutes is not None:
        daily_minutes = max(5, min(720, daily_minutes))
    # 只保留稳定、可解释的画像字段，避免把任意前端 JSON 作为事实写入。
    clean_report = {
        "goal": str(report.get("goal", ""))[:100],
        "exam_date": str(report.get("exam_date", ""))[:32],
        "daily_minutes": daily_minutes,
        "experience": str(report.get("experience", ""))[:100],
        "domain_status": {
            d: str((report.get("domain_status") or {}).get(d, ""))[:40]
            for d in domains
            if isinstance(report.get("domain_status"), dict)
            and (report.get("domain_status") or {}).get(d)
        },
    }
    session_id = str(uuid.uuid4())
    now = time.time()
    repos.create_diagnostic_session(
        session_id, thread_id, json.dumps(domains, ensure_ascii=False),
        json.dumps(clean_report, ensure_ascii=False), max_questions, now,
    )
    repos.record_diagnostic_event(session_id, "started", json.dumps({
        "selected_domains": domains, "self_report": clean_report,
    }, ensure_ascii=False), now)
    return {
        "session_id": session_id,
        "status": "active",
        "selected_domains": domains,
        "self_report": clean_report,
        "min_questions": MIN_QUESTIONS,
        "max_questions": max_questions,
    }


def _responses_by_domain(responses: list[dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for response in responses:
        result.setdefault(response.get("domain", ""), []).append(response)
    return result


def _domain_stats(responses: list[dict], domain: str) -> dict:
    rows = [r for r in responses if r.get("domain") == domain]
    correct = sum(bool(r.get("correct")) for r in rows)
    attempts = len(rows)
    # Beta(1, 1) 平滑，避免一题答错就声称“掌握度为 0”。
    mastery = (correct + 1) / (attempts + 2)
    confidence = attempts / (attempts + 4)
    high_confidence_errors = sum(
        not r.get("correct") and r.get("confidence") is not None
        and r["confidence"] >= 2 for r in rows
    )
    low_confidence_correct = sum(
        bool(r.get("correct")) and r.get("confidence") is not None
        and r["confidence"] <= 1 for r in rows
    )
    return {
        "domain": domain,
        "attempts": attempts,
        "correct": correct,
        "accuracy": round(correct / attempts, 4) if attempts else None,
        "mastery": round(mastery, 4) if attempts else None,
        "confidence": round(confidence, 4),
        "weak": bool(attempts >= 2 and mastery < 0.6),
        "insufficient": attempts < 2,
        "evidence": "diagnostic" if attempts else "self_report",
        "high_confidence_errors": high_confidence_errors,
        "low_confidence_correct": low_confidence_correct,
        "calibration_signal": (
            "misconception_risk" if high_confidence_errors
            else "fragile_knowledge" if low_confidence_correct else None
        ),
    }


def _candidate_pool(bank: QuestionBank, domain: str | None = None) -> list[dict]:
    rows = bank.all(domain)
    return [q for q in rows if q.get("answer") and not q.get("needs_review")
            and not q.get("dup_of")]


def _select_question(candidates: list[dict], session: dict) -> dict:
    """优先覆盖尚未测过的知识点；没有标签时退化为稳定题序。"""
    if not candidates:
        raise ValueError("没有可选题目")
    try:
        tags = repos.question_knowledge_points_bulk([q["id"] for q in candidates])
        seen = {
            point_id
            for response in session["responses"]
            for point_id in response.get("knowledge_points", [])
        }
    except Exception:
        tags, seen = {}, set()

    def score(question: dict) -> tuple[int, int, str]:
        question_tags = tags.get(question["id"], [])
        novel = sum(tag["knowledge_point_id"] not in seen for tag in question_tags)
        primary = any(tag.get("is_primary") for tag in question_tags)
        return novel, int(primary), question["id"]

    return max(sorted(candidates, key=lambda q: q["id"]), key=score)


def _choose_domain(session: dict, bank: QuestionBank) -> str | None:
    selected = session["selected_domains"]
    by_domain = _responses_by_domain(session["responses"])
    tested_ids = set(session["question_ids"])
    all_domains = [d for d in DOMAINS if _candidate_pool(bank, d)]
    controls = [d for d in all_domains if d not in selected]
    n = len(session["responses"])

    # 约每 5 题安排 1 道控制题；若题库不支持控制域则回到焦点域。
    want_control = bool(controls) and n > 0 and n % 5 == 4
    pool_domains = controls if want_control else [d for d in selected if d in all_domains]
    if not pool_domains:
        pool_domains = all_domains
    if not pool_domains:
        return None

    # 先保证每个焦点域至少有两道题，再按后验不确定性和自评加权。
    counts = {d: len(by_domain.get(d, [])) for d in pool_domains}
    if not want_control:
        unrepresented = [d for d in pool_domains if counts[d] == 0]
        if unrepresented:
            return min(unrepresented, key=selected.index)
        under_sampled = [d for d in pool_domains if counts[d] < 2]
        if under_sampled:
            return min(under_sampled, key=lambda d: (counts[d], selected.index(d)))

    def score(domain: str) -> float:
        stats = _domain_stats(session["responses"], domain)
        available = [q for q in _candidate_pool(bank, domain) if q["id"] not in tested_ids]
        if not available:
            return -1.0
        uncertainty = 1.0 - stats["confidence"]
        weakness = 1.0 - (stats["mastery"] if stats["mastery"] is not None else 0.5)
        self_bonus = 0.2 if domain in selected else 0.0
        return uncertainty + weakness * 0.5 + self_bonus

    return max(pool_domains, key=score)


def next_question(session_id: str) -> dict:
    """返回当前未作答题，或按诊断状态选择下一题。"""
    session = _session(session_id)
    if session["status"] == "completed":
        return {"session_id": session_id, "status": "completed", "report": report(session_id)}

    # 重试 GET 时返回同一道题，避免用户刷新页面导致题目跳跃。
    answered_ids = {r.get("question_id") for r in session["responses"]}
    if session["question_ids"] and session["question_ids"][-1] not in answered_ids:
        q = QuestionBank.get().get_by_id(session["question_ids"][-1])
        if q:
            return _public_question(session, q)

    if len(session["responses"]) >= session["max_questions"]:
        repos.update_diagnostic_session(session_id, status="completed")
        return {"session_id": session_id, "status": "completed", "report": report(session_id)}

    bank = QuestionBank.get()
    domain = _choose_domain(session, bank)
    if domain is None:
        repos.update_diagnostic_session(session_id, status="completed")
        return {"session_id": session_id, "status": "completed", "report": report(session_id)}
    tested_ids = set(session["question_ids"])
    candidates = [q for q in _candidate_pool(bank, domain) if q["id"] not in tested_ids]
    if not candidates:
        # 某域题目用尽时跨到仍有未测题的域，不能把“跳过”伪装成答题证据。
        fallback = [
            q for q in _candidate_pool(bank)
            if q.get("id") not in tested_ids
        ]
        if not fallback:
            repos.update_diagnostic_session(session_id, status="completed")
            return {"session_id": session_id, "status": "completed", "report": report(session_id)}
        question = _select_question(fallback, session)
    else:
        question = _select_question(candidates, session)
    session["question_ids"].append(question["id"])
    repos.update_diagnostic_session(
        session_id, question_ids=json.dumps(session["question_ids"], ensure_ascii=False))
    repos.record_diagnostic_event(session_id, "question_selected", json.dumps({
        "question_id": question["id"], "domain": question.get("domain", domain),
        "response_count": len(session["responses"]),
    }, ensure_ascii=False))
    return _public_question(session, question)


def _public_question(session: dict, question: dict) -> dict:
    return {
        "session_id": session["id"],
        "status": "active",
        "progress": {
            "answered": len(session["responses"]),
            "max_questions": session["max_questions"],
            "selected_domains": session["selected_domains"],
        },
        "question": {
            "id": question["id"],
            "source": question.get("source"),
            "domain": question.get("domain"),
            "stem": question["stem"],
            "options": question["options"],
        },
    }


def answer(session_id: str, question_id: str, choice: str,
           confidence: int | None = None, reason: str | None = None) -> dict:
    """判分并保存诊断证据；confidence 为用户自报信心，不参与正确性判定。"""
    session = _session(session_id)
    if session["status"] != "active":
        raise ValueError("诊断会话已结束")
    if not session["question_ids"] or session["question_ids"][-1] != question_id:
        raise ValueError("只能回答当前诊断题")
    if any(r.get("question_id") == question_id for r in session["responses"]):
        raise ValueError("该题已经回答过")
    question = QuestionBank.get().get_by_id(question_id)
    if question is None:
        raise KeyError(f"题目不存在: {question_id}")
    attribution = repos.effective_question_attribution(session["thread_id"], question_id)
    point_id = attribution.get("candidate_point_id") if attribution else None
    point_ids = [point_id] if (
        point_id and attribution.get("attribution_status") == "confirmed"
    ) else []
    profile_domain = (
        (attribution.get("candidate_domain") if attribution else None)
        or question.get("domain")
    )
    # 诊断页只展示确定性解析，不消费远程检索/精排额度。
    result = grade(
        question, choice, include_related=False,
        thread_id=session["thread_id"], profile_domain=profile_domain,
        # 诊断本身只把选题的原始域作为域级证据；细粒度知识点仍须
        # 通过 QuestionAttribution 的 confirmed 状态才可写入。
        attribution_status="confirmed",
        profile_applied=True,
        attribution_point_id=point_id,
    )
    repos.update_learner_knowledge_states(
        session["thread_id"], point_ids, bool(result["correct"])
    )
    response = {
        "question_id": question_id,
        "domain": profile_domain,
        "choice": choice,
        "correct": bool(result["correct"]),
        "confidence": max(0, min(3, int(confidence))) if confidence is not None else None,
        "reason": str(reason or "")[:1000],
        "knowledge_points": point_ids,
        "answered_at": time.time(),
    }
    session["responses"].append(response)
    done = len(session["responses"]) >= session["max_questions"] or _should_stop(session)
    repos.update_diagnostic_session(
        session_id,
        status="completed" if done else "active",
        responses=json.dumps(session["responses"], ensure_ascii=False),
    )
    repos.record_diagnostic_event(session_id, "answered", json.dumps(response, ensure_ascii=False))
    payload = {
        "session_id": session_id,
        "status": "completed" if done else "active",
        "result": {
            "correct": result["correct"], "answer": result["answer"],
            "analysis": result["analysis"],
        },
        "progress": {"answered": len(session["responses"]),
                     "max_questions": session["max_questions"]},
    }
    if done:
        payload["report"] = report(session_id)
    return payload


def _should_stop(session: dict) -> bool:
    if len(session["responses"]) < MIN_QUESTIONS:
        return False
    by_domain = _responses_by_domain(session["responses"])
    # 每个焦点域都有至少两条证据后，若后验置信度已足够，允许提前结束。
    if any(len(by_domain.get(d, [])) < 2 for d in session["selected_domains"]):
        return False
    return all(_domain_stats(session["responses"], d)["confidence"] >= 0.5
               for d in session["selected_domains"])


def report(session_id: str) -> dict:
    session = _session(session_id)
    observed_domains = set(r.get("domain") for r in session["responses"] if r.get("domain"))
    domains = list(dict.fromkeys(session["selected_domains"] + sorted(observed_domains)))
    domain_rows = [_domain_stats(session["responses"], d) for d in domains]
    point_ids = sorted({
        point_id
        for response in session["responses"]
        for point_id in response.get("knowledge_points", [])
    })
    point_names = {p["id"]: p for p in repos.load_knowledge_points(point_ids)} if point_ids else {}
    historical_states = {
        row["knowledge_point_id"]: row
        for row in repos.learner_knowledge_states(session["thread_id"], point_ids)
    } if point_ids else {}
    point_rows = []
    for point_id in point_ids:
        rows = [r for r in session["responses"] if point_id in r.get("knowledge_points", [])]
        session_correct = sum(bool(r.get("correct")) for r in rows)
        session_attempts = len(rows)
        state = historical_states.get(point_id)
        correct = state["correct"] if state else session_correct
        attempts = state["attempts"] if state else session_attempts
        mastery = ((state["beta_a"] / (state["beta_a"] + state["beta_b"]))
                   if state else (correct + 1) / (attempts + 2))
        point = point_names.get(point_id, {})
        point_rows.append({
            "id": point_id,
            "name": point.get("name", point_id),
            "domain": point.get("domain"),
            "status": point.get("status", "candidate"),
            "provisional": point.get("status", "candidate") != "active",
            "attempts": attempts,
            "correct": correct,
            "session_attempts": session_attempts,
            "mastery": round(mastery, 4),
            "insufficient": attempts < 2,
            "weak": attempts >= 2 and mastery < 0.6,
            "session_high_confidence_errors": sum(
                not r.get("correct") and r.get("confidence") is not None
                and r["confidence"] >= 2 for r in rows
            ),
            "session_low_confidence_correct": sum(
                bool(r.get("correct")) and r.get("confidence") is not None
                and r["confidence"] <= 1 for r in rows
            ),
        })
    weak = [row["domain"] for row in domain_rows if row["weak"]]
    recommendations = []
    for row in sorted(domain_rows, key=lambda x: (x["mastery"] is None, x["mastery"] or 1)):
        if row["high_confidence_errors"]:
            recommendations.append(
                f"优先澄清「{row['domain']}」中的高信心错题，排查稳定误区"
            )
        elif row["insufficient"]:
            recommendations.append(f"先用 2 道校准题验证「{row['domain']}」")
        elif row["weak"]:
            recommendations.append(f"优先复习「{row['domain']}」并做简化诊断题")
    return {
        "session_id": session["id"],
        "status": session["status"],
        "answered": len(session["responses"]),
        "selected_domains": session["selected_domains"],
        "self_report": session["self_report"],
        "domains": domain_rows,
        "knowledge_points": point_rows,
        "weak_domains": weak,
        "misconception_evidence": [
            {
                "question_id": response["question_id"],
                "domain": response.get("domain"),
                "knowledge_points": response.get("knowledge_points", []),
                "confidence": response.get("confidence"),
                "reason": response.get("reason", ""),
            }
            for response in session["responses"]
            if not response.get("correct")
            and response.get("confidence") is not None
            and response["confidence"] >= 2
        ],
        "recommendations": recommendations[:5],
        "evidence_policy": {
            "self_report": "先验，不能单独判定薄弱",
            "diagnostic": "答题证据，样本不足时保留 insufficient",
        },
    }
