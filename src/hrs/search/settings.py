from pathlib import Path
from typing import Literal

from pydantic import BaseModel

MODEL_REVISIONS = {
    "BAAI/bge-m3": "5617a9f61b028005a4858fdac845db406aefb181",
    "BAAI/bge-reranker-v2-m3": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
}


class RetrievalSettings(BaseModel):
    models_root: Path
    device: Literal["cpu", "cuda"] = "cpu"
    embedding_model: Literal["BAAI/bge-m3"] = "BAAI/bge-m3"
    reranking_model: Literal["BAAI/bge-reranker-v2-m3"] = "BAAI/bge-reranker-v2-m3"
    embedding_batch: int = 8
    reranking_batch: int = 4

    def model_path(self, model):
        return self.models_root / model.split("/")[-1] / MODEL_REVISIONS[model]
