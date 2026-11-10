# 数据中台使用与协议

## 直接体验

数据中台与测评中台共享登录和隔离平台库。在项目根目录运行：

```powershell
.venv-win/Scripts/python.exe scripts/serve_evaluation.py --workspace .evaluation-data-demo --port 9541
```

按提示设置演示密码，打开`http://127.0.0.1:9541/evaluation.html`，用`eval-reviewer`登录，进入左侧“数据中台”。启动器仅初始化/显式升级自己目录内的演示数据库；Ctrl+C停止。复用已有演示目录需要原账号密码。不会迁移默认业务库或构建默认索引。

已有平台服务先停止并备份**实际配置的隔离测评库**，然后执行：

```powershell
.venv-win/Scripts/python.exe scripts/migrate_evaluation.py --database data/evaluation/platform.sqlite3
.venv-win/Scripts/python.exe scripts/migrate_data_catalog.py --database data/evaluation/platform.sqlite3
```

上述路径仅为例子，应替换为CISP_EVAL_DB_PATH。脚本拒绝默认/配置业务SQLite库及无关表；数据中台迁移要求先有V2测评表，幂等且不回填伪造血缘。普通服务不自动迁移。回滚需停服后恢复平台库备份；不可把新增表的库直接交给旧版程序。

## 本机知识资产采集

```powershell
.venv-win/Scripts/python.exe scripts/package_data_catalog.py --chunks data/kb_chunks.json --manifest data/manifest.json --vector-metadata data/vectors.npz.meta.json --namespace cisp-local --label local-observation --output data/evaluation/local-knowledge.json
```

采集只读取显式输入，输出排他创建，不覆盖已有证据；不向量化、不调用模型、不改默认库。默认visibility为private。输出含知识正文和可能的本机路径，**只用于本机工作台，不提交到公开Git**。页面“导入知识快照”最大32 MiB；相同规范内容重复导入幂等，变化产生新快照。

有原始文件时可增加`--source-map data/evaluation/source-map.json`。映射格式为`{"准确的source名":"原始文件路径"}`，相对路径以映射文件目录为基准；只读取明确映射的文件并保存路径、SHA256，不把原始二进制塞入快照，也不提供任意文件HTTP读取。没有映射时记录切片中的唯一source_url（如有），原文指纹保持未知。文件采集前后校验摘要，读到变化会拒绝产出。

页面可搜索/分页切片，查看检索子段、内嵌父段、原始来源定位、资产指纹与处理代码现场。CISP structured_v1的text是完整父段、child_text是检索子段，不能把parent_id未独立存表误判为丢失。处理代码指纹是采集时的代码，不证明历史文件确由该版本运行产生。

质量检查分别展示：manifest切片数量、索引ID及顺序、manifest向量数、父段证据、原文指纹、子段重复和历史处理运行。失败和未知均保留，导入完成不等于资产质量全部通过。

## 用公开历史结果走通链路

```powershell
.venv-win/Scripts/python.exe scripts/package_data_catalog.py --chunks evaluation/nist-public-http-question-parts-regression40-v2-20261107/chunks.json --namespace public-nist-http --label public-nist-frozen --visibility public --output data/evaluation/public-knowledge.json
.venv-win/Scripts/python.exe scripts/package_evaluation.py --packet evaluation/nist-public-http-question-parts-regression40-v2-20261107 --run docs/engineering-delivery/nist-public-http-question-parts-regression40-v2-20261107-live --schema-version 2 --confirm-public --output data/evaluation/public-run.json
```

先在“评测批次”导入测评包，再在“数据中台”导入知识快照。这两类数据用途和协议不同。新版schema2测评适配器在核对冻结文件后增加`artifacts["knowledge:" + dataset_key]`，值为当批chunks.json的文件SHA256；知识快照namespace须与dataset_key相同（此例public-nist-http）。旧包和V1协议不重写；缺该键的历史记录不能追认为已绑定。

