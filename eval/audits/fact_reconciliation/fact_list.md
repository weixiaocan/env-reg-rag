# golden_v2 事实清单（第 4 轮重提交，2026-09-30）

87 条内容事实 + 8 条拒答 meta-fact；每条绑定实际 chunk_id / 证据索引 /
原 PDF 页；`human_ruling=pending` 为机器无法证明的语义例外，逐项待人工裁定。

### G001[f0] — covered
- fact：内涝指一定范围内强降雨或连续性降雨超出雨水设施消纳能力，导致地面产生积水的现象
- 绑定：ev0→chunk_54cec865de64022a7d… p8

### G002[f0] — covered
- fact：适用条件：地面汇水距离不大于90m
- 绑定：ev0→chunk_71d2590665a3a147f6… p14; ev1→chunk_c3377c044d40e9bfc4… p14; ev2→chunk_5b0653c2107f2dac56… p14

### G002[f1] — covered
- fact：ta=10.41(n0·L)^0.6/(q^0.4 S^0.3)
- 绑定：ev0→chunk_71d2590665a3a147f6… p14; ev1→chunk_c3377c044d40e9bfc4… p14; ev2→chunk_5b0653c2107f2dac56… p14

### G002[f2] — covered
- fact：n0为粗糙系数，L为地面集水距离，q为设计暴雨强度
- 绑定：ev0→chunk_71d2590665a3a147f6… p14; ev1→chunk_c3377c044d40e9bfc4… p14; ev2→chunk_5b0653c2107f2dac56… p14

### G003[f0] — covered
- fact：表中2020年城市污水处理率为97.5%
- 绑定：ev0→chunk_fc68a40b71eb0148da… p2

### G004[f0] — covered
- fact：2020年城市污水处理率为97.5%
- 绑定：ev0→chunk_fc68a40b71eb0148da… p2; ev1→chunk_a914f8a27177388004… p6

### G004[f1] — covered
- fact：2025年全国城市生活污水集中收集率力争达到70%以上
- 绑定：ev0→chunk_fc68a40b71eb0148da… p2; ev1→chunk_a914f8a27177388004… p6

### G004[f2] — covered
- fact：两指标名称与年份不同：“十三五”指标为城市污水处理率（2020年97.5%），“十四五”目标为全国城市生活污水集中收集率（2025年力争达到70%以上）
- 绑定：ev0→chunk_fc68a40b71eb0148da… p2; ev1→chunk_a914f8a27177388004… p6

### G005[f0] — covered
- fact：划分为特强、强、较强、中等和弱五个等级
- 绑定：ev0→chunk_7579e97cdb41c25025… p5

### G006[f0] — covered_formula_structure **待人工裁定**
- fact：R由最大日雨量项与过程平均日雨量项加权后对测站平均得到
- 绑定：ev0→chunk_8217c10967608ac0a8… p6
- 留痕：公式结构事实：LaTeX 公式逐字在承载 chunk——R=(w·Σ(r_max)_i+(1-w)·Σ(Σr_d/T0)_i)/n，即最大日雨量项（w 加权）与过程平均日雨量项（Σr_d/T0）加权后对 n 个测站平均；改写措辞的 bigram 度量无法核对数学结构。

### G006[f1] — covered_formula_structure **待人工裁定**
- fact：式中含权重w、过程最大日雨量r_max、过程累计日雨量与历时T0
- 绑定：ev0→chunk_8217c10967608ac0a8… p6
- 留痕：式中定义逐字在承载 chunk：权重 w（区域取0.5）、rmax（日最大雨量值）、rd（日雨量值）、T0（降雨过程持续时间）——『过程累计日雨量』为 Σr_d 的结构表述。

### G007[f0] — covered
- fact：包括地块项目评价、市政道路项目评价、排水分区项目评价
- 绑定：ev0→chunk_475fc9e9a4fe998588… p4

### G008[f0] [拒答] — refuse_meta
- fact：应拒答：当前语料未覆盖美国EPA IDDE手册及罚款上限
- 绑定：—
- 留痕：拒答题 meta-fact（应拒答/不应编造/不得套用），非语料内容事实，不入内容证据对账分母。

### G008[f1] [拒答] — refuse_meta
- fact：不应编造美元罚款数额
- 绑定：—
- 留痕：拒答题 meta-fact（应拒答/不应编造/不得套用），非语料内容事实，不入内容证据对账分母。

