> ⚠️ **可信度声明**：本文由子代理联网研究产出（2026-10-03），所有论断附来源 URL 并区分【一手】/【二手】。
> 环境限制：web_search 因缺 API Key 不可用，改用 web_fetch 直取官方文档与 Bing 检索；未能派生子代理。
> 第 10.6 节列出全部「未找到/未取得」项，未做编造。

---

# AI Agent 框架架构与设计取舍研究

> 目的：为设计另一个 agent harness（下文称「目标 harness」，重点是 QQ 聊天机器人场景）提供参考。
> 研究对象：**opencode**、**Hermes**（身份已确认，见第 1 节），补充研究 **Claude Code**、**SWE-agent / mini-SWE-agent**、**OpenHands**。
>
> **来源标注约定**
> - **【一手】** = 官方仓库 / 官方文档 / 官方设计文档 / 官方论文 / 官方工程博客
> - **【二手】** = 第三方博客、社区讨论、聚合站
> - 所有关键论断后均附 URL。查不到的写「未找到」，不做推测性补全。
>
> **调研环境限制（影响可复现性）**：本次调研中 `web_search` 工具不可用（缺少 API key），改用 `web_fetch` 直接检索 Bing（`https://cn.bing.com/search?q=...`）并直取官方文档页。DuckDuckGo 在此环境被阻断；GitHub 网页版常失败，改用 `raw.githubusercontent.com` 取原始 Markdown。所有下列 URL 均为本次实际抓取过的地址。

---

## 1. 研究对象确认：Hermes 到底是哪一个

### 1.1 结论

**用户所指的「Hermes」应当是 `NousResearch/hermes-agent`——一个独立的开源 agent harness（Python 实现，MIT 许可），而不是 NousResearch 的 Hermes 大模型系列。**

必须先说清一件事：**NousResearch 同时存在「Hermes 模型」与「Hermes Agent」两个完全不同的东西**，同名但不是一个层面的事物。

| 名称 | 是什么 | 是 harness 吗 |
| --- | --- | --- |
| NousResearch **Hermes 模型系列**（Hermes 2 / 3 / 4 …） | 微调后的**大语言模型权重**，可下载、可推理 | ❌ 是模型，不是 harness |
| **Hermes Agent**（`NousResearch/hermes-agent`） | 带 agent loop、工具注册表、记忆系统、消息网关的**完整 agent 运行时** | ✅ 是 harness |

### 1.2 判定依据

1. **官方文档自述【一手】**：官方文档首页明确定义为「The self-improving AI agent built by Nous Research」，并给出安装脚本、CLI 命令（`hermes setup` / `hermes gateway`）、GitHub 仓库地址 `https://github.com/NousResearch/hermes-agent`。
   → https://hermes-agent.nousresearch.com/docs/
2. **存在开发者架构文档【一手】**：官方文档站有独立的 Developer Guide，包含《Architecture》《Agent Loop Internals》《Tools Runtime》《Context Compression and Caching》《Session Storage》《Gateway Internals》等页面。**只有 harness 才会写这种文档**，模型系列不会。
   → https://hermes-agent.nousresearch.com/docs/developer-guide/architecture
3. **代码结构是 harness 结构【一手】**：架构页给出的目录树包含 `run_agent.py`、`agent/conversation_loop.py`、`tools/registry.py`、`gateway/platforms/`、`cron/`、`acp_adapter/`——这是运行时脚手架的组织方式，而非模型仓库。
   → 同上
4. **与主流 harness 生态并列【一手】**：DeepSeek 官方 API 文档将其归入「agent integrations」章节（与其它 agent CLI 并列）。
   → https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/hermes/
5. **第三方也将其明确定位为 harness【二手】**：一篇中文技术博客直白写道「Hermes 就是一个 harness：它的 47 个工具、四层审批、迭代预算、SessionDB——所有这些都不是『让模型变聪明』」。注意：**该文的「47 个工具」「四层审批」等具体数字与官方文档不一致（官方称 70+ 工具、三种审批模式），此处仅作为「它被业界视为 harness」的佐证引用，数字本身不作为事实依据。**
   → https://blog.csdn.net/buvuvib66/article/details/161517783

### 1.3 需要警惕的同名干扰项

调研中出现大量同名或易混站点，**其中多数并非官方，不建议作为设计依据**：

- `hermes-agent.nousresearch.com` — **官方**（Nous Research 域名）【一手】
- `github.com/NousResearch/hermes-agent` — **官方仓库**【一手】
- `hermes-agent.org` / `hermesagent.org.cn` — **第三方中文聚合/教程站**。其内容方向与官方一致（同样链接到 `github.com/NousResearch/hermes-agent`），但**不是官方站点**【二手】
- `runoob.com`（菜鸟教程）的 Hermes Agent 页 — 第三方教程【二手】
- `hermes.cn` / `hermes.com` — **法国奢侈品牌 Hermès 官网**，与 AI 无关，纯同名噪音
- 另有若干「Hermes」命名的小型 agent 项目，本次未找到具备官方架构文档、可支撑设计参考的同类项目（**未找到**足以与之并列的第二个 Hermes agent 框架）

### 1.4 为什么这个确认对本次任务特别重要

Hermes Agent 的官方能力清单里**明确包含 QQ Bot 平台适配**，并且是以「单网关进程 + 25 个以上平台适配器」的形式实现的。这意味着它是本次四个研究对象中**唯一一个把「多人消息平台上的常驻 agent」当作一等公民来设计的 harness**，对 QQ 聊天机器人 harness 的参考价值远高于其它三个（后三者本质是单用户、终端、任务驱动的编码 agent）。

→ 平台对比表中 `QQ` 一行：https://hermes-agent.nousresearch.com/docs/user-guide/messaging/
→ 架构页 `gateway/platforms/` 内置适配器列表含 `qqbot`：https://hermes-agent.nousresearch.com/docs/developer-guide/architecture

---

## 2. opencode（sst/opencode）

> 注：调研期间官方仓库地址在文档与 README 中显示为 `github.com/anomalyco/opencode`（README 徽章与文档页脚均指向该组织），而任务描述中的 `sst/opencode` 是同一项目的既有称呼。官方文档站为 `opencode.ai/docs`。本节的架构事实全部来自官方文档【一手】。
> 另注：官方文档页脚显示版本已到 **OpenCode v2**（文档头部有「New OpenCode v2 is now available」横幅），下文标注的配置项以 v1.x 文档为准，v2 可能存在差异。

### 2.1 Agent 主循环

opencode 的主循环是**单会话、工具驱动、迭代式**的：模型返回 `tool_calls` → 执行工具 → 结果回填 → 再次请求模型，直到模型不再请求工具或达到 `steps` 上限。

关键设计点在于**用「迭代步数预算」而不是「时间/成本预算」来兜底**：

- 每个 agent 可配置 `steps`（旧字段 `maxSteps` 已废弃）。
- **若未设置 `steps`，agent 会一直迭代到模型自己停止或用户中断**——没有隐式上限。
- 达到上限时，agent 会**收到一段特殊的 system prompt，指示它「总结已完成的工作 + 建议剩余任务」，并只允许输出文本**。

这一点很值得注意：**上限不是硬性掐断，而是切换成「总结模式」**，把一次被截断的执行转化为一次可交付的交接。原文：

> "When the limit is reached, the agent receives a special system prompt instructing it to respond with a summarization of its work and recommended remaining tasks."
> → https://opencode.ai/docs/agents/

此外有 `doom_loop` 保护：**同一个工具调用以完全相同的输入重复 3 次**时触发（默认 `"ask"`）。这是一个很轻量但有效的死循环探测器。

→ https://opencode.ai/docs/permissions/

### 2.2 工具系统

**内置工具清单【一手】**（https://opencode.ai/docs/tools/）：

| 工具 | 作用 | 受哪个权限键控制 |
| --- | --- | --- |
| `bash` | 执行 shell 命令 | `bash`（按解析后的命令匹配） |
| `edit` | 精确字符串替换修改文件 | `edit` |
| `write` | 新建/覆盖文件 | `edit` |
| `read` | 读文件（支持行范围） | `read` |
| `grep` | 正则内容搜索（底层 ripgrep） | `grep` |
| `glob` | 文件名模式匹配（底层 ripgrep） | `glob` |
| `apply_patch` | 应用 patch（路径写在 `patchText` 标记行内） | `edit` |
| `lsp`（实验性） | LSP 语义查询（定义/引用/hover/调用层级） | `lsp` |
| `skill` | 加载一个 `SKILL.md` 到对话 | `skill` |
| `todowrite` | 维护待办列表 | `todowrite` |
| `webfetch` | 抓取网页 | `webfetch` |
| `websearch` | 联网搜索（走 Exa/Parallel 托管 MCP） | `websearch` |
| `question` | **执行过程中向用户提问**（带选项列表） | `question` |

设计取舍要点：

- **权限键按「能力」而非「工具」聚合**：`edit` 一个键同时管住 `edit`、`write`、`apply_patch`。这是很好的抽象——用户想表达的是「能不能改我的文件」，而不是「能不能用某个叫 write 的函数」。
- **`grep`/`glob` 底层统一用 ripgrep，因此天然继承 `.gitignore`**；想搜被忽略的目录需要在项目根放 `.ignore` 文件。这是「复用成熟工具而非自己实现搜索」的典型取舍。
- **`read` 支持行范围**——对大文件是必要的，否则一次读入会直接吃掉上下文。
- **`question` 工具是一等公民**：允许 agent 在任务中途反问用户、给出候选选项。对交互式场景非常关键（对 QQ bot 同样关键，见第 6 节）。
- **`todowrite` 默认对 subagent 禁用**，需要手动开启。这是有意的能力裁剪：子代理应当专注执行，不该再分心维护计划。

**自定义工具与 MCP【一手】**：可通过 `custom-tools` 与 MCP server 扩展；MCP 工具可用通配符授权，例如 `"mymcp_*": "ask"`。
→ https://opencode.ai/docs/tools/ 、 https://opencode.ai/docs/custom-tools/

**并行调用**：官方文档未明确描述工具并行执行策略（**未找到** opencode 侧关于并行工具调用的官方说明）。从 `tool.execute.before/after` 是逐次触发的钩子来看，至少钩子层是串行语义。

### 2.3 上下文管理

**压缩是一个「隐藏的系统 agent」**【一手】：

> 「**compaction** — Hidden system agent that compacts long context into a smaller summary. It runs automatically when needed and is not selectable in the UI.」

同类隐藏 agent 还有 **title**（生成会话短标题）和 **summary**（生成会话摘要）。

→ https://opencode.ai/docs/agents/

这是一个很干净的设计模式：**把「上下文压缩」建模成一个普通的 agent 任务**（有自己的 prompt、模型、工具权限），而不是散落在主循环里的一段特判代码。同时它不在 UI 中暴露，避免用户误选。

**压缩钩子可被插件介入**【一手】：插件事件 `experimental.session.compacting` 在 LLM 生成续接摘要**之前**触发，插件可以：

- `output.context.push(...)` — 向压缩 prompt 注入额外上下文；
- `output.prompt = ...` — **整体替换压缩 prompt**。

> 原文：'Use it to inject domain-specific context that the default compaction prompt would miss.' / 'You can also replace the compaction prompt entirely by setting `output.prompt`'

→ https://opencode.ai/docs/plugins/

这条对目标 harness 极有参考价值：**压缩策略是产品级差异点，应当留出替换接口**。

**分层记忆**：opencode 用 `AGENTS.md`（由 `/init` 生成，建议提交到 Git）作为项目级持久上下文。官方未描述类似 Hermes 的跨会话记忆 DB（**未找到** opencode 侧的分层记忆/长期记忆机制）。

→ https://opencode.ai/docs/

### 2.4 子代理 / 多代理

opencode **有明确的两类 agent**【一手】：

| 类型 | 说明 |
| --- | --- |
| **primary**（主代理） | 用户直接交互的助手，用 **Tab 键**在会话中切换。内置 **Build**（全权限，默认）与 **Plan**（受限） |
| **subagent**（子代理） | 由 primary 代 agent 调用，或用户用 **@ mention** 手动调用。内置 **General**、**Explore**、**Scout** |

内置 subagent 的定位非常清晰：

- `general` — 通用研究/多步任务，**有全部工具权限（除 todo）**，官方明说「Use this to run multiple units of work in parallel」；
- `explore` — **只读**、快速探索代码库，**不能改文件**；
- `scout` — **只读**、面向外部文档与依赖研究（可把依赖仓库 clone 到 opencode 管理的缓存区，从而**不污染工作区**）。

**上下文隔离是「会话级」的、且可导航**【一手】：subagent 会创建**子会话（child session）**，并提供键位在父子会话间导航：

- `session_child_first`（默认 `<Leader>+Down`）进入第一个子会话
- `session_child_cycle` / `session_child_cycle_reverse`（默认 `Right` / `Left`）在子会话间循环
- `session_parent`（默认 `Up`）回到父会话

> 原文：'This lets you switch between the main conversation and specialized subagent work.'

这是一个很有价值的设计：**子代理的隔离上下文不是黑盒，而是用户可以进去看的独立会话**。对调试「子代理为什么给出这个结论」至关重要。

**结果回传方式**：官方文档未明确说明 subagent 结果如何回传给父会话（是全文还是摘要）（**未找到**）。

**模型继承规则**【一手】：subagent **若不显式指定 `model`，则使用「调用它的那个 primary agent」的模型**；primary agent 若不指定则用全局配置的模型。

→ https://opencode.ai/docs/agents/

**权限可分层覆盖**【一手】：agent 权限与全局 config 合并，**agent 规则优先**。可用对象语法做细粒度控制，例如只允许改文档：

```json
"agent": { "build": { "permission": { "bash": { "*": "ask", "git *": "allow", "git commit *": "ask", "git push *": "deny" } } } }
```

→ https://opencode.ai/docs/permissions/

### 2.5 决策与规划

opencode 的规划机制是**「Plan 模式（一个 primary agent）+ todowrite 工具」**的组合，而不是独立的 plan mode 状态机【一手】：

- `plan` 是一个 primary agent，**默认把所有文件编辑和所有 bash 命令设为 `ask`**——即「可以分析、可以提议，但每个改动都要你点头」。
- 官方推荐工作流：先用 Tab 切到 Plan 让它出方案 → 反复迭代方案 → 再切回 Build 让它执行。
- `todowrite` 提供执行期的任务列表跟踪。

→ https://opencode.ai/docs/agents/ 、 https://opencode.ai/docs/

**设计取舍**：opencode **没有**把 plan 做成一个独立的模式（像 Claude Code 的 Shift+Tab 权限模式那样），而是复用「agent + 权限」这套统一抽象。好处是概念少、可组合；代价是「计划模式」和「代理身份」被绑定——你不能在一个只读 agent 里临时开启写权限。

### 2.6 成本控制

**模型分级**【一手】：

- 每个 agent 可独立指定 `model`，官方文档直接点明用途：「a faster model for planning, a more capable model for implementation」；官方示例中 plan agent 用 `claude-haiku-*`、build 用 `claude-sonnet-*`。
- `temperature` 可按 agent 配置；未指定时用模型默认值（多数模型为 0，Qwen 系列为 0.55）。

**迭代预算控成本**【一手】：`steps` 是官方明确推荐的控成本旋钮：

> 'This allows users who wish to control costs to set a limit on agentic actions.'

**子代理模型降级**：文档未描述「subagent 统一跑小模型」的官方模式，但 `general`/`explore`/`scout` **各自可配 `model`**，实际上等价于可以做模型分级。

**Prompt cache**：**未找到** opencode 官方关于 prompt caching 的文档说明。

→ https://opencode.ai/docs/agents/

### 2.7 权限与安全

这是 opencode 文档中最成熟的部分。**三态权限模型**【一手】：

| 值 | 行为 |
| --- | --- |
| `"allow"` | 直接运行，不询问 |
| `"ask"` | 弹窗请求批准 |
| `"deny"` | 直接阻断 |

**细粒度对象语法 + 通配符匹配**【一手】：

```json
"permission": {
  "bash": { "*": "ask", "git *": "allow", "npm *": "allow", "rm *": "deny", "grep *": "allow" },
  "edit": { "*": "deny", "packages/web/src/content/docs/*.mdx": "allow" }
}
```

规则语义有两点很关键：

- **「最后匹配的规则获胜」（last matching rule wins）**，因此惯用法是**把兜底 `"*"` 放最前面**，具体规则放后面。这是「默认拒绝 + 白名单」的优雅表达。
- 通配符是简单匹配：`*` 匹配零或多个任意字符，`?` 匹配恰好一个字符。

**默认值（安全默认）**【一手】：

- 多数权限默认 `"allow"`；
- **`doom_loop` 与 `external_directory` 默认 `"ask"`**；
- **`read` 默认 allow，但 `.env` 文件默认 deny**（`*.env`、`*.env.*` deny，`*.env.example` allow）。

**`external_directory`：工作区隔离**【一手】：任何触碰**启动目录之外**路径的工具调用都会触发 `external_directory` 权限。官方明确强调一个反直觉点：

> 「Home expansion (like `~/...`) only affects how a pattern is written. It does not make an external path part of the current workspace.」

即 `~` 展开**只是写法上的便利，不会把家目录变成本工作区**——想访问必须显式授权。这是一个很好的「不给自己开后门」的坚持。

**"ask" 的三选项 UI**【一手】：`once`（仅此一次）/ `always`（本会话内批准匹配的模式）/ `reject`。而且 `always` 会批准的**模式集合由工具自己提供**——例如 bash 批准通常只把「安全命令前缀」加白（如 `git status*`）。这个细节很重要：**批准粒度由工具语义决定，而不是让用户手写正则**。

**`--auto` 自动批准模式**【一手】：自动批准所有**未被显式 deny** 的请求。官方特别说明：

> 'Explicit `"deny"` rules are still enforced. Auto mode only changes requests that would otherwise ask for approval.'

即 **auto 模式不能突破 deny 底线**——这是正确的分层：deny 是硬策略，ask 是可协商项。

**沙箱**：opencode 主要在**权限层**做管控，未描述容器/OS 级沙箱（**未找到** opencode 官方沙箱方案）。

→ https://opencode.ai/docs/permissions/

### 2.8 扩展机制

**四层扩展**【一手】：

**(1) Agent 定义（Markdown + YAML frontmatter）** — 放置位置决定作用域：

- 全局：`~/.config/opencode/agents/`
- 项目：`.opencode/agents/`
- **文件名即 agent 名**（`review.md` → `review` agent）

**(2) Skills（`SKILL.md`）**【一手】— 搜索路径非常值得注意，opencode **主动兼容竞品目录**：

- 项目：`.opencode/skills/<name>/SKILL.md`
- 全局：`~/.config/opencode/skills/<name>/SKILL.md`
- **`.claude/skills/<name>/SKILL.md`**（Claude 兼容）
- **`.agents/skills/<name>/SKILL.md`**（跨工具开放约定）

发现规则：项目路径下**从当前工作目录向上走到 git worktree 根**，沿途加载匹配的 skills。

frontmatter 只认 5 个字段：`name`、`description`（**均必需**）、`license`、`compatibility`、`metadata`（string→string map）；**未知字段被忽略**。

校验规则很具体（可直接抄）：

- `name`：1–64 字符，小写字母数字 + 单连字符分隔，不以 `-` 开头或结尾，不含连续 `--`，**且必须与所在目录名一致**。等价正则：`^[a-z0-9]+(-[a-z0-9]+)*$`
- `description`：1–1024 字符

**渐进式披露的实现方式**：skills **不出现在系统提示里**，而是**列在 `skill` 工具的 description 中**：

```xml
<available_skills>
  <skill><name>git-release</name><description>Create consistent releases and changelogs</description></skill>
</available_skills>
```

agent 通过调用 `skill({ name: "git-release" })` 加载全文。**这是一个与 Claude Code 不同的实现选择**（Claude Code 把 skill 名+描述注入系统提示），两者都实现了「元数据常驻、正文按需」。opencode 的做法把 skill 列表的成本算进了工具定义而非系统提示。

**skill 权限**同样是三态 + 通配符：`{"skill": {"*": "allow", "internal-*": "deny", "experimental-*": "ask"}}`，**`deny` 的 skill 对 agent 完全隐藏**。还可用 `tools: { skill: false }` 彻底关掉——此时 `<available_skills>` 段整体消失。

**(3) Plugins**【一手】— JS/TS 模块，加载位置 `.opencode/plugins/` 与 `~/.config/opencode/plugins/`（自动加载），或通过 config 里的 npm 包名（**启动时用 Bun 自动安装并缓存到 `~/.cache/opencode/node_modules/`**）。

插件上下文注入：`{ project, client, $, directory, worktree }`（`$` 是 Bun shell API，`client` 是 opencode SDK 客户端）。

**插件可注册自定义工具**，用 Zod schema 描述参数：

```ts
tool({ description: "...", args: { foo: tool.schema.string() }, async execute(args, context) {...} })
```

且**插件工具与内置工具同名时，插件工具优先**——这是刻意的可覆盖设计。

**事件清单（可直接作为 hook 设计的参考）**【一手】：
`command.executed`；`file.edited`、`file.watcher.updated`；`installation.updated`；`lsp.client.diagnostics`、`lsp.updated`；`message.part.removed/updated`、`message.removed/updated`；`permission.asked`、`permission.replied`；`server.connected`；`session.created/compacted/deleted/diff/error/idle/status/updated`；`todo.updated`；`shell.env`；`tool.execute.before`、`tool.execute.after`；`tui.prompt.append`、`tui.command.execute`、`tui.toast.show`。

**加载顺序（明确且可预期）**【一手】：
1. 全局 config → 2. 项目 config → 3. 全局插件目录 → 4. 项目插件目录。同名同版本 npm 包只加载一次；本地插件与同名 npm 插件**都会加载**。

一个很实用的插件示例（`.env` 保护）——直接 throw 就能阻断：

```js
"tool.execute.before": async (input, output) => {
  if (input.tool === "read" && output.args.filePath.includes(".env")) throw new Error("Do not read .env files")
}
```

还有 `shell.env` 钩子可向**所有 shell 执行（含用户终端）**注入环境变量。

**(4) MCP servers** — 见 2.2。

→ https://opencode.ai/docs/plugins/ 、 https://opencode.ai/docs/skills/ 、 https://opencode.ai/docs/agents/

### 2.9 opencode 明确承认的限制与取舍

| 承认的点 | 出处 |
| --- | --- |
| **未设 `steps` 时没有隐式迭代上限**——会一直跑到模型停或用户中断 | https://opencode.ai/docs/agents/ |
| `lsp` 工具是**实验性**的，需显式开 `OPENCODE_EXPERIMENTAL_LSP_TOOL=true` | https://opencode.ai/docs/tools/ |
| `websearch` **依赖特定 provider**（OpenCode / OpenCode Go）或需设 `OPENCODE_ENABLE_EXA` / `OPENCODE_ENABLE_PARALLEL`；实际走托管 MCP 且**无需 API key** | https://opencode.ai/docs/tools/ |
| `tools` 布尔配置自 v1.1.1 起**已废弃**，合并进 `permission`（旧配置向后兼容） | https://opencode.ai/docs/permissions/ |
| `maxSteps` 字段**已废弃**，改用 `steps` | https://opencode.ai/docs/agents/ |
| skill frontmatter **未知字段被静默忽略**（写错字段名不会报错） | https://opencode.ai/docs/skills/ |
| 命名冲突时 skill 名必须在**所有位置唯一**，否则不显示 | https://opencode.ai/docs/skills/ |
| **`read` 之外，`bash` 仍可绕过文件读取限制**（读 `.env` 之类）——官方未在权限文档中宣称这是硬边界 | 文档未声明为强隔离，见 https://opencode.ai/docs/permissions/ |
| Windows 上 **Bun 安装支持仍在进行中**；官方建议 Windows 用户用 WSL | https://opencode.ai/docs/ |
| 明确要求衍生项目**在 README 中声明与官方无关联**（命名政策） | https://github.com/sst/opencode README |

> 特别值得注意的开源治理细节：官方 README 明确要求，使用 "opencode" 作为名称一部分的项目（如 "opencode-dashboard"）**必须在 README 中声明不是官方构建、无关联**。设计自有 harness 的命名/生态政策时可参考。

---

## 3. Hermes（NousResearch/hermes-agent）

> 本节几乎全部来自官方 Developer Guide 与 User Guide【一手】。Hermes 是目前四个研究对象中**架构文档最完整、且唯一原生面向多人消息平台的 harness**。

### 3.1 Agent 主循环

**单一同步编排引擎 `AIAgent`**【一手】。`run_agent.py` 如今只是**薄门面**，真正的循环在 `agent/conversation_loop.py`，各阶段拆在 `agent/turn_*.py`（iteration prep / api call / api error / overflow / truncation / recovery）。

**轮次生命周期（完整 9 步）**【一手】：

```
run_conversation()
 1. 若未提供则生成 task_id
 2. 把用户消息追加到对话历史
 3. 构建或复用缓存的 system prompt（prompt_builder.py）
 4. 检查是否需要 preflight 压缩（>50% 上下文）
 5. 从历史构建 API 消息
    - chat_completions: 原样 OpenAI 格式
    - codex_responses: 转成 Responses API input items
    - anthropic_messages: 经 anthropic_adapter.py 转换
 6. 注入临时 prompt 层（预算警告、上下文压力提示）
 7. 若走 Anthropic 则打 prompt caching 标记
 8. 发起可中断的 API 调用（_interruptible_api_call）
 9. 解析响应：
    - 若有 tool_calls: 执行、追加结果、回到第 5 步循环
    - 若为文本响应: 持久化会话、按需 flush 记忆、返回
```

**支持三种 API 模式**【一手】：`chat_completions`（OpenAI 兼容）、`codex_responses`（OpenAI Responses/Codex）、`anthropic_messages`（原生 Anthropic）。三者**在任何 API 调用前后都归一到同一套内部格式**（OpenAI 风格 `role`/`content`/`tool_calls` 字典）。

模式解析优先级：显式构造参数 → provider 检测（如 `anthropic` provider）→ base URL 启发式（如 `api.anthropic.com`）→ 默认 `chat_completions`。

**严格的消息交替校验**【一手】——这是很实用的硬约束：

- system 之后：`User → Assistant → User → Assistant → ...`
- 工具调用期间：`Assistant (with tool_calls) → Tool → Tool → ... → Assistant`
- **绝不出现连续两条 assistant**
- **绝不出现连续两条 user**
- **只有 `tool` 角色允许连续多条**（并行工具结果）

官方理由：providers 会校验这些序列并拒绝畸形历史。**设计自有 harness 时，这是一个「必须主动维护、不能指望模型」的不变量。**

**可中断的 API 调用**【一手】：`_interruptible_api_call()` 把 HTTP 调用放在后台线程，主线程同时等待「响应就绪 / 中断事件 / 超时」。被中断时：

- API 线程被放弃（响应丢弃）
- agent 可处理新输入或干净关闭
- **不会把半截响应注入对话历史**

**迭代预算 `IterationBudget`**【一手】：

- 默认 **500 次**迭代（`agent.max_turns` 可配）
- **每个 agent 有自己的预算**；subagent 预算是独立的，上限为 `delegation.max_iterations`（默认 **50**）
- **因此 parent + subagents 的总迭代数可以超过 parent 自己的上限**
- 到 100% 时 agent 停止并返回工作总结

**模型降级（fallback）**【一手】：主模型失败（429 / 5xx / 401 / 403）时按 `fallback_providers` 列表依次尝试；401/403 会先尝试凭据刷新。有一个很细致的特判：

> A Codex Responses turn that stalls on reasoning-only output (**three consecutive continuations with no visible text or tool call**) also fails over to the next fallback with reason `incomplete_response`；若该停顿已耗尽迭代预算，fallback 获得**恰好一次有界宽限调用**。

**辅助任务有独立的 fallback 链**：vision、compression、web extraction 各自可通过 `auxiliary.*` 配置。

→ https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop

### 3.2 工具系统

**规模**【一手】：`tools/registry.py` 中央注册表，**70+ 注册工具、约 28 个 toolset**。终端工具支持 **7 种后端**：local、Docker、SSH、Daytona、Modal、Singularity、Vercel Sandbox。

**注册模式：导入即自注册 + AST 自动发现**【一手】：

每个工具文件在模块顶层调用 `registry.register(...)`：

```python
registry.register(
    name="terminal", toolset="terminal",
    schema={...},                 # 面向模型的 schema
    handler=handle_terminal,      # 执行函数
    check_fn=check_terminal,      # 可选：可用性判定
    requires_env=["SOME_VAR"],    # 可选：所需环境变量（用于 UI 展示）
    is_async=False,
    description="Run commands",   # ToolEntry 注册表元数据
    emoji="💻",
)
```

`discover_builtin_tools()` **用 AST 解析扫描 `tools/*.py`**，找出含顶层 `registry.register()` 调用的模块再 import 它们：

```python
if _module_registers_tools(path):  # AST 检查顶层 registry.register()
    importlib.import_module(f"tools.{path.stem}")
```

因为 AST 只匹配**顶层**调用（不匹配函数体内的），所以 `tools/` 下的辅助模块不会被误 import。**新增工具文件自动被发现，无需维护手工清单。**

**冲突策略**【一手】：注册**会遮蔽来自不同 toolset 的同名工具时被拒绝**（记 error log），除非传入 `override=True`；**插件覆盖内置工具还需要操作者显式开启** `plugins.entries.<plugin_id>.allow_tool_override: true`。这个两级开关非常合理——覆盖能力存在，但默认关闭且需要显式授权。

**`check_fn`：可用性即过滤**【一手】：

- 典型检查：API key 是否存在、服务是否在跑、二进制是否已安装
- **check_fn 抛异常视为「不可用」**（fail-safe）
- 每次构建 schema 列表时运行，**结果按调用缓存**（多个工具共用同一 check_fn 只跑一次）
- 不可用的工具**从 schema 中整个移除**——模型根本看不到

**toolset 解析**【一手】：

`model_tools.get_tool_definitions(enabled_toolsets, disabled_toolsets, quiet_mode)`：

1. 给了 `enabled_toolsets` → 只包含这些 toolset 的工具（`resolve_toolset()` 会把复合 toolset 展开成单个工具名）
2. 给了 `disabled_toolsets` → 从全部 toolset 里减去
3. 都没给 → 包含所有已知 toolset
4. 交给 `registry.get_definitions()` 做 `check_fn` 过滤，返回 OpenAI 格式 schema
5. **动态 schema 修补**：`execute_code` 与 `browser_navigate` 的 schema 会被**动态调整，只引用真正通过过滤的工具**——官方理由：**防止模型幻觉出不可用的工具**

第 5 点是很有价值的细节：**当某个工具的参数可以「点名」另一个工具时，必须同步裁剪它的 schema**。

**调度流程**【一手】：

```
Model response with tool_call
  → agent loop (agent/conversation_loop.py)
  → model_tools.handle_function_call(name, args, task_id, user_task)
  → [Agent-loop 工具？] → 由 agent loop 直接处理（todo, memory, session_search, delegate_task）
  → [Plugin pre-hook] → invoke_hook("pre_tool_call", ...)
  → registry.dispatch(name, args, **kwargs)
  → 查 ToolEntry → [async 则 _run_async() 桥接 | sync 直接调用]
  → 返回结果字符串（或 JSON 错误）
  → [Plugin post-hook] → invoke_hook("post_tool_call", ...)
```

**两层错误包裹**【一手】：`registry.dispatch()` 捕获 handler 任意异常返回 `{"error": "Tool execution failed: ..."}`；`handle_function_call()` 再包一层。**保证模型永远收到格式良好的 JSON 字符串，绝不收到未处理异常。**

**四个 agent 级工具被前置拦截**【一手】（在到达 registry 之前）：`todo_list`（规划/任务跟踪）、`memory`（持久记忆写入）、`session_search`（跨会话检索）、`delegate_task`（生成子代理会话）。理由：它们需要 agent 级状态（TodoStore、MemoryStore 等）。注意：**它们的 schema 仍然注册在 registry 中**（供 `get_tool_definitions` 使用），但 handler 若被直接调度会返回 stub 错误。

**异步桥接**【一手】——三种路径：
- **CLI 路径（无运行中的 loop）**：用常驻 event loop，保持缓存的 async client 存活
- **Gateway 路径（有运行中的 loop）**：起一个一次性线程跑 `asyncio.run()`
- **Worker 线程（并行工具）**：用线程局部存储中的每线程常驻 loop

**并行执行**【一手】：官方在《Agent Loop Internals》中明确：

> **Single tool call** → executed directly in the main thread
> **Multiple tool calls** → executed concurrently via `ThreadPoolExecutor`
> - 例外：标记为 **interactive** 的工具（如 `clarify`）**强制串行**
> - **结果按原始 tool_call 顺序重新插入，与完成顺序无关**

「结果顺序与完成顺序解耦」是并行工具调用的必要正确性条件（否则模型看到的 tool_call_id 对应关系会错乱）。**标记为 interactive 的工具强制串行**也是关键细节——一个要问用户的工具不能被并行。

