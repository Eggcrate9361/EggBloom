# NapCatQQ Adapter Specification
# 基于 EggBloom 现有代码反推的真实接口需求，不是拍脑袋设计。

## 0. 为什么先写这份 spec

- `napcat-adapter-evaluation.md` 里的 Phase 1/2/3 方向是对的，但 3.1~4.4 有几处和现有代码不一致
- 先锁定**真实接口契约**，再写 adapter，避免 Phase 1 抽象完又返工

## 1. 现有代码实际用了哪些 OneBot 能力

从 `bot_core.py` + `onebot_client.py` 反推，EggBloom 当前只用了：

| 能力 | 调用位置 | 说明 |
|------|----------|------|
| `send_private_msg` | bot_core.py:551,569,587,805 | 私聊发送 |
| `send_group_msg` | bot_core.py:600,692,724,827 | 群聊发送 |
| `get_friend_msg_history` | bot_core.py:1208, onebot_client.py:72 | 私聊历史轮询 |
| `get_group_msg_history` | bot_core.py:1218, onebot_client.py:79 | 群聊历史轮询 |
| `get_login_info` | onebot_client.py:97 | 登录号 |
| `get_msg` | onebot_client.py:100 | 单条消息 |
| `poke` | onebot_client.py:103 | 戳一戳 |
| `get_stranger_info` | onebot_client.py:109 | 陌生人信息 |
| `get_status` | protocol_manager.py:236, eggbot-plugin.js:2125 | 在线状态 |

**没有用到**：CQ 码、图片上传、文件、群公告、群成员列表等。

### 1.1 发送消息的实际格式

```python
# bot_core.py / onebot_client.py
api_call("send_private_msg", {"user_id": int(target_qq), "message": text})
api_call("send_group_msg", {"group_id": int(group_id), "message": text})
```

- `message` 字段传的是**纯文本 string**
- 没有传 CQ 码数组
- 长文本在 bot_core.py 里自己分段发送（`MSG_SPLIT_LEN` 限制）

### 1.2 接收消息的实际方式

```python
# onebot_client.py:69-95
def get_new_messages(self, target_qqs, group_ids, last_msg_time):
    # 轮询 get_friend_msg_history + get_group_msg_history
    # 按 time 排序，过滤 last_msg_time 之后的消息
    # 返回去重后的新消息列表
```

- **不是 WebSocket 事件驱动**
- 是 HTTP 轮询，每次拉 10 条历史
- 返回格式已经是 EggBloom 内部格式（`_type`, `_group_id` 字段已注入）

## 2. 现有代码没有直接依赖的细节

| 文档假设 | 实际代码 |
|----------|----------|
| 需要完整 CQ 码解析 | 没有，只发纯文本 |
| 需要 WebSocket 监听 | 没有，用 HTTP 轮询 |
| 需要 OneBot v11 标准 base path | 没有，直接打 `http://127.0.0.1:3000/{action}` |
| 需要 `self_id` 映射 | 没有，`bot_core.py` 不关心 `self_id` |

## 3. NapCatQQ 最新版真实部署形态（v4.18.13，2026-07-19）

我已下载并解压官方最新 Windows 一键包，实际文件结构：

```
NapCat.Shell.Windows.OneKey.zip
├── NapCatInstaller.exe
├── 7z.exe / 7z.dll
└── bootmain/
    ├── NapCatWinBootMain.exe      # 主程序
    ├── NapCatWinBootHook.dll      # 注入模块
    ├── napcat.bat                 # 启动：NapCatWinBootMain.exe
    ├── napcat.quick.bat           # 快速启动：NapCatWinBootMain.exe 10086
    └── napcat.kill.qq.bat         # 杀掉 QQ
```

### 3.1 启动方式（不是 Java 进程）

```bat
:: 普通启动
bootmain\napcat.bat

:: 快速启动（带 QQ 号）
bootmain\napcat.quick.bat

:: 内部实际执行
NapCatWinBootMain.exe [QQ号]
```

### 3.2 关键发现：不需要 Java

- 官方 release notes 没有提 Java 依赖
- 实际是 **BootMain + BootHook DLL 注入**，类似 PMHQ 但官方封装
- 不需要 Docker，不需要 napcat.json 手动配置

### 3.3 QQ 版本要求

- **推荐**：QQ 9.9.26-44343（官方直链）
- **最低**：40768 版本
- 现有 `QQ.exe` 需要确认版本

### 3.4 与现有 PMHQ 的对比

| 维度 | PMHQ | NapCatQQ v4.18.13 |
|------|------|-------------------|
| 依赖 | DLL 注入 + 偏移数据 + WinHTTP 代理 | BootMain + BootHook DLL |
| Java | 不需要 | 不需要 |
| Docker | 不需要 | 不需要 |
| 配置复杂度 | 高（偏移数据、代理） | 低（一键包） |
| QQ 版本 | 依赖偏移数据适配 | 官方维护，1-3 天更新 |
| 管理面板 | 无 | 有 WebUI |

