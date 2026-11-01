"""Configuration — all settings from environment variables (CISP-QA)."""
import os
from dotenv import load_dotenv

# 容器、CI 和测试显式传入的进程环境优先于本地 .env。
load_dotenv(override=False)

# ── LLM 通用配置（OpenAI 兼容协议）──
LLM_API_KEY  = os.getenv("LLM_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-pro")
SIMPLE_LLM_MODEL = os.getenv("SIMPLE_LLM_MODEL", "deepseek-v4-flash")

# ── Embeddings ────────────────────────────────────────────────────────
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "dashscope")
# 百炼 Model Studio：qwen3.7-text-embedding-flash 走 OpenAI 兼容接口。
# 若未直接配置完整 URL，则由 Workspace ID 和 region 组装。
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_WORKSPACE_ID = os.getenv("DASHSCOPE_WORKSPACE_ID", "")
DASHSCOPE_REGION = os.getenv("DASHSCOPE_REGION", "cn-beijing")
_DASHSCOPE_DEFAULT_BASE_URL = (
    f"https://{DASHSCOPE_WORKSPACE_ID}.{DASHSCOPE_REGION}.maas.aliyuncs.com/compatible-mode/v1"
    if DASHSCOPE_WORKSPACE_ID else ""
)
DASHSCOPE_EMBEDDING_BASE_URL = os.getenv(
    "DASHSCOPE_EMBEDDING_BASE_URL", _DASHSCOPE_DEFAULT_BASE_URL
)
DASHSCOPE_EMBEDDING_MODEL = os.getenv(
    "DASHSCOPE_EMBEDDING_MODEL", "qwen3.7-text-embedding-flash"
)
DASHSCOPE_EMBEDDING_DIMENSIONS = int(os.getenv("DASHSCOPE_EMBEDDING_DIMENSIONS", "1024"))
DASHSCOPE_EMBEDDING_REQUEST_INTERVAL = float(
    os.getenv("DASHSCOPE_EMBEDDING_REQUEST_INTERVAL", "0.1")
)
DASHSCOPE_EMBEDDING_MAX_RETRIES = int(os.getenv("DASHSCOPE_EMBEDDING_MAX_RETRIES", "4"))
DASHSCOPE_EMBEDDING_MAX_BACKOFF = float(os.getenv("DASHSCOPE_EMBEDDING_MAX_BACKOFF", "30"))
# embedding HTTP 客户端超时（秒）：长期记忆写入等旁路调用一旦挂起，
# 不应把 SSE 流的 done 事件拖住几分钟（压测实测 656s 超时）。
EMBEDDING_CLIENT_TIMEOUT = float(os.getenv("EMBEDDING_CLIENT_TIMEOUT", "30"))

