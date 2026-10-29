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
import io
import re
import json
import os
import sys
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── 路径 ──
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(PLUGIN_DIR, "plugin_state.json")

# API 状态检测缓存 TTL（秒）。检测涉及网络/浏览器启动，不能每次轮询都打
_API_STATUS_CACHE_TTL = 60

# ── 钩子三档拦截语义（T7 批一 ⑤ P3 · 对应 dsh-tools）──
# ⚠️ 刻意不叫 tier：本文件里 `tier` 已表示插件 ABI 形态（spec / legacy），复用会混淆。
#   observe   只读，返回值忽略，异常隔离 —— 观测类插件永远不改主流程
#   guard     单调，拒绝后下游无法推翻 —— 有副作用插件的安全根
#   waterfall 可重排，链式传递（上一环返回值经 carry 进下一环）
HOOK_SEMANTICS = ("waterfall", "guard", "observe")

# ── P14：卸载 fail-closed（T7 批二 · 不变量 A4/A14）──────────────────────
# 现状问题：`unload()` 原先直接调 on_unload 并摘掉注册表 —— 若此刻还有钩子
# 正在执行（web_operator / scheduler 这类有副作用插件），它的线程就被悬在半空：
# 模块对象没了、日志写不进去、`dispose` 从未"完全停稳"。
# 对策：**每个钩子调用计入在途**；卸载时若有在途且未强制 → **拒绝卸载**
# （失败关闭：宁可拒绝，也不静默放行一个不安全的卸载）。
_INFLIGHT: Dict[str, int] = {}
_INFLIGHT_CV = threading.Condition(threading.Lock())


#: 1.5.10 F1：插件源码里出现这些直连发送接口 → 拒绝加载。
#: 它们绕过 outbound 队列（串行/限频/去重/日志），等于把 §3 立的闸推倒。
_FORBIDDEN_SEND_RE = re.compile(
    r"send_(?:private|group)_msg|/send_private_msg|/send_group_msg"
)


def scan_forbidden_send(plugin_dir: str):
    """扫插件目录里的 .py，返回 [(相对路径, 行号, 行内容)]。空 = 通过。"""
    hits = []
    for root, dirs, files in os.walk(plugin_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "data", "assets")]
        for f in files:
            if not f.endswith(".py"):
                continue
            p = os.path.join(root, f)
            try:
                text = io.open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            # 只查**代码**：注释与文档字符串里提到旧接口名不算违规
            # （否则一份说明文档就能让插件加载失败 —— 反而逼人删注释）。
            Q = chr(34) * 3
            QS = chr(39) * 3
            in_doc = None
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if in_doc:
                    if in_doc in stripped:
                        in_doc = None
                    continue
                if stripped.startswith("#"):
                    continue
                opened = None
                for quote in (Q, QS):
                    if stripped.startswith(quote):
                        # 文档字符串行：单行（开头即结尾）直接跳过；
                        # 多行则记住状态，直到闭合行
                        if stripped.count(quote) == 1:
                            opened = quote
                        break
                if opened:
                    in_doc = opened
                    continue
                if stripped.startswith(Q) or stripped.startswith(QS):
                    continue
                if _FORBIDDEN_SEND_RE.search(line):
                    hits.append((os.path.relpath(p, plugin_dir), lineno, stripped[:120]))
    return hits


def _noop(*args, **kwargs):
    return None


