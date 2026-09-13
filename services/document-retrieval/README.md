> 2026-09-13：此独立后端已退役。现役实现、运行命令和内容规则见 [Research Platform V2](../research-platform/README.md)。下文仅为历史说明；旧代码和打包入口已存档，不参与部署。现有存储容器配置及业务文件保留原位，不迁移。

# 史料索引与检索

本模块从入库服务的公开固定输入建立 OpenSearch 文字／向量索引，用 SQLite 保存作业、索引代次、固定检索列表、原文锚点和保留关系。HTTP、Python Agent 工具和 CLI 共用请求模型与结果正文。它提供制卡前的取证工具，史料卡生成由相邻的 research-cards 模块负责。

2026-09-11：[注释联调](../../docs/note-rag-integration-20260911.md)将上游已采用的正文／注释／续注依赖接入 `related` 读取。它从证据所属固定快照的片段目录解析原始映射，复用原有用途检查和分页；无需重建索引即可读取目录中未索引的关联注释。结构化来源不回退到旧块关系或同号猜测。此次为隔离回放验证，未重启生产服务。

实现与验收依据是项目的 [已确认规格](../../.scratch/document-retrieval/spec.md)。真实语料的 GPT 开发审阅均为机器记录；它们不表示用户人工史料批准，也不建立 sealed gold。生产文档审阅仍由冻结提取模块的 DeepSeek 配置负责。

详细调用顺序、错误处理与备份恢复见 [操作说明](docs/operations.md)，实验命令及证据口径见 [复验说明](evaluation/README.md)。

## 安装和启动

使用独立 Python 3.11 环境。`uv.lock` 固定依赖；Windows 使用 CUDA 13.0 的 PyTorch 轮子，Linux 使用 CPU 轮子。运行时只读取已登记的本地模型，不自动下载或静默切换设备。

在本模块目录执行：

```powershell
uv sync --locked --python 3.11
docker compose -f config/compose.yml up -d
.venv/Scripts/python.exe evaluation/tools/download_models.py --root ../../models/document-retrieval
.venv/Scripts/document-retrieval.exe --config config/local.example.toml check --models
.venv/Scripts/document-retrieval.exe --config config/local.example.toml serve
```

先按入库模块说明启动其 HTTP 服务，并把 `ingestion_url` 指向实际地址。检索需要固定来源读取、用途检查、目录基准及变更事件等公开接口；运行记录中的 `18125` 是本项目隔离验收服务端口。OpenSearch 绑定本机 `19260`，检索绑定本机 `18130`。示例配置适合本机开发；远程部署的认证、网络与项目级集成约束由后续边界阶段另行配置。

TOML 的 `state_root`、`models_root` 相对配置文件所在目录解析。`RETRIEVAL_<字段名大写>` 环境变量覆盖配置，可用全局参数 `--env-file` 加载私有凭据。模型单独保存在 `models/document-retrieval/<模型名>/<revision>`，不改变提取模块的模型目录。`--comparisons` 可额外下载已登记的两个 Qwen 对照模型。

`auto_sync = true` 接收公开事件并逐批维护当前采用；隔离实验使用 `false`，由显式索引作业构建固定范围。状态查询只报告状态，不隐式开始建索引。每个状态目录只能有一个控制进程。

## 一次取证

先调用 `POST /api/v1/sources/resolve`，明确选择一篇或一个固定快照。名称出现多个候选时，选择来源后再查；`current` 在实际召回前固定，`snapshot`、列表和保留对象继续引用原输入。跨来源请求必须明确 `intent = supplemental`。

将以下内容保存为 `search.json`，以实际 UUID 替换 `snapshot_id`：

```json
{
  "query": "机构名称及其活动时间",
  "mode": "hybrid",
  "purpose": "card_input",
  "scope": {
    "kind": "snapshot",
    "snapshot_id": "00000000-0000-0000-0000-000000000000"
  },
  "budget": {
    "max_items": 6,
    "max_excerpt_tokens": 128,
    "max_response_tokens": 2000,
    "counter_ref": "bge-m3-proxy-v1"
  }
}
```

```powershell
.venv/Scripts/document-retrieval.exe --receipt receipts/search.json --output results/search.json --wait search --request search.json
```

`exact_quote` 查原文字形，`compatible_text` 使用固定字形映射和英文大小写兼容，`hybrid` 合并文字与向量候选并可重排。兼容文字仅用于检索；回读始终保留原字形、固定快照和 Unicode code point 偏移 `[start,end)`。

收到作业回执不等于已取得结果。保存 `job_id` 后查询状态，或从 `POST /jobs/{job_id}/result?wait_seconds=30` 等待并取得结果；仍在执行时返回 202 和原作业标识。状态与结果入口都支持 0–30 秒有界等待，默认 0。等待超时或响应丢失时复用同一幂等键／CLI 回执；同键改变参数会被拒绝。首批返回量受完整响应预算约束，后续使用返回的 `list_id` 和 `next_cursor`。正文续读使用独立的 `read_cursor`，两类游标不可互换。

