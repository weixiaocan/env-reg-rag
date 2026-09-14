# P7 开源发布前审计 v1

> 历史快照：本文记录 2026-09-08 审计时的仓库与发布决策。当前仓库已改用清理后的新 Git 历史，并决定公开携带 `data/raw/` 下全部 PDF；现行数据权利边界、文件口径和下架机制以根目录 `DATA_NOTICE.md` 及 `docs/OPEN_SOURCE_RELEASE.md` 为准。

## 1. 结论

**当前仓库不应直接公开。**

本轮是发布门控审计，不是法律意见。当前代码、运行能力和评估证据已可用，但在开源许可、PDF 再分发和 Git 历史清理三个方面仍有阻断项。未处理前，只能说“已进入开源准备”，不能说“仓库已可公开”。

## 2. 发布阻断项

| ID | 级别 | 发现 | 影响 | 处理前状态 |
| --- | --- | --- | --- | --- |
| OSR-001 | 阻断 | 仓库没有项目级 `LICENSE`、`NOTICE` 或第三方许可清单 | 第三方无法确定代码使用权利，依赖告知义务也没有交付 | 未通过 |
| OSR-002 | 阻断 | M2 直接依赖 `PyMuPDF==1.28.2`，安装包元数据为“GNU AGPL 3.0 或 Artifex 商业许可” | 不能在未选择 AGPL 兼容发布方案或商业许可时，直接给项目加 MIT/Apache-2.0 并宣称已完成兼容性审计 | 待选择 |
| OSR-003 | 阻断 | 5 个 Git 提交的历史中出现过 46 条 PDF 路径 | 当前工作树删除和 `.gitignore` 无法清除旧提交中的 PDF；直接 push 会带上历史语料 | 未通过 |
| OSR-004 | 阻断 | 历史 `.env.example` 的 `OPENAI_API_KEY` 为 `sk-` 开头、长度 35、不含常见占位词；5 个提交中是同一个值 | 按曾泄露凭据处理；即使已吊销，公开历史也不应保留该值 | 未通过 |
| OSR-005 | 阻断 | 个人姓名/本机标识模式在 3 个提交中命中已删除的 `.workbuddy-ai/memory/2026-09-03.md` 和 `.workbuddy-ai/memory/MEMORY.md` | 仅删除当前文件不足以满足“所有公开文档无真名”要求 | 未通过 |
| OSR-006 | 阻断 | 23 份本地 PDF 没有任何一份已记录明确的再分发开放许可 | “官方网站可下载”只证明来源可核验，不自动授予在 GitHub 再分发全文的权利 | 未通过 |

## 3. 依赖许可审计

### 3.1 直接依赖元数据

以当前 `.venv` 内安装包的 `METADATA` 为机器事实源，直接依赖归类如下：

| 许可类型 | 直接依赖 |
| --- | --- |
| AGPL 3.0 / 商业双许可 | PyMuPDF 1.28.2 |
| Apache-2.0 或以 Apache-2.0 为主的复合声明 | paddlepaddle、paddleocr、paddlex、qdrant-client、torch、transformers |
| MIT | docling、langchain-core、langchain-openai、pydantic、fastapi、mcp |
| BSD-3-Clause | pypdf、python-dotenv、starlette、uvicorn、httpx |

