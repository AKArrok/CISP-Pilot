# 四项升级使用与协议说明

本文对应V2应用；V1原始导入与原判保留。所有操作均针对独立测评库，未迁移或部署默认业务库。

## 本机直接体验

```powershell
.venv-win/Scripts/python.exe scripts/serve_evaluation.py --workspace .evaluation-local-v2 --port 9541
```

设置演示账号密码后，访问 `http://127.0.0.1:9541/evaluation.html`，账号为eval-reviewer。演示启动器会显式初始化/升级自己目录内的独立中台库，并启用零模型离线worker；已有同目录账号需要原密码。不会使用默认业务库；Ctrl+C停止。

## 已有V1中台库升级

先停止该服务/worker并备份**已配置的测评库**。对准确目标执行：

```powershell
.venv-win/Scripts/python.exe scripts/migrate_evaluation.py --database data/evaluation/platform.sqlite3
```

示例路径应替换为实际CISP_EVAL_DB_PATH，不能使用data/cisp_qa.db。脚本拒绝默认/配置业务SQLite库及含其他表的数据库。升级显式执行、幂等；旧包摘要被破坏时整轮回滚，不产生部分V2表。原eval_runs/payload、eval_reviews、eval_checks不重写。备份可用于回滚；不要把带V2表的库直接当作未升级V1库交给旧程序。

正常服务默认不启动worker。完成升级后，在现有认证、Origin和测评账号配置之外设置并重启服务：

```powershell
$env:CISP_EVAL_DB_PATH='data/evaluation/platform.sqlite3'
$env:CISP_EVAL_WORKER_ENABLED='true'
```

不启用worker时页面明确提示，创建的任务保持排队；不假称正在执行。所有获准账号仍共享一个工作区，当前不是项目级RBAC产品。

## 导入带trace的公开历史包

```powershell
.venv-win/Scripts/python.exe scripts/package_evaluation.py --packet evaluation/nist-public-http-question-parts-regression40-v2-20261107 --run docs/engineering-delivery/nist-public-http-question-parts-regression40-v2-20261107-live --output data/evaluation/release-a.json --schema-version 2 --dataset-key public-nist-http --confirm-public
.venv-win/Scripts/python.exe scripts/package_evaluation.py --packet evaluation/nist-public-http-question-groups-regression40-v3-20261107 --run docs/engineering-delivery/nist-public-http-question-groups-regression40-v3-20261107-live --output data/evaluation/release-b.json --schema-version 2 --dataset-key public-nist-http --confirm-public
```

输出文件排他创建，已有文件不会覆盖。默认schema-version仍是1，兼容旧流程。新版保留原trace、阶段事件、request_id、客户端耗时及可确认的观测token；掉落/未观测调用时token保持未知。单次费用不由批次金额摊算。历史语义标签来自原审阅，不重新评分、不称人工金标。

schema2的dataset_key是稳定逻辑身份，题目/契约/判据变化产生新的版本摘要。不同数据集应显式使用不同key。旧包以声明的名称与角色建立兼容身份，不猜测它与新显式key的等价性。原包仍可查看；不同协议或契约表示可能被保守标为不可比较，不能为提高可比率修改原题或标签。

打包器还在schema2题目契约中记录同thread_group此前的用户问题顺序，避免仅以当前问题判定多轮输入一致。它不补造历史助手消息，也不证明任意上传包的隐含上下文完整；调用者须将真实固定输入纳入契约。

## 1. 版本、次数与分项

数据集页显示逻辑身份、版本、显式父版本（未知时为空）、案例和实验数。案例页的次数选择器可查看每次结果、缺失、独立分项、判据摘要及原始trace。

每题汇总采用“任一次失败→失败；无失败但有缺失/未知→待证据；全部通过→已通过记录”。页面同时显示通过次数/计划次数，不挑最好一轮。不同attempt不是独立新题，统计题数和次数分别报告。

分项维度为检索、引用、语义、拒答范围、执行。记录包含指标ID、kind(code/ai/human)、evaluator_version、status(passed/failed/unknown/error)、可选数值、依据、输入摘要和rubric摘要。旧判据映射为legacy-import-v1，这表示导入适配版本，不证明原评分器身份；原始检查与语义判定不覆盖。声明的human类型也不构成独立审校认证。

## 2. 并排比较

选择基线和候选，按题对齐。可筛选退化、修复及总体状态未变但分项有变化的项。点击题号，直接显示两版回答、检索片段、参考契约、trace、逐指标差异和文本diff；两侧可分别选择重复次数。配置差异逐字段显示，缺失字段与null分别记录。

输入契约、角色或判据不同会标不可比较；缺失证据不算修复。“未变”仅指汇总状态，不能据此认定文本或错误原因完全相同。当前两批公开40题仍应为1修复、3退化、36总体状态未变。

## 3. 真正执行离线审计

在选定批次点击“创建离线审计任务”，进入离线任务页。固定命令saved-http-audit-v1实际调用现有scripts/audit_flash_answers.py的citation_checks，并检查答案摘要、适用时的目标来源与已记录SSE结束事件。非知识路径或明确无上下文拒答按冻结契约判定引用检查不适用，不制造引用失败。

