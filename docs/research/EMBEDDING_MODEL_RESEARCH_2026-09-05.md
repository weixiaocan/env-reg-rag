# 首期中文 Dense Embedding 候选研究

> 调研日期：2026-09-05  
> 适用项目：排水规范证据助手  
> 来源边界：仅使用官方 Hugging Face 模型卡、模型团队官方仓库和论文。公开榜单成绩不作为本项目选型结论。

## 1. 结论

首期进入同口径实测的候选限定为 3 个：

1. `BAAI/bge-small-zh-v1.5`：中文专用、24M 参数、512 维，作为最低资源基线。
2. `intfloat/multilingual-e5-small`：约 0.1B 参数、384 维，作为轻量多语言基线。
3. `Alibaba-NLP/gte-multilingual-base`：305M 参数、768 维、最长 8192 tokens，作为中等规模与长文本能力上探。

本文**不提前宣布赢家**。当前只有 58 个 Retrieval Chunk 和 12 题 Golden Set v1，公开通用榜单不能替代环保法规、标准号、条款、数值表格和地域政策问题上的项目实测。最终选择必须来自相同语料、查询、过滤、距离度量和指标口径下的 Qdrant `exact=true` dense 检索结果。

`Qwen/Qwen3-Embedding-0.6B` 已完成基本核对，但首轮暂缓：它有 0.6B 参数、32K 上下文和 instruction-aware 能力，而当前 chunk 上限约 600 字，长上下文价值尚未形成实际需求；在前三个候选不能达到召回门槛时再进入第二轮。这是基于当前项目规模和本机资源的范围控制，不代表模型效果较差。[Qwen3-Embedding-0.6B 模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)

## 2. 项目约束与选型原则

- 语料是中文环保法律法规、技术标准、政策和 PDF 解析文本，包含标准号、条款号、数值单位、表格文字与地区名称。
- 当前只有 58 个 chunk，首轮没有必要用 ANN 近似搜索掩盖模型差异；Qdrant 统一使用 cosine 与 `exact=true`。
- Golden Set v1 只有 12 题，指标波动会很大，必须同时保存逐题排名和失败案例，不能只报一个平均分。
- Windows 单机、GTX 1660 SUPER 6GB；所有候选必须允许小 batch 推理并能退回 CPU。本文只判断“值得进入本机试跑”，不虚构显存、速度或延迟数据。
- 项目开源，因此首期只保留许可证明确允许开源项目使用、且权重可本地获取的候选。
- 本轮只比较 dense embedding。Qdrant 的中文词法/BM25、RRF 和后续 reranker 单独评估，不能把其他能力混入 dense 模型成绩。

## 3. 候选对比

| 候选 | 许可证 | 参数量 | Dense 维度 | 最大长度 | 中文/多语言 | Query 与 Document 输入约定 | 依赖与本机判断 |
|---|---|---:|---:|---:|---|---|---|
| `BAAI/bge-small-zh-v1.5` | MIT；模型团队说明发布模型可免费商用 | 24M | 512 | 512 tokens | 中文专用 | 短 query 检索长 passage 时，query 推荐添加 `为这个句子生成表示以用于检索相关文章：`；document 不加。v1.5 也允许无指令，应在项目内固定一种配置 | 官方支持 FlagEmbedding、Sentence Transformers 或 Transformers；权重约 95.8 MB。三者中最轻，适合作为 CPU 回退和低资源基线 |
| `intfloat/multilingual-e5-small` | MIT | 约 0.1B | 384 | 512 tokens | 模型卡列出中文，并说明支持 XLM-R 的 100 种语言 | 非对称检索必须使用英文文本前缀：query 为 `query: `，document 为 `passage: `；中文正文也不能省略 | 官方示例支持 Transformers 和 Sentence Transformers；Safetensors 权重约 471 MB。可作为轻量多语言对照，但必须测试前缀是否被正确应用 |
| `Alibaba-NLP/gte-multilingual-base` | Apache-2.0 | 305M | 768，官方还支持截取 128～768 维 | 8192 tokens | 模型卡标注 75 种语言，并给出中文样例 | 官方 dense 示例对 query 与 document 使用同一编码方式，未要求专用前缀；首轮不得自行发明 instruction | `transformers>=4.36.0` 或 `sentence-transformers>=3.0.0`，加载需 `trust_remote_code=True`；权重约 611 MB。模型卡给出 CPU 和 GPU 服务示例，但 6GB 显卡上的 batch、长度和峰值显存仍须本机记录 |

来源：

