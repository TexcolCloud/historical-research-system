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
| 根 `.env` 与进程环境变量                          | 主机 [Settings.load](src/hrs_platform/core/config.py) 先读文件，再以同名进程变量覆盖；优先读取 `PLATFORM_*` 字段，缺失时兼容部分 `INGEST_*`、`CARDS_*` 和 `DEEPSEEK_*` 字段     |
| [应用 Compose](../../deploy/platform/compose.yml) | 显式将根配置映射到容器；例如 `CARDS_REASONING_MODEL` 映射成容器的 `PLATFORM_REASONING_MODEL`。不能假定任意主机变量都自动进入容器                                             |
| 文本模型                                          | Settings 与 Compose 未配置时均使用 `deepseek-flash`；但当前 [配置模板](../../.env.platform.example) 显式设置 `CARDS_REASONING_MODEL=deepseek-v4-pro`，复制模板后会覆盖默认值 |
| 自动制卡                                          | `PLATFORM_AUTO_CARDS_ENABLED=false` 使书籍在索引后结束；不取消已创建任务，也不禁用独立手动制卡                                                                               |
| GPU 检索                                          | `PLATFORM_RETRIEVAL_DEVICE=cuda` 为默认，经主机 broker 执行；CPU 必须显式选择 `cpu`，不会静默回退                                                                            |
| 输出与调用预算                                    | 阅读首轮 16000、推理首轮 32000、输出封顶 64000 tokens；文本与视觉调用预算默认分别为 4096，实际以配置和回执为准                                                               |

要统一使用 Flash，在根 `.env` 设置两项 `CARDS_REASONING_MODEL=deepseek-flash`、`CARDS_READING_MODEL=deepseek-flash`；同时核对是否有更高优先级的 `PLATFORM_REASONING_MODEL` 或 `PLATFORM_READING_MODEL`。此说明不代表对用户现有配置作了修改。

## 分层与职责

