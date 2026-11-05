# Historical Research System

本地史料工作流，默认由 **Codex 原生子代理 + 本地 MCP** 完成识别、独立原图审核和文章组织。本地程序负责原件留存、页面渲染、任务状态、证据校验、Markdown 入库和全文检索。史料卡生成已退出使用。

重构后的模块职责、去重边界与依赖规则见[架构说明](REFACTOR-20261105.md)，测试与20页真实材料结果见[本地验证](REFACTOR-VALIDATION-20261105.md)。

后续性能重构已将任务写入、调度、执行上下文和投影改为事务化主链，见[执行引擎、旧工作区迁移与恢复](EXECUTION-ENGINE-20261105.md)及[最终本地验收](EXECUTION-ENGINE-VALIDATION-20261105.md)。旧 JSON 工作区升级前须停机迁移；新工作区自动使用 SQLite/WAL。

## 快速开始

完整网页入口复用原项目的上传、核对、阅读、脚注、检索与导出；上传完成后自动写入同一 Codex 持久队列，无需在聊天中再次发出处理指令。准备 Docker Desktop、Python 3.11、uv、Node.js 和已登录的 Codex CLI，然后执行：

```powershell
uv sync --project services/research-platform --locked --group dev
npm --prefix services/review-workbench ci
uv run --no-sync --project services/research-platform python scripts/hrs_v2.py up --build
```

打开 http://127.0.0.1:18170 。SQL、S3 和 OpenSearch 使用根 `.env` 中的配置；完整检索沿用已安装的 BGE 嵌入/重排运行时，见[平台说明](services/research-platform/README.md)。本地 Codex 协调器通过 `codex exec` 分派原生子代理，不使用交互 TUI，也不调用旧 OCR 服务。识别固定为 GPT-6.1 Terra，独立原图审核与文章组织固定为 GPT-6.1 Sol。新任务按冻结的有限复核策略处理，复核未通过进入人工终审。

也可仅使用不依赖 SQL/S3 的本地 MCP 工作台。其安装命令与完整网页入口分开：

