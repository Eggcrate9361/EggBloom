# -*- coding: utf-8 -*-
"""harness/trace_tools.py —— 可观测三件套（FIXPLAN 1.5.11 §4.5 M1）

> 用户主线：agent 要强到「能自己修自己」。但自修复的第一步不是改代码，是**看得见**
> —— 得先能回答「今天最常失败的是哪条链路」「这次失败的全链路长什么样」。

三件套（全部只读，零副作用）：

    failure_top(hours=24, n=3)      今天失败最多的 N 条链路（按类别聚合 + 样例行）
    log_search(pattern, hours, …)   正则搜日志（先试正则，不是正则就按字面量）
    trace_get(trace_id="")          拉某个 trace 的全链路；不传 id 就用日志里最后一个

外加断言/回滚用的台账（M2 的 R1/R2 会往里写）：

    data/repairs.jsonl   一行一次修复 {"ts", "kind", "target", "action", "ok", "detail"}

日志格式（`data/logs/qq_bot.log`）：

    [2026-10-31 13:41:17] [Sandglass] 人物画像已载入 3 人
    [2026-10-31 13:41:17] [工具] search_web 失败: timeout trace=9f2c…

判定口径：**出现失败关键词**才算候选，再按链路正则归类（第一条命中即停）。
分不出类的落「其他」，不丢 —— 未知失败本身就是要看见的东西。
"""
import io
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 日志与台账路径（测试用环境变量覆盖，别指向真实文件）
LOG_PATH = os.environ.get("EGGBLOOM_LOG_FILE") or os.path.join(
    _ROOT, "data", "logs", "qq_bot.log")
REPAIRS_PATH = os.environ.get("EGGBLOOM_REPAIRS_FILE") or os.path.join(
    _ROOT, "data", "repairs.jsonl")

#: 失败关键词
_FAIL_KW = ("失败", "异常", "错误", "超时", "timeout", "Timeout", "TIMEOUT",
            "❌", "Traceback", "error", "Error", "ERROR", "未通过", "未就绪",
            "断开", "掉线", "disconnect")

#: 非零返回码也算失败（协议层的 `retcode=3` 不是"失败"两个字，但就是没成）
_FAIL_RE = re.compile(r"retcode\s*[=:]\s*(?!0\b)\d+")

#: 链路归类的正则（**顺序即优先级**）
#: `[工具]` 前缀要先于"搜索"判定 —— 否则 "search_web 失败" 会被归成联网搜索，
#: 而它其实是工具调用链（工具没跑通，不一定是网断了）。
_CHANNELS = (
    ("读图/视觉", re.compile(r"图片理解|视觉|vision|看图|OCR", re.I)),
    ("工具调用", re.compile(r"\[工具\]|工具调用|tool_|py_run|web_operate|job_", re.I)),
    ("联网搜索", re.compile(r"搜索|search|联网|Bing|google", re.I)),
    ("模型接口", re.compile(r"API|DeepSeek|token|429|50[0234]|rate.?limit", re.I)),
    ("协议层", re.compile(r"协议|OneBot|retcode|WebSocket|napcat|llonebot", re.I)),
    ("插件", re.compile(r"\[Plugin|插件|hook", re.I)),
    ("记忆", re.compile(r"\[记忆\]|沙漏|sandglass|SuperMemory|记忆", re.I)),
    ("人格/prompt", re.compile(r"\[prompt\]|人格|persona|段渲染", re.I)),
)

_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s?(.*)$")
_TRACE_RE = re.compile(r"trace=([0-9a-f]{6,32})")


# ── 读日志 ──────────────────────────────────────────────────────────

def _parse_ts(line: str) -> Optional[float]:
    m = _TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


