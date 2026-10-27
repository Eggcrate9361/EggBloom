# -*- coding: utf-8 -*-
"""丢弃判据：按话头翻篇，不按生成耗时（2026-10-27 · 1.5.9 §B5）

## 为什么

旧判据是「生成超过 25 秒就丢」。这个数是按当时模型 11–26 秒的生成速度
反推的：换快模型就过严（发出陈年回复），换慢模型就误杀。

真正的判据是**话头有没有走**：生成期间群里又来了几条，比"花了几秒"更
接近事实。时间只留一个很宽的兜底（120 秒），防"生成卡死十分钟"。

## 契约

1. `new_msgs >= turns` → 丢，原因写「话头翻篇 N 条新消息」
2. 1 条新消息不丢 —— 还在同一话头里
3. 时间不是主判据：30 秒没新消息，照发
4. 只有超过 hard_sec（默认 120 秒）才按过时丢
5. 官腔与时间无关，命中即丢
6. turns=0 表示关掉这条判据

运行：python tests/test_reply_drop.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


def _why(**kw):
    import bot_core
    return bot_core._drop_reason(**kw)


def test_turns_is_primary():
    check("2 条新消息 → 丢", _why(new_msgs=2, age=5).startswith("话头翻篇"), _why(new_msgs=2, age=5))
    check("1 条新消息 → 不丢", _why(new_msgs=1, age=30) == "", _why(new_msgs=1, age=30))
    check("0 条新消息 → 不丢（哪怕 30 秒）", _why(new_msgs=0, age=30) == "", _why(new_msgs=0, age=30))
    check("原因里带条数", "3 条" in _why(new_msgs=3, age=1), _why(new_msgs=3, age=1))


def test_time_is_backstop():
    check("超过 120 秒兜底丢", _why(new_msgs=0, age=200).startswith("过时"), _why(new_msgs=0, age=200))
    check("119 秒不丢", _why(new_msgs=0, age=119) == "", _why(new_msgs=0, age=119))


def test_official():
    check("官腔命中即丢", _why(new_msgs=0, age=1, text="作为AI，我建议你早点睡") == "官腔",
          _why(new_msgs=0, age=1, text="作为AI，我建议你早点睡"))
    check("正常短句不误伤", _why(new_msgs=0, age=1, text="那你就是待炒的那颗") == "",
          _why(new_msgs=0, age=1, text="那你就是待炒的那颗"))


def test_switch_off():
    check("turns=0 关掉话头判据", _why(new_msgs=5, age=1, turns=0) == "",
          _why(new_msgs=5, age=1, turns=0))


def test_defaults_from_config():
    import bot_core
    check("默认 2 条算翻篇", int(bot_core._config.get("reply_stale_turns", 2)) == 2,
          bot_core._config.get("reply_stale_turns"))
    check("时间兜底默认 120 秒", float(bot_core._config.get("reply_stale_drop_sec", 120)) == 120.0,
          bot_core._config.get("reply_stale_drop_sec"))


def test_wired():
    import io
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("pipeline 用统一判据", "_why = _drop_reason(" in src)
    check("数的是时间线上的新消息", "_e[0] > _seq_first" in src)
    check("日志能检索到翻篇原因", "[丢弃:{_why}]" in src)


def main():
    print("=" * 58)
    print("丢弃判据（话头翻篇）")
    print("=" * 58)
    for fn in (test_turns_is_primary, test_time_is_backstop, test_official,
               test_switch_off, test_defaults_from_config, test_wired):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
