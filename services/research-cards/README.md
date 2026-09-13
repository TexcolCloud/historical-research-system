> 2026-09-13：此独立后端已退役。现役实现、运行命令和内容规则见 [Research Platform V2](../research-platform/README.md)。下文仅为历史说明；旧代码和打包入口已存档，不参与部署。现有存储容器配置及业务文件保留原位，不迁移。

# 史料卡生成与任务编排

工程版本 `0.3.0`。模块从入库、检索服务的固定来源快照生成史料卡，保存阅读记录、证据、候选修订、机器核查和使用量，并提供 HTTP、Python、CLI 三个入口。

2026-09-12 用户确认取消制卡人工确认、保留机器核验：首次生成和更新的发布路径采用 `review_policy=machine_checks_auto_adopt`。阅读、卡片语义及原图检查通过后自动切换当前修订，保存机器采用记录；失败或缺证仍保留候选。已有人工保护及并发改动不被覆盖，组合后的内容仍须核验。人工编辑与后续修订接口保留。论文提示发布策略不在此次范围。

2026-09-09 按用户最新要求交付工程初版；全量史料质量验收、策略对照和 Linux 实机验收延期。当前实现、已执行检查及已知问题见[实施状态](docs/implementation-status.md)。完整设计仍见[首版实施规格](../../.scratch/research-cards/spec.md)。

当前采用 [Pro 主 agent／Flash 子 agent 与研究上下文](docs/subagents.md)：Pro 按文献委派和接收，Flash 连续完成内部阅读批次。各阶段可按需回读原文与完整产物，研究进度、笔记和模型对话可持久续接；接近输入上限时归档并重建上下文。

2026-09-11：[RAG 与制卡融合改进](../../docs/rag-card-integration-20260911.md)增加统一取证工具、明确授权的按需研究来源、完整条目回读、复用依赖和成本展示。[注释联调](../../docs/note-rag-integration-20260911.md)已验证上游结构进入 RAG 关联读取、证据包和阅读上下文；分页缺口按实际交付范围判断。工作流版本为 `cards-workflow-v1.8-notes`，证据包为 `evidence-packet-v1.1-notes`。真实模型整卡对照尚未完成，生产逐批与整卡语义复核保持；此次没有重启生产服务或发起真实模型任务。

## 已实现

- 固定主材料与补充材料，逐段读取、长文分层摘要、定向补查、证据与论证组装。
- 原图复核、逐段语义核查、整卡核查，以及最多一轮自动语义修订。生产模型仍走配置中的 DeepSeek。
- 不可变修订、候选与当前采用版本、关联条目成组采用、人工编辑与删除保护、修改预览及采用记录。
- 固定题目和卡片修订的论文提示、逐条依据绑定、卡片变化后的提示更新记录。
- 双执行单元、交互／后台调度、批任务、来源事件、初始补建、增量影响检查和可续接的有限发现。
- 持久回执与幂等提交、租约恢复、暂停／继续／取消／重试、累计预算及逐调用协议记录。
- 卡片检索、索引延迟状态、索引重建、Markdown／JSON 导出，以及 PostgreSQL 与附件的备份恢复。

## 安装与配置

需要 Python 3.11、PostgreSQL、已有入库及检索服务；卡片搜索使用独立 OpenSearch 索引。`uv.lock` 固定依赖，模块不下载 OCR 或嵌入模型。

以下命令在本模块目录执行：

```powershell
uv sync --locked
uv run research-cards --help
```

配置统一保存在项目根目录 `.env`；模板为根目录 [`.env.example`](../../.env.example)。至少填写：

```dotenv
CARDS_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@127.0.0.1:55436/historical_research_cards
DEEPSEEK_API_KEY=YOUR_KEY
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

主 agent、子 agent 和原图核查模型也在根目录 `.env` 配置：

```dotenv
CARDS_REASONING_MODEL=deepseek-v4-pro
CARDS_REASONING_EFFORT=high
CARDS_READING_MODEL=deepseek-flash
CARDS_READING_REASONING_EFFORT=low
CARDS_VISION_MODEL=deepseek-flash
CARDS_VISION_REASONING_EFFORT=low
```

修改后重启制卡 API 和 worker，新任务使用新配置；已接纳任务保留原有配置快照。共享的 `DEEPSEEK_MODEL` 不控制上述三个制卡角色。

数据库需提前建立，`db-upgrade` 仅迁移已配置数据库。已有本项目本地入库开发环境时，也可执行 `evaluation/tools/prepare_local_databases.py`，复用根目录的本地连接并建立两个固定名称的制卡数据库；该辅助脚本仅适用于 `127.0.0.1:55436`，会补写根目录中尚未配置的 `CARDS_DATABASE_URL` 和 `CARDS_TEST_DATABASE_URL`。

```powershell
uv run python evaluation/tools/prepare_local_databases.py
uv run research-cards --config config/development.toml --env-file ../../.env db-upgrade
uv run research-cards --config config/development.toml --env-file ../../.env check
```

`check` 检查数据库版本与索引状态，不发起付费模型探测。配置优先级为进程环境变量、根目录环境文件、模块 TOML、代码默认值；所有模块字段可用 `CARDS_<字段名大写>` 覆盖。相对 `state_root` 以 TOML 所在目录为基准。

## 启动

API 与工作进程分别在两个终端运行，使用同一配置：

```powershell
uv run research-cards --config config/development.toml --env-file ../../.env serve
```

```powershell
uv run research-cards --config config/development.toml --env-file ../../.env worker
```

开发配置默认地址为 `http://127.0.0.1:18140`，状态接口为 `/api/v1/status`，交互接口文档为 `/docs`。CLI 客户端使用 `--origin` 选择服务地址，不会从服务端 TOML 自动读取端口。

