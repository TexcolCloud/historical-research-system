# 当前 HTTP 接口：史料入库与文档组织

本文件记录已实现的 `0.1.0` 接口，不代表完整首版已经验收。
机器契约由代码生成：[OpenAPI](contracts/openapi.json)、[包装清单 Schema](contracts/ingestion-package.schema.json)。
`/docs` 与 `/openapi.json` 提供同一份在线契约。前端尚未制作。

## 通用约定

- 基地址 `http://127.0.0.1:8766/api/v1`；请求与成功响应为 JSON，UUID 用字符串表示，时间为带时区 UTC。
- 已声明的校验/业务错误使用 `application/problem+json`，含 `code`、`detail`、`request_id`、`retryable`、`errors`。
  JSON 语法错误为 400，契约不匹配为 422，版本/幂等冲突为 409，未就绪为 503。
- 每次响应返回新的 `X-Request-ID` 和 `Cache-Control: no-store`。
  任务中的 `request_id` 标识最初创建任务的请求，不会被轮询或重放的请求标识覆盖。
- 所有写操作要求 `Idempotency-Key`（1–200 字符且非全空白）。同一作用域下相同键、相同参数返回原操作；
  相同键配不同参数返回 409 `idempotency_key_reused`。输入准备以操作种类为作用域，任务控制以任务和动作区分。
- 当前无认证、权限、公网部署或多租户承诺，仅用于本机开发；不要把接收目录权限开放给不可信调用者。

| 方法与路径 | 用途 | 当前成功返回 |
| --- | --- | --- |
| `GET /health/live` | HTTP 进程存活 | 200；不连接数据库 |
| `GET /health/ready` | 当前已实现的来源入库运行环境 | 200；未就绪 503 |
| `POST /input-preparations` | 排队准备一个本地目录 | 首次 202；重放可能 200 或 202，须读任务状态 |
| `GET /jobs/{job_id}` | 持久任务状态与结果 | 200 |
| `POST /jobs/{job_id}/cancel` | 请求取消当前执行轮次 | 200，控制回执及当前任务 |
| `POST /jobs/{job_id}/retry` | 重试失败或取消的任务 | 首次 202；重放 200 |
| `GET /prepared-inputs/{input_id}` | 冻结文件清单与输入指纹 | 200 |
| `POST /imports` | 提交独立来源导入任务 | 首次 202；重放 200，须继续读任务状态 |
| `GET /imports` | 按载体、结果或任务状态浏览导入 | 200，游标分页 |
| `GET /imports/{import_id}` | 导入回执与任务状态 | 200 |
| `GET /carriers` | 按名称、原件摘要或待采用状态浏览载体 | 200，游标分页 |
| `GET /carriers/{carrier_id}` | 载体原件、当前快照与提取统计 | 200 |
| `GET /carriers/{carrier_id}/extractions` | 载体历次提取及采用状态 | 200，游标分页 |
| `GET /extractions/{extraction_id}` | 正文/证据标识与原始证据记录 | 200 |
| `GET /extractions/{extraction_id}/pages` | 物理页与页图、审核记录绑定 | 200，游标分页 |
| `GET /extractions/{extraction_id}/artifacts` | 图片收录与表格证据引用 | 200，游标分页 |
| `GET /current-input?target_type=carrier&target_id=UUID` | 载体当前采用的固定快照 | 200；没有当前结果为 404 |
| `GET /snapshots/{snapshot_id}` | 固定快照元数据 | 200 |
| `GET /snapshots/{snapshot_id}/segments` | 固定快照中的来源片段 | 200，游标分页 |
| `POST /source-excerpts` | 按固定来源范围回读文本 | 200；只读，不要求幂等键 |

上传、媒体读取、草案采用、目录、阅读组合、用途限制和增量读取也已有实际路由；完整机器合同以生成的 OpenAPI 为准。
当前 `current-input` 支持载体、收录实例和组织目标。规则分件候选已实现；制卡与向量索引属于后续模块。

## 1. 包装清单

接收目录下每个包包含 `ingestion-package.json`。下例仅是工程传输示例，
`example.md` 必须恰好为三个 ASCII 字节 `abc`，没有尾部换行；不是实际史料：

```json
{
  "schema_version": 1,
  "files": [
    {
      "path": "example.md",
      "role": "markdown",
      "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
      "byte_length": 3
    }
  ],
  "source_bindings": []
}
```

