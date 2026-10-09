# OCR 文本复测工作表 v2

本目录是 T6a 对修复后候选语料的复测工作表，不包含任何质量判定，也不改变语料准入状态。`sample_sheet.jsonl` 将 T1 的 201 个样本按原 `page_bboxes` 几何位置映射到新候选 chunk；`native_sample_sheet.jsonl` 从 T4 由旧双栏判定改为新单栏判定的原生文档页面中，按文档分层和固定种子抽取 30 个文本 chunk。

重建与核验：

```powershell
.venv\Scripts\python.exe scripts\build_ocr_sample_sheet_v2.py --corpus-version corpus-966c89b264cc
.venv\Scripts\python.exe scripts\build_ocr_sample_sheet_v2.py --corpus-version corpus-966c89b264cc --verify-only
```

截图位于 Git 忽略的 `crops/`，每个新 chunk 的每一页均包含 200dpi `context` 和带红框的 100dpi `page_thumbnail`，并在工作表中绑定 SHA-256。映射行保留 `old_chunk_id`，新增 `new_chunk_id`、`match_overlap` 和所有其余几何重叠候选。

复测沿用 T1 多维标准：

- `text_fidelity`：`exact` / `minor` / `error` / `unjudgeable`；数字、单位、条款号、术语、漏行、截断、串行或串栏错误均为 `error`。
- `error_types`：`char`、`number_unit_clause`、`truncation_or_missing`、`extra_or_order`。
- `heading_path`：`correct` / `wrong` / `not_applicable`。
- `standalone_usable`：`yes` / `fragment`。

本工作表不预填任何上述判定；自动映射、截图存在和哈希一致只证明复测材料可复现，不证明 OCR 内容质量通过。