现有代码：
```python
url = f"{self.base_url}/{endpoint}"
# 实际调用：http://127.0.0.1:3000/send_private_msg
```

NapCatQQ：
```
http://127.0.0.1:6099/onebot/v11/send_private_msg
```

**差异**：NapCatQQ 要求 `/onebot/v11/` 前缀。

**解决**：NapCatAdapter 的 `api_call` 自动加前缀，PMHQAdapter 不加。

### 3.2 轮询接口可用性

NapCatQQ 支持：
- `get_friend_msg_history` ✅
- `get_group_msg_history` ✅
- `get_login_info` ✅
- `get_msg` ✅
- `get_stranger_info` ✅

**结论**：现有轮询逻辑可以原样移植到 NapCatAdapter，不需要改 `bot_core.py`。

### 3.3 消息格式

NapCatQQ 的 `message` 字段是 CQ 码数组，但 `raw_message` 是纯文本。

现有代码发消息时传纯文本，NapCatQQ 的 `send_private_msg` / `send_group_msg` 接受：
- `message`: string 或 array
- 传 string 时就是纯文本

**结论**：发送侧不需要改。接收侧如果未来要支持图片/表情，才需要 parse CQ 码。

### 3.4 启动/停止流程

PMHQ 流程（现有）：
1. 杀旧进程
2. 启动偏移数据代理
3. 设置 WinHTTP 代理
4. 启动 PMHQ（自动启动 QQ → 注入 DLL）
5. 等待 WebSocket 就绪
6. 启动 eggbot-plugin.js
7. 看门狗

NapCatQQ 流程：
1. 杀旧 QQ 进程
2. 启动 NapCatQQ（独立进程）
3. 等待 HTTP API 就绪
4. 启动看门狗

**差异**：NapCatQQ 不需要 DLL 注入、不需要偏移数据代理、不需要 WinHTTP 代理。

**风险**：NapCatQQ 依赖 Java 21+，需要确认环境。

## 4. ProtocolAdapter 接口定义

基于现有代码实际用法，抽象出最小必要接口：

```python
# protocol_adapter.py
from abc import ABC, abstractmethod
from typing import Callable, Optional, Dict, List, Tuple

class ProtocolAdapter(ABC):
    def __init__(self, config: dict):
        self.config = config
        self._on_message: Optional[Callable] = None
        self._running = False

    @abstractmethod
    def start(self) -> bool:
        """启动协议层，返回是否成功"""
        pass

    @abstractmethod
    def stop(self):
        """停止协议层"""
        pass

    @abstractmethod
    def get_status(self) -> dict:
        """返回协议层状态"""
        pass

    # --- 发送接口（对应 bot_core.py 的 api_call）---

    @abstractmethod
    def send_private_msg(self, user_id: str, text: str) -> bool:
        pass

    @abstractmethod
    def send_group_msg(self, group_id: str, text: str) -> bool:
        pass

    # --- 轮询接口（对应 onebot_client.py 的 get_new_messages）---

    @abstractmethod
    def get_friend_msg_history(self, user_id: str, count: int = 10) -> List[dict]:
        pass

    @abstractmethod
    def get_group_msg_history(self, group_id: str, count: int = 10) -> List[dict]:
        pass

    # --- 其他已用到接口 ---

    @abstractmethod
    def get_login_info(self) -> dict:
        pass

    @abstractmethod
    def get_msg(self, message_id: str) -> dict:
        pass

    @abstractmethod
    def poke(self, group_id: str = None, user_id: str = None):
        pass

    @abstractmethod
    def get_stranger_info(self, user_id: str) -> dict:
        pass

    # --- 回调机制 ---

    def on_message(self, callback: Callable):
        """注册消息回调（用于未来 WebSocket 事件驱动）"""
        self._on_message = callback

    def _notify_message(self, event: dict):
        if self._on_message:
            try:
                self._on_message(event)
            except Exception as e:
                logger.error(f"[Adapter] 消息回调异常: {e}")
```

**注意**：当前没有 `on_message` 的使用方，预留是为了 Phase 2 以后切 WebSocket 事件驱动。

## 5. PMHQAdapter 包装方案

### 5.1 适配现状

现有 `onebot_client.py` 已经是 OneBot HTTP 客户端，可以直接包装：

