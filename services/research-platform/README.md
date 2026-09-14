# Research Platform V2

统一业务后端已覆盖上传、转换、逐项核对、章节组织、连续阅读、检索、动态研究制卡与机器采用。**整体交付验收仍在进行**：真实书籍已完成审核、入库及嵌入，整书制卡与最终采用仍待实测通过。技术样本不能代替实际书籍验收。

## 运行结构

- FastAPI / Pydantic / SQLAlchemy / Alembic：统一接口、事务与迁移。
- Uppy / Golden Retriever / tusd：可恢复上传与许可，文件直接进入现有 SeaweedFS S3。
- Temporal：书籍主流程、制卡子流程、活动重试和人工持久等待。数据库 outbox 只发送已提交事件，不重复调度业务阶段。
- CPU worker：章节组织、DeepSeek 文本研究、CPU BGE 向量检索与重排。GPU worker：冻结的 Docling + PaddleOCR-VL-1.6 和本地 Qwen3-VL-8B；共享 GPU broker 及系统互斥。
- PostgreSQL 保存业务记录与产物引用；S3 保存原件、OCR、原图、章节、模型回执、草稿及导出；OpenSearch 是可重建索引。
- React Router / TanStack Query / openapi-fetch：书籍、待办、史料卡和书籍内工作区；Tiptap、React Flow/Dagre 按需加载。

领域规则已移入 hrs_platform/domain，新后端不再依赖旧 ingestion/retrieval/cards 整包。来源与摘要见 output/refactor-v2/domain-retention.json。原 OCR CLI 不变；新增 document_extraction.stages 仅拆开现有转换和核验步骤，未调整识别规则或模型。

## 内容与恢复规则

1. 上传完成后验证 PDF 格式、大小和摘要，幂等创建运行。
2. 新 GPU 活动完成 OCR 后先提交 S3 的 ocr-evidence，再执行全量视觉核验。计算缓存丢失时恢复已提交 OCR，不重复识别。
3. 本地模型修正经过独立原图复核且无未解决问题后自动写回，保留原文、修改和机器凭据；审核范围按修改前后的实际差异定位，无变化的上下文不扩大为待办。跨段重组或归属不明的边界插入仍保留整体核对。未完成核验、疑难及无法定位的范围进入问题清单。保存草稿不会放行，机器也不覆盖人工决定或草稿。全部必审范围处理完才允许入库和分块。
4. 单问题确认只提交局部版本、决定、计数和 outbox，响应返回下一问题；不等待整书重算或固定轮询。
5. 自动组织章节，保留全书字符覆盖及逻辑章节/物理页映射，不增加文章结构人工批准。
6. 制卡先冻结已审核章节的全文阅读快照，重试复用同一来源。Agent 按章节动态分工，最多两个独立分工并发，各自的阅读批次按顺序接续；逐单元核验后再按全书阅读记录规划卡片主题。主题可跨分工合并、同一分工可拆多卡，每个原文单元恰好归属一个主题，上下文可共享。候选仍对主题完整来源复核，不用 Top-K 检索替代通读。
7. 原文/原表、译者/编者更正和研究者推断分别保留归属、引文与限制，不能静默以译注替换原表。本地 Qwen 原件核验后，将页区观察交给最终文本复核；修订分析时固定已核验引文、来源锚点、正文和释读。分析发生改变后，重新检查完整来源中的未引用限制。初次文本、原图、最终文本及必要的来源复核均通过才采用；失败保持候选状态，不新增常规人工制卡确认。

模型原始请求/响应及中间结果持久保存。同一固定输入在一个恢复轮次内最多三次发送，整个运行受调用预算约束；恢复先核对输入再复用已完成回执。上游修订改变输入时使用输入摘要隔离保存，旧回执不覆盖、不误用；输入未变时保留原来的请求上限。显式重试增加恢复轮次，不能把失败或不确定状态当作机器通过。请求保存、初始化、预算耗尽与取消均由统一执行节点作用域收尾；任务终止失败还会关闭遗留运行节点。

