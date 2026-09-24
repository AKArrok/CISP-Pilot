"""仓库层：题库/知识块的读写与计数（ORM 唯一出口，供 selector/manifest/import 使用）。"""

from __future__ import annotations

import json
import logging
import time

from sqlalchemy import case, func, or_, select, text, update

import config

from storage import cache, db
from storage.models import (
    DiagnosticEvent,
    DiagnosticSession,
    KBChunk,
    KnowledgePoint,
    CalibrationHypothesis,
    LearnerAttempt,
    LearnerKnowledgeState,
    LearnerReviewCard,
    Question,
    QuestionAttribution,
    QuestionAttributionCorrection,
    QuestionAttributionEvent,
    QuestionKnowledgePoint,
    QuestionSignal,
)

logger = logging.getLogger(__name__)


# ── 题库 ────────────────────────────────────────────────────────────────


def count_questions(exclude_ai: bool = False) -> int:
    with db.session() as s:
        stmt = select(func.count()).select_from(Question)
        if exclude_ai:
            stmt = stmt.where(Question.source != "AI生成")
        return s.scalar(stmt) or 0


def upsert_questions(items: list[dict], batch_size: int = 200) -> int:
    """按 id upsert 批量写入题库，返回写入条数。"""
    n = 0
    seen: set[str] = set()
    items = [q for q in items if not (q["id"] in seen or seen.add(q["id"]))]
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        with db.session() as s:
            ids = [q["id"] for q in batch]
            existing = {
                row.id: row for row in s.scalars(select(Question).where(Question.id.in_(ids)))
            }
            for q in batch:
                row = existing.get(q["id"])
                if row is None:
                    row = Question(id=q["id"])
                    s.add(row)
                row.source = q["source"]
                row.num = q.get("num", 0)
                row.stem = q["stem"]
                row.options = json.dumps(q["options"], ensure_ascii=False)
                row.answer = q.get("answer", "")
                row.analysis = q.get("analysis", "")
                row.domain = q.get("domain")
                row.needs_review = bool(q.get("needs_review"))
                row.dup_of = q.get("dup_of")
            s.commit()
        n += len(batch)
    return n


def load_questions() -> list[dict]:
    """题库全量加载（QuestionBank 用）。表为空返回 []，调用方决定是否回退 JSON。"""
    out = []
    with db.session() as s:
        rows = s.scalars(select(Question)).all()
    for r in rows:
        out.append(
            {
                "id": r.id,
                "source": r.source,
                "num": r.num,
                "stem": r.stem,
                "options": json.loads(r.options),
                "answer": r.answer,
                "analysis": r.analysis,
                "domain": r.domain,
                "needs_review": bool(r.needs_review),
                **({"dup_of": r.dup_of} if r.dup_of else {}),
            }
        )
    return out


# ── 知识块 ──────────────────────────────────────────────────────────────


def count_chunks() -> int:
    with db.session() as s:
        return s.scalar(select(func.count()).select_from(KBChunk)) or 0


def upsert_chunks(items: list[dict], batch_size: int = 200) -> int:
    n = 0
    seen: set[str] = set()
    items = [q for q in items if not (q["id"] in seen or seen.add(q["id"]))]
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        with db.session() as s:
            ids = [c["id"] for c in batch]
            existing = set(s.scalars(select(KBChunk.id).where(KBChunk.id.in_(ids))))
            for c in batch:
                if c["id"] in existing:
                    continue
                s.add(
                    KBChunk(
                        id=c["id"],
                        domain=c["domain"],
                        source=c["source"],
                        page=c["page"],
                        kind=c["kind"],
                        text=c["text"],
                    )
                )
            s.commit()
        n += len(batch)
    return n


def load_chunks() -> list[dict]:
    out = []
    with db.session() as s:
        rows = s.scalars(select(KBChunk)).all()
    for r in rows:
        out.append(
            {
                "id": r.id,
                "domain": r.domain,
                "source": r.source,
                "page": r.page,
                "kind": r.kind,
                "text": r.text,
            }
        )
    return out


# ── 答题记录（画像统计用）────────────────────────────────────────────────


def domain_accuracy_rows(thread_id: str) -> list[tuple[str, int, int]]:
    """[(知识域, 答题数, 正确数)]。"""
    from sqlalchemy import case

    with db.session() as s:
        rows = s.execute(
            select(
                LearnerAttempt.domain,
                func.count().label("n"),
                func.sum(case((LearnerAttempt.correct.is_(True), 1), else_=0)).label("c"),
            )
            .where(
                LearnerAttempt.thread_id == thread_id,
                LearnerAttempt.domain.is_not(None),
                LearnerAttempt.attribution_status == "confirmed",
                LearnerAttempt.profile_applied.is_(True),
            )
            .group_by(LearnerAttempt.domain)
        ).all()
    return [(d, n, int(c or 0)) for d, n, c in rows]


def attempt_rows(thread_id: str) -> list[tuple[str, int, float]]:
    """[(知识域, 是否正确, 答题时间戳)]，画像聚合用（Elo 需按时间 replay）。"""
    with db.session() as s:
        rows = s.execute(
            select(LearnerAttempt.domain, LearnerAttempt.correct, LearnerAttempt.answered_at).where(
                LearnerAttempt.thread_id == thread_id,
                LearnerAttempt.domain.is_not(None),
                LearnerAttempt.attribution_status == "confirmed",
                LearnerAttempt.profile_applied.is_(True),
            )
        ).all()
    return [(d, bool(c), float(t)) for d, c, t in rows]


def last_wrong_choice(thread_id: str, question_id: str) -> str | None:
    """某题最近一次答错时用户选的选项（定向出题的干扰项依据）。"""
    with db.session() as s:
        stmt = (
            select(LearnerAttempt.choice)
            .where(
                LearnerAttempt.thread_id == thread_id,
                LearnerAttempt.question_id == question_id,
                LearnerAttempt.correct.is_(False),
            )
            .order_by(LearnerAttempt.answered_at.desc())
            .limit(1)
        )
        return s.scalar(stmt)


def recent_wrong_attempts(thread_id: str, domain: str, limit: int = 5) -> list[dict]:
    """某域最近答错的题（去重取每题最后一次），定向出题的锚点。"""
    with db.session() as s:
        rows = s.execute(
            select(
                LearnerAttempt.question_id,
                LearnerAttempt.stem,
                LearnerAttempt.choice,
                LearnerAttempt.answered_at,
            )
            .where(
                LearnerAttempt.thread_id == thread_id,
                LearnerAttempt.domain == domain,
                LearnerAttempt.correct.is_(False),
                LearnerAttempt.attribution_status == "confirmed",
                LearnerAttempt.profile_applied.is_(True),
            )
            .order_by(LearnerAttempt.answered_at.desc())
            .limit(limit * 3)
        ).all()
    seen: set[str] = set()
    out = []
    for qid, stem, choice, at in rows:
        if qid in seen:
            continue
        seen.add(qid)
        out.append({"question_id": qid, "stem": stem or "", "choice": choice, "answered_at": at})
        if len(out) >= limit:
            break
    return out


# ── 提问信号（问答侧薄弱信号）────────────────────────────────────────────