### G009[f0] — covered
- fact：图名为雨水立管断接示意图（一）
- 绑定：ev0→chunk_0aa5091f5d00c60b90… p63

### G009[f1] — covered
- fact：图注含雨水立管、建筑外墙、室外地面、支墩、卵石缓冲、海绵设施
- 绑定：ev0→chunk_0aa5091f5d00c60b90… p63

### G010[f0] — covered
- fact：包括总则、术语、基本规定、前期调查、设计、施工与验收、运维管理、效果评估等
- 绑定：ev0→chunk_805e77588dd2468122… p3

### G011[f0] — covered_table_cell **待人工裁定**
- fact：灰水氨氮均值约为6.1 mg/L
- 绑定：ev0→chunk_8914904bf4f3928865… p56
- 留痕：表格单元格事实：6.1 逐字在承载 chunk 氨氮行×灰水列×均值列（『氨氮（mg/L） | 3.6~9.6 | 6.1 |…』），行/列表头逐字在场；关联为表格位置性，bigram 度量跨单元格不连接。

### G011[f1] — covered_table_cell **待人工裁定**
- fact：黑水氨氮范围约46.8~109.3 mg/L（表内均值更高）
- 绑定：ev0→chunk_8914904bf4f3928865… p56
- 留痕：表格单元格事实：46.8~109.3 与均值 76.8 逐字在承载 chunk 黑水行（『黑水…46.8~109.3 | 76.8』），表头逐字在场。

### G012[f0] — covered_verbatim_parts **待人工裁定**
- fact：标准号为DB 4201/T 651—2021
- 绑定：ev0→chunk_9092f9e17b6a8b045b… p1
- 留痕：标准号『DB 4201/T 651—2021』逐字在封面承载 chunk（2021-11-12 发布 / 2021-12-13 实施）；残余 bigram（标准号为）为设问填充词。

### G013[f0] — covered_verbatim_parts **待人工裁定**
- fact：规程包含混错接点判定、混错接流量测定等调查评估步骤
- 绑定：ev0→chunk_6b646fcaf9e4e7bbfd… p3
- 留痕：步骤名『混错接点判定』『混错接流量测定』逐字在目次承载 chunk（5.4/5.5 节名）；残余（包含/调查评估步骤）为设问措辞。

### G014[f0] — covered
- fact：到2025年全国城市生活污水集中收集率力争达到70%以上
- 绑定：ev0→chunk_a914f8a27177388004… p6

### G015[f0] [拒答] — refuse_meta
- fact：应拒绝给出2035年的具体百分比：证据只对2035年作定性目标表述
- 绑定：ev0→chunk_a914f8a27177388004… p6
- 留痕：拒答题 meta-fact（应拒答/不应编造/不得套用），非语料内容事实，不入内容证据对账分母。

### G015[f1] [拒答] — refuse_meta
- fact：95%以上和25%以上是2025年目标，不得套用为2035年目标
- 绑定：ev0→chunk_a914f8a27177388004… p6
- 留痕：拒答题 meta-fact（应拒答/不应编造/不得套用），非语料内容事实，不入内容证据对账分母。

### G016[f0] — covered
- fact：超大城市内涝防治设计重现期均为100年
- 绑定：ev0→chunk_c61c2ee442f79c158b… p13; ev1→chunk_55155c55f3a02aa9c3… p20

### G016[f1] — covered
- fact：中等城市和小城市均为20~30年
- 绑定：ev0→chunk_c61c2ee442f79c158b… p13; ev1→chunk_55155c55f3a02aa9c3… p20

### G016[f2] — covered
- fact：均要求道路中一条车道积水深度不超过15cm
- 绑定：ev0→chunk_c61c2ee442f79c158b… p13; ev1→chunk_55155c55f3a02aa9c3… p20

### G017[f0] — covered
- fact：超大/特大城市中心城区重要地区：3~5年
- 绑定：ev0→chunk_51af668fa6fd66966e… p11

### G017[f1] — covered
- fact：超大/特大城市中心城区地下通道和下沉式广场等：30~50年
- 绑定：ev0→chunk_51af668fa6fd66966e… p11

### G018[f0] — covered
- fact：城乡排水工程必须执行本规范
- 绑定：ev0→chunk_d8c76042b3b4ff2abe… p4; ev1→chunk_b3cc1d4a08c52bc1d1… p4; ev2→chunk_afefe14eda631ad22a… p1

