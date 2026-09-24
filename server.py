"""FastAPI 服务: SSE 流式问答 + 刷题接口 + 薄弱点统计 + 静态面板。

启动: python server.py → http://localhost:9528
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage

import config
from agents.graph import SessionStore
from storage import cache

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def warmup() -> None:
    """启动预热：建表一次、预载本地模型——避免首个请求承受建表/模型加载风暴。"""
    from storage import db

    db.create_all()
    import quiz  # noqa: F401  触发 quiz 包初始化（含 init_db）

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
    warmup()
    yield


app = FastAPI(title="CISP 备考问答机器人", lifespan=lifespan)

# ── 问答（SSE 流式）────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    thread_id: str = Field(min_length=1, max_length=64)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    import hashlib

    # 限流（固定窗口，Redis 计数；不可达时降级为进程内存计数）
    allowed, _n = cache.rate_limit(f"rl:{req.thread_id}", config.RATE_LIMIT_PER_MIN, 60)
    if not allowed:
        raise HTTPException(status_code=429, detail="请求过于频繁，稍后再试")

    app_graph = SessionStore.get(req.thread_id)
    config_ = {"configurable": {"thread_id": req.thread_id}}

    # 问答缓存：仅首轮（该会话还没有任何历史）问题，避免多轮上下文误命中
    history_state = await app_graph.aget_state(config_)
    graph_empty = not (history_state.values or {}).get("messages", [])
    from agents import memory
    restored_rows = memory.load_short_context(req.thread_id, limit=config.MEMORY_MAX_ROUNDS * 2) if graph_empty else []
    first_turn = graph_empty and not restored_rows
    cache_key = f"ans:{hashlib.md5(req.query.strip().encode()).hexdigest()}"
    if first_turn:
        cached_answer = cache.get_json(cache_key)
        if cached_answer:
            from agents.metrics import record
            record(req.thread_id, req.query, "knowledge(cached)", 0, 0, 1, 1, 0, 0)
            memory.save_round(req.thread_id, req.query, cached_answer, "knowledge")

            async def cached_gen():
                yield sse({"type": "intent", "intent": "knowledge"})
                yield sse({"type": "token", "content": cached_answer})
                yield sse({"type": "done", "cached": True})
            return StreamingResponse(cached_gen(), media_type="text/event-stream")

    async def event_gen():
        # 阶段计时（可观测性）: 路由 / 检索 / 首 token / 总耗时
        t0 = time.perf_counter()
        stamps = {"route_ms": None, "retrieval_ms": None, "first_token_ms": None}
        intent_seen = None
        sent_tokens = 0
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
                stamps["retrieval_ms"] = round((now - t0) * 1000, 1)
                yield sse({"type": "contexts", "contexts": [
                    {"source": c["source"], "page": c["page"], "domain": c["domain"],
                     "text": c["text"][:120]}
                    for c in contexts
                ]})
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
        usage = (final.values or {}).get("usage") or {}
        if sent_tokens == 0 and answer:
            yield sse({"type": "token", "content": answer})
        if answer and intent_seen == "knowledge" and first_turn:
            cache.set_json(cache_key, answer, ttl=config.ANSWER_CACHE_TTL)
        try:
            from agents.metrics import record
            record(req.thread_id, req.query, intent_seen,
                   stamps["route_ms"], stamps["retrieval_ms"],
                   stamps["first_token_ms"], total_ms,
                   usage.get("prompt_tokens"), usage.get("completion_tokens"))
        except Exception:
            logging.exception("指标写入失败（不影响回答）")
        yield sse({"type": "done"})

    return StreamingResponse(event_gen(), media_type="text/event-stream")


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


class AttributionCorrectionRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)
    corrected_point_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(default="", max_length=2000)


class DiagnosticStartRequest(BaseModel):
    """自评只缩小诊断范围，不能直接把所选域标成薄弱。"""
    thread_id: str = Field(min_length=1, max_length=64)
    selected_domains: list[str] = Field(min_length=1, max_length=4)
    self_report: dict = Field(default_factory=dict)
    max_questions: int = Field(default=12, ge=6, le=20)


class DiagnosticAnswerRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=64)
    choice: str = Field(min_length=1, max_length=4, pattern=r"^[A-Da-d]+$")
    confidence: int | None = Field(default=None, ge=0, le=3)
    reason: str | None = Field(default=None, max_length=1000)


@app.post("/quiz/next")
def quiz_next(req: QuizNextRequest):
    from quiz import get_next_question
    try:
        return get_next_question(mode=req.mode, domain=req.domain,
                                 anchor_question_id=req.anchor_question_id,
                                 thread_id=req.thread_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/quiz/answer")
def quiz_answer(req: QuizAnswerRequest):
    from quiz import submit_answer
    try:
        return submit_answer(req.question_id, req.choice, req.thread_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ── 入门诊断 ────────────────────────────────────────────────────────────

@app.post("/diagnostic/start")
def diagnostic_start(req: DiagnosticStartRequest):
    from quiz.diagnosis import start_session
    try:
        return start_session(req.thread_id, req.selected_domains,
                             req.self_report, req.max_questions)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.get("/diagnostic/{session_id}/next")
def diagnostic_next(session_id: str):
    from quiz.diagnosis import next_question
    try:
        return next_question(session_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.post("/diagnostic/{session_id}/answer")
def diagnostic_answer(session_id: str, req: DiagnosticAnswerRequest):
    from quiz.diagnosis import answer
    try:
        return answer(session_id, req.question_id, req.choice,
                     req.confidence, req.reason)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@app.get("/diagnostic/{session_id}/report")
def diagnostic_report(session_id: str):
    from quiz.diagnosis import report
    try:
        return report(session_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/stats")
def api_stats(thread_id: str):
    from quiz import get_stats
    return get_stats(thread_id)


@app.get("/api/attributions")
def api_attributions(status: str | None = None, limit: int = Query(default=100, ge=1, le=500)):
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
):
    """按稳定题号游标分页的纠错审阅列表，应用指定线程的有效纠错。"""
    from storage import repos

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
def api_attribution_correction(question_id: str, req: AttributionCorrectionRequest):
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
):
    """线程级不可变纠错事件；before_id 用于按事件 ID 倒序完整导出。"""
    from storage import repos

    return repos.attribution_corrections(
        thread_id,
        limit=limit,
        before_id=before_id,
    )


@app.get("/api/attribution-observability")
def api_attribution_observability(
    thread_id: str = Query(min_length=1, max_length=64),
    since: float | None = Query(default=None, ge=0),
):
    """线程级纠错聚合，仅用于灰度运行观测，不是准确率指标。"""
    from storage import repos

    return repos.attribution_correction_observability(thread_id, since=since)


@app.get("/api/knowledge-points")
def api_knowledge_points(status: str = "active"):
    if status not in {"active", "candidate"}:
        raise HTTPException(status_code=422, detail="Invalid knowledge-point status")
    from storage import repos
    return [
        point for point in repos.load_knowledge_points()
        if point["status"] == status
    ]


@app.get("/api/domains")
def api_domains():
    from domains import DOMAINS
    return DOMAINS


@app.get("/api/history")
def api_history(thread_id: str, limit: int = 50):
    """某会话的落盘历史（重启后仍可查，供会话恢复/续聊）。"""
    from agents.memory import load_history
    return load_history(thread_id, limit=limit)


@app.get("/api/threads")
def api_threads(limit: int = 20):
    """历史会话列表（最近优先）。"""
    from agents.memory import list_threads
    return list_threads(limit=limit)


@app.get("/api/metrics")
def api_metrics():
    """请求级指标聚合：阶段耗时分位数、Token 用量、意图分布。"""
    from agents.metrics import summary
    return summary()


@app.get("/api/document")
def api_document(source: str = Query(min_length=1), page: int = Query(ge=1)):
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


@app.get("/api/ready")
def readiness():
    from data_ingest.manifest import validate_index
    from storage import db

    schema = db.schema_status()
    try:
        validate_index()
        assets = {"ready": True, "error": None}
    except Exception as exc:
        assets = {"ready": False, "error": str(exc)}
    ready = schema["migration_current"] and assets["ready"]
    if ready:
        status = "ready"
    elif not schema["migration_current"]:
        status = "migration_required"
    else:
        status = "assets_invalid"
    payload = {
        "status": status,
        "schema": schema,
        "assets": assets,
    }
    return JSONResponse(payload, status_code=200 if ready else 503)

# ── 静态面板 ────────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    config.validate()
    from data_ingest.manifest import validate_index
    validate_index()  # 启动自检：数据资产齐全且与 manifest 一致
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9528, log_level="info")
