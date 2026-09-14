# 成熟 RAG 与企业知识问答产品界面研究

> 调研日期：2026-09-03  
> 研究目标：为“排水规范证据助手”的首期人工入口提出有一手证据支持的 UX 建议。  
> 来源范围：仅使用官方产品帮助文档、官方开发文档和官方开源项目文档；未采用博客榜单、媒体评测或其他二手资料。

## 1. 结论摘要

本次收敛研究 6 个代表产品：Perplexity、Gemini Notebook（原 NotebookLM）、Microsoft 365 Copilot/SharePoint agents、Glean、Elastic Playground、Onyx。

从官方资料能够确认的共同模式是：成熟产品通常把自然语言问答作为主要入口，同时提供答案内联引用、完整来源列表、连续追问以及某种形式的来源范围控制。面向企业知识的产品进一步提供权限继承、元数据过滤、原文预览或定位、反馈入口；面向开发者的产品则增加查询查看、配置和调试能力。

**针对本项目的核心推断：**“排水规范证据助手”不应只模仿普通聊天机器人。规范、标准和政策问答具有较高的误用风险，首期应采用“对话区 + 证据区”的桌面优先布局：答案中的每一项关键结论都能点击引用，在同一页面看到对应原文、页码、条款位置和文档效力元数据；证据不足时明确追问或拒答，而不是输出一段看似确定的答案。

## 2. 研究方法和事实边界

