# 调用与恢复

以下命令从 `services/document-ingestion` 执行，先按 [README](../README.md) 启动数据库、HTTP 与 worker。
Linux 将 `.venv/Scripts/history-ingest.exe` 换成 `.venv/bin/history-ingest`；不要共用提取模块的环境。

## 导入自己的提取包

将示例路径替换成明确选定的提取包与原件；输出 ZIP 必须是尚不存在的新路径：

```powershell
.venv/Scripts/history-ingest.exe pack "D:/your-extraction/doc-001" --original "D:/your-original.pdf" --output ".cache/intake-001.zip"
.venv/Scripts/history-ingest.exe --url http://127.0.0.1:8766 submit --package ".cache/intake-001.zip" --display-name "暂定载体名" --receipt ".cache/intake-001.receipt.json" --wait
```

保留 receipt 文件；同一命令同一回执可继续中断的上传/等待，不要为重试任意换目标。
已有载体重提取将 display-name 改为 `--existing-carrier 实际UUID`；新结果只待采用，不自动替换 current。
包装时缺少本地表格引用，应提供 `--bindings 明确绑定列表.json`，结构见 [API](api.md#5-标准来源绑定)。
不能把文件名相似当作绑定证明，也不能为通过校验修改冻结的提取输出。

提交输出的 `import.receipt` 包含 snapshot_id、extraction_id、partition_job_id。
`--wait` 等到导入任务结束；自动规则子任务仍须单独确认：

```powershell
.venv/Scripts/history-ingest.exe jobs wait 实际partition_job_id --timeout 300
```

无标准来源映射的旧材料必须显式 `--format-mode legacy_archive`，只得到 archived_pending，不能假装标准入库。

## 编辑与采用

可用 `http://127.0.0.1:8766/docs` 调用接口。流程是：读取规则任务 result 中 draft_id，导出清单，
编辑 body 后导回，固定新修订做预览，最后选择完整依赖组采用。候选文字、模型建议和旧清单都不能自动带来批准。

Python 消费示例（ID 替换为实际返回值）：

```python
from document_ingestion.client import ManagementClient

client = ManagementClient("http://127.0.0.1:8766")
manifest = client.get("/api/v1/drafts/实际草案UUID/manifest")
manifest["body"]["operations"][0]["display_name"] = "修订后的暂定题名"
edited = client.post("/api/v1/draft-manifests", manifest, "my-offline-edit-1")
draft_id = edited["draft_id"]
revision_id = edited["current_revision_id"]
preview = client.post(f"/api/v1/drafts/{draft_id}/previews",
                      {"draft_revision_id": revision_id}, "my-preview-1")
print(preview["groups"])  # 先检查依据、归属、限制和逐组问题，再选择要采用的 ready 组。
```

采用的完整请求包含 draft_revision_id、preview_id、preview_fingerprint、selected_group_ids 和 reason。
不要将所有组不加检查地自动采用；有依赖的操作不可拆开确认。返回的 application.job 是持久任务，
完成后还要看 application.outcome 和每组 state，partial 不是全部成功。

在线修改使用 `POST /drafts/{id}/revisions`，带 expected_current_revision_id 和完整 body。
hold/reject 操作须写理由；重新开启通过新修订改成 propose，不改旧记录、不继承旧预览。
已采用组织的修订同样建立新草案；恢复旧阅读结果须显式引用旧快照，不能删除新快照来“回滚”。

## 读取研究输入

先取 `GET /current-input?target_type=carrier&target_id=实际UUID`，再从固定快照读取 segments。
来源坐标是 Unicode 码点的半开范围，浏览器 UTF-16 索引不可直接传入；原始 needs-evidence 等状态一直保留。

研究读出使用 `/snapshots/{id}/research-segments?purpose=card_input&requested_fields=text`，
或将固定引用发到 `/usage-checks` 明确核查字段。即使旧快照不变，当前用途限制也可能已经变化。
批量消费者先冻结 research-baseline，分页读取其固定成员，再从 baseline_cursor 消费 changes；
完整处理一个事件后才推进游标。不要把动态管理列表拼接成研究全库快照。

需要根据原件复核固定范围时，先调用 `/source-qualification-context`，再将实际审核记录作为
`qualify_source` 草案操作，经过预览和采用。按具体用途与字段记录机器有限资格，保留原始内容状态。
撤销使用 `revoke_source_qualification`。完整约束见 [API：按证据范围记录机器复核](api.md#14-按证据范围记录机器复核)。
已有 `restrict_source` 限制仍然生效；机器复核不能覆盖活动限制，也不能被标作人工或金标批准。

## 故障恢复

| 情形 | 操作 |
| --- | --- |
| CLI 断开/超时 | 重用同一 receipt 再提交或 jobs wait；超时不会取消后台任务 |
| 任务 failed/cancelled | jobs show 查 code；修复环境后用 jobs retry，CLI 自动带原执行代次和尝试 ID |
| 输入内容本身变了 | 创建新包、新准备及新导入，关联旧失败记录；不覆盖旧输入 |
| 旧格式待补齐 | 标准包新导入时设置 completes_archive_id，旧归档保留 |
| 预览冲突 | 读取当前对象，追加草案修订并重新预览，不能伪造 preview_fingerprint |
| 资产暂不可用 | 检查 locations/storage-writes，显式提交 reverify；缺字节须从可信原输入重新入库 |
| S3 分段写完但回执失败 | 相同来源重试，模块先恢复原 UploadId/位置并完整回读，不手动删除对象 |
| DeepSeek 失败/预算耗尽 | 规则草案继续可用；修复明确配置后重新请求，不能假装模型已批准 |
| 数据库迁移落后 | 暂停自己的 worker，执行 db-upgrade 后恢复；不要重建库或为此重启 Docker Desktop |

取消/重试命令须保存各自非秘密控制回执：

```powershell
.venv/Scripts/history-ingest.exe jobs show 实际任务UUID
.venv/Scripts/history-ingest.exe jobs retry 实际任务UUID --receipt ".cache/retry-001.json"
.venv/Scripts/history-ingest.exe jobs cancel 实际任务UUID --receipt ".cache/cancel-001.json"
```

## 可运行的完整工程示例

以下命令创建明确标注的微型工程材料并执行真实 HTTP 链路，不是实际史料审定：

```powershell
.venv/Scripts/python.exe evaluation/tools/pipeline_smoke.py --url http://127.0.0.1:8766 --output .cache/my-engineering-walkthrough
```

它覆盖打包、tus/薄 CLI、准备、标准导入、自动规则、清单往返、采用、用途核查、固定基准与事件。
重启自己的 API/worker 后，加 `--resume` 可核对已保存的快照、基准成员和事件未变化。
输出目录中包含可恢复的工程回执，保留复核后再决定是否清理。

真实样本、大包与 Linux 的复现入口在 [README](../README.md#检查与接口导出)。
本机 Docker Desktop 的既有停启故障不属于本模块修复；运行服务不需要重启 Desktop。
