# -*- coding: utf-8 -*-
"""tests/test_self_learn_hygiene.py —— 自主学习语料卫生（用户 2026-10-19 报）

## 事故

`personality.txt` 的【自主学习成果】段被两类垃圾填满，占人格文件 33%：

```
【群200000001表达风格 - 10-19 13:25】
- 问候时该群常用: 「你这早上好从中午喊到下午，一天喊三回」(可靠度60%)
- 问候时该群常用: 「这猫笑得比早上好还瘆人」(可靠度60%)
- 拒绝时该群常用: 「关不了，我够不着你的电脑」(可靠度60%)
- 反对时该群常用: 「你不是要关机吗」(可靠度60%)
```

### 根因一：学群里**另一个 AI 机器人**

来源 uid 10000004（群昵称 `另一个AI机器人`），另有 10000005（群名片同名）。
两者合计 205+ 条消息，占学习语料 **37%** → EggBloom 开始模仿另一个 AI 的腔调，
这正是用户反复反馈的「越来越像机器人」。

### 根因二：把 bot 自己的**内部标注**当用户原话

`text` 在采集时已带上程序拼的引用注记：
```
[引用:占位群友A｜他在引用占位群友A，**不是在叫我**，别抢话] 你们难道不上课吗
```
→ 存库后被黑话挖掘成「当前」「别抢话」「引用」「他在引用」「不是在叫」。

### 根因三：场景关键词**裸子串**匹配

「关不了」命中"拒绝"的 `不了`、「你不是要关机吗」命中"反对"的 `不是`、
「这猫笑得比早上好还瘆人」命中"问候"的 `早` → 随机句子硬贴场景标签。

## 现在的契约

1. `_is_ai_bot_sender()`：配置黑名单 + 昵称/名片 AI 关键词 → 不学习；
2. `_strip_bot_annotation()`：只采集用户当前这句原话；
3. `ExpressionLearner._starts_phrase()`：关键词必须**句首或标点之后**；
4. 含 `[]【】`/@ 开头/疑问句 → 不收。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

sys.path.insert(0, ROOT)
import bot_core  # noqa: E402
from self_learning import ExpressionLearner  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


GID = "200000001"
_ORIG_CFG = dict(bot_core._config)
_ORIG_MEMBERS = dict(bot_core.GROUP_MEMBERS)


def reset():
    bot_core._config.clear()
    bot_core._config.update(_ORIG_CFG)
    bot_core.GROUP_MEMBERS.clear()


# ══════════════ 1. 排除其他 AI 机器人 ══════════════
def test_ai_bot_filter():
    print("\n[1] _is_ai_bot_sender 只看**全局昵称**判 AI")
    reset()
    bot_core.GROUP_MEMBERS[GID] = {
        "10000004": "另一个AI机器人",        # 昵称=AI，名片空 → 真·AI 机器人
        "10000005": "另一个AI机器人",        # 名片=AI，但昵称是 Texas → **真人扮的**
        "10000003": "占位昵称",           # 真人
        "10000002": "占位名片B",               # 用户本人
    }
    bot_core.QQ_NICK_CACHE.update({
        "10000004": "另一个AI机器人",        # 全局昵称就是模型名
        "10000005": "Texas",                # 全局昵称是本人名
        "10000003": "占位昵称", "10000002": "占位名片B",
    })
    bot_core._config["self_learn_exclude_ai_names"] = True
    bot_core._config["self_learn_ignore_qqs"] = []
    bot_core._config["self_learn_allow_qqs"] = []

    check("另一个AI机器人（全局昵称）→ 跳过",
          bot_core._is_ai_bot_sender(GID, "10000004") is True)
    # ★ 2026-10-19 用户纠正：名片像 AI 但昵称是人名的，是**真人 cosplay**，必须照学
    check("★ Texas（名片 另一个AI机器人）→ 照学，不能误杀真人",
          bot_core._is_ai_bot_sender(GID, "10000005") is False)
    check("真人 → 学习", bot_core._is_ai_bot_sender(GID, "10000003") is False)
    check("用户本人 → 学习", bot_core._is_ai_bot_sender(GID, "10000002") is False)

    # 关掉自动识别后，只靠显式黑名单
    bot_core._config["self_learn_exclude_ai_names"] = False
    check("关掉 AI 名识别 → 不再排除",
          bot_core._is_ai_bot_sender(GID, "10000004") is False)
    bot_core._config["self_learn_ignore_qqs"] = ["10000004"]
    check("★ 显式黑名单生效（不受自动识别开关影响）",
          bot_core._is_ai_bot_sender(GID, "10000004") is True)

    # 人工白名单优先于自动识别（真人起了个 AI 名）
    reset()
    bot_core._config["self_learn_exclude_ai_names"] = True
    bot_core._config["self_learn_allow_qqs"] = ["10000004"]
    check("★ 白名单覆盖自动识别（明确是真人就照学）",
          bot_core._is_ai_bot_sender(GID, "10000004") is False)

    # 常见 AI 名应该都能认出来（走全局昵称）
    reset()
    bot_core._config["self_learn_exclude_ai_names"] = True
    for uid, name in [("1", "ChatGPT"), ("2", "Claude"), ("3", "小助手"),
                      ("4", "机器人小冰"), ("5", "GPT-4o"), ("6", "Kimi")]:
        bot_core.QQ_NICK_CACHE[uid] = name
        check(f"识别「{name}」", bot_core._is_ai_bot_sender(GID, uid) is True)

    # 反向：名片里带 AI 字样但昵称正常 → 不排除
    reset()
    bot_core._config["self_learn_exclude_ai_names"] = True
    bot_core.GROUP_MEMBERS[GID] = {"777": "GPT-4 本体"}
    bot_core.QQ_NICK_CACHE["777"] = "老王"
    check("★ 仅名片像 AI 不排除（昵称才是身份）",
          bot_core._is_ai_bot_sender(GID, "777") is False)


# ══════════════ 2. 剥离内部标注 ══════════════
def test_strip_annotation():
    print("\n[2] _strip_bot_annotation 只留用户原话")
    cases = [
        ("[引用:占位群友A｜他在引用占位群友A，**不是在叫我**，别抢话] 你们上课吗\n[当前] 我不喜欢你了",
         "我不喜欢你了"),
        ("[引用:张三｜引用我，必回] 原文\n[当前] 这是哪首歌", "这是哪首歌"),
        ("普通消息没有标注", "普通消息没有标注"),
        ("[引用:某人｜别抢话] 只有引用没有当前标记", "只有引用没有当前标记"),
    ]
    for raw, want in cases:
        got = bot_core._strip_bot_annotation(raw)
        check(f"{want[:12]}", got == want, f"got={got!r}")

    print("\n[3] 剥离后不该再含内部标记")
    raw = "[引用:甲｜他在引用甲，**不是在叫我**，别抢话] 原文\n[当前] 真正的发言"
    got = bot_core._strip_bot_annotation(raw)
    for bad in ("别抢话", "不是在叫", "他在引用", "[引用", "[当前]"):
        check(f"不含「{bad}」", bad not in got, got)


# ══════════════ 4. 场景匹配改句首/标点后 ══════════════
def test_scene_boundary():
    print("\n[4] 场景关键词必须在句首或标点之后")
    L = ExpressionLearner()

    # 事故里那 5 条，一条都不该再被收
    junk = [
        "关不了，我够不着你的电脑",       # 命中过"拒绝"的 不了
        "你不是要关机吗",                # 命中过"反对"的 不是
        "这猫笑得比早上好还瘆人",         # 命中过"问候"的 早
        "真吵起来了有感觉吗",
        "[引用:某人｜别抢话] 你们上课吗",   # 引用注记块
        "你吃了吗？",                    # 疑问句
        "@小王 你好",                    # @ 开头
    ]
    for t in junk:
        r = L.extract_expressions([(1, "u", t)])
        check(f"拦掉「{t[:14]}」", not r, r)

    # 真正的场景表达要保留，且场景正确
    good = [
        ("不了，算了", "拒绝"),
        ("不是，你听我说", "反对"),
        ("早上好啊", "问候"),
        ("确实是这样", "赞同"),
        ("哈哈哈哈笑死", "开心"),
        ("谢谢老哥", "感谢"),
    ]
    for t, scene in good:
        r = L.extract_expressions([(1, "u", t)])
        got = r[0]["scene"] if r else "(拦掉)"
        check(f"「{t}」→ {scene}", got == scene, f"got={got}")

    # 去重
    r = L.extract_expressions([(1, "u", "确实是这样"), (2, "u", "确实是这样")])
    check("同一句只收一条", len(r) == 1, r)


# ══════════════ 5. 重复门槛：一次性的句子不准进人格 ══════════════
def test_promotion_gate():
    print("\n[5] 只有重复≥2 次的表达才准写进人格（严格化的核心）")
    import sqlite3          # noqa: F401
    import tempfile
    import self_learning as slmod

    orig = slmod.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            slmod.DB_PATH = os.path.join(td, "t.db")
            conn = slmod.get_db()
            now = 1.0
            rows = [
                # 事故里那 5 条：全是 count=1 的一次性 AI 发言
                ("g", "问候", "你这早上好从中午喊到下午，一天喊三回", 0.6, 1),
                ("g", "问候", "这猫笑得比早上好还瘆人", 0.6, 1),
                ("g", "拒绝", "关不了，我够不着你的电脑", 0.6, 1),
                # 真正被重复验证过的群习惯
                ("g", "拒绝", "不了，算了", 0.66, 3),
                ("g", "赞同", "确实是这样", 0.64, 2),
                # 别的群，不该串进来
                ("g2", "赞同", "老哥稳", 0.9, 9),
            ]
            for gid, scene, expr, q, cnt in rows:
                conn.execute(
                    "INSERT INTO expressions (group_id, scene, expression, quality, count, "
                    "created_at, last_seen) VALUES (?,?,?,?,?,?,?)",
                    (gid, scene, expr, q, cnt, now, now))
            conn.commit()
            conn.close()

            got = slmod.SelfLearningEngine._select_promotable_expressions("g", 2)
            exprs = [r[1] for r in got]
            check("★ count=1 的一次性句子全部被拦",
                  all("早上好从中午" not in e and "比早上好还瘆人" not in e
                      and "够不着你的电脑" not in e for e in exprs), exprs)
            check("重复≥2 的被保留", "不了，算了" in exprs and "确实是这样" in exprs, exprs)
            check("只取本群（不串群）", "老哥稳" not in exprs, exprs)
            check("结果 ≤ limit", len(exprs) <= 5, len(exprs))

            # 门槛可调：调到 3 就只剩 count=3 那条
            got3 = slmod.SelfLearningEngine._select_promotable_expressions("g", 3)
            check("门槛调到 3 → 只剩重复 3 次的",
                  [r[1] for r in got3] == ["不了，算了"], got3)

            # 非法 min_count 兜底成 2
            got_bad = slmod.SelfLearningEngine._select_promotable_expressions("g", "abc")
            check("非法 min_count 兜底为 2", len(got_bad) == 2, got_bad)
    finally:
        slmod.DB_PATH = orig


def main():
    test_ai_bot_filter()
    test_strip_annotation()
    test_scene_boundary()
    test_promotion_gate()
    reset()
    bot_core.GROUP_MEMBERS.clear()
    bot_core.GROUP_MEMBERS.update(_ORIG_MEMBERS)

    print("\n" + "=" * 52)
    print(f"PASS: {PASS}   FAIL: {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  ✗ " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())