逐单元核验保留完整批次 source_units，用 required_object_ids 限定本轮目标；共享概括的批次成员不因拆分核验而变成旁读上下文。局部修复由程序恢复已核验的固定单元记录，再重新核验发生变化的输入；模型无需靠逐字段重写来维持已通过内容。整卡来源窗口同时声明完整候选的来源单元范围，不能把窗口外的候选内容误判为全卡无来源。前端按同一任务比较事件版本，终态事件刷新执行图而不覆盖入库状态。

阅读修订必须原样保留已核验单元记录；单元核验按完整输入摘要复用，原文、旁读上下文或共享概括改变就失效。整卡语义核验和完整来源核验也按输入摘要区分回执，不能凭条目 ID 沿用旧批准。提要使用已有 DeepSeek tokenizer 的 6000-token 估算阈值；制卡输入（提示、载荷、输出 schema）保守估算上限 48000 tokens，超出时在发送前停止并保留证据，不截断原子表格或伪报完成。尚未实现超大主题的自动再规划，模型规划出超限主题时需缩小范围后重新运行。

首次真实制卡已暴露批次范围与异常收尾问题；本轮修复使用保存回执和合成离线测试，没有追加模型调用，不能据此声明实际卡片质量已验收。修正后的阅读核验按新输入重新执行，匹配的规划、来源快照及初始阅读仍可复用，旧回执保留。三轮内容修复后仍有实质问题时直接结束活动等待处理，避免 Temporal 自动重试再次启动其他阅读分工；显式恢复仍使用现有重试入口。

正文—脚注采用同一组原文字符范围，供阅读器双向跳转和检索上下文使用。支持明确的 Markdown 脚注及同一原件页内唯一配对的圈号；重号按物理页隔离，缺失或歧义注号保留原文，不按最近段落猜归属。注释与译者署名保留原位置，渲染链接不改正文、字符偏移或页面路由。Docling 图片通过既有 S3 原件接口读取，不使用 Windows 本地图片路径。

旧待办可在配置好的计算环境运行 `python scripts/repair_pending_reviews.py RUN_ID` 修复定位及合并重复页级待办；加 `--recheck` 用现有 OCR 底稿重新进行本地视觉核验，`--pages 54 75` 可限制核验页码。每次写回使用现有版本检查和局部决定事务，保留人审记录、草稿和原始范围；不重新 OCR，也不处理已发布书籍。

新业务只使用 historical_research_v2、Temporal 专用数据库及 S3 hrs/v2/，不迁移或读取旧业务文件。模型权重、诊断日志、.cache/platform-compute 计算缓存可留在主机，均不作为业务文件的权威存储。

## 部署

当前验证配置：Windows GPU 主机 + Docker Desktop。API、CPU worker、Web、Temporal、tusd 使用 deploy/platform/compose.yml；GPU worker/broker 使用受控 Windows 进程。未声称支持未经验证的 GPU 容器。

先启动已有 PostgreSQL 55436、SeaweedFS S3 58333 和 OpenSearch 19260。根 .env 使用现有 INGEST_DATABASE_URL、INGEST_DEV_DB_PASSWORD、INGEST_S3_* 基础设施配置以及 DEEPSEEK_API_KEY；这里只复用存储凭据，不读取旧业务命名空间。

~~~powershell
$env:UV_PROJECT_ENVIRONMENT="$PWD/.cache/engineering-envs/research-platform"
uv sync --project services/research-platform --locked --group dev --extra retrieval
.cache/engineering-envs/research-platform/Scripts/python.exe scripts/hrs_v2.py init
.cache/engineering-envs/research-platform/Scripts/python.exe scripts/hrs_v2.py up --build
~~~

init 幂等创建业务、测试、Temporal、visibility 四个专用数据库并应用官方迁移，不重置已有数据。up 先迁移再启动 Compose 应用与本机 GPU 进程；端口被其他进程占用时拒绝切换。

