"""Local dense embeddings using the pinned BGE small Chinese model."""

from __future__ import annotations

from collections.abc import Sequence

import torch
from transformers import AutoModel, AutoTokenizer


class BgeSmallZhEmbedder:
    model_id = "BAAI/bge-small-zh-v1.5"
    model_revision = "7999e1d3359715c523056ef9478215996d62a620"
    query_instruction = "为这个句子生成表示以用于检索相关文章："
    dimension = 512
    max_length = 512

    def __init__(
        self,
        *,
        local_files_only: bool = False,
        device: str = "cpu",
        batch_size: int = 64,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_id,
            revision=self.model_revision,
            local_files_only=local_files_only,
        )
        self._model = AutoModel.from_pretrained(
            self.model_id,
            revision=self.model_revision,
            local_files_only=local_files_only,
        ).to(device)
        self._model.eval()
        self._device = device
        self._batch_size = batch_size

    def embed_query(self, text: str) -> list[float]:
        return self._encode([self.query_instruction + text])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        values = list(texts)
        vectors = []
        for start in range(0, len(values), self._batch_size):
            vectors.extend(self._encode(values[start : start + self._batch_size]))
        return vectors

    def inspect_documents(self, texts: Sequence[str]) -> dict[str, int]:
        token_counts = [
            len(
                self._tokenizer.encode(
                    text,
                    add_special_tokens=True,
                    truncation=False,
                    verbose=False,
                )
            )
            for text in texts
        ]
        return {
            "input_count": len(token_counts),
            "max_length": self.max_length,
            "max_observed_tokens": max(token_counts, default=0),
            "truncated_count": sum(
                token_count > self.max_length for token_count in token_counts
            ),
        }

    def _encode(self, texts: list[str]) -> list[list[float]]:
        inputs = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        inputs = {name: value.to(self._device) for name, value in inputs.items()}
        with torch.inference_mode():
            cls_embeddings = self._model(**inputs).last_hidden_state[:, 0]
            normalized = torch.nn.functional.normalize(cls_embeddings, p=2, dim=1)
        return normalized.cpu().tolist()
