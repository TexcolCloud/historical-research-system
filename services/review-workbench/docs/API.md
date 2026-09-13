# 审核工作台：后端待接接口 v0.1.0

> 本契约仅覆盖正文审核演示，不是全链路合同。最新 [工作台设计](../../../docs/full-chain-workbench-design.md) 与 [接口缺口清单](../../../docs/full-chain-workbench-interfaces.md) 要求独立的高风险人工放行及新卡片强制人工采用规则；本文的 resolved、问题 fixed、保存并采用均不能直接解释为业务放行。本轮没有修改本契约对应的代码或生成 Schema。

状态：**本文正文审核聚合接口已预留，后端尚未实现/联调**。2026-09-10 后续已开展非转换模块的现有 API 适配，见 [模块联调说明](MODULE-INTEGRATION.md)；这不代表本文 G04 正文修订、重定位与放行已接通。真实来源阅读目前只读，研究卡片使用其独立的正式修订 API。

本协议是浏览器与审核后端之间的聚合契约，不强制数据库设计、现有模块归属或转换模块 API。后端可新增聚合入口，也可由 `src/api/http.ts` 映射到多个实际接口；页面仅依赖 `ReviewApi`。不要把现有入库模块修改标题/状态的 `revise_document` 当作 Markdown 正文修订。

## 1. 共通约定

- 建议基址 `/api/review/v1`，JSON UTF-8；Markdown 字符串不得截断，HTML 表格须按正文内容保存。
- 日期为 ISO 8601 UTC 字符串；正文 `sha256` 为 UTF-8 原文实际 SHA-256（小写十六进制），不包含浏览器渲染后的 DOM。
- `documentId` 为不透明业务 ID，不是任意文件系统路径；path 字段 URL 编码后从请求 body 移除。GET 其余字段放查询参数。
- 成功 envelope：`{"success":true,"data":{...}}`；失败：`{"success":false,"error":{"code":"CONFLICT","message":"草稿已改变，请重新载入。","recoverable":true,"requestId":"optional"}}`。
- HTTP 客户端对所有输入与响应做 Zod 校验，15 秒超时。失败保持当前编辑内容，不丢弃、不自动选择 Mock。网络超时不代表服务端未提交。
- 列表首版为完整 `items` 集合，未定义分页；大集合接入时先扩展契约与页面，再上线。角色/认证/CSRF/权限策略由正式接入阶段补齐，不能以演示模式默认值替代生产鉴权。

精确字段、枚举、必填项见 [contracts.json](contracts.json)。由 `npm run contracts` 从 `src/api/spec.ts` 生成，不手改生成文件。它是操作清单和 JSON Schema，不冒充 OpenAPI 文档。

## 2. 操作一览

| 方法与路由（相对基址） | 用途 | 关键输入 | 返回 `data` |
| --- | --- | --- | --- |
| GET `/capabilities` | 判定功能是否可用 | 无 | contractVersion、mode、sourceEditing、adoption、draftPersistence、limitations |
| GET `/documents` | 审核队列 | query，status=all/pending/drafts/resolved | items: DocumentSummary[] |
| GET `/documents/{documentId}` | 固定正文与可恢复草稿 | documentId | document、revision、draft/null、issues、evidence、updates |
| PUT `/documents/{documentId}/draft` | 独立保存工作草稿 | baseRevisionId、expectedDraftVersion/null、markdown、decisions | Draft |
| POST `/documents/{documentId}/revision-previews` | 固化本次候选内容与检查 | baseRevisionId、draftVersion | Preview |
| POST `/documents/{documentId}/revision-decisions` | 保存候选或保存并采用 | previewId、expectedCurrentRevisionId、action、note、operationId | Commit |
| GET `/documents/{documentId}/revisions` | 历史回读 | documentId | items: Revision[]，建议最新在前 |

### Capabilities 示例

```json
{"success":true,"data":{"contractVersion":"0.1.0","mode":"http","sourceEditing":true,"adoption":true,"draftPersistence":"server","limitations":[]}}
```

真实后端必须返回 `mode: "http"`，不可返回 mock；前端检查配置与返回值一致。暂不支持编辑或采用就返回 false，不能伪造成功。当前页面仅编辑 `kind: "source_text"`。

### 打开文献