def record_question_signal(
    thread_id: str, question: str, domain: str | None, intent: str | None
) -> None:
    with db.session() as s:
        s.add(
            QuestionSignal(
                thread_id=thread_id,
                question=question[:2000],
                domain=domain,
                intent=intent,
                created_at=time.time(),
            )
        )
        s.commit()
    cache.delete_keys(f"stats:summary:{thread_id}")


def signal_rows(thread_id: str) -> list[tuple[str, int, float]]:
    """[(知识域, 提问次数, 最近提问时间戳)]，画像融合用（仅已有域标签的）。"""
    with db.session() as s:
        rows = s.execute(
            select(
                QuestionSignal.domain,
                func.count().label("n"),
                func.max(QuestionSignal.created_at).label("last"),
            )
            .where(QuestionSignal.thread_id == thread_id, QuestionSignal.domain.is_not(None))
            .group_by(QuestionSignal.domain)
        ).all()
    return [(d, int(n), float(last)) for d, n, last in rows]


# ── 诊断会话 ────────────────────────────────────────────────────────────


def create_diagnostic_session(
    session_id: str,
    thread_id: str,
    selected_domains: str,
    self_report: str,
    max_questions: int,
    now: float,
) -> None:
    with db.session() as s:
        s.add(
            DiagnosticSession(
                id=session_id,
                thread_id=thread_id,
                status="active",
                selected_domains=selected_domains,
                self_report=self_report,
                question_ids="[]",
                responses="[]",
                max_questions=max_questions,
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()


def get_diagnostic_session(session_id: str) -> dict | None:
    with db.session() as s:
        row = s.get(DiagnosticSession, session_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "thread_id": row.thread_id,
            "status": row.status,
            "selected_domains": row.selected_domains,
            "self_report": row.self_report,
            "question_ids": row.question_ids,
            "responses": row.responses,
            "max_questions": row.max_questions,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


def update_diagnostic_session(
    session_id: str,
    *,
    status: str | None = None,
    question_ids: str | None = None,
    responses: str | None = None,
    now: float | None = None,
) -> None:
    with db.session() as s:
        row = s.get(DiagnosticSession, session_id)
        if row is None:
            return
        if status is not None:
            row.status = status
        if question_ids is not None:
            row.question_ids = question_ids
        if responses is not None:
            row.responses = responses
        row.updated_at = now if now is not None else time.time()
        s.commit()


def record_diagnostic_event(
    session_id: str, event_type: str, payload: str, now: float | None = None
) -> None:
    with db.session() as s:
        s.add(
            DiagnosticEvent(
                session_id=session_id,
                event_type=event_type,
                payload=payload,
                created_at=now or time.time(),
            )
        )
        s.commit()


# ── 知识点候选与题目多标签 ──────────────────────────────────────────────


def upsert_knowledge_points(points: list[dict]) -> int:
    """导入候选知识点；重复运行只更新计数/父级，不覆盖人工状态。"""
    written = 0
    with db.session() as s:
        for point in points:
            row = s.get(KnowledgePoint, point["id"])
            if row is None:
                row = KnowledgePoint(
                    id=point["id"],
                    domain=point["domain"],
                    name=point["name"],
                    parent_name=point.get("parent_name"),
                    description=point.get("description"),
                    evidence_count=int(point.get("evidence_count", 0)),
                    question_count=int(point.get("question_count", 0)),
                    status=point.get("status", "candidate"),
                )
                s.add(row)
            else:
                row.evidence_count = int(point.get("evidence_count", row.evidence_count))
                row.question_count = int(point.get("question_count", row.question_count))
                # candidate 重建不能覆盖人工状态；带审校结论的导入则以审校为准。
                if point.get("status") in {"active", "rejected"}:
                    row.name = point["name"]
                    row.parent_name = point.get("parent_name")
                    row.description = point.get("description")
                    row.status = point["status"]
                elif row.parent_name is None:
                    row.parent_name = point.get("parent_name")
            written += 1
        s.commit()
    return written


def replace_question_knowledge_points(
    tags: list[dict], question_ids: list[str] | None = None
) -> int:
    """按题目替换启发式标签；重跑时清理失配项，但保留人工标签。"""
    grouped: dict[str, list[dict]] = {}
    for tag in tags:
        grouped.setdefault(tag["question_id"], []).append(tag)
    target_ids = set(question_ids or grouped)
    written = 0
    with db.session() as s:
        for question_id in target_ids:
            s.query(QuestionKnowledgePoint).filter(
                QuestionKnowledgePoint.question_id == question_id,
                QuestionKnowledgePoint.tag_source == "heuristic",
            ).delete(synchronize_session=False)
            rows = grouped.get(question_id, [])
            for tag in rows:
                key = (question_id, tag["knowledge_point_id"])
                existing = s.get(QuestionKnowledgePoint, key)
                source = tag.get("tag_source", "heuristic")
                if existing is None:
                    s.add(
                        QuestionKnowledgePoint(
                            question_id=question_id,
                            knowledge_point_id=tag["knowledge_point_id"],
                            confidence=float(tag.get("confidence", 0.0)),
                            is_primary=bool(tag.get("is_primary", False)),
                            tag_source=source,
                        )
                    )
                elif source != "heuristic":
                    # 人工审校结果可幂等重放；启发式结果不能降级已存在的人工标签。
                    existing.confidence = float(tag.get("confidence", 0.0))
                    existing.is_primary = bool(tag.get("is_primary", False))
                    existing.tag_source = source
                written += 1
        s.commit()
    return written


def question_knowledge_points(question_id: str) -> list[dict]:
    with db.session() as s:
        rows = s.scalars(
            select(QuestionKnowledgePoint)
            .outerjoin(
                KnowledgePoint, KnowledgePoint.id == QuestionKnowledgePoint.knowledge_point_id
            )
            .where(
                QuestionKnowledgePoint.question_id == question_id,
                or_(
                    QuestionKnowledgePoint.tag_source != "heuristic",
                    KnowledgePoint.status == "active",
                ),
            )
        ).all()
    return [
        {
            "question_id": row.question_id,
            "knowledge_point_id": row.knowledge_point_id,
            "confidence": row.confidence,
            "is_primary": row.is_primary,
            "tag_source": row.tag_source,
        }
        for row in rows
    ]


def primary_question_domain(question_id: str) -> str | None:
    """返回可用主知识点的审校域；人工标签优先，其次按标签置信度。"""
    with db.session() as s:
        stmt = (
            select(KnowledgePoint.domain)
            .join(
                QuestionKnowledgePoint,
                KnowledgePoint.id == QuestionKnowledgePoint.knowledge_point_id,
            )
            .where(
                QuestionKnowledgePoint.question_id == question_id,
                QuestionKnowledgePoint.is_primary.is_(True),
                or_(
                    QuestionKnowledgePoint.tag_source != "heuristic",
                    KnowledgePoint.status == "active",
                ),
            )
            .order_by(
                (QuestionKnowledgePoint.tag_source != "heuristic").desc(),
                QuestionKnowledgePoint.confidence.desc(),
            )
            .limit(1)
        )
        return s.scalar(stmt)


def question_knowledge_points_bulk(question_ids: list[str]) -> dict[str, list[dict]]:
    if not question_ids:
        return {}
    with db.session() as s:
        rows = s.scalars(
            select(QuestionKnowledgePoint)
            .outerjoin(
                KnowledgePoint, KnowledgePoint.id == QuestionKnowledgePoint.knowledge_point_id
            )
            .where(
                QuestionKnowledgePoint.question_id.in_(question_ids),
                or_(
                    QuestionKnowledgePoint.tag_source != "heuristic",
                    KnowledgePoint.status == "active",
                ),
            )
        ).all()
    result: dict[str, list[dict]] = {}
    for row in rows:
        result.setdefault(row.question_id, []).append(
            {
                "knowledge_point_id": row.knowledge_point_id,
                "confidence": row.confidence,
                "is_primary": row.is_primary,
                "tag_source": row.tag_source,
            }
        )
    return result


def load_knowledge_points(point_ids: list[str] | None = None) -> list[dict]:
    with db.session() as s:
        stmt = select(KnowledgePoint)
        if point_ids:
            stmt = stmt.where(KnowledgePoint.id.in_(point_ids))
        rows = s.scalars(stmt).all()
    return [
        {
            "id": row.id,
            "domain": row.domain,
            "name": row.name,
            "parent_name": row.parent_name,
            "status": row.status,
            "evidence_count": row.evidence_count,
            "question_count": row.question_count,
        }
        for row in rows
    ]


# ── 保守题目归因（与用户掌握度严格分离）──────────────────────────────────


def upsert_question_attribution(result: dict, now: float | None = None) -> None:
    """写入当前归因快照并追加不可变审计事件。"""
    question_id = str(result["question_id"])
    candidate = result.get("candidate") or {}
    status = result.get("attribution_status") or result.get("status") or "unverified"
    now = now if now is not None else time.time()
    evidence = result.get("evidence") or {
        "dense": result.get("dense", {}),
        "tfidf": result.get("tfidf", {}),
        "exact": result.get("exact", {}),
    }
    payload = dict(result)
    with db.session() as s:
        row = s.get(QuestionAttribution, question_id)
        if row is None:
            row = QuestionAttribution(
                question_id=question_id,
                created_at=now,
                updated_at=now,
                candidate_point_id=None,
                candidate_domain=None,
                attribution_status="unverified",
                agreement_count=0,
                evidence_json="{}",
                reason="",
                classifier_version="",
            )
            s.add(row)
        row.candidate_point_id = candidate.get("knowledge_point_id") or result.get(
            "candidate_point_id"
        )
        row.candidate_domain = candidate.get("domain") or result.get("candidate_domain")
        row.attribution_status = status
        row.agreement_count = int(result.get("agreement_count", 0))
        row.evidence_json = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        row.reason = str(result.get("reason", ""))[:4000]
        row.classifier_version = str(result.get("classifier_version", ""))[:64]
        row.updated_at = now
        s.flush()
        s.add(
            QuestionAttributionEvent(
                question_id=question_id,
                attribution_status=status,
                payload=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                classifier_version=row.classifier_version,
                created_at=now,
            )
        )
        s.commit()


def question_attribution(question_id: str) -> dict | None:
    """返回归因快照；旧库/尚未导入快照时用已有标签做保守兼容。"""
    with db.session() as s:
        row = s.get(QuestionAttribution, question_id)
    if row is not None:
        evidence = _loads_json(row.evidence_json, {})
        candidate = None
        if row.candidate_point_id:
            points = load_knowledge_points([row.candidate_point_id])
            point = points[0] if points else {}
            candidate = {
                "knowledge_point_id": row.candidate_point_id,
                "point_id": row.candidate_point_id,
                "domain": row.candidate_domain or point.get("domain"),
                "name": point.get("name", row.candidate_point_id),
            }
        return {
            "question_id": row.question_id,
            "candidate": candidate,
            "candidate_point_id": row.candidate_point_id,
            "candidate_domain": row.candidate_domain,
            "agreement_count": row.agreement_count,
            "attribution_status": row.attribution_status,
            "status": row.attribution_status,
            "reason": row.reason,
            "classifier_version": row.classifier_version,
            "evidence": evidence,
            "updated_at": row.updated_at,
        }
    return _legacy_tag_attribution(question_id)


def _loads_json(value: str | None, default):
    try:
        parsed = json.loads(value or "")
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, ValueError):
        return default


def _legacy_tag_attribution(question_id: str) -> dict | None:
    """仅兼容旧数据：人工/测试标签可确认，启发式标签只能 probable。"""
    with db.session() as s:
        rows = s.execute(
            select(QuestionKnowledgePoint, KnowledgePoint)
            .join(KnowledgePoint, KnowledgePoint.id == QuestionKnowledgePoint.knowledge_point_id)
            .where(
                QuestionKnowledgePoint.question_id == question_id,
                QuestionKnowledgePoint.is_primary.is_(True),
                or_(
                    QuestionKnowledgePoint.tag_source != "heuristic",
                    KnowledgePoint.status == "active",
                ),
            )
        ).all()
    if not rows:
        return None
    # 多个主标签不是可安全压成一个候选的情况。
    if len(rows) != 1:
        return {
            "question_id": question_id,
            "candidate": None,
            "candidate_point_id": None,
            "candidate_domain": None,
            "agreement_count": 0,
            "attribution_status": "unverified",
            "status": "unverified",
            "reason": "旧标签存在多个主知识点",
            "classifier_version": "legacy-tag-compat",
            "evidence": {"legacy_tags": []},
        }
    tag, point = rows[0]
    candidate = {
        "knowledge_point_id": point.id,
        "point_id": point.id,
        "domain": point.domain,
        "name": point.name,
    }
    status = "confirmed" if tag.tag_source != "heuristic" else "probable"
    return {
        "question_id": question_id,
        "candidate": candidate,
        "candidate_point_id": point.id,
        "candidate_domain": point.domain,
        "agreement_count": 1,
        "attribution_status": status,
        "status": status,
        "reason": "兼容已有人工/测试标签"
        if status == "confirmed"
        else "兼容已有启发式标签，待校准",
        "classifier_version": "legacy-tag-compat",
        "evidence": {
            "legacy_tag": {
                "knowledge_point_id": point.id,
                "tag_source": tag.tag_source,
                "confidence": tag.confidence,
            }
        },
    }


def question_attribution_events(question_id: str, limit: int = 50) -> list[dict]:
    with db.session() as s:
        rows = s.scalars(
            select(QuestionAttributionEvent)
            .where(QuestionAttributionEvent.question_id == question_id)
            .order_by(QuestionAttributionEvent.created_at.desc())
            .limit(limit)
        ).all()
    return [
        {
            "question_id": row.question_id,
            "attribution_status": row.attribution_status,
            "payload": _loads_json(row.payload, {}),
            "classifier_version": row.classifier_version,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def _question_attribution_dict(row: QuestionAttribution) -> dict:
    evidence = _loads_json(row.evidence_json, {})
    routes = evidence.get("routes", {}) if isinstance(evidence, dict) else {}
    evidence_count = sum(
        bool(route.get("available") and route.get("candidates"))
        for route in routes.values()
        if isinstance(route, dict)
    )
    return {
        "question_id": row.question_id,
        "candidate_point_id": row.candidate_point_id,
        "candidate_domain": row.candidate_domain,
        "attribution_status": row.attribution_status,
        "status": row.attribution_status,
        "agreement_count": row.agreement_count,
        "evidence_count": evidence_count,
        "recent_evidence": {
            name: (route.get("candidates") or [])[:1]
            for name, route in routes.items()
            if isinstance(route, dict)
        },
        "reason": row.reason,
        "classifier_version": row.classifier_version,
        "updated_at": row.updated_at,
    }


def question_attribution_rows(
    limit: int = 500,
    status: str | None = None,
) -> list[dict]:
    """题目归因概览，先按状态筛选再应用数量上限。"""
    with db.session() as s:
        stmt = select(QuestionAttribution)
        if status is not None:
            stmt = stmt.where(QuestionAttribution.attribution_status == status)
        rows = s.scalars(stmt.order_by(QuestionAttribution.updated_at.desc()).limit(limit)).all()
    return [_question_attribution_dict(row) for row in rows]


class AttributionCorrectionConflict(ValueError):
    pass


def _correction_dict(row: QuestionAttributionCorrection) -> dict:
    return {
        "id": row.id,
        "thread_id": row.thread_id,
        "question_id": row.question_id,
        "corrected_point_id": row.corrected_point_id,
        "corrected_domain": row.corrected_domain,
        "previous_point_id": row.previous_point_id,
        "previous_status": row.previous_status,
        "reason": row.reason,
        "source": "user_correction",
        "replayed_attempts": row.replayed_attempts,
        "retracted_calibrations": row.retracted_calibrations,
        "replacement_calibration_id": row.replacement_calibration_id,
        "created_at": row.created_at,
    }


def latest_attribution_correction(thread_id: str, question_id: str) -> dict | None:
    with db.session() as s:
        row = s.scalars(
            select(QuestionAttributionCorrection)
            .where(
                QuestionAttributionCorrection.thread_id == thread_id,
                QuestionAttributionCorrection.question_id == question_id,
            )
            .order_by(QuestionAttributionCorrection.id.desc())
            .limit(1)
        ).first()
    return _correction_dict(row) if row else None


def attribution_corrections(
    thread_id: str,
    limit: int = 100,
    before_id: int | None = None,
) -> list[dict]:
    statement = select(QuestionAttributionCorrection).where(
        QuestionAttributionCorrection.thread_id == thread_id
    )
    if before_id is not None:
        statement = statement.where(QuestionAttributionCorrection.id < before_id)
    with db.session() as s:
        rows = s.scalars(
            statement.order_by(QuestionAttributionCorrection.id.desc()).limit(limit)
        ).all()
    return [_correction_dict(row) for row in rows]


def latest_attribution_corrections(
    thread_id: str,
    question_ids: list[str] | None = None,
) -> list[dict]:
    """Return the latest correction per question, optionally for one result page."""
    if question_ids is not None and not question_ids:
        return []
    filters = [QuestionAttributionCorrection.thread_id == thread_id]
    if question_ids is not None:
        filters.append(QuestionAttributionCorrection.question_id.in_(question_ids))
    latest_ids = (
        select(func.max(QuestionAttributionCorrection.id).label("id"))
        .where(*filters)
        .group_by(QuestionAttributionCorrection.question_id)
        .subquery()
    )
    with db.session() as s:
        rows = s.scalars(
            select(QuestionAttributionCorrection)
            .join(latest_ids, QuestionAttributionCorrection.id == latest_ids.c.id)
            .order_by(QuestionAttributionCorrection.id.desc())
        ).all()
    return [_correction_dict(row) for row in rows]


def apply_thread_attribution_corrections(rows: list[dict], thread_id: str) -> list[dict]:
    corrections = {
        row["question_id"]: row
        for row in latest_attribution_corrections(
            thread_id, [item["question_id"] for item in rows],
        )
    }
    for row in rows:
        correction = corrections.get(row["question_id"])
        if correction is not None:
            row.update({
                "candidate_point_id": correction["corrected_point_id"],
                "candidate_domain": correction["corrected_domain"],
                "attribution_status": "confirmed",
                "status": "confirmed",
                "reason": correction["reason"] or "用户已纠正",
                "source": "user_correction",
                "correction": correction,
            })
    return rows


def attribution_review_page(
    thread_id: str,
    *,
    limit: int = 30,
    after_question_id: str | None = None,
    question_id_query: str | None = None,
) -> dict:
    """Read a stable, question-id ordered page with thread-local corrections applied."""
    filters = []
    if question_id_query:
        filters.append(QuestionAttribution.question_id.contains(question_id_query, autoescape=True))
    if after_question_id:
        filters.append(QuestionAttribution.question_id > after_question_id)
    base = select(QuestionAttribution)
    count_query = select(func.count()).select_from(QuestionAttribution)
    if question_id_query:
        query_filter = QuestionAttribution.question_id.contains(
            question_id_query, autoescape=True,
        )
        count_query = count_query.where(query_filter)
    with db.session() as s:
        total = int(s.scalar(count_query) or 0)
        rows = s.scalars(
            base.where(*filters)
            .order_by(QuestionAttribution.question_id.asc())
            .limit(limit + 1)
        ).all()
    has_more = len(rows) > limit
    items = [_question_attribution_dict(row) for row in rows[:limit]]
    items = apply_thread_attribution_corrections(items, thread_id)
    if items:
        point_ids = {
            item["candidate_point_id"] for item in items if item["candidate_point_id"]
        }
        point_names = {
            point["id"]: point["name"]
            for point in load_knowledge_points(list(point_ids))
        } if point_ids else {}
        for item in items:
            item["candidate_point_name"] = point_names.get(item["candidate_point_id"])
    return {
        "items": items,
        "total": total,
        "has_more": has_more,
        "next_cursor": items[-1]["question_id"] if has_more and items else None,
    }


def attribution_correction_channel_counts(thread_id: str) -> dict[str, int]:
    with db.session() as s:
        total = int(
            s.scalar(
                select(func.count())
                .select_from(QuestionAttributionCorrection)
                .where(QuestionAttributionCorrection.thread_id == thread_id)
            )
            or 0
        )
        replays = int(
            s.scalar(
                select(func.count())
                .select_from(QuestionAttributionCorrection)
                .where(
                    QuestionAttributionCorrection.thread_id == thread_id,
                    QuestionAttributionCorrection.replayed_attempts > 0,
                )
            )
            or 0
        )
        replacements = int(
            s.scalar(
                select(func.count())
                .select_from(QuestionAttributionCorrection)
                .where(
                    QuestionAttributionCorrection.thread_id == thread_id,
                    QuestionAttributionCorrection.replacement_calibration_id.is_not(None),
                )
            )
            or 0
        )
        retractions = int(
            s.scalar(
                select(func.sum(QuestionAttributionCorrection.retracted_calibrations)).where(
                    QuestionAttributionCorrection.thread_id == thread_id,
                )
            )
            or 0
        )
    return {
        "events": total,
        "profile_replays": replays,
        "calibration_replacements": replacements,
        "calibrations_retracted": retractions,
    }


def _calibration_ledger_integrity(session, thread_id: str) -> dict:
    """Current thread ledger shape only; counts are operational, not accuracy."""
    calibrations = session.execute(
        select(
            CalibrationHypothesis.id,
            CalibrationHypothesis.status,
            CalibrationHypothesis.evidence_json,
        ).where(CalibrationHypothesis.thread_id == thread_id)
    ).all()
    attempt_rows = session.execute(
        select(
            LearnerAttempt.id,
            LearnerAttempt.calibration_id,
            LearnerAttempt.question_id,
            LearnerAttempt.correct,
        ).where(
            LearnerAttempt.thread_id == thread_id,
            LearnerAttempt.calibration_id.is_not(None),
            LearnerAttempt.profile_applied.is_(True),
        )
    ).all()

    status_by_id = {calibration_id: status for calibration_id, status, _ in calibrations}
    expected_by_id = {}
    for calibration_id, _, evidence_json in calibrations:
        expected = {}
        for item in _loads_json(evidence_json, []):
            question_id = item.get("question_id")
            correct = item.get("correct")
            if question_id and correct is not None:
                expected[str(question_id)] = {
                    "correct": correct,
                    "attempt_id": item.get("attempt_id"),
                }
        expected_by_id[calibration_id] = expected

    applied_by_calibration_question: dict[tuple[int, str], list[tuple[int, bool]]] = {}
    orphan_applied = 0
    for attempt_id, calibration_id, question_id, correct in attempt_rows:
        if calibration_id not in status_by_id:
            orphan_applied += 1
            continue
        key = (int(calibration_id), str(question_id))
        applied_by_calibration_question.setdefault(key, []).append((int(attempt_id), correct))

    missing = 0
    duplicates = 0
    unexpected = 0
    mismatched = 0
    nonconfirmed = 0
    for calibration_id, status in status_by_id.items():
        expected = expected_by_id[calibration_id]
        applied = {
            question_id: attempts
            for (attempt_calibration_id, question_id), attempts
            in applied_by_calibration_question.items()
            if attempt_calibration_id == calibration_id
        }
        if status == "confirmed":
            missing += sum(question_id not in applied for question_id in expected)
            duplicates += sum(max(len(attempts) - 1, 0) for attempts in applied.values())
            unexpected += sum(
                len(attempts) for question_id, attempts in applied.items()
                if question_id not in expected
            )
            for question_id, attempts in applied.items():
                expected_evidence = expected.get(question_id)
                if expected_evidence is None:
                    continue
                expected_attempt_id = expected_evidence["attempt_id"]
                for attempt_id, correct in attempts:
                    if (
                        (expected_attempt_id is not None and attempt_id != expected_attempt_id)
                        or correct is not expected_evidence["correct"]
                    ):
                        mismatched += 1
        else:
            nonconfirmed += sum(len(attempts) for attempts in applied.values())

    anomaly_total = missing + duplicates + unexpected + mismatched + nonconfirmed + orphan_applied
    return {
        "snapshot_scope": "current_thread_not_event_window",
        "confirmed_calibrations": sum(status == "confirmed" for status in status_by_id.values()),
        "confirmed_missing_applied_evidence": missing,
        "confirmed_duplicate_applied_evidence": duplicates,
        "confirmed_unexpected_applied_evidence": unexpected,
        "confirmed_mismatched_applied_attempts": mismatched,
        "nonconfirmed_applied_attempts": nonconfirmed,
        "orphan_applied_attempts": orphan_applied,
        "anomalies_total": anomaly_total,
        "status": "attention" if anomaly_total else "ok",
    }

def attribution_correction_observability(
    thread_id: str,
    since: float | None = None,
) -> dict:
    """Thread-scoped operational feedback aggregates; never an accuracy metric."""
    filters = [QuestionAttributionCorrection.thread_id == thread_id]
    if since is not None:
        filters.append(QuestionAttributionCorrection.created_at >= since)

    event_id = QuestionAttributionCorrection.id
    question_id = QuestionAttributionCorrection.question_id
    reason = QuestionAttributionCorrection.reason
    replayed = QuestionAttributionCorrection.replayed_attempts
    replacement_id = QuestionAttributionCorrection.replacement_calibration_id
    retracted = QuestionAttributionCorrection.retracted_calibrations

    with db.session() as s:
        totals = s.execute(
            select(
                func.count(event_id),
                func.count(func.distinct(question_id)),
                func.max(event_id),
                func.sum(case((func.length(func.trim(reason)) > 0, 1), else_=0)),
                func.sum(case((replayed > 0, 1), else_=0)),
                func.sum(case((replacement_id.is_not(None), 1), else_=0)),
                func.sum(retracted),
            ).where(*filters)
        ).one()
        domain_rows = s.execute(
            select(
                QuestionAttributionCorrection.corrected_domain,
                func.count(event_id),
            )
            .where(*filters)
            .group_by(QuestionAttributionCorrection.corrected_domain)
            .order_by(QuestionAttributionCorrection.corrected_domain)
        ).all()
        status_rows = s.execute(
            select(
                QuestionAttributionCorrection.previous_status,
                func.count(event_id),
            )
            .where(*filters)
            .group_by(QuestionAttributionCorrection.previous_status)
            .order_by(QuestionAttributionCorrection.previous_status)
        ).all()
        ledger_integrity = _calibration_ledger_integrity(s, thread_id)

    events = int(totals[0] or 0)
    unique_questions = int(totals[1] or 0)
    return {
        "metric_semantics": "operational_feedback_not_accuracy",
        "not_accuracy_metric": True,
        "window": {
            "since": since,
            "latest_event_id": int(totals[2]) if totals[2] is not None else None,
        },
        "events": events,
        "unique_questions": unique_questions,
        "repeat_events": max(events - unique_questions, 0),
        "reason_provided_events": int(totals[3] or 0),
        "profile_replays": int(totals[4] or 0),
        "calibration_replacements": int(totals[5] or 0),
        "calibrations_retracted": int(totals[6] or 0),
        "by_target_domain": {str(domain): int(count) for domain, count in domain_rows},
        "by_previous_status": {str(status): int(count) for status, count in status_rows},
        "ledger_integrity": ledger_integrity,
    }


def effective_question_attribution(thread_id: str, question_id: str) -> dict | None:
    base = question_attribution(question_id)
    correction = latest_attribution_correction(thread_id, question_id)
    if correction is None:
        return base
    point = load_knowledge_points([correction["corrected_point_id"]])
    point = point[0] if point else {}
    return {
        "question_id": question_id,
        "candidate": {
            "knowledge_point_id": correction["corrected_point_id"],
            "point_id": correction["corrected_point_id"],
            "domain": correction["corrected_domain"],
            "name": point.get("name", correction["corrected_point_id"]),
        },
        "candidate_point_id": correction["corrected_point_id"],
        "candidate_domain": correction["corrected_domain"],
        "agreement_count": 1,
        "attribution_status": "confirmed",
        "status": "confirmed",
        "reason": correction["reason"] or "Explicit user correction",
        "classifier_version": "user-correction-v1",
        "evidence": {"user_correction": correction},
        "updated_at": correction["created_at"],
        "source": "user_correction",
    }


def _reassign_applied_attempts(
    session,
    attempts: list[LearnerAttempt],
    point: KnowledgePoint,
) -> int:
    moved = [row for row in attempts if row.attribution_point_id != point.id]
    contributions: dict[str, dict[str, int]] = {}
    for attempt in moved:
        counts = contributions.setdefault(
            str(attempt.attribution_point_id), {"attempts": 0, "correct": 0}
        )
        counts["attempts"] += 1
        counts["correct"] += int(bool(attempt.correct))

    old_states = {}
    for point_id, counts in contributions.items():
        state = session.get(LearnerKnowledgeState, (attempts[0].thread_id, point_id))
        if (
            state is None
            or state.attempts < counts["attempts"]
            or state.correct < counts["correct"]
        ):
            raise AttributionCorrectionConflict(
                "Applied evidence is inconsistent with the learner state; replay refused"
            )
        old_states[point_id] = state

    if moved:
        new_state = session.get(LearnerKnowledgeState, (attempts[0].thread_id, point.id))
        if new_state is None:
            new_state = LearnerKnowledgeState(
                thread_id=attempts[0].thread_id,
                knowledge_point_id=point.id,
                beta_a=1.0,
                beta_b=1.0,
                attempts=0,
                correct=0,
                last_seen=max(row.answered_at for row in moved),
            )
            session.add(new_state)
        for attempt in moved:
            old_state = old_states[str(attempt.attribution_point_id)]
            old_state.beta_a -= 1.0 if attempt.correct else 0.0
            old_state.beta_b -= 0.0 if attempt.correct else 1.0
            old_state.attempts -= 1
            old_state.correct -= 1 if attempt.correct else 0
            new_state.beta_a += 1.0 if attempt.correct else 0.0
            new_state.beta_b += 0.0 if attempt.correct else 1.0
            new_state.attempts += 1
            new_state.correct += 1 if attempt.correct else 0
            new_state.last_seen = max(new_state.last_seen, attempt.answered_at)
            attempt.attribution_point_id = point.id

    for attempt in attempts:
        attempt.domain = point.domain
    session.flush()

    for point_id, state in old_states.items():
        if state.attempts == 0:
            session.delete(state)
            continue
        last_seen = session.scalar(
            select(func.max(LearnerAttempt.answered_at)).where(
                LearnerAttempt.thread_id == attempts[0].thread_id,
                LearnerAttempt.attribution_point_id == point_id,
                LearnerAttempt.profile_applied.is_(True),
            )
        )
        if last_seen is None:
            raise AttributionCorrectionConflict(
                "Applied evidence cannot be replayed without a complete attempt ledger"
            )
        state.last_seen = float(last_seen)

    card = session.get(LearnerReviewCard, (attempts[0].thread_id, attempts[0].question_id))
    if card is not None:
        card.domain = point.domain
    return len(moved)


def _begin_serialized_write(session) -> None:
    if db.backend() == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def _lock_calibration_point(session, point_id: str) -> KnowledgePoint:
    """Serialize pending creation on SQLite and MySQL using one point anchor."""
    _begin_serialized_write(session)
    point = session.scalars(
        select(KnowledgePoint).where(KnowledgePoint.id == point_id).with_for_update()
    ).first()
    if point is None:
        raise ValueError("knowledge_point_id must reference an existing knowledge point")
    return point


def record_attribution_correction(
    thread_id: str,
    question_id: str,
    corrected_point_id: str,
    reason: str = "",
    now: float | None = None,
) -> dict:
    now = now if now is not None else time.time()
    with db.session() as s:
        point = _lock_calibration_point(s, corrected_point_id)
        if point.status != "active":
            raise ValueError("corrected_point_id must reference an active knowledge point")

        # Serialize corrections for the same source question. This makes the latest
        # correction and previous-point ledger authoritative under concurrent writes.
        base_row = s.scalars(
            select(QuestionAttribution)
            .where(QuestionAttribution.question_id == question_id)
            .with_for_update()
        ).first()
        latest_correction = s.scalars(
            select(QuestionAttributionCorrection)
            .where(
                QuestionAttributionCorrection.thread_id == thread_id,
                QuestionAttributionCorrection.question_id == question_id,
            )
            .order_by(QuestionAttributionCorrection.id.desc())
            .limit(1)
            .with_for_update()
        ).first()
        previous_point_id = (
            latest_correction.corrected_point_id
            if latest_correction is not None
            else base_row.candidate_point_id
            if base_row is not None
            else None
        )
        previous_status = (
            "confirmed"
            if latest_correction is not None
            else base_row.attribution_status
            if base_row is not None
            else "unverified"
        )
        if previous_point_id == corrected_point_id:
            result = (
                _correction_dict(latest_correction)
                if latest_correction is not None
                else {
                    "id": None,
                    "thread_id": thread_id,
                    "question_id": question_id,
                    "corrected_point_id": corrected_point_id,
                    "corrected_domain": point.domain,
                    "previous_point_id": previous_point_id,
                    "previous_status": previous_status,
                    "reason": reason[:2000],
                    "source": "no_change",
                    "created_at": now,
                }
            )
            result.update(
                {
                    "deduplicated": True,
                    "replayed_attempts": 0,
                    "retracted_calibrations": 0,
                    "replacement_calibration_id": None,
                }
            )
            return result
        applied_attempts = s.scalars(
            select(LearnerAttempt)
            .where(
                LearnerAttempt.thread_id == thread_id,
                LearnerAttempt.question_id == question_id,
                LearnerAttempt.profile_applied.is_(True),
            )
            .order_by(LearnerAttempt.answered_at.asc())
            .with_for_update()
        ).all()
        if any(row.attribution_point_id is None for row in applied_attempts):
            raise AttributionCorrectionConflict(
                "Legacy applied evidence has no exact knowledge-point ledger"
            )

        row = QuestionAttributionCorrection(
            thread_id=thread_id,
            question_id=question_id,
            corrected_point_id=corrected_point_id,
            corrected_domain=point.domain,
            previous_point_id=previous_point_id,
            previous_status=previous_status,
            reason=reason[:2000],
            created_at=now,
        )
        s.add(row)
        pending = s.scalars(
            select(CalibrationHypothesis)
            .where(
                CalibrationHypothesis.thread_id == thread_id,
                CalibrationHypothesis.trigger_question_id == question_id,
                CalibrationHypothesis.status == "pending",
            )
            .with_for_update()
        ).all()
        for hypothesis in pending:
            hypothesis.status = "retracted"
            hypothesis.updated_at = now

        replayed_attempts = 0
        if applied_attempts:
            replayed_attempts = _reassign_applied_attempts(s, applied_attempts, point)

        latest_attempt = s.scalars(
            select(LearnerAttempt)
            .where(
                LearnerAttempt.thread_id == thread_id,
                LearnerAttempt.question_id == question_id,
            )
            .order_by(LearnerAttempt.answered_at.desc())
            .limit(1)
        ).first()
        replacement = None
        if not applied_attempts and latest_attempt is not None and not latest_attempt.correct:
            replacement = s.scalars(
                select(CalibrationHypothesis)
                .where(
                    CalibrationHypothesis.thread_id == thread_id,
                    CalibrationHypothesis.knowledge_point_id == corrected_point_id,
                    CalibrationHypothesis.status == "pending",
                )
                .limit(1)
                .with_for_update()
            ).first()
            if replacement is None:
                replacement = CalibrationHypothesis(
                    thread_id=thread_id,
                    knowledge_point_id=corrected_point_id,
                    trigger_question_id=question_id,
                    trigger_correct=False,
                    status="pending",
                    evidence_json="[]",
                    calibration_reason="User-corrected attribution requires calibration",
                    presentation_count=0,
                    last_presented_at=None,
                    created_at=now,
                    updated_at=now,
                )
                s.add(replacement)
        s.flush()
        row.replayed_attempts = replayed_attempts
        row.retracted_calibrations = len(pending)
        row.replacement_calibration_id = replacement.id if replacement is not None else None
        s.commit()
        s.refresh(row)
        result = _correction_dict(row)
        result["deduplicated"] = False
    cache.delete_keys(f"stats:summary:{thread_id}")
    return result


def confirmed_question_ids_for_point(point_id: str) -> set[str]:
    """校准题来源：只返回当前 confirmed 归因的题。"""
    with db.session() as s:
        ids = s.scalars(
            select(QuestionAttribution.question_id).where(
                QuestionAttribution.candidate_point_id == point_id,
                QuestionAttribution.attribution_status == "confirmed",
            )
        ).all()
        manual_ids = s.scalars(
            select(QuestionKnowledgePoint.question_id)
            .join(KnowledgePoint, KnowledgePoint.id == QuestionKnowledgePoint.knowledge_point_id)
            .where(
                QuestionKnowledgePoint.knowledge_point_id == point_id,
                QuestionKnowledgePoint.is_primary.is_(True),
                QuestionKnowledgePoint.tag_source != "heuristic",
                KnowledgePoint.status == "active",
            )
        ).all()
    return set(ids) | set(manual_ids)


# ── pending calibration queue ─────────────────────────────────────────────


def _calibration_dict(row: CalibrationHypothesis) -> dict:
    raw_evidence = _loads_json(row.evidence_json, [])
    evidence = [
        {key: value for key, value in item.items() if key != "attempt_id"} for item in raw_evidence
    ]
    answered = [item for item in evidence if item.get("correct") is not None]
    outcomes = [bool(item["correct"]) for item in answered]
    point = load_knowledge_points([row.knowledge_point_id])
    point = point[0] if point else {}
    return {
        "id": row.id,
        "thread_id": row.thread_id,
        "knowledge_point_id": row.knowledge_point_id,
        "candidate_domain": point.get("domain"),
        "candidate_name": point.get("name", row.knowledge_point_id),
        "trigger_question_id": row.trigger_question_id,
        "trigger_correct": bool(row.trigger_correct),
        "status": row.status,
        "evidence": evidence,
        "answered_evidence": answered,
        "evidence_count": len(answered),
        "consistent_evidence_count": max(
            (outcomes.count(value) for value in (True, False)), default=0
        ),
        "calibration_reason": row.calibration_reason,
        "presentation_count": row.presentation_count,
        "last_presented_at": row.last_presented_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def create_calibration_hypothesis(
    thread_id: str,
    knowledge_point_id: str,
    trigger_question_id: str,
    trigger_correct: bool,
    calibration_reason: str,
    now: float | None = None,
) -> dict:
    now = now if now is not None else time.time()
    with db.session() as s:
        _lock_calibration_point(s, knowledge_point_id)
        existing = s.scalars(
            select(CalibrationHypothesis)
            .where(
                CalibrationHypothesis.thread_id == thread_id,
                CalibrationHypothesis.knowledge_point_id == knowledge_point_id,
                CalibrationHypothesis.status == "pending",
            )
            .order_by(CalibrationHypothesis.created_at.desc())
        ).first()
        if existing is not None:
            return _calibration_dict(existing)
        row = CalibrationHypothesis(
            thread_id=thread_id,
            knowledge_point_id=knowledge_point_id,
            trigger_question_id=trigger_question_id,
            trigger_correct=trigger_correct,
            status="pending",
            evidence_json="[]",
            calibration_reason=calibration_reason[:2000],
            presentation_count=0,
            last_presented_at=None,
            created_at=now,
            updated_at=now,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return _calibration_dict(row)


def calibration_status_counts(thread_id: str) -> dict[str, int]:
    counts = {"pending": 0, "confirmed": 0, "retracted": 0, "inconclusive": 0}
    with db.session() as s:
        rows = s.execute(
            select(CalibrationHypothesis.status, func.count())
            .where(CalibrationHypothesis.thread_id == thread_id)
            .group_by(CalibrationHypothesis.status)
        ).all()
    for status, count in rows:
        if status in counts:
            counts[status] = int(count)
    return counts


def _lock_calibration_hypothesis(session, hypothesis_id: int):
    _begin_serialized_write(session)
    return session.scalars(
        select(CalibrationHypothesis)
        .where(CalibrationHypothesis.id == hypothesis_id)
        .with_for_update()
    ).first()


def pending_calibrations(thread_id: str, limit: int = 20) -> list[dict]:
    with db.session() as s:
        rows = s.scalars(
            select(CalibrationHypothesis)
            .where(
                CalibrationHypothesis.thread_id == thread_id,
                CalibrationHypothesis.status == "pending",
            )
            .order_by(CalibrationHypothesis.created_at.asc())
            .limit(limit)
        ).all()
    return [_calibration_dict(row) for row in rows]


def calibration_for_question(thread_id: str, question_id: str) -> dict | None:
    for row in pending_calibrations(thread_id, limit=100):
        if any(item.get("question_id") == question_id for item in row["evidence"]):
            return row
    return None


def mark_calibration_presented(
    hypothesis_id: int,
    question_id: str,
    now: float | None = None,
) -> dict | None:
    now = now if now is not None else time.time()
    with db.session() as s:
        row = _lock_calibration_hypothesis(s, hypothesis_id)
        if row is None or row.status != "pending":
            return _calibration_dict(row) if row else None
        evidence = _loads_json(row.evidence_json, [])
        outstanding = next(
            (item.get("question_id") for item in evidence if item.get("correct") is None),
            None,
        )
        assigned_question_id = outstanding or question_id
        if outstanding is None:
            evidence.append(
                {
                    "question_id": question_id,
                    "correct": None,
                    "presented_at": now,
                }
            )
            row.evidence_json = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
            row.presentation_count += 1
            row.last_presented_at = now
            row.updated_at = now
            s.commit()
        result = _calibration_dict(row)
        result["_presented_question_id"] = assigned_question_id
        return result


def record_calibration_evidence(
    hypothesis_id: int,
    question_id: str,
    correct: bool,
    attempt_id: int | None = None,
    now: float | None = None,
) -> dict | None:
    now = now if now is not None else time.time()
    with db.session() as s:
        row = _lock_calibration_hypothesis(s, hypothesis_id)
        if row is None or row.status != "pending":
            return _calibration_dict(row) if row else None
        evidence = _loads_json(row.evidence_json, [])
        item = next((item for item in evidence if item.get("question_id") == question_id), None)
        if item is None:
            item = {
                "question_id": question_id,
                "correct": bool(correct),
                "answered_at": now,
                "attempt_id": attempt_id,
            }
            evidence.append(item)
        elif item.get("correct") is None:
            item["correct"] = bool(correct)
            item["answered_at"] = now
            item["attempt_id"] = attempt_id
        row.evidence_json = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        row.updated_at = now
        s.commit()
        return _calibration_dict(row)


def resolve_calibration(hypothesis_id: int, status: str, now: float | None = None) -> dict | None:
    allowed = {"retracted", "pending", "inconclusive"}
    if status == "confirmed":
        raise ValueError("confirmed 必须通过原子证据确认流程写入")
    if status not in allowed:
        raise ValueError("校准状态必须是 retracted/pending/inconclusive")
    thread_id = None
    with db.session() as s:
        row = s.get(CalibrationHypothesis, hypothesis_id)
        if row is None:
            return None
        transitioned = row.status == "pending" and row.status != status
        if transitioned:
            row.status = status
            row.updated_at = now if now is not None else time.time()
            s.commit()
        result = _calibration_dict(row)
        result["_transitioned"] = transitioned
        thread_id = row.thread_id
    if transitioned:
        cache.delete_keys(f"stats:summary:{thread_id}")
    return result


def confirm_calibration_evidence(
    hypothesis_id: int,
    now: float | None = None,
) -> dict | None:
    """原子地认领每题首条证据、更新知识状态并确认校准。"""
    now = now if now is not None else time.time()
    thread_id = None
    with db.session() as s:
        claim = s.execute(
            update(CalibrationHypothesis)
            .where(
                CalibrationHypothesis.id == hypothesis_id,
                CalibrationHypothesis.status == "pending",
            )
            .values(status="confirmed", updated_at=now)
        )
        if int(claim.rowcount or 0) != 1:
            s.rollback()
            row = s.get(CalibrationHypothesis, hypothesis_id)
            if row is None:
                return None
            result = _calibration_dict(row)
            result["_transitioned"] = False
            result["_applied_question_ids"] = []
            return result

        row = s.get(CalibrationHypothesis, hypothesis_id)
        evidence = [
            item for item in _loads_json(row.evidence_json, []) if item.get("correct") is not None
        ]
        question_ids = {item["question_id"] for item in evidence}
        outcomes = [bool(item["correct"]) for item in evidence]
        if (
            len(question_ids) < config.ATTRIBUTION_CALIBRATION_MIN_EVIDENCE
            or max((outcomes.count(value) for value in (True, False)), default=0)
            < config.ATTRIBUTION_CALIBRATION_MIN_EVIDENCE
        ):
            raise ValueError("Calibration evidence is insufficient or inconsistent")
        attempts = s.scalars(
            select(LearnerAttempt)
            .where(
                LearnerAttempt.thread_id == row.thread_id,
                LearnerAttempt.calibration_id == hypothesis_id,
                LearnerAttempt.question_id.in_(question_ids),
                LearnerAttempt.attribution_status == "confirmed",
            )
            .order_by(LearnerAttempt.id)
        ).all()
        attempts_by_question: dict[str, list[LearnerAttempt]] = {}
        for attempt in attempts:
            attempts_by_question.setdefault(attempt.question_id, []).append(attempt)

        selected: dict[str, LearnerAttempt] = {}
        for item in evidence:
            question_id = item["question_id"]
            candidates = attempts_by_question.get(question_id, [])
            expected_attempt_id = item.get("attempt_id")
            if expected_attempt_id is not None:
                match = next(
                    (
                        attempt
                        for attempt in candidates
                        if attempt.id == expected_attempt_id
                        and bool(attempt.correct) == bool(item["correct"])
                    ),
                    None,
                )
            else:
                # Pending records created before attempt IDs were persisted can be
                # replayed deterministically by matching the recorded outcome.
                match = next(
                    (
                        attempt
                        for attempt in candidates
                        if bool(attempt.correct) == bool(item["correct"])
                    ),
                    None,
                )
            if match is None:
                raise RuntimeError("Calibration evidence does not match its raw learner attempt")
            selected[question_id] = match
        if any(attempt.profile_applied for attempt in attempts):
            raise RuntimeError(
                "Pending calibration contains already-applied attempts; confirmation refused"
            )

        for item in evidence:
            question_id = item["question_id"]
            selected[question_id].profile_applied = True
            _increment_learner_knowledge_state(
                s,
                row.thread_id,
                row.knowledge_point_id,
                bool(item["correct"]),
                float(item.get("answered_at") or now),
            )
        thread_id = row.thread_id
        s.commit()
        result = _calibration_dict(row)
        result["_transitioned"] = True
        result["_applied_question_ids"] = sorted(question_ids)
    cache.delete_keys(f"stats:summary:{thread_id}")
    return result


def profile_attempt_count(thread_id: str, since: float | None = None) -> int:
    """已进入长期画像的线程作答数，用于限制校准插入频率。"""
    with db.session() as s:
        stmt = (
            select(func.count())
            .select_from(LearnerAttempt)
            .where(
                LearnerAttempt.thread_id == thread_id,
                LearnerAttempt.attribution_status == "confirmed",
                LearnerAttempt.profile_applied.is_(True),
            )
        )
        if since is not None:
            stmt = stmt.where(LearnerAttempt.answered_at > since)
        return int(s.scalar(stmt) or 0)


def attempt_count(thread_id: str, since: float | None = None) -> int:
    """线程内原始作答数；校准节奏不能因 staged 证据而失去节流。"""
    with db.session() as s:
        stmt = (
            select(func.count())
            .select_from(LearnerAttempt)
            .where(
                LearnerAttempt.thread_id == thread_id,
            )
        )
        if since is not None:
            stmt = stmt.where(LearnerAttempt.answered_at > since)
        return int(s.scalar(stmt) or 0)


def calibration_presentation_count(thread_id: str) -> int:
    with db.session() as s:
        return int(
            s.scalar(
                select(func.sum(CalibrationHypothesis.presentation_count)).where(
                    CalibrationHypothesis.thread_id == thread_id
                )
            )
            or 0
        )


def _increment_learner_knowledge_state(
    session,
    thread_id: str,
    point_id: str,
    correct: bool,
    now: float,
) -> None:
    row = session.get(LearnerKnowledgeState, (thread_id, point_id))
    if row is None:
        row = LearnerKnowledgeState(
            thread_id=thread_id,
            knowledge_point_id=point_id,
            beta_a=1.0,
            beta_b=1.0,
            attempts=0,
            correct=0,
            last_seen=now,
        )
        session.add(row)
    row.beta_a += 1.0 if correct else 0.0
    row.beta_b += 0.0 if correct else 1.0
    row.attempts += 1
    row.correct += 1 if correct else 0
    row.last_seen = max(float(row.last_seen), now)


def update_learner_knowledge_states(
    thread_id: str, point_ids: list[str], correct: bool, now: float | None = None
) -> None:
    """将一次题目结果写入所有关联知识点；主/次标签都获得同一条证据。"""
    if not point_ids:
        return
    now = now or time.time()
    with db.session() as s:
        for point_id in set(point_ids):
            _increment_learner_knowledge_state(
                s,
                thread_id,
                point_id,
                correct,
                now,
            )
        s.commit()
    cache.delete_keys(f"stats:summary:{thread_id}")


def learner_knowledge_states(thread_id: str, point_ids: list[str] | None = None) -> list[dict]:
    with db.session() as s:
        stmt = select(LearnerKnowledgeState).where(LearnerKnowledgeState.thread_id == thread_id)
        if point_ids:
            stmt = stmt.where(LearnerKnowledgeState.knowledge_point_id.in_(point_ids))
        rows = s.scalars(stmt).all()
    return [
        {
            "knowledge_point_id": row.knowledge_point_id,
            "beta_a": row.beta_a,
            "beta_b": row.beta_b,
            "attempts": row.attempts,
            "correct": row.correct,
            "last_seen": row.last_seen,
        }
        for row in rows
    ]
