# EggBloom 插件开发（真插件 ABI v2）

从 EggBloom Desktop 开始，插件分两种形态：

| 形态 | 标识 | 面向 | 边界 |
|---|---|---|---|
| 内置插件（旧） | `PLUGIN_META` + hooks | 项目自带功能 | 强依赖主程序（可访问全局） |
| **真插件（新）** | `PLUGIN_SPEC = 1` + `PLUGIN_MANIFEST` + `PLUGIN_HOOKS` | **第三方/独立开发** | 只经 `ctx` 访问能力，不 import 主程序 |

> **ABI 版本**：当前 `PLUGIN_API_VERSION = 2`。manifest 里写 `api_version: 1` 的老插件**照常工作**，
> 写 `2` 才能用 v2 新增能力（`ctx.api_call` / `ctx.send_group` / `ctx.send_private` / 真事件总线）。
> 版本高于 2 的 manifest 会被拒绝加载（而不是静默出错）。

内置插件继续工作，不受影响；**新插件请一律用真插件 ABI**。

## 真插件三原则

1. **不 import 主程序**：禁止 `import __main__`、`import bot_core`、直读 `bot_config.json`；
2. **只通过 `ctx`（PluginContext）拿能力**：配置/日志/状态/AI/HTTP；
3. **声明式 manifest + 显式 PLUGIN_HOOKS**：由管理器做 API 版本与依赖校验，不符合即拒绝加载。

## 最小模板

```python
# plugins/my_plugin/__init__.py
from plugins.abi import PluginContext

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "my_plugin",          # 唯一标识（字母数字下划线）
    "version": "1.0.0",
    "api_version": 1,             # 必须 <= abi.PLUGIN_API_VERSION
    "author": "you",
    "description": "一句话说明",
    "dependencies": [],           # 预留：依赖的其他插件名
    "priority": 50,               # hook 派发优先级（升序，小先执行）
    "default_enabled": False,     # 默认关闭，用户到 WebUI 插件页启用
}

def on_load(ctx: PluginContext):
    ctx.log("loaded")

def on_message(ctx: PluginContext, event: dict):
    # event: {"type": "group"|"private", "group_id", "sender", "text", "message_id", "time"}
    if (event.get("text") or "").strip() == "!ping":
        return "pong"             # 返回文本 = 插件回复，直接发送并跳过 AI
    return None                   # 不干预

PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_message": on_message,
}
```

## ctx（PluginContext）能力

| 方法 | 说明 |
|---|---|
| `ctx.log(msg, level="info")` | 日志（自动进主程序统一日志） |
| `ctx.get_config(key=None, default=None)` | 只读配置（与主程序同一份合并配置：默认值 ← 程序目录 ← AppData ← env/凭据管理器） |
| `ctx.get_state(key=None, default=None)` / `ctx.set_state(key, value)` | 插件私有 KV（`plugins/state/<name>.json`） |
| `ctx.ai_chat(messages, tools=None, max_tokens=800)` | 调用主程序共享 AI 客户端（按当前 provider 配置；未注入返回 None） |
| `ctx.http_get(url, timeout=15, headers=None)` / `ctx.http_post(url, json_body=None, ...)` | HTTP（requests 优先，urllib 兜底，返回 dict） |
| **`ctx.api_call(action, params=None)`** | ★ v2：**调用 OneBot 动作的唯一出口**（含 token 与信任处理）。驱动提供的扩展动作也由此调用，如 `send_qzone_msg`（发说说）、`set_self_longnick`（改签名）、`send_group_ai_record`（AI 语音）。宿主未注入时返回 `None`，插件应据此降级 |
| **`ctx.send_group(group_id, text)`** / **`ctx.send_private(user_id, text)`** | ★ v2：发消息便捷封装（内部走 `api_call`） |
| **`ctx.emit(event_name, **payload)`** | ★ v2：**真事件总线** —— 派发给所有声明了 `on_event` 的插件，返回 `[(插件名, 返回值)]`。带递归保护（深度上限 5） |

## 钩子签名

| 钩子 | 签名 | 说明 |
|---|---|---|
| `on_load` | `(ctx)` | 加载时（若默认启用） |
| `on_enable` / `on_disable` / `on_unload` | `(ctx)` | 启停/卸载生命周期 |
| `on_message` | `(ctx, event)` | 每条消息；返回非空字符串=插件回复（跳过 AI），返回 None 不干预 |
| `on_before_reply` | `(ctx, event)` | 预留：AI 回复前干预 |
| `on_after_reply` | `(ctx, event, reply)` | 预留：发送前改写回复 |
| `on_event` | `(ctx, event_name, payload)` | ★ v2 已启用：接收 `ctx.emit` 广播。按 manifest 的 `priority` 升序派发；单个订阅方抛异常只打印，不影响其他订阅方 |

## 约束与提醒

- 插件与主程序**同一进程**运行：请勿阻塞（长时间任务自己开线程/队列），异常会被隔离不会拖垮主程序，但内存/全局仍是共享的；
- 不要在插件里存明文密钥——需要 key 时用 `ctx.get_config()` 读，敏感项在凭据管理器；
- 变更 ABI（新增钩子/改签名）需要升 `PLUGIN_API_VERSION`，旧插件会被版本校验拦截而不是静默出错；
- 示例：见 `plugins/echo_plugin/`（默认关闭，`!echo <内容>` 演示）。

---

## v2 能力用法示例

### 调用驱动扩展动作（QQ 空间）

```python
PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {"name": "qzone_demo", "version": "1.0.0", "api_version": 2,
                   "priority": 50, "default_enabled": False}

def publish(ctx, content: str):
    r = ctx.api_call("send_qzone_msg", {"content": content})
    if not r:
        ctx.log("发布失败（宿主未提供 api_call，或驱动不支持）", "warn")
        return None
    if r.get("retcode") not in (0, None):
        ctx.log(f"发布被拒: {r}", "warn")
        return None
    return r.get("data")
```

### 广播事件 + 订阅事件

```python
# 发布方
def some_task(ctx):
    ctx.emit("distill_done", target="10000002", level="L2")

# 订阅方（另一个插件）
def on_event(ctx, event_name, payload):
    if event_name == "distill_done":
        ctx.log(f"收到蒸馏完成: {payload}")
        ctx.send_private(payload["target"], "人格卡已更新")
    return None

PLUGIN_HOOKS = {"on_event": on_event}
```

### 自检

```bash
python tests/test_plugin_abi_v2.py     # 22 项检查：版本兼容 / 能力注入 / 消息发送 / 事件总线 / 递归保护
```

---

## v1 → v2 变更记录（2026-10-03）

| 变更 | 说明 | 兼容性 |
|---|---|---|
| `PLUGIN_API_VERSION` 1 → 2 | manifest 可写 `api_version: 2` | v1 插件不受影响 |
| 新增 `ctx.api_call(action, params)` | OneBot 动作唯一出口，宿主注入 | 未注入返回 `None`，不抛异常 |
| 新增 `ctx.send_group` / `ctx.send_private` | 消息便捷封装 | — |
| `ctx.emit` 由**空壳**改为**真事件总线** | 原先只打一行日志，插件之间无法通信 | v1 插件若已调用 `emit`，行为从"无效果"变为"真的派发" |
| 新增 `on_event` 实际派发 | 用已有的 `execute_typed_hooks` 通用派发器 | — |
| 新增递归保护 | `PluginManager.MAX_EMIT_DEPTH = 5` | — |