# 兼容保留：旧 Ark Coding Plan 配置。
ARK_API_KEY = os.getenv("ARK_API_KEY", "")
ARK_EMBEDDING_API_KEY = os.getenv("ARK_EMBEDDING_API_KEY") or ARK_API_KEY
ARK_EMBEDDING_BASE_URL = os.getenv("ARK_EMBEDDING_BASE_URL", "https://ark.cn-beijing.volces.com/api/coding/v3")
ARK_EMBEDDING_MODEL = os.getenv("ARK_EMBEDDING_MODEL", "doubao-embedding-vision")
ARK_EMBEDDING_DIMENSIONS = int(os.getenv("ARK_EMBEDDING_DIMENSIONS", "1024"))
ARK_EMBEDDING_REQUEST_INTERVAL = float(os.getenv("ARK_EMBEDDING_REQUEST_INTERVAL", "3.0"))
ARK_EMBEDDING_MAX_RETRIES = int(os.getenv("ARK_EMBEDDING_MAX_RETRIES", "6"))
ARK_EMBEDDING_MAX_BACKOFF = float(os.getenv("ARK_EMBEDDING_MAX_BACKOFF", "30"))
# 本地模型（HuggingFace sentence-transformers，EMBEDDING_BACKEND=local 时生效）
LOCAL_EMBEDDING_MODEL = os.getenv("LOCAL_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
_LOCAL_EMBEDDING_DEVICE_RAW = os.getenv("LOCAL_EMBEDDING_DEVICE", "cpu")


def _resolve_embedding_device() -> str:
    """auto 时自动检测 CUDA，不可用回退 cpu。"""
    if _LOCAL_EMBEDDING_DEVICE_RAW != "auto":
        return _LOCAL_EMBEDDING_DEVICE_RAW
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


LOCAL_EMBEDDING_DEVICE = _resolve_embedding_device()
HF_ENDPOINT = os.getenv("HF_ENDPOINT", "")
if HF_ENDPOINT:
    os.environ.setdefault("HF_ENDPOINT", HF_ENDPOINT)
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# ── 本地数据路径 ──
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
KB_CHUNKS_PATH = os.path.join(DATA_DIR, "kb_chunks.json")
QUESTIONS_PATH = os.path.join(DATA_DIR, "questions.json")
KNOWLEDGE_TAXONOMY_PATH = os.path.join(DATA_DIR, "knowledge_taxonomy.json")
QUESTION_TAGS_PATH = os.path.join(DATA_DIR, "question_knowledge_tags.json")
QUESTION_ATTRIBUTIONS_PATH = os.path.join(DATA_DIR, "question_attributions.json")
PARSE_FAILURES_PATH = os.path.join(DATA_DIR, "parse_failures.json")
VECTORS_PATH = os.path.join(DATA_DIR, "vectors.npz")
MEMORY_VECTORS_PATH = os.path.join(DATA_DIR, "memory_vectors.npz")
DB_PATH = os.getenv("CISP_DB_PATH", os.path.join(DATA_DIR, "cisp_qa.db"))

# ── 存储层（MySQL 主后端 + SQLite 降级；Redis 缓存可选）──
MYSQL_URL_OVERRIDE = os.getenv("DATABASE_URL", "")   # 例: mysql+pymysql://user:pwd@127.0.0.1/cisp_qa?charset=utf8mb4
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
ANSWER_CACHE_TTL = int(os.getenv("ANSWER_CACHE_TTL", "3600"))
RETRIEVAL_CACHE_TTL = int(os.getenv("RETRIEVAL_CACHE_TTL", "600"))
STATS_CACHE_TTL = int(os.getenv("STATS_CACHE_TTL", "30"))
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))

