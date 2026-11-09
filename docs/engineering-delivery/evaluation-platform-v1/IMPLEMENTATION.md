# 实现与验证记录

2026-11-09，Asia/Shanghai。本文逐阶段追加，失败轮次保留。无新付费模型调用。

## 输入与源码

- 起始核对时分支为 codex/nist-evaluation-20261030，后续核对 HEAD 为 c460e11；工作区含用户及并行工作留下的其他修改，不纳入本任务提交。
- 当前总路线图另有 auth-limit-20261109 接续，测评中台作为独立交付目录记录，不覆盖认证及语义调优状态。
- 默认业务库初始 SHA256：3d6d78663b9378d8791f1692afcdd702c9476866d5458c3492f307766e9d5b62。
- 选择公开 NIST 历史批次 question-parts-regression40-v2 与 question-groups-regression40-v3（20261107），各 40 题。输入核对及哈希清单见 history-validation.json（生成后有效）。
- 额度首次实际读取为五小时已用 36%，第二次 43%，均未达到剩余 10% 停止阈值；不用重置券。

## 阶段 1—3：数据导入与接口

已实现独立 SQLite 评测存储、严格 JSON schema、基于内容哈希的幂等导入、答案哈希绑定和追加式复核。HTTP 复用现有 Cookie、Origin 与 CSRF 检查，再以 CISP_EVAL_USER_IDS 显式授权。名单为空默认拒绝。

打包器只读取冻结产物并计算摘要，不重新评分、不调用 provider。CLI 需要 --confirm-public，确认所选输入均为获准公开证据；该确认不构成自动许可检测。导入来源和审阅者是产物声明，不是数字签名身份认证。

数据库目标防护拒绝默认业务库、当前配置的 SQLite 业务库（含别名/硬链接）及含非中台表的其他数据库。浏览器不能指定文件系统路径。

## 阶段 4—6：界面与离线检查

已实现总览、冻结数据集、批次列表、状态/关键词筛选、分页、逐题回答/检索片段/原始审阅、版本比较、追加复核和 JSON/Markdown 下载。所有导入文本使用 textContent 呈现。

比较要求相同题号、问题、题目契约、判据和角色；新旧缺题及证据不足另列。配置变化明确提示，比较只反映历史开发判定变化，不证明因果改进。

离线任务仅执行内置 integrity-v1，验证包摘要、数据集摘要和结果完整性；同步原子持久化，不运行任意 shell，不创建后台队列。中断时不提交完成记录，重复执行返回同一记录；检测到外部修改与旧检查不一致时拒绝返回旧绿灯。此任务不重新检索、不校验原始文件当前是否仍存在，也不执行语义判分。

导出采用字段白名单：排除问题、回答、原文、配置、产物路径、自由文本复核及明文账号，保留内容摘要、分母、判定、复核类型和时间。低熵文本的摘要不是不可逆匿名化保证，导出仍按实际资料范围处理。

## 已执行验证及失败

1. 首轮 ruff 发现 Depends 默认值及未使用 import；仅修复本轮引入项，后续定向 lint 通过。
2. 普通沙箱 pytest：7 项不使用临时目录的输入检查通过，其余 setup 因 WinError 5 / tmp_path 访问失败，退出时同样报临时目录权限错误；未把环境错误当成产品失败或通过。原始终端回执保留在本聊天。
3. 隔离提权重跑：15 passed，1 个既有 Starlette/anyio 弃用警告，见 backend-tests-first.log。后续修改需最终重跑。
4. 演示启动首次使用 data 下目录，被既有 prepare_demo 的默认数据目录防护拒绝。改用独立 .evaluation-demo-v1，并在启动器提前拒绝 data 子目录，保留原防护。
5. Chromium 首次启动被沙箱 spawn EPERM 阻断，见 browser-first.log；获自动权限审查后在本机隔离演示上重跑，见 browser-second.log。
6. 首轮浏览器实际完成 15 项检查，无 pageerror；两历史批次综合原判分别 39通过/1失败、37通过/3失败，对比为修复1、退化3、未变36。注意 v3 的 AI 语义支持本身为38/40，联合执行与语义后为37/40，不混用。
7. 桌面与390px移动截图已生成，代理通过图像工具查看两种布局，不属独立人工验收；浏览器检查移动端无页面横向溢出。截图及导出报告保留于本机 data/evaluation/browser，不公开上传原始文本。
8. 最终定向复测15 passed；补充了外部篡改后不能复用旧完整性绿灯的断言。最终浏览器在全新临时演示中完成15项工作流检查，再重启服务验证批次、意见和原检查记录持久化；默认业务库/知识片段/manifest前后哈希一致。见 browser-summary.json、protected-assets.json。临时服务已退出，早期本机演示也已核对进程身份后停止。

