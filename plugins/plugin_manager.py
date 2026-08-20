# -*- coding: utf-8 -*-
"""
EggBloom 插件管理器

功能：
  - 自动发现 plugins/ 目录下的插件子包
  - 按 PLUGIN_META 注册表加载/卸载/启停
  - 按 priority 派发 hooks（before_reply, after_reply 等）
  - 错误隔离：单个插件异常不影响其他插件
  - 状态持久化到 plugin_state.json
  - 向插件传播 AI 客户端（set_ai_client）

使用示例：
    from plugins.plugin_manager import init_plugin_manager, get_plugin_manager
    pm = init_plugin_manager()
    pm.load_all()
    pm.set_ai_client(ai_client)

    result = pm.execute_hook("before_reply", message=msg, group_id=gid, context={})
"""

import importlib
import json
import os
import sys
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── 路径 ──
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(PLUGIN_DIR, "plugin_state.json")

# API 状态检测缓存 TTL（秒）。检测涉及网络/浏览器启动，不能每次轮询都打
_API_STATUS_CACHE_TTL = 60


def _noop(*args, **kwargs):
    return None


class PluginManager:
    """插件管理器（单例）"""

    _instance: Optional["PluginManager"] = None

    def __init__(self):
        self._plugins: Dict[str, dict] = {}           # name → {module, meta, hooks, error?}
        self._ai_client: Any = None
        self._state: Dict[str, bool] = {}              # name → enabled
        self._load_state()

    # ── 单例 ──────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "PluginManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 发现 ──────────────────────────────────────────────

    def discover(self) -> List[str]:
        """扫描 plugins/ 的子目录，返回含 PLUGIN_META 的包名列表"""
        found = []
        if not os.path.isdir(PLUGIN_DIR):
            return found
        for name in sorted(os.listdir(PLUGIN_DIR)):
            pkg_path = os.path.join(PLUGIN_DIR, name)
            init_file = os.path.join(pkg_path, "__init__.py")
            if name.startswith("_") or name.startswith("."):
                continue
            if not os.path.isdir(pkg_path) or not os.path.isfile(init_file):
                continue
            if name == "group_chat_decider":
                found.append(name)
            else:
                found.append(name)
        return found

    # ── 加载 / 卸载 ─────────────────────────────────────────

    def load(self, name: str) -> bool:
        """加载指定插件，注册到注册表。返回是否成功。"""
        try:
            if name in sys.modules:
                mod = sys.modules[name]
            else:
                mod = importlib.import_module(f"plugins.{name}")

            meta = getattr(mod, "PLUGIN_META", None)
            if not meta:
                self._plugins[name] = {"error": "缺少 PLUGIN_META", "module": mod, "meta": {}}
                return False

            meta["name"] = meta.get("name", name)

            # 收集 hooks
            hooks: Dict[str, Callable] = {}
            for hook_name in meta.get("hooks", []):
                hook_func = getattr(mod, hook_name, None)
                if callable(hook_func):
                    hooks[hook_name] = hook_func

            # 合并持久化状态
            enabled = self._state.get(name, meta.get("enabled", True))
            meta["enabled"] = enabled

            self._plugins[name] = {
                "module": mod,
                "meta": dict(meta),
                "hooks": hooks,
                "error": None,
            }

            # 传播 AI 客户端
            self._propagate_ai_client(name)

            # 调用 on_load
            if enabled:
                on_load = getattr(mod, "on_load", None)
                if callable(on_load):
                    on_load()

            return True
        except Exception as e:
            self._plugins[name] = {
                "module": None,
                "meta": {"name": name, "version": "?", "description": str(e)},
                "hooks": {},
                "error": f"加载失败: {traceback.format_exc()}",
            }
            return False

    def load_all(self) -> int:
        """发现并加载所有插件。返回成功加载的数量。"""
        count = 0
        for name in self.discover():
            ok = self.load(name)
            if ok:
                count += 1
        return count

    def unload(self, name: str) -> bool:
        """卸载插件。调用 on_unload 并从注册表移除。"""
        entry = self._plugins.get(name)
        if not entry:
            return False
        mod = entry.get("module")
        if mod:
            on_unload = getattr(mod, "on_unload", None)
            if callable(on_unload):
                try:
                    on_unload()
                except Exception as e:
                    print(f"[Plugin:{name}] on_unload 异常: {e}")
        self._plugins.pop(name, None)
        return True

    # ── 启停 ──────────────────────────────────────────────

    def enable(self, name: str) -> bool:
        """启用插件。持久化状态。"""
        entry = self._plugins.get(name)
        if not entry:
            return False
        entry["meta"]["enabled"] = True
        self._state[name] = True
        self._save_state()
        mod = entry.get("module")
        if mod:
            on_load = getattr(mod, "on_load", None)
            if callable(on_load):
                try:
                    on_load()
                except Exception as e:
                    print(f"[Plugin:{name}] on_load 异常: {e}")
        return True

    def disable(self, name: str) -> bool:
        """禁用插件。持久化状态。"""
        entry = self._plugins.get(name)
        if not entry:
            return False
        entry["meta"]["enabled"] = False
        self._state[name] = False
        self._save_state()
        mod = entry.get("module")
        if mod:
            on_unload = getattr(mod, "on_unload", None)
            if callable(on_unload):
                try:
                    on_unload()
                except Exception as e:
                    print(f"[Plugin:{name}] on_unload 异常: {e}")
        return True

    def reload(self, name: str) -> bool:
        """重新加载插件。"""
        self.unload(name)
        # 清理 sys.modules 缓存
        for key in list(sys.modules.keys()):
            if key.startswith(f"plugins.{name}") or key == name:
                sys.modules.pop(key, None)
        return self.load(name)

    # ── 信息查询 ──────────────────────────────────────────

    def is_loaded(self, name: str) -> bool:
        return name in self._plugins

    def is_enabled(self, name: str) -> bool:
        entry = self._plugins.get(name)
        return bool(entry and entry["meta"].get("enabled", False))

    def get_meta(self, name: str) -> dict:
        entry = self._plugins.get(name)
        return dict(entry["meta"]) if entry else {}

    def get_error(self, name: str) -> Optional[str]:
        entry = self._plugins.get(name)
        return entry.get("error") if entry else None

    def list_plugins(self) -> List[dict]:
        """返回所有已知插件的元信息列表（含状态）。"""
        self._collect_api_status()
        result = []
        for name, entry in self._plugins.items():
            meta = dict(entry["meta"])
            meta["_loaded"] = entry["module"] is not None
            meta["_error"] = entry.get("error")
            meta["_hooks"] = list(entry.get("hooks", {}).keys())
            meta["_api_status"] = entry.get("api_status")
            mod = entry.get("module")
            meta["_api_capable"] = bool(mod) and callable(getattr(mod, "get_api_status", None))
            result.append(meta)
        return result

    def check_plugin_api(self, name: str) -> dict:
        """强制刷新指定插件的 API 连通状态（绕过 60s 缓存）。

        返回插件 get_api_status() 的结果（形如 {"ok", "status", "detail"}），
        或 {"ok": False, "error": ...}。
        """
        entry = self._plugins.get(name)
        if not entry:
            return {"ok": False, "error": f"插件 {name} 不存在"}
        mod = entry.get("module")
        if not mod:
            return {"ok": False, "error": f"插件 {name} 未加载"}
        fn = getattr(mod, "get_api_status", None)
        if not callable(fn):
            return {"ok": False, "error": f"插件 {name} 未实现 get_api_status()"}
        try:
            status = fn() or {}
        except Exception as e:
            status = {"ok": False, "status": f"检测异常: {e}", "detail": str(e)}
        if not isinstance(status, dict):
            status = {"ok": bool(status), "status": str(status), "detail": str(status)}
        entry["api_status"] = status
        entry["_api_status_cache"] = (time.time(), status)
        return status

    # ── API 连接监测 ─────────────────────────────────────────

    def _collect_api_status(self):
        """对启用的插件调用 get_api_status()（若实现），结果带缓存写入 entry。

        约定：插件模块可选实现 `get_api_status() -> dict`，
        返回形如 {"ok": bool, "status": str, "detail": str}。
        未实现该函数的插件 api_status 为 None（表示"无需 API"）。
        """
        now = time.time()
        for name, entry in self._plugins.items():
            mod = entry.get("module")
            if not mod or not entry["meta"].get("enabled", False):
                entry["api_status"] = None
                entry["_api_status_cache"] = None
                continue
            fn = getattr(mod, "get_api_status", None)
            if not callable(fn):
                entry["api_status"] = None
                entry["_api_status_cache"] = None
                continue
            cached = entry.get("_api_status_cache")
            if cached and now - cached[0] < _API_STATUS_CACHE_TTL:
                entry["api_status"] = cached[1]
                continue
            try:
                status = fn()
            except Exception as e:
                status = {"ok": False, "status": f"检测异常: {e}", "detail": str(e)}
            entry["api_status"] = status
            entry["_api_status_cache"] = (now, status)

    def has_hook(self, hook_name: str) -> bool:
        """是否有启用的插件注册了指定钩子。"""
        for entry in self._plugins.values():
            if entry["meta"].get("enabled", False) and hook_name in entry.get("hooks", {}):
                return True
        return False

    # ── 钩子派发 ──────────────────────────────────────────

    def execute_hook(self, hook_name: str, **kwargs) -> Any:
        """
        按 priority 升序执行所有已启用插件的指定钩子。
        返回首个非 None 结果（短路），或 None。
        异常安全：单个插件崩溃不影响其他插件。
        """
        candidates = []
        for name, entry in self._plugins.items():
            if not entry["meta"].get("enabled", False):
                continue
            hook_func = entry["hooks"].get(hook_name)
            if not hook_func:
                continue
            priority = entry["meta"].get("priority", 50)
            candidates.append((priority, name, hook_func))

        candidates.sort(key=lambda x: x[0])  # priority 升序

        for priority, name, hook_func in candidates:
            try:
                result = hook_func(**kwargs)
                if result is not None:
                    return result
            except Exception as e:
                print(f"[Plugin:{name}] hook {hook_name} 异常: {e}")
                traceback.print_exc()
                continue

        return None

    def execute_hooks(self, hook_name: str, **kwargs) -> List[Tuple[str, Any]]:
        """
        执行指定钩子，返回所有非 None 结果。
        结果按 (plugin_name, result) 组织，保持 priority 升序。
        """
        results: List[Tuple[str, Any]] = []
        candidates = []
        for name, entry in self._plugins.items():
            if not entry["meta"].get("enabled", False):
                continue
            hook_func = entry["hooks"].get(hook_name)
            if not hook_func:
                continue
            priority = entry["meta"].get("priority", 50)
            candidates.append((priority, name, hook_func))

        candidates.sort(key=lambda x: x[0])

        for priority, name, hook_func in candidates:
            try:
                result = hook_func(**kwargs)
                if result is not None:
                    results.append((name, result))
            except Exception as e:
                print(f"[Plugin:{name}] hook {hook_name} 异常: {e}")
                traceback.print_exc()
                continue

        return results

    # ── AI 客户端传播 ──────────────────────────────────────

    def set_ai_client(self, client: Any):
        """设置 AI 客户端并传播给所有已加载插件。"""
        self._ai_client = client
        for name in list(self._plugins.keys()):
            self._propagate_ai_client(name)

    def _propagate_ai_client(self, name: str):
        """向单个插件传播 AI 客户端。"""
        entry = self._plugins.get(name)
        if not entry or not entry["module"]:
            return
        set_fn = getattr(entry["module"], "set_ai_client", None)
        if callable(set_fn):
            try:
                set_fn(self._ai_client)
            except Exception as e:
                print(f"[Plugin:{name}] set_ai_client 异常: {e}")

    # ── 状态持久化 ─────────────────────────────────────────

    def _load_state(self):
        """从 plugin_state.json 加载启停状态。"""
        try:
            if os.path.isfile(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._state = data.get("enabled", data.get("已启用", {}))
            else:
                self._state = {}
        except Exception:
            self._state = {}

    def _save_state(self):
        """保存启停状态到 plugin_state.json。"""
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"enabled": self._state, "已启用": self._state}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PluginManager] 保存状态失败: {e}")


# ── 模块级便捷函数 ─────────────────────────────────────────

_manager_initialized = False


def init_plugin_manager() -> PluginManager:
    """初始化插件管理器（首次调用后返回单例）。"""
    global _manager_initialized
    pm = PluginManager.get_instance()
    _manager_initialized = True
    return pm


def get_plugin_manager() -> Optional[PluginManager]:
    """获取插件管理器单例。未初始化时返回 None。"""
    global _manager_initialized
    if not _manager_initialized:
        return None
    return PluginManager._instance