→ https://hermes-agent.nousresearch.com/docs/developer-guide/tools-runtime 、 .../agent-loop

### 3.3 上下文管理

这是 Hermes 文档中最深、也最值得借鉴的一部分。

**（A）Prompt 三层结构**【一手】：

`system_prompt.py` + `prompt_builder.py` 按序组装 system prompt 的**三层**：

```
stable  → identity / tool guidance / skills
context → context files（如 AGENTS.md）
volatile→ memory / profile / timestamp 块
```

**设计原则「Prompt stability」**【一手】（官方设计原则表）：

> **System prompt doesn't change mid-conversation.** No cache-breaking mutations except explicit user actions (`/model`).

**（B）双压缩系统**【一手】：

```
   Incoming message
        │
        ▼
┌──────────────────────────┐
│  Gateway Session Hygiene │  fires at 85% of context
│  (pre-agent, 粗估)        │  Safety net for large sessions
└─────────────┬────────────┘
              ▼
┌──────────────────────────┐
│  Agent ContextCompressor │  fires at 50% of context (default)
│  (in-loop, 真实 token)    │  Normal context management
└──────────────────────────┘
```

**1. Gateway Session Hygiene（85% 阈值）** — 在 agent 处理消息**之前**跑的**安全网**，防止会话在轮次之间长太大（例如 Telegram/Discord 里 overnight 累积）导致 API 失败。阈值**固定 85%**。仅在 `len(history) >= 4` 且压缩启用时触发。

> 官方明确记录了一个踩过的坑：「Setting it at 50% (same as the agent) caused **premature compression on every turn** in long gateway sessions.」

**2. Agent ContextCompressor（50% 阈值，可配）** — 主压缩系统，跑在 agent 的工具循环内，能拿到**准确的、API 报告的 token 数**。

**（C）Token 计量的工程质量（非常值得学）**【一手】：

- 每个压缩门（turn-start preflight / idle / pre-API pressure / post-tool）**先问 usage anchor**（`agent/usage_anchor.py`）：provider 上次报告的 prompt + completion token 数，**加上仅对「该响应之后追加的消息」的粗估**。
- anchor 用**内容指纹**标识被计价的 transcript，因此**能扛住 gateway 每轮从 DB 重读历史**；并且**持久化在 session 行上**，所以新进程（`--resume`、desktop 每轮 `serve`）能恢复它。
- **没有 anchor 时（首个请求、rewind/edit-resend），整个上下文的粗估即使超阈值也会「等一个请求」**以求 provider 证据（`should_defer_preflight_to_real_usage`）。官方理由：**估计值的大小不能证明这个请求一定会失败**（包含估计值等于甚至超过整个上下文窗口的情况）。代价被诚实记录：**真正超大的请求可能先挨一次被拒的请求，然后才走响应式恢复**。
- **图片按「从 provider usage 学到的每图成本」计价**，而不是厂商公式：在「自上次 anchor 以来新增 N 张图」的响应上，用真实 `prompt_tokens` 与纯文本投影之间的残差除以 N。值按 `model@host` 存于 `~/.hermes/cache/image_token_costs.json`。首次 vision 轮前用平坦默认值 **1500**。
- **不透明的 provider blob（Codex reasoning / compaction 项的 `encrypted_content`）对所有本地估算贡献 0** ——官方明说：只有真实 usage 才能给它们定价。

**（D）压缩算法 4 阶段**【一手】（`ContextCompressor.compress()`）：

**Phase 1 — 剪掉旧工具结果（便宜，无 LLM 调用）**
保护尾之外的、长度 >200 字符的旧工具结果被替换为：
```
[Old tool output cleared to save context space]
```
**重要例外**：模型**尚未回应的**那一轮工具调用，其文本结果**保持原样**、图片结果**保持完整**——这样模型能用上它自己要来的输出。若某一轮单独就超过输入预算的 20%，它可被摘要，其更早的图片会被退役以腾出空间。

**Phase 2 — 确定边界**
```
[0..2]   ← protect_first_n（system prompt + 首个交换）硬编码保留
[3..N]   ← middle turns → 被摘要
[N..end] ← tail（按 token 预算 或 protect_last_n）
```
- 尾部保护是 **token 预算驱动**的：从末尾向前走，累积 token 直到预算耗尽。
- 预算（以及整行可超出的 1.5× 软上限）**在任何模型上都封顶在上下文窗口的 20%**——因此 `protect_last_n` **只是最小值**，且仅在小数目下限（8 行）内有效，**永不允许造出一个「无法给压缩留空间」的尾**。
- 边界会对齐以避免切断 `tool_call`/`tool_result` 组：`_align_boundary_backward()` 向前走过连续 tool results 找到父 assistant 消息。

**Phase 3 — 生成结构化摘要**
摘要模板是固定的分节结构：
```
## Goal / ## Constraints & Preferences / ## Progress（Done / In Progress / Blocked）
## Key Decisions / ## Relevant Files / ## Next Steps / ## Critical Context
```
摘要预算随被压缩内容缩放：`content_tokens × 0.20`，下限 2000 token，上限 `min(context_length × 0.05, 12000)`。

**⚠️ 官方明确点名的最大陷阱**【一手】：
> **The summary model must have a context window at least as large as the main agent model's.** … If the summary model's context is smaller, the API returns a context-length error — `_generate_summary()` catches it, logs a warning, and returns `None`. **The compressor then drops the middle turns without a summary, silently losing conversation context.** This is the most common cause of degraded compaction quality.

**「摘要模型窗口小于主模型 → 静默丢中间轮」**——这是设计自有压缩时最该避免的失败模式：**降级必须响亮，不能静默。**

**Phase 4 — 组装**
head（首次压缩时在 system prompt 追加一条说明）+ summary 消息（角色选择会避开连续同角色冲突）+ tail 原样。`_sanitize_tool_pairs()` 清理孤儿对：引用被删调用的 tool result 被移除；结果被移除的 tool call 注入 stub 结果。

**迭代式再压缩**【一手】：后续压缩时，**上一次的摘要会连同指令一起给 LLM，要求「更新」而非从头摘要**。这样信息可以跨多次压缩存活（条目从 In Progress 移到 Done 等）。

**（E）Param 默认值（可直接借鉴的一整套数字）**【一手】：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `compression.enabled` | `true` | |
| `compression.threshold` | `0.50` | 提示 token ≥ `threshold × context_length` 时触发；**窗口 <512K 时下限被抬到 0.75** |
| `threshold_tokens` | `null` | 可选绝对上限，取「比例触发」与它之中的较低者 |
| `model_thresholds` | `{}` | 按模型名子串匹配覆盖 threshold，**最长匹配胜出**；`"<provider>:<substring>"` 键只在对应 provider 生效。**小窗口下限仍然叠加（只抬不降）** |
| `target_ratio` | `0.20` | 仅 `legacy` tail 模式使用 |
| `tail_mode` | `lean` | |
| `protect_last_n` | `20` | 最小保留的近期消息数 |
| `min_tail_user_messages` | `1` | 保证至少 N 条**真实**用户消息留在未压缩尾部；**该保证优先于尾部 token 预算** |
| `protect_first_n` | `3` | **硬编码**：system prompt + 首个交换 |
| `idle_compact_after_seconds` | `0` | 空闲恢复时先压缩，0=关 |
| `in_place` | `true` | 同一 session id 原地压缩，不轮转 |

**`lean` 模式是最有意思的设计**【一手】：尾部保留被夹到 `2.5% × context window`（下限 10K，上限 25K），**转而把连续性放进摘要里**——一次摘要请求同时产出：

- 保留标识符的详细 session log
- **机械抽取的 anchor index**（PR 号、SHA、路径、错误字符串——**用正则，绝不改写**）
- **每一条真实用户消息的原文引用**（newest-first 预算）
- 一个 `session_search` 恢复指针，让 agent 能重新取回被摘要掉的东西

**实测效果**：500K token 的真实会话上，保留约 **49K**（对比 legacy 模式的约 **162K**），且在配合恢复机制时**召回率更高**。lean 尾部里的旧工具结果被降级成带恢复指针的单行 stub。

这是一个**「激进压缩 + 可靠回取」**的范式：不追求摘要保真，而追求**摘要 + 可检索原始记录**的组合。**对目标 harness 极具参考价值**——因为它把「压缩的信息损失」从不可逆变成了可逆。

**（F）压缩失败的工程处理**【一手】——这部分细节说明生产环境的真实复杂度：

- **失败冷却（failure cooldown）**：摘要尝试失败/停滞后，按会话武装一个**递增冷却**（60s → 300s → 900s，且不短于 `compression.context_timeout_seconds`，持久化在 `state.db`）。冷却期间，普通的阈值触发压缩被推迟，**避免一个坏掉的摘要后端每轮重复开火**。
- 超时与停滞共用一个计数器；以 `finish_reason=length` 结束的摘要（撞输出上限，transcript 被保留）走**自己的**计数器，同样 60→300→900 阶梯。
- JSON 解码、流关闭、空内容失败保持**平坦 30s 冷却**。
- **三条路径仍会真的尝试**：手动 `/compress`（`force=True`，清除冷却）；同轮 `fallback_chain` 重试（`bypass_cooldown`，因为被取消的主路由自己的停滞冷却不该压制它）；**provider 证实的溢出**（provider 自己以 context-length 错误拒绝请求时，恢复过程在**不清除冷却**的前提下忽略冷却**做一次有界尝试**）。官方理由：这里推迟会**把会话卡死**——每轮都会撞 provider，下一次失败还会拉长阶梯。
- **重复停滞 → 确定性兜底**：首次停滞保留 transcript、武装冷却、让 LLM 路由在冷却失效后重试；**当该路由再次停滞且 stalled 类失败仍在阶梯上时，重试阶梯以「确定性档位」收尾**：worker 在跳过摘要 LLM 的情况下重跑（`DETERMINISTIC_SUMMARY_ROUTE` pin），提交**静态兜底摘要**，走正常的 lease/fence/watermark 管线。
- **摘要 provider 过载 → 中止，保留 transcript**：`overloaded` / `at capacity` / HTTP 529，且一次性主模型重试也失败时，`compress()` **中止并不改动 transcript**。**持续过载会升级**：单会话内连续 3 次过载中止后，过载不再算终态，`compress()` 提交确定性兜底（`failure_class=summary_overload_degraded`）——官方理由：**一次有界的中间窗口损失，好过让 transcript 增长到 `compression_exhausted` 触发 gateway 自动重置、丢弃整个会话**。

**（G）原地压缩 `in_place: true`（默认）**【一手】：一次压缩**在同一 session id 上重写活跃消息列表**——system prompt 被重建、被摘要的中间被换入、压缩前的轮次被**软归档**在同一 id 下（session store 中 `active=0, compacted=1`），**仍可被 `session_search` 检索、可恢复、绝不删除**。**没有 `parent_session_id` 链，也没有 `name #N` 重编号；一次对话终生一个持久 id。**

官方明确说这**消除了 session 轮转的一整簇 bug**：丢失 `/goal` 状态、孤儿会话、跨边界的搜索缺口。

**（H）Micro-compaction（微压缩）—— 把压缩成本分期付款**【一手】：

思想：长会话终将超出窗口，必须丢弃或摘要。Hermes 传统上**一次批量做**：跨过阈值时会话停下、一次性摘要大块中间、然后继续。**代价一次性到期**——一次可见的停顿、一个大的摘要请求。

微压缩**把同一笔账分期付**：**每完成一轮后，把最久远的一个未吸收的 exchange 折进一个滚动摘要**。

- **每轮只吸收一个 exchange**，因此**无论会话多长，每轮成本保持有界**。
- **「exchange」= 一个完整 agent 轮次**：一条 assistant 消息 + 它的工具结果 + 后续 assistant 迭代，直到下一条 user 消息。选整个轮次（而非单个 assistant+tools 组）也保持了 transcript 的角色交替严格合法。
- **⚠️ 用户消息永不被压缩**——这是整个设计中最有用的性质。因为 exchange **刻意从 assistant 消息开始**，微压缩**直接跨过用户消息**。官方理由极为清晰：

> What the assistant produces is largely an account of what it did… That kind of narration survives summarising with very little loss… **Your instructions are a different kind of thing. They're the intent everything else is derived from, and they cannot be reconstructed from the work that followed. Paraphrasing "use the existing retry helper, don't add a new one" into a summary is exactly how an agent ends up confidently doing the thing you told it not to, six turns later.**
> **So the asymmetry is on purpose: compact the derived material, keep the source of truth.**

代价也被诚实承认：因为用户轮次累积且永不吸收，**中间能压到多小有一个下限**。

- **两个区域永不被触碰**：head（system prompt + 开场消息）与 tail（按 token 预算的最近窗口）。
- **游标（cursor）**：记录第一个未被吸收的消息索引。若内存游标缺失/越界（新进程、恢复会话），**通过扫描 transcript 找最后一个 summary marker 并紧随其后恢复**——**transcript 本身是事实来源**。
- **单一滚动摘要**（而非一堆 per-exchange 摘要）：每次把新 exchange 合并进去；累积性质使得**只有最新的 marker 需要保留在 transcript 里**，更早的 marker 严格冗余、被丢弃。官方指出这比听起来更重要：留着它们会**堆叠近乎重复的文本**，每份都带自己的标题和结束标记脚手架，导致 transcript **每轮增长而非收缩**。
- 丢弃 marker 会让两侧的用户轮次相邻，于是它们被**合并成一条 user 消息**给模型；该合并行被标记 `display_metadata.model_only`，**所有展示投影（resume、分页历史、prompt timeline）都跳过这个合并**，所以恢复的会话对每条输入只显示一次。
- **Defrag**：滚动摘要超过 token 阈值（默认 **2000**）时，下一轮做一次 defrag——**用一次辅助调用把滚动摘要自身重新摘要**成紧凑版本，原地重写 marker。**defrag 不触碰 transcript 结构**（不吸收、不拼接、游标不动、用户轮次不动）。
- **与 append-only 会话 DB 保持一致**：Hermes 常规 session flush 是 append-only 的，所以内存里的拼接不够——原始行会仍标记为 active，恢复时会**同时载入摘要和它替代的消息**，直接把会话顶过上下文上限。因此每轮还调用 `archive_and_compact`，**原子地软归档活跃行并插入压缩后的集合**；随后消息被打上「已持久化」戳，让随后的 append-only flush 跳过它们。该 DB 步骤失败只记日志、会话继续（代价：在下次批量压缩清理前，恢复会双重载入）。
- **摘要失败处理**：transcript 不动、失败计数。**同一 exchange 连续失败 3 次后，游标直接推过它**——否则一个坏 exchange 会**永远每轮重试**。被跳过的消息留在 transcript 里，由下次批量压缩接管。
- **微压缩不替代批量压缩，而是推迟它**：阈值压缩仍然在，窗口真的填满时仍会触发；两者的 marker 格式相同，可以互操作。

**微压缩的代价（官方主动、明确地劝退）**【一手】：

> **A micro-compaction pass rewrites already-sent history, which invalidates the prefix from the rewrite point onward — so with micro-compaction on, you break the cache every turn instead of once per batch compaction.**

| | Batch-only（默认） | Micro-compaction on |
| --- | --- | --- |
| 压缩停顿 | 阈值处一次长停顿 | 摊到各轮 |
| 上下文占用 | 锯齿式升到阈值 | 保持低位平坦 |
| **Cache prefix** | 两次压缩之间完好 | **每轮被打断** |

配置：`compression.micro_compact`（**默认 false，必须显式开启**）、`micro_compact_every_n_turns`（默认 1，小于 1 会被夹到 1 而非静默关闭）、`micro_compact_defrag_threshold_tokens`（默认 2000）。

**关于压缩模型的官方建议（反直觉，很有价值）**【一手】：

- **延迟主导**：因为每轮都跑，墙钟成本被反复感知。「A model that takes 30 seconds turns every turn into a turn plus 30 seconds.」
- **推理模型是糟糕选择**：把一个 exchange 合并进摘要**是机械工作**。「A thinking model will spend reasoning tokens on it and be substantially slower than a plain instruct model of similar size, for no benefit.」
- 实测示例（作者自述仅作形状参考）：7B 4-bit 本地 instruct（MLX/Apple Silicon）约 **31s/次**；大型 MoE 推理模型远程 GPU **明显更慢**。
- 结论：**小、快、非推理的 instruct 模型通常是正确形状；更大或「更聪明」的模型在这里往往更差而不是更好。**

**诚实的测量读数**【一手】：

- **会话的第一次 pass 通常花 token 而不是省 token**：插入 summary marker 有约 **400 token 的固定脚手架**，第一次只抵扣一个 exchange。首次显示 `tokens_delta: +330` **不是故障**。从第二次起 marker 是**被替换**而非新增，脚手架已付清。**盈亏平衡通常在第二或第三次 pass。**
- 该关注的指标是 **occupancy**（窗口被保持在压缩阈值的百分之几），以及**实际触发了多少次批量压缩**（理想为零）。官方原话：**「A session can save nothing on paper and still be a clear win on both counts.」**
- 遥测行**只含计数、不含任何 transcript 内容**：
```
micro compaction telemetry: {"event":"micro_compaction","outcome":"absorbed","tokens_before":12739,"tokens_after":12060,"tokens_delta":-679,"occupancy_pct":38.4,"threshold_tokens":34816,"context_limit":40960,...}
```
- 真实会话观测（3.5 小时整项目代码审查，75K token transcript，400K 窗口，阈值 320K）：占用爬到约 22% 后**停住**；最后两次 pass 消息数相同（84→80），期间会话新增 4,841 token 而微压缩回收 4,395 —— **这就是均衡**。**整场没有触发批量压缩。** 回收只在超出 tail 预算后才开始爬升（该例 tail 预算 64,000 token = 窗口 16%，所以早期几乎无可回收）。
- **失败行为：全程 best-effort**。`finalize_turn` 中的调用被包住，任何异常都记日志并吞掉——失败返回未改动的会话，轮次正常结束。「It can degrade, but it shouldn't be able to break a session.」

→ https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching 、 .../micro-compaction

**（I）Prompt caching（Anthropic）**【一手】：`agent/prompt_caching.py`，策略名 **`system_and_3`**，使用 Anthropic 的 `cache_control` 断点。官方称多轮对话中输入 token 成本可降约 **75%**。（该页在抓取时被截断，断点具体分布未完整取得，**部分未找到**。）

**（J）可插拔 ContextEngine**【一手】：上下文管理建立在 `ContextEngine` ABC（`agent/context_engine.py`）之上，内置 `ContextCompressor` 是默认实现，插件可替换（例如 Lossless Context Management）。选择由 `context.engine` 配置驱动：

解析顺序：`plugins/context_engine/<name>/` 目录 → 通用插件系统（`register_context_engine()`）→ 回退内置 `ContextCompressor`。

**关键约束：插件引擎永不自动激活**——用户必须显式把 `context.engine` 设成插件名。默认 `"compressor"` 永远走内置。

这三个方法定义了引擎契约：`should_compress()`（何时压缩）、`compress()`（如何压缩）、可选地暴露 agent 可调用的工具（如 `lcm_grep`）、以及从 API 响应跟踪 token 使用。

**设计取舍的双压缩阈值之一也值得记下**：`codex_gpt55_autoraise` 默认 `true` —— 因为 ChatGPT Codex OAuth 后端把 gpt-5.4/5.5/5.6 和 Astra **硬顶在 272K** 窗口（同一 slug 在 OpenAI 直连 API 上是 1.05M），在默认 50% 触发点会在约 136K 就压缩，「half the window the model can actually use」。因此在**恰好这条路由**上把阈值抬到 **85%**（约 231K），并显示一次带 opt-out 命令的通知（用 `$HERMES_HOME` 下的标记文件保证每个 profile 只提示一次）。**只影响这一条确切路由**；同样模型在任何其它 provider 上保持用户全局阈值。

**「按路由而非按模型名决定压缩阈值」** 是个重要教训：**模型名的上下文窗口不是一个稳定属性。**

### 3.4 子代理 / 多代理

**两个不同机制**【一手】：

**(1) `delegate_task` 工具** — 被 agent 级前置拦截，**生成带隔离上下文的子代理会话**。子代理有**独立迭代预算**（`delegation.max_iterations`，默认 50）。官方强调：**parent + subagents 的总迭代数可以超过 parent 自己的上限**。

官方宣传语：「Spawn isolated subagents for parallel workstreams. Programmatic Tool Calling via `execute_code` collapses multi-step pipelines into single inference calls.」以及中文站的「通过 RPC 将多步骤流水线压缩为零上下文消耗的操作」。
→ https://hermes-agent.nousresearch.com/docs/ （Key Features）

**(2) `/bg` 后台会话** — 在**独立的 agent 实例**中异步跑一个 prompt【一手】：

- **隔离会话**：后台 agent 有自己的会话与自己的对话历史。**它对你的当前聊天上下文一无所知，只收到你提供的那个 prompt。**
- **同配置**：继承你的 model、provider、toolsets、reasoning 设置与 provider routing。
- **非阻塞**：主聊天完全可交互。
- **结果投递**：完成后结果发回**发起命令的同一个 chat/channel**，前缀 `✅ Background task complete`（失败为 `❌ Background task failed` 带错误）。

→ https://hermes-agent.nousresearch.com/docs/user-guide/messaging/

**（3）Bot Mode（多机器人协作）**【一手，但本次仅取到概览级描述】：「Build a durable team of specialist Bots that work together in group chats and through `@mentions`」——每个 Bot 有自己的 model、memory、skills、routines 和 chats。
→ https://hermes-agent.nousresearch.com/docs/ （Bot Mode 链接指向 https://hermes-agent.nousresearch.com/docs/user-guide/bot-mode ，**该页本次未展开抓取，细节未找到**）

**（4）Mixture of Agents**：文档导航中存在 `/docs/user-guide/features/mixture-of-agents` 页面（**本次未展开，细节未找到**）。

**（5）Background review（后台自省）作为一个「受限 fork」**【一手】——这是很精巧的一种「子代理」用法，值得单独说：

每轮结束后，后台自省 review 会**悄悄保存记忆或更新技能**。它在**你的主模型**上运行（重放对话，因为已在 prompt cache 中预热，所以是便宜的 cache read）。可以改到更便宜的模型：

```yaml
auxiliary:
  background_review:
    provider: openrouter
    model: google/gemini-3-flash-preview   # auto（默认）= 主聊天模型
```

**关键设计**：当 review 指向一个**不同于主模型**的模型时，它**无法复用主模型的 prompt cache**，所以该 fork **自动重放一个紧凑的对话 digest**（近期轮次原文 + 更早内容的摘要），而非完整 transcript——**以最小化写入新缓存的量**。官方基准：便宜约 **3–5×**，且「memory capture 完全一致、skill capture 接近一致」。

**同模型 review 的推理努力强制继承**【一手】：用同一模型的 review **总是继承 parent 的 reasoning effort**，`auxiliary.background_review.reasoning_effort` 不覆盖它（无论 `auto` 还是显式指向 parent）。理由：**推理设置、system prompt、完整对话快照、工具定义在 fork 诞生时保持逐字节一致，这样 review 才能复用其 prompt-cache 前缀。只改 review 的思考等级会破坏这种一致性。**

**成本封顶**【一手】：`auxiliary.background_review.max_input_tokens` **封顶单次 review 所有 provider 请求重放输入 token 的总和**；循环在跨过它之前停止。未设置时，预算**从 review 模型解析出的上下文窗口派生**：窗口的 **75%**，**封顶 600,000** token —— 所以它对小本地模型也生效（65,536 的模型得到 49,152），而固定的云端默认值在那里永远不会咬到。窗口无法解析时用保守的 **120,000** 回退。

**本地模型的 GPU 让步机制 `defer`**【一手】：在云端 provider 上 review 几秒完成、与你接下去做的事并行。但当 review 的运行时是**托管的本地 llama-server** 时，同一个 fork **占用你下一个 prompt 需要的 GPU**（大模型上要几分钟），且**发送新 prompt 会取消它、丢弃学习成果**。因此在托管本地运行时上，review **默认被推迟**：在轮次结束时排队，等机器安静一个短 settle 窗口后再执行。`defer: auto`（默认）| `never`；`defer_max_age_s` 默认 1800。排队 review **按会话合并**（新一轮的快照替换旧的——因为 review 重放整个对话，所以不会丢东西），被新 prompt 抢占的 review **重新排队而非丢弃**，等待超过 `defer_max_age_s` 的 review **即使机器永不空闲也会执行**。队列在内存中：应用退出时仍待处理的 review 被丢弃。

**可放行的额外工具白名单**【一手】：review 默认可用 memory、skill-management 和只读文件工具。若要放行另一个对无人值守 review 安全的工具，**按名字显式 opt-in**：`auxiliary.background_review.extra_tools: [propose_shared_memory]`。该工具必须已对 parent agent 可用；**此设置只是把它加入 review fork 的运行时白名单，不会启用任意工具，未列出的工具仍被拒绝**。官方建议保持清单狭窄，优先放行「生成提案供人审阅」而非直接施加外部/破坏性变更的工具。**默认空列表。**

**（6）Programmatic Tool Calling (`execute_code`)**【一手，仅概览】：把多步流水线折叠进单次推理调用，宣称「zero context consumption」的操作。**细节未找到。**

→ https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop 、 .../user-guide/features/memory 、 .../user-guide/messaging/

### 3.5 决策与规划

**Plan 是一个内置 slash 命令**【一手】（原文出自 Skills 页的括号说明）：

> `/plan [request]` tells Hermes to inspect context if needed, **write a markdown implementation plan instead of executing the task**, and save the result under `.hermes/plans/` relative to the active workspace/backend working directory.

设计要点：**计划被落盘为文件**（`.hermes/plans/`），而不是只留在对话里。这使它可被后续会话、其它工具和人类直接读取与版本化。

**TODO 机制**【一手】：`todo_list` 是被 agent loop 前置拦截的四个 agent 级工具之一（另有 `memory`、`session_search`、`delegate_task`），它读写 **agent-local task state**（TodoStore）。

**任务分解**：除 `todo_list` 外，没有独立的「任务分解器」组件（**未找到**），分解能力体现在 `delegate_task`（把工作交给子代理）与 cron（把工作排到未来）。

→ https://hermes-agent.nousresearch.com/docs/user-guide/features/skills 、 .../developer-guide/tools-runtime

### 3.6 成本控制

已散见于上文，汇总要点【均为一手】：

| 手段 | 机制 |
| --- | --- |
| **迭代预算** | `IterationBudget` 默认 500；subagent 独立预算默认 50 |
| **模型 fallback** | `fallback_providers` 列表依次降级；vision/compression/web extraction 各有独立 `auxiliary.*` fallback 链 |
| **辅助任务降级到便宜模型** | `auxiliary.compression.model/provider`、`auxiliary.background_review.model` |
| **同模型 fork 的 digest 策略** | 换模型后自动改用紧凑 digest，避免小量写入一个新缓存前缀 |
| **review 成本封顶** | `auxiliary.background_review.max_input_tokens`（未设则由上下文窗口 75%、封顶 600K 派生） |
| **可整体关闭自动 review** | `auxiliary.background_review.enabled: false`（手动 `/refine` 仍可用）——官方承认「The review fork can burn a meaningful share of total tokens on busy hosts」 |
| **调低频次** | `memory.nudge_interval` / `skills.creation_nudge_interval` |
| **Prompt caching** | Anthropic `cache_control`，策略 `system_and_3`，官方称多轮输入成本降约 75% |
| **本地 GPU 让步** | `auxiliary.background_review.defer`，避免 review 与用户抢 GPU 并被取消 |
| **token 遥测** | 每轮微压缩输出一行无内容 JSON 遥测（仅计数）；`session_model_usage` 表以 `task='background_review'` 持久化 fork 用量；`agent.log` 写完成行 |

**很值得借鉴的一点**：Hermes **把小模型路由做成了「按任务类型」而非「按 agent 类型」**——compression、vision、web extraction、background review 各自独立配置 provider/model/fallback。这与 opencode 的「按 agent 配模型」是两种不同切法，可以并存。

### 3.7 权限与安全

Hermes 的官方安全模型是**八层纵深防御**（原文列举）【一手】：

1. **用户授权** — 谁能跟 agent 说话（allowlist、DM pairing）
2. **危险命令审批** — 破坏性操作的人机回路
3. **文件写入安全** — `write_file`/`patch` 的 denylist 与可选写沙箱
4. **容器隔离** — Docker/Singularity/Modal 沙箱 + 加固设置
5. **MCP 凭据过滤** — MCP 子进程的环境变量隔离
6. **上下文文件扫描** — 项目文件中的提示注入检测
7. **跨会话隔离** — 会话不能访问彼此的数据或状态；cron 存储路径对路径穿越加固
8. **输入净化** — 终端后端的工作目录参数按 allowlist 校验以防 shell 注入

**（A）危险命令审批**【一手】：

审批模式 `approvals.mode`：**`smart`（默认）| `manual` | `off`**。

- `smart`：**用辅助 LLM 评估风险**。低风险命令（如 `python -c "print('hello')"`）**仅对该次命令**自动批准；真正危险的自动词拒绝；不确定的升级为手动提示。
- `manual`：总是提示。
- `off`：等于 `--yolo`。

**审批超时默认 300s，超时即拒绝（fail-closed）**【一手】。而且有一个很细的规定：

> An expired prompt cannot be reopened: the pending entry is discarded and the agent is told **not to retry on its own within that turn**. … **A timeout is not counted as a denial, so asking again is never penalized.**

**按上下文分档的无人值守策略**【一手】——这对 QQ bot 极其关键：

| 键 | 默认 | 控制什么 |
| --- | --- | --- |
| `cron_mode` | `deny` | cron 作业无头触发危险命令提示时：`deny` 阻断（agent 必须另找路径），`approve` 自动批准 |
| `single_query_mode` | `deny` | 一次性 `hermes chat -q` 会话（跑一轮就退出，没有用户在等）：同上 |
| `unattended_mode` | `deny` | 无人值守的程序化平台（webhook、msgraph_webhook、api_server）：**`deny` 会立刻阻断而不是阻塞整个审批超时**，因为这些面没有能回 `/approve` 的人 |

**「无人值守面立刻拒绝而不是等超时」是一个重要的区分**：没有人的地方，等待没有意义，只会浪费 300 秒。目标 harness 必须为「群聊里没人有权限/没人看」的情况定义明确策略。

**硬底线黑名单（不可覆盖的 floor）**【一手】：有些命令危险到 Hermes **无论** `--yolo`、`approvals.mode: off`、cron `approve` 模式、用户点「always allow」都拒绝执行。它在审批层**之前**触发，没有覆盖开关。当前覆盖（非穷尽，与 `tools/approval.py::UNRECOVERABLE_BLOCKLIST` 同步）：

| 模式 | 为何是硬线 |
| --- | --- |
| `rm -rf /` 及明显变体 | 抹掉文件系统根 |
| `rm -rf --no-preserve-root /` | 「是的我就是要根」变体 |
| `:(){ :\|:& };:`（bash fork bomb） | 打满主机直到重启 |
| 在已挂载根设备上 `mkfs.*` | 格式化运行中的系统 |
| `dd if=/dev/zero of=/dev/sd*` | 清零物理磁盘 |
| 在 rootfs 顶层把不可信 URL 管道给 `sh` | RCE 攻击面太宽无法审批 |

**fail-closed 的补充**：shell 引号无法解析的命令（如 `grep 'unterminated`）也走 floor，错误信息为 `malformed executable payload`。

**（B）用户自定义 deny 规则 `approvals.deny`**【一手】——硬底线黑名单是代码写死的，这是它的用户可编辑对等物：一串 glob 模式，**无条件阻断匹配的终端命令，且在 `--yolo`、`/yolo`、`approvals.mode: off` 之前被咨询**。用途是「yolo with exceptions」：

```yaml
approvals:
  deny:
    - "git push --force*"
    - "*curl*|*sh*"
    - "dd if=* of=/dev/*"
```

- 模式是 **fnmatch glob**（`*`、`?`、`[...]`），**大小写不敏感**，匹配**整个命令文本和各个可执行命令候选**。
- 匹配在**与危险模式检测器相同的归一化/去混淆变体**上运行，所以简单引号把戏（`git pu""sh --force`）不能绕过。
- 可执行候选**保留字面路径并同时匹配其 basename**：`sudo *` 覆盖 `/usr/bin/sudo -n id` 和 `./sudo -n id`；但路径特定的 `/usr/bin/sudo *` **不会**变成对每个叫 `sudo` 的二进制的规则。
- **引号感知解析**能暴露位于赋值、前导重定向、`;`、`&&`、`||`、管道、分组、命令替换，以及普通 `if`/`then`/`else`/`do` 转换之后的命令。支持的 launcher 包括 `sudo`、`env`、`command`、`exec`、`nohup`、`setsid`、`time`、`nice`、`timeout`、`stdbuf`、`ionice`、`chrt`、`taskset`、`chroot`。shell `-c` payload 被**递归检查**。
- **YAML 引号警告**：永远给模式加引号——裸的前导 `*` 是 YAML 别名会解析失败。
- 用户自定义 deny 规则**适用于所有终端后端，包括隔离容器，且在任何后端特定审批捷径之前**。
- 被拒命令返回 **BLOCKED 错误，告诉 agent 不要重试或改写**。

