# -*- coding: utf-8 -*-
"""autonomy.capability_gap —— 能力缺口检测（设计 §10.2）。

只认三类事实信号，不做关键词匹配：
  1. 工具缺失：模型想调的工具不在注册表；
  2. 调用失败：错误是依赖缺失类；
  3. 自述不足：reasoning/正文明确说"没有 X 能力"，且给出具体能力名。

第三类单独设闸：没有具体能力名就只算"自知"，不进循环。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, Optional, Tuple

_DEP_PATTERNS = (
    re.compile(r"ModuleNotFoundError|No module named", re.I),
    re.compile(r"No such file or directory|找不到文件", re.I),
    re.compile(r"command not found|不是内部或外部命令", re.I),
    re.compile(r"Connection refused|拒绝连接", re.I),
    re.compile(r"ImportError|cannot import name", re.I),
)

_SELF_PATTERNS = (
    re.compile(r"(?:我|这边|这里)[^。\n]{0,20}(?:没有|缺|做不到|无法)[^。\n]{0,10}(?:能力|功能|工具)", re.I),
    re.compile(r"(?:没有|缺)(?:一个)?[^。\n]{0,12}(?:能力|工具)", re.I),
)


@dataclass
class Gap:
    kind: str           # missing_tool / dependency / self_report
    need: str           # 具体能力名（tts / asr / ocr / ...）
    evidence: str = ""
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def missing_tool(tool: str, available: Iterable[str], *, need: str = "") -> Optional[Gap]:
    t = str(tool or "").strip()
    if not t:
        return None
    have = {str(x) for x in (available or [])}
    if t in have:
        return None
    return Gap("missing_tool", str(need or t), evidence=f"工具 {t} 不在注册表", source="tool_call")


def dependency_failure(error: str, *, need: str = "") -> Optional[Gap]:
    s = str(error or "")
    if not s:
        return None
    for p in _DEP_PATTERNS:
        if p.search(s):
            return Gap("dependency", str(need or ""), evidence=s[:240], source="tool_error")
    return None


_NEED_WORDS = (
    (("语音", "说话", "声音", "朗读", "读给我听"), "tts"),
    (("听懂", "语音识别", "转文字", "听写"), "asr"),
    (("图里", "识别文字", "ocr", "文字识别"), "ocr"),
    (("画图", "生成图片", "文生图", "做张图"), "image"),
)


def infer_need(text: str) -> str:
    """从自述里抽一个具体能力名；抽不出返回空串（此时不进自主循环）。"""
    s = str(text or "").lower()
    for words, need in _NEED_WORDS:
        if any(w.lower() in s for w in words):
            return need
    return ""


def self_report(text: str, *, need: str = "") -> Optional[Gap]:
    """只认"明确说没有某能力 + 给出具体能力名"的自述。"""
    s = str(text or "")
    if not s or not str(need or "").strip():
        return None
    if not any(p.search(s) for p in _SELF_PATTERNS):
        return None
    return Gap("self_report", str(need), evidence=s[:240], source="reasoning")


def detect(*, tool_calls: Iterable[str] = (), available: Iterable[str] = (),
           errors: Iterable[Tuple[str, str]] = (), texts: Iterable[str] = (),
           need_hint: str = "") -> list:
    """按优先级收集缺口：missing_tool > dependency > self_report。"""
    gaps = []
    for name in tool_calls or ():
        g = missing_tool(name, available, need=need_hint)
        if g:
            gaps.append(g)
    for name, err in errors or ():
        g = dependency_failure(err, need=name or need_hint)
        if g:
            gaps.append(g)
    for t in texts or ():
        g = self_report(t, need=need_hint)
        if g:
            gaps.append(g)
    # 去重（kind+need）
    seen, out = set(), []
    for g in gaps:
        k = (g.kind, g.need)
        if k not in seen:
            seen.add(k)
            out.append(g)
    return out
