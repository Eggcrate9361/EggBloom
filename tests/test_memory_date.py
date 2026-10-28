# -*- coding: utf-8 -*-
"""日期类记忆召回：问「昨天/一天前说过什么」要能查到（2026-10-27 真机）

## 事故

群 123456789，18:55。用户 @Bot 问「我一天前说过什么」，机器人答：

    记不住，我又不是录音机

## 根因（三层）

1. **`_resolve_date_targets` 不认「一天前」**
   它的表只有 `前天/昨天/今天` + 绝对日期。
   真人最常见的问法「N天前」「上周三」「前天晚上」全解析不出。

2. **群聊路径压根没接日期召回**
   `recall_by_date` 只接在私聊的 `get_memory_prompt_block` 上；
   群聊走 `harness/memory_context.build_block`（纯语义检索）。
   语义检索对"我一天前说过什么"是瞎的 —— 向量跟任何具体内容都不像。

3. **语义召回低于阈值 → 空**
   于是召回为空，模型只能承认"记不住"。

## 契约

1. 口语相对时间能解析：N天前 / 昨天 / 前天 / 大前天 / 上周X / 昨天和前天（多天）
2. 那天没记录 → **明确说没有**（不是"记不住"）
3. 那天有记录 → 把内容摘出来
4. 群聊路径也要能召回（build_block 走日期通道）
5. 「上周三」只解析成一天（不能同时命中"上周"的粗粒度 7 天前）

运行：python tests/test_memory_date.py
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


import datetime  # noqa: E402


def _today():
    return datetime.date.today()


def _d(offset):
    return (_today() - datetime.timedelta(days=offset)).isoformat()


def test_relative_expressions():
    """口语相对时间要能解析（原来只认 昨天/前天/今天）。"""
    import sandglass_bridge as sb
    check("N天前（中文数字）", sb._resolve_date_targets("我一天前说过什么") == [_d(1)],
          sb._resolve_date_targets("我一天前说过什么"))
    check("N天前（阿拉伯数字）", sb._resolve_date_targets("3天前呢") == [_d(3)],
          sb._resolve_date_targets("3天前呢"))
    check("昨天", sb._resolve_date_targets("昨天说了啥") == [_d(1)])
    check("前天", sb._resolve_date_targets("前天说的") == [_d(2)])
    check("今天", sb._resolve_date_targets("今天呢") == [_d(0)])
    check("大前天（不被前天多带一天）",
          sb._resolve_date_targets("大前天") == [_d(3)],
          sb._resolve_date_targets("大前天"))
    check("前天晚上", sb._resolve_date_targets("前天晚上说了啥") == [_d(2)],
          sb._resolve_date_targets("前天晚上说了啥"))


def test_multiple_days():
    """「昨天和前天」要查两天，不能只取第一个。"""
    import sandglass_bridge as sb
    got = sb._resolve_date_targets("昨天和前天都说了啥")
    check("多天都解析到", set(got) == {_d(1), _d(2)}, got)


def test_weekday():
    """「上周三」只解析成一天 —— 不能同时命中「上周」的粗粒度。"""
    import sandglass_bridge as sb
    got = sb._resolve_date_targets("上周三聊了什么")
    check("上周三只解析成一天", len(got) == 1, got)
    # 算出来的那天确实是上周三
    _wd = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    _dt = datetime.date.fromisoformat(got[0])
    check("那天是周三", _dt.weekday() == _wd["三"], (_dt, _dt.weekday()))
    check("在上周（6~13 天前）",
          6 <= (_today() - _dt).days <= 13, (_today() - _dt).days)


def test_absolute_date():
    import sandglass_bridge as sb
    check("绝对日期", sb._resolve_date_targets("10月25号说了什么") == ["2026-10-25"]
          or sb._resolve_date_targets("10月25号说了什么") == [_d(2)],
          sb._resolve_date_targets("10月25号说了什么"))
    check("无日期表达 → 空", sb._resolve_date_targets("随便聊聊") == [])


def test_recall_says_no_record():
    """★ 没有记录时必须**明确说没有**，而不是让模型自己说"记不住"。"""
    import sandglass_bridge as sb
    txt = sb.recall_by_date("我一天前说过什么")
    check("有输出（不是空）", bool(txt), repr(txt)[:80])
    check("写明没有记录", "没有" in txt and "记录" in txt, txt[:120])
    check("明确要求如实说", "如实" in txt or "不要编" in txt, txt[:120])


def test_group_path_can_recall():
    """★ 群聊路径也要能召回（原来只有私聊接了日期通道）。"""
    import harness.memory_context as mc
    import inspect
    src = inspect.getsource(mc.build_block)
    check("build_block 调了 recall_by_date", "recall_by_date" in src,
          src[:200])
    # 实际调用：问一个昨天有数据的日期（沙漏里 10-25 有 74 条）
    blk = mc.build_block("2026-10-25 说了什么", scope="group",
                         scope_id="123456789", sources=("sand",))
    check("群聊能召回那天的记录", bool(blk) and ("10-25" in blk or "记录" in blk),
          repr(blk)[:160])


def test_no_record_still_returns_block():
    """没有记录时也要产出块（让模型知道"查过了，确实没有"）。"""
    import harness.memory_context as mc
    blk = mc.build_block("我一天前说过什么", scope="group",
                         scope_id="123456789", sources=("sand",))
    check("无记录也产出块（明确告知）", bool(blk), repr(blk)[:120])
    check("块里写明没有", "没有" in blk, repr(blk)[:160])


def test_wired():
    import io
    src = io.open(os.path.join(ROOT, "sandglass_bridge.py"), encoding="utf-8").read()
    check("中文数字支持", "一" in src and "_CN_NUM" in src)
    check("多天收集", "out[:3]" in src)
    mb = io.open(os.path.join(ROOT, "harness", "memory_context.py"),
                 encoding="utf-8").read()
    check("群聊接日期召回", "recall_by_date" in mb)


def main():
    print("=" * 58)
    print("日期类记忆召回")
    print("=" * 58)
    for fn in (test_relative_expressions,
               test_multiple_days,
               test_weekday,
               test_absolute_date,
               test_recall_says_no_record,
               test_group_path_can_recall,
               test_no_record_still_returns_block,
               test_wired):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()