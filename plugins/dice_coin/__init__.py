# -*- coding: utf-8 -*-
"""沙雕骰子（真插件 ABI v1）：!roll / 丢骰子 / 丢硬币 + 连续手气统计。

触发：
- `!roll` / `丢骰子`            → 1d6
- `!roll 2d20` / `丢骰子 2d20`  → 2 个 20 面骰
- `!coin` / `丢硬币`            → 正/反
手气：用 ctx.get_state 记录连胜，连赢 3 次触发「天选之子」。
"""
import random
import re

from plugins.abi import PluginContext

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "dice_coin",
    "version": "1.0.0",
    "api_version": 1,
    "author": "EggBloom",
    "description": "沙雕骰子：!roll / 丢骰子 / 丢硬币，含连续手气统计",
    "dependencies": [],
    "priority": 100,
    "default_enabled": True,
}

_ROLL_TALK = [
    "骰子滚动了……好，结果是：",
    "（搓了搓手）掷出——",
    "骰子说：爱信不信，结果是——",
    "（把骰子扔向天花板）掉下来是——",
    "命运之骰旋转中，先给你一个'悬念'，再告诉你：",
]

_COIN_TALK = {
    "正": ["正面朝上！今天可以光明正大地摸鱼。", "正面！建议现在就去买彩票（不推荐）。"],
    "反": ["反面朝上……今天的运势存疑，建议多喝水。", "反面！哦豁，出门小心左脚绊右脚。"],
}


def _roll(expr: str, ctx: PluginContext) -> str:
    m = re.match(r"^(\d*)\s*[dD]\s*(\d+)$", expr.strip())
    if m:
        count = int(m.group(1) or 1)
        sides = int(m.group(2))
        count = max(1, min(count, 20))
        sides = max(2, min(sides, 1000))
    else:
        count, sides = 1, 6
    results = [random.randint(1, sides) for _ in range(count)]
    total = sum(results)
    head = random.choice(_ROLL_TALK)
    detail = f"{results}（共 {total}）" if count > 1 else f"{results[0]}"
    # 手气统计：> 平均线记为"赢"
    avg = (1 + sides) / 2
    wins = int(ctx.get_state("streak", 0) or 0)
    if total > avg * count:
        wins += 1
        ctx.set_state("streak", wins)
    else:
        wins = 0
        ctx.set_state("streak", 0)
    streak_txt = ""
    if wins >= 3:
        streak_txt = f"\n【天选之子】你已经连续 {wins} 次赢骰了，建议收手，不然下次必输。"
    elif wins >= 2:
        streak_txt = f"\n（连续 {wins} 次手气在线，稳住）"
    return f"{head} {detail}{streak_txt}"


def _coin(ctx: PluginContext) -> str:
    face = random.choice(["正", "反"])
    wins = int(ctx.get_state("streak", 0) or 0)
    if face == "正":
        wins += 1
        ctx.set_state("streak", wins)
    else:
        wins = 0
        ctx.set_state("streak", 0)
    base = random.choice(_COIN_TALK[face])
    if wins >= 3:
        base += f"\n（第 {wins} 次正面了！这硬币是不是坏了？）"
    return base


def on_message(ctx: PluginContext, event: dict):
    text = (event.get("text") or "").strip()
    if not text:
        return None
    if text.startswith("!roll") or text.startswith("丢骰子"):
        expr = text[len("!roll" if text.startswith("!roll") else "丢骰子"):].strip() or "1d6"
        return _roll(expr, ctx)
    if text.startswith("!coin") or text.startswith("丢硬币"):
        return _coin(ctx)
    return None


PLUGIN_HOOKS = {"on_message": on_message}
