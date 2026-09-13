# 非转换模块前端联调

日期：2026-09-10。范围：清理旧独立界面，将现有入库、检索、制卡 HTTP 接口接到桌面工作台。用户明确不使用历史数据，待转换模块完成后再做新材料全流程测试。

后续增补：转换模块重构完成后，用户已授权转换前端联调并提供新 PDF。原始 PDF 接收、CLI 任务、块级待审结果与显式入库交接现已接入，见 [转换接入说明](CONVERSION-INTEGRATION.md)。下文“排除转换 / 没有转换路由”是上一轮范围记录，不再代表当前能力；G04/G05 等未完成闭环仍保留。

## 连接方式

前端 `src/live/api.ts` 使用 `/modules/{module}/api/v1`。Vite 仅去掉 `/modules/{module}`，保留模块实际路径、JSON、状态码及版本信息。不是此前拟议的 `/api/workbench/v1/snapshot` 聚合服务。

| module | 默认后端 | 可选服务端配置 |
| --- | --- | --- |
| ingestion | http://127.0.0.1:18125 | WORKBENCH_INGESTION_ORIGIN |
| retrieval | http://127.0.0.1:18130 | WORKBENCH_RETRIEVAL_ORIGIN |
| cards | http://127.0.0.1:18140 | WORKBENCH_CARDS_ORIGIN |

变量放根目录已有 `.env` 或进程环境，不创建模块私有 dotenv，不填入凭据。前端默认为真实模式；`VITE_REVIEW_MODE=mock` 显式选择离线演练，二者不会自动互相切换。

运行期观察：入库与制卡 API 可通过同源代理响应；检索进程未启动，页面显示 502/未连接。检查连接只读，不隐式启动 worker、索引、同步或模型。现存旧记录可能仍在列表中，本轮不使用、迁移或清理它们。

模块启动遵循各 README。入库默认 CLI 端口是 8766，工作台约定为 18125，启动时需显式 `serve --port 18125` 或覆盖代理地址。制卡使用 `config/development.toml`，不要不带配置启动自动同步。检索正式运行前确认自动同步范围及现存状态，不为本轮联调重跑历史任务。本轮没有启动任何制卡/入库后台 worker，也没有建立全链路自动任务调度。

## 已适配实际接口

| 页面 | 模块操作 | UI 与合同约束 |
| --- | --- | --- |
| 任务 | ingestion `/jobs`、`/{id}`、`/retry`、`/cancel` | 提交当前 execution_epoch；重试包含 observed terminal attempt |
| 任务 | retrieval `/jobs`、`/{id}`、`/retry`、`/cancel` | 独立列表与状态；按该模块的 expected_attempt_id 提交 |
| 任务 | cards `/tasks`、`/{id}`、`/controls`、`/result`、`/artifacts`、`/usage` | control_version 与 attempt 原样绑定，不混用入库版本字段 |
| 已有交付包 | ingestion `/input-preparations`、`/prepared-inputs/{id}`、`/imports` | 服务端目录及包装清单摘要；准备与导入分开，回读 input_fingerprint |
| 文献 | ingestion `/carriers`、`/documents`、`/current-input`、`/snapshots/{id}/segments`、`/assets/{id}/content` | 固定片段只读，保留正文、页号、资产和可用性；不推断研究使用已获准 |
| 分件 | ingestion `/drafts`、`/{id}`、`/revisions`、`/previews`、`/applications` | 完整组织草案 JSON；固定草案修订、预览 fingerprint 和所选依赖组；应用是后台任务 |
| 检索 | retrieval `/searches`、`/jobs/{id}/result`、`/searches/{list_id}/results`、`/reads` | 固定快照与用途；命中 cursor 和 read_cursor 不混用；结果回读不创建另一检索任务 |
| 卡片 | cards `/cards`、`/{id}/revisions`、`/edit-document`、`/card-revisions/{id}/diff` | 当前与候选区分；编辑保留 source_manifest、check_record_refs 和来源选择 |
| 人工采用 | cards `/revision-previews`、`/{id}`、`/revision-decisions`、`/issues/{id}` | 必须后端预览 ready_for_decision 且 adoptable，提交固定预览指纹/当前修订/保护版本；研究分歧需逐项明确确认 |

