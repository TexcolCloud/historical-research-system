# Ragas 离线评测

本工具冻结 `/api/v2/search` 的检索结果及其来源，再调用 Ragas 评估离线回答。它不执行 OCR、审核、入库或重新建索引。项目默认 OCR 与审核使用 [Codex 原生流程](../../../../codex-pipeline/README.md)；本工具仍直接读取旧平台的 SQL run、章节、retrieval generation 和 S3 输出，不能直接评估仅存在于原生 MCP 工作区的资料。

返回 [项目 README](../../../../../README.md) · [平台说明](../../../README.md)

## 使用前提与当前限制

需要已发布且已完成检索索引的旧平台书籍、与其 SQL/S3 数据匹配的搜索 API，以及经过独立原图开发核验的题集和参考答案。`snapshot` 会调用搜索 API，可能触发本地嵌入／重排；`run` 会调用配置的 DeepSeek 文本模型并写入 SQL/S3。两步都不是纯本地文件分析。默认 Codex 流程不依赖 DeepSeek。

Python 要求为 3.11。评测依赖应隔离于 API/worker 环境，声明见 [pyproject.toml](pyproject.toml)。Ragas 固定为 0.4.3，`langchain-community<0.4` 用于其旧 Vertex 适配器兼容；脚本关闭 Ragas 使用统计。

**当前仓库版本的独立评测锁文件需要更新。** 本目录 [uv.lock](uv.lock) 未包含平台新增的 `hrs-codex`，只读锁检查会报告需要更新。使用原锁安装的独立环境也可能在帮助阶段报 `No module named 'hrs_codex'`。这是此版本依赖快照的已知限制，不能将现有锁当作已验证可重新安装的评测环境。

从仓库根目录检查锁状态：

```powershell
uv lock --project services/research-platform/evaluation/tools/ragas --check --offline
```

以下运行命令以已经配齐当前平台与评测依赖的独立环境为前提；若锁检查失败，应先单独修复、审阅并验证依赖，再安装。不要把评测依赖直接装入正在运行的 API/worker 环境。

## 配置与输入

脚本复用仓库根 `.env` 和进程环境，优先读取 `PLATFORM_*` 配置。至少需要平台数据库 URL 和 S3 连接配置；`run` 还需要 `PLATFORM_DEEPSEEK_API_KEY` 或 `DEEPSEEK_API_KEY`。生成器使用 `reading_model`，评分器使用 `reasoning_model`，可通过 `PLATFORM_READING_MODEL`、`PLATFORM_REASONING_MODEL` 和 `PLATFORM_DEEPSEEK_BASE_URL` 覆盖。字段与兼容别名见 [Settings](../../../src/hrs_platform/core/config.py)。

| 输入 | 必需内容 |
| --- | --- |
| 题集 JSON | `run_id`、`book_id`、`generation`、`cases`、`development_review` |
| 每道题 | 唯一 `id`、`query`、`expected`；有答案题标出必需的 `chapter_id`、`start`、`end`，无答案题使用空数组 |
| 参考 JSON | `references`（题目 ID 到参考答案的映射）和 `development_review`；可选 `criteria` 提供必需事实及允许补充范围 |

`start`、`end` 是不可变章节正文中的字符范围，遵循 Python 切片 `[start:end]`，不是 PDF 坐标或字节偏移。标注应覆盖答案所需的时间、表前说明、表头和脚注，不能只截答案词。

题集的开发核验记录须有 `original_first=true`、`machine_approval=true`；参考记录还须有匹配 `fingerprint(references)` 的 `references_sha256`。提供 `criteria` 时还要求匹配的 `criteria_sha256`。哈希函数见 [outputs.py](../../../src/hrs_platform/services/runs/outputs.py)。这些字段记录已经完成的独立原图核验，不能靠填写字段取得内容审批；脚本只验证记录，不替代原图核对。

生成器只接收问句和实际检索上下文；参考答案和 `criteria` 只交评分器，不进入回答提示。

一次快照只使用同一本书、同一 run 和当前有效 generation 的题集，题目 ID 不能重复。开发机器核验、模型评分和用户人工接受是不同记录，评测结果不是封存金标或生产放行。

## 运行命令

所有 PowerShell 命令从仓库根目录执行，路径为可替换示例。先确认所选环境能导入脚本：

