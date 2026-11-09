# GitHub 实现对照：CISP 测评中台下一轮应该补什么

日期：2026-11-09（Asia/Shanghai）。状态：调研与当前代码对照完成，以下改进尚未实施。

后续用户已授权实施四项，进度及验收见 [V2交付记录](../evaluation-platform-v2/README.md)。本报告保留调研时固定提交对应的基线判断，不作为升级后现状清单。

用户要求实际参考 GitHub 上别人的实现。此次通过只读 GitHub API 检索4个仓库，核读18个相关源码/设计文件中的目标部分，并检查4份主许可证。引用固定到当次提交，文件清单及 Git blob SHA 见 [sources.json](sources.json)。没有安装或执行第三方仓库，没有发送 CISP 私有资料，没有新模型调用。

## 判断与第一版定位

当前第一版定位应为**历史评测证据审阅台**。它完成了原定的导入、追溯、原判保留、粗粒度版本对比、追加意见、脱敏导出和完整性检查，本机及精确提交CI的通过记录仍有效。这些工程证据不能证明它已经具备完整的实验执行、指标管理、重复实验分析、审阅协作和团队权限体系。

下一轮应以“一次实验能执行、能解释失败、能复现”为目标。继续增加总览卡片，对当前回答质量问题的帮助有限。

## 实际参考范围

| 项目 | 固定提交 | 本次重点 |
|---|---|---|
| promptfoo/promptfoo | 6c06c8f14d1e6d4b7439634354da5b4d77fc26c7 | 执行器/存储分层，逐题结果，恢复与持久化失败 |
| langfuse/langfuse | 44993fc5156dafddf7ffcc145c57b7011fa05dab | 项目作用域、数据集项版本、trace关联、比较列与审阅队列 |
| Arize-ai/phoenix | 464a60fd3c24aaed780cea3f2e0e437d3c8ba037 | 数据集修订、重复运行、逐次trace/评分/费用和比较界面 |
| vibrantlabsai/ragas | 298b68274234c060deacab3cf5fb52aa3a20e885 | 单轮/多轮样本、主张级忠实度、输入校验与运行配置 |

旧 explodinggradients/ragas API 返回迁移信息；连接器不支持其按仓库ID的重定向端点，随后通过 GitHub 的主站搜索定位到 vibrantlabsai/ragas，再读取新仓库源码。没有凭旧路径或搜索摘要推断实现。

## 1. 实验执行：参考 promptfoo 的职责分离

