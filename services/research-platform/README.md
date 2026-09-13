# Research Platform V2

统一业务后端已覆盖上传、转换、逐项核对、章节组织、连续阅读、检索、动态研究制卡与机器采用。**整体交付验收仍在进行**：真实《华北治安战 上》正在转换，整书视觉核验、用户人工核对及最终制卡尚未完成。技术样本不能代替实际书籍验收。

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
6. Agent 依据实际章节动态分工；逐段阅读、独立核验、综合及候选对完整来源复核。相邻章节上下文和全书性质说明随分工保留。
7. 本地 Qwen 原件核验后，将实际页区观察交给最终文本复核，关闭已解决的页码、版面或来源疑问。必要时修订分析及证据的归属说明、上下文和局限，已核验的引文、来源锚点、正文和释读逐字段固定。初次文本、原图和最终文本复核均通过才采用；失败保持候选状态，不新增常规人工制卡确认。

模型原始请求/响应及中间结果持久保存。每个文本步骤最多三次发送，整个运行受调用预算约束；恢复先复用已完成回执。显式重试增加恢复轮次，不能把失败或不确定状态当作机器通过。

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

已审阅章节仍是正文和来源的权威记录。检索使用 `structure-1400-160-v2`：按 Markdown 标题和段落组织，长正文采用 1400 字符目标和最多 160 字符重叠；表格按完整行组拆分，HTML 跨行单元格、列表项、代码和注释保留完整结构，必要时允许超长块。书名、章节路径、对应表头及显式脚注只加入检索投影，不改写正文和引用坐标。脚注关联限于同章标准 `[^id]` 标记，不猜测未标明的归属。

检索先召回，再按需进行本地 BGE 重排；返回阶段合并相交证据，补充同节上下文与来源映射。默认候选数 20、结果数 20、每条上下文总预算 6000 字符、全次查询预算 24000 字符，接口可分别调整。预算优先留给命中正文；放不下的补充内容提示打开章节继续阅读。

检索投影使用本地 BGE tokenizer 验证，控制在 6144 tokens 内。超长原子结构仅在检索输入中划分为连续来源窗口（每窗最多 6000 字符），优先换行或句末边界；章节原文不改写。较短表头随窗口保留，放不下的完整补充仍保存在来源映射中供展开阅读。重排按实际“问题＋文段”的 token 数划窗，对同一候选取最高窗口得分，不让超长候选导致整个查询失败。常规 1400 字符分块无需改变。

索引缓存依赖章节内容摘要、标题、分块规则、输入窗口规则和嵌入模型版本。向量按实际检索文本摘要复用，同一书籍运行中仅计算变化或缺失的文本；每 8 个唯一输入提交一次 S3 批次回执，失败后复用已提交批次，无需等整书向量计算完成。新一代完整写入后切换 SQL 中的生效版本，再清理旧索引文档；中途失败继续读取上一代。首次入库自动使用当前策略。已发布书籍可从配置了平台环境和检索模型的 CPU worker 执行：

~~~sh
python -m hrs_platform.cli reindex --run-id <书籍运行UUID>
~~~

该命令只重建检索，不重复 OCR，也不绕过整书核验。制卡仍完整遍历章节，其 5000 字符、无重叠阅读单元保持独立。合成结构与故障恢复测试不能替代真实书籍的检索质量评测。

索引耗时、计算数与复用数保存在运行结果的 `retrieval_metrics` 中（失败也记录）；检索响应的 `Server-Timing` 分别提供关键词召回、查询向量计算、向量召回、重排及上下文组装耗时。分块耗时包含章节读取和 tokenizer 初始化，不应误当作纯解析耗时。

小规模评测复用真实检索接口实现，输入 JSON 数组，每项为 `{"query":"运输量","expected":[{"chapter_id":"章节UUID","start":10,"end":30}]}`；范围为已发布章节的零起点字符坐标，结束位置不包含。运行：

~~~sh
python -m hrs_platform.cli evaluate --run-id <书籍运行UUID> --cases cases.json --limit 10
# 加 --semantic 使用本地向量召回与重排。
~~~

输出包含完整预期证据范围的召回比例、累积结果首次覆盖证据的倒数排名、来源坐标一致性、P50/P95 耗时及输入摘要。相同题集和原文摘要才适合版本对照；少量样本的 P95 只是观察值。合成题集、机器标注以及来源一致性均不代表人类金标准或史实正确性。未发布书籍不能通过评测入口绕过审核。

/api/v2 提供 books、uploads、runs/retry、reviews/draft/decisions、chapters、search、cards、executions、exports 和 SSE。原件支持 HTTP Range。Markdown 导出保存于 S3，删除下载缓存后仍可读取；卡片导出保留完整日期、实体、证据关系和核验记录。

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