`document.currentRevisionId` 必须与 `revision.id` 一致，revision 是当前采用的固定正文。`draft` 可空；非空时其 `baseRevisionId` 为编辑起点，`version` 为服务端草稿版本。过期起点须明确冲突，不能默默套用在新正文上。`document.hasDraft` 是队列提示，不是修订存在标志。

`issues` 给出 ID、类型 text/table/source、说明、`anchorText`、证据 ID 和结论。`anchorText` 是旧版说明字段，不再据此猜测高亮。高亮使用下述明确定位字段。`resolution` 初始 open，人工选择 fixed/unclear/later；审核说明单独留存。不要将人工结论写作 GPT/DeepSeek machine approval。

`evidence` 为只读原件：image/pdf，URL、sha256（有则提供）、物理页码、印刷页码、绑定级别 page/region/unbound。`kind: demo` 和 `binding: demo` 仅属于演示数据。缺失原件时给空数组或 unbound，不提供推测的替代原件。已支持在图片上叠加矩形；PDF 本身仍以外部只读方式打开，需提供页图才能叠加高亮。

### 待核区域高亮（向后兼容字段扩展）

每个 Issue 可增加以下字段，旧响应缺失时按 `bodyAnchor: null`、`sourceRegions: []` 处理，不猜位置：

```json
{
  "bodyAnchor": {"revisionId":"rev-001","quote":"需要核对的原始 Markdown 片段"},
  "evidenceId":"page-image-012",
  "sourceRegions":[{"x":0.70,"y":0.20,"width":0.20,"height":0.30}]
}
```

- `bodyAnchor.quote` 是指定正文修订中的精确 Markdown 子串。当前仅在修订 ID 相同、且子串在单个内容块内全局唯一出现时框出该段落/表格；不是逐字错误判定。表格可用完整 `<table>...</table>` 作为 quote。
- 编辑导致 quote 消失、重复，或采用版本发生变化时，正文高亮停止并提示重新核对；后端重算定位后再提供新锚点。不会以旧偏移猜测新位置。
- `sourceRegions` 相对于 `evidenceId` 所指**实际展示图片**归一化，左上角为 (0,0)，x/y/width/height 均在 0～1，宽高大于 0，x+width 与 y+height 不大于 1。越界导致契约校验失败，不自动裁剪掩盖错误。
- 图片可为整页或片段裁剪，但坐标必须属于该资源最终旋转/裁剪后的像素平面，**不是** PDF point、Markdown DOM 或包含 UI 边框的截图坐标。例如像素框 `(left,top,w,h)` 转换为 `(left/W,top/H,w/W,h/H)`，W/H 是实际展示图片宽高。
- 后端同时提供物理页码、固定图片 URL 与原件 sha256，保证问题坐标绑定同一不可变图像。更换裁剪范围/方向或原件时必须重算坐标，不复用旧框。前端当前展示所选问题的绑定图片，不实现跨页坐标转换。
- 多个矩形可指向同一图像的多个位置；图像加载完成才画框，读取失败不画。仅返回 PDF URL 时提示需补页图。
- 标记 fixed 后退出待核高亮；unclear/later 仍保留。高亮开关与上一项/下一项只改变界面，不改 Markdown、问题结论或原始图像。高亮不代表问题已确认或审核通过。

演示资源为人工绘制 SVG，不冒充 PDF 截图；正式接入复用转换模块保存的页图与区域元数据。高亮位于编辑内容之外，不会出现在保存/下载的 Markdown 中。

### 保存草稿示例

```http
PUT /api/review/v1/documents/doc-123/draft
Content-Type: application/json

{"baseRevisionId":"rev-001","expectedDraftVersion":null,"markdown":"# 正文\n\n<table><tbody><tr><td>資料𠀀</td></tr></tbody></table>\n","decisions":[{"issueId":"issue-1","resolution":"unclear","note":"原件缺页，保留待查"}]}
```

首存 `expectedDraftVersion: null`，已有草稿则传最近回执的版本。服务端应在事务中校验当前采用 ID 与草稿版本，成功版本递增；不更新当前采用正文、检索输入或史料卡。前端串行保存、700ms 防抖，保存期间新输入会继续排队；关闭页面前仍有未确认内容时提示下载/保存。

### 预览与修订

