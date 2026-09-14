"""Rebuildable numeric-range projection derived from approved table evidence."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable


_MEASURE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:毫米|mm)", re.IGNORECASE)
_PERIOD_PATTERN = re.compile(r"(12|24)\s*(?:小时|h)", re.IGNORECASE)
_RANGE_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)\s*[~～—-]\s*(\d+(?:\.\d+)?)$")
_UPPER_PATTERN = re.compile(r"^[<＜]\s*(\d+(?:\.\d+)?)$")
_LOWER_PATTERN = re.compile(r"^[≥>=]\s*(\d+(?:\.\d+)?)$")


@dataclass(frozen=True)
class NumericRangeRecord:
    evidence_id: str
    document_version_id: str
    metric: str
    period: str
    category: str
    lower_bound: float | None
    upper_bound: float | None
    lower_inclusive: bool
    upper_inclusive: bool
    unit: str

    def contains(self, value: float) -> bool:
        if self.lower_bound is not None:
            if value < self.lower_bound or (
                value == self.lower_bound and not self.lower_inclusive
            ):
                return False
        if self.upper_bound is not None:
            if value > self.upper_bound or (
                value == self.upper_bound and not self.upper_inclusive
            ):
                return False
        return True


class NumericRangeIndex:
    """Match numeric questions to their original, citable table evidence."""

    def __init__(self, records: Iterable[NumericRangeRecord]) -> None:
        self._records = tuple(records)

    @classmethod
    def from_artifacts(
        cls,
        *,
        evidence_units_path: Path,
        corpus_manifest_path: Path,
    ) -> "NumericRangeIndex":
        manifest = json.loads(corpus_manifest_path.read_text(encoding="utf-8"))
        units = [
            json.loads(line)
            for line in evidence_units_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return cls.from_evidence_units(
            units,
            allowed_document_version_ids=set(manifest["document_version_ids"]),
        )

    @classmethod
    def from_evidence_units(
        cls,
        units: Iterable[dict[str, Any]],
        *,
        allowed_document_version_ids: set[str] | None = None,
    ) -> "NumericRangeIndex":
        records: list[NumericRangeRecord] = []
        for unit in units:
            if unit.get("evidence_type") != "table":
                continue
            if unit.get("quality_status") != "approved":
                continue
            if unit.get("usage_policy") != "answer_and_citation":
                continue
            document_version_id = str(unit.get("document_version_id", ""))
            if (
                allowed_document_version_ids is not None
                and document_version_id not in allowed_document_version_ids
            ):
                continue
            title = str(unit.get("title", ""))
            text = str(unit.get("text", ""))
            if "降雨量" not in f"{title}\n{text}":
                continue
            records.extend(cls._parse_rainfall_table(unit))
        return cls(records)

    @staticmethod
    def _parse_rainfall_table(unit: dict[str, Any]) -> list[NumericRangeRecord]:
        lines = [
            [cell.strip() for cell in line.split("|")]
            for line in str(unit.get("text", "")).splitlines()
            if "|" in line
        ]
        header_index = next(
            (
                index
                for index, cells in enumerate(lines)
                if any("12h" in cell.lower() for cell in cells)
                and any("24h" in cell.lower() for cell in cells)
            ),
            None,
        )
        if header_index is None:
            return []
        periods = [
            f"{match.group(1)}h"
            for cell in lines[header_index]
            if (match := _PERIOD_PATTERN.search(cell))
        ]
        if len(periods) < 2:
            return []

        records: list[NumericRangeRecord] = []
        for cells in lines[header_index + 1 :]:
            if len(cells) < len(periods) + 1:
                continue
            category = cells[0]
            for period, raw_interval in zip(periods, cells[1:], strict=False):
                parsed = _parse_interval(raw_interval)
                if parsed is None:
                    continue
                lower, upper, lower_inclusive, upper_inclusive = parsed
                records.append(
                    NumericRangeRecord(
                        evidence_id=str(unit["evidence_id"]),
                        document_version_id=str(unit["document_version_id"]),
                        metric="rainfall",
                        period=period,
                        category=category,
                        lower_bound=lower,
                        upper_bound=upper,
                        lower_inclusive=lower_inclusive,
                        upper_inclusive=upper_inclusive,
                        unit="毫米",
                    )
                )
        return records

    def match(
        self,
        *,
        question: str,
        resolved_scope: dict[str, str],
    ) -> list[NumericRangeRecord]:
        normalized = question.lower()
        if not any(term in normalized for term in ("降雨", "降水", "雨量")):
            return []
        measure = _MEASURE_PATTERN.search(normalized)
        if not measure:
            return []
        period = resolved_scope.get("statistical_period")
        if not period:
            period_match = _PERIOD_PATTERN.search(normalized)
            period = f"{period_match.group(1)}h" if period_match else ""
        if period not in {"12h", "24h"}:
            return []
        value = float(measure.group(1))
        return [
            record
            for record in self._records
            if record.metric == "rainfall"
            and record.period == period
            and record.contains(value)
        ]


def _parse_interval(
    raw: str,
) -> tuple[float | None, float | None, bool, bool] | None:
    value = raw.strip()
    if match := _RANGE_PATTERN.fullmatch(value):
        return float(match.group(1)), float(match.group(2)), True, True
    if match := _UPPER_PATTERN.fullmatch(value):
        return None, float(match.group(1)), False, False
    if match := _LOWER_PATTERN.fullmatch(value):
        return float(match.group(1)), None, True, False
    return None
