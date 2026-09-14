# E-05A FastAPI 两轮 HTTP 冒烟

## 1. 验证目标

验证运行中的 FastAPI 是否无损复用 M4 查询核心，而不是只验证内存函数或静态页面。

## 2. 执行环境

- HTTP：FastAPI 0.141.1、Uvicorn 0.52.4；
- 查询核心：`QueryApplicationService`；
- 检索：Qdrant `corpus_current`、本地 BGE、数值区间投影；
- 回答：LangChain 结构化输出、智谱 `glm-5.2`；
- 语料：`formal-corpus-v1`。

## 3. 结果

1. `GET /api/v1/health` 返回 `200` 和 `{"status":"ok"}`。
2. 第一轮 POST“降水量为20毫米时属于什么等级？”，返回 `needs_clarification`、缺失条件 `statistical_period` 和显式 `continuation_context`。
3. 第二轮只 POST“12小时”并带回上下文，返回 `answered`。
4. 回答包含“大雨”，引用 `ev_18de8c71615152558e1368e48ee73d1d`，证据定位为 `GB/T 28592-2012` 第 4 物理页表 1。

该链路通过，第二轮端到端耗时约 19.52 秒。机器可读记录位于 `data/eval_results/m5-http-followup-smoke-v1.json`，不包含密钥。

## 4. 限制

浏览器控制环境在连接阶段发生本地资源写入错误，因此本轮没有生成自动化浏览器截图。HTTP 接口、首页返回和连续追问已通过自动化测试，真实页面已在本地 `http://127.0.0.1:8000/` 提供，视觉与点击验收仍需补充。
