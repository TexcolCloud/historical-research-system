# 复验入口与证据

验收规格来自 [已确认方案](../../../.scratch/document-retrieval/issues/08-acceptance.md)。真实质量、工程夹具、Linux 实际运行和容量分别记录。容量测试已按用户要求暂停；其余必需场景继续执行。

## 固定输入与比较

原始代表集 `datasets/retrieval-representative-20260908-v6` 包含 21 个来源家族和 60 个固定任务，其中校准 20 个、留出 40 个。`retrieval-representative-20260909-v7` 在原件复核后纠正了重复附注和无关表格的参考语境，保留原问题和所需事实；这些任务已见，后续运行属于回归验证。评分更正必须同时应用于旧运行，不能只改新结果。`lock.json` 绑定任务、参考、语料和输入缺口哈希；划分按来源家族进行。

`retrieval-refresh-20260909-v1` 单独登记两个未参与前轮查询的来源家族、12 个新任务和事先确定的通过线。新来源原件与冻结输入先经 GPT 核对，合格范围经入库公开接口发布，随后锁定任务与参考，最后才能查询。原 PDF、采用范围、用途合格范围和索引覆盖分别登记，不能互相代替。

`preflight.json` 保留 3 个由冻结输入缺陷引起的原任务缺口及运行前替换理由。它们没有被算成检索成功。全部来源与候选审阅标为 GPT 机器记录，不代表用户史料审定或 sealed gold。

校准比较 BGE／BGE、关闭重排、单独更换 Qwen 重排与单独更换 Qwen 嵌入。随后比较一个较长片段候选和一个较大返回预算。`development/model-selection-v1.json`、`chunk-selection-v1.json`、`budget-selection-v1.json` 保存选择理由；`final-configuration-lock-v1.json` 在留出检索前锁定 BGE／BGE、384/768/64 和 6/128/2000/4000。

## 质量和完整读取

在模块目录使用独立环境。先启动配置一致、固定来源已全部就绪的服务，再为每次实际运行选择新的 run ID：

```powershell
.venv/Scripts/python.exe -X utf8 evaluation/tools/run_gpt_quality.py --dataset evaluation/datasets/retrieval-representative-20260909-v7 --run-id <新回归run-id> --instructions <本批GPT决策.json>
.venv/Scripts/python.exe -X utf8 evaluation/tools/score_quality.py --dataset evaluation/datasets/retrieval-representative-20260909-v7 --run-id <新回归run-id>
.venv/Scripts/python.exe -X utf8 evaluation/tools/full_read_baseline.py --dataset evaluation/datasets/retrieval-representative-20260909-v7 --run-id <新全文对照run-id> --split holdout --caller-task-prefix <所比较的回归run-id>
.venv/Scripts/python.exe -X utf8 evaluation/tools/compare_tool_volume.py --dataset evaluation/datasets/retrieval-representative-20260909-v7 --retrieval-run <新回归run-id> --full-run <新全文对照run-id>
```

`run_gpt_quality.py` 执行当前 GPT 根据任务和实际公开响应记录的逐步决定。每批 JSON 指令在执行前按哈希归档；后续决定只能使用已返回的引用和游标。每个任务最多两次新检索、四次继续／展开，共六次内容调用；媒体字节单列。同一固定来源集合共用保留和已经读到的内容，结束最后一个问题时释放。完成任务不能重写，源码与驱动在同一运行中不能变化。

参考锚点由后续评分器读取，不能反过来替运行中的 GPT 选择答案。原始请求、最终正文、响应头、独立代理计数、每次决策和错误均保留。相同 GPT 建立参考并审阅候选，必须明记为非独立盲审。`run_quality.py` 是确定性策略驱动，可用于校准和诊断；不能把它的启发式选择称作生成式 Agent 决策。

全文基线对相同固定来源集合完整读取一次。同一来源集合下的连带问题共用一次全文，不能按问题重复全文量。对照必须同时报告证据完整性、失败、按会话合并的量和预先登记的长篇子集。它测量完整序列化的取证工具正文，包括字段、状态、等待、保留和释放；宿主提示、对话重放、缓存及图像输入量未知。即使取证由当前 GPT 决策，也不能据此宣称已测完整生成式制卡流程。

`--caller-task-prefix` 使完整读取与检索返回的调用方会话标签完全相同，避免管理字段长度影响小幅差值。它不改变读取范围或工具调用计数。

若已受理搜索仅在等待结果时发生连接超时，先保存原始失败，再用 `resume_gpt_search.py --run-id <运行> --task-id <任务>` 读取同一作业的状态与结果。该辅助程序不创建新搜索，并保存恢复前状态和响应哈希。评分器默认仍将所有已记录错误判为失败；显式追加 `--version recovery-v2 --recover-existing-jobs` 才识别经哈希和原作业／列表／范围证明的恢复。原错误、原评分及无恢复证明的失败均保留。规则、执行版本归档和对旧运行的一致复评见 [计量与恢复评分补充说明](development/evaluation-method-addendum-20260909-v1.json)。

机械评分后由当前 GPT 回看原件依据和实际首批／展开内容，追加结构化候选判定。未覆盖的候选不能直接标为不相关。已知参考覆盖也不能称为全库召回率。留出结果导致的参数调整必须按规格补充新来源家族和至少 12 个未参与调整的任务；旧留出结果继续保留为已见回归证据。

