# T6a 构建修复后候选语料 + 临时集合 + 复测工作表

## 背景
T3（目录污染）、T4（阅读顺序）、T5（PP-OCRv6 融合）已合入代码（63c0280、7542b81、9db83cc）。现在需要用这些修复**正式走统一入口**生成一个候选语料，写入**临时（staging）集合**，并为样本外复测准备工作表。负责人已授权写入临时集合；**未授权发布或切换指针**。

## 范围
1. **候选构建**：使用现有 page-intermediate（不重新运行 OCR、不重跑 `build_corpus.py` 的 OCR 阶段，除非统一入口强制要求——如强制，先停下写报告说明），通过 `scripts/assemble_corpus_v2.py` 生成新候选（显式传入独立 OCR run `ocrv6-ef06411e1606ba35` 与公式 run）。新候选版本号由现有确定性逻辑产生，不手工编造。运行 Canonical 不变量校验，记录：文档数、页数、元素数、chunk 数、usage_policy 分布、与 `corpus-0b9a1fd84e3d` 的差异摘要（可复用 `scripts/report_canonical_candidate_diff.py`）。
2. **临时集合入库**：用 `scripts/ingest_corpus_v2.py` 的 staging 路径写入**新的不可变 staging collection**（T06 机制），**不得**写入或删除当前正式 collection `corpus_v2__staging__corpus-0b9a1fd84e3d__bi-d70cf457e0cac3fa3abbce0a`，**不得** `--publish`，**不得**改 `data/registry/corpus-current.json` 或 alias `corpus_v2_live`。执行前在报告中写明将创建的 collection 名与影响范围。入库后核验：point 数 = chunk 数；对 staging collection 做至少 1 次 `source_lookup` 烟测（检索调用直接指向 staging collection，不经正式 alias）。
3. **复测工作表 v2**（`eval/audits/ocr_sampling/v2/`）：
   - 对 T1 的 201 个 `sample_id`，按原 `page_bboxes`（document + physical_page + bbox 重叠）在新候选中找到覆盖该位置的新 chunk（可能 1 个或多个；取重叠最大者为主，并记录其余）。输出与 T1 相同字段（`heading_path_text`/`body_text`/截图等），新增 `old_chunk_id`、`new_chunk_id`、`match_overlap`。
   - 截图：为新 chunk 的 span 重新生成 `context` + `page_thumbnail`（复用 `src/evaluation/ocr_sampling.py`），截图目录被 Git 忽略。
   - 原生文档补充样本：从 T4 中"列判定由双栏改为单栏"的**原生文档页面**里，按文档分层、固定种子抽 30 个 chunk，同样生成工作表与截图（`native_sample_sheet.jsonl`）。
   - 不做任何判定；README 沿用 T1 多维标准。
4. **黄金集影响预估（只读）**：对 `eval/golden_v2.jsonl` 中绑定的 chunk，统计在新候选中仍存在 / 需重新锚定的数量（用现有 reanchor 工具的 dry-run 或只读模式，若无只读模式则只做统计不写文件）。不改黄金集。
5. 测试：为工作表 v2 的位置映射写单元测试；完整 pytest。

## 禁止
- 不 publish，不改正式指针/alias，不触碰正式 collection；不删除任何已有 collection。
- 不改 OCR/融合/排序代码逻辑（发现问题写报告）；不改准入/usage_policy；不改黄金集。
- 不调用外部 LLM；不下载模型。

## 验收标准
- 候选版本号、manifest、不变量校验结果、差异摘要。
- staging collection 名、point 数 = chunk 数、烟测输出原文。
- 工作表 v2：201 行 + 原生 30 行，截图全部存在、哈希可核；可复现（两次运行哈希一致）。
- 黄金集绑定影响统计。
- 测试全绿；提交一次（英文，末尾 `Co-Authored-By: Codex <noreply@openai.com>`），连同 `.claude/handoff/` 改动；大文件/截图不提交。

## 交付
`.claude/handoff/reports/T6a.md`
