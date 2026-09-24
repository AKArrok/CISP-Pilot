"""LangGraph 主图: route → rewrite → retrieve → answer。

CISP 问答以单点知识问答为主，采用「路由 → 追问改写 → 混合检索 → 长期记忆召回 → 流式回答」
的多阶段流水线；刻意不引入 Expert 并行/评估重规划等复杂编排——单点事实问答下收益低、延迟成本高。
"""
from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

import config
from agents.state import AgentState
from agents import prompts
from domains import DOMAINS
from llms import get_answer_llm, get_simple_llm, llm_ainvoke_with_retry, invoke_structured

logger = logging.getLogger(__name__)

NO_ANSWER = "知识库中未覆盖该问题。你可以换个问法，或先在「刷题」页签练习对应知识域的题目。"


def _below_relevance_floor(contexts: list[dict]) -> bool:
    """相关性门限：检索结果的最高 dense 余弦低于门限时拒答，跳过回答 LLM。

    检索端没有"相关与否"的判断，弱相关块几乎总会进上下文，此前拒答完全依赖
    回答 prompt 的自我裁定。门限用 gold-47 / 离题集 / 域边界题校准（docs/05 §3.2）；
    结果缺 dense_score（旧缓存、BM25-only）时保守放行。
    """
    if not config.ENABLE_RELEVANCE_GATE:
        return False
    scores = [c.get("dense_score") for c in contexts if c.get("dense_score") is not None]
    if not scores:
        return False
    return max(scores) < config.RELEVANCE_FLOOR


# ── 节点 ────────────────────────────────────────────────────────────────

class RouteOut(BaseModel):
    intent: str  # knowledge | quiz | chitchat
    domain: str | None = None  # knowledge 意图下的 CISP 知识域（薄弱信号）


class VariantsOut(BaseModel):
    variants: list[str] = Field(description="2 个检索变体：完整问句版、关键词版")


async def route(state: AgentState) -> dict:
    """意图路由（轻量 LLM + 结构化输出，顺带判定知识域）。同步 OpenAI 调用放线程池，避免阻塞事件循环。"""
    question = state["question"]
    simple = get_simple_llm()
    try:
        out = await asyncio.to_thread(
            invoke_structured, simple, RouteOut,
            [SystemMessage(content=prompts.ROUTE_SYSTEM), HumanMessage(content=question)],
        )
        intent = out.intent if out.intent in ("knowledge", "quiz", "chitchat") else "knowledge"
    except Exception:
        logger.exception("路由失败，默认走知识问答")
        intent = "knowledge"
        out = None
    domain = getattr(out, "domain", None) if out else None
    if domain not in DOMAINS:
        # LLM 没判出来时用关键词规则兜底，尽量不丢提问信号
        from domains import classify_domain_by_keywords
        domain = classify_domain_by_keywords(question) if intent == "knowledge" else None
    return {"intent": intent, "domain": domain}


async def chitchat(state: AgentState) -> dict:
    from langchain_core.messages import AIMessage
    reply = "你好！我是 CISP 备考助手。可以直接问我知识点（比如：什么是深度防御），也可以去「刷题」页签练习真题。"
    # contexts 置空：防止上一轮的检索结果残留在会话状态里
    return {"messages": [AIMessage(content=reply)], "answer": reply,
            "intent": "chitchat", "contexts": []}


async def quiz_hint(state: AgentState) -> dict:
    from langchain_core.messages import AIMessage

    hint = prompts.QUIZ_HINT
    # 带上画像里当前的薄弱/待复习提示，让引导有指向性
    try:
        from quiz import review
        from quiz.stats import weak_domains
        thread_id = state.get("thread_id", "default")
        weak = weak_domains(thread_id)
        due_cards = review.due_count(thread_id)
        tips = []
        if weak:
            tips.append(f"当前薄弱域：{'、'.join(weak[:3])}")
        if due_cards:
            tips.append(f"有 {due_cards} 道到期复习题")
        if tips:
            hint += "\n" + "；".join(tips) + "，建议用「薄弱强化」模式开始。"
    except Exception:
        logger.exception("刷题引导画像查询失败，使用默认话术")
    return {"messages": [AIMessage(content=hint)], "answer": hint,
            "contexts": []}


