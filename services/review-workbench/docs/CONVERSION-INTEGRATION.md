# 文档转换前端接入

2026-09-10。工作台新增本机转换适配层，使用重构后的现役 CLI；不修改识别、自动修正算法或模型配置。按用户新要求，放行策略已调整为 DeepSeek 错误块局部拦截，表格不因类型强制人审，见 [当前策略](DEEPSEEK-RELEASE-POLICY.md)。

## 运行边界

**1.4 更新：** 当前前端使用分块断点续传与 SHA-256 内容去重，完整核验后自动排队；见 [当前上传合同](RESUMABLE-UPLOAD.md)。本文下述整文件重传限制仅适用于旧 POST /jobs 入口，不适用于新前端。

**当前变更：** 上传和取消策略已更新为多选 / 拖拽、成功即排队、取消即停止并删除；接口细节和重启限制见 [上传队列说明](UPLOAD-QUEUE-VERIFICATION.md)。下述原始接入记录中的“手动开始”“排队项重启后中断”“不能运行中取消”已被新策略替代。旧客户端省略 enqueue 时仍只上传。

- 运行期只调用项目 `document_extraction.cli extract`。识别和语义复核均由项目自己的 Docling / PaddleOCR-VL / DeepSeek 配置决定，没有 GPT、Codex 或人工替换结果的运行分支。
- API 启动、连接检查、列表和结果读取不调用模型。上传只保存原件，点击“开始转换”才排队；单 worker 顺序执行，避免并发占用同一 GPU。
- 本次用户指定 `_117-157.pdf`（41 页）进行真实转换。没有重新导入历史数据库，没有自动启动入库 worker、检索同步或制卡。
- 上传文件保存为不可变 `source.pdf`，每次失败重试使用独立 `attempts/N` 目录；不覆盖上轮产物。原文件名、SHA-256、字节数和任务身份保存在 SQLite。
- 转换汇总待审与内容块可用性分开。表格待审不把同页合格正文一并冻结；`article_structure_status=not-claimed` 不被改写为篇目已确认。

## 启动

在项目根目录运行（复用入库模块已有 FastAPI / Uvicorn / Pydantic 运行环境，无新增依赖）：

```powershell
& services/document-ingestion/.venv/Scripts/python.exe services/review-workbench/server/conversion.py
```

默认仅监听 `127.0.0.1:18120`；可传 `--port`、`--state-root`、`--receive-root`，同一 state-root 只运行一个适配服务进程。默认状态目录为项目根 `state/workbench-conversion`。重启后原运行/排队转换任务标记中断，需要显式重试，不自动再扣费；正常关闭会停止它自己启动的转换子进程。已登记的目录交接独立恢复，不重新转换。

Vite 的 `/modules/conversion` 同源代理去掉此前缀后转发到上述服务。服务端 `WORKBENCH_CONVERSION_ORIGIN` 可覆盖代理地址（根 `.env` 或进程环境）；独立部署前端时需配置同等反向代理。浏览器不得配置模型密钥。

适配层的入库交接默认访问 `http://127.0.0.1:18125`，通过适配进程环境 `WORKBENCH_INGESTION_ORIGIN` 覆盖。转换 CLI 继续按自身规则加载项目根 `.env` 和 `config/default.json`，不由前端选择第二套识别器。

目录交接要求两个模块可访问同一接收目录。适配层取值顺序为 `--receive-root`、进程环境 / 根 `.env` 的 `INGEST_RECEIVE_ROOT`、项目 `services/document-ingestion/.cache/receiving`（对应当前入库 `config/local.example.toml`）。自定义部署时必须与入库模块 `receive_root` 指向同一绝对目录；不会自动把本机路径发送给远端或回退 ZIP。

此服务是本机桌面 happy-path 实现，不提供公网鉴权、多人写入、多进程任务租约或高可用调度。原始 PDF 上传首版上限 256 MiB，传输中断后重传整个文件，不声称支持原始 PDF 的 tus 续传。

## 实际 API

以下路径相对 `/api/v1`。自动生成的实际合同可从适配服务 `/openapi.json` 读取。

| 方法 / 路径 | 职责与约束 |
| --- | --- |
| GET `/health/live` | 连接及适配 worker 开关，不加载模型，不宣称模型就绪 |
| POST `/jobs?filename=...` | 原始 PDF 字节流，`Idempotency-Key` 必须 UUID；同键同文件返回原任务，不触发转换；检查后缀、签名与大小 |
| GET `/jobs?limit=50&cursor=0&pending_only=false&review_scope=all` | review_scope 可省略或为 all / pending；审核范围只列已完成结果，含机器通过可抽查，待审优先；pending_only 同时考虑待审页与块 |
| GET `/jobs/{id}` | 持久状态、轮次、控制版本、真实产物计数、估计 progress 和交接回执 |
| POST `/jobs/{id}/controls` | `{action: start\|cancel\|retry, expected_revision}`；状态不适用或旧版本返回 409。控制请求以版本防重复，不提供 operation-key 回执重放 |
| GET `/jobs/{id}/source` | 上传原始 PDF，按已登记任务取文件 |
| GET `/jobs/{id}/result?page=1` | 完成轮次的 v4 manifest、实际块、原文、审核卡及可用性；验证证据/正文 SHA-256；Unicode 字符切片由 Python 完成 |
| GET `/jobs/{id}/pages/{page}` | 读取转换器产生的真实原页图并验证摘要，不做二次 OCR 或自行推算精确框 |
| POST `/jobs/{id}/handoff` | 补交既有任务 / 立即重试目录交接；仍执行核验门禁，无可用块或证据不一致返回 409。新策略可交接局部可用产物，待审块不获得使用权限。同一轮复用持久回执，不自动启动入库 worker |

