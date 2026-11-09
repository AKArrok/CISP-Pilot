# 测评中台第一版使用说明

本页记录V1交付范围；新版本已接入逐次/分项结果、比较证据、离线任务与审阅队列，升级和实际验收状态见 [V2说明](../evaluation-platform-v2/USER_GUIDE.md) 及同目录README。

## 本机隔离体验

在仓库根目录执行（Windows 已有虚拟环境）：

```powershell
.venv-win/Scripts/python.exe scripts/serve_evaluation.py --workspace .evaluation-local --port 9541
```

终端会提示设置演示账号密码，长度 12–128 字符。浏览器打开 http://127.0.0.1:9541/evaluation.html ，用户名为 `eval-reviewer`，密码使用刚才设置的值。若端口已被占用，改为 9542 等空闲端口。

启动器只监听本机，首次在指定新目录创建原创合成应用资产、迁移隔离的认证数据库，并配置独立测评库；目录内自动写入 `.gitignore`，避免提交账号、库和证据。已有目录仅允许复用合成演示目录，必须提供匹配的已有账号密码。禁止将 workspace 放在默认 data 目录内。用 Ctrl+C 停止；同目录重启保留导入批次、复核和离线检查记录。

合成应用资产用于 readiness 与登录，并非回答准确率数据；历史评测记录需另行导入。启动器清空模型密钥并关闭 dense/rerank；不要在此演示里运行问答质量实验。

## 打包现有公开历史记录

```powershell
.venv-win/Scripts/python.exe scripts/package_evaluation.py --packet evaluation/nist-public-http-question-parts-regression40-v2-20261107 --run docs/engineering-delivery/nist-public-http-question-parts-regression40-v2-20261107-live --output data/evaluation/v2.json --confirm-public
.venv-win/Scripts/python.exe scripts/package_evaluation.py --packet evaluation/nist-public-http-question-groups-regression40-v3-20261107 --run docs/engineering-delivery/nist-public-http-question-groups-regression40-v3-20261107-live --output data/evaluation/v3.json --confirm-public
```

`--confirm-public` 表示操作者确认所选资料均在允许使用的公开范围内，不会自动审查许可。打包器验证冻结文件哈希、题目一致性和审阅绑定，保留判定，不执行新模型请求。输出文件使用排他创建，已有文件不会被覆盖；重复打包请选新文件名。原始历史文件缺失时明确失败，不下载、不合成替代成绩。

1. 在“评测批次”选择“导入 JSON”，上传两个文件；单包不超过 8 MiB、2000 题。
2. 打开 v2 批次，状态选“失败”，点击 `scenario-03`，查看实际回答、片段、原始 AI 审阅和答案摘要。
3. 追加复核时选择真实使用的类型并写依据。个人查看来源可记人工复核，但系统不会因此标记独立双人金标；使用 AI 的检查应记 AI 辅助复核。表单绑定答案版本，不能覆盖原分。
4. 在版本对比中，基线选 v2、候选选 v3。已核对的结果应为修复 1、退化 3、未变 36。这是历史开发判定的比较，不是本轮新评测。
5. 点击“离线完整性检查”，查看冻结导入包的摘要及结果完整性。完成仅证明这些工程检查通过，不判定语义或生产可用性。
6. 下载 JSON/Markdown 脱敏报告。文件不含原始题目、回答、片段、自由文本复核、明文账号或配置。完整证据仍从获准本机工作台查看。

## 接入已有 CISP 服务

页面为 `/evaluation.html`，学习应用导航也有入口。部署前使用现有登录账号，通过 `/api/auth/me` 获取本人 user.id，由服务维护者配置：

```powershell
$env:CISP_EVAL_DB_PATH='D:\explicit-isolated-directory\platform.sqlite3'
$env:CISP_EVAL_USER_IDS='approved-user-id-1,approved-user-id-2'
```

然后按项目既有方式启动服务。名单为空时全部拒绝；所有获准账号共享同一实验工作区，第一版没有项目级或逐批次授权，不应把互不信任的人员加入同一个名单。已有服务的认证和 readiness 仍生效；中台不自动迁移默认业务库。正式部署、业务库迁移需另行授权。

