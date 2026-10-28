# -*- coding: utf-8 -*-
"""关系档位（2026-10-27 · 1.5.9 §A.7.2）

## 为什么

人格文件【干活的时候】早就写好了四档抱怨强度（关系浅→直白嫌烦 / 一般→
"行吧行吧" / 好→傲娇 / 特别好→全撒娇），但**从来没生效过**：
它需要"关系"作为输入，而好感度从未进过 prompt ——

    self_learning.get_affection()        有实现
    self_learning.get_context_for_llm()  有分档
    bot_core 对这两个函数的引用数          0

所以那四档一直是悬空的散文。本测试锁住接线与边界。

## 契约

1. 四档阈值：<20 浅 / 20-49 一般 / 50-79 好 / >=80 特别好
2. 查不到（没装 self_learning / 无记录）→ 返回空，不兜底成"浅"（宁缺勿编）
3. 只给档位词，不把分数写进 prompt
4. 人格第 38 行必须已经改掉「做不做跟心情无关」——它和 A.7 是正面冲突

运行：python tests/test_relation_tier.py
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


class _FakeAffection:
    """好感度引擎的替身。

    ★ 2026-10-27 改结构：原来替身把 `get_affection` 放在**顶层**，
      而真实的 SelfLearningEngine 是 `engine.affection.get_affection`。
      替身比真机多一个顶层方法 → bot_core 那个 AttributeError
      在测试里永远不发生 → 测试全绿、真机功能全死。

      替身必须**照抄真实结构**，否则它遮住的正是要抓的 bug。
    """

    def __init__(self, score=None, boom=False):
        self.score = score
        self.boom = boom

    def get_affection(self, group_id, user_id):
        if self.boom:
            raise RuntimeError("db locked")
        if self.score is None:
            return {"score": 0, "interactions": 0}
        return {"score": self.score, "interactions": 1}


class _FakeLearn:
    """self_learn 的替身 —— 结构照抄 SelfLearningEngine。"""

    def __init__(self, score=None, boom=False):
        self.affection = _FakeAffection(score, boom)


def _with(score=None, boom=False, ok=True):
    import bot_core
    bot_core._RELATION_CACHE.clear()      # 档位有 5 分钟缓存，逐条测必须清
    old_ok, old_learn = bot_core.SELF_LEARN_OK, bot_core.self_learn
    bot_core.SELF_LEARN_OK = ok
    bot_core.self_learn = _FakeLearn(score, boom)
    try:
        return bot_core._relation_tier("123456789", "1234567890")
    finally:
        bot_core.SELF_LEARN_OK = old_ok
        bot_core.self_learn = old_learn


def test_real_engine_shape():
    """★ 不替换任何东西：断言真实引擎的结构与代码假设一致。

    这次事故的根因就是"代码假设 self_learn 上有 get_affection"，
    而真实结构是 self_learn.affection.get_affection。
    这条测试不依赖任何替身 —— 替身变了它不会变，真实结构变了它会变。
    """
    import bot_core
    import self_learning
    eng = self_learning.SelfLearningEngine()
    check("引擎有 affection 属性", hasattr(eng, "affection"),
          [a for a in dir(eng) if not a.startswith("_")])
    check("affection 上有 get_affection",
          hasattr(getattr(eng, "affection", None), "get_affection"))
    check("引擎自己没有 get_affection（所以别在引擎上找）",
          not hasattr(eng, "get_affection"))
    # 代码里的取法必须落到 .affection 上
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("代码从 .affection 取", 'getattr(self_learn, "affection"' in src)


def test_tiers():
    cases = [(0, "浅"), (19, "浅"), (20, "一般"), (49, "一般"),
             (50, "好"), (79, "好"), (80, "特别好"), (999, "特别好")]
    for score, want in cases:
        got = _with(score)[0]
        check(f"score={score} → {want}", got == want, got)


def test_notes():
    check("浅档不许撒娇", "不撒娇" in _with(5)[1], _with(5)[1])
    check("特别好档允许真不干", "真不干" in _with(90)[1], _with(90)[1])


def test_unknown_not_guessed():
    check("没装 self_learning → 空档", _with(90, ok=False)[0] == "", _with(90, ok=False))
    check("查询抛异常 → 空档", _with(90, boom=True)[0] == "", _with(90, boom=True))
    import bot_core
    old = bot_core.self_learn
    bot_core.self_learn = None
    try:
        got = bot_core._relation_tier("123456789", "1234567890")
    finally:
        bot_core.self_learn = old
    check("引擎为 None → 空档", got[0] == "", got)


def test_prompt_has_tier_not_score():
    import bot_core
    old_cfg = dict(bot_core._config)
    old_ok, old_learn = bot_core.SELF_LEARN_OK, bot_core.self_learn
    bot_core.SELF_LEARN_OK = True
    bot_core.self_learn = _FakeLearn(92)
    bot_core._RELATION_CACHE.clear()
    bot_core._config["persona_relation_enable"] = True
    bot_core.resolve_qq_nick = lambda q: "德州"
    # 预填角色缓存：否则会真打 OneBot（502，白等 3 秒）
    bot_core._BOT_ROLE_CACHE["123456789"] = (9e18, "member")
    try:
        blk = bot_core._capability_block(is_group=True, group_id="123456789",
                                         sender="1234567890", tool_names=())
    finally:
        bot_core.SELF_LEARN_OK, bot_core.self_learn = old_ok, old_learn
        bot_core._config.clear()
        bot_core._config.update(old_cfg)
    check("prompt 里出现关系档位", "我跟他关系：特别好" in blk, blk[-200:])
    check("prompt 里不出现分数", "92" not in blk.split("我跟他关系")[-1][:20], blk[-200:])


def test_switch_off():
    import bot_core
    old_cfg = dict(bot_core._config)
    old_ok, old_learn = bot_core.SELF_LEARN_OK, bot_core.self_learn
    bot_core.SELF_LEARN_OK = True
    bot_core.self_learn = _FakeLearn(92)
    bot_core._RELATION_CACHE.clear()
    bot_core._config["persona_relation_enable"] = False
    bot_core.resolve_qq_nick = lambda q: "德州"
    bot_core._BOT_ROLE_CACHE["123456789"] = (9e18, "member")
    try:
        blk = bot_core._capability_block(is_group=True, group_id="123456789",
                                         sender="1234567890", tool_names=())
    finally:
        bot_core.SELF_LEARN_OK, bot_core.self_learn = old_ok, old_learn
        bot_core._config.clear()
        bot_core._config.update(old_cfg)
    check("开关关掉后不注入关系行", "我跟他关系" not in blk, blk[-200:])


def test_refuse_license():
    """A.7.2 ② / ③：概率只决定要不要给许可，额度与逃避上限是硬闸。"""
    import bot_core
    old_learn, old_ok = bot_core.self_learn, bot_core.SELF_LEARN_OK
    old_rand = bot_core.random.random
    bot_core.SELF_LEARN_OK = True
    bot_core.self_learn = _FakeLearn(90)
    bot_core._REFUSE_LEDGER.clear()
    bot_core._EVADE_LEDGER.clear()
    bot_core._RELATION_CACHE.clear()
    try:
        bot_core.random.random = lambda: 0.0        # 必过概率闸
        r1 = bot_core._refuse_license("123456789", "1234567890")
        r2 = bot_core._refuse_license("123456789", "1234567890")
        r3 = bot_core._refuse_license("123456789", "1234567890")
        check("前两次给许可", r1[0] and r2[0], (r1, r2))
        check("第三次被额度挡住", (not r3[0]) and r3[1] == "quota", r3)

        bot_core._REFUSE_LEDGER.clear()
        for _ in range(3):
            bot_core._note_evade("1234567890")
        r4 = bot_core._refuse_license("123456789", "1234567890")
        check("逃避到上限后概率归零", (not r4[0]) and r4[1] == "evade-limit", r4)

        bot_core._REFUSE_LEDGER.clear()
        bot_core._EVADE_LEDGER.clear()
        bot_core._RELATION_CACHE.clear()
        bot_core.random.random = lambda: 0.05
        bot_core.self_learn = _FakeLearn(5)
        r5 = bot_core._refuse_license("123456789", "1234567890")
        check("浅档基本不给（p=0.04 < 0.05）", not r5[0], r5)
        bot_core.self_learn = _FakeLearn(95)
        bot_core._RELATION_CACHE.clear()
        r6 = bot_core._refuse_license("123456789", "1234567890")
        check("特别好档更容易给（p=0.16 > 0.05）", r6[0], r6)
    finally:
        bot_core.self_learn, bot_core.SELF_LEARN_OK = old_learn, old_ok
        bot_core.random.random = old_rand
        bot_core._REFUSE_LEDGER.clear()
        bot_core._EVADE_LEDGER.clear()


def test_persona_conflict_removed():
    p = os.path.join(ROOT, "plugins", "personality.txt")
    txt = io.open(p, encoding="utf-8").read()
    check("人格里不再写「做不做跟心情无关」", "跟心情无关" not in txt)
    check("人格里写明不想≠不会", "别绕成" in txt and "我不会" in txt, txt[:200])
    check("四档抱怨样例都在",
          all(k in txt for k in ("自己弄", "行吧", "凭什么", "叫声哥")))
    check("反问样例「还问？」已换掉", "还问？" not in txt)


def main():
    print("=" * 58)
    print("关系档位（A.7.2）")
    print("=" * 58)
    for fn in (test_real_engine_shape,
               test_tiers, test_notes, test_unknown_not_guessed,
               test_prompt_has_tier_not_score, test_switch_off,
               test_refuse_license, test_persona_conflict_removed):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