```powershell
$env:PYTHONUTF8 = '1'
$python = '.cache/engineering-envs/ragas/Scripts/python.exe'
$runner = 'services/research-platform/evaluation/tools/ragas/evaluate.py'
& $python $runner --help
```

`$python` 应替换为实际配齐依赖的环境路径；现有独立环境仍受前述锁问题限制。

准备好输入和服务后，先冻结检索结果，再评分：

```powershell
& $python $runner snapshot --dataset .scratch/evaluation-dataset.json --references .scratch/evaluation-references.json --output .scratch/ragas-snapshot.json --api http://127.0.0.1:18156 --limit 5
& $python $runner run --input .scratch/ragas-snapshot.json --output .scratch/ragas-report.json --concurrency 4
```

`--dataset` 可重复提供，`--limit` 允许 1–50（默认 5），`--concurrency` 允许 1–8（默认 4）。Linux 使用环境的 `bin/python`。`run` 不再次搜索，但仍需要数据库、S3 和文本模型配置。只持有快照 JSON 不足以脱离平台运行。

复评既有报告且保留原回答：

```powershell
& $python $runner run --input .scratch/ragas-report.json --reuse-answers --output .scratch/ragas-rescored.json --concurrency 4
```

`--reuse-answers` 的输入必须是含回答和原配置的报告。相同依赖指纹的已完成评分会复用；未完成项重试调用。保留新旧输出，不覆盖原始证据。

## 输出与指标

`snapshot` 逐片核对正文及来源坐标，并在开始和结束检查 generation，生成包含检索上下文、来源核验和输入指纹的 JSON。正文、章节路径、表头、脚注、表格共同范围和邻接上下文随同其所属命中保存，每个命中仍是一个排序单位。

`run` 将回答、评分、模型配置、结构化判定、提示哈希和调用错误写入报告。运行中更新本地 JSON，完成后按原题序输出。SQL 的 `stage_outputs` 保存指向 S3 不可变产物的引用：

| step 前缀 | 内容 |
| --- | --- |
| `ragas-snapshot:*` | 冻结的检索结果与来源记录 |
| `ragas-answer:*` | 按问题、上下文、模型与提示指纹缓存的回答 |
| `ragas-metric:*` | 各指标的值、理由和结构化判定 |
| `ragas-report:*` | 最终汇总报告 |

有答案题计算 Context Precision、Context Recall、Faithfulness 和 Factual Correctness。全部题计算 `historical_evidence`（1–5），检查时间、实体、统计范围、脚注归属及拒答附言；无答案题另列空检索和 `answerable=false` 数量。`complete_required_evidence` 按所有必需原文范围计算完整覆盖。

Context Precision 衡量有用证据是否靠前，不能当作普通块占比。空上下文的有答案题将检索覆盖记为 0，Faithfulness 记为不适用；非有限值记为 `null`。汇总列出实际评分数、应评分数和错误，不将调用错误当作 0 分。模型自报拒答仍需检查回答内容；同供应商生成和评分的结果需要结合独立原图核验解释。

## 测试与排障

在按根 README 安装平台 `dev` 依赖的环境中，运行无需服务或模型调用的契约测试：

```powershell
& '.cache/engineering-envs/research-platform/Scripts/python.exe' -m pytest services/research-platform/tests/test_ragas_evaluation.py
```

| 现象 | 处理 |
| --- | --- |
| 帮助阶段缺少 `hrs_codex` 等模块 | 检查独立环境和锁同步状态；此仓库版本的已知限制见前文 |
| 配置校验、SQL 或 S3 连接失败 | 核对 `.env` 与目标旧平台实例；`--api` 只覆盖搜索地址 |
| 原图核验或参考哈希不匹配 | 返回原件核验并保存对应版本的记录，不能只改哈希绕过检查 |
| generation 改变或证据与章节不符 | 确认书籍、API、SQL/S3 属于同一实例，待索引稳定后重新生成快照 |
| 报告有调用错误 | 查看逐题 `errors`；命令以非零状态退出，修复后重跑相同输入以复用已完成项 |

本地题集、参考答案、快照、报告和史料摘录不提交 Git。帮助和契约测试不等于端到端评测，也不能证明真实史料的检索与回答质量。
