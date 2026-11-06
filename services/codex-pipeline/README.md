# Codex Pipeline

本模块管理史料原件、页面渲染、持久任务、审核证据与 Markdown 发布。OCR、独立原图审核、修正验证和文章组织由 Codex 原生子代理执行；本地 MCP 负责分派任务、校验证据和保存状态，不执行模型推理。

完整网页平台的安装和日常使用见[根 README](../../README.md)。本页介绍独立运行、MCP 接入和队列维护；阶段输入与提交规则以[工作流契约](src/hrs_codex/workflow.md)为准。

## 独立使用

需要 Python 3.11 或 3.12、uv，以及已登录且可使用项目指定模型的 Codex CLI。独立工作台不需要 Docker、SQL/S3、BGE 或本地 OCR 模型。

以下示例使用 **PowerShell**，全部在仓库根目录执行。在新的终端中安装独立环境，避免继承完整平台的环境路径：

```powershell
$env:UV_PROJECT_ENVIRONMENT = [System.IO.Path]::GetFullPath('services/codex-pipeline/.venv')
uv sync --project services/codex-pipeline --locked --group dev
codex login status
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli doctor
```

`doctor` 检查 Codex 命令、登录与本地存储，不运行模型，也不证明材料已审核。后续命令继承同一环境；更新依赖前先停止使用该环境的常驻进程。

导入已有的本地文件，将示例路径替换为真实文件：

```powershell
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli start 'C:/materials/source.pdf' --title '待处理史料'
```

`start` 留存原件、提交持久队列，并启动或复用后台工作台。返回值包括 `document_id`、`workspace_id` 和实际访问网址。后台启动使用空闲端口，网址带本机访问令牌；不要提交或公开该令牌。

也可在前台启动工作台，再从网页导入：

```powershell
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli workbench --port 8766
```

前台默认端口为 8766，`--port 0` 可分配空闲端口；在该终端按 `Ctrl+C` 停止前台实例。关闭浏览器不会停止后台处理。工作台不是操作系统开机服务，后台进程退出后需要重新启动。

已通过完整平台上传的资料，应在同一工作区继续现有文档，不能再次运行 `start` 导入。

## 工作区与 MCP 接入

工作区选择优先级：CLI `--root` → 进程环境变量 `HRS_CODEX_ROOT` → [共享配置](../../.codex/historical-workspace.json) → `state/codex`。CLI 不会自动读取根 `.env` 中的变量。`--root` 必须位于子命令之前：

```powershell
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli --root state/codex workspace
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli --root state/codex list
```

`.workspace.json` 保存持久身份。操作前对比网页、CLI 和 MCP 的 `workspace_id`，尤其是平台设置了 `PLATFORM_CODEX_ROOT` 时。仅修改平台 `.env` 不会切换已连接的聊天 MCP。

[项目 MCP 配置](../../.codex/config.toml)通过下面的 stdio 命令连接本模块：

```powershell
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli mcp
```

正常使用时由 Codex 启动该进程，无需手动常驻一份。先完成独立环境安装，再连接项目 MCP；需要指定根目录时，在连接进程设置 `HRS_CODEX_ROOT` 并重新连接。对话入口见 [historical-research Skill](../../.agents/skills/historical-research/SKILL.md)，原生角色定义见 [.codex/agents](../../.codex/agents)。

完整平台的 Runner 使用平台自己的 Python 环境启动工作进程，不依赖独立 `.venv`；桌面对话 MCP 则需要其配置所引用的独立环境。

## 处理与发布规则

1. **逐页探测**：可靠文字层生成原生候选；混合页面结合文字层与区域识别；扫描件、不可靠字符映射及隐藏 OCR 等进入整页识别。探测结果不代表内容通过。
2. **独立原图审核**：新文档使用 GPT-6.1 Terra 识别，GPT-6.1 Sol 审核。审核者先读取原图像素、冻结观察，再读取候选对照；空白候选同样需要审核。
3. **有限修正复核**：明确且唯一定位的错误可修正；明确修正或读法冲突至多进行一次独立整页验证。原件不清、无法可靠定位或验证失败进入人工终审，不循环猜测。
4. **人工终审**：用户对疑点作出保留或修改的最终决定并提交本页。保存草稿不等于通过，无需填写审核人或审核意见。
5. **组织与发布**：所有页面通过、文章组织通过且目录解析任务完成后，校验证据并发布。目录跳转关系的争议可保留人工待办，不伪装成已确认关系。

