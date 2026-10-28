# -*- coding: utf-8 -*-
"""被 @ 的问题整批被丢：批次里的 @别人 不该让 Bot 闭嘴（2026-10-27 真机）

## 真机现象

    18:46:16 群[123456789] 文本: '@AAA无理由退货  讲解一下精神分析理论'
    18:46:19 [决策放行] 触发: [18:45] [外莱塔尼亚·6acy] [引用:(/ω＼)｜引用我，必回] …
    18:46:19 跳过定向他人的消息: [当前] @(/ω＼)  这是你的求救信号吗

用户明确 @ 了 Bot 并提问，**整批却被丢掉**。

## 根因

逐行检测对每一行都判「是不是在跟别人说话」，但没区分两种行：
- 回抓的**历史**里的 @别人 —— 那是别人之间的事，与 Bot 无关
- 用户**当前**这句里的 @别人 —— 用户引用了别人的话再问 Bot，
  这是在跟 Bot 说话

## 契约

1. 回抓的历史行（行首 `[HH:MM]`）不判定向他人
2. 当前行带 `[当前]` 且整批有"在叫我"信号 → 不判定向他人
3. 没有在叫我信号时判据照旧（群友互聊该跳过）

运行：python tests/test_directed_others.py
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


# ── ② 定向误判 ──────────────────────────────────────────────────────

def test_directed_to_others():
    import bot_core as bc
    # 回抓的历史行：不该判
    hist = "[18:45] [外莱塔尼亚·6acy] [引用:(/ω＼)｜引用我，必回] @AAA无理由退货  限定是谁"
    check("回抓的历史行不判定向他人",
          not bc._line_is_directed_to_others(hist, addressed=False), hist[:40])
    # 当前这句，被点名时：不判
    cur = "[当前] @(/ω＼)  这是你的求救信号吗"
    check("被点名 → 当前行的 @别人 不丢",
          not bc._line_is_directed_to_others(cur, addressed=True), cur)
    # 没被点名时：照旧判（群友互聊该跳过）
    check("没被点名 → 仍判定向他人",
          bc._line_is_directed_to_others(cur, addressed=False), cur)


def test_batch_addressed_wired():
    import io
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("批内触发信号被算出来", "_batch_addressed" in src)
    check("逐行检测用新函数", "_line_is_directed_to_others(line, addressed=_batch_addressed)" in src)


def main():
    print("=" * 58)
    print("定向他人误判")
    print("=" * 58)
    for fn in (test_directed_to_others,
               test_batch_addressed_wired):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
