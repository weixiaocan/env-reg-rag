# E-02A 证据单元与检索块基线

## 1. 结论

M3 的第一项已完成：20 个 M2 批准页被构造成 57 个可引用 Evidence Unit；另有 1 个公式转写隔离页被构造成仅原文定位单元。两类单元共同派生出 58 个一对一 Retrieval Chunk，待复核页和空白页没有进入这一基线。

| 类型 | Evidence Unit | Retrieval Chunk |
|---|---:|---:|
| 条款 | 18 | 18 |
| 政策/说明段落 | 29 | 29 |
| 完整表格 | 10 | 10 |
| 原文定位 | 1 | 1 |
| 合计 | 58 | 58 |

正式产物：

- `data/evidence/m3-evidence-units-v1.jsonl`
- `data/evidence/m3-evidence-units-v1.manifest.json`
- `data/retrieval/m3-retrieval-chunks-v1.jsonl`
- `data/retrieval/m3-retrieval-chunks-v1.manifest.json`

## 2. Evidence Unit 与 Retrieval Chunk 的边界

Evidence Unit 保存内容、使用策略、内容哈希、文档版本、标准号、适用地域、页码、Canonical 元素 ID、bbox 和表格单元格。`answer_and_citation` 可以支持回答主张；`source_locator_only` 只能提供文档、页码和原文入口。它的 ID 由文档版本、页码、证据类型、元素 ID 与内容哈希确定，重建索引不会改变 ID；来源或解析事实变化时则产生新 ID。

Retrieval Chunk 是搜索派生视图。v1 在原文前附加标准号、文件名和页内标题路径，以提高短条款、数值表格和同名概念的可检索性，但仍通过 `evidence_ids` 回到 Evidence Unit。v1 刻意采用一块对应一份证据，先建立可解释基线，不提前引入多证据合并。

## 3. 分段规则

- `publishable=true` 页面生成可引用证据；定位可信但转写隔离的页面可生成 `source_locator_only` 单元。
- 条款号开始新的条款证据；政策正文和说明文字形成段落证据。
- “1 总则”一类短编号标题作为检索语境，不单独伪装成事实。
- 结构化表格整体成为一个证据单元，保留标题、全部单元格、合并关系、单位和脚注字段；不能把“20”这类裸数值单独索引。
- 为防止多个 Canonical block 被合并为巨型证据，v1 仅在真实元素边界处分段，单元上限为 600 个字符。正式结果最短 14 字、中位数 93 字、最长 584 字。

## 4. 已验证场景

1. GBT 28592-2012 第 4 页降雨等级表保留“中雨”和“10.0~24.9”，检索块同时带有标准号和表格语境。
2. M2-P040 的 6.2.1 条款被识别为条款证据，并保留页内标题路径。
3. 同一输入在不同目录重复构建得到相同 evidence/chunk ID。
4. 每个 Retrieval Chunk 都能通过唯一 evidence ID 回到具体页码和 Canonical 元素。
5. P024 的公式关键词可以定位到第19物理页，但使用策略阻止系统直接引用未校准公式。

## 5. 当前限制

当前输入是离散代表页，所以 `context_scope` 明确记录为 `page_local_inferred`。这能验证页内标题和表格上下文，但不能宣称已经恢复跨页完整条款树。后续处理连续全文时再增加跨页章节栈，并以新配置版本重建派生检索块。

## 6. 下一步

建立第一版 Golden Set：先把用户已给出的两个真实问题拆成可评价查询，再由 AI 补充不同检索难度的问题。每题必须指定期望 evidence ID、问题类型、必要条件和是否允许无答案，然后在 Qdrant 内比较 exact dense、中文词法/BM25、ANN 与 RRF。
