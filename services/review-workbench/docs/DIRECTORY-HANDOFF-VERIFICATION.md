# 目录自动交接验证

2026-09-10。本轮实现用户批准的“无需 ZIP，转换后无待审项自动进入入库模块”。

## 当前行为

- 用户仍上传 PDF 并明确开始转换。新任务完成时读取项目生产 DeepSeek 的最终核验回执、块级可用性及待审报告；不由 GPT/Codex 生成、修改或补齐生产判定。
- 核验齐全且无待审项：后台持久排队，按目录提交准备与入库任务。页面关闭不影响交接。有待审项或证据缺失：不交接，手动请求同样不能绕过。
- 使用入库现有 `server_directory` 接口。交接清单和文件逐项校验；目录完整发布后才提交。没有 ZIP 打包、上传或解包。原件和转换产物不改写；既有独立 ZIP CLI 兼容入口保留，工作台不调用。
- 入库服务 / worker 暂不可用时保留回执并退避重试；重启继续同一准备任务和操作键。终态失败、证据改动、回执损坏则停在 blocked，保留产物供检查。
- 旧完成任务不自动补入库。本轮没有重新转换或入库此前 41 页 PDF，没有处理历史垃圾数据，没有启动真实库 worker 或制卡。

## 测试结果

| 检查 | 结果 |
| --- | --- |
| 转换适配层 unittest | 11 项通过：上传与控制、局部待审、生产证据门禁、目录完整性、自动交接、重启恢复、去重、异常回执 |
| 入库目录链路 / 原 ZIP CLI / 客户端回归 | 8 项通过；目录链路使用独立 PostgreSQL 数据库、实际 API 处理器、实际持久回执与 worker |
| 前端 Vitest | 27 项通过 |
| TypeScript / Vite 构建 | 通过；仍有既有编辑器约 703 kB 分块告警 |
| Playwright 桌面隔离检查 | 9 项通过：自动轮询、提交状态语义、防重复、待审禁用、无横向溢出及无页面运行错误 |

目录链路测试仅把 HTTP 传输接到进程内 TestClient；生产业务逻辑未替换。合成 PDF、页图和审核字段均明确标为工程夹具，不调用 OCR/LLM，不作为真实史料质量验收。独立测试数据库由 fixture 创建并仅删除其自身随机命名数据库。

浏览器初次运行发现测试执行环境没有全局 `URL`，已修正测试路径解析，并在独立新会话重新运行全部 9 项通过；不是生产页面问题。截图仅评估交接状态区与布局，下方内容是工程占位数据，不作内容审核结论。

证据及开发视觉判定：`output/playwright/hrs-workbench/directory-handoff-verdict.json`，同目录 `directory-handoff-ui-check.cjs` 及两张 `directory-handoff-*.png`。

## 尚未生效的运行步骤与限制

当前 `18120` 仍是旧适配进程。本轮确认无活动转换后尝试重载，但执行策略拦截了进程重启命令；没有绕过拦截，也没有声称新后端已上线。前端开发服务已热更新。需用户停止原适配进程后，在项目根目录重启：

```powershell
& services/document-ingestion/.venv/Scripts/python.exe services/review-workbench/server/conversion.py
```

重启后 `/api/v1/health/live` 应包含 `handoff_transport: server_directory`、`automatic_ingestion: true`。默认接收目录对应当前入库 `config/local.example.toml`；如有自定义配置，请用 `--receive-root` 指向入库模块实际 `receive_root`。

入库 API 与其独立 worker 必须运行才会实际完成入库。自动交接的 submitted 只是任务提交，不是入库完成、篇目人工采用或研究卡片完成。人工修订与正式放行合同仍待后续接入；这次不伪造放行。此结果不是上传至研究卡片的完整链路验收。

## 复跑

```powershell
# 项目根目录
& services/document-ingestion/.venv/Scripts/python.exe -m unittest discover -s services/review-workbench/server -p test_conversion.py -q
# services/document-ingestion 内
& .venv/Scripts/python.exe -m pytest tests/test_workbench_conversion_package.py tests/test_pack.py tests/test_client.py -q
# services/review-workbench 内
npm test
npm run build
```
