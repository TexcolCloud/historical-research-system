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

地址、端口、库名与数据库 URL 由代码提供默认值或在内存推导，初始化不向 `.env` 添加字段。现有显式 `PLATFORM_*`、`INGEST_*` 覆盖仍可用；主机 SQL 默认 `127.0.0.1:55436`，S3 默认 `127.0.0.1:58333`、桶 `ingestion-acceptance`，OpenSearch 默认 `127.0.0.1:19260`。部署和隔离测试见 [deploy](deploy/README.md)。

准备固定版本的 BGE-M3 与 reranker，然后启动：

```powershell
uv run --no-sync --locked python -m hrs models prepare
uv run --no-sync --locked python -m hrs init
uv run --no-sync --locked python -m hrs up --build
```

打开 [工作台](http://127.0.0.1:18170)。`init` 启动基础设施并迁移专用数据库，`up --build` 构建前端并启动 API、原生执行器及所需 BGE broker；已有前端构建时可省略 `--build`。使用已启动的自定义 SQL/S3/搜索/上传服务时，显式传 `--existing-services`。

```powershell
uv run --no-sync --locked python -m hrs status
uv run --no-sync --locked python -m hrs doctor
```

`status` 查看所属工作区和监听状态，`doctor` 检查实际依赖与登录。健康检查不代替真实模型、检索和整书验收。

## 处理流程

1. 网页上传 PDF，自动进入持久队列，无需聊天再次触发；图片可通过 CLI/MCP 导入。
2. 逐页选择原生文字候选、混合识别或整页 OCR。原生提取不代表内容已通过。
3. Terra 识别；另一 Sol 代理先读取原图、冻结观察，再与候选对照。
4. 清楚且唯一定位的错误或读法冲突至多进行一次独立整页验证。原件不清、无法定位或验证失败交人工终审。
5. 用户逐项保留或修改后提交本页，无需审核人、审核意见。保存草稿不算通过，人工决定保留来源和版本。
6. 全部页面通过、文章组织与目录解析任务完成后，校验证据并发布 Markdown，再建立阅读投影和检索索引。
7. 目录与检索共用正文定位；目录关联争议可保留待办。脚注归属、双向跳转、表格和插图原件贯穿阅读与导出。

新文档使用 GPT-6.1 Terra 识别，GPT-6.1 Sol 审核、验证和组织。旧原生任务的冻结策略、人工草稿及决定保持；用户主动暂停的任务不会自动恢复。后续勘误保留旧发布版本，新修订通过前继续使用旧资料。

## 独立 MCP 与命令行

独立 MCP 使用同一 Python 包，不要求 SQL 服务、Docker 或 BGE 已运行。项目配置位于 [.codex/config.toml](.codex/config.toml)，对话入口见 [historical-research Skill](.agents/skills/historical-research/SKILL.md)。MCP 保存和校验证据，模型处理由原生子代理完成。

```powershell
uv run --no-sync --locked python -m hrs mcp
uv run --no-sync --locked python -m hrs start 'C:/materials/source.pdf'
uv run --no-sync --locked python -m hrs workbench --port 0
```

正常情况下 MCP 由 Codex 启动，无需另开一份。将示例文件替换为真实路径。`start` 返回文档 ID、工作区 ID 与带本机访问令牌的网址；不要公开令牌。

全平台使用 `PLATFORM_CODEX_ROOT`，CLI 使用前置 `--root`，二者未设置时均遵循共享工作区配置及 `HRS_CODEX_ROOT`。操作前核对 `workspace_id`，不要因连接到了另一个工作区而重复导入。已有工作区与证据路径在代码重构时保持原位。

```powershell
uv run --no-sync --locked python -m hrs --root state/codex document-status 'DOCUMENT_ID'
uv run --no-sync --locked python -m hrs --root state/codex resume 'DOCUMENT_ID'
```

`resume` 从现有检查点继续；`run` 会执行真实模型，不是只读诊断。旧 JSON 工作区在停机备份后使用 `migrate-ledger` 校验迁移。详细阶段契约由 MCP 的 `workflow_instructions` 返回，发行资源位于 [workflow.md](src/hrs/codex/workflow.md)。

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

备份前停止上传、API及所有共享工作区的执行器，并等待退出。协调备份保存 SQL 和原生原件/队列/审核证据，仍须另行保存 S3 业务桶和私有配置：

```powershell
uv run --no-sync --locked python -m hrs stop
# 等待主机应用及所有独立工作台/执行器完全退出，再停止上传；SQL/S3 继续运行。
docker compose --env-file .env -f deploy/compose.upload.yml stop tusd
uv run --no-sync --locked python -m hrs backup | Set-Content -Encoding utf8 .cache/platform-backup-reference.json
```

该命令需要运行中的 SQL/S3 和 Docker `pg_dump`。保留生成的备份引用，并独立保存 S3 业务对象。恢复到新的数据库和不存在的新工作区：

```powershell
$backupReference = Get-Content -Raw .cache/platform-backup-reference.json
uv run --no-sync --locked python -m hrs restore --manifest-reference $backupReference --restore-suffix drill_example --restore-native-root state/codex-restore-drill
```

示例后缀和目录必须尚未使用。命令拒绝覆盖旧数据，返回新数据库和工作区映射，不自动切换配置。先在新目标核对再切换。数据库迁移历史保留，代码回退不等于数据降级。

历史卡片只保留读取与导出；旧 Temporal、Docling/Paddle/Qwen 和制卡执行链已退役。尚未完成的旧执行任务须显式迁移原件与既有记录，不能静默改成机器通过。

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
