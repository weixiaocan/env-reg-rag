"""Append-only local query trace persistence."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from threading import Lock

from src.application.query_observability import QueryTrace


class JsonlQueryTraceRecorder:
    """Persist one complete, JSON-serializable record per query."""

    def __init__(self, output_path: Path) -> None:
        self._output_path = output_path
        self._lock = Lock()

    def record(self, trace: QueryTrace) -> None:
        line = json.dumps(asdict(trace), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            with self._output_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
