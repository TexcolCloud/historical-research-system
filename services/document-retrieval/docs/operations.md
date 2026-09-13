# 调用与恢复

本模块服务于逐篇取证和明确授权的跨文献补查。请求和返回的完整定义见 [OpenAPI](../contracts/openapi.json)、[Agent 工具定义](../contracts/agent-tools.json) 和 [README](../README.md)。所有 HTTP 路由以 `/api/v1` 为前缀。

## 服务与固定输入

先启动入库服务和 `config/compose.yml` 中的 OpenSearch，再运行本模块。Windows 使用 `config/local.example.toml`；Linux 示例明确采用 CPU FP32。每个检索实例使用独立的 `state_root` 与 `index_prefix`，同一状态目录只允许一个控制进程。路径相对 TOML 所在目录解析。

输入来自入库公开 API，包括固定快照、原文片段、当前用途、采用关系、来源基准与变更事件。检索不修改冻结提取产物。历史输入需要额外的机器来源认定时，使用入库的公开草稿、预览和应用流程；认定记录及其字段／用途范围与原始 readiness 分开保存，详见 [入库来源认定接口](../../document-ingestion/docs/api.md)。

`auto_sync = true` 持续接收变更并维护所选当前来源；实验可关闭自动接续，由显式索引作业构建固定快照。状态读取不创建索引工作。

## 取证调用顺序

| 动作 | 路由 | 调用方需要保留的内容 |
| --- | --- | --- |
| 解析来源 | `POST /sources/resolve` | `options_ref` 或已固定的 `scope_ref`；有歧义时明确选择 |
| 检查范围就绪 | `POST /index/status` | 逐来源文字／向量覆盖及 `ready_for_auto_card` |
| 保留主篇 | `POST /retentions` | `retention_id`；补查来源单独建立保留 |
| 提交检索 | `POST /searches` | 幂等键、`job_id`；已完成时保存 `list_id` |
| 查看作业 | `GET /jobs/{job_id}` | 执行状态、执行代次、尝试标识和范围是否固定 |
| 取得完成结果 | `POST /jobs/{job_id}/result` | 固定列表、来源、阶段说明、预算和后续入口 |
| 继续固定列表 | `POST /searches/{list_id}/results` | `next_cursor`；继续使用同一个列表 |
| 展开原文 | `POST /reads` | 完整 `source_anchor`、原字形、用途观察及 `read_cursor` |
| 绑定补查列表 | `POST /retentions/{retention_id}/lists` | 主篇与补查之间明确的保留关系 |
| 释放任务 | `POST /retentions/{retention_id}/release` | 原始 `released_at`；重复释放不重置时钟 |

HTTP 与 Python 工具都可能先返回作业回执。受理成功、执行完成和取证完整是三个不同状态。等待超时后继续查询同一 `job_id`，或以相同参数、相同幂等键重放；不要据此另建重复任务。取消或重试时提供最新 `expected_execution_epoch` 与 `expected_attempt_id`。

作业状态 GET 和结果 POST 均接受 `wait_seconds=0..30`，默认立即返回。结果 POST 可携带预算并等待完成；仍在执行时返回 202、原 `job_id`、`Location` 和 `Retry-After`，完成后返回正常的预算化结果。失败和取消沿用原有明确错误。Python `wait_job` 使用有界长等待；调用方仍须保存原作业身份。

`current` 来源在召回前持久固定，并在首次结果交付前重查采用。若采用已改变，调用方处理 `current_input_changed`，重新选择当前来源。`explicit_fixed`、旧列表与保留对象始终引用原固定输入；它们每次读取仍重查当前用途。

`exact_quote` 使用原字形与连续原文，允许跨内部软分块匹配。`compatible_text` 使用固定 OpenCC 映射和英文大小写兼容；派生字符串不充当原文。`hybrid` 使用文字、向量和可选重排。返回区分阶段未请求、部分可用、失败与完成；部分列表完成后不自动补成另一份排序，恢复后的查询产生新列表。

## 展开与预算

默认首批最多 6 项，单项摘录 128 个代理 tokens，完整 JSON 响应 2000 tokens；默认原文展开 4000 tokens。字段名、出处、状态与错误都计入响应。必要出处放不下时返回明确的预算问题和恢复入口，不把无出处文字当作完整证据。

`passage` 读取原片段或整表后备文字，`neighbors` 展开连续窗口及原文空白证明相邻的正文，`related` 跟随已有正文／注释关联，`media` 提供原件和页图，`provenance` 与 `bibliography` 提供来源字段。相邻正文须属于同一原始来源范围，排版空白须未被归类；已归类缺口、受限内容、标题和其他内容边界不会被跨越。标题作为中心时只向后展开。表格保留整表后备和 `table_structure_unresolved`。正文 `read_cursor` 和列表 `next_cursor` 不能互换。

