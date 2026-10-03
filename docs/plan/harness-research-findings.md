# EggBloom Harness 调研结论与设计调整

> 来源：开源 agent 框架调研 + 项目内部功能重复检查（2026-10-03）。
> 用户约束：harness 要**现代、高功能、极高效率、极低成本**（不追求高性能）；
> 必须参考开源实现；功能重复要按逻辑与用途优化；允许接入 MCP 与 skill。

---

## 一、token 成本优化：可落地的手法（按收益排序）

| 手法 | 做法 | 效果量级 |
|---|---|---|
| **① 冻结前缀 + 稳定序列化** | system prompt / 工具定义 / L1 skill 元数据放在固定前缀，逐字节一致；工具按**字典序固定**排列，不插时间戳 | DeepSeek 前缀缓存命中价约为未命中 1/50~1/10，**最高省 90%** |
| **② 工具定义按需加载** | 不要全量注入。Anthropic：50+ MCP 工具占 ~72K token → 按需后 ~8.7K（**省 85%**）；GitHub Copilot 内置工具 40→13，准确率 +2~5pt、延迟 -400ms | **80% 收益来自 ①②两步** |
| **③ Skill 三级渐进披露** | L1 元数据（name+description，~50-100 token/skill，常驻）；L2 正文（触发时加载，<5K）；L3 资源（脚本只回传 stdout，代码不进上下文） | 抄 Claude Agent Skills，是目前最优解 |
| **④ 阈值触发压缩（不每轮）** | 攒到阈值才压缩，否则持续击穿前缀缓存；用**便宜模型**做摘要 | OpenHands：单轮成本降至基线 <50% |
| **⑤ 中间结果不进上下文** | 让模型写代码调工具链，结果在沙箱内流转，只回传最终值 | Anthropic 案例 150K→2K（-98.7%） |

## 二、MCP 接入最小实现

- Python 官方 `mcp` 包：`stdio_client` → `ClientSession.initialize()` → `list_tools()`
- **工具名加 `server__` 命名空间前缀**（避免冲突 + 便于路由）
- server 连接**启动时一次建立**，**缓存 `tools/list`**（不每次重查）
- stdio 子进程日志必须走 **stderr**（否则污染协议流）
- 工具数 >50 或单 schema >2K token → 必须切 **meta-tool 模式**（按需加载）

## 三、Skill 接入模式

抄 **Claude Agent Skills 三级披露**：
- **L1**：只读 YAML frontmatter 的 `name` + `description`（描述含"做什么"+"何时触发"），注入 system prompt
- **L2**：命中后读 `SKILL.md` 正文
- **L3**：`references/*.md`、`scripts/*.py` 只在被引用时读
- 命中判断：skill 数 <30 时**直接把 L1 列表交给模型自选**（推荐，最省）
- **绝不**把所有 SKILL.md 正文拼进 prompt

## 四、Subagent：**不做常驻**

- 收益拐点 **4-6 个**，协调成本超线性增长；过度分解比不做还贵
- **QQ 机器人场景结论**：不用常驻 subagent 架构。仅在「廉价模型跑长流程 + 强模型做决策」时按需用，且用 depth counter 硬限制递归深度

## 五、明确不做的事

1. ❌ 全量工具/skill 注入 —— token 杀手
2. ❌ 每轮做历史摘要 —— 持续击穿前缀缓存
3. ❌ 常驻多 agent 编排 —— 协调开销 > 收益
4. ❌ 用昂贵模型做压缩/摘要/工具选择
5. ❌ 复杂向量库 RAG 自动注入（先做对"按需搜索 + 显式拉取"）
6. ❌ 为性能做异步流水线（目标本是低成本，不是高性能）

---

## 六、内部功能重复检查结论（按逻辑与用途优化，不直接删）

### 真重复 · 需合并（P0）

| 项 | 位置 | 动作 |
|---|---|---|
| **LLM POST 重复** | `providers.py:119/201/403/567`（4 处同构）、`bot_core.py:3818`（远程画图）、`bot_core.py:5341`（摘要裸 urllib）、`webui_server.py:3133` | 收敛到 `_post_chat` / `http_llm_post()` |
| **工具循环重复** | `plugins/web_operator/operators/autonomous.py:252`（连 `_normalize_chat_return` 都抄了）、`bot_core.py:5860`（内联 while） | 迁 `harness/agent_loop` |

### 真重复 · 需合并（P1）

| 项 | 位置 | 动作 |
|---|---|---|
| **发送绕过封装** | `bot_core.py:4601`、`4774`、`4776` 直接 `api_call("send_...")`；`1374/1406` 封装内部重复 | 统一走 `smart_send_group` |
| **配置双轨** | 117 处 `_config.get(...)` 与模块顶层拉平常量并存 | 单轨化（热重载后可能读旧值） |

### 真重复 · 需合并（P2）

| 项 | 位置 |
|---|---|
| 窗口常量分散 | `[-10:]`(5815/5829/1473) vs `[-16:]`(5376/6503) vs `[-24:]`(1923/1947) 应集中 |
| 字节截断 | `bot_core.py:5936`、`providers.py:85`、`webui:3143` 对 bytes `[:500]` 再 decode → 用 `retention` |

### 不该合并（语义不同）

- 非 LLM 的 urllib：`plugins/abi.py:217`、`web_access.py:51`、`comfyui_client.py:34`
- 字符串型 `[:N]` 截断：Python 按字符切，**中文不会乱码**，只是分散（可收口不必强合）
- 插件跨进程 HTTP：进程边界原因可保留，但应走 `ctx.http_post`（abi 已有）

---

## 七、对已实现部分的调整清单

| 当前实现 | 按调研应调整 |
|---|---|
| agent 注入**全部 12 个工具** | 改为**按需加载**（meta-tool / 分组加载），保住前缀缓存 |
| 工具列表顺序 | 确保**固定顺序**（字典序），不因 dict 变动抖动 |
| 无 skill 支持 | 新增 **L1/L2/L3 三级披露**的 skill 加载器 |
| 无 MCP 支持 | 新增 MCP hub（stdio + 命名空间 + 缓存 tools/list） |
| 循环已有 `_normalize_chat_return` | 可让 `autonomous.py` 复用，消除重复 |
| `AGENT_MODE_SNAPSHOT` 追加式 | ✅ 已符合（不改写前缀） |

## 八、落地优先级（80% 收益在前两步）

1. **冻结前缀 + 固定序列化** ← 部分已有（任务 5.1）
2. **工具按需加载** ← 待做（关键）
3. Skill 三级披露 ← 待做
4. 阈值触发压缩（便宜模型） ← 待做
5. MCP hub ← 待做
6. 可选：收敛 `autonomous.py` / `bot_core:5860` 的工具循环 ← 去重