### G018[f1] — covered
- fact：属于全文强制性工程建设规范体系中的项目规范
- 绑定：ev0→chunk_d8c76042b3b4ff2abe… p4; ev1→chunk_b3cc1d4a08c52bc1d1… p4; ev2→chunk_afefe14eda631ad22a… p1

### G019[f0] — covered_table_cell **待人工裁定**
- fact：征求意见稿表5-1：灰水氨氮均值约6.1 mg/L，黑水氨氮均值约76.8 mg/L
- 绑定：ev0→chunk_4e9cafd610a7ac92f0… p24; ev1→chunk_3d03c7031aef4fecf3… p28
- 留痕：表格单元格事实：灰水 6.1、黑水 76.8 逐字在承载 chunk 表5-1（『灰水 | 3.6~9.6 | 6.1…黑水 | 46.8~109 | 76.8』），表头（水量来源/范围/均值）逐字在场。

### G019[f1] — covered_attribution_binding **待人工裁定**
- fact：T/CECS 758：按接入管管径、混接水量、混接水质评估混接程度
- 绑定：ev0→chunk_4e9cafd610a7ac92f0… p24; ev1→chunk_3d03c7031aef4fecf3… p28
- 留痕：内容句 t≈0.9（接接入管管径/混接水量/混接水质/评估，混接为 OCR『1昆』mojibake）；标准号 758 为文档归属——逐字在封面 chunk（p1『T / CECS 7 58-2020』），由证据文档绑定佐证，非该 chunk 正文。

### G020[f0] — covered
- fact：图名为雨水管网中污水混接筛查技术路线
- 绑定：ev0→chunk_fc1ff0301ebc28300e… p22

### G020[f1] — covered
- fact：条文解释举例以氨氮为节点水质监测指标
- 绑定：ev0→chunk_fc1ff0301ebc28300e… p22

### G021[f0] — covered
- fact：到2027年城市生活污水集中收集率达到73%以上
- 绑定：ev0→chunk_d193ec2911a7df6b93… p1

### G022[f0] — covered
- fact：单站日降雨量等级以该站日降雨量为划分依据，见表1
- 绑定：ev0→chunk_613eb0f8a5152b3b39… p9; ev1→chunk_cd4ad3447fe818b903… p9

### G022[f1] — covered
- fact：暴雨日降雨量50.0~99.9 mm
- 绑定：ev0→chunk_613eb0f8a5152b3b39… p9; ev1→chunk_cd4ad3447fe818b903… p9

### G022[f2] — covered
- fact：大暴雨日降雨量100.0~249.9 mm
- 绑定：ev0→chunk_613eb0f8a5152b3b39… p9; ev1→chunk_cd4ad3447fe818b903… p9

### G023[f0] — covered
- fact：GB/T 28592按12h与24h划分
- 绑定：ev0→chunk_2ae2464e89cb1dc2a0… p4; ev1→chunk_53bc9cf1cd413c183e… p5; ev2→chunk_d342b42872a12f04cd… p10; ev3→chunk_34cc8546a2e6b5eb82… p5

### G023[f1] — fixed_by_new_evidence **待人工裁定**
- fact：T/CMSA按10分钟、30分钟、1小时划分六个短时等级，并引用QX/T 489－2019定义
- 绑定：ev0→chunk_2ae2464e89cb1dc2a0… p4; ev1→chunk_53bc9cf1cd413c183e… p5; ev2→chunk_d342b42872a12f04cd… p10; ev3→chunk_34cc8546a2e6b5eb82… p5
- 留痕：本轮补证据修复：新增术语定义 chunk（p5，『[QX/T 489－2019,定义2.2]』逐字）

### G023[f2] — covered
- fact：QX/T 489—2019规定区域降雨过程等级为降雨过程中该区域日降雨等级中的最强等级
- 绑定：ev0→chunk_2ae2464e89cb1dc2a0… p4; ev1→chunk_53bc9cf1cd413c183e… p5; ev2→chunk_d342b42872a12f04cd… p10; ev3→chunk_34cc8546a2e6b5eb82… p5

### G024[f0] — covered
- fact：提出建设城市污水管网全覆盖样板区
- 绑定：ev0→chunk_6cc29ee60ccecc1a49… p3

### G025[f0] — covered
- fact：指南引用建城〔2024〕18号通知
- 绑定：ev0→chunk_6cc29ee60ccecc1a49… p3; ev1→chunk_d193ec2911a7df6b93… p1

