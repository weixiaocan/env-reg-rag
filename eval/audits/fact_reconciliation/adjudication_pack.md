# 人工裁定材料（14 条待裁定事实，2026-09-30）

台账：`fact_ledger.json`（dataset sha256 `3b1f1cdc459aadb5…`）。
每条事实列出：fact 原文、承载 chunk（实际 chunk_id / 证据索引 / 文档）、
原 PDF 页及已核验页图、机器留痕与**待判断点**。

**接受/驳回由负责人逐条决定，不得代填。** 标注『辅助核验材料』的页图
只是供人工对照的原始 PDF 页（来源可回查：PDF 相对路径 + sha256 + 页码），
**不是 must-hit 绑定，也不改变黄金集**。

裁定记录写入 `fact_rulings.json`，逐条字段：

```json
{"case": "Gxxx", "fact_index": 0, "ruling": "accepted|rejected",
 "adjudicator": "<负责人>", "date": "YYYY-MM-DD",
 "justification": "<理由；驳回时须写明缺陷与处置>",
 "fact": "<与本材料 fact 原文逐字一致>",
 "classification": "<与本材料分类逐字一致>"}
```

`fact` 原文与 `classification` 必须与本材料逐字一致，否则验收判 conflict。

## 处理流程（顺序固定）

1. **逐条裁定并处理驳回项**：接受→记录 accepted；驳回→记录 rejected，
   并按 justification 中的处置（改写 fact / 调整 answerability / 重锚）
   处理后重新对账、重出本材料；
2. **逐题确认 `status=frozen`**；
3. **重生成绑定最终数据集哈希的台账**：
   `python scripts/reconcile_fact_evidence.py --write`（注册表只读合入，
   human_ruling 由重算得出，不得手改台账字符串）；
4. **只读门禁复验**：`_fact_ledger_blockers(dataset, full=True)` /
   `gate_problems(...)` 必须返回空；`--formal` 未经另行授权不得运行。

## 状态转换规则（权威，重算核对）

- `refuse_meta`（真拒答 8 条）→ n/a，不入裁定；
- `covered`（机器支持 73 条）→ machine_approved，不入裁定；
- 机器不可证明例外（本表 14 条）→ 有完整 accepted 裁定记录才
  `human_adjudicated`，否则 `pending`（阻塞 formal）。

---

## 1. G006[f0] — covered_formula_structure

- **fact**：R由最大日雨量项与过程平均日雨量项加权后对测站平均得到
- **承载（must-hit 绑定）**：证据 ev0 · `降雨过程强度等级标准.pdf` · chunk `chunk_8217c10967608ac0a8132e4b7e595610` · PDF p6 · 页图 `eval\audits\fact_reconciliation\renders\降雨过程强度等级标准-p06.png`
- **机器留痕**：公式结构事实：LaTeX 公式逐字在承载 chunk——R=(w·Σ(r_max)_i+(1-w)·Σ(Σr_d/T0)_i)/n，即最大日雨量项（w 加权）与过程平均日雨量项（Σr_d/T0）加权后对 n 个测站平均；改写措辞的 bigram 度量无法核对数学结构。
- **待判断点**：公式结构：LaTeX 公式逐字在 chunk，但『最大日雨量项与过程平均日雨量项加权平均』为结构表述的改写——请判断表述与公式是否等价。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 2. G006[f1] — covered_formula_structure

- **fact**：式中含权重w、过程最大日雨量r_max、过程累计日雨量与历时T0
- **承载（must-hit 绑定）**：证据 ev0 · `降雨过程强度等级标准.pdf` · chunk `chunk_8217c10967608ac0a8132e4b7e595610` · PDF p6 · 页图 `eval\audits\fact_reconciliation\renders\降雨过程强度等级标准-p06.png`
- **机器留痕**：式中定义逐字在承载 chunk：权重 w（区域取0.5）、rmax（日最大雨量值）、rd（日雨量值）、T0（降雨过程持续时间）——『过程累计日雨量』为 Σr_d 的结构表述。
- **待判断点**：式中 w/rmax/rd/T0 定义逐字在 chunk；『过程累计日雨量』是否即 Σr_d 的结构表述，请判断。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 3. G011[f0] — covered_table_cell

