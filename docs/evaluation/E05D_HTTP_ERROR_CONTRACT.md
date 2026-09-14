# E-05D HTTP 稳定错误契约

## 1. 目的

让网页、脚本和后续 MCP adapter 只依赖稳定机器码，不解析 FastAPI、Pydantic、Qdrant或模型供应商的异常文本。

所有公开 HTTP 非成功响应采用同一外壳：

```json
{
  "error": {
    "code": "稳定机器码",
    "message": "面向调用方的说明"
  }
}
```

## 2. 当前错误码

- `invalid_request`：请求字段未通过校验；
- `source_lookup_unavailable`：只找原文能力未配置；
- `document_catalog_unavailable` / `evidence_catalog_unavailable`：对应目录未配置；
- `document_not_found` / `document_content_not_found` / `evidence_not_found`：资源未发布或本地内容不可用；
- `internal_error`：未预料异常，公开响应不包含内部异常内容；
- `http_error`：尚未细分的标准 HTTP 错误。

## 3. 验证结果

真实服务已验证：非法查询返回 422，未知证据和未知文档均返回 404；三者都只包含 `error.code` 和 `error.message`。测试客户端还验证了未知内部异常返回脱敏的 500，而详细堆栈仅进入服务日志。

机器可读结果位于 `data/eval_results/m5-error-contract-v1.json`，可通过 `scripts/run_m5_error_contract_smoke.py` 复现。
