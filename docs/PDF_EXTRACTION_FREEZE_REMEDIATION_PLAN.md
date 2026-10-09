# PDF 提取链路冻结整改方案与审核交接

## 文档控制

- 状态：待项目负责人批准
- 版本：v1.0
- 修订日期：2026-09-24
- 实施角色：CloudCode
- 审核角色：独立审核 Agent
- 范围：PDF → page-intermediate → V2 Canonical → chunk → Qdrant 发布门禁
- 当前基线：`corpus-069284e6d60e`
- 关联约束：`AGENTS.md`、`docs/ARCHITECTURE.md`、`docs/SCOPE_AND_DEFERRED.md`

本文是本轮整改的唯一执行清单。CloudCode 不得用本文件替代仓库约束，也不得因某项测试通过而扩大“内容质量已验证”的声明。每完成一个任务必须停止，提交证据给审核 Agent；只有收到 `PASS` 才能进入下一任务。

---

## 1. 目标、约束与非目标

### 1.1 必须达成的目标

| ID | 要求 |
| --- | --- |
| R1 | 每页先进行 native 文字质量判断，失败后进入 OCR；native 合格也必须继续版面分析。 |
| R2 | 正文、表格、公式、图片四类内容必须有 `completed / failed / unsupported / not_detected` 之一，不得静默跳过。 |
| R3 | page-intermediate 中的真实结构化结果、来源区域和质量信息必须无损进入 V2 Canonical，不能在装配时退化成另一套近似结果。 |
| R4 | `needs_review`、`quarantined`、不可读或来源状态缺失的元素默认不得支持回答，只能定位或排除。 |
| R5 | corpus version 不可变；任何内容、配置、模型或质量决策改变都生成新候选版本。 |
| R6 | chunk 必须可追溯到文件 SHA-256、物理页、真实 region/element ID、bbox、处理配置和质量决定。 |
| R7 | 发布前必须同时验证制品完整性、执行完整性、证据准入策略和固定质量评估。 |
| R8 | PDF、页面图像和裁剪图只在本地处理，不上传新的外部服务。 |
| R9 | 全库重跑、Qdrant 正式集合覆盖和 `corpus-current.json` 切换必须分别获得项目负责人明确授权。 |
| R10 | 完成后冻结提取配置、模型版本和质量策略；后续检索评估只允许调整分块、召回和排序，提取问题另开新版本处理。 |

### 1.2 非目标

- 不在本轮实现 P7 增量 embedding/Qdrant upsert。
- 不进行法规来源、效力的持续联网核验。
- 不宣称全库每个字、表格、公式和图片都经过人工核对。
- 不建设完整商业化人工审核平台；只要求可复现的审核队列与证据包。
- 不调用真实外部 LLM 做烟测，除非项目负责人另行授权提供方和数据发送。

### 1.3 当前已确认基线

- 当前指针、在线 Qdrant 和 ingest report 为 4182 chunks；同版本 manifest 与 README 仍记录 4176，口径不一致。
- 在线 Qdrant 为 4182 points：3915 `answer_and_citation`、267 `source_locator_only`。
- 在线结构化块中：formula 210 个，202 个可回答；table 260 个，258 个可回答；figure 38 个，全部可回答。
- 当前文字扫描为 23 文档、1144 页、59 个启发式坏页、5 个 mixed、0 个 `garbled_text`。
- structure audit 明确为 `full_recall_established=false`、`full_corpus_content_quality_established=false`。
- 当前完整 unittest：419 项中 418 通过，1 项因 `docs/TEXT_QUALITY_RECOVERY.md` 未纳入文档允许清单而失败。
- QXT 修复使用了硬编码专项脚本原地修改已发布版本，不能作为未来标准发布流程。

这些数字只是整改起点。CloudCode 在 T00 中必须重新只读核对，不得直接复制本文数字作为当前事实。

---

## 2. 核心设计决定

### D1：质量状态与执行状态分离

保留三个独立维度：

```text
execution_status = pending | running | completed | failed | unsupported | not_detected
quality_status   = passed | needs_review | quarantined
usage_policy     = answer_and_citation | source_locator_only | excluded
```

- `completed` 只表示执行完成。
- `passed` 必须有明确的机器规则或人工审核依据。
- 未知或缺失状态采用最保守策略，不得默认可回答。
- 不恢复 region 级 `publishable` 字段。

### D2：准入策略由 Canonical 元素拥有并向 chunk 单向投影

