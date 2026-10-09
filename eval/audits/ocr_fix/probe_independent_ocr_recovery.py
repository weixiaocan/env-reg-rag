import json, glob, re
run = 'data/model_runtime/independent_ocr/runs/ocrv6-ef06411e1606ba35'
norm = lambda s: re.sub(r'[\s,，。;；:：、()（）·•~～\-—]', '', s)
# (doc sha prefix, page, original text seen in image that primary OCR lost/garbled)
probes = [
    ('c3c3df5ae9ca', 56, '有效水深宜为2.0m'),
    ('c3c3df5ae9ca', 56, '宽深比宜为1.0'),
    ('c3c3df5ae9ca', 56, '进水方向应和池中旋流方向一致'),
    ('c3c3df5ae9ca', 14, '载体表面的生物膜对污染物吸附'),
    ('c3c3df5ae9ca', 43, '协调做到适用经济美观'),
    ('f28bb600c899', 4, '大雨暴雨大暴雨特大暴雨共7个等级'),
    ('4336797dedd1', 25, '2mg/L不扣分'),
    ('4336797dedd1', 25, '口数量仅1处扣4分'),
    ('60bfe78380e9', 10, '应满足城镇内涝防治要求'),
    ('60bfe78380e9', 10, '城镇雨水排水分区应以自然地势为基础'),
    ('fd10712cb0f7', 106, '合格后进行'),
]
pages = {}
for f in glob.glob(run + '/pages/*/*.json'):
    parts = f.replace('\\', '/').split('/')
    pages[(parts[-2][:12], int(parts[-1][1:5]))] = f
docs = sorted({k[0] for k in pages})
print('docs in run:', docs)
for sha, pg, needle in probes:
    f = pages.get((sha, pg))
    if not f:
        print('NO PAGE', sha, pg, needle); continue
    p = json.load(open(f, encoding='utf-8'))
    text = norm(''.join(l['text'] for l in p['lines']))
    print('HIT ' if norm(needle) in text else 'MISS', sha, pg, needle)
