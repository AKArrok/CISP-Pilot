# 第一版验收

2026-11-09。核心功能本地验收通过；扩大隔离回归与精确提交 CI 仍在执行，最终状态以 continuation.json 及后续追加为准。

| 检查 | 实际结果 | 证据 |
|---|---|---|
| 最终定向后端 | 15 passed，1 个既有弃用警告 | backend-tests-final.log |
| 最终浏览器工作流 | 15 项浏览器检查 + 1 项服务重启检查，通过，无 pageerror | browser-summary.json、browser-final.log |
| 原始记录对账 | 两批各40题，逐题原判均一致 | history-validation.json |
| 版本比较 | 修复1、退化3、未变36；另用原审计/语义记录重算一致 | history-validation.json |
| 默认业务库及资产 | 前后 SHA256 一致 | protected-assets.json |
| 源码 lint | Git 跟踪的 Python + 本轮新增文件通过 | tracked-lint.log |
| 前端语法 | node --check static/evaluation.js 及 tests/evaluation_browser.cjs 通过 | 本轮命令回执 |
| 本轮 provider 请求与费用 | 0 次、0 美元新增 | 路由和离线检查无模型调用；浏览器验证记录 |

历史开发判定：v2 AI 语义39/40且联合工程审计39/40；v3 AI语义38/40、联合37/40。第一版验证的是导入、呈现与比较的正确性，不是新一轮答案质量提升，也未晋级默认回答。

原始历史内容及桌面/移动截图只保留本机 `data/evaluation/browser-final/`；公开交付仅包含摘要与验证统计。源码快照见 source-snapshot.json；该快照是当次工作区字节哈希，Git 换行规范化可能改变字节。

失败保留：初次临时目录权限错误、Chromium spawn EPERM、演示目录防护拒绝及早期 lint 修正均见 IMPLEMENTATION.md。全仓 `ruff check .` 会扫描未跟踪冻结快照和正在生成的临时测试脚本，报40项历史/临时文件错误；未修改这些证据文件，改用 Git 跟踪源码加新增文件检查通过。远端干净 CI 仍需单独核实。

限制：获准账号共享单一实验空间；输入的公开范围、角色及审阅身份是声明；哈希绑定不证明语义真值；默认导出脱敏，不含完整证据原文；离线任务仅为固定同步完整性检查。使用及回滚见 USER_GUIDE.md。
