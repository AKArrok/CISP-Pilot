"""ORM 模型 — 仿 Dify 的做法：全项目单一建模，方言由 DATABASE_URL 决定。

MySQL（utf8mb4）为主后端，SQLite 为零配置降级。课件向量索引与长期
记忆向量都不进关系库（这里用本地 npz）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class RequestTrace(Base):
    """Bounded request observations; ownership is resolved through Thread."""
    __tablename__ = "request_traces"

    request_id: Mapped[str] = mapped_column(
        String(36).with_variant(String(36, collation="utf8mb4_bin"), "mysql"),
        primary_key=True,
    )
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("threads.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    trace_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)


class Question(Base):
    """结构化真题 + AI 生成题统一入库（AI 题复用此表，source='AI生成'）。"""
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    num: Mapped[int] = mapped_column(Integer, default=0)
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[str] = mapped_column(Text)      # JSON {"A":...}
    answer: Mapped[str] = mapped_column(String(8), default="")
    analysis: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    dup_of: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KBChunk(Base):
    """课件知识块（检索层启动时加载；BM25 与向量索引仍由本地文件构建）。"""
    __tablename__ = "kb_chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(128))
    page: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"),
                                     primary_key=True, autoincrement=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    question_source: Mapped[str] = mapped_column(String(64))
    domain: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    stem: Mapped[str | None] = mapped_column(Text, nullable=True)
    choice: Mapped[str] = mapped_column(String(8))
    correct: Mapped[bool] = mapped_column(Boolean)
    answered_at: Mapped[float] = mapped_column(Float)


class AnswerSubmission(Base):
    """Committed answer receipt; the thread/key pair identifies one practice."""
    __tablename__ = "answer_submissions"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    submission_id: Mapped[str] = mapped_column(
        String(128).with_variant(String(128, collation="utf8mb4_bin"), "mysql"),
        primary_key=True,
    )
    question_id: Mapped[str] = mapped_column(String(64))
    choice: Mapped[str] = mapped_column(String(8))
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float)


class LearnerAttempt(Base):
    """Thread-scoped answer evidence used by learner profiles."""
    __tablename__ = "learner_attempts"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"),
                                    primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    question_source: Mapped[str] = mapped_column(String(64))
    domain: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    stem: Mapped[str | None] = mapped_column(Text, nullable=True)
    choice: Mapped[str] = mapped_column(String(8))
    correct: Mapped[bool] = mapped_column(Boolean)
    answered_at: Mapped[float] = mapped_column(Float)
    attribution_status: Mapped[str] = mapped_column(
        String(16), default="confirmed", server_default="confirmed", index=True
    )
    profile_applied: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", index=True
    )
    calibration_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), nullable=True, index=True
    )
    attribution_point_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )


class ReviewCard(Base):
    """题目级 FSRS 复习卡（每道答过的题一张，答错/答对驱动 DSR 状态更新）。

    存 FSRS Card 的标量快照（epoch 浮点时间戳），由 quiz/review.py 读写。
    """
    __tablename__ = "review_cards"

    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    state: Mapped[int] = mapped_column(Integer, default=0)          # FSRS State
    step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stability: Mapped[float] = mapped_column(Float, default=0.0)
    difficulty: Mapped[float] = mapped_column(Float, default=0.0)
    due: Mapped[float] = mapped_column(Float, index=True)           # epoch 秒
    last_review: Mapped[float | None] = mapped_column(Float, nullable=True)
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)


class LearnerReviewCard(Base):
    """Thread-scoped FSRS review card."""
    __tablename__ = "learner_review_cards"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    state: Mapped[int] = mapped_column(Integer, default=0)
    step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stability: Mapped[float] = mapped_column(Float, default=0.0)
    difficulty: Mapped[float] = mapped_column(Float, default=0.0)
    due: Mapped[float] = mapped_column(Float, index=True)
    last_review: Mapped[float | None] = mapped_column(Float, nullable=True)
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)


class QuestionSignal(Base):
    """问答侧薄弱信号：用户在知识问答中提问的知识域（画像融合用）。

    与 attempts 互补：答题对错是强信号，反复追问某域是"哪里不会"的先验信号。
    """
    __tablename__ = "question_signals"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"),
                                     primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)


class DiagnosticSession(Base):
    """Recoverable onboarding diagnostic session."""
    __tablename__ = "diagnostic_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    selected_domains: Mapped[str] = mapped_column(Text, default="[]")
    self_report: Mapped[str] = mapped_column(Text, default="{}")
    question_ids: Mapped[str] = mapped_column(Text, default="[]")
    responses: Mapped[str] = mapped_column(Text, default="[]")
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_questions: Mapped[int] = mapped_column(Integer, default=12)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)


class DiagnosticStartReceipt(Base):
    """A stable start key creates exactly one diagnostic for its thread."""
    __tablename__ = "diagnostic_start_receipts"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    start_id: Mapped[str] = mapped_column(
        String(128).with_variant(String(128, collation="utf8mb4_bin"), "mysql"),
        primary_key=True,
    )
    payload_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float)


class DiagnosticAnswerReceipt(Base):
    """Each diagnostic question accepts one complete answer payload."""
    __tablename__ = "diagnostic_answer_receipts"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(
        String(64).with_variant(String(64, collation="utf8mb4_bin"), "mysql"),
        primary_key=True,
    )
    payload_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float)


class DiagnosticEvent(Base):
    """Auditable diagnostic event."""
    __tablename__ = "diagnostic_events"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"),
                                    primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[float] = mapped_column(Float)


class KnowledgePoint(Base):
    """Reviewable knowledge-point candidate."""
    __tablename__ = "knowledge_points"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    parent_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="candidate", index=True)


class QuestionKnowledgePoint(Base):
    """Many-to-many question and knowledge-point tag."""
    __tablename__ = "question_knowledge_points"

    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_point_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    tag_source: Mapped[str] = mapped_column(String(16), default="heuristic")


class QuestionAttribution(Base):
    """Conservative current attribution snapshot."""
    __tablename__ = "question_attributions"

    question_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_point_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    candidate_domain: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    attribution_status: Mapped[str] = mapped_column(String(16), index=True)
    agreement_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    reason: Mapped[str] = mapped_column(Text, default="")
    classifier_version: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)


class QuestionAttributionEvent(Base):
    """Immutable attribution write event."""
    __tablename__ = "question_attribution_events"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"),
                                    primary_key=True, autoincrement=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    attribution_status: Mapped[str] = mapped_column(String(16), index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    classifier_version: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[float] = mapped_column(Float)


class QuestionAttributionCorrection(Base):
    """Append-only, thread-scoped user correction event."""
    __tablename__ = "question_attribution_corrections"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"),
                                    primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    corrected_point_id: Mapped[str] = mapped_column(String(64), index=True)
    corrected_domain: Mapped[str] = mapped_column(String(32), index=True)
    previous_point_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text, default="")
    replayed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    retracted_calibrations: Mapped[int] = mapped_column(Integer, default=0)
    replacement_calibration_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), nullable=True
    )
    created_at: Mapped[float] = mapped_column(Float)


class CalibrationHypothesis(Base):
    """Thread-scoped calibration queue for probable attributions."""
    __tablename__ = "calibration_hypotheses"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"),
                                    primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    knowledge_point_id: Mapped[str] = mapped_column(String(64), index=True)
    trigger_question_id: Mapped[str] = mapped_column(String(64))
    trigger_correct: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    calibration_reason: Mapped[str] = mapped_column(Text, default="")
    presentation_count: Mapped[int] = mapped_column(Integer, default=0)
    last_presented_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)


class LearnerKnowledgeState(Base):
    """Thread-scoped knowledge mastery snapshot."""
    __tablename__ = "learner_knowledge_states"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_point_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    beta_a: Mapped[float] = mapped_column(Float, default=1.0)
    beta_b: Mapped[float] = mapped_column(Float, default=1.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    last_seen: Mapped[float] = mapped_column(Float)


class Conversation(Base):
    """已提交的对话历史；新轮次的 user/assistant 成对写入。"""
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"),
                                     primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))          # user | assistant
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)


class Metric(Base):
    """请求级可观测性指标。"""
    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"),
                                     primary_key=True, autoincrement=True)
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    route_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    retrieval_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_token_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