`files` 必须非空；每项还可提供 `upstream_id`。可选 `upstream_entry` 指明上游入口。
清单与文件声明拒绝未知字段；输入准备原样保留 `source_bindings`，正式导入验证已知来源绑定语义。
不要将上游原始清单替换成此包装清单：包装清单只是传输层外壳，上游文件作为声明文件原样保留。

包内路径使用 `/` 分隔的规范相对路径，不能绝对定位、使用 `..`、反斜杠、盘符或穿越链接。
按 Unicode NFC＋不区分大小写检查重名；不得把 `ingestion-package.json` 再列入 `files`，系统自动归档它。
默认限制为 8 MiB 包装清单、10,000 个声明文件、8 GiB 声明文件总量；这些是配置上限，不是大包性能验收结果。
未声明文件不会被自动扫描并纳入已冻结清单。

## 2. 提交输入准备

对包装清单**原始字节**计算 SHA-256，再提交：

```http
POST /api/v1/input-preparations
Content-Type: application/json
Idempotency-Key: my-first-directory-preparation
```

```json
{
  "kind": "server_directory",
  "directory": "my-package",
  "format_mode": "legacy_archive",
  "expected_manifest_sha256": "替换为包装清单的64位小写SHA-256"
}
```

示例摘要是占位说明，发送前必须替换。`directory` 相对配置的 `receive_root`，也允许 `.` 表示接收根本身。
另支持 `uploaded_package`，传入 tus 返回的 `upload_id` 与包装清单摘要。服务端不接收任意绝对路径。
`format_mode` 必须显式选择 `standard` 或 `legacy_archive`；失败时不会自动改成旧格式。

返回任务 `job_id`、`execution_state`、`execution_epoch`、`attempt_id`、`phase`、`progress`、`result` 和 `issues`，
并在 `Location` 返回任务查询地址。HTTP 202 只表明请求已排队，文件校验由独立 worker 完成。
清单摘要不匹配、成员缺失或文件摘要不匹配会记录为任务失败，不生成正式史料导入记录。

## 3. 轮询与冻结结果

通过 `GET /jobs/{job_id}` 轮询。执行状态为：

`queued → running → completed / failed`；运行中可经 `cancel_requested → cancelled`。
没有开始的排队任务可直接变为 `cancelled`。

任务完成时 `result` 包含：

```json
{
  "prepared_input_id": "实际返回的UUID",
  "input_fingerprint": "实际返回的64位摘要",
  "outcome": "prepared",
  "qualification": "transport_frozen_not_source_accepted"
}
```

再查询 `GET /prepared-inputs/{prepared_input_id}`，取得 `package_path`、`role`、`asset_id`、`sha256`、`byte_length`。
接口不返回服务器接收根、内部对象键或连接配置。
输入指纹由读取器版本、模式、规范文件声明和来源绑定生成；原包装清单的字节摘要与规范输入指纹用途不同。

**准备完成不是标准来源入库成功，也不是内容可用性或人工审定。** 两个模式的准备步骤均只完成传输冻结；
须单独提交导入，候选不会因为准备完成而自动出现。
资产内容通过 `GET/HEAD /assets/{asset_id}/content` 应用转发；支持单段 Range，不向客户端公开内部存储地址。

## 4. 取消与重试

取消请求体：

```json
{"expected_execution_epoch": 1}
```

正在运行时只设置取消请求，worker 在检查点停止，并在最终发布事务再次检查。
取消不会删除已保存资产，不是撤销历史。完成任务收到取消请求时保持完成状态。

重试请求体：

```json
{"expected_execution_epoch": 1, "expected_terminal_attempt_id": null}
```

实际有过执行尝试时，必须填入最近任务响应的 `attempt_id`；只有从未开始便取消的任务使用 `null`。
`expected_execution_epoch` 始终必填，包括没有尝试标识的情况，避免旧请求作用到新轮次。
重试仅适用于 `failed` 或 `cancelled`，沿用原冻结请求参数、增加执行轮次，保留旧尝试记录。
修改参数或包装清单摘要应创建新的输入准备请求，而不是利用重试覆盖原任务。

