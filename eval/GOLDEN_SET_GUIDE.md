# 黄金集出题指南 v1

## 字段（一行一条 JSONL）

见 `schemas/golden_case_v1.schema.json`。最少三件套：

- `question`：问题
- `evidence[]`：应检索到的原文（评检索）
- `expected_facts[]`：答案应包含的关键事实（评生成）

## 与现网 K 的关系

- 检索池 Top-10 → 评估附报 Recall/Precision@10
- 生成窗口 Top-5 → 评估主报 @5，并看「命中是否落在前 5」

命中判定优先：`chunk_id` / `element_ids` 交集；否则同文档 + 页码接近 + `quote` 子串。正式数据集的每条 evidence 必须保留当前 Canonical `doc_file + page + element_ids + quote` 定位。

## 覆盖

按 `golden_coverage_v1.md`：23 份主文本每份 ≥1，合计 50。

## 出题流程

1. 按配额选定 `doc_file`
2. 从该文档挑可核验条款/表格单元格，写 `quote` 与 `expected_facts`
3. 再改写成自然问法（可加同义、多条件、易混干扰）
4. `status=draft` → 人工核对原文后 `reviewed` → 冻结进回归为 `frozen`

## 本目录文件

| 文件 | 用途 |
| --- | --- |
| `schemas/golden_case_v1.schema.json` | 单条 schema |
| `golden_v1.examples.jsonl` / `golden_v1.draft.jsonl` | 示例 / 50 条草稿 |
| `golden_v1.jsonl` | 50 条已定位、已校验的正式候选集 |
| `golden_coverage_v1.md` | 23 份×条数配额 |
| `EVAL_SYSTEM_DESIGN_v0.md` | 评估体系总设计 |
| `README.md` | 目录入口；完整交接见根目录 AGENTS.md §10 |