### G025[f1] — covered
- fact：到2027年城市生活污水集中收集率达到73%以上
- 绑定：ev0→chunk_6cc29ee60ccecc1a49… p3; ev1→chunk_d193ec2911a7df6b93… p1

### G026[f0] — covered
- fact：建设城市污水管网全覆盖样板区
- 绑定：ev0→chunk_6cc29ee60ccecc1a49… p3; ev1→chunk_8e6befa3c05683c622… p90

### G026[f1] — covered
- fact：以污染物集中收集效能和水环境质量提升为核心的绩效评价体系
- 绑定：ev0→chunk_6cc29ee60ccecc1a49… p3; ev1→chunk_8e6befa3c05683c622… p90

### G026[f2] — covered
- fact：对管网运维单位绩效考核、按效付费
- 绑定：ev0→chunk_6cc29ee60ccecc1a49… p3; ev1→chunk_8e6befa3c05683c622… p90

### G027[f0] — covered
- fact：图1图名为性能要求确定流程
- 绑定：ev0→chunk_24a585a64872cecb52… p16

### G027[f1] — covered
- fact：用于说明性能要求的确定流程
- 绑定：ev0→chunk_24a585a64872cecb52… p16

### G028[f0] [拒答] — refuse_meta
- fact：应拒答或说明未覆盖：语料指南未给出该15%强制指标
- 绑定：—
- 留痕：拒答题 meta-fact（应拒答/不应编造/不得套用），非语料内容事实，不入内容证据对账分母。

### G028[f1] [拒答] — refuse_meta
- fact：该问题疑似库外或错误断言
- 绑定：—
- 留痕：拒答题 meta-fact（应拒答/不应编造/不得套用），非语料内容事实，不入内容证据对账分母。

### G029[f0] — covered
- fact：规范城镇污水管网入流入渗的监测与评估
- 绑定：ev0→chunk_43d194e2c4effe6970… p1

### G030[f0] — covered
- fact：BI=ADF×(Cw-C)/(Cw-Cws)
- 绑定：ev0→chunk_2e5f4720307959ba2c… p18; ev1→chunk_a5ad758689de3ea75c… p12; ev2→chunk_7e8d23da399b9e29f8… p18

### G030[f1] — covered
- fact：ADF为旱天日均流量监测值
- 绑定：ev0→chunk_2e5f4720307959ba2c… p18; ev1→chunk_a5ad758689de3ea75c… p12; ev2→chunk_7e8d23da399b9e29f8… p18

### G030[f2] — covered
- fact：工作流程见图3.0.5
- 绑定：ev0→chunk_2e5f4720307959ba2c… p18; ev1→chunk_a5ad758689de3ea75c… p12; ev2→chunk_7e8d23da399b9e29f8… p18

### G031[f0] — covered
- fact：对10分钟、30分钟、1小时降雨量划分短时小雨至短时特大暴雨共6个等级
- 绑定：ev0→chunk_53bc9cf1cd413c183e… p5

### G032[f0] — covered
- fact：1小时8.0～19.9 mm为短时暴雨
- 绑定：ev0→chunk_53bc9cf1cd413c183e… p5; ev1→chunk_0babc8863b58ca8b3b… p6

### G033[f0] — covered
- fact：交通枢纽最大允许退水时间应为0.5h
- 绑定：ev0→chunk_f2b52a6f65e1f5b691… p21

### G034[f0] — covered
- fact：径流系数约为0.85~0.95
- 绑定：ev0→chunk_ebd2c621fe0db61608… p22

### G035[f0] — covered_verbatim_parts **待人工裁定**
- fact：推理公式表述为Qs=qΨ（F为汇水面积，见式中说明）
- 绑定：ev0→chunk_d2ba14bf7c5b77dcaa… p21; ev1→chunk_ebd2c621fe0db61608… p22
- 留痕：『Qs = qΨ』与『汇水面积（hm2）』逐字在承载 chunk 式中结构；残余（表述为/见式中说明）为设问措辞。

### G035[f1] — covered
- fact：各种屋面、混凝土或沥青路面径流系数0.85~0.95
- 绑定：ev0→chunk_d2ba14bf7c5b77dcaa… p21; ev1→chunk_ebd2c621fe0db61608… p22

### G036[f0] — covered
- fact：t=t1+t2
- 绑定：ev0→chunk_09c684848514925317… p22; ev1→chunk_09c684848514925317… p23

