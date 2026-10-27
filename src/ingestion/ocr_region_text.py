"""Assemble OCR-page text by geometry-aligned three-source voting."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from difflib import SequenceMatcher
import re
from typing import Any

from src.ingestion.page_furniture import normalize_line, watermark_line_variants


_NUMBER_PREFIX = re.compile(r"^\s*(\d+(?:\.\d+){0,2})(?:\s+|(?=[一-鿿])|$)(.*)$")
# Rules turning OCR lines into text (vote and line joining); bumping it gives
# assembled corpora a new version.
OCR_VOTE_MODE = "three-source-region-vote-v8"
_STRUCTURED_KINDS = {"table", "formula", "image", "figure", "chart"}
_SOURCE_PRIORITY = ("pp_raw", "primary", "pp_clean")
_DUPLICATE_SEPARATOR = re.compile(r"^[\s，。；：、,.!?！？;:—-]{0,3}$")
_RAW_CONFLICT_RECOVERY = re.compile(r"^(?:[一-鿿]|[。，；：、！？])$")
# Digits and unit/range symbols that only the unpreprocessed run read inside
# a line.  Watermark removal erases thin glyphs ('/', '～', superscripts),
# so pp_raw alone is trusted for them mid-line; at line ends they are as
# likely to be a neighbouring item number or page number.
_RAW_SYMBOL_RECOVERY = re.compile(r"^[0-9/～~×−\-⁻⁰¹²³⁴⁵⁶⁷⁸⁹.%‰《》()（）]{1,4}$")


def assemble_page_region_text(
    page: dict[str, Any], clean_page: dict[str, Any], raw_page: dict[str, Any], *,
    run_id: str, run_manifest_sha256: str,
    raw_run_id: str, raw_run_manifest_sha256: str,
    preprocessing: dict[str, Any], watermarks: list[str],
    bbox_tolerance: float = 2.0, single_char_score: float = 0.85,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Return a page whose OCR-region lines are voted across all three sources."""
    result = deepcopy(page)
    regions = deepcopy(page.get("regions") or [])
    text_regions = [r for r in regions if str(r.get("kind") or "text") not in _STRUCTURED_KINDS]
    structured = [r for r in regions if str(r.get("kind") or "") in _STRUCTURED_KINDS]
    watermark_set = watermark_line_variants(watermarks)
    stats = {
        "majority_lines": 0, "merge_lines": 0, "single_source_lines": 0,
        "single_source_pp_clean_lines": 0, "conflict_fragment_count": 0,
        "suppressed_pp_raw_single_source_lines": 0,
        "suppressed_ambiguous_short_lines": 0,
        "merge_invariant_violation_count": 0,
        "noise_lines": 0, "orphan_line_groups": 0,
        "number_prefixes_preserved": 0,
        "repaired_adjacent_duplicate_count": 0,
        "source_supported_occlusion_repair_count": 0,
        "superset_over_fragment_majority_lines": 0,
        "superset_over_partial_reading_lines": 0,
        "dropped_fragment_inside_superset_lines": 0,
    }

    candidates: list[dict[str, Any]] = []
    candidates.extend(_ocr_candidates(
        clean_page.get("lines") or [], "pp_clean", text_regions, structured,
        watermark_set, bbox_tolerance, single_char_score,
    ))
    candidates.extend(_ocr_candidates(
        raw_page.get("lines") or [], "pp_raw", text_regions, structured,
        watermark_set, bbox_tolerance, single_char_score,
    ))
    stats["noise_lines"] = sum(bool(row.pop("_noise", False)) for row in candidates)
    candidates = [row for row in candidates if row.get("text")]
    candidates.extend(_primary_candidates(
        page.get("elements") or [], text_regions, structured,
        watermark_set, bbox_tolerance, single_char_score,
    ))
    candidates = _merge_same_source_fragments(candidates)

    output: list[dict[str, Any]] = []
    line_audit: list[dict[str, Any]] = []
    groups = _group_lines(candidates)
    for index, group in enumerate(groups):
        by_source = {row["source"]: row for row in group}
        source_texts = {source: str(by_source[source]["text"]) if source in by_source else None
                        for source in ("pp_clean", "pp_raw", "primary")}
        if _is_ambiguous_short_noise(source_texts):
            stats["suppressed_ambiguous_short_lines"] += 1
            line_audit.append({
                "bbox": _union_bbox([row["bbox"] for row in group]),
                "output_text": "", "source_texts": dict(source_texts),
                "rule": "suppressed_ambiguous_short_line", "single_source": None,
                "conflict_fragments": [], "merge_invariant_violations": [],
            })
            continue
        text, rule, conflicts = merge_source_texts(source_texts)
        if rule == "suppressed_pp_raw_single_source":
            stats["suppressed_pp_raw_single_source_lines"] += 1
            line_audit.append({
                "bbox": _union_bbox([row["bbox"] for row in group]),
                "output_text": "", "source_texts": dict(source_texts),
                "rule": rule, "single_source": "pp_raw",
                "conflict_fragments": [], "merge_invariant_violations": [],
            })
            continue
        primary = by_source.get("primary")
        preserved = False
        if primary is not None:
            primary_match = _NUMBER_PREFIX.match(str(primary.get("text") or ""))
            merged_match = _NUMBER_PREFIX.match(text)
            # Both Paddle runs reading the same number outvote MinerU's
            # ("7.5.10" vs "7.5.101").
            paddle_agreed = merged_match is not None and all(
                (match := _NUMBER_PREFIX.match(source_texts.get(source) or ""))
                and match.group(1) == merged_match.group(1)
                for source in ("pp_clean", "pp_raw"))
            if primary_match and not paddle_agreed and (
                not merged_match
                or (not primary_match.group(2).strip()
                    and merged_match.group(1) != primary_match.group(1))
            ):
                remainder = merged_match.group(2) if merged_match else text
                text = f"{primary_match.group(1)} {remainder}"
                preserved = True
                stats["number_prefixes_preserved"] += 1

        text, occlusion_repairs = recover_source_supported_occlusion_fragments(
            text, source_texts)
        stats["source_supported_occlusion_repair_count"] += len(occlusion_repairs)

        region_id = str(next((row.get("region_id") for row in group if row.get("region_id")), ""))
        bbox = _union_bbox([row["bbox"] for row in group])
        if not region_id:
            region_id = f"ocr-orphan-{int(page.get('physical_page') or 0):04d}-{index + 1:04d}"
            regions.append({
                "region_id": region_id, "element_id": region_id,
                "kind": "text", "model_label": "text", "bbox": bbox,
                "execution_status": "completed", "quality_status": "needs_review",
                "issues": ["ocr_line_group_outside_layout_regions"],
                "relations": [], "processing_config_id": f"{run_id}:orphan-line-group",
            })
            stats["orphan_line_groups"] += 1

        provenance = _source_record(
            group, source_texts=source_texts, rule=rule, conflicts=conflicts,
            run_id=run_id, manifest_sha=run_manifest_sha256,
            raw_run_id=raw_run_id, raw_manifest_sha=raw_run_manifest_sha256,
            preprocessing=preprocessing, primary_prefix_preserved=preserved,
        )
        invariant_violations = find_adjacent_duplicate_fragments(text, source_texts)
        repairs = deepcopy(invariant_violations)
        if repairs:
            text = remove_introduced_adjacent_duplicate_fragments(
                text, source_texts, repairs=repairs)
            stats["repaired_adjacent_duplicate_count"] += len(repairs)
            invariant_violations = find_adjacent_duplicate_fragments(text, source_texts)
        representative = by_source.get("pp_raw") or primary or by_source.get("pp_clean")
        output.append(_element_from_line(
            representative or {}, text, bbox, region_id, provenance, page))
        stats[f"{rule}_lines"] += 1
        if rule == "single_source" and provenance.get("single_source") == "pp_clean":
            stats["single_source_pp_clean_lines"] += 1
        stats["conflict_fragment_count"] += len(conflicts)
        stats["merge_invariant_violation_count"] += len(invariant_violations)
        line_audit.append({
            "bbox": bbox, "output_text": text, "source_texts": dict(source_texts),
            "rule": rule, "single_source": provenance.get("single_source"),
            "conflict_fragments": deepcopy(conflicts),
            "source_supported_occlusion_repairs": deepcopy(occlusion_repairs),
            "repaired_adjacent_duplicates": repairs,
            "merge_invariant_violations": deepcopy(invariant_violations),
        })

    output = _drop_fragments_inside_superset_lines(output, line_audit, stats)
    output.sort(key=lambda row: (_bbox(row.get("bbox"))[1], _bbox(row.get("bbox"))[0],
                                 str(row.get("element_id") or "")))
    for index, row in enumerate(output):
        row["reading_order"] = index
    result["elements"] = output
    result["regions"] = regions
    result["_v3_ocr_region_text"] = True
    result["_v3_ocr_line_audit"] = line_audit
    return result, stats


