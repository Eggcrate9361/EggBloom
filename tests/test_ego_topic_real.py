# -*- coding: utf-8 -*-
"""话题提取：真机 1499 条合并消息的结论（2026-10-28）

## 怎么来的

不信"构造输入"，直接把运行日志里 `合并消息: ` 后面的**原文**逐条喂给
`_extract_topic`，按结果分类。1499 条真实样本里，剔掉昵称之后仍有近一半是垃圾：

| 形态 | 条数 | 占比 | 为什么是垃圾 |
|---|---|---|---|
| `（戳了戳你，这是第 1 次）` | 305 | 20.3% | 系统生成，同一句、可预测 |
| `#sl` | 207 | 13.8% | 指令词，无语义 |
| `你和他们去吃饭了吗 [图` | 141 | 9.4% | 12 字截断切在 `[图片]` 中间 |
| `@AAA无理由退货   ` | 39 | 2.6% | 剥完只剩一个 @ |
| （空） | 584 | 39.0% | 纯图片/表情，本就该空 |

修完：垃圾 0 条。但那 584→1108 条空值带来一个新问题 ——
`update()` 原来是**直接赋值**，等于每发一张图就把"在聊什么"抹掉。
现在改成"抽不出就保留上一个"，话题失效交给 30 分钟空闲重置。

## 本文件锁什么

1. 四类垃圾形态都不再成为话题；
2. 行内占位符先删再截断，不会切出 `[图` 这种半个括号；
3. 图片/指令/戳一戳**不清空**已有话题（这是本次最容易回归的一条）；
4. `[HH:MM]` 时间标签不会挡住"这是 Bot 自己说的"识别。

样本取自真实日志，不联网、不依赖现场数据：下面每条都是原文复制。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from plugins.ego.personality_engine import ConversationState  # noqa: E402

_fail = []
_uid = ["_test_topic_real_"]


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def _st():
    _uid[0] += "x"
    return ConversationState.__new__(ConversationState)


# ── 真机原文（逐字复制，别"整理"它们）────────────────────────────
REAL_POKE = "[Texas] [Texas] （戳了戳你，这是第 1 次）"
REAL_CMD = "[碱式碳酸大脑] #sl"
REAL_IMG_TRUNC = "[小明] 你和他们去吃饭了吗 [图片]"
REAL_BARE_AT = "[(/ω＼)] @AAA无理由退货   "
REAL_TS_SELF = "[09:24] [我(Bot)] 然后呢"
REAL_QUOTE = "[18:30] [(/ω＼)·8zff] [引用:(/ω＼)｜引用我，必回] @AAA无理由退货  今年明日方舟周年半周年限定是谁"
REAL_PLAIN = "[碱式碳酸大脑] 这游戏真好玩啊兄弟们"


def test_poke_not_topic():
    print("\n[1] 戳一戳的系统文本不是话题（真机 305 条）")
    t = _st()._extract_topic(REAL_POKE)
    check("★ 不把「戳了戳你」当话题", "戳了戳你" not in t, repr(t))
    check("返回空（交给上层保留旧话题）", t == "", repr(t))


def test_command_not_topic():
    print("\n[2] 指令词不是话题（真机 207 条）")
    t = _st()._extract_topic(REAL_CMD)
    check("★ `#sl` 不当话题", t == "", repr(t))


def test_inline_placeholder_not_truncated():
    print("\n[3] 行内占位符先删再截断（真机 141 条）")
    t = _st()._extract_topic(REAL_IMG_TRUNC)
    check("★ 不留下半个占位符（含未闭合的 `[`）", "[" not in t, repr(t))
    check("★ 保住正文", t == "你和他们去吃饭了吗", repr(t))


def test_bare_at_not_topic():
    print("\n[4] 只剩一个 @ 不是话题（真机 39 条）")
    t = _st()._extract_topic(REAL_BARE_AT)
    check("★ 不把 @ 当话题", t == "", repr(t))


def test_self_line_skipped_with_time_tag():
    print("\n[5] 时间标签挡在前面时，仍认得出是 Bot 自己说的")
    t = _st()._extract_topic(REAL_TS_SELF)
    check("★ Bot 自己的话不当话题", t == "", repr(t))
    t2 = _st()._extract_topic(REAL_TS_SELF + "\n[09:24] [Texas] 你在哪")
    check("跳过自己那句，取后面别人的", t2 == "你在哪", repr(t2))


def test_quote_stripped():
    print("\n[6] 引用 + @ + 正文：只留正文")
    t = _st()._extract_topic(REAL_QUOTE)
    check("★ 不含 `[引用`", "[引用" not in t, repr(t))
    check("★ 不以 @ 开头", not t.startswith("@"), repr(t))
    check("★ 含正文关键词", "明日方舟" in t, repr(t))


def test_normal_still_works():
    print("\n[7] 普通消息仍然提取（别修过头）")
    t = _st()._extract_topic(REAL_PLAIN)
    check("不含昵称", "碱式碳酸大脑" not in t, repr(t))
    check("含正文", "游戏" in t, repr(t))


def test_empty_does_not_clear_topic():
    """最容易回归的一条：图片消息不能把话题抹掉。"""
    print("\n[8] 抽不出话题时保留上一个（原实现会清空）")
    _uid[0] += "x"
    st = ConversationState(_uid[0])
    st.turn_count = 0
    st.current_topic = None
    st.last_update = time.time()      # 不给空闲重置留机会
    st.update(REAL_PLAIN, "…")
    first = st.current_topic
    check("先建立一个话题", bool(first), repr(first))
    for junk in (REAL_POKE, REAL_CMD, "[小明] [图片]", REAL_BARE_AT):
        st.last_update = time.time()
        st.update(junk, "…")
        check(f"★ 仍是 `{first}`（输入 {junk[:12]}…）",
              st.current_topic == first, repr(st.current_topic))


def test_idle_still_clears():
    """保留 ≠ 永不清。半小时没说话仍然要重开 —— 但"重开"意味着话题归零，
    此时来一张图片应当保持**空**（新会话还没有文字），而不是凭空继承旧话题。"""
    print("\n[9] 空闲超时仍然会重开（保留不等于永不清）")
    _uid[0] += "x"
    st = ConversationState(_uid[0])
    st.turn_count = 0
    st.current_topic = "旧话题"
    st.last_update = time.time() - 3 * 3600
    st.update("[小明] [图片]", "…")
    check("★ 跨了 3 小时：旧话题不再继承", st.current_topic is None,
          repr(st.current_topic))

    # 同一次会话内（不超时）发图，才应该保留
    st.current_topic = "刚聊的事"
    st.last_update = time.time()
    st.update("[小明] [图片]", "…")
    check("★ 未超时时发图保留话题", st.current_topic == "刚聊的事",
          repr(st.current_topic))

    st.last_update = time.time()
    st.update(REAL_PLAIN, "…")
    check("★ 有文字时正常覆盖", "游戏" in (st.current_topic or ""),
          repr(st.current_topic))


def main():
    test_poke_not_topic()
    test_command_not_topic()
    test_inline_placeholder_not_truncated()
    test_bare_at_not_topic()
    test_self_line_skipped_with_time_tag()
    test_quote_stripped()
    test_normal_still_works()
    test_empty_does_not_clear_topic()
    test_idle_still_clears()
    print("\n" + "=" * 56)
    if _fail:
        print(f"FAILED ({len(_fail)}): " + ", ".join(_fail))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()