# Codex local pipeline

默认入口见[根 README](../../README.md)。该模块只在本机执行原件留存、PDF/图片渲染、队列与入库校验；所有识别和内容判断由 Codex 原生子代理完成。[工作流契约](src/hrs_codex/workflow.md) 同时供 MCP 和协调器读取。

当前[真实史料验收](ACCEPTANCE.md)：工程检查通过；三组六页中，两组四页通过全文验收，另一组因原件一字不清保持待审。

## 工具

| MCP 工具 | 用途 |
| --- | --- |
| `workflow_instructions` | 读取协调步骤、分工和提交契约 |
| `prepare_document` | 留存 PDF/PNG/JPEG/TIFF、渲染全部物理页、生成 OCR 任务 |
| `list_documents` / `document_status` | 查看文档、干净内容和待办 |
| `claim_task` / `task_details` | 领取有过期时间的任务，读取带哈希输入 |
| `original_image` | 返回原页图像像素及 SHA-256 |
| `original_detail` | 从原 PDF 以 432 dpi 重渲染指定区域，或从原始图片取原生像素细节，保存哈希证据 |
| `submit_task` | 提交有租约、输入哈希与结构校验的机器结果 |
| `retry_task` | 保留旧裁决并重开待处理任务 |
| `publish_document` | 全文与文章组织通过后发布 Markdown |
| `search_library` | 在已发布文本中检索，返回页码与原图证据 |

并行子代理共享文件锁和原子 JSON 检查点。此版本按单机个人工作区实现，检索使用已审文本的直接子串匹配；暂不引入向量服务、外部任务队列或账号体系。

原图复核不通过时，显式重试会重新打开原始 OCR 的审核，再提出新修订并由独立子代理复核；旧候选、失败裁决和任务输入快照保留。原件不清的内容仍保持待核对，重试本身不代表通过。

小字、无法识别的字符或读法冲突应先调用 `original_detail` 检查原始证据。细节区域使用归一化边界 `[left, top, right, bottom]`；原 PDF 重渲染后再裁切。扫描源的实际像素不足时，提高渲染 DPI 不会补出缺失信息，仍应待审。细节图片和来源元数据存于文档的 `evidence/details/`，由提交结果的 `original_details` 列表引用；提交及后续读取会校验来源、页码、文件哈希和元数据。

## 产物

```text
state/codex/
  documents/<source-sha256-first-24>/
    source.pdf|png|jpg|tif    原件字节
    pages/*.png             渲染页及工作副本
    render.json             渲染清单与原件/页图哈希
    document.json           页、任务、OCR/修订/审核、文章映射
    document.md             全部通过后的文本
    publication.json        机器入库回执与哈希
  runs/<document-id>/<timestamp>/
    request.json            协调器指令
    events.jsonl            Codex 原生执行事件（含子代理活动）
    stderr.log
    summary.txt
    receipt.json
```

`document.json` 的 `pages[].ocr` 保留初稿，`candidate` 保留修订，`approved` 保存通过的最终正文；`tasks[].result` 与 `receipt` 保留历史结果。图片原件可能有独立的归档页和审核工作副本，两者均保留。

CLI 的 `--root` 选项位于子命令前。以复制整个工作目录进行本机备份，恢复后通过 `status`/`run` 继续。渲染清单含绝对路径，移动目录时应保留原路径或从原件导入新工作区；不要手动改证据哈希。

MCP 使用官方 Python SDK 2.2.0 的 stdio 传输。启动工具为 `uv run --project services/codex-pipeline --locked hrs-codex mcp`。该进程不调用模型 API、不加载本地视觉或 OCR 模型；模型连接与子代理生命周期由 Codex 管理。

接入方式依据 [OpenAI 本地 MCP 文档](https://developers.openai.com/codex/mcp)、[原生子代理文档](https://developers.openai.com/codex/subagents) 和 [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)。本地 MCP 的工具预批准仅限项目注册的服务；协调器保留 workspace-write 沙箱。