def merge_source_texts(source_texts: dict[str, str | None]) -> tuple[str, str, list[dict[str, Any]]]:
    """Merge one geometry-aligned line group and return text, rule, conflicts."""
    present = {key: value for key, value in source_texts.items() if value}
    if len(present) == 1:
        if "pp_raw" in present:
            return "", "suppressed_pp_raw_single_source", []
        return next(iter(present.values())), "single_source", []

    normalized = {key: normalize_line(value) for key, value in present.items()}
    counts = Counter(normalized.values())
    majority = next((value for value, count in counts.items() if count >= 2), None)
    if majority is not None:
        # Two engines may agree on a *fragment* of a line that a third engine
        # read in full ("grass swale" vs "2.1.15 植草沟grass swale").  A
        # trusted single source (never pp_raw, which carries watermark
        # residue) that extends the agreed fragment at either end wins.
        superset = next((
            source for source in ("pp_clean", "primary")
            if source in normalized and normalized[source] != majority
            and len(normalized[source]) >= len(majority) + 2
            and (normalized[source].startswith(majority) or normalized[source].endswith(majority))
        ), None)
        if superset is not None:
            return present[superset], "superset_over_fragment_majority", []
        selected_source = next(source for source in _SOURCE_PRIORITY
                               if normalized.get(source) == majority)
        return present[selected_source], "majority", []

    # Two readings, one a clean extension of the other ("当管径小" vs "5.3.2
    # 管道转弯和交接处，…当管径小"): the partial reading did not see the rest
    # of the line, it did not read it as absent. The trusted longer reading
    # (never pp_raw, which carries watermark residue) keeps the whole line.
    if len(present) == 2:
        (src_a, norm_a), (src_b, norm_b) = normalized.items()
        longer, shorter = ((src_a, src_b) if len(norm_a) >= len(norm_b) else (src_b, src_a))
        long_norm, short_norm = normalized[longer], normalized[shorter]
        if (longer != "pp_raw" and short_norm and len(long_norm) >= len(short_norm) + 2
                and (long_norm.startswith(short_norm) or long_norm.endswith(short_norm))):
            return present[longer], "superset_over_partial_reading", []

    # Use the longest source as the shared coordinate system so that a partial
    # high-priority OCR line cannot shift a full line. Priority only breaks
    # length ties and selects values after every source is globally aligned.
    pivot_source = max(
        present, key=lambda source: (len(present[source]), -_SOURCE_PRIORITY.index(source)))
    pivot = present[pivot_source]
    aligned = {source: _align_to_pivot(pivot, present.get(source))
               for source in source_texts}
    pieces: list[str] = []
    conflicts: list[dict[str, Any]] = []
    for offset in range(len(pivot) + 1):
        gap_values = {source: aligned[source][1][offset] for source in source_texts}
        gap, gap_conflict = _choose_gap_fragment(gap_values, interior=0 < offset < len(pivot))
        pieces.append(gap)
        if gap_conflict:
            conflicts.append({"offset": offset, "kind": "insertion", **gap_conflict})
        if offset == len(pivot):
            break
        char_values = {source: aligned[source][0][offset] for source in source_texts}
        char, char_conflict = _choose_fragment(char_values, interior=0 < offset < len(pivot) - 1)
        pieces.append(char)
        if char_conflict:
            conflicts.append({"offset": offset, "kind": "character", **char_conflict})
    return "".join(pieces), "merge", _coalesce_conflicts(conflicts)


