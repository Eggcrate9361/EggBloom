# EggBloom 插件开发指南

> 版本：0.1.0  
> 适用：EggBloom Desktop v0.2.x  
> 风格：KISS、插件独立、不改本体

---

## 1. 目录结构

每个插件都是 `plugins/` 下的一个独立子包：

```
plugins/
  my_plugin/
    __init__.py      # 插件主入口，必须
    ui.html          # 独立配置页，可选但推荐
    data/
      config.json    # 插件配置，可选
    README.md        # 说明文档，可选
```

**规则**
- 目录名 = 插件名，小写 + 下划线，如 `web_search`
- `__init__.py` 是唯一必须文件
- 所有插件文件放在自己的子目录里，不要散落在 `plugins/` 根目录

---

## 2. 插件规格（PLUGIN_META）

`__init__.py` 顶部必须声明 `PLUGIN_META`：

```python
PLUGIN_META = {
    "name": "my_plugin",           # 与目录名一致
    "version": "0.1.0",
    "description": "一句话描述",
    "author": "你的名字",
    "group_only": False,           # True=仅群聊生效
    "enabled": True,               # 默认启用
    "dependencies": [],            # 依赖的其他插件名
    "hooks": ["before_reply"],     # 注册的钩子
    "priority": 50,                # 数字越小越先执行
    "estimated_tokens": 0,         # 预估消耗 token
    "page": "plugins/my_plugin/ui.html",  # 独立 UI 路径
}
```

### 2.1 可用钩子

| 钩子 | 触发时机 | 返回值 |
|------|----------|--------|
| `before_reply` | AI 生成回复前 | `dict` 或 `None` |
| `after_reply` | 消息发送后 | `dict` 或 `None` |

### 2.2 before_reply 返回值

```python
# 放行，不做任何事
return None

# 注入额外 prompt，AI 会看到这些内容
return {
    "action": "inject",
    "content": "【提示】请记住：用户喜欢猫。",
    "meta": {"trigger": "my_plugin", "key": "value"},
}

# 跳过本次回复
return {
    "action": "skip",
    "reason": "用户没 @ 我，不回。",
}

# 用插件生成的回复覆盖 AI 回复
return {
    "action": "override",
    "reply": "这是插件直接发的消息",
}
```

### 2.3 after_reply 返回值

```python
# 不干预
return None

# 拦截并替换已生成的回复
return {
    "action": "override",
    "reply": "替换后的安全回复",
    "meta": {"reason": "uncertain"},
}
```

---

## 3. 生命周期函数

```python
def on_load():
    """插件启用时调用一次。打印日志、初始化资源。"""
    print(f"[MyPlugin] 已启用")

def on_unload():
    """插件卸载/禁用时调用一次。清理定时器、关闭连接。"""
    print(f"[MyPlugin] 已卸载")

def set_ai_client(client):
    """
    可选。插件管理器会传播 AI 客户端。
    如果你需要插件内部调用 AI，实现这个函数接收 client。
    """
    pass
```

---

## 4. 插件图标

### 4.1 位置

```
plugins/
  my_plugin/
    icon.png        # 32x32 或 64x64，推荐 PNG
```

### 4.2 规范

- 尺寸：`32x32` 或 `64x64`，正方形
- 格式：PNG，透明背景
- 风格：扁平、简洁、高对比度
- 命名：`icon.png`，放在插件根目录
- 如果不提供，前端会显示默认占位图

### 4.3 前端引用

WebUI 加载插件时会自动查找 `plugins/<name>/icon.png`，通过 `/api/plugins/icon?plugin_id=<name>` 访问。

---

## 5. 插件 UI（ui.html）

### 5.1 什么时候需要 UI

- 插件有用户可调配置 → **必须做**
- 插件只做后台逻辑，无配置 → 可省略，但建议做最简展示页

### 5.2 UI 文件位置

```
plugins/
  my_plugin/
    ui.html
```

### 5.3 挂载方式

前端通过 `/api/plugins/page?plugin_id=<name>` 获取 `ui.html`，然后：

```javascript
// 前端自动调用，插件作者不需要写这段
mountPluginPage(container, pluginName);
```

`container` 是页面内容区 DOM，`pluginName` 是插件名。

### 5.4 API 调用规范

插件 UI 里的前端 JS 统一走后端 `/api/plugins/action`：

```javascript
async function api(path, opts={}) {
  const res = await fetch(path, Object.assign({
    headers: {'Content-Type': 'application/json'}
  }, opts));
  return res.json();
}

// 获取插件列表
const data = await api('/api/plugins');

// 切换启用/禁用
await api('/api/plugins/action', {
  method: 'POST',
  body: JSON.stringify({name: 'my_plugin', action: 'toggle'})
});

// 更新配置
await api('/api/plugins/action', {
  method: 'POST',
  body: JSON.stringify({
    name: 'my_plugin',
    action: 'config',
    key: 'my_setting',
    value: true
  })
});
```

### 5.5 UI 风格规范

- **字体**：`"Microsoft YaHei", "PingFang SC", sans-serif`
- **布局**：卡片式，每张卡片一个配置项
- **按钮**：显式 padding、border、圆角，hover 有反馈
- **输入框**：有 border 和圆角，不要浏览器默认样式
- **状态展示**：用文字 + 按钮，不要依赖颜色传递关键信息
- **响应式**：最小宽度 320px，不要横向溢出

