# Document Extraction Service

2026-09-13：转换在视觉审核前剔除完整原页边缘的独立数字页码，审核与导出使用同一清理底稿。使用共享 `page-layout-v2` 规则，保留原 OCR、扫描图和 `layout_cleanup` 删除范围/哈希记录；不再把所有 footer 文字当作噪声，表格、注释署名与正文数字保留。视觉核验区分 PDF 物理页序和印刷页码，独立初读与表格数字比较排除表外页码/表号，并保留单元格边界。见[章节与页码说明](../../docs/ebook-reader-20260912.md)。

将 PDF 或页面图片交给 Docling 转换，输出 Markdown、结构化文档和可回查的原页证据。
Docling 负责文件解析、分页、页面渲染、版面结构和文档序列化。每次仅启用一个识别后端，
默认先完成整书 OCR，再由本地 Qwen3-VL-8B-Instruct Q4_K_M 对照原图核验。
机器发现的实质错误先生成唯一定位的修正，再对修正后的整页进行原图核验；复核完成且无未解决问题时自动写回，保留原文、修改记录和机器核验凭据。读不清、定位不唯一或复核不通过的部分仍交人工，已有人工决定和草稿不会被覆盖。待审和未核验内容全部处理完成后，整书才能入库和分块；审核期间的内容定位块仅用于审阅，不触发下游检索分块。
文档与制卡视觉核对共用一个本地服务，显存调度和安装见[本地视觉运行说明](../../docs/local-vision-20260912.md)。

默认选择 **Docling + PaddleOCR-VL-1.6**，使用本机已有的 0.9B 权重及 transformers 引擎。
`config/default.json` 只启用这一条识别路径。
PaddleOCR-VL 通过页面预测适配器接入 Docling VLM 管线，共用输入、序列化、复核和输出接口。
`config/rapidocr.json` 保留 Docling 标准管线的显式替代配置：RapidOCR、torch、整页 OCR，
使用本机 PP-OCRv6 small 权重；不自动调用它补读或仲裁。
Docling 是转换框架，本次比较的是框架中的两种实际识别后端。

已开启 Paddle 块格式化与文档级 `restructure_pages()`（跨页表格合并、标题层级重建；不拼接物理页）。
标题应用到可唯一定位的 Markdown 标题；合并表作为有逐页来源的逻辑候选进入审核台与入库证据，
原页表格仍作为正式编辑底稿。详见[接入行为和限制](../review-workbench/docs/PADDLE-RESTRUCTURE.md)。

## 运行

在项目根目录执行：

```powershell
# 安装：保持现有 Windows 部署方式和模型目录
.\deploy\windows\bootstrap.ps1

# 验证当前选中的转换管线及模型
.\services\document-extraction\.venv\Scripts\history-extract.exe check

# PDF 或单张页面图片
.\services\document-extraction\.venv\Scripts\history-extract.exe extract input.pdf `
  --output output\input

# 递归处理 PDF，同一进程复用转换器
.\services\document-extraction\.venv\Scripts\history-extract.exe extract-batch input-dir `
  --output-root output\batch

# 切换唯一识别后端；配置参数置于子命令前
.\services\document-extraction\.venv\Scripts\history-extract.exe `
  --config services\document-extraction\config\rapidocr.json `
  extract input.pdf --output output\input-rapidocr

# 核对候选文本的精确来源；不调用模型
.\services\document-extraction\.venv\Scripts\history-extract.exe `
  verify-quote output\input --quote "候选中逐字存在的引文"
