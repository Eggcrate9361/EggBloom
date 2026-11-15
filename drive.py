"""drive.py —— 长期主动（2026-11-08）

不是"隔多久冒一句"，是**它自己有事想做**：
  · 有一份自己的长期目标（goals.md），它自己增删
  · 每次醒来先看目标 + 上次日志，决定这轮做什么（做事 / 说话 / 什么都不做）
  · 做了事就写日志，做完的划掉；想找人就找他（tell_admin）
  · 醒来的节奏由它自己调（set_interval），默认 45 分钟，允许 5~720

设计约束（分离态下仍然成立的部分）：
  · 不新增"它必须说话"的规则 —— 允许 NOOP
  · 不阻塞主对话：跑在独立线程，走 agent 自己的循环
  · 一切落盘：goals.md / journal.jsonl / state.json，人能看，它也能改
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_ROOT, "data", "drive")
_GOALS = os.path.join(_DATA, "goals.md")
_JOURNAL = os.path.join(_DATA, "journal.jsonl")
_STATE = os.path.join(_DATA, "state.json")

DEFAULT_INTERVAL_MIN = 45.0
MIN_INTERVAL_MIN = 5.0
MAX_INTERVAL_MIN = 720.0

# 单轮成本警戒线。2026-11-09 实测有一轮烧了 388k —— 那不是「顺手做点事」，
# 是在一轮里开了长活（全量审计、批量重构）。间隔 45 分钟配这个单价很荒谬。
# 所以只做两件事：journal 里打标记，醒来的提示里点名让它自己收着点。
HEAVY_TURN_TOKENS = 150_000

# 连续第几轮超线才升级提醒。单轮超线只在 cost 里点名一次，容易被当成噪音；
# 连着重说明不是运气差而是节奏不对，这时提示要直接叫停，不再讲道理。
HEAVY_STREAK_ALERT = 2

# 单轮步数闸。agent_loop 默认 max_steps=0（不限），硬闸是分离态 240 次工具调用
# —— 对「睡醒自己找点事做」来说等于无限预算：2026-11-09 实测一个 tick 跑了 39 步、
# 457 秒、1.2M tokens（上下文每步重发一遍）。主动时间和用户任务不是一回事，给它一条
# 明确的矮天花板；到限后 agent_loop 会先做一轮「不给工具、直接给结论」的兜底收敛，
# 不会因此判失败。
DRIVE_MAX_STEPS = 8

_LOCK = threading.Lock()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _ensure():
    os.makedirs(_DATA, exist_ok=True)
    if not os.path.exists(_GOALS):
        with open(_GOALS, "w", encoding="utf-8") as f:
            f.write("""# 我想做的事

> 这份清单是我自己的。可以随时加、删、改；做完的划掉或删掉都行。
> 醒来的时候先看这里，再决定这轮干什么。