Canonical element 必须保存质量决定及依据。chunk 的策略取全部来源元素中最严格者：

```text
excluded  > source_locator_only > answer_and_citation
```

这里的 `>` 表示“更严格”。只要一个来源元素不可回答，混合 chunk 就不可回答。Qdrant enrich、检索适配器和 UI 不得提升策略。

### D3：结构化内容默认只定位，显式复核后才能升级

- 表格、公式、图片默认 `source_locator_only`。
- 只有绑定明确 review/assertion ID，且对应内容范围通过类型化质量门槛时，才可升级为 `answer_and_citation`。
- 一个单元格或公式通过不自动升级整页或整个文档。

### D4：V2 Canonical 只装配，不再重新识别或近似重建

- 表格直接消费统一入口的 structured cells、row/column span、表头、单位、脚注和回退 provenance。
- 公式以检测 region 身份与公式缓存严格逐项关联。
- 图片使用真实本地裁剪图、SHA、图注和引用关系。
- 文字保存 native/OCR 路由、引擎、置信度、页级 decision 和原因。

### D5：候选、发布和回退均为不可变状态转换

```text
built_candidate
  → assembled_candidate
  → verified_candidate
  → indexed_candidate
  → published
  → superseded（仍保留用于回退）
```

任一阶段不得原地修补已发布版本。诊断性 `--only`、`--allow-garbled` 产物不能进入正式发布目录。

### D6：质量扫描是正式版本制品，不是 observability 旁路报告

文字、结构、策略与类型化质量报告必须被 manifest 以 SHA-256 绑定。发布门禁读取这些正式制品；`data/observability/` 可保存副本或运行日志，但不能作为唯一依据。

---

## 3. 目标数据契约与不变量

### 3.1 Page 级最小字段

- 文件 SHA-256、物理页、尺寸、旋转。
- extraction route、engine/version、config hash。
- `decision_status`、`decision_reasons`。
- text/table/formula/image 四类执行与质量汇总。
- blank 页保留页记录，但不生成正文元素和 chunk。

### 3.2 Region/Element 级最小字段

- 稳定 source region ID 与 Canonical element ID。
- 类型、bbox、物理页、真实本地资产引用。
- execution/quality/usage 三类状态及 reason codes。
- provenance：模型、版本、配置、回退路线和人工 review/assertion ID。
- 文本、表格、公式、图片各自的类型化内容。

### 3.3 发布不变量

1. manifest 引用的 page-intermediate、Canonical、质量报告、chunk manifest 和 ingest report 哈希全部匹配。
2. 文档数、页数、element 数、chunk 数、vector 数、point 数逐层可对账。
3. `garbled_text` 文档数为 0。
4. `needs_review/quarantined` 来源产生的 answer chunks 为 0。
5. 缺失 `usage_policy` 的 point 数为 0。
6. 离线与在线的 answer/locator/excluded 计数按类型完全一致。
7. 每个 formula 检测 region 与公式结果一一对应；缺失、多出、身份冲突均阻断。
8. 当前版本目录已存在时，只允许字节一致复用，不允许覆盖。

---

## 4. CloudCode 执行与交付协议

### 4.1 每次只执行一个 Task ID

CloudCode 收到任务后只能执行指定 Task ID。不得顺手完成下一任务，不得重跑全库，不得发布，不得清理用户文件。

### 4.2 每个任务的固定工作方式

1. 先读取本文件、`AGENTS.md` 和任务涉及的现有实现。
2. 运行任务指定的 baseline/reproduction，保存精确输出。
3. 先添加能暴露目标问题的回归测试，并观察它失败。
4. 只实施该任务范围内的最小完整能力。
5. 运行任务测试与规定范围的回归测试。
6. 检查 README/docs 是否需要同步；不提前宣称后续任务已完成。
7. 停止并提交审核包。

### 4.3 固定审核包格式

CloudCode 每次回复必须包含：

```text
Task ID:
Outcome:
Changed files:
Pre-fix failing evidence:
Implementation summary:
Commands run and exact results:
Artifact/count changes:
Known limitations:
Git diff summary:
Requested reviewer verdict: PASS / REJECT
```

不得只说“测试通过”。命令、测试数量、关键计数和失败输出必须可复现。

### 4.4 审核结论

- `PASS`：满足该任务全部验收证据，可以进入下一任务。
- `REJECT`：存在规格偏差、证据不足或回归；CloudCode 只修本任务。
- `BLOCKED`：缺依赖、授权或环境；不得绕过。

