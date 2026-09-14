"""P1 数据资产盘点。

产出《数据盘点表》CSV，回答三个问题：
1. 每份文档是否有可用文字层，以及 M2 应尝试哪条解析路径；
2. 哪些来源、版本和效力字段仍需人工核验；
3. 哪些文件字节完全相同，哪些只是标准号相同。

用法::

    python scripts/data_inventory.py --out data/registry/inventory.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

from pypdf import PdfReader


@dataclass
class InventoryRow:
    """《数据盘点表》一行 = 一份原始文档。"""

    file_name: str
    rel_path: str
    sha256: str
    size_mb: float
    pages: int
    text_coverage: float
    extraction_route_candidate: str
    std_no: str
    metadata_source: str
    document_kind: str
    jurisdiction: str
    official_source_uri: str
    source_review: str
    effective_status: str
    same_standard_as: str
    exact_duplicate_of: str
    inventory_status: str
    note: str


# ---------------------------------------------------------------- 标准号识别

# 文件名里出现这些词，多半是政策文件而非标准
POLICY_HINTS = [
    "规划", "通知", "方案", "指南", "意见", "实施方案", "导则", "征求意见稿",
    "十四五", "十五五", "行动计划", "办法", "规定",
]

# 标准前缀（按长度降序，保证 T/CECS 优先于 CECS 被匹配）
_PREFIX = (
    r"GB/T|GBT|DBJ/T|DBJT|DB/T|DBT|QX/T|QXT|T/CECS|TCECS|T/CMSA|TCMSA|"
    r"GB|CJJ|JGJ|SL|HJ|CJ|CECS|DBJ|DB|QX"
)

# 「前缀 + 编号 + 年份」：GB 50014-2021 / DBJ/T 13-477-2024 / T/CECS 758-2020
PAT_NUM_YEAR = re.compile(rf"(?:^|[^A-Z])({_PREFIX})\s*([0-9]+(?:-[0-9]+)?)\s*-\s*([0-9]{{4}})(?![0-9])")
# 「前缀 + 编号 + 年份」粘连写法：TCECS1764-2024
PAT_GLUED = re.compile(rf"(?:^|[^A-Z])(TCECS|CECS)\s*([0-9]+)\s*-\s*([0-9]{{4}})(?![0-9])")
# 「前缀 + 年份」（团体标准常无编号）：T/CMSA-2019
PAT_YEAR_ONLY = re.compile(rf"(?:^|[^A-Z])({_PREFIX})\s*-\s*([0-9]{{4}})(?![0-9])")
# 地方标准常把行政区划代码放在 DB 与 /T 之间：DB4201/T 651-2021。
PAT_LOCAL_DB = re.compile(
    r"(?:^|[^A-Z])DB\s*([0-9]{4})\s*/\s*T\s*([0-9]+)\s*-\s*([0-9]{4})(?![0-9])"
)

# 前缀规范化：把各种写法统一到官方写法
_CANON = {
    "GBT": "GB/T", "QXT": "QX/T", "TCECS": "T/CECS", "CECS": "T/CECS",
    "TCMSA": "T/CMSA", "DBJT": "DBJ/T", "DBT": "DB/T",
}

_LEVEL_BY_PREFIX = [
    (("GB", "GB/T"), "national"),
    (("CJJ", "JGJ", "SL", "HJ", "CJ", "QX", "QX/T"), "industry"),
    (("DB", "DBJ", "DB/T", "DBJ/T"), "local"),
    (("T/CECS", "T/CMSA"), "group"),
]


def _canon_prefix(prefix: str) -> str:
    p = re.sub(r"\s+", "", prefix.upper())
    return _CANON.get(p, p)


def _level_of(std_no: str) -> str:
    for prefixes, lvl in _LEVEL_BY_PREFIX:
        if std_no.startswith(prefixes):
            return lvl
    if std_no.startswith("T/"):
        return "group"
    return "unknown"


def normalize_name(name: str) -> str:
    """归一化文件名：全角横线、加号与下划线统一为分隔符。

    注意 ``_`` 有两种含义：
    - 字母之间的下划线是 ``/`` 的转义（``DBJ_T`` = ``DBJ/T``、``T_CECS`` = ``T/CECS``）
    - 其余位置的下划线是空格的转义
    先处理前者，否则 ``DBJ_T 13-477-2024`` 会被切成 ``DBJ T`` 而匹配不到。
    """
    s = name.upper()
    for ch in ("－", "—", "–", "‐", "‑"):
        s = s.replace(ch, "-")
    s = re.sub(r"(?<=[A-Z])_(?=[A-Z])", "/", s)  # DBJ_T -> DBJ/T
    s = re.sub(r"[+_]+", " ", s)  # 剩余分隔符 -> 空格
    return re.sub(r"\s+", " ", s)


# 文件名完全不含标准号时的人工补录表。
# 格式: 文件名片段 -> (标准号, 中文标题)
# 来源标注为 manual，盘点时需要人工复核（见 CSV 的 note 列）。
MANUAL_META: dict[str, tuple[str, str]] = {
    "城镇内涝防治技术规范": ("GB 51222-2017", "城镇内涝防治技术规范"),
}


def extract_std_no(name: str) -> tuple[str, str]:
    """从文件名抽取标准号与层级。返回 (标准号, 层级)，失败返回 ("", ...)。"""
    s = normalize_name(name)

    local = PAT_LOCAL_DB.search(s)
    if local:
        return f"DB{local.group(1)}/T {local.group(2)}-{local.group(3)}", "local"

    m = PAT_NUM_YEAR.search(s) or PAT_GLUED.search(s)
    if m:
        prefix = _canon_prefix(m.group(1))
        num, year = m.group(2), m.group(3)
        std_no = f"{prefix} {num}-{year}"
        return std_no, _level_of(std_no)

    m = PAT_YEAR_ONLY.search(s)
    if m:
        prefix = _canon_prefix(m.group(1))
        std_no = f"{prefix}-{m.group(2)}"
        return std_no, _level_of(std_no)

    for frag, (std_no, _title) in MANUAL_META.items():
        if frag in name:
            return std_no, _level_of(std_no)

    for kw in POLICY_HINTS:
        if kw in name:
            return "", "policy"
    return "", "unknown"


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    """计算文件 SHA-256，用于识别字节完全相同的副本。"""
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def extract_std_no_from_pdf(path: Path, max_pages: int = 3) -> tuple[str, str]:
    """文件名抽不到时，回退到 PDF 正文前几页找标准号。

    封面与扉页通常会印标准号，如「GB 50014-2021」「中华人民共和国国家标准」。
    """
    try:
        reader = PdfReader(str(path))
        for i in range(min(max_pages, len(reader.pages))):
            txt = (reader.pages[i].extract_text() or "")
            if not txt.strip():
                continue
            flat = normalize_name(txt.replace("\n", " ").replace("\r", " "))
            local = PAT_LOCAL_DB.search(flat)
            if local:
                return (
                    f"DB{local.group(1)}/T {local.group(2)}-{local.group(3)}",
                    "local",
                )
            m = PAT_NUM_YEAR.search(flat) or PAT_GLUED.search(flat)
            if m:
                prefix = _canon_prefix(m.group(1))
                std_no = f"{prefix} {m.group(2)}-{m.group(3)}"
                return std_no, _level_of(std_no)
            m = PAT_YEAR_ONLY.search(flat)
            if m:
                prefix = _canon_prefix(m.group(1))
                std_no = f"{prefix}-{m.group(2)}"
                return std_no, _level_of(std_no)
    except Exception:
        pass
    return "", "unknown"


REGION_PATTERN = re.compile(
    r"(北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|"
    r"河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|内蒙古|广西|西藏|宁夏|新疆|"
    r"武汉|广州|深圳|成都|杭州|南京)"
)


def guess_region(name: str, std_no: str) -> str:
    if std_no.startswith(("DB", "DBJ")):
        m = REGION_PATTERN.search(name)
        if m:
            return m.group(1)
        return "未知(地方标准)"
    m = REGION_PATTERN.search(name)
    if m:
        return m.group(1)
    return "全国"


def probe_pdf(path: Path) -> tuple[int, float, str]:
    """返回页数、抽样文字覆盖率和供 M2 复核的解析路径候选。"""
    reader = PdfReader(str(path))
    n = len(reader.pages)
    if n == 0:
        return 0, 0.0, "error"

    # 抽样，避免大文件全量解析过慢
    step = max(1, n // 40)
    idx = list(range(0, n, step))[:40]

    text_ok = 0
    for i in idx:
        page = reader.pages[i]
        txt = (page.extract_text() or "").strip()
        if len(txt) > 50:
            text_ok += 1

    coverage = text_ok / len(idx)
    if coverage >= 0.8:
        route = "native"
    elif coverage > 0:
        route = "hybrid_review"
    else:
        route = "full_ocr_review"
    return n, coverage, route


def detect_relationships(rows: list[InventoryRow]) -> None:
    """标注标准关系和字节级重复，不把两者混为一谈。"""
    by_std: dict[str, list[InventoryRow]] = {}
    for r in rows:
        if r.std_no:
            by_std.setdefault(r.std_no, []).append(r)

    for group in by_std.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: (-r.text_coverage, len(r.file_name), r.file_name))
        primary = group[0]
        for other in group[1:]:
            other.same_standard_as = primary.file_name

    # 只有 SHA-256 相同才是完全重复文件。
    by_hash: dict[str, list[InventoryRow]] = {}
    for r in rows:
        if not r.sha256:
            continue
        by_hash.setdefault(r.sha256, []).append(r)

    for group in by_hash.values():
        if len(group) < 2:
            continue
        group.sort(
            key=lambda r: (not bool(r.std_no), -r.text_coverage, len(r.file_name), r.file_name)
        )
        primary = group[0]
        for other in group[1:]:
            other.exact_duplicate_of = primary.file_name
            other.inventory_status = "exact_duplicate"
            other.note = f"SHA-256 相同；主文件 {primary.file_name}"


def main() -> None:
    ap = argparse.ArgumentParser(description="数据资产盘点")
    ap.add_argument("--raw-dir", default="data/raw", help="原始文档目录")
    ap.add_argument(
        "--out",
        default="data/registry/inventory.csv",
        help="机器生成的盘点 CSV；人工来源审核信息单独维护",
    )
    args = ap.parse_args()

    raw = Path(args.raw_dir)
    files = sorted(raw.rglob("*.pdf"))
    if not files:
        print(f"[!] 未在 {raw} 下找到 PDF", file=sys.stderr)
        return

    rows: list[InventoryRow] = []
    for f in files:
        name = f.name
        std_no, level = extract_std_no(name)
        if std_no and any(frag in name for frag in MANUAL_META):
            metadata_source = "manual"
        else:
            metadata_source = "filename" if std_no else ""
        if not std_no and level != "policy":
            # 文件名抽不到（命名不规范）→ 回退到 PDF 封面正文
            std_no, level = extract_std_no_from_pdf(f)
            metadata_source = "pdf_text" if std_no else ""
        try:
            pages, coverage, route = probe_pdf(f)
            err = ""
        except Exception as exc:  # 解析失败也要留在表里，不能静默跳过
            pages, coverage, route = 0, 0.0, "error"
            err = f"解析失败: {type(exc).__name__}: {exc}"[:80]

        inventory_status = "error" if err else "candidate"
        note = err
        if not note and route == "full_ocr_review":
            note = "抽样页未发现可用文字层；M2 需确认 OCR 路径"
        elif not note and route == "hybrid_review":
            note = "文字层覆盖不完整；M2 需按页确认原生解析或局部 OCR"

        document_kind = "policy" if level == "policy" else (
            "standard" if level in {"national", "industry", "local", "group"} else "unknown"
        )

        rows.append(
            InventoryRow(
                file_name=name,
                rel_path=str(f.as_posix()),
                sha256=file_sha256(f),
                size_mb=round(f.stat().st_size / 1024 / 1024, 2),
                pages=pages,
                text_coverage=round(coverage, 3),
                extraction_route_candidate=route,
                std_no=std_no,
                metadata_source=metadata_source,
                document_kind=document_kind,
                jurisdiction=guess_region(name, std_no),
                official_source_uri="",
                source_review="needs_review",
                effective_status="unknown",
                same_standard_as="",
                exact_duplicate_of="",
                inventory_status=inventory_status,
                note=note,
            )
        )

    detect_relationships(rows)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(asdict(rows[0]).keys()))
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))

    # ---- 汇总报告 ----
    candidates = [r for r in rows if r.inventory_status == "candidate"]
    ocr = [r for r in candidates if r.extraction_route_candidate == "full_ocr_review"]
    hybrid = [r for r in candidates if r.extraction_route_candidate == "hybrid_review"]
    dup = [r for r in rows if r.inventory_status == "exact_duplicate"]
    related = [r for r in rows if r.same_standard_as and not r.exact_duplicate_of]
    err = [r for r in rows if r.inventory_status == "error"]
    nostd = [r for r in candidates if not r.std_no and r.document_kind != "policy"]

    print(f"\n数据盘点完成：{len(rows)} 份文档 -> {out}\n")
    print(f"  待来源审核候选: {len(candidates):>2} 份")
    print(f"  无文字层待复核: {len(ocr):>2} 份")
    print(f"  混合解析待复核: {len(hybrid):>2} 份")
    print(f"  SHA-256 重复  : {len(dup):>2} 份")
    print(f"  同标准非重复  : {len(related):>2} 份")
    print(f"  解析失败      : {len(err):>2} 份")
    print(f"  未识别标准号  : {len(nostd):>2} 份（政策文件或命名不规范）")
    print(f"  总页数        : {sum(r.pages for r in rows):>4} 页")
    print(f"  OCR 候选页数  : {sum(r.pages for r in ocr):>4} 页")

    if ocr:
        print("\n无可用文字层、M2 需复核 OCR 的文档（按页数降序）：")
        for r in sorted(ocr, key=lambda x: -x.pages):
            print(f"  {r.pages:>4} 页  {r.file_name}")

    if dup:
        print("\nSHA-256 完全相同的文件：")
        for r in dup:
            print(f"  {r.file_name}  -> 主文件: {r.exact_duplicate_of}")

    if related:
        print("\n标准号相同但文件内容不同（不得自动当作重复）：")
        for r in related:
            print(f"  {r.file_name}  -> 同标准参考: {r.same_standard_as}")

    if nostd:
        print("\n未识别标准号（policy = 政策文件，无标准号属正常；unknown = 需人工补录）：")
        for r in nostd:
            print(f"  [{r.document_kind:8s}] {r.file_name}")

    manual = [r for r in rows if r.metadata_source == "manual"]
    if manual:
        print("\n人工补录的标准号（需人工复核，见 MANUAL_META）：")
        for r in manual:
            print(f"  {r.std_no:<20s} <- {r.file_name}")

    from_pdf = [r for r in rows if r.metadata_source == "pdf_text"]
    if from_pdf:
        print(f"\n从 PDF 正文补抽到标准号 {len(from_pdf)} 份（文件名不规范）：")
        for r in from_pdf:
            print(f"  {r.std_no:<20s} <- {r.file_name}")


if __name__ == "__main__":
    main()