- **fact**：灰水氨氮均值约为6.1 mg/L
- **承载（must-hit 绑定）**：证据 ev0 · `DBJ_T+13-477-2024+福建省建筑与小区雨污混接改造技术标准.pdf` · chunk `chunk_8914904bf4f39288652d79f16488ef73` · PDF p56 · 页图 `eval\audits\fact_reconciliation\renders\DBJ_T_13_477_2024_福建省建筑与-p56.png`
- **机器留痕**：表格单元格事实：6.1 逐字在承载 chunk 氨氮行×灰水列×均值列（『氨氮（mg/L） | 3.6~9.6 | 6.1 |…』），行/列表头逐字在场；关联为表格位置性，bigram 度量跨单元格不连接。
- **待判断点**：氨氮行的『灰水均值 6.1』：机器只能核对数值与行/列表头逐字，无法核对『该 6.1 属于氨氮×灰水×均值』的表格位置关联——请对照页图确认表位。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 4. G011[f1] — covered_table_cell

- **fact**：黑水氨氮范围约46.8~109.3 mg/L（表内均值更高）
- **承载（must-hit 绑定）**：证据 ev0 · `DBJ_T+13-477-2024+福建省建筑与小区雨污混接改造技术标准.pdf` · chunk `chunk_8914904bf4f39288652d79f16488ef73` · PDF p56 · 页图 `eval\audits\fact_reconciliation\renders\DBJ_T_13_477_2024_福建省建筑与-p56.png`
- **机器留痕**：表格单元格事实：46.8~109.3 与均值 76.8 逐字在承载 chunk 黑水行（『黑水…46.8~109.3 | 76.8』），表头逐字在场。
- **待判断点**：黑水行『46.8~109.3 / 均值 76.8』：同上，请确认数值属于黑水行的范围列与均值列。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 5. G012[f0] — covered_verbatim_parts

- **fact**：标准号为DB 4201/T 651—2021
- **承载（must-hit 绑定）**：证据 ev0 · `武汉市排水管道混错接改造技术规程.pdf` · chunk `chunk_9092f9e17b6a8b045b3e51fe69ff9d93` · PDF p1 · 页图 `eval\audits\fact_reconciliation\renders\武汉市排水管道混错接改造技术规程-p01.png`
- **机器留痕**：标准号『DB 4201/T 651—2021』逐字在封面承载 chunk（2021-11-12 发布 / 2021-12-13 实施）；残余 bigram（标准号为）为设问填充词。
- **待判断点**：标准号『DB 4201/T 651—2021』逐字在封面 chunk；残余二元组（『标准号为』）是否确系设问填充词，请判断。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 6. G013[f0] — covered_verbatim_parts

- **fact**：规程包含混错接点判定、混错接流量测定等调查评估步骤
- **承载（must-hit 绑定）**：证据 ev0 · `武汉市排水管道混错接改造技术规程.pdf` · chunk `chunk_6b646fcaf9e4e7bbfd05c1b35ca6e25d` · PDF p3 · 页图 `eval\audits\fact_reconciliation\renders\武汉市排水管道混错接改造技术规程-p03.png`
- **机器留痕**：步骤名『混错接点判定』『混错接流量测定』逐字在目次承载 chunk（5.4/5.5 节名）；残余（包含/调查评估步骤）为设问措辞。
- **待判断点**：步骤名『混错接点判定』『混错接流量测定』逐字在**目次** chunk（5.4/5.5 节名）——目次承载能否作为步骤名佐证（抑或应锚定正文节），请判断。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 7. G019[f0] — covered_table_cell

- **fact**：征求意见稿表5-1：灰水氨氮均值约6.1 mg/L，黑水氨氮均值约76.8 mg/L
- **承载（must-hit 绑定）**：证据 ev0 · `城镇分流制排水系统雨污混接调查 征求意见稿.pdf` · chunk `chunk_4e9cafd610a7ac92f0251c0c7fa7d1ab` · PDF p24 · 页图 `eval\audits\fact_reconciliation\renders\城镇分流制排水系统雨污混接调查_征求意见稿-p24.png`
- **承载（must-hit 绑定）**：证据 ev1 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_3d03c7031aef4fecf30651ecb74086d1` · PDF p28 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p28.png`
- **机器留痕**：表格单元格事实：灰水 6.1、黑水 76.8 逐字在承载 chunk 表5-1（『灰水 | 3.6~9.6 | 6.1…黑水 | 46.8~109 | 76.8』），表头（水量来源/范围/均值）逐字在场。
- **待判断点**：表5-1『灰水 6.1 / 黑水 76.8』：《征求意见稿》附录表格位置关联请对照页图确认。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 8. G019[f1] — covered_attribution_binding