- **事实**：官方资料明确展示或说明的现有产品行为。
- **推断/建议**：基于多个产品模式，结合环保规范问答的风险与本项目目标作出的设计判断。
- 官方帮助文档会随产品更新而变化。本报告反映截至 2026-09-03 可公开核验的状态。
- Google 已于 2026 年 7 月把 NotebookLM 更名为 Gemini Notebook；本报告保留“原 NotebookLM”帮助识别用户熟悉的产品名称。这一更名由 [Google 官方公告](https://blog.google/innovation-and-ai/products/gemini-notebook/notebooklm-gemini-notebook/)确认。

## 3. 代表产品观察

### 3.1 Perplexity

**官方事实**

- Perplexity 的回答带有指向原始来源的引用，并支持在同一会话中连续追问；会话保留问题、回答和使用过的来源。[How does Perplexity work?](https://www.perplexity.ai/help-center/en/articles/10352895-how-does-perplexity-work)、[What is a Session?](https://www.perplexity.ai/help-center/en/articles/10354769-what-is-a-thread)
- 回答级操作包括查看来源、点赞/点踩反馈、复制、改写和导出；来源按钮还展示回答所使用的模型、搜索模式和来源模式。[What is a Session?](https://www.perplexity.ai/help-center/en/articles/10354769-what-is-a-thread)
- 部分来源会显示 Government、Academic 或 Trusted 标签，但官方明确说明：标签评价的是域名整体，不代表单个页面准确，也不能代替用户阅读原文。[Understanding source labels](https://www.perplexity.ai/help-center/en/articles/20260806-understanding-source-labels)

**对本项目的推断**

- 内联编号引用适合保持答案可读性，但本项目不能只链接到文档首页；至少应定位到页码和证据片段。
- 可以借鉴来源类型标识，但应展示可核验的业务元数据，如“发布机关、文号、地域、发布日期、生效状态”，不宜用一个模糊的“可信”徽章替代判断。

### 3.2 Gemini Notebook（原 NotebookLM）

**官方事实**

- Gemini Notebook 的回答以用户选择的 notebook sources 为依据，并提供清晰的内联引用；当信息不在来源中、问题表述不清或触发安全限制时，可能无法回答。[Learn about Gemini Notebook](https://support.google.com/gemininotebook/answer/16164461?hl=en)
- 内联引用可以直接跳到来源中的支持段落，用户能够在原始上下文中核对内容。[NotebookLM goes global with better ways to fact-check](https://blog.google/innovation-and-ai/products/notebooklm-goes-global-support-for-websites-slides-fact-check/)
- 用户可以在来源面板中选择本轮对话要使用的特定来源；来源查看器位于界面左侧，并可查看单个来源的概览。[Add or discover new sources](https://support.google.com/notebooklm/answer/16215270?hl=en)
- 官方对移动端多处注明部分来源管理功能存在限制，说明完整的资料管理与核验体验仍以桌面端更成熟。[Add or discover new sources](https://support.google.com/notebooklm/answer/16215270?hl=en)

**对本项目的推断**

- “引用点击后在原上下文中核验”是最值得采用的模式，比单纯列出参考文献更适合规范依据场景。
- 对于“20 毫米降雨属于什么等级”这类缺少时段条件的问题，应模仿其“不在来源/问题不清”的处理方向：先指出缺少条件并追问，而不是猜测。
- 首期应允许用户限定来源范围，但不必照搬 notebook 的人工勾选流程；地域、文件类型和效力状态过滤更符合本领域。

### 3.3 Microsoft 365 Copilot 与 SharePoint agents

**官方事实**

- Microsoft 365 Copilot Chat 在答案相应语句旁显示内联引用。悬停引用可预览，点击后可在侧边面板打开来源；回答底部的 Sources 按钮可打开完整来源列表，并进一步在原应用中打开文件。[Control and review sources of Copilot Chat responses](https://support.microsoft.com/en-us/Microsoft-365-Copilot/control-review-sources-copilot-chat)
- 用户可以在撰写问题时控制 Copilot 可以引用的数据源；SharePoint agents 的回答受用户对站点和文档库的既有权限约束。[Control and review sources of Copilot Chat responses](https://support.microsoft.com/en-us/Microsoft-365-Copilot/control-review-sources-copilot-chat)、[Get started with agents in SharePoint](https://support.microsoft.com/en-us/sharepoint/copilot-in-sharepoint/get-started-with-agents-in-sharepoint)
- SharePoint agents 支持指定站点、页面和文件作为知识来源。Microsoft 官方示例包含让 agent 比较两个产品的场景，说明多来源综合与比较可通过对话完成。[Get started with agents in SharePoint](https://support.microsoft.com/en-us/sharepoint/copilot-in-sharepoint/get-started-with-agents-in-sharepoint)
- Copilot Studio 的页级引用并非对所有路径和格式都可用：官方当前说明特定 SharePoint 上传路径中的 PDF 可提供页级引用，其他格式或路径可能退化为文档级引用。[Copilot Studio quotas and limits](https://learn.microsoft.com/en-us/microsoft-copilot-studio/requirements-quotas)
- Microsoft 针对 MCP/API 扩展要求返回可打开的 URL，才能把引用渲染成用户可点击、可核验的来源；标题、副标题和 URL 构成基础引用信息。[Show citations with response semantics](https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/plugin-citations)

**对本项目的推断**

- “答案内联引用 → 同页侧栏预览 → 必要时打开完整 PDF”的三级核验路径最适合本项目。
- 页级定位能力必须由我们的数据模型和解析链路保证，不能假设使用任意框架后自然获得。
- 未来发布 MCP 服务时，应把可引用结果设计成稳定的结构化对象，至少返回标题、证据片段、页码/条款、规范元数据和可打开 URL。

### 3.4 Glean

**官方事实**

- Glean 的引用紧跟需要支撑的陈述；同时提供 View sources。悬停引用可预览上下文，打开来源后可先在预览中确认，再跳转到原应用。[Glean Citations](https://docs.glean.com/user-guide/assistant/glean-chat/glean-chat-citations/glean-citations)
- 启用深链接时，引用可指向精确段落并高亮被引用文本及其上下文；适用来源还会显示页码。具体能力会因连接器和管理员设置而不同。[Glean Citations](https://docs.glean.com/user-guide/assistant/glean-chat/glean-chat-citations/glean-citations)
- Glean Search 支持按来源应用、文档类型、人员和更新时间等字段过滤；过滤既可以输入到查询框，也可以在结果页点击选择。[How to search in Glean](https://docs.glean.com/user-guide/basics/how-to-search-in-glean)、[Advanced search filters](https://docs.glean.com/user-guide/advanced/advanced-search-filter)
- 回答下方有点赞/点踩；错误引用可被专门标记。官方说明反馈主要用于监控、复核和排障，不会因为一次点踩就自动改进答案。[Receive User Feedback](https://docs.glean.com/administration/assistant/configuration/user-feedback)、[Glean Citations](https://docs.glean.com/user-guide/assistant/glean-chat/glean-chat-citations/glean-citations)
- Glean 明确说明某些回答没有引用，是因为未调用检索工具或关闭了来源开关；Thinking 模式通常比 Fast 模式检索更多并产生更完整的引用。[Glean Citations](https://docs.glean.com/user-guide/assistant/glean-chat/glean-chat-citations/glean-citations)

**对本项目的推断**

- 本项目不应允许“未检索但凭模型常识回答”悄悄发生。规范问答模式中，未取得可引用证据就应显示“未找到依据”或切换到纯搜索结果，而不是返回无引用答案。
- 反馈不能只有“有用/无用”，应细分为回答错误、引用不支持结论、文档已失效、漏检文档和问题本身缺条件，以便真正定位 RAG 链路故障。

### 3.5 Elastic Playground

**官方事实**

- Elastic Playground 的主要界面分为 Chat 和 Query 两种模式：Chat 用于问答，Query 用于查看和修改界面生成的 Elasticsearch 查询。[Playground for RAG](https://www.elastic.co/docs/solutions/elasticsearch-solution-project/playground)
- 用户可以选择一个或多个索引作为数据源，配置模型、系统指令以及是否在回答中包含引用；它也保存对话上下文。[Playground for RAG](https://www.elastic.co/docs/solutions/elasticsearch-solution-project/playground)
- Playground 可以查看和下载支撑界面的 Python 代码，官方提供 Elasticsearch Python Client 与 LangChain 两种实现选项。[Playground for RAG](https://www.elastic.co/docs/solutions/elasticsearch-solution-project/playground)

**对本项目的推断**

- Query 模式非常适合作为开发/评估入口，但不应暴露给首期普通用户。我们可以设置一个受控的“检索详情”抽屉，展示检索词、过滤条件、命中的候选片段和排序分数。
- 人工问答入口与开发调试入口应复用同一后端证据对象，以避免演示页面和评估系统得出不同结果。

### 3.6 Onyx

**官方事实**

- Onyx 同时提供 Search UI 和 Chat UI；当用户主要想找文档时，Search 更适合展示文档结果，Chat 则用于综合回答。Search 支持时间、作者、标签及来源类型过滤。[RAG and Search](https://docs.onyx.app/overview/core_features/internal_search)
- Chat 的右侧栏展示回答使用的内部和外部来源；左侧栏包含新会话、项目、agents 和历史会话。回答支持点赞/点踩、重新生成和复制。[Chat UI](https://docs.onyx.app/overview/core_features/chat)
- Onyx 文件连接器支持链接、负责人、更新时间、显示名和任意标签等元数据；标签可以用于限制搜索或对话范围。[File connector](https://docs.onyx.app/admins/connectors/official/file)
- Onyx 的网站组件支持桌面弹窗和移动端全屏两种响应式形态，也可以嵌入页面；引用徽章可配置为链接到来源文档。[Website Widget](https://docs.onyx.app/overview/onyx_anywhere/website_widget)
- Onyx 的 MCP 搜索工具返回文档名、相关片段、来源类型、原文链接、相关性得分和实际执行的查询，并可按来源类型和时间过滤。[Onyx MCP Server](https://docs.onyx.app/deployment/configuration/mcp_server)

**对本项目的推断**

- Search/Chat 双模式适合保留：当系统无法形成可靠综合答案时，仍可让用户查看匹配文档与原文片段，而不是进入死胡同。
- Onyx 的右侧来源栏比小型聊天弹窗更适合本项目。首期需要长期阅读和核验 PDF，应该采用独立页面，而不是 400×600 的浮动客服组件。

## 4. 关键界面模式对比

| 界面能力 | 可核验的成熟模式 | 对高风险规范问答的判断 |
|---|---|---|
| 答案内联引用 | Perplexity、Gemini Notebook、Copilot、Glean 均有官方说明 | **必须采用**。引用应紧跟具体结论，而不是只在文末列参考资料。 |
| 完整来源列表 | Perplexity 会话来源、Copilot Sources、Glean View sources、Onyx 右侧来源栏 | **必须采用**。区分“实际支撑回答的来源”和“检索到但未使用的候选”。 |
| 原文预览与定位 | Gemini Notebook 跳到支持段落；Glean 支持预览、高亮、页码和深链接；Copilot 支持侧栏预览 | **必须采用**。点击引用后在同页定位原文、页码和条款，不迫使用户来回切换。 |
| 文档元数据与过滤 | Glean 和 Onyx 提供来源、类型、日期、作者/标签等过滤 | **必须采用领域化版本**：地域、发布机关、文件类型、效力状态、发布日期。 |
| 证据不足 | Gemini Notebook 明确可能因信息不在来源或问题不清而无法回答 | **必须显式化**：追问缺失条件、说明未找到依据、提示可能存在冲突。 |
| 连续追问 | Perplexity、Copilot、Onyx 等支持会话上下文 | **必须采用**，但界面要让用户看见本轮继承了哪些条件，避免地域、时间等条件悄然漂移。 |
| 跨文档比较 | SharePoint agent 官方示例显示多来源比较用途 | **应当采用专门展示**：按主题并列不同文档结论，每个单元格单独引用，禁止生成无来源的“综合规则”。 |
| 反馈 | Perplexity、Glean、Onyx 均有回答反馈；Glean 支持标记错误引用 | **必须结构化**，用于评估与排障，而不是只有满意度按钮。 |
| 调试 | Elastic 提供 Chat/Query 双模式；Onyx MCP 返回分数和执行查询 | **开发者应有，普通用户默认隐藏**。用于复现检索、过滤和排序问题。 |
| 移动/桌面 | Onyx 组件在移动端转全屏；Gemini Notebook 的部分移动功能有限；SharePoint agent link web part 官方注明不支持移动场景 | **首期桌面优先**。移动端先保证问答和证据卡可读，复杂 PDF 对照与调试后置。 |

## 5. 推荐的首期界面

以下内容均为**本项目设计推断/建议**，不是某一竞品的现成功能描述。

### 5.1 桌面布局

建议使用可折叠的三栏工作台：

```text
┌──────────────┬────────────────────────────┬──────────────────────┐
│ 会话/过滤     │ 问题与证据化回答             │ 原文证据              │
│              │                            │                      │
│ 地域          │ 证据状态                    │ 文档名称、文号          │
│ 文件类型      │ 回答 [1] [2]               │ 发布机关、效力状态       │
│ 效力状态      │ 适用条件/限制                │ 条款、页码、原文高亮     │
│ 日期          │ 建议追问                    │ 打开完整 PDF            │
└──────────────┴────────────────────────────┴──────────────────────┘
```

- 中栏保持阅读连续性；引用紧跟对应结论。
- 点击引用后，右栏切换到对应证据，并高亮精确文本。
- 左栏默认收起高级过滤，只常显当前地域、有效性范围和已选文档数量。
- 屏幕较窄时，右侧证据区变成抽屉；不在首期承诺完整移动端 PDF 对照体验。

### 5.2 回答结构

每次回答固定包含：

1. **证据状态**：证据充分、需要补充条件、未找到依据、来源存在冲突。
2. **直接回答**：只写能够被来源支持的结论，每项结论后放内联引用。
3. **适用条件和限制**：地域、时间、对象、统计口径以及文件效力。
4. **证据列表**：按对回答的实际贡献排序。
5. **下一步**：在问题含糊时给出一两个针对性的追问；不使用泛化的“你还想问什么”。

### 5.3 证据卡

每张证据卡至少展示：

- 文档全名、文件类型；
- 发布机关、文号；
- 适用地域；
- 发布/施行日期与当前效力状态；
- 章、节、条款标题或编号；
- PDF 页码和解析页码（若二者不同应明确区分）；
- 支撑答案的原文及前后文；
- 官方来源链接和本地 PDF 查看入口。

不要在首期展示一个未经校准的百分比“置信度”。用户需要的是证据完整性、适用条件和来源状态，而不是看似精确但难以解释的分数。

### 5.4 证据不足与冲突

- 缺少决定性条件：先追问。例如“20 毫米降雨”必须确认统计时段或采用的标准口径。
- 语料中没有答案：明确显示“当前知识库未找到可引用依据”，同时提供相关文档搜索结果。
- 仅有失效、草案或征求意见稿：可以展示，但必须显著标识，不能与现行正式文件混同。
- 多份文件要求不同：逐份陈列地域、时间、适用对象和原文，不擅自替用户作法律适用判断。
- 回答中的任一关键判断缺少引用：发布前门控应阻止其作为确定结论展示。

### 5.5 跨文档比较

比较不应只输出一段总结，建议采用证据矩阵：

| 比较项 | 文档 A | 文档 B | 说明 |
|---|---|---|---|
| 适用范围 | 原文结论 + 引用 | 原文结论 + 引用 | 是否可直接比较 |
| 工作要求 | 原文结论 + 引用 | 原文结论 + 引用 | 相同点/差异 |
| 时间与效力 | 元数据 + 引用 | 元数据 + 引用 | 新旧关系或未知项 |

每个结论单独绑定证据。若两个文件讨论的对象或效力层级不同，界面首先提示“不可直接比较”，再解释原因。

### 5.6 反馈与开发调试

普通用户反馈建议提供以下原因：

- 回答与证据不一致；
- 引用位置错误或无法打开；
- 漏掉了相关文件；
- 文档版本或效力状态错误；
- 问题理解错误；
- 回答有帮助。

开发者详情默认隐藏，可记录并查看：请求 ID、改写后的查询、过滤条件、召回候选、排序分数、最终上下文、模型及提示版本、引用映射和耗时。该设计借鉴 Elastic 的 Query 模式与 Onyx MCP 的结构化检索结果，但具体字段是本项目建议。

## 6. 分阶段建议

### Must：首期必须完成

1. 桌面优先的问答页，中间回答、右侧证据预览，窄屏时证据区变抽屉。
2. 关键结论级内联引用；点击后定位原文片段、页码和条款。
3. 完整证据列表及领域元数据：文档类型、地域、发布机关、文号、日期、效力状态。
4. 地域、文件类型、效力状态和日期过滤；高级选项可折叠。
5. 四类清晰状态：证据充分、需补充条件、未找到依据、来源冲突。
6. 支持上下文追问，同时显式展示本轮继承的关键条件。
7. 结构化反馈，并保留可复现的请求与检索日志。
8. “问答”和“只看搜索结果”两种结果形态；没有可靠答案时仍让用户找到原文。

### Should：基础闭环稳定后完成

1. 跨文档比较矩阵，每个比较项分别引用。
2. 开发者“检索详情”抽屉，用于查看查询改写、候选片段、排序和上下文选择。
3. 会话历史、收藏证据和带引用导出。
4. 对失效文件、草案、征求意见稿及疑似冲突文件提供明显视觉标识。
5. 将用户反馈连接到评估数据集和问题复现流程，但不自动把一次反馈当作正确标签。

### Later：暂不阻塞首期

1. 完整移动端适配与移动端 PDF 对照。
2. 面向不同角色的权限和个性化入口。
3. 文档入库、更新、撤回和质量管理后台。
4. API/MCP 消费者的可视化管理；MCP 服务本身应复用相同的结构化证据对象。
5. 多模型对比、复杂研究模式以及面向业务用户的检索参数调节。

## 7. 最终建议

首期入口最合适的定位不是“排水行业 ChatGPT”，而是“可核验的排水规范证据工作台”。

它可以保留聊天的低门槛，但信任主要来自以下闭环：

```text
提问 → 明确适用条件 → 给出带逐项引用的回答
     → 点击查看原文与文档效力 → 继续追问或报告问题
```

这个方案吸收了 Gemini Notebook/Glean 的精确证据核验、Copilot 的侧栏来源审阅、Perplexity 的连续研究对话、Onyx 的 Search/Chat 双模式，以及 Elastic 的可调试查询模式；同时针对规范依据场景增加了效力状态、适用条件、证据不足和跨文档可比性门控。这些新增部分属于本项目设计推断，应在后续原型测试和真实问题评估中验证。

