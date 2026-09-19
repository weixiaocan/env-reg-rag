"""Render original formula crops next to literal OCR for read-only evaluation."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import textwrap
import os
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image, ImageDraw, ImageFont
import pymupdf
from src.application.formula_review import FormulaReviewService
from src.application.pdf_source_audit import file_digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--fragments',action='store_true',help='screen the remaining fragment candidates; no approval')
    parser.add_argument('--unresolved',action='store_true',help='render full-width source lines for issues without a registered correction')
    args=parser.parse_args()
    service=FormulaReviewService(ROOT,args.run_id)
    items=[r for r in service._items() if r['category'] == 'fragment_candidate'] if args.fragments else [r for r in service._items() if r['category'] in ('relation_candidate','expression_candidate')]
    if args.unresolved:
        findings=json.loads((ROOT/'data/registry/formula-visual-findings.json').read_text(encoding='utf-8'))
        corrections=json.loads((ROOT/'data/registry/formula-corrections.json').read_text(encoding='utf-8'))
        unresolved={r['candidate_id'] for r in findings['records'] if r['finding']=='suspected_transcription_or_crop_issue'}-{r['candidate_id'] for r in corrections['records']}
        items=[r for r in service._items() if r['candidate_id'] in unresolved]
    target=service.path.parent/('visual-unresolved' if args.unresolved else 'visual-fragments' if args.fragments else 'visual-review')
    if target.is_symlink() or not target.resolve().is_relative_to(ROOT/'data/model_runtime'):
        raise ValueError('output escape')
    target.mkdir(exist_ok=True)
    font=ImageFont.truetype(str(Path(os.environ.get('WINDIR',''))/'Fonts/msyh.ttc'),14 if args.fragments else 18)
    count, columns, height = (2,2,300) if args.unresolved else (72,4,160) if args.fragments else (12,2,330)
    width=1600//columns
    for start in range(0,len(items),count):
        group=items[start:start+count]
        sheet=Image.new('RGB',(1600,((count+columns-1)//columns)*height),'white'); draw=ImageDraw.Draw(sheet)
        for offset,item in enumerate(group):
            x=(offset%columns)*width; y=(offset//columns)*height
            doc=service.documents.get('doc_'+item['file_sha256'][:16])
            if file_digest(doc.local_path)!=item['file_sha256']: raise ValueError('source checksum')
            with pymupdf.open(doc.local_path) as pdf:
                page=pdf[item['physical_page']-1]; box=pymupdf.Rect(item['bbox'])
                clip=pymupdf.Rect(0,max(0,box.y0-25),page.rect.width,min(page.rect.height,box.y1+25)) if args.unresolved else pymupdf.Rect(max(0,box.x0-8),max(0,box.y0-8),min(page.rect.width,box.x1+8),min(page.rect.height,box.y1+8))
                png=page.get_pixmap(dpi=200,clip=clip,alpha=False).tobytes('png')
                crop=Image.open(io.BytesIO(png)); crop.thumbnail((width-20,145 if args.unresolved else 65 if args.fragments else 155))
                sheet.paste(crop,(x+10,y+35))
            if file_digest(doc.local_path)!=item['file_sha256']: raise ValueError('source changed')
            label=f'{start+offset} {item["candidate_id"]}' if args.unresolved else f'{start+offset} p{item["physical_page"]} {item["candidate_id"].split("-")[-1]}' if args.fragments else f'{start+offset} {item["candidate_id"]}'
            draw.text((x+8,y+3),label,font=font,fill='black')
            for line_no,line in enumerate(textwrap.wrap(item['raw_latex'] or '',width=68 if not args.fragments else 43)[:4 if args.unresolved else 3 if args.fragments else 5]):
                draw.text((x+8,y+(175 if args.unresolved else 102 if args.fragments else 195)+line_no*(24 if not args.fragments else 17)),line,font=font,fill='black')
            draw.rectangle((x,y,x+width-1,y+height-1),outline='gray')
        sheet.save(target/f'sheet-{start//count:02d}.png')
    print(f'{len(items)} candidates; {(len(items)+count-1)//count} sheets; original crops only, not approval')


if __name__=='__main__': main()