**官方主动声明的威胁模型边界（非常重要）**【一手】：

> Deny rules are **a shell-command policy, not a complete shell interpreter or an OS capability sandbox**. Normalization does not resolve arbitrary variables (including GNU `env -S` `${NAME}` expansion), aliases, functions, **renamed binaries**, scripts, interpreter programs, or every shell/launcher grammar… **Do not use a basename deny rule as a guarantee that a capability cannot be reached by other means. For containment, use OS permissions and an isolated backend** with appropriately restricted mounts, credentials, and network access.

**（C）文件写入安全**【一手】：

- **永久阻断的受保护路径**：OS 凭据库（`~/.ssh/`、`~/.aws/`、`~/.kube/`、`/etc/sudoers`、`~/.netrc`）；Hermes 秘密存储（`.env`、`.anthropic_oauth.json`、Bitwarden 缓存、`vault/`、`browser-profile/`、`mcp-tokens/`、`pairing/`）。
- **Windows NT/设备命名空间路径**（`\??\...`、`\\.\...`、`\\?\UNC\...`、`\\?\GLOBALROOT...`）**读写都拒绝**。官方给出的理由很有教育意义：在 Windows 上**仅仅解析**这种路径（如 `\??\UNC\host\share`）就会触发**出站 SMB 认证，可能泄露用户 NTLM hash**。
- 项目内的 `.env`、`.env.local`、`.env.production`、`.envrc` **全盘 read-denied**（文件工具拒绝读取），**但保持可写**：「agent 可以为你创建或编辑它们，只是无法读回其值」。**这是很聪明的折中**——agent 需要能配置环境，但不需要看到密钥。
- `~/.ssh/config` **是审批门控而非硬阻断**（它不含私钥材料，编辑它是日常工作，如 host alias、ProxyJump、VS Code Remote-SSH）；但它仍可能携带 `ProxyCommand`/`Match exec` 指令执行命令，**所以写入永不静默**。非交互调用方（ACP 文件桥、无人类通道的后台作业）**fail closed**。私钥、`authorized_keys` 与 `~/.ssh/` 下其它一切仍硬阻断。
- **`HERMES_WRITE_SAFE_ROOT`（可选写沙箱）**：设置后 `write_file`/`patch` **只能**指向列出的目录前缀，之外**硬阻断**（不走危险命令审批）。多根用 `:`（Unix）或 `;`（Windows）分隔。官方 Docker 镜像自动设为 `/opt/data`。
- **官方明确声明的边界（诚实）**：

> **Defense-in-depth, not a hard boundary.** Write guards apply to `write_file` and `patch` **only**… **The `terminal` tool runs as the same OS user and can still `cat` or overwrite denied paths via shell commands.** The denylist reduces accidental damage and gives models a clear stop signal; **it does not sandbox a hostile or compromised agent.**

**（D）容器隔离**【一手】——Docker 后端的加固参数（`tools/environments/docker.py`）：

```python
_BASE_SECURITY_ARGS = [
    "--cap-drop", "ALL",                      # 丢弃全部 Linux capabilities
    "--cap-add", "DAC_OVERRIDE",              # root 可写 bind-mount 目录
    "--cap-add", "CHOWN",                     # 包管理器需要文件属主
    "--cap-add", "FOWNER",                    # 包管理器需要文件属主
    "--security-opt", "no-new-privileges",    # 阻止权限提升
    "--pids-limit", "256",                    # 限制进程数
    "--tmpfs", "/tmp:rw,nosuid,size=512m",    # 限量的 /tmp
    "--tmpfs", "/var/tmp:rw,noexec,nosuid,size=256m",
]
```

官网补充的容器加固要点：**只读根目录、权限降级、PID 限制**。
→ https://hermes-agent.org/zh/ （中文站【二手】描述的容器安全加固）

**容器内跳过危险命令检查（重要取舍）**【一手】：

> **Container bypass**: 当运行在 `docker`、`singularity`、`modal`、`daytona`、`vercel_sandbox` 后端时，危险命令检查被**跳过**，因为**容器本身就是安全边界**。容器内的破坏性命令伤不到宿主机。

这确立了一个清晰的原则：**安全边界只能有一处，且必须明确是哪一处。** 有容器就不要再在命令层做假的安全感。

**（E）监督网关的生命周期护栏（不可覆盖）**【一手】：终端工具有一个独立的、**不可覆盖的**护栏：防止从它自己受监督的进程内部停掉或重启 gateway（自我重启可能在工具完成前终止它，并导致 supervisor/自动恢复循环）。**用户批准、YOLO 模式、`force=True` 都不绕过这个护栏。** 它还拒绝针对「gateway 运行所依赖的解释器镜像」的进程杀手——`taskkill /F /IM python.exe`、`pkill -9 python3`、`killall python`，以及 `pgrep python | xargs kill` 这类派生杀手，因为**受监督的 gateway 字面上就是一个 `python` 进程**。而作用域限定在 agent 自己拥有的进程上的 kill 是放行的（`proc_*` 句柄，或显式 PID）。

**（F）用户授权（Gateway 层）**【一手】——对 QQ bot 场景是核心内容：

授权检查顺序（`_is_user_authorized()`）：
1. 平台级 allow-all 标志（如 `DISCORD_ALLOW_ALL_USERS=true`）
2. **DM pairing 批准名单**
3. 平台级 allowlist（如 `TELEGRAM_ALLOWED_USERS=12345,67890`）
4. 全局 allowlist（`GATEWAY_ALLOWED_USERS=...`）
5. 全局 allow-all（`GATEWAY_ALLOW_ALL_USERS=true`）
6. **默认：拒绝**

> ⚠️ 官方警告：**If no allowlists are configured and `GATEWAY_ALLOW_ALL_USERS` is not set, all users are denied.** 启动时会记警告日志。

**DM Pairing 系统**【一手】（设计参考了 OWASP + NIST SP 800-63-4）：

- 未知用户 DM → bot 回复 **8 字符配对码** → 拥有者在 CLI 跑 `hermes pairing approve <platform> <code>` → 该用户永久批准
- 码格式：32 字符无歧义字母表（**不含 0/O/1/I**）
- 随机性：**密码学随机**（`secrets.choice()`）
- 码 TTL：**1 小时**
- 限流：**每用户每 10 分钟 1 次请求**
- 待处理上限：每平台最多 **3 个**待批码
- 锁定：**5 次失败批准尝试 → 1 小时锁定**
- 文件安全：所有配对数据文件 `chmod 0600`
- 日志：**码永不写入 stdout**

**未授权 DM 的处理策略 `unauthorized_dm_behavior`**：`pair`（聊天类 DM 平台默认）/ `ignore`（静默丢弃）/ `decline`（发一句简短礼貌拒绝，如「我只能和我的主人聊天」，然后**对该发送者 24 小时内忽略后续消息**）。Email 默认 `ignore`（因为收件箱可能有不相关的未读邮件）。**平台段覆盖全局默认。**

**Admin / 普通用户分档**【一手】——allowlist 回答「这人能不能碰到 bot」，admin/user 分档回答「进来之后他能干什么」：

- **Admin**：完整访问，可运行所有已注册 slash 命令（内置 + 插件）与所有门控能力
- **普通用户**：可正常与 agent 对话，但**只能运行你显式启用的 slash 命令**；**永远允许的地板是 `/help` 和 `/whoami`**
- 分档**按平台、按作用域（DM vs 群/频道）独立配置**——**DM 的 admin 身份不蕴含群/频道 admin 身份**
- 目前分档门控的是 **slash 命令**；官方明说**未来可能把更多能力面（工具访问、模型切换、昂贵操作）挂到同一个 admin/user 区分上**，所以现在配置好这个分档可以让未来的限制平滑落地
- 向后兼容：**若某作用域未设置 `allow_admin_from`，该作用域的分档被禁用，每个允许的用户都有完整访问**——既有安装无需改动

**（G）跨会话隔离**【一手】：会话不能访问彼此的数据或状态；cron 作业存储路径对路径穿越加固。

**（H）MCP 凭据过滤**【一手】：MCP 子进程的环境变量隔离（细节未完全展开，**部分未找到**）。

**（I）上下文文件扫描**【一手】：项目文件中的提示注入检测（第 6 层，细节未完全展开）。

**（J）当前工作目录输入净化**【一手】：终端后端的工作目录参数按 allowlist 校验以防 shell 注入。

→ https://hermes-agent.nousresearch.com/docs/user-guide/security

### 3.8 扩展机制

**（A）插件系统：三个发现来源**【一手】：

1. `~/.hermes/plugins/`（用户）
2. `.hermes/plugins/`（项目）
3. **pip entry points**

插件通过**context API** 注册**工具、hook 和 CLI 命令**。

**两类专门插件，且都是「单选」**【一手】：

- **memory providers**（`plugins/memory/`）
- **context engines**（`plugins/context_engine/`）

> Both are single-select — **only one of each can be active at a time**.

「单选」是一个明确的设计决策：**记忆后端与上下文引擎是全局策略，不是可叠加的能力。**

**（B）Hook 系统**【一手】：`gateway/hooks.py` 负责 hook 发现与生命周期事件；插件 hook 名至少包括 `pre_tool_call` / `post_tool_call`（见工具调度流程）。`gateway/builtin_hooks/` 是「始终注册的 hook」的扩展点（**当前未随发行版附带任何内置 hook**）。

**（C）MCP 集成**【一手】：`tools/mcp_tool.py` 是 MCP 客户端门面，**MCP 工具是动态 toolset**——在核心工具发现之后，`tools.mcp_tool_discovery.discover_mcp_tools()` 读取 MCP server 配置并注册来自外部服务器的工具。文档有专门的《MCP Integration》与《Use MCP with Hermes》指南，并强调「connect to MCP servers, **filter their tools**, and extend Hermes safely」。

**（D）Skills（与 agentskills.io 兼容）**【一手】：

- **单一事实来源**：`~/.hermes/skills/`。首次安装时从仓库复制 bundled skills；Hub 安装与 agent 自建的 skills 也都放这里。**agent 可以修改或删除任何 skill。**
- **兼容开放标准**：与 `agentskills.io` spec 兼容（该标准由 Anthropic 于 2025-12-18 发布为开放标准——见第 4 节）。
- **渐进式披露三级**【一手】——很适合直接借用：
```
Level 0: skills_list()          → [{name, description, category}, ...]  (~3k tokens)
Level 1: skill_view(name)       → 全文 + 元数据
Level 2: skill_view(name, path) → 特定 reference 文件
```
- **每个已安装 skill 自动成为一个 slash 命令**。
- **可叠加调用**：消息开头连续的 `/skill` token（**最多 5 个**）全部加载，其余为指令。**解析在第一个不是已安装 skill 的 token 处停止**，所以碰巧以 `/` 开头的参数（如文件路径）永远不会被吞掉。这个边界条件处理得很仔细。
- **条件激活（fallback skills）**——很聪明的设计：
```yaml
metadata:
  hermes:
    fallback_for_toolsets: [web]      # 仅当这些 toolset 不可用时显示
    requires_toolsets: [terminal]     # 仅当这些 toolset 可用时显示
    fallback_for_tools: [web_search]
    requires_tools: [terminal]
```
官方例子：内置 `duckduckgo-search` skill 用 `fallback_for_toolsets: [web]`——有 `FIRECRAWL_API_KEY` 时 web toolset 可用，agent 用 `web_search`，DuckDuckGo skill **保持隐藏**；缺 key 时 web toolset 不可用，DuckDuckGo skill **自动出现作为兜底**。
**这是「免费/本地兜底能力」的优雅实现**：不需要用户手动切换，能力可用性自动决定哪个 skill 出现。
- **平台限定**：`platforms: [macos, linux, windows]`；设置后在不兼容平台上**自动从 system prompt、`skills_list()` 和 slash 命令中隐藏**。
- **安全设置即加载（Secure Setup on Load）**：skill 可声明所需环境变量：
```yaml
required_environment_variables:
  - name: TENOR_API_KEY
    prompt: Tenor API key
    help: Get a key from https://developers.google.com/tenor
    required_for: full functionality
```
缺失时**只在本地 CLI 中按需安全询问**（可跳过并继续用）。**消息平台永不在聊天里索取密钥**——它们告诉你用 `hermes setup` 或本地 `~/.hermes/.env`。一旦设置，声明的环境变量**自动透传给 `execute_code` 与 `terminal` 沙箱**。
- **非秘密配置项**也可由 skill 声明，存在 config.yaml 的 `skills.config` 下，skill 加载时其解析后的值被注入上下文。
- **外部 skill 目录**：`skills.external_dirs` 可加多个目录（支持 `~` 展开和 `${VAR}` 替换），便于与 `~/.agents/skills/` 之类跨工具共享目录共存。**本地优先**（同名时本地版本胜出）。**不存在的路径被静默跳过**（便于可选的共享目录）。官方明确警告：**外部目录不是写保护边界**——若 Hermes 进程对其可写，agent 管理的 skill 更新可以改动其中文件；要只读请用文件系统权限或独立 profile。
- **项目本地 skill**：`<project-root>/.hermes/skills/` 与 `<project-root>/.agents/skills/`（后者是跨工具约定，与其它 agent CLI 共享）。项目根 = 最近含 `.git` 的祖先目录（worktree 与 submodule 也算）。
- **必须显式信任项目**【一手】——这是很好的安全设计：
```
◆ 3 project skill(s) found in /home/you/myproject but not loaded — run `hermes skills trust` to enable them.
```
理由：「skills are procedure documents the agent follows, so Hermes does **not** auto-load them from arbitrary cloned repos」。信任根存在 `skills.trusted_project_dirs`；`skills.project_discovery: false` 可整体关闭。
- **优先级**：`project → local (~/.hermes/skills/) → external_dirs`。项目 skill 是**最高优先级层**，同名时在其「主场」覆盖 profile 或 bundled skill；项目 skill 在 agent 的 skill 索引中被标记 `[project]` 以保持来源可见。
- **扫描期隔离（Scan-time quarantine）**——很关键的一层：信任是仓库级决策，但**仓库的 skill 内容随每次 `git pull` 变化**。为堵这个缺口，每个项目 skill 在进入索引前都跑**与 Skills Hub 安装相同的安全扫描器**。判定为 **dangerous** 的 skill 被隔离：**不出现**在 skill 索引、`skills_list`、slash 命令中，且按名字加载会被带解释的错误拒绝。扫描结果按内容 hash 缓存在 `~/.hermes/cache/project_skill_scans/`（**绝不在你的仓库里**），内容变化时自动重跑。
- **非交互面继承信任决策**：cron 与其它非交互面**继承**你的交互式信任决定——**永不提示、永不自动信任**。
- **Hub 安装的供应链安全**【一手】：第三方 URL 与 GitHub 安装会带上 `SKILL.md` 及它引用的确切本地文件（`references/`、`templates/`、`scripts/`、`assets/`、`examples/`）；**未被引用的仓库文件不会被复制**。Hermes 扫描整个隔离包，并在 `skills/.hub/lock.json` 记录**源 URL、精确内容 hash、扫描器版本、发现项、时间戳、fresh-or-cached 状态**。
- **可选的 NVIDIA SkillEvaluator Tier 1 顾问式扫描**【一手】：确定性、无需 key——PII 检测（泄露的邮箱、个人路径、连接串）、unicode 走私检测、脚本 lint、许可证合规，以及经 NVIDIA SkillSpector 的静态安全扫描。**仅顾问性质**：发现项带文件与行号打印在安装确认之前，安装继续。看起来像真实凭据的发现项用红色高亮。PII 类发现是信息性的——上游扫描器有已知误报类（如 `git@github.com` SSH 语法、文档示例邮箱），**所以它们从不阻断**。可通过 `skills.tier1_advisory: false` 关闭。
- **重定向 skill 创建位置 `skills.create_dir`**：想让 agent 自建的 skill 落到别处（共享「大脑」目录、git 跟踪的仓库、全机队 skill 卷）时可配。**关键在于「agent 的指令跟随配置」**：每个面向 agent 的、提到 skill 创建路径的指令（`skill_manage` 工具描述与相关 prompt 文本）都会**动态渲染配置的目录**——所以 agent 被告知去那里创建，**不需要 system prompt 覆盖或文件系统把戏**。

**（E）记忆系统（分层记忆）**【一手】——这是 Hermes 最独特的机制：

**两个文件构成 agent 的记忆**：

| 文件 | 用途 | 字符上限 |
| --- | --- | --- |
| **MEMORY.md** | agent 的个人笔记——环境事实、约定、学到的东西 | **2,200 字符**（约 800 token） |
| **USER.md** | 用户画像——偏好、沟通风格、期望 | **1,375 字符**（约 500 token） |

两者存于 `~/.hermes/memories/`，并**在会话开始时作为冻结快照注入 system prompt**。

**冻结快照模式（Frozen snapshot pattern）**【一手】——与 prompt cache 直接相关：

> The system prompt injection is captured once at session start and **never changes mid-session**. This is intentional — **it preserves the LLM's prefix cache for performance**. 当 agent 在会话中增删记忆条目时，改动**立即持久化到磁盘**，但**要到下个会话开始才出现在 system prompt 中**。工具响应始终反映实时状态。

**记忆工具的三个动作**：`add` / `replace` / `remove`。**没有 `read` 动作**——记忆内容在会话开始时自动注入 system prompt，agent 把记忆当作自己对话上下文的一部分看到。

**substring 匹配**：`replace`/`remove` 用短的唯一子串匹配（`old_text`），不必给全文。若匹配到多条则报错要求更具体。**注意**：`replace` **整条覆盖**匹配到的条目——`old_text` 只用于定位，不会被挖掉替换；新 `content` 必须是完整的新条目。

**字符上限的强制方式（很值得学）**【一手】：**记忆不自动压缩**。当写入会超限时，**`memory` 工具返回错误而不是静默丢条目**：

```json
{
  "success": false,
  "error": "Memory at 2,100/2,200 chars. Adding this entry (250 chars) would exceed the limit. Consolidate now: use 'replace' to merge overlapping entries into shorter ones or 'remove' stale or less important entries (see current_entries below), then retry this add — all in this turn.",
  "current_entries": ["..."],
  "usage": "2,100/2,200"
}
```

**即：把「腾地方」变成 agent 在同轮内必须完成的显式动作**，并且在 system prompt 头部显示用量百分比与字符数让它知道容量。官方最佳实践：容量超 80% 时先合并再新增。官方还指出一个细节：**`replace` 同样受限**——把条目换成更长的仍可能溢出，所以新内容必须缩短或另删条目。

**记忆条目的安全扫描**【一手】：记忆条目在被接受前会**扫描注入与渗出模式**，因为它们会被注入 system prompt。匹配威胁模式（提示注入、凭据渗出、SSH 后门）或含**不可见 Unicode 字符**的内容被阻断。

**记忆需要会话边界（官方的诚实反模式警告）**【一手】：

> The whole memory system is built around the moment a session **ends**… **Inside a single session none of that machinery has a reason to run** — everything important is still in the live context, so the agent rarely consults `session_search` and mostly compacts memory entries instead of curating them.
> 这在消息平台上尤其重要，因为一个 chat 是**有意设计成一个连续会话**，能在重启、网关崩溃、机器重启后存活。**关掉机器过夜不会结束该会话。**
> 若你从不重置，一个 chat 可以**作为单个会话跑数周**：方便，但**越来越贵**（压缩在一个越来越长的历史上反复运行），而且 `forget → recall from memory → search past sessions` 这个学习回路**几乎永远不会触发**。新写入的记忆条目也因冻结快照而对运行中的会话不可见。
> **Practice: run `/new` at natural boundaries** — 一个任务完成、话题切换、一天开始。

**这是一个极有价值的、被官方明确承认的设计张力**：常驻消息平台的「连续会话」体验与「记忆/学习回路的有效性」直接冲突，而 Hermes 选择了**把边界交给用户**（`/new`），并诚实说明代价。**目标 harness 必须对这个问题有自己的答案。**

**会话检索 `session_search`（与记忆互补）**【一手】：

- 所有 CLI 与消息会话存在 SQLite（`~/.hermes/state.db`），带 **FTS5 全文检索**
- 查询返回**数据库里的真实消息——没有 LLM 摘要、没有截断**
- agent 可以找到几周前讨论过的东西，即使不在活跃记忆中
- agent 还能在找到的任何会话内**前后滚动**
- 约 **20ms** FTS5 查询，约 **1ms** 滚动

| 特性 | 持久记忆 | 会话检索 |
| --- | --- | --- |
| 容量 | 约 1,300 token 总量 | 无限（所有会话） |
| 速度 | 即时（在 system prompt 里） | 约 20ms FTS5 |
| 成本 | **每个 prompt 都付 token** | **免费——无 LLM 调用** |
| 用途 | 始终可用的关键事实 | 找特定过往对话 |
| 管理 | agent 手动策展 | 自动——所有会话都存 |
| token 成本 | 每会话固定（约 1,300） | 按需 |

**「记忆 vs 检索」的这个二象限划分非常清晰**：固定成本常驻 vs 零成本按需。**对目标 harness 是直接可用的架构。**

**记忆写入审批 `memory.write_approval`**【一手】：默认 `false`（自由写，包括每轮后的后台自省 review）。设 `true` 时：交互式 CLI 中前台写入**内联提示**（条目够小能全读）；**其它所有地方**（消息平台、脚本、后台 review）写入被**暂存**待审：

```
/memory pending             # 列出暂存写入（自动的标 [auto]）
/memory approve <id>        # 应用一条（或 all）
/memory reject <id>         # 丢弃一条（或 all）
/memory approval on|off     # 开关并持久化
```

官方称之为「『agent 存了一条关于我的错误假设』的答案」。一个精细设计：暂存的 `replace`/`remove` **记录它瞄准的完整条目**并在 `/memory pending` 中显示；批准**只对被瞄准的那一条生效**——**若该条目在暂存后发生了变化，写入被拒绝并保持 pending 待你驳回**。

**后台 review 的通知控制** `display.memory_notifications`：`off` | `on`（默认，一行 `💾 Memory updated`）| `verbose`（含变更预览，如 `💾 Memory 加 User prefers terse replies` 或 `"old" → "new"` 的 skill diff 片段）。**只治理 gateway 聊天通知，不影响 review 本身或写入。**

**技能写入审批 `skills.write_approval`**【一手】：同样 on/off 门，但 review UX 不同，因为 `SKILL.md` 太大不适合塞进聊天气泡——开启时 skill 写入（create / edit / patch / write_file / delete）**总是暂存**，你审阅一行 gist，完整 diff 走带外：`/skills pending`、`/skills diff <id>`、`/skills approve <id>`、`/skills reject <id>`、`/skills approval on|off`；暂存文件在 `~/.hermes/pending/skills/<id>.json`。

**外部记忆 provider**【一手】：内置记忆之外，还附带 5 个外部 memory provider 插件（OpenViking、Mem0、Holographic、RetainDB、ByteRover），插件目录中另有 Honcho、Hindsight、Supermemory 等。**外部 provider 与内置记忆并行运行（绝不替代）**，增加知识图谱、语义搜索、自动事实抽取、跨会话用户建模等能力。

**⚠️ 官方的重要运维警告**【一手】：

> **One agent per Hermes home** — 不要把两个 agent 进程指向同一个 Hermes home 目录。记忆写入是自动的，且会在会话开始时载回 system prompt，因此**两个写者共享一个 home 会把彼此的条目复合成谁（包括你）都没写过的状态**。记忆按 profile 隔离是**设计如此**——给第二个 agent 自己的 profile；若需要共享记忆，用外部记忆 provider。

**（F）Cron（一等 agent 任务）**【一手】：官方强调 cron 是**一等 agent 任务（不是 shell 任务）**。作业存在 JSON 中，支持多种调度格式，可附加 skills 与 scripts，能投递到任何平台。数据流：

```
Scheduler tick → 从 jobs.json 载入到期作业
  → 创建全新 AIAgent（无历史）
  → 注入附加 skills 作为上下文
  → 运行作业 prompt
  → 投递响应到目标平台
  → 更新作业状态与 next_run
```

注意「**创建全新 AIAgent（无历史）**」——cron 作业**不继承对话历史**，上下文完全来自附加的 skills。**这是刻意的隔离**。

调度器每 **60 秒** tick 一次。

**（G）ACP 集成**【一手】：通过 stdio/JSON-RPC 把 Hermes 暴露为编辑器原生 agent，支持 VS Code、Zed、JetBrains。
**（H）Trajectories**【一手】：从 agent 会话生成 **ShareGPT 格式**轨迹用于训练数据生成；另有批量处理（并行生成数千条工具调用轨迹）、RL 训练（集成 Atropos，11 种工具调用解析器）、轨迹压缩（把训练数据压进 token 预算）。

**（I）Profile 隔离**【一手】：每个 profile（`hermes -p <name>`）有自己的 HERMES_HOME、config、memory、sessions 和 gateway PID。**多个 profile 可并发运行。**

**（J）消息平台适配器的两种形态**【一手】——这个区分对目标 harness 很有指导性：

- **内置适配器**（`gateway/platforms/`）：`signal`、`weixin`、`bluebubbles`、**`qqbot`**、`whatsapp_cloud`、`yuanbao`、`webhook`、`api_server`
- **bundled 平台插件**（`plugins/platforms/`）：telegram、discord、slack、whatsapp、matrix、mattermost、email、sms、dingtalk、feishu、wecom、homeassistant、irc、line、teams、google_chat、buzz、ntfy、photon、raft、simplex

**即：从一个平台是「内置」还是「插件」，可以看出其战略重要性与维护成本的权衡。** 官方文档导航把平台分成「Popular」（Telegram）、「Microsoft 365」（Teams）、「Chinese platforms」（DingTalk）等组。

→ https://hermes-agent.nousresearch.com/docs/developer-guide/architecture 、 .../user-guide/features/memory 、 .../user-guide/features/skills 、 .../user-guide/messaging/ 、 .../developer-guide/tools-runtime

### 3.9 Hermes 明确承认的限制与设计取舍

官方文档中**明确写出的**限制/取舍（这是本次调研中最有价值的一节）：

**架构与循环层面**

| 承认的点 | 出处 |
| --- | --- |
| **parent + subagents 的总迭代数可以超过 parent 自己的上限**（预算是各自独立的） | agent-loop |
| `codex_responses` 轮次可能在**只有 reasoning 输出**的情况下停滞；用「连续三次续接无可见文本或工具调用」来判定，并降级 | agent-loop |
| 三个 API 模式的差异被归一化掉，但**模式决定消息如何格式化、工具调用如何构造、响应如何解析、缓存/流式如何处理**——是真实的复杂度来源 | agent-loop |

**上下文层面**

| 承认的点 | 出处 |
| --- | --- |
| **摘要模型窗口小于主模型会造成静默丢中间轮**（被称为「degraded compaction 最常见原因」） | compression |
| **估计值的大小不能证明请求会失败**——因此粗估超阈值时会「等一个请求」以求 provider 证据，代价是**真正超大的请求可能先挨一次被拒** | compression |
| **不透明的 provider blob（`encrypted_content`）对所有本地估算贡献 0**，只有真实 usage 能定价它们 | compression |
| **图片成本用「学到的」值而非厂商公式**，首次 vision 前只能用平坦默认 1500 | compression |
| **压缩停顿与 cache 断裂是二选一，不是免费的优化**（微压缩必须显式开启，官方主动劝退） | micro-compaction |
| 微压缩**没有等价于 `proactive_prune_min_reclaim_tokens` 的「回收量门」**——只收下一个 exchange 恰好省下的，无论大小；官方明说「a reclaim threshold on micro-compaction is the obvious follow-up and **does not exist yet**」 | micro-compaction |
| 微压缩**首次 pass 通常花 token 而非省 token**（约 400 token 脚手架），盈亏平衡在第二/三次 | micro-compaction |
| 微压缩**是一个下限**：用户 prompt 永不吸收，所以中间能压到多小有下限（若你常粘贴 10–20K token 的 prompt，这部分按设计留在上下文里） | micro-compaction |
| 微压缩的 DB 步骤失败时**恢复会双重载入**，直到下次批量压缩清理 | micro-compaction |
| 微压缩**是 best-effort**：能降级，但不应能弄坏会话 | micro-compaction |
| 微压缩的队列**在内存中**：应用退出时仍在等待的 review 被丢弃 | compression（background review defer） |
| `codex_gpt55_autoraise` 存在是因为 **ChatGPT Codex OAuth 把窗口硬顶在 272K**，而同一 slug 在别处是 1.05M——**模型名的窗口不是稳定属性** | compression |
| Codex `-900k` 变体是**Hermes 侧别名**（后缀在发给后端前被剥掉）；官方**故意默认用 272K**，因为「a bigger window means more tokens per request and much faster subscription-usage burn, so the large window is **strictly opt-in**」 | compression |
| **等待一个请求不等于禁用**：一旦响应不含 usage，现有启发式回退仍可用；且有「后压缩 latch」等一个响应 | compression |
| 「Provider count 端点仍然被推迟」——**消除剩余估算需要一次显式的策略决定**：接受有记录的 liveness 回退，或用 provider 证据替换它，同时为「永不返回 usage 的 provider」定义行为。官方明说「**simply disabling all unanchored maintenance is not equivalent**」 | compression |
| 评估脚本是**编排好的控制流检查，不是厂商 tokenizer 或计费证据** | compression |

**记忆层面**

| 承认的点 | 出处 |
| --- | --- |
| **记忆不自动压缩**（超限返回错误让 agent 自己腾地方）——代价是 agent 可能反复失败 | memory |
| **`replace` 也受上限约束**，换成更长的仍可能溢出 | memory |
| **冻结快照导致新写入的记忆对当前会话不可见** | memory |
| **单会话内记忆机制「没有理由运行」**；消息平台的连续会话会**越来越贵**且**学习回路几乎不触发**——官方把这个边界交给用户（`/new`） | memory |
| **小模型（约 30B 以下）和工具调用弱的模型会声称保存了记忆但没真的调用工具**——官方给的修复是**换更强的模型做 setup，而不是加更多指令** | memory |
| **记忆按 profile 隔离**：CLI 默认 profile 与另一个 profile 上的 Telegram bot **不共享笔记** | memory |
| **两个 agent 进程共用一个 Hermes home 会复合成谁都没写过的状态** | memory |

**安全层面**

| 承认的点 | 出处 |
| --- | --- |
| **写保护不是硬边界**：只覆盖 `write_file`/`patch`；**`terminal` 以同一 OS 用户运行，仍可用 shell 命令 `cat` 或覆盖被拒路径**。官方原话：「it **does not sandbox a hostile or compromised agent**」 | security |
| **deny 规则是 shell 命令策略，不是完整 shell 解释器或 OS 能力沙箱**；不解析任意变量、别名、函数、**改名的二进制**、脚本、解释器程序。「不要用 basename deny 规则作为「某能力无法通过其它方式到达」的保证」 | security |
| **`external_directory`/写防线都是 defense-in-depth，不是边界**——真正的隔离要用 OS 权限和受限 mount/凭据/网络的隔离后端 | security |
| **容器后端会跳过危险命令检查**——因为容器才是边界（这是取舍，不是漏洞，但必须知道） | security |
| **YOLO 模式禁用所有危险命令安全检查（硬线黑名单除外）** | security |
| **审批超时的提示无法重开**；挂起的条目被丢弃 | security |
| **从运行中的会话移除 allowlist 条目不会立即生效**（列表在启动时读取），「若你是出于安全考虑移除的，请重启」 | security |
| **webhook/API 等无人值守面必须显式选择 `deny`/`approve`**，否则默认 `deny` 会立即阻断 | security |
| **`launchctl` 限制是保守的注册限制**，旨在捕获中立标签的间接重启助手，**不是对目标 plist 的检查**；拒绝**并不证明**该作业用了 KeepAlive 或控制 Hermes | security |
| **Docker `docker exec` 默认以 root 运行，产生的 approval 文件 gateway 读不到、批准被静默忽略**——必须 `-u hermes` | security |
| **官方明确承认失败**：若你已用 root 跑过且用户仍未授权，重启容器 | security |
| **`allowManagedHooksOnly` 会连带收窄 statusLine / fileSuggestion 等设置**，且 `disableAllHooks` **无法从 managed settings 外部禁用 managed hooks** | (属 Claude Code，见第 4 节) |

**其他**

| 承认的点 | 出处 |
| --- | --- |
| **原生 Windows 支持仍处于实验阶段**，建议安装 WSL2 运行 | https://hermes-agent.org/zh/ 【二手】 |
| **官方测试规模**：约 25,000 个测试、约 1,250 个文件（侧面说明复杂度） | architecture |
| 架构文档大量使用「facade + `*_siblings`」拆分（如 `hermes_state.py` 是门面，逻辑在 `hermes_state_*.py`）——**这是复杂度已经被迫外化的信号** | architecture |
| 微压缩配置项写明：**a top-level `background_review:` block is not read**（键必须放在 `auxiliary:` 下）——这类「配置键位置陷阱」在文档中被专门记录 | memory |
| 官方建议：**「若 passes 感觉太慢，选项按效果排序是：选更快/更小的压缩模型；给它一个不那么争用的主机；或者关掉微压缩回到批量压缩」**——承认这不是普适优化 | micro-compaction |