def recover_source_supported_occlusion_fragments(
    merged: str, source_texts: dict[str, str | None],
) -> tuple[str, list[dict[str, Any]]]:
    """Recover narrowly provable text hidden by a seal/watermark.

    The repair never invents a value.  It accepts either an ASCII variable
    prefix whose complete suffix agrees with the merged line, or an explicit
    ``共N个`` count that is internally verified by the number of list items in
    that same source.  Every accepted repair records its source and evidence.
    """
    result = merged
    repairs: list[dict[str, Any]] = []

    merged_normalized = normalize_line(result)
    for source in _SOURCE_PRIORITY:
        value = source_texts.get(source)
        if not value:
            continue
        match = re.match(
            r"^\s*([A-Za-zΑ-Ωα-ω][A-Za-z0-9_Α-Ωα-ω]{0,15})\s*"
            r"((?:—{2,}|-{2,}))\s*(.+)$",
            value,
        )
        # This is deliberately symbol-agnostic.  The exact normalized suffix
        # equality is the evidence gate: a single OCR source may restore only
        # the variable-definition prefix that the merged line demonstrably
        # lost, never arbitrary formula text.
        if not match:
            continue
        suffix = normalize_line(match.group(3))
        if suffix and merged_normalized == suffix:
            prefix = f"{match.group(1)}{match.group(2)}"
            result = prefix + result
            repairs.append({
                "kind": "variable_prefix", "source": source,
                "evidence": value, "restored": prefix,
            })
            break

    count_match = re.search(r"共\s*(\d*)\s*个", result)
    for source in _SOURCE_PRIORITY:
        value = source_texts.get(source)
        if not value:
            continue
        source_match = re.search(r"分为(.+?)共\s*(\d+)\s*个", value)
        if not source_match:
            continue
        stated = int(source_match.group(2))
        enumerated = source_match.group(1).count("、") + 1
        if stated != enumerated or stated < 3:
            continue
        if count_match and count_match.group(1) != str(stated):
            old = count_match.group(0)
            new = f"共{stated}个"
            result = result[:count_match.start()] + new + result[count_match.end():]
            repairs.append({
                "kind": "enumerated_count", "source": source,
                "evidence": value, "enumerated_items": enumerated,
                "replaced": old, "restored": new,
            })
        break
    return result, repairs


