> 2026-09-13：此独立后端已退役。现役实现、运行命令和内容规则见 [Research Platform V2](../research-platform/README.md)。下文仅为历史说明；旧代码和打包入口已存档，不参与部署。现有存储容器配置及业务文件保留原位，不迁移。

# 史料入库与文档组织

2026-09-12 新增只读整书阅读投影：`/snapshots/{snapshot_id}/reader` 提供轻量页序和目录，`/snapshots/{snapshot_id}/reading-content` 分批提供正文与局部待核摘要。保留原始范围、哈希与用途限制，不写入审核或研究许可。见 [整书阅读器说明](../../docs/ebook-reader-20260912.md)。

2026-09-12 起，文章结构不再要求人工审核：入库 worker 自动读取已完成的篇目生成任务，通过既有预览校验后提交采用。既有尚未采用、未被人工修订的机器/规则草案也会恢复处理。重复运行复用同一采用任务，不重复建立篇目；冲突或无效的预览保留问题，不强行应用。

结构通过程序校验时记录 `program_validated`，不伪造人工确认，也不表示史料事实已获确认。原文识别、表格及注释的局部用途限制仍然保留。片段分类、注释关联和按需定向补核见 [实现与验证说明](../../docs/article-structure-review-20260911.md)（其中强制人工结构确认流程已由本条替代）。复用原草案/阅读组合/预览采用，数据库基线仍为 `0016_article_structure`。

研究片段枚举复用本次已读取的固定来源；用途检查在单个请求内复用不可变的篇目结构，避免每个片段重新解析整章。外部引用仍核对范围与哈希，各次请求仍重新检查当前限制和证据存储，不跨请求缓存放行结论。

2026-09-11 注释流程增量见 [核对、修改与质量证据](../../docs/note-workflow-20260911.md)：
在上述阅读组合中统一页注/篇末注/章末注/书末注的身份、原始符号、局部编号规则、有序来源与多引用关系。
`article_structure` 输出正文、`notes`、`note_references`、`note_candidates`、`numbering_rules` 与局部用途问题，
检索与制卡沿用同一用途检查，不另行匹配。普通未决关系随文提示，明确冲突限制相关片段及已采用依赖。
精确版面文字映射可生成片段草案；文章范围仍复用已有分件与人工组织，未新增整书自动分篇器。
本增量存入现有 JSON 草案/阅读组合字段，无新增数据库迁移；合同导出已同步。

独立 Python 3.11 模块，位于 `services/document-ingestion`，只通过交付包消费
`document-extraction` 输出；不导入提取模块内部代码，也不使用其 GPU/OCR 环境。

## 当前可用范围

已实现数据库基线、本地目录/ZIP/tus 输入、**正式来源入库**和双存储读取：HTTP 接收任务，
独立 worker 核验并冻结文件，登记载体、正文与证据修订，生成可回读的固定快照。
导入与载体可筛选分页浏览；来源读取包含物理页、原始来源记录、图片收录、明确引用的表格证据及片段可用状态。
暂定文献、版本、书目、目录、阅读组合、用途限制、固定研究基准与增量变更已有可运行 HTTP 流程。

`prepared` 只表示交付文件已按声明保存并核验，不表示史料来源、内容可用状态或组织结果已通过。
准备完成后须单独提交 `/imports`；标准包必须通过来源合同，旧结果只作待补齐归档。
入库不提升原有“待补证”状态，不等于史实审定或研究用途放行。
标准导入自动产生规则分件草案；按需 DeepSeek 辅助、离线清单编辑导回、身份拆合与撤回均已实现。
版本 `0.1.0` 完整首版及 2026-09-08 集成复验已通过机器验收：本轮 90 项核心测试无失败/跳过，Linux 双后端完整链路重新验证通过。
大包传输 4 项和 DeepSeek 实调用 1 项承接 2026-09-07 的已核验记录，未冒称本轮重跑。
详见[首版验收说明](docs/first-version-acceptance.md)；机器验收不等于用户最终接受或史料人工审定。

- [实施状态与保护范围](docs/implementation-status.md)
- [当前 HTTP 接口说明](docs/api.md)
- [生成的 OpenAPI](docs/contracts/openapi.json)
- [包装清单 JSON Schema](docs/contracts/ingestion-package.schema.json)
- [可编辑草案 JSON Schema](docs/contracts/draft-manifest.schema.json)
- [完整调用示例与恢复](docs/usage.md)

## 本地运行

以下命令均在本模块目录执行；配置中的相对路径也以该工作目录为基准。

```powershell
uv sync --frozen --python 3.11 --cache-dir .cache/uv
```

