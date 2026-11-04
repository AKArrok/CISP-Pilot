"""BM25 稀疏检索（jieba 分词），从 kb_chunks.json 内存构建，无需外部索引。"""
from __future__ import annotations

import math
import re
from collections import Counter

import jieba

jieba.setLogLevel(60)  # 静音建词日志

_STOPWORDS = {"的", "了", "和", "是", "在", "与", "及", "或", "一个", "以下", "以下哪一项", "下列",
              "哪些", "哪一种", "哪项", "对于", "关于", "通过", "进行", "可以", "能够", "主要",
              "是否", "所有", "必须"}

# 中文序数等级归一：第三级/三级/3级 → 3级。必须在做 jieba 分词前的文本层完成——
# jieba 把 "3级" 拆成 "3"+"级"，把 "第三级" 当整 token，三种写法在词法上永不相交。
_CN_LEVEL_RE = re.compile(r"第?([一二三四五六七八九十]+)(?=级)")
_CN_DIGITS = {"一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5",
              "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


def normalize_cn_levels(text: str) -> str:
    def repl(match: re.Match) -> str:
        raw = match.group(1)
        if len(raw) == 1:
            return _CN_DIGITS.get(raw, raw)
        if "十" in raw:  # 十→10、十五→15、三十→30
            left, _, right = raw.partition("十")
            tens = _CN_DIGITS.get(left, "1") if left else "1"
            ones = _CN_DIGITS.get(right, "") if right else "0"
            return tens + ones
        return raw

    return _CN_LEVEL_RE.sub(repl, text)


def tokenize(text: str) -> list[str]:
    words = jieba.lcut(normalize_cn_levels(text.lower()))
    # \W 只丢单个标点；单个数字保留——"赋值为5分"的"5"是有效检索词，之前被当成噪声丢弃
    return [w for w in words if w.strip() and w not in _STOPWORDS and not re.fullmatch(r"\W", w)]


def _word_tokens_to_bigrams(tokens: list[str]) -> list[str]:
    bigrams: list[str] = []
    for w in tokens:
        if len(w) >= 2 and re.fullmatch(r"[\u4e00-\u9fff]{2,}", w):
            bigrams.extend(w[i:i + 2] for i in range(len(w) - 1))
    return bigrams


def tokenize_bigrams(text: str) -> list[str]:
    """汉字二元词通道：错别字打断整词匹配（"威胁请报"）时，相邻 bigram 大部分仍对得上。"""
    return _word_tokens_to_bigrams(tokenize(text))


class _BM25Core:
    """单通道 BM25 打分（词级或 bigram 级共用）。"""

    def __init__(self, docs_tokens: list[list[str]]):
        self.doc_lens = [len(d) for d in docs_tokens]
        self.avgdl = sum(self.doc_lens) / max(len(docs_tokens), 1)
        self.tfs = [Counter(d) for d in docs_tokens]
        df: Counter = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        n = len(docs_tokens)
        # BM25+ 风格平滑 IDF
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def score(self, q_tokens: list[str]) -> list[float]:
        scores = []
        k1, b = 1.5, 0.75
        for i, tf in enumerate(self.tfs):
            if not tf:
                scores.append(0.0)
                continue
            s = 0.0
            dl = self.doc_lens[i]
            for t in q_tokens:
                if t in tf:
                    s += self.idf.get(t, 0.0) * tf[t] * (k1 + 1) / (
                        tf[t] + k1 * (1 - b + b * dl / self.avgdl)
                    )
            scores.append(s)
        return scores


class BM25Index:
    """词级 BM25 + 错别字兜底通道。

    常规查询（查询词全部在语料词表中）走纯词级打分，排序零扰动；
    查询含词表外词（错别字的特征信号）时切换混词打分（词+bigram），
    用相邻二元词的部分匹配救回被打断的词法召回（docs/05 §3.7 模拟用户评测）。
    """

    _OOV_BLEND_COVERAGE = 0.99

    def __init__(self, docs_tokens: list[list[str]], metadata: list[dict]):
        self.metadata = metadata
        self._word = _BM25Core(docs_tokens)
        # 降级通道用混词打分（词 + bigram 单一分数）：typo 实测优于双通道 RRF
        self._mixed = _BM25Core([t + _word_tokens_to_bigrams(t) for t in docs_tokens])
        self._vocab = {t for tokens in docs_tokens for t in tokens if len(t) >= 2}

    @classmethod
    def build(cls, chunks: list[dict]) -> "BM25Index":
        return cls([
            tokenize(c.get("embedding_text", c["text"])) for c in chunks
        ], chunks)

    def _is_degraded(self, q_tokens: list[str]) -> bool:
        """查询含语料外词（≥2 字）视为词法通道退化——错别字的特征信号。"""
        probe = [t for t in set(q_tokens) if len(t) >= 2]
        if not probe:
            return False
        hit = sum(1 for t in probe if t in self._vocab)
        return hit / len(probe) < self._OOV_BLEND_COVERAGE

    def search(self, query: str, top_k: int) -> list[dict]:
        q_tokens = tokenize(query)
        word_scores = self._word.score(q_tokens)
        if not self._is_degraded(q_tokens):
            ranked = sorted(range(len(word_scores)), key=lambda i: -word_scores[i])[:top_k]
            return [
                {**self.metadata[i], "score": word_scores[i], "word_score": word_scores[i]}
                for i in ranked if word_scores[i] > 0
            ]
        mixed_scores = self._mixed.score(q_tokens + tokenize_bigrams(query))
        ranked = sorted(range(len(mixed_scores)), key=lambda i: -mixed_scores[i])[:top_k]
        return [
            {**self.metadata[i], "score": mixed_scores[i], "word_score": word_scores[i]}
            for i in ranked if mixed_scores[i] > 0
        ]
