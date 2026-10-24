# -*- coding: utf-8 -*-
"""一次性诊断：群聊 vs 私聊的 prompt 构成差多少、差在哪。

背景（1.5.8 计划 #19）
    实测群聊单次 8066 token / 私聊 5413 token，差 2650。计划要求"排查多出的
    2650 是什么"。不实测就只能猜，所以这里逐段量出来。

    同时回答一个更实际的问题：**该从哪儿砍**。砍错地方会破坏前缀缓存
    （那是 64% 命中率的来源，比这 2650 token 值钱）。

用法
    python scripts/_diag_prompt_diff.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ── 用最小配置启动 bot_core 的 prompt 构建 ──
os.environ.setdefault("EGGBLOOM_NO_BOT", "1")

import bot_core as bc  # noqa: E402


def seg_len(text):
    """粗略 token 估算：中文 1 字 ≈ 1 token，英文按词。这里只做相对比较。"""
    if not text:
        return 0
    cjk = sum(1 for c in text if ord(c) > 0x2E80)
    other = len(text) - cjk
    return cjk + other // 4


def main():
    rows = []
    for is_group in (False, True):
        static = bc._build_static_prompt(is_group)
        segs = bc.render_prompt_segments(is_group)
        rows.append((is_group, static, segs))

    (pg, pg_static, pg_segs) = rows[1]
    (pv, pv_static, pv_segs) = rows[0]

    print("static_prompt 总字符：群聊 %d / 私聊 %d（差 %d）"
          % (len(pg_static), len(pv_static), len(pg_static) - len(pv_static)))
    print("粗估 token：群聊 %d / 私聊 %d（差 %d）"
          % (seg_len(pg_static), seg_len(pv_static),
             seg_len(pg_static) - seg_len(pv_static)))
    print()

    # 逐段对比：按段名对齐
    pv_map = {n: t for n, t in pv_segs}
    pg_map = {n: t for n, t in pg_segs}
    names = []
    for n, _ in pg_segs:
        if n not in names:
            names.append(n)
    for n, _ in pv_segs:
        if n not in names:
            names.append(n)

    print("%-14s %8s %8s %8s" % ("段", "群聊", "私聊", "差"))
    print("-" * 44)
    tot_pg = tot_pv = 0
    for n in names:
        a = len(pg_map.get(n, ""))
        b = len(pv_map.get(n, ""))
        tot_pg += a
        tot_pv += b
        mark = "  ← 仅群聊" if (a and not b) else ("  ← 仅私聊" if (b and not a) else "")
        print("%-14s %8d %8d %+8d%s" % (n, a, b, a - b, mark))
    print("-" * 44)
    print("%-14s %8d %8d %+8d" % ("段合计", tot_pg, tot_pv, tot_pg - tot_pv))

    # 人格本体（扣掉已注册段之后剩下的部分 = 人格文本 + 身份块）
    persona_pg = pg_static
    for _, t in pg_segs:
        if t and t in persona_pg:
            persona_pg = persona_pg.replace(t, "", 1)
    persona_pv = pv_static
    for _, t in pv_segs:
        if t and t in persona_pv:
            persona_pv = persona_pv.replace(t, "", 1)
    print()
    print("人格+身份块：群聊 %d / 私聊 %d（差 %d）"
          % (len(persona_pg), len(persona_pv), len(persona_pg) - len(persona_pv)))

    # 群聊规则段单独看：它是差值的最大嫌疑
    if "群聊规则" in pg_map:
        gr = pg_map["群聊规则"]
        print()
        print("「群聊规则」段 %d 字符，占群聊 static 的 %.1f%%"
              % (len(gr), 100.0 * len(gr) / max(1, len(pg_static))))

    # 缓存影响评估：static 部分变化会作废整段前缀缓存
    print()
    print("── 对前缀缓存的影响 ──")
    print("static_prompt 是缓存前缀的主体，群聊/私聊各自稳定 → 两套前缀互不干扰。")
    print("群聊比私聊多的部分全在 static 里，属于**结构差异**，不是波动 ——")
    print("所以这 2650 token 不会导致 miss，只是群聊这侧的单价基数更高。")
    print("要降群聊成本，方向是砍 static 里的群聊专属内容，或提高缓存命中率；")
    print("砍 dynamic 部分（历史/摘要）不影响缓存，但会影响回答质量。")


if __name__ == "__main__":
    main()