环境统一使用[项目根目录模板](../../.env.example)和根目录 `.env`；本模块不再维护独立 dotenv 文件。
首次准备环境且根目录尚无 `.env` 时才复制模板，在其中设置 `INGEST_*` 专用密码和连接 URL。
**不要覆盖已有根目录 `.env`**；本机配置已合并，原密码、模型配置、数据库及 S3 参数均保留。
密码中的特殊字符须在连接 URL 中编码；修改 `POSTGRES_PASSWORD` 环境变量不会修改已有数据库角色密码。

新环境可用以下命令创建专用 PostgreSQL 容器；它不是 Docker Desktop 重启命令：

```powershell
docker compose --env-file ../../.env -f config/compose.acceptance.yml up -d --wait --wait-timeout 60 postgres
```

当前固定 PostgreSQL 18.6 镜像及摘要，使用独立持久卷，端口仅映射至 `127.0.0.1:55436`。
本机已有的 Desktop 停启故障未被本模块修复，不要为了运行本模块反复停启 Desktop。

```powershell
New-Item -ItemType Directory -Force -Path .cache/assets, .cache/receiving
.venv/Scripts/history-ingest.exe --env ../../.env --config config/local.example.toml db-upgrade
```

迁移必须显式执行；HTTP 启动和就绪检查不会代为迁移或清库。当前迁移头为 `0016_article_structure`。
已有本模块开发库只需再次执行 `db-upgrade`；无需重建数据库或重启 Docker Desktop。
在两个终端分别启动 HTTP 与 worker：

```powershell
.venv/Scripts/history-ingest.exe --env ../../.env --config config/local.example.toml serve
```

```powershell
.venv/Scripts/history-ingest.exe --env ../../.env --config config/local.example.toml worker
```

HTTP 默认监听 `127.0.0.1:8766`，交互文档位于 `/docs`。
`worker --once` 处理一个任务；若该任务为标准导入，还继续处理它的自动规则子任务再退出。
任务保存在 PostgreSQL，关闭网页或 HTTP 进程不会删除任务；规则子任务失败不回滚已经成功的导入。
当前没有身份认证，不应暴露至公网或不可信网络；生产部署和跨模块边界检查尚未验收。

```powershell
.venv/Scripts/history-ingest.exe --env ../../.env --config config/local.example.toml check
```

`check` 检查真实迁移版本、本地存储目录、接收目录和近期 worker 心跳。
未就绪时输出问题 JSON、退出码 1；就绪范围仅为 `implemented_source_ingestion_runtime`，不是史料质量或用户接受证明。
环境变量覆盖显式 dotenv，dotenv 覆盖 TOML；CLI 继续显式指定根目录文件，不自动扫描其他目录寻找配置或凭据。
测试和验收脚本按项目路径读取同一根目录 `.env`；S3 测试只选取 `INGEST_S3_*` 字段，不混入提取参数或开发数据库配置。

## 输入准备与存储

将目录放在配置的 `receive_root` 下，使用 `ingestion-package.json` 明确列出文件路径、角色、SHA-256 和长度。
提交相对目录及包装清单原始字节摘要，或使用薄 CLI 的 `pack` 和 `submit` 上传 ZIP64，详见[调用示例](docs/usage.md)。

- 接收目录和包内文件只能使用规范相对路径，拒绝路径越界、链接/重解析点和跨平台重名。
- 原始包装清单与声明文件均保存；不修改原提取模块的输出格式。
- 新文件先核验输入，再复制并核验，最后完整读回核验 SHA-256 与长度，才记录已核验位置。
  因此当前新文件会完整读取输入两次、归档结果一次；大包验收计时包含这些开销。
- 相同字节可以复用已核验资产，但不据此合并文献身份。重复提交仍核验输入字节。
- 归档使用独立位置且不覆盖已有文件；后续失败不删除已写对象。已核验文件的大小或修改时间变化时停止提供，
  不将其继续当作可用。提供显式完整重核验任务、失效位置重建、未完成 multipart 原上传恢复和写入后发布失败恢复。
  重核验由调用者发起；首版不额外运行一个自动扫描所有资产的定时服务。

输入准备只完成传输级验证；标准来源验证在独立导入任务执行。
原 PDF/输入图片须显式绑定，不从上游绝对路径偷偷读取；正文保持 UTF-8、LF 和 Unicode 码点偏移。
同载体的等价结果可复用修订；变化结果先待采用，不自动替换当前快照。详见接口说明第 5–7 节。

