# R1d OCR 文字层：三源投票（primary / pp_raw / pp_clean）

## 必读
`reviews/R1c.md`（错字对照表）、`MIDDLE_LAYER_V3_SPEC.md`、`PROTOCOL.md`。

## 规则（替换 R1c 的"region 取 pp_clean 行 + primary 兜底"）
对每个 OCR region，收集三源行：`pp_clean`（预处理后 PP-OCRv6，R1c run）、`pp_raw`（未预处理 PP-OCRv6，`ocrv6-ef06411e1606ba35`）、`primary`（page-intermediate 元素）。按几何（纵向重叠 + 横向覆盖）把三源行对齐成"行组"，然后：
1. **整行多数**：行组中 ≥2 个来源规范化后相同 → 取该文本。
2. **字符级合并**：否则对行组内的来源做字符级对齐（difflib 或等价），逐片段：
   - ≥2 源一致的片段 → 取一致值；
   - 两源冲突、第三源缺失 → 优先级 `pp_raw > primary > pp_clean`（依据：T5 校准 PP 对 40 / primary 对 8；R1c 表明 pp_clean 损伤笔画）；
   - **仅一源存在的片段**（其他源在此处缺失/截断）→ 采用该源（这是 pp_clean 召回被水印吞掉的行/行尾的途径）。
3. 只有 pp_clean 有的整行（行组仅一源）→ 采用 pp_clean，标记 `single_source=pp_clean`。
4. 每行记录 provenance：各源文本、采用规则（majority / merge / single_source）、冲突片段。统计写入 metrics：三种规则的行数、冲突片段数。
5. 编号保留规则（R1c 已有）保持。

## 验证
- GB 55027 重跑试点产物；R1c 审核表 7 处逐项前/后；金标准 4/4；条文连续性无缺号；R1 所列完整性问题不得回退。
- 列出全部 `single_source=pp_clean` 行与全部冲突片段（GB 55027），供审核者对照原图。
- 单测：整行多数、字符合并（R1c 表中 `二级`、`竖向`、`烟气` 的合成案例）、仅一源片段采用、优先级冲突。完整 pytest。

## 禁止
同 R1c。不下载模型、不调用外部 LLM、不 ingest/publish、不改 registry。

## 交付
`.claude/handoff/reports/R1d.md`；提交一次（英文，末尾 `Co-Authored-By: Codex <noreply@openai.com>`），连同 `.claude/handoff/` 改动。
