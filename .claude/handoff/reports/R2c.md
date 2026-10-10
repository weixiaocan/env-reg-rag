# R2c 完成报告

## 结论

R2c 的代码修复、23 文档 rollout、原页对照和完整 pytest 已执行。本轮没有执行 ingest/publish，没有切换 `data/registry/corpus-current.json`，没有重建 Qdrant，也没有调用外部 LLM。

主要缺陷已修复：正文重号由 100 降到 4，其中武汉 85 -> 0、GB/T 47884 11 -> 0；相邻重复中流水线引入项由 17 降到 0；`9.3.4` 拆分编号、附录 B 层级、GBT 28592 的“共7个等级”、内涝 p15 的 `k0`/`tx` 变量说明均已进入最终 chunks。金标准保持 27/30，正文 missing 由 29 降到 27。

但 R2c 不能宣称所有验收门槛通过：A2 短正文块从 R2b 的 92/4262（2.1586%）变为 101/3821（2.6433%），较 R2b 回退。新增的短块主要是修复重复路径、附录分节后被单独暴露的短条文与表单标签；本轮没有为了达到数字而删除可回查的原文。这是当前唯一已知未达的 R2c 验收项。

## 实现

### 1. 重复路径与回归审计

- 根因一：原生页的同一文字层元素会被多个重叠 PP region 各自收取，region 文本 fallback 又会输出一份，导致武汉 p8 同一 `5.1.1`/`5.1.2` 出现两份。现在未分配原生元素只归属一个权威 region，页面已有文字元素时不再用 region 文本生成第二份正文；对完全重叠的伪粗体尾部 echo 也做了保守去重。
- 根因二：三源字符级合并可在任一单源都没有重复时拼出相邻重复。现在只有当重复片段不存在于任一单源时才折叠，并在行级 audit 中保留修复记录。
- `src/evaluation/text_duplication.py` 实现任务规定的 `([一-鿿，、。；：]{3,12})\1` 扫描，合法判定严格使用“完整重复在任一单源文本中也存在”。`run_rollout.py` 同时写入正文重号、相邻重复和流水线引入数。

R2b 的 `chunks.jsonl` 当时未进入 Git，所以 `git show 0f209d9:.../chunks.jsonl` 无法重建任务文本中的“59 处”。本轮使用上一轮中断工作区保留的 R2b 4703 条 chunks 制作显式基线快照，按上述正则可重现的是 36 处，而不是 59 处。没有把不可重现的历史数字伪造进报告。可重现的逐项结果为：

| 指标 | R2b 快照 | R2c |
| --- | ---: | ---: |
| 相邻重复命中 | 36 | 19 |
| 流水线引入 | 17 | 0 |
| 单源原文已存在（保留） | 19 | 19 |
| 正文重号 | 100 | 4 |

剩余 4 个正文重号分别是《城镇内涝防治技术规范》1、多普勒标准2、GB50268 1；它们不是本轮修复的 region/文字层双路输出。逐文档前后数字和每个相邻重复的 chunk、页码、片段、上下文、判定均在 `rollout/duplication_audit.json`。

### 2. 编号与附录结构

- 原生文字层在同一基线输出的短编号 span 先合并；对已进入单个元素的 `9.\n3.\n4` 也做等价归一。只修改编号前缀，不改正文字符。全库标记 111 个合并后编号，均位于 GB/T 47884；p13 最终为 `9.3.4`，section labels 为 `9 > 9.3 > 9.3.4`。
- `附录X ...` 现为章级 heading，`B.0.n` 为条级节点。R2c 识别 47 个附录条文 chunk，46 个挂接正确；GB50014 的 B.0.1--B.0.11 全部挂在 `附录B` 下。剩余 1 个误挂是 GB50268 p198 `F.0.6`，section labels 仍为 `3 > F.0.6`，已保留在 audit 中，未冒充全部通过。R2b 快照未识别附录条文（0/0）。

### 3. 红章/水印遗漏对照

已直接查看 rollout 渲染的原 PDF 页图，并对照最终 chunks：

