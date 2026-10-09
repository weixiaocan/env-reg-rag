# 检索评测 Harness

本 Harness 使用 `eval/golden_v1.jsonl` 的 50 条 reviewed 案例，调用当前线上同构的
BGE dense + BM25 sparse + RRF 检索链路。它只评估检索和原文定位，不调用 LLM，
也不修改正式语料或发布指针。

## 运行前提

- 当前语料指针：`data/registry/corpus-current.json`
- Qdrant 中存在指针对应 collection，且 point 数与指针中的 chunk 数一致
- 本地已有配置中指定的 embedding 模型缓存

从仓库根目录运行：

```powershell
.venv\Scripts\python.exe scripts\evaluate_golden_retrieval.py --run-id retrieval-baseline-v1
```

为避免误覆盖，已存在的 run id 会直接失败。新的结果写到：

```text
eval/runs/<run-id>/
  config.json
  summary.json
  report.html
  cases/G001.json ...
```

## 匹配与指标口径

每条 `must_hit=true` 的黄金证据按以下顺序匹配检索结果：

1. `element_ids` 相交；
2. 文档名 + 物理页码 + 黄金 quote 在命中文本中出现。

`must_hit=false` 用于库外/拒答案例，不进入检索召回率分母。主要指标：

- `macro recall@5/@10`：先按案例计算证据召回，再对可评案例平均；
- `micro recall@5/@10`：全部必命中证据合并计算；
- `MRR`：每个案例首个正确证据的倒数排名；
- `document_hit` / `page_hit`：分别检查目标文档、目标物理页是否出现；
- `complete_case_rate`：该案例的全部必命中证据是否均被召回；
- `by_label`：按题型、挑战类型、模态分别报告。

每个案例 trace 会保留检索排名、分数、文档、物理页、element IDs、定位信息和匹配方法，
用于复现坏例。评测通过只说明该固定集上的检索表现，不代表 OCR、法规有效性或生成答案已经通过权威核验。

## 首轮基线

`eval/runs/retrieval-baseline-v1/` 对应当前已发布语料 `corpus-069284e6d60e`：

- 50 条案例，其中 47 条进入检索召回评估；
- macro Recall@5 = 0.7199，Recall@10 = 0.8262，MRR = 0.6800；
- 文档命中率 @5 = 0.9610，物理页命中率 @5 = 0.7943；
- 图片/图注是主要短板：macro Recall@5/@10 均为 0.4375；
- 表格 Recall@10 = 0.9417，公式 Recall@10 = 0.9375。

详细结果以该目录下 `summary.json`、`report.html` 和逐题 trace 为准。