每个命中提供 `evidence_ref`。`POST /reads` 的 `passage` 回读原片段，`neighbors` 展开连续窗口及由原文空白证明相邻的正文，`related` 读取明确关联的附注，`media` 返回原件／页图入口；`provenance` 和 `bibliography` 返回对应来源信息。相邻展开不跨受限缺口或无连续依据的页界，标题只向后展开。作用于固定范围的 `full_scope` 可分批完整读取。缺表头、缺依赖、用途受限和预算不足通过状态与问题字段说明。

页图／原件通过返回的媒体路由流式下载，支持 HEAD、ETag 和 Range。下载时重新核验实际资产覆盖范围的当前用途，局部正文获准不会授权整页或整件。未归类的纯排版空白可在整件检查中排除；未知的可见文字及显式受限空白仍须检查。

## Python Agent 工具

```python
import json
from document_retrieval.client import AgentTools, RetrievalClient

client = RetrievalClient("http://127.0.0.1:18130")
try:
    agent = AgentTools(client)
    # request 来自已保存、已明确选择来源的请求。
    request = json.load(open("search.json", encoding="utf-8"))
    tool_text = agent.invoke(
        "search_evidence",
        {"kind": "search", **request},
        operation_key="caller-task-search-1",
    )
    result = json.loads(tool_text)
finally:
    client.close()
```

六个工具为 `resolve_sources`、`retrieval_status`、`retain_inputs`、`search_evidence`、`read_evidence`、`release_inputs`。主篇与补查分别建立保留关系；检索请求的 `retention_id` 自动绑定完成的列表，也可用 `retain_inputs(kind="bind")` 绑定已有列表。任务结束后释放；重复释放不重置到期时钟。普通实际访问续期七天，状态查看不续期；有效依赖会阻止回收。完整锚点可以另行回读，过期列表的原排序不能凭空恢复。

工具返回的 JSON 文本与 HTTP 最终正文一致。`X-Retrieval-Response-Tokens` 和 `X-Retrieval-Response-SHA256` 覆盖整个序列化响应。当前计数器是固定 BGE tokenizer 的 `proxy_estimate`，不是任意宿主模型的实际计费；宿主提示、工具定义、上下文重复、CLI 展示包装和视觉输入另计。

## 索引管理和恢复

CLI 的 `index baseline|rebuild|repair` 提交持久后台作业；请求文件可指定固定范围和配置。新配置使用独立代次及向量空间，后台核验与水位接续完成后才激活，原列表保留原代。`jobs retry|cancel` 使用当前执行代次和尝试标识。`cleanup` 默认预览，加 `--execute` 才执行回收。

```powershell
.venv/Scripts/document-retrieval.exe --receipt receipts/rebuild.json index rebuild --request rebuild.json
.venv/Scripts/document-retrieval.exe --receipt receipts/backup.json --wait recovery create
```

配对备份进入明确维护窗口，保存 SQLite、必要阶段文件、模型与规则清单，以及对应的 OpenSearch 快照。备份不是单独复制 SQLite；模型文件须按清单在本地存在，OpenSearch 快照仓库也必须保留。

停止该状态目录的检索服务后执行：

```powershell
.venv/Scripts/document-retrieval.exe --config config/local.example.toml --output receipts/restore.json recovery restore <package_id>
.venv/Scripts/document-retrieval.exe --config config/local.example.toml serve
```

离线恢复获取独占控制锁，核验配对内容，并在控制库之外写入恢复回执。恢复后从新的就绪目录基准开始消费；恢复点之后的外部回执可能不存在，不能当作旧对象继续。入库采用／用途状态仍以当前公开服务为准。

## Linux 与复验

`config/Dockerfile` 创建独立 Linux Python 运行环境，构建上下文必须为项目根目录以包含共享运行包；统一命令见[工程运行说明](../../docs/engineering.md)。`config/linux.example.toml` 明确使用 CPU FP32。把 `/data` 和 `/models` 挂载为持久目录或 Docker 卷，并按实际网络修改两个服务地址。Linux 最低路径脚本位于 `evaluation/tools/linux_minimum.py`；Windows GPU 与 Linux CPU 的运行证据分别归档，不能互相替代。

```powershell
.venv/Scripts/document-retrieval.exe export-contracts contracts
.venv/Scripts/pytest.exe
.venv/Scripts/ruff.exe check src tests evaluation/tools
```

离线合同从同一模型导出，不启动服务。真实数据集位于 `evaluation/datasets/`，每轮轨迹位于唯一的 `evaluation/runs/<run_id>/`。`run_quality.py` 只读取固定任务和实际工具结果；`score_quality.py` 在运行完成后比较机器参考；`full_read_baseline.py` 执行同范围完整读取对照。`fault_server.py` 仅供隔离工程验收，故障暂停和模拟时钟不会进入生产 HTTP 接口。

容量测试按用户要求暂停。十万页目标、Linux GPU 性能和完整制卡流程节省均须单独实测；小样本工具链结果不能代替这些结论。[首版验收报告](docs/first-version-acceptance.md) 已绑定源码、配置、数据集与运行证据。机器验收完成并保留已知失败、超时恢复及延迟限制；用户最终接受保持 pending。