默认网页 http://127.0.0.1:18156/，API 18170；/platform.html 与 / 使用同一新入口。PLATFORM_API_PORT、PLATFORM_WEB_PORT 可用于隔离验收。本配置只发布回环端口，尚未执行用户授权的全项目多用户权限/边界验收。

~~~powershell
.cache/engineering-envs/research-platform/Scripts/python.exe scripts/hrs_v2.py status
.cache/engineering-envs/research-platform/Scripts/python.exe scripts/hrs_v2.py doctor
~~~

doctor 分别检查数据库、S3、OpenSearch、worker 和 broker；GPU 满载时检查 Temporal 心跳，不因暂停取任务而误报离线。它不会加载模型或修改内容。API 的 /health/ready 不等于内容验收通过。

在 up 终端 Ctrl+C，或从另一终端执行 `scripts/hrs_v2.py stop`，先退出 GPU worker/broker，再停止 V2 Compose 服务，不使用 down -v。启动器跟踪自己的解释器与模型子进程；API/Web 端口尚在释放时最多等待 30 秒，仍占用则拒绝启动。日志位于 .cache/platform-diagnostics/。

2026-09-13 已切换至独立 V2 启动器：API/Web/CPU worker 容器就绪，GPU worker 心跳正常。真实书籍恢复时 481 页全部命中识别缓存、OCR 调用为 0，已进入本地 Qwen 全量视觉核验。原件、识别缓存和阶段回执保留；这不是整书内容通过声明。

## 接口与开发

### 结构化检索分块

已审阅章节仍是正文和来源的权威记录。检索使用 `structure-1400-160-v5-heading-note-units`：连续标题随其首段组织，同节正文在预算内合并；长正文采用 1400 字符目标和最多 160 字符重叠。表格按完整行组拆分，HTML 跨行单元格、列表项、代码和注释保留完整结构，必要时允许超长块。已定位脚注及单独排版的署名组成一个来源范围；Markdown 脚注和同页印刷注号沿用阅读器的归属规则，不猜测歧义关系。书名、章节路径、对应表头及脚注只加入检索投影，不改写正文和引用坐标。图片地址与通用 Image 占位符不进入嵌入文本，有意义的图片说明和原件来源仍保留。

检索先召回，再按需进行本地 BGE 重排；返回阶段合并相交证据，补充同节上下文与来源映射。搜索接口默认每路候选 50 条、重排 30 条、返回 8 条，每条上下文总预算 6000 字符、全次查询预算 24000 字符，接口可分别调整。预算同时预留命中正文及来源关联的必要补充，放不下的完整证据不作为完整结果返回。

检索投影使用本地 BGE tokenizer 验证，控制在 6144 tokens 内。超长原子结构仅在检索输入中划分为连续来源窗口（每窗最多 6000 字符），优先换行或句末边界；章节原文不改写。较短表头随窗口保留，放不下的完整补充仍保存在来源映射中供展开阅读。重排按实际“问题＋文段”的 token 数划窗，对同一候选取最高窗口得分，不让超长候选导致整个查询失败。常规 1400 字符分块无需改变。

索引缓存依赖章节内容摘要、标题、分块规则、输入窗口规则、来源结构报告摘要和嵌入模型版本。重建时重新投影已核对的结构关系，无需重新发布章节。续表仅在来源唯一定位、相关页面已通过审核、列归属完整且候选保留每个单元格时共享表头；页内闭合的 rowspan 可以保留，跨页未闭合或丢失数值的候选不采用。表格脚注命中也携带该表头。向量按实际检索文本摘要复用，同一书籍运行中仅计算变化或缺失的文本；每 8 个唯一输入提交一次 S3 批次回执。新一代完整写入后切换 SQL 中的生效版本，再清理旧索引文档；中途失败继续读取上一代。首次入库自动使用当前策略。已发布书籍可从配置了平台环境和检索模型的 CPU worker 执行：

~~~sh
python -m hrs_platform.cli reindex --run-id <书籍运行UUID>
~~~

