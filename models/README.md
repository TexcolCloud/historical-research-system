# Local Model Store

本目录只存放本项目运行所需的本地模型权重和模型缓存：

- `docling/`：Docling 预下载模型与工件。
- `paddleocr/`：PaddleOCR/PaddleX 官方模型缓存。
- `local-vision/`：本地 Qwen3-VL 视觉复核模型及运行文件。
- `document-retrieval/`：当前检索使用的 BGE-M3 嵌入与 BGE reranker 模型。
- `huggingface/`：Hugging Face Hub 模型缓存。
- `torch/`：PyTorch Hub 模型缓存。

实际权重文件由 `.gitignore` 排除。旧表格识别模型不再是当前运行要求；此次代码清理不删除或迁移本地已有权重。
