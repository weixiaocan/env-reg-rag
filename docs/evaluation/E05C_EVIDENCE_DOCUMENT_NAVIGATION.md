# E-05C 证据与文档跳转 HTTP 验收

## 1. 验证目标

验证网页取得回答后，可以使用稳定证据 ID 重新读取证据详情，并从证据的文档版本 ID 打开本地清单允许的 PDF 文件。

## 2. 接口

- `GET /api/v1/evidence/{evidence_id}`：从已发布正式语料或原文定位语料读取证据；
- `GET /api/v1/documents/{document_version_id}`：读取文档名称、标准号、来源和本地文件可用状态；
- `GET /api/v1/documents/{document_version_id}/content`：只提供 `inventory.csv` 登记且实际位于 `data/raw` 下的 PDF。

浏览器在内容 URL 后附加 `#page=N`，由 PDF 查看器跳到证据的物理页。服务器支持 Range 请求，浏览器不必先下载完整文件。

## 3. 安全约束

- 文档 ID 由完整 SHA-256 的前 16 位生成，不能传入任意文件路径；
- adapter 对解析后的文件路径再次验证：必须位于项目 `data/raw` 内且扩展名为 PDF；
- 完整 SHA-256 相同的文件别名共享一个文档版本；只有前缀相同但完整哈希不同才视为冲突；
- 实验 Qdrant 投影只允许读取 `source_locator_only` 证据。

## 4. 真实结果

公式案例通过全部三段接口：

- 证据：`ev_58337104c115f4ddb491ad736f859ae7`；
- 文档版本：`doc_adf038d73f824faf`；
- 位置：第 19 物理页、5.2.4；
- PDF Range 响应：`206`，文件头为 `%PDF-`。

机器可读结果位于 `data/eval_results/m5-evidence-navigation-v1.json`，可通过 `scripts/run_m5_evidence_navigation_smoke.py` 复现。