### 5.6 最简 UI 模板

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>my_plugin</title>
<style>
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 16px; color: #222; }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 14px; margin-bottom: 10px; }
  .row { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
  .muted { color: #666; font-size: 13px; }
  button { padding: 6px 12px; border: 1px solid #ccc; background: #f7f7f7; border-radius: 6px; cursor: pointer; }
  button:hover { background: #eaeaea; }
</style>
</head>
<body>
  <h2>my_plugin</h2>
  <div class="card">
    <div class="row">
      <div>
        <strong>启用插件</strong>
        <div class="muted">开启后插件才会生效。</div>
      </div>
      <button id="toggleBtn">加载中...</button>
    </div>
  </div>
<script>
async function api(path, opts={}) {
  const res = await fetch(path, Object.assign({headers: {'Content-Type':'application/json'}}, opts));
  return res.json();
}
async function toggle() {
  const data = await api('/api/plugins/action', {
    method:'POST',
    body: JSON.stringify({name:'my_plugin', action:'toggle'})
  });
  document.getElementById('toggleBtn').textContent = data.enabled ? '已启用' : '已禁用';
}
(async function init(){
  const data = await api('/api/plugins');
  const p = (data.plugins || []).find(x => x.name === 'my_plugin');
  if (!p) return;
  document.getElementById('toggleBtn').textContent = p.enabled ? '已启用' : '已禁用';
})();
</script>
</body>
</html>
```

---

## 6. 配置管理

### 6.1 config.json

```
plugins/
  my_plugin/
    data/
      config.json
```

示例：

```json
{
  "enabled": true,
  "intensity": 0.7,
  "max_items": 10
}
```

### 6.2 读写配置

```python
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "data", "config.json")

def _load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_config(data):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# 在插件类或模块级使用
config = _load_config()
```

### 6.3 配置变更通知

如果插件监听了 `set_ai_client`，配置变更通常由 WebUI 后端处理。插件不需要主动监听文件变化，但可以在 `before_reply` / `after_reply` 里实时读取最新配置。

---

## 7. 后端 API 路由

插件相关的后端路由在 `app-src/webui/webui_server.py` 里：

| 路由 | 方法 | 用途 |
|------|------|------|
| `/api/plugins` | GET | 获取所有插件列表 |
| `/api/plugins/page?plugin_id=<name>` | GET | 获取插件 ui.html |
| `/api/plugins/action` | POST | 切换启用/禁用、更新配置 |
| `/api/plugins/icon?plugin_id=<name>` | GET | 获取插件图标 |

**规则**
- 插件不要自己新增后端路由，尽量复用现有 `/api/plugins/*`
- 如果确实需要新路由，必须加在 `app-src/webui/webui_server.py`，并同步到 `webui/` 和 `resources/webui/` 两个副本

---

## 8. 错误处理

插件钩子必须**异常隔离**：

```python
def before_reply(message="", group_id="", context=None, **kwargs):
    try:
        # 你的逻辑
        pass
    except Exception as e:
        print(f"[MyPlugin] 处理失败: {e}")
        return None  # 异常时放行，不要因为插件崩溃导致 bot 不回消息
```

**原则**
- 单个插件崩溃，不能影响其他插件
- 不能因为插件异常导致 bot 主流程中断
- 所有 I/O（文件、网络、数据库）都要 try/except

---

## 9. 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 插件目录 | 小写 + 下划线 | `web_search` |
| 插件名 | 与目录名一致 | `web_search` |
| 类名 | PascalCase | `WebSearchEngine` |
| 函数名 | snake_case | `search_web` |
| 常量 | UPPER_SNAKE_CASE | `DEFAULT_KEYWORDS` |
| 配置键 | snake_case | `max_retries` |

---

## 10. 调试技巧

1. **启动时打印**：在 `on_load()` 里打印版本和配置，确认插件加载
2. **钩子日志**：在 `before_reply` / `after_reply` 里加 `print(f"[MyPlugin] hook 触发: ...")`
3. **临时测试**：写独立脚本 `test_my_plugin.py`，直接 import 插件函数测试
4. **WebUI 查看**：启动 `webui_server.py` 后，插件页面会显示状态和错误

---

## 11. 现有插件参考

| 插件 | 特点 | 适合学习 |
|------|------|----------|
| `board_voice` | 仅 after_reply，文本转换 | 简单钩子 |
| `group_chat_decider` | before + after，AI 决策 | 复杂钩子 + AI |
| `super_memory` | 数据库 + 配置 + 双钩子 | 持久化 + 配置页 |
| `web_search` | 外部依赖 + 异常处理 | 联网 + 降级 |

---

## 12. 禁忌

- **不要修改 `bot_core.py`**，除非用户明确要求
- **不要创建新线程/进程**，除非必要且Properly管理生命周期
- **不要在钩子里阻塞**，所有 I/O 加 timeout
- **不要硬编码 API Key**，用环境变量或配置文件
- **不要无限重试**，最多 2-3 次，避免卡住 bot
- **不要在插件里 import 其他插件的内部模块**，通过 `PLUGIN_META["dependencies"]` 声明依赖

---

## 13. 发布清单

- [ ] `__init__.py` 包含完整 `PLUGIN_META`
- [ ] `ui.html` 路径与 `PLUGIN_META["page"]` 一致
- [ ] 所有钩子函数有 `try/except`
- [ ] `on_load()` / `on_unload()` 有日志
- [ ] `data/config.json` 有默认值
- [ ] 独立测试脚本通过
- [ ] UI 在 320px 宽度下正常显示

---

*最后更新：2026-07-28*
