# 仓库整理与公开部署准备

> 状态更新（2026-10-15）：腾讯云演示工作台已接入 `https://weixiaocan.com/rag/`，原文检索、逐页查看和智谱 GLM-5.2 证据化回答已通过局部在线烟测。未经人工核对的表格和公式可以支持回答，对应结论标注"未核对"。下文 P0–P3 是部署前制定的检查清单，已完成的在线烟测与当前限制以 README 的“公网演示状态”为准；内容质量与法规效力未因此获得认证。

- 状态：In review
- 修订日期：2026-09-29
- 当前正式语料：`corpus-0b9a1fd84e3d`
- 查询入口：Qdrant alias `corpus_v2_live`
- 非目标：黄金集、检索效果评估、PDF 提取、Canonical、分块和索引重建

## 1. 现场基线与问题清单

以下数字来自当前工作区的 Git、发布指针、manifest 和本机 Qdrant，不沿用历史文档快照：

- 工作区已用 `git rm --cached` 取消 29 份 PDF 的 Git 跟踪并暂存删除；29 份本地文件仍全部存在，其中 `data/raw/_official_verification/` 4 份，普通资料目录 25 份。该变化要在提交后才会体现在远端仓库的新快照中；
- 当前 manifest 登记 29 个源文件、24 个独立字节内容、5 个完全重复副本，正式选择 23 份主文本、1144 页；
- 当前 pointer 为 `corpus-0b9a1fd84e3d`，chunk 数 4232；
- `corpus_v2_live` 指向 `corpus_v2__staging__corpus-0b9a1fd84e3d__bi-d70cf457e0cac3fa3abbce0a`；目标 collection 状态 green，point 数 4232；
- 当前回退记录保留了发布前 pointer，但旧 pointer 没有 alias target，`previous_collection=null`，因此不能把这条记录描述成已经验证可一键回退；
- 工作区有大量用户未提交代码、registry、测试、运行报告和临时文件，禁止批量清理或用 Git 恢复覆盖。
- 本轮修复尚未重新构建或替换当前运行容器；旧容器的 OpenAPI 仍暴露 `/api/v1/documents/{id}/content`。在预发布镜像完成重建并验证该路由为 404 之前，服务不得直接对公网开放。

确认的问题：

1. `compose.yaml` 和 Dockerfile 曾默认查询旧 `corpus_v2`，与 alias 发布模型冲突；重新创建容器可能绕过 live alias。
2. `/api/v1/documents/{id}/content` 直接返回完整 PDF，前端和公式诊断链接也使用该路由。
3. 公开查询接口原先没有应用级限流、answer 日额度和问题长度上限。
4. README、ARCHITECTURE、DATA_NOTICE 和 THIRD_PARTY_NOTICES 保留旧语料版本、旧 collection 名和 27 份 PDF 数字。
5. `.gitignore` 明确允许 `data/raw/**/*.pdf`；即使改为忽略 PDF，已经跟踪的 29 份文件仍会继续出现在后续提交，除非另做取消跟踪。
6. `/health` 只证明进程存活；`/ready` 当前验证 Qdrant 和查询入口存在，但尚未核验 alias 实际目标、pointer 目标和 point 数三者一致。

## 2. 保留／整理／可删除候选

### 必须保留

| 对象 | 依据 |
| --- | --- |
| `data/raw/` 本地文件 | 源 PDF；取消 Git 跟踪也不能删除本地源文件 |
| `data/registry/corpus-current.json`、当前 manifest | 正式发布身份与运行时来源 |
| `data/registry/rollback/` | 发布前状态与回退审计证据 |
| `data/registry/source_evidence.jsonl`、inventory、relations | 文档 allowlist、来源和重复关系 |
| `data/canonical/`、`data/model_runtime/` | 当前可复现与本地运行依赖，已由 Git ignore 管理 |
| T07/T08/T09 发布、hashchain、occurrence 和公式核对报告 | 当前发布与人工/程序审核证据 |
| `LICENSE`、`DATA_NOTICE.md`、`THIRD_PARTY_NOTICES.md` | 公开仓库的代码与第三方资料边界 |
| 所有用户现有修改和未跟踪测试 | 尚未完成归属确认，不可清理 |