def _choose_fragment(values: dict[str, str | None], *,
                     interior: bool = False) -> tuple[str, dict[str, Any] | None]:
    present = {key: value for key, value in values.items() if value is not None}
    if not present:
        return "", None
    whitespace = [value for value in present.values() if value and value.isspace()]
    if whitespace and all(not value or value.isspace() for value in present.values()):
        return whitespace[0], None
    raw = present.get("pp_raw")
    raw_only_against_covered_deletions = (
        raw
        and all(source == "pp_raw" or value == ""
                for source, value in present.items())
    )
    if raw_only_against_covered_deletions:
        keep = (_RAW_CONFLICT_RECOVERY.fullmatch(raw)
                or (interior and _RAW_SYMBOL_RECOVERY.fullmatch(raw)))
        return (raw if keep else ""), None
    counts = Counter(present.values())
    majority = next((value for value, count in counts.items() if count >= 2), None)
    if majority is not None:
        return majority, None
    selected_source = next(source for source in _SOURCE_PRIORITY if source in present)
    selected = present[selected_source]
    if len(present) == 1:
        if selected_source == "pp_raw":
            return "", None
        return selected, None
    return selected, {
        "sources": {source: values.get(source) for source in ("pp_clean", "pp_raw", "primary")},
        "selected": selected, "selected_source": selected_source,
    }


def _choose_gap_fragment(values: dict[str, str | None], *,
                         interior: bool = False) -> tuple[str, dict[str, Any] | None]:
    """Vote on an insertion gap, treating covered empty gaps as deletion votes."""
    present = {key: value for key, value in values.items() if value is not None}
    if not present:
        return "", None
    whitespace = [value for value in present.values() if value and value.isspace()]
    if whitespace and all(not value or value.isspace() for value in present.values()):
        return whitespace[0], None
    raw = present.get("pp_raw")
    if (raw and all(source == "pp_raw" or value == ""
                    for source, value in present.items())):
        return (raw if interior and _RAW_SYMBOL_RECOVERY.fullmatch(raw) else ""), None
    counts = Counter(present.values())
    majority = next((value for value, count in counts.items() if count >= 2), None)
    if majority is not None:
        return majority, None
    selected_source = next(source for source in _SOURCE_PRIORITY if source in present)
    selected = present[selected_source]
    if len(present) == 1:
        return ("" if selected_source == "pp_raw" else selected), None
    return selected, {
        "sources": {source: values.get(source) for source in ("pp_clean", "pp_raw", "primary")},
        "selected": selected, "selected_source": selected_source,
    }


def _align_to_pivot(
    pivot: str, text: str | None,
) -> tuple[list[str | None], list[str | None]]:
    """Align a full source line to one shared pivot coordinate system."""
    if text is None:
        return [None] * len(pivot), [None] * (len(pivot) + 1)
    chars: list[str | None] = [None] * len(pivot)
    inserts: list[str | None] = [None] * (len(pivot) + 1)
    matcher = SequenceMatcher(None, pivot, text, autojunk=False)
    for tag, pivot_start, pivot_end, text_start, text_end in matcher.get_opcodes():
        source_fragment = text[text_start:text_end]
        if tag == "equal":
            for gap in range(pivot_start, pivot_end + 1):
                inserts[gap] = inserts[gap] or ""
            for offset, value in enumerate(source_fragment):
                chars[pivot_start + offset] = value
        elif tag == "replace":
            for gap in range(pivot_start, pivot_end + 1):
                inserts[gap] = inserts[gap] or ""
            shared = min(pivot_end - pivot_start, len(source_fragment))
            for offset in range(shared):
                chars[pivot_start + offset] = source_fragment[offset]
            if not (text_end == len(text) and pivot_end == len(pivot)):
                for offset in range(pivot_start + shared, pivot_end):
                    chars[offset] = ""
        elif tag == "insert":
            inserts[pivot_start] = (inserts[pivot_start] or "") + source_fragment
        elif tag == "delete":
            if not (text_end == len(text) and pivot_end == len(pivot)):
                for offset in range(pivot_start, pivot_end):
                    chars[offset] = ""
    return chars, inserts


def _is_ambiguous_short_noise(source_texts: dict[str, str | None]) -> bool:
    """Reject tiny non-CJK raw/primary disagreements when pp_clean has no line."""
    if source_texts.get("pp_clean"):
        return False
    values = [normalize_line(value) for value in source_texts.values() if value]
    return bool(
        len(values) >= 2
        and max(map(len, values)) <= 4
        and all(re.fullmatch(r"[A-Za-zⅠ-ⅿ゠-ヿ]+", value) for value in values)
    )


