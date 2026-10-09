# Canonical 候选交付状态（等待人工裁定）

## 结论

- 候选：`corpus-0ae8a9423e2f`，23 份源 PDF、1144 个物理页。
- 当前结论：`needs_adjudication`；**不得**称为质量通过或可重新分块。
- 当前发布指针仍为 `corpus-0b9a1fd84e3d`，文件 SHA-256 为
  `dc76605fdf8af71211817471261fa9af2939311962a0c99ce04b99989e7034c7`。
- 未执行 chunk、embedding、Qdrant ingest、发布切换、检索/生成评估、重排实验或黄金集修改。

## 不可变候选与证据

- manifest：`data/registry/corpus-0ae8a9423e2f.json`
  - SHA-256 `75da2f00399a974306b1c9ac7200333efbf4279eb4d25f072a3875888b11f8f9`
- page-intermediate：`data/canonical/corpus-0ae8a9423e2f-documents.jsonl`
  - SHA-256 `b66a745e5ae6faad51edb5a1459e7b4aa9f9d6e0e4f9be8928f9c679036ca1e3`
- Canonical：`data/canonical/v2/corpus-0ae8a9423e2f/`
  - 23 文档、25067 元素：text 24520、table 251、formula 210、figure 86
  - 聚合 SHA-256 `8e9484c5a8ca6e9b6c0fafddb0ca9247e4cd18893a75423b523a27ee0d061aad`
- 独立本地 OCR：`data/model_runtime/independent_ocr/runs/ocrv6-5eb043978a39df9a/`
  - 737/737 页；8264 个文字区域；严格自动通过 5049，待审 3215
  - 仅为 `automatic_local_model_comparison`，不是人工权威核验
- 质量台账：`data/quality/corpus-0ae8a9423e2f/qa-d95842fca55c7d58/`
  - ledger SHA-256 `d95842fca55c7d5866550731049c9d30aa3167e39e2b192514a48a18f6e45d1b`
  - 完整性问题 0
  - verified_passed 35254、manual_review_required 9772、unsupported 458、not_detected 3099
  - 9772 是包含文档/页面/类型汇总及 Canonical 投影的台账记录数，不是人工题数
- 唯一源对象裁定包：
  `data/quality/corpus-0ae8a9423e2f/adjudication-565a984303c61e78/`
  - 3823 个唯一源对象，660 个物理页
  - text 2649、table 218、formula 246、image 710
  - source_region 3071、source_signal 752
  - 660 张原页图、3823 张对象裁剪；逐项哈希复验缺失 0、错配 0
  - 裁定模板 3823 行，预填裁定 0
- 与当前发布版差异：
  `data/quality/corpus-0ae8a9423e2f/comparison-5766982f7e987a09/`
  - 源 PDF 与物理页集合不变
  - Canonical 25062 → 25067（text +4、table +1）
  - 32 个页面的语义提取内容发生变化；详细页清单和元素例子见报告

## 已确认并修复的实际缺陷

1. 旋转 90/270 度的页面曾把旋转后的像素直接送入 OCR/表格模型，模型坐标也未映回显示页。
   统一 PDF 流水线现会先归一方向，再把 OCR、布局、表格与单元格 bbox 映回原页坐标。
2. T/CECS 758 第 42 页存在两个均覆盖布局区域的表格候选，旧选择逻辑把它判为歧义并漏掉表格。
   现在只有在候选均完整包围区域且存在唯一最小包围框时才自动选择；几何仍相同则继续报歧义。
3. 修复后第 42 页表头/列恢复为时间、管径、水深、流速、流量等正确六列，不再使用旧候选的乱码网格。
4. 原 PDF 损坏文字层中的 3 个纯标点伪文本，仅在同位置高置信独立 OCR 且 OCR 文本已进入
   Canonical 时，被明确处置为损坏文字层载体；没有作为正文静默加入。

## 测试

- 直接相关聚焦回归：123/123 通过。
- 完整 pytest：共收集 1125 项；1124 项通过，1 项失败。
- 唯一失败：`tests/test_repository_layout.py::PublicRepositoryLayoutTests::test_only_product_facing_documents_remain`。
  原因是用户现有未跟踪文件 `docs/HOSTING_OPTIONS_2026-10-07.md` 不在测试硬编码白名单；本任务未创建、
  未删除或修改该文件，也未扩大白名单。排除此单项后其余完整测试集全绿。

## 可重新分块门禁命令

```powershell
.venv\Scripts\python.exe scripts\audit_canonical_quality.py `
  --corpus-version corpus-0ae8a9423e2f `
  --independent-ocr-run data/model_runtime/independent_ocr/runs/ocrv6-5eb043978a39df9a `
  --require-ready-for-rechunk
```

当前必须非零退出，输出 `gate.status=needs_adjudication`。收到并合入全部人工裁定、按裁定修复真实缺陷、
重建不可变候选及最终台账后，此命令才允许返回成功。

## 人工交接

打开 `adjudication-565a984303c61e78/index.html`。页面中的选择只保存在浏览器本地；完成后点击
“导出已裁定 JSONL”，将导出的 `canonical_quality_rulings.jsonl` 交回。本目标在此期间保持未完成。