- BGE 的许可证、24M 参数、中文定位、512 维、query 指令和文档不加指令来自[官方模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)；512 位置长度来自[官方配置](https://huggingface.co/BAAI/bge-small-zh-v1.5/blob/main/config.json)；依赖方式来自[FlagEmbedding 官方推理说明](https://github.com/FlagOpen/FlagEmbedding/blob/master/examples/inference/embedder/README.md)。
- E5 的许可证、语言、384 维、512 截断、前缀规则和依赖来自[官方模型卡](https://huggingface.co/intfloat/multilingual-e5-small)；模型方法见[Multilingual E5 技术报告](https://arxiv.org/abs/2402.05672)。
- GTE 的许可证、305M 参数、768 维、8192 tokens、语言范围、依赖及 CPU/GPU 示例来自[官方模型卡](https://huggingface.co/Alibaba-NLP/gte-multilingual-base)；模型方法见[mGTE 论文](https://arxiv.org/abs/2407.19669)。

## 4. 分候选判断

### 4.1 BAAI/bge-small-zh-v1.5

进入短名单的原因：

- 明确面向中文，模型小，适合验证“当前小语料是否根本不需要更大的模型”。
- 依赖选择多，Windows 本地集成路径简单；即使 GPU 不可用也可以用 CPU 完成 58 个 chunk 的试跑。
- 512 tokens 对当前约 600 字的 chunk 可能发生截断，不能只看规格推断安全；实测必须记录每个 chunk 的 token 数和截断数量。

风险与固定项：

- 模型卡说明 v1.5 无指令也能用，但短 query 到长 passage 推荐加中文指令。首轮采用官方推荐的 query 指令、document 无指令；可额外做一次无指令消融，但不能把两种结果择优拼接。
- 模型卡提醒相似度绝对值不能直接解释为相关概率，因此只按排序评估，不在首轮设拍脑袋的 score threshold。

### 4.2 intfloat/multilingual-e5-small

进入短名单的原因：

- 体量仍较小，同时覆盖中文和多语言，可验证中文专用模型是否真的在本项目占优。
- 384 维是三者中最小，可同时观察 Qdrant 存储尺寸，但不能把维度更小直接等同于召回更差。
- 官方明确规定 query/document 前缀，输入契约清晰，适合做可复现测试。

风险与固定项：

- 所有中文 query 必须保留 `query: `，所有 chunk 必须保留 `passage: `；漏加前缀会形成无效比较。
- 512-token 上限同样要做截断审计。
- 多语言能力不等于环保法规领域效果，必须看标准号、条款和数值类逐题排名。

### 4.3 Alibaba-NLP/gte-multilingual-base

进入短名单的原因：

- 305M 参数构成与两个轻量候选不同的资源档位，且 8192-token 上限可检验未来较长条款或表格文本是否受益。
- 官方同时提供 768 维和弹性维度能力，但首轮使用原生 768 维，避免把模型选择与降维实验混在一起。
- 官方给出中文输入示例，以及 Transformers、Sentence Transformers、CPU 服务和 GPU 服务路径。

风险与固定项：

- `trust_remote_code=True` 会执行模型仓库的自定义代码。开源项目必须固定模型 revision，并在引入前审查对应代码；这是供应链和复现成本，需计入选型。
- 当前 chunk 很短，8192-token 优势可能完全用不上；不能因为规格更大就认定更适合。
- 资源消耗只能通过本机实测获得，首轮从 batch size 1 和实际 chunk 长度开始，失败时自动转 CPU，并如实记录。

## 5. 同口径实测方案

### 5.1 固定条件

- 固定同一个 corpus manifest、58 个 chunk、12 题 Golden Set v1 和相同 metadata filter。
- 每个模型使用独立 Qdrant collection，vector size 按模型原生维度设置，距离统一为 cosine，查询统一 `exact=true`。
- document 内容完全相同；只允许添加各模型官方要求的前缀或 query instruction。
- embedding 全部 L2 normalize，并记录模型 ID、revision、依赖版本、device、dtype、batch size、最大输入长度及截断统计。
- 不启用 ANN、BM25、RRF、reranker、查询改写或生成模型，确保测到的是 dense embedding 差异。

### 5.2 评价口径

- 对有目标证据的问题计算 `Recall@1`、`Recall@3`、`Recall@5` 和 MRR，并保存完整逐题排名。
- `REAL-002` 是明确的语料缺口，不纳入有相关证据问题的召回分母；单独检查地域过滤后是否错误返回山西答案。
- `REAL-001` 虽然最终状态是“需要补充统计时段”，仍应召回对应降雨等级证据。
- `AI-010` 的目标是 `source_locator_only`，检索到公式所在原文定位单元即计为召回，但不能把其 OCR 文本当作公式答案。
- 按数值表格、条款、政策段落、地域问题和原文定位分组查看失败，不用总平均掩盖关键类型。
- 记录模型下载/加载是否成功、索引耗时、逐查询耗时、峰值 RAM/VRAM 和 CPU 回退结果；在真实运行前不填写数值，也不外推到更大语料。

### 5.3 决策规则

首轮不按公开榜单选模型。项目候选应同时满足：

1. Golden Set 的关键问题没有不可接受的漏召回，尤其是数值表格、地区政策和原文定位。
2. Windows 环境可重复安装，GPU 与 CPU 至少各有一条可运行路径。
3. 输入前缀、截断、归一化和模型 revision 能被配置与日志完整复现。
4. 在召回接近时，优先选择依赖更少、资源更低、启动更稳定的模型；更大的参数量或上下文不是单独胜出理由。

12 题只能帮助首期做工程选择，不能支撑“模型普遍更好”的结论。若三个候选表现接近，应优先扩充真实问题和困难负例，而不是继续增加模型数量。

## 6. 暂缓与排除理由

| 模型/类型 | 首轮处理 | 理由 |
|---|---|---|
| `Qwen/Qwen3-Embedding-0.6B` | 暂缓到第二轮 | Apache-2.0、0.6B、1024 维、32K 上下文且支持自定义 instruction，是真实候选；但当前短 chunk 无法体现 32K 价值，资源档位也高于前三者。只有前三者召回不足时再引入，避免扩大首轮矩阵 |
| `BAAI/bge-m3` | 暂缓 | MIT、569M、1024 维、8192 tokens，并同时支持 dense/sparse/multi-vector；本轮只选 dense，而 Qdrant 的 BM25/RRF 要独立评估，引入其多功能路径会混淆实验边界。[官方说明](https://github.com/FlagOpen/FlagEmbedding/blob/master/docs/source/bge/bge_m3.rst) |
| 4B/7B/8B 级本地 embedding | 排除首期 | 与 6GB 显存、CPU 回退和 58-chunk POC 的资源约束不匹配；当前没有证据证明额外部署成本能改善本项目关键问题 |
| 仅云 API、不可本地获取权重的模型 | 排除首期 | 无法满足开源项目的离线复现和 CPU 回退要求，也会引入数据外发与费用变量 |

## 7. 实测前必须锁定的配置

| 候选 | Query 模板 | Document 模板 | 维度 | 最大长度起始值 |
|---|---|---|---:|---:|
| `BAAI/bge-small-zh-v1.5` | `为这个句子生成表示以用于检索相关文章：{query}` | `{chunk}` | 512 | 512 |
| `intfloat/multilingual-e5-small` | `query: {query}` | `passage: {chunk}` | 384 | 512 |
| `Alibaba-NLP/gte-multilingual-base` | `{query}` | `{chunk}` | 768 | 8192；同时记录实际 token 长度 |

以上模板一旦开始首轮评估就不得静默修改。任何 instruction、维度或截断策略变更都应生成新的实验配置和结果文件。

## 8. 一手来源链接

- [BAAI/bge-small-zh-v1.5 官方 Hugging Face 模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)
- [BAAI/bge-small-zh-v1.5 官方 config.json](https://huggingface.co/BAAI/bge-small-zh-v1.5/blob/main/config.json)
- [FlagEmbedding 官方推理说明](https://github.com/FlagOpen/FlagEmbedding/blob/master/examples/inference/embedder/README.md)
- [C-Pack / BGE 论文](https://arxiv.org/abs/2309.07597)
- [intfloat/multilingual-e5-small 官方 Hugging Face 模型卡](https://huggingface.co/intfloat/multilingual-e5-small)
- [Multilingual E5 技术报告](https://arxiv.org/abs/2402.05672)
- [Alibaba-NLP/gte-multilingual-base 官方 Hugging Face 模型卡](https://huggingface.co/Alibaba-NLP/gte-multilingual-base)
- [mGTE 论文](https://arxiv.org/abs/2407.19669)
- [Qwen/Qwen3-Embedding-0.6B 官方 Hugging Face 模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [Qwen3-Embedding 官方代码仓库](https://github.com/QwenLM/Qwen3-Embedding)
- [BGE-M3 官方说明](https://github.com/FlagOpen/FlagEmbedding/blob/master/docs/source/bge/bge_m3.rst)

