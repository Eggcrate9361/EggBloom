# -*- coding: utf-8 -*-
"""bot_core._outbound_verify 的判据回归（2026-11-09，§2 #32 配套）。

送达校验只做一件事：在「不确定失败」时确认这条消息**刚刚真的发出去过**。
它最容易写反的地方是把「查不到」当成「确认已送达」——那会直接吞掉用户
本该收到的回复，比重复一条更糟。本用例把这条边界钉死：

  · 只有 60 秒窗口内、自己发的、文本完全一致 → True；
  · 缺时间戳 / 窗口外 / 别人发的 / 文本不同 → None（按未送达重发）；
  · 历史接口不可用、无可比文本、校验自身异常 → None，绝不猜成 True。

运行：python tests/test_outbound_verify.py
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import ExitStack
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


TEXT = "这条是刚发的回复"
NOW = time.time()


def _msg(text=TEXT, *, uid="10001", ts=None, raw=True):
    m = {"user_id": uid}
    if ts is not None:
        m["time"] = ts
    if raw:
        m["raw_message"] = text
    return m


def _verify(msgs, payload=None, *, retcode=0, bot_qq="10001",
            raise_api=False, extract=None):
    resp = {"retcode": retcode, "data": {"messages": msgs}}
    calls = []

    def api(endpoint, data):
        calls.append((endpoint, data))
        if raise_api:
            raise RuntimeError("历史接口炸了")
        return resp

    patches = [mock.patch.object(bot_core, "api_call", side_effect=api),
               mock.patch.object(bot_core, "BOT_QQ", bot_qq)]
    if extract is not None:
        patches.append(mock.patch.object(bot_core, "extract_text",
                                        return_value=extract))
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        got = bot_core._outbound_verify(
            "text", "1000000005",
            payload if payload is not None else {"text": TEXT, "message": TEXT},
            is_group=False)
    return got, calls


def main():
    got, calls = _verify([_msg(ts=NOW - 5)])
    check("★ 5 秒前自己发的同文案 → 确认已送达", got is True, got)
    check("查的是私聊历史接口", calls and calls[0][0] == "get_friend_msg_history", calls)

    check("文本不同 → 查不了", _verify([_msg("别的", ts=NOW - 5)])[0] is None)
    check("别人发的 → 查不了", _verify([_msg(uid="99999", ts=NOW - 5)])[0] is None)
    # 关键边界：缺时间戳的老消息不能被当成「刚发的」
    check("★ 缺 time 字段的同文案 → 不误判成已送达",
          _verify([_msg(ts=None)])[0] is None)
    check("★ 窗口外（120 秒前）的同文案 → 不误判成已送达",
          _verify([_msg(ts=NOW - 120)])[0] is None)
    check("历史接口 retcode != 0 → 查不了",
          _verify([_msg(ts=NOW - 5)], retcode=-1)[0] is None)
    check("没有可比文本（消息段）→ 查不了",
          _verify([_msg(ts=NOW - 5)],
                  payload={"message": [{"type": "image"}], "text": ""})[0] is None)
    check("校验自身异常 → 查不了且不抛", _verify([], raise_api=True)[0] is None)
    check("raw_message 缺失时走 extract_text 兜底",
          _verify([_msg(ts=NOW - 5, raw=False)], extract=TEXT)[0] is True)
    with mock.patch.object(bot_core, "api_call", return_value={"retcode": 0}):
        check("target 为空 → 查不了",
              bot_core._outbound_verify("text", "", {"text": TEXT}) is None)

    print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
    for n in FAIL:
        print(f"  FAIL  {n}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