首批结果保留阶段完成／失败状态；详细候选数、排序和覆盖诊断由 `details_ref` 指向固定列表详情。多条用途观察可以汇总显示，每个返回片段仍保留用途与许可状态；在 `provenance` 读取中将原请求 ID 传给 `observation_request_id` 可取得原始观察明细。

每段原文携带固定快照、来源片段、Unicode code point 区间 `[start,end)` 与文字 SHA256。全文锚点可独立回读，过期列表的排序不能由锚点重建。普通内容访问续期七天，仅查看状态不续期；任务释放后保留七天，有效依赖可继续保护内容及对应索引校验记录。

响应头 `X-Retrieval-Response-SHA256` 校验实际 UTF-8 正文，`X-Retrieval-Response-Tokens` 校验完整正文计数。Python Agent 工具原样转发同一 JSON 文本。`bge-m3-proxy-v1` 是锁定 tokenizer 的代理量，不能当成任意宿主模型账单。工具定义、提示、历史上下文、缓存及视觉输入需要另计。

媒体必须使用响应中实际返回的资产路由。服务支持流式 GET、HEAD、ETag 与 Range，读取时检查资产覆盖范围的当前用途。一个正文片段获准不会授权整页或整件。媒体拒绝可能是 `asset_usage_excluded`，即使局部文字仍可读取。

当任务需要回看来源原页时，媒体选项和实际下载均明确使用 `purpose=source_reading`。局部 `card_input` 资格不能扩展为整页制卡资格；整件用途无法判断时返回 `whole_asset_usage_unresolved`。

## 建索引、修复与切代

`POST /management/index-jobs` 接受 `baseline`、`rebuild`、`repair`。例如 `rebuild.json`：

```json
{
  "scope": {
    "kind": "selected",
    "sources": [{"snapshot_id": "00000000-0000-0000-0000-000000000000"}]
  },
  "activate_when_ready": true
}
```

将快照 UUID 换成实际输入，在模块目录运行：

```powershell
.venv/Scripts/document-retrieval.exe --receipt receipts/rebuild.json --output results/rebuild.json --wait index rebuild --request rebuild.json
```

`rebuild` 建立独立代次；配置、模型或片段规则的变化不混写旧向量空间。后台输入与必要水位核验完成后才允许激活。旧列表继续访问原代次。一个来源就绪即可单独取证，其他来源可继续后台构建。

启动时对保留的文字与向量记录核验完整负载和阶段文件哈希。发现缺失时报告 `recovery_required`，使用公开 `repair` 作业修复。手动修复采用当前已接收的事件水位，旧输入或旧尝试不能覆盖新绑定。不能通过修改 SQLite 的 ready 字段修复索引。

## 配对备份与停服恢复

```powershell
.venv/Scripts/document-retrieval.exe --receipt receipts/backup.json --output results/backup.json --wait recovery create
.venv/Scripts/document-retrieval.exe recovery show <package_id>
.venv/Scripts/document-retrieval.exe --wait recovery verify <package_id>
```

备份进入维护窗口。普通取证会返回可重试的 `maintenance`；管理状态与作业控制继续可用。取消或失败会留下终态包回执并退出维护。完成的包包含独立 SQLite 备份、必要阶段文件、模型／规则清单，以及配对的 OpenSearch 快照引用。保留模型文件和 OpenSearch 快照仓库；单独复制控制库不能替代配对包。

停止使用目标状态目录的检索服务，再执行：

```powershell
.venv/Scripts/document-retrieval.exe --config config/local.example.toml --output receipts/restore.json recovery restore <package_id或manifest.json路径>
.venv/Scripts/document-retrieval.exe --config config/local.example.toml serve
```

离线 CLI 获取独占锁并核验配对关系。缺阶段文件或控制库不匹配会拒绝恢复。成功后，在控制库之外保存恢复回执；恢复点之后才创建的外部对象可能不存在。

恢复后的调用方重新建立 `POST /readiness-baselines`，用返回的 `baseline_change_cursor` 消费 `GET /readiness-changes`。旧消费代次会被拒绝。入库事件接收水位恢复到备份点，当前用途仍从入库服务重新检查。

清理先预览：

```powershell
.venv/Scripts/document-retrieval.exe --wait cleanup
.venv/Scripts/document-retrieval.exe --wait cleanup --execute
```

回收只删除已到期且无有效依赖的对象。生产使用前按部署环境安排备份保管和服务管理；项目级认证、网络与跨模块边界约束在用户启动边界阶段后统一处理。