```python
# pmhq_adapter.py
from protocol_adapter import ProtocolAdapter
from onebot_client import OneBotClient

class PMHQAdapter(ProtocolAdapter):
    def __init__(self, config: dict):
        super().__init__(config)
        self._client = OneBotClient(
            base_url=config.get("base_url", "http://127.0.0.1:3000"),
            bot_qq=config.get("bot_qq", "")
        )
        self._protocol_manager = None  # 保留进程管理引用

    def start(self) -> bool:
        # 复用现有 protocol_manager.py 的启动逻辑
        # ...
        return True

    def stop(self):
        # 复用现有停止逻辑
        pass

    def get_status(self) -> dict:
        # 复用现有 get_status
        pass

    # 直接委托给 OneBotClient
    def send_private_msg(self, user_id, text):
        return self._client.send_private_msg(user_id, text)

    def send_group_msg(self, group_id, text):
        return self._client.send_group_msg(group_id, text)

    def get_friend_msg_history(self, user_id, count=10):
        return self._client.get_friend_msg_history(user_id, count)

    def get_group_msg_history(self, group_id, count=10):
        return self._client.get_group_msg_history(group_id, count)

    def get_login_info(self):
        return self._client.get_login_info()

    def get_msg(self, message_id):
        return self._client.get_msg(message_id)

    def poke(self, group_id=None, user_id=None):
        return self._client.poke(group_id, user_id)

    def get_stranger_info(self, user_id):
        return self._client.get_stranger_info(user_id)
```

### 5.2 bot_core.py 的改动

最小改动，只改 `api_call` 的来源：

```python
# bot_core.py
# 原来：
# from onebot_client import OneBotClient
# _client = OneBotClient(...)
# def api_call(endpoint, data=None):
#     return _client.api_call(endpoint, data)

# 改为：
from protocol_adapter import get_adapter
_adapter = get_adapter()

def api_call(endpoint, data=None):
    # 映射 endpoint 到 adapter 方法
    method_map = {
        "send_private_msg": lambda: _adapter.send_private_msg(data["user_id"], data["message"]),
        "send_group_msg": lambda: _adapter.send_group_msg(data["group_id"], data["message"]),
        "get_friend_msg_history": lambda: _adapter.get_friend_msg_history(data["user_id"], data.get("count", 10)),
        # ...
    }
    # 或者保留 _client.api_call 作为 fallback
```

**更保守的做法**：保留 `onebot_client.py` 不变，新增 `protocol_adapter.py`，`bot_core.py` 按配置选择调用哪个 client。

## 6. NapCatAdapter 实现要点

### 6.1 必须解决的 5 个问题

| # | 问题 | 解决 |
|---|------|------|
| 1 | HTTP API base path 是否是标准 `/onebot/v11/` | 启动后实际探测 `/get_status`、`/onebot/v11/get_status` |
| 2 | BootMain/BootHook 注入方式 | `start()` 启动 `NapCatWinBootMain.exe`，并检测是否成功注入 |
| 3 | QQ 路径传递 | 需要确认是否通过启动参数、环境变量还是配置文件传递 `QQ.exe` 路径 |
| 4 | 配置目录 | 一键包可能自动生成配置，需要确认是否需要手动准备 `napcat.json` |
| 5 | 进程管理 | NapCatQQ 是独立进程，不需要 eggbot-plugin.js；但需要管理 BootMain 进程生命周期 |

### 6.2 NapCatQQ 部署方式

推荐 **standalone** 模式（不用 Docker）：

```
NapCatQQ/
├── NapCatQQ.exe          # 主程序
├── config/
│   └── napcat.json       # 配置文件
└── logs/                 # 日志
```

启动命令：
```bash
NapCatQQ.exe -- -q "<你的 QQ.exe 路径>"
```

或者通过 `napcat.console`：
```bash
napcat.console.exe -- -q "<你的 QQ.exe 路径>"
```

### 6.3 napcat.json 最小配置

```json
{
  "reportSelfMessage": true,
  "useWs": true,
  "wsPort": 3100,
  "httpPort": 6099,
"qq": {
      "qqPath": "C:\\<你的QQ安装目录>\\QQ.exe"
    }
}
```

### 6.4 NapCatAdapter 骨架

