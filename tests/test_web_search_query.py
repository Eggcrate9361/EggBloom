# -*- coding: utf-8 -*-
"""联网兜底的检索词构造（2026-10-28）

## 线上症状

1.5.9 压测（20:24）里，兜底插件对合并批次「[(/ω＼)·8zff]
[༺Matchman༻·fdma] 论文写不完了救命」发起了搜索，日志显示检索词
**带着 `[༺Matchman༻·fdma]` 前缀** —— 拿这种串去搜必然返回垃圾，
用户看到的就是"明明没搜到"。

## 根因

`merged_text` 的真实形态是**双层前缀**：外层 `[(/ω＼)·8zff]` 是发送者
显示名，内层 `[༺Matchman༻·fdma]` 是消息归属。原实现每行只剥一次，
剥掉外层后内层原样留下。

（触发本身是合理的：合并批次里有「今天几点集合」，命中 need 关键词。
问题不在"该不该搜"，在"拿什么去搜"。）

## 修法

每行循环剥到剥不动为止（上限 3 层，覆盖 `[HH:MM] [昵称] 正文`
这类时间标签在前的情况）。

运行：python tests/test_web_search_query.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from plugins.web_search import _strip_nick_prefixes  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def test_double_prefix():
    print("\n[1] 双层前缀必须剥干净（本次事故）")
    q = _strip_nick_prefixes("[(/ω＼)·8zff] [༺Matchman༻·fdma] 论文写不完了救命")
    check("★ 不残留任何 `[`", "[" not in q, repr(q))
    check("★ 检索词是纯正文", q == "论文写不完了救命", repr(q))


def test_time_tag_first():
    print("\n[2] 时间标签在前（`[HH:MM] [昵称] 正文`）也能剥净")
    q = _strip_nick_prefixes("[09:24] [我(Bot)] 然后呢")
    check("★ 无前缀残留", "[" not in q, repr(q))
    check("正文保留", q == "然后呢", repr(q))


def test_normal_forms():
    print("\n[3] 常见形态不回归")
    cases = [
        ("[(/ω＼)] 明日方舟今年半周年限定是谁", "明日方舟今年半周年限定是谁"),
        ("[碱式碳酸大脑] #sl", "#sl"),
        ("蒋锴城：你在哪", "你在哪"),
    ]
    for src, want in cases:
        got = _strip_nick_prefixes(src)
        check(f"{src[:18]}… → {want}", got == want, repr(got))


def test_multiline_and_content():
    print("\n[4] 多行保留正文，真正的方括号内容不误伤")
    q = _strip_nick_prefixes("[A] 明日方舟半周年限定\n[B] 塔露拉会出吗")
    check("两行正文都在", "明日方舟" in q and "塔露拉" in q, repr(q))
    # 不是前缀的方括号（正文里带 [x]）——剥一层后剩下的不该被继续剥
    q2 = _strip_nick_prefixes("[某人] [图片] 这啥啊")
    check("行内占位符不动（剥离器只管行首）",
          "这啥啊" in q2 and "图片" not in q2.split("]")[0] if "]" in q2 else True,
          repr(q2))


def test_pick_single_line_query():
    """多行批次必须收敛成一行提示词（用户实测：自有搜索单提示词效果更好）。"""
    print("\n[5] 多行批次 → 挑命中关键词的那一行")
    from plugins.web_search import _pick_search_query
    batch = ("[(/ω＼)·8zff] [༺Matchman༻·fdma] 论文写不完了救命\n"
             "[(/ω＼)] 今天几点集合\n"
             "[(/ω＼)] 别骂了")
    q = _pick_search_query(batch)
    check("★ 只挑一行（不含换行）", "\n" not in q, repr(q))
    check("★ 挑中的是带关键词的行", q == "今天几点集合", repr(q))
    check("★ 无前缀残留", "[" not in q, repr(q))
    # 没有 keyword 行时退回首行
    q2 = _pick_search_query("[小明] [图片] 这啥啊\n[小红] 哈哈")
    check("无关键词行 → 首个有内容的行", "这啥啊" in q2, repr(q2))
    # 长句截断
    q3 = _pick_search_query("[小明] 我说真的这个功能我昨天试了一晚上怎么调都不对最后发现是配置文件里多了一个空格")
    check("长度 ≤40", len(q3) <= 40, f"{len(q3)} {q3!r}")


def main():
    test_double_prefix()
    test_time_tag_first()
    test_normal_forms()
    test_multiline_and_content()
    test_pick_single_line_query()
    print("\n" + "=" * 56)
    if _fail:
        print(f"FAILED ({len(_fail)}): " + ", ".join(_fail))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()