`config/development.toml` 显式设置 `auto_sync = false`，按需启动自动来源发现时再设为 `true`。代码默认自动同步为开启状态，因此建议始终显式指定配置文件。自动同步的范围可用 TOML 的 `sync_snapshot_ids` 限定。

工作进程平稳退出：

```powershell
uv run research-cards --config config/development.toml --env-file ../../.env worker-drain
```

该命令让当前工作进程在保存正在执行的单元后退出；任务保留原状态。需要暂停任务时，另行提交 `pause` 控制。重新运行 `worker` 可处理队列中的工作，不会自动恢复已暂停任务。

## 首次制卡

输入为上游**固定来源快照 ID**。上游需提供这份材料的研究范围、可用文字及原图；文字和图像字段的使用资格分别判断。只有文字资格时，原图核查可能返回 `not_checked`，结果会保留为候选。

保存 `generate-card.json`，替换占位 UUID：

```json
{
  "kind": "generate_card",
  "primary_snapshot_id": "00000000-0000-0000-0000-000000000000",
  "supplemental_snapshot_ids": [],
  "workload": "interactive",
  "regenerate": false
}
```

提交前会先保存回执；网络中断后用同一请求、同一回执重发：

```powershell
uv run research-cards --origin http://127.0.0.1:18140 tasks submit --request generate-card.json --receipt receipts/generate-card.json
uv run research-cards --origin http://127.0.0.1:18140 tasks get TASK_ID
uv run research-cards --origin http://127.0.0.1:18140 tasks wait TASK_ID --timeout 30
uv run research-cards --origin http://127.0.0.1:18140 tasks result TASK_ID
uv run research-cards --origin http://127.0.0.1:18140 tasks usage TASK_ID
```

`wait` 超时只结束客户端等待。任务 `completed` 表示本次执行结束，是否采用需查看 `result_summary.adoption`、`classification`、核查与覆盖状态；候选未通过核查不等于完整制卡成功。

Python 客户端使用相同 HTTP 协议和回执：

```python
from pathlib import Path
from research_cards.client import Client
from research_cards.contracts import GenerateCard

snapshot_id = "替换为上游固定快照 UUID"
with Client("http://127.0.0.1:18140") as client:
    receipt = client.with_receipt(
        Path("receipts/generate-card.json"), "POST", "tasks",
        GenerateCard(kind="generate_card", primary_snapshot_id=snapshot_id),
    )
    print(client.wait_task(receipt["task_id"], timeout=30))
```

直接 HTTP 提交使用 `POST /api/v1/tasks`、相同 JSON，以及唯一的 `Idempotency-Key` 请求头。重发同一操作时保留该键。

## 接口与产物

当前 OpenAPI 与各类 JSON Schema 保存在 [docs/contracts](docs/contracts/manifest.json)。服务启动后 `/openapi.json` 是当前运行代码的契约；本地静态契约可重新生成：

```powershell
uv run research-cards --config config/development.toml --env-file ../../.env export-contracts --output docs/contracts
```

| 操作 | HTTP 资源（均以 `/api/v1` 开头） | CLI 入口 |
| --- | --- | --- |
| 任务、状态、控制、产物、用量 | `tasks`、`tasks/{id}/controls`、`tasks/{id}/artifacts`、`tasks/{id}/usage` | `tasks` |
| 批量提交与逐成员控制 | `batches`、`batches/{id}/members`、`batches/{id}/controls` | `batches` |
| 卡片与不可变修订 | `cards`、`card-revisions/{id}` | `cards`、`card-revisions` |
| 查询 | `cards/search` | `cards search` |
| 编辑、预览、采用及保护 | `cards/{id}/edit-document`、`revision-previews`、`revision-decisions`、`cards/{id}/protection-decisions` | `cards edit-document`、`previews`、`decisions`、`cards protect` |
| 题目与论文提示 | `topics`、`paper-notes`；生成通过 `tasks` 的 `generate_paper_note` | `topics`、`paper-notes`、`tasks submit` |
| 问题、影响及提示更新 | `issues`、`impacts`、`paper-note-updates` | 对应同名命令 |
| 固定基线与变更流 | `card-baselines`、`card-changes` | `baselines`、`changes` |
| 导出与索引重建 | `exports`、`index-rebuilds` | `exports`、`index` |

