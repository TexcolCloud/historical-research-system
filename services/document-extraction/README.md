# Document Extraction

PDF／图片转换模块，使用 Docling 与一个配置明确的 OCR 后端生成 Markdown、原页图及来源映射。默认后端为 PaddleOCR-VL-1.6；RapidOCR 是显式替代，不自动作为第二识别器。

[项目安装](../../README.md#快速开始) · [模型准备](../../models/README.md) · [平台处理流程](../research-platform/README.md#处理流程与数据职责)

## 使用范围

本模块负责原始载体提取、内容核验、证据和待办输出，不负责正式书目身份、章节入库、嵌入或研究制卡。平台通过独立阶段入口复用它，完整工作流由 Research Platform 管理。

独立 CLI 的本地输出是可恢复的转换工作目录；平台会把业务证据提交 S3。本地 CLI 完成不等于平台已入库，也不代表全部内容通过审核。

## 安装与命令

使用独立 OCR 环境，避免与平台依赖混装。在项目根目录按 [快速开始](../../README.md#快速开始) 准备配置和根目录标记，再运行：

```powershell
$env:UV_PROJECT_ENVIRONMENT="$PWD/services/document-extraction/.venv"
.\deploy\windows\bootstrap.ps1
```

安装脚本可能下载权重并初始化 GPU。原图核验需要另外准备 Qwen 模型并启动共享 broker，步骤见模型说明；不因配置缺失改用 DeepSeek 视觉。

以下命令仍在项目根目录执行：

```powershell
$extract = 'services/document-extraction/.venv/Scripts/history-extract.exe'
& $extract check
& $extract extract 'C:/materials/book.pdf' --output 'output/book'
& $extract extract-batch 'C:/materials' --output-root 'output/batch'
& $extract verify-quote 'output/book' --quote '要核对的原文'
```

输入路径仅为占位示例，需替换为实际材料。`check` 初始化识别后端，可能占用 GPU；不是只读健康探针。`verify-quote` 校验既有结果的精确来源，不生成史料卡。

切换识别后端时，配置选项放在子命令之前：

```powershell
& $extract --config services/document-extraction/config/rapidocr.json extract 'C:/materials/book.pdf' --output 'output/book-rapidocr'
```

后端选择与模型调优是独立工作；不要为复核失败自动重跑另一种 OCR。

## 当前审核规则

[default.json](config/default.json) 启用 `vision_review.review_mode=full`、自动接受和风险检查。视觉模型由 [settings.py](src/document_extraction/settings.py) 绑定本地 Qwen3-VL-8B-Instruct Q4_K_M，当前生产视觉不使用 DeepSeek。

1. OCR 保留原始识别结果与完整原页图。
2. 依据原页边缘、数字和上下文规则清理页码噪声，记录删除范围和摘要；不一律删除所有 footer，保留表格数字、注释与署名。
3. 全量视觉核验对照原图，保留请求、响应与证据绑定。
4. 唯一定位的修正经独立原图复核且无未解决内容后才能写回；歧义、不可读、未定位、请求失败或复核失败保留待审。
5. 平台保护已有人工决定和草稿。整书未解决／未核验内容清空后才入库和分块。

PDF 物理页序与印刷页码分开使用，表外页码不应作为表格正文比较。没有可信细粒度坐标时只承诺定位到原页，不把占位框当作精确证据。

配置类仍接受旧 `risk_based` 与 `conversion_only`，用于解释既有产物与显式 CLI 配置；这些模式不等于当前平台默认行为。`document_extraction.stages review` 明确要求视觉开启且为 `full`。不能关闭审核来绕过平台放行门槛。

## 平台阶段与恢复

[stages.py](src/document_extraction/stages.py) 提供 `ocr` 和 `review` 两个阶段，供 GPU worker 调用：

- OCR 检查点绑定输入、配置和文件摘要，记录页图与转换产物。
- 平台先将 OCR 证据提交 S3；视觉失败后可恢复这份证据，无需重新识别。
- 恢复前校验来源、配置、文件摘要和路径范围；不把其他书籍或旧配置的检查点复用为当前结果。
- 视觉缓存和修正记录保留来源绑定；仅服务健康或产生底稿不能算作机器通过。

阶段接口与 CLI 参数见 [cli.py](src/document_extraction/cli.py)。既有 `--memory-scope` 仅兼容旧命令，现役流程不使用纠错记忆。

## 输出参考

| 产物                                                      | 内容                                 |
| --------------------------------------------------------- | ------------------------------------ |
| `docling-document.json`、`docling-document.md`            | Docling 原始结构及序列化结果         |
| `docling-assets/`、`pages/`、`ocr/`                       | 图像资产、原页图和单路识别证据       |
| `conversion.json`                                         | 输入摘要、后端、页数、耗时与失败信息 |
| `document.candidate.md`                                   | 复核后的候选正文                     |
| `reviews/`、`completion.json`、`semantic-acceptance.json` | 核验、修正、原图和文字摘要及最终状态 |
| `source-map.json`、`page-boundaries.json`                 | 候选字符范围与原页的对应关系         |
| `content-readiness.json`                                  | 块级可用性、依赖和受限字段           |
| `manual-review.json`、`review-cards.json`                 | 待人工核对的内容及证据               |
| `artifact-manifest.json`、`manifest.json`                 | 产物入口与哈希绑定                   |

文件取决于运行阶段和完成状态；失败目录不保证包含完整成功清单。契约实际生成逻辑见 [artifacts.py](src/document_extraction/artifacts.py)。

Docling 使用 PDFium；表格采用 HTML 保留合并单元格，原图用于核对。布局检测分数不是 OCR 文字置信度；没有可靠页级分数时不伪造高置信度。脚注、标题和书目线索保留，但 `article_id: null`、`article_structure_status: not-claimed` 等状态不应被下游解释成已确定身份。

## 开发与验证

| 入口                                                                                                                         | 职责                         |
| ---------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| [pipeline.py](src/document_extraction/pipeline.py)、[stages.py](src/document_extraction/stages.py)                           | 转换／核验编排、检查点       |
| [docling_conversion.py](src/document_extraction/docling_conversion.py)、[ocr.py](src/document_extraction/ocr.py)             | 单路识别与模型适配           |
| [semantic_completion.py](src/document_extraction/semantic_completion.py)                                                     | 当前本地视觉校读、修正与复查 |
| [provenance.py](src/document_extraction/provenance.py)、[content_readiness.py](src/document_extraction/content_readiness.py) | 来源与可用性                 |
| [settings.py](src/document_extraction/settings.py)、[accelerator.py](src/document_extraction/accelerator.py)                 | 配置与设备资源               |

在模块目录和已安装 OCR 环境下，可运行 `.venv/Scripts/python.exe -m unittest discover -s tests`。平台 CI 另外使用轻量环境运行无需 OCR 权重的审核策略回归，实际选定文件与环境见 [CI](../../.github/workflows/engineering.yml)。

自动化行为检查不代表 OCR 准确率或真实整书审核完成。选型、历史样本修正、性能和验收报告保存在本地 `output/` 与历史资料目录，不随仓库交付，也不作为当前操作步骤。双 OCR、表格仲裁和纠错记忆已退役；历史实现通过 Git 追溯。