状态：`uploaded → queued → running → completed / failed`。仅 uploaded / queued 可取消；失败、中断、已取消可显式重试。运行中不伪造暂停、继续或取消能力。页面每 3 秒查询活动任务。progress 按识别 70%、首轮复核 20%、收尾 10% 估算，未知总页数时返回 null；100% 仅来自任务 completed。产物回执数不等于已核验合格页数，百分比不是耗时比例或剩余时间。

## 结果界面与交接

“处理任务”接收 PDF 并查看转换进度；“审核中心”可打开转换待审原件。结果按物理页提供待审问题、渲染 Markdown / HTML 表格和原图对照。点击问题切换对应原页并高亮已知内容块；页级问题只定位到该页，不从 VLM 占位坐标生成精确框。

生产正文和来源映射目前只读。审核台可编辑渲染 Markdown / HTML 表格及源码，但仅作为按任务、轮次、正文 SHA、内容块绑定的浏览器本机草稿保存；不会提交生产修订或解除待审。G04 正文修订、重定位、人工处置放行合同仍未接通，因此没有“已提交修订”或“放行”假按钮。G05 自动制卡业务资格也未被这次接入隐式关闭。三栏界面与估计进度的本轮验证见 [审核台记录](REVIEW-DESK-VERIFICATION.md)。

入库交接改用 `pack_directory(..., directory_mode=True)` 与 `client.submit_existing_input`：原样复制声明文件到独立暂存目录、逐文件校验、完整后发布为 `receive_root/workbench/{job_id}/{attempt}`。保留原始源文件绑定、readiness、审核卡和声明证据，不生成 ZIP，不走 tus 上传，也不修改转换器产物。

新任务转换完成时，按生产 DeepSeek 最终回执与块级可用性登记自动交接，由独立后台线程提交 `server_directory` 准备及入库任务。无错误则全部自动放行，无需人审；存在错误时只拦截错误 / 未核验块及其依赖，其余合格块继续。全无可用块或证据不一致则不交接。浏览器不触发自动交接，关闭页面不影响执行；手动按钮不能解除待审块的使用限制。旧版待审产物不按新策略自动补交。现阶段正式人工修订 / 放行合同仍需后续接入。

交接状态为 `queued → running → submitted / waiting / blocked`。submitted 只表示入库任务已提交，不等于入库完成或人工放行。网络 / 等待超时进入 waiting，按 15、30、60、120、240、300 秒上限退避恢复；终态失败或证据 / 回执异常进入 blocked，保留文件供排查。准备 / 入库操作键和 ID 保存在 `attempts/N-ingestion-receipt.json`，不会因重启重复建单。升级不扫描旧完成任务补入库，不自动启动入库 worker、制卡或模型。

本轮目录自动交接验证与限制见 [目录交接验证](DIRECTORY-HANDOFF-VERIFICATION.md)。以下为此前 PDF 接入阶段的历史检查，不替代本轮记录。

## 已运行检查

- 前端 `npm test`：26 项；TypeScript / Vite 构建通过，仍有既有编辑器大包告警。
- `server/test_conversion.py`：5 项，覆盖上传持久化与幂等、显式控制及重启恢复、实际现役产物写出、局部表格待审、证据版本、失败保留与现役打包器兼容。
- `document-ingestion/tests/test_workbench_conversion_package.py`：1 项真实隔离数据库测试，重构模块实际产物写出 → 标准包准备 → 入库及规则 worker 通过。使用人工编写的工程夹具，不是历史材料，不调用模型。
- 浏览器新增 10 项隔离交互通过；原有非转换模块 15 项隔离回归通过。工程夹具请求全被拦截，不进入真实数据库。
- 用户指定 41 页 PDF 已完成真实项目转换及 41 份 DeepSeek 复核回执，前端真实首尾页只读检查通过。详细结果见 [验证记录](CONVERSION-VERIFICATION.md) 及项目 `output/pdf/workbench-conversion-20260910/`；不把隔离夹具或 GPT 开发界面检查记为转换管线成绩。

测试命令（根目录）：

```powershell
& services/document-ingestion/.venv/Scripts/python.exe -m unittest discover -s services/review-workbench/server -p test_conversion.py
# 在 services/document-ingestion 内：
& .venv/Scripts/python.exe -m pytest tests/test_workbench_conversion_package.py -q
```