控制响应含 `operation_id`、`action`、`affected_execution_epoch`、`result_state`、`replayed` 和 `current_job`。
`affected_execution_epoch` 与 `result_state` 记录原控制操作的作用轮次与结果，`current_job` 则是查询时的任务状态。
重放第一轮的取消请求不会再次执行取消，即使当前任务已在第二轮排队；使用新键发送旧轮次请求则返回 409。

worker 使用 PostgreSQL 租约和尝试标识；过期租约可被新 worker 认领，旧 worker 无权发布准备结果或覆盖新尝试状态。
失败重试目前会重新检查原输入并复用已核验资产，不是文件分块续传；取消也不等于将部分数据回滚删除。

## 5. 标准来源绑定

正式读取器消费提取 manifest v4，以及 source-map、content-readiness、artifact-manifest v1。
包装清单须声明上游入口及其引用文件；单个被解析的 JSON 或 Markdown 当前上限 256 MiB，打包与归档读取使用同一上限。该限制针对转换后的记录；整书可用性证据可能远大于原始扫描 PDF，读取仍校验长度与摘要。
正文不能含 BOM、CR 或 NUL，不做字符替换或换行规范化；原字节、来源片段及未知上游字段保留。

原始 PDF 或输入图片需恰好一个 `original_source` 绑定，摘要须与上游相符：

```json
{"kind":"original_source","sha256":"实际64位摘要","package_path":"original/source.pdf"}
```

也可将 `package_path` 替换为已经核验的 `asset_id`，二者不能同时出现。
上游 `source` 绝对路径只是原始记录，不会被用作服务端自动读取地址。
常规相对引用以 `upstream_entry` 所在目录为基准；表格证据中的局部文件名若不满足该规则，
应提供精确上下文绑定，不按同名文件猜测：

```json
{
  "kind":"file_reference",
  "referrer":"ocr/page-001-work/table-structure.json",
  "field_pointer":"/table/coordinate_image",
  "reference":"table-focus.png",
  "package_path":"ocr/page-001-work/table-focus.png"
}
```

读取器沿 `processing.table_review`（含 `tables`）中的结构文件、相关结构审计 `artifact`、
`coordinate_image` 及 `original`/`rectified`/`original_evidence` 的图片引用校验已声明资产。
不把单元格文字或自由说明当文件指令，不要求整个 OCR 目录。
没有完整页面映射依据的表格坐标保持 `page_only`；上游声明帧另存，不冒称已映射到 PDF。
同字节资产可共用，但图片每次收录及表格的物理页、引用文件路径、字段指针分别保存。

## 6. 提交正式导入与修订

```http
POST /api/v1/imports
Content-Type: application/json
Idempotency-Key: my-source-import-1
```

```json
{
  "prepared_input_id":"实际准备结果UUID",
  "input_fingerprint":"实际准备结果摘要",
  "target":{"kind":"new_carrier","display_name":"暂定材料名称"},
  "intent":"ingest"
}
```

重新提取已有载体时，改为 `target:{"kind":"existing_carrier","existing_carrier_id":"实际UUID"}`
和 `intent:"reextract"`；必须绑定同一原始文件资产。相同字节新建另一载体不会自动合并其历史身份。
输入指纹不匹配返回 409。任务与导入登记在同一事务创建，响应含 `import_id`、完整 `job`，
`Location` 指向导入回执；202 不代表导入已成功。重复请求始终返回原任务，不另创建导入。

轮询 `GET /imports/{id}`，终态读取 `outcome` 和 `receipt`：

- `standard`：来源合同通过，回执含提取、正文修订、证据修订和快照标识，以及页/图/表格证据数量。
- `archived_pending`：旧结果已归档待补齐，保存缺项信息，不伪造正文来源、提取记录或正式载体。
- `rejected`：标准来源不匹配或执行失败，保存问题与执行轮次，已准备文件仍可查询；不自动降级成旧格式。

