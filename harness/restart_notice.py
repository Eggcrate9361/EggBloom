# -*- coding: utf-8 -*-
"""重启后「我回来了」的通报（2026-11-09）。

## 干什么

hotrestart 决定重启前，先往 `data/restart_notice.json` 写一张便条：谁让我
重启的、该回哪句话的地方、以及「预计什么时候真的断掉」。进程起来后
`bot_core.main()` 读到这张便条，就朝原来那个会话发一句话，然后把便条撕掉。

## 话从哪来（2026-11-09 改）

第一版是**查表**：按「走了多久」「现在几点」拼一句，同一档随机取。稳，但
听两次就腻 —— 天天都是「瞬移回来了」「出去 3 分钟，回来了」。用户原话是
「能不能智能一点？回来了多单调」。

现在主体交给**模型现场造句**：告诉它走了多久、现在几点，让它在这个会话的
人格与记忆里自己挑一句。`bot_core.call_deepseek` 走的是主链，`target_qq` /
`is_group` 一带，记忆检索（memory_api.recall）和人格都在里面，所以模型知道
「跟谁说话、之前聊到哪」，不用这里再拼背景。

`build_text` 退成**兜底**：模型超时 / 报错 / 回空 / 回了后台术语（重启、进程、
服务器…）就用它按档挑一句。宁可单调，也不能空手回来或者自曝后台。

## 落了哪些坑

· `at` 记的是「真正断掉的时刻」= 请求时间 + 延迟，不是请求时间 —— 否则
  启动时算出来是负数，会报「出去 -3 秒」。
· 便条只发一次：发完就 `clear_marker()`。发失败也清，不然每次重启都会
  往同一个会话重复吐同一句话。
· 读/写/清一律不抛异常：便条坏掉不能拦住 bot 启动，那是最亏的事。
· 通报要**另开线程**发：模型那一趟可能几十秒，卡在 `main()` 里就是启动卡住。
"""
from __future__ import annotations

import json
import os
import random
import re
import time

#: 便条位置（项目根/data/restart_notice.json）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKER_PATH = os.path.join(_ROOT, "data", "restart_notice.json")


# ── 便条读写 ────────────────────────────────────────────────

def write_marker(target, is_group: bool = False, kind: str = "restart",
                 at: float = 0.0, path: str = "") -> bool:
    """写便条。target 必填（没目标就没人可通报，直接返回 False）。"""
    target = str(target or "").strip()
    if not target:
        return False
    p = path or MARKER_PATH
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        data = {"target": target,
                "is_group": bool(is_group),
                "kind": str(kind or "restart"),
                "at": float(at or time.time()),
                "requested_at": time.time()}
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def read_marker(path: str = "") -> dict:
    """读便条。没有 / 坏了 → {}（调用方不用判 None）。"""
    p = path or MARKER_PATH
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and str(data.get("target") or "").strip():
            return data
    except Exception:
        pass
    return {}


def clear_marker(path: str = "") -> None:
    p = path or MARKER_PATH
    for f in (p, p + ".tmp"):
        try:
            os.remove(f)
        except Exception:
            pass


# ── 兜底：查表挑一句 ────────────────────────────────────────

def _elapsed_line(elapsed: float, rng) -> str:
    if elapsed < 15:
        return rng.choice([
            "瞬移回来了。",
            "一闭眼一睁眼就回来了。",
            "还没走远就回来了。",
        ])
    if elapsed < 180:
        e = int(elapsed)
        return rng.choice([
            f"出去 {e} 秒，回来了。",
            f"{e} 秒。回来了。",
            f"才断了 {e} 秒，接上了。",
        ])
    if elapsed < 3600:
        e = max(1, int(round(elapsed / 60.0)))
        return rng.choice([
            f"出去 {e} 分钟，回来了。",
            f"{e} 分钟，够我出去转一圈。回来了。",
            f"断了 {e} 分钟，回来了。",
        ])
    if elapsed < 86400:
        e = round(float(elapsed) / 3600.0, 1)
        e = int(e) if abs(e - round(e)) < 0.05 else e
        return rng.choice([
            f"出去 {e} 小时，回来了。",
            f"{e} 小时的活儿，我又爬回来了。",
        ])
    e = round(float(elapsed) / 86400.0, 1)
    e = int(e) if abs(e - round(e)) < 0.05 else e
    return rng.choice([
        f"走了 {e} 天，回来了。",
        f"{e} 天，不认得路也认得你。",
    ])