def find_adjacent_duplicate_fragments(
    merged: str, source_texts: dict[str, str | None], *, minimum_length: int = 4,
) -> list[dict[str, Any]]:
    """Return tandem repeats introduced by merging but absent from every source."""
    candidates: list[dict[str, Any]] = []
    size = len(merged)
    for start in range(size):
        for length in range((size - start) // 2, minimum_length - 1, -1):
            fragment = merged[start:start + length]
            if (not fragment.strip() or not fragment[0].isalnum()
                    or not fragment[-1].isalnum()
                    or all(not char.isalnum() for char in fragment)):
                continue
            separator_start = start + length
            for separator_length in range(4):
                separator_end = separator_start + separator_length
                if separator_end + length > size:
                    break
                separator = merged[separator_start:separator_end]
                if not _DUPLICATE_SEPARATOR.fullmatch(separator):
                    continue
                if merged[separator_end:separator_end + length] != fragment:
                    continue
                repeated_by_source = any(
                    _contains_adjacent_repeat(value, fragment)
                    for value in source_texts.values() if value
                )
                if not repeated_by_source:
                    candidates.append({
                        "fragment": fragment, "separator": separator, "length": length,
                    })
                break
            if candidates and candidates[-1].get("fragment") == fragment:
                break
    # Keep the longest description when nested substrings identify the same
    # tandem repeat; this makes the audit actionable instead of noisy.
    result: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda row: row["length"], reverse=True):
        if any(candidate["fragment"] in row["fragment"] for row in result):
            continue
        result.append(candidate)
    return result


def remove_introduced_adjacent_duplicate_fragments(
    merged: str, source_texts: dict[str, str | None], *,
    repairs: list[dict[str, Any]] | None = None,
) -> str:
    """Collapse tandem repeats introduced by voting, never source repeats.

    A repeat is eligible only when :func:`find_adjacent_duplicate_fragments`
    proves that no individual source contains it.  Longest fragments are
    applied first so nested matches cannot over-delete legitimate text.
    """
    candidates = repairs if repairs is not None else find_adjacent_duplicate_fragments(
        merged, source_texts)
    result = merged
    for item in sorted(candidates, key=lambda row: int(row["length"]), reverse=True):
        fragment = str(item["fragment"])
        separator = str(item.get("separator") or "")
        tandem = fragment + separator + fragment
        if tandem in result and not any(
            _contains_adjacent_repeat(value, fragment)
            for value in source_texts.values() if value
        ):
            result = result.replace(tandem, fragment, 1)
    return result


def _contains_adjacent_repeat(text: str, fragment: str) -> bool:
    first = text.find(fragment)
    while first >= 0:
        after = first + len(fragment)
        for separator_length in range(4):
            separator = text[after:after + separator_length]
            if (_DUPLICATE_SEPARATOR.fullmatch(separator)
                    and text.startswith(fragment, after + separator_length)):
                return True
        first = text.find(fragment, first + 1)
    return False


