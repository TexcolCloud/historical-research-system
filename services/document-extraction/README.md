# Document Extraction（旧转换模块）

本模块保留 Docling、单路 OCR、本地视觉复核和来源映射的兼容实现。项目默认处理路径已改为 [Codex 原生子代理与本地 MCP](../codex-pipeline/README.md)，新资料导入与继续处理见 [项目 README](../../README.md)。本模块不是默认流程的依赖；保留代码不表示重新开放 OCR 选型或调优。

## 职责与使用前提

输入为 PDF 或 Docling 支持的图片，输出为本地可恢复的转换工作目录。模块保存原页、候选 Markdown、来源坐标、核验记录和待审项，不确定书目身份、不划分正式文章、不建立检索索引。旧平台通过 `stages` 入口拆分识别与审核，并另外管理持久化和入库；独立 CLI 完成不等于已入库。

仅在维护既有转换任务或明确使用旧路径时运行。需要 Windows、Python 3.11，以及按 [pyproject.toml](pyproject.toml) 安装的独立 OCR 环境。模型和缓存位置见 [模型说明](../../models/README.md)。旧环境安装入口为 [bootstrap.ps1](../../deploy/windows/bootstrap.ps1)：它会安装依赖、执行后端初始化检查，并可能下载工件和占用 GPU。

旧路径的视觉复核使用本地 Qwen3-VL-8B-Instruct Q4_K_M 和共享 broker；配置缺失时不会改用 DeepSeek 视觉。模型准备、broker 和设备要求属于旧路径，不适用于默认 Codex 审核。

## 命令

以下 PowerShell 命令均从仓库根目录执行。先查看帮助，不会启动识别或审核：

```powershell
$env:PYTHONUTF8 = '1'
$extract = 'services/document-extraction/.venv/Scripts/history-extract.exe'
& $extract --help
& $extract extract --help
```

以下示例会实际转换或核验，需先替换输入路径并准备上述旧环境：

```powershell
& $extract extract 'C:/materials/book.pdf' --output 'output/book'
& $extract extract-batch 'C:/materials' --output-root 'output/batch'
& $extract verify-quote 'output/book' --quote '要核对的原文'
```

`extract-batch` 递归查找 `*.pdf`，按相对路径生成子目录，并写入 `batch-summary.json`；有转换异常时退出码为 1。`extract` 的成功退出只表示命令完成，应继续检查 `manifest.json` 的内容状态和待审数量。

`verify-quote` 检查候选正文中的精确引文及其来源，退出码为 0 表示 `evidence-linked`。引文重复时可用 `--start` 指定候选正文偏移，用 `--markdown-sha256` 绑定正文版本。它不会发起模型复核，也不生成研究卡。

```powershell
& $extract check
```

`check` 会初始化 Docling/OCR 后端，可能下载模型并占用 GPU；它不是只读健康探针，也不证明整份资料已通过审核。

## 配置

默认读取 [config/default.json](config/default.json) 和仓库根 `.env`。全局 `--config`、`--env` 必须放在子命令之前：

```powershell
& $extract --config services/document-extraction/config/rapidocr.json extract 'C:/materials/book.pdf' --output 'output/book-rapidocr'
```

| 配置 | 行为 |
| --- | --- |
| `ocr_backends` | 必须且只能有一个后端；默认 PaddleOCR-VL-1.6，`rapidocr.json` 为显式替代，不自动补读 |
| `native_pdf.policy` | 默认 `native-pdf-layout-v2`，检查文字层并保留原生布局证据；不满足条件的页交 OCR 或视觉复核 |
| `vision_review` | 默认开启 `full` 复核和缓存；旧平台 `review` 阶段要求视觉开启且模式为 `full` |
| `risk` | 管理风险检查与自动接受；未解决内容仍保留待审 |
| `acceleration` | 设备、精度、CPU 回退及显存约束，实际环境覆盖见 [settings.py](src/document_extraction/settings.py) |

