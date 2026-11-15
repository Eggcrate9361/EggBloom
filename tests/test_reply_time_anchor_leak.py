# -*- coding: utf-8 -*-
"""回复里抄进来的时间锚必须剥掉，正文里的日期不能被误伤。

背景（2026-11-09 真机）：输入行首是 `11-09 11:56｜`，模型把「日」抄丢了，
输出成 `11-11:56｜……`。`reply_budget._CTX_MARK_PLAIN` 原本只认两种形态：

    `10-30 11:40｜`（日期 空格 钟点）
    `21:12｜`      （只有钟点）

`11-11:56｜` 两边都不沾，时间戳就原样发给了用户。
修法：补一个「月-时:分」分支。**必须带分隔符才算**，否则正文里的
「11-11 那天」「11-11:56 走的」会被吃掉。

运行：python tests/test_reply_time_anchor_leak.py   （0 通过，1 失败）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from reply_budget import strip_ctx_marks  # noqa: E402

FAIL = []


def eq(got, want, name):
    if got == want:
        print("  ok   " + name)
    else:
        print("[FAIL] %s（得到 %r，期望 %r）" % (name, got, want))
        FAIL.append(name)


print("[1] 模型抄歪的时间锚要剥")
eq(strip_ctx_marks("11-11:56｜……漏出来了啊"), "……漏出来了啊", "月-时:分（真机形态）")
eq(strip_ctx_marks("11-11:55｜嗯，就这样。"), "嗯，就这样。", "月-时:分，短句")
eq(strip_ctx_marks("11-11:56｜第一行\n11-11:57｜第二行"), "第一行\n第二行", "多行逐行剥")
eq(strip_ctx_marks("11-11:56|半角竖线"), "半角竖线", "半角分隔符")

print("[2] 旧形态不能回退")
eq(strip_ctx_marks("11-09 11:56｜你好"), "你好", "日期 空格 钟点")
eq(strip_ctx_marks("11:56｜你好"), "你好", "只有钟点")
eq(strip_ctx_marks("[11-09 11:56] 你好"), "你好", "方括号旧格式")

print("[3] 正文里的日期/时间不能误伤")
eq(strip_ctx_marks("11-11 那天我在忙"), "11-11 那天我在忙", "没分隔符的日期")
eq(strip_ctx_marks("11-11:56 漏出来了"), "11-11:56 漏出来了", "月-时:分 但没｜")
eq(strip_ctx_marks("我 11-11:56 走的"), "我 11-11:56 走的", "行中的时间不动")

print("[4] 剥完是空的（整条只有时间戳）→ 交给上层当无声")
eq(strip_ctx_marks("11-11:56｜"), "", "只有标记")

print("-" * 60)
if FAIL:
    print("失败 %d 条：%s" % (len(FAIL), "、".join(FAIL)))
    sys.exit(1)
print("自检全部通过")
