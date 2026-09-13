# 入库、分块嵌入与 Agent 制卡可视化

本文件记录前一增量。当前四入口与任务关联方式以 [工作台重构说明](WORKBENCH-REDESIGN.md) 为准；下述旧模块标签页已移除，进度口径和后端合同继续复用。

更新：2026-09-11。沿用现有 React / 原生 CSS 工作台，无新增前端依赖、数据库表或生产模型。

## 入口与操作

- **处理任务 → 处理流水线**：切换入库与篇目划分、分块与嵌入、Agent 制卡。
- **研究卡片**：同样提供制卡任务入口。返回列表保留最近模块选择。
- 已加载 / 执行队列 / 需要关注筛选；归属待定的已完成篇目划分也归入需要关注。
- 列表与详情每 3 秒读取一次；隐藏标签页暂停。列表加载历史分页后暂停自动刷新，刷新首页恢复。计数只针对已加载记录，不表示数据库全量。
- 连接失败保留最近一次数据，并明确显示状态可能过期。详情保留既有带版本核验的暂停、继续、取消、重试确认，不新增自动重跑。

## 进度口径

| 模块 | 可视化依据 | 不代表什么 |
| --- | --- | --- |
| 入库 | `progress.completed/total/unit`；交付 `result`；已完成划分的候选数与归属待定范围 | 入库不等于篇目已确认；辅助请求的重试上限不是文章处理百分比 |
| 检索分块 | 已提交 `write_offset/expected_text_records` | 分块写入完成不等于向量就绪 |
| 嵌入 | 已写入且逐批校验的 `published_vectors/expected_vectors`；旧接口兼容 `verified_vectors/expected_vectors` 与发布回执字段 | 100% 不等于已完成最终索引发布；命中缓存也计入已校验结果，不是模型调用百分比 |
| 制卡 | 已规划阅读批次、委派目标、当前阶段、各文献阅读产物、交付与接收回执 | 阅读完成不等于制卡完成；宿主分批执行不等于所有子 agent 并行 |

没有总量不显示百分比；不估算耗时。已发布索引仍以 `build_complete`、`ready_for_auto_card` 回执说明后续条件，前端不自行启动制卡。

## 后端增量

### 检索

`GET /api/v1/jobs` 和 `GET /api/v1/jobs/{id}` 的 `progress` 增补既有持久化 payload 计数：

- `expected_text_records`、`written_text_records`
- `expected_vectors`、`verified_vectors`

由 `document_retrieval.progress.build_progress` 只读映射，不加载向量、不请求模型、不改任务状态。分块构建函数执行中尚无逐单元计数，只显示阶段；构建结束才知道精确分块数。

### 制卡

`GET /api/v1/tasks` 和 `GET /api/v1/tasks/{id}` 增加 `agent_progress`，版本 `agent-progress-v1`：

- `manager`：冻结配置的主模型、任务阶段/状态、规划与接收回执编号。
- `children`：文献范围、阅读目标/完成要求、冻结配置的阅读模型、批次计数、原请求/交付/接收产物编号。
- `returned` / `received`：子任务已交付与主 agent 已接收分别计数。
- `reading_batches`：当前已规划批次与已有阅读记录。
- `contexts`：执行位置、已保存轮次、是否仍有调用输入、结果编号。**不输出对话 history、输入正文、密钥或完整 checkpoint。**
- `supplements`：已有补充研究委派交付摘要；`candidate_artifact_id`：已保存候选编号。

新委派检查点保存来源单元集合；读取进度时按当前 `read_batches` 计算，支持输入过长后动态拆批及阅读复用。旧记录缺少分母时保留未知，不补造数据。`active_reading_delegation_id` 仅辅助定位宿主当前阅读任务；任务暂停/失败时不继续显示运行中。

产物列表复用 `/tasks/{id}/artifacts` 分页，内容复用 `/task-artifacts/{id}/content`；默认折叠原始任务与回执。JSON 内容支持 2 MiB 内只读预览，不执行 HTML/脚本。产物分页浏览期间停止自动刷新，避免翻页后跳回首页。

## 验证与运行限制

- 前端 Vitest 与 TypeScript/Vite 构建；新增身份字段、进度分母、重试预算、未知总量、交付/接收区分及服务端渲染测试。
- 制卡隔离 PostgreSQL 测试、真实 SDK 协议的本地模拟模型测试；检索计数与 HTTP 合同测试。未调用真实 DeepSeek、OCR 或 GPT API。
- 独立 Playwright 浏览器：入库详情、分块/嵌入、主子 agent、JSON 产物预览、1100/1440 桌面布局、503 过期提示。夹具和截图在 `output/playwright/module-monitor-20260910`；开发视觉评价单独存档，不是生产/人工验收。
- **本次未重启生产后端或 worker，也未开启 auto_sync、重跑旧任务。** 前端开发服务器可热更新；使用新摘要前需重启检索 API、制卡 API，新委派计数记录还需制卡 worker 加载新代码。已有正在处理的真实任务请先按正常运维流程结束或暂停。
- 真实材料的完整流水线验收、全量历史任务统计、推送式进度、逐 token 输出、统一材料中心不在本次交付范围。
