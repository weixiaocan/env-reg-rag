"""Local cross-encoder rerank seam for the E1 experiment.

The stage ONLY reorders an existing candidate list: it never filters, never
mutates hits, and never touches ``usage_policy`` or any document/source
field.  With no scorer attached (rerank off) it is an identity function --
the retrieval path is then exactly the baseline composition.

The scorer is injected by the caller (a local cross-encoder adapter to be
wired at execution time); this module itself never downloads or loads a
model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class RerankConfig:
    """E1 rerank stage parameters (execution-time values recorded in the run)."""

    model_id: str = "BAAI/bge-reranker-base"
    revision: str | None = None  # recorded at execution time
    device: str = "cpu"
    max_length: int = 512
    final_top_k: int = 10


class RerankStage:
    """Deterministic reorder-only stage over RRF fusion candidates.

    Contract (pinned by tests/test_rerank_stage.py):

    * **rerank off** (``scorer=None``) is an identity: the output list equals
      the input list -- the composition is then exactly the baseline;
    * **rerank on** only reorders: the same hit objects come out, so
      ``usage_policy``, ``file_name``, ``chunk_id`` and every other field are
      untouched (policy/source binding can never change here);
    * ties keep the original order (stable sort on the original rank), so the
      stage is deterministic for a given scorer;
    * **non-finite scores hard-fail**: a scorer returning ``None``, ``nan``
      or ``±inf`` raises ``ValueError`` -- the stage never silently sinks a
      bad score (E1 gate: 非有限分数硬失败).
    """

    def __init__(self, *, config: RerankConfig, scorer: Callable[[str, str], float] | None = None) -> None:
        self._config = config
        self._scorer = scorer

    @property
    def enabled(self) -> bool:
        return self._scorer is not None

    @property
    def config(self) -> RerankConfig:
        return self._config

    def rerank(self, question: str, hits: Sequence[Any]) -> list[Any]:
        """Return a new list: hits reordered by score desc (stable), or an
        identity copy when rerank is off.  Input hits are never mutated."""
        if not self.enabled:
            return list(hits)
        scored = []
        for index, hit in enumerate(hits):
            text = hit.text if not isinstance(hit, dict) else hit.get("text", "")
            value = self._scorer(question, text)
            if value is None or not math.isfinite(float(value)):
                raise ValueError(
                    f"rerank scorer must return a finite float, got {value!r}"
                )
            scored.append((float(value), index, hit))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [hit for _, _, hit in scored]