## 数据字典与 API

规范化导入包 schema_version=1，所有未知顶层字段拒绝，数值费用必须有限且非负。

| 字段 | 含义 |
|---|---|
| name / dataset_name | 批次和数据集显示名，不进入默认脱敏导出 |
| role | development / regression / unseen / synthetic；为输入声明，不构成隔离证明 |
| visibility | public / private / synthetic；为输入声明，获准账号均能读取 |
| criteria | 原始评分标准文本；变化后不计算共同题修复/退化 |
| config | 冻结运行配置；对比提示差异，不进入默认导出 |
| artifacts | 来源标识到 SHA256 的映射；不能据此读取本机路径 |
| cases[] | id、question、contract（来源要求、预期行为等原始契约） |
| results[] | id、answer、answer_sha256、contexts、checks、semantic_passed、semantic_reviewer、semantic_reason |
| known_cost_usd / cost_unknown | 历史已知保守估算和未知费用标志；不等于实际账单 |

`checks` 是记录下来的布尔分项，语义判定为 true/false/null。任一 false 为失败；缺少结果、没有工程检查或语义判定为空为待证据；两者齐全且全通过才记已通过记录。不自动改写或推断新分数。

SQLite 三张表：`eval_runs` 保存不可变 JSON 快照、批次/数据集摘要、导入人及时间；`eval_reviews` 保存追加式意见、账号、时间及客户端幂等键；`eval_checks` 保存同步完整性检查命令和结果。表只写入独立中台库。批次摘要绑定整个规范化包；数据集摘要绑定排序后的题目、判据与角色。

API 前缀 `/api/evaluation`：

| 方法与路径 | 功能 |
|---|---|
| GET /runs | 批次统计及当前获准账号 |
| POST /imports | 上传规范化 JSON，内容幂等导入 |
| GET /runs/{id} | 批次配置、判据与产物摘要 |
| GET /runs/{id}/cases | status/query/offset/limit 筛选分页，limit 最多100 |
| GET /runs/{id}/cases/{case_id} | 题目、结果及复核历史 |
| POST /runs/{id}/cases/{case_id}/reviews | 绑定答案 SHA256 的追加复核，request_id 幂等 |
| GET /compare?left=...&right=... | 逐题比较原始记录 |
| POST /runs/{id}/checks | 固定 integrity-v1，同步执行、持久化返回 |
| GET /runs/{id}/export?format=json或markdown | 脱敏下载 |

读取需 Cookie 与测评账号授权；写入还需同源 Origin 及登录会话 CSRF。接口不接收可执行命令或服务器路径，失败不返回原始异常数据。

## 限制与回滚

第一版支持一种 HTTP 历史产物适配器及通用规范包，离线任务为同步工程检查。没有在线模型调度、自动语义判分、完整任务队列、学生考试或数据中台。批次总览当前读取全部已导入批次，适用于本机小规模使用；逐题分页。导入的判定和审阅者身份由产物声明，哈希绑定只证明内容一致，不证明判分正确。

回滚时先停止本机演示；保留整个 workspace 即保留账号、导入快照和复核记录。现有应用可将 CISP_EVAL_USER_IDS 设为空关闭数据访问。移除新增路由及静态入口不会影响默认业务库，无业务库降级迁移。不要自动删除实验目录。

## 重跑验证

```powershell
.venv-win/Scripts/python.exe -m pytest tests/test_evaluation_platform.py -q --basetemp=data/evaluation/new-test-temp
.venv-win/Scripts/python.exe tests/run_evaluation_browser.py --baseline data/evaluation/v2.json --candidate data/evaluation/v3.json --output data/evaluation/browser-repeat
```

浏览器测试使用已安装的 node/playwright，自动创建临时合成演示、执行浏览器工作流、重启检查持久性并退出；会写测试复核且明确标为 AI 辅助流程检查。不要将测试脚本指向默认服务或真实业务库。Windows 沙箱可能阻止临时目录或 Chromium 子进程，需按环境权限规则执行，不能据此改掉产品断言。
