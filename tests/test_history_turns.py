# -*- coding: utf-8 -*-
"""历史原文轮数：硬编码 4 → 可配，默认 2（1.5.9 §C.3.5）

## 现状（改之前）

`bot_core._build_cached_messages()` 里 `RECENT_TURNS = 4` 写死，
配置键 `history_recent_turns` 根本不存在。

单位说明：history 是 `[user, assistant, user, assistant…]`，
**一轮 = 两条**，所以 `_RECENT_ENTRIES = RECENT_TURNS * 2`。

## 契约

1. `history_recent_turns` 在 DEFAULT_CONFIG 里，默认 2
2. 轮数可配：配 N 就注入 N 轮原文（2N 条）
3. 配 0 = 全部压成摘要（不能被当成"没配"）
4. 配置缺失/非法时回落到 2
5. 越界值被夹住（0~8）

## 两个必须踩过才知道的坑

- **`0 or 2` == 2**：配 0 时 0 是 falsy → "配了就等于没配"
- **`history[:-0]` == `history[:0]`**：N=0 时切片不是"去掉最后 0 个"

运行：python tests/test_history_turns.py
"""
import io
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


def _hist(n=8):
    h = []
    for i in range(n):
        h.append({"role": "user", "content": f"第{i}轮用户说的", "ts": 1700000000 + i})
        h.append({"role": "assistant", "content": f"第{i}轮机器人回的", "ts": 1700000000 + i})
    return h


def _build(bc, hist):
    return bc._build_cached_messages(
        history=hist, cache_key="k", static_prompt="S",
        dynamic_parts=["D"], user_msg="现在这条", stable_parts=["ST"])


def _count_raw(msgs):
    """数注入的历史原文条数（不含当前消息）。"""
    return len([m for m in msgs
                if m["role"] in ("user", "assistant") and m["content"] != "现在这条"])


def test_config_exists():
    import config
    check("配置键存在", "history_recent_turns" in config.DEFAULT_CONFIG,
          sorted(config.DEFAULT_CONFIG)[:5])
    check("默认值是 2", config.DEFAULT_CONFIG.get("history_recent_turns") == 2,
          config.DEFAULT_CONFIG.get("history_recent_turns"))


def test_example_has_it():
    import json
    p = os.path.join(ROOT, "bot_config.json.example")
    d = json.load(io.open(p, encoding="utf-8-sig"))
    check("example 里有这个键", "history_recent_turns" in d,
          [k for k in d if "history" in k])


def test_default_is_two_turns():
    import bot_core as bc
    bc._config["history_recent_turns"] = 2
    msgs = _build(bc, _hist())
    check("默认注入 2 轮（4 条）", _count_raw(msgs) == 4, _count_raw(msgs))


def test_configurable():
    import bot_core as bc
    for turns, want in ((1, 2), (2, 4), (4, 8), (6, 12)):
        bc._config["history_recent_turns"] = turns
        n = _count_raw(_build(bc, _hist(10)))
        check(f"配 {turns} 轮 → {want} 条原文", n == want, n)


def test_zero_means_zero():
    """★ 配 0 = 全部压成摘要。

    两个坑：
      · `0 or 2` → 2（0 是 falsy）
      · `history[:-0]` → `history[:0]`（不是"去掉最后 0 个"）
    """
    import bot_core as bc
    bc._config["history_recent_turns"] = 0
    n = _count_raw(_build(bc, _hist()))
    check("配 0 → 0 条原文（不是回落到默认）", n == 0, n)


def test_missing_and_invalid():
    import bot_core as bc
    bc._config.pop("history_recent_turns", None)
    n = _count_raw(_build(bc, _hist()))
    check("缺配置 → 回落 2 轮", n == 4, n)

    bc._config["history_recent_turns"] = None
    n = _count_raw(_build(bc, _hist()))
    check("显式 None → 回落 2 轮", n == 4, n)

    bc._config["history_recent_turns"] = "不是数字"
    n = _count_raw(_build(bc, _hist()))
    check("非法值 → 回落 2 轮", n == 4, n)


def test_clamped():
    import bot_core as bc
    bc._config["history_recent_turns"] = 99
    n = _count_raw(_build(bc, _hist(20)))
    check("越界值被夹住（≤8 轮）", n <= 16, n)
    bc._config["history_recent_turns"] = -5
    n = _count_raw(_build(bc, _hist()))
    check("负值被夹到 0", n == 0, n)


def test_no_hardcoded_four():
    """代码里不该再有写死的 4 轮。"""
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    i = src.find("def _build_cached_messages")
    seg = src[i:i + 4000]
    check("没有写死的 RECENT_TURNS = 4",
          "RECENT_TURNS = 4" not in seg,
          [ln.strip() for ln in seg.split("\n") if "RECENT_TURNS =" in ln][:3])
    check("读的是配置", 'history_recent_turns' in seg,
          "没读配置")
    check("没有用[:-N] 切历史", "[:-_RECENT_ENTRIES]" not in seg,
          "仍有负索引切片（N=0 时会变空）")


def main():
    print("=" * 58)
    print("历史原文轮数")
    print("=" * 58)
    for fn in (test_config_exists,
               test_example_has_it,
               test_default_is_two_turns,
               test_configurable,
               test_zero_means_zero,
               test_missing_and_invalid,
               test_clamped,
               test_no_hardcoded_four):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()