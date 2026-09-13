# 运行与恢复

本模块初版面向本机集成。跨模块边界加固、认证部署和完整研究质量验收按项目阶段留待后续处理。

## 配置及固定执行

开发配置为 `config/development.toml`，实际密钥和数据库连接读取根目录 `.env`。配置字段定义见 `src/research_cards/settings.py`。API 和工作进程必须使用同一数据库及 `state_root`。

| 配置 | 当前默认值 |
| --- | --- |
| agent 编排 | `pro_manager`；Pro 委派与接收，Flash 执行子任务 |
| 阅读／定向补查模型 | `deepseek-flash`，reasoning effort `low` |
| 主 agent／补查决策／综合／语义核查／论文提示 | `deepseek-v4-pro`，effort `high` |
| 原图核查 | `deepseek-flash`，effort `low` |
| 双执行单元上限 | 2 |
| 每任务累计预算 | 64 次模型调用、2,000,000 输入 token、256,000 输出 token、14,400 执行秒 |
| 单次模型输入上限 | 64,000 估计 token |
| 阅读／图像输出上限 | 16,000 token |
| 推理模型输出上限 | 32,000 token |
| 阅读批次 | 最多 48 单元、12,000 Unicode 码点；输出截断可分拆 |
| 局部 agent 上下文 | 最多 8 轮、24 次上下文工具调用；每轮最多执行 4 次新的上下文工具调用 |
| HTTP／全文读取超时 | 120 秒／600 秒 |
| 模型流超时 | 1,200 秒，仍受任务剩余预算限制 |

任务保存接纳时的配置、提示版本和固定输入。改变默认值主要影响新任务；旧工作流不兼容时会显式暂停。技术重试保留已保存的产物与累计成本，一次格式修正和一次语义修订是不同记录。

生产流程直接使用 Agents SDK 的 Responses 模型接口调用 DeepSeek。GPT 仅负责开发期间的机器评阅，不替换生产复核。

`0.3.0` 按完整文献登记阅读委派，普通内部批次直接由 Flash 推进，文献完成后交回 Pro；所有调用仍共用上述任务额度。计划、补查和综合均可回读原文与完整记录，原生对话在检查点续接，并在接近输入上限时归档重建。一次子任务结果返回不等于质量核查通过。上下文、产物查询与当前执行限制见 [subagent 说明](subagents.md)。

## 任务控制

先读取 `GET /api/v1/tasks/{id}` 的 `next_actions`、`control_version`、`attempt_id`，再提交控制，避免操作过时状态。暂停示例：

```json
{"action":"pause","expected_control_version":0,"reason":"保存后暂停"}
```

```powershell
uv run research-cards --origin http://127.0.0.1:18140 tasks control TASK_ID --request pause.json --receipt receipts/pause.json
```

恢复使用 `resume`；技术重试使用 `retry`，还需提交观察到的 `expected_attempt_id`（尚未执行时也要显式为 `null`）。新操作使用新回执，网络重发同一操作使用原回执。

预算不足时可用 `extend_budget` 和正整数 `increments` 增加相应额度；不清零历史调用。`resume_after` 仅适用于预算扩展。继续等待不会撤销服务端任务，CLI 等待超时退出码为 2。

`worker-drain` 通过本模块数据库通知现有工作进程，在当前单元保存后退出。该命令不会停止 API、Docker 或其他模块。未完成的模型流会等待当前调用完成或超时，再保存其结果或不确定用量。

## 备份恢复

备份在独立目录写入数据库逻辑快照、阶段附件及哈希清单，暂停派发直至正在执行的单元保存。等待超时会返回 `waiting_for_saved_units`；重复同一条备份命令继续。

```powershell
uv run research-cards --config config/development.toml --env-file ../../.env recovery backup --bundle state/backups/initial
uv run research-cards --config config/development.toml --env-file ../../.env recovery verify --bundle state/backups/initial
```

进行环境交接时给 `backup` 增加 `--keep-paused`，保留原环境派发暂停；需要继续原环境时显式运行：

```powershell
uv run research-cards --config config/development.toml --env-file ../../.env recovery release --bundle state/backups/initial
```

恢复创建新的独立数据库，并要求目标附件目录为空：

```powershell
uv run research-cards --config config/development.toml --env-file ../../.env recovery restore --bundle state/backups/initial --destination state/restored-initial --target-database historical_research_cards_restored
```

恢复后使用输出目录中的 `restored.toml` 启动。恢复保留旧任务、固定引用、停止状态与用量，建立新的本模块索引重建工作；不会把旧上游保留句柄当成新环境的当前授权。恢复账户需要创建目标数据库的权限。

## 本次开发环境留存

2026-09-09 收尾时保留如下本地环境，均沿用本模块独立数据库。正式启动前查看当前端口占用及配置，避免把旧进程误当成新代码。

| 环境 | API | 状态配置与用途 |
| --- | --- | --- |
| 早期工程开发 | `http://127.0.0.1:18140` | `config/development.toml`；较早启动的进程，保存早期工程试跑 |
| 初次校准 | `http://127.0.0.1:18141` | 已备份；原数据库维持派发暂停，保留交接前数据 |
| 恢复后的校准 | `http://127.0.0.1:18142` | `state/calibration-v1-restored/restored.toml`；API 可查，工作进程已平稳退出，C01 已暂停，其余失败或已结束 |

用户已将交付范围缩为工程初版，因此本次收尾不继续校准，也不启动正式样本、策略对照或 Linux 质量验收。所有既有输入、输出、原件评阅及消耗记录保留在 `evaluation/development/` 和相应 `state_root`。