```

重复引文使用 `--start` 指定最终 Markdown 的 Unicode 字符偏移；可用
`--markdown-sha256` 核对版本。保留原 CLI，包括 `--env` 和 `--memory-scope`；后者仅兼容
旧命令，不执行操作。批次失败项写入 `batch-summary.json`。

Docling 已列为核心依赖，锁定 `2.123.0`。适配器使用该版本的 VLM 扩展接口；升级时运行
PDF／图片转换及失败恢复测试。模型路径仍统一在根目录 `models/`：`docling/`、`paddleocr/`、
`huggingface/`、`torch/` 和现有表格模型目录，未迁移权重。单路流程不会启动旧第三路 OCR
或额外表格仲裁器；`check` 也不要求这些未启用的模型。

## 配置与接受标准

根目录 `.env` 保留现有 `HRS_MODEL_ROOT`、`HRS_OCR_DEVICE`、`HRS_MAX_VRAM_GB`、
`DEEPSEEK_API_KEY`、`DEEPSEEK_VISION_MODEL`、API 模式及端点设置。密钥不写入配置文件。
生产视觉复核使用本地 Qwen 8B，不回退 DeepSeek。GPT 只参与开发评估，其记录不进入生产放行逻辑。

接受繁简、标点和不改变含义的文字规范化；保留人名、地名、日期、数量、论断、否定、
因果、引语及注释关系。旧页审模式下，DeepSeek 对风险路由选中的页面逐页对照原图，邻页只提供上下文。修正必须绑定唯一底稿
片段与明确的原图读数，修正后检查最终文本及受影响邻页，不再进行双路候选选择。

普通书目著录问题标为局部警告，限制该块的书目字段；改变关键事实或引用归属的问题
仍需证据。缺少 API、复核失败或原图仍不可读时保留底稿和待审记录，不自动判为通过。
`risk.enabled` 和 `risk.auto_accept` 控制自动接受，单路流程不使用旧相似度阈值。
生产机器通过仍不是人工定稿，也不证明 OCR 引擎的整体准确度。

**旧页审模式按 DeepSeek 最终错误标记局部拦截。** 完整核验后未标错的 HTML 表格和组织图无需人工审核，
只有实际错误或未核验的内容标为局部 `needs-evidence`；自动修正仍不会改写表格内的内容。
`review-cards.json` 给出原页图、识别文本、块 ID 和字符范围。同页合格正文及实质脚注仍可用，
文档/页面的待审汇总状态不等于所有文字都不可用，下游应读取块级 `content-readiness.json`。
2026-09-10 风险分流策略为 `risk-routed-errors-only-block-v1`，兼容旧 `deepseek-errors-only-block-v1`；不回写旧产物。详见
[工作台按块放行说明](../review-workbench/docs/DEEPSEEK-RELEASE-POLICY.md)。下方历史样本结果保留当时策略。

### 当前全页核验与历史归档模式

`config/default.json` 的 `vision_review.review_mode` 现为 `full`，新上传任务在 OCR 后进行本地全页视觉核验。以下 `conversion_only` 行为仅解释仍冻结旧配置的既有任务：
页面标记 `deferred-article`、`verified=false`；交付状态为 `conversion-completed`、
`content_status=unreviewed-source`、`review_status=not-reviewed`。
`conversion-archive-article-review-v1` 允许完整且可校验的来源归档，不表示内容或结构已通过。
正文保留未审核警告，表格仍可局部待核；归档门检验路由与证据绑定，不靠关闭审核伪造通过。
工作台新任务冻结 `full`；已有模式的任务保持原配置，没有模式字段的既有任务继续原 `risk_based`，不批量迁移。
文章候选的程序关联、定向 DeepSeek 补核与人工修订见
[本轮实现和验证](../../docs/note-workflow-20260911.md)。现有运行进程需在空闲时加载新代码。

显式配置 `risk_based` 保留旧页审；`full` 保留全页核验。以下仅描述旧模式：
`confidence_threshold` 默认 `0.98`，仅接受明确标注为页级识别置信度的分数，不接受版面检测分数。
只有达到阈值、版面证据完整且无风险的简单内部页可规则放行；首尾页、标题/注释、表格/图、
复杂布局、稀疏/乱码/重复文本、识别与导出不一致、低分或未知置信度页面仍送 DeepSeek。
这是保守筛查规则，阈值尚未经真实样本校准，不是内容无误保证，也不保证识别所有漏字。

当前 PaddleOCR-VL 适配器没有输出可靠的页级识别置信度，旧 `risk_based` 会因未知分数送审。
默认 `conversion_only` 不以缺分数触发全文审核，也不生成替代分数。OCR 与模型配置未改动。
选中页被修正后，原先规则通过的受影响邻页也转入最终核验。人工放行直接保留人审决定；
显式提交修订仅重核涉及页，其他页保持原回执，同页未提交块继续由审核范围合并逻辑保护。

分流及前端字段、工程测试和部署限制见[风险分流说明](../review-workbench/docs/RISK-ROUTED-REVIEW.md)。

### 版面检测证据传入库

Paddle 返回的 `layout_det_res.boxes[].score` 现独立保存在
`ocr_evidence.metadata.layout_evidence`，并传入 `source-map.spans[].layout_evidence`。
包括区域标签、矩形、检测分数、图像摘要和坐标框架，供入库分件草稿及 DeepSeek 分件辅助使用。
它是区域检测置信度，不是文字识别置信度，也不证明标题是独立文章开头。
缺分数、停用检测器产生的整页占位框不会被伪造为高置信度；未经坐标变换校验的框不声明为原图坐标。
本次复用已有模型输出，不更换模型、不新增识别调用、不自动回填历史产物。

`source-map.spans[].block_text_hints` 仅为原块文字在导出文字中唯一精确出现时提供字符范围；
重复或找不到的块仍保存原文字摘要与版面信息，范围留空。定位可靠粒度为实际原页，
未证明旋转/裁切逆变换时不绘制原图精确高亮。原始 Markdown 不按脚注、页码或边注标签过滤。

## 输出及边界

| 文件 | 用途 |
| --- | --- |
| `docling-document.json`、`docling-document.md` | Docling 原始结构及序列化结果，修正前保留 |
| `docling-assets/`、`pages/page-NNN.png` | Docling 引用资产与完整原页图 |
| `conversion.json` | 输入哈希、框架版本、解析器、页数、耗时及失败记录 |
| `ocr/` | 单路识别初稿；Docling 标准管线保存其转换文本，Paddle 另保留原始识别数据 |
| `document.candidate.md` | 语义复核后的候选 Markdown |
| `reviews/`、`completion.json`、`semantic-acceptance.json` | 原图及文本哈希、模型原始响应、修正记录和最终状态 |
| `source-map.json`、`page-boundaries.json` | 最终候选字符位置到物理页及初稿证据的映射 |
| `content-readiness.json` | 块级可用性、跨页依赖与受限字段 |
| `artifact-manifest.json` | 原页资产路径及哈希 |
| `manual-review.json`、`review-cards.json` | 需要补证的内容 |
| `manifest.json` | 兼容现有 v4 提取包的入口及证据哈希 |

PDF 使用 Docling 自带的 PDFium 解析器。本机保留的旧期刊样本在默认解析器下出现过
页面数字和英文摘要渲染缺失，因此明确配置 PDFium，不自行实现 PDF 分割或渲染。
Docling JSON 保留其产生的结构和坐标；没有可信细粒度坐标时仅承诺定位到原页。
Markdown VLM 输出转换出的占位框不能当作精确定位证据。
Paddle 导出启用 `markdown_ignore_labels=[]`，避免忽略带史实的脚注，
并启用同一模型的图片内文字识别。Docling 加载 Paddle 已保存的图片资产，
不从零面积占位框裁图；表格采用 Docling HTML 序列化器保留合并单元格，供人工回查。

本模块输出原始载体内容；史料分件、文章题名和作者的最终归属由文档入库模块负责。
转换阶段保留标题、参考文献、续页标记和页观察，不推断整篇文章归属图。
`article_id: null`、`article_structure_status: not-claimed` 是明确的接口状态；
`bibliographic_identity_verified: false` 和受限字段必须由下游保留。
文本有来源不等于已确认历史论断或书目身份。本模块不生成嵌入或史料卡。

识别异常可重试一次；未识别的页仍保留原图并继续后页。Docling 本身失败或缺页时保存
`conversion.json` 和已生成资产，失败重跑会移除旧成功清单，避免误用历史结果。
Paddle 初稿缓存绑定图像、后端及实现指纹；DeepSeek 缓存绑定原图、完整提示和模型。
Docling 标准转换目前每次重跑，复核缓存仍可复用。

## 验证与历史

在本服务目录执行 `python -m unittest discover -s tests`。
当前版本通过 **38 项现役测试**，覆盖单路转换、局部表格待审、复核请求、缓存、
失败恢复及引用溯源。默认配置的 `check` 在本机只加载 PaddleOCR-VL 并通过。
精简依赖后的真实两页 PDF 完整提取也通过，正文及原页图片与先前已审版本逐字节一致。
旧双路测试随退役实现归档；清理前的 694 项测试数不作为现役测试数。
2026-09-10 的同源 21 页复测中，两条路径均完整导出；排除交人工的图表后，
最终候选中观察到关键语义/阅读顺序缺陷的页数为 Paddle 1 页、RapidOCR 4 页。
包括转换、DeepSeek 复核和初始化的耗时分别约 559 秒、93 秒；Paddle 更慢，但正文语义更稳。
样本为已使用过的诊断材料，不能据此推算全库错误率。

选型后的 21 页示例包保留在 `output/pdf/ocr-selection-20260910/selected/paddle/`。
其中旧扫描件的一处严重漏字另经开发原图校读修正，原失败、校读证据及生产复核均保留，
不能计入自动 OCR 成绩。DeepSeek 仍有漏检和把轻微问题列为待审的现象；未用 GPT 开发审批
强行解除生产待审。表格和组织图的两个内容块保持人工审核。

[OCR 选型报告](../../output/pdf/ocr-selection-20260910/OCR选型报告.md) 保存条件、逐页证据和选型后的状态。
[重构记录](../../output/pdf/single-ocr-refactor-20260910/Docling转换模块重构报告.md) 保存此前框架重构的范围与证据。

## 现役代码与工作区

| 文件 | 职责 |
| --- | --- |
| `cli.py`、`pipeline.py` | 命令入口、单转换器生命周期、批次复用及处理顺序 |
| `docling_conversion.py` | Docling PDF/图片转换与模型适配 |
| `ocr.py` | Paddle 识别和绑定原图、模型及代码的初稿缓存 |
| `semantic_completion.py` | DeepSeek 请求、语义校读、一次修正及最终复查 |
| `artifacts.py` | v4 提取包、块级可用性和人工审核卡导出 |
| `provenance.py`、`content_readiness.py` | 原页来源映射和精确引用校验 |
| `settings.py`、`accelerator.py` | 配置、模型目录和设备资源 |
| `models.py`、`utils.py`、`__init__.py` | 产物数据结构、文件辅助函数及包入口 |

2026-09-10 清理将源码从 37 个文件、37,310 行缩减到 13 个文件、2,353 行。
旧 PDF 渲染器、双 OCR 比选、表格多轮仲裁、纠错记忆及其专属配置和评估工具已退出现役代码。
`single_ocr.py` 的编排和导出分别并入 `pipeline.py`、`artifacts.py`；共用函数直接归入其使用模块。
依赖锁文件去除了 GMFT、python-doctr、PyMuPDF 等旧路径专用依赖，部署脚本使用 `paddle` extra。

临时诊断目录、重复嵌套工作目录、旧评估产物和旧复核缓存移至项目根目录的
`output/document-extraction-history-20260910/`。原 `output/`、`evaluation/runs/` 仅为 Windows
目录联接，保留其他模块及历史记录依赖的证据路径。现役缓存保留在
`state/vision-review-cache/single-draft/`。模型权重和已选定的 21 页示例包保留原位。

清理后重放 7 份文档的 21 页，91 个提取产物与清理前逐字节一致；这项验证证明重构等价，
不产生新的 OCR 准确率结论。完整源码快照、文件清单及历史证据哈希见
[工作区清理记录](../../output/document-extraction-cleanup-20260910/模块清理记录.md)。