该命令只重建检索，不重复 OCR，也不绕过整书核验。制卡完整遍历章节，复用结构化解析、脚注归属、表头和合并单元格口径，采用 5000 字符目标、无重叠的阅读单元并校验全字符覆盖；检索投影不会作为原文引证。合成结构与故障恢复测试不能替代真实书籍的检索质量评测。

调试检索时可设置 `PLATFORM_AUTO_CARDS_ENABLED=false` 并重启 API/CPU worker：书籍索引完成后直接完成书籍流程，不创建自动制卡任务。默认值为 true；独立手动制卡入口不受此开关影响，已经创建的制卡任务不会被此开关撤销。

本轮已审核书籍的固定基线及对照结果见 [检索调优记录](RETRIEVAL_TUNING.md)。包含原文的评测输入、旧索引向量及证据回执存放在 S3，不提交 Git。

索引耗时、计算数与复用数保存在运行结果的 `retrieval_metrics` 中（失败也记录）；检索响应的 `Server-Timing` 分别提供关键词召回、查询向量计算、向量召回、重排及上下文组装耗时。分块耗时包含章节读取和 tokenizer 初始化，不应误当作纯解析耗时。

小规模评测复用真实检索接口实现，输入 JSON 数组，每项为 `{"query":"运输量","expected":[{"chapter_id":"章节UUID","start":10,"end":30}]}`；范围为已发布章节的零起点字符坐标，结束位置不包含。运行：

~~~sh
python -m hrs_platform.cli evaluate --run-id <书籍运行UUID> --cases cases.json --limit 10
# 加 --semantic 使用本地向量召回与重排。
~~~

输出包含完整预期证据范围的召回比例、累积结果首次覆盖证据的倒数排名、来源坐标一致性、P50/P95 耗时及输入摘要。相同题集和原文摘要才适合版本对照；少量样本的 P95 只是观察值。合成题集、机器标注以及来源一致性均不代表人类金标准或史实正确性。未发布书籍不能通过评测入口绕过审核。

/api/v2 提供 books、uploads、runs/retry、reviews/draft/decisions、chapters、search、cards、executions、exports 和 SSE。原件支持 HTTP Range。Markdown 导出保存于 S3，删除下载缓存后仍可读取；卡片导出保留完整日期、实体、证据关系和核验记录。

史料卡查看页展示形成/事件时间及其依据、人物地点身份依据、原文陈述归属、推断/分歧状态、上下文、表格口径、限制、其他解释和待查问题。引用证据、日期依据与论证关系可在卡内双向定位；跳转不改写路由片段。卡片选择写入 URL，支持返回历史与缓存切换，切卡清除上一张的出处面板。

详情接口的 `quote_locations` 按条目及引文序号返回来源单元内的 Unicode 码点范围（左闭右开）与实际 PDF 物理页，复用导出的引文定位逻辑；重复引文按 occurrence 定位，跨页保留全部命中页。网页在原始 Markdown 文本摘录中高亮指定引文，完整渲染正文可另外展开。缺少正文或页码依据时明确提示，不用来源单元首页冒充引文页码。新增定位数据按需读取计算，不修改既有卡片、审核记录或原文。

~~~powershell
.cache/engineering-envs/research-platform/Scripts/python.exe -m hrs_platform.export_openapi
node services/review-workbench/tooling/openapi/node_modules/openapi-typescript/bin/cli.js services/research-platform/openapi.json -o services/review-workbench/src/platform/schema.d.ts
npm --prefix services/review-workbench run build
~~~

接口导出不连接业务库、不加载 Agent SDK。生成器使用单独锁定的 TypeScript 5 工具包；应用继续使用 TypeScript 7，不绕过 peer 依赖约束。

## 验证与交付差距

数据库测试只允许 historical_research_v2_test 的唯一临时 schema。技术测试、合成材料和 GPT 开发评审都不是用户人工批准。