**源码观察。** [运行时设计](https://github.com/promptfoo/promptfoo/blob/6c06c8f14d1e6d4b7439634354da5b4d77fc26c7/docs/architecture/evaluator-runtime.md) 区分整批编排、单题执行与运行时组合。[EvaluationStore](https://github.com/promptfoo/promptfoo/blob/6c06c8f14d1e6d4b7439634354da5b4d77fc26c7/src/evaluator/runtime.ts#L3-L55) 的逐题结果包含分数、命名指标、失败原因、耗时、费用、模型响应和测试输入；它还显式暴露持久化失败。[engine](https://github.com/promptfoo/promptfoo/blob/6c06c8f14d1e6d4b7439634354da5b4d77fc26c7/src/evaluator/engine.ts#L1806-L1823) 在恢复时读取已完成结果索引，在比较时保留未成功持久化的失败结果，防止漏掉失败后得到虚高比较结果。

**CISP现状。** [offline_check](../../../storage/evaluation_store.py) 只检查包哈希、数据集哈希、结果数量，没有发起检索或执行评分；eval_runs 是导入快照，没有真正运行中的实验、逐题进度、取消或中断恢复。原定第一版范围允许这样交付，但“完整性检查”不应在产品或文档中被解释为“重新测评”。

**建议。** 复用现有冻结协议与审计脚本，建立明确的实验计划、单题运行及独立评分记录。先提供固定白名单的离线执行任务，持久化状态、已完成题目和逐题错误，再考虑已批准范围内的真实模型任务。付费调用禁止自动重试；取消请求不能被直接记成底层调用已停止或费用已知。

不直接搬入其TypeScript执行器，也不为一个执行器先搭多层抽象。promptfoo自己的设计说明也区分存储隔离与provider/全局资源隔离，不能仅拆一个接口就声称CISP已实现全链路隔离。

## 2. 数据集版本与运行实体：参考 Phoenix、Langfuse

**源码观察。** Phoenix将 Dataset、DatasetVersion、DatasetExample、DatasetExampleRevision、Experiment和ExperimentRun分开。修订记录关联版本及CREATE/PATCH/DELETE，实验绑定数据集版本。[模型定义](https://github.com/Arize-ai/phoenix/blob/464a60fd3c24aaed780cea3f2e0e437d3c8ba037/src/phoenix/db/models.py#L1579-L1745)。Langfuse的数据集项带 validFrom/validTo，并可关联来源trace；dataset-run-item同时保存数据集项版本与trace/observation标识。[schema](https://github.com/langfuse/langfuse/blob/44993fc5156dafddf7ffcc145c57b7011fa05dab/packages/shared/prisma/schema.prisma#L732-L813)、[运行项结构](https://github.com/langfuse/langfuse/blob/44993fc5156dafddf7ffcc145c57b7011fa05dab/packages/shared/src/domain/dataset-run-items.ts)。

**CISP现状。** 当前仅3张表：整个批次与题集放在eval_runs.payload JSON里，另存意见与检查。dataset_id是题目、判据和角色的摘要，缺少稳定的数据集身份、父版本、题目修订及单独结果行。新增一题只能形成新的整包快照，页面没有清楚展示版本间题目变更。list_runs读取所有批次JSON，逐题分页也是解析整包后内存切片，不能称为数据库层分页。

**建议。** 保留原始导入包作为不可变证据，另建立可查询的数据集、版本、案例版本、实验和逐次结果。评分协议应独立版本化，不能只用一段criteria字符串代表评分器身份。只对独立测评库做显式版本升级，不迁移默认业务库。先满足当前题集规模和可追溯查询，无需引入ClickHouse等新存储。

## 3. 重复实验：这对CISP是已有需求

**源码观察。** Phoenix的Experiment带repetitions；ExperimentRun的唯一约束包含experiment、example、repetition_number，并记录trace_id、开始/结束、token和error。[逐次运行](https://github.com/Arize-ai/phoenix/blob/464a60fd3c24aaed780cea3f2e0e437d3c8ba037/src/phoenix/db/models.py#L1818-L1860)。[重复组](https://github.com/Arize-ai/phoenix/blob/464a60fd3c24aaed780cea3f2e0e437d3c8ba037/src/phoenix/server/api/types/ExperimentRepeatedRunGroup.py) 保留每次结果并提供耗时、费用和评分聚合，界面可选择具体重复轮次。

**CISP现状。** Bundle要求results内id唯一；同一批次不能自然表示同一道题多次运行。把重复题改名并不能替代稳定案例ID与attempt序号。路线图已有“首轮8/8、重复6/8或7/8”的不稳定问题，这不是为了将来才准备的功能。

**建议。** 以稳定case_id + case_version + attempt_no区分运行；失败、未知与每次费用全部保留。展示通过次数/实际次数及每次判定，不能挑最好一次。历史重复ID只有在manifest提供明确映射时才能合并，不能靠字符串猜测。先验证已有重复记录和合成输入，不新增付费实验。

## 4. 比较界面：应直接回答“差在哪里”

**源码观察。** [Phoenix比较表](https://github.com/Arize-ai/phoenix/blob/464a60fd3c24aaed780cea3f2e0e437d3c8ba037/js/app/src/pages/experiment/ExperimentCompareTable.tsx#L392-L511) 同行展示输入、参考输出和实验输出，并呈现指标、耗时/费用差异，提供trace抽屉及重复轮次选择。[Langfuse比较表](https://github.com/langfuse/langfuse/blob/44993fc5156dafddf7ffcc145c57b7011fa05dab/web/src/features/datasets/components/DatasetCompareRunsTable.tsx) 同样将输入、预期输出及各运行列组织在一起。

**CISP现状。** compare()只返回题号和前后总体状态，前端比较表没有两版答案、引用、分项指标或直接证据入口；使用者要回批次页重新找题。两版都失败也只记总体状态未变，无法从比较表看出失败原因是否变化。批次配置仅提示“有变化”，没有逐字段差异。

**建议。** 第一屏按题号对齐两版回答，展示引用/检索来源和逐指标变化；支持仅看退化、分项退化、失败原因改变。点退化项直接展开该题的两版证据和配置差异。保留当前总体状态计数，但将“未变”明确为总体状态未变，避免误读为内容与错误原因完全没变。

## 5. 评分：保留独立维度及评价者身份

**源码观察。** Phoenix的ExperimentRunAnnotation分开保存评价名称、评价者种类、数值、标签、解释和评分自身的错误/trace，区分CODE、LLM、HUMAN等来源。[定义](https://github.com/Arize-ai/phoenix/blob/464a60fd3c24aaed780cea3f2e0e437d3c8ba037/src/phoenix/db/models.py#L1862-L1895)。Ragas区分单轮与含人类/AI/工具消息的多轮样本，[样本定义](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/src/ragas/dataset_schema.py#L54-L137)。当前collection版[Faithfulness](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/src/ragas/metrics/collections/faithfulness/metric.py) 要求问题、回答、检索上下文，提取主张后逐项检查支持程度；缺失输入报错，无可评分主张时返回NaN。

**CISP现状。** checks字典和semantic_passed虽然保留原值，case_status仍将它们归并为一个三态结果。没有独立的evaluator_id/version、评分配置/提示摘要、适用输入及校准状态；“模型不能评分”“执行失败”“语义不支持”不能在比较页充分展开。

**建议。** 最少分为检索命中、引用支持、答案语义、拒答范围、执行完整性；每个评分保存类型、版本、输入/答案摘要、状态、依据和原始结果。保留现行判据，不为通过改标签。Ragas可以作为可选适配器或导出格式参考，其分数不自动成为CISP金标；当前审阅校准3/8问题必须继续显示未通过。多轮消息及工具调用也应进入输入契约，不只靠一个问题字符串判断输入等价。

## 6. 审阅：从意见记录推进到待办工作流

**源码观察。** Langfuse有AnnotationQueue、QueueItem和Assignment，记录PENDING/COMPLETED、锁定者/时间、标注者及完成时间，并关联ScoreConfig。[schema](https://github.com/langfuse/langfuse/blob/44993fc5156dafddf7ffcc145c57b7011fa05dab/packages/shared/prisma/schema.prisma#L622-L730)。[前端状态转换](https://github.com/langfuse/langfuse/blob/44993fc5156dafddf7ffcc145c57b7011fa05dab/web/src/features/annotation-queues/state/annotationQueueRun.ts) 还区分完成失败与完成后刷新失败，保存已确认完成状态，避免重复完成和错误跳题。

**CISP现状。** 当前追加意见和幂等键是有效基础，但没有待审分配、审阅进度、统一rubric、分歧归集或裁决状态。kind只是用户声明，不能证明独立人工审校已发生。

**建议。** 将真实失败、缺证据和AI/人工分歧送入待审队列；先做待审→审阅中→已审→需裁决，绑定评分标准版本和意见依据。没有外部人员时显示待人工，不补造人名或金标。当前单人阶段无需实现复杂团队调度，但审阅完成应有明确含义。

## 7. 追踪与访问范围

Langfuse运行项关联trace/observation、项目ID；Phoenix逐次运行和评分也关联trace。CISP打包器目前只提取token回答、contexts和已保存检查，丢掉了原结果中可用的trace、阶段事件及逐题用量；artifact摘要也不能直接提供可展开的追踪。

建议优先保留隔离证据中的request/trace标识、检索与生成阶段、失败点、用量和已知/未知费用；无记录明确为空，不能补出历史遥测。导入trace应来自获准产物，不从浏览器开放任意文件路径或直读默认业务库。

Langfuse模型以projectId限定数据和关联键；CISP当前访问名单中的账号共享全部批次，visibility是声明标签，并非访问控制。多人使用前需补明确工作区/项目作用域及读取、复核、执行权限。单用户本机体验可以维持现有隔离边界，不能宣传为团队级权限已经完成。

## 不直接照搬的部分

1. **运行参数。** Ragas本次查看的[RunConfig](https://github.com/vibrantlabsai/ragas/blob/298b68274234c060deacab3cf5fb52aa3a20e885/src/ragas/run_config.py#L51-L61) 默认max_retries=10、max_workers=16。Langfuse的[创建实验队列](https://github.com/langfuse/langfuse/blob/44993fc5156dafddf7ffcc145c57b7011fa05dab/packages/shared/src/server/redis/experimentCreateQueue.ts#L17-L33) 也配置了队列重试，但这不等同于直接的provider调用重试。CISP集成时必须检查完整外发路径，执行既有Flash、关闭思考、无自动重试、冻结费用上限规则。
2. **部署栈。** 参考实体关系和交互，优先复用现有Python服务、隔离SQLite和现有评测逻辑。多服务队列、分析数据库和完整前端框架迁移都不属于本次调研的结论。
3. **许可。** 本次查看的promptfoo主LICENSE为MIT许可文本；Langfuse主LICENSE按目录区分MIT与EE许可；Phoenix主LICENSE为ELv2；Ragas主LICENSE为Apache-2.0。具体链接见sources.json。不能统称为可任意复制的MIT源码；本次只记录设计与事实，未复制第三方实现代码，后续复用前逐文件核对。

## 下一轮建议：四个可验收的交付切片

| 顺序 | 具体交付 | 验收要求 |
|---|---|---|
| 1 | 数据集/版本、逐题逐次结果、独立分项评分；保留原始导入快照 | 当前两批40题原判和分母不变；同题3次结果可并存；缺失与失败分开；旧包仍能查看 |
| 2 | 两版回答/来源/指标并排比较与trace详情 | 点击退化项直接解释本次历史3个退化；显示1修复/3退化/36总体状态未变；同时失败但原因改变可见；不支持输入等价时明确不可比较 |
| 3 | 现有离线评测/审计器接入真实任务状态与逐题进度 | 页面创建冻结任务，能看到执行与逐题错误；中断不报通过；恢复不重复执行完成项；无付费调用、无任意shell入口 |
| 4 | 最小审阅队列、rubric版本和分歧状态 | 失败题可进入待审；复核与原判分别保留；没有真实审校者时仍为待人工；重复提交/导航失败不会重复完成 |

每个切片单独保留冻结输入、基线、变更、定向回归及浏览器证据。先完成上述闭环，再评估在线模型执行和数据中台。在线执行需要重新核对当时费用账本、批次预算及允许外发的材料，当前调研不构成新付费授权。

## 调研局限与状态

这是对固定提交中相关源码片段及当前CISP实现的静态对照，未运行或体验第三方部署，不评判其生产SLA、所有功能或默认配置在所有路径上的行为。不以star数决定取舍。18份源码/设计文件和4份许可证的可复查位置均已登记。

本轮只新增调研文档与入口链接，不改应用逻辑、不改变V1验收结果、不宣称以上缺口已补齐。V1交付提交a5a07f2的远端CI42578062434已在上轮核实success；本轮若提交文档，需单独核对新提交CI。
