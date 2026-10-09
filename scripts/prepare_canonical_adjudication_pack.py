"""Render one immutable, deduplicated adjudication pack for Canonical QA."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import sys
import tempfile
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.canonical_adjudication_pack import (  # noqa: E402
    build_adjudication_items,
    json_bytes,
    sha256_file,
)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"immutable adjudication artifact conflict: {path}")
        return
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _load_inputs(corpus_version: str, quality_report: Path):
    manifest_path = ROOT / "data/registry" / f"{corpus_version}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pi_path = ROOT / str(manifest["canonical_documents"])
    documents = [json.loads(line) for line in pi_path.read_text(encoding="utf-8").splitlines() if line]
    canonical_dir = ROOT / "data/canonical/v2" / corpus_version
    canonicals = [json.loads(path.read_text(encoding="utf-8"))
                  for path in sorted(canonical_dir.glob("*.canonical.json"))]
    ledger_path = quality_report / "quality_ledger.jsonl"
    ledger = {"entries": [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()
                          if line]}
    assets = {str(row["sha256"]): row for row in manifest["assets"]}
    return manifest_path, pi_path, ledger_path, documents, canonicals, assets


def _render_evidence(out_dir: Path, items: list[dict], assets: dict[str, dict]) -> dict[str, dict]:
    by_page: dict[tuple[str, int], list[dict]] = {}
    for item in items:
        source = item["source"]
        by_page.setdefault((source["file_sha256"], int(source["physical_page"])), []).append(item)
    evidence: dict[str, dict] = {}
    open_docs: dict[str, pymupdf.Document] = {}
    try:
        for page_index, ((sha, number), page_items) in enumerate(sorted(by_page.items()), 1):
            pdf = open_docs.get(sha)
            if pdf is None:
                pdf = pymupdf.open(ROOT / str(assets[sha]["canonical_rel_path"]))
                open_docs[sha] = pdf
            page = pdf[number - 1]
            page_rel = Path("pages") / sha / f"p{number:04d}.jpg"
            page_path = out_dir / page_rel
            if not page_path.exists():
                pix = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
                _atomic_write(page_path, pix.tobytes("jpg", jpg_quality=82))
            for item in page_items:
                bbox = item["source"].get("bbox")
                crop_rel = None
                crop_hash = None
                if isinstance(bbox, list) and len(bbox) == 4:
                    clip = pymupdf.Rect(bbox) & page.rect
                    if not clip.is_empty and clip.get_area() > 0:
                        crop_rel = Path("crops") / f"{item['item_id']}.jpg"
                        crop_path = out_dir / crop_rel
                        if not crop_path.exists():
                            pix = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), clip=clip,
                                                  alpha=False)
                            _atomic_write(crop_path, pix.tobytes("jpg", jpg_quality=90))
                        crop_hash = sha256_file(crop_path)
                evidence[item["item_id"]] = {
                    "page_image": page_rel.as_posix(),
                    "page_image_sha256": sha256_file(page_path),
                    "crop_image": crop_rel.as_posix() if crop_rel else None,
                    "crop_image_sha256": crop_hash,
                }
            if page_index % 25 == 0:
                print(f"rendered {page_index}/{len(by_page)} adjudication pages", flush=True)
    finally:
        for pdf in open_docs.values():
            pdf.close()
    return evidence


def _html(items: list[dict], corpus_version: str, ledger_hash: str) -> bytes:
    payload = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(f"Canonical 人工裁定包 · {corpus_version}")
    document = f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;margin:0;background:#f5f6f8;color:#17202a}}header{{position:sticky;top:0;background:#fff;padding:12px 18px;border-bottom:1px solid #ccd1d9;z-index:2}}main{{max-width:1500px;margin:auto;padding:18px}}.grid{{display:grid;grid-template-columns:minmax(420px,1.2fr) minmax(360px,1fr);gap:18px}}.card{{background:#fff;border:1px solid #d9dee7;border-radius:10px;padding:14px}}img{{max-width:100%;height:auto}}pre{{white-space:pre-wrap;word-break:break-word;max-height:360px;overflow:auto;background:#f7f8fa;padding:10px}}button,select{{padding:7px 10px;margin-right:6px}}label{{display:block;margin:8px 0}}.muted{{color:#596575;font-size:13px}}@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}</style></head><body>
<header><b>{title}</b> <span id=\"pos\"></span><br><span class=\"muted\">质量台账 SHA-256: {ledger_hash}；每题一个唯一源对象，选择保存在本浏览器，可导出 JSONL。</span></header>
<main><p><select id=\"kind\"><option value=\"\">全部类型</option><option>text</option><option>table</option><option>formula</option><option>image</option><option>unknown</option></select><button onclick=\"move(-1)\">上一项</button><button onclick=\"move(1)\">下一项</button><button onclick=\"exportRows()\">导出已裁定 JSONL</button></p><div id=\"view\"></div></main>
<script>const items={payload};let filtered=items,idx=0;const saved=JSON.parse(localStorage.getItem('canonicalQaRulings')||'{{}}');
function esc(x){{return String(x??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}}[c]));}}
function render(){{if(!filtered.length){{view.innerHTML='<p>无项目</p>';return}};idx=Math.max(0,Math.min(idx,filtered.length-1));const x=filtered[idx],s=x.source,q=x.question,e=x.evidence_images||{{}},v=saved[x.item_id]||{{}};pos.textContent=` ${{idx+1}} / ${{filtered.length}}（总计 ${{items.length}}）`;view.innerHTML=`<div class=grid><section class=card><h2>${{esc(x.item_id)}} · ${{esc(x.kind)}}</h2><p>${{esc(s.file_name)}} · 物理页 ${{s.physical_page}}</p><p class=muted>PDF ${{esc(s.file_sha256)}}<br>bbox ${{esc(JSON.stringify(s.bbox))}}</p>${{e.crop_image?`<h3>疑点裁剪</h3><img src=\"${{esc(e.crop_image)}}\">`:''}}<h3>原 PDF 页图</h3><img src=\"${{esc(e.page_image)}}\"></section><section class=card><h3>具体疑点</h3><p>${{esc(q.specific_doubt)}}</p><h3>提取结果</h3><pre>${{esc(JSON.stringify(x.extracted_result,null,2))}}</pre><h3>影响范围</h3><pre>${{esc(JSON.stringify(x.impact,null,2))}}</pre><h3>裁定</h3>${{q.suggested_options.map(o=>`<label><input type=radio name=r value=\"${{esc(o.value)}}\" ${{v.ruling===o.value?'checked':''}} onchange=\"save('${{esc(x.item_id)}}',this.value)\"> ${{esc(o.label)}}</label>`).join('')}}<label>说明（更正时必填）<textarea id=note rows=5 style=\"width:100%\" oninput=\"saveNote('${{esc(x.item_id)}}',this.value)\">${{esc(v.comment||'')}}</textarea></label></section></div>`;}}
function save(id,ruling){{saved[id]={{...(saved[id]||{{}}),ruling}};localStorage.setItem('canonicalQaRulings',JSON.stringify(saved));}}function saveNote(id,comment){{saved[id]={{...(saved[id]||{{}}),comment}};localStorage.setItem('canonicalQaRulings',JSON.stringify(saved));}}function move(d){{idx=(idx+d+filtered.length)%filtered.length;render();scrollTo(0,0)}}kind.onchange=()=>{{filtered=items.filter(x=>!kind.value||x.kind===kind.value);idx=0;render()}};
function exportRows(){{const rows=items.filter(x=>saved[x.item_id]?.ruling).map(x=>({{item_id:x.item_id,source_file_sha256:x.source.file_sha256,physical_page:x.source.physical_page,ruling:saved[x.item_id].ruling,comment:saved[x.item_id].comment||'',adjudicator:'',adjudicated_at:''}}));const b=new Blob([rows.map(x=>JSON.stringify(x)).join('\\n')+'\\n'],{{type:'application/jsonl'}}),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='canonical_quality_rulings.jsonl';a.click();}}render();</script></body></html>"""
    return document.encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-version", required=True)
    parser.add_argument("--quality-report", required=True, type=Path)
    parser.add_argument("--independent-ocr-run", required=True, type=Path)
    args = parser.parse_args()
    report_dir = ROOT / args.quality_report
    ocr_dir = ROOT / args.independent_ocr_run
    manifest_path, pi_path, ledger_path, documents, canonicals, assets = _load_inputs(
        args.corpus_version, report_dir)
    ledger_lines = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line]
    items = build_adjudication_items({"entries": ledger_lines}, documents, canonicals, assets)
    config = {"schema_version": "canonical-adjudication-pack-v1", "page_render_scale": 1.5,
              "crop_render_scale": 2.0, "page_jpeg_quality": 82, "crop_jpeg_quality": 90}
    identity = {
        "corpus_version": args.corpus_version,
        "quality_ledger_sha256": sha256_file(ledger_path),
        "independent_ocr_manifest_sha256": sha256_file(ocr_dir / "run_manifest.json"),
        "config": config,
        "item_ids": [row["item_id"] for row in items],
    }
    pack_hash = hashlib.sha256(json_bytes(identity)).hexdigest()
    out_dir = ROOT / "data/quality" / args.corpus_version / f"adjudication-{pack_hash[:16]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence = _render_evidence(out_dir, items, assets)
    for item in items:
        item["evidence_images"] = evidence[item["item_id"]]
    rows = b"".join(json_bytes(item, indent=None) + b"\n" for item in items)
    _atomic_write(out_dir / "adjudication_items.jsonl", rows)
    template_rows = b"".join(json_bytes({
        "item_id": item["item_id"], "source_file_sha256": item["source"]["file_sha256"],
        "physical_page": item["source"]["physical_page"], "ruling": "", "comment": "",
        "adjudicator": "", "adjudicated_at": "",
    }, indent=None) + b"\n" for item in items)
    _atomic_write(out_dir / "rulings.template.jsonl", template_rows)
    _atomic_write(out_dir / "index.html", _html(items, args.corpus_version, identity["quality_ledger_sha256"]))
    counts: dict[str, int] = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    pack_manifest = {
        **identity, "pack_id": out_dir.name, "item_count": len(items), "item_counts_by_kind": counts,
        "unique_page_count": len({(i["source"]["file_sha256"], i["source"]["physical_page"])
                                  for i in items}),
        "input_hashes": {"manifest": sha256_file(manifest_path), "page_intermediate": sha256_file(pi_path)},
        "artifacts": {
            "adjudication_items.jsonl": sha256_file(out_dir / "adjudication_items.jsonl"),
            "rulings.template.jsonl": sha256_file(out_dir / "rulings.template.jsonl"),
            "index.html": sha256_file(out_dir / "index.html"),
        },
        "status": "awaiting_human_adjudication",
        "note": "No ruling is pre-filled. Automatic model agreement is not a human assertion.",
    }
    _atomic_write(out_dir / "pack_manifest.json", json_bytes(pack_manifest))
    print(json.dumps(pack_manifest, ensure_ascii=False, indent=2))
    print(out_dir.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