- **fact**：T/CECS 758：按接入管管径、混接水量、混接水质评估混接程度
- **承载（must-hit 绑定）**：证据 ev0 · `城镇分流制排水系统雨污混接调查 征求意见稿.pdf` · chunk `chunk_4e9cafd610a7ac92f0251c0c7fa7d1ab` · PDF p24 · 页图 `eval\audits\fact_reconciliation\renders\城镇分流制排水系统雨污混接调查_征求意见稿-p24.png`
- **承载（must-hit 绑定）**：证据 ev1 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_3d03c7031aef4fecf30651ecb74086d1` · PDF p28 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p28.png`
- **辅助核验材料（非 must-hit 绑定，不改变黄金集）**：封面页图 `eval\audits\fact_reconciliation\renders\aux-G019f1-T_CECS_758-p01.png`——来源 `data/raw/标准和规范&参考资料/T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` p1，PDF sha256 `f3b23a4668270f1612eb55f6ea90bc528b57e95ab36496901e6651102d330941`；页文本起始：『T / CECS 7 58-2020 中国工程建设标准化协会标准 城镇排水管道混接调查及治理 技术规程 Technical specification for investigation and elimination of illicit』。封面页：文本层含『T / CECS 7 58-2020』（封面正文起始）——供人工核验『标准号 758 为文档归属』的佐证链。
- **机器留痕**：内容句 t≈0.9（接接入管管径/混接水量/混接水质/评估，混接为 OCR『1昆』mojibake）；标准号 758 为文档归属——逐字在封面 chunk（p1『T / CECS 7 58-2020』），由证据文档绑定佐证，非该 chunk 正文。
- **待判断点**：标准号 758 为文档归属：内容句在正文 chunk（t≈0.9），标准号逐字在**封面** chunk（p1『T / CECS 7 58-2020』）——『由证据文档绑定佐证标准号』是否成立，请判断。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 9. G023[f1] — fixed_by_new_evidence

- **fact**：T/CMSA按10分钟、30分钟、1小时划分六个短时等级，并引用QX/T 489－2019定义
- **承载（must-hit 绑定）**：证据 ev0 · `GBT 28592-2012降水量等级.pdf` · chunk `chunk_2ae2464e89cb1dc2a03c888569363179` · PDF p4 · 页图 `eval\audits\fact_reconciliation\renders\GBT_28592_2012降水量等级-p04.png`
- **承载（must-hit 绑定）**：证据 ev1 · `短时气象服务降雨量等级TCMSA-2019.pdf` · chunk `chunk_53bc9cf1cd413c183e4c40c2350faadc` · PDF p5 · 页图 `eval\audits\fact_reconciliation\renders\短时气象服务降雨量等级TCMSA_2019-p05.png`
- **承载（must-hit 绑定）**：证据 ev2 · `QXT 489－2019 降雨过程等级.pdf` · chunk `chunk_d342b42872a12f04cd192df8990fbcd6` · PDF p10 · 页图 `eval\audits\fact_reconciliation\renders\QXT_489_2019_降雨过程等级-p10.png`
- **承载（must-hit 绑定）**：证据 ev3 · `短时气象服务降雨量等级TCMSA-2019.pdf` · chunk `chunk_34cc8546a2e6b5eb82f8907440d00d5c` · PDF p5 · 页图 `eval\audits\fact_reconciliation\renders\短时气象服务降雨量等级TCMSA_2019-p05.png`
- **机器留痕**：本轮补证据修复：新增术语定义 chunk（p5，『[QX/T 489－2019,定义2.2]』逐字）
- **待判断点**：补证据（术语定义 chunk，p5『[QX/T 489－2019,定义2.2]』逐字）是否真实承载『并引用 QX/T 489-2019 定义』这一事实，请判断。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 10. G035[f0] — covered_verbatim_parts

- **fact**：推理公式表述为Qs=qΨ（F为汇水面积，见式中说明）
- **承载（must-hit 绑定）**：证据 ev0 · `GB50014-2021+室外排水设计标准+预览.pdf` · chunk `chunk_d2ba14bf7c5b77dcaa3ca773530d4f7e` · PDF p21 · 页图 `eval\audits\fact_reconciliation\renders\GB50014_2021_室外排水设计标准_预览-p21.png`
- **承载（must-hit 绑定）**：证据 ev1 · `GB50014-2021+室外排水设计标准+预览.pdf` · chunk `chunk_ebd2c621fe0db61608480e90f388e0e9` · PDF p22 · 页图 `eval\audits\fact_reconciliation\renders\GB50014_2021_室外排水设计标准_预览-p22.png`
- **机器留痕**：『Qs = qΨ』与『汇水面积（hm2）』逐字在承载 chunk 式中结构；残余（表述为/见式中说明）为设问措辞。
- **待判断点**：『Qs = qΨ』与『汇水面积（hm2）』逐字在式中结构；残余（『表述为/见式中说明』）是否设问措辞，请判断。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 11. G046[f0] — fixed_by_new_evidence