审核 Agent 的 PASS 只覆盖当前 Task ID，不代表整条链路完成。

---

## 5. 分阶段交付任务

## T00 — 冻结前只读基线与问题复现

**结果**：形成机器可读的当前状态基线，不修改语料、Qdrant、指针或 manifest。

**覆盖**：R5、R7、R9。

**必须完成**：

- 核对 current pointer、manifest、page-intermediate、Canonical、ingest report、Qdrant 的版本、时间、hash 和计数。
- 统计每种 element type 的 answer/locator/excluded 数量及缺失 policy 数。
- 固化以下红灯复现：
  - `needs_review` 表格/公式仍产生 answer chunk；
  - enrich 会提升 locator-only；
  - manifest/pointer/ingest 数字不一致；
  - 公式部分缺失不会触发 assemble 失败；
  - 已发布版本可被原地覆盖。
- 记录完整测试基线，不修复测试。

**禁止**：写正式报告文件、刷新 scan、重建索引、修改任何现有制品。

**验收证据**：只读命令、红灯测试或临时 harness 输出、当前 git status；临时文件在提交前清除。

---

## T01 — 证据准入策略闭环

**结果**：quality/usage 状态从 Canonical element 单向投影到 chunk 和 Qdrant，任何后处理不能升级策略。

**覆盖**：R4、R6、R7。

**依赖**：T00 PASS。

**必须完成**：

- 定义并验证 element 级 quality/usage 字段及 reason codes。
- chunk 使用“最严格来源获胜”规则。
- 表格、公式、图片默认 locator-only；显式审核范围才允许升级。
- quarantined/needs_review/状态缺失均不得成为 answer evidence。
- enrich 保留已有 policy；删除无条件覆盖逻辑。
- Qdrant payload 缺 policy 时按 locator-only 或拒绝，不得默认 answer。
- answer 路径继续双重过滤：Qdrant filter + 检索结果后过滤。

**验收证据**：

- 预修复失败、修复后通过的 table/formula/figure/quarantine/mixed-chunk 测试。
- enrich 不升级策略测试。
- missing-policy 安全默认测试。
- 不要求重建当前 Qdrant；只用内存或测试 collection 验证。

**排除**：不在本任务改变具体表格、公式或图片内容装配。

---

## T02 — 文字与页面质量 provenance 无损传递

**结果**：native/OCR/blank/quarantine 的决定可以从 chunk 追溯回 page-intermediate，且不会被 Canonical 装配丢失。

**覆盖**：R1、R2、R3、R4、R6。

**依赖**：T01 PASS。

**必须完成**：

- Canonical page/element 保存 extraction route、engine/version、config、confidence、decision status/reasons。
- OCR 文本 provenance 不得标为 `native_text`。
- blank 页保留页位且无 element/chunk。
- quarantine 页有文本时可保留定位，但不得支持回答。
- 中文启发式对英文目录、短标题、纯数字/公式页产生的假阳性必须有明确路由或复核状态，不得伪造 passed。

**验收证据**：native、OCR、乱码触发 OCR、OCR 仍坏、空白、英文目录、旋转页的固定 fixture 测试；逐项断言 provenance 与 policy。

---

## T03 — 表格 Canonical 忠实装配

**结果**：Canonical table 直接来源于统一入口的结构化表格结果，不再从重叠普通文字近似重建。

**覆盖**：R2、R3、R4、R6。

**依赖**：T02 PASS。

**必须完成**：

- 消费 `region.tables` 的 cells、row/column span、header roles、caption、unit、footnotes。
- 保留 cropped model、full-page fallback、OCR geometry fallback 的每次尝试与质量原因。
- fallback execution completed 仍默认 needs_review/locator-only。
- 处理跨页续表时只依据显式表号和关系证据，不按相邻页猜测。
- 结构化表格内容与原文字块去重，但保留各自来源身份。

**验收证据**：简单表、多级表头、合并单元格、单位/脚注、跨页续表、模型超时 fallback、无 cells 失败案例；运行现有 table gold/assertion 测试。

---

## T04 — 公式区域与缓存严格对账

**结果**：所有公式检测 region 均有唯一公式结果或显式失败状态，禁止部分静默遗漏。

**覆盖**：R2、R3、R4、R6、R7。

**依赖**：T02 PASS；可与 T03 实现顺序相邻，但仍需单独审核。

**必须完成**：

