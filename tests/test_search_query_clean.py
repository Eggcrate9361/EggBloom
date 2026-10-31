# -*- coding: utf-8 -*-
"""群聊检索词不能带昵称前缀（2026-10-25 新增）。

线上实证（群 123456789，11:49）。群友原话和实际检索词：

    群友说的   现在能证明你是人机了吧
    检索词     [碱式碳酸大脑] 现在能证明你是人机了吧

返回结果是 Yahoo「The race to tell humans from AI agents」、
IT之家「用一个字，证明你不是 AI」——全被「证明你是人机」带跑，
群友问的其实是他刚看到的 SnowLuma 状态栏。

成因：主链拼 buffer_text 时给每行加 `[昵称] ` 前缀（12031 行），
目的是让**主模型**知道谁在说话；这个前缀原样被当成检索词用了。
昵称占了检索词里唯一具体的名词位置，搜索引擎就把它当主题词。

`[昵称]` 对主模型有用，对检索词是纯噪声 —— 两种用途不能共用一个字符串。
修法是插件侧剥前缀（只有它知道这段文本要当检索词用），主链不动。

运行：python tests/test_search_query_clean.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def _load_plugin():
    src = os.path.join(ROOT, "plugins", "web_search", "__init__.py")
    ns = {"__name__": "_ws_under_test", "__file__": src}
    with open(src, encoding="utf-8") as f:
        code = compile(f.read(), src, "exec")
    exec(code, ns)  # noqa: S102  —— 只为拿到纯函数，不启动插件
    return ns


def main():
    ns = _load_plugin()
    P = ns["_strip_nick_prefixes"]

    print("[1] 线上那条原话")
    got = P("[碱式碳酸大脑] 现在能证明你是人机了吧")
    check("检索词里不再有昵称", "碱式碳酸大脑" not in got, got)
    check("正文完整保留", got == "现在能证明你是人机了吧", got)

    print("\n[2] 多行合并消息（回抓的上下文）")
    got = P("[碱式碳酸大脑] 第一句\n[半旧不新(awa)] 第二句")
    check("每行前缀都剥掉",
          "碱式碳酸大脑" not in got and "半旧不新" not in got, got)
    check("两行都在（不丢上下文）",
          "第一句" in got and "第二句" in got, got)

    print("\n[3] 昵称里带括号（本仓库真实昵称形态）")
    # 日志里确实有 `(/ω＼)(1234567890)`、`我(Bot)(AAA无理由退货 (1234567893))`
    for raw, want in (
        ("[/ω＼(1234567890)] 养大肥鱼 很火 游戏", "养大肥鱼 很火 游戏"),
        ("[我(Bot)(AAA无理由退货 (1234567893))] #sl", "#sl"),
    ):
        got = P(raw)
        check(f"{raw[:22]}… 剥干净", got == want, got)

    print("\n[4] 没有前缀的不能动")
    for raw in ("没有前缀的句子", "现在几点了", "查一下明天的天气"):
        got = P(raw)
        check(f"{raw!r} 原样保留", got == raw, got)

    print("\n[5] 正文方的方括号不是前缀（只剥行首）")
    got = P("[碱式碳酸大脑] 这个 [图片] 是啥")
    check("句中 [图片] 保留", "[图片]" in got, got)

    print("\n[6] 别的插件不受牵连")
    src = open(os.path.join(ROOT, "plugins", "web_search", "__init__.py"),
               encoding="utf-8").read()
    check("before_reply 用清理后的 query 去搜", "search_web(query)" in src)
    check("meta.query 记的是实际检索词", '"query": query' in src)
    # 2026-10-31：实参名从 `msg` 改成了 `_text_only`（先剥图片块再判定），
    # 卡死变量名会假红。判据回到语义：**判定不得用检索词 `query`**。
    import re as _re
    check("判定仍用原文（不因清理改变要不要搜）",
          bool(_re.search(r"_needs_search\((?!query\b)[^)]+\)", src)),
          "判定改成 query 会改变触发行为")

    print("\n[7] 主链没被改（前缀仍要给主模型看）")
    bc = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    # 1.5.9 §B1：前缀从 `[昵称]` 换成 `[昵称·4位锚]`，判据是"每条都带前缀"，
    # 不是"前缀长什么样"。锚的作用是改名不变、跨群稳定。
    check("buffer_text 仍给每条加说话人前缀",
          '_speaker_tag(sender, group_id)' in bc and '[{_tag}] {text}' in bc,
          "删了前缀主模型就分不清谁在说话")

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())