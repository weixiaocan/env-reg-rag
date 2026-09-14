# E-01G Canonical Document 与页面定位验收

## 1. 阶段结论

M2 的 8 份文档、40 个代表页面已经从解析器专有输出归一化为项目自有 Canonical Document。后续证据切分、检索和 LangChain 不直接依赖 PyMuPDF、PaddleOCR 或 Docling 的数据结构，只读取这一层稳定模型。

本产物仍是 `m2_representative_pages_only` 实验范围，不代表 8 份 PDF 已全部进入正式问答语料，也不改变 M1 的来源审核状态。

| 指标 | 结果 |
|---|---:|
| Canonical 文档 | 8 |
| 代表页面 | 40 |
| 元素 | 487 |
| 结构化表格 | 16 |
| 可发布页面 | 20 |
| 待人工复核 | 17 |
| 隔离 | 1 |
| 空白跳过 | 2 |

正式产物：

- `data/canonical/m2-canonical-sample-v1-documents.jsonl`
- `data/canonical/m2-canonical-sample-v1-manifest.json`
- `data/eval_results/m2-canonical-overlays-v1/` 中 40 张页面定位图及 `manifest.json`

## 2. 统一模型保留了什么

每份文档保留原文件 SHA-256、来源 URI、稳定 asset/document version ID、处理运行和路由账本哈希。每页保留物理页码、页面索引、尺寸、旋转、所选解析器/profile、质量决定、发布门控、原始解析产物引用、文字元素、表格单元格及坐标。

`publishable` 不是解析器给出的置信度，而是由质量状态推导：只有 `approved` 页面为 `true`；`needs_manual_review`、`quarantine` 和 `skip_blank` 均不能静默进入后续发布语料。

## 3. 三类解析输出如何归一化

- PyMuPDF 与 PaddleOCR 的文字/版面块统一映射为 `Element`；PaddleOCR 表格 HTML 解析成行列、跨行列和单元格文字。当前 Paddle 表格没有可靠的逐单元格 PDF 坐标时，bbox 明确保留为空，不伪造定位。
- Docling 的 text、table 与 provenance 转为同一模型；其左下角坐标系转换为项目统一的左上角坐标系，可靠的单元格 bbox 得以保留。
- 空白页保留页面事实和 `skip_blank` 决定，但不制造文字或元素。

## 4. 坐标问题与修正

契约测试首次发现 P010 为 270° 旋转页：PyMuPDF 原始文字 bbox 使用未旋转坐标，而页面渲染尺寸已经旋转，直接保存会产生越界和错位。构建器现先把 bbox 旋转到显示坐标，再裁剪 PDF 水印等少量跨 CropBox 元素，并把 `bbox_rotation_applied`、`bbox_clipped_to_page` 写入页面审计字段。

全量确定性检查保证每个元素满足：

```text
0 <= x0 <= x1 <= page.width
0 <= y0 <= y1 <= page.height
element.page_index == page.page_index
physical_page == page_index + 1
```

40 张叠加图随后作视觉抽查。P001 全页 OCR、P010 旋转原生页、P024 隔离公式页和 P036 Docling 表格页的框均与原文位置一致。

## 5. 可复现命令

```powershell
.venv\Scripts\python.exe scripts\build_m2_canonical_sample.py
.venv\Scripts\python.exe scripts\build_m2_canonical_overlays.py
.venv\Scripts\python.exe -m unittest tests.test_m2_canonical_builder tests.test_m2_canonical_overlays -v
```

默认不覆盖已存在的正式产物；需要重新生成时显式传入 `--overwrite`，避免无意改写实验事实。

## 6. M2 验收与下一步

M2 已满足“解析器输出可追溯、页面路由有质量门控、统一模型可供下游消费、坐标可视觉核验、失败不会静默发布”的阶段目标。17 个待复核页面继续作为未来语料发布前的质量队列。20 个批准页面进入 M3 可引用证据基线；P024 只作为原文定位入口进入检索，不允许自动引用其公式转写。

下一步进入 M3：先从批准页面构造条款/表格证据单元和稳定 evidence ID，再用同一 Golden Set 在 Qdrant 内比较 exact dense、中文词法/BM25、ANN 与 RRF。此阶段仍不生成最终回答，以便把检索错误与大模型生成错误分开。