正文相同则复用正文修订；JSON 纯排版变化可复用证据，未知证据字段变化保守视为新修订。
证据等价规则 `resolved-files-review-metrics-1` 将已解析的文件引用归一为内容摘要，支持显式重定位。
仅对页审阅 JSON 的 `vision_review` 或 `processing` 下、含模型及状态/提示版本/API 模式的审阅回执，
忽略 `elapsed_seconds`、`usage`、`cached_usage`、`cache_hit`；单元格内容不在该白名单中。
其余未知字段继续影响证据修订；原始文件与度量仍完整归档，不因语义等价而改写字节。
新载体建立首个技术来源快照；已有载体的新证据先标为 `pending_adoption`，当前快照不自动切换。
通过草案的 `set_reading` 及组织操作显式采用新结果或恢复旧的固定来源。原始 `needs-evidence` 等状态不因成功保存而提升。
`research_input_published` 当前仅标识首个来源快照是否发布，**不能作为允许研究使用的判断条件**。
基础片段/快照返回 `current_usage_evaluated:false`；用途核查另走 `usage-checks` 或 `research-segments`，不能跳过。

可选 `repairs_import_id`/`completes_archive_id` 记录新操作对应的失败导入/旧归档；
目前验证目标分别为 `rejected`/`archived_pending`，不等于全部补证完成或历史身份自动归并。
修改冻结输入后应重新准备并创建新导入，不能用重试覆盖旧来源。
快照、首个变更事件和自动规则子任务在同一受租约保护的事务登记；事件通过 `/changes` 拉取。

## 7. 固定来源回读

页、资产和片段列表支持 `limit`（默认 50，上限 200）及服务器返回的 `cursor`，
响应含 `items`、`has_more`、`next_cursor`、`order`。游标只在同一固定列表继续使用。
`physical_page` 是输入物理页序号，不是推断的书内印刷页码。

先取固定快照，再从其片段获得 `source_span_ref` 和范围。只读截取示例：

```json
{
  "snapshot_id":"实际UUID",
  "source_span_ref":"实际UUID",
  "start":42,
  "end":60,
  "expected_text_sha256":"该截取范围的实际摘要；不校验时省略本字段"
}
```

该请求发送至 `POST /source-excerpts`，不带 `Idempotency-Key`。
`start`/`end` 是最终 Markdown 的 Unicode 码点半开区间 `[start,end)`，不是 UTF-8 字节或浏览器 UTF-16 索引。
前端遇到补充平面汉字须按码点换算；例如 `甲𠀀乙` 的 `[0,2)` 为 `甲𠀀`。
必须属于同一固定快照中的明确来源片段；不能跨片段、模糊搜索或自动切换到当前版本。
响应保留重叠的原始 readiness 块（块范围本身不被改写）、页图/审核资产和原始来源记录。
生成标题等未被来源映射覆盖的部分仍保存在完整 Markdown，不伪造为来源片段。

## 8. 导入与载体管理浏览

`GET /imports` 支持 `carrier_id`、`outcome`、`execution_state`、`cursor` 和 `limit`。
列表返回完整导入回执及其当前任务状态；202 接收过的请求后来失败时会如实显示失败，不把“曾接收”写成成功。

`GET /carriers` 支持 `query`（显示名称包含匹配）、`source_sha256`、`pending_only`、`cursor` 和 `limit`。
结果含原件资产、来源声明、历次提取数、待采用提取数及当前固定快照；名称只用于浏览，不参与身份自动归并。
`GET /carriers/{id}` 另返回该载体历次证据修订中的物理页数，`GET /carriers/{id}/extractions` 返回每次提取的正文/证据修订、页数和采用状态。

上述列表按 UUID 升序稳定分页；`next_cursor` 只能继续同一筛选条件的列表，不能当作跨查询水位。

## 9. tus 与存储恢复

`OPTIONS /uploads` 公布 tus 1.0.0、creation/checksum，支持 SHA-1/SHA-256 块校验。
`POST /uploads` 使用 Upload-Length、Upload-Metadata（container、sha256 的 Base64 值）及幂等键创建会话。
`HEAD /uploads/{id}` 返回已确认前缀；PATCH 必须使用该 Upload-Offset、
`Content-Type: application/offset+octet-stream` 和 `Tus-Resumable: 1.0.0`。
错位偏移为 409，块超限为 413，校验失败为 460；未确认的新前缀不得计入客户端进度。
独立互操作客户端固定为 `tus-js-client 4.3.1`，脚本见 `evaluation/tools/tus-upload.cjs`。

