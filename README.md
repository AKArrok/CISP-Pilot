# CISP-QA — CISP 备考问答机器人

基于本地课程资料的 RAG 问答 + 真题刷题 + 薄弱点画像，采用基于 LangGraph 的多阶段 RAG 流水线，详见 `docs/`。

## 功能

- **问答**：基于 10 大知识域课件/知识点总结的混合检索（DashScope flash 向量 + BM25 → RRF → 特征重排 → text-rerank 分数融合精排；embedding API 不可用时自动降级 BM25 兜底召回，远端向量与精排分数带磁盘缓存避免重复计费），流式回答并标注课件出处；支持多轮追问（指代消解）
- **刷题**：当前本地构建产物含 1360 道结构化真题（700题PDF + CISP216 + 51CTO六套），随机练习 / 薄弱强化两种模式；真题不足时 AI 参照课件生成补充题
- **入门诊断**：用户先选择自认为薄弱的知识域并填写少量目标/时间信息，系统用跨域校准题和可恢复的自适应诊断验证自评，再输出带证据来源的画像；可一键把结果带入 Tutor 对话做误区追问（`/diagnostic/*`）
- **画像**：按 `thread_id` 隔离答题、提问、知识点 Beta 状态与 FSRS 复习卡；分别输出知识域和知识点的掌握度、置信度与证据不足标记，反哺“薄弱强化”出题
- **记忆**：短期上下文保存在 LangGraph `MemorySaver` + Redis/内存缓存；长期记忆把每轮对话 embedding 后写入 `data/memory_vectors.npz`，并通过元数据支持 `GET /api/history?thread_id=` / `GET /api/threads`
- **可观测性**：请求级指标采集（路由/检索/首 token/总耗时 + Token 用量）落 SQL，`GET /api/metrics` 聚合分位数
- **数据一致性自检**：`data/manifest.json` 版本清单，解析产物与课件向量索引条数/版本不符时启动即报错并给出修复指令
- **存储层**（仿 Dify 架构）：SQLAlchemy 2.0 单一建模 + Alembic 迁移，`DATABASE_URL` 驱动——MySQL(utf8mb4) 主后端，连接失败自动降级 SQLite；题库/知识块/答题记录/指标入库，用户长期记忆使用独立向量文件
- **Redis 缓存层**：问答缓存（首轮问题 TTL 1h）、检索结果缓存、画像统计缓存（答题即失效）、固定窗口限流；Redis 不可达自动降级进程内存

## 快速开始

```bash
pip install -r requirements.txt

# 1. 解析原始资料（课件 → 知识块；真题 → 题库）
python data_ingest/parse_courses.py
python data_ingest/parse_questions.py --llm-classify
python scripts/build_taxonomy.py --import-db  # 生成并导入可审校的知识点候选/题目多标签
# 若 dense 后端可用，可显式加入 --with-dense；不可用时会记录 dense_unavailable 而不是伪造三路一致

# 2. 构建向量索引
python data_ingest/build_index.py

# 3. 启动 Web 面板
python server.py        # → http://localhost:9528
```

## 测试与评测

```bash
pytest tests/test_parsers.py tests/test_grader.py tests/test_memory.py -q  # 快速单测；CI 执行完整离线套件
python tests/run_isolated_suite.py                   # 完整回归：临时 SQLite + 构建产物防污染
npm install && npx playwright install chromium     # 首次准备浏览器测试
npm run test:e2e                                   # 临时数据库上的浏览器诊断与归因纠错
python tests/eval_profile_accuracy.py              # 用户画像准确度基准与质量门槛
python tests/eval_retrieval.py                 # 旧检索 smoke：top5/top1 知识域命中率
python tests/eval_retrieval_gold.py --no-rerank # 知识点级 gold 检索评测（需 embedding API）
python tests/eval_retrieval_gold.py --gold tests/fixtures/retrieval_holdout.json --no-rerank # 独立 holdout 检索评测
python tests/eval_retrieval_gold.py --rerank    # 知识点级 gold 检索评测 + reranker
python tests/tune_retrieval_gold.py             # 基于 gold 集对 DENSE_K/SPARSE_K/RRF_K 做小网格调参
python tests/train_learning_ranker.py           # 固定 train/dev split 上训练并评估轻量学习排序
python tests/compare_chunking.py               # 三方案切片 A/B/C 对比（legacy/fixed/structured）
python tests/eval_intents.py                   # 意图路由/拒答行为回归（需 LLM API）
```

检索准确性看 `docs/05-检索测试集评测.md`；`tests/eval_retrieval.py` 只适合做回归烟测，不能证明知识点召回。
完整隔离回归、数据库目标与缓存原子写证据见 `docs/14-可复现可靠性门禁.md`；中文归因审阅与纠错口径见 `docs/13-归因纠错与画像重放.md`。

## Docker 部署

```bash
docker build -t cisp-qa .
docker run -p 9528:9528 --env-file .env \
  -v $(pwd)/data:/app/data -v cisp_hf:/app/.hf_cache cisp-qa
# 首次启动会下载 embedding/精排模型到 .hf_cache 卷；国内构建时可加
# --build-arg 或在 Dockerfile 中设置 HF_ENDPOINT=https://hf-mirror.com
```


## 存储层切换