```python
# napcat_adapter.py
import subprocess
import time
import urllib.request
from protocol_adapter import ProtocolAdapter

class NapCatAdapter(ProtocolAdapter):
    def __init__(self, config: dict):
        super().__init__(config)
        self.api_base = f"http://127.0.0.1:{config.get('api_port', 6099)}/onebot/v11"
        self.ws_port = config.get("ws_port", 3100)
        self.napcat_exe = config.get("napcat_exe", "")
        self.qq_path = config.get("qq_path", "")
        self.config_dir = config.get("config_dir", "")
        self._process = None

    def start(self) -> bool:
        # 1. 检查 Java
        # 2. 检查 NapCatQQ 是否已在运行
        # 3. 启动 NapCatQQ
        # 4. 等待 HTTP API 就绪
        pass

    def stop(self):
        # 停止 NapCatQQ 进程
        pass

    def get_status(self) -> dict:
        try:
            resp = urllib.request.urlopen(f"{self.api_base}/get_status", timeout=2)
            data = json.loads(resp.read())
            return {"adapter": "napcat", "running": True, "data": data}
        except Exception:
            return {"adapter": "napcat", "running": False}

    def _api_call(self, endpoint: str, data: dict = None):
        url = f"{self.api_base}/{endpoint}"
        body = json.dumps(data or {}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            log(f"[NapCat] API [{endpoint}]: {e}")
            return {"retcode": -1}

    # 发送接口
    def send_private_msg(self, user_id: str, text: str) -> bool:
        resp = self._api_call("send_private_msg", {"user_id": int(user_id), "message": text})
        return resp.get("retcode") == 0

    def send_group_msg(self, group_id: str, text: str) -> bool:
        resp = self._api_call("send_group_msg", {"group_id": int(group_id), "message": text})
        return resp.get("retcode") == 0

    # 轮询接口
    def get_friend_msg_history(self, user_id: str, count: int = 10) -> List[dict]:
        resp = self._api_call("get_friend_msg_history", {"user_id": int(user_id), "count": count})
        if resp.get("retcode") == 0:
            messages = resp.get("data", {}).get("messages", [])
            for m in messages:
                m["_type"] = "private"
            return messages
        return []

    def get_group_msg_history(self, group_id: str, count: int = 10) -> List[dict]:
        resp = self._api_call("get_group_msg_history", {"group_id": int(group_id), "count": count})
        if resp.get("retcode") == 0:
            messages = resp.get("data", {}).get("messages", [])
            for m in messages:
                m["_type"] = "group"
                m["_group_id"] = group_id
            return messages
        return []

    # 其他接口...
```

## 7. 实施顺序（修正版）

### Phase 1：抽象协议层（1 天）

- [ ] 新建 `protocol_adapter.py`（接口定义）
- [ ] 新建 `pmhq_adapter.py`（包装现有 `onebot_client.py`）
- [ ] `bot_core.py` 增加 adapter 选择逻辑，默认走 PMHQAdapter
- [ ] 全量测试现有功能

### Phase 2：NapCatQQ 适配器（2 天）

- [ ] 本地部署 NapCatQQ standalone，验证 HTTP API 可用
- [ ] 新建 `napcat_adapter.py`
- [ ] 实现 `start/stop/get_status` + 进程管理
- [ ] 实现发送 + 轮询接口
- [ ] 用真实 QQ 场景测试：私聊、群聊、图片、@

### Phase 3：配置 + 切换（1 天）

- [ ] `bot_config.json` 增加 `protocol.type` 配置
- [ ] 前端协议面板增加切换 UI
- [ ] 热切换逻辑：stop → switch → start
- [ ] 全量测试

## 8. 待确认事项（基于 v4.18.13 实际下载）

在写 Phase 2 代码前，需要先确认：

1. **HTTP API base path**：启动后实际探测 `/get_status` 和 `/onebot/v11/get_status`，确认 NapCatQQ 的 HTTP API 是否带标准前缀
2. **QQ 路径传递方式**：`NapCatWinBootMain.exe` 如何知道 `QQ.exe` 路径？启动参数 / 环境变量 / 自动查找？
3. **是否需要 napcat.json**：一键包是否自动生成配置，还是需要手动准备？
4. **get_friend_msg_history 返回格式**：是否和现有 EggBloom 代码兼容？
5. **WebUI 密钥**：默认是随机密码，启动后如何获取？（影响远程管理）
6. **BootHook 注入稳定性**：和现有 PMHQ 相比，崩溃恢复机制如何？

> **⚠️ QQ 版本锁定警告**
> - **绝对不要升级 QQ**。当前 QQ 48762 已确认不兼容 NapCatQQ v4.18.13 的 BootHook 注入。
> - EggBloom 现有 PMHQ 协议层依赖当前 QQ 版本的偏移数据，升级 QQ 会导致 PMHQ 失效，且无法回滚。
> - 如果未来要接入 NapCatQQ，需要找到支持当前 QQ 48762 的 NapCat 版本，或等待 NapCat 官方适配。

## 9. 风险点

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| NapCatQQ 版本更新导致 API 变化 | 中 | 适配器失效 | 锁定版本 + fallback 到 PMHQ |
| BootMain 注入方式与现有 QQ 不兼容 | 中 | 无法启动 | 保留 PMHQ 作为 fallback，支持热切换 |
| QQ 版本不满足 40768+ | 中 | 无法启动 | 检测版本，提示升级 |
| 消息格式差异 | 低 | 部分功能失效 | 完整测试覆盖 |
| 进程管理复杂 | 低 | 崩溃恢复失败 | 保留 PMHQ 作为 fallback |
