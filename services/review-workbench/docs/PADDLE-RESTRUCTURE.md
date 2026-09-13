# Paddle 格式化与跨页重构接入

2026-09-10，用户授权开启并接入原流程。复用现有 PaddleOCR-VL-1.6，不新增识别器、不改变模型权重。

## 现役流程

1. Paddle 构造参数 `format_block_content=True`。JSON 块的格式化结果另存 `formatted_block_content`，
   原始块内容继续保存在 `block_content`，完整 provider 结果随 OCR 证据保存，供缓存恢复后重构。
2. Docling 收齐全部物理页后调用现有 Paddle 实例的
   `restructure_pages(merge_tables=True, relevel_titles=True, concatenate_pages=False)`，不再次推理。
3. 标题层级只在当前 Markdown 中能唯一对应原标题时应用，保留 before/after、来源块编号；
   无法对应只记录未应用，不猜位置。然后进入原有 DeepSeek 审核。
4. 跨页合并表写入 `paddle-restructure.json`，保留成员页、矩形、原表 HTML、文本及图像摘要。
   原逐页表格继续作为规范 Markdown、审核编辑和研究输入，续页内容不会被清空或错标为前页来源。
5. 原有错误局部拦截、人工放行、目录交接继续适用。重构关系进入来源映射、审核上下文、入库分件草稿及 DeepSeek 分件辅助。

Paddle 的内置合表会把续页内容移入前页并清空续页块，即使 `concatenate_pages=False` 也如此。
本接入保留这份真实合并输出作为**逻辑表候选**，不直接将其替换进物理页底稿。
因此它支持跨页关联和合并对照，但不是“整张合并表统一编辑、自动分摊回各原页”的编辑器。
也不把高分检测框、标题级别或合表成功当成内容/篇目审核通过。

## 合同与审核台

- `manifest.paddle_restructure` 指向 `paddle-restructure.json`，包含在证据哈希与目录交付中。
- `source-map.spans[].restructure_evidence` 包含标题记录、跨页表组及文本绑定；`stale=true` 表示当前正文已改变。
- 工作台 `/jobs/{id}/result?page=N` 增加 `cross_page_tables`。合并候选仅用于结构对照，包含物理页跳转按钮。
- 来源页最终文本与合并时的文本摘要不同，就隐藏旧合并 HTML。尚未提交的编辑草稿不计入预览。
- 显式修订不重新跑 Paddle/OCR，保留原重构审计产物；因此旧候选可能过期，需以后显式重新生成才可恢复。
- 格式化结果不混入原始文字定位；派生标题不冒充独立文章边界。
- 入库辅助提示版本为 `partition-assistance-3-restructure-evidence`；文档审核策略版本为
  `docling-single-draft-semantic-v3-restructure`，旧模型缓存不会冒充新提示结果。

重构失败、页数/块身份不一致时转换失败并保留已有原页产物，不以不完整重构结果自动入库。
旧历史产物不自动回填。新适配器代码影响既有代码指纹缓存，显式重试时按正常缓存规则执行。

## 验证与限制

使用本机 PaddleX 3.7.2 的真实后处理函数，对合成双页表格、标题做工程测试；没有初始化 OCR 模型，
没有调用 DeepSeek/GPT API，没有改动真实任务数据库。另验证实际 Docling 入口在审核前调用重构、
产物和来源映射保真、入库分件回归、旧合并预览失效，以及独立浏览器的展开/跳页/过期状态。
这些验证不代表真实史料的合表或标题准确率验收。

工程测试：后端 105 项通过；前端 37 项通过，TypeScript/Vite 构建通过。
浏览器证据位于 `output/playwright/restructure-20260910/`，视觉结论只作为开发机器评估。
本轮没有重启长驻服务；使用新 PDF 测试前需先重启工作台转换后端及入库 API/worker。