- 用稳定 region ID，或文件 SHA + 页码 + bbox + processing config 的确定性身份关联。
- assemble 检查 detected、loaded、assembled 三组身份完全一致。
- 缺文件、损坏 JSON、少一个、多一个、bbox/配置冲突均硬失败或产生明确失败制品；不得静默 skip。
- 保留真实 crop、公式编号、参数来源、条件和人工 review ID。
- 未人工批准公式保持 locator-only；不得用于自动计算。

**验收证据**：完整匹配、部分缺失、额外结果、损坏缓存、错误 bbox、跨页参数、人工 gold 升级范围测试。

---

## T05 — 图片与图文关系忠实装配

**结果**：Canonical figure 使用真实本地裁剪资产，图注和正文引用有来源证据，未知关系不猜测。

**覆盖**：R2、R3、R4、R6、R8。

**依赖**：T02 PASS。

**必须完成**：

- 使用统一处理产生的 `image_ref`、image SHA、crop bbox，不创建 placeholder asset。
- 图注必须基于原文文字与明确图号；歧义关系保存 needs_review。
- 没有图注或正文引用时保留 locator，不生成图片描述。
- chunk/source_regions 可打开对应原页区域。

**验收证据**：唯一图注、多图歧义、无图注、正文引用、扫描图、矢量图漏检信号和真实资产存在性测试。

---

## T06 — 不可变候选与发布门禁

**结果**：构建、装配、入库、发布成为可验证的不可变状态机，诊断路径不能绕过发布门禁。

**覆盖**：R5、R7、R9。

**依赖**：T01–T05 全部 PASS。

**必须完成**：

- 停用硬编码 QXT 原地修补作为正式路径；专项恢复只能成为统一 build 的输入/override，并产生新版本。
- manifest 绑定 page-intermediate、每个 Canonical、质量报告、chunk manifest、ingest report 的 SHA-256。
- assemble 写新目录；目录已存在但内容不一致时硬失败。
- ingest 在连接正式 Qdrant 前完成全部离线校验。
- publish 检查 manifest ready、0 garbled、策略不变量、数量/hash、固定检索烟测。
- publish 保存可用 rollback record；失败不切指针。
- 诊断性 `--allow-garbled` 产物带不可发布标记，ingest 必须拒绝。
- metadata enrich 合并回正式 build，或变为保持策略的幂等步骤。

**验收证据**：篡改 hash、旧目录覆盖、诊断产物、count mismatch、policy mismatch、发布失败不切指针、回退指针测试；不得操作当前正式集合。

---

## T07 — 统一 v7 全库候选重建

**结果**：23 份选定主文档全部通过同一配置和统一入口生成全新候选，不混用旧文字阶段与手术式补丁。

**覆盖**：R1–R9。

**依赖**：T06 PASS；项目负责人显式授权高成本本地重跑。

**执行前必须提交**：

- 预计运行时间、CPU/磁盘影响。
- 新候选版本命名/指纹预期。
- 明确不会切换 current pointer、不会覆盖正式 Qdrant。
- 旧缓存复用规则及为什么不会复用受 v7 影响的阶段。

**完成后必须提交**：

- 源目录规划结果、候选 manifest、23 文档/1144 页对账。
- native/OCR/quarantine/blank/failed 分布。
- 四类 region 的 detected/not_detected/completed/failed/unsupported 分布。
- 公式逐项对账、Canonical validation、质量报告和失败清单。
- 新旧候选差异，特别是 QXT 和 5 个 mixed 文档。

**禁止**：`--publish`、覆盖 `corpus_v2`、修改 `corpus-current.json`。

---

## T08 — 提取质量验收包

**结果**：对新候选完成独立于流水线“执行成功”的内容质量评估，给出可冻结或不可冻结结论。

**覆盖**：R6、R7、R10。

**依赖**：T07 PASS。

**必须评估**：

- 文字：关键条款、编号、数字、比较符、单位、标点。
- 表格：cell、行列、span、表头、单位、脚注、跨页关系。
- 公式：归一化 LaTeX、分式/上下标/正负号、编号、参数和条件。
- 图片：检测框、真实资产、图号、图注、正文引用。
- 特殊页：native、扫描、旋转、目录、英文、空白、水印、双栏。
- 版面漏检：使用独立原 PDF gold/抽样，不得拿模型自己的检测输出证明自己没有漏检。

**报告必须区分**：

- 工程契约测试；
- 在线/本地运行烟测；
- 自动内容质量指标；
- 人工抽检；
- 未验证项目和坏例。

**冻结建议只能是**：`freeze_recommended`、`conditional` 或 `not_ready`，并列出依据。