def _coalesce_conflicts(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for conflict in conflicts:
        if (result and conflict["kind"] == result[-1]["kind"] == "character"
                and conflict["offset"] == result[-1]["end_offset"] + 1
                and conflict["selected_source"] == result[-1]["selected_source"]):
            result[-1]["end_offset"] = conflict["offset"]
            result[-1]["selected"] += conflict["selected"]
            for source in result[-1]["sources"]:
                result[-1]["sources"][source] = (
                    (result[-1]["sources"][source] or "")
                    + (conflict["sources"].get(source) or "")) or None
        else:
            item = dict(conflict)
            item["end_offset"] = item["offset"]
            result.append(item)
    return result


def _ocr_candidates(lines: list[dict[str, Any]], source: str,
                    text_regions: list[dict[str, Any]], structured: list[dict[str, Any]],
                    watermarks: set[str], tolerance: float,
                    single_char_score: float) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(lines):
        text = str(raw.get("text") or "").strip()
        score = _score(raw.get("score"))
        compact = normalize_line(text)
        bbox = _bbox(raw.get("bbox"))
        if (bbox is None or not compact or compact in watermarks
                or (len(compact) == 1 and (score is None or score < single_char_score))):
            result.append({"text": "", "_noise": True})
            continue
        if (_inside_structured(bbox, text, structured)
                and not _in_caption_region(bbox, text_regions)):
            continue
        region = _smallest_region(bbox, text_regions, tolerance)
        result.append({
            "source": source, "text": text, "bbox": bbox,
            "line_id": str(raw.get("line_id") or f"{source}-{index + 1:04d}"),
            "score": score, "region_id": str((region or {}).get("region_id") or ""),
        })
    return result


def _primary_candidates(elements: list[dict[str, Any]], text_regions: list[dict[str, Any]],
                        structured: list[dict[str, Any]], watermarks: set[str],
                        tolerance: float, single_char_score: float) -> list[dict[str, Any]]:
    result = []
    for index, raw in enumerate(elements):
        text = str(raw.get("text") or "").strip()
        bbox = _bbox(raw.get("bbox"))
        score = _score(raw.get("confidence"))
        if (str(raw.get("type") or "text") != "text" or bbox is None
                or not normalize_line(text) or normalize_line(text) in watermarks
                or (len(normalize_line(text)) == 1 and not normalize_line(text).isdigit()
                    and (score is None or score < single_char_score))):
            continue
        if (_inside_structured(bbox, text, structured)
                and not _in_caption_region(bbox, text_regions)):
            continue
        region = _smallest_region(bbox, text_regions, tolerance)
        if region is None:
            continue
        line_id = str(raw.get("element_id") or f"primary-{index + 1:04d}")
        rows: list[str] = []
        for row in (part.strip() for part in text.splitlines()):
            if not row:
                continue
            # A bare clause number on its own text-layer row belongs to the
            # visual line that follows it ("3.0.3 \n城镇分流制…").
            if rows and re.fullmatch(r"[\d.．\s]+", rows[-1]):
                rows[-1] = f"{rows[-1]} {row}"
            else:
                rows.append(row)
        if len(rows) <= 1:
            text = rows[0] if rows else text
            result.append({
                "source": "primary", "text": text, "bbox": bbox, "line_id": line_id,
                "score": score, "region_id": str(region.get("region_id") or ""),
            })
            continue
        # A multi-line primary element (e.g. an embedded scan text layer)
        # must vote line by line; as one block it would re-insert the next
        # line into the current one during the character merge.
        step = (bbox[3] - bbox[1]) / len(rows)
        for row_index, row_text in enumerate(rows):
            row_box = [bbox[0], bbox[1] + step * row_index, bbox[2], bbox[1] + step * (row_index + 1)]
            row_region = _smallest_region(row_box, text_regions, tolerance) or region
            result.append({
                "source": "primary", "text": row_text, "bbox": row_box,
                "line_id": f"{line_id}#{row_index + 1}", "score": score,
                "region_id": str(row_region.get("region_id") or ""),
            })
    return result


def _group_lines(candidates: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    candidates.sort(key=lambda row: (row["bbox"][1], row["bbox"][0],
                                     _SOURCE_PRIORITY.index(row["source"])))
    groups: list[list[dict[str, Any]]] = []
    for row in candidates:
        matches: list[tuple[float, list[dict[str, Any]]]] = []
        for group in groups:
            if any(item["source"] == row["source"] for item in group):
                continue
            score = _geometry_score(row["bbox"], _union_bbox([item["bbox"] for item in group]))
            if score is not None:
                matches.append((score, group))
        if matches:
            max(matches, key=lambda item: item[0])[1].append(row)
        else:
            groups.append([row])
    return groups


def _merge_same_source_fragments(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join adjacent fragments emitted by one engine on the same baseline."""
    pending = sorted(candidates, key=lambda row: (
        row["source"], row["bbox"][1], row["bbox"][0]))
    merged: list[dict[str, Any]] = []
    for row in pending:
        if not merged:
            merged.append(row)
            continue
        previous = merged[-1]
        same_stream = previous["source"] == row["source"]
        a, b = previous["bbox"], row["bbox"]
        min_height = min(max(0.1, a[3] - a[1]), max(0.1, b[3] - b[1]))
        vertical = _vertical_overlap(a, b) / min_height
        horizontal_gap = max(a[0], b[0]) - min(a[2], b[2])
        same_region = previous.get("region_id") == row.get("region_id")
        if (same_stream and same_region and vertical >= 0.5
                and -1.0 * min_height <= horizontal_gap <= 4.0 * min_height):
            previous_width = a[2] - a[0]
            row_width = b[2] - b[0]
            previous["text"] = (previous["text"] + row["text"] if a[0] <= b[0]
                                else row["text"] + previous["text"])
            previous["bbox"] = _union_bbox([a, b])
            if row_width > previous_width and row.get("region_id"):
                previous["region_id"] = row["region_id"]
            previous["line_id"] += f"+{row['line_id']}"
            scores = [value for value in (previous.get("score"), row.get("score"))
                      if value is not None]
            previous["score"] = min(scores) if scores else None
        else:
            merged.append(row)
    return merged


def _geometry_score(a: list[float], b: list[float]) -> float | None:
    ah, bh = max(0.1, a[3] - a[1]), max(0.1, b[3] - b[1])
    vertical = _vertical_overlap(a, b) / min(ah, bh)
    aw, bw = max(0.1, a[2] - a[0]), max(0.1, b[2] - b[0])
    horizontal = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) / min(aw, bw)
    if vertical < 0.25 or horizontal < 0.08:
        return None
    return 0.7 * vertical + 0.3 * horizontal


def _source_record(group: list[dict[str, Any]], *, source_texts: dict[str, str | None],
                   rule: str, conflicts: list[dict[str, Any]], run_id: str,
                   manifest_sha: str, raw_run_id: str, raw_manifest_sha: str,
                   preprocessing: dict[str, Any],
                   primary_prefix_preserved: bool) -> dict[str, Any]:
    by_source = {row["source"]: row for row in group}
    single_source = next(iter(by_source)) if len(by_source) == 1 else None
    return {
        "source": "three_source_vote", "line_id": "|".join(
            by_source[source]["line_id"] for source in _SOURCE_PRIORITY if source in by_source),
        "score": min((row["score"] for row in group if row["score"] is not None), default=None),
        "run_id": run_id, "run_manifest_sha256": manifest_sha,
        "preprocessing": dict(preprocessing),
        "primary_prefix_preserved": primary_prefix_preserved,
        "raw_run_id": raw_run_id, "raw_run_manifest_sha256": raw_manifest_sha,
        "source_texts": dict(source_texts), "rule": rule,
        "single_source": single_source, "conflict_fragments": deepcopy(conflicts),
    }


def _element_from_line(raw: dict[str, Any], text: str, bbox: list[float], region_id: str,
                       source: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    return {
        "element_id": str(raw.get("element_id") or f"ocr-{source['line_id']}"),
        "type": "text", "text": text, "normalized_text": text,
        "page_index": page.get("page_index"), "bbox": list(bbox),
        "coordinate_origin": "top_left", "reading_order": None,
        "heading_path": [], "clause_path": [], "parent_id": None,
        "children_ids": [], "confidence": source["score"], "source_block_id": None,
        "_v3_assigned_region_id": region_id, "_ocr_text_source": source,
    }


def _drop_fragments_inside_superset_lines(
        output: list[dict[str, Any]], line_audit: list[dict[str, Any]],
        stats: dict[str, int]) -> list[dict[str, Any]]:
    """Remove a single-source partial reading already covered by a superset line.

    When one engine reads a full line that two others split, the other
    engine's leftover fragment ("2.1.15植工") forms its own single-source
    group.  Keeping it would repeat the clause number with worse text.
    """
    supersets = [row for row in output
                 if (row.get("_ocr_text_source") or {}).get("rule")
                 in {"superset_over_fragment_majority", "superset_over_partial_reading"}]
    # If the superset's extra text is already emitted by another agreed line
    # (the other engines split the line into two groups), the superset would
    # duplicate it: fall back to the agreed fragment.
    for big in supersets:
        outer = _bbox(big.get("bbox"))
        big_text = normalize_line(str(big.get("text") or ""))
        for row in output:
            source = row.get("_ocr_text_source") or {}
            if row is big or source.get("rule") not in {"majority", "merge"}:
                continue
            box = _bbox(row.get("bbox"))
            text = normalize_line(str(row.get("text") or ""))
            if not outer or not box or not text:
                continue
            vertical = _vertical_overlap(outer, box) / max(0.1, min(outer[3] - outer[1], box[3] - box[1]))
            shared = sum((Counter(text) & Counter(big_text)).values())
            if vertical >= 0.5 and shared / len(text) >= 0.6:
                texts = (big.get("_ocr_text_source") or {}).get("source_texts") or {}
                normalized = {k: normalize_line(v) for k, v in texts.items() if v}
                counts = Counter(normalized.values())
                agreed = next((v for v, n in counts.items() if n >= 2), None)
                partial = big["_ocr_text_source"]["rule"] == "superset_over_partial_reading"
                if partial:
                    # Two readings only: fall back to the shorter one.
                    fragment = min((v for v in texts.values() if v), key=lambda v: len(normalize_line(v)))
                else:
                    fragment = next((texts[k] for k in _SOURCE_PRIORITY
                                     if agreed is not None and normalized.get(k) == agreed), None)
                if fragment is not None:
                    big["text"] = big["normalized_text"] = fragment
                    big["_ocr_text_source"]["rule"] = "merge" if partial else "majority"
                    stats[f"{'superset_over_partial_reading' if partial else 'superset_over_fragment_majority'}_lines"] -= 1
                    stats["merge_lines" if partial else "majority_lines"] += 1
                break
    # A single engine's fragment that sits inside a line two or more engines
    # read (MinerU split the line and only one piece joined the group) is a
    # second reading of that line, not new text.
    supersets = [row for row in output
                 if (row.get("_ocr_text_source") or {}).get("rule")
                 in {"superset_over_fragment_majority", "superset_over_partial_reading",
                     "majority", "merge"}]
    if not supersets:
        return output
    kept: list[dict[str, Any]] = []
    for row in output:
        source = row.get("_ocr_text_source") or {}
        box = _bbox(row.get("bbox"))
        covered = False
        if source.get("rule") == "single_source" and box:
            text = normalize_line(str(row.get("text") or ""))
            for big in supersets:
                outer = _bbox(big.get("bbox"))
                area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
                if not outer or area <= 0:
                    continue
                inter = (max(0.0, min(box[2], outer[2]) - max(box[0], outer[0]))
                         * max(0.0, min(box[3], outer[3]) - max(box[1], outer[1])))
                big_text = normalize_line(str(big.get("text") or ""))
                if not text:
                    continue
                if inter / area < 0.8:
                    # MinerU sometimes boxes a split piece between two rows;
                    # a long piece read verbatim in an adjacent voted line
                    # is that line again.
                    height = max(1.0, outer[3] - outer[1], box[3] - box[1])
                    adjacent = abs((outer[1] + outer[3]) - (box[1] + box[3])) / 2 <= 1.5 * height
                    if (adjacent and len(text) >= 6 and (big.get("_ocr_text_source") or {}).get("rule")
                            in {"majority", "merge"}):
                        # OCR noise inside the repeat ("雨污？昆接") splits
                        # the match into pieces; together they still cover it.
                        blocks = SequenceMatcher(None, text, big_text, autojunk=False).get_matching_blocks()
                        covered = sum(b.size for b in blocks if b.size >= 2) >= 0.85 * len(text)
                        if covered:
                            break
                    continue
                if (big.get("_ocr_text_source") or {}).get("rule") in {
                        "superset_over_fragment_majority", "superset_over_partial_reading"}:
                    shared = sum((Counter(text) & Counter(big_text)).values())
                    covered = shared / len(text) >= 0.6
                else:
                    # A short piece ("n——") shares its few characters with
                    # any line; only a run of the voted text marks a repeat.
                    blocks = SequenceMatcher(None, text, big_text, autojunk=False).get_matching_blocks()
                    covered = len(text) >= 4 and sum(
                        b.size for b in blocks if b.size >= 3) / len(text) >= 0.7
                if covered:
                    break
        if covered:
            stats["dropped_fragment_inside_superset_lines"] += 1
            line_audit.append({
                "bbox": box, "output_text": "", "source_texts": {},
                "rule": "dropped_fragment_inside_superset_line",
                "dropped_text": row.get("text"), "single_source": source.get("single_source"),
                "conflict_fragments": [], "merge_invariant_violations": [],
            })
            continue
        kept.append(row)
    return kept


def _smallest_region(bbox: list[float], regions: list[dict[str, Any]], tolerance: float) -> dict[str, Any] | None:
    matches = [region for region in regions if _center_inside(bbox, region.get("bbox"), tolerance)]
    return min(matches, key=lambda region: _area(_bbox(region.get("bbox")))) if matches else None


def _vertical_overlap(a: list[float] | None, b: list[float] | None) -> float:
    if a is None or b is None:
        return 0.0
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _center_inside(inner: list[float], outer_raw: Any, tolerance: float) -> bool:
    outer = _bbox(outer_raw)
    if outer is None:
        return False
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] - tolerance <= cx <= outer[2] + tolerance and outer[1] - tolerance <= cy <= outer[3] + tolerance


def _in_caption_region(bbox: list[float], text_regions: list[dict[str, Any]]) -> bool:
    """A figure/table title is printed text even when it sits inside the
    figure's box (a rotated page prints the title along the drawing)."""
    for region in text_regions:
        if str(region.get("model_label") or "").lower() != "figure_title":
            continue
        if _center_inside(bbox, region.get("bbox"), 2.0):
            return True
    return False


def _inside_structured(bbox: list[float], text: str, structured: list[dict[str, Any]]) -> bool:
    """A line belongs to a table/formula region when the region covers most of it.

    An inline formula region is a small box inside a body line; a centre
    test drops the whole line when the box happens to sit mid-line.  A
    fragment under twice the region's width without a run of Chinese text
    (the variable "ts" read on its own) still belongs to it.
    """
    line = _area(bbox)
    if not line:
        return any(_center_inside(bbox, region.get("bbox"), 0.0) for region in structured)
    for region in structured:
        outer = _bbox(region.get("bbox"))
        if outer is None:
            continue
        width = min(bbox[2], outer[2]) - max(bbox[0], outer[0])
        height = min(bbox[3], outer[3]) - max(bbox[1], outer[1])
        if width > 0 and height > 0 and width * height / line >= 0.5:
            return True
        if (_center_inside(bbox, outer, 0.0)
                and bbox[2] - bbox[0] < 2 * (outer[2] - outer[0])
                and not re.search(r"[一-鿿]{2}", text)):
            return True
    return False


def _area(bbox: list[float] | None) -> float:
    if bbox is None:
        return float("inf")
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _union_bbox(boxes: list[list[float]]) -> list[float]:
    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def _bbox(raw: Any) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        result = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None
    return result if result[0] <= result[2] and result[1] <= result[3] else None


def _score(raw: Any) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
