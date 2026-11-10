# 数据中台第一版验证

状态：首版功能及定向/浏览器验收完成；本机完整回归非全绿，两个失败均有上一提交同输入复现证据。无部署、无新模型调用、不改原语义判定。

代码提交`759a388abba5a39f6f76004a2810165b6ecade75`已推送至`codex/nist-evaluation-20261030`，[精确提交CI42777739163](https://github.com/AKArrok/CISP-Pilot/actions/runs/42777739163)全部通过。远端625 passed/13预期资产跳过/2依赖警告，64.09秒；其余lint、合成答案、Linux镜像、容器和浏览器门禁均success，见[code-ci.json](code-ci.json)。数据中台完整UI流程由本机浏览器额外覆盖，不混称远端冒烟已覆盖所有新页面。文档收尾提交CI在最终交付消息另行核实。

| 范围 | 实际结果 | 证据 |
| --- | --- | --- |
| 后端首轮 | 47 passed / 1既有依赖警告 | [backend-first.log](backend-first.log) |
| 后端最终边界 | 49 passed / 1既有依赖警告 | [backend-final.log](backend-final.log) |
| 冻结知识指纹适配器 | 1 passed，14 deselected；schema1保持兼容 | [package-final.log](package-final.log) |
| 首轮真实浏览器 | 24项功能及服务重启通过 | [browser-first.log](browser-first.log) |
| 后续界面复测 | 24项功能通过；重启登录5秒ReadTimeout，整轮失败 | [browser-final.log](browser-final.log) |
| 同输入重启复测 | 24项功能+重启通过，0页面异常，默认资产哈希不变 | [browser-release.log](browser-release.log)、[摘要](browser-summary.json)、[资产保护](protected-assets.json) |
| 账本可读性调整后 | 24项功能+重启通过；先显示假设/证据/验收与整批变化，绑定细节折叠 | [browser-readable.log](browser-readable.log) |
| 历史数据对账 | 两批各40题保留39/40及37/40；比较1修复/3退化/36未变 | [history-validation.json](history-validation.json) |
| 本机私有知识观察 | 1077块/30来源；数量、索引ID/顺序、父段和子段重复检查通过；原文指纹/历史执行未知 | 同上，仅计数及摘要公开 |
| 静态检查 | Git跟踪Python及本轮新增Python lint通过；JS语法检查通过 | [tracked-lint.log](tracked-lint.log) |
| 本机完整回归 | **1085 passed / 2 failed / 4 skipped / 1 warning**，1632.43秒，非全绿 | [isolated-regression.log](isolated-regression.log) |

浏览器功能包括实际登录/导入、101块公开知识搜索分页、哈希绑定的失败追溯、多候选和切片详情、绑定改进项、关联历史候选并保留整批3项退化、恶意元数据按文本渲染、匿名拒绝、移动端无页面横向溢出。V1/V2原测评流程同时回归。所有工程操作由代理完成，不属于独立人工验收。

## 已发现的问题及处理

1. CISP structured_v1父段内嵌在text、检索子段在child_text；资产检查按该实际模型处理。合成测试覆盖独立父块、内嵌父段及真正缺失。
2. 公开知识快照101条有71个重复正文，质量检查保留失败。每批80个历史预览上下文均出现多候选，不伪造唯一切片血缘。重复可能与辅助检索表示相关，尚未确认根因，未修改原数据。
3. 本机30来源在此次明确原文目录中没有取得文件指纹；不猜原文件版本，也不把当前处理代码指纹当历史执行证明。
4. 最终界面复测的重启登录默认5秒读取超时发生于并行全量/认证测试期间。保留失败日志，测试客户端显式设30秒读取上限，同输入再验证；没有降低密码参数、改变应用认证策略或增加自动重试。该变更是工程测试等待设置，不构成生产登录延迟承诺。
5. 本机既有未跟踪用例`tests/test_budget_integration.py::test_timeout_blocks_late_thread_commit`在50ms耗尽前未进入目标后台写入，导致entered/finished断言失败；[当前版定向复现](budget-repro.log)和[上一提交514e6b4同字节测试](budget-baseline.log)均失败，见[对照摘要](budget-baseline-comparison.json)。两次超时具体发生于前置恢复/图事件阶段，均未到所需测试接缝。没有修改该既有测试、预算或问答实现。主机调度/前置耗时判断仍是解释，不能凭此宣称生产预算安全性通过；本轮只证明该观测早于数据中台，局部超短时序验证仍有缺口。
6. 另一既有本机进程恢复用例`test_force_kill_running_trace_preserves_unknown_and_actual_history[after_commit]`在注册阶段遇到默认5秒ReadTimeout。完整回归及[当前版定向复现](recovery-repro.log)均失败。首次上一提交对照因缺本机索引启动失败，见[无资产探针](recovery-baseline.log)，**不能用这个不同错误证明相同故障**；随后仅在被忽略目录补齐相同本机只读资产，用完全相同测试/fixture重跑，亦在注册阶段出现同一5秒超时，见[有效基线复现](recovery-baseline-assets.log)及[测试/资产字节对照](recovery-baseline-comparison.json)。未复制业务库、未外发资产、未修改认证或恢复逻辑。精确主机性能原因尚未确认，保留为既有本机验证缺口，不宣称恢复能力本轮完整通过。

## 输入与方法

公开知识来自冻结包`evaluation/nist-public-http-question-parts-regression40-v2-20261107/chunks.json`，101块；两批历史实际问答包来自之前已冻结的公开NIST HTTP实验，不重发模型请求。新版schema2适配器仅补充经过验证的知识文件哈希；问题、答案、契约和原分不变。知识文件哈希与平台规范快照摘要分开。

复核命令（输出和数据库需新路径）：

```powershell
.venv-win/Scripts/python.exe scripts/verify_data_catalog.py --database data/evaluation/new-validation.sqlite3 --knowledge data/evaluation/data-platform-public-a.json --baseline data/evaluation/data-platform-run-a.json --candidate data/evaluation/data-platform-run-b.json --output data/evaluation/new-validation.json
.venv-win/Scripts/python.exe tests/run_evaluation_browser.py --baseline data/evaluation/data-platform-run-a.json --candidate data/evaluation/data-platform-run-b.json --knowledge data/evaluation/data-platform-public-a.json --output data/evaluation/new-browser-evidence
```

包生成步骤见[使用说明](USER_GUIDE.md)。`--local-knowledge`是可选本机采集验证，私有正文/路径只保存在被忽略的data/evaluation下，公开报告不含它们。

本机全量回归包含原工作区未提交测试，不能与干净CI的数量混称。两项失败未通过修改门限、跳过或更改原用例消除，当前主机上的完整回归仍非全绿。默认`data/cisp_qa.db`只读，初始及浏览器结束后SHA256均为`3d6d78663b9378d8791f1692afcdd702c9476866d5458c3492f307766e9d5b62`；完整回归1085 passed/2 failed/4 skipped/1 warning，1632.43秒，见isolated-regression.log。费用0；没有新依赖。源码字节摘要与公开输入摘要见[source-snapshot.json](source-snapshot.json)。