# ── 检索参数 ──
# RETRIEVER_K=5 是 gold 集（80 题）上 100% 召回的最小 k，依据与边界见 docs/05-检索测试集评测.md 第 7 节
RETRIEVER_K = int(os.getenv("RETRIEVER_K", "5"))
DENSE_K = int(os.getenv("DENSE_K", "10"))
SPARSE_K = int(os.getenv("SPARSE_K", "20"))
RRF_K = int(os.getenv("RRF_K", "20"))
ENABLE_QUERY_REWRITE = os.getenv("ENABLE_QUERY_REWRITE", "true").lower() == "true"
# RAG-Fusion 式查询变体：追问改写后额外生成"完整问句版/关键词纠错版"变体，
# 多路检索 RRF 融合（原查询权重加倍）。变体 embedding 走 flash（便宜），
# 变体生成走 DeepSeek flash。实测见 docs/07 §4。
ENABLE_QUERY_VARIANTS = os.getenv("ENABLE_QUERY_VARIANTS", "true").lower() == "true"
QUERY_VARIANT_MAX = int(os.getenv("QUERY_VARIANT_MAX", "2"))
ENABLE_FEATURE_RERANKING = os.getenv("ENABLE_FEATURE_RERANKING", "true").lower() == "true"
CHUNK_MAX_CHARS = int(os.getenv("CHUNK_MAX_CHARS", "1200"))
SUMMARY_CHUNK_MAX_CHARS = int(os.getenv("SUMMARY_CHUNK_MAX_CHARS", "800"))
CHUNK_STRATEGY = os.getenv("CHUNK_STRATEGY", "structured")
CHUNK_TARGET_CHARS = int(os.getenv("CHUNK_TARGET_CHARS", "220"))
STRUCTURED_CHUNK_MAX_CHARS = int(os.getenv("STRUCTURED_CHUNK_MAX_CHARS", "420"))
CHUNK_OVERLAP_UNITS = int(os.getenv("CHUNK_OVERLAP_UNITS", "1"))
# 方案 B（定长+字符重叠）对比基线参数
FIXED_CHUNK_TARGET_CHARS = int(os.getenv("FIXED_CHUNK_TARGET_CHARS", "300"))
FIXED_CHUNK_MAX_CHARS = int(os.getenv("FIXED_CHUNK_MAX_CHARS", "500"))
FIXED_CHUNK_OVERLAP_CHARS = int(os.getenv("FIXED_CHUNK_OVERLAP_CHARS", "100"))
# 表格感知切片（structured 策略）：PDF 表格序列化为 markdown 独立 chunk。
# 质量过滤剔除版式装饰框/图形文字误检；字符保真度兜底防止 find_tables 丢字。
ENABLE_TABLE_CHUNKING = os.getenv("ENABLE_TABLE_CHUNKING", "true").lower() == "true"
TABLE_MIN_FILLED_CELLS = int(os.getenv("TABLE_MIN_FILLED_CELLS", "8"))
TABLE_MIN_FILL_RATIO = float(os.getenv("TABLE_MIN_FILL_RATIO", "0.35"))
TABLE_FILL_RATIO_COLS_WAIVER = int(os.getenv("TABLE_FILL_RATIO_COLS_WAIVER", "9"))  # 包头示意图类合并单元格多，列数≤该值豁免密度要求
TABLE_MAX_COLS = int(os.getenv("TABLE_MAX_COLS", "10"))        # 更宽的"表格"多为并排多表误检
TABLE_MAX_CELLS = int(os.getenv("TABLE_MAX_CELLS", "100"))     # 大而稀疏的基本是整页图形
TABLE_MIN_CHAR_COVERAGE = float(os.getenv("TABLE_MIN_CHAR_COVERAGE", "0.75"))
TABLE_CONTEXT_MAX_CHARS = int(os.getenv("TABLE_CONTEXT_MAX_CHARS", "60"))  # 表题/引导语截断长度
# 精排（交叉编码重排序）：可选，默认关闭。qwen3.7-text-rerank 为 pointwise 逐对打分，
# 直接替换顺序会压掉"要素更全"的枚举/定义答案页（docs/05 §3.1.2/3.1.3 实测 -2.5pp）；
# 与特征序做加权融合后双集 +2.5pp（§3.1.4 离线重放扫参，α∈[0.3,0.6] 均为平台期）。
# "replace"=纯精排序（对照口径）；后端 "dashscope"=API（独立计费）/"local"=bge 交叉编码器；
# 失败自动降级 RRF 顺序。BM25 降级召回场景精排收益最大（+11.25pp）。
ENABLE_RERANKING = os.getenv("ENABLE_RERANKING", "false").lower() == "true"
RERANK_BACKEND = os.getenv("RERANK_BACKEND", "dashscope")
RERANK_FUSION = os.getenv("RERANK_FUSION", "alpha")
RERANK_FUSION_ALPHA = float(os.getenv("RERANK_FUSION_ALPHA", "0.5"))
RERANK_TOP_K = int(os.getenv("RERANK_TOP_K", "10"))
LOCAL_RERANKER_MODEL = os.getenv("LOCAL_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
DASHSCOPE_RERANK_BASE_URL = os.getenv("DASHSCOPE_RERANK_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")
DASHSCOPE_RERANK_MODEL = os.getenv("DASHSCOPE_RERANK_MODEL", "qwen3.7-text-rerank")
DASHSCOPE_RERANK_REQUEST_INTERVAL = float(os.getenv("DASHSCOPE_RERANK_REQUEST_INTERVAL", "0.2"))
DASHSCOPE_RERANK_MAX_RETRIES = int(os.getenv("DASHSCOPE_RERANK_MAX_RETRIES", "2"))
DASHSCOPE_RERANK_MAX_BACKOFF = float(os.getenv("DASHSCOPE_RERANK_MAX_BACKOFF", "8"))
# 显式关闭 dense 时直接使用 BM25，不记录为运行时降级。
ENABLE_DENSE_RETRIEVAL = os.getenv("ENABLE_DENSE_RETRIEVAL", "true").lower() == "true"
# dense 召回不可用（embedding 额度耗尽/网络故障）时降级为 BM25 兜底召回，避免查询直接报错。
ALLOW_DENSE_DEGRADATION = os.getenv("ALLOW_DENSE_DEGRADATION", "true").lower() == "true"
DENSE_DEGRADED_SPARSE_K = int(os.getenv("DENSE_DEGRADED_SPARSE_K", "60"))
# embedding 磁盘缓存：按 model+dim+文本哈希缓存向量，评测/重启不重复计费。
# 只对按量计费的远端后端生效；重建索引、回归评测、重复实验不再烧额度。
EMBEDDING_CACHE_ENABLED = os.getenv("EMBEDDING_CACHE_ENABLED", "true").lower() == "true"
EMBEDDING_CACHE_PATH = os.getenv("EMBEDDING_CACHE_PATH", "data/embedding_cache.json")
# rerank 磁盘缓存：精排对每个 (query, 文档) 对打分独立，按对缓存——同一查询
# 换候选池也能复用已打的分，评测/调参反复跑不烧 rerank 额度。
RERANK_CACHE_ENABLED = os.getenv("RERANK_CACHE_ENABLED", "true").lower() == "true"
RERANK_CACHE_PATH = os.getenv("RERANK_CACHE_PATH", "data/rerank_cache.json")
# 学习排序：只使用 query/title/text/source/检索分等线上可见特征；模型由 gold train split 训练。
ENABLE_LEARNED_RANKING = os.getenv("ENABLE_LEARNED_RANKING", "false").lower() == "true"
LEARNED_RANKER_PATH = os.getenv("LEARNED_RANKER_PATH", "data/learning_ranker_model.json")
# 门控学习排序：all 表示所有查询都用；否则只对指定意图启用，避免 acronym/definition 被模型误伤。
LEARNED_RANKER_QUERY_TYPES = os.getenv("LEARNED_RANKER_QUERY_TYPES", "process")
# 相关性门限：回答前检查检索结果的最高 dense 余弦，低于门限直接拒答（跳过回答 LLM）。
# 门限用 gold 集与离题问题集校准（见 docs/05 §3.3）；关闭后拒答完全依赖回答 prompt 规则 1。
ENABLE_RELEVANCE_GATE = os.getenv("ENABLE_RELEVANCE_GATE", "true").lower() == "true"
RELEVANCE_FLOOR = float(os.getenv("RELEVANCE_FLOOR", "0.45"))

# ── 复杂问答证据子代理实验（默认关闭）──
ENABLE_EVIDENCE_SUBAGENT = os.getenv("ENABLE_EVIDENCE_SUBAGENT", "false").lower() == "true"

# ── 出题 ──
QUIZ_AI_MIN_BANK = int(os.getenv("QUIZ_AI_MIN_BANK", "5"))   # 域内未做题少于该数时允许 AI 补题
# ── 用户画像（掌握度模型）──
# Beta 后验：掌握度=均值，置信度=样本量带来的区间收敛；薄弱= P(能力<阈值)>概率
MASTERY_WEAK_THRESHOLD = float(os.getenv("MASTERY_WEAK_THRESHOLD", "0.6"))   # 能力低于该值判薄弱
MASTERY_WEAK_PROB = float(os.getenv("MASTERY_WEAK_PROB", "0.7"))             # 后验概率阈值
BETA_PRIOR = float(os.getenv("BETA_PRIOR", "1.0"))                           # Beta 先验（伪计数）
# 遗忘曲线：记忆强度 = 2^(-天数/半衰期)，超过半衰期进入复习队列
MASTERY_HALF_LIFE_DAYS = float(os.getenv("MASTERY_HALF_LIFE_DAYS", "7"))
REVIEW_DUE_STRENGTH = float(os.getenv("REVIEW_DUE_STRENGTH", "0.5"))         # 强度低于该值判到期
# Elo 评级（Pelánek 2016）：按时间顺序 replay attempts 增量更新
ELO_INIT = float(os.getenv("ELO_INIT", "1500"))
ELO_K = float(os.getenv("ELO_K", "32"))
# 提问信号（问答侧薄弱信号融合）：同一域提问次数达到该值视为"高频提问"
ASK_SIGNAL_MIN = int(os.getenv("ASK_SIGNAL_MIN", "3"))
# 高频提问且答题样本不足/薄弱的域，在 weak 抽题中的加权倍数
ASK_WEIGHT_BONUS = float(os.getenv("ASK_WEIGHT_BONUS", "1.5"))
# FSRS 复习到期卡优先：weak 抽题时先消耗到期复习卡（0 关闭）
ENABLE_FSRS_REVIEW = os.getenv("ENABLE_FSRS_REVIEW", "true").lower() == "true"

# ── 保守题目归因 ──
# 这些阈值只能用开发集适配；classifier_version 随规则变化显式递增。
ATTRIBUTION_CLASSIFIER_VERSION = os.getenv(
    "ATTRIBUTION_CLASSIFIER_VERSION", "conservative-attribution-v3"
)
ATTRIBUTION_DENSE_TOP_K = int(os.getenv("ATTRIBUTION_DENSE_TOP_K", "5"))
ATTRIBUTION_DENSE_MIN_SCORE = float(os.getenv("ATTRIBUTION_DENSE_MIN_SCORE", "0.45"))
ATTRIBUTION_DENSE_MIN_MARGIN = float(os.getenv("ATTRIBUTION_DENSE_MIN_MARGIN", "0.03"))
ATTRIBUTION_TFIDF_TOP_K = int(os.getenv("ATTRIBUTION_TFIDF_TOP_K", "5"))
ATTRIBUTION_TFIDF_MIN_SCORE = float(os.getenv("ATTRIBUTION_TFIDF_MIN_SCORE", "0.08"))
ATTRIBUTION_TFIDF_MIN_MARGIN = float(os.getenv("ATTRIBUTION_TFIDF_MIN_MARGIN", "0.02"))
ATTRIBUTION_EXACT_STRONG_SCORE = float(os.getenv("ATTRIBUTION_EXACT_STRONG_SCORE", "0.95"))
ATTRIBUTION_EXACT_MIN_MARGIN = float(os.getenv("ATTRIBUTION_EXACT_MIN_MARGIN", "0.02"))
ATTRIBUTION_EXACT_STEM_SCORE = float(os.getenv("ATTRIBUTION_EXACT_STEM_SCORE", "0.88"))
ATTRIBUTION_EXACT_CORRECT_OPTION_SCORE = float(
    os.getenv("ATTRIBUTION_EXACT_CORRECT_OPTION_SCORE", "0.95")
)
ATTRIBUTION_EXACT_ANALYSIS_SCORE = float(
    os.getenv("ATTRIBUTION_EXACT_ANALYSIS_SCORE", "0.82")
)
ATTRIBUTION_CALIBRATION_MIN_EVIDENCE = int(
    os.getenv("ATTRIBUTION_CALIBRATION_MIN_EVIDENCE", "2")
)

# ── probable 弱点校准 ──
ENABLE_CALIBRATION = os.getenv("ENABLE_CALIBRATION", "true").lower() == "true"
# 首次 pending 假设可立即给一道可靠题；后续校准机会至少间隔 N 次画像作答。
CALIBRATION_MIN_ATTEMPT_GAP = int(os.getenv("CALIBRATION_MIN_ATTEMPT_GAP", "1"))
CALIBRATION_MAX_INSERTIONS = int(os.getenv("CALIBRATION_MAX_INSERTIONS", "2"))
CALIBRATION_MAX_PENDING = int(os.getenv("CALIBRATION_MAX_PENDING", "3"))

# ── AI 出题质量 ──
# 生成后自校验（第二个 LLM 验证答案可由资料推出/干扰项互斥/题干无歧义），不过则标 needs_review
QUIZ_VERIFY_ENABLED = os.getenv("QUIZ_VERIFY_ENABLED", "true").lower() == "true"
# 近似查重：与同域现有题干的 SequenceMatcher 相似度超过该值视为重复
QUIZ_DUP_SIMILARITY = float(os.getenv("QUIZ_DUP_SIMILARITY", "0.85"))

# ── 多轮记忆 ──
MEMORY_MAX_ROUNDS = int(os.getenv("MEMORY_MAX_ROUNDS", "5"))
CHAT_MAX_CALLS = int(os.getenv("CHAT_MAX_CALLS", "12"))
CHAT_MAX_ATTEMPTS_PER_CALL = int(os.getenv("CHAT_MAX_ATTEMPTS_PER_CALL", "2"))
CHAT_MAX_OUTPUT_TOKENS = int(os.getenv("CHAT_MAX_OUTPUT_TOKENS", "1024"))
CHAT_TIMEOUT_SECONDS = float(os.getenv("CHAT_TIMEOUT_SECONDS", "60"))
SHORT_MEMORY_TTL = int(os.getenv("SHORT_MEMORY_TTL", "7200"))
LONG_MEMORY_TOP_K = int(os.getenv("LONG_MEMORY_TOP_K", "3"))

# ── 原始资料路径（只读）──
COURSE_DIR = os.getenv(
    "CISP_COURSE_DIR",
    os.path.join(DATA_DIR, "raw", "courses"),
)
EXAM_DIR = os.getenv("CISP_EXAM_DIR", os.path.join(DATA_DIR, "raw", "exams"))


# Browser origin is explicit; never derive trusted origins from request Host headers.
CISP_PUBLIC_ORIGIN = os.getenv("CISP_PUBLIC_ORIGIN", "http://127.0.0.1:9528").rstrip("/")


def validate() -> None:
    missing = [k for k, v in {
        "LLM_API_KEY": LLM_API_KEY,
    }.items() if not v]
    if (ENABLE_DENSE_RETRIEVAL
            and EMBEDDING_BACKEND == "dashscope" and not DASHSCOPE_API_KEY):
        missing.append("DASHSCOPE_API_KEY")
    if (ENABLE_DENSE_RETRIEVAL
            and EMBEDDING_BACKEND == "dashscope"
            and not DASHSCOPE_EMBEDDING_BASE_URL):
        missing.append("DASHSCOPE_WORKSPACE_ID or DASHSCOPE_EMBEDDING_BASE_URL")
    if (ENABLE_DENSE_RETRIEVAL
            and EMBEDDING_BACKEND == "ark" and not ARK_EMBEDDING_API_KEY):
        missing.append("ARK_EMBEDDING_API_KEY")
    if missing:
        raise EnvironmentError(f"Missing env vars: {missing}. Fill the required keys into .env")
    if (ENABLE_DENSE_RETRIEVAL
            and EMBEDDING_BACKEND == "ark"
            and ARK_EMBEDDING_MODEL != "doubao-embedding-vision"):
        raise ValueError("Ark Coding Plan embedding model must be doubao-embedding-vision")
    if (ENABLE_DENSE_RETRIEVAL
            and EMBEDDING_BACKEND == "dashscope"
            and DASHSCOPE_EMBEDDING_DIMENSIONS not in {256, 512, 768, 1024, 1536, 2048, 2560}):
        raise ValueError("qwen3.7-text-embedding dimensions must be 256/512/768/1024/1536/2048/2560")
