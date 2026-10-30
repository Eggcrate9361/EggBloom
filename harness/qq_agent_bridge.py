# -*- coding: utf-8 -*-
"""harness/qq_agent_bridge.py —— 把 QQAgent（A3）接到 QQ 主链的桥（2026-10-30）。

## 这一层解决什么

`harness/qq_agent.py` 把「一句话」跑成「一次 agent 循环」，但它不知道自己要
接在哪儿、什么时候该接、接完之后失败算什么。这一层补那三个决定：

1. **开关与允许名单**：`qq_agent_enable` 默认 False；就算打开，也只对
   `qq_agent_allow` 里点名的对象生效。第一次上线只开给自己私聊，
   出事的影响面是"一个人"，不是"所有群"。
2. **占位先发**：过了闸就先把「等下，我看看」发出去，把等待压在 LLM 之前
   （v2 §6）。这一步必须在 agent 跑之前，晚了就没意义。
3. **失败回落**：agent 没拿到答案（模型挂了 / 超了 25 秒 / 收敛成空）时
   返回 `text=""`，**由调用方回落到老链路**。宁可多花一次调用，也不许
   因为新通道而把一条消息吞掉。

## 为什么还留着 `deps` 字典

这一层**不 import bot_core**（bot_core 一 import 就起插件线程，离线测不了）。
宿主把 OneBot / 视觉 / 记忆 / 下载器 / 模型调用全部注入进来，
`tests/test_qq_agent_bridge.py` 用假函数就能跑完整个决策链。

## 谁往台账里写

`QQAgent.run()` 的 `tool_results` 是 `[[name, ok], ...]`，而
`harness/reply_audit` 吃的是 `[(ts, name, ok, detail), ...]`。

这一层负责保证**两种实现都能被审计看到**：`harness.agent_loop` 执行工具时
会走 `tool_runner.execute_tool_call`，它自己已经记了一条；如果哪天换成
不走那条路的 runner，就没人记了。所以桥在跑完后对照 `ledger()` 快照，
**只补记台账里没有的**（重复记账会让"这一轮调了几次"统计失真）。
转写之后，主链上原有的第三类断言（能力自述校验）不用改一行就能认出
"这一轮真的调过 vision，而且成功了"。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from harness.qq_agent import (MAX_STEPS_GROUP, MAX_STEPS_PRIVATE, TOTAL_TIMEOUT,
                              QQAgent, placeholder_for, should_use_agent)
from harness.qq_primitives import PrimitiveSet

#: 开关键。默认关：没被点名的对象一律走老链路。
KEY_ENABLE = "qq_agent_enable"

#: 允许名单。条目形状：
#:   "123456789"        只对这个人私聊生效
#:   "private:123456789" 同上（显式写法）
#:   "group:123456"     只对这一个群生效
#:   "*"                全部（调试用，别长期开）
KEY_ALLOW = "qq_agent_allow"

#: 额外信任的抓取域名（叠加在 DEFAULT_HOSTS 上）。
KEY_HOSTS = "qq_agent_hosts"

#: 写类 OneBot 动作总开关。默认关：agent 不许自己发消息/踢人。
KEY_ALLOW_WRITE = "qq_agent_allow_write"


def _as_list(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v).strip()] if str(v).strip() else []


def enabled(cfg: Optional[dict]) -> bool:
    """总开关。缺键 / 值不像真 = 关。"""
    try:
        return bool((cfg or {}).get(KEY_ENABLE, False))
    except Exception:
        return False


def allowed(cfg: Optional[dict], *, is_group: bool,
            group_id: str = "", sender: str = "") -> bool:
    """这个对象在不在允许名单里。名单空 = 谁都不许（不是"全都允许"）。"""
    entries = set(_as_list((cfg or {}).get(KEY_ALLOW)))
    if not entries:
        return False
    if "*" in entries:
        return True
    if is_group:
        gid = str(group_id or "").strip()
        if not gid:
            return False
        # 群只认显式 `group:<号>`：裸号码是"某人的私聊"，不能顺手把同号群也开了。
        return f"group:{gid}" in entries
    who = str(sender or "").strip()
    if not who:
        return False
    return bool({who, f"private:{who}"} & entries)


def should_try(cfg: Optional[dict], text: str, *, has_media: bool = False,
               is_group: bool = False, group_id: str = "",
               sender: str = "") -> bool:
    """三层串起来：开关 → 名单 → 意图闸。任何一层不过就回老路。"""
    if not enabled(cfg):
        return False
    if not allowed(cfg, is_group=is_group, group_id=group_id, sender=sender):
        return False
    return bool(should_use_agent(text, has_media=has_media))


class Bridge:
    """一次「要不要走 agent → 走 → 收尾」的完整编排。

    Args:
        deps: 注入的宿主能力（缺省即"没这个能力"，原语会如实报错）
            onebot(action, params) -> dict
            vision(path, question) -> {ok, text} / {ok: False, error}
            asr(path) -> {ok, text}
            recall(query) -> list|str
            remember(text) -> Any
            fetch(url, dest=None, max_bytes=...) -> {ok, status, bytes, error}
            chat(messages, tools, on_reasoning=None) -> (content, tool_calls, usage)
            placeholder(text, is_group, group_id, sender) -> Any
            note_call(name, ok) -> None
            ledger() -> [(ts, name, ok, detail), ...]
                已有台账快照。给了就先看有没有记过，避免同一轮记两遍。
        root:  沙箱根（= app 根），手册也在这里读
        cfg:   运行配置（dict）
        log:   日志函数
    """

    def __init__(self, *, deps: Dict[str, Callable], root: str,
                 cfg: Optional[dict] = None,
                 log: Optional[Callable[[str], None]] = None,
                 max_steps_private: int = MAX_STEPS_PRIVATE,
                 max_steps_group: int = MAX_STEPS_GROUP,
                 total_timeout: float = TOTAL_TIMEOUT):
        self.deps = dict(deps or {})
        self.root = str(root or "")
        self.cfg = dict(cfg or {})
        self.log = log or (lambda _m: None)
        self.max_steps_private = int(max_steps_private)
        self.max_steps_group = int(max_steps_group)
        self.timeout = float(total_timeout)

    # ── 工具装配 ────────────────────────────────────────────────
    def _chat_fn(self):
        chat = self.deps.get("chat")
        if chat is None:
            raise RuntimeError("未注入模型调用（chat）")

        def _call(messages, tools=None, on_reasoning=None):
            # run_agent 会先试 3 参数、TypeError 再退回 2 参数，这里直接兼容。
            try:
                ret = chat(messages, tools, on_reasoning=on_reasoning)
            except TypeError:
                ret = chat(messages, tools)
            return _normalize_chat(ret)

        return _call

    def _primitives(self) -> PrimitiveSet:
        d = self.deps
        return PrimitiveSet(
            sandbox_root=self.root,
            onebot=d.get("onebot"),
            vision=d.get("vision"),
            asr=d.get("asr"),
            recall=d.get("recall"),
            remember=d.get("remember"),
            fetch=d.get("fetch"),
            allowed_hosts=_as_list(self.cfg.get(KEY_HOSTS)),
            allow_write=bool(self.cfg.get(KEY_ALLOW_WRITE, False)),
        )

    # ── 跑一次 ──────────────────────────────────────────────────
    def run(self, text: str, *, has_media: bool = False, is_group: bool = False,
            group_id: str = "", sender: str = "", session_id: str = "",
            system_prompt: str = "", history: Optional[list] = None,
            extra_system: str = "") -> Dict[str, Any]:
        """返回 `{used, ok, text, tools, tool_results, elapsed, aborted, error,
        placeholder_sent}`。

        `text` 为空 = 没接管，调用方必须回落老链路。
        """
        out: Dict[str, Any] = {
            "used": False, "ok": False, "text": "", "tools": [],
            "tool_results": [], "elapsed": 0.0, "aborted": False,
            "error": "", "placeholder_sent": False,
        }
        t0 = time.time()

        # ① 占位先发（≤3 秒的短句）。失败不影响主流程。
        try:
            ph_fn = self.deps.get("placeholder")
            if ph_fn is not None:
                ph_fn(placeholder_for(text), bool(is_group),
                      str(group_id or ""), str(sender or ""))
                out["placeholder_sent"] = True
        except Exception as e:
            self.log(f"[qq_agent] 占位发送失败（继续）: {type(e).__name__}: {e}")

        # ② 真跑 agent
        _ledger_snapshot: Optional[int] = None
        _ledger_fn = self.deps.get("ledger")
        if _ledger_fn is not None:
            try:
                _ledger_snapshot = len(list(_ledger_fn() or []))
            except Exception:
                _ledger_snapshot = None
        try:
            agent = QQAgent(
                primitives=self._primitives(),
                chat_fn=self._chat_fn(),
                root=self.root,
                log=self.log,
                max_steps_private=self.max_steps_private,
                max_steps_group=self.max_steps_group,
                total_timeout=self.timeout,
            )
            res = agent.run(
                str(text or ""),
                session_id=session_id or (f"qq_g_{group_id}" if is_group else f"qq_p_{sender}"),
                system_prompt=system_prompt,
                history=history,
                is_group=bool(is_group),
                extra_system=extra_system,
            )
        except Exception as e:
            # 装配级失败（比如没注入 chat）：当作没接管，别打断主链。
            self.log(f"[qq_agent] 装配失败: {type(e).__name__}: {e}")
            out["elapsed"] = round(time.time() - t0, 2)
            out["error"] = f"{type(e).__name__}: {e}"
            return out

        out.update({
            "used": True,
            "ok": bool(res.get("ok")),
            "text": str(res.get("text") or ""),
            "tools": list(res.get("tools") or []),
            "tool_results": [list(x) for x in (res.get("tool_results") or [])],
            "aborted": bool(res.get("aborted")),
            "error": str(res.get("error") or ""),
            "elapsed": round(time.time() - t0, 2),
        })

        # ③ 把工具留痕转写进主台账（第三类断言吃这份）
        #    ★ 去重：execute_tool_call 自己会记账，这里只补"没人记过的"。
        note = self.deps.get("note_call")
        if note is not None:
            already: List[tuple] = []
            if _ledger_fn is not None and _ledger_snapshot is not None:
                try:
                    _cur = list(_ledger_fn() or [])
                    already = [(str(c[1]), bool(c[2])) for c in _cur[_ledger_snapshot:]]
                except Exception:
                    already = []
            for item in out["tool_results"]:
                try:
                    seq = list(item) if isinstance(item, (list, tuple)) else []
                    name, ok = (str(seq[0] or ""), bool(seq[1])) if len(seq) >= 2 else ("", False)
                except Exception:
                    name, ok = "", False
                if (name, ok) in already:
                    already.remove((name, ok))     # 同轮多次同名调用按次数消
                    continue
                try:
                    note(name, ok)
                except Exception:
                    pass

        self.log(f"[qq_agent] {'群' if is_group else '私聊'}"
                 f"{group_id or sender} ok={out['ok']} tools={','.join(out['tools']) or '无'} "
                 f"{out['elapsed']}s 占位={out['placeholder_sent']}")
        return out


def _normalize_chat(ret) -> tuple:
    """宿主 chat 的返回可能是 tuple / dict / str，统一成 3 元组。"""
    if isinstance(ret, tuple):
        c = ret[0] if len(ret) > 0 else ""
        tc = ret[1] if len(ret) > 1 else []
        u = ret[2] if len(ret) > 2 else {}
        return str(c or ""), list(tc or []), dict(u or {})
    if isinstance(ret, dict):
        return (str(ret.get("content") or ""),
                list(ret.get("tool_calls") or []),
                dict(ret.get("usage") or {}))
    return str(ret or ""), [], {}


__all__ = ["KEY_ENABLE", "KEY_ALLOW", "KEY_HOSTS", "KEY_ALLOW_WRITE",
           "enabled", "allowed", "should_try", "Bridge"]