Qdrant Server 的官方仓库为 Apache-2.0；镜像版本已在 `compose.yaml` 固定为 `qdrant/qdrant:v1.19.1`。[Qdrant LICENSE](https://github.com/qdrant/qdrant/blob/master/LICENSE)

Docker 镜像内固定的 `BAAI/bge-small-zh-v1.5` 模型卡标注 MIT License。[BAAI/bge-small-zh-v1.5 模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)

### 3.2 PyMuPDF 决策点

PyMuPDF 官方文档说明它与 MuPDF 采用 AGPL/商业双许可；官方 FAQ 还特别以 RAG PDF 解析为例，说明 AGPL 义务会适用。[PyMuPDF License and Copyright](https://github.com/pymupdf/PyMuPDF/blob/main/docs/about.rst)、[PyMuPDF FAQ](https://pymupdf.readthedocs.io/en/latest/faq/index.html)

因此当前有两条可行路线：

1. **保留 PyMuPDF**：将项目按与 PyMuPDF AGPL 条款兼容的方式发布，并保留所有 MIT/BSD/Apache 许可和 NOTICE 义务；
2. **希望项目使用 MIT/Apache-2.0**：先替换或完全隔离 PyMuPDF，重跑 M2 解析回归，再根据新依赖图做许可审计。

FSF 的许可兼容说明指出 Apache-2.0 与 GPLv3 兼容，且其 GPL FAQ 说明兼容矩阵中对 GPLv3 的表述同样适用于 AGPLv3。这支持“AGPL 主项目 + Apache/MIT/BSD 依赖”作为候选方案，但仍需逐项保留原始声明。[GNU GPL FAQ](https://www.gnu.org/licenses/gpl-faq.en.html)、[GNU License Compatibility](https://www.gnu.org/licenses/license-compatibility.en.html)

当前尚未完成整个 Python 转移依赖闭包、`python:3.11-slim` 基础镜像内 Debian 包和所有 NOTICE 文件的最终清单，因此看板中“依赖许可兼容性”仍不能勾选。

## 4. PDF 再分发审计

`document_reviews.csv` 已证明 3 份正式语料的官方来源和本地文件一致性，但这是“可用于本项目查询”的数据准入证据，不是“可将原 PDF 重新上传到 GitHub”的授权。

官方来源中已找到的最明确边界来自中国气象局：其版权声明禁止商业性的原版原式转载，对由其他单位提供的内容要求另行获得授权。[中国气象局版权声明](https://www.cma.gov.cn/2011ggxx/202111/t20211104_4194610.html)

武汉市水务局与重庆市住房城乡建设委员会网站页脚均声明网站内容版权所有，未在目标 PDF 或登记记录中发现 Creative Commons 或其他明确再分发许可。[武汉市水务局](https://swj.wuhan.gov.cn/)、[重庆市住房城乡建设委员会](https://zfcxjw.cq.gov.cn/)

因此 P7 默认发布策略应为：

- 不将 `data/raw/**/*.pdf` 或它们的历史 blob 放入公开仓库；
- 保留文档标题、标准号、官方来源 URL、SHA-256 和处理说明；
- 提供用户自行从官方来源获取文档并校验 SHA-256 的导入流程；
- 评估样例只保留项目自己创建的问题、指标和非侵权的少量定位元数据，不附带整份 PDF 或大段转写全文。

## 5. Git 历史审计

本轮使用本地 `git rev-list --all`、`git log --all --name-only` 和 `git grep -I -l` 扫描 5 个提交。密钥检查只输出字段名、分类、长度和不可逆同值分组，没有输出原值。

| 检查 | 结果 |
| --- | ---: |
| 提交数 | 5 |
| 历史 PDF 路径 | 46 |
| 历史 `.env` 文件 | 0 |
| 含同一个可疑 `OPENAI_API_KEY` 值的 `.env.example` 提交 | 5 |
| 个人姓名/本机标识命中 | 3 个提交、2 个唯一文件 |
| 当前 `.env.example` | 3 个 provider 字段均为占位值 |

历史凭据应按“曾泄露”处理。对于即将新建的公开仓库，更安全的方案是从已清理工作树建立新的公开根提交，同时在公开仓库之外保留旧历史备份。如必须保留原提交，则需要使用专门历史重写工具，并对所有 refs 和重写后对象库再扫描。两者都是会改变 Git 历史的操作，必须单独获得确认。

## 6. 建议的发布门控顺序

```text
选择 PyMuPDF/项目许可路线
  → 生成 LICENSE 与 THIRD_PARTY_NOTICES
  → 排除所有原始 PDF，只保留官方来源清单
  → 新建公开历史或重写旧历史
  → 对新历史重跑密钥、个人信息、PDF blob 扫描
  → 完成第三方复现验收
  → 才能创建公开远程仓库
```

## 7. 当前判定

- P7“确认代码和依赖许可证兼容性”：**未通过，等待 PyMuPDF/项目许可选择**；
- P7“逐份处理 PDF 再分发权利”：**默认不分发 PDF 的策略可行，但历史尚未清理**；
- P7“清理 Git 历史中的 PDF、密钥和个人信息”：**未通过，已精确定位风险类型**。

机器可读摘要保存在 `data/evaluation/p7-open-source-audit-v1.json`。
