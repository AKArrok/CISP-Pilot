"""抽题：random（均匀）/ weak（薄弱域 1-正确率 加权）。"""
from __future__ import annotations

import json
import random

import config
from quiz import store


class QuestionBank:
    """题库统一视图：优先读数据库（questions 表，真题与 AI 题同表），
    表为空时回退 JSON 文件（零配置场景）。"""

    _instance = None

    def __init__(self):
        self._questions = self._load()

    @staticmethod
    def _load() -> list[dict]:
        from storage import repos
        items = repos.load_questions()
        if items:
            return items
        with open(config.QUESTIONS_PATH, encoding="utf-8") as fp:
            return json.load(fp)

    @classmethod
    def get(cls) -> "QuestionBank":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def reload(self) -> None:
        self._questions = self._load()

    def get_by_id(self, question_id: str) -> dict | None:
        return next((q for q in self._questions if q["id"] == question_id), None)

    def available(
        self, domain: str | None = None, *, thread_id: str = "default",
    ) -> list[dict]:
        """可出题目：有效（题干/选项/答案齐全）、非重复题；默认排除已答对的题。"""
        done_correct = store.attempted_ids(thread_id, correct_only=True)
        pool = [
            q for q in self._questions
            if q["answer"] and not q.get("needs_review") and not q.get("dup_of")
        ]
        if domain:
            pool = [q for q in pool if q["domain"] == domain]
        fresh = [q for q in pool if q["id"] not in done_correct]
        return fresh or pool  # 全部做过时允许复做

    def domains(self) -> list[str]:
        return sorted({q["domain"] for q in self._questions if q["domain"]})

    def all(self, domain: str | None = None) -> list[dict]:
        """全量题（含 needs_review/dup_of），查重与审校等内部用途。"""
        if not domain:
            return list(self._questions)
        return [q for q in self._questions if q.get("domain") == domain]


def domain_weights(
    bank: QuestionBank, domain: str | None, *, thread_id: str = "default",
) -> dict[str, float]:
    """weak 模式: 权重 = Elo 缺口 + 薄弱加成 + 复习到期加成 + 提问信号加成。"""
    from quiz.stats import domain_profiles
    from storage import repos

    profiles = domain_profiles(thread_id)
    asks = {d: n for d, n, _ in repos.signal_rows(thread_id)}
    domains = [d for d in bank.domains() if (not domain or d == domain)]
    weights = {}
    for d in domains:
        p = profiles.get(d)
        if p is None:
            w = 0.5
        else:
            # Elo 缺口：rating 越低越该练（rating 1000 → 0.5，满分 2000 → 0）
            w = max(0.1, (2000 - p["rating"]) / 2000)
            if p["weak"]:
                w *= 1.8          # 薄弱域显著提权
            if p["due_for_review"]:
                w *= 1.3          # 复习到期提权（防遗忘）
        # 提问信号：反复追问但答题样本不足/薄弱的域 → "哪里不会问哪里"提权。
        # 抬到不低于无记录域基线 0.5（Elo 初始分的缺口公式只有 0.25，不抬会被压过）
        if asks.get(d, 0) >= config.ASK_SIGNAL_MIN and (
                p is None or p["insufficient"] or p["weak"]):
            w = max(w, 0.5) * config.ASK_WEIGHT_BONUS
        weights[d] = w
    total = sum(weights.values()) or 1.0
    return {d: w / total for d, w in weights.items()}


def _pick_due_card(
    bank: QuestionBank, domain: str | None, thread_id: str,
) -> dict | None:
    """FSRS 到期复习卡优先：最久未复习的到期题先练。无到期卡返回 None。"""
    if not config.ENABLE_FSRS_REVIEW:
        return None
    try:
        from quiz import review
        due_ids = [
            row.question_id
            for row in review.due_rows(thread_id, domain, limit=20)
        ]
    except Exception:
        return None
    if not due_ids:
        return None
    by_id = {q["id"]: q for q in bank.available(domain, thread_id=thread_id)}
    candidates = [by_id[qid] for qid in due_ids if qid in by_id]
    return random.choice(candidates) if candidates else None


def next_question(
    mode: str = "random", domain: str | None = None, *, thread_id: str = "default",
) -> dict | None:
    """抽一道题。weak 模式先消耗 FSRS 到期复习卡，无到期卡再按薄弱加权抽域、域内均匀抽题。"""
    bank = QuestionBank.get()
    if mode == "weak":
        due = _pick_due_card(bank, domain, thread_id)
        if due is not None:
            return due
        weights = domain_weights(bank, domain, thread_id=thread_id)
        if not weights:
            return None
        domains, probs = list(weights.keys()), list(weights.values())
        picked_domain = random.choices(domains, weights=probs, k=1)[0]
        pool = bank.available(picked_domain, thread_id=thread_id)
    else:
        pool = bank.available(domain, thread_id=thread_id)
    return random.choice(pool) if pool else None