---

## 4. Claude Code（公开架构信息）

> 来源以官方文档 `code.claude.com/docs/en/*` 与 Anthropic 官方工程博客为主【一手】。注意：调研期间 `docs.claude.com` 会 302 到 `code.claude.com`；`.md` 后缀可取得完整 Markdown 原文（Mintlify 特性），比 HTML 页信息密度高得多。

### 4.1 Agent 主循环

Anthropic 官方对 agent 的定义非常简洁明确【一手】：

> we've gravitated towards a [simple definition](https://simonwillison.net/2025/Sep/18/agents/) for agents: **LLMs autonomously using tools in a loop**.

→ https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

从 Hook 生命周期图可以反推出循环的**精确结构**【一手】（官方图片 alt 文本本身即结构说明）：

```
（可选 Setup）
  → SessionStart
  → 每轮循环：
      UserPromptSubmit → UserPromptExpansion（slash 命令）
      → 嵌套的 agentic loop：
           PreToolUse → PermissionRequest → PostToolUse
           → PostToolUseFailure → PostToolBatch
           → SubagentStart/Stop、TaskCreated/TaskCompleted
      → Stop 或 StopFailure
  → TeammateIdle、PreCompact、PostCompact
  → SessionEnd
```

其中 **`PostToolBatch` 的官方定义直接证实了并行工具调用**：

> `PostToolBatch` | After a **full batch of parallel tool calls** resolves, before the next model call

→ https://code.claude.com/docs/en/hooks

**关键观察**：`PreToolUse` / `PostToolUse` 「fires on every tool call **inside the agentic loop**」。**Hook 事件名本身就是循环结构的文档**——这是一个很好的设计示范：把循环的阶段边界外化为可观测、可介入的事件。

### 4.2 工具系统

官方有独立的 Tools reference 页（`/docs/en/tools-reference`）。从各页交叉确认的工具名包括：`Read`、`Write`、`Edit`、`Bash`、**`PowerShell`**、`Glob`、`Grep`、`WebFetch`、`WebSearch`、`Task`、`TodoWrite`（→ 见 `TaskCreate`/`TaskUpdate`）、`Skill`、`NotebookEdit`、`EndConversation`、`ToolSearch`、`WaitForMcpServers`。

**并行调用获得官方确认**（`PostToolBatch` 的定义，见上）。

**工具权限与「从工具自身派生批准模式」**——一个很好的细节【一手】：opencode 的 `always` 批准模式集合由工具提供；Claude Code 中对应的是 **`if` 条件语法**，它按工具语义解析：

```json
{ "type": "command", "if": "Bash(rm *)", "command": "..." }
```

即 `Bash(...)` 是对 **Bash 子命令**的匹配，而不是对原始字符串的正则。**「权限模式由工具自己定义语法」是这一层的正确抽象。**

**MCP 工具命名与工具搜索**【一手】：

- MCP 工具命名空间形如 `mcp__<server>__<tool>`（matcher 示例中出现 `mcp__memory__.*`、`mcp__.*`）
- **`ToolSearch` 工具 + MCP tool search**：**MCP 工具定义默认被推迟（deferred）**——「only tool names and server instructions enter context until Claude uses a specific tool」
- 官方对 MCP 开销的明确建议：**「Prefer CLI tools when available」**——「Tools like `gh`, `aws`, `gcloud`, and `sentry-cli` are **still more context-efficient than MCP servers because they don't add any per-tool listing**」

→ https://code.claude.com/docs/en/costs 、 .../mcp

**MCP 输出限额（具体数字，可直接借鉴）**【一手】：

- **输出警告阈值**：任何 MCP 工具输出超过 **10,000 token** 时告警
- **默认上限**：**25,000 token**（`MAX_MCP_OUTPUT_TOKENS` 可调）
- **超限处理**：**无图片内容的结果超限时，Claude Code 把它存到文件，并在对话中替换为一条指明文件路径的消息**，让 Claude 在需要时自己读。文件放在会话的 `tool-results` 目录（`~/.claude/projects/` 下）
- **单工具提额**：MCP server 可在 `tools/list` 响应条的 `_meta["anthropic/maxResultSizeChars"]` 中声明，Claude Code 把该工具阈值提到标注值，**硬顶 500,000 字符**。该标注**独立于 `MAX_MCP_OUTPUT_TOKENS` 生效**（对文本内容），所以用户不必为该工具改环境变量。**对返回图片内容的工具无效**——那些仍受 token 限额约束。

**「大结果落盘 + 给路径」是一个通用且高效的模式**（Hermes 的 `additionalContext` >10,000 字符时也做同样的事）。

**MCP 传输**【一手】：`http`（推荐，`streamable-http` 是别名）、`sse`（**已废弃**）、`stdio`、**`ws`（WebSocket）**、`sdk`（仅 SDK 宿主可注册）。一个很实用的健壮性细节：

> Claude Code **tries the HTTP transport first and switches to SSE when the server doesn't accept it**.

**WebSocket 的位置说明得很清楚**：适合**会主动向 Claude 推事件**的远程 server；**只响应请求的服务用 HTTP**——因为 HTTP 支持 OAuth 与 `claude mcp add --transport`，**WebSocket 两者都不支持**（认证只能靠 header 或 `headersHelper`）。

**配置卫生（几个具体的失败模式与修复，很实用）**【一手】：

- **`url` 有但 `type` 缺失 = 配置错误**，因为「Claude Code 把无 `type` 的条目读作 stdio server」→ 报错并把 `type` 加为 `http`/`sse`/`ws`
- **隐藏空白字符警告**：配置值带前后空白（常来自粘贴 token 时的尾随换行）会告警，检查 `command`、`url`、每个 `args` 条目、`env`/`headers` 下的值与键名；**告警只点名受影响字段而不回显其值**（`Leading or trailing whitespace in: headers.Authorization`）；**不自动 trim，按原样使用**
- **同名跨 scope 冲突警告**：OAuth 登录**按 endpoint 存储**，所以在一个项目里认证的定义不会自动在另一个项目生效
- **保留名**：`workspace`、`claude-in-chrome`、`computer-use`、`Claude Preview`、`Claude Browser` 被保留，配置里定义会被跳过并告警

**凭据隔离**（安全设计）【一手】：

- **仓库或插件提供的 `headersHelper` 是一个你没写的命令**，所以 Claude Code 运行它时**不带你环境里的凭据变量**（如 `ANTHROPIC_API_KEY`）
- 移除规则：**移除名字里含 `TOKEN`、`SECRET`、`PASSWORD`、`KEY`、`AUTH`（任意大小写）的每个变量**，外加一个固定清单（如 `ANTHROPIC_CUSTOM_HEADERS`）
- 若 server 的 `url` 携带这些变量之一的实时值，helper 收到的 `CLAUDE_CODE_MCP_SERVER_URL` 中那部分也被替换为 `REDACTED`
- **信任前置**：对项目 `.mcp.json` 或 local scope 的 server，**helper 只在你接受信任对话框后才运行**；信任前 Claude Code 只用静态 `headers` 连接，且 `-p`/SDK 会话会每 server 打一行 `headersHelper not run` 到 stderr

**`CLAUDE_PROJECT_DIR`** 被设置在**子进程的环境里而非 Claude Code 自己的环境**，因此想在项目 scope 的 `.mcp.json` 里用 `${VAR}` 展开引用它，需要默认值写法 `${CLAUDE_PROJECT_DIR:-.}`。

**Claude Code 自身可作为 MCP server**：`claude mcp serve`（stdio）。官方提醒：**这个 MCP server 只把 Claude Code 的工具暴露给客户端，所以「用户确认」的责任在客户端**。

→ https://code.claude.com/docs/en/mcp

### 4.3 上下文管理

**Anthropic 的官方框架：context engineering**【一手】。核心论点：

- **context rot**：随着上下文窗口里 token 数增加，模型从该上下文准确召回信息的能力**下降**。「This characteristic emerges across all models.」
- **attention budget**：LLM 有一个「注意力预算」；「Every new token introduced depletes this budget by some amount」。
- 架构原因：transformer 的 **n² pairwise relationships**；模型训练分布中更常见的是较短序列，所以**对跨上下文的依赖经验更少、专门参数更少**。
- 结论：**「Context... must be treated as a finite resource with diminishing marginal returns.」**；目标是找到**最小的、高信噪比的 token 集合**。
- 性能梯度而非悬崖：「models remain highly capable at longer contexts but may show reduced precision for information retrieval and long-range reasoning」。

**三种长时程技术（官方并列）**【一手】：

1. **Compaction（压缩）** — 把接近窗口上限的对话摘要，用摘要重新开始一个新窗口。Claude Code 的实现细节被官方写明：
   > 「The model **preserves architectural decisions, unresolved bugs, and implementation details** while **discarding redundant tool outputs or messages**. The agent can then continue with this compressed context **plus the five most recently accessed files**.」
   - **明确的调优建议**：「Start by **maximizing recall** to ensure your compaction prompt captures every relevant piece of information from the trace, then **iterate to improve precision** by eliminating superfluous content.」
   - 官方承认的核心难点：「overly aggressive compaction can result in the loss of subtle but critical context **whose importance only becomes apparent later**」
   - 官方点名的**最低垂果实**：**tool result clearing**——「once a tool has been called deep in the message history, why would the agent need to see the raw result again?」并称其为「**one of the safest lightest touch forms of compaction**」
2. **Structured note-taking（结构化记笔记 / agentic memory）** — agent 定期把笔记写到上下文之外的记忆。例子：Claude Code 创建 **to-do list**，或自建 agent 维护 `NOTES.md`。
3. **Sub-agent architectures** — 见 4.4。

**最小化上下文的官方技术清单**【一手】（`/docs/en/costs` 的 "Reduce token usage"）：

- **在任务间清空**：切到不相关工作用 `/clear`。「Stale context wastes tokens on every subsequent message.」用 `/rename` 先命名以便 `/resume` 找回
- **自定义压缩指令**：`/compact Focus on code samples and API usage`；也可在项目根 `CLAUDE.md` 里写 `# Compact instructions` 段自定义压缩行为
- **选对模型**（见 4.10）
- **降低 MCP server 开销**（见 4.2）
- **为类型化语言装 code intelligence 插件**：「A single 'go to definition' call replaces what might otherwise be a grep followed by reading multiple candidate files」；且装了语言服务器后**编辑后自动报类型错误**
- **把处理下推到 hooks 和 skills**：
  > 「Instead of Claude reading a 10,000-line log file to find errors, a hook can **grep for `ERROR` and return only matching lines**, reducing context from tens of thousands of tokens to hundreds.」
- **把指令从 CLAUDE.md 移到 skills**：
  > 「Your CLAUDE.md file is loaded into context at session start. If it contains detailed instructions for specific workflows... **those tokens are present even when you're doing unrelated work**. Skills load on-demand only when invoked... **Aim to keep CLAUDE.md under 200 lines** by including only essentials.」
- **调整 extended thinking**（思考 token 按输出 token 计费；用 `/effort` 降档或 `/config` 关闭；`MAX_THINKING_TOKENS` 环境变量；**自适应推理模型忽略非零预算，只能用 effort 等级**；Opus 5.5 / Sonnet 5.5 / Fable 系列**无法关闭思考**）
- **把冗长操作委托给 subagents**（见 4.4）
- **写具体的 prompt**：「Vague requests like 'improve this codebase' trigger broad scanning.」
- **复杂任务的工作习惯**：复杂任务先用 plan mode；尽早纠偏（Esc 停止、`/rewind` 或双击 Esc 回滚）；**给出验证目标**（测试用例、截图、期望输出）；**增量测试**

**「即时（just-in-time）上下文」与 CLAUDE.md 的混合策略**【一手】：

- 趋势是从 embedding 预取检索 → **agent 用轻量标识符（文件路径、存储的查询、web 链接）在运行时按需加载**
- Claude Code 用这个方式做大数据库分析：「The model can write targeted queries, store results, and leverage Bash commands like `head` and `tail` to analyze large volumes of data **without ever loading the full data objects into context**」
- **元数据本身是信号**：「the presence of a file named `test_utils.py` in a `tests` folder implies a different purpose than a file with the same name located in `src/core_logic/`」
- **官方明确承认混合策略与其代价**：
  > 「Claude Code is an agent that employs this **hybrid model**: CLAUDE.md files are **naively dropped into context up front**, while primitives like glob and grep allow it to navigate its environment and retrieve files just-in-time」
  > 「there's a trade-off: **runtime exploration is slower than retrieving pre-computed data**... Without proper guidance, an agent can **waste context by misusing tools, chasing dead-ends, or failing to identify key information**.」

**CLAUDE.md 记忆层级与 `InstructionsLoaded` 事件**【一手】：指令文件有作用域 `"User"` / `"Project"` / `"Local"` / `"Managed"`，且**支持惰性加载**：

> This event fires at session start for **eagerly-loaded** files and again later when files are **lazily loaded**, for example when Claude accesses a subdirectory that contains a nested `CLAUDE.md` or when **conditional rules with `paths:` frontmatter** match.

`load_reason` 取值：`session_start`、`nested_traversal`、`path_glob_match`、`include`、`compact`（**压缩后重新加载指令文件**）。

**这是一个很有价值的模式**：**指令文件按目录嵌套 + 按路径 glob 条件激活 + 压缩后自动重新加载**。

**缓存生命周期与成本**【一手】：

- 缓存生命周期**在订阅上是 1 小时，一旦开始动用 usage credits 就降到 5 分钟**；API key 或云 provider 上默认 5 分钟
- **`SessionStart` 事件会告诉你恢复一个陈旧会话的代价**（需要 v2.1.251+），字段包括：`seconds_since_last_response`、`context_tokens`（会话首个请求要重发的 prompt token 数）、`prompt_cache_likely_expired`、`estimated_cache_write_usd`
- 官方示例输入：会话在最后响应后 90 分钟恢复 → `"context_tokens": 182340, "prompt_cache_likely_expired": true, "estimated_cache_write_usd": 1.1396`
- **`/usage` 的 Prompt cache 行**会显示：请求数、来自缓存的输入 token 占比、cache miss 数、是否 warm，以及**expected rebuild（因压缩或清理工具结果）**。miss 时若能识别原因会点名，例如 `likely cause: tool definitions changed`

**⚠️ 一个非常有教育意义的因果链（解释了为什么改工具定义很贵）**【一手】：

> `/reload-mcp` 会问，因为**重建 MCP 工具集会作废 provider 的 prompt cache（工具 schema 存在于 system prompt 中）**，所以下一条消息会重发完整输入 token。

→ 这与 Hermes 的「Prompt stability」设计原则（system prompt 不在会话中途变化）是同一个约束的两种表述。**目标 harness 必须把「会改变 system prompt / 工具定义的动作」当成昂贵操作来对待。**

→ https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents 、 https://code.claude.com/docs/en/costs 、 .../hooks

### 4.4 子代理 / 多代理

**Anthropic 官方的 sub-agent 定位与回传契约**【一手】：

> Rather than one agent attempting to maintain state across an entire project, **specialized sub-agents can handle focused tasks with clean context windows**. The main agent coordinates with a high-level plan while subagents perform deep technical work or use tools to find relevant information. Each subagent might explore extensively, **using tens of thousands of tokens or more, but returns only a condensed, distilled summary of its work (often 1,000-2,000 tokens)**.
> This approach achieves a clear separation of concerns—**the detailed search context remains isolated within sub-agents**, while the lead agent focuses on synthesizing and analyzing the results.

**「子代理烧几万 token，只回传 1,000–2,000 token 摘要」**——这是子代理架构的核心经济账。

**Claude Code 的子代理定义**【一手】：`.claude/agents/*.md`（含 frontmatter `model:`、`description` 等）。官方相关文档页：`/docs/en/sub-agents`（本次 HTML 抓取时正文未渲染，**细节未完整取得**）、`/docs/en/agent-teams`、`/docs/en/agent-view`、`/docs/en/workflows`、`/docs/en/worktrees`、`/docs/en/cross-session-messaging`。

**从其他官方页面交叉确认的事实**【一手】：

- **子代理可指定模型**：`model: haiku` 用于简单子代理任务
- **Opus 切换会传染**：「A switch to Opus also applies to **the subagents that inherit your session's model**」
- **子代理有独立上下文窗口**：「every subagent, and every agent a dynamic workflow spawns, **sends its own requests on top of the main conversation's**」
- **子代理的请求仍然计入你的用量**：「The subagent's own requests still draw on your usage.」
- **子代理事件**：`SubagentStart` / `SubagentStop`，matcher 匹配 agent type（`general-purpose`、`Explore`、`Plan`、自定义名、`^my-plugin:reviewer$`）
- **子代理里也跑你配置的 hooks**：「When a subagent calls a tool, tool events such as `PreToolUse` and `PostToolUse` fire the same configured hooks as in the main conversation, and the input carries the `agent_id` and `agent_type` common input fields that identify the subagent.」——**注意：这同时意味着 hook 需要自己判断调用来自哪个 agent**
- **MCP server 可作用域限定到子代理**（`/docs/en/sub-agents#scope-mcp-servers-to-a-subagent`，细节未展开）
- **skill 可预加载进子代理**（`/docs/en/sub-agents#preload-skills-into-subagents`）

**Agent Teams（实验性）与其成本**【一手】：

- **默认禁用**，需 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`
- **每个 teammate 是一个独立的 Claude 实例，有自己的上下文窗口**
- **成本数字**：**「Agent teams use approximately 7x more tokens than standard sessions when teammates run in plan mode」**
- 官方控本建议：teammate 用 Sonnet；保持团队小；**spawn prompt 保持聚焦**（teammate 会自动加载 CLAUDE.md、MCP servers 和 skills，所以 spawn prompt 里的一切都会从头进入它们的上下文）；**工作完成就关掉 teammate**——「Each active teammate continues consuming tokens until it exits or the session ends」
- `TeammateIdle` 事件：teammate 即将空闲时触发；可用 `{"continue": false, "stopReason": "..."}` **完全停下该 teammate**

**Worktree 隔离**【一手】：有 `WorktreeCreate` / `WorktreeRemove` hook，可用 `--worktree`、`isolation: "worktree"` 或后台会话触发；`WorktreeCreate` **可替代默认 git 行为**（command hook 把路径打到 stdout；HTTP hook 返回 `hookSpecificOutput.worktreePath`；**hook 失败或路径缺失则创建失败**）。文档还有 `/docs/en/worktrees` 专页说明「What worktrees share with the main checkout」。

→ https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents 、 https://code.claude.com/docs/en/costs 、 .../hooks

### 4.5 决策与规划

**Plan mode** 是一个**权限模式**，不是 agent 类型【一手】：

- 通过 **Shift+Tab 循环**进入 `/docs/en/permission-modes#analyze-before-you-edit-with-plan-mode`
- 官方描述：「Claude explores the codebase and proposes an approach for your approval, **preventing expensive re-work when the initial direction is wrong**」
- 它与其他权限模式并列：`default`、`acceptEdits`、`plan`、`bypassPermissions`，另有 `auto` 与 `dontAsk`（见 4.7）

**TODO 机制**【一手】：从 hooks 事件表可见 **`TaskCreated`（通过 `TaskCreate`）与 `TaskCompleted`（任务被标记完成）** 是一等事件，且有对应决策控制（`TaskCreated` 可用 exit code 2 或 `decision: "block"` **取消任务**并把消息返回给 Claude；`continue: false` 被忽略）。`TodoWrite` 工具在既有文档中亦存在。

**`/goal`（Goals）**【一手，部分】：文档导航含 `/docs/en/goal`，且成本页提到：

> **Goal check-ins**: while background work keeps an active goal waiting, Claude Code **asks Claude to check on that work even when the session sits idle**, starting a new turn that sends your full context. Claude Code starts **at most three idle check-ins per goal** between your prompts.（v2.1.236+；`CLAUDE_CODE_GOAL_CHECKIN_MINUTES=0` 可关闭）

**注意其成本含义**：**空闲也会产生新 turn 并重发完整上下文**。**这是常驻 agent 的一个普遍陷阱**——目标 harness 必须为「空闲时的主动性」设上限。

→ https://code.claude.com/docs/en/hooks 、 .../costs

### 4.6 成本控制

**官方实测成本基准**【一手】（企业部署）：

- **平均约 $13 / 开发者 / 活跃日**，**$150–250 / 开发者 / 月**
- **90% 用户低于 $30 / 活跃日**
- 官方建议：**先小规模试点**，用跟踪工具建立基线再推广

**`/usage` 的会话块内容（示例原文）**【一手】：

```
Total cost:            $0.55
Total duration (API):  6m 20s
Total duration (wall): 6h 33m 10s
Total code changes:    0 lines added, 0 lines removed
Usage by model:
   claude-sonnet-4-6:  1.2k input, 5.3k output, 940.0k cache read, 50.0k cache write ($0.55)
Prompt cache (main):   14 requests · 91% of input tokens from cache · 2 misses (last 6m 10s ago, 310.2k tokens re-cached) · 1 expected rebuild (compaction or tool-result clearing) · warm (1h TTL, last activity 40s ago)
```

**成本数字本地计算，且可用合同价覆盖**【一手】：Claude Code 在本地按 token 数×标价算美元数；管理员可用 **`modelPricing` managed setting**（`multiplier` <1 折扣 / >1 加价，或按模型列四个 per-token 费率）让它匹配合同价。该键**在 user/project/local settings 和 `--settings` 中被忽略**，只能通过 managed settings 下发。**它改变 Claude Code 报告什么，不改变 Anthropic 收什么费。**

**按用量归因（Plan usage breakdown）**【一手】：`/usage` 会把近期用量**归因到 skills、subagents、plugins 和各个 MCP server**，各显示为总量的百分比；并给出 **behavior flags**（如 long context、cache misses）——**当某项占近期用量 ≥10% 时标记**；以及 **Loops 行**（每个近期跑过的 `/loop` 或定时任务的总 token、每次 token、触发频率、上次运行时间），按总 token 排序。可按 `d`/`w` 切换 24 小时 / 7 天。

**`/insights`**：分析本机近期会话（一次最多 200 个未见过的会话，跳过很短的），产出 HTML 报告，覆盖你在做什么、摩擦点（如被误解的请求、有 bug 的代码）和改进建议；写到 `~/.claude/usage-data/report.html` 并按时间戳保留每次运行的副本。

**为什么长会话用量会攀升（官方的归因清单，极有参考价值）**【一手】：

1. **Long context**：「Claude Code sends your full conversation with every request, and **each time Claude uses tools it sends another request carrying that batch of tool results**.」→「a one-line question in a session that has been open all day still draws usage for the whole conversation」
2. **Cache misses**：「your first message after a break longer than the cache lifetime misses the cache and **reprocesses your full context**」
3. **Scheduled tasks**：「a scheduled task fires on its interval **even while the session is idle, sending your full context each time**」
4. **Cross-session messages**：「Claude Code delivers a message from another of your sessions as a new turn when this session sits idle, sending your full context each time」（`crossSessionInbound: "hold"` 可改为暂存）
5. **Goal check-ins**（见 4.5）
6. **Subagents and workflows**：「every subagent, and every agent a dynamic workflow spawns, sends its own requests on top of the main conversation's」
7. **Agent teammates**：「each active teammate keeps consuming tokens until it exits」
8. **Compaction**：「`/compact` **reads the conversation it summarizes, so compacting a large context is itself a large request**.」——**压缩本身是一次大请求**（这与 Hermes 微压缩的「分期付款」动机完全一致）

**模型分级**【一手】：

- 「**Sonnet handles most coding tasks well and costs less than Opus.** Reserve Opus for complex architectural decisions or multi-step reasoning.」
- `/model` 中途切换或 `/config` 设默认
- 「For simple subagent tasks, specify `model: haiku`」
- **`/effort`** 调整努力等级；思考 token 按输出 token 计费

**Prompt caching**【一手】：官方称「reduces costs for repeated content like system prompts」；有专页 `/docs/en/prompt-caching`（含「Actions that invalidate the cache」、「Choose the TTL yourself」、「Compacting the conversation」等节，**本次未展开抓取**）。

**后台 token 消耗（诚实披露）**【一手】：

> Claude Code uses tokens for some background functionality even when idle: **Conversation summarization**（为 `claude --resume` 摘要历史会话的作业）、**Command processing**（如 `/usage` 可能发请求查状态）。
> 「These background processes consume a small amount of tokens (**typically under $0.04 per session**) even without active interaction.」

另有 **prompt suggestions**：开启时 Claude 回复后会向会话所用模型发一个短请求以建议下一个 prompt；它**复用对话的 prompt cache**，所以主要是 cache read 加少量输出 token；**账户接近或达到用量上限时会被跳过**。

**成本治理（组织侧）**【一手】：workspace spend limits（Console）、seat allowance + usage credits + 组织/组/成员级 spend limits（Team/Enterprise）、**OpenTelemetry 导出**（「the only option that streams per-user token and cost metrics into your own observability stack in near real time」）、**Claude Code Analytics API**、LLM gateway（如 LiteLLM，官方注明**未审计安全性且与 Anthropic 无关联**）。

**Rate limit 建议表（TPM/RPM per user，很实用）**【一手】：

| 团队规模 | TPM/user | RPM/user |
| --- | --- | --- |
| 1–5 | 200k–300k | 5–7 |
| 5–20 | 100k–150k | 2.5–3.5 |
| 20–50 | 50k–75k | 1.25–1.75 |
| 50–100 | 25k–35k | 0.62–0.87 |
| 100–500 | 15k–20k | 0.37–0.47 |
| 500+ | 10k–15k | 0.25–0.35 |

官方说明：TPM/user 随团队增大而下降，因为**大组织中并发使用的人更少**；限额是**组织级**而非个人级，所以个人在他人不活跃时可临时超额。

→ https://code.claude.com/docs/en/costs

### 4.7 权限与安全

**权限模式**【一手】：`default`、`acceptEdits`、`plan`、`bypassPermissions`，另有 **`auto`**（auto mode，「Eliminate prompts with auto mode」）与 **`dontAsk`**（「which never prompts」，会**直接拒绝**而非提示）。

**规则语法**【一手】：

- **`allow` / `deny` 规则**，支持 `Tool(specifier)` 形式
- **按输入参数匹配**：permissions 页有 `#match-by-input-parameter` 节；skill 相关示例为 `Skill(commit)`、`Skill(review-pr *)`、`Skill(deploy *)`，以及参数形式 `Skill(skill:deploy)`
- **deny 规则的覆盖面比字面名更广**（官方给了对照表）：
  | deny 规则中点名的是 | 例 | **同时也阻断** |
  | --- | --- | --- |
  | 一个别名 | `Skill(review)` | 通过 `/review` 别名访问的 bundled `/code-review` |
  | 未限定的名字 | `Skill(deploy)` | 被列为 `apps/web:deploy` 的嵌套 skill |
  | 同步自 claude.ai 的 skill | `Skill(anthropic-skills:deploy)` | 该 skill 被 Claude Desktop 作为插件投递时 |
  | 同步 skill 的插件形式 | `Skill(deploy:deploy)` | 该同步 skill |
  | 参数形式的 skill | `Skill(skill:deploy)` | 无论 Claude 用哪个名字调用它（含别名与显示名） |
- **allow 规则只匹配 skill 自身的名字和 Claude 调用时用的名字**；批准同步 skill 需在其保留命名空间内点名：`Skill(anthropic-skills:pdf)` ✔、`Skill(anthropic-skills *)` ✔、**`Skill(anthropic *)` ✘**（「命名空间外的前缀不匹配其内部的名字」）

**沙箱**：官方有 `/docs/en/permissions` 与安全相关页面（`/docs/en/security`）。从其他页交叉确认：**`--dangerously-skip-permissions`** 存在于 CLI flags；**workspace trust dialog**（信任对话框）是多个安全决策的前置——**未信任的仓库不能自己批准自己的 MCP server**：

> A cloned repository **can't approve its own servers**: `enableAllProjectMcpServers` 或 `enabledMcpjsonServers` 提交在项目的 `.claude/settings.json` 里，**在未信任的目录中会被忽略**，server 保持 `⏸ Pending approval`。

**这条「被克隆的仓库不能自行授权」的原则极其重要**，目标 harness 在设计项目级配置时必须遵守同样的规则。

**官方明确承认的限制（来自 `/docs/en/costs`，其中几条对 harness 设计很关键）**【一手】：

| 承认的点 |
| --- |
| **`/usage` 的美元数字是估算**，权威计费以 Console 为准 |
| **契约价需要 `modelPricing` 手动录入**——「Claude Code doesn't fetch them from the Claude Console, so **update the setting when the contract changes**」 |
| **`modelPricing` 只影响报告，不影响实际收费** |
| **云 provider（Bedrock / Google Cloud / Foundry）上 Claude Code 不回传指标给 Anthropic**，所以 analytics 面板与 Analytics API **不覆盖**这些用量 |
| **LiteLLM 等 LLM gateway 未审计安全性，且与 Anthropic 无关联** |
| 在 1.1× data residency rate 生效前（v2.1.239 之前），**会话成本数字低于实际账单** |
| **归因数据来自本机会话历史，其他设备或 claude.ai 的用量不含在内** |
| **`/usage` 的归因是近似的** |
| **v2.1.222 之前，一次 MCP server 调用后，Claude Code 会把其后每个请求都归因到该 server，高估其占比** |
| **`/skill-doctor` 在通过 Remote Control 运行时不可用**（会回复 `Skill usage reports are not available on this connection.`），必须在跑会话的那台机器的终端里跑 |
| **agent teams 默认禁用**，且 plan mode 下约 7× token |
| **`/insights` 的分析请求本身消耗 token 并计入你的套餐/API 用量** |
| **云端会话不读本机 `~/.claude/settings.json`**；Cowork 和 cloud sessions **不读 `~/.claude/skills/`** |
| **skill 描述在列表预算溢出时会被丢弃**——「if you have many skills, Claude Code drops some descriptions to fit the listing's character budget, **which removes the keywords Claude needs to match your request**. The budget scales at **1% of the model's context window**.」 |
| **每个 skill 在列表里都增加每轮上下文**：「Every skill in the skill listing adds to your context **on every turn**, whether or not Claude ever uses it.」 |

**Hooks 的安全/治理机制**【一手】（可作为自有 hook 系统的设计清单）：

- **`disableAllHooks`** 设置可禁用 hook，**但从 managed settings 外部无法禁用 managed hooks**
- **`allowManagedHooksOnly`**（managed settings）可限制哪些 hook 运行：**user/project/local/plugin hooks 被阻断**；同时会**收窄 `statusLine`、`fileSuggestion`、`subagentStatusLine`**；并**禁用带 `command` source 的插件**（除非显式把 `disableCommandPluginSources` 设为 `false`）
- **hook 条目跨 settings 层级合并而非替换**：user/project/local 各自添加自己的 hook，**不移除 managed 的**
- **HTTP hook 白名单（对所有来源的 hook 生效，含 managed）**：
  - `allowedHttpHookUrls`：任一层级定义后，**HTTP hook handler 的 URL 必须匹配合并后的白名单才会运行**
  - `httpHookAllowedEnvVars`：定义后，**只有该清单上的环境变量会被插值进 hook headers**
- **`CLAUDE_ENV_FILE`**：SessionStart / Setup / CwdChanged / FileChanged hook 可把 `export` 语句写进它，**持久化到后续 Bash 命令**；其它 hook 类型无此变量
- **`additionalContext`（把 hook 输出注入 Claude 上下文）的官方写法建议（安全相关）**：
  > Write the text as **factual statements rather than imperative system instructions**. Phrasing such as "The deployment target is production" or "This repo uses `bun test`" reads as project information. **Text framed as out-of-band system commands can trigger Claude's prompt-injection defenses**, which causes Claude to surface the text to you instead of treating it as context.
- **`additionalContext` 的长度上限行为**：「If a value exceeds **10,000 characters**, Claude Code **writes the text to a file in the session directory and passes Claude the file path with a preview of up to the first 2,000 characters** instead.」
- **transcript 里保存注入文本的后果**：恢复时会**重放保存的文本而不是重跑 hook**，所以时间戳、commit SHA 之类的值会**变陈旧**；`SessionStart` hook 在 resume 时会带 `source: "resume"` 重跑，因此可以刷新自己的上下文

→ https://code.claude.com/docs/en/hooks 、 .../costs 、 .../mcp

### 4.8 扩展机制

**四层扩展：Skills + Hooks + MCP + Plugins**（另有 Agent SDK）。

**（A）Agent Skills**【一手】——本节的格式规范对目标 harness 最有直接价值。

