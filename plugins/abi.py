# -*- coding: utf-8 -*-
"""EggBloom 插件 ABI v1 —— 面向第三方插件的稳定契约。

真正的插件三原则：
1. 不 import 主程序（禁止 `import __main__` / `import bot_core` / 直读 bot_config.json）；
2. 只通过 `ctx`（PluginContext）访问能力（配置、日志、状态、AI、HTTP）；
3. 声明式 `PLUGIN_MANIFEST` + 显式 `PLUGIN_HOOKS`，由管理器做版本/依赖校验。

第三方插件写法（最小示例）：

    from plugins.abi import PluginContext

    PLUGIN_SPEC = 1
    PLUGIN_MANIFEST = {
        "name": "my_plugin",
        "version": "1.0.0",
        "api_version": 1,          # 必须 <= abi.PLUGIN_API_VERSION
        "author": "you",
        "description": "...",
        "dependencies": [],        # 预留：其他插件名
        "priority": 50,            # hook 派发优先级（升序）
        "default_enabled": False,
    }

    def on_message(ctx: PluginContext, event: dict):
        # event: {"type": "group"|"private", "group_id", "sender", "text", "message_id", "time"}
        if (event.get("text") or "").strip() == "!ping":
            return "pong"
        return None   # 不干预

    PLUGIN_HOOKS = {"on_load": on_load, "on_message": on_message}

说明：
- 现有内置插件（PLUGIN_META 老式）不受影响，走旧通道；
- 新插件统一用 PLUGIN_SPEC=1 的 ABI，管理器只对 spec 插件做版本校验与门面注入；
- on_message 返回非空字符串 = 插件回复，由主程序发送并跳过 AI。
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

PLUGIN_API_VERSION = 2

# ── 钩子名（签名见各自文档） ──
HOOK_LOAD = "on_load"                    # (ctx)
HOOK_ENABLE = "on_enable"                # (ctx)
HOOK_DISABLE = "on_disable"              # (ctx)
HOOK_UNLOAD = "on_unload"                # (ctx)
HOOK_MESSAGE = "on_message"              # (ctx, event) -> Optional[str]
HOOK_BEFORE_REPLY = "on_before_reply"    # (ctx, event) -> Optional[dict]
HOOK_AFTER_REPLY = "on_after_reply"      # (ctx, event, reply) -> Optional[str]
HOOK_EVENT = "on_event"                  # (ctx, event_name, payload) -> None


def validate_manifest(manifest: Dict[str, Any]) -> Optional[str]:
    """校验 manifest，返回错误信息或 None。"""
    if not isinstance(manifest, dict):
        return "PLUGIN_MANIFEST 必须是 dict"
    name = manifest.get("name")
    if not name or not str(name).strip():
        return "manifest 缺少 name（插件名，字母数字下划线）"
    try:
        api_version = int(manifest.get("api_version") or 1)
    except (TypeError, ValueError):
        return "api_version 必须是整数"
    if api_version > PLUGIN_API_VERSION:
        return f"api_version {api_version} 超出当前支持的 {PLUGIN_API_VERSION}"
    return None


class PluginContext:
    """插件门面：所有能力都从这里拿，不接触主程序全局状态。"""

    def __init__(self, name: str, version: str = "0.0.0", manager: Any = None):
        self.name = name
        self.version = version
        self._manager = manager
        self._state_file: Optional[str] = None

    # ── 日志：优先走主程序统一日志，不可用时退化 print ──
    def log(self, msg: str, level: str = "info") -> None:
        try:
            from bot_core import log as _bot_log  # 惰性导入，调用时主程序已加载
            _bot_log(f"[Plugin:{self.name}] {msg}")
            return
        except Exception:
            pass
        print(f"[Plugin:{self.name}] [{level}] {msg}")

    # ── 只读配置：与主程序同一份合并配置（默认值 ← 程序目录 ← AppData ← env/凭据管理器）──
    def get_config(self, key: Optional[str] = None, default: Any = None) -> Any:
        try:
            from config import load_config
            cfg = load_config()
            return cfg if key is None else cfg.get(key, default)
        except Exception:
            return default if key is not None else {}

    # ── 插件私有 KV 状态：plugins/state/<name>.json ──
    def _state_path(self) -> str:
        if self._state_file is None:
            from pathlib import Path
            base = Path(__file__).resolve().parent  # plugins/
            d = base / "state"
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            self._state_file = str(d / f"{self.name}.json")
        return self._state_file

    def get_state(self, key: Optional[str] = None, default: Any = None) -> Any:
        try:
            p = self._state_path()
            data = json.loads(open(p, encoding="utf-8").read()) if os.path.exists(p) else {}
            return data.get(key, default) if key is not None else data
        except Exception:
            return default if key is not None else {}

    def set_state(self, key: str, value: Any) -> bool:
        try:
            p = self._state_path()
            data = json.loads(open(p, encoding="utf-8").read()) if os.path.exists(p) else {}
            data[key] = value
            with open(p, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=2))
            return True
        except Exception:
            return False

    # ── 共享 AI 客户端（由主程序注入）──
    def ai_chat_raw(self, messages: List[dict], tools: Optional[list] = None,
                    max_tokens: int = 800):
        """原始返回，交给调用方自己解包。未注入或失败返回 None。

        `AIClient.call()` 的形状是 `(content, tool_calls, usage)` 三元组。
        需要 tool_calls / usage 的调用方用这个；只要正文用 `ai_chat()`。
        """
        try:
            ac = self._manager._ai_client if self._manager else None
            if ac is None:
                self.log("AI 客户端未注入", "warn")
                return None
            return ac.call(messages, tools=tools, max_tokens=max_tokens)
        except Exception as e:
            self.log(f"ai_chat 失败: {e}")
            return None

    def ai_chat(self, messages: List[dict], tools: Optional[list] = None,
                max_tokens: int = 800) -> str:
        """调共享 AI 客户端，**只返回正文文本**；失败返回空串。

        为什么在 ABI 层归一化（2026-10-22）：
          `AIClient.call()` 返回的是三元组 `(content, tool_calls, usage)`，
          而这里原来直接透传，等于把"猜返回形状"的活丢给了每个插件。
          结果 proactive_talk 和 qzone 都按 `str` / `dict` 写解包，
          tuple 一路落到最后的 `return ""` —— **模型明明生成了内容，被静默丢掉**。
          症状是「插件装了但永远不产出」「AI 未产出可用消息」，
          日志里没有异常、没有报错，纯靠单步调试才看得出来。

          归一化放在这里，插件就不用各自兼容；需要完整结构的用 `ai_chat_raw()`。
        """
        r = self.ai_chat_raw(messages, tools=tools, max_tokens=max_tokens)
        if r is None:
            return ""
        # 三元组 / 列表 → 取正文
        if isinstance(r, (tuple, list)):
            if not r:
                return ""
            head = r[0]
            return str(head).strip() if head else ""
        if isinstance(r, str):
            return r.strip()
        if isinstance(r, dict):
            for k in ("content", "text", "reply"):
                if r.get(k):
                    return str(r[k]).strip()
        return ""

    # ── HTTP：requests 优先，urllib 兜底 ──
    def http_get(self, url: str, timeout: float = 15, headers: Optional[dict] = None) -> Optional[dict]:
        return _http("GET", url, timeout=timeout, headers=headers)

    def http_post(self, url: str, json_body: Optional[dict] = None, timeout: float = 15,
                  headers: Optional[dict] = None) -> Optional[dict]:
        return _http("POST", url, timeout=timeout, json_body=json_body, headers=headers)

    # ── 宿主能力：OneBot 调用（ABI v2，由主程序 set_host_api 注入）──
    def _host(self, key: str):
        try:
            return (self._manager._host_api or {}).get(key) if self._manager else None
        except Exception:
            return None

    def api_call(self, action: str, params: Optional[dict] = None) -> Optional[dict]:
        """调用 OneBot 动作 —— 走主程序唯一出口（含 token 与信任处理）。

        SnowLuma 等驱动提供 184 个动作，标准 OneBot v11 之外的扩展动作
        （send_qzone_msg / set_self_longnick / send_group_ai_record …）也由此调用。
        宿主未注入时返回 None —— 插件应据此降级，而不是抛异常。
        """
        fn = self._host("api_call")
        if not callable(fn):
            self.log("api_call 未注入（宿主未提供该能力）", "warn")
            return None
        try:
            return fn(action, params or {})
        except Exception as e:
            self.log(f"api_call {action} 失败: {e}", "warn")
            return None

    def send_group(self, group_id, text: str) -> Optional[dict]:
        """发群消息。"""
        return self.api_call("send_group_msg", {"group_id": str(group_id), "message": text})

    def send_private(self, user_id, text: str) -> Optional[dict]:
        """发私聊消息。"""
        return self.api_call("send_private_msg", {"user_id": str(user_id), "message": text})

    # ── 事件广播：真总线（ABI v2）──
    def emit(self, event_name: str, **payload: Any) -> list:
        """广播事件给其他插件。

        订阅方在自己的 PLUGIN_HOOKS 里声明：
            def on_event(ctx, event_name, payload): ...
        返回 [(插件名, 返回值)]；无人订阅时返回 []。
        """
        if not self._manager:
            return []
        try:
            return self._manager.emit_event(event_name, payload, source=self.name)
        except Exception as e:
            self.log(f"emit {event_name} 失败: {e}", "warn")
            return []


def _http(method: str, url: str, timeout: float = 15, json_body: Optional[dict] = None,
          headers: Optional[dict] = None):
    try:
        import requests  # type: ignore
        kw = {"timeout": timeout, "headers": headers or {}}
        if method == "POST":
            kw["json"] = json_body or {}
        resp = requests.request(method, url, **kw)
        try:
            return resp.json()
        except Exception:
            return {"status": resp.status_code, "text": resp.text[:500]}
    except Exception:
        pass
    try:
        import urllib.request
        import json as _json
        data = _json.dumps(json_body).encode("utf-8") if json_body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
        try:
            return _json.loads(raw)
        except Exception:
            return {"status": getattr(r, "status", 200), "text": raw[:500]}
    except Exception:
        return None
