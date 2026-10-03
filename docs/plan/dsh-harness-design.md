> ⚠️ **可信度声明**：本文由子代理从本机源文件/设计文档机械提取（2026-10-03），结论均标注来源与行号。
> 我逐条抽验过其中若干关键结论（动作总数 190、wrapper 四类、HTTP 端点=动作名、缓存命中率字段存在），均吻合。
> 引用具体数字前建议按来源行号回查。

---

# DeepSeek Harness（DSH）真实设计提取

> 目的：把 DSH 桌面端（Electron 外壳 + `app.asar` 内捆绑的 283 个 `@deepseek-ai/dsh-*` 包）的**设计意图**提取成一份可用于设计**另一个 harness**（EggBloom，QQ 聊天机器人）的参考文档。
>
> **证据来源**：每个包随包出货的 `README.zh.md`（中文设计文档，权威说明），不逆向压缩 JS。
> 提取到的源码副本位于 `D:\git\_eggbloom-backup-20261003\dsh-src\<包名>\`。
> 本文每条结论都标注 `来源：<包名>/README.zh.md`；标注「(推断)」的是本文从多条证据推出的结论，不是文档原话。
>
> 已提取并精读的包（31 个）：
> `dsh-agent-loop`、`dsh-agent`、`dsh-tools`、`dsh-session`、`dsh-compaction-basic`、`dsh-subagent`、`dsh-goal`、`dsh-goal-round-driver`、`dsh-tool-goal`、`dsh-command-goal`、`dsh-workflow`、`dsh-workflow-ptc`、`dsh-tool-workflow`、`dsh-system-prompt`、`dsh-plan-mode`、`dsh-token-meter`、`dsh-spill-policy`、`dsh-output-retention`、`dsh-permission-presets`、`dsh-user-approval`、`dsh-sandbox`、`dsh-sandbox-policy`、`dsh-base`、`dsh-agent-preset`、`dsh-plugin-manager`、`cordis`、`dsh-mcp-client`、`dsh-acp`、`dsh-hook-protocol`、`dsh-hooks-claude-code`、`dsh-hooks-codex`。

---

## 1. 整体分层

DSH 不是一个单体 agent，而是**七个可独立装卸的层**，层与层之间只通过 Cordis 服务（`ctx.*`）和**会话事件**耦合。任何一层都可以被换掉而不动其他层——这是它最重要的架构性质。

| # | 层 | 职责 | 主包 | 对外服务 |
|---|---|---|---|---|
| 0 | **插件容器 / DI** | 依赖注入、作用域、fiber 生命周期、事件、撤回 | `cordis` | `Context` / `Service` / `ctx.on` / `fiber.dispose()` |
| 1 | **会话（事件溯源日志）** | 每个模型可见事实的仅追加记录 + 从日志派生模型历史 | `dsh-session` | `ctx.sessions`、`Session.append/deriveMessages` |
| 2 | **模型** | 提供方/模型路由、适配器默认值、推理强度、上下文窗口、流式 | （`dsh-llm`，本文未提取，见 `dsh-agent-loop` 引用的 `ctx.llm.prepareCall()` / `resolveModelInfo()`） | `ctx.llm` |
| 3 | **工具** | 类型化能力的注册、schema 投影、执行流水线、策略 | `dsh-tools` | `ctx.tools` |
| 4 | **决策 / 拦截** | 允许-拒绝-询问、审批、计划模式、钩子桥接 | `dsh-user-approval`、`dsh-plan-mode`、`dsh-hook-protocol` | `ctx.approval`、`ctx.planMode` |
| 5 | **成本与上下文** | token 计量、压缩、溢出到磁盘、输出保留 | `dsh-token-meter`、`dsh-compaction-basic`、`dsh-spill-policy`、`dsh-output-retention` | `ctx.tokenMeter` |
| 6 | **主循环 / 驱动器** | 轮次与步骤状态机、inbox、请求组装、取消 | `dsh-agent`（接口）+ `dsh-agent-loop`（唯一具象实现） | `ctx.agents` |
| 7 | **组合 / profile** | 声明式插件清单、平台门控、用户覆盖 | `dsh-base`、`dsh-agent-preset`、`dsh-plugin-manager` | profile 的 `cordis.patch.yml` |

**关键设计约束（贯穿全文）**：

1. **模型可见即已记录（model-visible ⇒ logged）。** 任何到达模型请求的内容都必须能从会话日志重建。
   *来源：`dsh-session/README.zh.md`「设计理念」*
2. **接口与实现分离。** `dsh-agent` 定义 `Agent` 句柄、注册表与 `agent/*` 事件；`dsh-agent-loop` 是唯一具象实现，通过已注册的 `AgentFactory` 提供。消费方依赖 `dsh-agent` 而**不**依赖 `dsh-agent-loop`。
   *来源：`dsh-agent/README.zh.md`「设计理念」、`dsh-agent-loop/README.zh.md`「设计理念」*
3. **层间解耦靠事件，不靠导入。** 例如 `dsh-subagent` 拥有 descriptors 与 catalog，但委派工具的 schema 归消费方包；`dsh-workflow` 拥有运行约定，执行引擎归 `dsh-workflow-ptc`。
   *来源：`dsh-subagent/README.zh.md`、`dsh-workflow/README.zh.md`「设计理念」*
4. **失败关闭（fail-closed）。** 审批应答者缺失 → `unavailable` 拒绝执行；沙箱后端不可用 → `SANDBOX_UNAVAILABLE` 拒绝执行（**绝不**静默无限制放行）。
   *来源：`dsh-user-approval/README.zh.md`「概述」、`dsh-sandbox/README.zh.md`「故障关闭行为」*

---

## 2. 主循环契约

> 本章主要来自 `dsh-agent-loop/README.zh.md`（唯一具象驱动器 `ReactLoopAgent`），接口面来自 `dsh-agent/README.zh.md`。

### 2.1 轮次（turn）与步骤（step）状态机

```
inbox 待处理条目
   │
   ├─ 轮次边界：① 打开持久轮次 ② 原子领取「next-step 输入 + 一条排队提示词」
   │
   └─ 步骤 N ──────────────────────────────────────────────┐
        ├─ 组装 prompt + 工具 schema、投影 runtime context    │
        ├─ agent/pre-step  waterfall → PreStepDecision        │
        │     { kind:'reject' }  → 不打开步骤，返回           │
        │     { kind:'enter', messages, startsRequestSeries? }│
        ├─ step/start 记录                                    │
        ├─ agent/request waterfall → ctx.llm.prepareCall()     │
        │     （解析路由、推理强度、输出上限、adapterDefaults）│
        ├─ 【同步】协调渲染后的提示词与存活的 system/message    │
        │     （首次尝试追加已接纳的 user/message 批次）        │
        ├─ 按需记录 request/header 与 request/context         │
        ├─ 派生并深冻结请求 → 发起流式请求                     │
        │     start → chunk* → 恰好一个终态 end                │
        ├─ 持久 assistant/message（committed）                 │
        ├─ 工具流水线（见第 3 节）                             │
        └─ 步骤之间：只领取 next-step 输入；回到步骤 N+1 ──────┘
```

- **轮次结束事件** `turn/end` 声明类型为 `TurnEndCancelCause`；取消时循环在其中记录一份新的 `AgentCancelCause`（保留调用方 `kind` 与 hook 的 `reason` 文本），**实时 `AbortSignal.reason` 仍是调用方对象**——拷贝是为了让调用栈不进入日志、让结束事件可追加。
  *来源：`dsh-agent-loop/README.zh.md`「失败与取消」*
- **没有内置轮次预算。** 工具调用或 steering 会让当前轮次继续；限制失控轮次的策略必须从既有生命周期扩展点（如 `agent/turn-stopping`）执行取消。
  *来源：`dsh-agent-loop/README.zh.md`「已知限制」*
- **`maxParallelToolCalls` 是 volatile Config 字段**，在**下一个工具组开始时**读取（`agents` 数组则是启动配置，不热改）。
  *来源：`dsh-agent-loop/README.zh.md`「配置声明式 agent」*

### 2.2 `agent/*` 事件分类

| 事件 | 分发 mode | 语义 |
|---|---|---|
| `agent/pre-step` | **waterfall（可拒绝）** | 拒绝拟进入的步骤，或替换进入它的消息批次。`PreStepDecision = {kind:'reject'} \| {kind:'enter', messages, startsRequestSeries?}` |
| `agent/request` | **waterfall（可改写）** | 补齐/覆盖提供方+模型对；分发前可改请求 envelope |
| `agent/request-error` | **waterfall（可重试）** | 监听器返回 `{kind:'retry'}` 且**不调用 `next()`** = 重试；未被处理的失败是终态，进入 `agent/request-error` |
| `agent/turn-stopping` | **串行监听器** | 在本可完成的轮次关闭前运行；可通过 steer 使轮次保持打开 |
| `agent/assistant-stream` | 进程本地流 | 一次 Assistant attempt 的有序 `start` → 瞬态 `chunk`*（索引从 0 密集递增）→ 恰好一个终态 `end`（`end.index` = 下一个分片位置） |
| `agent/created` / `agent/disposed` | **串行** | 创建/销毁公告。`agent/created` 监听器**不得**等待 `agent.whenIdle()` 或自身所有者 dispose（会造成死锁） |
| `agent/status` | 观察 | UI 与协调状态；不变式要求「无操作的 status 转换会失败」 |
| `agent/inbox/spliced` | **持久投影 fold** | 每次 inbox 变更提交一条规范化事件，投影注册表同步折叠 |
| `agent/inbox/inserted { message }` | 实时通知 | 每次插入 |
| `agent/inbox/claimed { message, turn }` | 实时通知 | 纯删除 splice = 领取 |
| `agent/inbox/discarded { message }` | 实时通知 | 普通删除携带 `outcome:'canceled'` |
| `agent/error` | 观察 | 驱动层错误（如 goal 驱动器的 flush 失败） |

*来源：`dsh-agent/README.zh.md`「拦截或观察进行中的工作」与「步骤准入」「持久 inbox」；`dsh-agent-loop/README.zh.md`「轮次与步骤流程」*

**重要分层细节**：`loop` 会在 committed end frame **之前**把完整紧凑流提交为一个 `assistant/message` 或 `assistant/attempt`，因此**实时事件是呈现数据，不是回放来源**——回放来源永远是持久日志。
*来源：`dsh-agent/README.zh.md`「拦截或观察进行中的工作」*

### 2.3 Inbox（收件箱）投影

- `Inbox` 暴露两份待处理列表 `nextTurn` 与 `nextStep`，通过 `append` / `prepend` / `replace` / `remove` / `clear` / `splice` 变更。
- **普通删除与 `clear()` 都是持久取消。** 领取使用**不带 outcome 的纯删除**。
- 重建过程会拒绝不安全/越界的 splice 坐标，以及跨两份列表重复的 `MessageId`，并报告出错事件的 seq。
- API 层三个动词：
  - `followup()` —— 排队一条普通的下一个**轮次**提示词并唤醒驱动器
  - `steer()` —— 提交**下一步**输入并唤醒
  - `inject()` —— 添加面向模型的上下文但**不唤醒**，落在下一个被接纳的步骤中
  *来源：`dsh-agent/README.zh.md`「驱动 agent 的对话」*

### 2.4 创建 / 恢复 / 拆除（事务与所有权）

- 创建是**同一个受回滚保护的事务**：构造私有会话 + 具象 agent + 带作用域上下文 → 等待可选 `setup` → 进入两个注册表 → 宣告 `session/created` → 等待串行 `agent/created` 监听器 → 释放已排队输入。
- `AgentHandle.dispose()` 是一项**能力（capability）**：只有其持有者能拆除该 agent。`ctx.agents.get(id)` 只返回裸 `Agent`。已注册的工厂提供方是**结构化共同拥有者**——提供方卸载会停止并排空它创建的每个句柄。
- 拆除顺序：停止并排空驱动器 → 撤销作用域 → 关闭会话写路径 → detach agent → detach 会话。**每次 detach 绑定到确切进入的对象**，陈旧 disposer 无法移除之后出现的同 id 替代项。
- `whenIdle()` 在整个 agent 达到完全停稳后完成；这是所有路径汇合的记忆化边界。
  *来源：`dsh-agent/README.zh.md`「注册表与生命周期」「所有权不变式」；`dsh-agent-loop/README.zh.md`「创建与拆除」*

### 2.5 持久化集成

- 挂载 `ctx.sessionPersistence` 后：`create` → `persistence.create(header)`（发布前就存储持久身份、取得写所有权）；`resume` → `persistence.open(id,'write')`（**排除同 id 并发恢复**）。
- 对轮次中途崩溃的日志，循环把 `interruptedTurnClosers` 作为普通批次追加——**语义崩溃修复是 agent 层的职责，不是存储入口的职责**。
- `appendUnstoredSuffix` 把 setup 窗口期间追加的事件（seed 标记、委派策略记录）存储，它们**绝不会**经 `session/event` 重新发出。
- 记忆化 teardown 在循环提交收尾事件之后关闭句柄。**没有后端时，会话只存在于内存中，其余一切不变。**
  *来源：`dsh-agent-loop/README.zh.md`「持久化集成」*

### 2.6 取消语义（协作式）

| 场景 | 行为 |
|---|---|
| `agent.cancel(cause)` | 中止当前活动，并**清除待处理工作** |
| `agent.cancel(cause, {keepInbox:true})` | 只中止轮次，保留待处理项。**不存在「让轮次继续、只中止步骤」的操作** |
| 已流式交付的文本 | **保留**（`assistant/message` 带 `interrupted:true` 与已交付前缀，使下一次请求包含用户看到的内容） |
| 取消后未分发的工具调用 | 收到合成的 `tool/call` + 结果对，错误码 `ABORTED_BEFORE_DISPATCH`，文本 `Error: tool call aborted before dispatch` |
| 工具主体前的取消 | `ABORTED_BEFORE_DISPATCH` |
| 工具主体后的取消 | 只能把**成功**结果替换为 `ABORTED` |
| 用户「停止」按钮路径 | `agent.cancel({kind:'user'})`，**不带** `keepInbox` → 排队输入被丢弃并记录一次收件箱拼接，不会在之后唤醒已归档会话 |
| 插件失败 | 结束的是**轮次**，不是循环（middleware、结果处理、工具及其他扩展失败仍抛出并直接关闭轮次） |

*来源：`dsh-agent/README.zh.md`「已知限制」「注册表与生命周期」；`dsh-agent-loop/README.zh.md`「失败与取消」；`dsh-tools/README.zh.md`「执行与取消」*

### 2.7 提示词准入与请求序列（决定 KV cache 生死）

这一节是 DSH 里最反直觉、也最有价值的部分：

- 请求 = `header.config` + `deriveMessages()` + `header.tools`，**不携带 `system` 字段**——渲染后的系统提示词本身是一个 `system/message` **surface 节点**。
- **准入依据实际的 `prepareCall()` 结果**，而非先前的 `request/context`。
- 路由不具备 `systemPromptUpdate` 能力时：非空渲染文本**归并到首个系统节点**（in-place 重写 → 从该节点第一个 token 起缓存未命中）。
- 路由声明 `systemPromptUpdate: 'in-history'` 时：同一请求序列延续期间的非空提示词变更**追加到已缓存历史之后**（→ 直到历史末尾的前缀仍可复用）。
- **以下情况开启新请求序列**：pre-step 决定声明 `startsRequestSeries`，或 `session.surface.contentGeneration` 自附接/上次请求以来变化（surface 替换或图片省略决定）。
- 清除提示词必须为**所有生效的 system 节点**记录空内容替换，而非仅替换最新节点。
  *来源：`dsh-agent-loop/README.zh.md`「轮次与步骤流程」；`dsh-session/README.zh.md`「追加与派生」；`dsh-system-prompt/README.zh.md`「模型体验」*

---

## 3. 工具系统

> 主来源：`dsh-tools/README.zh.md`。

### 3.1 注册方式

```ts
ctx.tools.register(defineTool({
  name: 'read_file',
  description: 'Read a file from disk.',
  parameters: {
    path:   { type: 'string', required: true, description: 'Absolute file path' },
    offset: { type: 'number' },
    limit:  { type: 'number' },
  },
  output: {
    schema: { type: 'string' },
    render: (_args, value) => [{ type: 'text', text: value }],
  },
  async execute(args, exec) {           // args 是类型化的
    return readFile(args.path, { encoding: 'utf8', signal: exec.signal })
  },
}))
```

- **注册一个工具就足以让它可见**——注册表会自动把其 schema 送入系统提示词组装（`dsh-system-prompt` 的 `ToolRuntime` 自动注册自身）。
- `output`、`execute`、`finalizeContent`、`timeoutMs`、呈现回调**绝不泄漏到协议上**；请求时把它们投影为面向模型的 `ToolSchema` 集合。
- MCP 服务器发现工具后，用服务器的 schema 调用 `ctx.tools.register()`。
- **按 agent 限制**：`ctx.tools.restrict(filter)` 对单个 agent 继承的全局工具应用允许或拒绝掩码；掩码**取交集**，作用域注册保持可见，限制在 dispose 时解除。`ctx.tools.get(name, scope)` / `ctx.tools.schemas(scope)` 按一个作用域的视角解析。

### 3.2 Schema 声明

- 统一 schema DSL 支持：`string`、`number`、`integer`、`boolean`、`null`、`array`、`object`、仅供作者使用的 `json`、以及**恰好匹配一个分支**的 `oneOf`。
- `InferValue` 在 **16 层容器内**保留精确类型，之后加宽为 `JsonValue`。
- 原始 JSON Schema（`JsonSchemaNode`）是与 **subagent、工作流和 MCP 共享的协议级对应类型**。
- `deferLoading: true` 由 `defineTool()`、注册表模式投影和系统提示组装保留；它请求延迟加载工具定义，**不**意味着存在 `tool-addition` 记录。
  *来源：`dsh-tools/README.zh.md`「注册工具」「已知限制」*

### 3.3 并行安全 vs 独占（本设计的核心分类）

- **分类读的是已解析的工具定义**：`executionMode()` 直接读取，插件只能在**自身拥有的定义**上声明分类器。
- 配置 `maxParallelToolCalls`（**默认 10**）：每个步骤同时在途的并行安全工具调用数；`1` 为串行。
- 独占调用**单独运行并构成排序屏障**。
- **分类是一元的（monolithic）**：安全性取决于「比较同级调用或资源」的调用必须保持独占。
- 文档给出的判定准则（原文）：「安全性取决于比较同级调用或资源的调用必须保持独占」。
- 在 PTC 模式里这条约定被复述给模型：`Independent read-only calls MAY overlap under Promise.all (safe calls run concurrently; mutating calls run alone, in submission order).`
- **限制**：**并发策略不是事件门禁**——插件无法在事件层动态改变分类。
  *来源：`dsh-tools/README.zh.md`「使用本包」「已知限制」「PTC mode」；`dsh-agent-loop/README.zh.md`「配置声明式 agent」*

### 3.4 结果回传

- 定义持有规范输出声明；`execute` 只返回**所声明的 JSON 值**。
- `output.render(args, value)` 把值渲染成内容块（`[{type:'text',text}]`、图片等）。
- `projectContent` 在执行后策略**之前**安装执行期间准备的图文内容；`finalizeContent` 保留为策略之后的最终内容处理。
- 模型侧看到的最终内容 = 注册表的最终内容；**任何抛出异常或遭到拒绝的调用都会转换为确切的 `Error: <message>`**，结构化的用户可见失败详情（`ToolErrorInfo` 的名称/代码/原因）**不会**加入该消息。
- 失败与结果词汇：`UNKNOWN_TOOL`（未知工具或抛异常）、`TOOL_TIMEOUT`、`ABORTED`、`ABORTED_BEFORE_DISPATCH`、`TOOL_NOT_STARTED`、`TOOL_OUTCOME_UNKNOWN`。**未知工具与抛出异常的工具都会变成结构化错误，因此调用会失败而不会结束轮次。**
  *来源：`dsh-tools/README.zh.md`「执行与取消」「模型体验」；`dsh-session/README.zh.md`「崩溃修复结果」*

### 3.5 受守卫的工具流水线（固定顺序）

```
tools/pre-execute   →  已注册单调守卫 (ctx.tools.guard)  →  tools/execute  →  tools/post-execute  →  finalizeContent  →  tools/result
   可扩展的 allow/deny/ask      在 pre-execute 之后注册的        环绕分发包装层        检查/替换、附加有序上下文   定义持有          仅观测
   waterfall                   拥有方策略；返回的理由                                   （值替换、反馈阻止）
                               会拒绝调用，后续监听器
                               无法把拒绝重新变为允许
```

- 只有 `tools/execute` 视图**可以替换必填信号**，注册表会在调用主体前重新融合调用方信号。
- **`tools/pre-execute` 有意不允许改写 `exec.arguments`**（否则日志记录与呈现的参数会与实际运行内容失去同步）。
- 每次类型化调用都会实体化并**冻结解析后的参数**、分配**不透明关联 token**，再运行策略与分发。
- **`deferLoading`** 之外，每个调用都会收到调用方拥有的 `exec.signal`，工具主体**必须观测它**。
  *来源：`dsh-tools/README.zh.md`「对调用实施策略」「设计理念」「执行与取消」「扩展点」*

### 3.6 超时与失败策略

- **定义中的 `timeoutMs` 仅作声明之用**：注册表**绝不**强制截止时间；要强制执行必须使用 `dsh-tool-call-timeout-policy` 包装层。
- pre-execute 拒绝可在模型可见原因旁附带 `ToolErrorInfo`；原生与 PTC 持久投影保留结构化名称/代码/可选用户可见原因。
- 取消等待**完全停稳**。
- 工具输出超限由第 7 节的「输出保留 + spill」两层处理，不在这里。
  *来源：`dsh-tools/README.zh.md`「已知限制」「执行与取消」*

### 3.7 PTC 模式（工具呈现方式的第二种形态）

- 配置 `mode`：`native`（每个可见 schema，**默认**）、`ptc`（只有 `run_code` 加一份生成 SDK）、`both`。
- `maxParallelSubCalls`（默认 10）限制 `run_code` 程序**重叠子调用**的并发；`1` 恢复严格串行分发。
- 子调用使用 `<parent>:ptc:<n>` 标识；调用通过复用原生并发约定的**每次运行独有池**调度。
- 在纯 `ptc` 下，模型直呼其他任何可见工具都会**在策略之前**解析为 `UNKNOWN_TOOL`——通告面与可调用面保持一致。
- `run_code` 的 Node 默认超时 120,000 ms，上限 600,000 ms（**包含嵌套工具和审批等待**）。
- **每次运行都会获得全新的 `run_code` 状态**（MVP 不采用持久 REPL 内核，因为跨调用状态不会出现在日志中）。
- **中间值只存在于执行局部且没有字节上限**，无法从会话回放重建，可能耗尽进程/worker 内存；只有外层 `run_code` 结果有硬大小上限。
  *来源：`dsh-tools/README.zh.md`「配置呈现模式」「PTC mode」「已知限制」*

---

## 4. 子代理（Subagent）

> 主来源：`dsh-subagent/README.zh.md`。

### 4.1 一个服务，多个提供方

- 服务是**具名提供方注册表**；每个后端以唯一名称注册（进程内 spawn 后端默认为 `spawn`），请求按名称选择一个。
- 一个组合可以**并排**提供进程内、ACP、SDK、Codex、Claude Code 子级。
- **只挂载服务本身不会改变任何行为**：在组合出提供方和工具之前，什么都不能委派。
- `dsh-tool-subagent` 提供面向模型的工具（`provider`、`toolName` 配置）。

### 4.2 两种子级形态（这是 spawn / fork 问题的真实答案）

DSH 的两种形态是 **one-shot（一次性）** vs **continuable（可继续）**，而**「fork」在 DSH 里不是 subagent 的模式，而是 `dsh-session` 的能力**——`ctx.sessions.fork(source, boundary?, childSessionId?)` 从实时源会话复制包含切点的**精确事件前缀**。这是一个重要的概念区分（很多 harness 把 fork 当成子代理模式，DSH 把它当成**会话派生**）。

| 维度 | 一次性（one-shot） | 可继续（continuable） |
|---|---|---|
| 配置 | `backgroundMode: 'one-shot'`（默认） | `backgroundMode: 'continuable'`（需提供方支持） |
| 生命周期 | 只运行一次，以单个结果结算 | 保留**持久会话**，按顺序接受后续消息 |
| 调用方拿到 | 子 agent 的最终答案作为**工具结果** | **稳定的子 agent id** + `sendMessage()` + 中断能力 |
| 所有权 | **发布时转移**给调用方 | 保留 Activation 图；同一时刻**至多一个进程内 Activation** |
| 容量 | 不受 `maxActiveSubagents` 限制 | 共享名额池，默认 `maxActiveSubagents: 8` |
| 失败 | 返回停止原因 + 安全诊断；取消以 `aborted` 结算 | 同左 |

*来源：`dsh-subagent/README.zh.md`「一次性与可继续子级」「委派设置」「可续接子代理容量」*

### 4.3 上下文如何隔离

- **「兑现即发布」**：提供方的 `start()` 只有在**真实子 agent 存在之后**才兑现，因此调用方要么拥有一段在线运行，要么一无所有。
- **「同进程值可信」**：请求、描述符与结果按不可变约定**借用**；序列化与不可信输入校验属于**进程与协议边界**。
- **子 agent 相互隔离**：崩溃或行为异常的子 agent **无法破坏父级会话**。
- 每个进程内子 agent 的运行时上下文快照携带固定声明 `subagent:delegation`：
  > `You are a delegated subagent: your permission scope was fixed when you were started and cannot be widened from inside this session — operations that require approval are rejected automatically. When the job needs access beyond that scope, do not retry the denied operation; state the limitation in your reply so the delegating agent can handle it.`
- **权限继承**：两个进程内提供方在**第一次 await 之前**快照委派权限状态——Auto 或 Full access 父级让子级获得相同的 `permission/preset` 身份，既有沙箱覆盖与审批策略固定仍然生效；同时记录这两个身份可防止 fork 中更早的同旋钮组合身份胜出。Auto 随后**独立审查 child 的每个受支持调用**（低风险直接允许、中风险需明确授权、**高风险始终拒绝**）。DSH SDK / ACP / Codex / Claude Code 子级**运行独立子运行时，不继承 Auto**。
- **模型分层**：子级创建时把请求字段叠加到**父级最新已记录请求**的提供方/模型/推理强度之上；父级还没有请求时回退到创建选项。**更改路由而不显式指定推理强度时，会清除继承的路由自有强度**，使所选模型解析自己的默认值。
  *来源：`dsh-subagent/README.zh.md`「设计理念」「一次性流程」「模型体验」*

### 4.4 结果如何回传

- **一次性**：最终答案作为**工具结果**。
- **可继续**：模型通过 `sendMessage()` 走固定 Steer 调度跨一条 parent/child 边；浏览器人类 prompt 通过内部适配器选择 Queue 或 best-effort Steer。
- **结算通知**（运行时生成的用户角色父级消息，与模型编写的父子消息相互独立）：
  > `Background subagent <child-id> finished and will do no further work unless you send it more.` → `Its closing message:` → 子级最终 assistant 输出中的非空文本块（**推理与其他非文本块不会进入通知**）。若无剩余非空文本：`It left no closing message.`
- **发现**：`listChildren(parentSessionId, signal?)` 读取 parent 自有的 `subagentCatalog` projection，**不读取子级日志、不加载或恢复子级 Agent**。物化以 `O(D)` 时间保留 D 条事实的父日志事件顺序。
  *来源：`dsh-subagent/README.zh.md`「模型体验」「消息、中断与发现」*

### 4.5 深度、并发与限制

- `maxDepth` **默认 1**（在委派工具自身未配置深度时提供默认值）；工具显式指定的深度（含 `provider-managed`）优先。深度 `0` 禁止继承此设置的工具委派；深度 `1` 只允许直接子代理。
- `maxActiveSubagents` **默认 8**：限制通过**连续可续接父子关系**共享名额的存活子代理数。**非可续接父代理建立独立的池，自身不占名额**；池的继承**不会跨越一次性父代理**。
- **容量耗尽时，新建或冷恢复以 `ACTIVATION_LIMIT_REACHED` 拒绝**（浏览器消息 `subagent/delivery-unavailable`）。**接纳不会排队**（避免等待后代的父代理又等待自己占用的名额）。
- 关键限制：
  - **仅允许相邻模型消息**——`sendMessage()` 要求**确切在线 sender**；每个 sender 可指定直接可续接 child，只有具备驻留可续接 Activation 的 sender 可指定自己的直接 parent。
  - **没有持久 parent mailbox**——child → parent 投递要求直接 parent 保持在线；服务提供的是**接受标识，不保证恰好一次投递**。
  - **驻留仅限进程内**——Activation inbox 与所有权图**不会**在两个 harness 进程之间协调；并发访问同一持久化存储需要持久化邮箱与跨进程租约协议。
  - **描述符仅进日志**，不进模型历史，并**跨压缩保留**；可继续描述符显式记录解析后的子级提供方、模型与推理强度，用于冷恢复。
  - **不回放已接受但未记录的消息**——崩溃可能丢失从未写入子会话日志、已被接受的提示词。
  *来源：`dsh-subagent/README.zh.md`「委派设置」「可续接子代理容量」「所有权与不变式」「已知限制」*

---

## 5. 工作流（Workflow）与 PTC

> 主来源：`dsh-workflow/README.zh.md`、`dsh-workflow-ptc/README.zh.md`、`dsh-tool-workflow/README.zh.md`。

### 5.1 PTC 是什么

**PTC = Programmatic Tool Calling（程序化工具调用）**，在 `dsh-tools` 里就叫「PTC mode」。它不是「workflow 的实现」，而是**工具呈现与调用的一种替代形态**：模型不再逐个发 function call，而是**写一段代码**，代码里 `await tools.name(args)` 调用工具。`dsh-workflow-ptc` 是**复用同一个 PTC Node 进程运行时**来执行工作流脚本的**引擎**——文档明确写「同一个执行提供方服务 PTC 与工作流」。

因此关系是：

```
dsh-tools (mode: ptc)  ──┐
                         ├──→ 共享同一个 PTC 执行提供方（沙箱化 Node 进程 + VM）
dsh-workflow-ptc      ──┘        （dsh-ptc-runtime-node）
   ↑ 实现 ctx.workflowEngine
dsh-workflow              定义脚本/运行/结果/事件约定（引擎无关）
   ↑
dsh-tool-workflow         面向模型的 `workflow` 工具（schema + 结果包络 + 后台任务）
```

### 5.2 工作流脚本模型

- 编排脚本是**纯 JavaScript 脚本体**（**不是 TypeScript**），以顶层 `await` 运行，以 `return <json-value>` 结尾。
- **`meta` 与 `args` 以普通 JSON 数据到达——绝不作为代码求值。**
- 提供的钩子：

| 钩子 | 语义 |
|---|---|
| `agent(prompt, opts?)` | 启动一个 subagent，以其**最终文本**兑现；提供 `schema` 时兑现**经过校验的结构化值**；**普通子 agent 失败兑现为 `null`** |
| `parallel(thunks)` | 组合独立的零参函数并发；thunk 抛出 → 该项 `null` |
| `pipeline(items, ...stages)` | 每个 item 独立走各 stage，**stage 之间无屏障**；普通 stage 抛出 → **该 item** 变 `null` 并跳过其剩余 stage |
| `phase(title)` / `log(message)` | 为观察者叙述进度 |

```js
// Script body — runs with top-level await, ends with a JSON return value:
const reviews = await parallel([
  () => agent('Review src/a.ts for correctness'),
  () => agent('Review src/b.ts for correctness'),
])
return { reviewed: reviews.length }
```

- **`meta` 是工具调用参数、不是代码**：`agent(prompt, opts)` 形式要求 `meta` 含 `name`、`description`，可选 `whenToUse`、`phases`。

### 5.3 失败纪律（很值得抄的一条）

- **钩子误用**——错误参数、未知选项、不支持的 schema、超出上限——**明确终止脚本**，绝不溶解为逐项 `null`。
- **逐项 `null` 只保留给**子运行失败与**阶段内普通脚本错误**。
- `WorkflowError` 携带**机器可路由的 code 与 `fatal` 标志**；每个 code 都是致命的，`parallel()` 与 `pipeline()` **会重新抛出致命错误**（「拼错的选项必须明确终止脚本」）。
- 启动前的同步拒绝：无法解析的脚本、格式错误的 meta、不可用的提供方路由、不支持的单次运行限制——都会**在运行存在之前**被同步拒绝，工具把这些报告为**模型可以修正的错误**。
- 取消或异常完成**返回错误，而不是部分成功**；「部分输出绝不会被报告为成功」。
  *来源：`dsh-workflow/README.zh.md`「失败与恢复」「失败纪律」；`dsh-tool-workflow/README.zh.md`「概述」「调用工具」*

### 5.4 引擎配置与隔离（`dsh-workflow-ptc`）

| 字段 | 默认值 | 含义 |
|---|---|---|
| `provider` | `spawn` | `agent()` 调用使用的宿主侧 subagent 提供方 |
| `maxConcurrentAgents` | `0` | 并发 `agent()` 上限；`0` 按可用 CPU 并行度解析 |
| `maxTotalAgents` | `1000` | 一次运行最多启动的 `agent()` 调用数 |
| `maxItemsPerCall` | `4096` | 一次 `parallel()`/`pipeline()` 接受的条目数 |
| `syncTimeoutMs` | `5000` | 脚本最初**同步**片段的 VM 超时 |

- **脚本钩子不能更改** `subagentProvider` 与 `maxTotalAgents` 这两项选择。
- 在**全新 Node 进程**中按**调用 Session 的文件沙箱策略**运行；**程序可见的环境为空**。
- 文件策略**不限制网络访问**。
- 工作流向 PTC 请求 `timeoutMs: null`——**没有整体经过时间截止**；只有最初的 VM 片段受 `syncTimeoutMs` 限制。
- **VM 不是安全边界**：触达 Node 的代码仍受所选 OS 文件策略约束。
- **跨 realm 错误在脚本内无法通过 `instanceof Error`**——必须根据 `name` 与 `code` 等稳定字段分支。
- Ralph 循环（Ralph = 固定全新 agent 迭代）在已发布默认组合中**保持禁用**，需显式启用。**Python PTC 组合必须禁用 `workflow-ptc`、`tool-workflow` 以及任何已启用的 `tool-ralph` 条目**——引擎在加载时**拒绝非 TypeScript 的 PTC 提供方**。
  *来源：`dsh-workflow-ptc/README.zh.md`「最小配置」「文件策略与取消」「已知限制」*

### 5.5 工具侧契约（`dsh-tool-workflow`）

- 调用参数：`meta`（必需）、`script`（必需）、`args`（可选 JSON 对象，作为全局 `args` 暴露）、`run_in_background`（可选）。
- **前台**：工具**阻塞父级轮次**直到整个工作流结算——「模型只看到最终结果，**永远不会看到中间子 agent 消息**」。成功包络 `{kind:'foreground', runId, agentsStarted, result}`，渲染为 `workflow "<name>" completed (<count> agent<optional-s>).` + `Return value:` + 美化 JSON。
- **后台**：`{kind:'background', jobId, runId}`，运行以**自有 `workflow` 任务身份注册到 `ctx.jobs`**；会话头部任务列表从该任务的**输出环**实时流式显示 `phase()`/`log()`/成员生命周期行。**没有任何工具步骤信号到达该运行**——`job_kill`、列表里的停止控件与 owner 拆除才是取消它的途径。结算即任务的结算。
- **失败文本是精确的**：`Error: workflow run was cancelled`（可追加 ` (<error>)`）、`Error: workflow run failed: <error-or-unknown error>`、防御性的 `Error: workflow run ended abnormally (<reason>)`、`Error: workflow tool requires a calling agent (exec.agent was undefined)`。
- **`execute` 在 `try/finally` 内等待 `run.result`**，该结构**总会对运行执行 dispose**；`exec.signal` 桥接到 `run.cancel()`，**包括启动前已经中止的情况**。
- 配置：`toolName`（默认 `workflow`）、`maxResultChars`（默认 50000）、`enableRunInBackground`（默认 true）。
- **持久会话记录**：对根 transport 执行，工具用**四个 log-only 事件**把运行投影到调用方 agent 的会话（run-start / 成员 start / 成员 end / run-end），**嵌套调用不写任何记录**；**会话日志刻意只记录 run 与成员生命周期**，`phase`/`log` 行只经任务观察 record 抵达人类。
- **使用指导以提示词段的形式随工具插件交付，绝不放入部署 persona**：
  > `Use the <toolName> tool ONLY when the user explicitly asks for a workflow or for large multi-agent orchestration... For one or two delegations, prefer plain subagent calls.`
  *来源：`dsh-tool-workflow/README.zh.md`「调用工具」「运行期间的预期」「后台运行」「配置」「运行生命周期」「持久会话记录」「设计理念」「系统提示词」*

### 5.6 已知限制

- **仅支持前台收集**（后台启动/轮询、spill 句柄与分离收集暂缓）——注意 `dsh-tool-workflow` 已补上后台，但 `dsh-workflow` seam 本身仍只有前台。
- **没有日志化或恢复**——脚本、子 agent 进度与中间值均不设检查点，进程重启后无法继续。
- **没有已保存或嵌套工作流**——工作流脚本**不会收到用于递归编排的 `workflow()` 钩子**。
- **没有 token 预算词汇**——引擎限制并发、条目与子 agent，但请求与结果都**不统计跨子 agent 的模型 token**。
- **运行由持有方负责，不由服务跟踪**——卸载引擎**不会**发现独立的活动句柄；每个消费方都必须 dispose 自己启动的运行。
- **后台运行不向模型报告中间值**——结算前的 `job_output` 只返回状态。
- **每次工具注册的工作流策略固定**——提供方选择、上限与工具名称属于**部署配置，不是模型调用参数**。
  *来源：`dsh-workflow/README.zh.md`「已知限制」；`dsh-tool-workflow/README.zh.md`「已知限制」*

---

## 6. 目标系统（Goal）

> 主来源：`dsh-goal/README.zh.md`、`dsh-goal-round-driver/README.zh.md`、`dsh-tool-goal/README.zh.md`、`dsh-command-goal/README.zh.md`。

### 6.1 职责切分（三个包，一个领域）

| 包 | 角色 |
|---|---|
| `dsh-goal` | **只存状态**。`ctx.goals` 服务、事件溯源、严格回放。**不调度工作** |
| `dsh-tool-goal` | **模型侧一半**：`get_goal` / `create_goal` / `update_goal` 与执行时权限检查 |
| `dsh-command-goal` | **人类侧一半**：`/goal`、`/goal edit`、`/goal pause`、`/goal resume`、`/goal clear` |
| `dsh-goal-round-driver` | **调度**：无人干预时的自动续跑 |

「只挂载 `dsh-goal` 只会存储和提供 goal，**不会启动任何工作**。」
*来源：`dsh-goal/README.zh.md`「何时使用」*

### 6.2 状态模型

- **四种持久 phase**：`active` / `paused` / `blocked` / `complete`；**外加一个进程本地标志**表示自动续行是否已启用（`armed` / `disarmed`）。
- 动词：`create`、`edit`、`pause`、`resume`、`complete`、`block`、`clear`。
- **`pause`、`complete`、`block`、`clear` 都停用续行。**
- **`block` 是唯一保留策略自有 lower-kebab-case 代码与自由文本说明的 phase**，因此「提供方限制、预算耗尽、执行错误、请求人工输入」**共用一种持久 phase**，而不是扩增生命周期状态。
- `resume` 只在 Round 上限**仍有剩余容量**时接受已停止的 goal，或 active 但已停用续行的 goal，并**清除任何先前的 blocker reason**。
- **`goal/change` 是唯一存储**：每项被接受的变更都追加持久的 `goal/change` 事件（版本 1），携带**变更后的完整快照**；clear 写入带 revision 的 **tombstone**。**会话日志是唯一的持久权威。**
- **比较并设置的变更（compare-and-set）**：变更携带 `GoalRef { id, revision }` 并**拒绝陈旧引用**——持有旧状态的消费方会收到清晰的陈旧 revision 错误，而不是静默覆盖更新的状态。
- `ctx.goals.get(agent)` 返回**脱离内部状态**的 `GoalView`：`objective`、`phase`、`roundsStarted`、`maxGoalRounds`、`blockedReason?`、`activation`（`'armed' | 'disarmed'`，**不持久化**）。
- **严格回放**：折叠只从 `goal/change` 派生生命周期变更，并拒绝形状错误、**不连续 revision**、**非法 phase 转换**、每目标时间戳非单调、以及**不连续的已准入 Round**。挂钟时间倒退时，变更时间戳被限制在不早于上一次更新的值。
  *来源：`dsh-goal/README.zh.md`「驱动生命周期」「观察 goal」「设计」*

### 6.3 自动续跑（goal-round-driver）

- **触发条件（全部满足）**：对应的活跃 agent **处于 idle** + 存在 `active` 且**已启用续行**且**仍有容量的** goal。
- **每轮做什么**：排入一条 **goal-round 提示词**——点明以 JSON 引用的目标、Round 编号与上限，并告诉模型以当前工作区、工具结果和持久状态为准。被接纳的 Round **开启独立请求序列**，因此 Chat 会在 goal 消息之前渲染其自包含请求 header。
- **提示词形态**（保留的 `<goal_round>` 文本块）：前几行为 JSON 引用的目标与 `round/maxGoalRounds`，其后是工作指令。不变式伴生会从持久前缀重建 goal，并拒绝内容与该提示词**不完全一致**的任何 goal 来源消息。
- **先预留，后准入**：idle 时驱动器为当前 `{goalId, revision}` 预留 `roundsStarted + 1`；**只有进入步骤的 `user/message` 才会增加 `roundsStarted`**。因陈旧而被拒绝的预留**不会消耗 Round 编号**。
- **竞态防护**：`agent/pre-step` 监听器在**下游监听器前后**验证完整的已领取记录与当前 goal，因此陈旧、已取消或竞争中的提示词**会在其步骤进入前被拒绝**。**在预留前到达的人类工作会让自动工作让行**，直到 agent 重新进入 idle。
- **持久性检查点**：`goal/changed` 会产生持久性义务——排队工作前驱动器**等待 `ctx.sessions.flush()`**，并在等待后**重新检查 goal revision 与竞争输入**。通过 `agent/error` 到达的 flush 失败会**停用续行**。
- **停止条件**：完成/暂停/阻塞会阻止续行；**宿主**发起的暂停**还会中止正在运行的轮次**，而模型在自己轮次内发起的暂停会正常结束。编辑只通过修订栅栏使进行中的 Round 失效，驱动器会继续新修订。驱动器自行停止的情况：轮次因 **max tokens** 结束、**持久性写入失败**、agent 被取消、插件卸载、**Round 上限耗尽**（以稳定代码 `round-limit` 记录 blocker）。
- **取消绝不自动重启 Round**：Round 已在进行或已排入队列的 goal **会在下一次 idle 时被暂停**；与 goal 尝试无关的取消**只会停用续行**。
- **挂载驱动器到现有 agent 上绝不会启用任何 goal 的续行**；会话 resume 或 fork 后 active goal 保持 disarmed，直到用户明确 `resume`——「**驱动器绝不会自行复活工作**」。
- **fail-closed teardown**：关闭准入 → 停用所有活跃 goal 的续行 → 以 `parent` 原因取消进行中的工作 → 在事件防护仍生效的情况下等待驱动器和 agent 完全停稳。
- **Round 上限默认 256**（`defaultMaxGoalRounds`），由 `dsh-goal` 配置。
  *来源：`dsh-goal-round-driver/README.zh.md`「每轮做什么」「何时停止续行」「resume、fork 或卸载之后」「设计」；`dsh-goal/README.zh.md`「配置服务」*

### 6.4 权限模型（goal 工具，很精巧）

- 工具**只为活跃驱动器内、处于开放轮次中的精确活跃调用 agent** 执行。
- `create`、`edit`、`pause`、`resume` **还要求运行时根 agent 的当前轮次中存在人类直接消息**——**subagent 或非人类生产方不能创建或编辑 goal**。
- `resume` 会在 goal 服务执行前**拒绝持久的 paused goal**（`GOAL_TOOL_RESUME_PAUSED`）；该状态**只属于面向用户的恢复路径**（`/goal resume` 或 Web 控件）。
- `complete` 和 `blocked` **还接受完全一致的当前 Goal Round**：来源为 goal 的 Round 可以**立即**完成 goal，但 `blocked` 调用在达到配置的连续 Round 数量之前会**被机械拒绝**——模型判断同一条件是否确实持续，并**必须**在 `blocked_reason` 中说明。
- **`blockedAfterConsecutiveRounds` 默认 3**，既提供模型自行报告阻塞的**硬下限**，也决定模型指引中指明的数值。
- **人类输入的宿主证明**：`Agent.followup()` 与 `steer()` 会在调用方省略 source 时分配 `{kind:'user'}`，因此**插件、调度器与其他非人类生产方必须传入自己的 source，不能继承人类权限**。（这是一个很干净的防伪造设计。）
- 成功的自主 `complete`/`blocked` 会延后一条 `<goal_complete>` / `<goal_blocked>` 结束指令，并**在该步骤后结束物理轮次**，让模型在结束前向用户交代一次；**人类直接变更绝不会触发这种停止**。
- **没有独立评估器**——记录完成或阻塞的调用方拥有最终决定权；由评估器支持的认证**暂缓**。
- **只有 Round 数量预算**——`maxGoalRounds` **不**计量 token、货币、挂钟时间或提供方配额。
- **只有一个当前 goal**——系统有意不支持并行目标或独立 goal 数据库。
  *来源：`dsh-tool-goal/README.zh.md`「权限规则」「设计」「已知限制」；`dsh-goal/README.zh.md`「已知限制」*

---

## 7. 上下文与成本

这一层由四个**互相独立**的机制组成，职责边界非常清楚：

| 机制 | 包 | 触发点 | 手段 |
|---|---|---|---|
| **计量** | `dsh-token-meter` | 每次 `measure()` | 回放持久日志做确定性估算 |
| **压缩** | `dsh-compaction-basic` | 压力 > 阈值 / 溢出 / `/compact` | 用一次额外模型请求把旧历史换成摘要 |
| **输出保留（内存内）** | `dsh-output-retention` | 工具执行中 | `ItemRetainer` / `TextRetainer` 保留 head/tail 窗口 |
| **溢出到磁盘（spill）** | `dsh-spill-policy` | `tools/post-execute` 之后 | 超大结果落盘 + 内联首尾预览 + 读取提示 |

### 7.1 Token 计量（`dsh-token-meter`）

- `ctx.tokenMeter.measure(session, requestHeader?)` 返回**独立、深度不可变**的快照：`totalTokens`（请求与响应压力）、`surfaceTokens`（仅表面的路由定价总量，= Σ `nodes[].tokens`）、`nodes`。
- `estimateMessage(message)` 为单条消息计价。**每次调用都会克隆带位置的表面节点，因此测量是 O(surface)。**
- **测量回放持久会话日志，因此确定、无需模型调用**——压缩规划、占用 UI 与压力检查读取**同一个 fold**。
- **核心设计：一个 fold 与一个锚点。** 每个会话有隔离的回放状态（已消费事件游标、规范请求标头、已计价表面、步骤边界、**测量锚点**）。**只有当最新成功调用的规范请求 envelope 与已测量 envelope 匹配、且其总量不低于该调用完整路由定价锚点时，才复用提供方用量**；否则对完整 envelope 与表面做估算。
- **测量锚点包含成功的 `assistant/message` 之前的已计价表面**，包括 `step/start` 之后接纳的系统与用户消息，以及重试之前执行的替换。持久输出未变时，完成调用的**表面增量为零**（其提示词已包含在提供方用量中）；后续表面变更仍是**相对于该锚点的带符号增量**（含缩减替换后的**负 delta**）。
- **固定启发式规则：每 token 四字符加块与角色开销**。文档坦承：**CJK 文本与 JSON schema 在每 token 四字符下严重低估**；「请把构成行呈现为近似构成，**绝不呈现为总量**」。
- 三个投影单元：

| 投影 | 内容 |
|---|---|
| `tokenUsage` | 完整持久日志的 `uncachedInputTokens`、`outputTokens`、`cacheReadTokens`、`cacheWriteTokens`。**最终 assistant 消息样本会替换同一次尝试的流式用量**；`llm/retry-started` 结束该替换范围，因此同一步骤中的重试贡献**另一次**计费用量 |
| `contextPressure` | `pressureTokens`（提供方报告的最新提示词规模）、`projectedTokens`（下一个请求的提示词将花费多少）、`contextWindow` |
| `contextBreakdown` | 启发式 `systemTokens` / `toolsTokens` / `messageTokens`——**上下文的构成，而非提供方计费规模** |

- **`contextBreakdown` 把 surface 顺序中最后一个非空且存活的 `system/message` 归入 `systemTokens`**；休眠的空节点不贡献 token；`messageTokens` 包含其余所有可见节点（**包括被取代的提示词**）。**两者之和始终等于 `measure().nodes[].heuristicTokens`。**
- **模块没有配置，也不添加模型可见表面**；容量属于适配器，经 `ctx.llm.resolveModelInfo().context` 获取。
- **占用是参考数字，不是计费记录**：**harness 中没有任何机制依据它做决定，压缩读取的是 `measure()`。**
  *来源：`dsh-token-meter/README.zh.md`「测量压力」「会话投影」「解读数字」「设计理念」「Fold 流程」「已知限制」*

### 7.2 压缩（`dsh-compaction-basic`）

**触发阈值**（设上下文窗口 `W`、生效请求输出上限 `O`、余量 `B`）：

```
threshold = floor( min(W × thresholdRatio, W − O − headroomTokens) )
retain    = (W − O) × retainRatio         // 逐字保留的近期历史预算
默认：thresholdRatio = 0.8, headroomTokens = 65536, retainRatio = 0.16
```

- 四种行为：① 压力上升时自动压缩；② 提供方确认 `CONTEXT_WINDOW_EXCEEDED` 后**先压缩再重试**；③ `/compact` 按需压缩；④ 挂载修剪器时压缩前修剪超大工具输出。
- **四项承诺**：
  1. **一个测量服务为每个决策定价**——压力、近期尾部保留、范围选择与缩减验证**使用同一套路由定价的节点数值**。
  2. **日志记录的标记对就是事务**：验证范围与活动锁 → 同步追加 `compaction/start` → 准备并等待摘要 → 重新验证 → 追加 `compaction/summary` 与替换 → **恰好进行一次** `compaction/end` 尝试。**活动的未匹配 start 是持久锁**：位于较新 `session/end-seed` 之前的未匹配标记是陈旧证据，不阻塞；之后则报告 `busy`。**闭合失败会有意留下阻塞性的未匹配标记。**
  3. **摘要复用提供方的热前缀**——逐字回放 surface 节点 0 处的系统提示词、上次已路由请求的工具与已遮蔽区域消息，使辅助调用成为会话的**真正前缀**，因此只有尾随指令与摘要输出未缓存。
  4. **`summarize()` 是唯一的子类钩子。**
- **范围选择**：每个选定范围都**从第一个不是 `system/message` 的 surface 节点开始**，因此**位于 surface 节点 0 的系统提示词永不会被遮蔽**；由历史内提示词更新追加的后续 `system/message` 是普通历史，范围**可以**遮蔽它。
- **`/compact` 期间的并发**：agent 正在轮次中或压缩已在运行时报告不可用；**运行期间发送的提示词会被接受，并在压缩结束后才开始**。
- **溢出恢复**：`agent/request-error` listener 响应 `CONTEXT_WINDOW_EXCEEDED`，**绕过常规阈值与保留策略**，尝试一次最大平衡头部缩减，并且**只在表层替换 generation 前进后才授权重试**。**取消全程保持最终决定权。**
- **摘要调用**：直接 `ctx.llm.stream()`（**不运行** `agent/request` 扩展点），`GenerateOptions.purpose = 'compaction'`；**只有返回文本进入检查点，推理与工具调用都被排除**；图片输出以 `UNSUPPORTED_CONTENT` 失败；替换 user 消息用 `<compacted-summary>` 标签框定摘要，**原始摘要保留在 `compaction/summary` 事件上**。
- **拒绝不缩小源内容的摘要**。
- **不可缩减的**：系统提示词、工具、会话前缀、单个不可分单元（例如一次超大工具调用）；只支持 envelope 溢出。
- 配置错误**快速失败**：未知设置、重复的按模型覆盖、无效 token 数、两种保留形式同时出现、保留比例不小于阈值比例。
  *来源：`dsh-compaction-basic/README.zh.md`「概述」「调整压缩开始的时机」「设计理念」「自动触发与溢出恢复」「摘要机制」「区域事务」「已知限制」*

**检查点前导（模型看到的确切文本）**：

```markdown
This is an automatically generated checkpoint condensing an earlier span of the conversation to free up context. Treat the captured context as established background and build on it without restating it. Continue the task directly from the messages that follow, without acknowledging this checkpoint.
```

**压缩指令是 8 段式固定结构**：`Primary Request and Intent` / `Key Technical Concepts` / `Files and Code` / `Errors and Fixes` / `Pending Jobs` / `Current Work` / `Next Step` / `Critical Context`，配以规则：terse bullets、空段写 `(none)` 而**绝不删段**、preserve exact file paths / commands / error strings / identifiers、**不要提及这次摘要请求**、**如果已有 `<compacted-summary>` 块，不要逐字复制**（保留仍为真的事实、丢弃过期的、合并新信息）。
*来源：`dsh-compaction-basic/README.zh.md`「辅助摘要器请求」*

### 7.3 输出保留（`dsh-output-retention`，内存内、库形式）

- `ItemRetainer` 限制**有序逻辑单元**，只保留前 `maxItems` 个；**调用方持续送入每个已观察单元，因此省略计数是精确的**：
  ```ts
  const retainer = new ItemRetainer<{path:string}>({ kind:'head', maxItems: globMaxResults })
  for await (const entry of candidates) retainer.push(entry)   // keep draining past the cap for an exact count
  const { items, truncated, omitted } = retainer.finish()
  ```
- `TextRetainer` 用同一个前缀/后缀累加器限制**字节**：`head` / `tail` / `headTail`。**累加器在内存中至多持有 `headBytes + tailBytes + 一个分片`**，因此大流不会无界累积。
- **`kind` 按字节而非字符或行计数**——「子进程管道与 HTTP 正文都是字节流」；`finish()` **会在每个切割处修剪不完整的码点**，因此返回的文本绝不会携带由切割引入的替换字符。
- `formatRetentionNotice(...)` 标准化省略子句（`Omitted 3 items.`）并与**工具自有的恢复指引**拼接——「只有工具知道恢复动作」。
- **`truncated` 是预算事实**：retainer 因上限而省略了本可获得的内容。它**绝不**表示上游不完整——权限失败、跳过二进制文件、提供方部分失败与不可读候选项都留在**工具领域字段**中，绝不并入 `truncated`。
- **限制**：项保留只支持 `head`；tail / head-tail / 分页 / 分组 / 提供方完整性语义仍归工具所有。`read` **不属于**本库（`offset`/`limit` 行窗口是文件专属渲染器，单个 `Omitted` 值无法表示该窗口两侧）。
- 各工具的用法：`glob`/`grep`/`web_search` → `ItemRetainer(head)`；`bash` → `TextRetainer(tail|headTail)`；`web_fetch` → `TextRetainer(head|headTail)`。
  *来源：`dsh-output-retention/README.zh.md`*

### 7.4 Spill（溢出到磁盘，插件形式）

- 文字和图片**共享**配置的**估算 token 预算**；`maxInlineTokens` **默认省略（即禁用策略）**，随附推荐值示例为 12500。
- **模型看到的形态**：保持原始顺序，扣除省略提示后**两端各使用剩余预算的一半**；**文字可以切分，图片整张保留或省略**；省略区间中的图片也会被省略。
  ```
  <retained head/tail preview>

  (Omitted N bytes. Full formatted result stored at: /…/session-…/…-web_fetch.txt. Use read with offset/limit, or grep this path to search within it.)
  ```
- **完整结果文件保存全部已接受文字，并在每张图片的位置记录附件路径**；模型可先 `read` 再 `read_image`。**图片字节不复制到这个文件中。**
- 两条分支：以 **prepend** 注册的 `tools/post-execute` 监听器**先委托、再限制已接受的内容**；`tools/ptc-dispatch-log` 共用同一辅助函数。
- **有意原样通过的**：预算内结果、`read`、被阻止的决策、值替换、其他内容块类型。
- **尽力而为的故障行为**：缺少归属或 spill 后端、存储失败、缺少模型图片计量、图片路径无法读取时，**记录警告并保留原内容**；**策略不会用无法读取的路径替换图片**。
- **通知无法容纳时会禁用该次调用的替换**——这也是一个必须承认的边界（后端已经保存了无引用的 spill，但过大的原始结果仍留在内联位置）。
- 图片按当前模型的 `imageRequestPricing` 计价，并**计入说明文字**。
  *来源：`dsh-spill-policy/README.zh.md`*

### 7.5 KV cache 决策规则（汇总）

这是 DSH 文档里**逐包重复声明**的一组规则，值得单独抽出：

| 操作 | 对前缀缓存的影响 |
|---|---|
| **追加**历史 / steering / 注入 / 工具结果 / hook 上下文 / 委派通知 / goal Round / 策略快照 | **保留**可复用前缀（新内容位于前缀之后） |
| **追加式提示词更新**（路由声明 `systemPromptUpdate: 'in-history'` 时） | **保留**直到已缓存历史末尾的前缀 |
| **原地替换某个 system 节点** | **使复用从该节点第一个 token 起失效**；该节点是**第 0 号节点时整个请求都不同** |
| **surface `replace` 操作 / 压缩** | **使复用从第一个被遮蔽的历史 token 起失效**（即使底层事件日志保持仅追加） |
| **更改保留工具的定义或重建声明** | 从**第一个改变的请求 token**起失效 |
| **注册 / dispose / 作用域限制 / 重排序工具** | 从**第一个改变的 schema token** 起失效 |
| **PTC 模式或筛选器变更** | 从第一个改变的提示词或 schema token 起失效 |
| **重连后恢复未变的 MCP 工具列表** | 生成**完全相同**的定义，前缀保持稳定 |
| **`ask`/`never` 策略切换** | 追加一份新的完整快照到保留历史之后，**保留**稳定的系统与对话前缀 |

- 前缀复用要求**在同一提供方与模型路由下，此前消息和声明保持逐字节一致**。
- **「模型、persona 前缀、工具与前置指令一致时，不同源码路径、本地 Web URL 或 persona 后缀值不会改变可复用的第一方前缀。」**
- **不保证提供方共享缓存或实际命中率。**
  *来源：`dsh-agent-loop/README.zh.md`「KV Cache 影响」各节；`dsh-session/README.zh.md`「模型体验」；`dsh-system-prompt/README.zh.md`「KV Cache 影响」；`dsh-compaction-basic/README.zh.md`「KV Cache 影响」；`dsh-user-approval/README.zh.md`「KV Cache 影响」；`dsh-mcp-client/README.zh.md`「KV Cache 影响」*

---

## 8. 权限与沙箱

> 主来源：`dsh-permission-presets/README.zh.md`、`dsh-user-approval/README.zh.md`、`dsh-sandbox/README.zh.md`、`dsh-sandbox-policy/README.zh.md`。

### 8.1 三旋钮、三层职责

```
dsh-permission-presets   拥有「选择与默认值」  →  把 (sandbox, approval) 捆绑成具名预设
        │  setSandboxMode            │ setApprovalPolicy
        ▼                            ▼
dsh-sandbox-policy            dsh-user-approval
（执行行为：模式 + 工作区根）      （执行行为：策略 + 审计）
        ▼
dsh-sandbox                 ← 隔离 seam 本体（能力词汇）
        ▼
sandbox-local / bash-sandbox / fs-sandbox / pwsh-sandbox ...
```

**关键不变量：沙箱、审批和 Auto integration 各自拥有执行行为；本包只拥有选择与默认值。** 因此「**以后移除本包，最后一次取值依然生效**」。
*来源：`dsh-permission-presets/README.zh.md`「概述」*

### 8.2 权限预设如何分级

```yaml
- name: '@deepseek-ai/dsh-permission-presets'
  config:
    presets:
      workspace-write:   { sandbox: workspace-write,   approval: ask }
      danger-full-access: { sandbox: danger-full-access, approval: never }
    defaultPreset: workspace-write
```

- `presets` 默认已含 `workspace-write` 与 `danger-full-access` 两项。
- **保留名称 `custom` 与 `auto` 不能出现在配置表中**：
  - `custom` = **推导得出的非预设状态**（不匹配任何预设的旋钮组合）。用户可以**离开**该状态，但**不能选择**它，也**不能通过此服务选中或持久化一个具名 custom 预设**。
  - `auto` = 保留给显式加载的 **Auto review integration**，**只在 integration effect 存活期间存在**，**有意不进入 `permission` 设置 schema**，**不能成为默认值**。
- **切换是「只改实际值不同的旋钮」**：`set()` 解析预设，并在适用时同步执行 Auto 准入检查；**切换仅在有效预设变化时**追加 `permission/preset`，再通过各自的权威 setter（`setSandboxMode` / `setApprovalPolicy`）写入每个变化的旋钮。**净变化为零的选择不追加任何内容。**
  - 这样设计的结果：**两个预设共享同一组取值时，选择事件仍会保留用户意图**——Auto 与 Full access 之间切换时，沙箱与审批值已经相同，**只记录新的身份**。
- **当前值解析顺序**：仍匹配的最近一次记录选择 → 配置表中的第一个匹配项 → `custom`。
- `permission` 设置命名空间为**未来会话**持有 `defaultPreset`；**创建会话时读取它**；**之后的设置变更绝不会改变现有会话**。恢复的 seed（含 `session/end-seed` 明确标记的空 seed）**保留其有效权限**，只接收缺失的持久事实，而**不会**接收最新用户默认值。
  *来源：`dsh-permission-presets/README.zh.md`「配置预设」「切换预设」「读取侧与 custom」「会话默认值」「写入路径」「已知限制」*

### 8.3 沙箱种类

**三种模式**（`SandboxMode`）：

| 模式 | 效果 |
|---|---|
| `read-only` | 拒绝写入，必需 sink（如 `/dev/null`）除外。**这是故障安全默认值** |
| `workspace-write` | 允许写入工作区根目录**及后端定义的临时区域** |
| `danger-full-access` | 绕过隔离；消费方直接 spawn 原始 argv |

**强制执行完整度（逐调用报告）**：`full` = 后端管辖模式承诺的每个文件操作；`partial` = 活动后端或较旧的内核 ABI 只管辖子集。**Windows ACL 档与较旧的 Landlock ABI 是当前的部分强制执行情形**；需要绝对边界的消费方**可以拒绝或向上暴露**它们。

**平台后端**：Linux → bubblewrap 或 Landlock；macOS → Seatbelt（`sandbox-exec`）；Windows → **ACL 受限令牌 runner**（`dsh-sandbox-windows-acl`）。`workspace-write` 在 Windows 上把写入限制在工作区与会话自己的 temp 子目录（`<temp>\dsh-<hash>`，受限子进程的 TMP/TEMP 被改写）；`read-only` 不授予任何临时目录写入权限。

**故障关闭**：没有后端能强制执行所请求的模式时，调用以 `SANDBOX_UNAVAILABLE` 失败，**而不是不受限制地运行**；错误文本指明缺失的平台 runner。启动后失败的后端还会报告结构化的 runner 失败签名，因此**损坏的沙箱可以与命令失败区分开**。

**策略解析优先级**：`resolve({session, mode})` = 已批准的显式模式 → 会话最后一条 `sandbox/mode` 事件 → 部署默认值。会话的**不可变 `cwd`** 提供工作区根目录；否则用配置的 `workspaceRoot`（默认 `process.cwd()`，相对值在加载时拒绝）。

**逐会话存储**：运行时切换是会话日志中一条**仅写入日志**的 `sandbox/mode` 事件——
`effective = explicit grant ?? fold(events) ?? deployment default`——**切换本身就是事件，任何机制都不会在带外修改模式状态**。

**关键限制**：
- **文件操作是完整的策略词汇**——该 seam **不表达**网络、进程、系统调用、设备或凭据限制。
- **每个会话只有一个主要工作区根目录**；额外可写根目录不属于 `SandboxExecutionPolicy`。
- **只支持与宿主共享文件系统和内核的限制**——容器、microVM 与远程执行需要**替换整个能力实现**（`ctx.shell`/`ctx.fs`），而不是在此添加提供方。
- **每个上下文只有一个提供方**；调用方**逐调用选择策略，而非后端标识**（`SandboxPolicy` 逐调用携带，**绝不在提供方上固定**）。
  *来源：`dsh-sandbox/README.zh.md`「模式与强制执行」「故障关闭行为」「设计理念」「已知限制」；`dsh-sandbox-policy/README.zh.md`「最小配置」「解析优先级」「逐会话存储」；`dsh-base/README.zh.md`「平台门控」「已知限制」*

### 8.4 用户批准流程

- **与通道无关的一次性审批 seam**：`ctx.approval.request(req)` 指名 agent、工具、原因、可选调用 id 与中止信号。
- **策略**：`ask`（默认）委托给已组合的应答者；`never` **在交互式分发之前确定性地拒绝每个请求**（CI 与无人值守运行的严格无头模式）。
- **应答者是 `approval/request` waterfall 监听器**：返回结果 = 为所负责 agent 作答；否则 `next()` 委托。**限定到 agent 的监听器只接收该 agent 的请求。**
- **`never` 策略在服务内部、waterfall 分发之前执行，因此之后以 `prepend` 注册的监听器也无法绕过确定性的拒绝。**
- **失败关闭**：**应答者缺失或失败时返回 `unavailable`**，使操作以拒绝方式关闭；抛出异常的监听器会使问题以 `unavailable` 关闭；不属于结果词汇的异常返回值也被规范化为 `unavailable`。**服务自身绝不会提示人类。**
- **请求必须处于尚未结束的轮次中**：空闲或在轮次之间调用会**在审计前抛出异常**——「因为轮次是持久日志的提交/回放边界，轮次之间的裸事件与崩溃尾部无法区分」。
- **审计**：`request()` 先追加 `approval/asked`（携带请求身份与工具），再追加 `approval/decided`（携带最终结果）；**两者都只写入日志**；不变式在**同一个未结束轮次内按 id 校验这一事件对**，并校验结果属于封闭词汇。
- **中止会撤回问题**——请求以 `cancelled` 结算，**迟到的回答被丢弃**。**若任一审计事件在提交前失败，请求会被拒绝，而不会返回一项未记录的决定。**
- **模型只看到**发起请求的消费方最终给出的工具结果（允许/拒绝/取消/不可用）**与运行时上下文快照中的当前策略**；**审计事件与面向人类的权限 UI 不属于模型上下文**。
- **每项批准只适用于对应请求**——结果词汇含 `allowed-once`，**不含** `allow-always`、已记住的规则、撤销或授权存储。
- **请求不携带工具参数**——应答者只看到工具名称、原因和可选调用 id。
- 策略切换（`setPolicy`）会**为下一个模型步骤排队一条「由用户更改」消息**；`approval:policy` 提示词贡献**在保留历史之后陈述有效策略的完整当前含义**——「切换策略会追加一份新的完整快照，而不会改写稳定的请求头」。
  *来源：`dsh-user-approval/README.zh.md`*

### 8.5 升权（escalation）

- 调用被拒绝后，**模型可以请求一个严格更宽的模式**，交由人类批准**一次**。
- **阶梯是封闭表**：`read-only` → `workspace-write` 或 `danger-full-access`；`workspace-write` **只能** → `danger-full-access`。
- **在执行时检查，绝不写入工具 schema**——「schema 的枚举保持封闭的目标词汇」。
- 拒绝标记：`[sandbox: file access denied under <mode> mode]`；升权提示：`[sandbox: escalation available — retry this exact <subject> once with sandbox_permissions (the narrowest wider mode that suffices) + justification; the approval prompt asks the user]`。
- 模型用 `sandbox_permissions`（**足以放行的最窄更宽模式**）+ `justification` **重试一次完全相同的调用**；用户看到一次审批提示，可选择允许一次/拒绝/取消。**更宽的模式需要审批，且只作用于该次调用。** **重复指定调用的生效模式无需审批即可成功；更窄的目标仍然无效。**
- `run_code` 的 `sandbox_permissions` 要求**非空 `justification`**，并在**程序启动前**获得审批；授权**仅用于该次完整执行**，常驻 Session 策略与嵌套工具保留各自权限；**程序不会自动重放**。
- PTC/工作流引擎**为 PTC 执行解析调用 Session 的常设文件策略与 cwd**。
  *来源：`dsh-sandbox/README.zh.md`「被拒绝的调用与升权」「升权编排」「模型体验」；`dsh-tools/README.zh.md`「PTC mode」*

---

## 9. 插件 / 组合机制

> 主来源：`cordis/README.md`、`dsh-base/README.zh.md`、`dsh-agent-preset/README.zh.md`、`dsh-plugin-manager/README.zh.md`。

### 9.1 `cordis` 是什么

**Cordis 是一个 TypeScript 插件框架**（Meta-Framework for Modern JavaScript Applications），为需要**显式依赖注入、作用域服务、生命周期管理的清理、以及可选的配置驱动加载**的应用而设计。它是**第三方库**（作者 Shigma，MIT，仓库 `vendor/cordis`），DSH 把它 vendor 进来作为整个 harness 的容器。

四个原语：

```ts
import { Context, Service } from 'cordis'

const root = new Context()          // ① 根依赖容器
await root.plugin(Counter)          // ② 启动插件，返回 Fiber
await root.plugin(greeter)          //    greeter.inject = ['counter']  → 声明依赖
root.emit('app/ready', 'started')   // ③ 事件
await root.fiber.dispose()          // ④ 效果/监听器/服务随所属 fiber 一起被移除
```

- **`new Context()`** 创建根依赖容器。
- **`ctx.plugin()`** 启动插件并返回 **`Fiber`**。
- **`inject`** 告诉 Cordis 哪些服务必须**先存在**，插件才能运行。
- **Effects、事件监听器与服务在拥有它们的 fiber 被 dispose 时一起移除。**
- **真正的插件三原则**（DSH 在 `plugins/abi.py` 层面的对照，见第 11 节）：不 import 主程序、只通过 `ctx` 访问能力、声明式 manifest + 显式 hooks。

**`Volatile<T>`**：schema 可以返回 `Volatile<T>` 引用，其值通过 `.get()` 读取。**Loader 会把仅 volatile 的变更提交进运行中的引用而无需重启**，并通过 `loader/volatile-update` 通知所属 fiber。这解释了 `dsh-agent-loop` 里 `maxParallelToolCalls` 为什么能热改而不是重启。
*来源：`cordis/README.md`；`dsh-agent-loop/README.zh.md`「配置声明式 agent」*

### 9.2 包如何声明挂载

DSH 的组合是**一份静态 YAML patch 文档**，不是代码：

```json
// profile 的 package.json
{ "name": "my-profile", "private": true,
  "dsh": { "profile": { "bundles": ["@deepseek-ai/dsh-base"] } } }
```

```yaml
# cordis.patch.yml —— 一个应用到空 profile 根之上的 insert 列表
- id: sandbox-policy
  name: '@deepseek-ai/dsh-sandbox-policy'
  config:
    mode: workspace-write
    workspaceRoot: !!js process.cwd()
- insert:
    - id: tool-str-replace-editor
      name: '@deepseek-ai/dsh-tool-str-replace-editor'
      config:
        maxOutputChars: 16000
```

- **`dsh-base` 本身不挂载任何服务、不发出任何事件、也不持有任何可变状态**——它**只是一份静态 patch 文档**（一个 `insert` 列表）；**每条插入行所属的包负责该行的行为与不变式**。
- **patch 会替换目标行的整个 `config`，而不是合并进它。** 后续组合包层与用户的 profile `cordis.patch.yml` **按 id 覆盖行，每行最后一次写入生效**。因此「**每个 patch 条目会替换目标的整个配置，因此请重述每个想保留的设置**」——这是最容易踩的坑。
- **平台门控写在 patch 自身上**：
  ```yaml
  - id: bash-sandbox
    disabled: !!js process.platform === 'win32'
  ```
  每个平台**恰好有一套 shell 栈**；权限面与 POSIX 完全一致。
- **保持沙箱化文件系统提供方作为唯一的文件写入路径**：在其之上再添加普通文件系统提供方会导致 **profile 加载失败**（两者注册同一个服务，profile 因此拒绝加载；二选一）。
- 随附 profile：`web`、`headless`、`sdk`、`acp`（首次使用时自动创建）；`sdk-minimal` **刻意改用完整的独立配置树**。
- **`dsh-base` 的默认权限姿态**：把文件写入限制在工作区内，**危险操作前征询许可**。
  *来源：`dsh-base/README.zh.md`「最小自定义 profile」「你得到什么」「组合机制」「平台门控」「已知限制」*

### 9.3 Agent preset（逐会话的能力集）

- **在普通 Cordis YAML 中定义 Agent 的子插件列表**；可同时声明多个 preset，**由会话选择**。
- 声明会**提前加载**，**修改只影响之后创建的 Agent**（「同一 Agent 的组合保持稳定；新 Agent 可使用更新后的定义」）。
- 字段：`id`（必填，稳定 preset 标识符）、`plugins`（必填，子插件行列表）、`name`、`description`、`order`。
- 声明行的 `id` 是 **Loader 编辑地址**；`config.id` 是**会话保存的 preset 标识符**。
- **`dsh-agent` 的作用域机制就是 preset 的实现基础**：`Agent.ctx` 是该 agent 的作用域上下文；通过它进行的注册（**工具、提示词段、变量、事件监听器、限制**）只对该 agent 生效，并在 dispose 时全部撤销。**同一机制也是 agent preset 用来让一个会话获得不同能力集、同时不影响其邻居的方式。**
- 需要 `agentPresets` 服务；**重复的 preset ID 会导致声明加载失败**。
  *来源：`dsh-agent-preset/README.zh.md`；`dsh-agent/README.zh.md`「将注册限定到单个 agent」*

### 9.4 配置如何注入

四层优先级（从低到高，后写的赢）：

```
1. 组合包（bundle）层：@deepseek-ai/dsh-base 的 cordis.patch.yml
2. 后续组合包层（按 id 覆盖行，最后写入生效）
3. home 级 patch
4. 用户 profile 的 cordis.patch.yml / 单次启动 patch（最高优先级）
```

- **`!!js` 表达式在 patch 解析时求值**（`process.platform === 'win32'`、`process.cwd()`、`` `Bearer ${process.env.MCP_TOKEN}` ``）。
  - 例外：**插件的 group 标记保留子配置中的 `!!js`，直到对应子插件加载时才求值**。
- **Volatile 字段与启动配置分开**：DSH 明确区分 volatile Config 字段（下一个工具组开始时读取）与启动配置（`agents` 数组）。
- **HMR**：在 YAML 中启用 HMR 时配置变化立即生效；未启用时运行中的组合保留到重启。**DSH HMR 串行执行模块重载、文件监听和管理写入**；每次刷新重新读取组合包选择与 patch 层、更新原有根 Include、并等待已移除插件释放资源及剩余 Loader 树稳定。
- **`plugin_manager`（应用拥有的 profile 管理器）**：启停单个插件条目、选择已安装的组合包、安装或删除外部组合包。**每个工具操作都要求 `danger-full-access` 或本次调用的批准**；**批准不改变会话的权限模式**。**profile 变更跨会话持久化；已安装的 Host 代码在宿主进程内运行，不受工作区沙箱限制**（重要：插件是宿主任意代码，沙箱管不住它）。
- **失败行为**：安装失败恢复 `package.json` 与 `pnpm-lock.yaml` 快照；卸载依次执行「从 `dsh.profile.bundles` 移除 → 卸载运行时贡献 → `pnpm remove`」，**任一步失败都不继续执行后续步骤**。
- **CLI 与 service 操作共用 profile manifest 写锁**，防止并发包操作和 manifest 写入。
  *来源：`dsh-plugin-manager/README.zh.md`「使用本包」「配置」「失败行为」；`dsh-base/README.zh.md`「更改默认值」；`dsh-agent-preset/README.zh.md`「理解实现」*

### 9.5 系统提示词组装（配置注入的另一个面）

- 贡献带作用域：**通过 `agent.ctx` 注册只影响该 agent，并遮蔽同名全局项**。
- **段（section）**携带静态或按上下文解析的文本与 `order`；**先按顺序值升序拼接，顺序值相同时再按名称的代码单元顺序排列**。
- **仓库自带贡献方通过 `ctx.systemPrompt.getSectionOrder(name)` 解析集中分配的位置**；runtime-context 贡献方使用 `getContextOrder(name)`；**外部贡献可以使用任意有限的顺序值**。
- **`complete: true` 段会在组装后成为精确的完整提示词**；有效的 complete 段**超过一个时组装失败**。
- **变量**在段文本中以 `{{name}}` 引用，**每次组装时解析**；带作用域变量为该 agent 遮蔽同名全局变量。循环提供 `model` 与 `cwd`。`interpolate: false` 原样保留文本（含 `{{…}}`）。
- **两阶段求值与渲染**：`assemble()` 返回文本已求值但**尚未插值**的段、有序工具 schema、以及每个已注册变量按当前上下文求得的值；`renderPrompt()` 做插值、**删除空段**、用空行连接。
- **严格规则**：未知引用、已注册但无值的引用、格式错误的完整组都会**抛出**——「**格式错误的提示词比明确失败更糟**」。
- **`toolOrder` 在 waterfall 分发前规范化收集到的工具**（「注册顺序只是插件加载产物」）；修改列表的 waterfall 监听器对其输出的**确定性**负责。
- `suppressRuntimeContext()` 移除调用作用域的所有动态运行时上下文贡献，但**不禁用拥有底层事实的服务**；多个抑制器独立组合，当不再存在抑制器时该 effect 恢复上下文。
- **段与动态上下文是独立的输入**：段成为提示词文本，而**上下文在循环下成为模型历史中带来源的 user 角色快照**。
- **第一方顺序**：harness 身份 `−1000`；persona 前缀 `0`；plan 策略 `500`；工具 SDK `5000`；harness 源码 `10000`；Web 表层 `10100`；persona 后缀 `10200`。
- **限制**：**不存在终端用户提示词编辑 API**，部署方编写的提示词文本只来自配置/组合。
  *来源：`dsh-system-prompt/README.zh.md`「贡献提示词段」「组装与渲染」「作用域」「模型体验」「已知限制」；`dsh-plan-mode/README.zh.md`「模型体验」；`dsh-tools/README.zh.md`「PTC mode schema 与系统提示词」*

---

## 10. 互操作：MCP 与 ACP，以及 hooks 桥接

### 10.1 两者解决完全不同的问题

| | **MCP**（Model Context Protocol） | **ACP**（Agent Client Protocol） |
|---|---|---|
| 方向 | **向内**：让 DSH 的模型使用**外部服务器**的工具与资源 | **向外**：让**受信程序**通过标准协议**驱动 DSH agent** |
| 包 | `dsh-mcp-client`（客户端） | `dsh-acp`（服务器） |
| 传输 | stdio / Streamable HTTP | JSON-RPC over stdio |
| 谁发起 | DSH 连出去 | 外部程序连进来 |
| 面向 | 模型能力扩展 | 自动化/编排（进程外 subagent、测试运行器、脚本化控制器） |
| 明确不做 | MCP 提示词模板不受支持；资源订阅不受支持 | **刻意只提供标准 ACP v1 界面**；不提供 DSH 专用呈现数据与交互式 UI 功能 |

**一句话**：MCP 扩展**工具面**，ACP 暴露**控制面**。
*来源：`dsh-mcp-client/README.zh.md`「概述」；`dsh-acp/README.zh.md`「概述」「何时选择」*

### 10.2 MCP 客户端要点

- **每台服务器配置一条记录**，`serverName` 作为 namespace（`[A-Za-z0-9_-]{1,32}`，在一个注册作用域内唯一）；**默认不启用任何服务器**。
- **工具命名 `mcp__<serverName>__<rawName>`**（如 `mcp__github__create_issue`）——**与 Claude Code 和 Codex 使用的命名形态相同**。有损规范化会追加 **12 位十六进制 SHA-256 hash**，使不同身份**绝不会折叠**。
- **三个身份不变量**：
  1. **服务器限定身份** `(serverName, rawName)`；**namespace 是本地配置，绝不采用远程 `serverInfo.name`**——「远程名称不可信、在部署间不唯一、且升级时可能变化」。
  2. **命名是固定约定**：公开名称是 `(serverName, rawName)` 的**纯函数**，因此**会话历史与权限规则能在 HMR、重新同步和其他服务器变化后保持有效**。
  3. **原始名称是唯一的协议名称**：`tools/call` **始终**收到原始名称；**公开名称绝不会发给服务器，也绝不会被解析来还原原始名称**。
- **要么完整世代，要么没有**：同步**原子地交换世代**——获取失败**保留上一世代**，注册冲突**回滚整个尝试中的世代**；「**绝不会得到该服务器的部分工具集**」。
- **两个服务器可以同时提供同名工具**（`mcp__github__search` 与 `mcp__web__search` 共存）；**两条配置项使用相同服务器名 → 后加载的一条在加载时以明确错误失败**。
- 重连：`reconnect.enabled` 默认 true，`initialDelayMs` 500 **每次连续失败翻倍**，`maxDelayMs` 30000，`maxAttempts` 10。**中断期间最后已知的工具仍会列出，但对它们的调用会失败**，直到服务器恢复；**连续失败十次后该服务器的工具被移除**，重连停止；**服务器持续连接超过 `maxDelayMs` 则重置预算**。
- **环境清洗（stdio）**：子进程环境以 `scrubbedParentEnv()` 为基座——**删除匹配 `/KEY|PASSWORD|SECRET|TOKEN/i` 的环境名与所有 `DSH_*` 名称**——再在其上合并配置的 `env`。
- `toolCallTimeoutMs` 默认 60,000；`maxInstructionBytes` 默认 32,768；`failOnStartupError` 默认 false（**初始连接失败时 harness 仍会启动**，只是该服务器的工具不出现并记录错误）。
  *来源：`dsh-mcp-client/README.zh.md`「最小配置」「工具命名与共存」「设计理念」「生命周期与同步」「环境清洗」*

### 10.3 ACP 服务器要点

- **一个连接可以同时运行多个会话，彼此独立。**
- 协议面（标准 ACP v1）：`initialize` / `authenticate`（立即成功，**服务器不需要身份验证**）/ `session/new` / `session/list` / `session/resume` / `session/close` / `session/set_config_option` / `session/prompt` / `session/cancel` + `$/cancel_request` / `session/update` / `session/request_permission`。
- **三项承诺**：
  1. **只发送标准语义更新**——原始提供方增量、重试尝试、DSH 呈现数据与不受支持内容**不会**进入协议。
  2. **诚实的能力与配置状态**——`initialize` **只公布已挂载支持**；提示词**固定其准入时的确切路由**。
  3. **停稳后才结算**——提示词与关闭操作**只在其拥有的准入、Agent 活动、有序更新、后代、持久化与释放达到所需终态后才结算**。
- **每个会话只允许一个正在处理的提示词**；准入先校验**整个提示词批次**、快照所选路由、重新检查 Agent 是否为**同一对象**与图片能力、持久化图片附件，**然后才**把用户消息入队——「**先于准入完成的取消绝不会让迟到的轮次入队**」。
- **结算优先级**：显式取消 → 已提交输出失败 → 区间内 Agent 失败 → 关联轮次结束。
- **清理用同一停稳式流程**（显式关闭、客户端断开、Cordis 释放）：停止新工作 → 取消提示词准入与 Agent 活动 → drain 已提交更新 → **按子优先顺序释放可继续后代** → flush 持久化 → 释放所拥有的 Agent 作用域。
- 不支持的界面**会被省略或拒绝**：`session/load`、删除、fork、附加目录、SSE 或 ACP 传输 MCP、mode、命令、计划、终端、客户端文件系统操作与 elicitation。
- **模型侧**：内联图片 base64 **在批量准入后即被丢弃**，持久消息只包含**经过校验的附件引用**；**协议元数据、客户端能力、权限选择与会话 id 绝不进入模型请求**。
  *来源：`dsh-acp/README.zh.md`*

### 10.4 `dsh-hooks-claude-code` / `dsh-hooks-codex` 说明了什么

**它们说明「扩展点」是真的、可移植的抽象，而不是为某个 UI 造的。** 两个桥接把**第三方编码 agent 的私有钩子方言**映射到同一个内部扩展点表面上，而核心侧只需要一套 `hook-protocol`：

| 第三方钩子事件 | 映射到的 DSH 扩展点 |
|---|---|
| `SessionStart` | 需等待的 `agent/created` 初始化（加入上下文） |
| `UserPromptSubmit` | `agent/pre-step`（可拒绝的 waterfall） |
| `PreToolUse` | `tools/pre-execute`（可拒绝；`deny` / `ask`） |
| `PostToolUse` | `tools/post-execute`（可带反馈阻塞、附加上下文） |
| `Stop` | `agent/turn-stopping`（串行监听器；阻塞结果**通过 `steer()` 强制再执行一步**） |
| `SubagentStart` / `SubagentStop` | `subagent/start` / `subagent/end`（start 注入上下文，**stop 只观测**） |

**共享引擎明确的分层（`dsh-hook-protocol`）**：

- **把唯一差异轴收拢进 `mode`**：两个方言**只在 matcher pattern 的解读方式上不同**（`claude-code` = 字面量备选或正则；`codex` = **始终未锚定正则**），因此 matcher 把 mode 作为**参数**，而不是复制引擎。
- **处理流水线是一串单一用途的步骤，每步一个函数**：校验 matcher pattern → 通过 `dsh-shell` 执行器运行命令 → 解码结果 → **把每个匹配 hook 的结果合并为最严格的一个** → 记录持久的 `hook/*` 事件对。
- **绝不向循环抛异常**：**每种失败模式**——格式错误的 JSON、无效正则、执行器拒绝——都会**降级为受控的结果或不匹配**，因此「钩子永远不能使调用轮次崩溃」。**无效正则是运行时的不匹配**；执行器拒绝变成没有退出码的 `HookOutput`；**退出码 2 以 stderr 作为原因阻塞**；其他失败均不阻塞。
- **合并规则**：应用 `deny > ask > allow` 优先级；**保持首个 `continue: false` 停止的粘性**；按 hook 顺序累积上下文。**只有 command 形态会运行**。
- **`hook/*` 是仅日志、轮次内的事件**（与 `compaction/*` 相同，**不是 surface 事件，也不携带 `surfaceOp`**）；`hook/result` 按 `handlerId` 与其 `hook/invoked` 配对。**不变式伴生插件拒绝在尚未结束的轮次之外追加的 `hook/*` 事件。**
- **执行器拥有进程控制**：命令通过 `dsh-shell` 执行器运行，**而非自建 spawn**——「执行器已经提供了协议所需的已清理但可覆盖的环境、进程组取消与超时」。
- **串行而非并发**（一个有意的取舍）：匹配 hook **按配置顺序串行运行**，这使每个 `hook/invoked`/`hook/result` 对在日志中**相邻**，且**最严格折叠与顺序无关**——「结果与参考引擎的并发启动一致，代价是串行化的延迟」。
- **脱离运行必须达到完全停稳**：三个/一个 emit 点（`SessionStart`、`SubagentStart`、`SubagentStop` / `SessionStart`）**以脱离方式运行**（没有扩展点等待它们）；每条运行链都会被跟踪，**dispose 时会中止仍在运行的 hook 进程并在 dispose 完成前排空 continuation**——「不会有 hook 进程或迟到回调超出 fiber 存活」。

**它们同时诚实地列出了缺口**（这也是一个设计信号）：Claude Code 30 项事件中只支持 7 项、Codex 10 项中只支持 5 项；`updatedInput` 会被解析但**不应用**（输入改写是**延期的一致性设计问题**）；`{"continue": false}` 会被折叠为粘性 stop 但**没有运行级效果**（拦截点缺少硬停止原语）；`transcript_path` **永远为空/`null`**，因为「持久化 seam 不暴露产物路径，且默认使用 Zstandard 压缩的会话日志无法被 hook 脚本读取」。
*来源：`dsh-hook-protocol/README.zh.md`「处理流水线」「设计理念」「已知限制」；`dsh-hooks-claude-code/README.zh.md`「Hook 点映射」「Matcher subject 与串行执行」「脱离运行与释放」；`dsh-hooks-codex/README.zh.md`「Hook 点映射」「设计理念」*

**旁证：`dsh-acp` 的 `session/request_permission`「带一次性允许/拒绝选项的权限提示；你的客户端可以自动回答」以及 `dsh-subagent` 的 ACP provider——说明权限 seam 与协议无关，可以被非人类应答者兑现。**

---

## 11. ★ 可移植到 EggBloom 的设计清单

> 本章是本文档最重要的部分。EggBloom 是 **Python 写的、事件驱动的、多人的、永久在线的 QQ 聊天机器人**（见 `docs/ARCHITECTURE.md`、`plugins/abi.py`、`plugins/plugin_manager.py`、`token_stats.py`），**不是编码 agent**。
> 判断标准：**这个模式是否依赖「单用户、单任务、有工作目录、有可撤销的文件副作用、有结束时刻」？** 依赖 → 不要搬。不依赖 → 可以搬。

### 11.1 ✅ 通用架构模式（建议搬到 EggBloom）

#### P1. 事件溯源会话日志 + 从日志派生上下文（**最高价值**）

- **DSH 做什么**：`Session` 是类型化 `SessionEvent` 的**仅追加日志**；模型历史、transcript、遥测、标题、持久化**全部从这条流派生**。铁律是「**模型可见即已记录**」。追加会**先快照并冻结载荷、校验其为无损 JSON**，再通知观察者；**被拒绝的追加不会改变日志、派生状态或事件流**。
- **为什么与编码无关**：QQ bot 的「这个人在这个群里的对话上下文」同样可以从日志纯函数派生。
- **EggBloom 现状**：`chat_logs/` + `data_store.py` 更像「边算边存」，聊天记录与「送给模型的上下文」是两条路径。`bot_core.py` 的会话窗口（20 条/5 分钟）、滚动摘要、群时间线各自从原始数据重算。
- **建议**：为每个 `(群/私聊, 会话)` 建立**仅追加事件日志**，把「构造 prompt」改成**从日志派生**的纯函数。收益：① 可回放/可重算（换 prompt 策略不用重跑线上）；② 崩溃后有确定的重建路径；③ 压缩/摘要变成对日志的一个投影，而不是改写历史。
- **注意**：QQ 的 `message_id` 不保证全局唯一，需要自己的 `SessionSeq`。
- *来源：`dsh-session/README.zh.md`「设计理念」「追加校验」「派生历史」*

#### P2. 内核与实现分离（接口包 + 驱动器包）

- **DSH 做什么**：`dsh-agent`（`Agent` 句柄、注册表、`agent/*` 事件）与 `dsh-agent-loop`（唯一具象驱动器）分处两个包，通过已注册的 `AgentFactory` 连接。**消费方从不导入驱动器。** 「只有标准的『调用模型、运行工具、重复』生命周期无法满足需求时，才应选择自定义 Agent 实现。」
- **为什么与编码无关**：这是纯粹的可替换性投资。
- **EggBloom 现状**：`bot_core.py` 6744 行，消息接收、缓冲、过滤、决策、AI 调用、拟人发送、指令、联系人管理**全在一个文件**；插件只能挂 `before_reply`/`after_reply`，**主循环本身不可替换**。
- **建议**：把「群消息 → 是否回复 → 组装上下文 → 调模型 → 发消息」抽成**可替换的主循环接口**（`ReplyLoop`），`bot_core` 只做 OneBot 适配与调度。AGI 无关，纯粹止损。
- *来源：`dsh-agent/README.zh.md`「设计理念」；`dsh-agent-loop/README.zh.md`「设计理念」「概述」*

#### P3. 固定顺序的拦截流水线 + 三种叠加语义

- **DSH 做什么**：工具调用固定走 `tools/pre-execute`（**可扩展的 allow/deny/ask waterfall**）→ **已注册单调守卫**（在 waterfall 之后注册的**拥有方策略**，**返回的理由会拒绝调用，后续监听器无法把该拒绝重新变为允许**）→ `tools/execute`（**环绕**包装层，做超时/重试/指标）→ `tools/post-execute`（检查/替换、附加有序上下文）→ `finalizeContent` → `tools/result`（**仅观测**）。
- **为什么与编码无关**：这是**事件总线的升级版**。EggBloom 的 `execute_hook` 已经做了「priority 升序 + 首个非 None 短路」和 `execute_hooks`（收集全部），但**没有区分「可重排的否决」与「不可推翻的拥有方策略」**。
- **建议**：给 `plugins/abi.py` 加三档语义：`waterfall`（可被下游改写）、`guard`（单调，不可推翻）、`observe`（只读，异常隔离）。再把群聊过滤链（定向他人/短消息/乱码/旁观者评论）从 `bot_core` 里的硬编码顺序改成**注册到流水线的 guard**。
- **配套警告（DSH 亲测）**：`tools/pre-execute` **有意不允许改写参数**——「否则日志记录与呈现的参数会与实际运行内容失去同步」。**EggBloom 同理：拦截器不要改写 `event["text"]`，要改写就另立字段。**
- *来源：`dsh-tools/README.zh.md`「对调用实施策略」「设计理念」「扩展点」「已知限制」*

#### P4. 作用域注册（scoped registration）+ 遮蔽

- **DSH 做什么**：`Agent.ctx` 是该 agent 的**作用域上下文**；通过它进行的注册（工具、提示词段、变量、事件监听器、限制）**只对该 agent 生效，并在 dispose 时全部撤销**。带作用域的段/变量/工具提供方会**遮蔽同名全局项**。「同一机制也是 agent preset 用来让一个会话获得不同能力集、同时不影响其邻居的方式」。
- **为什么与编码无关**：QQ bot 的「每个群有不同人格/不同回复策略」正是同一问题。
- **EggBloom 现状**：`plugin_manager` 是**全局**单例；插件配置全局共享（`ctx.get_config()` 读同一份合并配置），**没有逐群的注册作用域**。
- **建议**：为每个 `(群, 会话)` 引入**作用域上下文**，允许插件在其中注册 per-group 的钩子/人格片段/工具开关，作用域结束时自动撤销。这会把现在散落在 `bot_config.json` 里的按群开关变成**可组合的能力集**。
- *来源：`dsh-agent/README.zh.md`「将注册限定到单个 agent」；`dsh-system-prompt/README.zh.md`「作用域」；`dsh-agent-preset/README.zh.md`「概述」*

#### P5. 令牌预算的「保留策略 + 精确省略计数」原语

- **DSH 做什么**：`ItemRetainer`（保留前 N 个**有序逻辑单元**，**调用方持续送入每个已观察单元因此省略计数精确**）与 `TextRetainer`（**按字节**保留 head/tail/headTail，**内存中有界**：`headBytes + tailBytes + 一个分片`；**在每个切割处修剪不完整的码点**）。`formatRetentionNotice` 只标准化省略子句，**恢复指引由工具自己提供**（「只有工具知道恢复动作」）。
- **为什么与编码无关**：纯库，零编码语义。
- **EggBloom 现状**：`ARCHITECTURE.md` 提到省 token 靠「群聊时间线/群摘要/成员按需注入」，`token_stats.py` 只做计量；**没有统一的有界保留原语**，各处的截断各自实现（容易切坏 UTF-8 中文）。
- **建议**：在 `utils.py` 或新模块实现 `ItemRetainer` / `TextRetainer`（**Python 里注意按字节切**——中文 UTF-8 三字节，切坏会产生替换字符；DSH 的修剪逻辑正是为此存在）。
- **配套语义（很重要）**：**`truncated` 是预算事实，绝不表示上游不完整**——「权限失败、跳过二进制文件、提供方部分失败与不可读候选项都留在**工具领域字段**中，绝不并入 `truncated`」。EggBloom 里对应的就是：**「因为太长被截断」和「因为没搜到/没权限」必须是两个不同的字段**，否则模型会误判。
- *来源：`dsh-output-retention/README.zh.md`*

#### P6. 溢出到磁盘（spill）：把超大内容落到可读取的路径

- **DSH 做什么**：超过 `maxInlineTokens` 时，**保留首尾两端**（扣除省略提示后**两端各用剩余预算的一半**），并在末尾给出**完整结果文件的绝对路径** + 读取提示；**文字可切分，图片整张保留或省略**；**存储失败就保留原内容并记录警告**（「不会用无法读取的路径替换图片」）。
- **为什么与编码无关**：任何「模型要读一坨文本」的场景都适用。
- **EggBloom 现状**：群聊长记录、网页正文、群友画像、super_memory 召回结果**都会被硬截断**，丢掉的部分不可恢复。
- **建议**：把超长内容落盘到 `data/spill/<date>/<hash>.txt`，内联只给首尾 + 路径，再给 LLM 一个 `read(path, offset, limit)` 工具。**EggBloom 已有 12 个工具，加一个读取工具成本很低。**
- *来源：`dsh-spill-policy/README.zh.md`*

#### P7. 压缩用「标记对 = 事务」+ 结构化的 8 段式检查点

- **DSH 做什么**：① **`compaction/start` … `compaction/summary` + `compaction/end` 的日志标记对就是事务**；**活动的未匹配 start 是持久锁**（在较新 seed 之前 = 陈旧证据不阻塞；之后 = 报告 `busy`）；**闭合失败会有意留下阻塞性的未匹配标记**。② **摘要提示词是固定的 8 段结构**（Primary Request and Intent / Key Technical Concepts / Files and Code / Errors and Fixes / Pending Jobs / Current Work / Next Step / Critical Context），**空段写 `(none)` 而绝不删段**，规则含「**不要提及这次摘要请求**」「如果已有 `<compacted-summary>` 块，不要逐字复制，而是保留仍为真的事实、丢弃过期的、合并新信息」。③ **替换**旧范围而不是删原始日志。④ **拒绝不缩小源内容的摘要**。
- **为什么与编码无关**：8 段结构里的「Files and Code」是编码特化，**但「标记对事务」和「结构化检查点 + 不逐字复制前一版摘要 + 替换而非删除」是通用的**。
- **EggBloom 现状**：`ARCHITECTURE.md` 提到「滚动摘要」，但**摘要格式随意、没有并发保护、没有版本合并规则**；永久在线的 bot 一旦摘要崩了没有回退路径。
- **建议**：① 摘要过程写 `compact/start`、`compact/end` 到会话日志，**用未闭合的 start 当锁**防并发压缩；② 摘要提示词改用**结构化的段**，并按 EggBloom 领域重定义段名（例如 `人物与关系` / `进行中的事` / `用户偏好与禁忌` / `待办与承诺` / `情绪与氛围` / `下一步`），保留「空段写 (none)」「不要逐字复制上一版摘要」「不要提及摘要这件事」三条规则。
- *来源：`dsh-compaction-basic/README.zh.md`「设计理念」「区域事务」「辅助摘要器请求」*

#### P8. 用「策略快照追加」而不是「改写系统提示词」来切换模式

- **DSH 做什么**：策略变化（审批策略、沙箱模式、plan 模式）**追加一份完整的运行时上下文快照到保留历史之后**，而**不改写稳定的请求头/系统提示词**。`approval:policy`、`sandbox:policy` 都如此；`sandbox-policy` 明确写「**模式切换时，稳定的系统提示词仍逐字节相同**」。并强调：**未变化的请求不增加重复的策略 token**。
- **为什么与编码无关**：这是 KV cache 工程，跟编码无关。
- **EggBloom 现状**：`ARCHITECTURE.md` 已经把「static_prompt 稳定 + 滚动摘要 + dynamic 内容后置」列为设计特征——方向对了。但模式切换（例如「群聊行为」开关、人格切换）**是否改写 static_prompt 需要核实**；若改写，就会整段丢掉前缀缓存。
- **建议**：把所有「模式/策略」类动态状态**统一成追加式快照**（`system` 之前的静态段保持字节稳定），并像 DSH 一样让 `token_stats.py` **按 scene 记录 cache_hit/cache_miss**（EggBloom 已有此字段！）来**验证**这个不变量。
- *来源：`dsh-user-approval/README.zh.md`「KV Cache 影响」；`dsh-sandbox-policy/README.zh.md`「KV Cache 影响」「模型体验」；`dsh-plan-mode/README.zh.md`「KV Cache 影响」*

#### P9. 短横线命名的事件 + 「实时事件是呈现数据、不是回放来源」

- **DSH 做什么**：事件名统一 `域/动作` 形态（`agent/pre-step`、`tools/post-execute`、`session/event`、`goal/change`、`compaction/start`、`workflow/phase`、`hook/invoked`）。并把事件分成三类：**surface 事件**（携带 `surfaceOp`，派生模型消息）、**仅日志事件**（如 `compaction/*`、`hook/*`，**永不产生消息**）、**实时通知**（逐消息最小载荷，只用于 UI 同步）。**关键**：`loop` 会在 committed end frame **之前**提交持久 `assistant/message`，因此**实时流只是呈现数据**——回放永远读日志。
- **为什么与编码无关**：纯粹的可观测性设计。
- **EggBloom 现状**：`plugins/abi.py` 的事件是 `on_event(ctx, event_name, payload)`，**没有命名约定、没有三类区分**，`emit_event` 的 payload 是任意 dict。
- **建议**：① 定命名约定（`group/message.received`、`reply/before`、`reply/after`、`memory/recall`…）；② **明确区分「会进 LLM 上下文的事件」与「只进日志的事件」**——这是防止上下文被遥测数据污染的关键；③ 实时流（WebUI 事件通知页）与持久日志分开。
- *来源：`dsh-session/README.zh.md`「追加与派生」「设计理念」；`dsh-agent-loop/README.zh.md`「轮次与步骤流程」；`dsh-hook-protocol/README.zh.md`「`hook/*` 会话事件」*

#### P10. 一次性审批 seam（与通道无关）+ 失败关闭 + 审计配对

- **DSH 做什么**：`ctx.approval.request(req)` **与通道无关**；应答者是 `approval/request` waterfall 监听器（**返回结果即为所负责 agent 作答，否则 `next()` 委托**）；**没有最终应答者 → `unavailable` → 拒绝执行**；**`never` 策略在 waterfall 分发之前执行，因此 prepend 的监听器也无法绕过**；**模型只看到最终工具结果与当前策略，看不到审计事件与人类 UI**；审计是 `approval/asked` + `approval/decided` 的**配对**，不变式**在同一个未结束轮次内按 id 校验这一事件对**。
- **为什么与编码无关**：QQ bot 里「要不要执行这个敏感操作（跑 shell / 发文件 / 改配置 / 用管理员命令）」是同一问题，而且**多人**属性让「谁有权批准」变成真问题。
- **EggBloom 现状**：`ARCHITECTURE.md` 有「权限分级：strict/off 两档，管理员 = target_qqs + command_admins + Bot 本人」——**但这是硬编码在 bot_core 里的**，插件拿不到，也没有审批事件/审计日志。
- **建议**：抽一个 `approval` 服务：`request(plugin, action, reason)`；应答者可以是管理员 QQ（发一条确认消息）、可以是 WebUI、可以是自动策略；**默认失败关闭**（没有应答者 → 拒绝）；审计写进事件日志。
- *来源：`dsh-user-approval/README.zh.md`*

#### P11. 沙箱/能力的「策略逐调用传递、不要在提供方上固定」

- **DSH 做什么**：`SandboxPolicy` **逐调用携带，绝不在提供方上固定**——「两个消费方可以同时按不同策略隔离，获批的升权重试只是用更宽策略发起的新调用」。`effective = explicit grant ?? fold(events) ?? deployment default`。
- **为什么与编码无关**：这是「**多人**」系统的正确并发模型。
- **EggBloom 现状**：权限是「strict/off 两档 + 管理员列表」的**全局配置**；不同群、不同人、不同插件共享同一个开关。
- **建议**：把权限决策改成**逐次调用解析**（`resolve(group, sender, plugin, action)`），回退链是「本次显式授权 → 该群日志中的设置事件 → 部署默认」。**注意 DSH 的模式词汇只有文件操作**——EggBloom 需要自己定义能力词汇（发文件/跑命令/改配置/读私聊/生图…），**不要照抄 `read-only`/`workspace-write`**。
- *来源：`dsh-sandbox/README.zh.md`「设计理念」「升权编排」；`dsh-sandbox-policy/README.zh.md`「解析优先级」「逐会话存储」*

#### P12. 「比较并设置」的持久状态 + 陈旧引用拒绝

- **DSH 做什么**：goal 变更携带 `GoalRef { id, revision }` 并**拒绝陈旧引用**——「持有旧状态的消费方会收到清晰的陈旧 revision 错误，而不是静默覆盖更新的状态」。且有**严格回放**：拒绝形状错误、**不连续 revision**、**非法状态转换**、时间戳非单调、不连续的计数。
- **为什么与编码无关**：这是**多人并发编辑同一状态**的标准解法，QQ bot 里到处都是（多个人同时改人格/改配置/改群设置）。
- **建议**：EggBloom 的 WebUI 与 QQ 命令都能改配置（`config.py` 热重载 24 个变量）——**引入 revision 或 ETag**，让并发编辑返回明确冲突而不是静默覆盖。
- *来源：`dsh-goal/README.zh.md`「观察 goal」「设计」*

#### P13. 「人类输入的宿主证明」——防止非人类生产方伪造人类权限

- **DSH 做什么**：`Agent.followup()` 与 `steer()` 会在调用方省略 source 时分配 `{kind:'user'}`，因此「**插件、调度器与其他非人类生产方必须传入自己的 source，不能继承人类权限**」。goal 工具的权限检查要求「运行时**根** agent 的当前轮次中存在**人类直接消息**」——**subagent 或非人类生产方不能创建或编辑 goal**。
- **为什么与编码无关**：**任何有插件的系统都需要**。这是「谁能触发敏感动作」的根问题。
- **EggBloom 现状**：`plugins/abi.py` 里 `ctx.emit()` 是插件之间广播；`on_message` 拿到的 `event` 里 `sender` 是 QQ 号——**但插件可以自己调 `send_group` 发任意消息，也可能伪造/转发同类事件**；「管理员命令」的鉴权只检查 `sender` 字段。
- **建议**：给事件带上**不可伪造的来源标记**（`source: 'human' | 'plugin' | 'scheduler' | 'system'`，由**主程序**而不是插件填写），并让敏感权限检查**只接受 `human` 来源**。这直接对应 DSH 的 `AgentMessageSource`。
- *来源：`dsh-tool-goal/README.zh.md`「权限规则」「设计」*

#### P14. 挂载/卸载要 fail-closed 且达到完全停稳

- **DSH 做什么**：驱动器 teardown：「关闭准入 → 停用所有活跃续行 → 以 `parent` 原因取消进行中的工作 → **在事件防护仍生效的情况下**等待驱动器和 agent 完全停稳」；脱离运行（hook 进程）**被跟踪并在 dispose 时中止**，「不会有 hook 进程或迟到回调超出 fiber 存活」；提供方卸载「**阻止新启动，但绝不撤销已接受的运行**」。
- **为什么与编码无关**：永久在线的 bot 里，插件热重载是常态（EggBloom 有 HMR/热重载）。
- **EggBloom 现状**：`plugin_manager.unload()` 调 `on_unload` 并从注册表移除，但**没有「停止接受新工作 → 等待在途工作结束」的阶段**。`plugins/web_operator` 用 Playwright，`scheduler` 有定时任务——**卸载时在途任务与线程可能泄漏**。
- **建议**：给插件生命周期加 `CLOSING` 状态：卸载时先拒绝新 hook 调用，再等待在途任务结算（带超时），最后 `on_unload`。
- *来源：`dsh-goal-round-driver/README.zh.md`「设计」；`dsh-hook-protocol/README.zh.md`「脱离运行会被跟踪」；`dsh-subagent/README.zh.md`「所有权与不变式」；`dsh-workflow/README.zh.md`「生命周期与归属」*

#### P15. 用「配置表 + 推导兜底」而不是「枚举分支」表达分级

- **DSH 做什么**：权限预设是**配置表**（`presets: {name → {sandbox, approval}}`），**默认值可推导**（组合默认值不匹配任何预设时才必填 `defaultPreset`）；**不匹配任何预设的组合显示为 `custom`——可以离开，但不能选择**；保留名 `auto` 由 integration 动态提供。
- **为什么与编码无关**：任何「档位」设计都适用。
- **EggBloom 现状**：`strict/off` 两档硬编码 + `target_qqs`、`command_admins` 分散在 config。
- **建议**：把权限档位改成**配置表**，允许部署自定义档位名；对「不匹配任何档位的旋钮组合」显示为 `custom` 并禁止选中——这样加档位不需要改代码。
- *来源：`dsh-permission-presets/README.zh.md`「配置预设」「读取侧与 custom」*

#### P16. 委派（subagent）的通用骨架：一次性 vs 可续接 + 深度/容量上限 + 只读清单

- **DSH 做什么**：① **一次性**（结果作为返回值、发布即转移所有权）vs **可继续**（保留持久会话、有稳定 id、可 `sendMessage`/中断）两种形态；② `maxDepth`（**默认 1**）与 `maxActiveSubagents`（**默认 8**，且**非可续接父代理建立独立的池、自身不占名额**）；③ **容量耗尽直接拒绝，接纳不排队**（「避免等待后代的父代理又等待自己占用的名额」）；④ **发现用投影、不加载子级**（`listChildren` **不读取子级日志、不加载或恢复子级 Agent**，`O(D)` 时间）；⑤ 子级崩溃**无法破坏父级会话**；⑥ 子级与父级的上下文**默认隔离**（子级消耗自己的模型上下文，**子历史不会直接加入父级历史**）。
- **为什么与编码无关**：QQ bot 里「让一个子任务去搜资料/生图/跑网页，主对话不被污染」是同一个问题，只是子任务不是 agent 而是工具链。
- **EggBloom 现状**：`web_operator`（Playwright 浏览器任务）、`generate_image`、`search_web` 都是**阻塞式**的；`group_chat_decider` 是同步打分的；**没有「派出去、后台跑、回来汇报」的模型**，也没有并发上限。
- **建议**：如果要引入后台任务（这在永久在线的 bot 里很有价值——用户问「帮我下首歌」，不该让主对话卡住），**照搬这组约束**：默认深度 1、默认并发上限、**容量耗尽拒绝而非排队**、发现只读投影、子任务上下文隔离、**子任务崩溃不影响主循环**。
- *来源：`dsh-subagent/README.zh.md`*

#### P17. 工作流（脚本化扇出）的**失败纪律**——只搬规则，不搬机器

- **DSH 做什么**：**钩子误用（错误参数、未知选项、不支持的 schema、超上限）明确终止脚本，绝不溶解为逐项 `null`；逐项 `null` 只保留给普通子运行失败**；`WorkflowError` 携带**机器可路由的 code + `fatal` 标志**；**「部分输出绝不会被报告为成功」**（取消或异常完成返回错误）；**脚本最初同步片段有 VM 超时**（`syncTimeoutMs` 5000），**没有整体经过时间截止**；**跨 realm 错误无法 `instanceof Error`**，必须按 `name`/`code` 分支。
- **为什么与编码无关**：**失败分类纪律**完全通用。
- **EggBloom 现状**：`ARCHITECTURE.md` 提到「问卷强制工具链（≤20 轮）+ 完成验证，杜绝假装执行」——问题意识一致。但 `plugins/web_operator`、`scheduler` 等的失败目前多是 `try/except` + `print`，**没有机器可路由的错误码**，也没有区分「参数写错」（应立刻失败）与「目标站点暂时不可达」（应降级/重试）。
- **建议**：① 定义少量**机器可路由的错误码**并区分 fatal/非 fatal；② **参数/配置错误必须立刻失败，不要静默降级**；③ **部分成功绝不报告为成功**。
- **不要搬的**：`agent()` / `parallel()` / `pipeline()` 这套「让 LLM 写 JS 脚本来编排」的机制。QQ bot 的编排需求（「查天气然后生图然后发群」）用**固定的 Python 工具链**更可靠、更省 token、更好调试——LLM 写 JS 的收益主要在「几十上百个子任务的审计/迁移」这类场景。
- *来源：`dsh-workflow/README.zh.md`「失败纪律」「失败与恢复」；`dsh-workflow-ptc/README.zh.md`「最小配置」「已知限制」*

#### P18. 外部能力接入的命名不变量（**如果 EggBloom 要接 MCP**）

- **DSH 做什么**：`mcp__<serverName>__<rawName>`，**namespace 是本地配置、绝不采用远程 `serverInfo.name`**（「远程名称不可信、在部署间不唯一、且升级时可能变化」）；**公开名称是身份的纯函数**（有损规范化追加 12 位 SHA-256 hash），因此**会话历史与权限规则能在重载后保持有效**；**原始名称是唯一的协议名称**（公开名称绝不发给服务器）；**要么完整世代、要么没有**（同步原子交换世代，冲突回滚整个尝试）；**两个服务器同名工具共存**，**同服务器名重复在加载时明确报错**；**环境清洗删掉 `/KEY|PASSWORD|SECRET|TOKEN/i` 与所有前缀变量**。
- **为什么与编码无关**：纯工程不变量。
- **EggBloom 现状**：12 个硬编码工具在 `bot_core.py` 的 `TOOLS` 列表里（`bot_core.py:2738`）；**没有外部工具来源**。若要接 MCP（例如接一个记忆服务器或图像服务器），这五条不变量都直接适用。
- *来源：`dsh-mcp-client/README.zh.md`「设计理念」「工具命名与共存」「环境清洗」*

#### P19. ACP 式的「控制面」——**只暴露标准语义，不暴露实现细节**

- **DSH 做什么**：ACP 服务器**刻意只提供标准 ACP v1 界面**；**只发送标准语义更新**（原始提供方增量、重试尝试、呈现数据、不支持内容不进协议）；**诚实的能力公布**（`initialize` 只公布已挂载支持）；**协议元数据、客户端能力、权限选择与会话 id 绝不进入模型请求**；**内联 base64 在批量准入后即被丢弃**，持久消息只含**经过校验的附件引用**。
- **为什么与编码无关**：如果 EggBloom 要暴露一个「让外部程序驱动 bot」的接口（例如让另一个程序通过 WebUI 发消息给它、或让它作为子任务被别的系统调用），这些约束直接适用。
- **建议**：EggBloom 的 WebUI `/api/plugins/action` 目前只约定「`{"ok": True, ...}` 或 `{"error": "..."}`」；如果要做外部控制面，**明确「哪些是标准语义、哪些是内部细节」**，并**不要让内部状态（如 `_manager`、`_host_api`）泄漏到协议**。
- *来源：`dsh-acp/README.zh.md`「设计理念」「准入与提示词结算」「模型体验」*

#### P20. 把「设置/选项目录」和「当前选择」分开，并用事件通知目录变化

- **DSH 做什么**：客户端从**进程级目录（catalog）**读取可选项，从 **Session 投影**读取当前选择；**两者用不同的事件/接口**。注册或移除 Auto 会发出**无 payload 的** `permission-presets/catalog-changed` 通知，因此「**客户端先订阅再重新读取目录**」；该过程**不会追加 Session 事件、不发布 Session 投影帧、也不改变 Session 序列**。
- **为什么与编码无关**：纯 UI 状态同步设计。
- **EggBloom 现状**：`PLUGIN_SPEC.md` 的通用通道是「每次操作 request/response」，**没有「目录变化通知」概念**。
- **建议**：把「有哪些插件/有哪些档位/有哪些人格」做成**目录接口 + 变化通知**，与「当前选了什么」分离。加上 DSH 的原则：**目录变化不污染对话日志**。
- *来源：`dsh-permission-presets/README.zh.md`「目录、投影与可选命令」*

#### P21. 「先接收、后决策」的准入事务 + 取消不留半成品

- **DSH 做什么**：ACP 的准入是**一个事务**——先校验**整个**提示词批次、快照路由、重新检查**同一对象**与能力、持久化附件，**然后才**入队；「**先于准入完成的取消绝不会让迟到的轮次入队**」；**若任一审计事件在提交前失败，请求会被拒绝，而不会返回一项未记录的决定**；**中止会撤回问题，迟到的回答被丢弃**。
- **为什么与编码无关**：多人系统里「撤销/取消」是常态（QQ 的撤回消息、用户改口）。
- **EggBloom 现状**：`bot_core.py` 有 3/5/15s 三档**合并缓冲窗口**（`bot_core.py:6100`）——这天然就是一个准入窗口。**但取消路径没有明确语义**（用户发完立刻撤回会怎样？缓冲区里的消息被召回后是否还会回复？）。
- **建议**：把缓冲窗口明确定义为**准入事务**：窗口内可撤销、窗口关闭时原子提交；**取消必须撤回未进入队列的内容**；**不要在提交前产生不可回滚的副作用**（例如已经调用 LLM 的 token 是已花的——所以要**在准入之后才调 LLM**）。
- *来源：`dsh-acp/README.zh.md`「准入与提示词结算」；`dsh-user-approval/README.zh.md`「请求决定」*

#### P22. 长期目标（goal）的模型——**如果 EggBloom 要做「跨天持续任务」**

- **DSH 做什么**：① **每会话至多一个当前 goal**（「系统有意不支持并行目标」）；② **四种持久 phase + 一个进程本地 `armed`/`disarmed` 标志，后者绝不持久化**；③ **`block` 是唯一保留策略 code 与自由文本的 phase**，因此「提供方限制、预算耗尽、执行错误、请求人工输入」**共用一种持久 phase，而不是扩增生命周期状态**；④ **`resume` 清除先前 blocker reason**；⑤ **`active` 的 goal 在任何会话开始边界之后都会被停用续行**——「**驱动器绝不会自行复活工作**」；⑥ **Round 上限（默认 256）只计量轮次，不计量 token/货币/时间**；⑦ **取消绝不自动重启**（Round 已在进行/已排队的 goal 会在下一次 idle 时**被暂停**）。
- **为什么「部分」与编码无关**：状态机设计通用；**但「自动续跑」这个产品行为对 QQ bot 是危险的**——一个永久在线、多人共享的 bot **不应该**无人干预地自己连续行动 256 轮。
- **建议（重要）**：如果 EggBloom 要实现「帮我盯着这件事，有进展告诉我」这类跨天任务，**可以搬状态机**（phase + revision + 比较并设置 + 严格回放），但必须改掉续跑语义：**触发源应该是外部事件（新消息/定时器/条件满足）而不是「agent 空闲就自己继续」**；并且**必须有硬预算**（DSH 自己也承认「只有 Round 上限，不是资源预算」是限制）。
- *来源：`dsh-goal/README.zh.md`「驱动生命周期」「什么会保留，什么不会」「已知限制」；`dsh-goal-round-driver/README.zh.md`「何时停止续行」「已知限制」*

#### P23. 「健康度/用量」的锚点法（不重复计数）

- **DSH 做什么**：**测量锚点**——只有「最新成功调用的规范请求 envelope 与已测量 envelope 匹配、且其总量不低于该调用完整路由定价锚点」时，才复用提供方用量；否则重新估算。**测量锚点包含成功的 `assistant/message` 之前的已计价表面**，因此**持久输出未变时完成调用的表面增量为零**；后续变更只是**相对于该锚点的带符号增量（含负 delta）**。
- **为什么与编码无关**：任何「回放日志算总量」的系统都需要它，否则会重复计数。
- **EggBloom 现状**：`token_stats.py` 用的是**累加式**（`_cumulative[scene]` 逐次累加 `prompt_tokens` 等）——对「一共花了多少」是对的，**但对「当前这个会话现在占了多少上下文」给不出答案**（而这些请求的 prompt 是累积的，累加会把共享前缀重复计数 N 次）。
- **建议**：为「**当前上下文压力**」单独实现锚点式测量（记录最近一次成功调用的 envelope，只在 envelope 变化时算增量），不要复用累加计数器。
- **另一条值得抄的诚实**：DSH 明说启发式「**每 token 四字符**」会**严重低估 CJK**，并叮嘱「**把构成行呈现为近似构成，绝不呈现为总量**」。EggBloom 是中文场景，**更加**需要这条纪律——不要给用户看「精确 token 数」的假象。
- *来源：`dsh-token-meter/README.zh.md`「测量压力」「设计理念」「解读数字」「开发备注」*

#### P24. 提示词组装的「段 + 变量 + 严格渲染」模型

- **DSH 做什么**：段带 `order`（**先按顺序值升序，顺序值相同时按名称的代码单元顺序**）；**集中分配顺序值**（`getSectionOrder(name)`）；`complete: true` 段成为完整提示词，**超过一个就组装失败**；变量 `{{name}}` **每次组装时解析**；`interpolate: false` 原样保留；**严格规则：未知引用、已注册但无值的引用、格式错误的完整组都抛出**——「**格式错误的提示词比明确失败更糟**」；**空段会消失**。
- **为什么与编码无关**：纯文本组装工程。
- **EggBloom 现状**：`ARCHITECTURE.md` 第 7 节：「人格注入：EGO personality_engine 优先，personality.txt 兜底，mtime 缓存」；`plugins/ego/chat_style`、`humanlike_rhythm`、`slang_injector` 各自往 prompt 里塞东西——**优先/兜底靠 if-else，顺序靠调用顺序**。
- **建议**：把 prompt 组装改成**注册表**（每个插件注册带 `order` 的段），并用**严格渲染**（缺变量 → 明确报错，而不是给模型一个 `{{user_name}}`）。这直接消除现在「谁能塞进 prompt」的隐式耦合。
- *来源：`dsh-system-prompt/README.zh.md`「贡献提示词段」「组装与渲染」*

#### P25. 插件的「三原则」与 ABI 版本化（EggBloom 已部分具备，值得对齐 DSH）

- **DSH 做什么**：`cordis` 的 `inject` 声明依赖；**效果、事件监听器与服务在拥有它们的 fiber 被 dispose 时一起移除**；**`Volatile<T>`** 让「只改 volatile 的配置变更」无需重启即可生效。
- **EggBloom 现状**：`plugins/abi.py` 已经写了**真正的插件三原则**（不 import 主程序、只通过 `ctx` 访问能力、声明式 manifest + 显式 hooks）并有 `PLUGIN_API_VERSION = 2`、`validate_manifest`——**方向完全正确，与 DSH 同构**。
- **建议对齐的两点**：
  1. **依赖声明**：`PLUGIN_MANIFEST` 有 `dependencies: []` 但注释写「预留」。DSH 的 `inject` 是**加载顺序与可用性的硬保证**，值得实装。
  2. **Volatile 配置**：EggBloom 的配置热重载被 `config.py:400` 的「24 个可重载全局变量」限死（端口类需重启）。DSH 的做法是**区分启动配置与 volatile 配置**（`maxParallelToolCalls` 是 volatile，`agents` 是启动配置）——**把「哪些能热改」变成每个字段的显式声明**，而不是一张全局白名单。
- *来源：`cordis/README.md`「Quick Start」「Volatile configuration」；`dsh-agent-loop/README.zh.md`「配置声明式 agent」*

### 11.2 ❌ 编码 agent 特化（**不要**搬到 EggBloom）

| 模式 | 出处 | 为什么不搬 |
|---|---|---|
| **文件沙箱三模式**（`read-only`/`workspace-write`/`danger-full-access`）、`workspaceRoot`、「每个会话只有一个主要工作区根目录」 | `dsh-sandbox`、`dsh-sandbox-policy` | **词汇表本身就是文件操作**——文档明说「**文件操作是完整的策略词汇**——该 seam 不表达网络、进程、系统调用、设备或凭据限制」。QQ bot 需要的能力词汇是「发文件/跑命令/改配置/读私聊/生图/发言」，不是「能不能写工作区」。**要搬的是「策略逐调用传递 + 逐会话 fold + 故障关闭」这个骨架（见 P11），不是这套模式名。** |
| **`sandbox_permissions` + `justification` 升权重试** | `dsh-sandbox` | 强绑定「模型可以重试一次完全相同的调用」这一编码 agent 交互形态。QQ bot 里敏感操作的正解是**先问管理员**（异步、可能几分钟后才答），不是「模型立刻重试」。 |
| **`tools/pre-execute` 的 allow/deny/ask + 逐工具 guard** | `dsh-tools` | **机制通用（见 P3），但「工具」这个层级是编码 agent 的**。EggBloom 的对应物是「回复前/回复后/是否插话」这些**对话级**决策点，不是「函数调用级」。可以直接用流水线骨架，但不要照搬 `ToolDefinition`/`ToolSchema`/`exec.signal` 这套形状。 |
| **`defineTool` 的 schema DSL + `InferValue` 16 层容器 + TS/Python SDK 渲染** | `dsh-tools` | 为**给模型看的类型化参数**服务。EggBloom 的 12 个工具是硬编码 dict（`bot_core.py:2738`），好处是**简单、可控、无构建步骤**。引入 schema DSL 在没有大量工具、没有类型化 SDK 生成需求前是过度设计。**但要搬的一条是：工具输出要有「规范值」+「渲染」两层分离**（DSH 的 `output.schema` + `output.render`）——这能让日志与呈现解耦。 |
| **PTC（`run_code` + 生成 SDK + `<parent>:ptc:<n>` 子调用）** | `dsh-tools`、`dsh-workflow-ptc` | 「让 LLM 写代码来调用工具」在**工具数量多、调用链长**时省 token；QQ bot 的调用链短（1-3 步），**直接用 function calling 更可靠**。而且 PTC 的中间值「只存在于执行局部、无法从会话回放重建、可能耗尽内存」——对一个永久在线的 bot 是**稳定性风险**，不是收益。 |
| **`dsh-workflow` 的 JS 脚本扇出（`agent`/`parallel`/`pipeline`/`phase`/`log`）** | `dsh-workflow`、`dsh-tool-workflow` | 收益场景是「一次审计几十个文件、跨多角度研究」这类**大规模扇出**。QQ 对话的编排需求用固定 Python 工具链更好。**只搬失败纪律（P17）。** |
| **`mode: native/ptc/both` 的工具呈现模式配置** | `dsh-tools` | 同上，无 PTC 就没这个轴。 |
| **`dsh-compaction-basic` 的 8 段式摘要里的 `Files and Code` 段，以及 `thresholdRatio`/`headroomTokens`/`retainRatio` 的具体默认值** | `dsh-compaction-basic` | 段名与阈值是**按「长编码会话、上下文窗口 128k+、工具输出巨大」标定的**。QQ bot 的单轮上下文小得多、且**每个群是独立的小会话**——**照抄阈值会导致过度压缩**。要搬的是**结构（标记对事务 + 结构化检查点 + 替换而非删除 + 不逐字复制上一版）+ 三条规则**，阈值和段名必须按 EggBloom 的语料重新标定。 |
| **`goal` 的「agent 空闲就自动续跑 Round」** | `dsh-goal-round-driver` | **对一个永久在线、多人共享的 QQ bot 是危险行为**——它会无人干预地连续行动最多 256 轮。见 P22：可以搬状态机，**必须**换掉触发源。 |
| **`goal` 的「Round 上限 256」作为唯一预算** | `dsh-goal`、`dsh-goal-round-driver` | DSH 自己列为已知限制：「**只有 Round 上限，不是资源预算**——token、货币、时间与提供方配额策略保持独立」。QQ bot 的 token 是真金白银且**多个群共享**，需要**token/频率/并发**预算，不只是轮次。 |
| **`maxParallelToolCalls` 默认 10、`maxActiveSubagents` 默认 8、`maxTotalAgents` 默认 1000、`maxItemsPerCall` 默认 4096** | `dsh-agent-loop`、`dsh-subagent`、`dsh-workflow-ptc` | 这些数字**为单用户本地编码会话标定**（一台机器、一个用户、要跑满 CPU）。QQ bot 的并发约束来自**上游 LLM 的 rate limit、QQ 的风控（发消息过快会被限制/封号）、以及多个群同时说话**。数字必须重算——**但「有明确上限」这件事本身要抄**。 |
| **`dsh-hooks-claude-code` / `dsh-hooks-codex` 的整个桥接** | 两者 | 价值是「复用已有的 Claude Code / Codex 用户配置」，**前提是用户是编码 agent 用户**。QQ bot 用户没有这类配置。**要搬的是它们证明的方法论（见 P26 下面的「扩展点可移植性」）**，不是桥接本身。 |
| **`dsh-acp` 作为「程序化驱动 agent」的协议** | `dsh-acp` | ACP 是**编码 agent 生态的标准协议**（Zed 等编辑器用的）。EggBloom 若要暴露控制面，应定义**自己的**（或复用 OneBot/WebUI），不必实现 ACP v1 的 `session/*` 方法集。**但「只暴露标准语义、不泄漏实现细节」的原则要抄（P19）。** |
| **`dsh-mcp-client` 的 `mcp__server__tool` 命名与 MCP 接入** | `dsh-mcp-client` | **MCP 本身是通用的**，如果 EggBloom 要接 MCP 服务器可以照用（P18）。但**「命名不变量」「原子世代交换」「环境清洗」这些是实现细节**——若只用一两个 MCP 服务器，成本收益不划算。 |
| **`dsh-base` 的 profile/bundle 组合体系（`cordis.patch.yml` + `!!js` + 按 id 覆盖行）** | `dsh-base`、`dsh-plugin-manager` | 为「多个分发形态（web/headless/sdk/acp）× 多种定制」而建。EggBloom 只有**一个**分发形态（桌面端 + WebUI），引入 patch 层会让配置来源变成四层（程序目录/AppData/.env/凭据管理器 **再加** patch）——**复杂度大于收益**。**要搬的只有一条**：「**覆盖会替换整个设置块，不会自动合并**」这个坑本身值得在文档里写清楚。 |
| **`dsh-plan-mode`（`exit_plan_mode` + plan-review 评审 UI）** | `dsh-plan-mode` | 「先探索设计、提交计划、等人类批准再执行」是**编码任务**的协作形态（因为编码的副作用大且难撤销）。QQ bot 的对应场景（「先说说你打算怎么回」）**没有产品需求**，而且计划模式自己承认「**引导而非强制**——只通过文本约束」。 |
| **`agent/assistant-stream` 的 `start`/`chunk`/`end` 帧与「committed end frame 前提交持久消息」** | `dsh-agent`、`dsh-agent-loop` | 为「流式渲染 + 精确回放」设计，依赖「一次模型调用 = 一个 attempt」这个编码 agent 的强约定。QQ bot 的流式价值低（QQ 不是流式终端），**但「实时事件是呈现数据、回放永远读日志」这条原则通用（见 P9）**。 |
| **`SessionSeq` / `SessionLogOffset` / `SessionSeqCursor` / `OptionalSessionSeq` 这套四种数字类型** | `dsh-session` | 精细的类型区分服务于「事件序号 vs 日志偏移 vs 尚无事件」这三种语义的严格区分。Python 里没有 brand 类型，**收益远低于成本**——用 `int | None` + 文档约定即可。**但要搬的是「事件的序号是唯一权威坐标」这个观念。** |
| **「严格回放」（拒绝不连续 revision / 非法状态转换 / 时间戳非单调）用于所有状态** | `dsh-goal`、`dsh-agent-loop` | 只在**必须防伪造/防损坏**的状态上用（DSH 自己也只用在 goal 和 session 上，并承认「**这是完整性检测，不是插件隔离**」）。给所有 EggBloom 状态都加严格回放会拖慢启动。 |

**关于第 10 节的一个可移植性结论**：`dsh-hooks-claude-code` 与 `dsh-hooks-codex` 最值得 EggBloom 学的不是桥接本身，而是它们**证明了「扩展点」是一套稳定的、被第三方生态验证过的抽象**——两个完全不同的私有方言（Claude Code 的 30 个事件、Codex 的 10 个事件）都能映射到同一组 6 个内部扩展点上。**EggBloom 的 `PLUGIN_HOOKS`（`on_load`/`on_enable`/`on_disable`/`on_unload`/`on_message`/`on_before_reply`/`on_after_reply`/`on_event`）已经是一组不错的扩展点**，值得做的是：① **保持它的稳定性并文档化**（对应 `hook-protocol` 的角色）；② **为它写「共享规则」文档**（对应 `hook-protocol/README.zh.md`，明确规定「失败必须降级为受控结果、绝不向主循环抛异常」「合并规则」「事件只在轮次内」）；③ 用**同一组扩展点**去适配未来的多个上游（例如不同的 QQ 协议适配器、不同的 LLM 提供方），而不是为每个适配器复制逻辑。
*来源：`dsh-hook-protocol/README.zh.md`「设计理念」；`dsh-hooks-claude-code/README.zh.md`「Hook 点映射」；`dsh-hooks-codex/README.zh.md`「Hook 点映射」*

### 11.3 建议的落地顺序

按「收益 / 改动成本」排序，前四项是**高收益低风险**：

| 顺序 | 项 | 对应 DSH 包 | 成本信号 |
|---|---|---|---|
| 1 | **P8 策略快照追加（保住前缀缓存）** + 用现成的 `token_stats.py` 的 `cache_hit_rate` 验证 | `dsh-user-approval`、`dsh-sandbox-policy` | 小：主要是把「模式切换改写 static_prompt」改成「追加动态段」 |
| 2 | **P5 + P6 有界保留原语 + spill 落盘** | `dsh-output-retention`、`dsh-spill-policy` | 小：两个纯函数 + 一个落盘目录 + 一个 `read` 工具 |
| 3 | **P1 事件溯源会话日志（先做「对话历史」这一条流）** | `dsh-session` | 中：但要选一个边界（建议先只覆盖一个群的对话历史）做试点 |
| 4 | **P24 prompt 段注册表 + 严格渲染** | `dsh-system-prompt` | 中：要改 `ego` 各子插件与 `slang_injector` 的塞入方式 |
| 5 | **P3 三档拦截语义（waterfall / guard / observe）** | `dsh-tools` | 中：`plugin_manager.execute_hook` 已在，主要是加语义档位并迁移过滤链 |
| 6 | **P20 目录 + 变化通知** | `dsh-permission-presets` | 中：WebUI 侧改动 |
| 7 | **P10 审批 seam（失败关闭 + 审计配对）** | `dsh-user-approval` | 中：需要「管理员应答者」的实现 |
| 8 | **P13 人类输入宿主证明** | `dsh-tool-goal` | 中：要给事件加不可伪造的 source |
| 9 | **P7 压缩的事务化 + 结构化检查点** | `dsh-compaction-basic` | 中：依赖 P1 |
| 10 | **P14 卸载 fail-closed** | `dsh-goal-round-driver` | 中：`web_operator`/`scheduler` 有在途任务 |
| 11 | **P4 作用域注册（逐群能力集）** | `dsh-agent`、`dsh-agent-preset` | 大：动 `plugin_manager` 核心 |
| 12 | **P2 主循环内核/实现分离** | `dsh-agent` / `dsh-agent-loop` | 大：要拆 `bot_core.py` |
| 13 | **P16 后台委派（一次性/可续接 + 上限 + 只读发现）** | `dsh-subagent` | 大：新增并发模型 |
| 14 | **P22 跨天任务状态机（触发源改为外部事件）** | `dsh-goal` | 大：产品决策未定 |
| 15 | **P23 上下文压力的锚点式测量** | `dsh-token-meter` | 中：独立于累加统计 |

---

## 附录 A：DSH 的关键不变量速查

供设计新 harness 时逐条自问「我有没有等价物」。

| # | 不变量 | 出处 |
|---|---|---|
| A1 | **模型可见即已记录**——任何到达模型请求的内容都必须能从日志重建 | `dsh-session` |
| A2 | **接口与实现分离**——消费方依赖接口包，不依赖驱动器包 | `dsh-agent` |
| A3 | **发布即边界**——发布前提供方拥有设置并须在失败时回滚；发布后调用方拥有运行并须 dispose | `dsh-subagent` |
| A4 | **失败关闭**——无法强制执行所请求的策略时拒绝执行，绝不静默放行 | `dsh-sandbox`、`dsh-user-approval` |
| A5 | **先记录标记、后做事**——日志标记对就是事务；未匹配的 start 是持久锁 | `dsh-compaction-basic` |
| A6 | **先预留、后准入**——预留在陈旧时被丢弃，不消耗配额 | `dsh-goal-round-driver` |
| A7 | **比较并设置**——变更携带精确 revision，拒绝陈旧引用 | `dsh-goal` |
| A8 | **追加优先**——模式/策略变化追加完整快照，不改写稳定前缀 | `dsh-user-approval`、`dsh-sandbox-policy` |
| A9 | **严格渲染**——格式错误的提示词比明确失败更糟 | `dsh-system-prompt` |
| A10 | **绝不向主循环抛异常**——每种失败模式降级为受控结果 | `dsh-hook-protocol` |
| A11 | **钩子误用明确终止，普通失败才是逐项 null**——不要混淆两类失败 | `dsh-workflow` |
| A12 | **部分输出绝不报告为成功** | `dsh-tool-workflow` |
| A13 | **要么完整世代、要么没有**——同步原子交换，冲突回滚整个尝试 | `dsh-mcp-client` |
| A14 | **dispose 必须达到完全停稳**——不留下进程、任务或迟到回调 | `dsh-hook-protocol`、`dsh-agent-loop` |
| A15 | **每个可观察效果都通过事件发生**——包内部实现绝不属于公开接口 | `dsh-agent-loop` |
| A16 | **实时事件是呈现数据，回放来源永远是持久日志** | `dsh-agent` |
| A17 | **先于准入完成的取消绝不会让迟到的轮次入队** | `dsh-acp` |
| A18 | **`truncated` 是预算事实，不是上游完整性事实** | `dsh-output-retention` |
| A19 | **非人类生产方必须传入自己的 source，不能继承人类权限** | `dsh-tool-goal` |
| A20 | **测量/构成是近似值时就明说**——不要把启发式呈现为精确值 | `dsh-token-meter` |

## 附录 B：本次提取的范围与方法

- **提取方式**：`D:\git\_eggbloom-backup-20261003\extract-asar.py`（手写 asar 头部解析 + 顺序数据读取）从 `D:\deepseek\resources\app.asar` 的 `dsh/node_modules/@deepseek-ai/` 下按包提取到 `D:\git\_eggbloom-backup-20261003\dsh-src\<包名>\`。
- **本次提取的包（31 个）**：任务指定的 18 个（`dsh-agent`、`dsh-subagent`、`dsh-workflow`、`dsh-goal`、`dsh-plan-mode`、`dsh-system-prompt`、`dsh-session`、`dsh-token-meter`、`dsh-spill-policy`、`dsh-output-retention`、`dsh-permission-presets`、`dsh-user-approval`、`dsh-mcp-client`、`dsh-acp`、`dsh-agent-preset`、`dsh-base`、`dsh-plugin-manager`、`cordis`）+ 为补齐论证而追加的 10 个（`dsh-goal-round-driver`、`dsh-tool-goal`、`dsh-command-goal`、`dsh-tool-workflow`、`dsh-workflow-ptc`、`dsh-sandbox`、`dsh-sandbox-policy`、`dsh-hook-protocol`、`dsh-hooks-claude-code`、`dsh-hooks-codex`）+ 任务开始前已提取好的 3 个（`dsh-agent-loop`、`dsh-tools`、`dsh-compaction-basic`）= **31 个**。
- **只读不改**：未修改 `D:\deepseek\` 下任何文件，未修改 EggBloom 仓库；只向 `dsh-src\` 写了提取产物，并向 `D:\git\EggBloom\_ref\plan\` 写了本文档。
- **未做的**：没有逆向 `lib/*.js`（压缩产物）；文档未说明之处标为「文档未说明」或标注「(推断)」。
- **本文档未覆盖的相邻包**（存在但未提取，未来需要时可补）：`dsh-tool-*` 系列各具体工具、`dsh-compaction-tool-result-pruner`、`dsh-command-compact`、`dsh-session-projection`、`dsh-spill-local`、`dsh-tool-call-timeout-policy`、`dsh-pty`/`dsh-shell` 系列、`dsh-client-ui-*`、`dsh-ptc-runtime-node`、`dsh-subagent-spawn-in-process`、`dsh-subagent-acp`、`dsh-tool-subagent-control`、`dsh-tool-ralph`、`dsh-agent-preset-registry`、`dsh-agent-tool-presentation`、`dsh-jobs`、`dsh-commands`、`dsh-session-persistence`、`dsh-mcp-resources`、`dsh-invariants`。
