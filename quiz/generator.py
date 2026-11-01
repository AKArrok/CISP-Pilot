"""AI 补充出题：检索该域知识块 → LLM 生成单选题 → 质量校验 → 落库。

定向出题：weak 模式下以用户最近答错的题为锚点，围绕同一考点的易混淆
概念出新题（而不是固定"{域名} 核心考点"盲抽）。质量控制三道闸：
1. 结构校验（答案合法、选项非空且互异）
2. 近似查重（题干前缀 + 同域题干 SequenceMatcher 相似度）
3. LLM 自校验（答案可由资料推出/干扰项互斥/题干无歧义），不过则标
   needs_review=True 留档、不进入抽题池
"""
from __future__ import annotations

import difflib
import logging

from pydantic import BaseModel
from langchain_core.messages import HumanMessage, SystemMessage

from domains import normalize_for_match
import config
from llms import get_answer_llm, get_simple_llm, invoke_structured
from quiz import store
from quiz.selector import QuestionBank

logger = logging.getLogger(__name__)


class GeneratedQuestion(BaseModel):
    stem: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    answer: str      # 'A'-'D'
    analysis: str


class VerifyOut(BaseModel):
    valid: bool
    reason: str


GEN_SYSTEM = """你是 CISP 考试出题人。依据给定的课件知识块出 1 道单选题，要求:
1. 考点必须完全来自资料内容，不得编造
2. 风格贴近 CISP 真题（"以下哪一项…""关于…说法正确的是"）
3. 四个选项干扰项要有合理性，答案唯一且在 A-D 中
4. analysis 给出答案依据（引用资料要点）
只输出 JSON。"""

GEN_ANCHOR_TMPL = """参考下面这道公共题目（正确答案 {answer}）:
【原题】{stem}
【原题解析】{analysis}

请围绕同一考点的核心概念或易混淆点再出 1 道新单选题：考察同一知识点但换角度，
不得与原题雷同；针对知识点的易混淆概念设置干扰项，不描述任何用户记录。"""

VERIFY_SYSTEM = """你是 CISP 试题审校员。依据给定的课件资料审查一道单选题，判定:
1. 标注的正确答案能否从资料内容直接推出（资料未覆盖 → 不合格）
2. 四个选项表述是否互不重复、错误选项是否明确错误（有两个可选答案 → 不合格）
3. 题干是否表述清晰、无歧义
只输出 JSON: {"valid": true/false, "reason": "简短理由"}。"""


def _looks_duplicate(stem: str, domain: str | None) -> bool:
    """近似查重：40 字前缀精确比对 + 同域题干相似度比对（拦换说法的雷同题）。"""
    key = normalize_for_match(stem)[:40]
    bank = QuestionBank.get()
    pool = bank.all(domain)
    for q in pool:
        q_norm = normalize_for_match(q["stem"])
        if q_norm[:40] == key:
            return True
        if difflib.SequenceMatcher(None, normalize_for_match(stem), q_norm).ratio() \
                >= config.QUIZ_DUP_SIMILARITY:
            return True
    return stem in store.existing_ai_stems()


def _structural_ok(out: GeneratedQuestion) -> bool:
    """结构校验：答案合法、题干/选项非空、选项互异。"""
    answer = out.answer.strip().upper()[:1]
    if answer not in "ABCD" or not out.stem.strip():
        return False
    options = [out.option_a, out.option_b, out.option_c, out.option_d]
    texts = [str(o).strip() for o in options]
    return all(texts) and len(set(texts)) == 4


def _verify(out: GeneratedQuestion, material: str) -> bool:
    """LLM 自校验；校验器故障时放行（宁可有瑕疵的题也不让出题链路瘫痪）。"""
    if not config.QUIZ_VERIFY_ENABLED:
        return True
    try:
        simple = get_simple_llm()
        q_text = (f"题干: {out.stem}\n"
                  f"A. {out.option_a}\nB. {out.option_b}\n"
                  f"C. {out.option_c}\nD. {out.option_d}\n"
                  f"标注答案: {out.answer}\n解析: {out.analysis}")
        result = invoke_structured(simple, VerifyOut, [
            SystemMessage(content=VERIFY_SYSTEM),
            HumanMessage(content=f"课件资料:\n{material[:3000]}\n\n待审题目:\n{q_text}"),
        ])
        return bool(result.valid)
    except Exception:
        logger.exception("AI 题自校验失败，放行（人工兜底走 needs_review 流程）")
        return True


def _anchor_from_question(q: dict, wrong_choice: str | None) -> dict:
    return {
        "stem": q["stem"],
        "wrong_choice": wrong_choice or "（未知）",
        "answer": q.get("answer", ""),
        "analysis": (q.get("analysis") or "")[:300],
    }


def generate_question(domain: str, anchor: dict | None = None) -> dict | None:
    """为指定知识域生成一道新题；anchor 为用户答错的锚点题时定向出题。

    与现有题库查重 + 自校验，未通过则不返回（自校验不过的题标 needs_review 留档）。
    """
    from retrieval.hybrid import HybridRetriever

    if anchor:
        query = f"{domain} {anchor['stem']}"[:200]
        anchor_block = GEN_ANCHOR_TMPL.format(**anchor)
    else:
        query = f"{domain} 核心考点"
        anchor_block = ""
    chunks = HybridRetriever.get().retrieve(query, top_k=3, domain=domain)
    if not chunks:
        return None
    context_text = "\n\n".join(c["text"][:600] for c in chunks)
    llm = get_answer_llm(temperature=0.5)
    messages = [
        SystemMessage(content=GEN_SYSTEM),
        HumanMessage(content=f"知识域: {domain}\n\n{anchor_block}\n\n课件资料:\n{context_text}"),
    ]
    out = None
    for attempt in range(2):  # DeepSeek 结构化输出偶发 400，重试一次
        try:
            out = invoke_structured(llm, GeneratedQuestion, messages)
            break
        except Exception:
            logger.exception("AI 出题失败 (第%d次)", attempt + 1)
    if out is None:
        return None
    if not _structural_ok(out):
        logger.info("AI 题结构校验不过，丢弃: %s", out.stem[:50])
        return None

    stem = out.stem.strip()
    if _looks_duplicate(stem, domain):
        return None
    if not _verify(out, context_text):
        # 自校验不过：落库留档（needs_review 不会进抽题池），不出给用户
        options = {"A": out.option_a, "B": out.option_b, "C": out.option_c, "D": out.option_d}
        store.insert_ai_question(stem, options, out.answer.strip().upper()[:1],
                                 out.analysis.strip(), domain, needs_review=True)
        logger.info("AI 题自校验不过，标 needs_review 留档: %s", stem[:50])
        return None

    answer = out.answer.strip().upper()[:1]
    options = {"A": out.option_a, "B": out.option_b, "C": out.option_c, "D": out.option_d}
    qid = store.insert_ai_question(stem, options, answer, out.analysis.strip(), domain)
    QuestionBank.get().reload()
    return {
        "id": qid, "source": "AI生成", "num": 0, "stem": stem,
        "options": options, "answer": answer, "analysis": out.analysis.strip(),
        "domain": domain, "needs_review": False,
    }