预览读取已确认的草稿版本并冻结 Markdown/决策，返回 `previewId`、正文哈希、是否改变、未决项计数、检查明细与 `canAdopt`。服务端不能因客户端传入 fixed 就跳过应有证据校验。生产审查只使用配置的 DeepSeek 路由，不能依赖 Codex/GPT 开发评审。

```http
POST /api/review/v1/documents/doc-123/revision-decisions
Content-Type: application/json
Idempotency-Key: op-123

{"previewId":"preview-123","expectedCurrentRevisionId":"rev-001","action":"save_and_adopt","note":"修正表格缺列；缺页项保留待查","operationId":"op-123"}
```

- `save_only`：创建可回读候选修订，不改变 currentRevisionId，不触发采用后的下游更新。
- `save_and_adopt`：创建新修订并显式切换采用指针，保留旧正文、旧引用与证据；审核问题结论随本次采用更新。未决项可保留，是否允许采用由后端 preview 检查决定。
- 提交需在事务内校验预览所属文献、草稿版本、当前采用 ID、预览哈希/内容及权限。不接受跨文献或过期预览。
- 草稿更新后使旧预览失效；即使清除草稿后新草稿的版本号重新从 1 开始，也不得复用旧预览（可用草稿代次 ID 或持久递增版本避免 ABA）。
- 同一 operationId + 相同请求必须返回同一回执；相同 ID 不同请求返回 CONFLICT。header 与 body 必须一致。先检查幂等回执，再检查已变化的 currentRevisionId，支持“服务端提交成功但响应丢失”重试。
- 确认提交成功后清除对应草稿；不可清除并发新增的草稿。回执至少包含 operationId/documentId/revision/adopted/currentRevisionId/updates。
- 修订正文与哈希不可变；adopted/historical/candidate 是当前状态投影，可随采用指针变化。历史恢复是以旧内容建立新草稿，再走预览保存，不覆盖历史。

## 3. 下游状态与错误

`updates.retrieval`：not_connected/disabled/queued/running/ready/failed。
`updates.cards`：not_connected/disabled/queued/running/suggestions/unchanged/failed。
`message` 为具体状态说明。`not_connected` 不等于成功，`queued` 不等于完成。前端当前只显示打开/保存回执中的状态，没有后台轮询；真实任务监控在接入时补充。旧快照仍须可引用，不能在采用正文时就地替换旧引用。

| error.code | 建议 HTTP | 前端含义 |
| --- | --- | --- |
| INVALID_REQUEST | 400 | 输入不符合契约 |
| NOT_FOUND | 404 | 文献/预览等不存在 |
| CONFLICT | 409 | 起点、草稿、预览或幂等请求冲突；备份后重新载入 |
| NOT_ADOPTABLE | 422 | 检查阻止采用，可继续修改或保存候选 |
| UNAVAILABLE | 503 | 服务/网络不可用；提交结果可能不确定 |
| STORAGE_FAILED | 500 | 存储失败，不能显示已保存 |
| INVALID_RESPONSE | 502 | 客户端发现响应缺字段/非 JSON/版本不兼容 |

请求失败后保留本地编辑内容；网络不确定的提交重用原 operationId，不能创建新操作号反复点击。草稿保存冲突暂用“下载备份 → 重新载入 → 手动合并”，不自动覆盖。

## 4. 后端完成后逐项补齐

- [ ] 文献队列、固定正文 Markdown、草稿查询与独立保存。
- [ ] 正文修订存储、原始 UTF-8 哈希、版本冲突与幂等提交。
- [ ] 原始文件/页图访问 URL、正确证据归属、缺页状态。
- [ ] 实际预览检查与采用资格，生产 DeepSeek 审查路由。
- [ ] 采用后检索同步、制卡影响检查，以及可回读任务状态。
- [ ] 人工操作者身份/审计记录，不混用机器审批或 sealed gold 标签。
- [ ] HTTP 同源部署或 CORS、身份认证、权限、上传/下载策略。
- [ ] 用真实转换产物联调 HTML 表格、罕见字、图片路径与大文献；发现不支持结构先保留源码，不强制转换。
- [ ] 人工验收整条真实保存链路；演示工作流通过不能替代生产验收。

用户随后可根据转换模块实际输出调整这些缺口；无需现在重构转换模块。史料卡结构化编辑继续使用独立卡片修订契约，不应把卡片 Markdown 导出当作数据主本。
