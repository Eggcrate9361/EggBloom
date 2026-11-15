# -*- coding: utf-8 -*-
"""autonomy.report —— 交付话术（设计 §16）。

不说“我尝试了”，说“装成了/没装成”；不报流水账，只报结论 + 一个可验证细节；
成本只说一次。
"""
from __future__ import annotations

from typing import Any, Dict


def ok_text(need: str, detail: str = "", size_mb: float = 0, seconds: float = 0) -> str:
    parts = [f"装好了。我现在能{_human_need(need)}了"]
    if detail:
        parts.append(f"（{detail}）")
    tail = []
    if size_mb:
        tail.append(f"占 {size_mb:.0f}MB")
    if seconds:
        tail.append(f"{seconds:.1f} 秒")
    s = "，".join(parts)
    if tail:
        s += "。" + "，".join(tail) + "。"
    return s


def fail_text(need: str, step: str = "", reason: str = "", retry_days: int = 30) -> str:
    s = f"{_human_need(need)}没装成。"
    if step:
        s += f"卡在 {step}："
    if reason:
        s += str(reason)
    if retry_days:
        s += f"。已经记下来，{retry_days} 天内不再试这个源。"
    return s


def _human_need(need: str) -> str:
    n = str(need or "").lower()
    return {"tts": "说话了", "asr": "听懂语音了", "ocr": "读图里的字了",
            "ffmpeg": "看视频了", "video": "看视频了",
            "image": "看图了", "imagegen": "画图了"}.get(n, f"用 {need}")
