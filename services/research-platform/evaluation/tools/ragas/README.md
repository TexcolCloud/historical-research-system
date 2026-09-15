# Ragas 离线评测

复用 `/api/v2/search`，冻结最终返回的章节路径、正文、表头、脚注和邻接上下文，再运行 Ragas 0.4.3。依赖安装在独立环境，不改变 API/worker 环境或现有索引。无需重新 OCR、机审、入库或嵌入，也不会启动制卡。

## 安装与运行（PowerShell）

从仓库根目录运行，复用 `.env` 中的 SQL、S3 和 DeepSeek 文本配置。

先准备已发布、已完成索引的书籍、可访问的 API，以及按下节格式核对过的题集和参考答案。本工具不会生成可信题集或自动补齐原图审批；`snapshot` 可能触发本地嵌入／重排，`run` 会调用付费文本模型。这里只提供可替换的路径示例，不依赖某一轮私有 `.scratch` 文件。

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$PWD/.cache/engineering-envs/ragas"
$env:PYTHONUTF8 = '1'
uv sync --project services/research-platform/evaluation/tools/ragas --locked

$runner = 'services/research-platform/evaluation/tools/ragas/evaluate.py'
$python = '.cache/engineering-envs/ragas/Scripts/python.exe'
& $python $runner snapshot --dataset .scratch/evaluation-dataset.json --references .scratch/evaluation-references.json --output .scratch/ragas-snapshot.json
& $python $runner run --input .scratch/ragas-snapshot.json --output .scratch/ragas-report.json --concurrency 4
```

`--api` 可指定服务地址，`--limit` 默认返回 5 条。`--dataset` 可重复提供同一书籍运行、同一有效 generation 的题集，不能一次混合多本书。Linux 使用环境的 `bin/python`。若输入已存于 S3，可先通过现有 `Outputs.get` 导出对应 `ragas-snapshot:*`，直接运行第二步。实际题集与报告不随代码提交。

Ragas 0.4.3 仍引用旧 Vertex 适配器，所以固定 `langchain-community<0.4`，精确版本由本目录的 `uv.lock` 管理。默认关闭 Ragas 使用统计。

## 输入和评测边界

- 题集沿用已有 `run_id`、`book_id`、`generation`、`cases` 和 `development_review`。每题保留 `id`、`query`、`expected` 来源坐标；无答案探针的 `expected` 为空。
- 参考答案文件为 `{"references":{"题目ID":"参考答案"},"development_review":{...}}`。机器核验记录需要 `original_first`、`machine_approval` 和匹配 `fingerprint(references)` 的 `references_sha256`。评测工具验证记录，不能代替原件核对或生成审批。
- 原图核验和参考答案都属于开发机器核验，不是用户人工审核、封存金标或生产放行。
- 检索结果逐片检查正文和来源坐标；开始和结束检查 generation。一次评测使用同一快照，避免运行中索引变化影响比较。
- 生成器只看到问题和真实检索上下文；参考答案只给评分器。每条检索结果及其关联脚注、表头作为一个排序单位，不把辅助上下文拆成额外的高排名结果。
- 生成器使用 `reading_model`，评分器使用 `reasoning_model`，复用现有 DeepSeek 文本配置。这里增加的是离线问答探针，不能冒充已上线的问答产品。相同供应商的模型评分可能相关，需结合独立原图开发核验分析。

## 指标及续跑

有答案题计算 Context Precision、Context Recall、Faithfulness、Factual Correctness。所有题额外计算 Ragas `DomainSpecificRubrics` 的历史证据评分（1–5），检查时间、实体、共享统计范围及拒答附言；它是模型评分，不能代替原图复核。无答案题单独统计空检索和生成器的 `answerable=false`；该字段仍需抽查回答内容，不能单凭模型自报认定拒答正确。

参考文件可增加 `criteria: {"题目ID":"必须保留的事实及允许补充范围"}`，并在开发核验中提供 `criteria_sha256=fingerprint(criteria)`。规则只给评分器，不进入回答提示。`complete_required_evidence` 根据所有必需原文范围统计完整覆盖；标签必须包含时间前导段和表前说明，不能只标答案词。

对旧报告使用 `run --input old-report.json --reuse-answers --output rescored.json` 可保留原始回答，仅按相同新规则复评；原四项指标按固定输入缓存复用。新旧结果均需保留，不覆盖 S3 历史产物。新增题先核对原图、冻结问句和参考，再检索；同书新页段不能称为跨书泛化测试。

Context Precision 衡量有用证据是否靠前，不能当作普通“有用块占比”。空上下文的有答案题将检索覆盖记为 0，Faithfulness 记为不适用。非有限分数记为 null；调用错误记录类型与原因，不按 0 或成功处理。汇总同时显示实际评分数和应评分数，存在调用错误时命令以非零状态退出。

每个回答和每项指标分别以输入、模型和版本指纹存入 S3 `stage_outputs`：`ragas-answer:*`、`ragas-metric:*`、`ragas-report:*`。记录 Ragas 的结构化判定和提示哈希。重复运行相同输入复用已完成项；失败项会重新调用。`.scratch` 输出只是方便阅读的本地副本，原始史料与题目不进入 Git。

参考：[Context Precision](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/)、[Context Recall](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_recall/)、[Faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)。