## 工程与恢复

`engineering_fixture.py` 建立有明确 synthetic 标签的来源，经入库公开草稿、预览和应用发布。它使用一像素工程图片验证字节和用途流程，不能证明历史文字或图像质量。

```powershell
.venv/Scripts/python.exe -X utf8 evaluation/tools/engineering_functional.py --run-id <新工程run-id>
.venv/Scripts/python.exe -X utf8 evaluation/tools/engineering_changes.py --run-id <同一工程run-id>
.venv/Scripts/python.exe -X utf8 evaluation/tools/engineering_faults.py --run-id <同一工程run-id> --groups partial_vectors,engine_kill,reply_kill,stage_errors,engine_missing
.venv/Scripts/python.exe -X utf8 evaluation/tools/engineering_background.py --run-id <同一工程run-id>
.venv/Scripts/python.exe -X utf8 evaluation/tools/engineering_faults.py --run-id <同一工程run-id> --groups retention,maintenance_outcomes,paired_recovery
```

这些工具使用独立的状态目录、索引前缀和端口 `18131`，默认读取当前已登记的 `state/engineering-protocol-20260908-v2/fixture.json`。从空环境复建时，先按夹具工具参数创建并传入新的 `--fixture`，保留新的上游回执。上游采用／用途情景应串行运行；不要同时启动或重启同一个控制服务。

故障工具会真正终止自己登记的检索进程，并执行隔离状态的实际清理和停服恢复。故障暂停／模拟时钟只存在于 `fault_server.py`，不作为生产接口暴露。失败后先检查保存的故障标记与实际作业，取消或重试相同对象；旧失败报告不覆盖。定向暂停不能算作真实进程终止，只有保存了外部终止与重启回执的两个场景作此声明。

`runs/engineering-protocol-20260908-v1/gpt-engineering-review-v1.json` 汇总 18 份实际观察与 11 个场景组的对应关系。真实长材料、真实媒体和混合负载另有必需证据，不能用工程 fixture 替代。

## 性能与 Linux

最终固定数据与配置完成后，以 `run_performance.py --help` 查看参数，传入对应 `--config` 和该实验服务实际 PID。它会验证进程归属后重启服务，单列首次模型请求，并以四个并发客户端执行 20 个固定请求的三轮热运行，同时推进一个真实来源后台重建。使用最终预算参数 `--budget 128 2000 4000`，避免回落到脚本的较大实验预算默认值。运行期间停止其他本模块实验进程，保留共享宿主资源说明。

本次首轮有一次真实 900 秒客户端等待超时，原作业后来完成。`resume_performance.py --run-id final-mixed-20260909-v1` 的已执行接续保留原轨迹，领取同一作业结果并执行尚未运行的两轮；不要在已存在 `continued/` 的目录重复启动它。`performance-recovery-v2.json` 分别记录全部 60 个服务作业、59 次正常客户端完成和一次恢复，后两轮发生在后台完成以后。正常完成延迟不能掩盖超时，恢复前的人工诊断间隔也不能当成服务计算耗时。

`profile_model_timing.py --performance-run <已完成混合run-id> --run-id <新计时run-id>` 另执行一个冷请求和三个既有查询的三轮顺序热请求；接续结果需加 `--performance-summary performance-recovery-v2.json`。它按原搜索作业合计各批嵌入／重排调用耗时。该补测没有后台负载，不能冒充混合运行的模型延迟分布。`fault_server.py` 的计时只存在于评估包装中；交付时恢复普通 CLI `serve` 进程。

Linux 使用 `config/Dockerfile` 和 `config/linux.example.toml`。模型放入只读 `/models`，状态目录挂载到 `/data`；`18132` 映射至容器 `18130`。本次 Linux 夹具约定容器名 `historical-retrieval-linux-acceptance`，宿主状态目录 `state/linux-acceptance`，以便核对 Linux 原生 CLI 的中文输入／输出路径。

```powershell
.venv/Scripts/python.exe -X utf8 evaluation/tools/linux_minimum.py run --run-id <新Linux-run-id>
.venv/Scripts/python.exe -X utf8 evaluation/tools/linux_minimum.py verify-media --run-id <同一Linux-run-id>
docker restart historical-retrieval-linux-acceptance
.venv/Scripts/python.exe -X utf8 evaluation/tools/linux_minimum.py verify-restart --run-id <同一Linux-run-id>
```

运行前用公开索引作业构建其两个固定真实来源，并等待容器启动验证完成。六个任务覆盖精确、兼容、混合，混合任务断言实际向量与重排执行；还核对 HTTP、Python 工具、Linux 原生 CLI、原件流式字节、ETag／Range 和重启后的旧列表锚点。Linux CPU FP32 证据不等于 Linux GPU 性能或独立 Linux 主机容量。

## 自动验证与归档

```powershell
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m ruff check src tests evaluation/tools
.venv/Scripts/document-retrieval.exe export-contracts contracts
```

`runs/<run_id>` 中的历史报告不覆盖。最终汇总绑定源码、依赖、配置、数据集、规则／模型指纹与证据文件哈希；逐项区分本次执行和经影响分析承接的记录。未执行、失败与容量暂停不能计为通过。设计接受、实现完成、机器验收和用户最终接受是分别记录的状态。
