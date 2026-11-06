# Research Platform

本模块提供 FastAPI 业务接口，将上传、核对、阅读、检索和导出接到原生 Codex 队列。处理权威是 [Codex pipeline](../codex-pipeline/README.md)：新文档由 Terra 识别，Sol 独立对照原图审核、复核和组织文章；无法确认的原件与失败修正保留待人工处理。机器通过不等于人工审核。

首次安装与启动见 [根 README 快速开始](../../README.md#快速开始)。本页补充配置、数据职责、维护和排障；所有命令均从项目根目录执行，并继承根 README 设置的 `UV_PROJECT_ENVIRONMENT`。

## 运行方式与配置

默认启动器是 [scripts/hrs_v2.py](../../scripts/hrs_v2.py)，实际主机装配在 [hrs_codex_platform.py](../../scripts/hrs_codex_platform.py)。它使用 PostgreSQL、S3、OpenSearch 和 tusd，主机 FastAPI 同时提供 React 页面与 API，地址为 `http://127.0.0.1:18170`。应用生命周期启动桥接及原生 Runner；默认不启动 Temporal 或旧 OCR／视觉模型。

```powershell
uv run --no-sync --project services/research-platform --locked python scripts/hrs_v2.py status
uv run --no-sync --project services/research-platform --locked python -m hrs_platform.cli doctor
```

启动器的 `status` 与 `doctor` 都只输出工作区、端口和 Codex 命令存在情况。上面的平台 CLI `doctor` 才会检查 SQL 迁移、S3 bucket、OpenSearch 和 Codex 登录；这些检查不执行模型推理，也不批准内容。

[配置加载器](src/hrs_platform/core/config.py) 先读根 `.env`，再由同名进程环境变量覆盖，并在内存中派生管理、业务、测试和容器数据库连接。`init` 创建缺失数据库并应用迁移，不回写 `.env`。[模板](../../.env.platform.example) 只保留三项必填凭据和部署身份；新安装的 `COMPOSE_PROJECT_NAME` 留空，已有部署保留原名。独立 MCP 不依赖这些 SQL/S3 配置。

| 配置项 | 默认值与行为 |
| --- | --- |
| PostgreSQL | 主机 `127.0.0.1:55436`；账号 `ingestion_owner`；管理库 `postgres` |
| 业务／测试库 | `historical_research_v2`／`historical_research_v2_test` |
| 容器数据库连接 | 使用 `host.docker.internal:55436` 访问同一业务库 |
| S3 | `http://127.0.0.1:58333`；bucket 为 `ingestion-acceptance`，region 为 `us-east-1` |
| `PLATFORM_OPENSEARCH_URL` | 默认 `http://127.0.0.1:19260` |
| `PLATFORM_PROCESSING_BACKEND` | 默认 `codex`；旧路径须显式使用 `--legacy` |
| `PLATFORM_CODEX_ROOT` | 平台显式工作区；未设置时使用 `HRS_CODEX_ROOT`，再使用项目工作区配置或 `state/codex`。MCP、平台与独立工作台应指向同一根目录 |
| `PLATFORM_RETRIEVAL_DEVICE` | 默认 `cuda`；GPU 检索通过主机 broker，不静默回退 CPU |
| `PLATFORM_RETRIEVAL_ENDPOINT` | 默认 `http://127.0.0.1:18160/retrieval` |

已有显式数据库连接、S3 地址／桶及其他覆盖变量仍兼容：`PLATFORM_*` 字段优先于对应 `INGEST_*` 别名。默认部署无需将这些派生值或固定值加入 `.env`；自定义数据库账号需具备建库和迁移权限，显式 URL 中的特殊字符需按 URL 规则编码。隔离测试继续使用 [CI 配置](../../.github/workflows/engineering.yml) 的显式连接与存储参数。

GPU 检索需要根 README 指定的 `.cache/engineering-envs/research-platform` 环境和本地 BGE 权重，见 [模型说明](../../models/README.md)。CPU 检索须显式设置 `PLATFORM_RETRIEVAL_DEVICE=cpu`，并在运行 API 的 Python 环境安装 `retrieval` extra；BGE 权重和 OpenSearch 仍然需要。更改配置后重新启动相应进程。

主机模式的 API 端口固定为 `18170`；`PLATFORM_API_PORT`、`PLATFORM_WEB_PORT` 和网页端口 `18156` 属于旧应用 Compose 配置。

## 模块与数据职责

| 位置 | 职责 |
| --- | --- |
| [main.py](src/hrs_platform/main.py)、`api/` | FastAPI 装配、资源生命周期、路由、参数校验和异常响应 |
| [codex_bridge.py](src/hrs_platform/services/codex_bridge.py)、`services/native/` | 将 SQL outbox 请求交给原生队列，将页文、审核、文章与发布结果投影回业务库和对象存储 |
| `services/documents/` | 核对、草稿、勘误、文章阅读及结构投影 |
| `services/retrieval/` | 分块、索引代次、混合检索、重排和评测 |
| `services/runs/`、[storage.py](src/hrs_platform/services/storage.py) | 运行状态、检查点引用、事件与书籍对象归属 |
| `core/`、[models.py](src/hrs_platform/models.py)、`migrations/` | 配置、连接、SQL 表结构及迁移 |
| `jobs/` | 旧 Temporal 兼容工作流及 worker |

原生工作区保存原件、页图、证据哈希、持久队列、审核决定和发布检查点。SQL 保存书籍状态、业务投影、事件、outbox 与对象归属；S3 保存上传原件、阅读投影和导出等业务对象；OpenSearch 保存可重建的检索索引。`.cache/platform-compute/` 是计算缓存，不能替代业务备份。

发布要求所有物理页和文章组织任务通过。脚注锚点、归属、表格／插图原件及物理页来源映射随阅读和 Markdown 导出保留。完整阶段合同见 [workflow.md](../codex-pipeline/src/hrs_codex/workflow.md)。

## 恢复与检索维护

技术故障修复后，在网页处理进展的“断点恢复”区域继续同一运行。恢复请求先持久保存，再异步核对登录、执行器与原件证据；保留已通过内容、人工草稿和决定。主动停止的旧运行不会因升级自动恢复。恢复状态不替代内容审核。

检索只使用已发布文章。索引生成失败时保留先前有效代；需要重建时使用实际已发布运行的 UUID：

```powershell
uv run --no-sync --project services/research-platform --locked python -m hrs_platform.cli reindex --run-id '<已发布运行UUID>'
```

该命令需要 SQL、S3、OpenSearch 与配置对应的 BGE 计算环境；不会重复 OCR 或绕过审核。已发布内容的用户勘误走网页原生修订流程，保留旧发布与新修订的关系。CLI `amend-library` 是受等长、单页和原图核验限制的兼容接口，不是通用编辑入口。

## 备份与恢复

[备份实现](src/hrs_platform/services/backups.py) 在 Codex 模式保存当前业务 SQL，以及原生原件、队列、审核和发布检查点。清单与 dump 写入配置的 S3；必须另行备份 S3 bucket／卷，否则不能应对对象存储丢失。OpenSearch 可由已发布内容重建。

备份前停止上传、API、桥接和所有共享该工作区的执行器。主机 `stop` 只提交退出请求，应等待启动器终端完全退出；它不会停止 tusd 或共享基础设施：

```powershell
uv run --no-sync --project services/research-platform --locked python scripts/hrs_v2.py stop
# 等待主机启动器退出，并确认独立工作台/执行器已停止后：
docker compose --env-file .env -f deploy/platform/compose.yml stop tusd
uv run --no-sync --project services/research-platform --locked python -m hrs_platform.cli backup | Set-Content -Encoding utf8 .cache/platform-backup-reference.json
```

SQL 与 S3 服务须继续运行。备份使用 Docker 容器内的 `pg_dump`，默认容器名为 `historical-ingestion-acceptance-postgres-1`；其他部署须设置 `PLATFORM_POSTGRES_CONTAINER`。数据库账号须具备备份权限。备份的端口／容器检查不能代替自定义部署的停写协调；原生快照也会拒绝仍在运行或持有有效任务租约的工作区。

生成的 JSON 是清单对象引用，不含凭据；应随独立备份保存在仓库和本机之外。恢复必须指定新的小写字母、数字或下划线后缀（最多 20 字符），以及不存在的原生目标目录：

```powershell
$backupReference = Get-Content -Raw .cache/platform-backup-reference.json
uv run --no-sync --project services/research-platform --locked python -m hrs_platform.cli restore --manifest-reference $backupReference --restore-suffix drill_example --restore-native-root state/codex-restore-drill
```

恢复创建 `hrs_restore_*` 新数据库，拒绝覆盖已有库或工作区；失败创建的新库保留检查。命令只返回新库与工作区映射，不切换运行配置。先核对原件、审核／发布检查点和 SQL/S3 引用，再在维护窗口调整 `PLATFORM_DATABASE_URL` 及工作区配置；需要时重建检索索引。旧 Temporal 三库备份清单仍可恢复，兼容清单不要求原生目标目录。

## 开发与验证

依赖与可选项见 [pyproject.toml](pyproject.toml)。测试包含旧模块导入，因此使用 `dev` 组与 `legacy` extra；测试数据库必须为 `historical_research_v2_test`。测试基础设施与环境变量以 [compose.test.yml](../../deploy/platform/compose.test.yml)、[CI 配置](../../.github/workflows/engineering.yml) 和 [测试 fixture](tests/conftest.py) 为准，SQL、S3、OpenSearch 都应与业务数据隔离。

在独立开发终端准备测试环境，避免修改正在运行的 API 环境；配置隔离测试服务后执行：

```powershell
$env:UV_PROJECT_ENVIRONMENT = [System.IO.Path]::GetFullPath('.cache/engineering-envs/research-platform-dev')
uv sync --project services/research-platform --locked --group dev --extra legacy
uv run --no-sync --project services/research-platform --locked python -m pytest services/research-platform/tests packages/runtime-support/tests -q
uv run --no-sync --project services/research-platform --locked python -m pytest scripts --collect-only -q
uv run --no-sync --project services/research-platform --locked python -m ruff check services/research-platform/src scripts/hrs_v2.py
npm --prefix services/review-workbench run contracts
```

最后一条命令生成 [OpenAPI](openapi.json) 及前端类型；需要前端与契约工具依赖，不连接业务库。常规测试使用模拟模型；`PLATFORM_TEST_TEMPORAL`、`PLATFORM_TEST_BACKUP`、`PLATFORM_TEST_MODELS`、`PLATFORM_TEST_SEARCH`、`PLATFORM_TEST_VISION` 等真实集成测试须显式开启，相关测试会访问服务、创建恢复库、占用 GPU 或调用付费模型。测试通过不代表史料内容经人工验收。

## 故障处理

| 现象 | 检查与处理 |
| --- | --- |
| 网页打不开 | 确认主机启动器仍在运行、`18170` 无其他占用、前端已构建；查看启动终端 |
| 配置／初始化失败 | 核对 `.env` 中占位符、数据库建库权限、S3 bucket 和 Docker Desktop；查看 `.cache/platform-diagnostics/bootstrap.log` |
| 执行器没有进展 | 查看具体运行的停止状态、租约和恢复原因；检查 Codex 登录及工作区一致性，再从断点恢复 |
| 有底稿却未发布 | 检查未解决页、失败复核或文章组织任务；保存草稿不等于通过 |
| 检索失败／无结果 | 检查发布状态、索引代次、OpenSearch、BGE 权重和 CPU/GPU 依赖；GPU broker 日志为 `.cache/platform-diagnostics/codex-retrieval.log` |
| 删除失败 | 查看清理回执并修复原因后重试；不要手工移除共享 S3 对象 |

诊断资料保留运行 ID、阶段与脱敏错误；不要提交原件、密钥、权重或本机缓存。

## 旧路径兼容

`scripts/hrs_v2.py --legacy` 显式选择旧 Temporal／Docling／PaddleOCR／Qwen 路径，部署定义在 [应用 Compose](../../deploy/platform/compose.yml)。它需要 `legacy` extra、本地旧模型与独立 GPU worker；模型准备见 [模型说明](../../models/README.md)。该 Compose 也保留默认主机路径所用的 tusd，因此不要把整个文件视为可删除的旧配置。

历史制卡记录、读取与导出保留；新建、重试和调度制卡已退出，`PLATFORM_AUTO_CARDS_ENABLED` 不能重新启用它们。旧文本模型字段仅用于兼容，不是 Codex 处理依赖。
