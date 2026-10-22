# EggBloom 插件 UI 规范

> 本文只管 `ui.html` 的写法与前端/后端契约。
> **插件 ABI（`PLUGIN_SPEC` / `PLUGIN_MANIFEST` / `PLUGIN_HOOKS` / `ctx` 能力）见
> [`PLUGIN_DEV.md`](PLUGIN_DEV.md)** —— 当前 ABI 版本为 2，新插件请一律用真插件 ABI。

## 1. 目录结构
```
plugins/<plugin_id>/
  ui.html          # 独立单页应用，必须自包含
  __init__.py      # Python 后端：PLUGIN_META + PLUGIN_ACTIONS
  data/            # 运行时数据（config/history），不入 git
    config.json
  plugin.json      # 插件元数据（可选）
```

## 2. ui.html 要求
- 单文件 SPA，HTML/CSS/JS 全部内联
- 不依赖外部 CDN，所有资源内嵌或走 /api/plugins/action
- `<script>` 必须包裹在 IIFE 或 DOMContentLoaded 里，避免全局污染
- 必须实现 init() 并在 DOMContentLoaded 后调用

## 3. API 契约
所有后端交互统一走：
```
POST /api/plugins/action
{
  "plugin_id": "<id>",
  "action": "<action_name>",
  "params": { ... }
}
```

## 4. 通用插件通道（核心对接方式）
**你不需要修改 webui_server.py。** 只需在插件的 `__init__.py` 导出
`PLUGIN_ACTIONS` 字典，webui 就会自动把它注册为可调用动作：

```python
# plugins/my_plugin/__init__.py
def get_config():
    ...

def set_config(params):
    ...

PLUGIN_ACTIONS = {
    "get_config": get_config,            # fn(params) -> JSON 可序列化
    "set_config": set_config,
    "do_thing": lambda params: {...},
}
```

### 4.1 通道匹配顺序
对一次 `POST /api/plugins/action`，webui 依次尝试：
1. `PLUGIN_ACTIONS[action]` → 直接调用 `fn(params)`
2. 兜底：模块导出的 `get_config` / `set_config` / `toggle`
3. 兜底：模块导出的 `execute_action(action, params)`
4. 都不匹配 → 返回 `{"error": "unknown plugin_id or action"}`

### 4.2 只有声明了 PLUGIN_ACTIONS 或 execute_action 的插件走通用通道
未声明的旧插件仍走 webui_server.py 内的既有分支，二者完全兼容，互不影响。

### 4.3 动作设计规范
- **单一职责**：一个 action 做一件事
- **参数扁平化**：前端传 `params` 对象，不要嵌套太深
- **返回统一格式**：`{"ok": True, ...}` 或 `{"error": "..."}`
- **异常捕获**：webui 已对 `PLUGIN_ACTIONS` 调用包 try/except，返回 `{"error": str(e)}`

## 5. 按钮映射规则
- 每个按钮必须有真实后端 action，禁止无意义按钮
- onclick 必须调用前端 helper，禁止直接 fetch
- 所有 async 函数必须 try/catch，错误写入日志区

## 6. 错误处理
- API 层统一捕获 fetch 错误，输出 `[api] <url> <error>` 到 Console
- UI 层用 toast() 提示用户，不要 alert()
- 空数据必须显示"暂无数据"，不要空白

## 7. 状态管理
- 页面初始化时调用 loadModuleConfig() / loadPersonality() 等
- 保存后必须刷新对应列表，保持数据一致性
- 日志区自动滚动到底部

## 8. 命名规范
- 插件 ID：小写英文，如 ego, slang_learner
- action：蛇形命名，如 get_config, save_examples
- DOM ID：`<plugin>-<component>`，如 ego-log, ego-mem-msg

## 9. 安全
- **禁止**把 API Key 写进 config.json 或代码
- 密钥走 `cred_store.py`（Windows 凭据管理器）；网页只回显 `_has_api_key` 布尔
- 参考 web_operator：`data/` 内含独立 `.gitignore` 忽略 secret