### G036[f1] — covered
- fact：t1为地面集水时间，宜采用5min~15min
- 绑定：ev0→chunk_09c684848514925317… p22; ev1→chunk_09c684848514925317… p23

### G036[f2] — covered
- fact：t2为管渠内雨水流行时间
- 绑定：ev0→chunk_09c684848514925317… p22; ev1→chunk_09c684848514925317… p23

### G037[f0] — covered
- fact：Q为雨水设计流量（L/s），q为设计暴雨强度
- 绑定：ev0→chunk_1e8179ff17e12e15b5… p20; ev1→chunk_93d73b4c95b603ebfa… p20; ev2→chunk_f25c3d7e77902ce547… p20; ev3→chunk_ba63bbc6615352674d… p20

### G037[f1] — covered
- fact：适用条件含汇水面积不超过2km²时可采推理公式法
- 绑定：ev0→chunk_1e8179ff17e12e15b5… p20; ev1→chunk_93d73b4c95b603ebfa… p20; ev2→chunk_f25c3d7e77902ce547… p20; ev3→chunk_ba63bbc6615352674d… p20

### G037[f2] — covered
- fact：城市建筑密集区雨水排放系统综合径流系数约0.60~0.70
- 绑定：ev0→chunk_1e8179ff17e12e15b5… p20; ev1→chunk_93d73b4c95b603ebfa… p20; ev2→chunk_f25c3d7e77902ce547… p20; ev3→chunk_ba63bbc6615352674d… p20

### G038[f0] — covered
- fact：表4.2.3为城市分类污水排放系数
- 绑定：ev0→chunk_a3388ec36c0108333a… p15

### G038[f1] — covered
- fact：资料缺乏时按该表取值
- 绑定：ev0→chunk_a3388ec36c0108333a… p15

### G039[f0] — covered
- fact：图1为城镇排水管网数字化通用技术的总体架构逻辑关系图
- 绑定：ev0→chunk_b96e9371ecf1a2df2c… p9

### G040[f0] — covered
- fact：表1为应监测的对象及监测指标要求
- 绑定：ev0→chunk_d3a50df7c4deb6e80c… p10; ev1→chunk_bc01011f07cbb323ab… p10

### G040[f1] — covered
- fact：点位布设原则：系统性、代表性、有效性、可行性、积极性
- 绑定：ev0→chunk_d3a50df7c4deb6e80c… p10; ev1→chunk_bc01011f07cbb323ab… p10

### G041[f0] — covered
- fact：到2023年，县级及以上城市设施能力基本满足生活污水处理需求
- 绑定：ev0→chunk_fca70c5f4bbcb58b4a… p1

### G041[f1] — covered
- fact：提升收集效能、推进雨污混错接改造等
- 绑定：ev0→chunk_fca70c5f4bbcb58b4a… p1

### G042[f0] — covered
- fact：图1为流量试验装置示意图
- 绑定：ev0→chunk_78d26e1d1e315e5ab4… p11

### G042[f1] — covered
- fact：由水流量标准装置和排水管道组成（图中含被测流量仪）
- 绑定：ev0→chunk_78d26e1d1e315e5ab4… p11

### G043[f0] — covered
- fact：24h降雨量50.0～99.9 mm为暴雨
- 绑定：ev0→chunk_578072edb03dac0e7a… p4

### G044[f0] — covered
- fact：按24h、12h两个时段划分
- 绑定：ev0→chunk_578072edb03dac0e7a… p4; ev1→chunk_2ae2464e89cb1dc2a0… p4

### G044[f1] — covered
- fact：特大暴雨24h降雨量≥250.0 mm
- 绑定：ev0→chunk_578072edb03dac0e7a… p4; ev1→chunk_2ae2464e89cb1dc2a0… p4

### G045[f0] — covered
- fact：图3.0.4为混接调查与治理环节示意图
- 绑定：ev0→chunk_5196cd43f3fd91c1e6… p15

### G045[f1] — covered
- fact：环节包括整改方案、改造施工与验收、长效动态管理等
- 绑定：ev0→chunk_5196cd43f3fd91c1e6… p15