def _iter_lines(hours: float = 24, path: str = ""):
    """按时间窗读日志。没有时间戳的行沿用上一条的时间（多行 traceback）。"""
    path = path or LOG_PATH
    try:
        f = io.open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    cutoff = time.time() - float(hours or 24) * 3600
    last_ts = None
    with f:
        for line in f:
            line = line.rstrip("\n")
            ts = _parse_ts(line)
            if ts is not None:
                last_ts = ts
                if ts < cutoff:
                    continue
            elif last_ts is not None and last_ts < cutoff:
                continue
            yield last_ts, line


def _channel_of(text: str) -> str:
    for name, pat in _CHANNELS:
        if pat.search(text):
            return name
    return "其他"


def failure_top(hours: float = 24, n: int = 3, path: str = "") -> List[Dict[str, Any]]:
    """今天最常失败的 N 条链路（按类别聚合，带计数与样例行）。"""
    buckets: Dict[str, List[str]] = {}
    for _ts, line in _iter_lines(hours, path):
        body = _TS_RE.match(line)
        text = body.group(2) if body else line
        if not (any(k in text for k in _FAIL_KW) or _FAIL_RE.search(text)):
            continue
        buckets.setdefault(_channel_of(text), []).append(text.strip()[:160])
    ranked = sorted(buckets.items(), key=lambda kv: -len(kv[1]))
    out = []
    for name, rows in ranked[:max(1, int(n or 3))]:
        out.append({"channel": name, "count": len(rows), "samples": rows[:2]})
    return out


def log_search(pattern: str = "", hours: float = 24, limit: int = 30,
               level: str = "", path: str = "") -> Dict[str, Any]:
    """正则搜日志。pattern 不是合法正则时按**字面量**匹配（不因为手滑就搜不到）。"""
    pat = str(pattern or "").strip()
    rx = None
    if pat:
        try:
            rx = re.compile(pat, re.I)
        except re.error:
            rx = None
    lvl = str(level or "").strip().lower()
    hits = []
    total = 0
    for _ts, line in _iter_lines(hours, path):
        m = _TS_RE.match(line)
        text = m.group(2) if m else line
        if lvl and lvl not in text.lower():
            continue
        if pat:
            ok = bool(rx.search(text)) if rx else (pat.lower() in text.lower())
            if not ok:
                continue
        total += 1
        if len(hits) < max(1, int(limit or 30)):
            hits.append(line.strip()[:200])
    return {"pattern": pat, "total": total, "returned": len(hits), "lines": hits}


def trace_get(trace_id: str = "", limit: int = 40, hours: float = 24,
              path: str = "") -> Dict[str, Any]:
    """拉某个 trace 的全链路；不传 id 就用日志里最后一个。"""
    tid = str(trace_id or "").strip()
    if not tid:
        last = ""
        for _ts, line in _iter_lines(hours, path):
            m = _TRACE_RE.search(line)
            if m:
                last = m.group(1)
        tid = last
    if not tid:
        return {"trace_id": "", "count": 0, "lines": [],
                "hint": "日志里没有 trace=（可能没开 trace 或这次运行很短）"}
    hits = []
    for _ts, line in _iter_lines(hours, path):
        if tid in line:
            hits.append(line.strip()[:200])
    return {"trace_id": tid, "count": len(hits),
            "lines": hits[:max(1, int(limit or 40))]}


# ── 修复台账（M2 的 R1/R2 往里写）──────────────────────────────────────

