# Historical Research System

面向单人使用的本地史料工作台。导入 PDF 或图片后，Codex 原生子代理完成识别、独立原图审核、修正验证和文章组织；系统保留原件与证据，提供人工终审、阅读、目录导航、检索和 Markdown 导出。

## 工程与环境

仓库只有一个 Python 工程和一个 React 前端。Python 3.11、uv、Node.js 24+、Docker Desktop Linux 引擎，以及已登录且可使用 Terra/Sol 的 Codex CLI 为运行前提。完整平台检索使用本地 BGE 权重；默认使用 CUDA，CPU 模式须显式配置。

以下命令均在仓库根目录的 PowerShell 中执行。更新依赖前正常停止所属应用进程。

```powershell
uv sync --locked --group dev --extra retrieval
npm --prefix web ci
```

Python 环境统一为根 `.venv`；API、MCP 和 BGE 子进程使用同一解释器。`retrieval` extra 安装 PyTorch/Transformers，普通无模型开发测试可不安装该 extra。

运行数据与源码分开保存。新安装默认使用 Windows 的 `%USERPROFILE%/.local/share/HistoricalResearchSystem/<项目标识>/`，避开应用包对 AppData 的重定向；Linux 使用 `$XDG_DATA_HOME/HistoricalResearchSystem/<项目标识>/`（未设置时为 `~/.local/share`）。项目标识由项目绝对路径生成，同一项目的 API、CLI 和 MCP 共用目录，不同项目互相隔离。需要自定义位置时，可选设置 `HRS_DATA_ROOT`；相对路径以项目根为准，初始化不会向 `.env` 追加配置。

| 数据 | 默认位置与用途 |
| --- | --- |
| 原生任务与证据 | 数据根下 `native/`：SQLite 账本、原件、页图、审核决定和发布版本，属于持久数据 |
| 临时导入 | 数据根下 `cache/intake/`：处理成功或异常后回收；启动时定向清理超过 24 小时且未被占用的导入临时目录 |
| 运行记录与备份 | 数据根下 `diagnostics/`、`retrieval/`、`backups/`，与源码分开管理 |
| 平台业务对象 | S3：上传内容、发布页图、Markdown 和文章对象；PostgreSQL 保存引用及阅读投影 |
| 检索模型 | 既有 `models/document-retrieval/` 或显式模型目录，只需固定版本的 BGE-M3 和 BGE reranker |

默认 S3 是本机 Docker 中的 SeaweedFS，实际字节保存在 Docker 数据卷。独立 MCP 仍能在没有 SQL/S3 服务时处理资料，因此原生工作区是必要的本地持久存储；S3 接入不等于可以直接删除它。阅读使用有界内存缓存，导出流式读取 S3，不再积累整书下载副本。

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

`status` 同时显示配置和在线工作区、实际端口及是否匹配；`doctor` 检查依赖、登录和模型制品。`models check` 校验配置、分词器、完整权重与已有校验清单，配置文件存在不代表模型可用。健康检查不代替真实模型、检索和整书验收。

## 处理流程

1. 网页上传 PDF，自动进入持久队列，无需聊天再次触发；图片可通过 CLI/MCP 导入。
2. 逐页选择原生文字候选、混合识别或整页 OCR。原生提取不代表内容已通过。
3. Terra 识别；另一 Sol 代理先读取原图、冻结观察，再与候选对照。
4. 清楚且唯一定位的错误或读法冲突至多进行一次独立整页验证。原件不清、无法定位或验证失败交人工终审。
5. 用户逐项保留或修改后提交本页，无需审核人、审核意见。保存草稿不算通过，人工决定保留来源和版本。
6. 全部页面通过、文章组织与目录解析任务完成后，校验证据并发布 Markdown，再建立阅读投影和检索索引。
7. 目录与检索共用正文定位；目录关联争议可保留待办。脚注归属、双向跳转、表格和插图原件贯穿阅读与导出。