旧文档保留冻结的模型与审核策略，只有明确迁移或恢复时才按对应协议更新。机器决定、人工决定和隔离流程测试分别标记；机器通过不等于整本书经人工批准。

脚注锚点与归属、文章边界、表格及插图原件、物理页来源需要贯穿组织与 Markdown 导出。定位疑点使用正文锚点、偏移和文本哈希；不可靠的位置明确降级为区域或整页核对，不伪造字级高亮。

### 任务接口

运行时协调器使用 `dispatch_work` / `open_task_packet`；直接 MCP 客户端先读取 `workflow_instructions`。不要通过修改任务状态代替代理执行与证据提交。

| 接口组 | 常用工具与职责 |
| --- | --- |
| 工作区与文档 | `workspace_status`、`list_documents`、`document_status`、`open_workbench`：确认身份、进度和工作台 |
| 导入与控制 | `process_document`、`resume_document`、`pause_document`：导入排队、显式恢复与暂停 |
| 原生分派 | `dispatch_work`、`open_task_packet`、`wait_for_changes`、`finish_worker`：共享容量、租约、任务包和会话退出 |
| 原图证据 | `original_image`、`original_detail`、`original_details`：读取像素与局部证据哈希 |
| 审核与提交 | `freeze_observations`、`review_candidate`、`candidate_text`、`submit_packet`：先冻结原图观察，再覆盖候选并提交 |
| 人工交接与目录 | `human_review_queue`、`request_human_review`、`navigation_status`：保留疑点与冲突，人工决定在本地网页提交 |
| 组织与使用 | `approved_page_catalog`、`approved_pages_text`、`publish_document`、`search_library`：读取完整通过页、校验发布及检索 |

具体参数、兼容低层工具和提交格式由 MCP 工具 schema 及[工作流契约](src/hrs_codex/workflow.md)提供。独立 `search_library` 直接检索已发布文本；完整 React 平台另用 BGE/OpenSearch 建立检索索引。

## 调度与容量

新工作区默认使用 14 个跨书共享名额、每会话至多 8 页，并开启原图预读。未配置的旧工作区保留 7 个名额、每会话 2 页，不自动提升。启动、预热和空闲会话均占用名额；调度依据单页耗时及识别、审核积压分配容量。

显式配置同一工作区的执行策略：

```powershell
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli --root state/codex configure-execution --capacity 14 --batch-pages 8 --overlap-reading
```

容量范围为 2–64，批次范围为 1–8。配置后重启所属执行器，使协调进程采用新设置。保守配置可使用 `--capacity 7 --batch-pages 2 --no-overlap-reading`。容量上限不代表实际可达并发或吞吐保证，也不会改变内容审核门槛。

账户或模型限流触发共享降速与退避，冷却后用真实待处理任务探测，再逐档恢复。额度耗尽或登录失效需要修复后显式继续。尚待人工的页面不会阻止其他可执行页面处理。

## 状态、人工核对与恢复

查看已有文档时，将占位标识替换为返回的原生 `document_id`：

```powershell
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli --root state/codex status 'DOCUMENT_ID'
```

“已识别”“已审核”与“已通过”分别统计；审核完成但仍有疑点的页面会留在人工队列。可运行 `review` 打开同一工作台，或使用已返回的后台网址：

```powershell
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli --root state/codex review --port 0
```

核对页展示原图、疑点、正文及历史，支持保留当前识别或修改后确认。草稿与人工决定保留版本；自动重试不能覆盖人工修改。全部页面通过后，由正在运行的执行器继续组织和发布。

技术故障修复后，从界面继续，或显式恢复现有文档：

```powershell
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli --root state/codex resume 'DOCUMENT_ID'
```

