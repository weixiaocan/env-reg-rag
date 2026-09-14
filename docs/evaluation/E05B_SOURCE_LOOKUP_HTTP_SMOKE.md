# E-05B 公式原文定位 HTTP 冒烟

## 1. 验证目标

验证用户可以通过同一个 FastAPI 查询入口定位尚未通过转写质量门控的公式原文，同时确保系统不把实验语料当作可回答、可引用的正式证据。

## 2. 安全边界

- 请求显式使用 `mode: source_lookup`；
- 检索固定访问 Qdrant 的 `m3_experiment_current` 别名；
- adapter 强制附加 `usage_policy = source_locator_only` 过滤条件，请求不能覆盖；
- 查询结果固定为 `search_only`，`answer` 为 `null`，不会调用大模型生成公式；
- 自动转写只帮助定位，用户仍需打开对应 PDF 页核验公式。

## 3. 结果

POST“采用用水量折算法计算旱天入流入渗量时，具体公式是什么？”，返回：

- 状态：`search_only`；
- 证据：`ev_58337104c115f4ddb491ad736f859ae7`；
- 使用策略：`source_locator_only`；
- 定位：`T/CECS 1764-2024` 第 19 物理页、5.2.4；
- 回答：`null`。

机器可读记录位于 `data/eval_results/m5-source-lookup-smoke-v1.json`，可通过 `scripts/run_m5_source_lookup_smoke.py` 在本地服务启动后复现。
