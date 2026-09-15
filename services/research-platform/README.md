# Research Platform

统一业务后端：上传、转换、内容核对、章节入库、混合检索和研究制卡。部署面向 Windows NVIDIA GPU 主机与 Docker Desktop；真实整书内容验收、完整灾难恢复及公网多用户边界仍需单独验证。

[首次安装](../../README.md#快速开始) · [模型准备](../../models/README.md) · [前端](../review-workbench/README.md) · [OpenAPI](openapi.json)

## 运行与配置

首次安装、模型下载和初始化统一使用根 README。本页命令均在项目根目录运行，使用已经安装的环境：

```powershell
$platformPython = '.cache/engineering-envs/research-platform/Scripts/python.exe'
& $platformPython scripts/hrs_v2.py status
& $platformPython scripts/hrs_v2.py doctor
```

[启动器](../../scripts/hrs_v2.py) 的 `init` 创建专用数据库、配置 Temporal 并应用迁移；`up --build` 构建应用并保持主机 GPU 进程受管；`stop` 等待主机进程退出后停止应用容器。共享 PostgreSQL、S3 和 OpenSearch 由独立 Compose 管理，不随应用停止。修改配置后需重新启动相应进程。

### 准备共享存储

已有满足配置的存储服务时跳过。新主机可使用仓库保留的基础设施 Compose；先填写根配置模板中的 `INGEST_DEV_DB_PASSWORD`、S3 bucket 和凭据，再执行：

```powershell
docker compose --env-file .env -f services/document-ingestion/config/compose.acceptance.yml up -d --wait
docker compose -f services/document-retrieval/config/compose.yml up -d --wait
```

前者提供 PostgreSQL 和 SeaweedFS S3，后者提供 OpenSearch。数据库用户、端口、bucket 与根配置必须一致；确认 S3 bucket 可访问后再运行平台 `init`。这些路径保留原项目名和卷归属，不代表旧业务服务仍在使用。已有部署不要随意改项目名或删除卷。

### 配置优先级

| 配置入口                                          | 当前行为                                                                                                                                                                     |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 根 `.env` 与进程环境变量                          | 主机 [Settings.load](src/hrs_platform/settings.py) 先读文件，再以同名进程变量覆盖；优先读取 `PLATFORM_*` 字段，缺失时兼容部分 `INGEST_*`、`CARDS_*` 和 `DEEPSEEK_*` 字段     |
| [应用 Compose](../../deploy/platform/compose.yml) | 显式将根配置映射到容器；例如 `CARDS_REASONING_MODEL` 映射成容器的 `PLATFORM_REASONING_MODEL`。不能假定任意主机变量都自动进入容器                                             |
| 文本模型                                          | Settings 与 Compose 未配置时均使用 `deepseek-flash`；但当前 [配置模板](../../.env.platform.example) 显式设置 `CARDS_REASONING_MODEL=deepseek-v4-pro`，复制模板后会覆盖默认值 |
| 自动制卡                                          | `PLATFORM_AUTO_CARDS_ENABLED=false` 使书籍在索引后结束；不取消已创建任务，也不禁用独立手动制卡                                                                               |
| GPU 检索                                          | `PLATFORM_RETRIEVAL_DEVICE=cuda` 为默认，经主机 broker 执行；CPU 必须显式选择 `cpu`，不会静默回退                                                                            |
| 输出与调用预算                                    | 阅读首轮 16000、推理首轮 32000、输出封顶 64000 tokens；文本与视觉调用预算默认分别为 4096，实际以配置和回执为准                                                               |

要统一使用 Flash，在根 `.env` 设置两项 `CARDS_REASONING_MODEL=deepseek-flash`、`CARDS_READING_MODEL=deepseek-flash`；同时核对是否有更高优先级的 `PLATFORM_REASONING_MODEL` 或 `PLATFORM_READING_MODEL`。此说明不代表对用户现有配置作了修改。

## 处理流程与数据职责

| 阶段     | 完成条件与恢复方式                                                                               |
| -------- | ------------------------------------------------------------------------------------------------ |
| 上传     | tusd 将文件写入 S3；平台校验 PDF、大小和摘要后幂等创建运行                                       |
| OCR      | Docling + 单路 PaddleOCR-VL；完成后先保存 S3 OCR 证据，后续恢复可复用，不因视觉失败重做 OCR      |
| 内容核对 | 本地 Qwen 全量对照原图；唯一定位的修正经独立原图复核通过后写回。不确定、不可读或未完成部分交人工 |
| 人工处理 | 保存草稿不放行；局部确认提交版本、决定与计数，响应提供下一问题；保留人工决定和编辑内容           |
| 章节入库 | 整书无未解决或未核验内容后组织章节；文章结构不增加人工批准步骤。保留全文覆盖和物理页映射         |
| 检索索引 | 从已发布章节构造检索投影；完整一代索引写入后切换生效版本，中途失败保留此前有效代                 |
| 研究制卡 | 冻结完整来源并通读、规划、综合；文本、本地原图和必要的最终来源核验通过后自动采用                 |

SQL 保存状态、来源引用、事件及 outbox；S3 保存原件、OCR、页图、章节、草稿、模型回执和导出；OpenSearch 保存可重建索引。本地仅保留权重、诊断日志和可恢复计算缓存。

原文、译者／编者修正与研究推断须区分归属。机器通过不等于用户人工审核，也不证明历史论断真实；缺失原图或不可读内容不能强制转为通过。

## 检索与来源

当前分块规则为 `structure-1400-160-v6-evidence-scopes`，见 [retrieval_chunks.py](src/hrs_platform/retrieval_chunks.py)。已发布章节是正文与字符坐标的权威来源，索引不改写它。

- 正文以 1400 字符为目标、最多 160 字符重叠；连续标题携带首段。
- 表格按完整行组处理，保留表头、合并单元格及表前范围说明；脚注保留正文归属，不猜测歧义链接。
- 书名、章节路径、表头和脚注加入检索投影；图片路径及通用占位符不进入嵌入文本。
- BGE 输入按实际 tokenizer 限制至 6144 tokens；超长结构在投影层划窗，保留原文坐标。重排按问题与文段总 token 数划窗。
- 默认每路召回 50、重排 30、返回 8 条；单条上下文预算 6000 字符、整次 24000 字符，实际参数见 [API 路由](src/hrs_platform/api.py)。
- 相交证据合并，必要标题、脚注与限定信息随结果返回；共享统计量不能因分行而被误归属。

索引检查点按文本、模型与规则指纹复用，只有变化或缺失输入重算。运行结果的 `retrieval_metrics` 记录计算／复用量；搜索的 `Server-Timing` 区分召回、嵌入、重排和上下文组装耗时。

### 重建索引

环境必须能访问对应 SQL、S3、OpenSearch 和检索模型。在根目录把下面 UUID 替换为实际已发布运行：

```powershell
& $platformPython -m hrs_platform.cli reindex --run-id '<书籍运行UUID>'
```

重建不重复 OCR、不绕过审核。少量已发布文本勘误另有 `amend-library`：仅支持唯一定位、等长、单页且同一来源片段内的改动，经本地原图核验后提交；不覆盖明确人工修改。输入列表包含 `chapter_id`、`expected_content_sha256`、`changes[{start,before,after}]`。修改后必须重建索引，期间旧检索代停用。非等长或跨页修订需要新的来源版本，不能推算偏移。

### 评测

- `evaluate --run-id UUID --cases dataset.json [--semantic]`：评测已发布书籍，题目格式由 [retrieval_evaluation.py](src/hrs_platform/retrieval_evaluation.py) 定义。
- `evaluate-offline --cases dataset.json`：创建独立 `hrs-offline-*` 临时索引比较候选策略，见 [离线评测实现](src/hrs_platform/retrieval_offline.py)。
- [Ragas 操作说明](evaluation/tools/ragas/README.md)：冻结真实检索上下文，再运行离线回答与模型评分。

评测数据与报告留在本地或 S3。单书、合成输入及同书新页段的结果不能推断跨书泛化；开发机器评审不能冒充人工金标。

## 制卡、回退与重试

制卡使用完整来源，不能用 Top-K 召回代替通读。阅读单元以 5000 字符为目标、无重叠并验证全字符覆盖。主 Agent 动态分工，最多两个阅读分工并行，各分工内部顺序推进；主题可跨章节合并或拆分，不预设一本书的卡片数量。

新任务按 6000 估算输入 tokens、最多 8 个结构单元装载阅读批次。已有阅读计划仍沿用原来的两单元布局，以便续用检查点；全书来源和通读记录不删改。综合阶段使用去除重复候选引文的阅读线索，引用必须来自实际提供的原文。

主题研究复用 [OpenSearch 检索](src/hrs_platform/search.py)，由 Agent 调用 [证据工具](src/hrs_platform/card_evidence.py)：

1. `search_evidence` 查支持材料、反证和限定。支持材料先查主题章节，无命中时扩大到本书；反证与限定查全书。沿用混合召回、重排和校准，返回预览与来源单元 ID。
2. `read_evidence` 读取固定来源原文，并保留关联脚注、表头和邻文。预览、线索与模型概括不能作为引用；工具不能读取其他书籍或任意文件。
3. 综合失败允许一轮补证。首次研究和补证各最多 3 个不同查询、3 次不同原文读取；章节空召回最多增加一次全书检索。单轮累计原文预算为 10000 估算 tokens，不截断原子表格。
4. 候选仍须通过引用精确定位、完整主题来源核验、本地原图核验和最终核验。证据不足保留待补证问题；目录或过渡文字属于阅读覆盖范围，不要求逐段生成卡片。

检索回执绑定书籍、来源快照、索引代次与校准策略。活动重试复用已完成查询和原文读取；索引或策略改变时停止混用，需新建任务使用新版本。搜索异常按活动策略有限重试，空结果不代表服务故障，也不证明不存在相关史料。执行图展示实际工具调用、检索目的、命中范围和回执。已有完成候选继续保留，不自动重制；要比较新旧方案，应创建新制卡任务。

| 情况                       | 当前处理                                                                                        |
| -------------------------- | ----------------------------------------------------------------------------------------------- |
| 引用或结构校验失败         | 保留错误与来源，反馈后有界重试；不删除关键引文来规避校验                                        |
| 输出被截断                 | 只有真实 `incomplete/max_output_tokens` 回执才提高下一次额度，至配置封顶；不拼接或采用半截 JSON |
| 局部阅读修订耗尽           | 保留已核验单元，未通过单元退回完整来源原文；撤下失败解读，明确标记原文回退，最终卡片仍需核验    |
| 提要／主题规划不收敛       | 按章节来源建立基础主题，并保留完整单元覆盖；不声称规划回退等于内容通过                          |
| 输入过大                   | 按来源单元局部再规划；不可拆原子结构仍超限则保留失败状态，不截断原表                            |
| 连接中断、408/409/429/5xx  | 暂停新请求、保存已完成检查点，由 Temporal 定时等待后恢复                                        |
| 鉴权、参数、存储或预算错误 | 保留失败证据，修正原因后使用显式重试，不作为内容问题吞掉                                        |
| 取消或删除                 | 中止对应执行与等待，不转换成内容回退                                                            |

同一固定输入最多三次生成尝试，网络与内容修复共用预算；默认等待 60、300 秒，尊重更长的 `Retry-After`。阶段累计超过 1 小时或 12 次等待会失败并保留进度。SDK 不额外重试。未收到响应不证明服务端未处理，重发仍可能计费。

检查点绑定完整输入、来源和规则；输入改变不能复用旧批准。关闭网页不终止工作流；服务恢复后由 Temporal 接续。执行页展示等待原因和预计重试时间。具体预算与恢复判断见 [工作流](src/hrs_platform/workflows.py) 和 [研究调用实现](src/hrs_platform/agents.py)。

## 运维

### 常见故障

| 现象                 | 先检查                                                                               |
| -------------------- | ------------------------------------------------------------------------------------ |
| 网页不能访问         | 启动器 `status`、18156 端口、Docker 应用状态                                         |
| 长时间停在 OCR／视觉 | GPU worker 与 broker 日志、模型安装记录、显存占用；`doctor` 健康不代表模型推理已成功 |
| 有底稿却未入库       | 内容核对页是否还有未解决／未核验范围；草稿保存不算通过                               |
| 检索缺少结果         | 书籍是否已发布、索引是否完成、SQL 中生效代及 OpenSearch 状态                         |
| 制卡等待或失败       | 执行页的阶段、请求预算与重试时间；修复配置或连接后重试同一任务                       |
| 删除失败             | 对应执行是否停止、清理回执中的失败原因；修复后重试删除，不手工删除共享 S3 对象       |

主机日志位于 `.cache/platform-diagnostics/`，缓存位于 `.cache/platform-compute/`。诊断时保留运行 ID、阶段与脱敏错误，勿把模型原文或密钥提交仓库。

### 备份与恢复

[备份实现](src/hrs_platform/backups.py) 使用 Docker 容器内的 PostgreSQL 工具，默认数据库容器名为 `historical-ingestion-acceptance-postgres-1`；其他容器名需设置进程变量 `PLATFORM_POSTGRES_CONTAINER`。数据库用户须有相应备份／建库权限。外部 PostgreSQL 和 S3 必须继续运行。

先停止上传与应用，并等待启动器退出。备份命令会检查应用／Temporal 容器及主机端口是否已停，但该检查不能替代对自定义部署的协调停写：

```powershell
& $platformPython scripts/hrs_v2.py stop
& $platformPython -m hrs_platform.cli backup | Set-Content -Encoding utf8 backup-reference.json
```

备份三个固定数据库：`historical_research_v2`、`hrs_temporal_v2`、`hrs_temporal_visibility_v2`。dump 和清单保存到 S3，返回文件只是清单对象引用，应在仓库外独立保管。**同一 S3 内的数据库备份不能抵御 S3 自身丢失**，还需独立 bucket／卷备份。

恢复创建新的 `hrs_restore_*` 数据库，同名目标会拒绝覆盖。下面的后缀必须换成新的小写字母／数字／下划线值，最长 20 字符：

```powershell
$backupReference = Get-Content -Raw backup-reference.json
& $platformPython -m hrs_platform.cli restore --manifest-reference $backupReference --restore-suffix drill_example
```

输出原库与新库的映射；失败创建的新库保留检查。核对记录、运行状态及 S3 引用后，再在维护窗口调整业务和 Temporal 连接。完整生产协调切换与 S3 灾难恢复仍需演练，不以单次测试库恢复宣称完成。

## 开发参考与验证

| 入口                                                                                           | 职责                                                                                                            |
| ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| [api.py](src/hrs_platform/api.py)、[OpenAPI](openapi.json)                                     | HTTP 路由与生成契约                                                                                             |
| [cli.py](src/hrs_platform/cli.py)                                                              | `migrate/api/namespace/worker/gpu-worker/doctor/backup/restore/reindex/amend-library/evaluate/evaluate-offline` |
| [workflows.py](src/hrs_platform/workflows.py)、[activities.py](src/hrs_platform/activities.py) | Temporal 编排与实际阶段                                                                                         |
| [domain/](src/hrs_platform/domain/)                                                            | 来源、研究合同与模型适配规则                                                                                    |
| [migrations/](src/hrs_platform/migrations/)                                                    | 版本化数据库迁移                                                                                                |
| [tests/](tests/)                                                                               | 行为与故障恢复回归                                                                                              |

接口变更后使用根 README 的 `npm run contracts` 命令同步后端 OpenAPI 与前端类型。API 导出不连接业务库；不手工维护另一份前端契约草案。

常规测试使用专用测试 SQL、S3 和 OpenSearch；完整配置见 [CI](../../.github/workflows/engineering.yml) 和 [测试 Compose](../../deploy/platform/compose.test.yml)。不要对业务库设置测试连接。

```powershell
& $platformPython -m pytest services/research-platform/tests services/runtime-support/tests -q
& $platformPython -m pytest scripts --collect-only -q
& $platformPython -m ruff check services/research-platform/src scripts/hrs_v2.py
```

`PLATFORM_TEST_TEMPORAL`、`PLATFORM_TEST_BACKUP`、`PLATFORM_TEST_MODELS`、`PLATFORM_TEST_SEARCH`、`PLATFORM_TEST_VISION` 为显式开启的集成检查；真实模型测试可能产生费用或占用 GPU。运行真实任务时不启用 `PLATFORM_TEST_RESTART_TEMPORAL`。测试结果以本次日志和 CI 为准，不在操作文档中固定历史通过数。