def _persist_sync(state: AgentState) -> None:
    """persist 的同步落盘体（SQL + 长期记忆 embedding），运行在线程池。"""
    from agents import memory

    answer = state.get("answer", "")
    if answer:
        try:
            memory.save_round(state.get("thread_id", "default"),
                              state["question"], answer, state["intent"])
        except Exception:
            logger.exception("对话落盘失败（不影响回答）")
    # 提问信号：用户问了什么域 → question_signals 表，供画像融合（"哪里不会问哪里"）
    if state.get("intent") == "knowledge":
        try:
            from storage import repos
            repos.record_question_signal(
                state.get("thread_id", "default"), state["question"],
                state.get("domain"), "knowledge")
        except Exception:
            logger.exception("提问信号写入失败（不影响回答）")


async def persist(state: AgentState) -> dict:
    """记忆持久化：同步落盘（含长期记忆的外部 embedding 调用）放线程池。

    必须避免在 async 节点里直接做同步外部调用——embedding 请求一旦挂起
    会阻塞整个事件循环，SSE 流卡死不发 done（e2e 冒烟发现的真 bug）。
    """
    await asyncio.to_thread(_persist_sync, state)
    return {}


async def rewrite(state: AgentState) -> dict:
    """追问改写 + 查询变体：结合历史把问题改写为独立检索查询，并生成多路变体。"""
    history = state.get("messages", [])[:-1]  # 去掉刚追加的本轮问题
    question = state["question"]
    search_query = question
    if history:
        simple = get_simple_llm()
        try:
            hist_text = "\n".join(
                f"{'用户' if isinstance(m, HumanMessage) else '助手'}: {str(m.content)[:200]}"
                for m in history[-4:]
            )
            out = await llm_ainvoke_with_retry(simple, [
                SystemMessage(content=prompts.REWRITE_SYSTEM),
                HumanMessage(content=f"对话历史:\n{hist_text}\n\n当前问题: {question}"),
            ])
            rewritten = str(out.content).strip()
            if rewritten and len(rewritten) <= 200:
                search_query = rewritten
        except Exception:
            logger.exception("追问改写失败，使用原始问题")

    # 查询变体（RAG-Fusion 式多路召回）：完整问句版/关键词版，首单轮也生成
    # （顺带纠正错别字），失败时静默降级为单查询检索。
    query_variants: list[str] = []
    if config.ENABLE_QUERY_VARIANTS:
        simple = get_simple_llm()
        try:
            out = await asyncio.to_thread(
                invoke_structured, simple, VariantsOut,
                [SystemMessage(content=prompts.QUERY_VARIANTS_SYSTEM),
                 HumanMessage(content=question)],
            )
            query_variants = [v.strip() for v in out.variants
                              if v.strip() and v.strip() != question][:2]
        except Exception:
            logger.exception("查询变体生成失败，降级为单查询检索")
    return {"search_query": search_query, "query_variants": query_variants}


async def retrieve(state: AgentState) -> dict:
    from retrieval.hybrid import HybridRetriever

    # 上一轮命中的知识页（checkpointer 按会话保留）：多轮追问时特征重排
    # 会对知识邻域页小幅加成——追问通常落在上一轮内容的知识邻域。
    prev_pages = [(c["source"], c["page"])
                  for c in (state.get("contexts") or []) if c.get("page") is not None]
    # 本地 CPU embedding + 检索是同步重计算，放线程池避免阻塞事件循环
    contexts = await asyncio.to_thread(
        HybridRetriever.get().retrieve, state["search_query"], config.RETRIEVER_K,
        None, prev_pages, state.get("query_variants") or [])
    return {"contexts": contexts}


async def recall_memory(state: AgentState) -> dict:
    """长期向量记忆：按当前问题召回同 thread 的相关历史轮次。"""
    from agents import memory

    memories = await asyncio.to_thread(
        memory.search_long_memory,
        state["question"],
        state.get("thread_id", "default"),
        config.LONG_MEMORY_TOP_K,
    )
    return {"memories": memories}


