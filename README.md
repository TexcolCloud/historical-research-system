# Historical Research System

本地史料工作流，默认由 **Codex 原生子代理 + 本地 MCP** 完成识别、原图审核、修订复核和文章组织。本地程序负责原件留存、页面渲染、任务状态、证据校验、Markdown 入库和全文检索。史料卡生成已退出使用。

## 快速开始

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

后台执行器启动 Codex 主代理，由主代理分派原生子代理完成各阶段，继承已配置的模型和登录。PDF 按物理页选择原生文字候选、混合区域识别或整页 OCR，所有路径都经过独立原图审核。统一根目录由 [.codex/historical-workspace.json](.codex/historical-workspace.json) 设置；CLI、MCP、工作台读取相同配置。显式 `--root` 或 `HRS_CODEX_ROOT` 用于单独工作区，界面展示实际工作区 ID，验收数据与正式库分开。新流程无需启动旧平台容器或加载旧 OCR 权重。

## 在 Codex 对话中使用

项目的 [.codex/config.toml](.codex/config.toml) 注册本地 historical_research stdio MCP。安装依赖后重新打开或重启项目的 Codex 会话，再通过 /mcp 查看连接；项目须按 Codex 的规则受到信任。可以直接说：

> 使用 historical-research 处理 D:/materials/source.pdf，并打开工作台查看进度。

项目 [historical-research skill](.agents/skills/historical-research/SKILL.md) 通过 `workspace_status`、`process_document`、`open_workbench` 接入统一入口。独立角色定义在 [.codex/agents](.codex/agents)。[工作流契约](services/codex-pipeline/src/hrs_codex/workflow.md) 同时供 skill、MCP 和协调器读取。

## 验收和恢复

每页保存原图与 SHA-256、OCR 初稿、审核结论、修订补丁、输入/结果哈希和子代理身份。Codex 审核记为机器裁决；人工确认另行记录署名和依据，两者均不视为 sealed gold 审核。模型修订必须由另一个子代理再次对照原图通过后采用。全文通过且文章组织完成后才发布 document.md；清晰页面在待核对期间仍可读取。

多轮识别仍不确定时，工作台的人工队列保留原图、候选和失败原因。人工确认最后一页后自动排队续跑文章组织和发布；保存待审草稿则继续阻断。技术故障记录退避重试，连续三次后等待显式恢复，不会被写成原件不清。已发布内容的复核使用新版本；新版本失败时旧发布版本仍可检索，人工决定受到保护。

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

旧的 [Research Platform](services/research-platform/README.md)、[Review Workbench](services/review-workbench/README.md)、[Docling/Paddle 转换模块](services/document-extraction/README.md) 和相关 GPU 运维脚本保留作为历史数据兼容路径，不属于当前 Codex 默认处理链。旧平台中的制卡新建、重试、自动调度及前端入口已撤下，历史卡片只读/导出和数据库迁移保留。新工作区与旧 SQL/S3 库相互独立，尚未自动迁移历史数据；现有记录与存储卷不会被清理。
