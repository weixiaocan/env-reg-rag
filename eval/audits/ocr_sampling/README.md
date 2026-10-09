# OCR 文本抽样核对表

本目录是 T2 独立核对的工作表，不包含任何 OCR 质量判定，也不改变正式语料的准入状态。`sample_sheet.jsonl` 每行是一条样本；跨页样本在 `screenshots` 中每页都有上下文裁剪和红框整页缩略图。截图位于被 Git 忽略的 `crops/`，可用仓库根目录下的命令重建：

```powershell
.venv\Scripts\python.exe scripts\build_ocr_sample_sheet.py
```

## 多维判定标准

- `text_fidelity`：对 `body_text` 与原图判定 `exact` / `minor` / `error` / `unjudgeable`。`exact` 允许忽略空白和换行；`minor` 仅限不影响数字、单位、条款号、术语和语义的标点、全半角、空格或版式差异；任何数字、单位、符号、条款编号或关键词错误，词义变化，漏/多超过一个短语，以及上下文图中可见的漏行、行尾截断、串行/串栏均为 `error`；截图不清或裁剪错误为 `unjudgeable` 并记录原因。
- `error_types`（可多选）：`char`、`number_unit_clause`、`truncation_or_missing`、`extra_or_order`。
- `heading_path`：对 `heading_path_text` 判定 `correct` / `wrong` / `not_applicable`。
- `standalone_usable`：判定 `yes` / `fragment`。`body_text` 脱离上下文无法构成可引用信息（如“水 □”或半句话）时为 `fragment`。

## 文档准入门槛（仅供 T2 汇总使用）

在可判定样本中，`text_fidelity=error` 占比不超过 `1/25`，并报告 Wilson 95% 下界。`unjudgeable` 超过 3 条的文档不作结论。`heading_path=wrong` 与 `standalone_usable=fragment` 比例单独报告，T2 不据此下准入结论，留给 T3 决定是否额外过滤。Codex 与 Claude 应分别独立判定；本工作表本身不预填结论。

## 字段与校验

`chunk_text` 来自当前正式 pointer 对应 Canonical 的确定性分块结果；生成前会把完整重算 chunk stream 与已发布 `chunk_manifest.json` 比较。`heading_path_text` 由 chunk metadata 的 `section_path` 按 `src/application/v2_chunker.py::_format_section_path` 的规则格式化；`body_text` 按同一前缀的实际拼接规则从 `chunk_text` 中精确去除，不使用“第一行”启发式。`page_bboxes` 是每页 `source_spans` 的并集；`context` 为 200dpi、页面左右各 24pt 内全宽、bbox 上下各扩 40pt 的上下文图；`page_thumbnail` 为 100dpi 整页红框图。每张图均绑定 SHA-256，失败会写入 `failure_reason`，不会静默跳过。
