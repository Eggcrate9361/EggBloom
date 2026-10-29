# -*- coding: utf-8 -*-
"""harness/qq_primitives.py —— QQ 回复链的 8 个原语（方案 v2 §4，2026-10-30）。

## 为什么是「原语 + 手册」而不是「一项能力一个工具」

v1 的路线是给每项能力写一个工具（看头像、查群号、听语音…），12 项要动 48 个点，
而且每次新需求都得再焊一个。v2 换成：给 agent 一套**能拼起来的手** + 一本
**怎么拼的书**（`docs/qq_agent_manual.md`）。看头像不是工具，是
`onebot → http_fetch → vision` 这条链，模型自己拼。

## 三条硬约束（写在这里，不是写在文档里）

1. **不 import bot_core**。原语必须能离线单测；bot_core 一 import 就起插件线程。
   所有宿主能力（OneBot 调用、视觉、沙漏）都靠**注入**。
2. **没有 shell / run_python**。一条 `os.system` 就把权限模型打穿。要算数、要
   转换，用现成工具或加新原语，不加"万能执行"。
3. **每个调用返回同一个 Envelope** `{ok, data, error, evidence}`。`evidence` 是
   "凭什么"（HTTP 状态、文件 hash、字节数）——没有 evidence 的"我做过了"一律
   不许发给用户（配合 harness/reply_audit 的第三类断言）。

## 安全边界

| 原语 | 边界 |
|---|---|
| `onebot` | action 白名单；`get_*` 默认放行，写类（`set_*`/`send_*`/`kick`/`ban`）必须显式开 |
| `http_fetch` | 域名白名单；只落沙箱内；有字节上限 |
| `vision` / `asr` | 只读沙箱内文件；单次超时，不串第二条链 |
| `fs_list` / `fs_read` | 沙箱 = app 根；禁 `..`；禁 `.env` / `cred*` / `token*` / `*.pem` |
| `recall` / `remember` | 走现有沙漏，只读写记忆，不碰文件 |
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Callable, Dict, Optional

#: 单次 http 抓取的默认上限（8MB）。头像 / 图片够用，网页正文也够。
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

#: 抓取默认只信任这些域。其余要由宿主显式加白名单（配置里来）。
DEFAULT_HOSTS = ("qlogo.cn", "multimedia.nt.qq.com.cn", "gchat.qpic.cn")

#: OneBot 只读动作。**白名单，不是黑名单** —— 新增写类动作必须显式加。
READ_ACTIONS = frozenset({
    "get_stranger_info", "get_friend_list", "get_group_list",
    "get_group_info", "get_group_member_list", "get_group_member_info",
    "get_login_info", "get_status", "get_version_info",
    "get_image", "get_record", "get_file", "get_msg",
})

#: OneBot 写类动作：默认**全部拒绝**，必须 allow_write=True 才放。
WRITE_ACTIONS = frozenset({
    "send_private_msg", "send_group_msg", "send_msg", "delete_msg",
    "set_group_kick", "set_group_ban", "set_group_card", "set_group_name",
    "set_friend_add_request", "set_group_add_request",
})

#: 读文件黑名单：命中任一条就不给读（大小写不敏感）。
_FORBID_NAME = re.compile(
    r"(^\.env|^cred|^token|\.pem$|\.key$|^id_rsa|^\.git[/\\]config$|cookie)",
    re.I)

#: 这些目录属于运行态/凭据，越权读取风险最高，一并挡。
_FORBID_DIRS = (".git", "node_modules", "__pycache__", ".venv", "mcp-venv")


def envelope(ok: bool, data: Any = None, error: str = "",
             evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """统一返回形状。`evidence` 至少能给审计链留一句"凭什么"。"""
    return {"ok": bool(ok), "data": data, "error": str(error or ""),
            "evidence": dict(evidence or {})}


class PrimitiveError(Exception):
    pass


class PrimitiveSet:
    """一套绑好宿主能力的原语。

    Args:
        sandbox_root: 文件系统沙箱根。默认 app 根（`harness/..`）。
        onebot:       `Callable[[str, dict], dict]`，宿主注入的 OneBot 调用。
        vision:       `Callable[[str, str], dict]`，返回 `{ok, text}` 或 `{ok:False,error}`。
        asr:          `Callable[[str], dict]`。
        recall:       `Callable[[str], list|str]`，查沙漏记忆。
        remember:     `Callable[[str], Any]`，写沙漏记忆。
        allowed_hosts:额外允许的域名（配置里来）。
        allow_write: 是否允许写类 OneBot 动作（默认 False）。
    """

    def __init__(self, sandbox_root: Optional[str] = None, *,
                 onebot: Optional[Callable[[str, dict], dict]] = None,
                 vision: Optional[Callable[[str, str], dict]] = None,
                 asr: Optional[Callable[[str], dict]] = None,
                 recall: Optional[Callable[[str], Any]] = None,
                 remember: Optional[Callable[[str], Any]] = None,
                 allowed_hosts=(), allow_write: bool = False,
                 fetch: Optional[Callable[..., dict]] = None):
        self.root = os.path.abspath(
            sandbox_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.onebot_fn = onebot
        self.vision_fn = vision
        self.asr_fn = asr
        self.recall_fn = recall
        self.remember_fn = remember
        self.hosts = tuple(DEFAULT_HOSTS) + tuple(h or "" for h in (allowed_hosts or ()))
        self.allow_write = bool(allow_write)
        self.fetch_fn = fetch          # 注入的下载器；None 时 http_fetch 报"未注入"

    # ── 路径 ────────────────────────────────────────────────────
    def resolve(self, path: str) -> str:
        """把用户/模型给的路径解析成沙箱内的绝对路径。越界就抛。"""
        raw = str(path or "").strip()
        if not raw:
            raise PrimitiveError("路径为空")
        if "\x00" in raw:
            raise PrimitiveError("路径含非法字符")
        p = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(self.root, raw))
        try:
            common = os.path.commonpath([p, self.root])
        except ValueError:
            raise PrimitiveError(f"路径越界：{path}")
        if os.path.normcase(common) != os.path.normcase(self.root):
            raise PrimitiveError(f"路径越界：{path}")
        rel = os.path.relpath(p, self.root)
        parts = [x for x in re.split(r"[/\\]", rel) if x not in ("", ".")]
        for seg in parts:
            if seg == "..":
                raise PrimitiveError(f"路径越界：{path}")
            if seg.lower() in _FORBID_DIRS:
                raise PrimitiveError(f"这个目录不给读：{seg}")
            if _FORBID_NAME.search(seg):
                raise PrimitiveError(f"这个文件不给读：{seg}")
        return p

    # ── onebot ──────────────────────────────────────────────────
    def onebot(self, action: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """调 OneBot 动作。只读默认放行，写类必须 allow_write。"""
        name = str(action or "").strip()
        if not name:
            return envelope(False, error="action 为空")
        if name not in READ_ACTIONS:
            if name not in WRITE_ACTIONS:
                return envelope(False, error=f"不在白名单里的 action：{name}",
                                evidence={"action": name})
            if not self.allow_write:
                return envelope(False, error=f"写类动作需要授权：{name}",
                                evidence={"action": name, "need": "allow_write"})
        if self.onebot_fn is None:
            return envelope(False, error="onebot 未注入")
        try:
            r = self.onebot_fn(name, dict(params or {}))
        except Exception as e:                      # 注入实现抛了也要给 Envelope
            return envelope(False, error=f"{type(e).__name__}: {e}",
                            evidence={"action": name})
        if not isinstance(r, dict):
            return envelope(bool(r), data=r, evidence={"action": name})
        ok = bool(r.get("ok", r.get("status") == "ok" or r.get("retcode") == 0))
        return envelope(ok, data=r.get("data", r), error="" if ok else str(r.get("error") or r.get("message") or "调用失败"),
                        evidence={"action": name, "retcode": r.get("retcode")})

    # ── http ────────────────────────────────────────────────────
    def host_allowed(self, url: str) -> bool:
        try:
            from urllib.parse import urlparse
            host = (urlparse(str(url)).hostname or "").lower()
        except Exception:
            return False
        if not host:
            return False
        return any(host == h or host.endswith("." + h) for h in self.hosts if h)

    def http_fetch(self, url: str, save_as: Optional[str] = None,
                   max_bytes: int = DEFAULT_MAX_BYTES) -> Dict[str, Any]:
        """抓一个 URL（可落盘）。域名白名单 + 沙箱 + 字节上限。"""
        u = str(url or "").strip()
        if not re.match(r"^https?://", u, re.I):
            return envelope(False, error="只接受 http/https")
        if not self.host_allowed(u):
            return envelope(False, error="域名不在白名单",
                            evidence={"host": u.split("/")[2] if "//" in u else ""})
        dest = None
        if save_as:
            try:
                dest = self.resolve(save_as)
            except PrimitiveError as e:
                return envelope(False, error=str(e))
        if self.fetch_fn is None:
            return envelope(False, error="下载器未注入")
        try:
            r = self.fetch_fn(u, dest=dest, max_bytes=int(max_bytes))
        except Exception as e:
            return envelope(False, error=f"{type(e).__name__}: {e}", evidence={"url": u})
        if not isinstance(r, dict):
            return envelope(False, error="下载器返回形状不对", evidence={"url": u})
        ok = bool(r.get("ok"))
        ev = {"url": u, "status": r.get("status"), "bytes": r.get("bytes")}
        if dest and ok and os.path.isfile(dest):
            try:
                with open(dest, "rb") as f:
                    ev["sha256"] = hashlib.sha256(f.read()).hexdigest()[:16]
            except Exception:
                pass
            ev["path"] = dest
        return envelope(ok, data=r.get("data", {"path": dest, "bytes": r.get("bytes")}),
                        error="" if ok else str(r.get("error") or "下载失败"), evidence=ev)

    # ── 视觉 / 语音 ─────────────────────────────────────────────
    def vision(self, path: str, question: str = "") -> Dict[str, Any]:
        """看图。**只读沙箱内文件**，失败就如实返回失败（不许猜）。"""
        try:
            p = self.resolve(path)
        except PrimitiveError as e:
            return envelope(False, error=str(e))
        if not os.path.isfile(p):
            return envelope(False, error="文件不存在", evidence={"path": p})
        if self.vision_fn is None:
            return envelope(False, error="视觉未注入")
        try:
            r = self.vision_fn(p, str(question or ""))
        except Exception as e:
            return envelope(False, error=f"{type(e).__name__}: {e}")
        if not isinstance(r, dict):
            return envelope(False, error="视觉返回形状不对")
        ok = bool(r.get("ok")) and bool(str(r.get("text") or "").strip())
        return envelope(ok, data=str(r.get("text") or ""),
                        error="" if ok else str(r.get("error") or "没读出来"),
                        evidence={"path": os.path.basename(p)})

    def asr(self, path: str) -> Dict[str, Any]:
        try:
            p = self.resolve(path)
        except PrimitiveError as e:
            return envelope(False, error=str(e))
        if self.asr_fn is None:
            return envelope(False, error="语音识别未注入")
        try:
            r = self.asr_fn(p)
        except Exception as e:
            return envelope(False, error=f"{type(e).__name__}: {e}")
        ok = isinstance(r, dict) and bool(r.get("ok")) and bool(str(r.get("text") or "").strip())
        txt = (r or {}).get("text") if isinstance(r, dict) else ""
        return envelope(ok, data=str(txt or ""),
                        error="" if ok else "没听清",
                        evidence={"path": os.path.basename(p)})

    # ── 文件 ────────────────────────────────────────────────────
    def fs_list(self, path: str = ".", limit: int = 200) -> Dict[str, Any]:
        try:
            p = self.resolve(path)
        except PrimitiveError as e:
            return envelope(False, error=str(e))
        if not os.path.isdir(p):
            return envelope(False, error="不是目录", evidence={"path": p})
        try:
            names = sorted(os.listdir(p))[: max(1, int(limit))]
        except Exception as e:
            return envelope(False, error=f"{type(e).__name__}: {e}")
        items = []
        for n in names:
            fp = os.path.join(p, n)
            try:
                size = os.path.getsize(fp) if os.path.isfile(fp) else None
            except Exception:
                size = None
            items.append({"name": n, "dir": os.path.isdir(fp), "bytes": size})
        return envelope(True, data=items,
                        evidence={"path": os.path.relpath(p, self.root), "count": len(items)})

    def fs_read(self, path: str, max_chars: int = 20000) -> Dict[str, Any]:
        try:
            p = self.resolve(path)
        except PrimitiveError as e:
            return envelope(False, error=str(e))
        if not os.path.isfile(p):
            return envelope(False, error="文件不存在", evidence={"path": p})
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read(max(1, int(max_chars)))
        except Exception as e:
            return envelope(False, error=f"{type(e).__name__}: {e}")
        return envelope(True, data=txt,
                        evidence={"path": os.path.relpath(p, self.root),
                                  "chars": len(txt), "truncated": len(txt) >= int(max_chars)})

    # ── 记忆 ────────────────────────────────────────────────────
    def recall(self, query: str, limit: int = 5) -> Dict[str, Any]:
        if self.recall_fn is None:
            return envelope(False, error="记忆未注入")
        try:
            r = self.recall_fn(str(query or ""))
        except Exception as e:
            return envelope(False, error=f"{type(e).__name__}: {e}")
        if isinstance(r, list):
            r = r[: max(1, int(limit))]
        return envelope(True, data=r, evidence={"query": str(query or "")[:40]})

    def remember(self, text: str) -> Dict[str, Any]:
        if self.remember_fn is None:
            return envelope(False, error="记忆未注入")
        try:
            r = self.remember_fn(str(text or ""))
        except Exception as e:
            return envelope(False, error=f"{type(e).__name__}: {e}")
        return envelope(True, data=r, evidence={"chars": len(str(text or ""))})

    # ── 给 agent 看的注册表 ─────────────────────────────────────
    def specs(self) -> list:
        """工具定义。手册与这里必须一一对应（test_manual_consistency 会查）。"""
        return [
            {"name": "onebot", "signature": "(action, params) -> Envelope",
             "side_effects": "read", "permission": "L0",
             "desc": "调 OneBot 动作。只读 get_* 直接可用；写类要授权。"},
            {"name": "http_fetch", "signature": "(url, save_as) -> Envelope",
             "side_effects": "read", "permission": "L0",
             "desc": "抓一个 URL，可存到沙箱内的 save_as。"},
            {"name": "vision", "signature": "(path, question) -> Envelope",
             "side_effects": "read", "permission": "L0",
             "desc": "看懂沙箱内的一张图。失败就如实失败，不许猜。"},
            {"name": "asr", "signature": "(path) -> Envelope",
             "side_effects": "read", "permission": "L0",
             "desc": "把沙箱内的一段语音转成文字。"},
            {"name": "fs_list", "signature": "(path) -> Envelope",
             "side_effects": "read", "permission": "L0",
             "desc": "列一个目录。"},
            {"name": "fs_read", "signature": "(path, max_chars) -> Envelope",
             "side_effects": "read", "permission": "L0",
             "desc": "读沙箱内的文本文件。"},
            {"name": "recall", "signature": "(query) -> Envelope",
             "side_effects": "read", "permission": "L0",
             "desc": "查记忆（沙漏）。"},
            {"name": "remember", "signature": "(text) -> Envelope",
             "side_effects": "write", "permission": "L1",
             "desc": "往记忆里写一条。"},
        ]

    def call(self, name: str, **kwargs) -> Dict[str, Any]:
        """按名字调原语（agent 侧统一入口）。未知名字返回失败 Envelope。"""
        fn = getattr(self, str(name or ""), None)
        if not callable(fn) or str(name) not in {s["name"] for s in self.specs()}:
            return envelope(False, error=f"没有这个原语：{name}")
        try:
            return fn(**kwargs)
        except TypeError as e:
            return envelope(False, error=f"参数不对：{e}")
        except Exception as e:
            return envelope(False, error=f"{type(e).__name__}: {e}")


__all__ = ["DEFAULT_MAX_BYTES", "DEFAULT_HOSTS", "READ_ACTIONS", "WRITE_ACTIONS",
           "PrimitiveSet", "PrimitiveError", "envelope"]
