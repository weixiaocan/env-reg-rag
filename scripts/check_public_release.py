"""Validate the candidate public tree and bundled source-PDF inventory."""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


EXPECTED = {
    "pdf_files": 27,
    "unique_contents": 22,
    "original_candidates": 23,
    "official_copies": 4,
    "duplicate_copies": 5,
    "total_pages": 1252,
}

IGNORED_PROBES = (
    ".env",
    "data/registry/release-check.sqlite3",
    "data/qdrant/release-check.bin",
    "data/observability/release-check.jsonl",
    "data/backups/release-check.snapshot",
    "data/canonical/release-check.json",
    "data/evidence/release-check.json",
    "data/retrieval/release-check.json",
    "data/eval_results/release-check.json",
    "logs/release-check.log",
    "models/release-check.bin",
)

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{30,}\b"),
)
SECRET_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*=\s*(.*?)\s*$"
)
LOCAL_PATH = re.compile(
    r"(?i)(?:[A-Z]:[\\/](?:Users|Documents and Settings|huangxh)[\\/]"
    r"|/ho" r"me/[^/\s]+/|/Us" r"ers/[^/\s]+/)"
)
PLACEHOLDERS = ("replace-with-", "your-", "example", "placeholder", "changeme", "<", "${")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_candidate_paths(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail)
    return {
        item.decode("utf-8", errors="strict").replace("\\", "/")
        for item in result.stdout.split(b"\0")
        if item
    }


def is_ignored(root: Path, rel_path: str) -> bool:
    return subprocess.run(
        ["git", "check-ignore", "--quiet", "--", rel_path],
        cwd=root,
        check=False,
    ).returncode == 0


