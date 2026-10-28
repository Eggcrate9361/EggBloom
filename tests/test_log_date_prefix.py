# -*- coding: utf-8 -*-
"""日志时间戳带日期（2026-10-28）

## 为什么改

`qq_bot.log` 原来只写 `[18:44:45]`。文件 5MB 轮转一次，一次运行跨天是常事，
于是**今天 18:44 和三天前 18:44 在文件里长得一模一样**，谁先谁后只能靠
"时钟回绕超过 6 小时"去猜（`webui._assign_dates` 的启发式）。

真机排查 1.5.9 那次「AI 不回复」时，按小时 grep 直接把不同日期的两条会话
串成了一条，绕了很大一圈才发现真日志在另一个目录、而且这一段是昨天的。

## 改了这一端，另一端必须跟着

写入端（`bot_core.log` / `utils.log`）加了日期，读取端有三处会**静默失效**：

| 读取端 | 不改的后果 |
|---|---|
| `log_levels._TS` | 剥不掉时间戳 → `_TAG` 把 `2026-10-28` 当标签名 → 全部退化成 INFO |
| `webui._ROW_TS_BRACKET` | 认不出新格式 → 整行被当成"续行"丢掉 → 日志页少掉全部业务日志 |

两者都不抛异常，只是"看起来正常但内容不对"，所以必须有测试钉住。

运行：python tests/test_log_date_prefix.py
"""
import datetime
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import log_levels  # noqa: E402
import webui.webui_server as ws  # noqa: E402

PASS = 0
FAIL = 0
_FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        _FAILURES.append(name)
        print(f"  [FAIL] {name}  {extra}")


def test_writer_has_date():
    """写入端：落在文件里的那一行必须带日期。"""
    print("\n[1] bot_core.log 写出的行首带 YYYY-MM-DD")
    import bot_core
    tmp = tempfile.mkdtemp(prefix="eggbloom_logdate_")
    p = Path(tmp) / "qq_bot.log"
    orig = bot_core.LOG_FILE
    bot_core.LOG_FILE = str(p)
    try:
        bot_core.log("[测试] 只有一行")
        text = p.read_text(encoding="utf-8").strip()
    finally:
        bot_core.LOG_FILE = orig
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    check("★ 行首是 [YYYY-MM-DD HH:MM:SS]",
          text.startswith(f"[{today} "), text[:40])
    check("正文仍在", "只有一行" in text, text)


def test_leading_tag_strips_date():
    """读取端 1：加了日期之后，行首标签仍要能取到。"""
    print("\n[2] log_levels 能从带日期的时间戳后面取到标签")
    cases = [
        ("[2026-10-28 18:44:45] [发送] 私聊 → x", "发送", "send"),
        ("[2026-10-28 18:44:45] [Plugin:x] 心跳线程启动", "Plugin", "sys"),
        ("[18:44:45] [事件] 收到: x", "事件", "sys"),   # 旧格式不能坏
    ]
    for line, tag, level in cases:
        got_tag = log_levels.leading_tag(line)
        got_lv = log_levels.classify(line)
        check(f"标签 {tag}", got_tag == tag, repr(got_tag))
        check(f"级别 {level}", got_lv == level, repr(got_lv))
    # 反面：不改 _TS 时，标签会变成日期字符串 —— 这条断言就是防回退的护栏
    check("★ 标签不是日期串",
          log_levels.leading_tag("[2026-10-28 18:44:45] [发送] x") != "2026-10-28",
          log_levels.leading_tag("[2026-10-28 18:44:45] [发送] x"))


def test_row_parser_accepts_date():
    """读取端 2：WebUI 行解析必须吃下新格式，且用行内日期。"""
    print("\n[3] webui 行解析认新格式（不能当成续行丢掉）")
    r = ws._parse_log_row("[2026-10-26 09:15:03] [事件] 收到: x", "2020-01-01", "data")
    check("★ 没有被当成续行丢掉", r is not None, r)
    if r:
        check("★ 日期取行内的，不是 base_date", r["date"] == "2026-10-26", r["date"])
        check("时间解析正确", r["time"] == "09:15:03", r["time"])
        check("正文正确", r["text"] == "[事件] 收到: x", r["text"])

    # 旧格式仍然可用（历史日志还在）
    r2 = ws._parse_log_row("[09:15:03] 旧行", "2020-01-01", "data")
    check("旧格式仍解析", r2 is not None and r2["time"] == "09:15:03", r2)
    check("旧格式沿用 base_date",
          r2 is not None and r2["date"] == "2020-01-01", r2)


def test_read_log_rows_mixed():
    """混合新旧格式：带日期的不再靠启发式猜。"""
    print("\n[4] 新旧混排时排序正确，带日期的行用真实日期")
    tmp = tempfile.mkdtemp(prefix="eggbloom_logmix_")
    a = Path(tmp) / "qq_bot.log"
    a.write_text(
        "[2026-10-27 23:59:59] 前一天的最后一分钟\n"
        "[2026-10-28 00:00:01] 新一天的第一分钟\n"
        "[00:00:02] 没有日期的旧行\n",
        encoding="utf-8")
    # mtime 钉在今天，模拟"旧行属于今天"
    stamp = time.mktime(datetime.datetime.strptime(
        "2026-10-28 12:00:00", "%Y-%m-%d %H:%M:%S").timetuple())
    os.utime(a, (stamp, stamp))
    orig = (ws._log_paths, ws._log_label)
    ws._log_paths = lambda: [a]
    ws._log_label = lambda p: "data"
    try:
        rows = ws._read_log_rows(1000, with_meta=False)
    finally:
        ws._log_paths, ws._log_label = orig
    times = [r[0] for r in rows]
    check("三行都没丢", len(rows) == 3, rows)
    check("★ 按真实日期排序（不是按 HH:MM:SS 排序）",
          times == ["23:59:59", "00:00:01", "00:00:02"], times)


def main():
    test_writer_has_date()
    test_leading_tag_strips_date()
    test_row_parser_accepts_date()
    test_read_log_rows_mixed()
    print("\n" + "=" * 56)
    if FAIL:
        print(f"通过 {PASS} / 失败 {FAIL}: " + ", ".join(_FAILURES))
        sys.exit(1)
    print(f"通过 {PASS} / 失败 0")


if __name__ == "__main__":
    main()