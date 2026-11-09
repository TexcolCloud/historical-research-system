# Historical Research System

面向单人使用的本地史料工作台。导入 PDF 或图片后，Codex 原生子代理完成识别、独立原图审核、修正验证和文章组织；系统保留原件与证据，提供人工终审、阅读、目录导航、自动史料卡、实体事件检索和 Markdown 导出。

## 工程与环境

仓库只有一个 Python 工程和一个 React 前端。Python 3.11、uv、Node.js 24+、Docker Desktop Linux 引擎，以及已登录且可使用 Terra/Sol 的 Codex CLI 为运行前提。完整平台检索将原文按保留来源位置的片段建立 BGE-M3 向量，结合关键词召回和 BGE reranker 重排；默认使用 CUDA，CPU 模式须显式配置。

以下命令均在仓库根目录的 PowerShell 中执行。更新依赖前正常停止所属应用进程。

```powershell
uv sync --locked --group dev --extra retrieval
npm --prefix web ci
```

Python 环境统一为根 `.venv`；API、MCP 和 BGE 子进程使用同一解释器。`retrieval` extra 安装 PyTorch/Transformers，普通无模型开发测试可不安装该 extra。

运行数据与源码分开保存。新安装默认使用 Windows 的 `%USERPROFILE%/.local/share/HistoricalResearchSystem/<项目标识>/`，避开应用包对 AppData 的重定向；Linux 使用 `$XDG_DATA_HOME/HistoricalResearchSystem/<项目标识>/`（未设置时为 `~/.local/share`）。项目标识由项目绝对路径生成，同一项目的 API、CLI 和 MCP 共用目录，不同项目互相隔离。需要自定义位置时，可选设置 `HRS_DATA_ROOT`；相对路径以项目根为准，初始化不会向 `.env` 追加配置。

| 数据 | 默认位置与用途 |
| --- | --- |
| 原生任务与证据 | 数据根下 `native/`：SQLite 账本、原件、页图、审核决定、发布版本及研究来源快照/卡片修订，属于持久数据 |
| 临时导入 | 数据根下 `cache/intake/`：处理成功或异常后回收；启动时定向清理超过 24 小时且未被占用的导入临时目录 |
| 运行记录与备份 | 数据根下 `diagnostics/`、`retrieval/`、`backups/`，与源码分开管理 |
| 平台业务对象 | S3：上传内容、发布页图、Markdown、文章对象、全书目录/定位和研究证据对象；PostgreSQL 保存引用、阅读及研究检索投影 |
| 检索模型 | 既有 `models/document-retrieval/` 或显式模型目录，只需固定版本的 BGE-M3 和 BGE reranker |

默认 S3 是本机 Docker 中的 SeaweedFS，实际字节保存在 Docker 数据卷。独立 MCP 仍能在没有 SQL/S3 服务时处理资料，因此原生工作区是必要的本地持久存储；S3 接入不等于可以直接删除它。阅读使用有界内存缓存和独立的轻量定位投影，目录查询无需反复读取整书章节；导出流式读取 S3，不积累整书下载副本。正文、组织和章节集合未变时，目录更新复用章节对象与定位对象，兼容的现有索引只更新目录和词法字段，不重新计算向量；索引缺失、旧规则或章节集合变化时回退完整索引。

## 首次启动

已有 `.env` 时保留原文件。首次安装复制模板并填写三项凭据：

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

| 字段 | 填写方式 |
| --- | --- |
| `INGEST_DEV_DB_PASSWORD` | 本地 PostgreSQL 密码 |
| `INGEST_S3_ACCESS_KEY` | 专用 S3 访问密钥 |
| `INGEST_S3_SECRET_KEY` | 专用 S3 私有密钥 |
| `COMPOSE_PROJECT_NAME` | 新安装留空；已有部署保留原值，维持容器和卷身份 |

地址、端口、库名与数据库 URL 由代码提供默认值或在内存推导，初始化不向 `.env` 添加字段。现有显式 `PLATFORM_*`、`INGEST_*` 覆盖仍可用；主机 SQL 默认 `127.0.0.1:55436`，S3 默认 `127.0.0.1:18333`、桶 `ingestion-acceptance`，OpenSearch 默认 `127.0.0.1:19260`。S3 默认使用低于 Windows 动态端口范围的端口，避免原 `58333` 在系统重启后被保留。部署和隔离测试见 [deploy](deploy/README.md)。