def personal_markers(root: Path) -> set[str]:
    markers = {
        item.strip().casefold()
        for item in os.environ.get("PUBLIC_RELEASE_FORBIDDEN_NAMES", "").split(",")
        if item.strip()
    }
    for variable in ("USERNAME", "USER"):
        value = os.environ.get(variable, "").strip()
        if len(value) >= 3:
            markers.add(value.casefold())
    result = subprocess.run(
        ["git", "config", "--get", "user.name"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    git_name = result.stdout.strip()
    if result.returncode == 0 and len(git_name) >= 2:
        markers.add(git_name.casefold())
    return markers


def check_inventory(root: Path, errors: list[str]) -> None:
    inventory_path = root / "data" / "registry" / "inventory.csv"
    raw_dir = root / "data" / "raw"
    if not inventory_path.is_file():
        errors.append("缺少 data/registry/inventory.csv")
        return

    with inventory_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    actual = {path.relative_to(root).as_posix(): path for path in raw_dir.rglob("*.pdf")}
    registered = {row["rel_path"].replace("\\", "/") for row in rows}

    if len(rows) != EXPECTED["pdf_files"]:
        errors.append(f"inventory.csv 应有 27 行，实际 {len(rows)} 行")
    if len(registered) != len(rows):
        errors.append("inventory.csv 包含重复 rel_path")
    if set(actual) != registered:
        errors.append(
            f"PDF 与清单路径不一致；未登记={sorted(set(actual) - registered)}，"
            f"无文件={sorted(registered - set(actual))}"
        )

    by_name = {row["file_name"]: row for row in rows}
    digests: Counter[str] = Counter()
    relation_count = 0
    for row in rows:
        rel_path = row["rel_path"].replace("\\", "/")
        path = actual.get(rel_path)
        if path is None:
            continue
        actual_digest = file_sha256(path)
        digests[actual_digest] += 1
        if actual_digest != row["sha256"]:
            errors.append(f"SHA-256 不一致: {rel_path}")
        duplicate_of = row["exact_duplicate_of"].strip()
        if duplicate_of:
            relation_count += 1
            primary = by_name.get(duplicate_of)
            if primary is None or primary["sha256"] != row["sha256"]:
                errors.append(f"无效的 exact_duplicate_of 关系: {rel_path}")

    official = sum(
        "/_official_verification/" in f"/{row['rel_path'].replace(chr(92), '/')}"
        for row in rows
    )
    measured = {
        "unique_contents": len(digests),
        "original_candidates": len(rows) - official,
        "official_copies": official,
        "duplicate_copies": sum(count - 1 for count in digests.values()),
        "total_pages": sum(int(row["pages"]) for row in rows),
    }
    for key, actual_value in measured.items():
        if actual_value != EXPECTED[key]:
            errors.append(f"{key} 应为 {EXPECTED[key]}，实际 {actual_value}")
    if relation_count != EXPECTED["duplicate_copies"]:
        errors.append(f"exact_duplicate_of 关系应有 5 条，实际 {relation_count} 条")


def check_ignore_policy(root: Path, errors: list[str]) -> None:
    for pdf in sorted((root / "data" / "raw").rglob("*.pdf")):
        rel_path = pdf.relative_to(root).as_posix()
        if is_ignored(root, rel_path):
            errors.append(f"原始 PDF 仍被 .gitignore 排除: {rel_path}")
    for rel_path in IGNORED_PROBES:
        if not is_ignored(root, rel_path):
            errors.append(f"运行数据或派生产物未被忽略: {rel_path}")


def check_candidate_tree(root: Path, errors: list[str]) -> None:
    try:
        candidates = git_candidate_paths(root)
    except RuntimeError as exc:
        errors.append(f"无法读取 Git 候选树: {exc}")
        return
    if ".env" in candidates:
        errors.append(".env 将进入公开提交")

    pdf_paths = {
        path.relative_to(root).as_posix()
        for path in (root / "data" / "raw").rglob("*.pdf")
    }
    missing_pdfs = sorted(pdf_paths - candidates)
    if missing_pdfs:
        errors.append(f"以下 PDF 不会进入 Git 候选树: {missing_pdfs}")
    candidate_pdfs = {path for path in candidates if path.lower().endswith(".pdf")}
    unexpected_pdfs = sorted(candidate_pdfs - pdf_paths)
    if unexpected_pdfs:
        errors.append(f"data/raw 之外存在将公开的 PDF: {unexpected_pdfs}")

    markers = personal_markers(root)
    for rel_path in sorted(candidates):
        path = root / rel_path
        if not path.is_file() or path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".gif"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        folded = content.casefold()
        for number, line in enumerate(content.splitlines(), start=1):
            if LOCAL_PATH.search(line):
                errors.append(f"发现绝对本机路径: {rel_path}:{number}")
            if any(pattern.search(line) for pattern in SECRET_PATTERNS):
                errors.append(f"发现疑似真实密钥: {rel_path}:{number}")
            match = SECRET_ASSIGNMENT.match(line)
            if match:
                value = match.group(1).strip().strip("\"'")
                if value and not any(marker in value.casefold() for marker in PLACEHOLDERS):
                    errors.append(f"发现非占位密钥配置: {rel_path}:{number}")
        if any(marker and marker in folded for marker in markers):
            errors.append(f"发现可能的个人姓名或本机用户名: {rel_path}")


def run_checks(root: Path) -> list[str]:
    errors: list[str] = []
    if not (root / "DATA_NOTICE.md").is_file():
        errors.append("缺少 DATA_NOTICE.md")
    check_inventory(root, errors)
    check_ignore_policy(root, errors)
    check_candidate_tree(root, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="检查公开候选树、PDF 清单和发布卫生")
    parser.add_argument("--root", default=".", help="仓库根目录")
    args = parser.parse_args()
    errors = run_checks(Path(args.root).resolve())
    if errors:
        print("公开发布检查失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "公开发布检查通过：27 个 PDF、22 个独立内容、5 个重复副本、"
        "23 份原候选语料、4 份官方核验副本、1252 页；发布卫生门控通过。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