所有列表按后端游标分页，首批 50 条；此轮没有跨模块合并身份或全局计数。卡片“审核入口”列出所有卡片，不把“无 issue”当作无需人审，也不把存在 current 当作后续候选已全部审核。细化候选级未决组待办仍属 G07 后续工作。

卡片编辑开放 `items[].text`，复用 Markdown 可视编辑与源码入口，HTML 表格仍保留 HTML。2026-09-12补充释读、陈述归属、原文语境、使用限制、其他解释及待查问题字段，可明确声明机器辅助修订及原件依据；机器修订候选仍须原图和语义核验，不自动采用。引文、锚点及引用清单只读；完整结构增删、保护管理与其余卡片字段表单尚未开放。分件是正式完整草案，但本轮先用 JSON 编辑器表达复杂组织操作，不伪装为简单增减 PDF 页数。

## 恢复与写入

- 浏览器发送的相同未确定请求复用持久操作键；成功响应后清除该请求键。没有自动重放或后台补发。
- 操作失败保留编辑内容，不能把错误当作已保存。HTTP 失败不回退演示数据。
- 真实编辑缓冲尚非独立持久草稿，离开/刷新有未提交提醒；后端预览及任务已有持久回执。多标签原子提交、统一回执恢复界面、所有版本冲突 UX 留待边界阶段。
- 文档转换没有路由、调用或修改。原 PDF 上传不接通；可用入口是已有标准交付包的目录准备，不新增虚假的 PDF 进度。

## 制卡发布策略变化

`research_cards/generation.py::publish` 新发布只记录候选、核查及全部 pending_groups，不再调用 `store.adopt(origin="machine")`。此路径同时用于首次生成与更新发布，当前采用版本保持不变；采用由现有 revision-decisions 显式完成。

保护/并发组合检查继续保留，不把机器核查升级为用户人审。历史 `machine_adopted` publication 的幂等回读保持原样，不回填或改写。论文提示 `paper.py` 不在本轮范围，行为未变。

## 未完成闭环，不宣称联调完成的部分

1. **G01 / 转换**：用户排除本轮；转换交付包的新输出兼容与 PDF 上传待后续。
2. **G04 / 正文修订**：入库暂无全文 Markdown 草稿、修订、定位更新与独立人审放行合同。真实正文目前只读，不能将组织 `revise_document` 冒充正文修订。
3. **G05 / 自动制卡资格**：已有检索 ready_for_auto_card 是技术就绪，不是新规则要求的人工放行。没有伪造放行记录或开启自动制卡；显式生成按钮亦未绕过它。
4. **G02 / G03 / G07 / G08**：当前采用分模块列表与实际操作，不声称统一身份、完整风险待办、全候选未决组统计和动态能力合同已完成。
5. 不启用导出、论文提示、历史清库、认证或全局边界验收。

## 本轮证据

- `npm test`：24 个前端测试通过；`npm run build`：通过，编辑器主功能包仍有 >500 kB 体积警告。
- 入库：`tests/test_http.py tests/test_draft_revisions.py tests/test_drafts.py`，8 项通过，使用独立测试数据库，不使用历史数据。
- 检索：`tests/test_contracts.py tests/test_job_wait_http.py tests/test_client_continuation.py`，6 项通过；HTTP 层使用隔离 runtime，不冒充真实索引/GPU 验证。
- 制卡：`tests/test_frontend_review_policy.py tests/test_public_revisions.py tests/test_persistence.py`，12 项通过；独立 schema，不执行付费模型。
- 浏览器：`output/playwright/hrs-workbench/module-contract-check.cjs`，15 项隔离 UI 检查；请求全部拦截为明确工程夹具，未发往历史数据库。真实代理连接另行只读观察，不混算。
- 请求合同：将 [捕获记录](../../../output/playwright/hrs-workbench/module-browser-result.json) 交给各模块现有虚拟环境执行 `scripts/check-module-requests.py`，入库 6、检索 3、制卡 3 份通过真实 Pydantic 类型。
- 开发机器视觉评估见 [development-review-modules.json](development-review-modules.json)。不等于人类验收、史料审批或生产发布许可。

后续用新转换产物开展真实材料全流程测试时，先补齐上述资格/修订闭环，再验证高风险放行、自动制卡、后台预览及人工采用；不能仅凭本轮请求格式通过就启动旧数据回填。