- **格式**：一个目录，含 `SKILL.md`，**必须以 YAML frontmatter 开始**，其中 **`name` 与 `description` 为必需**
- **渐进式披露（三级）**：
  - **Level 1 — 元数据**：「At startup, the agent pre-loads the `name` and `description` of every installed skill into its system prompt. This metadata is the first level of progressive disclosure: it provides **just enough information for Claude to know when each skill should be used** without loading all of it into context.」
  - **Level 2 — SKILL.md 正文**：「If Claude thinks the skill is relevant to the current task, it will load the skill by reading its full `SKILL.md` into context.」
  - **Level 3+ — 打包文件**：「skills can bundle additional files within the skill directory and reference them by name from `SKILL.md`. These additional linked files are the third level (and beyond) of detail, which Claude can choose to navigate and discover only as needed.」
- **核心经济论点**【一手】：
  > 「Agents with a filesystem and code execution tools don't need to read the entirety of a skill into their context window when working on a particular task. **This means that the amount of context that can be bundled into a skill is effectively unbounded.**」
- **skill 可包含代码**，官方解释为何这是必要的（而非偏好）：
  > 「**Large language models excel at many tasks, but certain operations are better suited for traditional code execution.** For example, sorting a list via token generation is far more expensive than simply running a sorting algorithm. Beyond efficiency concerns, **many applications require the deterministic reliability that only code can provide**.」
  > 「Claude can run this script **without loading either the script or the PDF into context**.」
- **官方承认的触发机制脆弱性**【一手】：
  > 「Pay special attention to the `name` and `description` of your skill. **Claude will use these when deciding whether to trigger the skill** in response to its current task.」
- **官方承认的安全边界**【一手】：
  > 「**Skills provide Claude with new capabilities through instructions and code. While this makes them powerful, it also means that malicious skills may introduce vulnerabilities in the environment where they're used or direct Claude to exfiltrate data and take unintended actions.**」
  > 「We recommend **installing skills only from trusted sources**. When installing a skill from a less-trusted source, **thoroughly audit it before use**.」并特别提醒注意「instructions or code within the skill that instruct Claude to connect to potentially untrusted external network sources」
- **开放标准**：Agent Skills 已于 **2025 年 12 月 18 日**由 Anthropic 发布为开放标准；官方原文注明 "Update: We've published Agent Skills as an open standard for cross-platform portability."

**Claude Code 对标准的扩展（超出 agentskills.io 的部分）**【一手】：

- **动态上下文注入（`!`command``）**——在 Claude 看到 skill 内容**之前**执行命令并把输出替换进去：
  ```yaml
  ## Current changes
  !`git diff HEAD`
  ```
- **`context: fork` + `agent: <name>`** — 让 skill 在**一个 fork 出去的子代理**里运行：
  ```yaml
  ---
  name: deep-research
  description: Research a topic thoroughly
  context: fork
  agent: Explore
  ---
  Research $ARGUMENTS thoroughly: ...
  ```
  官方描述的四步：「1. A new isolated context is created 2. The subagent receives the skill content as its prompt 3. The `agent` field determines the execution environment (**model, tools, and permissions**) 4. The subagent summarizes its results and returns them to your main conversation when it finishes」
- **`allowed-tools`**：在调用该 skill 的那一轮**免逐次批准**地授予这些工具；**授予在你发下一条消息时清除**
- **`disable-model-invocation: true`**：把 skill **从 Claude 的上下文里完全移除**（不只是禁止自动调用）
- **`user-invocable: false`**：你不能调用，但 Claude 仍可以

**skill 位置与优先级**【一手】（完整表格，直接可用）：

| 位置 | 路径 | 加载范围 |
| --- | --- | --- |
| Enterprise | managed settings 目录下 `.claude/skills/<name>/SKILL.md` | 组织部署的机器上所有用户 |
| Personal | `~/.claude/skills/<name>/SKILL.md` | 本机所有项目 |
| Project | `.claude/skills/<name>/SKILL.md` | 本仓库会话 |
| Nested | `<subdir>/.claude/skills/<name>/SKILL.md` | 在 `<subdir>` 中或之下启动的会话 |
| Additional directory | `--add-dir` 的目录下 `.claude/skills/...` | 该会话 |
| Plugin | `<plugin>/skills/<name>/SKILL.md` | 插件启用的地方，作为 `/plugin-name:skill-name` |
| claude.ai account | 账户启用的 skills | Cowork、云会话、用该账户登录的终端会话 |

**发现规则**【一手】：
- 「Claude Code loads project skills from `.claude/skills/` in the directory where you start it **and in every parent directory up to the repository root**」
- **嵌套 skill 惰性加载**：「Skills in a `.claude/skills/` directory **below** where you started **don't load at startup**. They load the first time Claude reads or edits a file in that subdirectory and stay available for the rest of the session.」
- git worktree 中只向上搜到 worktree 根；v2.1.277+ 若 worktree 根没有 `.claude/skills`，则加载主 checkout 的项目 skills
- **同名解析**：enterprise > personal > project；你的 skill **替换 bundled skill 和内置命令，但不替换其别名**（「A project `usage` skill replaces `/usage`, and the built-in alias `/cost` still runs the built-in command」）
- **保留名**：`synced`（任何大小写）与 `anthropic-skills`（或以 `anthropic-skills:` 开头）
- **command 文件已被合并进 skills**：`.claude/commands/deploy.md` 与 `.claude/skills/deploy/SKILL.md` 都创建 `/deploy` 且行为相同
- **skill 文件夹可作为插件**：加一个 `.claude-plugin/plugin.json` 就以 `<name>@skills-dir` 加载为插件，从而可捆绑 agents、hooks 和 MCP servers

**skill 内容的生命周期（官方承认的坑）**【一手】：

> 「Claude Code adds the skill's content to the conversation when the skill is invoked and **doesn't re-read the file on later turns**.」
> 「If the conversation was compacted, invoke the skill again to restore its full content. After compaction, Claude Code **can keep only the start of an invoked skill**, so **put the most important instructions near the top of `SKILL.md`**.」

**「重要指令放 SKILL.md 顶部」**是一个由压缩机制直接推导出的写作规范——**很好的例子：机制约束塑造内容创作规范**。

**skill 的评测（Evals）**【一手】：官方提供了完整的迭代方法论：
- **基线对比法**：「Collect a few realistic prompts, run each one in a fresh session with the skill available and again with it turned off, and compare the results. **A fresh session matters because leftover context from authoring the skill will mask gaps in the written instructions.**」
- **`skill-creator` 插件**：存测试用例于 `evals/evals.json`；**为每个测试用例 spawn 一个 subagent**，使每次运行从干净上下文开始，并记录 token 数与耗时；把断言结果与证据写入 `grading.json`；把 with-skill vs without-skill 的通过率、时间、token 聚合进 `benchmark.json`；**version comparison** 做两版 skill 的盲测 A/B；**description tuning** 生成 should-trigger / should-not-trigger prompts，测命中率并提出描述修改建议；**review viewer** 打开 HTML 报告
- **`claude plugin eval`** 可在 CI 中按阈值 gate（低于阈值非零退出）

**`/skill-doctor`**：报告每个 skill 的上下文成本与使用频率，帮你决定关掉哪些；**会标记从未被调用过的 skill**；也列出你近期没用过的 plugin。

**（B）Hooks**【一手】——事件清单（本次调研中最完整的公开 hook 清单）：

| 事件 | 何时触发 |
| --- | --- |
| `SessionStart` | 会话开始或恢复 |
| `Setup` | `--init-only`，或 `-p` 模式下的 `--init`/`--maintenance` |
| `UserPromptSubmit` | prompt 被提交、Claude 处理之前（**也包括 Claude Code 自行开始的轮次**） |
| `UserPromptExpansion` | 用户输入的命令展开成 prompt 时（**可阻断展开**） |
| `PreToolUse` | 工具调用执行前（**可阻断**） |
| `PermissionRequest` | 工具调用需要权限决定时 |
| `PermissionDenied` | auto mode 拒绝工具调用时（含无分类器裁决的拒绝） |
| `PostToolUse` | 工具调用成功后 |
| `PostToolUseFailure` | 工具调用失败后 |
| `PostToolBatch` | **一整批并行工具调用**全部解决后、下次模型调用前 |
| `Notification` | Claude Code 发通知时 |
| `MessageDisplay` | assistant 消息文本**正在流式显示**时（**仅显示层**：替换屏上文本，transcript 与 Claude 看到的仍是原文） |
| `SubagentStart` / `SubagentStop` | 子代理被 spawn / 结束时 |
| `TaskCreated` / `TaskCompleted` | 任务经 `TaskCreate` 创建 / 被标记完成时 |
| `Stop` / `StopFailure` | Claude 结束响应时 / 因 API 错误结束轮次时 |
| `TeammateIdle` | agent team teammate 即将空闲时 |
| `InstructionsLoaded` | `CLAUDE.md` 或 `.claude/rules/*.md` 被载入上下文时 |
| `ConfigChange` | 会话中配置文件变化时 |
| `CwdChanged` | 工作目录变化时（如 Claude 执行 `cd`） |
| `DirectoryAdded` | 会话中经 `/add-dir` 或 SDK `register_repo_root` 添加工作目录时 |
| `FileChanged` | 被监视的文件在磁盘上变化时（**matcher 指定要监视的文件名**） |
| `WorktreeCreate` / `WorktreeRemove` | worktree 创建 / 移除时 |
| `PreCompact` / `PostCompact` | 上下文压缩前 / 后 |
| `PreModelSwitch` / `PostModelSwitch` | 模型切换前（**可阻断**）/ 后 |
| `Elicitation` / `ElicitationResult` | MCP server 在工具调用中请求用户输入时 / 用户响应后 |
| `SessionEnd` | 会话终止时 |

**按节奏分类（官方自己的分类，很有设计指导性）**【一手】：

> Events fall into three cadences:
> - **per session**: `SessionStart` and `SessionEnd`
> - **per turn**: `UserPromptSubmit`, `Stop`, and `StopFailure`
> - **on every tool call inside the agentic loop**: `PreToolUse` and `PostToolUse`, except `EndConversation` calls, which skip both

**Hook 配置三层嵌套**【一手】：

1. 选**事件** → 2. 加**matcher group**（过滤何时触发）→ 3. 定义一或多个 **hook handler**

**hook handler 类型**【一手】：**`command`（shell）、HTTP 端点、`mcp_tool`（MCP 工具调用）、`prompt`、`agent`**（子代理）。原文定义：

> Hooks are user-defined **shell commands, HTTP endpoints, MCP tool calls, LLM prompts, or subagents** that execute automatically at specific points in Claude Code's lifecycle.

另有 **plugins 可把 hooks 注册为 JavaScript 函数**在 Claude Code 自己的进程内调用（称为 **mod**）。

**Hook 位置与作用域**【一手】：

| 位置 | 作用域 | 可共享 |
| --- | --- | --- |
| `~/.claude/settings.json` | 你的所有项目 | 否 |
| `.claude/settings.json` | 单个项目 | **是，可提交到仓库** |
| `.claude/settings.local.json` | 单个项目 | 否（被 gitignore） |
| Managed policy settings | 组织级 | 是，管理员控制 |
| Plugin `hooks/hooks.json` | 插件启用时 | 是 |
| **Skill frontmatter** | **skill 被调用后的本会话余下部分** | 是 |
| **Subagent frontmatter** | **该子代理运行期间** | 是 |

**⚠️ hook 也在子代理里运行**：「Hooks from settings files, managed policy settings, and plugins **also run inside subagents**... the input carries the `agent_id` and `agent_type` common input fields that identify the subagent.」

**Matcher 语义（一个精细但重要的设计）**【一手】：

| matcher 值 | 如何求值 | 例 |
| --- | --- | --- |
| `"*"`、`""` 或省略 | 匹配全部 | 每次事件都触发 |
| 只含字母、数字、`_`、`-`、空格、`,`、`|` | **精确字符串**，或用 `|`/`,` 分隔的精确字符串列表（可带周围空白） | `Bash`；`Edit\|Write` 与 `Edit, Write`；`code-reviewer` |
| 含任何其它字符 | **JavaScript 正则，非锚定** | `^Notebook`；`mcp__memory__.*` |

**官方警告**：正则路径用 `RegExp.prototype.test`，**在值的任意位置匹配即成功**——「`Edit.*` matches both `Edit` and `NotebookEdit`; wrap the pattern in `^` and `$`, as in `^Edit$`, when you need a whole-string match」。

**两种事件使用更窄的精确匹配集**：`FileChanged` 与 `StopFailure` 只支持字母数字 `_` 和 `|`；这两个事件里出现连字符、空格或逗号会**让它留在正则路径上**，且只有 `|` 分隔备选项。

**决策控制（按事件分化的契约）**【一手】——这张表是设计 hook 系统时最有参考价值的部分：

| 事件 | 决策模式 | 关键字段 |
| --- | --- | --- |
| UserPromptSubmit, UserPromptExpansion, PostToolUse, PostToolUseFailure, PostToolBatch, Stop, SubagentStop, ConfigChange, PreCompact | 顶层 `decision` | `decision: "block"`, `reason` |
| TeammateIdle, TaskCompleted | exit code 或 `continue: false` | exit 2 阻断并带 stderr 反馈；`{"continue": false, "stopReason": "..."}` 也完全停下 teammate |
| TaskCreated | exit code 或顶层 `decision` | exit 2 或 `decision: "block"` **取消任务**并把消息返回给 Claude；`continue: false` 被忽略 |
| **PreToolUse** | `hookSpecificOutput` | **`permissionDecision`（allow/deny/ask/defer）**, `permissionDecisionReason` |
| PreModelSwitch | `hookSpecificOutput` 或顶层 `decision` | `permissionDecision`（allow/deny/ask）；`decision: "block"` 也取消切换 |
| PermissionRequest | `hookSpecificOutput` | `decision.behavior`（allow/deny） |
| PermissionDenied | `hookSpecificOutput` | `retry: true` 告诉模型它可以重试被拒的工具调用 |
| WorktreeCreate | 返回路径 | command hook 打到 stdout；HTTP hook 返回 `hookSpecificOutput.worktreePath` |
| WorktreeRemove | exit code | 任何非零退出码使移除失败（若目录仍存在）；JSON 输出被丢弃 |
| Elicitation / ElicitationResult | `hookSpecificOutput` | `action`（accept/decline/cancel）、`content` |
| MessageDisplay | `hookSpecificOutput` | `displayContent` 替换屏上显示文本 |
| SessionStart, SubagentStart, PostModelSwitch | **仅上下文** | `additionalContext`；**无阻断或决策控制** |
| Setup, Notification, SessionEnd, PostCompact, InstructionsLoaded, StopFailure, CwdChanged, DirectoryAdded, FileChanged | **无** | 仅用于副作用（日志、清理） |

**三个可以「改写内容」而不只是允许/阻断的事件**【一手】：

- **`PreToolUse` 的 `updatedInput`**（直接在 `hookSpecificOutput` 下）**在工具运行前替换其参数**
- **`PermissionRequest` 的 `updatedInput`**（在 `decision` 对象内）
- **`PostToolUse` 的 `updatedToolOutput`** **替换工具结果**
- 官方给出的用法建议：「For **redaction or transformation** use cases, intercept at `PreToolUse` for **outbound** tool inputs and `PostToolUse` for **inbound** tool results.」
- **`UserPromptSubmit` 不能替换 prompt**，只能注入 `additionalContext`

**exit code 语义**【一手】：`exit 0` **无输出 = 无决策**，工具调用继续走正常权限流程。官方特别澄清一个常见误解：

> 「**The hook can deny the call, but staying silent doesn't approve it.**」

即 **hook 静默 ≠ 批准**。这在安全上至关重要。

**`additionalContext` 的注入位置按事件而异**【一手】：

- `SessionStart` / `SubagentStart`：在对话开头、首个 prompt 之前
- `UserPromptSubmit` / `UserPromptExpansion`：与提交的 prompt 并列
- `PreToolUse` / `PostToolUse` / `PostToolUseFailure` / `PostToolBatch`：紧挨工具结果
- `Stop` / `SubagentStop`：轮次末尾（**对话继续，好让 Claude 能对反馈采取行动**）
- `PostModelSwitch`：切换后的下一个请求
- 多个 hook 为同一事件返回 `additionalContext` 时，**Claude 收到全部值**
- 包装方式：**包成 system reminder 插入**，Claude 在下一次模型请求时读到，**但不作为聊天消息出现在界面上**

**一个非常优雅的安全设计：`PreToolUse` hook 可以「避免进程 spawn 开销」**——matcher + `if` 双重过滤：

> 「The `if` condition `"Bash(rm *)"` matches because `rm -rf /tmp/build` is a subcommand matching `rm *`, so this handler spawns. **If the command had been `npm test`, the `if` check would fail and `block-rm.sh` would never run, avoiding the process spawn overhead.**」

**（C）Plugins**【一手】：文档有 `/docs/en/plugins/overview`、`components`（含 `mcp-servers`、`hooks`）、`loading`、`install`、`marketplace-reference`、`plugin-evals`、`mods`（JS 函数 hook）。插件可捆绑 **skills、agents、hooks、MCP servers、commands**，并可用 `plugin.json` 声明。官方提及**插件有持久数据目录**（`${CLAUDE_PLUGIN_DATA}`）与**路径变量**。

**（D）MCP** — 见 4.2。

**（E）其他扩展面**【一手，从导航确认存在但未展开】：Channels（外部事件推入会话，`/docs/en/channels`）、Dynamic workflows（`/docs/en/workflows`）、Cross-session messaging、Scheduled tasks、Agent SDK、Artifacts、Deep links、Goal。

→ https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills 、 https://code.claude.com/docs/en/hooks 、 .../skills 、 .../mcp

### 4.9 Claude Code 明确承认的限制与设计取舍（汇总）

除上文分散记录者外，另有（均一手）：

- **Context rot 是普适现象**：「This characteristic emerges across all models.」
- **压缩本质上是取舍**：overly aggressive compaction 会丢掉「其重要性稍后才显现」的细节
- **运行时探索比预取慢**，且没有好的引导时 agent 会「misuse tools, chasing dead-ends, or failing to identify key information」
- **CLAUDE.md 无差别前置注入是刻意选择**（「naively dropped into context up front」）——官方用「naively」自陈
- **MCP 相比 CLI 工具是上下文劣势**：「Prefer CLI tools when available」
- **skill 触发依赖 description 质量**，而 description **会在列表预算溢出时被丢弃**（预算 = 模型上下文窗口的 1%）
- **每个 skill 无论用不用都在每轮消耗上下文**
- **skill 内容在压缩后可能只剩开头**
- **恢复会话时 hook 注入的文本会被重放而非重跑**，因此值会陈旧
- **agent teams 约 7× token，且默认关闭**
- **空闲也会烧 token**（scheduled tasks、cross-session messages、goal check-ins、background summarization、prompt suggestions）
- **Cursor/其他客户端的 MCP 配置需要人工「修复」**（`url` 无 `type`、名称含非法字符）
- **macOS/Windows 之外的 Claude Desktop 导入 MCP 不可用**：「This feature only works on macOS and Windows Subsystem for Linux (WSL)」
- **Docker/远程场景下 Claude Code 不回传指标**（云 provider）
- **`modelPricing` 只影响报告，不影响收费**

---

## 5. SWE-agent / mini-SWE-agent 与 OpenHands

### 5.1 SWE-agent：Agent-Computer Interface（ACI）

**官方论文核心论点**【一手】：把 LM agent 视为**一类新的终端用户**，他们需要**为自己专门构建的接口**。

> 「Just as humans benefit from powerful software applications, such as integrated development environments, for complex tasks like software engineering, we posit that **LM agents represent a new category of end users with their own needs and abilities, and would benefit from specially-built interfaces to the software they use**.」
> 「**SWE-agent's custom agent-computer interface (ACI) significantly enhances an agent's ability to create and edit code files, navigate entire repositories, and execute tests and other programs.**」

**论文给出的成绩**【一手】：SWE-bench pass@1 **12.5%**，HumanEvalFix pass@1 **87.7%**，「far exceeding the previous state-of-the-art achieved with non-interactive LMs」。
→ https://arxiv.org/abs/2405.15793

**ACI 的四条具体设计（官方文档原文，非常有借鉴价值）**【一手】：

> 1. We add a **linter** that runs when an edit command is issued, and **do not let the edit command go through if the code isn't syntactically correct**.
> 2. We supply the agent with a **special-built file viewer**, instead of having it just `cat` files. We found that this file viewer works best when **displaying just 100 lines in each turn**. The **file editor** that we built has commands for **scrolling up and down and for performing a search within the file**.
> 3. We supply the agent with a special-built full-directory string **searching command**. We found that it was important for this tool to **succinctly list the matches — we simply list each file that had at least one match. Showing the model more context about each match proved to be too confusing for the model**.
> 4. When commands have an empty output we return a message saying "**Your command ran successfully and did not produce any output.**"

**这四条每一条都在做同一件事：把「模型的注意力」当作稀缺资源来设计工具输出。** 特别是第 3 条——**故意提供更少信息反而更好**，这与「给模型更多上下文总是更好」的直觉相反。

→ https://swe-agent.com/latest/background/aci/

**架构**【一手】：

> The central entry point is the `sweagent` CLI (1). It initializes `SWEEnv` (2), which with SWE-agent 1.0 is **merely a thin wrapper around SWE-ReX**. `SWEEnv` initializes the SWE-ReX **Deployment**, which either starts a local Docker container (4) or on a remote system like modal or aws (3). Inside the container SWE-ReX starts a **shell session** (5). SWE-ReX also **installs the ACI elements as custom tools** (9) available to the shell session.
> The second class is the `Agent` (6), configurable with a yaml file. Its most important method is **`forward()` which prompts the model and executes its action**.
> To prompt the model, the history needs to be sent to the LM. In order to make the best use of the context window, **the history gets compressed by a `HistoryProcessor` (7)**. The model output (8) is then interpreted by the `Agent` class (in particular, **we use a parser to extract the action**) and executed in the Shell session via `SWEEnv` (10).

**要点**：
- **`HistoryProcessor` 是一个可插拔的抽象**，专门负责「为省上下文窗口而压缩历史」——与 Hermes 的 `ContextEngine` ABC 是同一个设计意图的两种实现。参考页 `reference/history_processor_config/`。
- **`parser` 是可插拔的**，负责从模型输出中抽取 action——因为**不同模型的输出格式不同**，格式错误是主要失败来源。参考页 `reference/parsers/`。
- **配置全在 YAML**（`config/` 目录），有 templates、models、demonstrations（少样本示例）、tools、environments 等分节。
- **1.0 引入 tool bundle**（`reference/bundle_config/`）与「添加自定义工具」教程。

→ https://swe-agent.com/latest/background/architecture/ 、 https://swe-agent.com/latest/usage/adding_custom_tools/

### 5.2 mini-SWE-agent：官方的「减法」宣言（最有价值的一手设计取舍）

**官方在文档顶部主动推荐 mini 取代 SWE-agent**【一手】：

> 📣 We now recommend **mini-swe-agent** instead of SWE-agent: **Same performance, much more simple & flexible**

**核心追问**：

> In 2024, we built SWE-bench & SWE-agent and helped kickstart the coding agent revolution. We now ask: **What if our agent was 100x simpler, and still worked nearly as well?**

**"minimal" 的具体含义**【一手】**——约 100 行 Python 的 agent class**（另加 environment、model、run script 各一点）：

- **除 bash 之外没有任何工具**——**「it doesn't even need to use the tool-calling interface of the LMs」**。因此在沙箱环境里**你不需要安装任何包——它只需要 bash**。
- **完全线性的历史**——每一步只是 append 到消息里。**「So there's no difference between the trajectory and the messages that you pass on to the LM.」** 对调试与微调极友好。
- **用 `subprocess.run` 执行动作**——每个动作**完全独立**（相对于维持一个有状态的 shell 会话）。**「This makes it trivial to execute the actions in sandboxes (literally just switch out `subprocess.run` with `docker exec`) and to scale up effortlessly.」**

**官方对「一年后为什么不需要 ACI」的解释**【一手】：

> Back then, we placed a lot of emphasis on tools and special interfaces for the agent. **However, one year later, as LMs have become more capable, a lot of this is not needed at all to build a useful agent!**

**官方给出的「何时用哪个」的决策表**【一手】：

| 用 mini-SWE-agent 如果你 | 用 SWE-agent 如果你 |
| --- | --- |
| 想要一个能本地工作的快速命令行工具 | 想实验**不同工具集，各自有各自的接口** |
| 想要**非常简单的控制流** | 想实验**不同的 history processors** |
| 想要更快、更简单、更稳定的沙箱与基准评测 | |
| 做 FT 或 RL，**不想过拟合到某个特定 agent scaffold** | |

**性能与部署**【一手】：SWE-bench verified **>74%**；支持 local、docker/podman、singularity/apptainer、bubblewrap、contree 等环境；通过 litellm、openrouter、portkey 支持所有模型。

**采用者**【一手】：Meta、NVIDIA、Essential AI、IBM、Nebius、Anyscale、Princeton、Stanford 等。

→ https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/README.md

**⚠️ 这是本次调研中最重要的一条设计教训**：SWE-agent 团队用一年时间**主动推翻了自己的 ACI 结论**——当模型变强时，**复杂的工具脚手架带来的收益下降，而它的复杂度、脆弱性与过拟合风险持续存在**。目标 harness 应当**按模型能力分档设计复杂度**，并为「模型变强后删掉某些机制」留出可能。

### 5.3 OpenHands

**定位与许可**【一手】：论文标题即定位——「An Open Platform for AI Software Developers as **Generalist Agents**」，「a platform for the development of powerful and flexible AI agents that **interact with the world in similar ways to those of a human developer: by writing code, interacting with a command line, and browsing the web**」。论文明确列举其设计目标包含：「**safe interaction with sandboxed environments for code execution, coordination between multiple agents, and incorporation of evaluation benchmarks**」。**MIT 许可**，ICLR 2025 接收。
→ https://arxiv.org/abs/2407.16741

**当前形态（重要变化）**【一手】：仓库现已演进为 **"Agent Canvas"——「a self-hosted developer control center for coding agents and automations」**，并明确说明：

> 「Agent Canvas runs the **open source OpenHands agent out-of-the-box, but can use any third-party agent like Claude Code and Codex**.」
> 「**Use with any agent** — Use with OpenHands, Claude Code, Codex, Gemini, or **any agent with Agent-Client Protocol (ACP)**.」

**即 OpenHands 已经从「一个 agent」演进为「一个 agent 编排/控制平面」。** 这是一个值得注意的战略转向：**harness 供应商正在把「支持任意 agent」当作产品护城河**（与 opencode 的 ACP 支持、Hermes 的 ACP adapter 是同一趋势）。

**多仓库边界（很清晰的架构分层，值得学）**【一手】：

| 仓库 | 职责 |
| --- | --- |
| `OpenHands/OpenHands` | Agent Canvas 前端、面向用户的控制中心、后端选择、本地栈编排 |
| `OpenHands/software-agent-sdk` | **Python SDK、Agent Server、agents、tools、conversations、workspaces、events，以及规范的服务端 API** |
| `OpenHands/typescript-client` | 浏览器兼容的 Agent Server API TypeScript 客户端 |
| `OpenHands/automation` | 自动化定义、调度、webhooks、运行历史、派发 |

**职责划分的关键句**【一手】：

> 「**The automation service decides when work runs and dispatches conversations to the Agent Server/SDK, which decides what runs.**」

**「何时跑」与「跑什么」被拆到两个服务**——这是一个对常驻 agent 非常有用的架构分离。

**运行时与沙箱的三种模式（官方明示的风险差异）**【一手】：

1. **无沙箱**（`npm install -g @openhands/agent-canvas; agent-canvas`）——官方原文警告：
   > ⚠️ 「This runs the agent-server **directly on the machine you're installing on — the agent will have full access to your filesystem!**」
2. **Docker 沙箱**——`docker run` 挂载 `~/.openhands` 与 `PROJECTS_PATH`；「The agent will be able to access any project under `PROJECTS_PATH`.」
3. **每会话一个 Docker 容器**（`OH_CONVERSATION_RUNTIME=docker`）——「Run each new conversation in its own Docker container, with its own Agent Server and tools. This is useful for running several agents concurrently.」
   - 「Each container mounts its conversation's workspace and persisted state, so **workspace files and conversation history survive container replacement**.」
   - 官方诚实指出局限：「**Conversations using the same host workspace still share those files; choose separate directories or worktrees to avoid conflicting edits.**」
   - 并明确边界：「This setting isolates conversation execution; **it does not move the entire Canvas or automation service into a sandbox.**」

**网络暴露的安全设计（很细致）**【一手】：

- 本地（`npx` / `npm run dev`）监听器**只绑 loopback（`127.0.0.1`）**，「so the auto-injected session key is not reachable from other machines on the network」
- 要监听所有接口需 `--host 0.0.0.0`（或 `OH_BIND_HOST`），此时**不再注入 session key**，UI 改用与 `--public` 相同的 API key 输入界面
- Docker 监听所有容器接口以便端口发布，**但默认不把 session key 注入 HTML**；quickstart 显式开启注入的同时**只把宿主端口发布在 `127.0.0.1`**
- 「**If you publish Docker on a LAN or public interface, omit `AGENT_CANVAS_ALLOW_LAN_SESSION_KEY` and enter the API key in the UI.**」

**这里的原则很清晰：便捷性与暴露面严格挂钩，且「方便」默认只在本机生效。**

**关于 OpenHands 的 Condenser / 事件流架构**：本次调研**未取得** OpenHands 关于 `EventStream`、`AgentController`、`Condenser`、`LLMSummarizingCondenser`、`max_message_chars`、`SecurityAnalyzer` 等内部机制的官方一手文档（文档站 `/openhands/usage/architecture/backend` 的正文是图片，`docs.all-hands.dev` 重定向到 `docs.openhands.dev`，相关内部细节页未在本次抓取范围内取到）。**以上名词在本次调研中未获得一手确认，故不写入。**

→ https://raw.githubusercontent.com/OpenHands/OpenHands/main/README.md 、 https://docs.openhands.dev/openhands/usage/architecture/backend

### 5.4 三者（+ SWE-agent）哲学差异小结

| 维度 | SWE-agent | mini-SWE-agent | OpenHands |
| --- | --- | --- | --- |
| 定位【一手】 | 定制 **ACI**（工具与交互格式）以提升 agent 表现 | 「**100x simpler, and still worked nearly as well**」 | **generalist agent**，像人类开发者一样写代码、用命令行、浏览网页 |
| 工具面 | 精心设计的专用工具（file viewer、editor、search）+ linter 门控 | **只有 bash** | 未取得一手细节 |
| 历史处理 | 可插拔 `HistoryProcessor` | **完全线性 append**，轨迹 == 消息 | 未取得一手细节 |
| 隔离 | Docker / modal / aws（经 SWE-ReX） | local / docker / podman / singularity / bubblewrap / contree；**动作用 `subprocess.run`，天然易沙箱化** | 无沙箱 / Docker 沙箱 / **每会话一个容器**（三种模式官方明示风险差异） |
| 配置 | YAML（templates/models/tools/environments/demos/parsers/bundles） | YAML + Python bindings | 多仓库架构；Canvas/SDK/client/automation 分离 |
| 面向 | 基准评测与 ACI 研究 | **日常工具 + FT/RL 基线**（「不想过拟合到某个 scaffold」） | **自托管控制中心**，可驱动任意 ACP agent |
| 自陈取舍 | ACI 设计影响表现（论文核心贡献） | **模型变强后 ACI「a lot of this is not needed at all」** | 沙箱内外差异巨大，**「agent will have full access to your filesystem」** |

---

## 6. 横向对比表

### 6.1 主循环

| | opencode | Hermes | Claude Code | SWE-agent / mini | OpenHands |
| --- | --- | --- | --- | --- | --- |
| **形态**【一手】 | 单会话迭代循环；`tool_calls` → 执行 → 回填 → 再请求 | `AIAgent` 同步编排引擎；`run_agent.py` 是门面，循环在 `agent/conversation_loop.py` + `agent/turn_*.py` | agentic loop：`PreToolUse → PermissionRequest → PostToolUse/Failure/Batch`，直到 `Stop` | `Agent.forward()`：prompt → parse action → 在 env 执行 → observation | 未取得一手细节（现为 Agent Canvas 控制平面） |
| **迭代上限**【一手】 | `steps`；**未设置则无隐式上限**，跑到模型停或用户中断；到限时**切换为「总结模式」** | `IterationBudget` 默认 **500**；subagent 独立预算默认 **50**（总数可超 parent） | 未取得明确数值上限 | 论文/文档描述有 step 与 cost 限额；**具体数值未取得** | 未取得 |
| **可中断性**【一手】 | 未明确描述 | **显式设计**：`_interruptible_api_call()` 后台线程 + 取消；**不注入半截响应** | `Esc` 停止、`/rewind` 回滚 | 未取得 | 未取得 |
| **多 API 协议**【一手】 | 多 provider | **三模式**：`chat_completions` / `codex_responses` / `anthropic_messages`，归一到 OpenAI 内部格式 | 原生 Anthropic | 多 provider（litellm） | 未取得 |
| **消息不变量**【一手】 | 未明示 | **严格角色交替**，绝不连续两条 assistant/user；**只有 tool 允许连续** | 未明示 | 无（迷你版历史完全线性） | 未取得 |
| **循环可观测性**【一手】 | 插件事件（`session.*`、`tool.execute.*`） | **8 个 callback**（tool_progress / thinking / reasoning / clarify / step / stream_delta / tool_gen / status） | **31 个 hook 事件**，按 session / turn / tool-call 三种节奏分类 | 未取得 | 未取得 |
| **死循环防护**【一手】 | **`doom_loop`**：同工具同输入重复 3 次（默认 `ask`） | 未明确描述（有 iteration budget 兜底） | **`StopFailure`** 事件、`--max-budget-usd` | 未取得 | 未取得 |