async def answer(state: AgentState) -> dict:
    contexts = state.get("contexts", [])
    if not contexts or _below_relevance_floor(contexts):
        return {
            "messages": [],
            "answer": NO_ANSWER,
        }
    context_text = "\n\n".join(
        f"<chunk id={i} 来源《{c['source']}》第{c['page']}页 知识域[{c['domain']}]>\n{c['text']}\n</chunk>"
        for i, c in enumerate(contexts, 1)
    )
    # 传入最近几轮对话背景，供回答时理解指代（回答内容仍以资料为准）
    history = state.get("messages", [])[:-1]
    history_text = ""
    if history:
        lines = "\n".join(
            f"{'用户' if isinstance(m, HumanMessage) else '助手'}: {str(m.content)[:300]}"
            for m in history[-4:]
        )
        history_text = f"\n\n对话背景（仅用于理解指代，回答内容仍必须依据资料）:\n{lines}"
    memories = state.get("memories", [])
    memory_text = ""
    if memories:
        lines = "\n".join(
            f"- {m.get('content', '')[:500]}"
            for m in memories
        )
        memory_text = f"\n\n长期记忆（仅用于延续用户偏好和已讨论背景，不可替代资料依据）:\n{lines}"
    llm = get_answer_llm()
    messages = [
        SystemMessage(content=prompts.ANSWER_SYSTEM),
        HumanMessage(content=prompts.ANSWER_CONTEXT_TEMPLATE.format(
            contexts=context_text, question=state["question"]) + history_text + memory_text),
    ]
    resp = await llm_ainvoke_retry(llm, messages)
    usage_meta = getattr(resp, "usage_metadata", None) or {}
    usage = {
        "prompt_tokens": usage_meta.get("input_tokens", 0),
        "completion_tokens": usage_meta.get("output_tokens", 0),
    }
    return {"messages": [resp], "answer": str(resp.content), "usage": usage}


async def llm_ainvoke_retry(llm, messages):
    from llms import llm_ainvoke_with_retry
    return await llm_ainvoke_with_retry(llm, messages)


# ── 路由函数 ────────────────────────────────────────────────────────────

def route_after_intent(state: AgentState) -> str:
    if state["intent"] == "chitchat":
        return "chitchat"
    if state["intent"] == "quiz":
        return "quiz_hint"
    return "rewrite"


# ── 图构建 ──────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(AgentState)
    g.add_node("route", route)
    g.add_node("chitchat", chitchat)
    g.add_node("quiz_hint", quiz_hint)
    g.add_node("rewrite", rewrite)
    g.add_node("retrieve", retrieve)
    g.add_node("recall_memory", recall_memory)
    g.add_node("answer", answer)
    g.add_node("persist", persist)

    g.add_edge(START, "route")
    g.add_conditional_edges(
        "route", route_after_intent,
        {"chitchat": "chitchat", "quiz_hint": "quiz_hint", "rewrite": "rewrite"},
    )
    g.add_edge("rewrite", "retrieve")
    g.add_edge("retrieve", "recall_memory")
    g.add_edge("recall_memory", "answer")
    # 三条出口统一经 persist 落盘长期记忆再结束
    g.add_edge("answer", "persist")
    g.add_edge("chitchat", "persist")
    g.add_edge("quiz_hint", "persist")
    g.add_edge("persist", END)
    return g


class SessionStore:
    """同一 thread_id 共享 MemorySaver 与已编译图实例（thread 间隔离）。"""

    _instances: dict[str, tuple] = {}

    @classmethod
    def get(cls, thread_id: str):
        if thread_id not in cls._instances:
            memory = MemorySaver()
            app = build_graph().compile(checkpointer=memory)
            cls._instances[thread_id] = (app, thread_id)
        return cls._instances[thread_id][0]


async def run(question: str, thread_id: str = "default") -> dict:
    """一次性执行（CLI/测试用）。server.py 用 astream_events 做流式。"""
    app = SessionStore.get(thread_id)
    config_ = {"configurable": {"thread_id": thread_id}}
    state = await app.aget_state(config_)
    messages = [HumanMessage(content=question)]
    if not (state.values or {}).get("messages"):
        from agents import memory
        from langchain_core.messages import AIMessage

        restored = []
        for row in memory.load_short_context(thread_id, limit=config.MEMORY_MAX_ROUNDS * 2):
            cls = HumanMessage if row.get("role") == "user" else AIMessage
            restored.append(cls(content=row.get("content", "")))
        messages = restored + messages
    result = await app.ainvoke(
        {"messages": messages, "question": question,
         "thread_id": thread_id},
        config=config_,
    )
    return result


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "什么是深度防御？"
    result = asyncio.run(run(q, thread_id="cli"))
    print(f"[意图] {result['intent']}")
    print(f"[检索] {len(result.get('contexts', []))} 块")
    print(result.get("answer", ""))