def _period_phrase(hour: int, rng) -> str:
    """时段前缀。空串表示不需要前缀（让「回来了」自己说话）。"""
    if 0 <= hour < 5:
        return rng.choice(["这个点还没睡？", "半夜了。", "这么晚。"])
    if 5 <= hour < 9:
        return rng.choice(["这个点起来了。", "早。", ""])
    if 9 <= hour < 12:
        return rng.choice(["", "", "上午。"])
    if 12 <= hour < 14:
        return rng.choice(["", "这个点，饭吃了没。", ""])
    if 14 <= hour < 18:
        return rng.choice(["", "", "下午。"])
    if 18 <= hour < 23:
        return rng.choice(["", "", "这个点。"])
    return rng.choice(["", "快十二点了。"])


def build_text(elapsed, hour, rng=None) -> str:
    """兜底句：按「离开多久 + 现在几点」拼一句。elapsed 单位秒，hour 0-23。"""
    rng = rng or random
    try:
        elapsed = max(0.0, float(elapsed))
    except (TypeError, ValueError):
        elapsed = 0.0
    try:
        hour = int(hour) % 24
    except (TypeError, ValueError):
        hour = time.localtime().tm_hour
    line = _elapsed_line(elapsed, rng)
    pre = _period_phrase(hour, rng)
    return (pre + line) if pre else line


# ── 主路：让模型现场造一句 ──────────────────────────────────

def describe_gap(elapsed) -> str:
    """把秒数说成人话，给模型看。"""
    try:
        e = max(0.0, float(elapsed))
    except (TypeError, ValueError):
        e = 0.0
    if e < 15:
        return "几秒，几乎没断"
    if e < 120:
        return f"{int(e)} 秒"
    if e < 5400:
        return f"{max(1, int(round(e / 60.0)))} 分钟"
    if e < 172800:
        v = round(e / 3600.0, 1)
        v = int(v) if abs(v - round(v)) < 0.05 else v
        return f"{v} 小时"
    v = round(e / 86400.0, 1)
    v = int(v) if abs(v - round(v)) < 0.05 else v
    return f"{v} 天"


def build_prompt(elapsed, hour, now_str: str = "") -> str:
    """给模型的指令。故意短 —— 长指令会把它催成解说腔。"""
    gap = describe_gap(elapsed)
    hh = now_str or f"{int(hour) % 24:02d}:00"
    return (
        f"（系统事件，不是对方说的话）你刚断线了一会儿，现在回来了："
        f"离线约 {gap}，当前时间 {hh}。\n"
        "就这个事实，朝对方说一句话。要求：\n"
        "· 一句话，15 字以内，可以有语气词，别分段\n"
        "· 别解释你去了哪，别提重启/掉线/进程/服务器/程序/系统这些词\n"
        "· 结合你记得的这个人、这段时间在聊什么，别用通用客套\n"
        "· 只输出这一句，不要引号、不要前后缀、不要旁白"
    )


#: 模型嘴里漏出来的后台术语 —— 出现就判定这句不能用，走兜底
_BACKSTAGE = re.compile(
    r"重启|重载|掉线|断线|下线|进程|服务端|服务器|后台|程序|系统|"
    r"数据库|接口|故障|报错|加载|初始化|部署", re.I)

_MAX_LEN = 32


def clean_reply(text) -> str:
    """清洗模型输出。不合格 → 空串（调用方走兜底）。"""
    t = str(text or "").strip()
    if not t:
        return ""
    # [不说话] 这类工具标记直接作废
    if t.startswith("[") and t.endswith("]"):
        return ""
    t = t.strip("\"'“”‘’「」『』 \n\t").replace("**", "").replace("\\n", " ")
    t = re.sub(r"\s*\n+\s*", "，", t).strip("，, ")
    # 模型偶尔多写解释：只留第一句
    m = re.match(r"^(.+?[。！？!?~～…]+)", t)
    if m:
        t = m.group(1)
    t = t.strip("\"'“”‘’「」『』 ")
    if not t or len(t) > _MAX_LEN:
        return ""
    if _BACKSTAGE.search(t):
        return ""
    return t


def compose(elapsed, hour, target: str = "", is_group: bool = False,
            call_ai=None, rng=None, now_str: str = "") -> str:
    """造一句「回来了」。

    call_ai：bot_core.call_deepseek（同步版）。给了就先让它现场造，
    造不出来（超时 / 报错 / 空 / 不合格）再退回 build_text。
    """
    rng = rng or random
    fallback = build_text(elapsed, hour, rng)
    if call_ai is None:
        return fallback
    try:
        raw = call_ai(
            build_prompt(elapsed, hour, now_str),
            target_qq=(None if is_group else (target or None)),
            group_id=(str(target) if is_group else None),
            is_group=bool(is_group),
        )
    except Exception:
        return fallback
    line = clean_reply(raw)
    return line or fallback
