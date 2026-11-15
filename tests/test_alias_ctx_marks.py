# -*- coding: utf-8 -*-
"""消息壳（`[当前]` / `[引用…]`）不能变成称呼，也不能被发出去。

同源两处事故（2026-11-09 真机）：

1. 引用消息在 prompt 里被拼成 `明天记得叫我\\n[当前] 你没发` ——
   `people_alias.parse_alias` 拿整段去找「叫我 X」，把 `[当前]` 存成了**称呼**，
   之后每轮 prompt 都在教模型叫他「[当前]」。
2. `[当前]` 本来只是拼 prompt 用的分隔标记，模型却把它抄进回复发了出去。

两层修法：
  · 写侧 `people_alias`：解析前先剥壳（`strip_annotations`），带方括号的一律
    不算称呼；`alias_usable` 读侧也拦（存量数据里躺着的 `[当前]` 直接失效）。
  · 出口 `reply_guard`：模型把壳抄进回复时删掉，别让用户看见后台标记。

运行：python tests/test_alias_ctx_marks.py   （0 通过，1 失败）
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import people_alias as pa  # noqa: E402
import reply_guard as rg  # noqa: E402

FAIL = []


def check(cond, name, extra=""):
    if cond:
        print("  ok   " + name)
    else:
        print("[FAIL] %s  %s" % (name, extra))
        FAIL.append(name)


def eq(got, want, name):
    check(got == want, name, "得到 %r，期望 %r" % (got, want))


tmp = tempfile.mkdtemp(prefix="alias_ctx_marks_")
_orig = pa.store_path()
try:
    pa.set_store_path(os.path.join(tmp, "people_aliases.json"))

    print("[1] 写侧：引用消息里的壳不能被当成称呼")
    eq(pa.parse_alias("明天记得叫我\n[当前] 你没发"), "",
       "「明天记得叫我 / [当前] 你没发」不产出称呼")
    eq(pa.parse_alias("[引用] 明天记得叫我\n[当前] 你没发"), "",
       "带 [引用] 前缀的同一句也不产出称呼")
    eq(pa.parse_alias("[引用:某人｜他在引用某人] 他说\n[当前] 叫我小a吧"), "小a",
       "壳前面的旧话不参与，取 [当前] 后面的「叫我小a吧」→ 小a")
    eq(pa.parse_alias("叫我[当前]"), "", "「叫我[当前]」不当称呼")
    eq(pa.parse_alias("以后叫我小a吧"), "小a", "正常说法不受影响（叫我小a吧 → 小a）")
    eq(pa.parse_alias("就叫我L啦"), "L", "正常说法不受影响（就叫我L啦 → L）")
    eq(pa.parse_alias("别叫我老板"), "", "否定句仍然反向，不存")

    print("[2] 剥壳本身")
    eq(pa.strip_annotations("[引用:甲｜…] 原文\n[当前] 真正的发言"), "真正的发言",
       "有 [当前] 就只取它后面的")
    eq(pa.strip_annotations("[引用:甲｜…] 只有引用没有当前标记"), "只有引用没有当前标记",
       "没有 [当前] 就按行删引用壳")

    print("[3] 读侧：存量脏数据读出来也不能用")
    check(pa.alias_usable("[当前]") is False, "alias_usable('[当前]') = False")
    check(pa.alias_usable("(・_・)") is True, "真网名不被误杀：'(・_・)' = True")
    check(pa.alias_usable("😀") is True, "emoji 名字不被误杀")
    check(pa.set_alias("9", "[当前]") is False, "set_alias 写侧拒绝 [当前]")
    eq(pa.get_alias("9"), "", "写完读出来是空")
    pa.set_alias("9", "老大")
    check(pa.get_alias("9") == "老大" and "老大" in pa.prompt_line("9"),
          "正常称呼照样能存能注入", pa.prompt_line("9"))

    print("[4] 约定解析同样先剥壳")
    eq(pa.parse_agreement("说一句，天王盖地虎，你再回答我小a炖蘑菇"),
       ("天王盖地虎", "小a炖蘑菇"), "老说法不受影响")
    trig2, rep2 = pa.parse_agreement("你把内容记下来\n[当前] 说天王盖地虎你答小a炖蘑菇")
    check(trig2 != "天王盖地虎你", "壳不参与解析（触发词不会粘上「你」字）",
          repr((trig2, rep2)))

    print("[5] 出口：模型把壳抄进回复时要删掉")
    eq(rg.guard("[当前]，我为谁忙成这样。"), "我为谁忙成这样。",
       "「[当前]，…」删标记并清掉句首逗号")
    eq(rg.guard("嗯，漏了。\n[当前] 我留了个空"), "嗯，漏了。\n我留了个空",
       "多行里只删标记，其它内容不动")
    eq(rg.guard("[引用:甲] 嗯"), "嗯", "引用壳也删")
    eq(rg.guard("正常一句，没有标记。"), "正常一句，没有标记。", "无标记文本原样返回")
    eq(rg.guard(""), "", "空串原样返回")
    eq(rg.guard(rg.guard("[当前]，我为谁忙成这样。")), "我为谁忙成这样。",
       "幂等：跑两遍结果不变")
finally:
    pa.set_store_path(_orig)
    shutil.rmtree(tmp, ignore_errors=True)

print("-" * 60)
if FAIL:
    print("失败 %d 条：%s" % (len(FAIL), "、".join(FAIL)))
    sys.exit(1)
print("自检全部通过")