目录职责参考 [Full Stack FastAPI Template 的固定版本](https://github.com/fastapi/full-stack-fastapi-template/tree/cb740b656d7a0a6c5e12c7bf8e50343ec94ee9c7/backend/app)，采用应用装配、分组路由、依赖注入和核心配置的组织方式。本项目的长任务逻辑独立于 HTTP 入口，保留既有 SQLAlchemy、S3 与 Temporal，不引入重复的 ORM、存储库包装层或模板中的用户／邮件业务。

| 位置 | 唯一职责 |
| --- | --- |
| [main.py](src/hrs_platform/main.py) | 装配 FastAPI、异常处理与应用资源生命周期；外部注入的数据库连接池由调用方管理 |
| [api/main.py](src/hrs_platform/api/main.py)、`api/routes/` | 按书籍、史料卡、运行、核对、检索、上传、事件和健康检查注册路由；负责请求校验、响应及 HTTP 头 |
| [api/deps.py](src/hrs_platform/api/deps.py) | 提供应用级资源和可覆盖的请求依赖，测试可以替换业务服务而不启动模型 |
| `services/` | 业务用例及其数据操作：上传事务、内容审阅、入库、检索、制卡、删除请求、导出；跨入口复用同一实现 |
| [services/lifecycle.py](src/hrs_platform/services/lifecycle.py)、[services/events.py](src/hrs_platform/services/events.py) | 共享任务状态事务及已提交事件的分发次序，不依赖 HTTP 路由或 worker |
| `jobs/` | Temporal 工作流、活动适配器、worker 注册／派发及转换 CLI 调用；不定义第二份业务状态规则 |
| `core/config.py`、`core/db.py` | 配置加载、数据库连接与迁移入口 |
| [models.py](src/hrs_platform/models.py)、[schemas.py](src/hrs_platform/schemas.py) | SQL 表结构与公开请求／响应模型，迁移历史保留在 `migrations/` |
| `domain/` | 已有文本、结构、token 与模型数据契约和算法；不依赖应用装配、路由或 worker |

依赖方向是 `main → api → services → core/models/domain`，`jobs → services`；业务代码不得反向导入 `main/api/jobs`。SQL 事务留在有业务含义的服务中，S3 对象归属保护由 `services/storage.py` 实现。既有异常的 HTTP 状态和响应格式保持兼容。Python 内部导入全部迁移到新位置，不保留空转的旧模块转发层；Temporal 工作流和活动名称、检查点键及 CLI 命令保持兼容。

`tests/test_app_composition.py` 验证依赖替换、应用资源隔离、生命周期及分层方向；其余测试继续覆盖原业务链路。离线回归使用合成内容和模拟模型响应，默认不启用真实文本／视觉模型测试。

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

当前分块规则为 `structure-1400-160-v6-evidence-scopes`，见 [retrieval_chunks.py](src/hrs_platform/services/retrieval_chunks.py)。已发布章节是正文与字符坐标的权威来源，索引不改写它。

- 正文以 1400 字符为目标、最多 160 字符重叠；连续标题携带首段。
- 表格按完整行组处理，保留表头、合并单元格及表前范围说明；脚注保留正文归属，不猜测歧义链接。
- 书名、章节路径、表头和脚注加入检索投影；图片路径及通用占位符不进入嵌入文本。
- BGE 输入按实际 tokenizer 限制至 6144 tokens；超长结构在投影层划窗，保留原文坐标。重排按问题与文段总 token 数划窗。
- 默认每路召回 50、重排 30、返回 8 条；单条上下文预算 6000 字符、整次 24000 字符，实际参数见 [API 路由](src/hrs_platform/main.py)。
- 相交证据合并，必要标题、脚注与限定信息随结果返回；共享统计量不能因分行而被误归属。

索引检查点按文本、模型与规则指纹复用，只有变化或缺失输入重算。运行结果的 `retrieval_metrics` 记录计算／复用量；搜索的 `Server-Timing` 区分召回、嵌入、重排和上下文组装耗时。

### 重建索引

环境必须能访问对应 SQL、S3、OpenSearch 和检索模型。在根目录把下面 UUID 替换为实际已发布运行：

```powershell
& $platformPython -m hrs_platform.cli reindex --run-id '<书籍运行UUID>'
```

重建不重复 OCR、不绕过审核。少量已发布文本勘误另有 `amend-library`：仅支持唯一定位、等长、单页且同一来源片段内的改动，经本地原图核验后提交；不覆盖明确人工修改。输入列表包含 `chapter_id`、`expected_content_sha256`、`changes[{start,before,after}]`。修改后必须重建索引，期间旧检索代停用。非等长或跨页修订需要新的来源版本，不能推算偏移。

### 评测

- `evaluate --run-id UUID --cases dataset.json [--semantic]`：评测已发布书籍，题目格式由 [retrieval_evaluation.py](src/hrs_platform/services/retrieval_evaluation.py) 定义。
- `evaluate-offline --cases dataset.json`：创建独立 `hrs-offline-*` 临时索引比较候选策略，见 [离线评测实现](src/hrs_platform/services/retrieval_offline.py)。
- [Ragas 操作说明](evaluation/tools/ragas/README.md)：冻结真实检索上下文，再运行离线回答与模型评分。

评测数据与报告留在本地或 S3。单书、合成输入及同书新页段的结果不能推断跨书泛化；开发机器评审不能冒充人工金标。

## 制卡、回退与重试

制卡使用完整来源，不能用 Top-K 召回代替通读。阅读单元以 5000 字符为目标、无重叠并验证全字符覆盖。主 Agent 动态分工，最多两个阅读分工并行，各分工内部顺序推进；主题可跨章节合并或拆分，不预设一本书的卡片数量。

新任务按 6000 估算输入 tokens、最多 8 个结构单元装载阅读批次，批次策略在首次执行时冻结，后续分批恢复不改变布局或预算估计。已有旧阅读计划仍沿用原来的两单元布局，以便续用检查点；全书来源和通读记录不删改。阅读结果先整批核验，遗漏结论单独补核，无法定位的整体失败不能批准单元。逐单元核验回执绑定原文、上下文、共享概括、单元解读和核验规则；局部修订复用未变化的回执，共享概括变化则重新核验受影响批次。

模型输入按来源 ID 去重，关联脚注与表头通过 ID/role 指向保留的完整原文，不以摘要替换证据。上一批阅读线索和综合输入去除重复候选引文。局部制卡修订只输出变更条目及必要元数据；程序合并后检查引用关系，仍对完整候选执行独立语义、主题全文覆盖和本地原图核验。未按条目建立可靠影响范围前，不跳过整卡最终核验。

主题研究复用 [OpenSearch 检索](src/hrs_platform/services/search.py) 和 [证据工具](src/hrs_platform/services/card_evidence.py)：

1. 新主题规划同时生成支持、反证、限定三类查询，程序先执行检索、去重与原文装配，模型可直接评估证据。旧检查点中未含查询的主题、规划回退及定向补证仍由 Agent 调用 `search_evidence`。支持材料先查主题章节，无命中时扩大到本书；反证与限定查全书。沿用混合召回、重排和校准。
2. `read_evidence` 读取固定来源原文，并保留必需的关联脚注、归属和表头；邻文返回可选 ID，需要时显式读取。预览、线索与模型概括不能作为引用；工具不能读取其他书籍或任意文件。
3. 综合失败允许一轮补证。首次研究和补证各按支持、反证、限定保留一个查询名额（改写复用该用途原查询）；章节空召回最多增加一次全书检索。原文读取使用追加检查点，已读来源的子集或重新组合直接复用，不再按三次调用限额阻断。单轮累计去重原文预算为 10000 估算 tokens，不截断原子表格；恢复输入携带已完成检索、已读原文和剩余额度。必需原文仍超限时拆分主题。
4. 候选仍须通过引用精确定位、完整主题来源核验、本地原图核验和最终核验。证据不足保留待补证问题；目录或过渡文字属于阅读覆盖范围，不要求逐段生成卡片。

检索回执绑定书籍、来源快照、索引代次与校准策略。活动重试复用已完成查询和原文读取；索引或策略改变时停止混用，需新建任务使用新版本。查询意图先保存，恢复前先补齐中断查询。工具进度不改变已完成模型结果的缓存身份；动态恢复上下文只在确需新请求时装配，真实请求体仍保存在 S3。空结果不代表服务故障，也不证明不存在相关史料。

新制卡工作流按检查点分批推进：每次最多补完 8 个阅读批次，主题阶段每次处理一个新主题；候选通过本地原图、最终文本和完整来源核验后即可采用，无需等待全书其他主题。批次清单不可变，服务等待携带当前批次和阶段，避免跳过尚未采用的候选。每批使用 Temporal Continue-As-New 延续同一工作流，控制历史增长。旧工作流历史通过 `card-incremental-v1` patch 保留原字符串活动入口；只有确认没有旧版在途工作流后才能删除此兼容分支。

规划回退按来源顺序以 8 单元、约 6000 输入 tokens 为目标打包基础主题，原子单元不能强拆，主题总数达到 256 时保留剩余完整范围；后续按章节／小节边界和 token 重量拆分。总主题上限保留，取消固定四层深度限制。已失败且不能继续拆分的主题保留问题，不阻断其他主题。

一轮完成后，可修复的候选或待补证主题自动进入最多两轮修订。已采用卡片不重制；相同失败指纹再次出现时停止自动修订，避免空转。原图缺失、不清、定位不明、未实际核验的项目保留原候选与审核证据，等待补齐原件或人工处理；同一任务中的其他可修复错误不会带动它们重新生成。自动修订不代表人工批准，不豁免任何采用条件。

| 情况                       | 当前处理                                                                                        |
| -------------------------- | ----------------------------------------------------------------------------------------------- |
| 引用或结构校验失败         | 保留错误与来源，反馈后有界重试；不删除关键引文来规避校验                                        |
| 输出被截断                 | 只有真实 `incomplete/max_output_tokens` 回执才提高下一次额度，至配置封顶；不拼接或采用半截 JSON |
| 单卡生成／定稿尝试耗尽     | 记录该卡问题，继续其余主题；轮末按失败指纹与次数限制决定是否自动修订                         |
| 局部阅读修订耗尽           | 保留已核验单元，未通过单元退回完整来源原文；撤下失败解读，明确标记原文回退，最终卡片仍需核验    |
| 提要／主题规划不收敛       | 按结构与 token 预算建立有界基础主题，保留完整单元覆盖；回退不代表内容通过                       |
| 输入过大                   | 按来源单元局部再规划；不可拆原子结构仍超限则保留失败状态，不截断原表                            |
| 连接中断、408/409/429/5xx  | 暂停新请求、保存已完成检查点，由 Temporal 定时等待后恢复                                        |
| 鉴权、参数、存储或预算错误 | 保留失败证据，修正原因后使用显式重试，不作为内容问题吞掉                                        |
| 取消或删除                 | 中止对应执行与等待，不转换成内容回退                                                            |

同一固定输入最多三次内容／未知结果尝试；文本服务故障不消耗内容修订次数，但所有实际请求仍计入整次任务调用上限，预算不会自动提高。静态总量预估只阻止预算不足的新任务；已有请求的恢复任务可继续回放已保存结果，每个新增请求仍在数据库锁内检查硬上限。文本、检索和本地视觉服务故障逐步退避，30 分钟封顶，文本请求尊重更长的 `Retry-After`。新制卡工作流等待累计达到一小时或超过 12 次时延续新历史继续等待，不要求手动点击；旧历史及书籍入库工作流保留原有有限等待策略。鉴权、参数、来源冲突和总调用预算不足仍须处理原因后显式恢复。SDK 不额外重试，未知响应重发仍可能计费。

检索工具进度不进入模型输入指纹，最终阶段保存失败可复用已完成模型结果。GPU 排队超过 60 秒交回持久化恢复；工具等待不消耗文本模型请求超时，HTTP 请求仍独立限时。取消检索携带任务和请求标识，终止所属 GPU 工作并等待底层线程结束；取消接口断连时也要等原调用退出，不能提前释放活动名额。

检查点绑定完整输入、来源和规则；输入改变不能复用旧批准。关闭网页不终止工作流；服务恢复后由 Temporal 接续。执行页展示等待原因和预计重试时间。具体预算与恢复判断见 [工作流](src/hrs_platform/jobs/workflows.py) 和 [研究调用实现](src/hrs_platform/services/agents.py)。

制卡活动心跳包含当前步骤及距最近检查点的时间。超过 30 分钟（或两倍文本请求超时，取较大值）没有新检查点会中止该次活动并按有限活动重试恢复；取消等待底层请求退出，避免占用尚未释放就重复启动。不把持续心跳视为业务进展。合成故障测试覆盖 SDK 异常包装、长期断连、视觉服务等待、分批采用、检查点恢复和自动修订去重；真实卡片准确度仍须另行验证。

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

[备份实现](src/hrs_platform/services/backups.py) 使用 Docker 容器内的 PostgreSQL 工具，默认数据库容器名为 `historical-ingestion-acceptance-postgres-1`；其他容器名需设置进程变量 `PLATFORM_POSTGRES_CONTAINER`。数据库用户须有相应备份／建库权限。外部 PostgreSQL 和 S3 必须继续运行。

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
| [api.py](src/hrs_platform/main.py)、[OpenAPI](openapi.json)                                     | HTTP 路由与生成契约                                                                                             |
| [cli.py](src/hrs_platform/cli.py)                                                              | `migrate/api/namespace/worker/gpu-worker/doctor/backup/restore/reindex/amend-library/evaluate/evaluate-offline` |
| [workflows.py](src/hrs_platform/jobs/workflows.py)、[activities.py](src/hrs_platform/jobs/conversion.py) | Temporal 编排与实际阶段                                                                                         |
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


## 生命周期、文件与状态恢复

所有书籍产物写入都必须提供 `run_id`。`storage.OwnedObjects` 在短数据库事务中登记文件归属，再写入 S3；登记不代表阶段完成。归属保留到任务删除，即使业务引用尚未提交、上传失败或进程重启，也能追踪和清理。对象级数据库锁协调写入和删除，删除时在锁内重新检查其他书籍归属；不同对象的写入互不等待。旧文件引用仍可读，删除仍遍历旧引用闭包，备份工具保留没有书籍任务的存储入口。

迁移 `0008_pipeline_reliability` 增加文件归属和事件投递序号。升级应暂停旧版本写入进程，执行 `hrs-platform migrate`，再统一启动新版 API、CPU/GPU worker；不要混跑未登记归属的旧写入代码。迁移保留已发出的 SSE 游标，不需重新 OCR 或清除现有书籍。

事件只在事务提交后分配投递序号，前端每 30 秒校准活动页面状态。PDF 和导出直接从 S3 流式读取，PDF 保留单范围请求；阅读正文使用每进程最多 32 MiB 的内存缓存。上传临时 PDF 在交接后释放，原图恢复使用请求独立的临时目录并在成功或失败后清理。CPU 共享临时卷上限为 2 GiB，为并发上传与恢复留出空间，不承担业务文件持久化。

异步业务阶段在独立线程事件循环运行，主 worker 保持心跳和调度响应；取消会等待底层操作结束。数据库语句与行锁等待分别限制为 30 秒、10 秒；对象锁等待会发送心跳并限制为 5 分钟。索引与制卡共用检索恢复策略和任务取消标识，已保存的嵌入批次和检索回执可在服务恢复后复用。