新文档使用 GPT-6.1 Terra 识别，GPT-6.1 Sol 审核、验证和组织。旧原生任务的冻结策略、人工草稿及决定保持；用户主动暂停的任务不会自动恢复。后续勘误保留旧发布版本，新修订通过前继续使用旧资料。

人工核对共用有序保存队列，旧自动保存和迟到的读取响应不能覆盖新判断。中间疑点保存后进入下一处，最后一处进入本页提交区；全部判断保存后仍需明确提交本页。版本冲突会重新读取当前状态并保留本地输入，已提交页退出编辑。

目录关联综合标题版式、日期、正文开头和有证据的分段页码映射；人工校准只影响候选推荐，不替其他条目作人工决定。目录页可“更新推荐”，利用 PDF 书签和已有 BGE 模型补充候选，再由独立 Sol 原图核验残余歧义。偏移或语义分数本身不授予关联。界面展示前三个候选及依据，支持分组查看和预取；`Alt+Enter` 确认已加载的当前候选，`Alt+←/→` 切换候选，输入框内不触发快捷确认。

跨页目录和标题保留各物理页的文字片段、位置及证据哈希，全文标题可直接检索和跳转。目录完整性按页检查，遗漏页会独立显示，不以“已有条目全部关联”代替全目录验收。阅读定位使用独立的轻量投影；仅修改目录时保留章节正文引用，并增量更新检索导航信息。

## 独立 MCP 与命令行

独立 MCP 使用同一 Python 包，不要求 SQL 服务、Docker 或 BGE 已运行。项目配置位于 [.codex/config.toml](.codex/config.toml)，对话入口见 [historical-research Skill](.agents/skills/historical-research/SKILL.md)。MCP 保存和校验证据，模型处理由原生子代理完成。

```powershell
uv run --no-sync --locked python -m hrs mcp
uv run --no-sync --locked python -m hrs start 'C:/materials/source.pdf'
uv run --no-sync --locked python -m hrs workbench --port 0
```

正常情况下 MCP 由 Codex 启动，无需另开一份。将示例文件替换为真实路径。`start` 返回文档 ID、工作区 ID 与本机网址；独立界面网址可能含访问令牌，不要公开。同一工作区的平台在线时直接打开 React 工作台；已有 MCP 文档通过幂等附接进入同一书籍队列，不重复识别。平台离线时使用不依赖 SQL 的独立核对界面，也支持疑点定位和目录终审。

API 与独立 MCP 共用工作区选择：显式 `--root`（或直接提供的 API 设置）> 进程 `PLATFORM_CODEX_ROOT` > 进程 `HRS_CODEX_ROOT` > 根 `.env` 的 `PLATFORM_CODEX_ROOT` > 根 `.env` 的 `HRS_CODEX_ROOT` > 共享 JSON 中的非空 `root`。默认 JSON 的 `root: null` 表示自动选择：`state/codex` 已有账本、旧文档或工作区身份文件时沿用原路径，否则使用数据根下的 `native/`。需要本机自定义工作区时，可在被忽略的根 `.env` 中可选设置 `HRS_CODEX_ROOT`；dotenv 相对路径以项目根为准，CLI `--root` 和进程 `HRS_CODEX_ROOT` 的相对路径保持以当前目录为准。操作前核对 `workspace_id`，不要因连接到了另一个工作区而重复导入。升级不会自动移动、删除已有工作区或修改证据路径。

```powershell
uv run --no-sync --locked python -m hrs document-status 'DOCUMENT_ID'
uv run --no-sync --locked python -m hrs resume 'DOCUMENT_ID'
```

`resume` 从现有检查点继续；`run` 会执行真实模型，不是只读诊断。旧 JSON 工作区在停机备份后使用 `migrate-ledger` 校验迁移。详细阶段契约由 MCP 的 `workflow_instructions` 返回，发行资源位于 [workflow.md](src/hrs/codex/workflow.md)。