`HRS_MODEL_ROOT` 指定模型根目录，`HRS_ENV_FILE` 可覆盖环境文件；`HRS_OCR_DEVICE`、`HRS_OCR_DEVICE_ID` 和 `HRS_MAX_VRAM_GB` 可覆盖设备设置。`HRS_LOCAL_VISION_URL` 指向本机 broker，`HRS_VISION_CACHE` 覆盖审核缓存目录。不要将密钥、权重或本机缓存提交 Git。

原生提取的确定性通过属于本模块的旧规则，不替代默认 Codex 流程的独立原图审核。旧策略和检查点继续按绑定配置恢复，不能通过改配置追溯放宽审核。

## 输出与恢复

| 产物 | 用途 |
| --- | --- |
| `docling-document.json`、`docling-document.md` | 转换结构与原始序列化正文 |
| `docling-assets/`、`pages/`、`ocr/` | 图像资产、原页图和识别证据 |
| `conversion.json` | 来源摘要、后端、页数和转换错误 |
| `document.candidate.md` | 复核后的候选正文 |
| `source-map.json`、`page-boundaries.json` | 正文字符范围与物理原页的对应关系 |
| `reviews/`、`completion.json`、`semantic-acceptance.json` | 逐页核验、修正与汇总状态 |
| `manual-review.json`、`review-cards.json` | 未解决内容及其证据；这里的 review cards 是待审条目 |
| `content-readiness.json`、`article-structure-review.json` | 块级可用性与页面结构观察，不声明文章身份 |
| `artifact-manifest.json`、`manifest.json` | 资产入口、状态和证据哈希 |

文件随执行阶段和成功状态生成，失败目录不保证包含完整清单。以 [artifacts.py](src/document_extraction/artifacts.py) 的实际输出为准。表格通过 HTML 保存合并关系，图片通过相对资源路径保留。物理页与印刷页码分开记录；缺少可靠细粒度坐标时，只能定位到原页。

[stages.py](src/document_extraction/stages.py) 提供旧平台的 `ocr`、`review` 阶段入口。`ocr-checkpoint.json` 绑定来源、提取配置和文件摘要；审核可复用已提交的识别证据。恢复必须使用匹配的原件、目录和配置，不能手改哈希或复用另一份资料的检查点。独立 `extract` 命令没有 `resume` 子命令。

唯一定位的修正只有经独立原图复核且无未解决内容后才能写回。不可读、歧义、请求失败和复核失败保留待审；候选正文与清晰块可供查看，不能据此宣布整份资料完成。已有人工决定和草稿应保留，研究卡生成已退役。

## 开发验证与排障

在已安装本模块 `dev` 依赖的环境中，从仓库根目录运行：

```powershell
& 'services/document-extraction/.venv/Scripts/python.exe' -m pytest services/document-extraction/tests
```

这些测试检查转换契约、来源绑定、原生分流、审核和恢复；集成测试使用临时 PDF 和模拟识别／审核响应，不证明真实史料准确率。[工程 CI](../../.github/workflows/engineering.yml) 另外从平台轻量环境运行无需模型权重的指定测试。

| 现象 | 检查方法 |
| --- | --- |
| CLI 不存在或依赖导入失败 | 确认使用独立 OCR 环境及 Python 3.11；不要混用平台环境 |
| 后端初始化、权重或设备失败 | 检查模型目录、所选 OCR extra 与设备配置；`check` 本身会加载后端 |
| 视觉请求超时或 broker 不可达 | 检查旧本地视觉配置及日志；保留待审状态，不将服务健康当作核验完成 |
| 检查点摘要不符 | 核对原件、冻结配置和工作目录；保留错误证据，不绕过摘要校验 |
| 命令完成但仍有待审 | 查看 `manifest.json`、`manual-review.json` 和相应逐页证据；不通过关闭审核放行 |
