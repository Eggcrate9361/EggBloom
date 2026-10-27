# -*- coding: utf-8 -*-
"""入站归一与指纹判重（2026-10-27 · 1.5.9 §B0）

## 为什么

同一条消息可能带五种形态（原文 / 引用预览 / 图片描述 / `[图片]` 占位 / @ 昵称），
又被存进三处。模型看到字面不同的 2–3 份，当成多条新消息，于是回两次。
现存三道判重都按 mid 或字面相似度工作，挡不住"换形态的同一内容"。

## 契约

1. `normalize_text` 只留内容：去占位符、标点、空白，全角转半角，英文小写
2. `fingerprint` 同人 + 2-gram 集合；换标点/空白/加表情都不改指纹
3. 纯表情 / 纯图（归一后为空）返回空指纹，**放行** —— 对空内容判重会把
   所有「[图片]」当成同一条
4. `DupeGuard` 窗口内同人同指纹判重；窗口外放行
5. 登记表有上限，淘汰只按时间丢最旧的一半，不清空

运行：python tests/test_inbound.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import inbound

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


def test_normalize():
    cases = {
        "[图片] 你吃了吗": "你吃了吗",
        "你吃了吗？": "你吃了吗",
        "你吃了吗": "你吃了吗",
        "  你 吃 了 吗  ": "你吃了吗",
        "[表情]": "",
        "［全角］ＡＢＣ": "［全角］abc",
        "[CQ:at,qq=123] 你好": "你好",
    }
    for src, want in cases.items():
        got = inbound.normalize_text(src)
        check(f"归一 {src!r}", got == want, f"{got!r} != {want!r}")


def test_fingerprint_stable():
    a = inbound.fingerprint("10001", "你吃了吗")
    b = inbound.fingerprint("10001", "你吃了吗？")
    c = inbound.fingerprint("10001", "[图片] 你吃了吗")
    check("标点差异同指纹", a == b, (a, b))
    check("占位符差异同指纹", a == c, (a, c))
    check("不同人不同指纹", a != inbound.fingerprint("10002", "你吃了吗"))
    check("不同内容不同指纹", a != inbound.fingerprint("10001", "你睡了吗"))
    check("纯图返回空指纹（放行）", inbound.fingerprint("10001", "[图片]") == "",
          inbound.fingerprint("10001", "[图片]"))
    check("单字也有指纹", inbound.fingerprint("10001", "草") != "")


def test_dupe_guard():
    g = inbound.DupeGuard(window=30.0)
    t0 = 1_000_000.0
    check("首次不命中", not g.hit("g1", "10001", "你吃了吗", now=t0))
    check("窗口内同内容命中", g.hit("g1", "10001", "你吃了吗？", now=t0 + 5))
    check("窗口外放行", not g.hit("g1", "10001", "你吃了吗", now=t0 + 31))
    check("别人说同一句不命中", not g.hit("g1", "10002", "你吃了吗", now=t0 + 6))
    check("别的群不命中", not g.hit("g2", "10001", "你吃了吗", now=t0 + 7))
    check("纯图不占用判重", not g.hit("g1", "10001", "[图片]", now=t0 + 8))
    check("纯图连续两次都放行",
          not g.hit("g1", "10001", "[图片]", now=t0 + 9))


def test_eviction():
    g = inbound.DupeGuard(window=30.0, max_keys=10)
    t0 = 2_000_000.0
    for i in range(12):
        g.hit("g", "u", f"消息内容{i}", now=t0 + i)
    check("登记表不超上限", g.size() <= 10, g.size())
    # 淘汰只丢最旧的一半，最近登记的仍在 → 再发同一条应命中
    check("最近登记的仍记得", g.hit("g", "u", "消息内容11", now=t0 + 12))


def test_clear():
    g = inbound.DupeGuard()
    g.hit("g", "u", "别走")
    g.clear()
    check("clear 后可再次命中为 False", not g.hit("g", "u", "别走"))


def test_wired_in_bot_core():
    import io
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("入口做了指纹判重", "_INBOUND_DUPE.hit" in src)
    check("判重日志可检索", "[重复:指纹]" in src)
    check("用去注记原文判重", "_INBOUND_DUPE.hit(group_id, sender, oc)" in src)
    # 2026-10-27 真机：窗口 30 秒把「我是鸡蛋」隔 3 秒说第二遍当成了重复投递。
    # 判据要挡"同一条事件被投递两次"，不是"同一句话被说了两次"。
    check("窗口不超过 6 秒（30 秒会误伤真人重复发言）",
          "DupeGuard(window=6.0)" in src, "窗口太长")


def main():
    print("=" * 58)
    print("入站归一与指纹判重（B0）")
    print("=" * 58)
    for fn in (test_normalize, test_fingerprint_stable, test_dupe_guard,
               test_eviction, test_clear, test_wired_in_bot_core):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
