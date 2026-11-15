---
name: plugin-authoring
description: 当主人的需求超出内置能力、需要"造一个新功能/新插件"时，先查内置文档验证可行性，再按 ABI 模板生成插件并提交审批。触发词：能不能新增、写个插件、扩展功能、做个新功能。
when_to_use: 用户提出内置工具/插件都做不到的新能力请求时。
triggers: 新插件, 造插件, 写个插件, 扩展功能, 能不能加个, 新增功能, 做个功能, 定制一个
---

# 插件创作手册（先验证，再生成，最后交审批）

## 铁律
1. **不承诺做不到的事**：造插件前必须先确认"这件事 ctx 能不能干"。
   ctx 只能：读配置/日志/私有状态、调 AI、发 HTTP、调 OneBot 动作
   （发消息/群操作/驱动扩展动作如发说说）。**做不到的**（如改 QQ 客户端、
   绕过平台限制、访问未提供的第三方账号）→ 直接如实告诉主人，不要硬造。
2. **绝不直接写进 plugins/**：未审批的代码进运行路径 = 安全事故。
   唯一出口是 `plugin_forge` 工具 → 进 staging（plugins/_pending/）等主人点同意。
3. **提交后不许说"已完成/已生效"**：正确说法是"已提交审批，等主人在 Agent 页同意"。
   主人问进展用 `plugin_forge_status` 查真实状态，不猜。

## 步骤
1. **查内置文档验证可行性**（这是"接口说明书"，务必先读）：
   - `ws_read docs/PLUGIN_DEV.md` —— 真插件 ABI v2：三原则、最小模板、
     ctx 全部能力、钩子签名。**必读**。
   - 需要插件配置页时再看 `docs/PLUGIN_SPEC.md`（ui.html 契约）。
   - 对照 ctx 能力清单判断：主人要的"新功能"能不能用
     `ctx.api_call / ctx.http_get / ctx.ai_chat / ctx.set_state` 组合实现？
     不能 → 停，如实说明边界；能 → 继续。
2. **按最小模板生成代码**（`__init__.py` 是唯一必须文件）：
   - `PLUGIN_SPEC = 1`
   - `PLUGIN_MANIFEST = {...}`：name（=插件名，^[a-z][a-z0-9_]{1,30}$）、
     version、api_version（用 2 才有 ctx.api_call/send_*）、description、
     default_enabled 填 **False**（等审批启用）
   - 钩子函数（on_message / on_load / on_event …）+ `PLUGIN_HOOKS = {...}` 声明
   - **禁止** `import bot_core / config / data_store` 等主程序模块 ——
     一切能力走 ctx（提交时会被静态检查拦下）
   - 不阻塞：耗时逻辑自己开线程；不存明文密钥（用 ctx.get_config）
3. **提交审批**：`plugin_forge {plugin_name, description, files, motivation}`
   - files = {"__init__.py": 完整代码, ...}
   - 返回 ok=true 表示进了 staging；返回 problems 就照错误改再提交
     （常见：语法错、缺 PLUGIN_HOOKS、import 了主程序、name 不一致）
4. **如实汇报**：告诉主人"新插件「X」已生成并提交，QQ 会收到通知，
   去 Agent 页点『同意启用』后就生效；在那之前它还没工作"。

## 钩子速查（详见 docs/PLUGIN_DEV.md）
| 想要 | 用 |
|---|---|
| 收到消息就处理/回复 | on_message(ctx, event) 返回文本=直接回复 |
| 启停时初始化 | on_load / on_enable / on_disable / on_unload (ctx) |
| 响应其他插件广播 | on_event(ctx, name, payload)（配 ctx.emit） |
| 发群/私聊消息 | ctx.send_group / ctx.send_private / ctx.api_call |
| 调大模型 | ctx.ai_chat(messages, ...) |
| 存插件自己的数据 | ctx.get_state / ctx.set_state |

## 注意
- 一个插件只做一件事，名字见名知意（小写下划线）
- 同名（正式目录或另一条 pending）会被拒 —— 换名字，别试图覆盖
- 主人若问"上次那个插件好了没" → `plugin_forge_status` 查真实状态再答
- 静态校验只是第一道关，**真正生效只发生在主人点同意之后**