`GET /uploads` 及 `/uploads/{id}` 查询管理状态。`GET /assets/{id}/locations` 与 `/storage-writes`
读取位置及写入回执；`POST /asset-locations/{id}/reverify` 提交完整 SHA-256 重核验任务，要求幂等键。
缺失位置保留为 unavailable，新位置修复不改变资产身份；未完成 multipart 使用原 UploadId/list-parts 恢复。
写入成功但数据库回执失败时先完整回读现存对象，不重复覆盖。

媒体使用稳定 ETag；单 Range 返回 206/Content-Range，无法满足返回 416；多 Range 忽略并返回完整 200。
If-None-Match 匹配返回 304，If-Range 不匹配回退 200。HEAD 无正文；S3 Range 下传存储，
本地文件 seek 后限量读取，不将整个大文件载入内存。完整上传、存储 verified、来源合同通过和用途 allowed 不可混同。

## 10. 候选、清单与在线修订

标准导入回执的 `partition_job_id` 指向自动规则任务。同证据修订/规则版本复用已有任务；
相同固定输入显式重跑也复用原草案，不覆盖管理者修改。`POST /partition-runs` 接收 extraction_id。
规则只建立未批准候选；参考文献、摘要、导航/章标题、脚注及不充分的新布局边界保留为待定范围。
原始上游角色和来源记录不因待定而丢失，入库成功不自动采用候选。

| 方法与路径 | 内容 |
| --- | --- |
| `GET /drafts?kind=catalogue&pending=true` | 分页；pending 表示尚未完整采用，含待定/拒绝操作 |
| `POST /drafts`、`GET /drafts/{id}` | 提交/读取当前草案 |
| `POST /drafts/{id}/revisions` | expected_current_revision_id + 完整 body，追加修订；旧基准返回 409 |
| `GET /drafts/{id}/revisions`、`GET /drafts/{id}/revisions/{revision_id}` | 分页历史及固定历史正文 |
| `GET /drafts/{id}/manifest?revision_id=UUID` | 可编辑清单；可省略版本选择当前修订 |
| `POST /draft-manifests` | 校验原草案/修订摘要，建立新草案，不继承旧批准 |
| `POST /drafts/{id}/previews`、`GET /previews/{id}` | draft_revision_id 固定预览；服务器计算依赖组及问题 |
| `POST /drafts/{id}/applications`、`GET /applications/{id}` | 提交完整选定组及理由，读取逐组实际结果 |

操作的 disposition 可为 propose/hold/reject；后两者须有 disposition_reason，所属依赖组不会采用。
重新开启须追加新修订改为 propose，并生成新预览。修改草案不改已发布组织；撤回已采用结果须提交反向操作。
采用请求字段为 draft_revision_id、preview_id、preview_fingerprint、selected_group_ids、reason。
每组原子发布，独立组允许 partial；不能只看任务 completed 就宣布全部采用成功。
修订重放返回原修订回执，不将草案头倒退到旧版本。

## 11. DeepSeek 辅助

`POST /partition-assistance-runs` 仅由明确请求触发，包含固定 draft_id、draft_revision_id、
question 和 sources（固定摘录引用结构）。只发送列出的文本范围，不冒称查看过未提供的图片。
结果为带来源引用的角色/边界/归属/关系建议，始终是 `model_suggestions_not_approved`，不自动改草案。
使用显式 `DEEPSEEK_*` 或 `INGEST_DEEPSEEK_*` 配置，支持 Responses/Chat；生产路由仍为 DeepSeek。

默认每次最多 20,000 码点/1,200 输出 token，每包累计 20 次调用/200,000 输入码点；
调用与失败重试均在请求前持久预留预算，一次请求最多 2 次尝试，缓存命中不耗预算。
缓存绑定固定引用、问题、提示版本和模型配置；换模型不借用旧结果，排队后配置变化须重新提交。
未配置、预算耗尽或模型失败只影响辅助任务；规则候选和人工处理仍可继续。
`GET /capabilities` 公布当前模型开关及预算，不返回密钥。

## 12. 文献与组织

`GET /documents` 支持 query（显示名及文献/版本/实例书目说法包含匹配）、state、carrier_id、limit、cursor。
匹配到书目说法不代表已采用，须读取断言状态与当前选择。
`GET /documents/{id}`、`GET /editions?document_id=UUID`、`GET /editions/{id}`、
`GET /occurrences?edition_id=UUID`、`GET /occurrences/{id}` 区分文献身份、历史版本和具体收录位置。
多说法及采用理由位于 `/subjects/{id}/bibliography`，旧修订位于 `/bibliographic-revisions/{id}`。

