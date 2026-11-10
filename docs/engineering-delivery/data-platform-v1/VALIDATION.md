# 数据中台第一版验证

状态：核心功能已实现，完整回归及精确提交CI待核实。无部署、无新模型调用、不改原语义判定。

| 范围 | 实际结果 | 证据 |
| --- | --- | --- |
| 后端首轮 | 47 passed / 1既有依赖警告 | [backend-first.log](backend-first.log) |
| 后端最终边界 | 49 passed / 1既有依赖警告 | [backend-final.log](backend-final.log) |
| 冻结知识指纹适配器 | 1 passed，14 deselected；schema1保持兼容 | [package-final.log](package-final.log) |
| 首轮真实浏览器 | 24项功能及服务重启通过 | [browser-first.log](browser-first.log) |
| 后续界面复测 | 24项功能通过；重启登录5秒ReadTimeout，整轮失败 | [browser-final.log](browser-final.log) |
| 同输入重启复测 | 24项功能+重启通过，0页面异常，默认资产哈希不变 | [browser-release.log](browser-release.log)、[摘要](browser-summary.json)、[资产保护](protected-assets.json) |
| 历史数据对账 | 两批各40题保留39/40及37/40；比较1修复/3退化/36未变 | [history-validation.json](history-validation.json) |
| 本机私有知识观察 | 1077块/30来源；数量、索引ID/顺序、父段和子段重复检查通过；原文指纹/历史执行未知 | 同上，仅计数及摘要公开 |
| 静态检查 | Git跟踪Python及本轮新增Python lint通过；JS语法检查通过 | [tracked-lint.log](tracked-lint.log) |

浏览器功能包括实际登录/导入、101块公开知识搜索分页、哈希绑定的失败追溯、多候选和切片详情、绑定改进项、关联历史候选并保留整批3项退化、恶意元数据按文本渲染、匿名拒绝、移动端无页面横向溢出。V1/V2原测评流程同时回归。所有工程操作由代理完成，不属于独立人工验收。

## 已发现的问题及处理

1. CISP structured_v1父段内嵌在text、检索子段在child_text；资产检查按该实际模型处理。合成测试覆盖独立父块、内嵌父段及真正缺失。
2. 公开知识快照101条有71个重复正文，质量检查保留失败。每批80个历史预览上下文均出现多候选，不伪造唯一切片血缘。重复可能与辅助检索表示相关，尚未确认根因，未修改原数据。
3. 本机30来源在此次明确原文目录中没有取得文件指纹；不猜原文件版本，也不把当前处理代码指纹当历史执行证明。
4. 最终界面复测的重启登录默认5秒读取超时发生于并行全量/认证测试期间。保留失败日志，测试客户端显式设30秒读取上限，同输入再验证；没有降低密码参数、改变应用认证策略或增加自动重试。该变更是工程测试等待设置，不构成生产登录延迟承诺。

## 输入与方法

公开知识来自冻结包`evaluation/nist-public-http-question-parts-regression40-v2-20261107/chunks.json`，101块；两批历史实际问答包来自之前已冻结的公开NIST HTTP实验，不重发模型请求。新版schema2适配器仅补充经过验证的知识文件哈希；问题、答案、契约和原分不变。知识文件哈希与平台规范快照摘要分开。

复核命令（输出和数据库需新路径）：

```powershell
.venv-win/Scripts/python.exe scripts/verify_data_catalog.py --database data/evaluation/new-validation.sqlite3 --knowledge data/evaluation/data-platform-public-a.json --baseline data/evaluation/data-platform-run-a.json --candidate data/evaluation/data-platform-run-b.json --output data/evaluation/new-validation.json
.venv-win/Scripts/python.exe tests/run_evaluation_browser.py --baseline data/evaluation/data-platform-run-a.json --candidate data/evaluation/data-platform-run-b.json --knowledge data/evaluation/data-platform-public-a.json --output data/evaluation/new-browser-evidence
```

包生成步骤见[使用说明](USER_GUIDE.md)。`--local-knowledge`是可选本机采集验证，私有正文/路径只保存在被忽略的data/evaluation下，公开报告不含它们。

本机全量回归包含原工作区未提交测试，不能与干净CI的数量混称。默认`data/cisp_qa.db`只读，初始及浏览器结束后SHA256均为`3d6d78663b9378d8791f1692afcdd702c9476866d5458c3492f307766e9d5b62`；完整回归及CI在完成后追加。费用0；没有新依赖。源码字节摘要与公开输入摘要见[source-snapshot.json](source-snapshot.json)。
