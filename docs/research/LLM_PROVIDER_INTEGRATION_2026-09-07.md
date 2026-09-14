# LLM Provider 接入调研：智谱 GLM、LangChain 与 DeepSeek

> 调研日期：2026-09-07  
> 适用项目：排水规范证据助手 M4 回答生成  
> 来源边界：仅使用智谱开放平台、DeepSeek API、LangChain 官方文档与 LangChain 官方源码。未发起真实 API 请求，本文不代表运行验证结果。  
> 结论时点：模型名称和平台能力变化较快，实施时应再次核对官方模型概览和 API 文档。

## 1. 结论

1. 智谱开放平台提供 OpenAI API 兼容接口。通用 `base_url` 为 `https://open.bigmodel.cn/api/paas/v4/`，REST 端点前缀也写作不带末尾斜杠的 `https://open.bigmodel.cn/api/paas/v4`；鉴权使用 `Authorization: Bearer YOUR_API_KEY`。[智谱 OpenAI API 兼容](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)、[智谱 API 快速开始](https://docs.bigmodel.cn/cn/api/introduction)
2. 截至本次调研，智谱“模型概览”明确把 `glm-5.2` 列为最新推荐旗舰文本模型。对本项目而言，可把 `glm-5.2` 作为首轮质量基线；是否改用更轻、更便宜的型号，必须通过本项目问答评估和实际费用数据决定，不能只凭模型宣传判断。[智谱模型概览](https://docs.bigmodel.cn/cn/guide/start/model-overview)、[GLM-5.2 模型说明](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2)
3. 智谱 Chat Completions 明确支持 `response_format={"type":"json_object"}`，保证返回有效 JSON；官方示例中的 JSON Schema 是放入提示词后，再由客户端 `jsonschema` 校验，并非已确认的服务端原生严格 JSON Schema 约束。[智谱对话补全 API](https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8)、[智谱结构化输出](https://docs.bigmodel.cn/cn/guide/capabilities/struct-output)
4. LangChain 官方允许用 `langchain-openai` 的 `ChatOpenAI(base_url=..., api_key=..., model=...)` 连接实现 OpenAI Chat Completions 规范的第三方端点。它只承诺处理 OpenAI 标准字段，第三方扩展字段（例如 `reasoning_content`）可能不会保留。[LangChain OpenAI-compatible endpoints](https://docs.langchain.com/oss/python/concepts/providers-and-models#openai-compatible-endpoints)、[ChatOpenAI 官方文档](https://docs.langchain.com/oss/python/integrations/chat/openai)
5. 智谱当前已确认的是 JSON mode，因此项目首次接入应显式使用 `method="json_mode"`，在提示词中写清 JSON 结构，并用 Pydantic 做客户端校验；不能依赖 `ChatOpenAI.with_structured_output()` 当前默认的 `method="json_schema"`，因为没有官方证据证明智谱 Chat Completions 支持 OpenAI 原生 JSON Schema 协议。[LangChain 模型结构化输出](https://docs.langchain.com/oss/python/langchain/models#structured-output)、[ChatOpenAI.with_structured_output API](https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI/with_structured_output)、[智谱结构化输出](https://docs.bigmodel.cn/cn/guide/capabilities/struct-output)
6. DeepSeek 官方同样提供 OpenAI 兼容接口，当前 `base_url` 为 `https://api.deepseek.com`。截至本次调研，官方当前文本型号为 `deepseek-v4-flash` 和 `deepseek-v4-pro`；二者支持 JSON Output。LangChain 已提供专用的 `langchain-deepseek` / `ChatDeepSeek`，未来需要 DeepSeek 专有字段或能力时，应优先采用专用集成。[DeepSeek First API Call](https://api-docs.deepseek.com/)、[DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)、[LangChain ChatDeepSeek](https://docs.langchain.com/oss/python/integrations/chat/deepseek)

## 2. 智谱 GLM OpenAI 兼容接口

### 2.1 Base URL 与请求路径

| 项目 | 已确认值 | 说明 |
|---|---|---|
| OpenAI SDK `base_url` | `https://open.bigmodel.cn/api/paas/v4/` | 智谱 OpenAI 兼容指南的官方示例值 |
| 通用 REST API 前缀 | `https://open.bigmodel.cn/api/paas/v4` | 智谱 API 快速开始中的通用端点 |
| Chat Completions | `POST /chat/completions` | 完整地址为 `https://open.bigmodel.cn/api/paas/v4/chat/completions` |
| Coding Plan 专属端点 | `https://open.bigmodel.cn/api/coding/paas/v4` | 只适用于 GLM Coding Plan 的指定工具/产品环境，不应当作本项目普通开放平台 API 地址 |

来源：[智谱 OpenAI API 兼容](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)、[智谱 API 快速开始](https://docs.bigmodel.cn/cn/api/introduction)、[GLM Coding Plan 快速开始](https://docs.bigmodel.cn/cn/coding-plan/quick-start)

末尾斜杠是 SDK 配置写法差异，不代表两套服务。项目配置宜固定使用兼容指南原样值 `https://open.bigmodel.cn/api/paas/v4/`，避免自行拼接版本路径。

### 2.2 鉴权

- REST 请求使用标准 Bearer 认证：`Authorization: Bearer YOUR_API_KEY`。
- OpenAI SDK 或 LangChain 中把智谱 API Key 传给 `api_key` 参数，由客户端生成认证头。
- 智谱官方建议使用环境变量 `ZAI_API_KEY`，不要把密钥硬编码或提交到仓库。

来源：[智谱 API 快速开始](https://docs.bigmodel.cn/cn/api/introduction)、[智谱 OpenAI API 兼容](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)

### 2.3 推荐文本模型

截至 2026-09-07，本次可确认的信息如下：

| 模型 | 官方定位/能力 | 对本项目的判断 |
|---|---|---|
| `glm-5.2` | 智谱模型概览列为“最新旗舰模型”；文本输入/输出，1M 上下文，最大输出 128K，支持 Function Calling 和 JSON 等结构化输出 | 作为首轮回答质量基线，先验证证据约束、引用完整性和中文法规回答质量 |
| `glm-4.7` | 高智能文本模型，200K 上下文，最大输出 128K，支持 Function Call 和结构化输出 | 可作为后续成本/时延对照；是否替代 `glm-5.2` 需用项目评估集实测 |

来源：[智谱模型概览](https://docs.bigmodel.cn/cn/guide/start/model-overview)、[GLM-5.2](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2)、[GLM-4.7](https://docs.bigmodel.cn/cn/guide/models/text/glm-4.7)

这里的“首轮质量基线”是本项目的工程建议，不是智谱针对环保 RAG 场景给出的官方选型结论。官方资料没有给出环保法规 RAG 专用推荐，也没有证明最长上下文或最大模型必然有更好的证据忠实度。

### 2.4 JSON 与结构化输出能力

智谱对话补全 API 的 `response_format` 已确认支持：

- `{"type":"text"}`：普通文本；
- `{"type":"json_object"}`：返回有效 JSON，使用时建议在提示词中明确要求 JSON。

智谱“结构化输出”官方示例进一步表明：

1. 具体字段结构通过 system prompt 或 user prompt 描述；
2. 服务端请求仍只传 `response_format={"type":"json_object"}`；
3. 返回后由 Python `json.loads` 解析；
4. 需要字段级约束时，由客户端 `jsonschema.validate` 再验证。

因此，本项目应把能力边界表述为：**智谱已确认支持 JSON mode；客户端可以把 JSON 解析成 Pydantic 模型并校验；尚未确认 Chat Completions 支持 OpenAI 原生 `json_schema` 响应格式或服务端严格 Schema 保证。**

来源：[智谱对话补全 API](https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8)、[智谱结构化输出](https://docs.bigmodel.cn/cn/guide/capabilities/struct-output)

## 3. LangChain Python 接入 OpenAI-compatible Provider

### 3.1 官方基础用法

LangChain 官方给出的通用接入形式是安装 `langchain-openai`，然后为 `ChatOpenAI` 指定第三方 `base_url`、API Key 和模型名称：[LangChain Providers and models](https://docs.langchain.com/oss/python/concepts/providers-and-models#openai-compatible-endpoints)

```python
from langchain_openai import ChatOpenAI

model = ChatOpenAI(
    model="glm-5.2",
    base_url="https://open.bigmodel.cn/api/paas/v4/",
    api_key="<ZAI_API_KEY>",
    temperature=0,
    max_retries=2,
)
```

这是文档中的集成示意，不是本次已执行代码。生产实现应从环境变量读取密钥。

LangChain 还记录了 `base_url` 的解析优先级：显式 `base_url` / `openai_api_base` 参数优先，其次是 `OPENAI_API_BASE`，最后是底层 OpenAI SDK 读取的 `OPENAI_BASE_URL`。多 Provider 项目不宜共享全局 `OPENAI_API_BASE`，更适合在各 Provider 配置对象中显式传入，避免智谱和 DeepSeek 相互串线。[ChatOpenAI 官方文档](https://docs.langchain.com/oss/python/integrations/chat/openai)

### 3.2 结构化输出的三种方法

LangChain 官方将 `with_structured_output` 的方法分为：

| 方法 | 含义 | 智谱当前判断 |
|---|---|---|
| `json_schema` | 使用 Provider 的原生结构化输出能力，按 Schema 约束 | **不应默认使用**；智谱 Chat Completions 原生 JSON Schema 支持未确认 |
| `function_calling` | 通过强制工具/函数调用产生符合参数 Schema 的数据 | 智谱支持 Function Calling，但本次未确认 `ChatOpenAI` 与具体 GLM 型号在该模式下的完整兼容行为 |
| `json_mode` | 只要求有效 JSON，Schema 必须写进提示词 | **当前首选**；与智谱已确认的 `json_object` 能力直接对应 |

来源：[LangChain 模型结构化输出](https://docs.langchain.com/oss/python/langchain/models#structured-output)、[ChatOpenAI.with_structured_output API](https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI/with_structured_output)

关键兼容性风险是：`langchain-openai` 当前 `with_structured_output` 默认方法为 `json_schema`。如果省略 `method`，代码可能向智谱发送尚未确认支持的原生 Schema 请求。因此要显式写出 `method="json_mode"`。

面向本项目 Answer Schema 的建议形式：

```python
structured_model = model.with_structured_output(
    AnswerPayload,
    method="json_mode",
    include_raw=True,
)
```

提示词必须同时写清 JSON 字段、必填项和“每个 claim 只能引用输入 EvidencePack 中已有的 evidence_id”。`include_raw=True` 可同时保留原始消息、解析结果和解析错误，便于审计。Pydantic 负责类型/字段验证，现有业务层仍负责 evidence ID 白名单、`usage_policy` 和引用完整性校验；LangChain 不取代这些领域规则。[ChatOpenAI.with_structured_output API](https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI/with_structured_output)

### 3.3 `ChatOpenAI` 的能力边界

LangChain 明确说明 `ChatOpenAI` 只面向 OpenAI 官方 API 规范。第三方 Provider 增加的 `reasoning_content`、`reasoning`、`reasoning_details` 等非标准字段不会被提取或保留。因此：

- 本项目首期只依赖标准 Chat Completions 消息内容和 JSON 响应，不把 GLM 专有推理字段写入核心接口；
- Provider 专有参数或字段应留在适配器内部；
- 若后续确实需要某 Provider 的专有能力，应使用其专用 LangChain 集成或直接使用官方 SDK，而不是继续假设 `ChatOpenAI` 可以无损透传。

来源：[ChatOpenAI 官方文档](https://docs.langchain.com/oss/python/integrations/chat/openai)、[LangChain 官方源码：ChatOpenAI](https://github.com/langchain-ai/langchain/blob/master/libs/partners/openai/langchain_openai/chat_models/base.py)

## 4. DeepSeek 未来扩展确认

### 4.1 OpenAI 兼容接口

| 项目 | 已确认值 |
|---|---|
| OpenAI 格式 `base_url` | `https://api.deepseek.com` |
| OpenAI SDK | 传入 DeepSeek API Key 和上述 `base_url` 后调用 Chat Completions；官方也已提供 Responses API 兼容说明 |
| 当前文本模型 | `deepseek-v4-flash`、`deepseek-v4-pro` |
| JSON Output | 两个当前文本模型均支持；Chat Completions 使用 `response_format={"type":"json_object"}` |

来源：[DeepSeek First API Call](https://api-docs.deepseek.com/)、[DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)、[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[DeepSeek Responses API](https://api-docs.deepseek.com/guides/responses_api/)

DeepSeek JSON Output 官方要求：请求设置 `json_object`；system 或 user prompt 中包含“json”并给出期望格式示例；合理设置输出 token 上限，避免 JSON 截断。官方还提示 JSON Output 偶尔可能返回空内容，因此未来适配器必须把空响应作为可观测、可重试或受控失败，而不是直接当作有效 Answer。[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)

### 4.2 LangChain 路径

未来接 DeepSeek 有两条官方可依据的路径：

1. 只使用标准兼容字段时，可继续用 `ChatOpenAI(base_url="https://api.deepseek.com", ...)`；
2. 需要 DeepSeek 专有字段、思考模式或更完整能力映射时，使用 `pip install langchain-deepseek` 和 `langchain_deepseek.ChatDeepSeek`。

LangChain 官方明确建议对 DeepSeek 这类扩展 OpenAI 格式的 Provider 使用专用包，以免专有字段丢失；因此本项目未来默认应评估 `ChatDeepSeek`，同时保持上层 `AnswerGenerator` 接口不变。[ChatOpenAI 官方文档](https://docs.langchain.com/oss/python/integrations/chat/openai)、[LangChain ChatDeepSeek](https://docs.langchain.com/oss/python/integrations/chat/deepseek)

注意：LangChain 的 ChatDeepSeek 页面仍以旧名称 `deepseek-chat` / `deepseek-reasoner` 举例，而 DeepSeek 当前官方模型页已经列出 `deepseek-v4-flash` / `deepseek-v4-pro`。实际实施时模型名称应以 DeepSeek 官方当前模型页为准，不能照抄可能滞后的 LangChain 示例。[LangChain ChatDeepSeek](https://docs.langchain.com/oss/python/integrations/chat/deepseek)、[DeepSeek Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)

## 5. 面向本项目的接入建议

这部分是基于上述官方能力边界形成的工程判断，不是 Provider 官方架构要求。

1. 保持现有 `AnswerGenerator` 为领域无关端口；智谱、OpenAI、DeepSeek 分别作为适配器，业务层不直接依赖 Provider SDK 对象。
2. 首期配置智谱 `glm-5.2`，使用 `ChatOpenAI` 的显式 `base_url` 和独立的 `ZAI_API_KEY`。
3. 首期结构化生成显式使用 `method="json_mode"`、Pydantic Schema 和 `include_raw=True`。
4. Prompt 只允许依据传入 EvidencePack 生成，输出 claim 与 evidence ID 的绑定；现有业务层继续做引用白名单校验，模型不能自行决定证据是否合法。
5. 记录 Provider、model、请求 ID、延迟、token usage、解析错误和重试次数，但不记录 API Key、完整敏感请求或模型思考内容。
6. 在相同 Golden Set 上比较至少三类指标：回答正确性、引用/证据忠实度、JSON/Pydantic 解析成功率；再叠加时延与成本决定默认模型。
7. DeepSeek 上线时优先验证 `ChatDeepSeek`，复用同一个 Answer Schema 和验收集，做到“换适配器，不改领域逻辑”。

## 6. 未确认事项

以下事实不能由本次允许范围内的官方资料充分确认，实施或上线前必须补测：

1. **智谱原生严格 JSON Schema：未确认。** 已确认 `json_object` 保证有效 JSON；未发现 Chat Completions 将完整 Schema 交给服务端并保证字段级严格匹配的官方说明。当前方案必须保留客户端 Pydantic/JSON Schema 校验。
2. **LangChain `function_calling` + 智谱的端到端兼容性：未确认。** 智谱模型宣称支持 Function Calling，LangChain也支持该结构化方法，但本次没有真实调用证明字段、强制 tool choice、流式行为和错误处理完全兼容。
3. **`glm-5.2` 是否是本项目最终最佳模型：未确认。** 它是官方推荐旗舰，不等于在环保法规 RAG 的正确性、引用忠实度、时延和成本上最优。
4. **智谱具体 API 价格、免费额度、限流和并发：未纳入本次结论。** 这些信息变化快，且用户本次要求的核心是接口与能力；上线容量与成本评估需单独读取实施时的官方计费和限流页面。
5. **智谱兼容接口的 Responses API 能力：未确认。** 本次只为项目首期确认 Chat Completions，不假设支持 OpenAI Responses API。
6. **第三方推理字段保留：不能保证。** LangChain 明确指出 `ChatOpenAI` 不保留非 OpenAI 标准字段；若项目未来依赖 GLM 或 DeepSeek 的专有 reasoning 字段，需另做适配方案。
7. **官方文档的模型名称一致性：存在变化风险。** 本次智谱模型概览明确推荐 `glm-5.2`，但 API 快速开始页面的示例已经出现 `glm-5.3`。由于本次未找到对应的完整模型说明页，不能据此把 `glm-5.3` 定为已确认可选型号；实施前应再次核对智谱模型概览、模型说明和控制台可用模型列表。[智谱模型概览](https://docs.bigmodel.cn/cn/guide/start/model-overview)、[智谱 API 快速开始](https://docs.bigmodel.cn/cn/api/introduction)
8. **LangChain ChatDeepSeek 文档的旧模型名：存在滞后风险。** 该页面示例仍使用 `deepseek-chat`，但 DeepSeek 当前官方文档列出 V4 型号；实施时须以 DeepSeek 官方 API 的当前模型列表为准。

## 7. 实施前最小验证清单

本文不修改代码。后续开始实现时，应先用最小测试确认：

- 智谱 `glm-5.2` 普通非流式调用成功，认证失败时不泄露 Key；
- `json_object` 返回可被 `json.loads` 解析；
- `with_structured_output(..., method="json_mode", include_raw=True)` 能得到 Pydantic 对象，错误时保留 `parsing_error`；
- Answer 中不存在 EvidencePack 之外的 evidence ID，`source_locator_only` 不会被当作回答证据；
- 空内容、截断 JSON、超时、429 和 5xx 都能形成受控错误；
- 切换到假 DeepSeek 适配器时，上层 `QueryApplicationService` 和领域模型无需变化。

只有这些最小契约通过后，才能把“官方协议兼容”升级为“本项目已验证兼容”。