## 检查与接口导出

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/ruff.exe check --no-cache src tests
.venv/Scripts/history-ingest.exe export-contracts --output docs/contracts
```

导出接口不需要数据库。生成文件仅包含当前已实现操作；接口更新后应重新生成。

数据库测试必须连接专用 `ingestion_test` 数据库，逐测试创建、使用并删除自己生成的临时库，
不重置开发库或既有史料库。未配置真实 PostgreSQL 时集成测试会跳过，不能将跳过报告当成验收通过。
工程输入验证协议、复杂关系和故障恢复；真实五页与九页包验证固定范围、原始状态、图片及表格引用的入库保真。
样本注册见 `evaluation/real-inputs.json`；真实 S3 测试仅允许本机回环地址，不自动上传到云端。
GPT 原件优先开发审阅不替代全文 OCR 校订、史实审定、人审或金标批准；DeepSeek 实调用测试只发送工程文字。
现役首版汇总为 `evaluation/v1-recheck-20260908/acceptance-summary.json`；
`evaluation/v1-20260907/` 保留原验收基线，其他旧报告均为生成时的阶段快照。
根目录环境统一后的定向配置验证见 [configuration-verification.json](evaluation/env-unification-20260908/configuration-verification.json)：
15 项通过，生产源码未变化；原集成验收报告保持不变，测试及验收脚本的新哈希单独记录。

显式执行核心、传输及 Linux 验收（模型实调用为单独的显式通道；大包约占用数十 GiB，可持续数十分钟）。每次使用新输出目录保留旧证据：

```powershell
$acceptanceOutput = 'evaluation/acceptance-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
.venv/Scripts/python.exe evaluation/tools/run_acceptance.py core --output $acceptanceOutput
npm.cmd ci --prefix evaluation/tools --ignore-scripts --no-audit --no-fund
.venv/Scripts/python.exe evaluation/tools/run_acceptance.py large --restart-acceptance-s3 --output $acceptanceOutput
docker build -t historical-document-ingestion:acceptance-v1 -f config/Dockerfile ../..
.venv/Scripts/python.exe evaluation/tools/run_linux.py --output $acceptanceOutput
```

大包命令最后重启**专用验收 S3 容器**以检查持久卷，运行时不要并行其他依赖该容器的操作。
Linux 验收包含容器内 CLI/中文路径、迁移及重复升级、两后端完整链路和 API/worker 重启回读。
测试清理自己新建的隔离数据库；复核报告、工作文件及持久卷保留，不自动清场。

本轮复验给 `run_acceptance.py core` 和 `run_linux.py` 传入
`--output evaluation/v1-recheck-20260908`，不覆盖旧基线。
核心报告附带运行前后源码哈希；汇总还校验当前源码、Linux 镜像、原件及报告是否对应。
承接旧通道时使用 `summarize_v1.py` 的 `--baseline` 和 `--baseline-source`，
必须具备与原汇总哈希一致的旧源码快照；只允许本次明确的分篇规则差异，不能把任意新代码配上旧测试记录。


## 2026-09-12 UI 修订接口

### 整书篇目组织

新入库默认使用 `partition-rules-4`。当固定来源缺少明确篇目边界、规则仍有未归属范围时，使用已配置的 DeepSeek 顺序读取整书正文，生成章级篇目草案。目录、封面等导航内容单独记账；既有篇目不重复建立。不重跑 OCR，不改写来源正文，也不记录人工确认。

第一批根据卷首与目录建立整书篇章层级，后续批次固定沿用；章内标题、注释、表格和引文不单独拆篇。计划中的篇目未找到实际开头时，不能宣告候选覆盖完成。整书结构输出使用 `reasoning.effort=none`、8000输出token；保留模型截断原因与用量，避免把推理耗尽额度误报为JSON错误。

每批校验全部输入单元以及边界原文的唯一匹配，保存来源/配置指纹、批次结果和调用用量。失败可通过任务重试复用已完成批次；来源、既有篇目或模型配置改变时须提交新任务。历史规则任务可在详情中选择“整理整书未覆盖内容”。

整书限制分别由 `INGEST_DEEPSEEK_BOOK_BATCH_CODEPOINTS`（默认12000）、`INGEST_DEEPSEEK_BOOK_MAX_CALLS`（128）、`INGEST_DEEPSEEK_BOOK_MAX_INPUT_CODEPOINTS`（2000000）控制。未配置模型时明确返回 `requires_model_configuration`，不将零候选视为整书覆盖完成。覆盖账目区分新候选、既有篇目、导航、附属、空白和待定内容；候选覆盖完成不等于已应用或人工验收。

- `GET /api/v1/snapshots/{snapshot_id}/segments` 增加可组合筛选：`physical_page`（从1起的文件物理页）、`query`（正文子串，最多500字）、`source_span_ref`（固定来源片段UUID）。仍按快照范围分页返回原文，不改写正文或锚点；无匹配返回空列表。
- `GET /api/v1/snapshots/{snapshot_id}/structure-draft` 只读恢复已执行的阅读组合，返回现有 `DraftRequest` 合同。只包含 `set_reading`，解析创建时的临时引用，保留源范围、注释和组织结构，清除人工确认；无法找到原命令时返回409，不凭当前文本猜测结构。调用方仍须创建草案、预览并选择应用组。
- 该修订入口不会把旧来源锚点自动改接新提取快照；正文修订的来源传播须单独验证。测试与当前边界见[UI改进修复记录](../../reports/ui-remediation-20260912.md)。