恢复保留已通过页面、人工草稿和证据，只重开可恢复的未完成任务。新运行的普通技术尝试最多三次，预算耗尽后最多自动从已校验检查点回退执行一次，仍失败则停止等待人工恢复。用户主动暂停或取消的任务不会自动重启。

`run` 是同步执行入口，会请求模型处理，**不是只读诊断**；未完成发布时退出码为 2，错误为 1，中断为 130。`prepare`、`retry` 等低层维护命令不能用来绕过人工争议、证据校验或发布门槛。`run --model` 只选择协调模型，不替换冻结的识别与审核角色。

| 问题 | 处理方式 |
| --- | --- |
| CLI 与网页看不到同一资料 | 核对实际根目录和 `workspace_id`，不要重复导入 |
| 原件看不清 | 保留原图，必要时检查原始局部，再由用户终审；放大不能补出缺失像素 |
| 登录失效、额度耗尽 | 修复账户状态后显式恢复，保留当前检查点 |
| 原件、页图或文本哈希不符 | 找回对应原文件并重新校验，不能改哈希强行放行 |
| 状态显示待人工 | 完成真实内容决定；草稿不会批准内容，也不需要继续调用模型碰运气 |
| 恢复提示仍有实例或有效租约 | 等待原实例正常退出，避免同时启动第二份处理 |

## 数据与备份

工作区采用 SQLite/WAL，`ledger.sqlite3` 是现役任务、页面、会话与决定的唯一写入账本。文件示意：

```text
state/codex/
  .workspace.json                   持久工作区身份
  ledger.sqlite3                    现役状态与事件
  .workbench.json                   本机进程与访问令牌，不共享
  documents/<source-hash-prefix>/
    source.*                        留存原件
    pages/*.png                     渲染页与审核工作副本
    render.json                     渲染清单与原件、页图哈希
    evidence/details/               原件局部与证据元数据
    document.md                     全文通过后的发布文本
    publication.json                发布回执与哈希
    revisions/                      不可覆盖的旧发布快照
  runs/<document-id>/<timestamp>/
    request.json
    events.jsonl
    stderr.log
    summary.txt
    receipt.json
```

原稿、候选、通过正文及历史裁决在账本中保留。`document.json` 只属于旧格式或兼容快照，不是当前可写状态入口。不得手动编辑账本或覆盖证据文件。

完整平台使用[协调备份与恢复](../research-platform/README.md#备份与恢复)，并独立保存 S3 对象。独立模块的校验快照实现位于 [backup.py](src/hrs_codex/backup.py)，没有 CLI 备份子命令；调用前必须停止执行器写入，恢复到不存在的新目录。不能在运行时只复制主 SQLite 文件，也不能只备份数据库而遗漏原件和修订证据。

日志、原件、访问令牌与工作区不进入 Git。分享排障记录前脱敏；仓库跟踪边界见[根 README](../../README.md#仓库跟踪范围)。

## 旧工作区迁移

出现 `Legacy workspace is read-only` 时，先停止该工作区所有执行器和工作台，保存完整备份，再在同一环境执行：

```powershell
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli --root state/codex migrate-ledger
```

迁移校验原件与审核证据，将旧 JSON 导入账本并封存为 `document.legacy.json`。迁移中断时保持执行器停止，修复报错后重跑同一命令；不要删除迁移标记或重写哈希。完成后用 `workspace`、`list` 和 `status` 核对身份及文档，再启动工作台。迁移不等于内容重新批准。

## 开发验证

使用本页独立环境，在仓库根目录执行：

```powershell
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli --help
uv run --no-sync --project services/codex-pipeline --locked python -m pytest services/codex-pipeline/tests --basetemp .cache/readme-codex-tests -q
uv run --no-sync --project services/codex-pipeline --locked ruff check services/codex-pipeline
```

单元测试不调用真实模型。真实史料验收需使用独立工作区，实际执行原图审核并保留证据，未解决内容交用户处理。涉及完整平台时，还需验证上传、投影、阅读、检索及导出，见[平台说明](../research-platform/README.md)。
