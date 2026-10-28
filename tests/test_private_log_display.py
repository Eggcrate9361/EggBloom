# -*- coding: utf-8 -*-
"""私聊日志的 QQ 号显示（2026-10-28）

## 线上症状

私聊日志显示 `私聊[朱哥(201125)]` —— 而真实对话者是 Texas(1234567892)。

## 根因（两层叠加）

1. 私聊日志全部写 `sender[:6]`，把 1234567892 截成 **201125**；
2. `log()` 里的 `_replace_qq_with_nick` 用 `\d{5,12}` 匹配数字并**真实查询**，
   201125 恰好是另一个真实存在的老 QQ 号（主人"朱哥"）→
   `get_stranger_info` 查到他的昵称，日志变成 `朱哥(201125)`，
   昵称缓存里还多了一条错误映射。

这不只是显示难看：**日志里每出现一次截断号，就可能对错误的人发起一次
真实 API 查询**，还污染缓存。

## 修法

- 7 处 `sender[:6]` 全部改为完整 `sender` —— `log()` 会用启动时预加载的
  昵称缓存把完整 QQ 渲染成 `Texas(1234567892)`，不再产生假号码。
- 源码护栏：`sender[:6]` 这种"截断 QQ 再交给昵称替换器"的写法禁止回归。

运行：python tests/test_private_log_display.py
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_core as bc  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def test_no_truncated_qq_in_source():
    print("\n[1] 源码护栏：私聊日志不得再截断 QQ 号")
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    hits = re.findall(r"sender\[:6\]", src)
    check("★ 不存在 sender[:6]", not hits, f"命中 {len(hits)} 处")
    hits2 = re.findall(r"str\(sender\)\[:6\]", src)
    check("★ 不存在 str(sender)[:6]", not hits2, f"命中 {len(hits2)} 处")


def test_replacer_formats_known_nick():
    print("\n[2] 完整 QQ 会被替换器渲染成 昵称(QQ)")
    orig = bc.resolve_qq_nick
    bc.resolve_qq_nick = lambda qq: {"1234567892": "Texas"}.get(qq)
    try:
        out = bc._replace_qq_with_nick("私聊[1234567892] 入缓冲 等 5.0s")
        check("★ 渲染为 Texas(1234567892)",
              out == "私聊[Texas(1234567892)] 入缓冲 等 5.0s", out)
    finally:
        bc.resolve_qq_nick = orig


def test_replacer_ignores_non_qq_numbers():
    print("\n[3] 不是 QQ 的数字保持原样（未缓存则不查、不猜）")
    orig = bc.resolve_qq_nick
    bc.QQ_NICK_CACHE.pop("300000", None)
    bc.resolve_qq_nick = lambda qq: bc.QQ_NICK_CACHE.get(qq)
    try:
        out = bc._replace_qq_with_nick("花了 300000 元买了个教训")
        check("★ 未知数字原样保留", out == "花了 300000 元买了个教训", out)
    finally:
        bc.resolve_qq_nick = orig


def main():
    test_no_truncated_qq_in_source()
    test_replacer_formats_known_nick()
    test_replacer_ignores_non_qq_numbers()
    print("\n" + "=" * 56)
    if _fail:
        print(f"FAILED ({len(_fail)}): " + ", ".join(_fail))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()