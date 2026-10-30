# -*- coding: utf-8 -*-
"""harness/life_context.py —— 统一上下文拼装（1.6.0「一个大脑」的地基，2026-10-30）。

## 为什么要先立这一层

1.6.0 的第一件事是把「agent 模式」拆掉，让主链和 agent 用**同一份上下文**：
人格 / 记忆 / 情绪关系 / 场景 / 称呼约定 / 另一边近况，都从同一个出口出。

现在这些块是散的：主链 `_call_deepseek_sync` 里手拼 dynamic_parts，
agent 侧只有一句 `context_hint`，A3 侧干脆没有 ego 和画像。
先抽一个**纯函数**出来（不 import bot_core、可离线测），1.6.0 只要把两边
都改成调它，注入顺序和预算就自然一致了 —— 这就是"铺路"。

## 契约

- 输入：`{"ego": ..., "memory": ..., "scene": ..., "alias": ..., "cross_scene": ...}`
- 输出：一段可直接拼进 system prompt 尾部的文本（空 dict → ""）
- 顺序固定（`ORDER`）：对话对象 → 刚做的事 → 记忆 → 头像 → 情绪关系 → 场景 → 称呼约定 → 另一边近况。
  固定顺序是为了**前缀缓存**：同一轮重复渲染必须逐字节一致。
- 预算：总字数超 `budget` 时**从后往前**整块丢（近况最先丢，记忆最后丢），
  不切半句 —— 半句话比没有更容易让模型去圆。
- 去重：同一段文字出现两次只留一次（ego 和 memory 常常互相包含）。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

#: 注入顺序（也是"被丢的逆序"：越靠后越先丢）。
ORDER: Tuple[str, ...] = ("who", "acts", "memory", "avatar", "ego", "scene", "alias", "cross_scene")

#: 默认预算：这几块都是"补充信息"，不该挤掉人格与当前消息。
DEFAULT_BUDGET = 2000


def _clean(v) -> str:
    return str(v or "").strip()


def compose(parts: Optional[Dict[str, str]], *, budget: int = DEFAULT_BUDGET,
            order: Iterable[str] = ORDER) -> str:
    """把各来源的上下文块拼成一段（空块自动跳过、超预算整块丢）。"""
    p = {str(k): _clean(v) for k, v in (parts or {}).items()}
    seq: List[Tuple[str, str]] = []
    seen = set()
    for key in order:
        text = p.get(key) or ""
        if not text or text in seen:
            continue
        seen.add(text)
        seq.append((key, text))
    limit = max(0, int(budget or 0))
    # 从后往前丢，直到装得下；至少保留第一块（记忆），避免"什么都没注入"
    while seq and limit and sum(len(t) + 2 for _, t in seq) > limit:
        if len(seq) == 1:
            break
        seq.pop()
    if not seq:
        return ""
    out = "\n\n".join(t for _, t in seq)
    if limit and len(out) > limit:
        out = out[:limit].rstrip()
    return out


def describe(parts: Optional[Dict[str, str]], *, budget: int = DEFAULT_BUDGET) -> Dict[str, object]:
    """调试用：这一轮注入了哪些块、各多少字、丢了什么。"""
    p = {str(k): _clean(v) for k, v in (parts or {}).items()}
    kept, dropped = [], []
    total = 0
    for key in ORDER:
        text = p.get(key) or ""
        if not text:
            continue
        if total + len(text) + 2 > max(0, int(budget or 0)) and kept:
            dropped.append(key)
            continue
        kept.append(key)
        total += len(text) + 2
    return {"kept": kept, "dropped": dropped, "chars": total}


__all__ = ["ORDER", "DEFAULT_BUDGET", "compose", "describe"]