### 6.2 工具系统

| | opencode | Hermes | Claude Code | SWE-agent / mini | OpenHands |
| --- | --- | --- | --- | --- | --- |
| **定义/注册**【一手】 | 内置 + 自定义工具（Zod schema）+ 插件工具 + MCP；**插件工具可覆盖同名内置工具** | **导入即自注册** + **AST 扫描 `tools/*.py` 自动发现**；70+ 工具 / 约 28 toolsets；**跨 toolset 遮蔽被拒，除非 `override=True`，插件覆盖需操作者显式开启** | 内置工具集 + MCP + 插件；MCP 有命名空间 `mcp__server__tool` | ACI 元素作为 custom tools 装进 shell 会话（SWE-ReX）；mini **只有 bash** | 未取得 |
| **schema 描述**【一手】 | Zod → 工具定义 | 工具文件自带 `schema={...}` + `ToolEntry` 元数据（`description`/`emoji`）；**`schema["description"]` 是面向模型的权威描述**，`description=` 参数只进注册表元数据，**不写进给模型的 schema** | 未取得 schema 格式细节 | YAML tool config + tool bundle | 未取得 |
| **可用性过滤**【一手】 | 未明确 | **`check_fn`**：返回 False 或抛异常 → 工具**从 schema 中整个移除**；结果按调用缓存 | 组织可把工具设为 `blocked`，**在 Claude 看到之前过滤掉** | 未取得 | 未取得 |
| **动态 schema 修补**【一手】 | 未明确 | **有**：`execute_code` / `browser_navigate` 的 schema 被裁剪为只引用通过过滤的工具，**防模型幻觉出不可用工具** | 未取得 | 未取得 | 未取得 |
| **并行调用**【一手】 | 未明确 | **有**：多 tool call 走 `ThreadPoolExecutor`；**interactive 工具（如 `clarify`）强制串行**；**结果按原始顺序重新插入** | **有**：`PostToolBatch` = 「a full batch of parallel tool calls resolves」 | 未取得 | 未取得 |
| **错误包装**【一手】 | 未明确 | **两层 try/except**，保证模型永远收到格式良好的 JSON，绝非未处理异常 | 未取得 | 未取得 | 未取得 |
| **大输出处理**【一手】 | `read` 支持行范围 | 压缩 Phase 1 剪掉 >200 字符的旧工具结果；`additionalContext` >10,000 字符落盘给路径 | **MCP 输出 >10,000 token 告警，默认上限 25,000 token；超限存文件并给路径**；单工具可用 `_meta["anthropic/maxResultSizeChars"]` 提额，**硬顶 500,000 字符** | ACI 文件查看器**每轮只显示 100 行**；搜索**只列出有匹配的文件名** | 未取得 |
| **特殊工具**【一手】 | `question`（执行中问用户）、`todowrite`、`skill`、`lsp` | 四个 agent 级前置拦截工具：`todo_list`、`memory`、`session_search`、`delegate_task` | `ToolSearch`、`WaitForMcpServers`、`EndConversation`、`TaskCreate/Update` | linter 门控 edit | 未取得 |

### 6.3 上下文管理

| | opencode | Hermes | Claude Code | SWE-agent / mini | OpenHands |
| --- | --- | --- | --- | --- | --- |
| **历史存储**【一手】 | 会话；子代理创建子会话 | **SQLite + FTS5**（`~/.hermes/state.db`），会话谱系，按平台隔离，原子写 | 会话 transcript（`~/.claude/projects/`），有 `--resume` | 内存/文件；mini 完全线性 | 未取得 |
| **压缩触发**【一手】 | **隐藏系统 agent `compaction`**，自动按需运行 | **双层**：Gateway hygiene **85%**（安全网）+ Agent compressor **50%**（可配，默认） | auto-compact（接近上限时摘要），阈值可配（`/docs/en/model-config#set-the-auto-compact-window`） | `HistoryProcessor` 压缩历史以「make the best use of the context window」 | 未取得 |
| **压缩算法**【一手】 | 未公开细节 | **4 阶段**：① 剪旧工具结果（无 LLM）→ ② 定边界（token 预算，对齐 tool 对）→ ③ 结构化摘要（9 个固定分节）→ ④ 组装 + 清理孤儿 tool 对 | 摘要「preserves architectural decisions, unresolved bugs, and implementation details」；**保留最近访问的 5 个文件** | 未取得 | 未取得 |
| **尾部保护**【一手】 | 未公开 | `protect_last_n` 默认 **20**、`protect_first_n` **3**（硬编码）、`min_tail_user_messages` 默认 **1**（**优先于 token 预算**）；尾部预算**封顶窗口 20%** | 未取得具体数值 | 未取得 | 未取得 |
| **迭代式摘要**【一手】 | 未公开 | **有**：上次摘要交给 LLM 要求「更新」而非重来 | 未取得 | 未取得 | 未取得 |
| **激进压缩 + 可回取**【一手】 | 未公开 | **lean 模式**：尾只留 2.5%（10K–25K），连续性放进摘要——含**正则抽取的 anchor index**（PR/SHA/路径/错误串，绝不改写）、**所有真实用户消息原文**、**`session_search` 恢复指针**。500K 会话实测保留 ~49K（vs legacy ~162K）**且召回更高** | 未取得 | 未取得 | 未取得 |
| **微压缩（分期付款）**【一手】 | 无 | **有但默认关**（`micro_compact: false`）：每轮吸收一个最旧 exchange 进滚动摘要；**用户消息永不被压缩**；defrag 阈值 2000 token；**代价是每轮打断 prompt cache** | 未取得类似机制 | 无 | 未取得 |
| **用户消息保护**【一手】 | 未公开 | **微压缩刻意跳过所有用户消息**（理由：指令是不可从后续工作重建的意图来源） | 未取得 | 未取得 | 未取得 |
| **压缩失败处理**【一手】 | 未公开 | **极详尽**：递增冷却 60→300→900s；三条强制尝试路径（`/compress`、`fallback_chain`、provider 证实溢出）；重复停滞→**确定性兜底摘要**；provider 过载→中止保留 transcript，**连续 3 次过载后降级为有界中间窗口损失**（好过 `compression_exhausted` 丢全会话） | 未取得 | 未取得 | 未取得 |
| **可插拔压缩**【一手】 | **插件钩子可注入或整体替换压缩 prompt**（`experimental.session.compacting`） | **`ContextEngine` ABC**，`context.engine` 选择；**插件引擎永不自动激活**；内置 `compressor` 为默认 | 未取得 | **`HistoryProcessor` 可插拔**（官方列为其相对 mini 的优势之一） | 未取得 |
| **分层记忆**【一手】 | `AGENTS.md`（项目级，建议提交 Git） | **MEMORY.md（2200 字符）+ USER.md（1375 字符）**，冻结快照注入 system prompt；**超限报错强制 agent 同轮腾地方**；条目被注入/渗出扫描；**外部 provider 并行不替代** | **CLAUDE.md 层级**（User/Project/Local/Managed）+ `.claude/rules/*.md`（支持 `paths:` 条件激活）；**惰性加载**；**压缩后重新加载**；官方建议 **<200 行** | 无 | 未取得 |
| **跨会话检索**【一手】 | 未找到 | **`session_search`**：FTS5，**返回真实消息、无摘要无截断**，~20ms 查询 / ~1ms 滚动，**零 LLM 成本** | `/resume`、`/sessions`、resume-from-a-summary | 无 | 未取得 |
| **Prompt cache**【一手】 | **未找到** | **Anthropic `cache_control`，策略 `system_and_3`**，官方称多轮输入成本降约 75%；**system prompt 不在会话中途变化**（设计原则） | **有**：cache 生命周期 1h（订阅）/ 5min（credits/API）；`/usage` 显示命中率与 miss 原因；**压缩或清理工具结果算 expected rebuild** | 无 | 未取得 |
| **Prompt 分层**【一手】 | 未公开 | **stable → context → volatile** 三层 | 未取得明确分层 | 未取得 | 未取得 |

### 6.4 子代理 / 多代理

| | opencode | Hermes | Claude Code | SWE-agent / mini | OpenHands |
| --- | --- | --- | --- | --- | --- |
| **有/无**【一手】 | **有**：primary + subagent 两类 | **有**：`delegate_task` + `/bg` + Bot Mode + MoA + background review fork | **有**：subagents、agent teams（实验）、dynamic workflows | mini **无**；SWE-agent 未取得 | 论文明确以「**coordination between multiple agents**」为设计目标 |
| **上下文隔离**【一手】 | **子会话**，可用键位在父子会话间导航（`session_child_first`/`cycle`/`parent`） | subagent 有**隔离上下文**与**独立迭代预算**；`/bg` 后台 agent **对当前聊天上下文一无所知，只收到那个 prompt** | **clean context windows**；每个 subagent **有自己的上下文窗口**；worktree 可做文件级隔离 | — | 未取得 |
| **结果回传**【一手】 | 未找到 | `/bg` 结果发回**发起命令的同一 chat/channel**；`delegate_task` 回传细节未找到 | **只回传「condensed, distilled summary」（通常 1,000–2,000 token）**，而子代理自己可能烧掉几万 token | — | 未取得 |
| **模型选择**【一手】 | subagent **默认继承调用它的 primary agent 的模型**，可各自覆盖 | 各自可配；background review 可路由到便宜模型（官方基准便宜 **3–5×**，且**自动改重放紧凑 digest** 以少写新缓存） | **可指定 `model: haiku`**；Opus 切换会传染给继承会话模型的 subagent | — | 未取得 |
| **成本代价（官方数字）**【一手】 | 未公开 | background review 未设上限时由上下文窗口 75%（封顶 **600K**）派生；可 `enabled: false` 或调低 nudge 间隔 | **agent teams 在 plan mode 下约 7× token**；每个活跃 teammate 持续消耗直到退出 | — | 未取得 |
| **并行**【一手】 | `general` 子代理官方明说「to run multiple units of work in parallel」 | 官方称「Spawn isolated subagents for parallel workstreams」 | agent teams / dynamic workflows 可并行 | — | 未取得 |
| **工具继承与裁剪**【一手】 | subagent 有 `permission` 覆盖；`todowrite` **默认对 subagent 禁用** | 未明确描述工具继承 | 可预加载 skills 进子代理；可把 MCP server 作用域限定到子代理；**subagent 里也跑主会话配置的 hooks**（需靠 `agent_id`/`agent_type` 区分） | — | 未取得 |

### 6.5 成本控制

| | opencode | Hermes | Claude Code | SWE-agent / mini | OpenHands |
| --- | --- | --- | --- | --- | --- |
| **迭代预算** | `steps`（未设为无上限） | **500** 默认 / subagent **50** | `--max-budget-usd`；未取得迭代上限 | 论文述有 step/cost 限额 | 未取得 |
| **模型分级**【一手】 | **按 agent** 配 `model`（plan 用 haiku、build 用 sonnet） | **按任务类型**配（`auxiliary.compression` / `background_review` / vision / web extraction 各有 provider/model/fallback） | **按 agent 与 subagent** 配（Opus / Sonnet / Haiku；subagent 可 `model: haiku`）；`/model` 中途切换 | 多模型经 litellm | 未取得 |
| **Prompt cache**【一手】 | 未找到 | Anthropic `cache_control`，`system_and_3`；**微压缩会每轮打断缓存（官方主动劝退）** | 完整支持，且有 `prompt-cache` 专页与 miss 原因归因 | 无 | 未取得 |
| **token 计量**【一手】 | 未公开 | **usage anchor**（provider 真实 token + 追加消息粗估，带内容指纹，**持久化到 session 行以扛住重读历史**）；**图片成本按学到的每图成本计价**；**不透明 blob 贡献 0**；每轮压缩输出**无内容 JSON 遥测** | `/usage` 本地按 token×标价算；**`modelPricing` managed setting 可用合同价**；OpenTelemetry 实时导出；Analytics API | 未取得 | 未取得 |
| **成本可见性**【一手】 | 未公开 | `session_model_usage` 表按 task 分类持久化用量 | `/usage`（含 **Prompt cache 行**、**按 skill/subagent/plugin/MCP server 归因**、behavior flags ≥10%）、`/insights`、`/skill-doctor` | 未取得 | 未取得 |
| **官方成本基准**【一手】 | 无 | 无 | **约 $13/开发者/活跃日，$150–250/月；90% 低于 $30/活跃日**；agent teams 约 7×；后台功能 **< $0.04/会话** | 无 | 无 |
| **控本旋钮**【一手】 | 每 agent 的 `temperature`、`model`、`steps` | `compression.*`、`auxiliary.*`、`background_review.enabled`、nudge 间隔、`micro_compact_every_n_turns` | `/clear`、`/compact <instructions>`、`/effort`、`MAX_THINKING_TOKENS`、CLAUDE.md <200 行、MCP 工具搜索、CLI 替代 MCP、code intelligence 插件 | 简化 scaffold（mini 的整个论点） | 未取得 |
| **缓存与压缩的冲突是否被承认**【一手】 | n/a | **是，且主动劝退**（微压缩） | **是**（`/reload-mcp` 会作废 prompt cache，因为工具 schema 在 system prompt 里） | n/a | 未取得 |

### 6.6 扩展机制

| | opencode | Hermes | Claude Code | SWE-agent / mini | OpenHands |
| --- | --- | --- | --- | --- | --- |
| **Skills**【一手】 | `SKILL.md`；搜索 `.opencode/skills/`、`~/.config/opencode/skills/`、**`.claude/skills/`**、**`.agents/skills/`**；**列在 `skill` 工具的 description 里**；名称正则 `^[a-z0-9]+(-[a-z0-9]+)*$` | `~/.hermes/skills/`；**兼容 agentskills.io 标准**；**三级渐进披露**（`skills_list` ~3k token → `skill_view(name)` → `skill_view(name, path)`）；**每个 skill 自动成 slash 命令，可叠最多 5 个**；**条件激活**（`fallback_for_toolsets` / `requires_toolsets`）；平台限定；**项目 skill 需显式 trust + 扫描期隔离** | `SKILL.md` + **Agent Skills 开放标准**（agentskills.io）；**三级渐进披露，正文「effectively unbounded」**；7 个位置 + 明确优先级；`context: fork` 在子代理里跑 skill；**动态上下文注入 `` !`cmd` ``**；`allowed-tools`、`disable-model-invocation`；**`skill-creator` 评测闭环** | 无 | 有 Skills 文档页（**本次未展开**） |
| **Hooks**【一手】 | **插件事件**：`tool.execute.before/after`、`session.*`、`permission.*`、`file.*`、`shell.env`、`tui.*`、`lsp.*`、`message.*`、`todo.updated`、`server.connected`、`command.executed`、`installation.updated` | **插件 hook**：至少 `pre_tool_call` / `post_tool_call`；`gateway/hooks.py`；`gateway/builtin_hooks/` 是**空的内置 hook 扩展点** | **31 个事件**；5 种 handler 类型（command / HTTP / mcp_tool / prompt / agent）+ 插件内 **JS 函数（mod）**；**matcher 三层 + `if` 条件**；事件分化的决策控制；**`updatedInput` / `updatedToolOutput` 可改写内容**；hooks 也在子代理里跑 | 无 | 有 Hooks 文档页（`/openhands/usage/customization/hooks`，**本次未展开**） |
| **Plugins**【一手】 | JS/TS 模块；`.opencode/plugins/`、`~/.config/opencode/plugins/`、**npm 包（启动时 Bun 自动装，缓存到 `~/.cache/opencode/node_modules/`）**；可注册工具、hook、事件；**同名工具覆盖内置** | **三来源**：`~/.hermes/plugins/`、`.hermes/plugins/`、**pip entry points**；通过 context API 注册工具/hook/CLI 命令；**两类专门插件（memory provider / context engine）都是单选** | 可捆绑 skills / agents / hooks / MCP servers / commands；**有 marketplace、评估（`claude plugin eval`）、持久数据目录、路径变量**；`strictPluginOnlyCustomization` 策略 | 无 | 有 Plugins / Agent Plugins Packages 文档页（**本次未展开**） |
| **MCP**【一手】 | 支持；通配符授权 `"mymcp_*": "ask"` | **支持**；MCP 工具作为**动态 toolset**；`tools/mcp_tool_discovery.py`；官方强调可「**filter their tools**」 | **支持**：`http`（推荐，HTTP 优先失败回落 SSE）/ `sse`（废弃）/ `stdio` / **`ws`** / `sdk`；**工具搜索默认推迟 MCP 工具定义**；命名空间 `mcp__server__tool`；三类 scope（local/project/user）+ managed；**项目 server 需 workspace trust** | 无 | **有** MCP 文档页（**本次未展开**） |
| **其他协议**【一手】 | **ACP 支持** | **ACP adapter**（VS Code / Zed / JetBrains，stdio/JSON-RPC）；**Batch runner**；**API server**；**trajectories（ShareGPT）**；**RL（Atropos）** | **Agent SDK**；Channels（外部事件推入会话）；Deep links；Artifacts | SWE-ReX（部署抽象）；轨迹文件 | **ACP**（可驱动 Claude Code / Codex / Gemini / 任意 ACP agent） |
| **自定义工具**【一手】 | Zod schema 的 `tool({...})`；**插件工具与内置同名时插件优先** | `registry.register(...)` + AST 自动发现；跨 toolset 遮蔽需 `override=True` | 未取得 | YAML tool config + tool bundle | 未取得 |
| **配置语言**【一手】 | JSON（`opencode.json`）+ Markdown frontmatter | YAML（`config.yaml`）+ Markdown frontmatter | JSON settings（4 层优先级）+ Markdown frontmatter | YAML（`config/`） | 未取得 |

### 6.7 权限与安全

| | opencode | Hermes | Claude Code |
| --- | --- | --- | --- |
| **危险操作拦截**【一手】 | **三态权限**（allow/ask/deny）× 细粒度对象语法 + 通配符；**last matching rule wins**，惯用法是兜底 `*` 放最前 | **危险命令模式匹配 + 审批**；`approvals.mode`: **smart（默认，辅助 LLM 评风险）/ manual / off**；**审批超时默认 300s，超时即拒绝** | **权限模式**（default / acceptEdits / plan / bypassPermissions / auto / dontAsk）+ `allow`/`deny` 规则（支持 `Tool(specifier)` 与参数匹配） |
| **默认策略**【一手】 | 多数 `allow`；**`doom_loop` 与 `external_directory` 默认 `ask`**；**`.env` 默认 deny** | **gateway 默认拒绝所有不在 allowlist/未 pair 的用户**；无人值守面（cron / `-q` / webhook）**默认 `deny` 且立即阻断而非等超时** | **未信任的仓库不能批准自己的 MCP server**；workspace trust dialog |
| **不可覆盖的底线**【一手】 | `deny` 规则在 `--auto` 下**仍然生效**（auto 只改变本来会 ask 的请求） | **硬底线黑名单**（`rm -rf /`、fork bomb、`mkfs.*`、`dd of=/dev/sd*`、rootfs 顶层 pipe-to-shell）+ **用户 `approvals.deny` glob**，**都在 `--yolo` / `mode: off` 之前触发，无覆盖开关**；shell 引号无法解析也走 floor（fail-closed） | managed settings 的组织级 `ask`/`blocked` 工具控制；`allowManagedHooksOnly` |
| **沙箱**【一手】 | **未找到**官方容器/OS 级沙箱；主要在权限层管控 | **7 种终端后端**（local/Docker/SSH/Daytona/Modal/Singularity/Vercel）+ **Docker 加固**（`--cap-drop ALL` + 仅 DAC_OVERRIDE/CHOWN/FOWNER、`no-new-privileges`、`--pids-limit 256`、限尺寸 tmpfs）；**`HERMES_WRITE_SAFE_ROOT` 写沙箱**；**容器内跳过危险命令检查**（容器即边界） | 有 `/docs/en/permissions`、`/docs/en/security`、`--dangerously-skip-permissions`；sandbox 具体机制**本次未取得** |
| **官方承认的边界**【一手】 | 未宣称是硬隔离 | **明确、反复承认**：写保护只覆盖 `write_file`/`patch`，**`terminal` 以同一 OS 用户运行仍可绕过**；deny 规则是命令策略**不是 OS 能力沙箱**；「**does not sandbox a hostile or compromised agent**」 | 未取得同等直白的自陈 |
| **凭据保护**【一手】 | `.env` 默认 read-deny（但 `bash` 可绕过） | 受保护路径（`~/.ssh/`、`~/.aws/`、`~/.kube/`、`/etc/sudoers`、`~/.netrc`、Bitwarden 缓存、`vault/`、`mcp-tokens/`）；**Windows NT 设备命名空间路径读写都拒**（防 NTLM hash 泄露）；`.env` **read-denied 但可写**；`~/.ssh/config` 走审批而非硬阻断；**MCP 子进程环境变量隔离** | **仓库/插件提供的 `headersHelper` 运行时移除所有名字含 TOKEN/SECRET/PASSWORD/KEY/AUTH 的环境变量**；`url` 里对应值替换为 `REDACTED`；**trust 前置** |
| **多人授权**【一手】 | n/a（单用户） | **六步检查顺序** + **DM pairing**（8 字符码、密码学随机、1h TTL、10min 限流、3 个待批上限、5 次失败锁 1h、文件 0600、码不写 stdout）+ **Admin/User 分档（按平台按作用域）** | n/a |
| **无人值守策略**【一手】 | n/a | **专门的三组键**：`cron_mode` / `single_query_mode` / `unattended_mode`，默认都是 `deny` | `dontAsk` 模式（从不提示，直接拒绝） |
| **生命周期护栏**【一手】 | n/a | **不可覆盖**：禁止从受监督进程内部停/重启 gateway；拒绝针对 `python` 解释器镜像的进程杀手（因 gateway 字面上是 `python` 进程） | 未取得 |

---

## 7. ★ 共性模式（多个项目都在做的事 = 被验证过的做法）

以下每一条都在 **≥2 个项目**中独立出现，且多为一手文档明确表述。**这些是最值得直接采纳的部分。**

### 7.1 「元数据常驻、正文按需」的渐进式披露

- **Claude Code**【一手】：三级 disclosure（元数据 → SKILL.md → 打包文件）；核心经济论点：「**the amount of context that can be bundled into a skill is effectively unbounded**」
- **Hermes**【一手】：`skills_list`（~3k token）→ `skill_view(name)` → `skill_view(name, path)`
- **opencode**【一手】：skills 列在 `skill` 工具的 description 里，按需 `skill({name})` 加载

**共同点**：把「有哪些能力」和「能力的具体内容」分开计价。**这是解决「上下文有限 vs 知识无限」唯一被三个独立团队验证过的方案。**

**且三者都收敛到同一种文件格式**（`SKILL.md` + YAML frontmatter + `references/` 子目录），并共同指向 **agentskills.io 开放标准**（Anthropic 2025-12-18 发布；Hermes 官方称兼容；Claude Code 官方称遵循）。

### 7.2 压缩是必然的，且必须有兜底

- **opencode**【一手】：一个**隐藏的系统 agent** 负责 compaction
- **Hermes**【一手】：**双层**（85% 安全网 + 50% 主压缩）+ **完整失败阶梯**（60→300→900s 冷却 → 确定性兜底摘要 → 过载时中止保留 transcript → 连续 3 次过载降级为有界损失）
- **Claude Code**【一手】：auto-compact + 官方 context engineering 文章把 compaction 列为长时程三技术之首
- **SWE-agent**【一手】：`HistoryProcessor` 专门负责压缩历史

**共同点**：
1. **压缩是框架的责任，不是靠模型自己「记住要点」**
2. **压缩必须有降级路径**——Hermes 的失败阶梯（冷却 → 确定性兜底 → 有界损失）是最完整的公开实现
3. **压缩的触发阈值必须按模型窗口分别设定**（Hermes 的 `model_thresholds` 子串匹配 + 小窗口下限只抬不降；Claude Code 的 auto-compact window 可配）

### 7.3 「旧工具结果」是最安全、最先被清理的东西

- **Hermes**【一手】：压缩 **Phase 1** 就是无 LLM 调用的工具结果剪除（>200 字符 → `[Old tool output cleared to save context space]`）
- **Claude Code**【一手】：官方称 tool result clearing 是「**one of the safest lightest touch forms of compaction**」，并反问「once a tool has been called deep in the message history, why would the agent need to see the raw result again?」
- **Claude Code**【一手】：MCP 输出超限落盘给路径（等价于「保留可回取句柄，丢弃正文」）
- **Hermes**【一手】：lean 尾部把旧工具结果降级为**带恢复指针的单行 stub**

**共同点**：**丢弃工具结果正文 + 保留「去哪儿取回」的指针**。这是压缩中投入产出比最高的一步，且**不需要 LLM 调用**。

### 7.4 上下文压缩与 prompt cache 是直接的取舍关系，且必须显式承认

- **Hermes**【一手】：微压缩**默认关闭**，理由是「**A micro-compaction pass rewrites already-sent history... so with micro-compaction on, you break the cache every turn instead of once per batch compaction.**」并给出对照表（停顿 vs 占用 vs cache prefix）
- **Hermes**【一手】：设计原则「**Prompt stability** — System prompt doesn't change mid-conversation」
- **Claude Code**【一手】：`/reload-mcp` 会询问，因为**重建 MCP 工具集会作废 prompt cache（工具 schema 在 system prompt 里）**；`/usage` 区分 cache miss 与 **expected rebuild（压缩/清理工具结果）**

**共同点**：**「改写已发送的历史」和「保持缓存前缀」不可兼得。** 两个团队都把这个代价**写在文档里让人自己权衡**，而不是藏在实现里。**目标 harness 若做缓存，必须内建这个度量与开关。**

### 7.5 子代理 = 干净上下文 + 只回传摘要

- **Claude Code**【一手】：明确数字——子代理「might explore extensively, using tens of thousands of tokens or more, but **returns only a condensed, distilled summary of its work (often 1,000-2,000 tokens)**」
- **Hermes**【一手】：subagent 有**隔离上下文**与**独立迭代预算**；`/bg` 后台 agent「**has no knowledge of your current chat context and receives only the prompt you provide**」
- **opencode**【一手】：subagent 创建**子会话**
- **OpenHands 论文**【一手】：把「coordination between multiple agents」列为平台设计目标

**共同点**：子代理的价值**不在于并行，而在于上下文隔离**——它是一个「把大量 token 烧在别处、只把结论带回来」的装置。

**附带共性**：
- **子代理需要独立的成本上限**（Hermes 独立 iteration budget；Claude Code agent teams 约 7× token 并建议保持小、早关）
- **子代理默认继承父模型，但应能降级**（opencode 继承 primary 的模型；Claude Code 建议 `model: haiku`；Hermes 的 background review 可路由到便宜模型且**自动改用紧凑 digest**）
- **子代理也要跑父的 hooks，所以 hook 输入必须带 agent 身份**（Claude Code 的 `agent_id` / `agent_type`）

### 7.6 危险操作 = 模式匹配 + 人机回路 + 不可覆盖的底线

- **Hermes**【一手】：三层——`approvals.mode` (smart/manual/off) → 危险模式列表 → **硬底线黑名单（YOLO 也不能覆盖）**；加上用户可编辑的 `approvals.deny` glob
- **Claude Code**【一手】：权限模式 + allow/deny 规则 + managed settings 的组织级控制；`dontAsk` 模式从不提示直接拒绝
- **opencode**【一手】：三态权限 + 细粒度规则；**`deny` 规则在 `--auto` 下仍生效**
- **Hermes 的 `smart` 模式**用**辅助 LLM 评风险**自动批准低风险命令

**共同点**：
1. **默认拒绝**（Hermes gateway 无 allowlist 时拒绝所有用户；无人值守面默认 deny）
2. **存在一个连「关闭安全」都不能覆盖的底线**（Hermes 硬线黑名单 / opencode 的 `deny` 不被 `--auto` 覆盖 / Claude Code 的 managed settings）
3. **审批的超时行为必须明确定义**（Hermes：300s 超时即拒绝，且**超时不算一次拒绝，重问不受罚**）
4. **无人值守场景需要独立的策略档**（Hermes 的 `cron_mode` / `single_query_mode` / `unattended_mode`；Claude Code 的 `dontAsk`）

### 7.7 「权限规则应该由工具自己定义语法」

- **opencode**【一手】：`bash` 权限**匹配解析后的命令**（如 `git status --porcelain`）；`always` 批准的模式集合**由工具提供**（bash 通常只白名单安全命令前缀，如 `git status*`）
- **Claude Code**【一手】：`if` 条件用 `Bash(rm *)` —— 对 **Bash 子命令**匹配，而非原始正则
- **Hermes**【一手】：审批的 `command_allowlist` 可接受**危险模式的规则键**（如 `script execution via heredoc`）而非命令字面量

**共同点**：**不要强迫用户为每个工具手写字符串匹配规则。** 「什么算同一个操作」是工具自己的知识。

### 7.8 大输出落盘 + 返回路径

- **Claude Code**【一手】：MCP 输出 >10,000 token 告警，超 25,000 token（默认）**存文件并在对话里替换为文件路径**；`additionalContext` >10,000 字符同样落盘
- **Hermes**【一手】：lean 压缩后旧工具结果变**带恢复指针的单行 stub**；`session_search` 提供零 LLM 成本的原始记录回取
- **SWE-agent**【一手】：文件查看器**每轮只显示 100 行**；搜索**只列有匹配的文件名**（「Showing the model more context about each match proved to be too confusing」）

**共同点**：**「先给句柄，需要时再取」优于「一次给全」。** 这与 Anthropic 的 just-in-time 上下文策略是同一原则。

### 7.9 Hook / 事件是循环阶段的外化

- **Claude Code**【一手】：**31 个事件**，官方按 **per session / per turn / on every tool call** 三种节奏分类；5 种 handler 类型；matcher 三层过滤
- **Hermes**【一手】：`pre_tool_call` / `post_tool_call` 插件 hook；**8 个 callback** 面向 UI 进度
- **opencode**【一手】：**35+ 插件事件**，含 `tool.execute.before/after`、`session.*`、`permission.*`、`file.*`
- **OpenHands**【一手】：文档有独立的 Hooks 页

**共同点**：
1. **`tool.execute.before/after`（PreToolUse/PostToolUse）是三个项目的公约数**——这是最小可用的 hook 面
2. **「压缩前」也是一个需要 hook 的点**（Claude Code `PreCompact`/`PostCompact`；opencode `experimental.session.compacting` 可**整体替换压缩 prompt**）
3. **hook 可以在工具执行前改写输入、执行后改写输出**（Claude Code `updatedInput` / `updatedToolOutput`；opencode 直接修改 `output.args`）
4. **hook 静默 ≠ 批准**（Claude Code 明确澄清：`exit 0` 无输出 = 无决策，继续走正常权限流程）

### 7.10 能力可插拔，但**默认不自动激活**

- **Hermes**【一手】：`ContextEngine` ABC + `context.engine` 选择，**「Plugin engines are never auto-activated」**；memory provider / context engine 两类插件**都是单选**
- **Claude Code**【一手】：MCP server 需显式添加；项目 `.mcp.json` 里的 server 默认 `⏸ Pending approval`
- **opencode**【一手】：`lsp` 工具需显式开环境变量；`websearch` 需特定 provider 或显式环境变量
- **Hermes**【一手】：项目 skills **需要 `hermes skills trust`**

**共同点**：**扩展能力存在 ≠ 扩展能力生效。** 默认关闭、显式开启、并对「可能危险的扩展」加信任步骤。

### 7.11 单一循环 + 多入口（平台无关内核）

- **Hermes**【一手】：设计原则「**Platform-agnostic core** — One AIAgent class serves CLI, gateway, ACP, batch, and API server. Platform differences live in the entry point, not the agent.」
- **opencode**【一手】：同一 agent 定义服务 TUI / CLI / Web / IDE / Desktop
- **OpenHands**【一手】：Canvas / Agent Server / automation 分层，「**The automation service decides when work runs and dispatches conversations to the Agent Server/SDK, which decides what runs.**」

**共同点**：**把「什么时候跑」和「跑什么」分开**，把平台差异推到入口层。**这一条对 QQ bot harness 是最直接可用的架构指导。**

### 7.12 工具调用的顺序不变式必须由框架维护

