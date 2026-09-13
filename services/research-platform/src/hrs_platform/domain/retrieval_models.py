"""Pinned local retrieval adapters; no legacy response-accounting API."""

import gc
import threading

import numpy as np
import torch
from transformers import (
    AutoModel,
    AutoModelForCausalLM,
    AutoModelForSequenceClassification,
    AutoTokenizer,
)

from .errors import Problem
from .settings import MODEL_REVISIONS

QWEN_INSTRUCTION = "Given a historical research question, retrieve source passages relevant to the question."
QWEN_RANK_PREFIX = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
QWEN_RANK_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


class LocalModels:
    def __init__(self, settings):
        self.settings = settings
        self._lock = threading.RLock()
        self._tokenizers = {}
        self._models = {}
        self.last_batches = []

    def tokenizer(self, model):
        with self._lock:
            if model not in self._tokenizers:
                try:
                    self._tokenizers[model] = AutoTokenizer.from_pretrained(
                        self.settings.model_path(model),
                        local_files_only=True,
                        trust_remote_code=False,
                    )
                except (OSError, ValueError):
                    raise Problem(
                        "model_artifacts_missing",
                        "Install the registered local tokenizer artifacts before using this operation.",
                        status=503,
                        retryable=True,
                    ) from None
            return self._tokenizers[model]

    def identity(self):
        return {
            "embedding": self.settings.embedding_model,
            "embedding_revision": MODEL_REVISIONS[self.settings.embedding_model],
            "reranker": self.settings.reranking_model,
            "reranker_revision": MODEL_REVISIONS[self.settings.reranking_model],
            "torch": torch.__version__,
            "transformers": "4.57.6",
            "device": self.settings.device,
            "precision": "float16" if self.settings.device.startswith("cuda") else "float32",
            "dimension": 1024,
            "normalization": "l2",
            "query_instruction": QWEN_INSTRUCTION
            if self.settings.embedding_model.startswith("Qwen/")
            else None,
            "embedding_pooling": "last_token" if self.settings.embedding_model.startswith("Qwen/") else "cls",
        }

    def _load(self, role):
        model_name = self.settings.embedding_model if role == "embedding" else self.settings.reranking_model
        if model_name in self._models:
            return self._models[model_name]
        if self.settings.device.startswith("cuda") and not torch.cuda.is_available():
            raise Problem(
                "gpu_unavailable",
                "The explicitly configured GPU is unavailable.",
                status=503,
                retryable=True,
            )
        loader = (
            AutoModel
            if role == "embedding"
            else AutoModelForCausalLM
            if model_name.startswith("Qwen/")
            else AutoModelForSequenceClassification
        )
        try:
            model = loader.from_pretrained(
                self.settings.model_path(model_name),
                local_files_only=True,
                trust_remote_code=False,
                torch_dtype=torch.float16 if self.settings.device.startswith("cuda") else torch.float32,
            )
            model.to(self.settings.device).eval()
        except torch.OutOfMemoryError:
            raise Problem(
                "model_memory_unavailable",
                "The configured model could not fit in memory.",
                status=503,
                retryable=True,
            ) from None
        except (OSError, ValueError):
            raise Problem(
                "model_artifacts_missing",
                "Install the exact registered model revision before inference.",
                status=503,
                retryable=True,
            ) from None
        self._models[model_name] = model
        return model

    def _batched(self, values, batch_size, operation, lengths=None):
        order = sorted(range(len(values)), key=lambda i: lengths[i]) if lengths is not None else list(range(len(values)))
        values = [values[i] for i in order]
        output, offset = [], 0
        self.last_batches = []
        while offset < len(values):
            size = min(batch_size, len(values) - offset)
            try:
                with torch.inference_mode():
                    output.extend(operation(values[offset : offset + size]))
                self.last_batches.append(size)
                offset += size
            except torch.OutOfMemoryError:
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                if size == 1:
                    raise Problem(
                        "inference_memory_unavailable",
                        "A complete model input cannot fit; the unfinished work is recoverable.",
                        status=503,
                        retryable=True,
                    ) from None
                batch_size = max(1, size // 2)
        restored = [None] * len(output)
        for original, value in zip(order, output, strict=True):
            restored[original] = value
        return restored

    def _check(self, tokenizer, values, limit, *, pairs=False, special=True):
        lengths = []
        for value in values:
            encoded = (
                tokenizer(*value, add_special_tokens=special, truncation=False)
                if pairs
                else tokenizer(value, add_special_tokens=special, truncation=False)
            )
            if len(encoded["input_ids"]) > limit:
                raise Problem(
                    "model_input_too_long",
                    "The complete model input exceeds its registered token limit; no input was truncated.",
                    status=422,
                    errors=[{"actual_tokens": len(encoded["input_ids"]), "maximum_tokens": limit}],
                )
            lengths.append(len(encoded["input_ids"]))
        return lengths

    def embed(self, texts, *, query=False):
        name = self.settings.embedding_model
        tokenizer = self.tokenizer(name)
        values = [
            f"Instruct: {QWEN_INSTRUCTION}\nQuery:{value}" if query and name.startswith("Qwen/") else value
            for value in texts
        ]
        lengths = self._check(tokenizer, values, 32768 if name.startswith("Qwen/") else 8192)
        with self._lock:
            model = self._load("embedding")
            tokenizer.padding_side = "left" if name.startswith("Qwen/") else "right"

            def infer(batch):
                inputs = tokenizer(batch, padding=True, truncation=False, return_tensors="pt").to(
                    self.settings.device
                )
                hidden = model(**inputs).last_hidden_state
                pooled = hidden[:, -1] if name.startswith("Qwen/") else hidden[:, 0]
                return torch.nn.functional.normalize(pooled.float(), p=2, dim=1).cpu().numpy().tolist()

            vectors = self._batched(values, self.settings.embedding_batch, infer, lengths)
        array = np.asarray(vectors, dtype=np.float32)
        if (
            array.shape != (len(values), 1024)
            or not np.isfinite(array).all()
            or not (np.linalg.norm(array, axis=1) > 0).all()
        ):
            raise Problem(
                "embedding_invalid",
                "The model returned an invalid dense vector.",
                status=503,
                retryable=True,
            )
        return array.tolist()

    def rerank(self, query, texts):
        name = self.settings.reranking_model
        tokenizer = self.tokenizer(name)
        qwen = name.startswith("Qwen/")
        values = (
            [
                QWEN_RANK_PREFIX
                + f"<Instruct>: {QWEN_INSTRUCTION}\n<Query>: {query}\n<Document>: {value}"
                + QWEN_RANK_SUFFIX
                for value in texts
            ]
            if qwen
            else [(query, value) for value in texts]
        )
        lengths = self._check(tokenizer, values, 32768 if qwen else 8192, pairs=not qwen, special=not qwen)
        with self._lock:
            model = self._load("reranking")
            tokenizer.padding_side = "left" if qwen else "right"

            def infer(batch):
                inputs = tokenizer(
                    batch,
                    padding=True,
                    truncation=False,
                    add_special_tokens=not qwen,
                    return_tensors="pt",
                ).to(self.settings.device)
                logits = model(**inputs).logits
                if qwen:
                    yes, no = (
                        tokenizer.convert_tokens_to_ids("yes"),
                        tokenizer.convert_tokens_to_ids("no"),
                    )
                    scores = (logits[:, -1, yes] - logits[:, -1, no]).float()
                else:
                    scores = logits.view(-1).float()
                return scores.cpu().tolist()

            scores = self._batched(values, self.settings.reranking_batch, infer, lengths)
        if not np.isfinite(scores).all():
            raise Problem(
                "reranker_invalid",
                "The reranker returned a non-finite score.",
                status=503,
                retryable=True,
            )
        return scores

    def unload(self):
        with self._lock:
            self._models.clear()
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
