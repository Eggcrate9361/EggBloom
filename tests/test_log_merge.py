# -*- coding: utf-8 -*-
"""tests/test_log_merge.py —— 原始日志并流（2026-10-25）

`_read_log_rows` 原来是"按文件顺序追加、最后切尾"：多文件时时间倒序、
跨文件重复行各留一份、外部 logger 的日期前缀没人认。现在改成
"先解析、再排序、后截断"。这个文件锁住六件事：

  1. 三种时间戳写法都认，没有日期的行以文件 mtime 的日期为基准。
  2. 排序按 `(日期, 时间)`，不再按文件顺序。
  3. 行首 `[INFO]` / `[ERROR]` 抽进 level，正文不再重复。
  4. 连续重复的标签只留一个（`[事件] [事件]` → `[事件]`）。
  5. 跨文件完全相同的 `(时间, 正文)` 只留一条。
  6. 来源标签来自 `_log_label`，`data/logs/qq_bot.log` 在候选列表里。

运行：python tests/test_log_merge.py
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

import webui.webui_server as ws  # noqa: E402

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


class _TempLogs:
    """两个临时日志文件 + 固定来源标签，退出时还原 monkeypatch。"""

    def __init__(self, text_a, text_b, fixed_date="2026-10-25"):
        self.text_a, self.text_b, self.date = text_a, text_b, fixed_date
        self.dir = None
        self._orig = None

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="eggbloom_logmerge_")
        a = Path(self.dir) / "qq_bot.log"
        b = Path(self.dir) / "other.log"
        a.write_text(self.text_a, encoding="utf-8")
        b.write_text(self.text_b, encoding="utf-8")
        # A 文件没有日期前缀 → 读的时候取 mtime 的日期；钉死 mtime 让断言稳定
        stamp = time.mktime(datetime.datetime.strptime(
            self.date + " 12:00:30", "%Y-%m-%d %H:%M:%S").timetuple())
        os.utime(a, (stamp, stamp))
        self._orig = (ws._log_paths, ws._log_label)
        labels = {str(a): "data", str(b): "temp"}
        ws._log_paths = lambda: [a, b]
        ws._log_label = lambda p: labels.get(str(p), "log")
        return self

    def __exit__(self, *exc):
        ws._log_paths, ws._log_label = self._orig
        return False


def test_order_level_source():
    print("\n[1] 排序 / 级别抽取 / 来源标签")
    a = (
        "[12:00:01] [事件] [事件] 收到: post_type=message uid=小明(10001) gid=123456789\n"
        "[12:00:03] 第三条\n"
    )
    b = (
        "2026-10-25 12:00:00,900 [INFO] Sandglass 人物画像已载入\n"
        "2026-10-25 12:00:02,100 [ERROR] ONEBOT API 失败 [get_stranger_info]\n"
        "2026-10-25 12:00:01,500 [事件] [事件] 收到: post_type=message uid=小明(10001) gid=123456789\n"
    )
    with _TempLogs(a, b):
        rows = ws._read_log_rows(1000, with_meta=True)
    times = [r[0] for r in rows]
    check("按时间排序", times == ["12:00:00", "12:00:01", "12:00:02", "12:00:03"], times)
    check("四元组含级别与来源", all(len(r) == 4 for r in rows), rows)
    texts = [r[1] for r in rows]
    check("INFO 前缀抽走", texts[0] == "Sandglass 人物画像已载入", texts[0])
    check("ERROR 前缀抽走",
          texts[2] == "ONEBOT API 失败 [get_stranger_info]", texts[2])
    check("level: INFO→info", rows[0][2] == "info", rows[0][2])
    check("level: ERROR→err", rows[2][2] == "err", rows[2][2])
    check("来源: A 文件=data", rows[0][3] == "temp" and rows[1][3] == "data", rows)
    check("来源: B 文件=temp", rows[2][3] == "temp" and rows[0][3] == "temp", rows)
    check("重复标签折叠", texts[1] == "[事件] 收到: post_type=message uid=小明(10001) gid=123456789",
          texts[1])
    check("跨文件重复行只留一条",
          sum(1 for x in texts if "收到:" in x) == 1, texts)
    check("同一秒内保持文件内顺序",
          [t for t in times] == sorted(times), times)


def test_default_shape_and_continuation():
    print("\n[2] 默认返回 [时间, 正文]，续行仍并回上一条")
    a = "[12:00:01] Traceback (most recent call last):\n  File \"x.py\", line 1\n"
    with _TempLogs(a, ""):
        rows = ws._read_log_rows(1000)
    check("默认是二元组", rows and all(len(r) == 2 for r in rows), rows)
    check("续行并入正文", "\n" in rows[0][1] and "line 1" in rows[0][1], rows[0][1])


def test_chat_logs_dicts():
    print("\n[3] get_chat_logs 带 level / source，正文取 text")
    a = "[12:00:01] [INFO] 第一行\n[12:00:02] 第二行\n"
    with _TempLogs(a, ""):
        logs = ws.get_chat_logs(100)
    check("两条", len(logs) == 2, len(logs))
    check("正文来自 text", logs[0].get("text") == "第一行", logs[0])
    check("级别已抽好", logs[0].get("level") == "info", logs[0])
    check("来源标签在", logs[1].get("source") == "data", logs[1])
    check("没带 date 字段进前端", "date" not in logs[0], logs[0])


def test_real_paths_include_data_log():
    print("\n[4] data/logs/qq_bot.log 进了候选列表")
    paths = [str(p) for p in ws._log_paths()]
    check("含 data/logs/qq_bot.log",
          any(p.endswith(os.path.join("data", "logs", "qq_bot.log")) for p in paths), paths)
    hit = [p for p in ws._log_paths()
           if str(p).endswith(os.path.join("data", "logs", "qq_bot.log"))]
    check("来源标签是 data", hit and ws._log_label(hit[0]) == "data",
          hit and ws._log_label(hit[0]))
    check("未知文件记 log", ws._log_label(Path(tempfile.gettempdir()) / "nope.log") == "log")


def main():
    test_order_level_source()
    test_default_shape_and_continuation()
    test_chat_logs_dicts()
    test_real_paths_include_data_log()

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
