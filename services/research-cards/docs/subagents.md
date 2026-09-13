# Pro 主 agent、Flash 子 agent 与研究上下文

`0.3.0` 默认使用 `agent_mode = "pro_manager"`：DeepSeek V4 Pro 制定文献级阅读计划、接收结果、选择补查问题并综合卡片；DeepSeek V4 Flash 执行阅读和有界 RAG 补查。实现保留 Agents SDK、ModelHost、共享任务预算、PostgreSQL 检查点与不可变产物。

## 调用顺序

1. 宿主先取齐固定来源范围的文字和结构信息，建立文献、章节目录及来源单元清单。
2. Pro 调用 `delegate_reading`，为每个文献范围指定目标、关注点和完成条件。工具保存 `reading_plan` 和委派请求；重复的并行派单只登记一次。
3. 宿主按文献、章节和批次大小推进 Flash。一个文献任务可以有多个内部批次；普通批次不重新调用 Pro。每批提供当前原文、相关脚注与邻段、全篇进度和可容纳的前批阅读记录，保留完整逐段覆盖与连续原文引用校验。
4. 所有文献阅读完成后，宿主保存完整结果并向 Pro 返回目录。Pro 可打开产物或回读原文，随后返回全部实际委派编号和综合关注点。
5. Pro 对阅读中的问题作补查决策；Flash 使用已有 `search_evidence` / `read_evidence` 工具完成具体问题，结果交由 Pro 综合。

例如一篇文献有两个普通阅读批次，阅读阶段通常是 **Pro → Flash → Flash → Pro**。证据回读、格式修正和额外研究会增加调用，因此这个顺序不是成本承诺。

每次工作派发最多运行一轮模型请求。当前一个任务内的文献及批次按顺序推进，服务的两个执行单元仍受原有上限约束；本版未增加递归委派或文献子任务并行调度。

## 显式数据与证据回读

`DelegationContext` 的版本为 `delegation-context-v2`，包含 `artifact_ids`、`source_unit_ids` 和本次实际资料 `data`。`ReadingAssignment` 保存 `scope_id`、`objective`、`focus`、`completion_criteria`。请求产物另保存父子模型、预算归属和上下文哈希。

文献结果保存覆盖范围、完整阅读产物编号、研究问题和篇界观察；Pro 收取的是目录和可回读引用。模型对话及 Flash 的完整执行历史留在执行产物中。

主 agent 的计划、补查决策和综合阶段，以及 Flash 的阅读和补查阶段，均可使用：

| 工具 | 用途 |
| --- | --- |
| `read_source_units` | 读取原文、明确关联的脚注和相邻上下文；长文本通过 `next_read` 连续读取 |
| `read_research_artifact` | 按产物编号打开完整阅读记录、研究目录、已保存结果和本 agent 笔记；长 JSON 按返回偏移继续读取 |
| `save_research_note` | 保存带来源编号的发现、问题、冲突或下一步；状态始终是机器研究笔记 |

正文与注释关联取自上游 `readiness.dependencies`，按固定快照内的 block ID 双向寻找。相邻段落只作阅读背景，不据此推定注释归属。关联原文超出本批输入额度时，输入明确列出 `context_deferred_unit_ids`，供 Flash 回读。

综合阶段只预载有界原文和阅读目录。最终引用除了需要与来源中的连续文字吻合，还必须落在该综合 agent 已收到的原文范围内；未进入当前输入的引用需通过工具实际读取。阅读覆盖与最终引用回读分别记录，不能用目录摘要代替原文。

## 上下文分层与恢复

| 层次 | 本版保存方式 |
| --- | --- |
| 原始证据与完整记录 | 固定上游快照、带来源锚点和哈希的文字、读取页、逐段阅读与检索结果产物 |
| 研究与执行状态 | 文献任务、已读范围、问题、结果目录、研究笔记、调用账本及任务检查点 |
| 模型工作上下文 | 按阶段选择的原文、目录、必要前批记录和当前 native 工具交互；每轮保存 `context_packet` |
| 跨任务记忆 | 本版未自动启用；任务笔记不会变成跨任务共享史实或用户批准记录 |

