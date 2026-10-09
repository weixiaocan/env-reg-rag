# expected_fact → 实际承载 chunk → 原 PDF 对账（第 4 轮验收门，2026-09-30）

数据集 sha256：`3b1f1cdc459aadb50bd1b39531bc90f51ecc25a0d969612d41612fa011bd6e89` → `3b1f1cdc459aadb50bd1b39531bc90f51ecc25a0d969612d41612fa011bd6e89`；台账绑定同一哈希，
数据集任何改动而未重生成台账 → 验收门哈希过期失败。

## 匹配器（红灯级）

数字事实：逐字比对（空格剥离、上标 ³²¹ 归一）；文本事实：非数字部分
CJK bigram 比率 ≥ 0.5；作用域为 must_hit 承载 chunk 并集。锚点可定位
不等于事实有证据——机器无法证明的语义例外逐项 `human_ruling=pending`
留人工裁定，不得静默关闭。

## 绑定（第 4 轮）

每条 fact 台账绑定：**实际 chunk_id**（离线复现、等于发布 payload id）+
**证据索引** + **原 PDF 页**；机器不可证明的例外另附已核验页图
（`renders/`，14 页）与摘录（quote）。

## 分类计数（87 内容事实 + 8 拒答事实）

| 分类 | 数量 | 含义 | human_ruling |
| --- | --- | --- | --- |
| `covered` | 73 | 机器支持（数字逐字 + bigram ≥0.5） | machine_approved |
| `covered_table_cell` | 4 | 表格单元格事实：数值与行/列表头逐字在 chunk，关联为表格位置性 | pending |
| `covered_formula_structure` | 2 | 公式结构事实：LaTeX 公式/式中定义逐字在 chunk | pending |
| `covered_verbatim_parts` | 4 | 内容 token 逐字在 chunk，残余 bigram 为设问措辞（逐条留痕） | pending |
| `covered_ocr_variant` | 1 | 数值在 chunk 为 OCR 退化形态（逐条留痕） | pending |
| `covered_attribution_binding` | 1 | 标准号为文档归属：逐字在封面 chunk，由证据文档绑定佐证 | pending |
| `fixed_by_new_evidence` | 2 | 本轮补证据修复（新增 must_hit 证据） | pending |
| `refuse_meta` | 8 | 拒答题 meta-fact，不入内容证据对账分母 | n/a |

## 缺失 / 仅 PDF / 仅 rendering（真实探针阶梯产生，记录不关闭）

真实探针（全库 chunk → PDF 文本层 → 人工 rendering）逐 fact 探测：三类不可机器佐证缺口 **0**。

## 结构性编辑（第 3 轮，保留）

