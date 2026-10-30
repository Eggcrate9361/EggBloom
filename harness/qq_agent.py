# -*- coding: utf-8 -*-
"""harness/qq_agent.py —— QQ 回复链进 agent 的适配层（方案 v2 §2/§6，2026-10-30）。

## 这一层解决什么

`harness/agent_loop.run_agent` 早就写好了（多步、预算、溯源、abort），但 QQ 主链
**从没进去过**（`run_agent` 引用数 = 0）。这里不重写 agent，只做三件事：

1. **意图闸**：判断这句话该走闲聊还是走 agent。闲聊中位 21 秒不能动 ——
   全量走 agent 会把它变成 60 秒，"像人"就没了（v2 §2）。
2. **占位**：进 agent 前先让宿主发一条 ≤3 秒的短句（"等下，我看看"），
   把等待压在 LLM 之前。
3. **预算与收敛**：私聊 3 步 / 群聊 2 步、总时长 25 秒硬闸；超时收敛成
   "没查到"，绝不挂着（v2 §6）。

## 为什么不直接写在 bot_core 里

bot_core 一万五千行，`run_agent` 塞进去就没法离线测了。这一层全部依赖注入
（原语集、chat_fn、日志），单测里给一个假 chat_fn 就能跑完整个循环 ——
包括"模型调了 vision，失败了，然后如实说没看到"这种最关键的分支。

## 与第三类断言的关系

agent 跑完会带回 `trace`（这一轮真调过哪些工具、成没成）。宿主把它交给
`reply_audit.find_capability_claims`，"我看不到图"这句话就不再靠猜 ——
**有失败调用才准说看不到**。
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional

#: 私聊 / 群聊的步数预算（v2 §6）。群聊插一句不值 3 步成本。
MAX_STEPS_PRIVATE = 3
MAX_STEPS_GROUP = 2

#: 总时长硬闸（秒）。超了收敛成"没查到"，不挂着。
TOTAL_TIMEOUT = 25.0

#: 占位消息：抢在 LLM 之前发出去，固定 3–6 字，不带标点花样。
PLACEHOLDERS = ("等下，我看看", "我查查", "让我看看")

#: 意图闸「动词」。**只管"要不要取数/动手"**，不管"能不能答"。
GATE_VERBS = (
    "查", "找", "看", "听", "翻", "搜",
    "是不是", "有没有", "在不在", "认得", "认不认得", "记得",
    "知道", "帮我", "能不能", "会不会", "是什么", "是谁", "在哪",
)

#: 意图闸「对象」。动词 + 对象同时命中才算，避免"你是不是傻"这种被卷进来。
GATE_OBJECTS = (
    "图", "头像", "语音", "文件", "群", "列表", "记录", "日志", "上次",
    "谁", "名字", "身份", "表情包", "歌", "视频", "消息", "群号", "成员", "叫",
)

#: 明确不该进 agent 的（纯情绪、打招呼、太短）。先判这个，再判命中。
GATE_DENY_HINT = ("哈哈", "在吗", "你好", "晚安", "早", "嗯", "哦", "行", "算了")


def should_use_agent(text: str, has_media: bool = False) -> bool:
    """这句话要不要走 agent 通道（v2 §2 的意图闸）。

    保守取向：**宁可漏判也不误判**。误判的代价是闲聊也被拖进 2–4 轮 LLM，
    中位延迟直接从 21 秒变成 60 秒；漏判的代价只是这条走老路。
    """
    t = str(text or "").strip()
    if len(t) < 4:
        return False
    if t in GATE_DENY_HINT:
        return False
    hit_verb = any(v in t for v in GATE_VERBS)
    hit_obj = any(o in t for o in GATE_OBJECTS)
    # 带图的轮次：图本身是对象，只要不是纯斗图就值得看一眼
    if has_media and (hit_verb or len(t) >= 6):
        return True
    return hit_verb and hit_obj


def placeholder_for(text: str) -> str:
    """按内容选一条占位（确定性的，测试可断言）。"""
    return PLACEHOLDERS[sum(map(ord, str(text or ""))) % len(PLACEHOLDERS)]


def load_manual(root: str) -> str:
    """读操作手册并替换 `%APP_ROOT%`。读不到返回空串（宁可没有手册，也别崩）。"""
    p = os.path.join(str(root), "docs", "qq_agent_manual.md")
    try:
        with open(p, "r", encoding="utf-8") as f:
            t = f.read()
    except Exception:
        return ""
    return t.replace("%APP_ROOT%", str(root))


def build_system_prompt(base_prompt: str, root: str) -> str:
    """人格（base）+ 手册。手册在前缀之后，改手册才失效缓存。"""
    man = load_manual(root)
    if not man:
        return str(base_prompt or "")
    return f"{base_prompt or ''}\n\n{man}"


class QQAgent:
    """把「一句话」跑成「一次 agent 循环」的适配器。

    Args:
        primitives: `harness.qq_primitives.PrimitiveSet`
        chat_fn:    `(messages, tools) -> (content, tool_calls, usage)`；宿主注入
        root:       程序根（读手册）
        log:        日志函数
        max_steps_private / max_steps_group / total_timeout: 预算（v2 §6）
    """

    def __init__(self, *, primitives, chat_fn: Callable, root: str = "",
                 log: Optional[Callable[[str], None]] = None,
                 max_steps_private: int = MAX_STEPS_PRIVATE,
                 max_steps_group: int = MAX_STEPS_GROUP,
                 total_timeout: float = TOTAL_TIMEOUT,
                 runner: Optional[Callable] = None):
        self.primitives = primitives
        self.chat_fn = chat_fn
        self.root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.log = log or (lambda _m: None)
        self.max_steps_private = int(max_steps_private)
        self.max_steps_group = int(max_steps_group)
        self.timeout = float(total_timeout)
        self._runner = runner          # 注入 run_agent（测试可替身）

    # ── 工具映射 ────────────────────────────────────────────────
    def tool_functions(self) -> Dict[str, Callable]:
        """原语 → run_agent 要的 `{name: callable(args_dict)}`。

        ★ 2026-10-30：`tool_runner.execute_tool_call` 传的是**一个 dict 位置参数**
          （`fn(args)`），不是 kwargs。第一版写成 `**kwargs` 就变成
          "没有这个原语：{'path': ...}"，工具永远调不到 —— 而模型只会看到一句
          失败回灌，继续按自己的想象答。这种"接线错了但看起来在跑"最难查。
        """
        from harness.qq_primitives import envelope

        out = {}
        for spec in self.primitives.specs():
            name = spec["name"]

            def _bound(args=None, _n=name, **kwargs):
                if isinstance(args, dict):
                    kwargs = {**args, **kwargs}
                elif args is not None:
                    return envelope(False, error="工具参数必须是对象")
                return self.primitives.call(_n, **kwargs)

            out[name] = _bound
        return out

    def tool_schemas(self) -> List[Dict[str, Any]]:
        """给模型的工具定义（OpenAI function 形状）。"""
        return [{
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s["desc"],
                "parameters": {"type": "object", "properties": {},
                               "additionalProperties": True},
            },
        } for s in self.primitives.specs()]

    # ── 跑一轮 ──────────────────────────────────────────────────
    def run(self, message: str, *, session_id: str = "",
            system_prompt: str = "", history: Optional[list] = None,
            is_group: bool = False, extra_system: str = "") -> Dict[str, Any]:
        """返回 `{used, ok, text, steps, tools, elapsed, aborted, error}`。

        `used` 恒为 True —— 调用方只在本层决定要走 agent 时才调这里
        （闸在外面，判断在 `should_use_agent`）。

        `extra_system`（2026-10-30，1.6.0 铺路）：**拼在手册之后**的一轮上下文
        （记忆/情绪关系/场景）。放在手册后面是为了不动前缀缓存 —— 手册稳定，
        它每轮变。
        """
        t0 = time.time()
        deadline = t0 + self.timeout
        steps = self.max_steps_group if is_group else self.max_steps_private
        runner = self._runner
        if runner is None:
            from harness.agent_loop import run_agent as runner  # 延迟 import

        def _abort():
            return time.time() > deadline

        #: 工具调用留痕。**不靠 AgentResult.trace** —— persist=False 时它是空的
        #: （trace 只在 persist=True 时从 session_log 回读）。QQ 主链不写第二份
        #: session_log，所以这里自己从 on_event 收：tool_result 带 ok 标记，
        #: 正好是第三类断言要的那份"这一轮到底有哪些工具成功过"。
        results: List[List[Any]] = []

        def _on_event(kind, data):
            if kind == "tool_result" and isinstance(data, dict):
                results.append([str(data.get("tool") or ""), bool(data.get("ok"))])

        try:
            _sys = build_system_prompt(system_prompt, self.root)
            _extra = str(extra_system or "").strip()
            if _extra:
                _sys = _sys + "\n\n" + _extra
            res = runner(
                session_id=session_id or "qq_agent",
                user_input=str(message or ""),
                chat_fn=self.chat_fn,
                tool_functions=self.tool_functions(),
                system_prompt=_sys,
                history=history,
                max_steps=steps,
                should_abort=_abort,
                logger=self.log,
                on_event=_on_event,
                persist=False,          # QQ 主链的溯源由 chat_logs 负责，别写两份
                tool_schemas=self.tool_schemas(),
            )
        except Exception as e:
            self.log(f"[qq_agent] 跑挂了: {type(e).__name__}: {e}")
            return {"used": True, "ok": False, "text": "", "steps": 0,
                    "tools": [], "tool_results": [], "elapsed": round(time.time() - t0, 2),
                    "aborted": False, "error": f"{type(e).__name__}: {e}"}

        elapsed = round(time.time() - t0, 2)
        tools = _tool_names(getattr(res, "trace", None)) or [n for n, _ in results]
        text = str(getattr(res, "answer", "") or "").strip()
        ok = bool(getattr(res, "ok", False)) and bool(text)
        aborted = bool(getattr(res, "aborted", False)) or elapsed > self.timeout

        if not ok:
            # 超时/中止都不许挂空：给一句能和第三类断言对齐的收敛话术
            if aborted:
                text = "我这儿没查到，等下再试"
            elif not text:
                text = ""
        self.log(f"[qq_agent] steps={getattr(res, 'steps', '?')} "
                 f"tools={','.join(tools) or '无'} {elapsed}s ok={ok}")
        return {"used": True, "ok": ok, "text": text,
                "steps": int(getattr(res, "steps", 0) or 0),
                "tools": tools, "tool_results": results,
                "elapsed": elapsed,
                "aborted": aborted, "error": str(getattr(res, "error", "") or "")}


def _tool_names(trace) -> List[str]:
    """从 trace 里取工具名（有的实现是 dict，有的是对象）。"""
    out: List[str] = []
    for item in (trace or []):
        name = ""
        if isinstance(item, dict):
            name = str(item.get("tool") or item.get("name") or item.get("type") or "")
        else:
            name = str(getattr(item, "tool", "") or getattr(item, "name", "") or
                       getattr(item, "type", "") or "")
        if name and name not in out:
            out.append(name)
    return out


__all__ = ["MAX_STEPS_PRIVATE", "MAX_STEPS_GROUP", "TOTAL_TIMEOUT", "PLACEHOLDERS",
           "GATE_VERBS", "GATE_OBJECTS", "should_use_agent", "placeholder_for",
           "load_manual", "build_system_prompt", "QQAgent"]