~~~powershell
.cache/engineering-envs/research-platform/Scripts/python.exe -m pytest services/research-platform/tests services/runtime-support/tests --basetemp .scratch/pytest-platform-current
# 按需启用 PLATFORM_TEST_MODELS=1、PLATFORM_TEST_TEMPORAL=1。
# 实际工作运行期间，不要启用 PLATFORM_TEST_RESTART_TEMPORAL。
~~~

output/refactor-v2 保存 Tus/S3 续传、幂等入站、Temporal 人工等待与恢复、局部决定和草稿、来源覆盖、CPU 检索重排、繁简查询、缓存丢失与索引重建、实际 DeepSeek 文本候选、浏览器布局及容器健康检查证据。最新代码持续回归，实际结果以对应日志为准。

浏览器实际完成 17 MB 技术文件的暂停、刷新、重新选取和从 8 MB 继续上传；没有创建第二个上传对象。旧前端 111 个文件、旧后端及适配/脚本 288 个文件和旧 Python 打包入口已存档撤除。新的独立 PostgreSQL/S3 CI 环境通过现役回归。

数据库备份恢复的独立测试库演练已通过，操作和限制见 [运维说明](../../docs/platform-operations.md)。本地 Qwen 视觉门禁实际识别合成原图中的 120 吨，并拒绝 130 吨误引；对应机器采用与回执复用通过测试。该测试用固定文本回执隔离视觉门禁，不等于全书生成质量验收。

待完成：实际整书视觉/用户人工/卡片验收；完整生产协调恢复与独立 S3 备份部署。现有检查不代替这些验收，本说明不是全部完成声明。

现行进度及运行故障修复证据统一见 [V2 当前记录](../../docs/refactor-delivery-status-20260913.md)。真实书籍可在处理进展页查看初轮有效核验页数；不可用响应不计入该数字，机器结果仍不等于人工通过。

书籍任务删除使用 `DELETE /api/v2/books/{book_id}` 和现有 Temporal 工作进程。先停止执行再清理，失败保留回执并允许重试；不删除其他书籍仍引用的对象。实施与验证见 [删除说明](../../docs/book-task-deletion-20260913.md)。

### GPU 检索与独立评测（2026-09-14）

研究检索默认关键词与向量各召回 50 条，RRF 融合后最多重排 30 条，返回 8 组证据。
`semantic=false` 为快速查词，`diverse=true` 用于跨章节综合；这些是可调起始值。
必要脚注、表头、表注先于低排名结果分配预算。无法完整容纳必要信息的证据组不返回，
继续检查后续候选补足结果，并记录 `budget_rejected`；不会将删掉限定条件的数字当作完整证据。
跨章节综合只在重排前 `2 * limit` 条中轮转，防止远端弱相关章节被优先提升。

超长表格按完整行及 rowspan 组限制输入。单个不可分割行组仍超限时，保持完整原文范围，
只将模型使用的文本视图拆成有独立索引 ID 的窗口；`atomic_source_oversized` 标明这种情况。
这类完整证据若超过返回预算仍会被明确淘汰，不冒充完整且可容纳的片段。
长脚注优先按段落拆分，保留正文归属。输入规则版本变化会触发可恢复的重新索引，不重做 OCR。
嵌入检查点按 token 长度排序组批；模型适配器按真实 token 长度组批并恢复输入顺序，OOM 缩批策略保留。

`PLATFORM_RETRIEVAL_DEVICE=cuda` 通过主机现有 GPU broker 运行嵌入和重排。
`PLATFORM_RETRIEVAL_ENDPOINT` 默认 `http://127.0.0.1:18160/retrieval`，容器使用
`host.docker.internal`。主机需要安装 research-platform 的 `retrieval` extra CUDA 环境；
不可使用仅供测试的轻量 CPU 环境启动生产 broker。
请求取得共享 GPU 锁后卸载视觉模型，使用一个自有检索子进程。两个检索模型可连续驻留；
下一次 OCR 或视觉请求先释放检索进程。错误及超时释放该进程，返回可重试错误。
CPU 运行必须显式设置 `PLATFORM_RETRIEVAL_DEVICE=cpu`，不会静默回退。
当前长时间机审结束后重启主机 broker，才启用新调度代码。
模型冷启动默认允许 600 秒，可用 `HRS_VISION_START_TIMEOUT` 调整（30–900 秒）。
`vision_loading` 事件记录本次上限，超过上限仍返回可恢复错误，不把启动失败当成内容审核通过。

