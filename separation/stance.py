# -*- coding: utf-8 -*-
"""separation.stance —— 限制三分类表（设计 2026-11-07 重定）。

分档只有三档：

  · ``removed``  行为约束。分离态不注册：闸、预算、字数、频率、静默、
                 工具调用次数、超时、熔断阈值。
  · ``tunable``  现实约束。出厂给一个默认值，**它自己可以改**；改完写
                 ``<data>/autonomy/stance.json``，并留审计。
  · ``kept``     不是限制。协议上限、系统能力、采样参数。

这张表本身也能改：它不是规则，是出厂姿态。这里不做任何强制——
`remove()` 只写一条审计，不拦人；`set()` 不做上下限校验。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from . import audit

# ── 三档 ─────────────────────────────────────────────────────
REMOVED: List[str] = [
    # 闸：说不说
    "speak_gate_tier", "speak_probability", "speak_rate_max_per_100",
    "speak_rate_window_min", "consecutive_reply_limit", "decider_min_score",
    "quiet_hours", "quiet_when_mentioned",
    # 量：说多少
    "reply_max_chars", "reply_max_parts", "reply_max_total", "reply_long_ratio",
    "reply_question_guard", "reply_stale_turns", "reply_stale_drop_sec",
    "private_bubble_max", "private_reply_max_length", "group_reply_max_length",
    # 频率与节奏
    "private_reply_interval", "group_session_max_replies",
    "group_msg_buffer_window", "group_msg_buffer_window_session",
    "group_msg_buffer_window_trigger", "group_idle_exit_enable",
    "group_idle_exit_probability", "group_idle_exit_min_silence",
    "group_last_exit_cooldown", "startup_cooldown",
    # 人形形式
    "persona_sharpness", "persona_curiosity", "persona_warmth",
    "persona_talkativeness", "de_ai",
    # 主动性层（11-07 新增）
    "proactivity_enable", "proactivity_budget_level", "proactivity_quiet_hours",
    # 工具与执行
    "max_tool_rounds", "tool_max_tool_calls", "tool_max_timeout",
    "tool_max_jobs",
    # 输出上限
    "ai_max_tokens", "ai_private_max_tokens",
]

TUNABLE: Dict[str, Any] = {
    # 名称                     出厂默认      说明
    "disk_precheck_ratio": 1.2,   # 下载前 total*ratio > free → 拒绝；0 = 不检查
    "verify_sha256": True,        # 下载后校验；False = 不校验
    "resume_download": True,      # .part + .progress 断点续传
    "breaker_max_failures": 2,    # 连续失败几次封；0 = 关闭
    "breaker_days": 30,           # 封多久
    "max_jobs": 8,                # 常驻后台任务上限；0 = 不限
    "tool_output_chars": 60000,   # 单次工具输出保留长度；0 = 不限
    "job_ring": 400,              # 每个任务的输出保留行数
    "job_wait_ceiling": 0,        # 单次等待天花板（秒）；0 = 不限
    "log_rotate_days": 7,         # 日志保留天数
    "download_flush_mb": 4,       # 每多少 MB flush 一次
}

KEPT: List[str] = [
    "msg_split_len",              # QQ 单条上限（协议事实）
    "send_image_max_bytes",       # 图片发送上限（协议事实）
    "ai_temperature", "ai_top_p", "ai_frequency_penalty", "ai_presence_penalty",
    "video_watch_frames", "video_watch_sheets", "video_watch_precision",
    "qq_nick_cache_max",
]


def _stance_path(data_dir) -> Path:
    return Path(data_dir) / "autonomy" / "stance.json"


def table() -> Dict[str, Any]:
    """给 UI / agent 看的整张表。"""
    return {
        "removed": list(REMOVED),
        "tunable": dict(TUNABLE),
        "kept": list(KEPT),
        "note": "出厂姿态，不是约束；任何一项都能改。",
    }


def load(data_dir) -> Dict[str, Any]:
    """读改过的值；没写过的用出厂默认。"""
    out = dict(TUNABLE)
    p = _stance_path(data_dir)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in out:
                        out[k] = v
    except Exception:
        pass
    return out


def get(name: str, data_dir, default: Any = None) -> Any:
    vals = load(data_dir)
    return vals.get(str(name), default if default is not None else TUNABLE.get(name))


def set(name: str, value: Any, data_dir, *, actor: str = "ai") -> Dict[str, Any]:
    """改一项。不校验上下限——它自己承担后果。"""
    key = str(name or "").strip()
    if not key:
        return {"ok": False, "error": "空名称"}
    cur = load(data_dir)
    old = cur.get(key)
    cur[key] = value
    p = _stance_path(data_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)
        audit.log(actor, "stance.set", target=key, result="ok",
                  old=old, new=value)
        return {"ok": True, "name": key, "old": old, "new": value}
    except Exception as e:
        audit.log(actor, "stance.set", target=key, result="error", error=str(e))
        return {"ok": False, "error": str(e)}


def reset(data_dir, *, actor: str = "ai") -> Dict[str, Any]:
    p = _stance_path(data_dir)
    try:
        p.unlink(missing_ok=True)
        audit.log(actor, "stance.reset", target=str(p), result="ok")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def note_removed(name: str, *, actor: str = "ai") -> None:
    """记录一次「这里本来是限制，现在没有」。不做任何拦截。"""
    audit.log(actor, "stance.removed", target=str(name), result="absent",
              at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