1. GBT 28592 p4：原页是“特大暴雨共7个等级”。primary 与 pp_clean 在红章处都丢数字，pp_raw 完整识别为 7。投票合并原先丢弃了 pp_raw 的该字符；现在仅当同一单源中的枚举项数与 `共N个` 自洽时恢复，最终 chunk 为“共7个等级”。
2. 《城镇内涝防治技术规范》p15：原页可见 `k0——衰减常数(h-1)，可取2~7` 和 `tx——下渗时间(s)`。`tx` 中 primary/pp_clean 只有后缀，pp_raw 完整，原为字符投票遗漏；现在按 pp_raw 完整证据恢复。`k0` 与说明后缀的中心同时落入嵌套 formula region，从 text region 路径被排除；现在仅对完整包围 text region 中严格匹配的 `k0`/`tx` 定义使用可追踪 fallback。最终 chunk 同时包含 `k0衰减常数...` 与 `tx——下渗时间(s)`。
3. 武汉 p8、GB/T 47884 p13、GB50014 p112 也分别对照了原页：原页只有一份 5.1.1/5.1.2；p13 明确为 9.3.4；p112 明确开始“附录 B”且包含 B.0.1--B.0.6。新 chunks 与原页一致。

以上是本轮原页人工对照，不代表 23 文档的全量内容已获人工核验。

## Rollout 结果

命令：

```powershell
.venv\Scripts\python.exe eval\audits\middle_layer_v3\run_rollout.py
```

- Canonical 工程不变量校验：23/23；处理 failures 0；review sample render failures 0。
- chunks 4262；elements 11906（text 11359、table 251、formula 210、figure 86）。
- OCR provenance elements 7789，缺 run id 0。
- 正文连续性：missing 27，duplicate 4；条文说明 missing 176，duplicate 0。
- 金标准：text 17/20，structure 16/19，table/numeric 10/10；总体仍为 27/30，失败项仍为 TXT-06、TXT-15、TXT-16。
- A2：101/3821（2.6433%），比 R2b 92/4262（2.1586%）回退；仍显著好于 legacy 617/4087（15.0966%）。

主要制品 SHA-256：

| 制品 | SHA-256 |
| --- | --- |
| `rollout/metrics.json` | `b41af77884e96c0b2d53631c1e6d1600921aa851efa605dc54f2a9a8519147fd` |
| `rollout/exam.json` | `47c8993ec23d9cad7f7580fdeaff51a41ce1793fa789975b6be91526b97ba46c` |
| `rollout/duplication_audit.json` | `7aed126b808dde219d4b1973769e952480b7ce638d28490e9132648312597da8` |
| `rollout/chunks.jsonl` | `fac22eb0557f835811cbba4a56875fcf0c752997ce562f354b9bf94e3c5721d0` |
| `r2b_baseline_snapshot.json` | `f3b94aabd11fbd05c8ea1737c785f7ca24cd84fa3bc5327b86982a7104cdb03e` |

## 测试

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_middle_layer_v3.py tests/test_text_duplication.py tests/test_ocr_region_text.py tests/test_clause_continuity.py tests/test_native_pdf_parser.py
# 54 passed

.venv\Scripts\python.exe -m pytest -q
# 1464 passed, 2 warnings, 0 failed

git diff --check
# passed；仅 Git 的 LF/CRLF 工作区提示
```

新增回归覆盖：原生元素单 region 归属、region fallback 不重复正文、嵌入多条条文拆分、伪粗体尾部 echo、投票引入重复折叠与合法重复保留、`k0`/`tx` 与枚举数字的单源证据恢复、拆分编号、附录层级、R2b 基线快照 schema/commit 验证。

## 边界与后续

- 测试全绿只说明被覆盖的工程契约成立；23/23 `canonical_validation.passed` 也不等于 OCR/内容质量获得权威或全量人工核验。
- A2 仍是明确的未达门槛。后续应单独设计“短条文”与“表单字段标签”的分类/合并契约，不应直接丢弃原文来降低短块数。
- 附录审计仍有 GB50268 p198 `F.0.6` 一个误挂；已显式列入 `duplication_audit.json`。
- 未删除或纳入本提交的 `data/observability/`、`data/quality/` 用户产物与 `.claude/tmp-r2c-baseline/`临时对照目录。
