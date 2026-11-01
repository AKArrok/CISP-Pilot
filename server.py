"""FastAPI 服务: SSE 流式问答 + 刷题接口 + 薄弱点统计 + 静态面板。

启动: python server.py → http://127.0.0.1:9528
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import json
import logging
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage

import config
from agents.graph import SessionStore
from storage import cache
from storage.http_auth import (
    USER_DEPENDENCY, WRITE_DEPENDENCY, require_thread, require_diagnostic,
    router as auth_router, validate_origin_config,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def warmup() -> None:
    """只读检查迁移和资产；未就绪时保留健康探针，不改表或加载模型。"""
    payload, ready = _readiness_payload()
    if not ready:
        logging.warning("服务未就绪: %s；请显式完成迁移和资产部署", payload["status"])
        return

    if config.EMBEDDING_BACKEND == "local":
        from llms import get_embeddings

        get_embeddings()  # 预载 embedding 模型
        if config.ENABLE_RERANKING:
            from retrieval.reranker import _get_encoder

            _get_encoder()  # 预载精排模型
        get_embeddings().embed_query("预热")  # 消除首次前向的惰性初始化
    logging.info("预热完成")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_origin_config()
    warmup()
    yield


app = FastAPI(title="CISP 备考问答机器人", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(auth_router)


@app.middleware("http")
async def private_responses(request: Request, call_next):
    from sqlalchemy.exc import SQLAlchemyError

    private = request.url.path.startswith(("/api/", "/chat/", "/quiz/", "/diagnostic/"))
    write = private and request.method in {"POST", "PUT", "PATCH", "DELETE"}
    if write:
        from starlette.concurrency import run_in_threadpool

        _payload, ready = await run_in_threadpool(_readiness_payload)
        if not ready:
            return JSONResponse(
                {"status": "unavailable", "message": "Service unavailable"},
                status_code=503, headers={"Cache-Control": "no-store"},
            )
    try:
        response = await call_next(request)
    except SQLAlchemyError:
        if not write and request.url.path not in {"/api/readiness", "/api/diagnostics", "/api/history"} and not request.url.path.startswith("/api/traces/"):
            raise
        response = JSONResponse(
            {"status": "unavailable", "message": "Service unavailable"},
            status_code=503,
        )
    if private:
        response.headers["Cache-Control"] = "no-store"
    return response

# ── 问答（SSE 流式）────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    thread_id: str = Field(min_length=1, max_length=64)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, user: dict = WRITE_DEPENDENCY):
    require_thread(req.thread_id, user)

    # 限流（固定窗口，Redis 计数；不可达时降级为进程内存计数）
    allowed, _n = cache.rate_limit(f"rl:chat:user:{user['id']}", config.RATE_LIMIT_PER_MIN, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="请求过于频繁，稍后再试")

    from agents.tracing import RequestTrace, current_trace
    from agents.budget import RequestBudget, current_budget
    from storage import request_traces

    request_id = str(uuid4())
    trace = RequestTrace(request_id)
    budget = RequestBudget(max_calls=config.CHAT_MAX_CALLS,
                           timeout_seconds=config.CHAT_TIMEOUT_SECONDS,
                           max_output_tokens=config.CHAT_MAX_OUTPUT_TOKENS,
                           max_attempts_per_call=config.CHAT_MAX_ATTEMPTS_PER_CALL)
    observation_deadline_at = time.time() + budget.remaining_seconds

    def save_trace(status):
        try:
            snapshot = trace.snapshot()
            snapshot["budget"] = budget.snapshot()
            snapshot["observation_deadline_at"] = observation_deadline_at
            if not request_traces.update(request_id, snapshot, status=status):
                request_traces.create(request_id, req.thread_id, snapshot, status=status)
            return True
        except Exception:
            logging.exception("Request trace persistence failed")
            return False

    save_trace("running")
    config_ = {"configurable": {"thread_id": req.thread_id}, "metadata": {"request_id": request_id}}

    # 问答缓存：仅首轮（该会话还没有任何历史）问题，避免多轮上下文误命中
    trace.observe("history", "started")
    try:
        async with asyncio.timeout(budget.remaining_seconds):
            app_graph = SessionStore.get(req.thread_id)
            history_state = await app_graph.aget_state(config_)
            graph_empty = not (history_state.values or {}).get("messages", [])
            from agents import memory
            from starlette.concurrency import run_in_threadpool
            restored_rows = await run_in_threadpool(memory.load_short_context, req.thread_id,
                           limit=config.MEMORY_MAX_ROUNDS * 2) if graph_empty else []
            budget.check()
    except asyncio.CancelledError:
        budget.cancel()
        trace.observe("history", "cancelled")
        save_trace("cancelled")
        raise
    except Exception as exc:
        budget.cancel("deadline" if isinstance(exc, TimeoutError) or budget.remaining_seconds <= 0 else "failed")
        trace.observe("history", "failed")
        persisted = save_trace("failed")
        logging.exception("Chat history restoration failed")
        return JSONResponse({"message": "Service unavailable", "request_id": request_id,
                             "trace_persisted": persisted}, status_code=503,
                            headers={"X-Request-ID": request_id})
    trace.observe("history", "completed")
    # First turns can recall private long-term memory. Never share answer caches.

    async def chat_events():
        # 阶段计时（可观测性）: 路由 / 检索 / 首 token / 总耗时
        t0 = time.perf_counter()
        stamps = {"route_ms": None, "retrieval_ms": None, "first_token_ms": None}
        intent_seen = None
        sent_tokens = 0
        if config.ENABLE_EVIDENCE_SUBAGENT:
            yield sse({"type": "progress", "stage": "retrieval", "content": "正在检索资料"})
        messages = [HumanMessage(content=req.query)]
        if restored_rows:
            restored = []
            for row in restored_rows:
                cls = HumanMessage if row.get("role") == "user" else AIMessage
                restored.append(cls(content=row.get("content", "")))
            messages = restored + messages
        async for ev in app_graph.astream_events(
            {"messages": messages, "question": req.query,
             "thread_id": req.thread_id},
            config=config_, version="v2",
        ):
            kind = ev["event"]
            node = (ev.get("metadata") or {}).get("langgraph_node")
            if node in {"route", "rewrite", "retrieve", "recall_memory", "answer", "persist", "chitchat", "quiz_hint"} and ev.get("name") == node:
                if kind in {"on_chain_start", "on_chain_end"}:
                    trace.observe(node, "started" if kind == "on_chain_start" else "completed")
            now = time.perf_counter()
            if kind == "on_chain_end" and node == "route":
                out = ev["data"]["output"]
                intent = out.get("intent") if isinstance(out, dict) else getattr(out, "intent", None)
                if intent and intent_seen is None:
                    intent_seen = intent
                    stamps["route_ms"] = round((now - t0) * 1000, 1)
                if intent:
                    yield sse({"type": "intent", "intent": intent})
            elif kind == "on_chain_end" and node == "retrieve":
                out = ev["data"]["output"]
                contexts = out.get("contexts", []) if isinstance(out, dict) else []
                report = out.get("evidence_report") if isinstance(out, dict) else None
                if report:
                    yield sse({"type": "evidence", "report": report})
                stamps["retrieval_ms"] = round((now - t0) * 1000, 1)
                from agents.sources import source_preview
                yield sse({"type": "contexts", "contexts": [source_preview(c) for c in contexts]})
            elif kind == "on_chat_model_stream" and node == "answer":
                chunk = ev["data"]["chunk"]
                if chunk.content:
                    if stamps["first_token_ms"] is None:
                        stamps["first_token_ms"] = round((now - t0) * 1000, 1)
                    sent_tokens += 1
                    yield sse({"type": "token", "content": str(chunk.content)})
        # 闲聊/做题引导不走 answer 节点、无 token 流：补发完整回复，避免前端空白气泡
        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        final = await app_graph.aget_state(config_)
        answer = (final.values or {}).get("answer", "")
        from agents.usage import USAGE_SCOPE, add_receipts

        usage = add_receipts((final.values or {}).get("usage") or {})
        if sent_tokens == 0 and answer:
            yield sse({"type": "token", "content": answer})
        try:
            from agents.metrics import record
            record(req.thread_id, req.query, intent_seen,
                   stamps["route_ms"], stamps["retrieval_ms"],
                   stamps["first_token_ms"], total_ms,
                   usage.get("prompt_tokens"), usage.get("completion_tokens"))
        except Exception:
            trace.observe("metrics", "degraded")
            logging.exception("指标写入失败（不影响回答）")
        yield sse({"type": "usage", "usage": usage, "scope": USAGE_SCOPE, "billing_cost": None})

    async def event_gen():
        token = current_trace.set(trace)
        budget_token = current_budget.set(budget)
        trace.observe("graph", "started")
        terminal = False
        try:
            async with asyncio.timeout(budget.remaining_seconds):
                async for event in chat_events():
                    budget.check()
                    yield event
                budget.check()
            budget.cancel("completed")
            trace.observe("graph", "completed")
            persisted = save_trace("completed")
            terminal = True
            yield sse({"type": "trace", "request_id": request_id, "trace_persisted": persisted,
                       "conversation_commit": trace.snapshot()["conversation_commit"]})
            yield sse({"type": "done"})
        except (asyncio.CancelledError, GeneratorExit):
            if not terminal:
                budget.cancel()
                trace.observe("graph", "cancelled")
                save_trace("cancelled")
            raise
        except Exception as exc:
            budget.cancel("deadline" if isinstance(exc, TimeoutError) or budget.remaining_seconds <= 0 else "failed")
            trace.observe("graph", "failed")
            persisted = save_trace("failed")
            terminal = True
            logging.exception("Chat failed; completion withheld")
            yield sse({"type": "error", "message": "本轮未确认完成，请刷新历史后再试。",
                       "request_id": request_id, "trace_persisted": persisted,
                       "conversation_commit": trace.snapshot()["conversation_commit"]})
        finally:
            current_budget.reset(budget_token)
            current_trace.reset(token)

    return StreamingResponse(event_gen(), media_type="text/event-stream", headers={"X-Request-ID": request_id})


def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ── 刷题 ────────────────────────────────────────────────────────────────

class QuizNextRequest(BaseModel):
    mode: str = "random"          # random | weak
    domain: str | None = None
    anchor_question_id: str | None = None   # 以该题（通常是刚答错的）为锚点定向生成同类题
    thread_id: str = Field(min_length=1, max_length=64)


class QuizAnswerRequest(BaseModel):
    question_id: str
    choice: str = Field(min_length=1, max_length=4, pattern=r"^[A-Da-d]+$")
    thread_id: str = Field(min_length=1, max_length=64)
    submission_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class AttributionCorrectionRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)
    corrected_point_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=2000)


class DiagnosticStartRequest(BaseModel):
    """自评只缩小诊断范围，不能直接把所选域标成薄弱。"""
    thread_id: str = Field(min_length=1, max_length=64)
    start_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    selected_domains: list[str] = Field(min_length=1, max_length=4)
    self_report: dict = Field(default_factory=dict)
    max_questions: int = Field(default=12, ge=6, le=20)


class DiagnosticAnswerRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=64)
    choice: str = Field(min_length=1, max_length=4, pattern=r"^[A-Da-d]+$")
    confidence: int | None = Field(default=None, ge=0, le=3)
    reason: str | None = Field(default=None, max_length=1000)


@app.post("/quiz/next")
def quiz_next(req: QuizNextRequest, user: dict = WRITE_DEPENDENCY):
    require_thread(req.thread_id, user)
    from quiz import get_next_question
    try:
        return get_next_question(mode=req.mode, domain=req.domain,
                                 anchor_question_id=req.anchor_question_id,
                                 thread_id=req.thread_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/quiz/answer")
def quiz_answer(req: QuizAnswerRequest, user: dict = WRITE_DEPENDENCY):
    require_thread(req.thread_id, user)
    from quiz import submit_answer
    from storage.submissions import SubmissionConflict
    try:
        return submit_answer(req.question_id, req.choice, req.thread_id,
                             submission_id=req.submission_id)
    except SubmissionConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ── 入门诊断 ────────────────────────────────────────────────────────────


@app.get("/api/diagnostics")
def api_diagnostics(thread_id: str = Query(min_length=1, max_length=64),
                    limit: int = Query(default=20, ge=1, le=100),
                    offset: int = Query(default=0, ge=0),
                    user: dict = USER_DEPENDENCY):
    require_thread(thread_id, user)
    from storage.diagnostics import list_diagnostics
    return list_diagnostics(thread_id, limit, offset)

@app.post("/diagnostic/start")
def diagnostic_start(req: DiagnosticStartRequest, user: dict = WRITE_DEPENDENCY):
    require_thread(req.thread_id, user)
    from quiz.diagnosis import start_session
    from storage.submissions import SubmissionConflict
    try:
        return start_session(req.thread_id, req.selected_domains,
                             req.self_report, req.max_questions, start_id=req.start_id)
    except SubmissionConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.post("/diagnostic/{session_id}/next")
def diagnostic_next(session_id: str, user: dict = WRITE_DEPENDENCY):
    require_diagnostic(session_id, user)
    from quiz.diagnosis import next_question
    try:
        return next_question(session_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.post("/diagnostic/{session_id}/answer")
def diagnostic_answer(session_id: str, req: DiagnosticAnswerRequest,
                      user: dict = WRITE_DEPENDENCY):
    require_diagnostic(session_id, user)
    from quiz.diagnosis import answer
    from storage.submissions import SubmissionConflict
    try:
        return answer(session_id, req.question_id, req.choice,
                     req.confidence, req.reason)
    except SubmissionConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.get("/diagnostic/{session_id}/report")
def diagnostic_report(session_id: str, user: dict = USER_DEPENDENCY):
    require_diagnostic(session_id, user)
    from quiz.diagnosis import report
    try:
        return report(session_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/stats")
def api_stats(thread_id: str, user: dict = USER_DEPENDENCY):
    require_thread(thread_id, user)
    from quiz import get_stats
    return get_stats(thread_id)


@app.get("/api/attributions")
def api_attributions(status: str | None = None, limit: int = Query(default=100, ge=1, le=500),
                     user: dict = USER_DEPENDENCY):
    """题目归因审计概览；status 只筛选 confirmed/probable/unverified。"""
    if status is not None and status not in {"confirmed", "probable", "unverified"}:
        raise HTTPException(status_code=422, detail="无效的归因状态")
    from storage import repos
    return repos.question_attribution_rows(limit=limit, status=status)


@app.get("/api/attribution-review")
def api_attribution_review(
    thread_id: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=30, ge=1, le=100),
    after_question_id: str | None = Query(default=None, min_length=1, max_length=64),
    question_id: str | None = Query(default=None, max_length=64),
    user: dict = USER_DEPENDENCY,
):
    """按稳定题号游标分页的纠错审阅列表，应用指定线程的有效纠错。"""
    from storage import repos
    require_thread(thread_id, user)
    page = repos.attribution_review_page(
        thread_id,
        limit=limit,
        after_question_id=after_question_id,
        question_id_query=(question_id or "").strip() or None,
    )
    if page["items"]:
        from quiz.selector import QuestionBank

        bank = QuestionBank.get()
        for item in page["items"]:
            question = bank.get_by_id(item["question_id"])
            item["question_stem"] = question.get("stem") if question else None
            item["question_options"] = question.get("options", {}) if question else {}
    return page


@app.post("/api/attributions/{question_id}/correction")
def api_attribution_correction(question_id: str, req: AttributionCorrectionRequest,
                               user: dict = WRITE_DEPENDENCY):
    require_thread(req.thread_id, user)
    from quiz.selector import QuestionBank
    from storage import repos

    if QuestionBank.get().get_by_id(question_id) is None:
        raise HTTPException(status_code=404, detail="Question not found")
    try:
        return repos.record_attribution_correction(
            req.thread_id, question_id, req.corrected_point_id, req.reason,
        )
    except repos.AttributionCorrectionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/attribution-corrections")
def api_attribution_corrections(
    thread_id: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
    before_id: int | None = Query(default=None, gt=0),
    user: dict = USER_DEPENDENCY,
):
    """线程级不可变纠错事件；before_id 用于按事件 ID 倒序完整导出。"""
    from storage import repos
    require_thread(thread_id, user)
    return repos.attribution_corrections(
        thread_id,
        limit=limit,
        before_id=before_id,
    )


@app.get("/api/attribution-observability")
def api_attribution_observability(
    thread_id: str = Query(min_length=1, max_length=64),
    since: float | None = Query(default=None, ge=0),
    user: dict = USER_DEPENDENCY,
):
    """线程级纠错聚合，仅用于灰度运行观测，不是准确率指标。"""
    from storage import repos
    require_thread(thread_id, user)
    return repos.attribution_correction_observability(thread_id, since=since)


@app.get("/api/knowledge-points")
def api_knowledge_points(status: str = "active", user: dict = USER_DEPENDENCY):
    if status not in {"active", "candidate"}:
        raise HTTPException(status_code=422, detail="Invalid knowledge-point status")
    from storage import repos
    return [
        point for point in repos.load_knowledge_points()
        if point["status"] == status
    ]


@app.get("/api/domains")
def api_domains(user: dict = USER_DEPENDENCY):
    from domains import DOMAINS
    return DOMAINS


@app.get("/api/history")
def api_history(thread_id: str, limit: int = Query(default=50, ge=1, le=500),
                user: dict = USER_DEPENDENCY):
    """某会话的落盘历史（重启后仍可查，供会话恢复/续聊）。"""
    from agents.memory import load_history
    require_thread(thread_id, user)
    return load_history(thread_id, limit=limit)


@app.get("/api/traces/{request_id}")
def api_trace(request_id: str, user: dict = USER_DEPENDENCY):
    from storage import request_traces

    trace = request_traces.get(request_id, owner_user_id=user["id"])
    if trace is None:
        raise HTTPException(status_code=404, detail="Request trace not found")
    return {**trace, "recovery": request_traces.recovery_state(trace)}


@app.get("/api/threads")
def api_threads(limit: int = Query(default=20, ge=1, le=100),
                user: dict = USER_DEPENDENCY):
    """历史会话列表（最近优先）。"""
    from storage import repos
    return repos.list_user_threads(user["id"])[:limit]


@app.get("/api/metrics")
def api_metrics(user: dict = USER_DEPENDENCY):
    """请求级指标聚合：阶段耗时分位数、Token 用量、意图分布。"""
    from agents.metrics import summary
    return summary(owner_user_id=user["id"])


@app.get("/api/document")
def api_document(source: str = Query(min_length=1), page: int = Query(ge=1),
                 user: dict = USER_DEPENDENCY):
    """返回知识库引用的原始课件。

    前端将页码附在 URL fragment（``#page=N``）中；PDF 查看器会据此直接定位。
    ``page`` 仍作为查询参数校验，避免前端生成的无效链接悄悄失效。
    """
    from document_library import find_document

    path = find_document(source)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail="未找到原始资料。请检查 CISP_COURSE_DIR / CISP_EXAM_DIR 是否指向导入知识库时使用的目录。",
        )

    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    return FileResponse(path, media_type=media_type, filename=path.name,
                        content_disposition_type="inline")


@app.get("/api/health")
def health():
    return {"status": "ok"}


def _readiness_payload():
    from sqlalchemy.exc import SQLAlchemyError

    from data_ingest.manifest import validate_index
    from storage import db

    database_available = True
    try:
        schema = db.schema_status()
    except Exception:
        schema = {
            "database_backend": None,
            "expected_revision": db.SCHEMA_HEAD,
            "current_revisions": [],
            "migration_current": False,
            "error": "Database unavailable",
        }
        return {
            "status": "database_unavailable", "schema": schema,
            "assets": {"ready": False, "error": "Database unavailable"},
        }, False
    try:
        validate_index()
        assets = {"ready": True, "error": None}
    except SQLAlchemyError:
        database_available = False
        assets = {"ready": False, "error": "Database unavailable"}
    except Exception as exc:
        assets = {"ready": False, "error": str(exc)}
    ready = schema["migration_current"] and assets["ready"]
    if ready:
        status = "ready"
    elif not database_available:
        status = "database_unavailable"
    elif not schema["migration_current"]:
        status = "migration_required"
    else:
        status = "assets_invalid"
    payload = {
        "status": status,
        "schema": schema,
        "assets": assets,
    }
    return payload, ready


@app.get("/api/ready")
def readiness():
    _payload, ready = _readiness_payload()
    return JSONResponse({"status": "ready" if ready else "unavailable"},
                        status_code=200 if ready else 503)


@app.get("/api/readiness")
def readiness_details(user: dict = USER_DEPENDENCY):
    payload, ready = _readiness_payload()
    return JSONResponse(payload, status_code=200 if ready else 503)

# ── 静态面板 ────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    config.validate()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9528, log_level="info")
