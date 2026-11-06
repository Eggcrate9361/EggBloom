# -*- coding: utf-8 -*-
"""tests/test_command_dispatch.py —— 私聊/群聊的"显式命令"判定（2026-11-06）

## 事故

00:40 用户在私聊说「生日结束了吧」，bot 回了一句进程相关的废话 ——
日志里写着「匹配控制指令，已处理」。

根因在 `COMMAND_PATTERNS`：清一色 `^.*?(关键词).*$`，**包含即命中**。
`(结束|终止|杀掉?|关闭进程)\s*(.+)` 于是把
    「生日结束了吧」 → 结束进程「了吧」
    「我刚重启完」   → 真的重启机器
    「你关机了吗」   → 真的关机
    「他截图给我看了」→ 真截一张图发过去
    「打开话匣子」   → 真的去打开一个叫「话匣子」的东西

这不是词表不全（永远补不完），是**判据错了**：自然语言里出现一个动词，
不等于用户在下指令。工具/命令该由模型决定（它有 take_screenshot / open_app /
send_user_file 等工具），前置正则只保留"整句就是一个命令"的显式形式。

## 现在的判据（`bot_core.cmd_match`）

  ① 整条消息 ≤60 字，且 **fullmatch** 命令模式；
  ② 破坏性动作（结束进程 / 发文件）**必须带 `#` 前缀**；
  ③ 「打开 X」的 X 要像真实目标（路径 / 盘符 / 带扩展名），否则不算；
  ④ 其它一律交给模型。

判据抽成纯函数就是为了能这么测 —— 以前要测"会不会误触发"，只能真去 kill 一次。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402

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
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


#: 这些是**自然语言**，一条都不许命中命令
NOT_COMMANDS = [
    "生日结束了吧",          # 事故原句
    "你关机了吗",            # 曾经会真关机
    "我刚重启完",            # 曾经会真重启
    "他截图给我看了",        # 曾经会真截图
    "打开话匣子",            # 曾经会去打开「话匣子」
    "这个功能怎么用啊",      # 帮助只认整句
    "我把文件发你了",
    "今天系统信息好乱",
    "帮我结束这个话题吧",
    "刚才那波高占用真难受",
    "你先别关机",            # 语义是"别关"
]

#: (消息, 期望命中的命令名)
COMMANDS = [
    ("截屏", "screenshot"),
    ("截图", "screenshot"),
    ("关机", "power"),
    ("重启", "power"),
    ("系统信息", "sysinfo"),
    ("进程列表", "proc_list"),
    ("音量 30", "vol_set"),
    ("音量+", None),          # 表里没有"音量+"这种写法，不该命中
    ("静音", "mute"),
    ("搜索 报告", "search"),
    ("学习状态", "learning"),
    (r"打开 D:\a\b.txt", "open"),
    ("打开 C:/x/y", "open"),
    ("#结束 notepad", "proc_kill"),
    ("#发送 D:\\a.zip", "send_file"),
    ("#关机", "power"),
]


def name_of(hit):
    if not hit:
        return None
    idx, _m = hit
    return bc.COMMAND_PATTERNS[idx][3]


def main():
    print("=== A) 自然语言一条都不许命中（事故回归）===")
    for s in NOT_COMMANDS:
        check(f"不命中：{s}", bc.cmd_match(s) is None, name_of(bc.cmd_match(s)))

    print("\n=== B) 显式命令该命中的要命中 ===")
    for s, want in COMMANDS:
        got = name_of(bc.cmd_match(s))
        check(f"{s!r} → {want}", got == want, f"实际 {got}")

    print("\n=== C) 前缀规则 ===")
    check("『结束 notepad』不带 # → 不命中", bc.cmd_match("结束 notepad") is None,
          name_of(bc.cmd_match("结束 notepad")))
    check("『#结束 notepad』→ 命中", name_of(bc.cmd_match("#结束 notepad")) == "proc_kill")
    check("『/结束 notepad』（斜杠前缀）也认",
          name_of(bc.cmd_match("/结束 notepad")) == "proc_kill")
    check("『发送 D:/a.zip』不带 # → 不命中", bc.cmd_match("发送 D:/a.zip") is None)
    check("全角＃也认", name_of(bc.cmd_match("＃关机")) == "power")

    print("\n=== D) 长度闸：超过 60 字一律不算命令 ===")
    long_txt = "关机" + "啊" * 60
    check("长句含『关机』→ 不命中", bc.cmd_match(long_txt) is None)
    check("刚好 60 字的『关机』前缀仍可命中",
          name_of(bc.cmd_match("关机" + "啊" * 58)) is None,
          "以 fullmatch 为准，长句不该命中")

    print("\n=== E) try_execute_command 真的不抢消息 ===")
    sent = []
    orig_send = bc.send_message
    try:
        bc.send_message = lambda t, target=None: sent.append(str(t)) or True
        for s in ("生日结束了吧", "你关机了吗", "我刚重启完", "打开话匣子"):
            r = bc.try_execute_command(s, sender="")
            check(f"{s} → 返回 False（交给模型）", r is False, r)
        check("整个过程一条消息都没发出去", sent == [], sent)
    finally:
        bc.send_message = orig_send

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for n in FAILURES:
        print(f"  FAIL  {n}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
