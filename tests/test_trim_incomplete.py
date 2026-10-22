# -*- coding: utf-8 -*-
"""tests/test_trim_incomplete.py —— 最后一句不许被吞（2026-10-13 线上日志实证）

17:28:10 群里那条：模型回了
    这图看着跟熬夜了三天没睡似的。\n\n你发这个是想让我心疼你吗
结果只发出第一句。日志 [表情] 解析结果 里 cleaned 带着完整两句 →
说明 reply 是全的，是 _trim_incomplete 把"没标点的尾行"当半截话删了。
QQ 聊天里「……吗」「……啊」这种不带问号的完整句太常见，一律删 = 吞最后一句。

新规则只有两种情况才删：
  ① 连接符收尾（，、：（……）→ 明显没说完；
  ② 长度 ≥20 且无任何结尾符号 → 疑似 token 掐断的长残句。
短尾行一律保留。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

# 停掉 bot_core 带起来的守护线程：否则测试全过、退出码却是 0xC0000409
# （解释器关闭时 daemon 线程还在写 stdout，抢锁失败 → 拖垮退出码）。
# 详见 tests/_quiet_exit.py 开头的说明。
sys.path.insert(0, HERE)
from _quiet_exit import quiet_exit  # noqa: E402
quiet_exit()

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


def main():
    t = bot_core._trim_incomplete

    print("\n=== ① 线上事故原样钉死 ===")
    src = "这图看着跟熬夜了三天没睡似的。\n\n你发这个是想让我心疼你吗"
    out = t(src)
    check("★ 不带问号的完整尾句保留", "你发这个是想让我心疼你吗" in out, repr(out))
    check("第一句也在", "熬夜了三天" in out, repr(out))

    print("\n=== ② 短尾行（真人不带标点）一律保留 ===")
    for s in ["行", "好嘞", "确实", "那倒也是", "笑死我了哈哈哈哈", "你干嘛啊"]:
        check(f"保留「{s}」", t(s) == s, repr(t(s)))

    print("\n=== ③ 真·半截话仍然要删 ===")
    out2 = t("今天天气不错。\n但是吧，")
    check("逗号收尾 → 删（多行时前面的保留）", out2 == "今天天气不错。", repr(out2))
    out2b = t("今天天气不错。但是吧，")
    check("单行逗号收尾 → 保守返回原文（宁发不吞）",
          out2b == "今天天气不错。但是吧，", repr(out2b))
    # 2026-10-14 语义升级：长残句"无标点就删"的启发式让位给**事实信号**——
    # 只有 truncated=True（finish_reason=length 实锤被掐断）才删长残句。
    out3 = t("第一句说完了。\n" + "这是一段明显被掐断的很长的话它甚至都没有说完的迹象",
             truncated=True)
    check("截断实锤 + 长残句无结尾符号 → 删", out3 == "第一句说完了。", repr(out3))
    out3b = t("第一句说完了。\n" + "这是一段明显被掐断的很长的话它甚至都没有说完的迹象")
    check("未截断 → 长残句也保留（不再瞎猜）", out3b != "第一句说完了。", repr(out3b)[:60])
    out4 = t("未闭合（括号里的话")
    check("全是不完整段 → 保守返回原文（不吞成空）",
          out4 == "未闭合（括号里的话", repr(out4))

    print("\n=== ③b 2026-10-14 私聊事故钉死：21字完整句曾被误删 ===")
    incident = "是不是你发的方式不对，要不你截个图搜我瞅瞅"
    check("★ 事故句（21字无标点·未截断）必须保留",
          t(incident) == incident, repr(t(incident)))
    # 元信息透传：_mark_truncated 的 str 子类对下游透明
    marked = bot_core._mark_truncated("正常回复", True)
    check("★ _mark_truncated 是 str 子类且带 truncated=True",
          isinstance(marked, str) and marked.truncated is True)
    check("普通字符串 getattr 兜底 False",
          getattr("裸串", "truncated", False) is False)

    print("\n=== ④ 带正常结尾符号的尾行保留 ===")
    for s in ["你说呢？", "走。", "好！"]:
        check(f"保留「{s}」", t(s) == s, repr(s))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
