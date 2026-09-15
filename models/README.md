# Local Models

本目录保存运行所需的模型权重与缓存。权重不随 Git 分发，也不作为书籍业务文件写入 S3；原件、OCR 产物和研究记录使用平台 S3 存储。返回 [项目部署说明](../README.md#快速开始)。

## 当前模型与目录

| 用途     | 模型／工件                                    | 默认目录                                            |
| -------- | --------------------------------------------- | --------------------------------------------------- |
| OCR      | PaddleOCR-VL-1.6 与 PaddleX 所需模型          | `paddleocr/`                                        |
| 文档转换 | Docling 工件                                  | `docling/`                                          |
| 原图核对 | Qwen3-VL-8B-Instruct Q4_K_M 与 F16 视觉投影器 | `local-vision/`                                     |
| 嵌入     | BAAI/bge-m3                                   | `document-retrieval/bge-m3/<revision>/`             |
| 重排     | BAAI/bge-reranker-v2-m3                       | `document-retrieval/bge-reranker-v2-m3/<revision>/` |
| 框架缓存 | Hugging Face、PyTorch                         | `huggingface/`、`torch/`                            |

旧 Table Transformer／TableCenterNet 权重不是现役单路 OCR 的运行要求。RapidOCR 仅作为显式替代配置保留，不在默认流程中自动补读。

## OCR 与视觉

在项目根目录，按 [快速开始](../README.md#快速开始) 准备本地根目录标记和配置后执行：

```powershell
$env:UV_PROJECT_ENVIRONMENT="$PWD/services/document-extraction/.venv"
.\deploy\windows\bootstrap.ps1
```

该脚本安装独立 OCR 环境并初始化检查当前后端，首次运行可能下载 Paddle 工件。缓存位置由 [转换设置](../services/document-extraction/src/document_extraction/settings.py) 统一指定；识别路径由 [default.json](../services/document-extraction/config/default.json) 决定。

平台环境安装后，运行：

```powershell
.cache/engineering-envs/research-platform/Scripts/python.exe scripts/setup_local_vision.py
```

[安装脚本](../scripts/setup_local_vision.py) 固定 Qwen 上游版本和 Windows llama.cpp 版本，校验下载摘要，生成 `local-vision/installation.json`。其中记录当前机器上的 `llama-server.exe` 路径；运行时文件位于 `.cache/llama.cpp/`。迁移主机后重新运行安装脚本，不直接沿用另一台机器的绝对路径。

## 嵌入与重排

当前后端使用 [模型配置](../services/research-platform/src/hrs_platform/domain/settings.py) 中注册的固定 revision，运行时只读取本地文件。安装平台的 `retrieval` extra 后，在根目录运行下列命令下载对应快照，无需另建下载工具：

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

需要联网访问上游模型仓库，并为权重与下载缓存预留磁盘空间。示例直接读取项目注册版本，避免文档内复制的版本号与代码分叉。下载本身不启动模型推理。

## 检查与显存调度

- OCR 检查：`services/document-extraction/.venv/Scripts/history-extract.exe check`。它会初始化识别后端并可能占用 GPU，不要与正在处理的书籍并行执行。
- 平台运行状态：使用根目录的 `scripts/hrs_v2.py doctor`；这只检查服务，不验证全部权重是否能成功推理。
- `scripts/local_vision.py` 协调 OCR、Qwen 和检索模型的 GPU 使用。当前以阶段切换为主，不以多个模型同时驻留作为运行前提。
- Docker 中的平台按现有 Compose 只读挂载 `models/document-retrieval/`，Windows broker 从主机模型目录加载。修改目录时同时核对两侧路径。

不要将模型权重、下载临时文件或本机 `installation.json` 提交 Git；模型许可证以各上游仓库为准。
