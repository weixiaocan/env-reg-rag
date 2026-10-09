# OCR 文本抽样核对表

本目录是 T2 独立核对的工作表，不包含任何 OCR 质量判定，也不改变正式语料的准入状态。`sample_sheet.jsonl` 每行是一条样本；跨页样本在 `screenshots` 中每页对应一张 200dpi 原图裁剪。截图位于被 Git 忽略的 `crops/`，可用仓库根目录下的命令重建：

```powershell
.venv\Scripts\python.exe scripts\build_ocr_sample_sheet.py
```

## 判定标准

- `exact`：与原图一致（忽略空白、换行）。
- `minor`：仅标点、全半角、空格或版式差异，不影响任何数字、单位、条款号、术语与语义。
- `error`：任何数字、单位、符号、条款编号或关键词错误；错字导致词义变化；漏字或多字超过一个短语；串行或串栏导致语义错误。
- `unjudgeable`：截图不清或裁剪错误，必须记录原因。

## 文档准入门槛（仅供 T2 汇总使用）

在可判定样本中，`error` 占比不超过 `1/25`，并报告 Wilson 95% 下界。`unjudgeable` 超过 3 条的文档不作结论。Codex 与 Claude 应分别独立判定；本工作表本身不预填结论。

## 字段与校验

`chunk_text` 来自当前正式 pointer 对应 Canonical 的确定性分块结果；生成前会把完整重算 chunk stream 与已发布 `chunk_manifest.json` 比较。`page_bboxes` 是每页 `source_spans` 的并集；`screenshots[].sha256` 绑定对应 PNG。裁剪失败会写入 `failure_reason`，不会静默跳过。
