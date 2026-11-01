# -*- coding: utf-8 -*-
"""主链动态上下文出口（1.6.0 刀 2）—— golden 逐字节 + 开关语义。

## 钉死三件事

1. **开关关 = 逐字节等于改造前**：`_fixtures/life_dynamic_golden.json` 是在
   改造前（`d19b4f2` 的 bot_core）用同一套桩跑出来的动态块清单；改完必须一模一样。
   （生成方式：`git stash bot_core.py` → `python tests/test_life_context_mainchain.py --update`
     → `git stash pop`。这份基线是"旧代码说过的原话"，不是我手抄的期望值。）
2. **开关开 = 合成一块且不丢内容**：块数 12 → 1，字数差 < 2%。
3. **容器语义**：`Parts` 保序、丢空块、空文本不加；`merge()` 去重、超预算整块丢尾、
   只剩一块时宁可超预算也不丢（"什么都没注入"是最坏结果）。

## 为什么用桩

主链那段依赖时钟 / 记忆库 / 头像 / 技能清单 —— 不钉住它们，golden 每天都不一样，
测出来的就不是"顺序对没对"，而是"今天几点"。

运行：python tests/test_life_context_mainchain.py [--update]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FIXTURE = os.path.join(HERE, "_fixtures", "life_dynamic_golden.json")
UPDATE = "--update" in sys.argv

import bot_core  # noqa: E402
from harness import life_context as lc  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


class _Stop(Exception):
    """在 messages 构建处中断：只需要动态块，不要真发请求。"""


CAP = {}


def _fake_build(history, cache_key, static_prompt, dynamic_parts, user_msg,
                images=None, stable_parts=None):
    CAP["dynamic"] = list(dynamic_parts)
    CAP["stable"] = list(stable_parts or [])
    raise _Stop()


def _stub_producers():
    """把所有"会随时间/磁盘变"的产出换成固定串（顺序与真实注入一致）。"""
    import persona_tag
    persona_tag.context = lambda *a, **k: "【语气】固定标签"

    import people
    people.display = lambda uid: "测试对象"

    import self_actions
    self_actions.render = lambda **k: "【刚做过】发过一张图"

    from harness import memory_api
    memory_api.recall = lambda *a, **k: "【记忆】固定一段记忆"

    from harness import bot_hook
    bot_hook.time_parts = lambda key: ("【今天是】固定日期", "【当前时间】固定钟点")
    bot_hook.mismatch_note = lambda *a, **k: ""
    bot_hook.has_skills = lambda: False
    bot_hook._should_offer_skills = lambda *a, **k: False
    bot_hook.skill_l1_block = lambda: ""
    bot_hook.freshness_parts = lambda *a, **k: ("", "")
    bot_hook.tool_names_for = lambda *a, **k: []

    bot_core._avatar_desc = lambda *a, **k: "黑色短发"
    bot_core.get_slang_context = lambda msg: "【黑话】固定黑话"
    bot_core._capability_block = lambda **k: ""
    bot_core._tools_subset_for_scene = lambda *a, **k: []
    bot_core._build_group_tools = lambda *a, **k: []
    bot_core._private_tool_allowed = lambda *a, **k: True
    bot_core.TOOLS = []


CFG_KEYS = {
    "quiet_hours": "",          # 不进"睡觉"块
    "focus_qq": "",
    "slang_injector_enable": True,
    "qq_agent_mode": "tools",   # 命中两条「派活/表情包」提示
    "qq_agent_enable": True,
    "tools_full_inject_enable": True,
    "history_recent_turns": 2,
}


def capture(is_group=False, **cfg):
    """跑一遍主链，返回它要注入的动态块清单（不联网、不打日志以外的副作用）。"""
    _stub_producers()
    backup_cfg = dict(bot_core._config)
    backup_build = bot_core._build_cached_messages
    CAP.clear()
    try:
        bot_core._config.update(CFG_KEYS)
        bot_core._config.update(cfg)
        bot_core._build_cached_messages = _fake_build
        try:
            if is_group:
                bot_core._call_deepseek_sync(
                    "在吗", target_qq=None, group_id="20001", is_group=True,
                    sender="10001", ego_inject_context="【EGO】固定人格上下文",
                    scene_context="【现场】固定现场资料")
            else:
                bot_core._call_deepseek_sync(
                    "在吗", target_qq="10001", is_group=False, sender="10001",
                    private_memory_context="他喜欢猫", ego_inject_context="【EGO】固定人格上下文",
                    scene_context="【现场】固定现场资料")
        except _Stop:
            pass
    finally:
        bot_core._build_cached_messages = backup_build
        bot_core._config.clear()
        bot_core._config.update(backup_cfg)
    return CAP.get("dynamic", [])


# ── 1. 生成 / 比对 golden ────────────────────────────────────────────
import config as _cfgmod  # noqa: E402

check("默认关：DEFAULT_CONFIG 里 life_context_enable=False",
      _cfgmod.DEFAULT_CONFIG.get("life_context_enable") is False,
      _cfgmod.DEFAULT_CONFIG.get("life_context_enable"))
check("默认预算 8000", _cfgmod.DEFAULT_CONFIG.get("life_context_budget") == 8000,
      _cfgmod.DEFAULT_CONFIG.get("life_context_budget"))

captured = capture()

if UPDATE:
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    with open(FIXTURE, "w", encoding="utf-8") as f:
        json.dump({"private": captured}, f, ensure_ascii=False, indent=1)
    print(f"已写入基线：{FIXTURE}（{len(captured)} 块）")
    for i, t in enumerate(captured, 1):
        print(f"  {i:>2}. {t.splitlines()[0][:50]}")
    sys.exit(0)

with open(FIXTURE, encoding="utf-8") as f:
    GOLD = json.load(f)["private"]

check("基线非空（没抓到说明桩把主链打断了）", len(GOLD) >= 8, len(GOLD))
check("★ 开关关：块数与改造前相同", len(captured) == len(GOLD),
      f"现在 {len(captured)} / 基线 {len(GOLD)}")
same = captured == GOLD
check("★ 开关关：逐字节等于改造前", same,
      "" if same else f"首个不同：第 {next((i for i, (a, b) in enumerate(zip(captured, GOLD)) if a != b), -1) + 1} 块")
if not same:
    for i, (a, b) in enumerate(zip(captured, GOLD), 1):
        if a != b:
            print(f"    [{i}] 现在：{a[:70]!r}\n        基线：{b[:70]!r}")

# ── 2. 开关开：合成一块、不丢内容 ────────────────────────────────────
on = capture(life_context_enable=True)
check("开关开：动态块收成 1 块", len(on) == 1, len(on))
merged = on[0] if on else ""
old_chars = sum(len(t) for t in captured)
check("开关开：所有块都还在（逐块包含）",
      all(t in merged for t in captured),
      [t[:20] for t in captured if t not in merged][:3])
# 合并只应该多出块间分隔符（"\n\n"），内容不增不减 —— 比"字数差 <2%"更硬：
# 拿分隔符当"内容变多"会把一条恒真的判据写成假红（第一版就是这么错的）。
gap = len(merged) - old_chars
check("开关开：字数只多出块间隔，内容不增不减",
      gap == (len(captured) - 1) * 2, f"合并 {len(merged)} / 原 {old_chars}（多了 {gap} 字）")
check("开关开：块间有分隔（不是粘成一坨）",
      merged.count("\n\n") >= len(captured) - 1, merged.count("\n\n"))

# ── 3. 预算：超了整块丢尾，不截半句 ─────────────────────────────────
tiny = capture(life_context_enable=True, life_context_budget=80)
check("预算很小：只剩前面几块", len(tiny) == 1 and len(tiny[0]) < old_chars, len(tiny and tiny[0]))
check("预算很小：丢的是尾部（前面的块还在）",
      tiny and captured[0][:20] in tiny[0], (tiny and tiny[0][:40]))

# ── 4. Parts / merge 的容器语义 ─────────────────────────────────────
p = lc.Parts()
p.add("a", "第一块")
p.add("b", "")
p.add("c", "  第二块  ")
check("Parts：空文本不记", len(p) == 2, len(p))
check("Parts：保序", p.texts() == ["第一块", "第二块"], p.texts())
check("Parts：blocks 带来源", [k for k, _ in p.blocks] == ["a", "c"], p.blocks)
check("Parts：空容器为假", not lc.Parts(), lc.Parts())
check("merge：去重", lc.merge(["同", "同", "异"]) == "同\n\n异", lc.merge(["同", "同", "异"]))
check("merge：空输入 → 空串", lc.merge([]) == "" and lc.merge(None) == "")
long3 = ["A" * 100, "B" * 100, "C" * 100]
check("merge：超预算丢尾", lc.merge(long3, budget=250) == "A" * 100 + "\n\n" + "B" * 100,
      len(lc.merge(long3, budget=250)))
one_big = lc.merge(["X" * 500], budget=10)
check("merge：只剩一块时不丢（宁超勿空）", one_big == "X" * 500, len(one_big))
check("merge：不截半句（结果总是整块拼接）",
      lc.merge(long3, budget=100) in ("A" * 100, "A" * 100 + "\n\n" + "B" * 100),
      repr(lc.merge(long3, budget=100))[:40])

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