`GET /organizations?query=名称` 筛选列表，`GET /organizations/{id}?revision_id=UUID` 读取固定目录/附属归属。
节点区分 navigation/independent_item，独立篇目须有来源；题名、同字节、上游 article_id 不作身份自动合并键。
草案支持文献创建/修订、版本关系、书目断言/采用、节点创建/移动/重排/删除、附属归属及阅读组合。
身份 merged_into/split_into 使用 relate_documents；retract_document_relation 追加撤回。
`GET /documents/{id}/identity-relations` 返回活动关系、撤回和可能有歧义的后继，旧身份和快照仍保留。

set_reading 使用固定范围，可表达 primary/continuation、替代扫描、跨版本补配及显式 gap。
`GET /composition-revisions/{id}` 与 `/gaps/{id}` 保留原缺口及理由，不重写原 Markdown。
操作结构与必填依据以[草案 Schema](contracts/draft-manifest.schema.json)为准。

## 13. 用途与下游增量

`POST /usage-checks` 是只读请求：purpose 为 source_reading/index_input/card_input，requested_fields 指定字段，
references 为固定来源引用。返回 allowed/limited/blocked/unknown、readiness、依赖/附件约束、资产可用性、
当前限制、policy_version、checked_at 和 observed_change_cursor；未知字段不默认为可用。
`GET /snapshots/{id}/research-segments` 在同一策略下明确区分 included/excluded，不悄悄丢掉受限范围。
`GET /usage-restrictions` 及详情读取当前和历史限制；新增/撤回经由草案，保留原因与旧记录。

`POST /research-baselines` 以 scope.kind=all_current/targets/organization 冻结成员和水位。
等待返回的 job_id 完成后，读取 `/research-baselines/{id}` 和分页 `/items`；不要用动态列表代替全量研究扫描。
从 baseline_cursor 开始 `GET /changes?after=游标`；按已提交顺序交付同一原子组的完整变更。
处理完整事件后才保存 next_cursor，`GET /changes/{id}` 可重读。采用结果、快照与事件同事务发布。
固定基准不是永久用途许可，检索展示或制卡前仍须核查当前用途。

`GET /jobs` 支持 kind、execution_state、carrier_id 及稳定游标。管理列表默认 50、上限 200，
不承诺精确总数；载体过滤关联其导入和自动规则任务。完整可运行示例见[调用与恢复](usage.md)。

## 14. 按证据范围记录机器复核

检索实施阶段经用户授权增加 `0015_source_qualifications`。先用 `POST /source-qualification-context`
传入固定 `snapshot_id`、`source_span_ref`、码点 `[start,end)` 和 `expected_text_sha256`。
响应包含正文／证据修订、原始 readiness、实际原件及页图哈希、`context_sha256` 和 `reviewable`；
取得上下文本身不构成用途许可。生成内容、未知或不完整的来源范围不能由此转为可用正文。

完成原件优先的机器复核后，通过既有草案预览／采用流程提交 `qualify_source`：

- `source` 与上下文请求保持同一固定范围，并绑定返回的 `context_sha256`。
- `purposes` 明确选择 `source_reading`、`index_input` 或 `card_input`；`verified_fields` 列出已核实字段，不能使用通配符。
- `review` 保存审核者、原件及页图实际哈希、观察、证据记录和置信度；`original_first=true`、`verdict=verified`，不得残留未解决项。
- `human_review`、`sealed_gold`、`historical_truth_approved` 均为 false，`confirmation_kind=machine_review`。

机器记录只使匹配证据修订、范围、用途和字段得到有限资格，原始 readiness 与原文保持不变。
活动限制、依赖注释、实际资产可用性仍参与每次用途判断；局部正文合格不表示整页或原件已获准。
后续 `usage-checks` 返回适用的 qualification ID，并保留 `limited` 与机器复核性质。
撤销通过草案操作 `revoke_source_qualification` 指定 qualification ID、证据和理由，保留原记录及撤销事件。
资格记录和撤销均进入完整的变更事件，供下游重新核查。

公开字段以导出的 OpenAPI／草案 Schema 为准。此入口不调用或替换 DeepSeek 生产审核模型，也不修改提取模块。