class PluginManager:
    """插件管理器（单例）"""

    _instance: Optional["PluginManager"] = None

    def __init__(self):
        self._plugins: Dict[str, dict] = {}           # name → {module, meta, hooks, error?}
        # 1.5.10 H1：插件清单（plugins/<id>/plugin.json）。只读层，未接入任何运行行为。
        self._manifests: Dict[str, dict] = {}
        self._manifest_errors: Dict[str, list] = {}
        self._manifest_conflicts: dict = {}
        self._ai_client: Any = None
        self._host_api: Dict[str, Any] = {}            # ABI v2：宿主注入的能力（api_call 等）
        self._emit_depth: int = 0                      # 事件总线递归保护
        self._state: Dict[str, bool] = {}              # name → enabled
        self._load_state()

    # ── 单例 ──────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "PluginManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 发现 ──────────────────────────────────────────────

    # ego 的子模块在 plugins/ 顶层留有历史副本（内容与 ego 内部的有差异，
    # 但它们的功能由 ego 插件统一提供）。它们不该以"独立插件"身份出现，
    # 否则插件列表里会多出一排同名项。这里跳过枚举；**文件保留不删**，
    # 万一有直接 import 依赖，模块本身仍可被 import。
    EGO_SUBMODULE_COPIES = frozenset({
        "chat_style", "humanlike_rhythm", "mood_engine",
        "personality_engine", "slang_learner", "super_memory",
    })

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
            if name in self.EGO_SUBMODULE_COPIES:
                continue
            if name == "group_chat_decider":
                found.append(name)
            else:
                found.append(name)
        return found

    # ── 加载 / 卸载 ─────────────────────────────────────────

    def load(self, name: str) -> bool:
        """加载指定插件，注册到注册表。返回是否成功。

        支持两种形态：
        - 老式内置插件：PLUGIN_META + hooks（进程内强依赖模块）
        - 真插件 ABI：PLUGIN_SPEC=1 + PLUGIN_MANIFEST + PLUGIN_HOOKS（只经 ctx 访问能力）
        """
        try:
            if name in sys.modules:
                mod = sys.modules[name]
            else:
                mod = importlib.import_module(f"plugins.{name}")

            # 1.5.10 F1：直连发送 = 拒绝加载（先查源码再谈 manifest）
            try:
                _hit = scan_forbidden_send(os.path.join(PLUGIN_DIR, name))
            except Exception:
                _hit = []
            if _hit:
                _p, _ln, _txt = _hit[0]
                self._plugins[name] = {
                    "module": mod, "meta": {"name": name, "version": "?"},
                    "hooks": {}, "error": f"直连发送接口被拒（{_p}:{_ln} {_txt}）",
                }
                print(f"[Plugin:{name}] 拒绝加载：直连发送接口 {_p}:{_ln} —— 请改 ctx.send_text()")
                return False

            spec = getattr(mod, "PLUGIN_SPEC", None)
            if spec is not None:
                return self._load_spec(name, mod)

            meta = getattr(mod, "PLUGIN_META", None)
            if not meta:
                self._plugins[name] = {"error": "缺少 PLUGIN_META 或 PLUGIN_SPEC", "module": mod, "meta": {}}
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
        self._reload_manifests()
        count = 0
        for name in self.discover():
            ok = self.load(name)
            if ok:
                count += 1
        self._check_manifest_conflicts()
        return count

    # ── 插件清单（1.5.10 H1/H2，只读）──────────────────────────
    def _reload_manifests(self) -> None:
        """读 plugins/<id>/plugin.json；无清单的老插件照常加载（兼容层）。"""
        try:
            from plugins import plugin_manifest as _pmf
        except Exception as e:
            print(f"[PluginManifest] 读取层不可用: {e}")
            return
        self._manifests, self._manifest_errors = {}, {}
        for name in self.discover():
            m, errs = _pmf.load(os.path.join(PLUGIN_DIR, name), fallback_id=name)
            if m:
                self._manifests[name] = m
            if errs:
                self._manifest_errors[name] = errs
                print(f"[PluginManifest] {name} 清单无效（按无清单处理）: {'; '.join(errs)}")
        if self._manifests:
            print(f"[PluginManifest] 已读取 {len(self._manifests)} 份清单: "
                  f"{sorted(self._manifests)}")

    def _check_manifest_conflicts(self) -> None:
        """provides/requires 冲突检查：**只警告不拒绝**（1.6.0 才改成拒绝加载）。"""
        try:
            from plugins import plugin_manifest as _pmf
            self._manifest_conflicts = _pmf.check(self._manifests)
        except Exception as e:
            print(f"[PluginManifest] 冲突检查失败（已忽略）: {e}")
            return
        c = self._manifest_conflicts
        for cap, ids in (c.get("duplicate_provides") or {}).items():
            print(f"[PluginManifest] 能力冲突：{cap} 被多个插件声明 → {ids}")
        for pid, cap in (c.get("missing_requires") or []):
            print(f"[PluginManifest] 缺失依赖：{pid} 需要 {cap}，但没有插件提供")
        for cyc in (c.get("cycles") or []):
            print(f"[PluginManifest] 依赖环：{' → '.join(cyc)}")

    def get_manifests(self) -> Dict[str, dict]:
        return dict(self._manifests)

    def get_manifest_report(self) -> dict:
        return {
            "manifests": dict(self._manifests),
            "errors": dict(self._manifest_errors),
            "conflicts": dict(self._manifest_conflicts),
        }

    # ── 真插件 ABI（PLUGIN_SPEC=1）────────────────────────

    def _load_spec(self, name: str, mod) -> bool:
        """加载 ABI 插件：校验 manifest、构建 ctx、注册类型化 hooks、触发 on_load。"""
        from plugins.abi import PluginContext, validate_manifest, PLUGIN_API_VERSION
        manifest = dict(getattr(mod, "PLUGIN_MANIFEST", None) or {})
        err = validate_manifest(manifest)
        if err:
            self._plugins[name] = {
                "module": mod, "tier": "spec", "meta": {"name": name, "version": "?", "description": err},
                "hooks": {}, "ctx": None, "error": f"manifest 校验失败: {err}",
            }
            return False
        hooks = dict(getattr(mod, "PLUGIN_HOOKS", None) or {})
        pname = str(manifest.get("name") or name)
        pversion = str(manifest.get("version") or "0.0.0")
        enabled = self._state.get(name, bool(manifest.get("default_enabled", True)))
        ctx = PluginContext(name=pname, version=pversion, manager=self)
        self._plugins[name] = {
            "module": mod,
            "tier": "spec",
            "manifest": manifest,
            "meta": {"name": pname, "version": pversion, "enabled": enabled,
                     "description": manifest.get("description", ""),
                     "author": manifest.get("author", ""),
                     "priority": int(manifest.get("priority") or 50)},
            "hooks": {k: v for k, v in hooks.items() if callable(v)},
            "ctx": ctx,
            "error": None,
        }
        self._propagate_ai_client(name)
        if enabled:
            self._call_spec_hook(name, "on_load", ctx)
        return True

    def _call_spec_hook(self, name: str, hook_name: str, ctx, **kwargs) -> Any:
        """调用单个 spec 插件的钩子（异常隔离）。"""
        entry = self._plugins.get(name)
        if not entry or entry.get("tier") != "spec":
            return None
        fn = (entry.get("hooks") or {}).get(hook_name)
        if not callable(fn):
            return None
        try:
            return fn(ctx, **kwargs) if kwargs else fn(ctx)
        except Exception as e:
            print(f"[Plugin:{name}] {hook_name} 异常: {e}")
            return None

    def has_typed_hook(self, hook_name: str) -> bool:
        """是否有已启用的 spec 插件注册了指定类型化钩子。"""
        return any(
            e.get("tier") == "spec" and e.get("meta", {}).get("enabled")
            and hook_name in (e.get("hooks") or {})
            for e in self._plugins.values()
        )

    def execute_typed_hooks_first(self, hook_name: str, **kwargs) -> Any:
        """按 priority 升序调用启用 spec 插件的钩子，返回第一个非 None 结果。"""
        candidates = []
        for name, e in self._plugins.items():
            if e.get("tier") != "spec" or not e.get("meta", {}).get("enabled"):
                continue
            fn = (e.get("hooks") or {}).get(hook_name)
            if callable(fn):
                candidates.append((int(e.get("meta", {}).get("priority") or 50), name, fn, e.get("ctx")))
        candidates.sort(key=lambda x: x[0])
        for _, name, fn, ctx in candidates:
            try:
                r = fn(ctx, **kwargs) if kwargs else fn(ctx)
                if r is not None:
                    return r
            except Exception as e:
                print(f"[Plugin:{name}] {hook_name} 异常: {e}")
        return None

    def execute_typed_hooks(self, hook_name: str, **kwargs) -> List[Tuple[str, Any]]:
        """调用所有启用 spec 插件的钩子，返回 [(plugin_name, result)]。"""
        results = []
        candidates = []
        for name, e in self._plugins.items():
            if e.get("tier") != "spec" or not e.get("meta", {}).get("enabled"):
                continue
            fn = (e.get("hooks") or {}).get(hook_name)
            if callable(fn):
                candidates.append((int(e.get("meta", {}).get("priority") or 50), name, fn, e.get("ctx")))
        candidates.sort(key=lambda x: x[0])
        for _, name, fn, ctx in candidates:
            try:
                results.append((name, fn(ctx, **kwargs) if kwargs else fn(ctx)))
            except Exception as e:
                print(f"[Plugin:{name}] {hook_name} 异常: {e}")
        return results

    def unload(self, name: str, force: bool = False) -> bool:
        """卸载插件。调用 on_unload 并从注册表移除。

        **P14 fail-closed（不变量 A4/A14）**：若该插件还有钩子在途未结束，
        默认**拒绝卸载**并返回 False —— 宁可拒绝，也不静默放行一次会把
        在途调用悬在半空（模块已摘、日志写不进、dispose 从未"完全停稳"）的卸载。
        确需强卸传 `force=True`；想先等它跑完用 `wait_idle()` / `unload_ex(wait=..)`。
        """
        entry = self._plugins.get(name)
        if not entry:
            return False
        _n = self.inflight_count(name)
        if _n > 0 and not force:
            print(f"[Plugin:{name}] 拒绝卸载：{_n} 个在途钩子未结束"
                  f"（fail-closed；确需强卸请传 force=True）")
            return False
        mod = entry.get("module")
        if mod:
            if entry.get("tier") == "spec":
                self._call_spec_hook(name, "on_unload", entry.get("ctx"))
            else:
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
            if entry.get("tier") == "spec":
                self._call_spec_hook(name, "on_enable", entry.get("ctx"))
            else:
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
            if entry.get("tier") == "spec":
                self._call_spec_hook(name, "on_disable", entry.get("ctx"))
            else:
                on_unload = getattr(mod, "on_unload", None)
                if callable(on_unload):
                    try:
                        on_unload()
                    except Exception as e:
                        print(f"[Plugin:{name}] on_unload 异常: {e}")
        return True

    def unload_ex(self, name: str, force: bool = False, wait: float = 0.0) -> Dict[str, Any]:
        """卸载的**显式结果版**（给 UI / 排查用）：返回 {ok, reason, inflight}。

        wait>0：先等在途结束（最多 wait 秒）；超时且未 force → 仍拒绝（fail-closed）。
        """
        if name not in self._plugins:
            return {"ok": False, "reason": "未找到该插件", "inflight": 0}
        if wait and float(wait) > 0:
            self.wait_idle(name, float(wait))
        n = self.inflight_count(name)
        if n > 0 and not force:
            return {"ok": False, "inflight": n,
                    "reason": f"有 {n} 个在途钩子未结束，拒绝卸载（fail-closed）"}
        ok = self.unload(name, force=True)     # 检查已在上方做过
        return {"ok": bool(ok), "inflight": 0,
                "reason": "" if ok else "卸载失败（on_unload 未成功）"}

    def reload(self, name: str) -> bool:
        """重新加载插件。**P14**：若卸载被拒（有在途调用）则中止重载，返回 False。"""
        if name in self._plugins and not self.unload(name):
            print(f"[Plugin:{name}] 拒绝重载：卸载未完成（fail-closed）")
            return False
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
            # 1.5.10 H1：清单字段（无清单 = None，前端/API 可据此区分旧插件）
            _mf = (self._manifests or {}).get(name)
            meta["_manifest"] = _mf
            meta["_provides"] = list((_mf or {}).get("provides") or [])
            meta["_requires"] = list((_mf or {}).get("requires") or [])
            meta["_budget"] = dict((_mf or {}).get("budget") or {})
            meta["_permissions"] = list((_mf or {}).get("permissions") or [])
            meta["_scope"] = (_mf or {}).get("scope")
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

    def _invoke_hook(self, name: str, hook_func: Callable, kwargs: dict):
        """调用一次钩子并**计入在途**（供卸载的 fail-closed 判断，不变量 A14）。"""
        with _INFLIGHT_CV:
            _INFLIGHT[name] = _INFLIGHT.get(name, 0) + 1
        try:
            return hook_func(**kwargs)
        finally:
            with _INFLIGHT_CV:
                _INFLIGHT[name] = max(0, _INFLIGHT.get(name, 1) - 1)
                _INFLIGHT_CV.notify_all()

    def inflight_count(self, name: str) -> int:
        """该插件当前正在执行的钩子数（0 = 完全静止）。"""
        with _INFLIGHT_CV:
            return int(_INFLIGHT.get(name, 0))

    def wait_idle(self, name: str, timeout: float = 5.0) -> bool:
        """等待该插件静止。返回是否已静止（超时返回 False，调用方自行决定拒还是强卸）。"""
        deadline = time.time() + max(0.0, float(timeout))
        with _INFLIGHT_CV:
            while _INFLIGHT.get(name, 0) > 0:
                remain = deadline - time.time()
                if remain <= 0:
                    return False
                _INFLIGHT_CV.wait(remain)
            return True

    def _hook_candidates(self, hook_name: str):
        """钩子候选，按 priority 升序。execute_hooks 与三档语义共用同一筛选，
        避免两套筛选各自漂移（这正是 N2 类问题的成因）。"""
        candidates = []
        for name, entry in self._plugins.items():
            if not entry["meta"].get("enabled", False):
                continue
            hook_func = entry["hooks"].get(hook_name)
            if not hook_func:
                continue
            candidates.append((entry["meta"].get("priority", 50), name, hook_func))
        candidates.sort(key=lambda x: x[0])
        return candidates

    def execute_hooks(self, hook_name: str, **kwargs) -> List[Tuple[str, Any]]:
        """
        执行指定钩子，返回所有非 None 结果。
        结果按 (plugin_name, result) 组织，保持 priority 升序。

        （旧语义：只做「收集」。三档拦截语义见 execute_hooks_semantic。）
        """
        results: List[Tuple[str, Any]] = []
        for priority, name, hook_func in self._hook_candidates(hook_name):
            try:
                result = self._invoke_hook(name, hook_func, kwargs)
                if result is not None:
                    results.append((name, result))
            except Exception as e:
                print(f"[Plugin:{name}] hook {hook_name} 异常: {e}")
                traceback.print_exc()
                continue

        return results

    def execute_hook_chain(self, hook_name: str, text_key: str = "reply", **kwargs):
        """链式执行 after_reply 类钩子，返回 (最终文本, 改写过它的插件名列表)。

        为什么需要它（2026-10-21）：
          `execute_hook`（单数）是**首个非 None 即返回**，配合 priority 升序，
          意味着 ego(20) 只要返回 override，排在它后面的 web_search(30)
          幻觉拦截就永远跑不到 —— 一个插件把另一个插件静默顶掉。
          `execute_hooks`（复数）虽然全跑，但彼此独立、看不到前一个的改写结果，
          没法做「人格删违禁句 → 联网插件拦幻觉」这种叠加。

          这里逐个执行，**后一个插件看到的是前一个改完的文本**，
          所以改写能链式叠加，且不会被高优先级插件短路。

        契约：每个 hook 返回 {"action": "override", "reply": <新文本>} 才认；
        返回其他/None 视为"无改写"，文本原样传给下一个。
        """
        cur = kwargs.get(text_key, "") or ""
        changed_by = []
        for priority, name, hook_func in self._hook_candidates(hook_name):
            call_kwargs = dict(kwargs)
            call_kwargs[text_key] = cur
            try:
                r = self._invoke_hook(name, hook_func, call_kwargs)
            except Exception as e:
                print(f"[Plugin:{name}] hook {hook_name} 异常: {e}")
                continue
            if isinstance(r, dict) and r.get("action") == "override":
                new_text = r.get(text_key, "")
                # 空串是"抑制发送"的合法契约，但只在**原本非空**时才算改写
                if new_text != cur:
                    changed_by.append(name)
                    cur = new_text
        return cur, changed_by
    # ⚠️ 不要复用 `tier` 一词：本文件里 `tier` 已表示插件 ABI 形态（spec/legacy）。
    # 三档的分工（都是「绝不向主循环抛异常」的兜底，不变量 A10）：
    #   · observe   —— 只读：返回值**一律忽略**，异常隔离。观测/日志类插件用它，
    #                  保证观测永远不改变主流程结果（A11：误用与普通失败不混淆）。
    #   · guard     —— **单调**：任一环拒绝后**终止后续环**，下游无法推翻。
    #                  有副作用插件（web_operator / scheduler）的安全根。
    #   · waterfall —— **可重排**：逐环链式，上一环返回值经 `carry` 进入下一环，
    #                  终值放在返回的 value 里。
    def execute_hooks_semantic(self, hook_name: str, semantics: str = "observe",
                               initial: Any = None, **kwargs) -> Dict[str, Any]:
        """按语义档位执行钩子，返回结构化结果：

            {"injects": [(name, content)],   # 注入内容（已合并 prompt_blocks）
             "skips":   [(name, reason)],    # 拒绝/覆盖（guard 下最多一条）
             "trail":   [(name, status)],    # 每环结局，排查用
             "value":   <waterfall 终值>}

        semantics 必须是 HOOK_SEMANTICS 之一；未知值直接 ValueError
        （宁可明确失败，不静默按某个档位跑）。
        """
        if semantics not in HOOK_SEMANTICS:
            raise ValueError(
                "未知钩子语义: %r（可选 %s）" % (semantics, ", ".join(HOOK_SEMANTICS)))

        out: Dict[str, Any] = {"injects": [], "skips": [], "trail": [], "value": initial}
        carry = initial
        for priority, name, hook_func in self._hook_candidates(hook_name):
            call_kwargs = dict(kwargs)
            if semantics == "waterfall":
                call_kwargs["carry"] = carry
            try:
                result = self._invoke_hook(name, hook_func, call_kwargs)
            except Exception as e:
                # 三档共同兜底：单个插件异常绝不影响其它环与主流程
                print(f"[Plugin:{name}] hook {hook_name}（{semantics}）异常: {e}")
                traceback.print_exc()
                out["trail"].append((name, "error"))
                continue

            if semantics == "observe":
                out["trail"].append((name, "observed"))
                continue                        # ★ 返回值一律忽略

            if not isinstance(result, dict):
                out["trail"].append((name, "ignored"))
                continue

            action = result.get("action")
            if action == "inject":
                content = result.get("content") or ""
                blocks = result.get("prompt_blocks") or []
                if blocks:
                    content = "\n".join(str(b) for b in blocks) + (
                        "\n" + content if content else "")
                if content:
                    out["injects"].append((name, content))
                out["trail"].append((name, "inject"))
            elif action in ("skip", "override"):
                out["skips"].append(
                    (name, result.get("reason") or result.get("content") or ""))
                out["trail"].append((name, action))
                if semantics == "guard":
                    break                       # ★ 单调：拒绝后下游无法推翻
            else:
                out["trail"].append((name, "noop"))

            if semantics == "waterfall":
                carry = result
                out["value"] = carry
        return out


    # ── 宿主能力注入（ABI v2）──────────────────────────────

    def set_host_api(self, **caps: Any) -> List[str]:
        """注入宿主能力（如 api_call）。

        纯追加：v1 插件完全不受影响；未注入时 ctx.api_call() 返回 None 并告警，
        插件据此降级 —— 不会因为宿主没提供能力而崩。
        返回已注入的能力名列表。
        """
        self._host_api.update({k: v for k, v in caps.items() if v is not None})
        return self.get_host_api()

    def get_host_api(self) -> List[str]:
        """已注入的能力名（供自检与插件页展示）。"""
        return sorted(self._host_api.keys())

    # ── 事件总线（ABI v2）──────────────────────────────────

    MAX_EMIT_DEPTH = 5

    def emit_event(self, event_name: str, payload: Optional[dict] = None,
                   source: str = "") -> List[Tuple[str, Any]]:
        """把事件派发给所有启用 spec 插件的 on_event 钩子。

        - 按 manifest 的 priority 升序派发（与其它类型化钩子一致）；
        - 单个插件抛异常只打印，不影响其他订阅方（沿用 execute_typed_hooks 的隔离）；
        - 带递归保护：插件在 on_event 里再 emit 同一/其他事件时，超过深度即丢弃。
        """
        if self._emit_depth >= self.MAX_EMIT_DEPTH:
            print(f"[Plugin] 事件 {event_name} 递归过深（>{self.MAX_EMIT_DEPTH}），已丢弃"
                  f"（source={source or '?'}）")
            return []
        self._emit_depth += 1
        try:
            return self.execute_typed_hooks(
                "on_event", event_name=event_name, payload=payload or {}
            )
        finally:
            self._emit_depth -= 1

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