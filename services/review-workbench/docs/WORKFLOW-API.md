# 全链路前端接入接口 v0.1.0

状态：本文保留前端模拟链路及建议聚合契约，不表示这些路由已由后端提供。2026-09-10 默认入口已改为直接适配各模块现有 API，实际范围和限制以 [模块联调说明](MODULE-INTEGRATION.md) 为准。业务目标见 [全链路设计](../../../docs/full-chain-workbench-design.md)，已有服务与缺口见 [映射清单](../../../docs/full-chain-workbench-interfaces.md)。

## 1. 接入点

- 页面：`src/workflow/WorkflowApp.tsx`。当前以浏览器 Hash 路由覆盖五入口及详情页。
- 类型及运行时校验：`src/workflow/contracts.ts`，Zod 是唯一字段来源。
- 数据访问：`WorkflowApi.snapshot / command / editor`，实现位于 `src/workflow/api.ts`。
- 正文编辑：复用 `Workbench`、`DraftBuffer` 和原有 ReviewApi。新增可注入 ReviewApi，不改变旧演示数据。
- `npm run contracts` 同时生成 [workflow-contracts.json](workflow-contracts.json) 和旧 [contracts.json](contracts.json)。不要手改生成文件。

仅显式 `VITE_REVIEW_MODE=mock` 进入本文模拟页面。以下 HTTP 描述属于保留的拟议聚合适配器，不是默认正式页面；其基址为 `VITE_WORKFLOW_API_BASE`（默认 `/api/workbench/v1`），正文编辑基址为 `VITE_REVIEW_API_BASE`。不得在 VITE 变量中放密钥。

保留的聚合适配器只读状态，command 仍是预留方法。当前默认页面的分服务读写能力见模块联调说明。页面不会自动启动后端服务或在请求失败后回退 Mock。旧独立编辑器入口已移除，可视 Markdown 编辑组件继续复用。

## 2. 拟议接口

| 方法 / 路径（相对聚合基址） | 输入 | 响应 / 说明 |
| --- | --- | --- |
| GET `/snapshot` | 无 | 原始 JSON Snapshot；必须 `mode=http`，无旧 ReviewApi success/data 包装 |
| POST `/commands` | `{operationId, expectedRevision, command}`，Idempotency-Key 同 operationId | 新 Snapshot；失败使用非 2xx，前端保留编辑与已有状态 |
| POST `/uploads`（留口，未实现） | 拟用 multipart 文件及来源元数据 | 实际上传回执、任务关联与可恢复进度；不能发送文件名代替原件 |
| 正文编辑接口 | 见 [API.md](API.md) | 旧 ReviewApi 使用 Result 包装，与 Snapshot 协议不同，适配层负责区分 |

Snapshot 包含 articles、tasks、cards、events 与快照版本。它是当前小规模前端演练使用的完整集合；生产接入应先补分页/查询和真实能力字段，不直接全库拉取。

`expectedRevision` 防止使用旧聚合状态作决定。Mock 操作串行，非 tick 命令保存操作指纹用于重放；重放返回最新快照而不是重复变更。HTTP 超时后不得盲目换新操作 ID 重发；在正式启用写入前需接入回执查询、同键恢复及真实冲突响应。目前 UI 不启用 HTTP 写入。

## 3. 命令与行为

| type | 当前演练内容 | 后续后端接入要求 |
| --- | --- | --- |
| tick | 页面开启时按 1.6 秒推进一个模拟阶段 | 仅 Mock；HTTP 禁止。正式页面只能查询服务任务进度 |
| upload | 登记 1–20 个非空 PDF 的文件名和大小，选择固定演示场景 | 仅 Mock；不读 PDF 字节、不发送网络、不持久化 File，不是实际上传 |
| control | 卡片暂停/继续；任务取消/失败重试 | 实际可用动作由各模块状态决定；不得据此宣称提取器支持暂停 |
| release | 当前正文版本、非空核对说明，未决问题/证据/组织条件检查 | G04/G05 后端正式记录人工范围与版本；不能把机器资格记录改成人审 |
| partition | 单一待定候选标题与物理页范围，经页面预览后应用 | G03 + 入库草案/依赖组；本轮未实现多篇拆分、合并、附件归属及完整书目表单 |
| cardDraft | 保存稳定组 ID 对应的 Markdown 编辑稿 | 映射真实 EditDocument，保留条目/引用/保护。当前是两个独立演示组，不是完整研究卡片 Schema |
| cardPreview | 固定稿件版本，生成预览 ID；空内容、机器阻断、来源变更不可采用 | 调用真实 revision-previews，展示证据核查与依赖组结果 |
| cardDecide | 选择组 adopt / keep，记录人工说明；未选组继续待审 | 调用真实 revision-decisions。必须有有效预览与保护/当前版本校验 |

## 4. 审核语义

- 无风险且核验完整、组织明确的篇目自动模拟制卡；高风险篇目需显式人工放行。原件不清/核验未完成始终等待。
- 正文草稿不会放行；采用正文修订后清除本篇旧放行，用户需按当前版本作决定。
- 原先无风险的正文一旦采用新修订，模拟器也会转入人工待放行，不能继承旧版机器通过；这是保守的演练规则，正式新版核验仍由后端返回。
- 当前 mock 的技术就绪与索引等待没有独立服务状态，完成已模拟步骤后直接排队；不能把它当作 G05 已接通。
- 新卡片从 pending 开始，机器模拟通过也不采用。卡片编辑稿保存/预览不改变 adoptedMarkdown。
- 未选择的组保留 pending；首次没有采用内容的组禁止 keep。已有采用组再次编辑会重新待审，旧采用文本不变。
- 来源改变后旧卡片仍保留生成时 sourceRevision/sourceMarkdown；旧预览禁止直接采用。新结果是新候选，不自动覆盖旧卡片。
- 模拟器只演练关系及操作状态，不评判实际史料质量。它不做真实原图核查、来源偏移重算或卡片事实检查。

## 5. 浏览器存储及恢复

- 全链路元数据：`historical-workflow:mock:v1`，包含状态与命令回执。
- 各篇正文编辑数据：`historical-workflow:mock:v1:editor:{articleId}:historical-review-workbench:mock:v1`。
- 旧独立演示数据仍在 `historical-review-workbench:mock:v1`，可由“旧版编辑器与草稿”进入，不迁移或覆盖。
- 同一浏览器来源内刷新恢复；关闭页面后不再模拟执行，重新打开继续。全链路与正文缓存通过编辑器回读同步当前修订，草稿不会同步成已采用文本。
- 损坏或被拒绝的存储会显示错误，不自动清空、重新播种或虚报保存成功。清除站点数据会丢失模拟历史。
- 本轮面向单标签演练，不提供跨标签原子并发、多用户审核或持久服务存储承诺。

## 6. 仍需保留的后端工作

G01–G08 没有因前端可演练而关闭：真实原件上传/提取任务、跨模块阶段聚合、完整风险读模型、正文与证据修订、人审放行资格、关闭新卡片机器自动采用、候选级待办及动态能力仍待统一接入。

检索页目前是演示正文的本地字面匹配，包含待审材料并明确标记状态；不冒充真实用途授权过滤、向量检索或研究输入选择。多篇组织、补原件、重新核验、真实研究卡片结构、人工保护和历史依赖传播保留在真实接口阶段。

不会修改冻结提取模块、DeepSeek 生产配置、历史机器采用记录，也不增加导出或论文提示页面。
