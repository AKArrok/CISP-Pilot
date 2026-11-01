"""AgentState — LangGraph 图共享状态。"""
from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages


class AgentState(TypedDict):
    # 会话线程 id（短期缓存/长期向量记忆用）
    thread_id: str
    # 消息历史（多轮上下文，MemorySaver 按 thread_id 维护当前运行态）
    messages: Annotated[list[AnyMessage], add_messages]
    # 原始用户问题
    question: str
    # 意图: knowledge(知识问答) | quiz(想做题) | chitchat(闲聊)
    intent: Literal["knowledge", "quiz", "chitchat"]
    # 问题所属知识域（route 节点顺带判定；闲聊/无法判定为 None）。
    # 写入 question_signals 作为问答侧薄弱信号，与刷题画像融合。
    domain: str | None
    # 改写后的独立检索查询（追问消解后）
    search_query: str
    # 检索查询变体（RAG-Fusion 式多路召回：完整问句版/关键词纠错版）
    query_variants: list[str]
    # 检索到的知识块
    contexts: list[dict]
    # 实验证据子代理报告；每轮检索重置，模型缺口判断不等于人工验收。
    evidence_report: dict | None
    # 从长期向量记忆召回的历史对话片段
    memories: list[dict]
    # 最终回答
    answer: str
    # 回答 LLM 的 Token 用量（可观测性）
    usage: dict