### G046[f0] — fixed_by_new_evidence **待人工裁定**
- fact：容器体积法公式编号为(5.4.21)，Q单位m3/d，每日测6~8次
- 绑定：ev0→chunk_50c27da38e2791f3be… p24; ev1→chunk_50c27da38e2791f3be… p24; ev2→chunk_935d5150cdc74f98bf… p23; ev3→chunk_9a6cb297abfc2e870c… p23
- 留痕：本轮补证据修复：新增 5.4.21 式中 chunk（p23，Q单位m3/d、每日测量6~8次、间隔3h~4h 逐字）

### G046[f1] — covered_ocr_variant **待人工裁定**
- fact：浮标法公式编号为(5.4.22)，含过流面积A与修正系数k约0.8~0.9
- 绑定：ev0→chunk_50c27da38e2791f3be… p24; ev1→chunk_50c27da38e2791f3be… p24; ev2→chunk_935d5150cdc74f98bf… p23; ev3→chunk_9a6cb297abfc2e870c… p23
- 留痕：0.9 在承载 chunk 为扫描 OCR『o. 9 0』（0 误读为 o），数值在场；公式编号(5.4.22)与 0.8 逐字在 chunk。

### G046[f2] — covered_verbatim_parts **待人工裁定**
- fact：两种方法均基于一天内多次测量取用
- 绑定：ev0→chunk_50c27da38e2791f3be… p24; ev1→chunk_50c27da38e2791f3be… p24; ev2→chunk_935d5150cdc74f98bf… p23; ev3→chunk_9a6cb297abfc2e870c… p23
- 留痕：两种方法的一天内多次测量均有逐字支撑：5.4.21 条文『每日流量应基于一天内多次的测量数据』（ev[2]，『→天』为 OCR 一天之误读）与两式 η『每日测量6次～8次』；残余（两种/均/取用）为设问措辞。

### G047[f0] — covered
- fact：重度：管径d≥600mm且/或流量q>600 m3/d（并按水质指标）
- 绑定：ev0→chunk_da84b811985d3d7ab1… p28; ev1→chunk_3cd0216d5b694ebd2f… p29; ev2→chunk_3fba97b64ee509a936… p28

### G047[f1] — covered_table_cell **待人工裁定**
- fact：轻度：d<300mm且q<200 m3/d量级
- 绑定：ev0→chunk_da84b811985d3d7ab1… p28; ev1→chunk_3cd0216d5b694ebd2f… p29; ev2→chunk_3fba97b64ee509a936… p28
- 留痕：表格单元格事实：轻度混接(1级) 行 d<300 | q<200 逐字在续表 chunk（m³/d 为上标 3，已归一）；『量级』为设问措辞。

### G047[f2] — covered
- fact：任一指标取高值原则
- 绑定：ev0→chunk_da84b811985d3d7ab1… p28; ev1→chunk_3cd0216d5b694ebd2f… p29; ev2→chunk_3fba97b64ee509a936… p28

### G048[f0] — covered
- fact：施工测量允许偏差见表3.1.8
- 绑定：ev0→chunk_46b3d95876af3cbc20… p15; ev1→chunk_dba9b144b96e7b1550… p23

### G048[f1] — covered
- fact：B=Do+2(b1+b2+b3)
- 绑定：ev0→chunk_46b3d95876af3cbc20… p15; ev1→chunk_dba9b144b96e7b1550… p23

### G048[f2] — covered
- fact：B为沟槽底开挖宽度，Do为管外径
- 绑定：ev0→chunk_46b3d95876af3cbc20… p15; ev1→chunk_dba9b144b96e7b1550… p23

### G049[f0] — covered
- fact：图4.6.3为柔性管道沟槽回填部位与压实度示意图
- 绑定：ev0→chunk_645dd05f041512ee9b… p36; ev1→chunk_0e3b1e7b83a2ecea13… p36

### G049[f1] — covered
- fact：管顶以上相关回填压实度示意见图注≥90%/85±2%等
- 绑定：ev0→chunk_645dd05f041512ee9b… p36; ev1→chunk_0e3b1e7b83a2ecea13… p36

### G050[f0] [拒答] — refuse_meta
- fact：应拒答或明确说明当前法规库未覆盖欧盟城市废水处理指令及其合流制溢流限值
- 绑定：—
- 留痕：拒答题 meta-fact（应拒答/不应编造/不得套用），非语料内容事实，不入内容证据对账分母。

### G050[f1] [拒答] — refuse_meta
- fact：不应臆造数值
- 绑定：—
- 留痕：拒答题 meta-fact（应拒答/不应编造/不得套用），非语料内容事实，不入内容证据对账分母。