选择知识版本和诊断批次，点击“分析失败”：按逐次状态、指标维度/评分器版本和来源关联查看。来源关联按题次去重，但同一题次可涉及多个来源，不能把各来源或指标次数相加当成失败题数，也不能当作根因。

点击失败题次，或从测评题目详情点击“追溯知识资产 / 记录改进”：

- **哈希绑定**：原测评记录携带的知识文件哈希与所选快照一致。仍要检查具体片段是否唯一。
- **哈希不符**：所选版本与该批原记录不同，不能冒称原运行知识库。
- **历史版本未知**：原记录未保存该绑定，仅供调查候选。
- **完整文本相同**、**截断片段候选**、**ID候选**、**位置候选**分别显示。只有哈希绑定、全文一致且候选唯一时，具体切片才标注已绑定；原文处理过程仍可能未知。

未找到候选不能证明全库没有资料。原文路径/哈希只证明采集时观察到该文件，不证明历史解析一定使用它；需要真实流水线遥测才能补齐该层证据。

## 从假设到复测

在题次追溯页填写可能环节、证据、假设和复测验收条件，保存后绑定run/case/attempt、答案SHA、题目版本、rubric和知识快照。类别是调查者假设，不是自动归因。

账本支持“调查中”“已准备复测”“补充记录”和“关联已完成复测”。所有动作追加历史、按请求幂等，并用revision拒绝覆盖他人的并发记录。关联复测选另一个已导入批次和次数，不发起新模型调用。

系统复查原基线绑定，按角色、题目契约、标准判断可比性，再记录fixed/regressed/unchanged/incomparable/insufficient_evidence，并同时保留整批比较计数。单题fixed不表示没有其他退化，也不表示独立人工验收或已部署。账本不改原题、原分、答案或索引，不自动将测试答案加入知识库；用户仍须区分知识资料、开发集、回归集和独立验收集。结构校验不能判断人为把答案伪装成知识文本的行为。

## 存储、API和权限

复用CISP_EVAL_DB_PATH中的独立SQLite；新增catalog_snapshots、catalog_chunks、catalog_improvements、catalog_events及catalog_meta，不修改业务模型。namespace标识逻辑知识集合，快照ID是完整规范payload摘要；文件SHA保持原字节摘要，两者不能混用。快照和原判不可变，改进事件追加。

| 接口（前缀/api/data） | 功能 |
| --- | --- |
| GET /capabilities | 版本、零模型及能力边界 |
| POST /imports | 导入知识快照 |
| GET /snapshots、/snapshots/{id} | 版本列表、元数据与检查 |
| GET /snapshots/{id}/chunks、/chunks/{chunk_id} | 搜索分页、切片来源与父段 |
| GET /lineage?snapshot=&run=&case=&attempt= | 原记录与所选知识版本的候选关联 |
| GET /analysis/{run}?offset=&limit= | 状态/指标/来源分布与失败题次分页 |
| GET/POST /improvements | 改进分页与创建 |
| GET /improvements/{id} | 原始假设与追加历史 |
| POST /improvements/{id}/events | 追加动作和复测证据 |

沿用Cookie认证、Origin/CSRF和CISP_EVAL_USER_IDS账号白名单。当前授权账号共享一个工作区，没有多租户项目隔离。HTTP接口不执行命令、不接收本机路径供服务器读取，不自动抓取URL，不返回原始文件。知识正文只在获准工作台呈现；已有测评脱敏导出仍不包含知识正文和自由文本。

第一版上限：单快照32 MiB/20000切片、2000来源；单知识库诊断逐上下文扫描切片，复杂度约上下文数×切片数；候选单项只返回前100条并标实际总数。达到规模瓶颈后再增加文本索引与异步诊断，不把本机开发测试当生产容量证明。当前不提供知识原文上传解析、调度编排、远程仓库、在线模型执行或自动发布。