---

## T09 — 候选索引、发布与冻结

**结果**：只有通过 T08 的候选才建立正式索引、切换指针，并成为后续分块/检索评估的固定提取基线。

**覆盖**：R7、R9、R10。

**依赖**：T08 给出 `freeze_recommended`；项目负责人分别授权正式 Qdrant 重建和指针切换。

**必须完成**：

- 分块、embedding、Qdrant point 三者数量一致。
- offline/online policy counts 按 element type 一致。
- Qdrant ready，固定 `source_lookup` 烟测通过。
- 回退记录可用，旧 corpus 和旧索引所需信息保留。
- README、ARCHITECTURE、SCOPE_AND_DEFERRED 的版本、数字、能力口径同步。
- 完整 pytest/unittest 全绿。
- 生成冻结记录：corpus version、全部配置/model hash、质量报告 hash、已知限制。

**冻结后的规则**：后续检索评估不得原地改提取制品；发现提取缺陷时记录坏例并另开新 corpus version。

---

## 6. 审核矩阵

| 任务 | 审核重点 | 不能作为通过依据 |
| --- | --- | --- |
| T00 | 当前事实、红灯可复现、无写操作 | 文档描述、历史日志单独存在 |
| T01 | 策略单向且默认拒绝 | 仅证明 query filter 存在 |
| T02 | OCR/native/quarantine provenance 到 chunk 完整 | 页面有 text 字段 |
| T03 | structured table 忠实进入 Canonical | 表格模型返回成功 |
| T04 | 公式逐 region 对账 | 公式总数非 0 |
| T05 | 真实图片资产和关系证据 | placeholder 或自动描述 |
| T06 | 不可变、hash、失败不发布 | point 数相等 |
| T07 | 同配置全库候选、无发布 | 单独修好 QXT |
| T08 | 独立内容质量证据 | 单元测试全绿 |
| T09 | 策略/数量/烟测/回退/文档一致 | 服务 health=200 |

---

## 7. 风险与处理原则

| 风险 | 影响 | 处理 |
| --- | --- | --- |
| 中文可读性规则误伤英文、目录、公式页 | 不必要 OCR 或 quarantine | 保留类型化原因与人工复核，不以放宽为默认方案 |
| 表格结构修正导致 chunk 文本和 ID 大量变化 | 检索评估基线变化 | 新 corpus version，保留新旧差异报告 |
| 公式严格对账暴露大量旧缓存缺口 | assemble 阻断 | 视为真实失败，不用 `--allow-garbled` 或跳过绕行 |
| 全库重跑耗时、占磁盘 | 交付延迟 | T07 前单独估算并获授权，保留旧制品 |
| 结构化内容全部 locator-only 降低回答覆盖 | 短期召回可见但不能回答 | 优先安全；用明确人工 review/assertion 逐项升级 |
| 当前工作区已有大量用户修改和运行产物 | 改动相互污染 | 每任务只提交相关文件，不清理、不 reset、不覆盖用户文件 |

---

## 8. 需求追踪

| 要求 | 设计决定 | 交付任务 | 主要证据 |
| --- | --- | --- | --- |
| R1 | D4 | T02、T07 | native/OCR/版面 fixture 与全库路由统计 |
| R2 | D1、D4 | T02–T05、T07 | 四类状态账本与失败测试 |
| R3 | D4 | T02–T05 | Canonical 忠实性测试 |
| R4 | D1–D3 | T01–T05 | policy 回归与在线/离线计数 |
| R5 | D5 | T06、T07、T09 | hash/不可变/回退测试 |
| R6 | D2、D4 | T01–T05、T08 | region→element→chunk 回查 |
| R7 | D5、D6 | T06、T08、T09 | manifest 门禁与质量报告 |
| R8 | 本地处理约束 | T05、T07 | 无外传配置与本地资产 |
| R9 | D5 | T06、T07、T09 | 授权记录与指针差异 |
| R10 | D5、D6 | T08、T09 | 冻结记录与新版本规则 |

---

## 9. 批准

项目负责人批准本文后，CloudCode 从 T00 开始。批准只授权按任务逐项实施，不授权提前全库重跑、覆盖 Qdrant、切换发布指针、调用外部 LLM 或上传 PDF。

审核 Agent 应在每次审核中引用 Task ID，并明确给出 `PASS / REJECT / BLOCKED`。只有 T09 PASS 后，才能声明“PDF 提取链路已冻结，可以把后续主要评估集中在分块和检索”。
