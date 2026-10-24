# -*- coding: utf-8 -*-
"""重算 static_prompt 黄金夹具（tests/fixtures_static_prompt_golden.json）。

什么时候要跑
    改了任何 prompt 段、人格文件读取方式、或「你是谁」身份块的拼法之后，
    `tests/test_prompt_segments.py::test_golden_byte_identical` 会报
    「长度变了 / 内容变了」。那是**预期的** —— 黄金哈希的本意就是
    " prompt 变了必须有人明确知道"。

    确认变动是你要的之后，跑本脚本重算夹具，然后**在提交信息里写清
    为什么变**（否则下一个人看到哈希变了不知道该回退还是该接受）。

环境与 test_prompt_segments.py 完全一致（钉死昵称/QQ/人格/de_ai），
否则生成的哈希只在你的机器上成立。
"""
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import bot_core  # noqa: E402
import de_ai  # noqa: E402
from test_prompt_segments import (PINNED_DEAI, PINNED_NICK, PINNED_PERSONALITY,  # noqa: E402
                                  PINNED_QQ, GOLDEN)


def main():
    old = {
        "nicks": bot_core._bot_nicknames,
        "group_nick": bot_core.BOT_GROUP_NICK,
        "qq": bot_core.BOT_QQ,
        "load_p": bot_core.load_personality,
        "deai": de_ai.de_ai_rule_block,
    }
    bot_core._bot_nicknames = lambda: [PINNED_NICK]
    bot_core.BOT_GROUP_NICK = PINNED_NICK
    bot_core.BOT_QQ = PINNED_QQ
    bot_core.load_personality = lambda: PINNED_PERSONALITY
    de_ai.de_ai_rule_block = lambda cfg=None: PINNED_DEAI
    try:
        out = {}
        for is_group, key in ((True, "group"), (False, "private")):
            bot_core._STATIC_PROMPT_CACHE.clear()
            t = bot_core._build_static_prompt(is_group)
            out[key] = {
                "len": len(t),
                "sha256": hashlib.sha256(t.encode("utf-8")).hexdigest(),
            }
            print("%s: %d 字符  sha=%s" % (key, len(t), out[key]["sha256"][:16]))
    finally:
        bot_core._bot_nicknames = old["nicks"]
        bot_core.BOT_GROUP_NICK = old["group_nick"]
        bot_core.BOT_QQ = old["qq"]
        bot_core.load_personality = old["load_p"]
        de_ai.de_ai_rule_block = old["deai"]

    prev = {}
    if os.path.exists(GOLDEN):
        try:
            prev = json.load(io.open(GOLDEN, encoding="utf-8"))
        except Exception:
            prev = {}
    if prev:
        for k in out:
            if k in prev:
                d = out[k]["len"] - prev[k]["len"]
                print("  %s: %d → %d（%+d）" % (k, prev[k]["len"], out[k]["len"], d))

    io.open(GOLDEN, "w", encoding="utf-8", newline="").write(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print("已写出", os.path.relpath(GOLDEN, ROOT))


if __name__ == "__main__":
    main()