`ResearchContext` 显式组装各阶段输入。阅读在已完成批次之间从源记录和进度重新组装；同一 agent 尚在进行的工具对话保留 SDK 原生 history，下一次派发继续执行。

每轮模型调用前按输入估算及预留空间判断是否需要重建上下文。达到阈值时，先将完整对话、工具记录和研究笔记存为 `context_archive`，再用当前任务、来源目录、已完成工具引用、笔记定位和原归档编号续接。完整笔记可继续打开，未完成的工具调用／结果链不允许被截断重建。

DeepSeek Responses 的服务端会话与自动压缩功能未被用作恢复依赖。应用保存每轮输入、原生响应及检查点；已保存的模型结果和工具结果在恢复时复用。工具调用由宿主锁控制，避免重复并行派单；上下文工具和补查工具共享各自执行轮的锁。

默认每个局部 agent 上下文最多 8 轮、24 次上下文工具调用；每轮最多执行 4 次新的上下文工具调用。工具返回文本采用有界片段。它们同时受原任务的模型调用、输入输出及执行时间预算限制。上下文轮次或任务预算耗尽时保留产物，不能将未完成研究标为成功。

## 产物、配置与运维

通过 `GET /api/v1/tasks/{id}/artifacts` 或 `tasks artifacts TASK_ID` 列出产物；内容仍使用 `GET /api/v1/task-artifacts/{id}/content`。

| 产物类型 | 内容 |
| --- | --- |
| `reading_plan`、`delegation_request:*` | 文献目标、来源范围和持久委派 |
| `reading:*`、`delegation_result:*`、`reading_receipt` | 逐段原记录、文献／补查结果与 Pro 收取回执 |
| `research_state` | 完整阅读目录、来源章节、覆盖和未解项 |
| `context_packet:*`、`context_archive:*` | 当前轮实际输入与重建前的完整对话 |
| `context_tool:*`、`research_note:*`、`agent_result:*` | 工具返回、机器笔记和局部 agent 完成结果 |

修订的 `source_manifest` 保存文献计划、研究状态、当前上下文与归档编号，并保留原有来源、阅读及委派引用。`tasks usage TASK_ID` 可按 `manager:reading_plan:round:*`、`subagent:reading:*:round:*`、`supplement_plan:round:*`、`question:*:round:*` 和 `synthesis:*:round:*` 查看模型调用。

新任务工作流版本为 `cards-workflow-v1.6-context`，提示版本为 `research-cards-prompts-v1.5-context`。无需新增数据库表或依赖。API 和 worker 使用同一配置；通过 `max_context_rounds`、`max_context_tool_calls` 或同名大写 `CARDS_` 环境变量配置局部上限。

`agent_mode = "fixed_pipeline"` 保留无 Pro 阅读委派的运行入口；它不是旧版本代码的冻结副本。旧校准任务的工作流和提示版本仍保留，升级不自动恢复旧任务。复现实验应使用对应版本代码及配置。

生产原图复核与文字阅读统一使用配置中的 `deepseek-flash`，后续逐段和整卡核查、候选及采用规则继续生效。Pro 接收子任务结果不等于史料质量批准。

## 验证范围

工程测试使用真实 Agents SDK 连接本地 Responses/SSE 协议夹具及独立 PostgreSQL schema，覆盖文献级多批阅读、重复并行派单、错误范围修正、累计预算暂停／扩展和模型结果保存后中断恢复。上下文测试覆盖跨页注释关联、取齐固定来源、综合回读非预载原文、原生工具历史恢复，以及归档后研究笔记回读。

夹具中的文本均明确标识为合成工程材料。本轮验证不构成真实史料质量、研究效果或成本收益的验收；这些仍需后续同条件史料对照。