### 需要整理但暂不删除

| 对象 | 建议 |
| --- | --- |
| 根目录 `_tmp_*`、`_iw*`、`_watch*`、`_wait*` 等 | 先按所属任务和最后修改时间列清单；确认无复现价值后再移出仓库工作目录 |
| `_tmp_extract/`、`_tmp_qxt489_diag/`、`_tmp_t08_evidence/` | 可能包含修复证据；先与 observability/测试引用比对 |
| `data/observability/` 旧日志和失败输出 | 发布证据长期保留；纯进程日志按日期归档，不与发布报告混删 |
| `eval/` | 本任务不评估、不修改；由评测任务单独管理 |
| 旧 Qdrant staging collections | 先确认回退链和备份，再制定保留数量；本轮不删除 |

### 可删除候选（必须逐项确认后执行）

- Python/pytest 缓存；
- 已确认无引用且不承担复现证据的根目录临时脚本与临时文本；
- 已归档、已由正式报告替代的过程日志；
- 空且无占位用途的 `data/backups/`、`data/evidence/`、`data/retrieval/`、`data/regulations/`。

上述仅是候选清单，本轮未删除任何用户文件。

## 3. 已选公开访问方案

### 3.1 PDF 页面访问

选择“服务端按需渲染单页 PNG”：

```text
GET /api/v1/documents/{document_version_id}
GET /api/v1/documents/{document_version_id}/pages/{physical_page}/image
```

公共浏览目录从 `inventory.csv` 读取并核对本地文件 SHA-256，按 24 个唯一内容提供稳定文档 ID，同时保留 29 个文件别名；回答准入仍由正式语料和 `source_evidence.jsonl` 独立控制。页面渲染限制像素预算并拒绝越界页码。完整 PDF 路由已移除，前端只打开命中的物理页。现场验证 24 个内容的首末页均可渲染，共对应 1215 个唯一内容页面。

未选择：

- PDF.js / Range：仍需向浏览器提供完整 PDF 字节；
- 预渲染所有页面：占用额外存储并扩大静态文件暴露面；
- 只显示 OCR 文本：无法满足原版页面核对。

限制：逐页接口无法从技术上阻止访客依次抓取所有页面并自行重组；需要边缘限速、缓存和访问日志降低批量抓取风险。

### 3.2 公开查询限制

应用兜底默认值：

- `source_lookup`：每客户端每分钟 30 次；
- `answer`：每客户端每分钟 3 次；
- `answer`：每客户端每天 10 次；
- `answer`：单实例全局每天 20 次；
- question 最长 1000 字符；
- 超限返回 HTTP 429、稳定错误码和 `Retry-After`。

当前实现是单进程内存计数，服务重启会重置，多个副本之间也不共享。公开部署必须再配置边缘网关限流，并在 LLM 提供商侧设置硬消费上限；不能把应用内计数描述成严格费用上限。

### 3.3 两种运行模式

- `self_hosted` 是 GitHub 默认形态：部署者自行提供 PDF、索引和模型 API Key，不使用仓库维护者的费用额度。
- `demo` 是阿里云面试演示形态：使用维护者在服务器 Secret 中配置的 Key，强制启用匿名 IP 日额度和全站日预算。
- 两种模式使用同一代码和镜像；不建立第二个仓库。
- 当前不建设账号系统，也不允许访客在公开前端填写 API Key。账号、加密密钥托管和多租户配额属于后续 SaaS 范围。

## 4. 公网部署边界

推荐流量路径：

```text
访客 -> HTTPS/CDN 或反向代理 -> FastAPI -> Qdrant 私有网络
                                      -> 外部 LLM（仅 answer）
```