编辑使用 `edit-document` 返回的整份编辑文档提交预览，并按返回的版本及组合组作决定。旧修订和旧论文提示始终保留；保存人工决定不会把机器核查记录升级为人工逐字审定。

编辑提案可声明 `author_kind=machine` 并提供非空 `reason`，用于有原件依据的机器辅助修订，保存为 `machine_edit_candidate`；默认仍是人工编辑。两者共用来源只读验证、原图与语义核验及后续采用流程，机器修订不会自动采用，也不构成人工审定。

PostgreSQL 保存任务、修订、引用、采用及保护记录。`state_root` 保存不可变阶段产物、模型请求与响应、流事件、SDK 状态、原图及裁切、回执和导出。路径和 SHA-256 随记录保存；未知的模型用量保持未知及预算预留，不记为零。复制数据库时必须同时保留对应附件。

备份、恢复、任务控制及运行限制见[运行与恢复](docs/operations.md)。

## 代码入口

| 职责 | 文件 |
| --- | --- |
| 固定输入与制卡阶段推进 | `src/research_cards/generation.py` |
| Pro 委派与回执、Flash 子任务、上下文数据包 | `subagents.py` |
| 显式上下文、原文与产物回读、研究笔记、对话归档续接 | `research_context.py` |
| 阅读摘要、补查与模型结构 | `digests.py`、`supplement.py`、`generation_contracts.py` |
| 模型调用与调用账本 | `models.py`、`metering.py` |
| 原图复核、提示词 | `vision.py`、`prompts.py` |
| 修订、人工保护、论文提示 | `revisions.py`、`adoption.py`、`paper.py` |
| 来源事件与增量影响 | `source_sync.py`、`impacts.py` |
| 队列、持久化及恢复 | `queue.py`、`worker.py`、`store.py`、`recovery.py` |
| 对外入口与导出 | `api.py`、`client.py`、`cli.py`、`exports.py` |

后续可在保留阶段产物、来源锚点、模型调用账本和外部契约的基础上替换编排策略。

2026-09-11 内置浏览器全流程回归补齐了两个恢复点：研究产物读取同时支持单元记录与分组阅读目录；失败任务的显式重试在旧保留凭据已释放时，为原固定快照重新取得并检查凭据。新凭据及历史写入检查点，终止清理固定绑定当次凭据，旧清理不会释放后一次重试的凭据。来源拒绝仍会停止工作，候选、原件核查及人工采用保持独立。真实UI复现和回归记录见[全流程测试报告](../../reports/ui-e2e-20260911-iab.md)。

语义核查与综合制卡在末轮停止新增工具取证。核查依据已有完整来源与核查记录形成逐项结论，证据不足明确列为未核实，保留实质错误和逐项覆盖校验。综合输出完整CardDraft，保留修订要求、原图核查和完整机器笔记，引用仍须通过原文回读校验，候选仍须进入后续核查。旧版已耗尽取证轮次的检查点可执行一次有界收尾，不自动转为通过；其他研究任务的轮次硬限制不变。

若推理调用明确因`max_output_tokens`截断，之后的显式技术重试可将该逻辑步骤单次上限按基准最多翻倍、最高64000；独立指定`max_output`的调用不覆盖，普通自动重试不提高上限。实际上限、恢复来源调用和计量记录保留，任务总预算仍须足额预留。截断结果不作为完整候选保存。

无工具最终答案的格式修复保留完整失败答案、固定来源、原图核查和修订要求，不重放失败响应的推理内容。仅综合修复移除已被完整新候选替代的旧候选，原请求收据及修复上下文哈希保留；核查修复保留全部待核候选。此恢复不改变单次输入上限、原文引用校验或后续核查要求，实际恢复策略和来源调用另行计量留痕。

核查发现引用未知对象时，错误明确返回`findings.object_id`、错误ID和该阶段允许的对象ID；阅读核查的来源单元与整卡核查的条目对象保持独立，不放行错误归属或删除实质发现来结束重试。

## 工程检查

测试使用独立的 `historical_research_cards_test` 数据库，每个测试创建独立 schema；索引测试使用临时专属索引。配置根目录 `CARDS_TEST_DATABASE_URL` 后：

```powershell
uv run pytest -q --basetemp state/pytest-local
uv run ruff check src tests evaluation/tools
uv build --wheel
```

工程测试中的模型故障场景使用真实 SDK 连接本地协议夹具，不计为历史内容质量证明。真实 DeepSeek 开发运行和原件优先的 GPT 机器评阅记录保存在本地 `evaluation/development/`；不要自动重放其中已提交的校准脚本。
