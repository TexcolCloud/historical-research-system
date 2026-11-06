# 本地模型与缓存

默认 Codex 流程由 Terra 执行 OCR、Sol 执行独立原图审核、复核和文章组织，不依赖本地 PaddleOCR、Qwen 或 DeepSeek。完整 React 平台的检索仍使用本地 BGE 权重；独立 MCP 的使用要求见 [根 README 快速开始](../README.md#快速开始)。所有命令从项目根目录执行，并继承根 README 设置的 `UV_PROJECT_ENVIRONMENT`。

权重和下载缓存不随 Git 分发。此目录不保存书籍业务原件或审核记录；业务持久化职责见 [平台说明](../services/research-platform/README.md#模块与数据职责)。

## 当前检索模型

| 用途 | 模型 | 本地目录 |
| --- | --- | --- |
| 嵌入 | `BAAI/bge-m3` | `models/document-retrieval/bge-m3/<revision>/` |
| 重排 | `BAAI/bge-reranker-v2-m3` | `models/document-retrieval/bge-reranker-v2-m3/<revision>/` |

revision 由 [domain/settings.py](../services/research-platform/src/hrs_platform/domain/settings.py) 固定。运行时只读本地模型，不自动下载。GPU broker 使用根 README 指定的 `.cache/engineering-envs/research-platform` Python 环境；CPU 模式须在 API 自身环境安装 `retrieval` extra，并显式设置 `PLATFORM_RETRIEVAL_DEVICE=cpu`。CPU 模式仍需要两份 BGE 权重。

安装根 README 中的检索依赖后，可执行下面的下载命令；它需要网络和足够的磁盘空间，不启动模型推理：

```powershell
@'
from pathlib import Path
from huggingface_hub import snapshot_download
from hrs_platform.domain.settings import MODEL_REVISIONS

for name, revision in MODEL_REVISIONS.items():
    target = Path("models/document-retrieval") / name.split("/")[-1] / revision
    snapshot_download(repo_id=name, revision=revision, local_dir=target)
'@ | .cache/engineering-envs/research-platform/Scripts/python.exe -
```

模型目录和固定 revision 必须与运行时代码一致。主机检索直接读取 `models/document-retrieval/`；旧应用 Compose 将它只读挂载到容器内相同项目布局。不要只改一侧路径。

## 旧 OCR／视觉兼容

下列工件只用于显式旧转换／平台路径，不是默认 Codex 处理的安装步骤。配置与 CLI 见 [document-extraction README](../services/document-extraction/README.md)；旧平台选择方式见 [research-platform README](../services/research-platform/README.md#旧路径兼容)。不需要删除本机已有权重。

| 用途 | 工件 | 默认目录 |
| --- | --- | --- |
| OCR | PaddleOCR-VL-1.6、PaddleX 工件 | `models/paddleocr/` |
| 文档转换 | Docling 工件 | `models/docling/` |
| 本地原图核对 | Qwen3-VL-8B-Instruct Q4_K_M、F16 视觉投影器 | `models/local-vision/` |
| 框架缓存 | Hugging Face、PyTorch | `models/huggingface/`、`models/torch/` |

[Windows bootstrap](../deploy/windows/bootstrap.ps1) 安装旧 OCR 环境并执行后端检查，首次运行可能下载模型、初始化后端并占用 GPU。它不属于只读健康检查；仅在明确准备旧 OCR 路径时运行：

```powershell
.\deploy\windows\bootstrap.ps1
```

该脚本使用提取模块自己的 `services/document-extraction/.venv`；若当前终端已为平台设置 `UV_PROJECT_ENVIRONMENT`，应在单独终端运行旧 OCR 安装，避免将依赖同步到平台环境。

[视觉安装脚本](../scripts/setup_local_vision.py) 下载固定版本 Qwen 权重和 Windows llama.cpp，校验下载摘要并生成 `models/local-vision/installation.json`：

```powershell
uv run --no-sync --project services/research-platform --locked python scripts/setup_local_vision.py
```

运行时位于 `.cache/llama.cpp/`。安装记录包含当前机器的可执行文件路径；迁移主机后重新安装，不直接复用另一台机器的安装记录。旧 OCR 缓存可通过 `HRS_MODEL_ROOT` 指定，路径规则见 [提取设置](../services/document-extraction/src/document_extraction/settings.py)；平台 BGE 路径并不读取该变量。RapidOCR 仅为显式替代配置，不在默认单路 OCR 后自动补读。

## 检查与排障

```powershell
uv run --no-sync --project services/research-platform --locked python -m hrs_platform.cli doctor
```

平台 `doctor` 检查依赖服务和 Codex 登录，不加载 BGE／旧视觉模型，也不证明权重能成功推理。旧 OCR 的 `history-extract check` 会初始化模型，使用方式见提取模块 README。

| 问题 | 检查 |
| --- | --- |
| 找不到 BGE 权重／tokenizer | 下载目录是否包含代码固定 revision，运行进程是否从项目模型目录读取 |
| GPU 检索不可用 | CUDA 检索环境、显卡状态、`18160` broker；主机日志位于 `.cache/platform-diagnostics/codex-retrieval.log` |
| 选择 CPU 后缺少 torch／transformers | `retrieval` extra 是否安装在 API 实际使用的 Python 环境 |
| 旧视觉模型无法启动 | 安装记录、`.cache/llama.cpp/`、`state/local-vision/llama.log` 与 `events.jsonl` |

[local_vision.py](../scripts/local_vision.py) 按请求协调旧 OCR、视觉与检索的 GPU 使用；显存准入配置是调度阈值，不能当作实际峰值或内容验收证据。不要将权重、安装记录、下载临时文件或缓存提交 Git；模型使用条款以各上游许可证为准。
