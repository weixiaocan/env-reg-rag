# T3 根因 A 只读诊断

## 结论

两个指定页面不支持“版面检测漏框导致整行被丢弃”的假设。GB50014 物理页 56 中，PP-OCRv6 找回的 5 条有效原文均与主流程已有 layout region 重叠（行 bbox 面积覆盖 68%--100%），却没有进入主 OCR `parsing_res_list`；属于结构化 OCR 识别/解析输出缺失，不是 Canonical 组装时丢弃。重庆导则物理页 25 的 28 条 PP-OCRv6 行均能在主 OCR elements 中找到对应文本，该页的问题是字符/标点差异而非整行缺失。

本诊断只读现有 page-intermediate 和独立 OCR 产物，没有修改 OCR 文本。完整 layout boxes、regions、elements、PP-OCRv6 lines 及逐行比对见 `T3_rootcause_A_inventory.json`。

## 输入与 profile

| 文档 / 页 | 主 OCR page-intermediate | PP-OCRv6 |
| --- | --- | --- |
| GB50014，物理页 56 | `data/canonical/pages/c3c3df5ae9ca.../1224404f0711/0056.json` | `data/model_runtime/independent_ocr/runs/ocrv6-ef06411e1606ba35/pages/c3c3df5ae9ca.../p0056.json` |
| 重庆导则，物理页 25 | `data/canonical/pages/4336797dedd1.../1224404f0711/0025.json` | `data/model_runtime/independent_ocr/runs/ocrv6-ef06411e1606ba35/pages/4336797dedd1.../p0025.json` |

当前语料 manifest `data/registry/corpus-0b9a1fd84e3d.json` 的 `config_hash` 为 `1224404f07114cd3578d6a87b32417fadb5587000d908e43b8916538e6b46c49`，因此选用 profile 目录 `1224404f0711`。

## 页面清单

| 页面 | layout boxes | regions | primary elements | PP-OCRv6 lines |
| --- | ---: | ---: | ---: | ---: |
| GB50014 p56 | 26 | 26 | 36 | 28 |
| 重庆导则 p25 | 12 | 12 | 29 | 28 |

`T3_rootcause_A_inventory.json` 对每个 layout box 列出 `label/coordinate`，对每个 region 列出 `region_id/model_label/bbox/text`，对每个 primary element 列出 `element_id/type/bbox/text`，并对每条 PP-OCRv6 line 列出最佳主 OCR 匹配及重叠 region。

## GB50014 p56 缺失行证据

以原 PDF 页图为准，5 条是可见的有效原文；PP-OCRv6 的 `l0007="4"` 和 `l0008="息"` 是水印/噪声识别，不计为原文恢复。

| PP line | 原图文字 / PP-OCRv6 | 主 OCR | layout/region 覆盖 | 归因 |
| --- | --- | --- | --- | --- |
| l0005 | `7.3.8 格栅间应设置通风设施和硫化氢等有毒有害气体的检测` | 无对应 element | `paragraph_title` + `text`，100% | 识别/结构化解析缺失 |
| l0010 | `7.4.1 污水厂应设置沉砂池。沉砂池应按去除相对密度2.65、` | 无对应 element | `text`，90% | 识别/结构化解析缺失 |
| l0013 | `1 最大流速应为0.30m/s，最小流速应为0.15m/s；` | 只剩独立序号 `1` | `text`，74% | 识别/结构化解析缺失 |
| l0017 | `3 有效水深宜为2.0m～3.0m，宽深比宜为1.0～1.5；` | 无该条；主 OCR 同页另一条 7.4.4-3 文本不同 | `text`，68% | 识别/结构化解析缺失 |
| l0018 | `5 进水方向应和池中旋流方向一致，出水方向应和进水方向` | 无对应 element | `text`，98% | 识别/结构化解析缺失 |

数字汇总：PP-OCRv6 28 行；其中 21 行在主 OCR 有对应，5 条有效原文缺失，2 条为独立 OCR 水印/噪声。5 条有效缺失行均落在已有 layout region 内；版面漏框 0，识别/结构化解析缺失 5，组装丢弃 0。

## 重庆导则 p25 证据

PP-OCRv6 28 行均有主 OCR element 对应，包括原图中的 `2mg/L，不扣分；` 和 `口数量仅1处，扣4分）；`。存在空格、全半角括号和标点差异，但本页“PP-OCRv6 有、主 OCR 无”的整行数为 0。因此：版面漏框 0，识别整行失败 0，组装丢弃 0；字符精度问题不在本卡修复范围。

## 主流程代码路径

1. `src/ingestion/ocr_pdf.py:381` 调用主引擎 `engine.predict(engine_image)`。
2. `src/ingestion/ocr_pdf.py:391-401` 只读取引擎返回的 `parsing_res_list`并遍历 blocks 生成 page elements。不在 `parsing_res_list` 中的行不会进入 page-intermediate。
3. `src/application/unified_pdf.py:462-463` 独立保存 layout 检测返回的 `raw_boxes`；`src/application/unified_pdf.py:585` 仅用已有 page elements 与 region bbox 相交生成 region text，不会从 layout box 再做一次逐行识别。
4. 因此 GB50014 p56 的丢失发生在引擎的识别/结构化解析输出之前或之内，不是 `canonical_assembly.py` 的后续组装过滤。

## 限制

- 本结论只覆盖任务卡指定的 2 页，不外推到全部 OCR 页。
- 0.72 字符序列相似度仅用于候选对齐；上述 5 条有效缺失与 2 条水印/噪声的分类以 PDF 原页图人工目视复核为准。