历史版本曾丢失页码或目录分类时，先备份并确认工作区，用 `python -m hrs repair-structure DOCUMENT_ID` 预览恢复计划；加入 `--apply` 才应用并请求正常目录接续。停止所属执行器并等待租约结束后，可再加 `--replan-navigation` 将旧的未完成大目录任务转换为小批补解析，旧输入和记录保留。该维护操作从保留的原图审核证据恢复结构，保存逐页审计，不修改正文、旧审批或人工目录决定；有活跃执行租约时拒绝应用。

## 代码职责

| 位置 | 职责 |
| --- | --- |
| [src/hrs/config.py](src/hrs/config.py) | 配置、默认值及项目/模型/前端路径 |
| [src/hrs/cli.py](src/hrs/cli.py)、[app.py](src/hrs/app.py) | 统一入口、模块装配与进程生命周期 |
| `src/hrs/api/` | HTTP 参数、响应和公开接口 |
| `src/hrs/processing/` | SQLite 账本、任务、证据、审核与发布状态机 |
| `src/hrs/codex/` | MCP、原生协调器、执行器与独立工作台 |
| `src/hrs/library/` | 书籍、文章、脚注、目录、人工草稿、阅读与导出 |
| `src/hrs/search/` | BGE、索引、检索、重排与检索运行器 |
| `src/hrs/storage/`、`src/hrs/migrations/` | SQL/S3 接入及连续的数据库迁移历史 |
| [web](web/README.md) | React 上传、进展、核对、阅读与检索界面 |
| [deploy](deploy/README.md) | 当前部署与隔离测试配置 |
| `tests/` | 单元、集成、协议、安装包与端到端回归 |

SQLite 是任务和审核决定的唯一处理权威；PostgreSQL 保存业务资料和阅读投影；S3 保存业务对象；OpenSearch 保存可重建索引。HTTP 与 MCP 共用处理用例，不各自实现审核逻辑。

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

先核对恢复库中的阅读、导出、原件与证据，再配置新的数据库、S3 桶和 `PLATFORM_CODEX_ROOT`。OpenSearch 索引可以从发布内容重建；使用进展页恢复入库或 `reindex --run-id <已发布任务ID>`，无需重做识别。数据库迁移历史保留，代码回退不等于数据降级。

历史卡片只保留读取与导出；旧 Temporal、Docling/Paddle/Qwen 和制卡执行链已退役。尚未完成的旧执行任务须显式迁移原件与既有记录，不能静默改成机器通过。

## 发行与升级

发行部署使用源码包，包含锁文件、迁移、MCP/子代理契约和已编译网页。单独 Python wheel 适用于 Python/MCP 安装，不包含完整平台网页与部署配置。完成下方验证后生成部署包：

```powershell
npm --prefix web run build
uv run --no-sync --locked python -m hrs bundle --destination dist/historical-research-system-0.2.1.zip
```

打包依据 Git 跟踪文件和 `web/dist`，新增源码须先纳入暂存区。包内清单记录代码版本、提交、工作区是否有修改及逐文件哈希；私有环境文件、权重、原件和运行数据不会入包。正式发行应在干净提交上重新打包。将 ZIP 解压到新的空目录，保留已有工作区和卷身份，复制四项必要私有配置，执行 `uv sync --locked --extra retrieval`，准备或指定既有模型目录，再用 `init` 和 `up --existing-services` 验证。预编译网页可直接启动，无需在部署机安装 Node。

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

集成测试使用 [隔离设施](deploy/compose.test.yml) 及 [CI](.github/workflows/engineering.yml) 中的专用数据库、S3 和 OpenSearch；不能把业务数据卷接入测试。契约生成使用 `npm --prefix web run contracts`，同时提交 `web/openapi.json` 与客户端类型。真实模型和备份恢复测试显式开启，机器通过不作为人工史料准确率证明。

Git 只保留现役源码、锁文件、必要测试/契约、部署和 README，以及实际执行需要的 Skill/工作流资源。模型权重、原件、账本、运行日志、私有配置和本地归档保持忽略；功能或命令变更在同一 PR 同步对应 README。
