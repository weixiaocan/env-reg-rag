# 评测框架 v1

当前阶段只固定评测契约，不执行最终全量评测。应用代码、语料和配置冻结后，才生成可用于简历和版本对比的 official run。

## EVAL-FRAMEWORK-1 验收边界

- `EF-01`：同一套 50 条 reviewed 黄金集支持 `retrieval`、`generation`、`e2e` 三种模式；
- `EF-02`：三种模式通过端口隔离，评测核心不依赖 Qdrant 或具体模型供应商；
- `EF-03`：每题统一输出问题、金证据、检索结果、生成上下文、答案、Judge、指标、分阶段耗时和失败归因；
- `EF-04`：official run 必须绑定数据集哈希、语料版本、代码 revision，并要求工作区干净；
- `EF-05`：外部生成或 Judge 调用必须显式记录数据发送授权；
- `EF-06`：开发期结果统一标为 `provisional`，不得作为最终基线或简历数字。

核心代码：`src/evaluation/framework.py`。机器契约：

- `schemas/evaluation_run_v1.schema.json`
- `schemas/evaluation_case_trace_v1.schema.json`

## 三种模式

| 模式 | 输入 | 必需组件 | 主要指标 |
| --- | --- | --- | --- |
| retrieval | question | retriever | Recall@5/@10、MRR、文档/页/元素命中、延迟 |
| generation | question + 人工金证据 | gold-context generator + judge | 事实覆盖、正确性、证据一致性、拒答行为、成本/延迟 |
| e2e | question | 真实 answer pipeline + judge | 检索与生成指标、引用约束、端到端延迟、失败归因 |

生成上下文固定取检索前 5，检索池固定为 10；主报 @5，附报 @10。

## 正式全量运行门禁

代码落定后按以下顺序执行：

1. 固定 Git revision，确认工作区干净；
2. 固定 `golden_v1.jsonl` SHA-256、语料版本、collection 和 point 数；
3. 运行相关单元/契约测试；
4. 先跑 retrieval 50 条并保存逐题 Trace；
5. 经明确外部数据发送授权后，再跑 generation 和 e2e；
6. 抽检 Judge 和坏例，生成 official summary 与 HTML；
7. official run 只追加，不覆盖旧 run。

应用代码尚未冻结期间，不执行第 4–6 步。