- **fact**：容器体积法公式编号为(5.4.21)，Q单位m3/d，每日测6~8次
- **承载（must-hit 绑定）**：证据 ev0 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_50c27da38e2791f3bedd1926717ad896` · PDF p24 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p24.png`
- **承载（must-hit 绑定）**：证据 ev1 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_50c27da38e2791f3bedd1926717ad896` · PDF p24 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p24.png`
- **承载（must-hit 绑定）**：证据 ev2 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_935d5150cdc74f98bf75a9425daf383a` · PDF p23 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p23.png`
- **承载（must-hit 绑定）**：证据 ev3 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_9a6cb297abfc2e870c060dcb3ae43915` · PDF p23 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p23.png`
- **机器留痕**：本轮补证据修复：新增 5.4.21 式中 chunk（p23，Q单位m3/d、每日测量6~8次、间隔3h~4h 逐字）
- **待判断点**：补证据（5.4.21 式中 chunk，p23）是否真实承载『Q单位m3/d、每日测量6~8次、间隔3h~4h』，请判断。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 12. G046[f1] — covered_ocr_variant

- **fact**：浮标法公式编号为(5.4.22)，含过流面积A与修正系数k约0.8~0.9
- **承载（must-hit 绑定）**：证据 ev0 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_50c27da38e2791f3bedd1926717ad896` · PDF p24 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p24.png`
- **承载（must-hit 绑定）**：证据 ev1 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_50c27da38e2791f3bedd1926717ad896` · PDF p24 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p24.png`
- **承载（must-hit 绑定）**：证据 ev2 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_935d5150cdc74f98bf75a9425daf383a` · PDF p23 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p23.png`
- **承载（must-hit 绑定）**：证据 ev3 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_9a6cb297abfc2e870c060dcb3ae43915` · PDF p23 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p23.png`
- **机器留痕**：0.9 在承载 chunk 为扫描 OCR『o. 9 0』（0 误读为 o），数值在场；公式编号(5.4.22)与 0.8 逐字在 chunk。
- **待判断点**：数值 0.9 在承载 chunk 为扫描 OCR『o. 9 0』（0 误读为 o）——请对照页图确认该形态确为 0.9。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 13. G046[f2] — covered_verbatim_parts

- **fact**：两种方法均基于一天内多次测量取用
- **承载（must-hit 绑定）**：证据 ev0 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_50c27da38e2791f3bedd1926717ad896` · PDF p24 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p24.png`
- **承载（must-hit 绑定）**：证据 ev1 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_50c27da38e2791f3bedd1926717ad896` · PDF p24 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p24.png`
- **承载（must-hit 绑定）**：证据 ev2 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_935d5150cdc74f98bf75a9425daf383a` · PDF p23 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p23.png`
- **承载（must-hit 绑定）**：证据 ev3 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_9a6cb297abfc2e870c060dcb3ae43915` · PDF p23 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p23.png`
- **机器留痕**：两种方法的一天内多次测量均有逐字支撑：5.4.21 条文『每日流量应基于一天内多次的测量数据』（ev[2]，『→天』为 OCR 一天之误读）与两式 η『每日测量6次～8次』；残余（两种/均/取用）为设问措辞。
- **待判断点**：『一天内多次测量』：5.4.21 条文有『每日流量应基于一天内多次的测量数据』（『→天』为 OCR 一天之误读）与两式 η『每日测量6次～8次』——两种方法均有的判断请确认。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

## 14. G047[f1] — covered_table_cell

- **fact**：轻度：d<300mm且q<200 m3/d量级
- **承载（must-hit 绑定）**：证据 ev0 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_da84b811985d3d7ab1b60c58054f5457` · PDF p28 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p28.png`
- **承载（must-hit 绑定）**：证据 ev1 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_3cd0216d5b694ebd2f36f027fcf10ad5` · PDF p29 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p29.png`
- **承载（must-hit 绑定）**：证据 ev2 · `T_CECS_758-2020城镇排水管道混接调查及治理技术规程.pdf` · chunk `chunk_3fba97b64ee509a9366e42def4d66f91` · PDF p28 · 页图 `eval\audits\fact_reconciliation\renders\T_CECS_758_2020城镇排水管道混接调-p28.png`
- **机器留痕**：表格单元格事实：轻度混接(1级) 行 d<300 | q<200 逐字在续表 chunk（m³/d 为上标 3，已归一）；『量级』为设问措辞。
- **待判断点**：轻度混接(1级)行『d<300 | q<200』：数值在续表 chunk 逐字在场，但『量级』为设问措辞——请确认轻级行数值即题目所指。

- 负责人决定：☐ 接受 ☐ 驳回（驳回时请在 justification 中
  指明缺陷与处置：改写 fact / 调整 answerability / 重锚）

