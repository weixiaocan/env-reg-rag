# 评估结果

本页汇总公开版本已经实际执行的质量与系统验证。数字用于说明当前固定语料和测试集上的工程基线，不代表整个排水领域的生产准确率。

## 当前结果

| 维度 | 结果 |
| --- | --- |
| 自动化回归 | 21 份主文本全部装配为 V2 Canonical；自动化测试全绿 |
| V2 Canonical 覆盖 | 21 文档、1078 页、23095 个元素（文本 22568 / 表格 239 / 公式 210 / 图片 78） |
| 不变量校验 | 每文档 17 条不变量（含 bbox 不越页面边界等）由 `validate_document` 全部通过 |
| 处理覆盖 | 1078/1078 页完成版面分析与四类状态记录；11977 个区域均有执行结果 |
| 质量状态 | passed 10788、quarantined 234、needs_review 955（处理覆盖完整，非全库内容已核验） |
| 检索验证 | `verify_v2_chunk_trace.py` 对 CECS758 做 chunk 溯源与混合检索抽样命中 |
| 原文定位 | 检索命中经 `element_ids` 追溯到 V2 元素，还原文档、条款路径、物理页码与 bbox |

## V2 Canonical 装配质量

`scripts/assemble_corpus_v2.py` 把 21 份主文本的 page-intermediate OCR 缓存装配为 V2 Canonical，逐文档通过 `compute_ids` 填充 ID 与指纹、`validate_document` 校验 17 条不变量。装配是纯重组层，不重新跑 OCR；page-intermediate 缓存只读复用一次。

装配过程中修复的关键问题：早期 `_make_span` 对 bbox 做了 `round(v, 4)`，会把恰好落在页面边缘的合法坐标（如 `x1 == page width 595.365478515625`）向上舍入到 `595.3655`，使不变量 #8（bbox 不越页面边界）以约 2e-5 pt 误差失败。修正为保留原始 float 精度后，21/21 文档全部通过。

## 检索验证

`scripts/verify_v2_chunk_trace.py` 对单文档（默认 CECS758）做 chunk 溯源断言：每个 V2Chunk 的 `element_ids` 必须能在 V2 Canonical 中定位回原文元素，且混合检索（dense bge-small-zh + BM25 + RRF）在固定问题上命中预期片段。该脚本对比的是 V2 装配结果与原文，不依赖任何遗留检索链。

## 已知限制

- V2 检索覆盖当前语料全部 21 文档；V2 检索 HTTP API / MCP 接口尚未提供。
- 固定测试集规模较小，检索抽样是回归基线而不是行业准确率。
- OCR 重路径对复杂表格仍需质量门控，失败页面不自动升级为正式回答准入。
- 当前部署是单机 POC，尚未验证多用户并发、多副本高可用或异地灾备。
- 直接 PDF→V2 Canonical 路径（不经 page-intermediate 缓存）为后续工作。

运行完整回归：

```powershell
python -m unittest discover -s tests -v
```