- **Hermes**【一手】：**并行工具结果按原始 tool call 顺序重新插入**；**严格角色交替校验**；**interactive 工具强制串行**；压缩时 **`_align_boundary_backward()` 保证不切断 tool_call/tool_result 组**；`_sanitize_tool_pairs()` 清理孤儿对
- **Claude Code**【一手】：`PostToolBatch` 在「一整批并行工具调用」全部解决后、下次模型调用前触发——说明框架在批级别有同步点
- **opencode**【一手】：`apply_patch` 需检查 `input.tool === "apply_patch"`（而非 `"patch"`）、参数是 `args.patchText` 而非 `args.filePath`——说明工具接口的一致性需要显式维护

**共同点**：**「工具 ID 与结果的对应关系、消息角色交替、tool 对不被切断」是框架必须保证的不变量**，模型不会替你维护。这些是最容易出现「看似能用但偶发诡异失败」的地方。

---

## 8. ★ 分歧点（同一问题不同做法，各自取舍与代价）

### 8.1 分歧一：规划是「一种 agent」还是「一种权限模式」？

| 项目 | 做法 | 代价 |
| --- | --- | --- |
| **opencode**【一手】 | plan 是一个 **primary agent**，靠权限（edit/bash = ask）实现只读 | 概念少、可组合；但**「计划」与「代理身份」被绑定**——不能在一个只读 agent 里临时开写权限，Tab 切换意味着换身份 |
| **Claude Code**【一手】 | plan 是一个 **权限模式**（Shift+Tab 循环） | 可在**同一会话、同一上下文**里进出计划态；但「模式」与「agent」两套概念并存，且模式是全局状态（容易忘记自己在哪个模式） |
| **Hermes**【一手】 | plan 是一个 **slash 命令**，且**产物落盘**到 `.hermes/plans/` | 计划成为**可版本化、可跨会话读取的文件**；但它不约束后续执行（不是权限门） |
| **Claude Code**【一手】 | 另有 `/goal` 与 `TaskCreate`/`TaskCompleted` 事件 | 目标/任务是一等对象，可被 hook 观测与阻断 |

**取舍本质**：**「计划」是状态（模式）、身份（agent）、还是产物（文件）？** 三者可以并存，但混用时必须明确谁是权威。**对 QQ bot：建议采用「产物（文件/消息）」形态**，因为多人群聊里「模式」这种隐含状态极易造成误解（用户不知道 bot 现在处于哪个模式）。

### 8.2 分歧二：上下文压缩是「一次性大动作」还是「持续小额分期」？

| 项目 | 做法 | 代价 |
| --- | --- | --- |
| **Hermes 批量（默认）**【一手】 | 跨阈值时一次性摘要大块中间 | 上下文占用**锯齿式**上升到阈值；有一次**可见的长停顿**；**cache 前缀在两次压缩之间完好** |
| **Hermes 微压缩（opt-in，默认关）**【一手】 | 每轮吸收一个最旧 exchange 进滚动摘要 | 占用**低位平坦**、会话可无限长、无长停顿；**每轮打断 prompt cache**；**知识变成二手更早**（细节保真度下降）；首次 pass 反而花 token |
| **Claude Code**【一手】 | auto-compact at threshold（批量） | 同 Hermes 批量；官方承认「compacting a large context **is itself a large request**」 |
| **SWE-agent**【一手】 | `HistoryProcessor` 在每次 prompt 前压缩 | 可插拔是优势；但同样有「改写历史 → 缓存失效」的一般性问题 |

**Hermes 的官方结论**【一手（原文）】：
> 「the honest framing is a **trade of one cost for another, not a saving**」；「**Which side wins depends on numbers specific to you**: how much your provider discounts cached input, how large your prefix is, how long your sessions run, and how much a mid-session stall actually costs you.」

**这是本次调研中最诚实的一段设计取舍表述。** 目标 harness 应当**提供两个模式 + 一个度量，而不是替用户选一个**。

**并且注意 Hermes 自己的立场**：即使它实现了微压缩，也**默认关闭**，并在文档里写了「Read this before enabling the feature. **It is the strongest argument against it.**」

### 8.3 分歧三：子代理的隔离上下文是「黑盒」还是「可导航的会话」？

| 项目 | 做法 | 代价 |
| --- | --- | --- |
| **opencode**【一手】 | 子代理创建**子会话**，并有键位在父子会话间导航（`session_child_first` / `cycle` / `parent`） | **可调试、可审查**子代理为什么给出某结论；代价是「子代理」变成用户可见的一等对象，UI 复杂度和会话管理成本上升 |
| **Hermes**【一手】 | subagent 隔离上下文；`/bg` 结果**只回传结论**到发起频道 | 干净、简单；但用户**看不到子代理的推理过程**，出问题时只能靠日志 |
| **Claude Code**【一手】 | 只回传 1,000–2,000 token 摘要 | 上下文经济性最好；有独立文档页 `/docs/en/agent-view` 与 `SubagentStart/Stop` 事件可观测；但内容仍不可回溯 |
| **Hermes 的折中**【一手】 | `session_search` 让 agent（和用户）**能事后检索** | 把「不可见」变成「可按需检索」——**只付检索成本，不付常驻可见性的成本** |

**取舍本质**：**子代理的中间过程是「默认可见」、「默认不可见但可检索」，还是「不可见」？** Hermes 的 `session_search` 折中方案在成本上最优，但对目标是「可解释」的场景不够。

### 8.4 分歧四：记忆是「固定预算的小文件」还是「无限制的检索」？

| 项目 | 做法 | 代价 |
| --- | --- | --- |
| **Hermes 双层**【一手】 | **MEMORY.md（2200 字符）+ USER.md（1375 字符）固定预算，常驻 system prompt** ＋ **`session_search` 无限制 FTS5 按需检索，零 LLM 成本** | 固定的 ~1300 token/prompt 成本；换来「关键事实永远在上下文里」。两个机制成本结构完全不同（固定 vs 按需） |
| **Hermes 的强制机制**【一手】 | **超限返回错误**，要求 agent 在同轮内 consolidate | 保证 system prompt 有界；但 agent（特别是弱模型）可能反复失败 |
| **Claude Code**【一手】 | **CLAUDE.md 层级**（User/Project/Local/Managed）+ `.claude/rules/*.md`，**无硬字符上限**，但官方建议 `<200 行`；支持 `paths:` 条件激活与惰性加载 | 无上限意味着容易膨胀（官方专门写了一节「Move instructions from CLAUDE.md to skills」来治这个问题）；换来的是结构灵活 |
| **opencode**【一手】 | 只有 `AGENTS.md`（项目级，`/init` 生成，建议提交 Git） | 最简单；但**无跨会话长期记忆**（未找到） |
| **Hermes 外部 provider**【一手】 | OpenViking / Mem0 / Holographic / RetainDB / ByteRover / Honcho / Hindsight / Supermemory，**与内置并行，绝不替代** | 知识图谱、语义搜索、自动事实抽取、跨会话用户建模；但引入外部依赖与数据边界问题 |

**取舍本质**：**记忆应该是「有界常驻」还是「无界检索」？** Hermes 用**两个机制并存**给出了最完整的答案，并且明确写出了成本结构差异表。**这是本节最值得直接抄的一张表。**

**并且 Hermes 官方承认了一个所有常驻 bot 都会遇到的结构性冲突**【一手】：
> 消息平台的「一个 chat = 一个连续会话」设计，会让会话**跑数周**，导致「**compaction runs repeatedly over an ever-longer history**」且「**the learning loop of forget → recall from memory → search past sessions almost never gets to fire**」；而**冻结快照**又让新记忆对当前会话不可见。**官方的解法是把边界交给用户（`/new`），而不是自动切分。**

### 8.5 分歧五：记忆/技能的写入是「自由」还是「需批准」？

| 项目 | 默认 | 可切换 |
| --- | --- | --- |
| **Hermes**【一手】 | **自由写**（`memory.write_approval: false`、`skills.write_approval: false`），包括**后台自省 review 的无人提示写入** | 可开启，开启后**消息平台/脚本/后台 review 的写入全部暂存**（`/memory pending`、`/skills diff`） |
| **Hermes 的精细之处**【一手】 | 暂存的 `replace`/`remove` **记录它瞄准的完整条目**；批准只对被瞄准的那一条生效，**条目变了则拒绝并保持 pending** | 这是防止 TOCTOU（检查与使用之间被改）的正确做法 |
| **Claude Code**【一手】 | skill 由用户/组织创建；`disable-model-invocation`、`skillOverrides` 控制可见性；**未找到「agent 自建 skill」机制** | — |
| **opencode**【一手】 | agent 无法自建 skill（未找到） | — |

**取舍本质**：**「自我改进的 agent」与「用户控制」天然冲突。** Hermes 的选择是**默认自由以便让学习回路真正跑起来，但提供一个不损失信息量的审批门**（暂存 + 按条批准 + diff 走带外，因为 SKILL.md 太大塞不进聊天气泡）。

**Hermes 官方对审批模式的诚实评价**【一手】：官方称之为「**『agent 存了一条关于我的错误假设』的答案**」——但**默认是关的**，说明团队认为默认自由的收益更大。

### 8.6 分歧六：工具的「自注册自动发现」还是「显式清单」？

| 项目 | 做法 | 代价 |
| --- | --- | --- |
| **Hermes**【一手】 | **AST 扫描 `tools/*.py`**，找含顶层 `registry.register()` 的模块并 import | 新增工具零配置；**代价是需要 AST 检查来避免误 import 辅助模块**，且「导入即产生副作用（注册）」是可读性/可测试性的负担 |
| **opencode**【一手】 | 内置清单 + **插件可注册工具，且插件工具与内置同名时插件优先** | 覆盖行为明确；但「同名覆盖」是隐式行为，且**本地插件与同名 npm 插件都会加载** |
| **Claude Code**【一手】 | 内置工具（有独立的 Tools reference 页）+ MCP/插件扩展 | 最可预测；扩展只能通过 MCP/插件 |
| **Hermes 的冲突策略**【一手】 | 跨 toolset 遮蔽**被拒绝**（记 error log），除非 `override=True`；**插件覆盖内置还需 `plugins.entries.<plugin_id>.allow_tool_override: true`** | 两级开关——这是防止插件静默劫持内置工具的正确设计 |

### 8.7 分歧七：沙箱边界的「一处」还是「多处」？

| 项目 | 立场 | 代价 |
| --- | --- | --- |
| **Hermes**【一手】 | **容器后端会跳过危险命令检查**——「因为容器本身就是安全边界」 | 概念清晰、无重复劳动；**但意味着安全强度完全取决于是否真的用了容器**（local 后端上只剩命令模式匹配） |
| **Hermes 的自陈**【一手】 | 写保护「**does not sandbox a hostile or compromised agent**」；deny 规则「**not an OS capability sandbox**」 | 诚实；把真正的隔离责任推给 OS 权限 |
| **Claude Code**【一手】 | managed settings 的组织级策略 + workspace trust + `--dangerously-skip-permissions` | 策略层次多；但**未取得 sandbox 机制细节** |
| **OpenHands**【一手】 | 三种运行时模式并**明示风险差异**：无沙箱（「full access to your filesystem!」）/ Docker / 每会话一容器 | 把选择权交给用户并明确标注风险；**每会话一容器**是隔离最彻底的方案，但官方承认「Conversations using the same host workspace still share those files」 |
| **mini-SWE-agent**【一手】 | 动作**用独立 `subprocess.run`** 而非有状态 shell 会话——「literally just switch out `subprocess.run` with `docker exec`」 | **把「可沙箱化」变成架构属性而非事后加固**——这是很聪明的一招 |

**取舍本质**：**安全边界只能有一处并且必须说清楚是哪一处。** 有容器就别在命令层做假的安全感；没容器就必须诚实地说明这只是「减少意外损害」。

### 8.8 分歧八：工具集的大小

| 项目 | 规模 | 官方立场 |
| --- | --- | --- |
| **Hermes**【一手】 | **70+ 工具 / 约 28 toolsets** | 靠 `check_fn` 动态过滤 + toolset 启停控制暴露面；官方还做**动态 schema 修补**防幻觉 |
| **Claude Code**【一手】 | 内置工具集 + **MCP 工具定义默认推迟**（只进名字与 server 说明） | 官方明确建议：**「Prefer CLI tools when available」**——`gh`/`aws`/`gcloud` 比 MCP「更省上下文，因为它们不增加 per-tool listing」 |
| **opencode**【一手】 | 13 个内置工具 | 中等规模，靠权限而非数量控制 |
| **mini-SWE-agent**【一手】 | **只有 bash** | 「it doesn't even need to use the tool-calling interface of the LMs」；「Instead of implementing custom tools for every specific thing the agent might want to do, **the focus is fully on the LM utilizing the shell to its full potential**」 |
| **SWE-agent**【一手】 | 精心设计的专用工具集（file viewer/editor/search）+ linter | 官方对「为什么需要」的解释在 mini 的 README 中**被自己的团队推翻**：「one year later, as LMs have become more capable, **a lot of this is not needed at all**」 |

**这是全篇最尖锐的分歧**：**Hermes 的 70+ 工具 vs mini 的 1 个工具，两者都在 2026 年存活且都有官方文档背书。**

**官方给的判据**【一手（mini README）】：用 mini 如果你「**don't want to overfit to a specific agent scaffold**」；用 SWE-agent 如果你「want to experiment with different sets of tools, each with their own interface」。

**对目标 harness 的直接启示**：**先用「bash + 少量通用工具」起步，只在证明有收益时增加专用工具。** 因为工具集的成本是持续的（schema 常驻上下文、决策歧义、维护负担），而收益随模型能力提升而递减。

### 8.9 分歧九：模型分级按什么维度切？

| 项目 | 切法 | 优点 | 代价 |
| --- | --- | --- | --- |
| **opencode**【一手】 | **按 agent**（plan 用 haiku，build 用 sonnet，subagent 各自可配） | 直观，与「谁在干活」对应 | 同一 agent 内的不同任务（如压缩）无法单独降级 |
| **Claude Code**【一手】 | **按 agent / subagent**（`model: haiku` 给简单子代理） | 同上；且「Opus 切换会传染给继承会话模型的 subagent」 | 同上 |
| **Hermes**【一手】 | **按任务类型**（`auxiliary.compression` / `auxiliary.background_review` / vision / web extraction 各有 provider+model+fallback 链） | **可以只把「机械工作」降级而保留主模型**；且每类任务可配独立的 fallback 链 | 配置面更大；官方也承认同模型 review 的推理努力**无法独立调**（会破坏 prompt-cache 前缀一致性） |
| **Hermes 的反直觉发现**【一手】 | 压缩任务上：**「a small, fast, non-reasoning instruct model is usually the right shape, and that a bigger or 'smarter' model is often worse here rather than better」** | — | 意味着「按能力分级」的直觉在机械任务上是错的 |

**取舍本质**：**「分级」的正确粒度是任务类型，不是模型能力档位。** 一个「更聪明」的模型在摘要任务上可能更慢且更差。

### 8.10 分歧十：API 协议的归一化点在哪里？

| 项目 | 做法 | 代价 |
| --- | --- | --- |
| **Hermes**【一手】 | 支持 3 种 api_mode（`chat_completions` / `codex_responses` / `anthropic_messages`），**在任何 API 调用前后都归一到同一套 OpenAI 风格内部格式** | 一处统一处理逻辑；但官方承认「The mode determines how messages are formatted, how tool calls are structured, how responses are parsed, and **how caching/streaming works**」——归一化掩盖了真实差异，导致需要大量 provider 特定特判（如 Codex 的 reasoning-only 停滞检测、`encrypted_content` 计价为 0、原生 compaction） |
| **Hermes 的 provider 特判清单**【一手】 | `codex_gpt55_autoraise`、`codex_app_server_auto`、`codex_responses_native`、`-900k` 别名、`DANGEROUS…` | **这些特判本身就是「归一化失败」的证据**——官方文档中大量篇幅用于说明某个 provider 的某个模型在某条路由上的例外 |
| **mini-SWE-agent**【一手】 | 通过 litellm/openrouter/portkey 统一；**且不使用 tool-calling 接口**（只有 bash） | **回避了工具调用格式差异**；但也就无法利用原生 tool calling |

**取舍本质**：**归一化能减少重复代码，但 provider 差异总会以特判的形式回来。** 若目标 harness 只支持 1–2 个 provider，**明确不支持多协议可能是更省事的选择**。

---

## 9. ★ 对「QQ 聊天机器人 harness」的启示

### 9.0 先明确 QQ bot harness 与上述四个项目的根本差异

| 维度 | 上述四个项目 | QQ 聊天机器人 harness |
| --- | --- | --- |
| **驱动方式** | 单用户任务驱动（用户下指令 → agent 干活 → 交付） | **事件驱动**（消息到达即触发，无「任务完成」的终点） |
| **会话主体** | 1 个用户 ↔ 1 个 agent | **N 个用户 ↔ 1 个 bot**，且用户之间互相可见 |
| **在线性** | 用户关闭终端即结束 | **永久在线**，且**多群并发** |
| **上下文归属** | 会话 = 任务 | **会话 = 一个群/一个私聊**，可跨越数周甚至数月 |
| **交互节奏** | 用户等结果 | **多条消息可能同时到达**；用户可能在 agent 工作时插话 |
| **权限模型** | 操作者 = 使用者 | **操作者 ≠ 使用者**（部署者 vs 群成员） |
| **危险操作的后果** | 毁掉自己的仓库 | **毁掉部署者的服务器**，且可能被**任意群成员的提示注入**触发 |
| **成本归属** | 自己付费，自己感知 | **部署者付费**，群成员无感知（滥用风险） |
| **失败可见性** | 自己看到报错 | **N 个群成员看到 bot 发疯** |

**结论：Hermes 是唯一一个原生面向这个问题的研究对象**，因此它应当是主要参照；其余三个项目提供的是**循环/工具/上下文层面的通用工程经验**，其「单用户任务驱动」的假设**必须被替换**。

### 9.1 适用：应当直接借鉴的设计

#### ✅ 1. 平台无关内核 + 入口层承载平台差异（Hermes 设计原则）

**依据**【一手】：Hermes 的「Platform-agnostic core — One AIAgent class serves CLI, gateway, ACP, batch, and API server. **Platform differences live in the entry point, not the agent.**」

**对 QQ bot 的落地**：
- 内核只认识「一个会话 + 一条用户消息 + 一个回复投递接口」
- QQ 适配器负责：消息去重、CQ 码/富文本解析、@ 检测、频率限制、消息分片、图片/文件上传
- **不要把「群」「频道」「@」这些概念泄漏进 agent 内核**——否则将来接入第二个平台就要改内核

**并且**：Hermes 把「何时跑」与「跑什么」分开（OpenHands 的多仓库分层也印证了这一点）。QQ bot 天然需要这个分离：**消息事件的接收/排队/去抖**（何时跑）与**agent 执行**（跑什么）应当是两个组件。

#### ✅ 2. 多人授权：默认拒绝 + 显式 allowlist + DM pairing

**依据**【一手】：Hermes 的六步授权检查顺序，**默认拒绝**，且「若未配置任何 allowlist 且未设 `GATEWAY_ALLOW_ALL_USERS`，**所有用户都被拒绝**」；DM pairing 的完整参数（8 字符码、32 字符无歧义字母表不含 0/O/1/I、密码学随机、1h TTL、10min 限流、3 个待批上限、5 次失败锁 1h、文件 0600、码不写 stdout）。

**对 QQ bot 的落地**：这套参数**可以直接照抄**。QQ 群场景下尤其需要：
- **群级别白名单**（哪些群可以用）+ **用户级别白名单/管理员**（谁能触发危险操作）
- 未授权 @ 的处理策略应当**可配**（`pair` / `ignore` / `decline`），因为不同群的文化不同
- **`decline` 的 24 小时静默**是防止骚扰的好设计

#### ✅ 3. Admin / 普通用户分档，且按作用域独立

**依据**【一手】：Hermes 的 admin/user 分档「按平台、按作用域（DM vs 群/频道）独立配置」，**DM 的 admin 不等于群的 admin**；永远允许的地板是 `/help` 和 `/whoami`；**未来可把工具访问、模型切换、昂贵操作挂到同一分档**。

**对 QQ bot 的落地**：这是**群聊场景的核心权限抽象**。QQ 群里的「群主/管理员」与 bot 部署者**往往不是同一批人**，必须能独立配置：
- 部署者（超级管理员）：可改配置、可执行危险操作
- 群管理员：可在本群内使用某些命令
- 普通成员：只能聊天，可能连命令都不能用
- **并预留「把更多能力面挂到分档上」的接口**（Hermes 明确说这是它的演进方向）

#### ✅ 4. 无人值守/半无人值守场景的独立策略档

**依据**【一手】：Hermes 的 `cron_mode` / `single_query_mode` / `unattended_mode`，**默认全部 `deny`**，且 `unattended_mode` 的 `deny` 是「**立即阻断而不是阻塞整个审批超时**」——理由是「these surfaces have no human who can answer `/approve`」。

**对 QQ bot 的落地**：**这是最重要的一条**。QQ 群里「危险命令需要人审批」这个模型**根本不成立**：
- 群成员可能没权限批
- 部署者可能不在线
- 等 300 秒没有任何意义

所以必须定义**分场景策略**：
- **私聊（有明确所有者）**：可以走审批（甚至 `smart` 模式）
- **群聊（多人在场，权限不清）**：**默认直接拒绝危险操作**，或者退化为「只在安全子集内工作」
- **定时任务（无人）**：直接拒绝或只允许预授权命令集

**并且要定义「谁有资格批准」**——这比「是否批准」更难。

#### ✅ 5. 硬底线黑名单：连「关闭安全」都不能覆盖

**依据**【一手】：Hermes 的 `UNRECOVERABLE_BLOCKLIST`（`rm -rf /`、fork bomb、`mkfs.*`、`dd of=/dev/sd*`、rootfs 顶层 pipe-to-shell），**在审批层之前触发，`--yolo` / `mode: off` / cron `approve` / 「always allow」都不能覆盖**。

**对 QQ bot 的落地**：**必须要有**。理由：QQ 群是**不可信输入源**，提示注入是现实威胁。一个被注入的 agent 在部署者的服务器上执行 `rm -rf /` 的后果是不可逆的。设计要点：
- **代码级硬编码**，不通过配置文件（避免被配置文件注入绕过）
- **在权限检查之前触发**
- **shell 引号无法解析时 fail-closed**（Hermes 的 `malformed executable payload`）
- 明确记录：**这不是沙箱，只是减少灾难**

#### ✅ 6. 记忆：有界常驻 + 无界检索的双层结构

**依据**【一手】：Hermes 的成本结构对照表——持久记忆 ~1300 token 固定/prompt、即时、**每个 prompt 都付费**；`session_search` 无限、~20ms、**零 LLM 成本**、按需。

**对 QQ bot 的落地**：这个二象限划分对 QQ bot 尤其合适：
- **常驻层（固定预算）**：这个群的规矩、常用机器人命令、群成员称呼偏好、部署者的硬性要求——**小而稳定**
- **检索层（按需）**：历史对话、上次谁问过什么、之前的结论——**大而稀疏**，用 FTS5（或 SQLite + 全文检索）零成本存
- **必须显式设置字符上限并让 agent 知道剩余额度**（在 system prompt 头部显示用量百分比，如 Hermes 的 `[67% — 1,474/2,200 chars]`）
- **超限时返回错误要求 agent 当场合并**，而不是静默丢弃

**关键**：QQ 群的历史非常长（一个活跃群一天可能几百条），**必须有零 LLM 成本的检索**，否则每次「上次说的那个」都要触发一次模型调用。

#### ✅ 7. 「用户消息永不被压缩」

**依据**【一手】：Hermes 微压缩的核心设计——**exchange 刻意从 assistant 消息开始，直接跨过用户消息**，理由：

> 「**Your instructions are a different kind of thing. They're the intent everything else is derived from, and they cannot be reconstructed from the work that followed.** Paraphrasing "use the existing retry helper, don't add a new one" into a summary is exactly how an agent ends up confidently doing the thing you told it not to, six turns later.」

**对 QQ bot 的落地**：**这条比在编码 agent 里还重要**。QQ 群里用户的指令往往是**短、口语、隐含上下文**（「那个弄好了吗」「就按刚才说的」），一旦被摘要就**完全不可恢复**。而 bot 自己干的活（查了什么、调了什么 API）摘要损失很小。**这个不对称性在群聊场景下更极端。**

#### ✅ 8. 「改写已发送历史」是昂贵操作，必须显式开关 + 度量

**依据**【一手】：Hermes 微压缩**默认关闭**并主动劝退；设计原则「**Prompt stability**」；Claude Code 的 `/reload-mcp` 会因**工具 schema 在 system prompt 里**而作废整个 prompt cache。

**对 QQ bot 的落地**：
- 如果用了支持 prompt caching 的 provider（如 Anthropic），**缓存新鲜度直接等于钱**
- **system prompt 绝不在会话中途变化**——这意味着「群设置变了」「换了工具集」应当**创建一个新会话**，而不是热改 system prompt
- 提供**两个压缩模式**（批量 vs 分期）并**默认选便宜的批量**，把选择权与度量交给部署者

#### ✅ 9. 子代理只回传摘要（把「烧 token」隔离在别处）

**依据**【一手】：Claude Code 明确数字——子代理烧几万 token，**只回传 1,000–2,000 token 摘要**；Hermes 的 `/bg` 「has no knowledge of your current chat context and receives only the prompt you provide」。

**对 QQ bot 的落地**：**群聊场景下这一条尤其有价值**：
- 「帮我调研一下 X」这类请求会烧大量 token 且产出的是**结论**而非过程
- 子代理在后台跑，只把结论发回群里
- **但注意**：Hermes 的 `/bg` 是 **fire-and-forget**，结果异步回到同一 chat——对 QQ 群这意味着**结果可能在几十分钟后突然出现**，需要明确提示「任务已启动」和「任务完成」

**并且要考虑**:QQ 群里子代理的输出会**被所有人看到**，所以「回传什么」不只是成本问题，也是**隐私/噪音问题**。

#### ✅ 10. 「大输出落盘 + 返回句柄」

**依据**【一手】：Claude Code 的 MCP 输出超 25,000 token 存文件给路径；`additionalContext` 超 10,000 字符落盘；SWE-agent 的文件查看器每轮只显示 100 行、搜索只列文件名（因为「Showing the model more context about each match proved to be too confusing」）。

**对 QQ bot 的落地**：QQ 消息本身有长度限制，且**长消息在群聊里是灾难**。所以：
- 长输出**必须**落盘/转文件，聊天里只给摘要 + 下载链接或「回复某条消息获取详情」
- 工具结果**不要原样进上下文**——先裁剪、摘要或落盘
- **`[[as_document]]` 这类「交付形态指令」是有用的**（Hermes 用它强制把图片作为文件而非压缩预览投递）

**Hermes 的一个可直接抄的机制**【一手】：**响应里出现媒体文件绝对路径时，gateway 自动检测、从可见文本中剥离、并以平台原生方式投递文件**；`[[audio_as_voice]]` 提升为语音气泡；`[[as_document]]` 强制作为文档附件投递（避免 Telegram 把 2MB PNG 压成 200KB 导致图表不可读）。**「模型输出里嵌入投递指令，gateway 解释并剥离」是一个干净的模式。**

#### ✅ 11. Skills 用 `SKILL.md` + 三级渐进披露，且兼容开放标准

**依据**【一手】：三个项目独立收敛到同一格式；Anthropic 2025-12-18 将其发布为**开放标准**（agentskills.io）；Hermes 官方称兼容；Claude Code 官方称遵循。

**对 QQ bot 的落地**：**直接采用 agentskills.io 标准格式**，因为：
- 生态可复用（Hermes/Claude/opencode 的 skills 可直接用）
- 三级披露（元数据 → 正文 → 打包文件）是解决「能力多 vs 上下文小」的唯一验证过方案
- **且要抄 Hermes 的两个增强**：
  - **条件激活**（`requires_tools` / `fallback_for_tools`）——让「免费/本地兜底能力」自动出现，无需用户切换
  - **`/skill` 可叠加上限**（Hermes 是最多 5 个），**且解析在第一个非 skill token 处停止**（避免吞掉以 `/` 开头的参数）

#### ✅ 12. 项目级配置必须显式信任，并在内容变化时重新扫描

**依据**【一手】：Hermes 的 `hermes skills trust` + **扫描期隔离**（因为「a repo's skill content changes with every `git pull`」，所以每个项目 skill 在进入索引前跑与 Hub 安装相同的扫描器，dangerous 的直接不进索引且按名加载会被拒，扫描结果按内容 hash 缓存**在 `~/.hermes/cache/` 而非仓库里**）；Claude Code 的「**被克隆的仓库不能批准自己的 MCP server**」。

**对 QQ bot 的落地**：**这一条在多人场景下是安全刚需**。如果 bot 支持「群级别配置」或「从某处加载提示词/技能」：
- **绝不能自动信任**任何用户可写的配置源
- 配置内容变化后**必须重新校验**（因为内容是变化的，信任是静态的）
- 扫描缓存**不能放在用户可写的位置**

#### ✅ 13. hook 面：「工具执行前后 + 压缩前后」是最小可用集

**依据**【一手】：三个项目的公约数是 `tool.execute.before` / `after`（PreToolUse/PostToolUse）；压缩前的 hook 是第二个必选项（Claude Code `PreCompact`/`PostCompact`；opencode `experimental.session.compacting` 可注入或**整体替换压缩 prompt**）。

**对 QQ bot 的落地**：hook 在 QQ bot 场景下的**主要用途不是扩展，而是安全与合规**：
- **工具执行前**：拦截/改写（防注入、脱敏、审批）
- **消息发出前**（QQ bot 特有！）：审核、脱敏、@ 处理、字数检查、频率限制
- **压缩前**：注入群级别的「必须保留的信息」（如当前正在进行的话题、群规）

**并注意**：Hermes 的消息平台**永不在聊天里索取密钥**——必须强制走本地配置。**这条对 QQ 群尤其重要**（聊天记录可能被其他人看到）。

#### ✅ 14. 静默令牌（Silence Tokens）——群聊场景的刚需

**依据**【一手】：Hermes 的 `[SILENT]` / `SILENT` / `NO_REPLY` / `NO REPLY` / `[静默]` / `静默` / `[沉默]` / `沉默`——**若最终响应恰好是这个令牌，gateway 抑制出站投递，什么都不发**。规则很细：
- 空白与大小写被归一化，但**整个最终响应必须就是这个令牌**（「Use `[SILENT]` when nothing changed」这样的句子会正常投递）
- **静默只是一个投递决策**——Hermes **仍把这条 assistant 静默轮次保存在会话 transcript 里**，所以对话仍然正常交替
- **失败轮次仍会以错误形式浮出**——不会仅因为文本像静默令牌就掩盖失败
- **来自真人的消息，裸静默令牌会被替换成一条简短通知**，因为「a message that needed a reply must not vanish」
- **内部唤醒**（如后台进程通知）可以保持静默
- 平台适配器报告「这条消息不是对 bot 说的」时也可以静默（Slack 在消息以 @ 别人开头、或在自由响应频道里未提及就开新帖时报告）

**对 QQ bot 的落地**：**这是「永久在线、每群都监听」场景的必需机制**。没有它，bot 会对群里的每句话都回一句废话。Hermes 的实现细节（尤其是「整个响应必须恰好是令牌」「真人的消息会被替换成通知而非消失」「失败不会被静默掩盖」）都值得逐条照抄。

#### ✅ 15. 忙时输入策略：排队 / 打断 / 引导（busy-input mode）

**依据**【一手】：Hermes 的三种模式：
- **`interrupt`（默认）**：在 agent 工作时发消息会**重定向当前轮次**——模型生成**带上下文重启**，已显示的理由与部分文本作为普通 assistant checkpoint 保留；**已完成的工作保持可用**（先前的工具调用与结果留在轮次里）；**正在运行的工具安全跑完**（修正在下一个工具结果边界应用，而不是杀掉工具）；`/stop` 仍是硬停
- **`queue`**：后续消息等待，作为当前任务完成后的下一轮运行；每个后续（文本/语音/视频/文档）按到达顺序各得一轮；**只有快速连拍的图片被合并成一个相册轮次**
- **`steer`**：后续消息经 `/steer` 注入当前运行，**在下一次工具调用之后到达 agent**；不打断、不开新轮；agent 尚未启动时回退为 `queue`

**并且有合并与去抖配置**：`busy_text_debounce_seconds`（默认 0.35）、`busy_text_hard_cap_seconds`（默认 1.0，「never hold merged busy text longer than this」）、`busy_ack_enabled`。

**对 QQ bot 的落地**：**这是 QQ bot 最需要「翻译」的一条**。终端里的「用户独占一个 agent」在群里不成立——**多个用户可能同时对同一个群会话说话**。必须定义：
- 同一群内多人同时说话：**排队**（默认）还是**合并成一个轮次**？
- 一个人连发多条：**必须去抖合并**（QQ 用户习惯分多条发），Hermes 的 0.35s / 1.0s 是很好的起点
- 是否要有 busy 确认（「已收到，正在处理」）——**群里这个提示可能是噪音**，应当可关（`busy_ack_enabled: false`）
- **不同群之间必须并行**（一个群的任务不应阻塞另一个群）

