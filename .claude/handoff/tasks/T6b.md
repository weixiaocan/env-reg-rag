# T6b 修复后样本外复测（Codex 侧双盲判定）

## 背景
`eval/audits/ocr_sampling/v2/` 是修复后中间候选 `corpus-966c89b264cc` 的复测工作表：`sample_sheet.jsonl`（201 个扫描文档样本，T1 同位置映射到新 chunk）+ `native_sample_sheet.jsonl`（30 个原生文档样本，来自 T4 列判定改变的页面）。截图在 `v2/crops/`（本地，已忽略）。判定标准见 `v2/README.md`（与 T1 多维标准相同）。

这是**样本外验证**：T5 的校准集已排除这些页面。Claude 将独立判定一个分层子集；**你不得读取** `eval/audits/ocr_sampling/v2/judgments_claude.jsonl`（如出现）。

## 范围
1. 对全部 231 个样本逐条判定，每条**实际查看**该样本所有页的 `context` 图；判 heading_path 时查看 `page_thumbnail`。
2. 输出 `v2/judgments_codex.jsonl`（字段同 T2：sample_id、judge、model、text_fidelity、error_types、heading_path、standalone_usable、evidence、unjudgeable_reason、images_viewed、notes），按 sample_id 排序。
3. **evidence 硬性要求**（T2 的教训）：
   - `error` 与 `minor` 必须写出具体差异："OCR『<候选 chunk 中的实际文字>』 vs 原图『<原图实际文字>』"，原图片段必须是你在图中读到的真实文字；漏行则写出漏掉的原图文字。
   - `exact` 也必须给出一处可核对的锚点："原图『<该块中任意一段 ≥8 字原文>』一致"。
   - 不得出现两个样本 evidence 完全相同；不得使用"同一红框中的对应原文…"之类的套话。
   - `error_types` 按实际差异标注，不要默认填充。
4. 扩展校验器（`src/evaluation/` 现有判定校验）：上述 evidence 规则全部自动检查（含跨样本重复检测、套话黑名单），校验失败则不得生成汇总；配单测。
5. 输出 `v2/judgments_codex_summary.json`：
   - 扫描文档：每文档 可判定数、error 数与比例、Wilson 95% 区间、error_types 分布、heading wrong 比例、fragment 比例；**并与 T2（`eval/audits/ocr_sampling/judgments_codex_summary.json`）逐文档并列对比**。
   - 原生文档 30 样本：error 数与清单（重点检查是否把真双栏/并排内容错误合并）。
6. 不做文档准入结论（留给合并步骤）。提交一次（英文，末尾 `Co-Authored-By: Codex <noreply@openai.com>`），连同 `.claude/handoff/` 改动。

## 禁止
- 不改工作表、截图、语料、代码逻辑（除校验器）；不改准入；不 ingest/publish；不调用外部 LLM。
- 不读取 Claude 的判定。

## 额度
231 条判定量大。若额度可能不足，**按文档顺序判定并每完成一个文档就追加写入并校验**，中断后可续；报告写明已完成到哪个文档。

## 交付
`.claude/handoff/reports/T6b.md`
