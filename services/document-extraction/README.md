# Document Extraction

PDF／图片转换模块，使用 Docling 与一个配置明确的 OCR 后端生成 Markdown、原页图及来源映射。默认后端为 PaddleOCR-VL-1.6；RapidOCR 是显式替代，不自动作为第二识别器。

[项目安装](../../README.md#快速开始) · [模型准备](../../models/README.md) · [平台处理流程](../research-platform/README.md#处理流程与数据职责)

## 使用范围

本模块负责原始载体提取、内容核验、证据和待办输出，不负责正式书目身份、章节入库、嵌入或研究制卡。平台通过独立阶段入口复用它，完整工作流由 Research Platform 管理。

独立 CLI 的本地输出是可恢复的转换工作目录；平台会把业务证据提交 S3。本地 CLI 完成不等于平台已入库，也不代表全部内容通过审核。

## 安装与命令

使用独立 OCR 环境，避免与平台依赖混装。在项目根目录按 [快速开始](../../README.md#快速开始) 准备配置，再运行：

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

1. 默认先在 CPU 上检查 PDF 文字层。PDFium 检查可见 Unicode，pdfplumber 提取段落、分栏和表格；两者字符覆盖一致后保留块框、表格单元格与合并关系、物理页和原件摘要。缩进、明显段间距、多栏、不同字号与独立脚注不再单独触发 OCR。扫描、隐藏文字层、提取缺失和无法可靠定位的内容仍交 PaddleOCR-VL。
2. 依据原页边缘、数字和上下文规则清理页码噪声，记录删除范围和摘要；不一律删除所有 footer，保留表格数字、注释与署名。
3. 原生页通过证据绑定的确定性检查后标记 `native-pass`，无需 OCR 或视觉模型；不伪造识别置信度或视觉回执。有边框表格只有字符归属和行列跨度一致才能直接通过。无边框数字列、行内变字号／上标、重叠结构与图像保留原生提取和原图，进入本地视觉机审，不重复 OCR；机审未解决的问题才交人工。Docling 序列化若改变文字、段落顺序或单元格结构，则批量回退到 OCR，重新取得 GPU 租约。
4. 唯一定位的修正经独立原图复核且无未解决内容后才能写回；歧义、不可读、未定位、请求失败或复核失败保留待审。
5. 平台保护已有人工决定和草稿。整书未解决／未核验内容清空后才入库和分块。

PDF 物理页序与印刷页码分开使用，表外页码不应作为表格正文比较。没有可信细粒度坐标时只承诺定位到原页，不把占位框当作精确证据。

默认原生策略为 `native_pdf.policy=native-pdf-layout-v2`；删除此配置即保持旧全 OCR 路径，RapidOCR 配置仍使用全页 OCR。配置类接受旧 `risk_based` 与 `conversion_only`，用于既有产物与显式 CLI 配置；平台 `stages review` 仍要求视觉开启且为 `full`，该模式核验所有未通过原生规则的页。不能关闭审核来绕过平台放行门槛。

原生放行验证字符覆盖、可见性、几何范围和输出结构，不以字号、缩进或分栏本身判失败。图像以原页裁剪保留，输出使用可移植的相对资源路径。该规则不保证发现字体自身的错误字符映射，也没有真实书籍的免审率承诺。冻结运行中的 `native-pdf-simple-text-v1` 保持原判定，避免旧检查点被追溯放宽；所有该版本运行和可恢复检查点退出后才能移除此兼容路径。Paddle 跨页结构处理仅在连续 OCR 页段内执行，表格组 ID 包含该段起始物理页，不跨过原生页合并。序列化不一致页会在同一轮收集后批量回退，避免逐页重跑整书。

## 平台阶段与恢复

[stages.py](src/document_extraction/stages.py) 提供 `ocr` 和 `review` 两个阶段，供 GPU worker 调用：

- v2 OCR 检查点绑定输入、提取配置指纹和文件摘要，记录页图、原生提取证据与转换产物；审核参数变化不使已有识别失效。
- 平台在提取开始前将该运行的配置冻结到 S3，重试和恢复继续使用该配置。旧 v1 检查点保持全量视觉审核，不能因新默认配置而免审。
- 平台先将 OCR 证据提交 S3；视觉失败后可恢复这份证据，无需重新识别。
- 恢复前校验来源、提取指纹、文件摘要和路径范围；不把其他书籍的检查点复用为当前结果。完成进度包含规则通过的原生页与已完成首轮机审的 OCR 页，进度不代表最终放行。
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
| [native_pdf.py](src/document_extraction/native_pdf.py)、[native_layout.py](src/document_extraction/native_layout.py) | PDFium 文字层验证、pdfplumber 布局提取、原生证据分流 |
| [semantic_completion.py](src/document_extraction/semantic_completion.py)                                                     | 当前本地视觉校读、修正与复查 |
| [provenance.py](src/document_extraction/provenance.py)、[content_readiness.py](src/document_extraction/content_readiness.py) | 来源与可用性                 |
| [settings.py](src/document_extraction/settings.py)、[accelerator.py](src/document_extraction/accelerator.py)                 | 配置与设备资源               |

在已安装 OCR 环境中安装 `dev` 组后，运行 `.venv/Scripts/python.exe -m pytest tests`。混合 PDF 集成测试使用真实 PDFium／Docling 与模拟识别、审核响应，不加载模型。平台 CI 另外使用轻量环境运行无需 OCR 权重的原生分流与审核策略回归，实际选定文件与环境见 [CI](../../.github/workflows/engineering.yml)。

自动化行为检查不代表 OCR 准确率或真实整书审核完成。选型、历史样本修正、性能和验收报告保存在本地 `output/` 与历史资料目录，不随仓库交付，也不作为当前操作步骤。双 OCR、表格仲裁和纠错记忆已退役；历史实现通过 Git 追溯。
