"""Actual local inference probe; synthetic strings are runtime checks, not quality labels."""

import argparse
import platform
import time
from pathlib import Path

import numpy as np
import torch

from document_retrieval.models import LocalModels
from document_retrieval.records import atomic_json, fingerprint, utc_now
from document_retrieval.settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparisons", action="store_true")
    args = parser.parse_args()
    settings = Settings.load(args.config)
    pairs = [(settings.embedding_model, settings.reranking_model)]
    if args.comparisons:
        pairs.append(("Qwen/Qwen3-Embedding-0.6B", "Qwen/Qwen3-Reranker-0.6B"))
    documents = [
        "本项检查验证中文史料检索的模型运行路径。",
        "The historical records describe taxation and trade.",
    ]
    output = {
        "started_at": utc_now(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "device": settings.device,
        "inputs": documents,
        "inputs_sha256": fingerprint(documents),
        "quality_evaluation": False,
        "runs": [],
    }
    for embedding, reranker in pairs:
        models = LocalModels(
            settings.model_copy(update={"embedding_model": embedding, "reranking_model": reranker})
        )
        start = time.perf_counter()
        vectors = np.asarray(models.embed(documents))
        scores = models.rerank("历史文献中的税收与贸易", documents)
        output["runs"].append(
            {
                "identity": models.identity(),
                "shape": list(vectors.shape),
                "norms": np.linalg.norm(vectors, axis=1).tolist(),
                "finite": bool(np.isfinite(vectors).all()),
                "vector_sha256": fingerprint(vectors.tolist()),
                "rerank_scores": scores,
                "response_counter_tokens": models.counter().count(documents[0]),
                "seconds": time.perf_counter() - start,
                "max_cuda_allocated_bytes": torch.cuda.max_memory_allocated()
                if settings.device.startswith("cuda")
                else None,
            }
        )
        models.unload()
        atomic_json(args.output, output)
    output["completed_at"] = utc_now()
    atomic_json(args.output, output)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
