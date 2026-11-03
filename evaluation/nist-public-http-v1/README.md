# 公开语料 HTTP 质量预验

这是开发验证，不是独立人工测试或生产容量测试。冻结三条用户请求和八份公开 NIST 控制原文；来源、参数定义与许可沿用 `evaluation/nist-new-controls-v2`，每个知识块保留固定版本 URL 与 CC0 说明。检索元数据不是可引用证据，题目判定要点不发送给模型。

入口：`python -m scripts.evaluate_public_http --packet evaluation/nist-public-http-v1 --output <全新隔离输出目录>` 默认仅预检。真实执行另加 `--execute`，必须有与清单哈希绑定的授权文件；一次性 dispatch.lock 不可移除来重复使用额度。保留的 v1 输出不能覆盖或当作新实验。

应用通过进程内 ASGI TestClient 执行身份、线程、SSE、路由、追问改写、稀疏检索和 SQL 历史；仅模型服务是真实外部调用。dense、查询变体、重排和向量记忆关闭；向量是启动协议fixture，不提供语义质量证据。原始框架回执不是 HTTP 原始线路报文。

离线复核：`python -m scripts.audit_public_http --packet evaluation/nist-public-http-v1 --run <运行输出目录> --output <新的审计JSON>`，失败返回非零。程序门禁验证记录完整性、来源与调用边界；语义正确性另作原文复查，不把程序通过当成人工金标。