#### ✅ 16. 按会话/频道的模型与 system prompt 覆盖

**依据**【一手】：Hermes 的 `channel_overrides`——单个 gateway 进程可在不同频道跑不同模型与人格（如 `#daily` 用便宜快模型、`#dev` 用前沿模型 + 专家 prompt）；**查找顺序是先精确 channel/thread id，再是父 channel/forum id**（所以 thread 自动继承父频道）；模型解析优先级是 **session `/model` 覆盖 → `channel_overrides` → 全局配置**；`system_prompt` 覆盖是**临时的（按轮注入，不入历史）**。

**对 QQ bot 的落地**：**极其实用**。QQ bot 常在多个群里：
- 不同群可以用不同模型（闲聊群用便宜的，工作群用强的）
- 不同群可以有不同人格/规矩
- **「先精确再父级」的查找顺序**在 QQ 场景对应「先看群号，再看群分类/标签」
- **`system_prompt` 覆盖按轮注入而不入历史**——这对保持 prompt cache 前缀稳定很关键

**⚠️ 但要注意**：Hermes 的 `channel_overrides` 是**配置级**覆盖。如果允许群成员自己设置，会变成**权限与成本漏洞**（群里有人把模型切到最贵的）。必须与 9.1 的第 3 条（Admin/User 分档）结合。

#### ✅ 17. 投递可靠性：at-least-once + 明确标注模糊性

**依据**【一手】：Hermes 的 **delivery ledger**（`state.db`）——最终响应在每次平台发送前后被记录；若 gateway 在「产出响应」与「平台确认收到」之间崩溃或重启，**下次启动会重投递存储的响应，而不是丢失它、也不是重跑整个轮次**。语义被诚实地定义为 **at-least-once**：

- **从未开始发送**的响应按原样重投
- **发送中途**gateway 死掉（平台可能收到也可能没收到）的响应，重投时**带可见前缀「♻️ Recovered reply — … may be a duplicate」**——**「Ambiguity is labeled, never silently resent」**
- 被**流控**（如 Telegram 限流）拒绝的最终发送，在记录的惩罚期过后**自动重试**，无需重连或重启；重启期间处于惩罚期的会**采用存储的回复而不消耗重试次数、也不重跑 agent**
- 其它被拒的最终发送按**递增退避**（30s，然后 2min）重试；最后一次预算内的尝试留给下次 gateway 启动，所以**持续超过计时器的故障也不会让回复永久搁浅**
- **永久不可达的 chat（bot 被拉黑、群被删）不重试**
- **重投有界**：3 次尝试、24 小时新鲜度，然后该行被放弃；已投递行 7 天后清理
- 可用 `gateway.delivery_ledger: false` 关闭（回到旧行为：崩溃时在途响应丢失）

**对 QQ bot 的落地**：**QQ bot 的崩溃/重启/网络抖动是常态**（尤其在国内网络环境）。这套设计几乎可以直接照抄：
- **重投而非重跑**（重跑会重复执行工具副作用，可能更糟）
- **模糊性必须被标注而非静默重发**（群里出现重复消息比不出现更容易被接受，但不能让 bot 假装没发生过）
- **流控惩罚要持久化**，否则重启会立刻再撞墙
- **有界重试**，避免永久重试风暴

#### ✅ 18. 群聊的「被 @ 才响应」与消息时间戳

**依据**【一手】：
- Hermes 的平台适配器会报告「消息没有对 bot 说话」（Slack 在消息以 @ 别人开头、或在自由响应频道里未提及就开新帖时报告），此类消息**可以保持静默**
- **消息时间戳入模型上下文**（默认关）：`gateway.message_timestamps.enabled` 开启时，Hermes 在**每条用户消息前**加一个人类可读时间戳（如 `[Tue 2026-04-28 13:40:53 CEST]`）**在模型的上下文里**，让 agent 知道消息何时发的——**对时序推理有用**（「你今早问过」「注意到一个长间隔」）。**不加到 assistant 消息或 system prompt**；**持久化的 transcript 始终保持干净**（时间戳作为消息元数据存储，所以后开也能显示历史消息的发送时间，且重放不会累积重复前缀）

**对 QQ bot 的落地**：**「是否被 @」是 QQ bot 的第一道过滤器**，必须在**进 agent 之前**判断，而不是让 agent 每次都读一遍消息再决定是否回。**时间戳入上下文**对群聊特别有用（用户会说「刚才」「昨天」），而「持久化 transcript 保持干净、时间戳存为元数据」的做法避免了重放污染。

### 9.2 不适用：不要照搬的设计

#### ❌ 1. 不要把「危险命令的人机审批」当作主要安全机制

**依据**【一手】：Hermes 的审批流程完全建立在「有一个人在看」的假设上（CLI 内联提示 / messaging 平台回复 yes/no / 300s 超时）。而 QQ 群的现实是：**没人看、没人有权、没人负责**。

**替代方案**：以「**能力白名单**」为主、「审批」为辅：
- 群聊会话默认**只暴露安全工具子集**（只读 + 受限于工作目录的写）
- 危险能力（shell、任意路径写）**默认不暴露给群聊会话**
- 若必须暴露，走**预授权的固定命令集**（对应 Hermes 的 `command_allowlist` 概念，但由部署者预先配置而非运行时批准）
- 审批只保留给**私聊**场景

#### ❌ 2. 不要用「模型名的上下文窗口」作为稳定属性

**依据**【一手】：Hermes 的 `codex_gpt55_autoraise` 存在的唯一原因就是**同一个模型 slug 在不同路由上有不同窗口**（ChatGPT Codex OAuth 硬顶 272K，OpenAI 直连 1.05M，GitHub Copilot 400K）。官方还需要为 `-900k` 变体做别名、并在模型目录发布的 `max_context_window` 低于 900K 时改用发布值。

**对 QQ bot 的落地**：如果支持多 provider（很可能，因为国内用到各种中转/国产模型），**必须**：
- 窗口从**运行时探测**或 **provider 错误**学习，并**持久化**
- 窗口未知时用**保守回退**（Hermes 用 120,000）
- **阈值按「路由」而非「模型名」解析**
- 并且**承认估算会失败**：Hermes 明确说粗估超阈值时会「等一个请求」以取得 provider 证据，代价是**真正超大的请求可能先挨一次被拒**——**这是可接受的，必须为它设计恢复路径**

#### ❌ 3. 不要照搬 70+ 工具的工具集

**依据**【一手】：mini-SWE-agent 的整个论点是「**100x simpler, and still worked nearly as well**」，且 SWE-agent 团队自陈「as LMs have become more capable, **a lot of this is not needed at all**」；Claude Code 官方也说 **「Prefer CLI tools when available」**，因为「they don't add any per-tool listing」。

**对 QQ bot 的落地**：QQ bot 的工具需求与编码 agent 差异很大，**照抄工具集既无必要也有害**：
- 编码 agent 的 `read`/`edit`/`grep`/`glob`/`apply_patch` 在群聊里多半用不上
- QQ bot 真正需要的工具集可能只有十来个（发消息/发文件、搜索、HTTP 请求、简单的键值存储、定时任务管理、群成员信息）
- **每一个工具都在每轮消耗上下文**，且**增加模型的决策歧义**
- **建议：从「少量通用工具 + 可能一个受控的代码执行/shell」起步，只在有明确证据时增加**

#### ❌ 4. 不要用「单会话长跑数周」的模型

**依据**【一手】：Hermes 官方明确承认，消息平台的「一个 chat = 一个连续会话」会导致会话跑数周，结果是「**compaction runs repeatedly over an ever-longer history**」且「**the learning loop of forget → recall from memory → search past sessions almost never gets to fire**」；而**冻结快照**让新记忆对当前会话不可见。**官方的解法是把边界交给用户（`/new`），并承认这是用户的负担。**

**对 QQ bot 的落地**：**QQ 群成员不会主动 `/new`**。所以必须**自动定义会话边界**，而 Hermes 没有给出自动方案（它把这个决定留给用户）。QQ bot 需要的替代方案：
- **按时间切**（如每天/每半天新会话）——简单，但会切断正在进行的话题
- **按话题切**（用辅助模型检测话题转换）——更好，但引入额外成本与误判
- **滑动窗口 + 强检索**（不切会话，但把「活跃窗口」与「可检索历史」分开，用检索而非上下文来维持长期记忆）——**这条最贴合 QQ 群**
- **明确的 `/new` 命令**（给懂的用户用），**但默认必须自动**

**关键洞察**：Hermes 的记忆系统（MEMORY.md / USER.md + session_search）**在「会话会结束」的前提下才有效**。QQ 群若不切会话，就要**用检索替代记忆的跨会话角色**——即让 `session_search` 类能力在**会话内部**也承担「找到很久以前说过的话」的职责。

#### ❌ 5. 不要假设「一个消息 = 一个轮次」

**依据**【一手】：Hermes 的 busy-input 处理（interrupt/queue/steer）、去抖窗口（0.35s / 1.0s 硬上限）、以及「只有快速连拍的图片被合并成一个相册轮次”。

**对 QQ bot 的落地**：QQ 的用户行为是**高度碎片化的**（「在吗」「帮我查个东西」「就是那个」「昨天说的那个」可能是五条独立消息）。若每条消息触发一个轮次：
- 成本爆炸
- bot 会回五条不完整的话
- 上下文里塞满半截消息

**必须做的事**：
- **去抖 + 合并**（在进 agent 之前），窗口长度可配
- **区分「消息」与「轮次」**——这是两个概念，QQ bot 的 harness 里应当是显式分离的两层
- **多人同时说话时的归并策略必须明确**（默认排队？合并？还是以最后一个说话者为准？）——Hermes 只解决了「单用户连发」，**多人并发是 QQ bot 独有的、Hermes 也未完全覆盖的问题**

#### ❌ 6. 不要照搬「每个 skill 自动成 slash 命令」

**依据**【一手】：Hermes 与 Claude Code 都把 skill 暴露为 `/skill-name`。

**对 QQ bot 的落地**：QQ 的斜杠命令支持有限（QQ 频道机器人有 `/` 命令，**普通群聊没有**）。所以：
- 不能用 `/` 作为主要触发方式
- 需要**自然语言触发**（skills 的 description 匹配）作为主路径
- 或者用**消息前缀约定**（如 `.` 或 `#`）
- **skill 的 description 质量因此变得至关重要**——Claude Code 官方明确说「Claude will use these when deciding whether to trigger the skill」，而 Hermes 与 Claude Code 都承认 **description 会在列表预算溢出时被丢弃**（Claude Code 的预算 = **模型上下文窗口的 1%**）

**这意味着 QQ bot 的 skill 数量必须有硬上限**，且 description 必须极简且区分度高。

#### ❌ 7. 不要让「后台任务」的结果无提示地突然出现

**依据**【一手】：Hermes 的 `/bg` 是 fire-and-forget，结果前缀 `✅ Background task complete` 发回同一 chat。**但 Hermes 的 `/bg` 假设用户在一个有上下文的持续会话里**。

**对 QQ bot 的落地**：群聊里，几十分钟后突然出现一条「✅ Background task complete: ...」会**让所有群成员困惑**（他们不知道这是什么、谁触发的）。必须：
- **明确标注发起者**（「@某人 你让我查的 X 有结果了」）
- **或者只在私聊里回结果**，群里只说「结果已私聊给你」
- **或者让 agent 在群里说一句简短完成语**而不是倾倒全部结果
- **且要考虑**：如果发起者退群/被禁言怎么办？

#### ❌ 8. 不要忽略「成本归属与滥用」

**依据**【一手】：Claude Code 官方的成本治理完全建立在「操作者 = 使用者 = 付费者」的假设上（seat allowance、spend limits per member、per-user analytics）。这个假设在 QQ 群里**完全不成立**。

**对 QQ bot 的落地**：必须自建：
- **按群/按用户的用量统计**（对应 Claude Code 的归因，但归因到 QQ 用户 ID）
- **按群/按用户的配额或限流**（避免一个群把预算烧光）
- **昂贵操作的额外门控**（模型切换、长任务、网络搜索）
- **对「无意义高频请求」的抑制**（QQ 群里刷屏是常态）

**这是四个研究对象都没有直接解决、但 QQ bot 必须解决的问题。**

#### ❌ 9. 不要假设「工具调用格式可靠」

**依据**【一手】：SWE-agent 需要**可插拔的 action parser**（`reference/parsers/`）正是因为不同模型输出格式不同，格式错误是主要失败来源；mini-SWE-agent 干脆**不使用 tool-calling 接口**（只有 bash），从而「you can run it with literally any model」；Hermes 需要处理 Codex 的「reasoning-only 停滞」（连续三次续接无可见文本或工具调用）。

**对 QQ bot 的落地**：如果 QQ bot 要支持国产/本地模型（很可能，出于成本与合规），**工具调用可靠性会成为主要工程负担**：
- **准备一个真实的失败处理路径**（重试、修复、降级为纯文本）
- **考虑「不用 tool calling」的降级模式**（模型输出结构化文本，harness 解析）——mini-SWE-agent 证明了这在 SWE-bench 上可行（>74%）
- **记录「模型声称做了但没真的调用工具」的情况**——Hermes 官方警告：小模型（约 30B 以下）和工具调用弱的模型**会声称保存了记忆但没真的调用工具**，而官方给的修复是「**换更强的模型做 setup，而不是加更多指令**」

#### ❌ 10. 不要把「单用户 CLI 的交互假设」带进来

**依据**【一手】：opencode 的 Tab 键切换 agent、`session_child_cycle` 键位导航、`/undo` 双击 Esc 之类；Claude Code 的 Shift+Tab 循环权限模式、Esc 中断、`/rewind`。

**对 QQ bot 的落地**：**QQ 群成员没有键盘快捷键**。所有依赖「隐含状态 + 快捷键」的设计都不成立：
- **没有 Tab 切换 agent** → agent 选择必须由消息内容自动决定，或由配置固定
- **没有 Shift+Tab 切模式** → 模式必须由「谁在什么场景说话」决定，而非用户手动切
- **不能靠 Esc 中断** → 中断必须通过消息指令（`/stop`）或超时实现
- **没有「当前会话」的概念** → 「当前会话」在群里是「当前群」，但**用户可能以为是「当前对话」**

**这条的深层含义**：**QQ bot 的 harness 不能有任何「隐含的当前状态」**。所有状态必须要么从消息显式推导，要么通过显式命令设置，要么**固定不变**。Hermes 的做法（`/new`、`/model`、`/plan` 都是显式命令）比 opencode/Claude Code 的模式+快捷键更适配，但仍不够——因为群成员不会用命令。

### 9.3 需要在 Hermes 基础上「再加一层」的问题

以下是 Hermes 未解决或解决得不充分、但 QQ bot 必须处理的问题：

| 问题 | Hermes 的立场 | QQ bot 需要什么 |
| --- | --- | --- |
| **会话边界** | 交给用户（`/new`），并承认「若你从不重置，一个 chat 可以跑数周」 | **自动**切分（时间/话题/滑动窗口），因为群成员不会用 `/new` |
| **多人并发输入** | 解决了单用户连发（去抖、queue/steer/interrupt），但**未明确多用户同时说话** | **明确的多用户归并策略**（按人排队？合并？优先 @bot 的？） |
| **成本归属** | 有 per-profile 隔离、`session_model_usage` 表按 task 分类 | **按 QQ 用户/群归因 + 配额 + 限流** |
| **能力面授权粒度** | Admin/User 分档**当前只门控 slash 命令**；官方说「未来可能把工具访问、模型切换、昂贵操作挂到同一分档」 | **现在就需要**按分档门控工具、模型、成本 |
| **无斜杠命令的 skill 触发** | skill 主路径是 `/skill-name` | **自然语言 / 前缀约定触发**，且 description 预算要硬控 |
| **群聊可见性** | 有 `[[as_document]]`、媒体自动投递、静默令牌 | **「什么该在群里说、什么该私聊」的策略** |
| **提示注入的防御** | 有上下文文件扫描、记忆条目注入扫描、skill 安装扫描 | **QQ 消息本身的注入扫描**（消息是不可信输入，且比文件更直接） |
| **滥用与骚扰** | 有 unauthorized DM 的 `decline` + 24h 静默、pairing 限流 | **群消息级别的限流、重复消息抑制、@ 洪水防护** |
| **多群并发的资源控制** | 有 profile 隔离与 multiplexed gateway | **并发上限、队列长度上限、单群预算** |

### 9.4 一句话总结对 QQ bot 的建议架构

**分层**（借 Hermes 的平台无关内核 + OpenHands 的「何时跑/跑什么」分离）：

```
QQ 适配层      ← 接收事件、去重、CQ 码解析、@ 检测、权限查询、投递、流控、静默
             （平台差异只在这一层）
   ↓ 规范化事件
会话路由层      ← 群 ↔ 会话映射、多用户归并、去抖、并发上限、配额
   ↓ 「一个会话 + 一条合并后的输入」
平台无关内核    ← agent loop、工具注册表、上下文压缩、记忆、skills、子代理
   ↓ 输出 + 投递指令
QQ 适配层      ← 分段、脱敏、审核、@ 处理、（可选）私聊化
```

**继承的原则**（来自本次四个研究对象的一手文档）：
1. **默认拒绝**的多人授权 + Admin/User 分档（Hermes）
2. **硬底线黑名单**在一切权限检查之前（Hermes）
3. **有界常驻记忆 + 无界零成本检索**的双层结构（Hermes）
4. **用户消息永不被压缩**（Hermes 微压缩）
5. **元数据常驻、正文按需**的 skills 渐进披露（三家共识）
6. **压缩必须有降级阶梯**，且**缓存 vs 停顿是明示的取舍**（Hermes / Claude Code）
7. **子代理只回传摘要**，把 token 烧在隔离上下文里（Claude Code）
8. **大输出落盘 + 返回句柄**（Claude Code / SWE-agent）
9. **工具调用顺序与角色交替是框架的不变量**（Hermes）
10. **从最小工具集起步**，按模型能力分档设计复杂度（mini-SWE-agent 的教训）

---

## 10. 参考链接清单

### 10.1 一手来源：opencode

| # | 内容 | URL |
| --- | --- | --- |
| 1 | 官方仓库 README（安装、内置 agents、命名政策） | https://github.com/sst/opencode |
| 2 | 官方 README（raw，含 Desktop 与 agents 说明） | https://raw.githubusercontent.com/sst/opencode/dev/README.md |
| 3 | Intro / 快速开始 / Plan 与 Build 工作流 / `/undo` `/redo` / `AGENTS.md` | https://opencode.ai/docs/ |
| 4 | Agents：primary/subagent、内置 7 个 agent、`steps` 与迭代预算、模型继承、配置项 | https://opencode.ai/docs/agents/ |
| 5 | Tools：13 个内置工具、权限键、MCP、ripgrep/`.ignore` | https://opencode.ai/docs/tools/ |
| 6 | Permissions：三态、对象语法、last-match-wins、默认值、`external_directory`、`doom_loop`、`--auto` | https://opencode.ai/docs/permissions/ |
| 7 | Agent Skills：搜索路径、frontmatter 校验、`<available_skills>`、skill 权限 | https://opencode.ai/docs/skills/ |
| 8 | Plugins：加载位置、npm 自动安装、插件上下文、完整事件清单、加载顺序、压缩钩子、自定义工具 | https://opencode.ai/docs/plugins/ |
| 9 | Custom Tools（导航确认存在） | https://opencode.ai/docs/custom-tools/ |
| 10 | MCP servers（导航确认存在） | https://opencode.ai/docs/mcp-servers/ |

### 10.2 一手来源：Hermes Agent（NousResearch）

| # | 内容 | URL |
| --- | --- | --- |
| 11 | 文档首页：定位、安装、Key Features、平台清单（含 QQ）、Bot Mode、MCP、Memory、Skills、Security | https://hermes-agent.nousresearch.com/docs/ |
| 12 | Architecture：系统总览图、目录结构、数据流（CLI/Gateway/Cron）、设计原则、文件依赖链、测试规模 | https://hermes-agent.nousresearch.com/docs/developer-guide/architecture |
| 13 | Agent Loop Internals：9 步轮次生命周期、三种 API 模式、消息交替规则、可中断调用、顺序/并行工具执行、iteration budget、fallback、压缩持久化、源码文件表 | https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop |
| 14 | Tools Runtime：`registry.register()`、AST 自动发现、`check_fn`、toolset 解析、动态 schema 修补、调度流程、错误包装、agent 级工具、异步桥接、`DANGEROUS_PATTERNS` 审批、7 种终端后端 | https://hermes-agent.nousresearch.com/docs/developer-guide/tools-runtime |
| 15 | Context Compression and Caching：双压缩系统、usage anchor、图片计价、4 阶段算法、结构化摘要模板、失败冷却与降级阶梯、`in_place`、`lean` 模式、全部配置默认值、Codex 阈值 autoraise、原生 Responses compaction | https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching |
| 16 | Micro-compaction：分期付款模型、用户消息永不压缩、游标/滚动摘要/defrag、DB 同步、失败处理、prompt cache 代价、压缩模型选择、遥测、实测数据 | https://hermes-agent.nousresearch.com/docs/developer-guide/micro-compaction |
| 17 | Persistent Memory：MEMORY.md/USER.md 上限、冻结快照、三个动作、substring 匹配、超限报错、注入扫描、`session_search` 对照表、`/journey`、`write_approval`、`auxiliary.background_review`（含 `defer`/`max_input_tokens`/`extra_tools`）、外部 provider | https://hermes-agent.nousresearch.com/docs/user-guide/features/memory |
| 18 | Skills System：`~/.hermes/skills/`、渐进披露三级、SKILL.md 格式与校验、平台限定、条件激活、环境变量声明、目录结构、Hub 供应链安全、NVIDIA SkillEvaluator、外部目录、`create_dir`、项目 skills + trust + 扫描期隔离、`/learn`、媒体投递指令、`[[as_document]]` | https://hermes-agent.nousresearch.com/docs/user-guide/features/skills |
| 19 | Security：八层模型、审批模式与超时、`cron_mode`/`single_query_mode`/`unattended_mode`、硬底线黑名单、`approvals.deny` 全部细节与威胁模型边界、文件写入安全与受保护路径、`HERMES_WRITE_SAFE_ROOT`、Docker 加固参数与容器内跳过检查、监督网关护栏、用户授权六步顺序、DM pairing 完整参数、Admin/User 分档 | https://hermes-agent.nousresearch.com/docs/user-guide/security |
| 20 | Messaging Gateway：平台能力对照表（含 QQ 行）、架构、静默令牌、gateway 命令、watchdog、chat 命令清单、会话管理、`/model` 持久化、delivery ledger、会话连续性、`channel_overrides`、安全（allowlist/pairing/admin 分档）、重定向与 busy-input 模式、clarify 多选、tool progress、后台会话 `/bg`、后台进程通知 | https://hermes-agent.nousresearch.com/docs/user-guide/messaging/ |
| 21 | Bot Mode（导航确认存在，**本次未展开**） | https://hermes-agent.nousresearch.com/docs/user-guide/bot-mode |
| 22 | 中文第三方站（容器加固要点，**二手**） | https://hermes-agent.org/zh/ |
| 23 | DeepSeek API 官方文档：Hermes 接入（**一手，佐证其 agent 定位**） | https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/hermes/ |
| 24 | CSDN 博客：Hermes 为何好用（**二手**，注意其数字与官方不一致） | https://blog.csdn.net/buvuvib66/article/details/161517783 |

### 10.3 一手来源：Claude Code / Anthropic

| # | 内容 | URL |
| --- | --- | --- |
| 25 | Engineering：Equipping agents for the real world with Agent Skills（三级渐进披露、代码执行、评测指南、安全考虑、开放标准公告） | https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills |
| 26 | Engineering：Effective context engineering for AI agents（context rot、attention budget、compaction/note-taking/sub-agent 三技术、just-in-time 上下文、混合策略与其代价） | https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents |
| 27 | Hooks reference（31 个事件、生命周期图、matcher 语义、5 种 handler、决策控制表、`additionalContext`、`updatedInput`/`updatedToolOutput`、`CLAUDE_ENV_FILE`、治理设置） | https://code.claude.com/docs/en/hooks |
| 28 | Hooks reference（Markdown 原文，信息密度更高） | https://code.claude.com/docs/en/hooks.md |
| 29 | Skills（locations 表、发现与优先级、`context: fork`、动态上下文注入、`allowed-tools`、`skillOverrides`、`/skill-doctor`、skill-creator 评测、压缩后内容丢失） | https://code.claude.com/docs/en/skills.md |
| 30 | MCP（传输、命名空间、工具搜索、输出限额 10k/25k/500k、`_meta["anthropic/maxResultSizeChars"]`、配置卫生、凭据隔离、trust 前置、`claude mcp serve`） | https://code.claude.com/docs/en/mcp.md |
| 31 | Manage costs（`/usage` 输出、成本基准、归因、long context/cache miss 归因清单、模型分级、TPM/RPM 表、后台 token、治理） | https://code.claude.com/docs/en/costs.md |
| 32 | Create custom subagents（**HTML 正文未渲染，细节未取得**） | https://code.claude.com/docs/en/sub-agents |
| 33 | 文档索引（用于发现其他页面，如 agent-teams / permission-modes / prompt-caching / worktrees） | https://code.claude.com/docs/llms.txt |
| 34 | 官方 skills 仓库（PDF skill 示例） | https://github.com/anthropics/skills/tree/main/document-skills/pdf |
| 35 | Agent Skills 开放标准 | https://agentskills.io/ |

### 10.4 一手来源：SWE-agent / mini-SWE-agent / OpenHands

| # | 内容 | URL |
| --- | --- | --- |
| 36 | SWE-agent 论文（arXiv:2405.15793，ACI 论点、pass@1 12.5% / 87.7%） | https://arxiv.org/abs/2405.15793 |
| 37 | SWE-agent 架构文档（CLI → SWEEnv → SWE-ReX Deployment → Docker/shell → Agent.forward() → HistoryProcessor → parser） | https://swe-agent.com/latest/background/architecture/ |
| 38 | SWE-agent ACI 文档（linter 门控、100 行文件查看器、只列文件名的搜索、空输出提示） | https://swe-agent.com/latest/background/aci/ |
| 39 | mini-SWE-agent README（「100x simpler」、只有 bash、线性历史、`subprocess.run`、SWE-bench >74%、采用者、与 SWE-agent 的选择表、**「a lot of this is not needed at all」**） | https://raw.githubusercontent.com/SWE-agent/mini-swe-agent/main/README.md |
| 40 | mini-SWE-agent 文档站 | https://mini-swe-agent.com/latest/ |
| 41 | SWE-agent 配置文档（YAML：templates/models/tools/environments/demos/parsers/bundles） | https://swe-agent.com/latest/config/ |
| 42 | SWE-agent History processors 参考 | https://swe-agent.com/latest/reference/history_processor_config/ |
| 43 | SWE-agent Action parsers 参考 | https://swe-agent.com/latest/reference/parsers/ |
| 44 | SWE-agent 添加自定义工具教程 | https://swe-agent.com/latest/usage/adding_custom_tools/ |
| 45 | OpenHands 论文（arXiv:2407.16741，generalist agent 定位、多 agent 协调、沙箱、MIT、ICLR 2025） | https://arxiv.org/abs/2407.16741 |
| 46 | OpenHands 仓库 README（Agent Canvas 定位、三种运行时模式与风险警告、多仓库边界、ACP 支持、网络暴露安全设计） | https://raw.githubusercontent.com/OpenHands/OpenHands/main/README.md |
| 47 | OpenHands Backend Architecture（**正文为图片，细节未取得**） | https://docs.openhands.dev/openhands/usage/architecture/backend |
| 48 | OpenHands 文档站首页 | https://docs.openhands.dev/ |
| 49 | OpenHands software-agent-sdk 仓库（README 指其为 SDK/Agent Server 所在，**本次未展开**） | https://github.com/OpenHands/software-agent-sdk |
| 50 | OpenHands automation 仓库（调度与 webhook，**本次未展开**） | https://github.com/OpenHands/automation |

### 10.5 二手来源（用于身份确认与交叉参照）

| # | 内容 | URL |
| --- | --- | --- |
| 51 | 第三方 Hermes 中文站（与官方能力一致，指向官方仓库） | https://hermes-agent.org/zh/ |
| 52 | 第三方 Hermes 中文社区文档站 | https://hermesagent.org.cn/ |
| 53 | 菜鸟教程 Hermes Agent（第三方教程） | https://www.runoob.com/ai-agent/hermes-agent.html |
| 54 | Hermes Desktop 官方下载页（nousresearch.com 域名） | https://hermes-agent.nousresearch.com/desktop |

### 10.6 本次调研中明确「未找到 / 未取得」的项（不编造，列出以便后续补齐）

| 主题 | 状态 |
| --- | --- |
| Hermes 的 Prompt Assembly 页（`stable → context → volatile` 三层的具体内容） | 页名确认存在，**未展开抓取** |
| Hermes 的 Gateway Internals / Gateway Session Lifecycle / Multiplexing Gateway / Session Storage / State DB Recovery / Provider Runtime Resolution | 页名确认存在，**未展开抓取** |
| Hermes 的 Bot Mode 细节（Named Bots、群内协作、`@mentions`） | **未取得** |
| Hermes 的 Mixture of Agents、Curator、Tool Search、Honcho、Context Files、Context References | 页名确认存在，**未取得** |
| Hermes 的 ACP Internals、Cron Internals、Trajectory Format、Egress proxy、Browser CDP Supervisor | 页名确认存在，**未取得** |
| Hermes 的 `prompt_caching.py` 断点具体分布（`system_and_3` 的完整定义） | 页面被截断，**部分未取得** |
| opencode 的上下文压缩阈值/算法细节 | **未找到**（官方未公开） |
| opencode 的并行工具调用策略 | **未找到** |
| opencode 的 subagent 结果回传方式（全文 vs 摘要） | **未找到** |
| opencode 的 prompt caching 支持 | **未找到** |
| opencode 的沙箱方案 | **未找到** |
| opencode v2 相对 v1 的架构变化 | **未找到**（文档横幅提示 v2 已发布，但 `/docs/` 仍为 v1 内容） |
| Claude Code 的 sandbox 具体实现（seatbelt/bubblewrap 等） | **未取得** |
| Claude Code 的 tools reference 完整工具清单与 schema | 页名确认存在（`/docs/en/tools-reference`），**未展开抓取** |
| Claude Code 的 permission-modes 专页、prompt-caching 专页细节 | 页名确认存在，**未取得** |
| Claude Code 的 sub-agents 页正文（含 `model`、`isolate-high-volume-operations`、`scope-mcp-servers-to-a-subagent`、`run-every-subagent-on-one-model`） | **HTML 正文未渲染** |
| Claude Code 的主循环迭代上限与成本上限的具体数值 | **未取得** |
| SWE-agent 的 step limit / cost limit 具体数值与「invalid action」重试机制的官方描述 | **未取得**（论文有描述但本次仅取得摘要页） |
| OpenHands 的 EventStream / AgentController / Action-Observation / Condenser / `LLMSummarizingCondenser` / `max_message_chars` / SecurityAnalyzer / AgentDelegateAction / microagents 的官方一手文档 | **未取得**（文档站相关页为图片或未在抓取范围内） |
| 是否存在第二个值得并列研究的「Hermes」agent 框架 | **未找到** |

---

## 附录：给目标 harness 设计者的一页速查

**如果只记 12 条：**

1. **默认拒绝**所有未授权用户；`/new` 之类的显式命令之外，**不要有隐含状态**。
2. **硬底线黑名单代码级硬编码**，在一切权限检查之前，不可被配置或 YOLO 覆盖。
3. **无人值守场景（群聊）默认拒绝危险操作**，而不是等问题超时。
4. **记忆分两层**：小而有界的常驻（固定 token 成本）+ 大而无界的检索（零 LLM 成本）。
5. **用户消息永不被摘要**；只压缩 agent 自己产生的内容。
6. **改写过已发送的历史 = 作废 prompt cache**，把它做成默认关闭的开关 + 一个度量。
7. **压缩必须有降级阶梯**，且「摘要失败」不能静默丢中间轮。
8. **旧的工具结果是最先该丢的**，丢正文、留句柄。
9. **子代理的价值在上下文隔离**，不在并行；只回传摘要，并给它独立的预算上限。
10. **工具输出越少越好**（SWE-agent 的搜索工具故意只列文件名）；大结果落盘给路径。
11. **从最小工具集起步**；模型变强后，复杂脚手架的收益下降而维护成本不降。
12. **工具调用顺序、角色交替、tool 对完整性是框架的责任**，模型不会替你维护。

**目标 harness 必须自己解决、上述项目未解决的 5 件事：**

1. **自动的会话边界**（群成员不会主动 `/new`）
2. **多人并发输入的归并策略**（Hermes 只解决了单用户连发）
3. **按 QQ 用户/群的成本归因与配额**（操作者 ≠ 使用者 ≠ 付费者）
4. **无斜杠命令的 skill 触发**（普通 QQ 群没有 `/` 命令）
5. **群聊可见性策略**（什么在群里说、什么私聊、什么静默）