- FastAPI 仅由反向代理访问；当前 Compose 的 `127.0.0.1:8000` 适合作为这一边界。
- 演示版按 `request.client.host` 识别访客。阿里云反向代理必须覆盖客户端提供的转发头，并将 Uvicorn `FORWARDED_ALLOW_IPS` 限定为实际代理地址；不得信任任意来源的 `X-Forwarded-For`。上线验收需用两个不同客户端确认不会都被记成代理 IP。
- Qdrant 不允许公网入口；当前端口只绑定 `127.0.0.1`，正式主机应进一步取消宿主机端口映射，只保留 Docker 内网。
- `.env` 不进 Git；生产密钥使用平台 Secret 注入，不写 Compose、镜像、日志、trace 或前端。
- 本轮检查过程中，`docker compose config` 曾在本地命令输出中展开 `.env` 值；未向外部服务发送，但应在公开部署前轮换受影响的 LLM 密钥。后续配置核对只使用 `docker compose config --no-interpolate`。
- `source_lookup` 不调用 LLM；当日 answer 预算耗尽后，提问自动改走 `source_lookup`，返回相关原文并说明额度已用完（2026-10-19 起）。
- CORS 保持同源，不为公开站点开放任意来源。

## 5. 健康、可观测性与回退

- `/api/v1/health`：只检查进程；用于存活探针。
- `/api/v1/ready`：用于接流量前检查运行查询入口、pointer alias、alias 实际目标、pointer collection 和 point/chunk 数一致；任一不一致返回 503。
- 查询 trace 必须记录语料版本、查询 alias、模式、耗时、模型用量和错误类型，但不得记录密钥。
- 发布采用不可变版本 collection，验证后原子切换 `corpus_v2_live`。
- 回退前先验证目标 collection 仍存在且 point 数/身份匹配，再切 alias，最后恢复 pointer；任一步失败都要补偿或停止。
- 当前唯一回退记录的 `previous_collection` 为空，正式上线前必须做一次不改变数据的回退演练设计核对；本轮不实际切换 alias。

## 6. Git 中 PDF 的两个不同动作

### 今后提交不再包含 PDF

需要两步：

1. `.gitignore` 禁止新增 PDF；
2. 已执行 `git rm --cached` 取消 29 份 PDF 的跟踪；当前处于待提交状态。

`--cached` 不删除本地文件，但会让下一次提交的仓库快照不再包含它们。阿里云演示部署使用服务器私有磁盘只读挂载 PDF，不依赖 GitHub 获取资料。

### 从旧提交中清除 PDF

需要 `git filter-repo` 或同类工具改写全部历史，并强制推送；所有协作者必须重新同步，旧 commit/hash 和现有链接会失效。本轮明确不执行，必须另行获得用户确认并先备份。

## 7. 分阶段实施

### P0：本地安全基线（本轮）

- 修正 Qdrant live alias 默认值；
- 移除整本 PDF HTTP 路由，增加 allowlisted 单页 PNG；
- 前端改为原文页链接；
- 增加应用级限流和 question 长度上限；
- 修正文档当前数字与公开边界；
- 运行 HTTP、配置和限流局部测试。

### P1：提交边界与仓库整理（部分完成）

- 已取消 Git 对 29 份 PDF 的跟踪并验证本地文件全部保留；尚未提交或推送；
- 逐项确认临时文件清单后归档或删除；
- 将当前 registry、回退和发布证据纳入明确的提交策略；
- 不改写历史。

### P2：公网部署加固（需部署环境选择）

- HTTPS、边缘限流、请求体大小、超时和并发限制；
- Qdrant 仅内网；
- 平台 Secret、供应商硬预算、日志保留期；
- 诊断接口默认关闭（本地代码已完成，待新镜像验证）；
- readiness 三方一致性检查（本地代码已完成，待连接实际 Qdrant 验证）。

### P3：上线前验证（需上线授权）

- 构建镜像并在预发布环境运行；
- 验证页面浏览、429、无完整 PDF 路由、health/ready；
- 验证无密钥泄漏和 Qdrant 无公网端口；
- 演练 alias/pointer 回退；
- 用户批准后再正式上线和推送远端。

## 8. 待决策

1. 已确定阿里云服务器和域名；最终版本完成后再配置 HTTPS、反向代理和私有 PDF 挂载。
2. 公开演示保留完整体验：开放逐页原文定位和受额度保护的 `answer`；初始默认每 IP 每天 10 次、全站每天 20 次，可由环境变量调整。
3. 公式复核等内部诊断接口默认不向访客开放。
4. Git 历史清理不执行；现阶段只让新提交不再携带 PDF。