准备 Python 3.11、[uv](https://docs.astral.sh/uv/) 和已登录的 Codex CLI；在项目根目录执行：

```powershell
uv sync --project services/codex-pipeline --locked
codex login status
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli doctor
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli start 'D:/materials/source.pdf' --title '待处理史料'
```

依赖安装与运行分开：先执行 `uv sync`，常驻服务使用 `--no-sync python -m hrs_codex.cli`。这避免 Windows 上正在运行的控制台入口占用自身文件、后续启动又触发重装的问题；原有 `hrs-codex` 命令继续兼容。

start 返回文档 ID、工作区 ID 和本地工作台网址，后台执行器自动接续处理。也可以先打开工作台，从网页导入：

```powershell
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli workbench
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli status <document_id>
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli search '中东铁路'
```

后台执行器启动 Codex 主代理，由主代理分派原生子代理完成各阶段，使用已登录的 Codex 账号和项目固定角色模型。PDF 按物理页选择原生文字候选、混合区域识别或整页 OCR，所有路径都经过独立原图审核。统一根目录由 [.codex/historical-workspace.json](.codex/historical-workspace.json) 设置；CLI、MCP、工作台读取相同配置。显式 `--root` 或 `HRS_CODEX_ROOT` 用于单独工作区，界面展示实际工作区 ID，验收数据与正式库分开。新流程无需启动旧平台容器或加载旧 OCR 权重。

## 在 Codex 对话中使用

项目的 [.codex/config.toml](.codex/config.toml) 注册本地 historical_research stdio MCP。安装依赖后重新打开或重启项目的 Codex 会话，再通过 /mcp 查看连接；项目须按 Codex 的规则受到信任。可以直接说：

> 使用 historical-research 处理 D:/materials/source.pdf，并打开工作台查看进度。

项目 [historical-research skill](.agents/skills/historical-research/SKILL.md) 通过 `workspace_status`、`process_document`、`open_workbench` 接入统一入口。独立角色定义在 [.codex/agents](.codex/agents)。[工作流契约](services/codex-pipeline/src/hrs_codex/workflow.md) 同时供 skill、MCP 和协调器读取。

## 验收和恢复

每页保存原图与 SHA-256、OCR 初稿、审核结论、修订补丁、输入/结果哈希和子代理身份。Codex 审核记为机器裁决；人工确认记录本地使用者与原图依据，无需填写姓名和审核意见，两者均不视为 sealed gold 审核。新任务的明确、唯一定位修正先经过另一名 Sol 子代理对原图完整复核；无法确认或复核失败进入人工终审。既有任务保留冻结策略。全文通过且文章组织完成后才发布 document.md；清晰页面在待核对期间仍可读取。

独立审核未通过时，工作台的人工队列保留原图、候选和失败原因。人工确认最后一页后自动排队续跑文章组织和发布；保存待审草稿则继续阻断。技术故障记录退避重试，连续三次后，新策略允许最后一次检查点自动恢复；仍失败时等待显式恢复，不会被写成原件不清。已发布内容的复核使用新版本；新版本失败时旧发布版本仍可检索，人工决定受到保护。

工作台自动同步进度与待审数量，提交错误和临时断线保留正在编辑的内容。审核与修订验证先通过 MCP 获取带任务回执的原图、保存原图观察，再读取候选正文；观察和最终裁决绑定哈希。独立终验发现的内容错误使用显式复核版本关闭，历史发布文本会提示复核状态。

原件不清、读法不确定、修订无法唯一定位或复核失败时保持 pending。run 对未发布文档返回退出码 2；Codex 执行失败保留检查点和日志并报错。确认需要重试后运行：

```powershell
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli retry <document_id> <pending_task_id>
uv run --no-sync --project services/codex-pipeline --locked python -m hrs_codex.cli run <document_id>
```

中断中的租约过期后可重新领取，已完成阶段不会重做。state/codex/runs/ 保留协调器请求、Codex JSONL 事件与执行回执。书目身份默认为 unclaimed，文章与物理页码映射显式保存。

## 开发验证

```powershell
uv sync --project services/codex-pipeline --locked --group dev
uv run --no-sync --project services/codex-pipeline --locked python -m pytest services/codex-pipeline/tests --basetemp .cache/codex-tests
uv run --no-sync --project services/codex-pipeline --locked python -m ruff check services/codex-pipeline
```

新流程的验证不依赖旧平台容器。[模块说明](services/codex-pipeline/README.md) 给出工具和流程图；[融合验收](services/codex-pipeline/FUSION-VALIDATION.md) 记录默认工作区的真实后台处理、原图优先协议、人工交接、应用内浏览器实测和 96 项工程测试。郭村报告的“政／攻”读法分歧与[更早宁夏材料](services/codex-pipeline/ACCEPTANCE.md)的模糊字仍待人工判断；不计作内容修复成功。[统一流程验收](services/codex-pipeline/UNIFIED-VALIDATION.md)及[首次单页验证](services/codex-pipeline/VALIDATION.md) 保留历史运行与后续复评说明。

## 原有平台与历史资料

[Research Platform](services/research-platform/README.md)、[Review Workbench](services/review-workbench/README.md)、[Docling/Paddle 转换模块](services/document-extraction/README.md) 和相关 GPU 运维脚本保留。原 React UI 的上传、人工核对、阅读、脚注、表格/插图、检索、Markdown 导出、删除与勘误已接入 Codex 队列和结果。原 SQL/S3 历史数据继续可读；不会自动重跑或迁移历史任务。默认运行不依赖 Temporal 或 Qwen/Paddle/DeepSeek 识别服务；`scripts/hrs_v2.py --legacy` 才使用旧处理路径。制卡新建、重试和调度保持退出，历史卡片只读与导出继续保留。

本轮完整功能映射与20页真实史料流程验收见[融合验收记录](services/codex-pipeline/INTEGRATION-20261104.md)。流程验收授权只验证顺序与格式，不能证明内容准确率。
