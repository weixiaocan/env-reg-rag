# V2 PDF Pipeline Test Baseline Coverage Matrix

本文件只描述 `pdf_pipeline_cases.jsonl` 中已经存在的测试资产，不补造测试页，也不代表整页或整份 PDF 已通过人工验收。

## Baseline summary

- Cases: 46
- `human_gold`: 29 cases
- `human_review`: 13 cases
- `automated_only`: 4 cases
- `regression`: 8 cases
- Human Gold 的有效范围仅限各 case 的 `human_assertions`。页面具有一条人工锚点，不等于该页所有结构都已人工确认。
- 表格的 29 条 `derived_checks` 是 10 条人工表格锚点的机械拆分，不作为新的人工事实重复计数。
- `automated_expectations` 来自助手视觉检查，只能用于自动回归和复核线索，不能作为 Human Gold。

## Coverage

| 能力 | 是否有测试页 | 是否有 Human Gold | 是否有 Regression | 当前状态 |
| --- | ---: | ---: | ---: | --- |
| native text | 是，14 cases | 是，正文/条款锚点 | 是，CECS758 p67同时覆盖native来源页 | 已经足够 |
| OCR | 是，17 cases | 是，扫描正文、表格和数值锚点 | 是，重庆导则p15 | 已经足够 |
| hybrid OCR | 是，7 cases | 是，6页具有局部人工锚点 | 否 | 有案例但 Gold 不足：缺完整页面输出真值和稳定routing真值 |
| heading | 是，7 cases | 部分；有目录、总则和政策标题相关锚点 | 否 | 有案例但 Gold 不足：没有完整标题层级树真值 |
| clause numbering | 是，4个显式cases，更多锚点含structure path | 是，局部条文编号与结构路径 | 否 | 已经足够：限条文编号与局部层级，不代表完整目录树 |
| normal text | 是，3个显式cases | 是 | 否 | 已经足够 |
| dense text | 是，4 cases | 部分；只有局部句子锚点 | 否 | 有案例但 Gold 不足 |
| table | 是，17 cases | 是，10组人工表格锚点、29条派生checks及14个cell | 是，CECS758 p67和重庆导则p15 | 已经足够：限当前人工断言范围 |
| merged cell | 是，2 cases | 是，重庆导则p15完整表格断言 | 是，重庆导则p15 | 有案例但 Gold 不足：只有一个主要gold页面 |
| rotated table | 是，CECS758 p35 | 否 | 否 | 有案例但 Gold 不足 |
| cross-page table | 是，CECS758 p67、重庆导则p16 | 否；现有gold只验证续表局部cell，不验证跨页拼接关系 | 是，CECS758 p67的局部表格恢复 | 有案例但 Gold 不足 |
| formula | 是，7 cases | 是，3条公式gold分布于CECS758 p24、p68 | 是，6 cases | 已经足够：仅作为开发基线，不代表全库公式准确率 |
| figure | 是，6 cases | 否；相关页面的人工gold针对正文或公式，不是图片区域真值 | 是，CECS758 p68包含图3关系 | 有案例但 Gold 不足 |
| figure caption | 是，5 cases | 部分；仅有图3关系和页面选择线索 | 是，CECS758 p68 | 有案例但 Gold 不足 |
| reading order | 是，4 cases | 否，没有完整element顺序oracle | 否 | 有案例但 Gold 不足 |
| header/footer | 是，1个显式case及若干水印/网页元数据风险页 | 部分；仅确认元数据不得并入正文 | 否 | 有案例但 Gold 不足 |
| watermark | 是，7 cases | 是，多个正文锚点明确要求水印不得混入正文 | 否 | 已经足够 |
| blank page | 是，2 cases | 是，两页人工确认应跳过且不得产生OCR内容 | 否 | 已经足够 |
| multi-column | 否 | 否 | 否 | 完全缺失 |

## 已经足够

- native text
- OCR 基础正文与表格场景
- clause numbering 的局部锚点
- normal text
- table 的当前关键字段范围
- formula 的固定开发样例
- watermark
- blank page

“已经足够”仅表示可作为 V2 第一版长期基线，并不表示生产级覆盖或整页人工批准。

## 有案例但 Gold 不足

- hybrid OCR
- heading
- dense text
- merged cell
- rotated table
- cross-page table
- figure
- figure caption
- reading order
- header/footer

这些能力已有代表页或问题记录，但缺少完整、能力专属的人工正确答案。旧自动系统的 `needs_manual_review`、pass/fail、parser选择和运行产物不能填补该缺口。

## 完全缺失

- multi-column

本轮不补充新页面。

## Regression inventory

| Regression | Truth level | 主要保护内容 |
| --- | --- | --- |
| CECS758 p67续表 | human_gold | 14个关键cell、数字、单位和行列关系，历史0/14到14/14 |
| 重庆导则p15复杂表 | human_gold | 合并层级、目标表、完整行名、分值、总分及“竣工” |
| CECS758 p24浮标法公式 | human_gold | 分式、求和上下限、下标、六个变量及局部条件 |
| CECS758 p68圆形断面公式 | human_gold | l/R/d/h与图3关系，并保留A定义和正负号条件未解决状态 |
| GB50014 p61污泥负荷公式 | automated_only | 公式边界和同页参数集合 |
| GB50014 p61–62污泥龄公式 | automated_only | 跨页参数关联 |
| GB50014 p63–64缺氧区公式 | automated_only | 跨页参数和条件关联 |
| GB50014 p63–64排出微生物量公式 | automated_only | 跨页参数关联 |

## Excluded implementation identities

V2 case 身份只使用 PDF SHA-256、物理页和语义后缀。以下信息没有进入 case 身份或判定依据：

- M2 page probe
- old route plan / route ledger
- M3 evidence ID
- old canonical ID
- Docling/Paddle runtime artifact path
- 旧模型运行hash
- 旧自动review的pass/fail

旧 `M2-Pxxx` 只出现在 `source_review_refs` 或说明中，用于定位原始人工记录，不承担 V2 case 身份。
