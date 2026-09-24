"""LLM and embedding instances — OpenAI-compatible protocol layer (no Pinecone/Tavily/local models)."""
import hashlib
import httpx
import json
import logging
import os
import random
import tempfile
import threading
import time
from typing import List, Type, TypeVar

from openai import OpenAI
from langchain_openai import ChatOpenAI
from langchain_core.embeddings import Embeddings
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import APIError, APITimeoutError, RateLimitError
import config

T = TypeVar("T", bound=BaseModel)

def _write_json_atomically(path: str, payload) -> None:
    """Write JSON via a unique sibling file; retry transient Windows replace locks."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    descriptor, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as fp:
            json.dump(payload, fp)
        for attempt in range(4):
            try:
                os.replace(tmp_path, path)
                return
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.02 * (2 ** attempt))
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                logging.warning("无法清理缓存临时文件: %s", tmp_path)

# ══════════════════════════════════════════════════════════════════════
# LLM Factory
# ══════════════════════════════════════════════════════════════════════

_answer_llm: ChatOpenAI | None = None
_simple_llm: ChatOpenAI | None = None


def get_answer_llm(**overrides) -> ChatOpenAI:
    global _answer_llm
    if _answer_llm is None or overrides:
        kwargs = dict(
            base_url=overrides.pop("base_url", config.LLM_BASE_URL),
            api_key=overrides.pop("api_key", config.LLM_API_KEY),
            model=overrides.pop("model", config.LLM_MODEL),
            temperature=overrides.pop("temperature", 0.3),
            request_timeout=60,
            max_retries=2,
        )
        kwargs.update(overrides)
        _answer_llm = ChatOpenAI(**kwargs)
    return _answer_llm


def get_simple_llm(**overrides) -> ChatOpenAI:
    global _simple_llm
    if _simple_llm is None or overrides:
        kwargs = dict(
            base_url=overrides.pop("base_url", config.LLM_BASE_URL),
            api_key=overrides.pop("api_key", config.LLM_API_KEY),
            model=overrides.pop("model", config.SIMPLE_LLM_MODEL),
            temperature=overrides.pop("temperature", 0.2),
            request_timeout=45,
            max_retries=2,
        )
        kwargs.update(overrides)
        _simple_llm = ChatOpenAI(**kwargs)
    return _simple_llm


def reset_llms() -> None:
    global _answer_llm, _simple_llm
    _answer_llm = None
    _simple_llm = None


# ══════════════════════════════════════════════════════════════════════
# LLM 调用重试 - 指数退避
# ══════════════════════════════════════════════════════════════════════

_RETRYABLE = (APIError, APITimeoutError, RateLimitError)


def _make_retry(max_attempts: int = 3):
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )


def llm_invoke_with_retry(llm: ChatOpenAI, messages: list[BaseMessage],
                          max_retries: int = 3) -> BaseMessage:
    return _make_retry(max_retries)(llm.invoke)(messages)


async def llm_ainvoke_with_retry(llm: ChatOpenAI, messages: list[BaseMessage],
                                 max_retries: int = 3) -> BaseMessage:
    @_make_retry(max_retries)
    async def _call():
        return await llm.ainvoke(messages)
    return await _call()


# ══════════════════════════════════════════════════════════════════════
# 结构化输出 - 自动降级（with_structured_output -> JSON fallback）
# ══════════════════════════════════════════════════════════════════════

def invoke_structured(llm: ChatOpenAI, output_class: Type[T],
                      messages: list[BaseMessage],
                      max_retries: int = 3) -> T:
    try:
        structured_llm = llm.with_structured_output(output_class)
        return llm_invoke_with_retry(structured_llm, messages, max_retries=max_retries)
    except Exception as e:
        err_str = str(e).lower()
        if "response_format" in err_str or "unavailable" in err_str:
            logging.info("  [降级] with_structured_output 不可用，改用 JSON 模式")
            return _json_fallback_invoke(llm, output_class, messages, max_retries)
        raise


def _json_fallback_invoke(llm: ChatOpenAI, output_class: Type[T],
                          messages: list[BaseMessage],
                          max_retries: int = 3) -> T:
    schema_json = json.dumps(output_class.model_json_schema(), ensure_ascii=False)
    prompt_text = (
        f"请严格按照以下 JSON Schema 输出，不要包含额外文字，不要用 markdown 代码块包裹:\n"
        f"{schema_json}"
    )
    try:
        json_llm = llm.bind(response_format={"type": "json_object"})
        resp = llm_invoke_with_retry(
            json_llm, [*messages, HumanMessage(content=prompt_text)],
            max_retries=max_retries,
        )
    except Exception:
        resp = llm_invoke_with_retry(
            llm,
            [*messages, SystemMessage(
                content="你只输出 JSON，不要包含任何解释、markdown 标记或额外文字。"
            )],
            max_retries=max_retries,
        )

    text = resp.content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return output_class.model_validate_json(text)


# ══════════════════════════════════════════════════════════════════════
# Ark Coding Plan Embeddings（含限流退避）
# ══════════════════════════════════════════════════════════════════════

class ArkCodingEmbeddings(Embeddings):
    """火山方舟 Coding Plan 的 doubao-embedding-vision 客户端。"""

    def __init__(self, api_key: str, base_url: str, model: str, dimension: int):
        if not api_key:
            raise EnvironmentError("ARK_EMBEDDING_API_KEY is required when EMBEDDING_BACKEND=ark")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.dimension = dimension
        self.request_interval = config.ARK_EMBEDDING_REQUEST_INTERVAL
        self.max_retries = config.ARK_EMBEDDING_MAX_RETRIES
        self.max_backoff = config.ARK_EMBEDDING_MAX_BACKOFF
        self._last_request_at = 0.0

    @property
    def active_model(self) -> str:
        return self.model

    @property
    def model_identity(self) -> str:
        return f"ark-coding:{self.model}:{self.dimension}"

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        message = str(exc).lower()
        return status_code == 429 or any(marker in message for marker in (
            "account_ratelimitexceeded", "accountratelimitexceeded",
            "rate limit", "ratelimit", "too many requests", "toomanyrequests",
        ))

    def _wait_for_request_slot(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.request_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _create_embeddings(self, batch: List[str]):
        for attempt in range(self.max_retries + 1):
            self._wait_for_request_slot()
            try:
                return self.client.embeddings.create(
                    model=self.model,
                    input=batch,
                    dimensions=self.dimension,
                    encoding_format="float",
                )
            except Exception as exc:
                if not self._is_rate_limit_error(exc) or attempt >= self.max_retries:
                    raise
                delay = min(max(self.request_interval, 2 ** attempt), self.max_backoff)
                delay += random.uniform(0, 0.5)
                logging.warning("Ark embedding 触发限流，%.1f 秒后重试 (%d/%d)",
                                delay, attempt + 1, self.max_retries)
                time.sleep(delay)
            finally:
                self._last_request_at = time.monotonic()

    def embed_documents(self, texts: List[str], target_dim: int | None = None) -> List[List[float]]:
        expected_dim = target_dim or self.dimension
        if expected_dim != self.dimension:
            raise ValueError(
                f"Ark embedding dimension {self.dimension} does not match target {expected_dim}."
            )
        all_vectors: List[List[float]] = []
        for start in range(0, len(texts), 10):
            batch = texts[start:start + 10]
            response = self._create_embeddings(batch)
            all_vectors.extend(item.embedding for item in response.data)
        return all_vectors

    def embed_query(self, text: str, target_dim: int | None = None) -> List[float]:
        return self.embed_documents([text], target_dim=target_dim)[0]


# ══════════════════════════════════════════════════════════════════════
# DashScope / Model Studio Embeddings（OpenAI 兼容接口）
# ══════════════════════════════════════════════════════════════════════

class DashScopeEmbeddings(Embeddings):
    """百炼 qwen3.7-text-embedding 客户端，支持批处理和限流退避。"""

    def __init__(self, api_key: str, base_url: str, model: str, dimension: int):
        if not api_key:
            raise EnvironmentError(
                "DASHSCOPE_API_KEY is required when EMBEDDING_BACKEND=dashscope"
            )
        if not base_url:
            raise EnvironmentError(
                "DASHSCOPE_WORKSPACE_ID or DASHSCOPE_EMBEDDING_BASE_URL is required"
            )
        self.client = OpenAI(api_key=api_key, base_url=base_url,
                             timeout=config.EMBEDDING_CLIENT_TIMEOUT)
        self.model = model
        self.dimension = dimension
        self.request_interval = config.DASHSCOPE_EMBEDDING_REQUEST_INTERVAL
        self.max_retries = config.DASHSCOPE_EMBEDDING_MAX_RETRIES
        self.max_backoff = config.DASHSCOPE_EMBEDDING_MAX_BACKOFF
        self._last_request_at = 0.0
        self._cache: dict[str, List[float]] | None = None
        self._cache_lock = threading.Lock()

    # ── 磁盘缓存：按 model+dim+文本哈希复用向量，评测/重建索引不重复计费 ──

    def _cache_key(self, text: str) -> str:
        return hashlib.md5(f"{self.model}|{self.dimension}|{text}".encode()).hexdigest()

    def _load_cache(self) -> dict[str, List[float]]:
        if self._cache is not None:
            return self._cache
        with self._cache_lock:
            if self._cache is None:
                cache: dict[str, List[float]] = {}
                if os.path.exists(config.EMBEDDING_CACHE_PATH):
                    try:
                        with open(config.EMBEDDING_CACHE_PATH, encoding="utf-8") as fp:
                            cache = json.load(fp)
                    except Exception:
                        logging.warning("embedding 缓存文件损坏，忽略并重建: %s",
                                        config.EMBEDDING_CACHE_PATH)
                self._cache = cache
        return self._cache

    def _save_cache(self) -> None:
        # 多进程并发下 last-write-wins：丢条目只是下次重新计费一次，不影响正确性。
        _write_json_atomically(config.EMBEDDING_CACHE_PATH, self._cache)


    @property
    def active_model(self) -> str:
        return self.model

    @property
    def model_identity(self) -> str:
        return f"dashscope:{self.model}:{self.dimension}"

    def _wait_for_request_slot(self) -> None:
        remaining = self.request_interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def _create_embeddings(self, batch: List[str]):
        for attempt in range(self.max_retries + 1):
            self._wait_for_request_slot()
            try:
                return self.client.embeddings.create(
                    model=self.model,
                    input=batch,
                    dimensions=self.dimension,
                    encoding_format="float",
                )
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                retryable = status_code == 429 or status_code is None
                if not retryable or attempt >= self.max_retries:
                    raise
                delay = min(max(self.request_interval, 2 ** attempt), self.max_backoff)
                delay += random.uniform(0, 0.5)
                logging.warning("DashScope embedding 请求失败，%.1f 秒后重试 (%d/%d)",
                                delay, attempt + 1, self.max_retries)
                time.sleep(delay)
            finally:
                self._last_request_at = time.monotonic()

    def embed_documents(self, texts: List[str], target_dim: int | None = None) -> List[List[float]]:
        expected_dim = target_dim or self.dimension
        if expected_dim != self.dimension:
            raise ValueError(
                f"DashScope embedding dimension {self.dimension} does not match target {expected_dim}."
            )
        if not texts:
            return []
        use_cache = config.EMBEDDING_CACHE_ENABLED
        cache = self._load_cache() if use_cache else None
        vectors: List[List[float] | None] = [None] * len(texts)
        missing: list[int] = []
        for index, text in enumerate(texts):
            if cache is not None:
                cached = cache.get(self._cache_key(text))
                if cached is not None:
                    vectors[index] = cached
                    continue
            missing.append(index)
        if missing:
            batch_texts = [texts[index] for index in missing]
            new_vectors: List[List[float]] = []
            # qwen3.7-text-embedding 的文本列表单次最多 20 条。
            for start in range(0, len(batch_texts), 20):
                response = self._create_embeddings(batch_texts[start:start + 20])
                new_vectors.extend(item.embedding for item in response.data)
            for index, vector in zip(missing, new_vectors, strict=True):
                vectors[index] = vector
                if cache is not None:
                    cache[self._cache_key(texts[index])] = vector
            if cache is not None:
                self._save_cache()
        return vectors  # type: ignore[return-value]

    def embed_query(self, text: str, target_dim: int | None = None) -> List[float]:
        return self.embed_documents([text], target_dim=target_dim)[0]


# ══════════════════════════════════════════════════════════════════════
# DashScope Text Rerank（qwen3.7-text-rerank，原生 rerank 端点，独立计费）
# ══════════════════════════════════════════════════════════════════════

class DashScopeReranker:
    """百炼 text-rerank API 客户端：query 与文档列表 → 相关性分数。

    走原生 rerank 端点（非 OpenAI 兼容），与 embedding 分开计费；
    embedding 额度耗尽不影响本通道。
    """

    def __init__(self, api_key: str, base_url: str, model: str):
        if not api_key:
            raise EnvironmentError(
                "DASHSCOPE_API_KEY is required when RERANK_BACKEND=dashscope"
            )
        if not base_url:
            raise EnvironmentError("DASHSCOPE_RERANK_BASE_URL is required")

        self.model = model
        self._client = httpx.Client(timeout=30.0)
        self._url = base_url.rstrip("/") + "/services/rerank/text-rerank/text-rerank"
        self._headers = {"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"}
        self.request_interval = config.DASHSCOPE_RERANK_REQUEST_INTERVAL
        self.max_retries = config.DASHSCOPE_RERANK_MAX_RETRIES
        self.max_backoff = config.DASHSCOPE_RERANK_MAX_BACKOFF
        self._last_request_at = 0.0
        self._cache: dict[str, float] | None = None
        self._cache_lock = threading.Lock()

    # ── 磁盘缓存：精排对每个 (query, 文档) 对打分独立，按对缓存复用 ──

    def _cache_key(self, query: str, document: str) -> str:
        return hashlib.md5(
            f"{self.model}|{query}|{document}".encode()).hexdigest()

    def _load_cache(self) -> dict[str, float]:
        if self._cache is not None:
            return self._cache
        with self._cache_lock:
            if self._cache is None:
                cache: dict[str, float] = {}
                if os.path.exists(config.RERANK_CACHE_PATH):
                    try:
                        with open(config.RERANK_CACHE_PATH, encoding="utf-8") as fp:
                            cache = json.load(fp)
                    except Exception:
                        logging.warning("rerank 缓存文件损坏，忽略并重建: %s",
                                        config.RERANK_CACHE_PATH)
                self._cache = cache
        return self._cache

    def _save_cache(self) -> None:
        # 多进程并发下 last-write-wins：丢条目只是下次多计费一次，不影响正确性。
        _write_json_atomically(config.RERANK_CACHE_PATH, self._cache)


    @property
    def model_identity(self) -> str:
        return f"dashscope:{self.model}"

    def _wait_for_request_slot(self) -> None:
        remaining = self.request_interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def _post(self, query: str, documents: List[str], top_n: int) -> dict:
        payload = {
            "model": self.model,
            "input": {"query": query, "documents": documents},
            "parameters": {"top_n": min(top_n, len(documents)),
                           "return_documents": False},
        }
        for attempt in range(self.max_retries + 1):
            self._wait_for_request_slot()
            try:
                response = self._client.post(self._url, json=payload, headers=self._headers)
            except httpx.HTTPError:
                status_code = None
                error_body = ""
            else:
                status_code = response.status_code
                if status_code == 200:
                    return response.json()
                error_body = response.text[:300]
            retryable = status_code is None or status_code == 429
            if not retryable or attempt >= self.max_retries:
                raise RuntimeError(
                    f"DashScope rerank 请求失败 status={status_code}: {error_body}"
                )
            delay = min(max(self.request_interval, 2 ** attempt), self.max_backoff)
            delay += random.uniform(0, 0.5)
            logging.warning("DashScope rerank 请求失败，%.1f 秒后重试 (%d/%d)",
                            delay, attempt + 1, self.max_retries)
            time.sleep(delay)
        raise RuntimeError("DashScope rerank 请求失败：重试次数耗尽")

    @staticmethod
    def _map_results(payload: dict, num_documents: int) -> List[float]:
        scores = [0.0] * num_documents
        for item in payload["output"]["results"]:
            scores[item["index"]] = float(item["relevance_score"])
        return scores

    def rerank(self, query: str, documents: List[str], top_n: int | None = None) -> List[float]:
        """返回与 documents 等长的相关性分数（未返回的文档记 0 分）。

        精排对每个 (query, 文档) 对独立打分：命中缓存的直接复用，
        只对缺失的文档发起一次批量请求，结果按对落盘。
        """
        top_n = len(documents) if top_n is None else min(top_n, len(documents))
        use_cache = config.RERANK_CACHE_ENABLED
        cache = self._load_cache() if use_cache else None
        scores: List[float | None] = [None] * len(documents)
        missing: list[int] = []
        for index, document in enumerate(documents):
            if cache is not None:
                cached = cache.get(self._cache_key(query, document))
                if cached is not None:
                    scores[index] = cached
                    continue
            missing.append(index)
        if missing:
            batch_documents = [documents[index] for index in missing]
            payload = self._post(query, batch_documents, len(batch_documents))
            self._last_request_at = time.monotonic()
            batch_scores = self._map_results(payload, len(batch_documents))
            for index, score in zip(missing, batch_scores, strict=True):
                scores[index] = score
                if cache is not None:
                    cache[self._cache_key(query, documents[index])] = score
            if cache is not None:
                self._save_cache()
        return scores  # type: ignore[return-value]


_reranker = None
_reranker_lock = threading.Lock()


def get_reranker() -> DashScopeReranker:
    global _reranker
    if _reranker is not None:
        return _reranker
    with _reranker_lock:
        if _reranker is None:
            if config.RERANK_BACKEND == "dashscope":
                _reranker = DashScopeReranker(
                    api_key=config.DASHSCOPE_API_KEY,
                    base_url=config.DASHSCOPE_RERANK_BASE_URL,
                    model=config.DASHSCOPE_RERANK_MODEL,
                )
            else:
                raise ValueError(
                    f"Unsupported RERANK_BACKEND={config.RERANK_BACKEND!r}; "
                    "expected dashscope or local."
                )
            logging.info("  Reranker: %s", _reranker.model_identity)
        return _reranker


# ══════════════════════════════════════════════════════════════════════
# 本地 HuggingFace Embeddings（离线兜底）
# ══════════════════════════════════════════════════════════════════════

class LocalEmbeddings(Embeddings):
    """基于 sentence-transformers 的本地模型（默认 Qwen3-Embedding-0.6B, 1024 维）。"""

    def __init__(self, model_name: str, device: str = "cpu"):
        from sentence_transformers import SentenceTransformer
        logging.info("加载本地 Embedding 模型: %s (device=%s) ...", model_name, device)
        self._model = SentenceTransformer(model_name, device=device)
        self.model = model_name
        self.dimension = len(self._model.encode("test", normalize_embeddings=True))

    @property
    def active_model(self) -> str:
        return self.model

    @property
    def model_identity(self) -> str:
        return f"local:{self.model}:{self.dimension}"

    def embed_documents(self, texts: List[str], target_dim: int | None = None) -> List[List[float]]:
        vecs = self._model.encode(texts, normalize_embeddings=True,
                                  show_progress_bar=len(texts) > 50)
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str, target_dim: int | None = None) -> List[float]:
        # Qwen3-Embedding 用 query 前缀提升检索质量
        v = self._model.encode(text, prompt_name="query", normalize_embeddings=True)
        return v.tolist()


_embeddings: Embeddings | None = None
_emb_lock = threading.Lock()


def get_embeddings() -> Embeddings:
    global _embeddings
    if _embeddings is not None:
        return _embeddings
    with _emb_lock:  # 并发首调只加载一次模型（否则重复加载 1GB 权重 + HF 请求风暴）
        if _embeddings is not None:
            return _embeddings
        if config.EMBEDDING_BACKEND == "dashscope":
            _embeddings = DashScopeEmbeddings(
                api_key=config.DASHSCOPE_API_KEY,
                base_url=config.DASHSCOPE_EMBEDDING_BASE_URL,
                model=config.DASHSCOPE_EMBEDDING_MODEL,
                dimension=config.DASHSCOPE_EMBEDDING_DIMENSIONS,
            )
        elif config.EMBEDDING_BACKEND == "ark":
            _embeddings = ArkCodingEmbeddings(
                api_key=config.ARK_EMBEDDING_API_KEY,
                base_url=config.ARK_EMBEDDING_BASE_URL,
                model=config.ARK_EMBEDDING_MODEL,
                dimension=config.ARK_EMBEDDING_DIMENSIONS,
            )
        elif config.EMBEDDING_BACKEND == "local":
            _embeddings = LocalEmbeddings(
                model_name=config.LOCAL_EMBEDDING_MODEL,
                device=config.LOCAL_EMBEDDING_DEVICE,
            )
        else:
            raise ValueError(
                f"Unsupported EMBEDDING_BACKEND={config.EMBEDDING_BACKEND!r}; "
                "expected dashscope, ark, or local."
            )
        logging.info("  Embedding: %s | %s", config.EMBEDDING_BACKEND, _embeddings.model)
        return _embeddings