任务冻结原始导入、规范化逐次输入和审计器依赖字节摘要（包装器、引用检查及来源定位代码）。不会接受shell、本机资料路径或模型配置。输出另存于任务结果，不回写原始评分。

- queued：排队；running：持有租约执行；completed：所有项执行结束，**仍可能有未通过检查**。
- failed：任务异常或存在缺失/执行错误；cancelled：取消请求已在安全边界落实；interrupted：进程停止或60秒无心跳。
- 运行中取消只请求停止，不能声称已经抢占正在执行的函数。页面分别显示执行进度、未通过检查、缺失/执行错误。
- “继续未完成项”显式恢复停止的任务，已保存的项不重复执行。检查失败仍是已完成的检查；缺失/错误记录保留。冻结输入或代码摘要变化时拒绝混合协议恢复，应新建任务。
- worker以SQLite事务领取单个有效租约；过期token不能继续写结果。该机制不等于已验证分布式生产调度或严格物理进程抢占。

当前只重查保存证据，不重新调用RAG或模型、不重新做语义判分。真实模型执行、费用批准和私有资料外发仍需另外核对授权。

## 4. 审阅队列

案例/次数详情可点击“加入审阅队列”，同一答案/判据去重。领取后锁定15分钟，可在队列中查看冻结标准、回答和意见，再提交复核。状态为待审、审阅中、已审、待人工/需裁决、已裁决。

AI辅助意见保持待人工/需裁决；需要更多证据或与原语义判定分歧也进入需裁决。另一获准账号可领取裁决，原复核者不能自行裁决；裁决需声明人工类型并填写依据。账号声明本身不证明真实独立人类身份，系统始终不自动标独立人工金标或晋级默认回答。

完成请求绑定case/attempt、答案SHA和rubric版本，使用request_id幂等；锁过期或其他账号持有时拒绝写入。已确认完成后重复提交不会新增意见；更改内容不能重用旧幂等键。原判、追加意见、裁决分别保留，不能为裁决覆盖历史失败。

## 数据表和新API

V2表：eval_meta、eval_datasets、eval_versions、eval_case_versions、eval_experiments、eval_attempts、eval_scores、eval_rubrics、eval_jobs、eval_job_results、eval_review_queue、eval_attempt_reviews。旧三张表保留。案例筛选/分页由SQL完成；不可变原包作为审计快照，原始证据文本不进入默认脱敏导出。

新API前缀/api/evaluation，仍需Cookie、测评访问名单，写请求还需Origin/CSRF：

| 方法 / 路径 | 功能 |
|---|---|
| GET /capabilities | 数据库版本及worker配置 |
| GET /datasets | 稳定数据集与版本 |
| GET /runs/{id}/cases/{case_id}?attempt=N | 指定次数、分项、trace及意见 |
| GET /compare-detail?left=...&right=...&case_id=... | 两侧证据及逐项差异；支持left_attempt/right_attempt |
| POST /runs/{id}/cases/{case_id}/attempts/{N}/reviews | 绑定判据与答案的次数级意见 |
| POST /runs/{id}/jobs | 创建固定离线审计，request_id幂等 |
| GET /jobs、GET /jobs/{id} | 任务状态与逐题结果；列表最近100任务 |
| POST /jobs/{id}/cancel、/resume | 请求取消或显式恢复 |
| POST /runs/{id}/cases/{case_id}/attempts/{N}/queue | 将指定次数加入审阅队列 |
| GET /review-queue?status=...&offset=...&limit=... | SQL分页队列 |
| POST /review-queue/{id}/claim、/complete | 领取或提交绑定答案/判据/lock_token的意见 |

schema2扩展：dataset_key、version_label、parent_version_id、repetitions(1–20)；results允许相同case不同attempt_no，禁止重复(case,attempt)。结果扩展metrics、trace、events、request_id、latency_ms、prompt_tokens、completion_tokens、known_cost_usd/cost_unknown。总计划最多10000次，导入仍限8MiB，案例最多2000条。完整字段定义以storage/evaluation_workspace.py的严格模型为准。

## 验证与限制

```powershell
.venv-win/Scripts/python.exe -m pytest tests/test_evaluation_platform.py tests/test_evaluation_workspace.py -q --basetemp=data/evaluation/new-v2-test-temp
.venv-win/Scripts/python.exe tests/run_evaluation_browser.py --baseline data/evaluation/release-a.json --candidate data/evaluation/release-b.json --output data/evaluation/new-v2-browser
```

浏览器测试创建临时合成应用和测评库，不应指向默认服务。重复标签和800项取消样例是原创合成工程测试，不证明答案准确率。任务结果页面按25项显示；任务API单个详情当前返回该任务全部结果，规模受计划上限约束，不宣称生产容量。

停止服务后备份整个独立测评库即可保留导入、任务与意见。关闭worker后任务可排队，禁止把排队误记执行。生产部署、完整项目级权限、独立人工金标、真实在线模型调度仍未完成或不属于本轮四项交付。