`.env` 填 `MYSQL_PASSWORD`（或 `DATABASE_URL=mysql+pymysql://user:pwd@host/cisp_qa?charset=utf8mb4`）即用 MySQL；
留空自动降级 SQLite，行为一致。建库导入：

```bash
python scripts/import_to_db.py            # JSON 解析产物 → 数据库（幂等，按 id upsert）
python scripts/import_to_db.py --reset    # 清空 questions/kb_chunks 后重导
python scripts/import_to_db.py --legacy   # 迁移旧 SQLite 的历史答题/对话数据
alembic upgrade head                      # 或用迁移管理 schema
```

## 配置（.env）

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | DeepSeek 问答与路由 |
| `EMBEDDING_BACKEND` | `dashscope`（默认，qwen3.7-text-embedding）/ `local`（Qwen3-Embedding-0.6B）/ `ark`（doubao-embedding-vision，需 Coding Plan 订阅有效） |
| `DASHSCOPE_API_KEY` / `DASHSCOPE_EMBEDDING_BASE_URL` / `DASHSCOPE_EMBEDDING_MODEL` | 百炼 embedding（OpenAI 兼容接口，默认模型 `qwen3.7-text-embedding`，1024 维） |
| `ENABLE_DENSE_RETRIEVAL` / `DENSE_K` / `SPARSE_K` / `RRF_K` | dense 显式开关与候选池参数；关闭时直接走 BM25，不计为运行时降级 |
| `ENABLE_LEARNED_RANKING` / `LEARNED_RANKER_PATH` / `LEARNED_RANKER_QUERY_TYPES` | 轻量学习排序实验开关；默认关闭，当前模型全开会误伤 acronym/definition，门控默认 `process` |
| `ENABLE_RERANKING` / `RERANK_TOP_K` | 可选本地精排；默认关闭，当前 gold 评测显示会增加延迟且可能误伤 Top5 |
| `CHUNK_STRATEGY` | `structured`（默认：语义 Child 检索、完整 Parent 回答）/ `fixed`（定长+字符重叠）/ `legacy`（旧按页切片，仅用于 A/B/C 对比） |
| `CISP_COURSE_DIR` / `CISP_EXAM_DIR` | 原始资料路径 |
| `DATABASE_URL` | 显式数据库目标；支持 `mysql+pymysql://...` 和 `sqlite:///...`，设置后优先于 `CISP_DB_PATH` |
| `CISP_DB_PATH` | 未设置 `DATABASE_URL` 时的 SQLite 文件路径，默认 `data/cisp_qa.db` |
| `SHORT_MEMORY_TTL` / `LONG_MEMORY_TOP_K` | 短期上下文缓存 TTL、长期向量记忆召回条数 |
| `ATTRIBUTION_*` | 题目归因三路阈值与 `ATTRIBUTION_CLASSIFIER_VERSION`；只能用开发集适配 |
| `ENABLE_CALIBRATION` / `CALIBRATION_MIN_ATTEMPT_GAP` / `CALIBRATION_MAX_INSERTIONS` | probable 弱点可靠题校准的开关、最小间隔和线程上限；题源或预算耗尽且仍不一致时记为 inconclusive，不进入画像 |

## 结构

```
data_ingest/   解析与建库        retrieval/  本地向量库 + BM25 + RRF
agents/        LangGraph 问答    quiz/       抽题/生成/判分/画像
server.py      FastAPI+SSE       static/     双页签面板
docs/          选型/PRD/架构/诊断流程     tests/      自检与评测
```

## 题目归因与长期画像边界

题目归因状态和用户掌握度是两个不同概念：`confirmed/probable/unverified` 表示“题目→知识点”的证据可信度；Beta 掌握度、Elo 和 FSRS 只表示用户在已确认知识点上的学习证据。三路归因结果（dense、字符 TF-IDF、题干/正确选项/解析精确证据）保存到 `question_attributions`，完整路由候选保存到审计事件；`GET /api/stats?thread_id=...` 和 `GET /api/attributions` 会返回状态、证据数、最近证据和原因。

`probable` 错题只创建线程隔离的 pending calibration 假设，不修改长期 Beta/Elo/FSRS。校准题必须是另一个已确认归因的 active 题；至少两道独立题的结果一致后，假设才解决并把暂存证据纳入画像。没有可靠题、证据矛盾且题源耗尽，或展示预算耗尽时记为 `inconclusive`，不进入画像。同一校准题的重复提交保留原始审计记录，但只有首次证据可进入画像；状态转正、证据认领与知识状态更新在同一事务内完成，重复确认幂等。FSRS 复习卡是事务后的派生数据，更新失败会记录错误但不会伪装成整次画像提交失败。

评测报告使用 `scripts/evaluate_attribution.py`。`rejection_rate`、`accepted_coverage` 的分母是全部待归因题；旧字段 `observed_misattribution_rate` 保持知识点优先口径以兼容历史报告。新报告同时给出 `observed_domain_misattribution_rate`、`candidate_domain_accuracy`、`joint_observed_misattribution_rate` 和 confirmed/probable 联合精度；空标签不计为已知金标，候选领域缺失按错处理。`calibration_resolution_rate` 是规定机会内解决假设的比例，不是答对率。以上都是可追溯开发/运行指标，不替代独立 holdout。外部 holdout 没有两名独立人工盲审者时保持 `blocked`，不参与普通 CI 或运行时功能。
