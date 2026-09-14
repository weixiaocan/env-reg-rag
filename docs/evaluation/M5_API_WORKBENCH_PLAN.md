# M5 FastAPI 与证据工作台实施计划

## 1. 所在位置

M4 已证明查询核心可以回答、追问、拒答、展示冲突和定位原文。M5 不重写这些规则，只增加 HTTP 与网页 adapter，让人工用户可以操作同一个 `QueryApplicationService`。

## 2. 已确认的 HTTP interface

首个稳定入口为 `POST /api/v1/queries`。

请求字段：

```json
{
  "request_id": "可选；省略时由服务端生成",
  "question": "必填问题文本",
  "mode": "answer 或 source_lookup，默认 answer",
  "conversation_context": {
    "pending_question": "上一轮返回的待补充问题",
    "statistical_period": "12h"
  }
}
```

响应无损映射领域 `AnswerResult`，包括 `query_id`、状态、已解析条件、缺失条件、回答主张、冲突、证据、语料版本、提示和下一轮上下文。网页不得从回答文本猜测状态，也不得自行拼装引用。

当前还提供 `GET /api/v1/health` 作为进程存活检查。它只表示 HTTP 进程可以响应，不代表 Qdrant 或模型供应商已经就绪；依赖就绪检查将在后续纵向切片补充。

## 3. 网页第一条纵向切片

工作台使用原生 HTML/CSS/JavaScript，避免在首期为单页交互引入前端构建系统。它支持：

- A 连续对话、B 原文对照、C 证据板三种布局自由切换；
- 发送首次问题，并把服务端返回的 `continuation_context` 带入下一轮；
- 按 `AnswerResult.status` 显示回答、追问、拒答、原文定位和来源冲突；
- 将 claim 的 evidence ID 映射为引用按钮；
- 在右侧展示文档名称、发布机关、地域、效力状态、条款路径、物理页、证据正文和官方来源链接；
- 使用 DOM `textContent` 呈现问题、回答和证据，不把文档内容当 HTML 执行。
- 在“证据化回答”和“只找原文”间切换；后者只返回 `source_locator_only` 定位结果，不调用模型生成答案。
- 点击引用时按稳定 evidence ID 读取证据详情，并通过 allowlist 文档接口打开对应 PDF 物理页。

## 4. 阶段结论

代表查询已通过 API 验收，公式原文定位、PDF 页跳转和 A/B/C 布局已由项目使用者完成人工 E2E 验收。自动截图和录屏移至 P7 演示材料阶段。

首期保持完整 JSON 响应，不启用 SSE。结构化回答需要先通过证据完整性校验，提前输出未经校验的文本会削弱证据门控；SSE 也不能直接消除约 49.85 秒的模型长尾。就绪检查、超时、断连取消、并发和阶段事件流对照实验进入 M6 系统运行评估。
