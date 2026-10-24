# -*- coding: utf-8 -*-
"""静默时段（"睡觉"）与"在做什么"。

覆盖 2026-10-24 的四件事：

  [1] 静默时段解析只有一份（time_ctx.in_quiet_hours），群聊才可能生效
  [2] 群聊回复链真的接了这道闸，且位置在 AI 调用之前（省的是真金白银）
  [3] 私聊/群聊共用同一个"你正在睡觉"注入函数（不是两段话术）
  [4] "在做什么"只跟时段走、不另开小时表

情绪基线不在这里，见 test_mood_baseline.py —— 它属于另一件事（mood_engine），
混在一起会让两个提交各自都跑不过。

判据全部落在**真实代码与真实行为**上，不测"某个变量名存在"。
"""
import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from harness import time_ctx  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def at(h, m=0):
    """构造今天的某个钟点（用当天日期，避开夏令时之类的怪值）。"""
    now = datetime.datetime.now()
    return now.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def main():
    print("[1] 静默时段解析：一份实现，边界明确")
    # 常规窗口
    check("1:00-7:00 在 03:00 命中", time_ctx.in_quiet_hours("1:00-7:00", at(3)))
    check("1:00-7:00 在 00:30 不命中（起点之前）",
          not time_ctx.in_quiet_hours("1:00-7:00", at(0, 30)))
    check("1:00-7:00 在 07:00 不命中（右开区间）",
          not time_ctx.in_quiet_hours("1:00-7:00", at(7)))
    check("1:00-7:00 在 06:59 命中", time_ctx.in_quiet_hours("1:00-7:00", at(6, 59)))
    # 跨天
    check("23:00-7:00 在 23:30 命中（跨天左段）",
          time_ctx.in_quiet_hours("23:00-7:00", at(23, 30)))
    check("23:00-7:00 在 02:00 命中（跨天右段）",
          time_ctx.in_quiet_hours("23:00-7:00", at(2)))
    check("23:00-7:00 在 12:00 不命中", not time_ctx.in_quiet_hours("23:00-7:00", at(12)))
    # 空 / 坏值：一律不静默（宁可少静默，也不能误静默把人全挡掉）
    for bad in ("", "   ", None, "abc", "25:00-7:00", "1:00", "1:00-7:70"):
        check(f"{bad!r} 不静默（不误伤）", not time_ctx.in_quiet_hours(bad, at(3)))
    check("起止相同（8:00-8:00）视为零长度，不静默",
          not time_ctx.in_quiet_hours("8:00-8:00", at(8)))
    # 解析函数本身
    check("parse_window 返回分钟数", time_ctx.parse_window("1:00-7:00") == (60, 420))
    check("parse_window 对空串返回 None（不是 (0,0)）",
          time_ctx.parse_window("") is None)

    print("\n[2] 解析只有一份：proactive_talk 不再自带边界表")
    pt = read("plugins/proactive_talk/__init__.py")
    check("proactive_talk._in_quiet_hours 转发给 time_ctx",
          "from harness.time_ctx import in_quiet_hours" in pt)
    # 旧的"自己 split('-') 再算 cur >= start or cur < end"必须消失
    check("proactive_talk 里没有第二份跨天判断",
          "cur >= start or cur < end" not in pt)
    check("proactive_talk._period 不再自带小时表",
          "h < 5" not in pt and "return \"早上\"" not in pt)
    check("proactive_talk._period 走 time_ctx.period_of",
          "from harness.time_ctx import period_of" in pt)
    # 修之前这里有 7 个小时与 time_ctx 对不上，现在必须为 0
    import importlib
    ptmod = importlib.import_module("plugins.proactive_talk")
    diff = []
    for h in range(24):
        ts = at(h, 30)
        if ptmod._period(datetime.datetime.fromtimestamp(ts)) != time_ctx.period_of(ts)[0]:
            diff.append(h)
    check("24 小时里 _period 与 time_ctx 完全一致（原先是 7 处不一致）",
          not diff, diff)

    print("\n[3] 群聊回复链接了静默闸，且在 AI 调用之前")
    bc = read("bot_core.py")
    check("群聊处理函数里调用了 in_quiet_hours", "_iqh(_config.get(\"quiet_hours\"))" in bc)
    # 位置：必须在 process_buffered_message 里、且在第一次 call_deepseek 之前
    pb = bc.index("def process_buffered_message(")
    gate = bc.index("_iqh(_config.get(\"quiet_hours\"))", pb)
    first_ai = bc.index("call_deepseek(", pb)
    check("闸门在 process_buffered_message 内", pb < gate)
    check("闸门在第一次 call_deepseek 之前（不白花一次请求）", gate < first_ai)
    # 被点名 vs 没被点名的分流必须存在
    check("没被点名时直接 return",
          re.search(r"if not _mention:\s*\n\s*log\(.*?\)\s*\n\s*return", bc) is not None)
    check("quiet_when_mentioned=ignore 时也 return",
          "_mode_q == \"ignore\"" in bc)
    check("四个点名信号都算被叫到",
          all(k in bc[gate:first_ai] for k in
              ("is_at_bot", "has_xiao_a", "has_bot_nick", "has_text_at", "quote_me")))

    print("\n[4] 「你正在睡觉」只有一处话术")
    check("存在共用函数 _inject_sleep_state", "def _inject_sleep_state(" in bc)
    n_call = bc.count("_inject_sleep_state(dynamic_parts")
    # 定义行本身也含这个子串，所以是 1 定义 + 2 调用
    check("群聊与私聊各调一次（1 定义 + 2 调用）", n_call == 3, n_call)
    # 话术只允许出现在函数体内一次；docstring 里提到不算第二份实现
    body = bc.split("def _inject_sleep_state(")[1].split("\ndef ")[0]
    check("话术只在共用函数体里出现一次",
          body.count("【你正在睡觉】") == 1, body.count("【你正在睡觉】"))
    check("注入走 dynamic_parts（尾部），不进 static",
          "_inject_sleep_state(dynamic_parts" in bc
          and "_inject_sleep_state(stable_parts" not in bc)

    print("\n[5] 主配置登记（否则用户没有入口，功能等于不存在）")
    import config
    check("DEFAULT_CONFIG 有 quiet_hours", "quiet_hours" in config.DEFAULT_CONFIG)
    check("DEFAULT_CONFIG 有 quiet_when_mentioned",
          "quiet_when_mentioned" in config.DEFAULT_CONFIG)
    check("quiet_hours 默认空 = 不静默（不改老行为）",
          config.DEFAULT_CONFIG.get("quiet_hours") == "")
    check("quiet_when_mentioned 默认 wake",
          config.DEFAULT_CONFIG.get("quiet_when_mentioned") == "wake")
    ex = read("bot_config.json.example")
    check("example 登记了 quiet_hours", '"quiet_hours"' in ex)
    check("example 登记了 quiet_when_mentioned", '"quiet_when_mentioned"' in ex)
    v3 = read("webui/webui_artistic_v3.html")
    for fid in ("sQuietHours", "sQuietWhenMentioned"):
        # v3 每个字段必须三处一致：标记 / 填充 / 收集
        check(f"v3 {fid} 三处都有",
              f'id="{fid}"' in v3
              and v3.count(f"'{fid}'") >= 2, v3.count(f"'{fid}'"))
    # v4 那两个字段的断言在 test_webui_v4_pages.py 的端到端里 —— 那边要真起
    # 浏览器、验到"值进了 save 请求"，比在这里查字符串强。这里不重复。

    print("\n[6] proactive_talk 与主配置对齐（同一件事不能两套时段）")
    check("插件配置缺 quiet_hours 时继承主配置",
          "from config import load_config" in pt
          and "cfg[\"quiet_hours\"] = str(load_config().get(\"quiet_hours\")" in pt)

    print("\n[7] 在做什么：只跟时段走，不另开小时表")
    periods = time_ctx._PERIODS
    check("每个时段都有一句活动（4 元组）", all(len(r) == 4 for r in periods))
    check("活动全部非空", all(str(r[3]).strip() for r in periods))
    acts = [r[3] for r in periods]
    check("活动文案各不相同（否则等于没分时段）",
          len(set(acts)) == len(acts), acts)
    check("activity_of 与 period_of 取的是同一行",
          time_ctx.activity_of(at(3)) == periods[0][3]
          and time_ctx.activity_of(at(14)) == periods[4][3])
    # 不许出现"具体内容"式的编造（书名/剧名/具体人名）
    check("活动不编具体内容（无《》/无具体作品名）",
          not any(("《" in a or "》" in a) for a in acts))
    ct = time_ctx.coarse_text(at(14))
    check("coarse_text 里带上了活动", "你" in ct and periods[4][3] in ct, ct)
    # 活动文本必须落在 dynamic/coarse 这一段，不能进 static 黄金段
    check("活动不进 static_prompt（进的是 time_ctx 的 coarse 段）",
          "activity_of" not in read("bot_core.py").split("def _build_static_prompt")[1]
          .split("def ")[0])

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())