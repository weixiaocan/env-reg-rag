# T6c 报告：标题层级与 OCR 段落重建

## 结论

T6c 的 A/B/C 已实现并完成离线验证。验证使用候选 `corpus-966c89b264cc` 的既有 page-intermediate、公式缓存 `5d3264515515e228` 和独立 OCR 缓存 `ocrv6-ef06411e1606ba35`，只在内存重算 Canonical/chunks；未覆盖正式产物，未 ingest/publish，未改 `data/registry/`，未调用外部 LLM。

工程验证通过不等于 23 份法规/标准内容已获权威核验。原图复核仍发现若干上游 OCR 缺字、页脚/水印、罗马数字小标题和既有阅读顺序问题；它们在逐样本表中明确列出，没有计作 T6c 已修复内容。

## A/B/C 覆盖

### A. 分项不得成为章/节标题

- `heading_hierarchy` 拒绝句末/句中正文标点、多行 OCR 条文、长规范性语句形成章级 heading。
- bare-number 根标题增加实际 23 文档适用的几何门槛：真实 bare-number 章标题居中；左对齐数字行按分项/正文处理。`4地块项目评价` 这类 OCR 丢空格的真实标题仍保留。
- 点号编号只继承编号前缀一致的数字祖先；`5.10.7` 不再继承 `1 …`，`4.3.13` 不再继承 `4.4`。缺失祖先时缩短路径，不编造标题。
- 没有采用跨全文“章号只增不减”：条文说明会重启编号，且早先伪根会错误压掉后续真实标题。该全局状态在离线样本中确实破坏了 `4 地块项目评价`，故改为局部前缀一致性。

### B. OCR 行到段落/条文重建

- 只对 `full_ocr` / `hybrid` 页生效；native 页保持原元素边界。
- 在列检测之前，按上游页内行流、text region、垂直间距、条文/分项起点和句末标点合并；这样列排序不会先拆散同一 region 的首行与续行。
- 数字开头但不是新条文的续行（实样 `5.6.2、5.6.5 和 5.7.1 条的规定；`）并入上一分项。
- 合并文本以换行原样连接；每条原始行 bbox 保留为独立 `primary` / `continuation` source span。provenance 和 generation 记录 `ocr-paragraph-reconstruction-1` / `t6c-1`。
- 独立 OCR fusion 元数据在合并后仍保留，质量仍为 `needs_review`。

### C. 面包屑不得进入正文

- chunk pending 单元的 `section_path` 改变时先 flush，不再把不同路径的正文合并到同一 chunk。
- 根因补充：旧 prefix cache 只以 label 为 key，同 label、不同 title 会复用陈旧前缀。新 Canonical 使用 label+title key；`section-prefix-rendering-1/t6c-1` 标记新语义。
- 旧 Canonical 没有该标记，继续按历史 labels-only 投影，保证已发布 chunk ID/黄金集绑定可重复；完整 pytest 中黄金集与事实账本门禁通过。

## 离线重算与 23 文档统计

命令：

```powershell
.venv\Scripts\python.exe eval\audits\ocr_fix\analyze_t6c_recompute.py
```

审计产物：`eval/audits/ocr_fix/T6c_recompute.json`，SHA-256 `a289cc94c238fecf97c4bc1636f2ce0cdd30efbd243989e5b7b213e7e596e8b7`。脚本内保留 pre-T6c labels-only prefix cache 仅用于 before 复现；after 使用工作区代码。

| 指标 | before | after |
| --- | ---: | ---: |
| 文档 | 23 | 23 |
| chunk | 4510 | 4568 |
| 分项型不同根节点 | 94 | 0 |
| 以正文句末标点结尾的 heading | 234 | 0 |
| 纯文本 chunk 正文含 ` > ` 面包屑 | 194 | 0 |
| OCR text element | 14709 | 9326 |
| OCR 原始行/source span | 14709 | 14924 |
| OCR 平均每 element 行数 | 1.000000 | 1.600257 |
| 半句结束且下一元素同 section 的 chunk 信号 | 1085 | 945 |

`OCR 原始行` after 多 215 是既有独立 OCR fusion 插入行被 source span 显式计入，不是改写源文本。半句信号下降但没有归零：固定 token 切分、上游缺行和本卡外原生页问题仍可形成该信号，不能据此宣称内容质量全部通过。

## 指定 20 样本逐项对照原图

20/20 均以旧样本 source span 匹配到新 chunk 或 Canonical heading；查看了全部 26 张 context 原图（含跨页样本第二页）。下表给出新路径和 body 结果；每项未截断 `old_body`、`new_body`、element 文本、overlap、原图路径均在 `T6c_recompute.json -> samples[]`，避免在报告内截断长条文后误作完整正文。