def record_repair(kind: str, target: str, action: str, ok: bool,
                  detail: str = "") -> bool:
    """记一条修复。kind: config/cache/snapshot/test；返回是否写成功。"""
    try:
        os.makedirs(os.path.dirname(REPAIRS_PATH), exist_ok=True)
        row = {"ts": time.time(),
               "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "kind": str(kind or ""), "target": str(target or ""),
               "action": str(action or ""), "ok": bool(ok),
               "detail": str(detail or "")[:300]}
        with io.open(REPAIRS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def list_repairs(hours: float = 168, limit: int = 20) -> List[Dict[str, Any]]:
    """最近 N 条修复记录（默认一周）。文件不存在 → 空列表。"""
    try:
        rows = []
        cutoff = time.time() - float(hours or 168) * 3600
        with io.open(REPAIRS_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if float(row.get("ts") or 0) >= cutoff:
                    rows.append(row)
        rows.sort(key=lambda r: r.get("ts", 0), reverse=True)
        return rows[:max(1, int(limit or 20))]
    except OSError:
        return []


# ── 工具 schema ─────────────────────────────────────────────────────

TRACE_TOOLS = [
    {"type": "function", "function": {
        "name": "failure_top",
        "description": ("看自己今天最常失败的是哪几条链路（读图/搜索/工具/模型/协议…），"
                        "带计数和样例日志行。排查『最近怎么老出错』时先用它。"),
        "parameters": {"type": "object", "properties": {
            "hours": {"type": "number", "description": "往前看多少小时，默认 24"},
            "n": {"type": "number", "description": "取前几名，默认 3"},
        }, "required": []},
    }},
    {"type": "function", "function": {
        "name": "log_search",
        "description": ("按关键词/正则搜运行日志（默认今天）。排查具体报错、"
                        "确认某件事有没有发生时用；返回命中总数和前 N 行原文。"),
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "关键词或正则，可空=不过滤"},
            "hours": {"type": "number", "description": "往前看多少小时，默认 24"},
            "limit": {"type": "number", "description": "最多返回几行，默认 30"},
        }, "required": []},
    }},
    {"type": "function", "function": {
        "name": "trace_get",
        "description": ("拉某一次对话/任务的全链路日志。已知 trace id 就传；"
                        "不传则用日志里最近的一个。"),
        "parameters": {"type": "object", "properties": {
            "trace_id": {"type": "string", "description": "12 位 trace id，可空"},
            "limit": {"type": "number", "description": "最多几行，默认 40"},
        }, "required": []},
    }},
    {"type": "function", "function": {
        "name": "repairs_list",
        "description": "看最近的自动修复记录（改过哪个开关/清了什么缓存/回滚了什么），"
                       "以及每条有没有成功。",
        "parameters": {"type": "object", "properties": {
            "hours": {"type": "number", "description": "往前看多少小时，默认 168"},
            "limit": {"type": "number", "description": "最多几条，默认 20"},
        }, "required": []},
    }},
]


def register_all() -> Dict[str, Any]:
    """注册进 harness.tool_registry（work 组：只读、常驻）。"""
    from . import tool_registry as _tr
    fns = {
        "failure_top": failure_top,
        "log_search": log_search,
        "trace_get": trace_get,
        "repairs_list": list_repairs,
    }
    for sch in TRACE_TOOLS:
        name = sch["function"]["name"]
        _tr.register(name, sch, fn=_tr.adapt_kwargs(fns[name]),
                     groups=(_tr.GROUP_WORK,), source="harness")
    return {k: _tr.adapt_kwargs(v) for k, v in fns.items()}


__all__ = ["TRACE_TOOLS", "failure_top", "log_search", "trace_get",
           "list_repairs", "record_repair", "register_all"]


if __name__ == "__main__":
    # 人工入口：python -m harness.trace_tools
    print("今天最常失败的链路：")
    _rows = failure_top(hours=24, n=3)
    if not _rows:
        print("  （24 小时内没有失败记录）")
    for i, r in enumerate(_rows, 1):
        print(f"  {i}. {r['channel']} × {r['count']}")
        for s in r.get("samples") or []:
            print(f"     - {s[:100]}")
    _rep = list_repairs(hours=168, limit=5)
    print(f"\n最近修复记录：{len(_rep)} 条")
    for r in _rep:
        flag = "OK" if r.get("ok") else "失败"
        print(f"  [{flag}] {r.get('time')} {r.get('kind')} {r.get('target')} → {r.get('action')}")