准备固定版本的 BGE-M3 与 reranker，然后启动；模型准备仅下载实际加载的权重和分词器，不下载 ONNX 等其他推理格式：

```powershell
uv run --no-sync --locked python -m hrs models prepare
uv run --no-sync --locked python -m hrs init
uv run --no-sync --locked python -m hrs up --build
```

打开 [工作台](http://127.0.0.1:18170)。`init` 启动基础设施并迁移专用数据库，`up --build` 构建前端并启动 API、原生执行器及所需 BGE broker；已有前端构建时可省略 `--build`。使用已启动的自定义 SQL/S3/搜索/上传服务时，显式传 `--existing-services`。

工作台面向单人使用，按桌面窗口设计，打开后直接进入书籍列表。处理使用本机已登录的 Codex。

```powershell
uv run --no-sync --locked python -m hrs status
uv run --no-sync --locked python -m hrs doctor
```

`status` 同时显示配置和在线工作区、实际端口及是否匹配；`doctor` 检查依赖、登录和模型制品。`models check` 校验配置、分词器、完整权重与已有校验清单，配置文件存在不代表模型可用。托管 GPU 检索服务由活跃入口共同持有，关闭一个入口不会中断其他入口，最后一个入口退出后回收；外部服务仍由其部署方管理。就绪检查包含所需 GPU 服务的连通性与配置匹配，健康检查不代替真实模型、检索和整书验收。

## 处理流程

1. 网页选择 PDF 并点击上传；文件接收完成后自动进入持久队列，无需聊天再次触发。顶部区分待上传与正在上传；图片可通过 CLI/MCP 导入。
2. 逐页选择原生文字候选、混合识别或整页 OCR。原生提取不代表内容已通过。
3. Terra 识别；另一 Sol 代理先读取原图、冻结观察，再与候选对照，并记录页面类别、印刷页码和标题。
4. 清楚且唯一定位的错误或读法冲突至多进行一次独立整页验证。验证保留有证据的页面结构，避免修正文稿通过后丢失目录分类或页码；原件不清、无法定位或验证失败交人工终审。
5. 用户逐项保留或修改后提交本页，无需审核人、审核意见。保存草稿不算通过，人工决定保留来源和版本。
6. 全部页面通过、所需目录页逐页解析覆盖完成后，进行文章组织；必需任务通过后校验证据并发布 Markdown，再建立阅读投影和检索索引。
7. 目录与检索共用正文定位；目录关联争议可保留待办。归属明确的脚注支持双向跳转，整条脚注引文可定位并持续高亮其可见正文；无法确认关联的印刷注号和注释原文继续保留，表格和插图原件贯穿阅读与导出。原文发布且 OpenSearch 原文索引版本一致后，具备内容认证的完整文章自动进入独立制卡队列，无需手动启动；原文可独立阅读。

新文档使用 GPT-6.1 Terra 识别，GPT-6.1 Sol 审核、验证、目录解析与关联核验、文章组织。新 v7 制卡任务由原生 Sol 子代理完整拟卡，再交独立代理核验；首次内容核验未通过时最多进行一次定向修正，再由新的独立代理复核，仍未通过则交人工。核验者先完整读来源并记录事实、实体、事件及遗漏检查，再看候选。制卡有独立的进度、暂停/恢复和待人工核验状态，技术失败不会变成内容通过；人工采用、结构化修订或排除保留新旧卡片版本，不开放代理代人确认。长任务由协调器心跳续约当前有效的执行租约，中断后从检查点恢复；心跳不会复活已过期、暂停或取消的任务。旧原生任务的冻结策略、人工草稿及决定保持；用户主动暂停的任务不会自动恢复。后续勘误保留旧发布版本，新修订通过前继续使用旧资料。

人工核对共用有序保存队列，旧自动保存和迟到的读取响应不能覆盖新判断。中间疑点保存后进入下一处，最后一处进入本页提交区；全部判断保存后仍需明确提交本页。版本冲突会重新读取当前状态并保留本地输入，已提交页退出编辑。

每篇独立文章建立一张完整史料卡，主题和论证单元置于卡内。v7 卡片在书目、忠实提要、原文事实与观点、逐段覆盖、精确引文及脚注之外，提供研究范围、形成背景、文本结构、史料性质与角色、研究价值、可以支持／不能支持／需要互证的边界，以及绑定原文证据并说明适用限制的论证单元；这些说明不构成论文结论或史料可信度评级。形成时间、事件时间与出版时间分开记录；完整及简写引注标明缺项，原书印刷页只记录来源支持的页码，不以 PDF 页序推算。人物、机构、地点、文献的来源提及与稳定身份分开，事件保存行动、地点和参与者角色，区分记载已发生、计划、条件性叙述与否定。身份合并、区分、拆分和别名变更保留依据及版本，不能仅凭同名合并。

史料卡使用文献清单与单卡正文布局，清单显示稳定的界面编号、题名、作者、形成时间和核验状态；界面编号不是档案馆档号。默认进入阅读模式，八个章节可直接跳转，点击具体引文才展开该条证据与原页，关闭后回到阅读位置；核对模式集中显示待处理问题与人工操作，将已知字段和本卡编号解释为中文含义、对象名称及可点击引文，原始核验记录完整折叠保留。完整结构化修订按章节和条目展开，折叠保留输入，校验提示可直接定位到需补充的条目。个人备注置于末章并独立保存版本，模型修订不会覆盖，也不作为史料证据或内容认证；当前窗口内切换页面后可恢复未保存的备注，刷新或关闭前仍需保存。Markdown 导出保留卡片章节、引注缺项、精确引文及原注，并将核验与版本记录折叠保存。

检索候选排除空白、纯标题和有印刷页码依据的页首页码，原文字符覆盖和脚注仍完整保留；结果注明原文片段、标题目录或关联上下文中的字面匹配，语义相关结果提示结合原文判断。索引规则升级自动重建索引，不重做识别或制卡。

拟卡或定向修正可按需使用共享 OpenSearch 混合检索、实体库和关系工具寻找线索，每次任务最多执行八次成功查询；不强制查询计划或最低检索次数。代理读完冻结原文后，可批量取得精确引文的 Unicode 字符位置及有范围依据的脚注 ID，重复位置保留歧义，由代理结合上下文选择；程序定位不代替史料判读。修正可提交绑定冻结草稿的字段变更，减少重复生成；程序合成完整候选后仍执行全部校验，并交独立代理核验全卡。跨卡关系区分回函、转载、同源、支持、反证及限制，冻结双方卡片版本、完整来源和引文。独立核验在审读来源后读取冻结检索回执，查询次数等运行记录单独保存。未知书目信息与原文没有记载的类别可以留空，真实遗漏、归属或读法争议仍需处理；已有证据支持的别名无需再重复建立同义关系边。

工作台聚焦自动制卡、核验、实体库和有证据的事件关系，不再提供新建研究问题、自动论文论证或自动研究补链。旧研究问题、选材和结论保留只读与导出，旧 v1–v6 任务继续遵守各自冻结策略；旧卡缺少的新章节如实显示未提供，重建索引或阅读投影不会升级或重复制卡，明确修订才生成替代版本。卡片和实体使用独立的 OpenSearch 派生索引，复用 BGE 分块嵌入、关键词召回与重排，不将模型提要当作原文证据。来源、身份或卡片变更通过持久队列更新投影和索引，原文与制卡任务互不阻塞。

制卡任务按篇章隔离故障：子代理退出却未提交结果，或单个执行超时，仅退还对应任务重试，其他有效任务继续处理；重试有次数和绝对时长上限。限流后先通过单个工作探针恢复，再按账号与模型预算逐步增加并发；预算足够时第二本书的调度会话正常计数，并为实际制卡留出名额。新的容量压力会让相关执行从检查点让位。覆盖及引文校验指出具体对象、字符位置或脚注归属，不自动改写内容或授予通过。阅读与导出还支持正文明确 Markdown 注号与唯一“注释”节中同号印刷定义的跨页关联；普通括号编号、重复编号等未确认关系继续保留原文。

目录关联综合标题版式、日期、正文开头和有证据的分段页码映射，支持“标题……作者（页码）”等目录格式；与已核验印刷页码一致的页边独占页码才作为页脚排除，可见目标页码逐项绑定，单个条目的源片段不能吞并多个目录目标。人工校准只影响候选推荐，不替其他条目作人工决定。目录页可“更新推荐”，利用 PDF 书签和已有 BGE 模型补充候选，再由独立 Sol 原图核验残余歧义。偏移或语义分数本身不授予关联。界面展示前三个候选及依据，支持分组查看和预取；`Alt+Enter` 确认已加载的当前候选，`Alt+←/→` 切换候选，输入框内不触发快捷确认。

目录解析每批最多处理 3 个核心物理页，连同相邻上下文最多 5 页；上下文用于拼接跨页片段，覆盖结果只声明核心页，逐批保存后继续剩余页面。跨页目录和标题保留各物理页的文字片段、位置及证据哈希，完整标题可直接查询和跳转。目录完整性按页检查，已知目录之间尚未分类的页面也进入检查；遗漏或未决覆盖独立显示，完成前不进入文章组织和首次发布，不以“已有条目全部关联”代替全目录验收。已发布正文在目录补全期间仍可阅读，无法确定的关联保留目录待办。

## 独立 MCP 与命令行

独立 MCP 使用同一 Python 包，识别与原图核对不要求 SQL 服务、Docker 或 BGE 已运行；已入库原文的研究检索和跨书取证使用平台 SQL/S3、OpenSearch 与 BGE。项目配置位于 [.codex/config.toml](.codex/config.toml)，对话入口见 [historical-research Skill](.agents/skills/historical-research/SKILL.md)。MCP 保存和校验证据，模型处理由原生子代理完成。`search_library` 与网页共用原文混合检索；平台不可用时，无书籍限定的本地字面匹配明确返回 `available=false`、`backend=local_literal_fallback`，不能当作完整检索或“无证据”。研究任务的 `research_search` 不静默降级，跨书命中须冻结完整文章后才能引用。

```powershell
uv run --no-sync --locked python -m hrs mcp
uv run --no-sync --locked python -m hrs start 'C:/materials/source.pdf'
uv run --no-sync --locked python -m hrs workbench --port 0
```

正常情况下 MCP 由 Codex 启动，无需另开一份。将示例文件替换为真实路径。升级代码或修改工作区配置后，在 Codex 的 MCP 设置中重启已有连接，再核对其 `workspace_id` 与平台、CLI 一致；已启动的长连接不会自动切换工作区。`start` 返回文档 ID、工作区 ID 与本机网址；独立界面网址可能含访问令牌，不要公开。同一工作区的平台在线时直接打开 React 工作台；已有 MCP 文档通过幂等附接进入同一书籍队列，不重复识别。平台离线时使用不依赖 SQL 的独立核对界面，也支持疑点定位和目录终审。

API 与独立 MCP 共用工作区选择：显式 `--root`（或直接提供的 API 设置）> 进程 `PLATFORM_CODEX_ROOT` > 进程 `HRS_CODEX_ROOT` > 根 `.env` 的 `PLATFORM_CODEX_ROOT` > 根 `.env` 的 `HRS_CODEX_ROOT` > 共享 JSON 中的非空 `root`。默认 JSON 的 `root: null` 表示自动选择：`state/codex` 已有账本、旧文档或工作区身份文件时沿用原路径，否则使用数据根下的 `native/`。需要本机自定义工作区时，可在被忽略的根 `.env` 中可选设置 `HRS_CODEX_ROOT`；dotenv 相对路径以项目根为准，CLI `--root` 和进程 `HRS_CODEX_ROOT` 的相对路径保持以当前目录为准。操作前核对 `workspace_id`，不要因连接到了另一个工作区而重复导入。升级不会自动移动、删除已有工作区或修改证据路径。

```powershell
uv run --no-sync --locked python -m hrs document-status 'DOCUMENT_ID'
uv run --no-sync --locked python -m hrs resume 'DOCUMENT_ID'
```

`resume` 显式解除暂停、重新给予技术重试预算并从现有检查点继续，保留正文、人工草稿和审批；有效协调租约仍在运行时不重复启动。`run` 会同步尝试执行一次真实模型协调请求，不是只读诊断，也不解除暂停或清除限流、额度阻塞。执行后正文尚未发布、仍有排队或运行中的目录任务，或目录覆盖明确不完整时，`run` 返回退出码 2；命令返回不能代替完成状态核对。旧 JSON 工作区在停机备份后使用 `migrate-ledger` 校验迁移。详细阶段契约由 MCP 的 `workflow_instructions` 返回，发行资源位于 [workflow.md](src/hrs/codex/workflow.md)。

历史版本曾丢失页码或目录分类时，先备份并确认工作区，用 `python -m hrs repair-structure DOCUMENT_ID` 预览恢复计划；加入 `--apply` 才应用并请求正常目录接续，仍需执行器消费任务。停止所属执行器并等待租约结束后，可加 `--replan-navigation` 预览或应用旧契约未完成目录解析任务的小批重规划，原输入和记录保留，不替代独立关联核验任务，也不解除用户暂停。该维护操作从保留的原图审核证据恢复结构，保存逐页审计，不修改正文、旧审批或人工目录决定；有活跃执行租约时拒绝应用。

## 代码职责

| 位置 | 职责 |
| --- | --- |
| [src/hrs/config.py](src/hrs/config.py) | 配置、默认值及项目/模型/前端路径 |
| [src/hrs/cli.py](src/hrs/cli.py)、[app.py](src/hrs/app.py) | 统一入口、模块装配与进程生命周期 |
| `src/hrs/api/` | HTTP 参数、响应和公开接口 |
| `src/hrs/processing/` | SQLite 账本、任务租约、证据、页面结构、目录覆盖及审核/发布状态机 |
| `src/hrs/codex/` | MCP、原生协调器、执行器与独立工作台 |
| `src/hrs/research/` | 原生 Sol 完整制卡与核验、来源冻结、实体事件身份和关系、独立个人备注、历史研究读取、HTTP 用例及 SQL/S3/检索投影 |
| `src/hrs/library/` | 书籍、文章、脚注、跨页目录、人工草稿、轻量阅读投影与导出 |
| `src/hrs/search/` | BGE、正文向量与目录增量索引、检索、重排及检索运行器 |
| `src/hrs/storage/`、`src/hrs/migrations/` | SQL/S3 接入及连续的数据库迁移历史 |
| [web](web/README.md) | React 上传、进展、核对、阅读、研究卡片及人物事件检索界面 |
| [deploy](deploy/README.md) | 当前部署与隔离测试配置 |
| `tests/` | 单元、集成、协议、安装包与端到端回归 |

SQLite 是原文与研究任务、审核决定的唯一处理权威；PostgreSQL 保存业务资料、阅读和研究投影；S3 保存业务对象；OpenSearch 保存可重建索引。HTTP 与 MCP 共用处理用例，不各自实现审核逻辑；人物同名不自动合并，有证据的身份关联保留来源与核验状态。

## 维护与恢复

```powershell
uv run --no-sync --locked python -m hrs stop
```

停止所属主机应用会保留检查点，不清空数据或停止共享存储。技术故障先修复，再从进展页或 `resume` 恢复；内容疑点交用户终审。

备份前停止上传、API、检索 broker 及所有共享工作区的执行器，并等待退出。协调备份独立保存 SQL、原生原件/队列/审核证据，以及 S3 `hrs/v2/` 下的完整对象字节；私有 `.env` 仍须单独安全保存：

```powershell
uv run --no-sync --locked python -m hrs stop
# 等待主机应用及所有独立工作台/执行器完全退出，再停止上传；SQL/S3 继续运行。
docker compose --env-file .env -f deploy/compose.upload.yml stop tusd
$backupReference = (& uv run --no-sync --locked python -m hrs backup | Out-String)
```

该命令需要运行中的 SQL/S3 和对应 PostgreSQL 容器的 `pg_dump`。自定义容器指定 `PLATFORM_POSTGRES_CONTAINER`。默认写入数据根下新的 `backups/<时间>-<随机值>/`，也可用 `backup --destination <新目录>`。只有 SQL、原生归档和对象归档均校验通过才写入完整 `manifest.json`；未完成上传或仍在线的所属服务会阻止备份。完整目录可离线保管，恢复不依赖原桶存活。

同一 SQL 库保留了其他原生工作区的任务时，用可重复的 `backup --additional-native-root <原生根目录>` 明确纳入全部工作区；缺少原生检查点、身份重复或旧关联无法唯一确认时拒绝完整备份。系统不会只备份当前工作区却把整个 SQL 标为完整可恢复。

恢复到新的数据库、空目标桶和不存在的新工作区：

```powershell
uv run --no-sync --locked python -m hrs restore --manifest-reference $backupReference --restore-suffix drill_example --restore-bucket hrs-restore-drill --restore-native-root state/codex-restore-drill
```

也可直接把 `manifest.json` 路径传给 `--manifest-reference`。示例后缀、桶和目录必须尚未使用；不存在的目标桶会创建，非空目标拒绝恢复。命令先校验全部归档，再写入新目标，不自动切换配置。中断时保留新目标并给出恢复记录，不能将部分恢复标成成功。历史 S3 备份引用仍可读取，但需要原桶保留，且不使用 `--restore-bucket`。

单工作区仍直接恢复到指定目录；多工作区会恢复到这个新父目录下各自的工作区 ID 子目录，并返回 ID 与路径映射。恢复完成前再次验证新 SQL 与所有原生工作区的对应关系。

先核对恢复库中的阅读、导出、原件与证据，再配置新的数据库、S3 桶和 `PLATFORM_CODEX_ROOT`。OpenSearch 索引可以从发布内容重建；使用进展页恢复入库或 `reindex --run-id <已发布任务ID>`，无需重做识别。单独重建当前卡片和人物派生索引时使用 `reindex --run-id <原文任务ID> --research`，不调用模型或重做 OCR。数据库迁移历史保留，代码回退不等于数据降级。

旧 `kind=cards` 历史卡片只保留读取与导出；旧 Temporal、Docling/Paddle/Qwen 和制卡执行链已退役。新研究卡片采用独立的 `research` 工作流，不恢复旧执行链。尚未完成的旧执行任务须显式迁移原件与既有记录，不能静默改成机器通过。

## 发行与升级

发行部署使用源码包，包含锁文件、迁移、MCP/子代理契约和已编译网页。单独 Python wheel 适用于 Python/MCP 安装，不包含完整平台网页与部署配置。完成下方验证后生成部署包：

```powershell
npm --prefix web run build
uv run --no-sync --locked python -m hrs bundle --destination dist/historical-research-system-0.2.1.zip
```

打包依据 Git 跟踪文件和 `web/dist`，新增源码须先纳入暂存区。包内清单记录代码版本、提交、工作区是否有修改及逐文件哈希；私有环境文件、权重、原件和运行数据不会入包。正式发行应在干净提交上重新打包。将 ZIP 解压到新的空目录，保留已有工作区和卷身份，在 `.env` 中配置三项凭据并保留已有部署的 `COMPOSE_PROJECT_NAME`，执行 `uv sync --locked --extra retrieval`，准备或指定既有模型目录，再用 `init` 和 `up --existing-services` 验证。预编译网页可直接启动，无需在部署机安装 Node。

升级先停机并完成协调备份，在新目录验证原件、脚注、目录、检索和导出，再切换启动入口。回退保留上一版代码；若迁移不向后兼容，应把升级前备份恢复到新库、新桶和新原生目录后整体切换，不对在用库执行猜测性降级。备份、模型与私有配置独立于发行包保管。

## 开发验证

执行以下集成测试前，先按 [CI](.github/workflows/engineering.yml) 设置隔离服务及全部数据库、S3、OpenSearch 连接变量；未配置隔离环境时只运行 `tests/unit`。

```powershell
uv run --no-sync --locked ruff check src tests
uv run --no-sync --locked pytest tests/unit tests/integration -q
npm --prefix web test
npm --prefix web run check-unused
npm --prefix web run build
```

集成测试使用 [隔离设施](deploy/compose.test.yml) 及 [CI](.github/workflows/engineering.yml) 中的专用数据库、S3 和 OpenSearch；不能把业务数据卷接入测试。安装包 MCP 检查需先构建 wheel，并将 `HRS_TEST_WHEEL` 指向该文件；备份恢复和搜索回归分别显式设置 `PLATFORM_TEST_BACKUP=1`、`PLATFORM_TEST_SEARCH=1`，备份测试还需指定专用 `PLATFORM_POSTGRES_CONTAINER`。契约生成使用 `npm --prefix web run contracts`，同时提交 `web/openapi.json` 与客户端类型。真实史料验收使用原生视觉子代理并检查完整证据链；节选外的目录目标保持未定位，样本未包含的脚注、表格等不计作真实内容覆盖，机器通过不作为人工史料准确率证明。

Git 只保留现役源码、锁文件、必要测试/契约、部署和 README，以及实际执行需要的 Skill/工作流资源。模型权重、原件、账本、运行日志、私有配置和本地归档保持忽略；功能或命令变更在同一 PR 同步对应 README。