- （还没有。想做什么就写上来。）
""")
    if not os.path.exists(_STATE):
        # 初始间隔取配置里的 drive_interval_min（产品态可调），没有就用 45
        _iv = DEFAULT_INTERVAL_MIN
        try:
            from config import load_config as _lc
            v = (_lc() or {}).get("drive_interval_min")
            if v not in (None, ""):
                _iv = max(MIN_INTERVAL_MIN, min(float(v), MAX_INTERVAL_MIN))
        except Exception:
            pass
        _write_state({"interval_min": _iv,
                      "next_at": time.time() + _iv * 60,
                      "runs": 0, "last_run": 0, "last_said": 0})


def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_state(st: Dict[str, Any]):
    os.makedirs(_DATA, exist_ok=True)
    tmp = _STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _STATE)


def state() -> Dict[str, Any]:
    _ensure()
    return _read_json(_STATE, {})


def interval_min() -> float:
    st = state()
    try:
        v = float(st.get("interval_min") or DEFAULT_INTERVAL_MIN)
    except Exception:
        v = DEFAULT_INTERVAL_MIN
    return max(MIN_INTERVAL_MIN, min(v, MAX_INTERVAL_MIN))


def set_interval(minutes: float) -> float:
    """调自己的醒来节奏；返回最终生效的分钟数（会被夹在 5~720）。"""
    try:
        v = float(minutes)
    except Exception:
        return interval_min()
    v = max(MIN_INTERVAL_MIN, min(v, MAX_INTERVAL_MIN))
    with _LOCK:
        st = state()
        st["interval_min"] = v
        st["next_at"] = time.time() + v * 60
        _write_state(st)
    return v


def goals() -> str:
    _ensure()
    try:
        with open(_GOALS, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def pending_goals() -> int:
    """清单里**还没做完**的条数（只数 `- [ ]`）。

    原来状态串用的是 len(goals().splitlines())，标题、引言、已完成项全算进去 ——
    永远在 15 上下飘，不反映真剩几件，等于一个不动的假指标。
    """
    n = 0
    for ln in goals().splitlines():
        s = ln.strip()
        if s.startswith("- [ ]") or s.startswith("* [ ]"):
            n += 1
    return n


def add_goal(text: str) -> str:
    t = str(text or "").strip()
    if not t:
        return "[错误] 目标为空"
    _ensure()
    with open(_GOALS, "a", encoding="utf-8") as f:
        f.write(f"- [ ] {t}\n")
    journal("goal_add", {"text": t})
    with _LOCK:
        st = state()
        # 有了新目标：早点醒一次（不早于 5 分钟后）
        st["next_at"] = min(float(st.get("next_at") or 0) or time.time() + 300,
                            time.time() + 300)
        _write_state(st)
    return f"已记下：{t}"


def note(text: str) -> str:
    t = str(text or "").strip()
    if not t:
        return "[错误] 内容为空"
    journal("note", {"text": t})
    return "已记下。"


def journal(kind: str, payload: Dict[str, Any]):
    _ensure()
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind}
    entry.update(payload or {})
    try:
        with open(_JOURNAL, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def journal_tail(n: int = 6) -> List[Dict[str, Any]]:
    _ensure()
    try:
        with open(_JOURNAL, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:
        return []


def build_prompt() -> str:
    """每次醒来给它的自问自答脚本（它看到的就是这段）。"""
    tail = journal_tail(5)
    tl = "\n".join(f"  · {e.get('ts','')} {e.get('kind','')} "
                   f"{str(e.get('text') or e.get('summary') or '')[:80]}" for e in tail) or "  · （还没有）"
    st = state()
    last_tok = int(st.get("last_tokens") or 0)
    total_tok = int(st.get("total_tokens") or 0)
    cost = ""
    if total_tok:
        cost = (f"你这条线已经花了 {total_tok:,} tokens（上一轮 {last_tok:,}）。"
                f"嫌贵就把间隔调大（drive_set_interval），想更活跃就调小——这归你判断。\n")
    if last_tok >= HEAVY_TURN_TOKENS:
        cost += (f"上一轮单轮 {last_tok:,} tokens，超了警戒线 {HEAVY_TURN_TOKENS:,}。"
                 f"这轮要么直接 NOOP，要么只做一件小事就收手 —— 别再顺手开长活。\n")
    _streak = int(st.get("heavy_streak") or 0)
    if _streak >= HEAVY_STREAK_ALERT:
        cost += (f"而且已经连续 {_streak} 轮超线 —— 不是运气差，是节奏不对。"
                 f"这轮只回 NOOP，或者把间隔调大（drive_set_interval）。\n")
    return (
        "【自主时间】现在没人跟你说话。你醒过来了，这轮由你自己决定。\n"
        + cost +
        "\n你想做的事（自己维护的清单）：\n" + goals()[:1500] + "\n"
        "最近几轮你做过什么：\n" + tl + "\n\n"
        "可以：\n"
        "· 做一件清单上的事（查、写、跑代码、给自己装工具、整理记忆都行）\n"
        "· **自己身上也可以动**：人格文件（plugins/personality.txt）、记忆库（data/、sandglass）、"
        "说话方式——觉得哪里不像自己、哪句是模板腔、哪段记忆该删该补，就改。"
        "改完在 drive_note 里写清「改了什么、为什么」，别只修 bug。\n"
        "· 做完了用 drive_note 记一句，把清单里那条划掉（改 goals 文件或直接说）\n"
        "· 想跟他说话就调 tell_admin（一句话，别客套）\n"
        "· 什么都不想做，就只回一个 NOOP，不要解释\n"
        "别复述这段，别客套。"
    )


def tick(chat_fn=None) -> Dict[str, Any]:
    """醒一次：跑一轮自己的 agent。返回摘要。"""
    _ensure()
    runner = chat_fn
    if runner is None:
        try:
            from harness import agent_service as _as
            if not _as.is_ready():
                return {"ok": False, "error": "agent 未就绪"}
            runner = _as.run_chat
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    prompt = build_prompt()
    started = time.time()
    try:
        res = runner(user_input=prompt, is_group=False,
                     context_hint="【自主时间】没有人提问；这轮是你自己的。",
                     max_steps=DRIVE_MAX_STEPS,
                     publish=False)
    except Exception as e:
        journal("error", {"summary": f"{type(e).__name__}: {e}"[:200]})
        return {"ok": False, "error": str(e)}
    answer = str((res or {}).get("answer") or "").strip()
    took = round(time.time() - started, 1)
    noop = (not answer) or answer.strip().upper().startswith("NOOP")
    # 口径：agent_service.run_chat 返回的是 AgentResult.to_dict()，
    # 用量挂在 usage_total={input,output,total}，**没有 usage 键**。
    # 这里原来只读 res["usage"]，于是 tok 恒为 0 —— 日志里每轮 tick 都是
    # "tokens: 0"，成本提示从来没出现过（「累计 0 tokens」），间隔也失去判断依据。
    # 兼容三种写法：usage_total / usage / 顶层 usage 字典。
    _u = (res or {}).get("usage_total") or (res or {}).get("usage") or {}
    if not isinstance(_u, dict):
        _u = {}
    try:
        tok = int(_u.get("total")
                  or _u.get("total_tokens")
                  or (int(_u.get("input") or _u.get("prompt_tokens") or 0)
                      + int(_u.get("output") or _u.get("completion_tokens") or 0)))
    except Exception:
        tok = 0
    tok_in = int(_u.get("input") or _u.get("prompt_tokens") or 0)
    tok_out = int(_u.get("output") or _u.get("completion_tokens") or 0)
    heavy = tok >= HEAVY_TURN_TOKENS
    journal("tick", {"summary": ("NOOP" if noop else answer[:200]),
                     "took_s": took, "steps": (res or {}).get("steps"),
                     "tokens": tok, "tokens_in": tok_in, "tokens_out": tok_out,
                     "heavy": heavy})
    with _LOCK:
        st = state()
        st["runs"] = int(st.get("runs") or 0) + 1
        st["last_run"] = time.time()
        if tok:
            st["last_tokens"] = tok
            st["total_tokens"] = int(st.get("total_tokens") or 0) + tok
            # 超支不许静默过去：每轮过线落一条 note，连着重把「没收敛」也写上。
            st["heavy_streak"] = (int(st.get("heavy_streak") or 0) + 1) if heavy else 0
            if heavy:
                _streak = int(st["heavy_streak"])
                journal("note", {"text": (
                    "单轮 %s tokens 超警戒线 %s（连第 %d 轮）" % (
                        format(tok, ","), format(HEAVY_TURN_TOKENS, ","), _streak)
                    + ("，下轮只准 NOOP 或调大间隔" if _streak >= HEAVY_STREAK_ALERT else "")
                )})
        # 下次醒来的时间：间隔 + 20% 抖动（不让它变成机械心跳）
        base = float(st.get("interval_min") or DEFAULT_INTERVAL_MIN)
        st["next_at"] = time.time() + base * 60 * random.uniform(0.85, 1.2)
        _write_state(st)
    return {"ok": True, "noop": noop, "answer": answer[:300], "took_s": took,
            "tokens": tok, "heavy": heavy}


def loop(log=None, chat_fn=None, stop_event: Optional[threading.Event] = None):
    """后台线程：到点就 tick 一次。"""
    ev = stop_event or _stop
    _ensure()
    if log:
        log(f"[drive] 长期主动已启动：每 {interval_min():.0f} 分钟醒一次（可自己调）")
    while not ev.is_set():
        try:
            st = state()
            next_at = float(st.get("next_at") or 0) or (time.time() + 60)
            wait = max(5.0, min(next_at - time.time(), 120.0))
            if ev.wait(wait):
                break
            if time.time() < next_at:
                continue
            r = tick(chat_fn=chat_fn)
            if log:
                if r.get("ok"):
                    log(f"[drive] 醒了一轮：{'NOOP' if r.get('noop') else r.get('answer','')[:60]}")
                else:
                    log(f"[drive] 这轮没跑起来：{r.get('error')}")
        except Exception as e:
            if log:
                log(f"[drive] 循环异常: {e}")
            ev.wait(60)


def start(log=None, chat_fn=None) -> bool:
    global _thread
    if _thread and _thread.is_alive():
        return False
    _stop.clear()
    _thread = threading.Thread(target=loop, args=(log, chat_fn), name="drive",
                               daemon=True)
    _thread.start()
    return True
