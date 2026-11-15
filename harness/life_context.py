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

## 1.6.0 刀 2（2026-11-01）：主链接得上了

`compose()` 只认固定的 8 个 key，而主链 `_call_deepseek_sync` 实际有 20 来块
（时间/睡觉/群身份/工具清单/派活提示…），直接套 `compose()` 会把没登记的块**整块丢掉**。
所以补两个东西，成对使用：

- `Parts`：有序块收集器，`add(key, text)` 记下来源与顺序；
- `merge(parts)`：按收集顺序合成一段（可去重、可整块丢尾），不依赖固定 key 表。

主链开关 `life_context_enable` 关 → `Parts.texts()`，与改造前逐字节同一列表；
开 → `merge(parts)` 合成一块。两种走法在 `tests/test_life_context_mainchain.py` 里钉死。
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


class Parts:
    """有序上下文块收集器（`(key, text)` 保序，空块不进）。

    为什么要 key：① 日志能说"这轮注入了哪几块"；② 合并时能按来源丢尾，
    而不是按"第几段"这种脆弱下标。为什么要保序：开关关的路径要**逐字节**
    等于改造前的 `dynamic_parts`，顺序是最容易被重构弄丢的东西。
    """

    __slots__ = ("_blocks",)

    def __init__(self, blocks: Optional[Iterable[Tuple[str, str]]] = None):
        self._blocks: List[Tuple[str, str]] = [
            (str(k), _clean(v)) for k, v in (blocks or []) if _clean(v)]

    def add(self, key: str, text) -> str:
        """记一块（空串不记），返回规范化后的文本 —— 调用方可直接接收。"""
        t = _clean(text)
        if t:
            self._blocks.append((str(key), t))
        return t

    def extend(self, key: str, texts: Iterable[str]) -> "Parts":
        for t in texts or []:
            self.add(key, t)
        return self

    @property
    def blocks(self) -> List[Tuple[str, str]]:
        return list(self._blocks)

    def texts(self) -> List[str]:
        """只剩文本、保持顺序 —— 老路径（`dynamic_parts`）就是这个形状。"""
        return [t for _, t in self._blocks]

    def __len__(self) -> int:
        return len(self._blocks)

    def __bool__(self) -> bool:
        return bool(self._blocks)

    def __repr__(self) -> str:
        return f"Parts({len(self._blocks)} 块)"


def _texts_of(parts) -> List[str]:
    """把 Parts / [(key,text)] / [text] 统一成文本列表（顺序不变）。"""
    if isinstance(parts, Parts):
        return parts.texts()
    out: List[str] = []
    for item in parts or []:
        if isinstance(item, str):
            out.append(_clean(item))
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            out.append(_clean(item[1]))
        else:
            out.append(_clean(item))
    return [t for t in out if t]


def merge(parts, *, budget: int = DEFAULT_BUDGET, dedupe: bool = True,
          sep: str = "\n\n") -> str:
    """按收集顺序合成一段（`compose()` 的"不挑 key"版本，给主链 20 来块用）。

    与 `compose()` 的区别：
      · 不靠 `ORDER`，顺序就是调用方的注入顺序（主链的顺序有语义，不能重排）；
      · 超预算**整块**从尾部丢，**不截半句**——半句比没有更容易让模型去圆；
      · 只剩一块时宁可超预算也保留（"什么都没注入"是最坏结果）。
    """
    texts = _texts_of(parts)
    if dedupe:
        seen, uniq = set(), []
        for t in texts:
            if t in seen:
                continue
            seen.add(t)
            uniq.append(t)
        texts = uniq
    limit = max(0, int(budget or 0))
    while limit and len(texts) > 1 and sum(len(t) + 2 for t in texts) > limit:
        texts.pop()
    if not texts:
        return ""
    return sep.join(texts)


__all__ = ["ORDER", "DEFAULT_BUDGET", "compose", "describe", "Parts", "merge"]
