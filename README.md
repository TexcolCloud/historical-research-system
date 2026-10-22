# Historical Research System

本地史料工作流，默认由 **Codex 原生子代理 + 本地 MCP** 完成识别、原图审核、修订复核和文章组织。本地程序负责原件留存、页面渲染、任务状态、证据校验、Markdown 入库和全文检索。史料卡生成已退出使用。

## 快速开始

准备 Python 3.11、[uv](https://docs.astral.sh/uv/) 和已登录的 Codex CLI；在项目根目录执行：

```powershell
uv sync --project services/codex-pipeline --locked
codex login status
uv run --project services/codex-pipeline --locked hrs-codex doctor
uv run --project services/codex-pipeline --locked hrs-codex prepare 'D:/materials/source.pdf' --title '待处理史料'
```

prepare 返回 document_id。继续执行：

```powershell
uv run --project services/codex-pipeline --locked hrs-codex run <document_id>
uv run --project services/codex-pipeline --locked hrs-codex status <document_id>
uv run --project services/codex-pipeline --locked hrs-codex search '中东铁路'
```

run 启动 Codex 主代理，由主代理分派原生子代理完成各阶段。默认继承当前 Codex 的模型和提供方配置，使用当前登录；可通过 --model 显式指定模型。模型推理需要 Codex 可用连接和额度，原件与结果保存在本机。默认工作目录为 state/codex/，通过 --root 或 HRS_CODEX_ROOT 切换。新流程无需启动 Docker、下载 OCR 权重或配置 DeepSeek API。

## 在 Codex 对话中使用

项目的 [.codex/config.toml](.codex/config.toml) 注册本地 historical_research stdio MCP。安装依赖后重新打开或重启项目的 Codex 会话，再通过 /mcp 查看连接；项目须按 Codex 的规则受到信任。可以直接说：

> 使用 historical_research MCP 处理 D:/materials/source.pdf。调用 workflow_instructions，使用原生子代理分别完成 OCR、独立原图审核、必要的修订复核及文章组织；全部通过后入库，并验证检索。

独立的 OCR、审核、文章组织角色定义在 [.codex/agents](.codex/agents)。[完整工作流与结果契约](services/codex-pipeline/src/hrs_codex/workflow.md) 是 MCP workflow_instructions 的同一份内容。CLI 启动器也会显式配置同一个 MCP，避免依赖当前对话是否已重载配置。

## 验收和恢复

每页保存原图与 SHA-256、OCR 初稿、审核结论、修订补丁、输入/结果哈希和子代理身份。Codex 审核记为机器裁决；人工确认另行记录署名和依据，两者均不视为 sealed gold 审核。模型修订必须由另一个子代理再次对照原图通过后采用。全文通过且文章组织完成后才发布 document.md；清晰页面在待核对期间仍可读取。

多轮识别仍不确定时转交[本地人工审核页](services/codex-pipeline/README.md#人工审核)：运行 `uv run --project services/codex-pipeline --locked hrs-codex --root state/codex review`，打开输出网址，对照原图确认或保存待审草稿。人工裁决独立留痕；全部页面通过后再继续文章组织和发布。

原件不清、读法不确定、修订无法唯一定位或复核失败时保持 pending。run 对未发布文档返回退出码 2；Codex 执行失败保留检查点和日志并报错。确认需要重试后运行：

```powershell
uv run --project services/codex-pipeline --locked hrs-codex retry <document_id> <pending_task_id>
uv run --project services/codex-pipeline --locked hrs-codex run <document_id>
```

中断中的租约过期后可重新领取，已完成阶段不会重做。state/codex/runs/ 保留协调器请求、Codex JSONL 事件与执行回执。书目身份默认为 unclaimed，文章与物理页码映射显式保存。

## 开发验证

```powershell
uv sync --project services/codex-pipeline --locked --group dev
uv run --project services/codex-pipeline --locked python -m pytest services/codex-pipeline/tests --basetemp .cache/codex-tests
uv run --project services/codex-pipeline --locked python -m ruff check services/codex-pipeline
```

新流程的验证不依赖旧平台容器。[模块说明](services/codex-pipeline/README.md) 给出工具和文件契约；[真实史料验收](services/codex-pipeline/ACCEPTANCE.md) 记录当前结果，三组中两组全文通过，另一组因原件一字不清保持待审。[首次单页验证](services/codex-pipeline/VALIDATION.md) 保留历史工程记录及后续复评说明。

## 原有平台与历史资料

旧的 [Research Platform](services/research-platform/README.md)、[Review Workbench](services/review-workbench/README.md)、[Docling/Paddle 转换模块](services/document-extraction/README.md) 和相关 GPU 运维脚本保留作为历史数据兼容路径，不属于当前 Codex 默认处理链。旧平台中的制卡新建、重试、自动调度及前端入口已撤下，历史卡片只读/导出和数据库迁移保留。新工作区与旧 SQL/S3 库相互独立，尚未自动迁移历史数据；现有记录与存储卷不会被清理。
