# E-04A 智谱真实 RAG 冒烟记录

## 1. 结论

2026-09-07 使用正式语料别名 `corpus_current` 和智谱 `glm-5.2` 完成一次真实端到端调用。查询核心返回 `answered`；Golden Set AI-007 指定证据成功进入 EvidencePack，模型生成的三条 claim 均绑定该证据，并通过确定性引用校验。

本记录只批准“当前代码可以完成一次 Qdrant RRF → LangChain → 智谱 JSON → Pydantic → 引用校验”的技术闭环，不批准回答质量、稳定性、成本或延迟已经达到上线标准。

## 2. 固定场景

- 问题：武汉市混错接改造项目的评估周期有什么要求？
- 显式地域：武汉
- Golden Set case：AI-007
- 语料版本：`formal-corpus-v1`
- Qdrant 入口：`corpus_current`
- 检索模式：dense + multilingual BM25 的 RRF
- 模型供应商：智谱
- 模型：`glm-5.2`
- 预期证据：`ev_d6a4f70024ff1539091b28cdf9a66bf3`

## 3. 结果

- 状态：`answered`
- 预期证据已召回：是
- 单次端到端耗时：9192.43 ms
- 生成 claim 数：3
- 引用校验：通过；三条 claim 都只引用预期证据
- 回答事实：工程完工验收后的试运行或运行维护阶段开展评估；至少跨越一个完整雨季；分为短期和长期评估
- 原文入口：武汉市排水管道混错接改造技术规程，物理页 18，官方来源 URL 已返回

机器可读结果保存在 `data/eval_results/m4-zhipu-real-rag-smoke-v1.json`。该结果未保存 API Key，也未保存模型思考内容。

## 4. 当前不足

1. 只有一个真实问题和一次模型调用，不能据此计算成功率、P95 或稳定性。
2. 当前计时覆盖在线 query 的 embedding、Qdrant 和 LLM，但未拆分各阶段耗时。
3. 尚未记录 token 用量和实际费用。
4. 该证据已经返回文档、物理页和官方 URL，但 `heading_path` 为空；后续需补齐条款定位，不能把页码定位描述成完整条款定位。
5. 尚未覆盖无效 JSON、空输出、超时、429、5xx 的真实供应商行为；当前仅有本地契约测试。
6. E-04 仍需扩展到正常回答、缺条件、search-only、无覆盖和冲突等 10～20 个场景。

后续修正：冒烟完成后，查询适配器增加了保守的条款号降级定位。当权威 `heading_path` 为空、但检索块的“原文”开头存在明确编号时，返回该编号（本场景为 `8.3.1`）；不推测条款标题。该修正已通过 Qdrant 服务到 `QueryApplicationService` 的契约测试，但未重复发起付费模型调用，因此上面的历史机器报告仍保留修正前结果。

## 5. 可复现命令

在本地 `.env` 配置 `LLM_PROVIDER=zhipu`、`LLM_MODEL=glm-5.2` 和 `ZAI_API_KEY`，并确保 Qdrant 已启动、`corpus_current` 已发布后运行：

```powershell
.venv\Scripts\python.exe scripts\run_m4_real_rag_smoke.py
```

该命令会发起真实付费模型请求。重复执行会覆盖同名机器可读报告，因此正式批量评估应使用带运行 ID 的独立产物，不复用本冒烟脚本作为评估运行器。
