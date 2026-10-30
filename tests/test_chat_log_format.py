# -*- coding: utf-8 -*-
"""私聊日志格式（2026-10-30 真机复盘）。

## 现象

    14:16:55  **对方** 你好
    14:17:16  **小a**  你好          ← 用户的同一句话被记在 Bot 名下
              > 醒着呢

读起来像"Bot 把对方的话重复了一遍 / 又打了声招呼"。实际发出去的是
`[发送] 句=醒着呢`，用户看到的没有问题 —— 坏的只是日志。

根因：入口已经用「对方」写过一次用户消息（bot_core:15836），
回复侧又把同一个 `text` 传给了 `save_chat_log`，于是同一句出现两次、
后一次挂在 Bot 名字下。现在回复侧传空串，`save_chat_log` 只写 `> 回复`。

运行：python tests/test_chat_log_format.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


tmp = tempfile.mkdtemp(prefix="chatlog_test_")
_orig = bot_core.CHAT_LOG_DIR
_orig_log = bot_core.log
try:
    bot_core.CHAT_LOG_DIR = tmp
    bot_core.log = lambda _m: None

    # 入口：用户消息
    bot_core.save_chat_log("10001", "对方", "我在上课")
    # 回复：只写回复（新行为）
    bot_core.save_chat_log("10001", "小a", "", "上课还戳我，摸鱼摸得挺自在\n\n讲台上的没发现你吧")

    import datetime
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    p = os.path.join(tmp, "QQ_10001", f"chat_{today}.md")
    body = open(p, encoding="utf-8").read() if os.path.isfile(p) else ""

    check("用户消息出现且只出现一次", body.count("我在上课") == 1, body.count("我在上课"))
    check("用户消息挂在「对方」下", "**对方**" in body, body[:200])
    check("回复挂在 Bot 名下", "**小a**" in body, body[:200])
    check("回复是引用块", "> 上课还戳我" in body, body[:300])
    check("Bot 名下不再重复用户原话",
          body.split("**小a**")[-1].count("我在上课") == 0, body[-300:])
    check("没有多余空行块", "\n\n\n\n" not in body, repr(body[-80:]))

    # 旧式调用（text + reply 都给）仍能工作，不破坏别处
    bot_core.save_chat_log("10001", "小a", "问题", "答案")
    body2 = open(p, encoding="utf-8").read()
    check("旧式调用仍写问答", "问题" in body2 and "> 答案" in body2, body2[-120:])
finally:
    bot_core.CHAT_LOG_DIR = _orig
    bot_core.log = _orig_log
    shutil.rmtree(tmp, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