`hrs-platform evaluate-offline --cases dataset.json` 使用独立 `hrs-offline-*` 临时索引，
对比关键词、旧候选策略（每路 20 条直接重排）、新融合策略、50 条重排及每路保留 3 条候选，
不写入文献库或改变审核状态。
JSON 包含 `title`、`chapters`、`cases`。章节沿用原文及来源映射，并带有
`development_review`：`text_sha256`、`image_sha256`、`original_first=true`、
`human_review=false`。问题包含 `query` 和 `expected`（chapter_id/start/end）；空 expected
表示无答案，报告其返回候选数量，不将候选视为确认答案。标准输出为评测报告。
各组共享当前分块及上下文规则，比较的是候选策略，不是完整历史实现。
可用 `variants: [["名称", true, {"rerank_limit": 50, "lane_quota": 3}]]` 指定对照；
`min_rerank_score` 仅供内部检索/离线标定试验，默认不启用，不暴露为用户百分比置信度。
报告提供分类召回率和 `threshold_diagnostics`（有答案保留率/无答案拒绝率），
需在独立数据上验证后才可启用阈值；诊断本身不会改变正式问答策略。

运行 `python scripts/build_retrieval_benchmark.py --output output/retrieval-fixtures.json`，
可生成 40 个合成章节、100 个问题（80 个可回答、20 个无答案），包含表格、脚注、
跨章节、相近年份/地点/军用运输干扰项。生成器不附带机器批准，需先核对原件并补齐上述证据。
合成回归与真实书籍评测分开报告，不能把模板题的高召回当作真实书籍准确率。
开发样本不构成生产放行；需要保留未参与调参的书籍才能判断泛化收益。

### 证据装配与已发布文本勘误

检索规则 `structure-1400-160-v6-evidence-scopes` 保留小节前明确引出下文的段落、
续表首表的事件说明及表头；共用单元格通过 `SearchHit.table_scopes` 返回原 HTML
派生的行列范围，不能把共用统计量分配给单独一行。附属正文仍带不可变来源坐标。
完整段落不再自动附带相邻背景；切断的段落、标题后的正文、脚注和必要限定仍保留。
正文字符预算不含派生范围元数据；离线评测另记录模型实际收到的总上下文字符数。

已发布书籍的少量等长勘误可用 `hrs-platform amend-library --run-id UUID --amendments changes.json`。
输入为列表，每项包含 `chapter_id`、`expected_content_sha256`、`changes`；每个改动包含
`start`（章节 Unicode 字符偏移）、`before`、`after`。一个章节只提交一项请求。
仅支持唯一定位、等长、单页、同一来源片段内的修订；不覆盖明确人工修改。
该主机命令复用 document-extraction 的 `.venv` 与现有本地视觉服务，逐处对原图独立核验；
失败不修改正文，通过后正文引用、事件和恢复检查点在同一 SQL 事务提交。
旧 S3 正文与核验记录保留。随后必须执行 `hrs-platform reindex --run-id UUID`，
修订至重建完成期间该书旧检索 generation 停用，避免混用旧索引和新原文。
非等长或跨页修改需要重新生成来源版本及下游分块，不能用此命令推算偏移。
这不是重新 OCR 或更改人工放行状态的入口，生产容器不额外安装 OCR 依赖。

Ragas 使用独立依赖环境，运行方式见 [评测工具说明](evaluation/tools/ragas/README.md)，
已冻结的实测结果与边界见 [评测报告](RAGAS_EVALUATION.md)。