## 待最终核实

最终结论以本目录 VALIDATION.md 和 continuation.json 为准。

## 扩大回归与首次远端失败

- 完整本机隔离回归：1056 passed、3 skipped、2 warnings，817.22秒。跳过和原始输出保留于 isolated-regression.log；该次运行包含工作区原有修改，不能代替干净提交 CI。
- 两个警告分别是既有 Starlette/anyio 弃用，以及既有 holdout 工作流的 Windows 子进程 stdout 解码。后者以 PYTHONUTF8=1、将 PytestUnhandledThreadExceptionWarning 视为错误重跑原测试，1 passed、无警告，见 encoding-recheck.log。未改动旧测试或产品编码逻辑。
- 实现提交 de5f21dacb6b4b10a2c62272ba09f10fa24e2616 已推；[CI 42576340660](https://github.com/AKArrok/CISP-Pilot/actions/runs/42576340660) 失败，3 failed、588 passed、13 skipped。三个失败位于旧认证审计合成夹具。原始失败日志保留本机 data/evaluation/implementation-ci-failed.log。
- 干净 git archive 快照复现同样3失败，见 ci-clean-repro.log。定位：夹具复制当前运行代码，却沿用历史计划中的源码字节哈希；新路由改变 server.py，跨平台换行也令 auth/cache 字节不同。失败项为 runtime_code_unchanged。
- 最小修复只在夹具复制循环中，把临时计划中的对应哈希绑定到刚复制的文件；不改审计器、历史 plan、原实验判据或冻结证据。干净快照复测3 passed，见 ci-clean-fixed.log；额外篡改临时 server.py 后审计仍失败，见 ci-runtime-negative.log。
- 工作区已有别人留下的同文件修改，未纳入提交。只用索引补丁暂存本轮这一行并核对差异；首轮上下文补丁因混合换行未应用、没有产生提交，改用LF补丁成功。修正提交 f73b7ce 已推，其精确 CI 42577191076 已 completed/success。
- Git首次暂存的自动权限审查超时，未执行；按允许方式重试成功。普通沙箱远端读取另遇 Windows 凭证不可用，使用已有账户在授权环境读取后确认远端提交。未改变凭证或配置。

## 代码验收完成

远端 f73b7ceb2755a72c5fd104f126728a9a46b2a169 的干净CI为591 passed、13个预期资产跳过；同一运行的合成回答门禁、Linux运行镜像构建、Docker smoke与Linux浏览器smoke均成功。见 code-ci.json、code-ci-tests.log。第一次失败未隐藏，修正只改变合成测试夹具绑定，没有降低判据。

本机新中台浏览器为15项工作流及1项重启验证；远端浏览器smoke是项目现有工程检查，两者分别报告。默认业务库最终SHA256与初始相同，零新增模型费用。最后阶段实际额度五小时63%已用，未触发10%剩余额度停止阈值。

第一版范围完成：离线证据管理、比较、追加复核及脱敏报告可用；未运行新的付费语义实验，未迁移默认库、未部署。后续文档证据提交不更改应用代码，其精确远端/CI在最终交付回执单独核对。
