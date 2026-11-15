# -*- coding: utf-8 -*-
"""搜索结果年份对不上 → 强制换词重搜（2026-10-27 真机事故）

## 事故

群 123456789，18:13。用户问「今年明日方舟周年半周年限定是谁」。

```
18:13:04 工具调用: ['search_web']
18:13:06 搜索「明日方舟 2026 半周年 限定 干员」→ 自家搜索命中
         1. [2025-06-18 · 9game.cn] 明日方舟:限定干员大揭秘…
18:13:20 群回复: 十一月的还没宣 / 再蹲两周呗
```

而 11 月的消息**就在搜索结果里**（另一轮搜到的）：
「[限定寻访·庆典]【以风雪为誓】限时寻访即将开启 活动时间:11月01日…」。

## 三个叠加原因

1. 模型把检索词里的「今年」改写掉了 → `wants_recency` 变 False →
   时效加权整个失效 → 2025 年旧闻压过 2026 年新消息
2. 「问 2026、结果全是 2025」这件事**没有任何机制指出** ——
   搜到了就算成功，模型拿着旧闻当权威答案
3. 模型搜 1 轮就下结论，主链没有「结果不够 → 换词再搜」的强制路径

第 1 条已在 search_core 修（年份数字触发时效加权）；
本文件固定第 2、3 条。

## 契约

1. 问句有年份、结果日期年份**完全不交集** → 判不符
2. 只要有一条结果年份对得上 → 不算不符（那可能就是答案）
3. 问句没年份 → 永��判不符（不冤枉 timeless 内容）
4. 结果里没有日期 → 不判不符（无从判断，不冤枉）
5. 判不符时主链必须强制换词重搜，而不是直接收尾

运行：python tests/test_search_year.py
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


def _mismatch(query, text):
    import bot_core
    return bot_core._search_year_mismatch(query, text)


#: 18:13 真机那批结果（全是 2025）
REAL_2025 = ("【明日方舟 2026 半周年 限定 干员】搜索结果："
             " 1. [2025-06-18 · 9game.cn] 明日方舟:限定干员大揭秘,稀有角色一览"
             " 2. [2025-07-16 · 9game.cn] 【明日方舟】半周年限定干员大揭秘")

#: 17:55 那批（含 2026-10-17 的感谢庆典预测）
REAL_MIXED = ("【明日方舟 2026 感谢庆典 限定干员 是谁】搜索结果："
              " 1. [2026-10-17 · gdlingyun.cn] 明日方舟感谢庆典限定干员预测"
              " 2. [2025-04-27 · qieyou.com] 明日方舟六周年限定是谁")

#: 那条 11 月消息所在的结果（zhihu，无日期前缀）
REAL_NOV = ("【明日方舟 将雪投火 限定寻访 干员】搜索结果："
            " 1. [zhihu.com] 明日方舟 的想法: [限定寻访·庆典]【以风雪为誓】"
            "限时寻访即将开启活动时间:11月01日版本更新后 - 11月15日 03:59")


def test_real_2025_mismatch():
    """★ 真机那批：问 2026、结果全 2025 → 必须判不符。"""
    got = _mismatch("明日方舟 2026 半周年 限定 干员", REAL_2025)
    check("真机 2025 批次判年份不符", got == "2025", got)


def test_mixed_batch_ok():
    """有一条 2026 → 不算不符（那可能就是答案）。"""
    got = _mismatch("明日方舟 2026 感谢庆典 限定干员 是谁", REAL_MIXED)
    check("混合批次不算不符", got is None, got)


def test_no_year_in_query():
    """问句没年份 → 永不判不符。"""
    got = _mismatch("弗洛伊德 精神分析引论", REAL_2025)
    check("问句无年份 → 不判不符", got is None, got)


def test_no_date_in_result():
    """结果里没有日期 → 不判不符（无从判断，不冤枉）。"""
    got = _mismatch("明日方舟 2026 半周年 限定 干员", REAL_NOV)
    check("结果无日期 → 不判不符", got is None, got)


def test_empty_inputs_safe():
    check("空 query 安全", _mismatch("", REAL_2025) is None)
    check("空结果安全", _mismatch("明日方舟 2026", "") is None)
    check("None 安全", _mismatch("明日方舟 2026", None) is None)


def test_year_in_text_not_bracket_not_counted():
    """只有 `[YYYY-MM-DD · site]` 这种**日期标记**才作数。

    正文里随便出现的年份（比如「2025 年的活动」）不能算 ——
    否则正文提一句旧年份就误判。
    """
    text = "1. [zhihu.com] 明日方舟 2025 年的活动回顾，2026 年的还没出"
    got = _mismatch("明日方舟 2026 半周年 限定 干员", text)
    check("正文里的年份不算结果日期", got is None, got)


def test_wired_force_research():
    """主链必须强制换词重搜，而不是直接收尾。"""
    import io
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("主链用了年份检测", "_search_year_mismatch(" in src)
    check("有强制重搜分支", "_stale_hit and web_rounds < _max_plain_rounds" in src)
    check("重搜提示在上下文里", "【必须重搜】" in src)
    check("提示说清年份不对", "年份不对" in src)
    check("日志可检索", "[搜索年份不符]" in src)


def test_search_core_year_signal():
    """年份数字必须触发时效加权（模型改写掉「今年」也不失效）。"""
    import io
    import search_core as sc
    check("年份触发时效", sc.wants_recency("明日方舟 2026 半周年 限定 干员"))
    check("无年份不触发", not sc.wants_recency("明日方舟 半周年 限定 干员"))
    check("wants_recency 只有一个定义",
          io.open(os.path.join(ROOT, "search_core.py"), encoding="utf-8")
          .read().count("def wants_recency(") == 1)


def main():
    print("=" * 58)
    print("搜索年份判不上 → 强制换词重搜")
    print("=" * 58)
    for fn in (test_real_2025_mismatch,
               test_mixed_batch_ok,
               test_no_year_in_query,
               test_no_date_in_result,
               test_empty_inputs_safe,
               test_year_in_text_not_bracket_not_counted,
               test_wired_force_research,
               test_search_core_year_signal):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()