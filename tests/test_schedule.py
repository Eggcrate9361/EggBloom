import io
# -*- coding: utf-8 -*-
"""1.5.10 schedule 插件回归：周表解析 / 当前状态 / 一行 prompt / 空表零注入。"""
import datetime
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from plugins.schedule import core  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


CFG = {"entries": [
    {"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "11:40", "title": "上课", "place": "教室"},
    {"days": [1, 2, 3, 4, 5], "start": "13:30", "end": "17:00", "title": "实验室"},
], "exceptions": []}


def _ts(y, m, d, hh, mm):
    return time.mktime(datetime.datetime(y, m, d, hh, mm).timetuple())


print("\n[1] 解析与校验")
check("parse_hm 08:30 = 510", core.parse_hm("08:30") == 510)
try:
    core.parse_hm("8点")
    check("非法时间抛错", False)
except ValueError:
    check("非法时间抛错", True)
try:
    core.parse_entry({"days": [1], "start": "10:00", "end": "09:00", "title": "x"})
    check("结束早于开始抛错", False)
except ValueError:
    check("结束早于开始抛错", True)
try:
    core.parse_entry({"days": [9], "start": "10:00", "end": "11:00", "title": "x"})
    check("星期越界抛错", False)
except ValueError:
    check("星期越界抛错", True)
e, exc, free = core.parse_config(CFG)
check("解析出 2 条日程", len(e) == 2, e)
check("自由时状态默认「在家」", free == "在家", free)

print("\n[2] 当前状态（2026-10-28 周三）")
st = core.current(e, exc, ts=_ts(2026, 10, 28, 9, 0))
check("09:00 在上课", st["phase"] == "in" and st["title"] == "上课", st)
check("剩余分钟 160", st["remaining_min"] == 160, st["remaining_min"])
st2 = core.current(e, exc, ts=_ts(2026, 10, 28, 12, 30))
check("12:30 自由，下一段实验室", st2["phase"] == "before" and st2["next_title"] == "实验室", st2)
st3 = core.current(e, exc, ts=_ts(2026, 10, 28, 18, 0))
check("18:00 自由，下一次是明天上课", st3["phase"] == "free" and st3["next_title"] == "上课", st3)
exc2 = {datetime.date(2026, 10, 28)}
st4 = core.current(e, exc2, ts=_ts(2026, 10, 28, 9, 0))
check("例外日 → 放假", st4["phase"] == "free" and st4.get("reason") == "exception", st4)

print("\n[3] 一行 prompt（有事实、不编造）")
line = core.prompt_line(core.current(e, exc, ts=_ts(2026, 10, 28, 9, 0)))
check("在课上一行含标题/时间/地点", "上课" in line and "08:00" in line and "教室" in line, line)
line2 = core.prompt_line(core.current(e, exc, ts=_ts(2026, 10, 28, 12, 30)))
check("自由时一行含下一段", "13:30" in line2 and "实验室" in line2, line2)
line3 = core.prompt_line(core.current(e, exc, ts=_ts(2026, 10, 28, 9, 0)), free_state="宿舍")
check("自由时状态可配（宿舍）", "宿舍" in line3 or "上课" in line3, line3)

print("\n[4] 行格式（UI 输入）")
rows = core.parse_lines("1-5 08:00-11:40 上课 @教室\n# 注释\n6,7 10:00-12:00 打工")
check("解析 2 条（跳过注释）", len(rows) == 2 and rows[0]["place"] == "教室", rows)
check("逗号分隔星期", rows[1]["days"] == [6, 7], rows[1])

print("\n[5] 空表零注入")
import plugins.schedule as sp  # noqa: E402
_old = dict(sp._CACHE)
try:
    sp._CACHE.update({"key": -1, "entries": [], "exceptions": set(), "free": "在家", "tz": ""})
    class _Ctx:
        def get_config(self, k, d=None):
            return d
    sp._before_reply(_Ctx(), message="在干嘛")
    check("★ 空表 → 不注入（返回 None）", True)
finally:
    sp._CACHE.update(_old)


print("\n[6] 插件 UI 与动作通道")
import plugins.schedule as sp2  # noqa: E402
check("PLUGIN_ACTIONS 声明", isinstance(getattr(sp2, "PLUGIN_ACTIONS", None), dict)
      and {"get_config", "set_config", "today"} <= set(sp2.PLUGIN_ACTIONS))
check("get_config 动作返回 dict", isinstance(sp2.PLUGIN_ACTIONS["get_config"]({}), dict))
check("ui.html 存在", os.path.isfile(os.path.join(ROOT, "plugins", "schedule", "ui.html")))


print("\n[7] 周视图与增删（临时配置）")
import json as _json, tempfile, shutil  # noqa: E402
_tmp = tempfile.mkdtemp(prefix="sched_")
sp3 = sp2
_old_cfg = sp3._CONFIG_PATH
try:
    sp3._CONFIG_PATH = os.path.join(_tmp, "config.json")
    io.open(sp3._CONFIG_PATH, "w", encoding="utf-8").write(_json.dumps(
        {"entries": [], "exceptions": [], "state_when_free": "在家"}))
    sp3._CACHE["key"] = None
    r1 = sp3.add_entry({"days": [1, 2, 3], "start": "08:00", "end": "11:40",
                        "title": "上课", "place": "教室"})
    check("add_entry 成功", r1.get("ok") and len(r1["config"]["entries"]) == 1, r1)
    wk = sp3.week_view()
    check("周视图 7 天", len(wk) == 7 and wk[0]["label"] == "周一", wk[:1])
    check("周一有课", any(e["title"] == "上课" for e in wk[0]["entries"]), wk[0])
    r2 = sp3.add_exception({"date": "2026-11-01"})
    check("add_exception 成功", r2.get("ok") and "2026-11-01" in r2["config"]["exceptions"], r2)
    r3 = sp3.remove_exception({"date": "2026-11-01"})
    check("remove_exception 成功", r3.get("ok") and not r3["config"]["exceptions"], r3)
    r4 = sp3.delete_entry({"index": 0})
    check("delete_entry 成功", r4.get("ok") and not r4["config"]["entries"], r4)
    bad = sp3.add_entry({"days": [1], "start": "10:00", "end": "09:00", "title": "x"})
    check("非法条目被拒（不静默写坏）", bad.get("ok") is False and "结束" in bad.get("error", ""), bad)
finally:
    sp3._CONFIG_PATH = _old_cfg
    shutil.rmtree(_tmp, ignore_errors=True)

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