| sample | 新 heading_path | 新 body / 原图判定 |
| --- | --- | --- |
| `ocr-1e4f99290e1e-013` | `4.2.24` | body 以 `4.2.24生物滞留设施宜设置雨水径流预处理设施` 开始；假根已清除，但仍混入公式符号/后续行，原图不支持完整 body，属既有结构问题，未宣称 exact。 |
| `ocr-1e4f99290e1e-024` | `7 运行维护 > 7.3 应急管 > 7.3.3` | 7.3.3 首行与 1–4 款连续，跨 p34–35；与原图一致。 |
| `ocr-4336797dedd1-011` | `4 地块项目评价` | 识别为 heading，按既定规则不单独产生正文 chunk；Canonical 文本 `4地块项目评价` 与原图一致。 |
| `ocr-4336797dedd1-024` | `5.1 评分项 > 5.1.1` | body 为 5.1.1 完整段落；旧面包屑正文已消失，与原图一致。 |
| `ocr-60bfe78380e9-005` | `2 基本规定 > 2.1 规模和布局 > 2.1.10` | 首行/续行合为同一条文；原图中的“为单位”在上游文本已缺失，本卡禁止补字，结构修复但文字非 exact。 |
| `ocr-60bfe78380e9-011` | `2 基本规定 > 2.3 运行维护 > 2.3.2` | 2.3.2 自身两行已合并；原图 2.3.3 首行在 page-intermediate 缺失，其续行仍单列并可与该 chunk 合并，属上游缺行，非 T6c 可补写。 |
| `ocr-60bfe78380e9-024` | `4 污水系统 > 4.3.13` | 不再继承不一致的 `4.4`；4.3.13 行已合并。body 仍含后续 `装置直接连接` 残片，未判 exact。 |
| `ocr-adf038d73f82-005` | `4.1 监测方案 > 4.1.8` | 4.1.8 与 1–5 款连续；页脚 `.7` 仍在正文，属于 T6b D/T6d 噪声范围。 |
| `ocr-adf038d73f82-022` | `5.3 降雨入流入渗计算 > 5.3.2` | p47–48 条文说明连续；旧正文前缀 `X > Y > Z` 已消失，与原图一致。 |
| `ocr-adf038d73f82-023` | `5.2 旱天入流入渗计算 > 5.2.7` | 5.2.7 三行连续；旧正文面包屑已消失，与原图一致。 |
| `ocr-c3c3df5ae9ca-009` | `5.4.7` | 不继承编号不一致的旧祖先；5.4.7 两行完整，与原图一致。 |
| `ocr-c3c3df5ae9ca-016` | `7 污水和再生水处理 > 7.8 生物膜法 > 7.8.3` | 7.8.3 两行完整；body 末尾仍带下一罗马数字小标题 `Ⅱ生物接触氧化池`，未判 exact。 |
| `ocr-c3c3df5ae9ca-018` | `7 污水和再生水处理 > 7.9 供氧设施 > 7.9.7` | 7.9.7 两行连续，与原图一致。 |
| `ocr-c3c3df5ae9ca-019` | `7.12.1` | `比较合` 与下一行 `理时，可采用污水自然处理。` 已合为完整条文；路径保守截短，不编造缺失祖先；与原图一致。 |
| `ocr-c3c3df5ae9ca-024` | `8 污泥处理和处置 > 8.5 污泥机械脱水` | body 为原图 `I一般规定`；罗马数字小标题仍按 paragraph 投影，属当前 heading 词法外，文本本身一致。 |
| `ocr-fd10712cb0f7-004` | `4 土石方与地基处理 > 4.6 质量验收标准 > 4.6.2` | 4.6.2、主控/一般项目及分项续行连续，跨 p32–33；与原图目标区域一致。 |
| `ocr-fd10712cb0f7-008` | `5.10.7` | `第5.6.1、` 后的数字续行 `5.6.2、5.6.5和5.7.1条的规定；` 保持同一 body；错误根已去除。chunk 在后续第 6 款按预算结束，不把该切分说成整条 5.10.7 全文完成。 |
| `ocr-fd10712cb0f7-016` | `8 管道附属构筑物 > 8.3 支墩 > 8.3.4` | body 为完整 8.3.4，与原图一致；旧 `第5章…规定执行；` 假根消失。 |
| `ocr-fd10712cb0f7-017` | `8 管道附属构筑物 > 8.5 质量验收标准 > 8.5.1` | 8.5.1 与 1–8 项跨 p164–165 连续，与原图目标区域一致。 |
| `ocr-fd10712cb0f7-024` | `9 管道功能性试验 > 9.1 一般规定 > 9.1.1` | 新匹配 body 从条文说明连续段开始，source overlap `0.947517`；文字与 p242–243 原图一致，但 chunk 边界较旧样本移动，未称同一 chunk。 |

## 测试

1. 聚焦测试：

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_heading_hierarchy.py tests/test_canonical_assembly.py tests/test_v2_chunker.py tests/test_independent_ocr_fusion.py
```

结果：83 tests，100% 通过。

2. 完整测试：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

结果：1169 tests collected，100% 通过。仅有两项既有 warning：Starlette `BlockingPortal` deprecation；Qdrant local mode 忽略 `search_params`（除 `idf`）。

3. `git diff --check`：通过；只有工作区 LF/CRLF 提示，无 whitespace error。

## 边界与未完成项

- 本卡没有修改 OCR/fusion 字符内容、usage policy、ID 计算函数或 registry。
- 没有生成/覆盖 `data/canonical/v2/corpus-966c89b264cc/` 正式候选；`T6c_recompute.json` 是离线审计证据，不是发布制品。
- 上表明确列出的上游缺行、页脚/水印、罗马数字小标题和剩余正文边界问题仍需 T6d/后续卡处理。
- 自动测试与 26 张样本原图核对只证明本卡覆盖的工程规则和这些样本结果，不代表全库 OCR/法规内容质量已通过人工核验。
