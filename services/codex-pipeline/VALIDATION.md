# 本地验证：2026-10-21

结论：本次 Codex 默认工作流的本地验证通过。所有内容裁决为机器决定，不是人工审核或 sealed gold 认证。[结构化裁决和证据摘要](validation-20261021.json) 随代码保存；原件、全文和运行日志保留在本机的 `state/codex/`。

## 真实 Codex 运行

使用已登录的 Codex CLI 0.148.0，继承当前模型配置，接入真实 stdio MCP。协调器 JSONL 记录了三次成功的原生 `spawn_agent`：

| 阶段 | 子代理身份 | 结果 |
| --- | --- | --- |
| OCR | `ocr-a85c20-page1` | 完整转录正文、页眉和脚注 |
| 独立原图审核 | `review-a85c20-page1` | approved；记录排版与标点规范化，无未解决问题 |
| 文章组织 | `organization-a85c20` | 物理第 1 页对应印刷第 10 页，标明首尾续文与脚注归属，书目身份 unclaimed |

样本为已有真实扫描 `output/document-extraction-broad-scan-20260909/originals/B17/page-0041.png`。原图先于候选文本接受视觉检查，人物、时间、数量、因果和注释归属保持。原图审核接受初稿，本次真实样本无需修订；修订、独立复核及失败阻断路径由协议与 Store 回归测试覆盖。

协调器实际调用了 `publish_document` 与 `search_library`，文档状态为 `published`，待办和活动租约均为 0；以“马福祥”检索命中原物理页。CLI 退出码为 0，未调用 Paddle、Qwen、DeepSeek 或旧 Temporal 流程。

- 文档 ID：`a85c20ae8d54fe8bc73c3fc2`
- 原件 SHA-256：`a85c20ae8d54fe8bc73c3fc214f3a571378dfeab55e217eb0a284a0ae584a893`
- 审核页图 SHA-256：`e7a22db2bef4a7ae66833ae7dcec49e4c0a3d36d54ed99dfd32ddee87ec42f52`
- 发布 Markdown SHA-256：`558fc2812c4541f28db161e4363ee82d46451714ea3df678010c8fab2925655b`
- 成功运行日志：`state/codex/runs/a85c20ae8d54fe8bc73c3fc2/20261021T151652052558Z/`

## 工程验证

| 检查 | 实际结果 |
| --- | --- |
| 新模块单元与真实 stdio MCP 协议测试 | 30 passed |
| 旧平台和共享运行库完整回归 | 386 passed，14 skipped（原有实时服务条件未启用） |
| 前端测试 | 15 文件，34 passed |
| OpenAPI 契约生成 | 通过 |
| 前端未使用代码检查 | 通过 |
| 前端生产构建 | 通过 |
| 新模块及旧平台 Ruff | 通过 |
| Git diff 空白检查 | 通过 |

合计 450 项测试通过，14 项按既有条件跳过。旧平台回归使用独立 Docker 测试 PostgreSQL、S3 和 OpenSearch；现有生产卷与应用容器未改动。新增 GitHub Actions job 可在无 Codex 登录和模型凭据的环境复现新模块的全部工程测试。

```powershell
uv sync --project services/codex-pipeline --locked --group dev
uv run --project services/codex-pipeline --locked python -m pytest services/codex-pipeline/tests --basetemp .cache/codex-final-regression --junitxml reports/codex-pipeline.xml
uv run --project services/codex-pipeline --locked python -m ruff check services/codex-pipeline
```

本次验证覆盖一页真实扫描和完整任务/恢复协议。旧 SQL/S3 库与新本地工作区保持独立，尚未迁移历史资料，也未据此宣称整书批量准确率或人工金标通过。
