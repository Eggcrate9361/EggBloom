# -*- coding: utf-8 -*-
"""情绪兴奋度衰减参数必须**运行时可改**（1.5.9 §C.1 / #4，2026-10-28）。

核查结论：`arousal_decay_seconds` 只在 `_decay_arousal` 里硬编码读默认值，
不在 mood_engine 自己的 `DEFAULT_CONFIG` 里；而 `set_config` 是按
`if k in DEFAULT_CONFIG` 过滤的 → 这个键**写不进去**，恒为 900 秒。

这个文件钉住：
1. 键在 DEFAULT_CONFIG 里（否则 set_config 会静默丢弃）。
2. `set_config` 真能改到它，并且改完 `_decay_arousal` 的行为跟着变。
3. 关闭（<=0）时不衰减 —— 与旧兜底分支一致，不能因为加了键就改语义。

★ 不写生产 mood.json：整个用例把 MOOD_PATH 指到临时文件，结束时只还原内存值。
运行：python tests/test_mood_decay_config.py
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import plugins.ego.mood_engine as me  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def main():
    print("\n[1] 键在 DEFAULT_CONFIG 里（否则 set_config 会静默丢掉）")
    check("arousal_decay_seconds 在默认表里",
          "arousal_decay_seconds" in me.DEFAULT_CONFIG,
          list(me.DEFAULT_CONFIG))

    tmpdir = tempfile.mkdtemp(prefix="eggbloom_mood_")
    old_path = me.MOOD_PATH
    old_val = me._mood.config.get("arousal_decay_seconds")
    me.MOOD_PATH = os.path.join(tmpdir, "mood.json")
    try:
        print("\n[2] set_config 能改到它")
        me.set_config({"arousal_decay_seconds": 60})
        check("★ 运行时读回 60", me.get_config().get("arousal_decay_seconds") == 60,
              me.get_config().get("arousal_decay_seconds"))

        print("\n[3] 衰减行为跟着参数走")
        m = {"arousal": 0.9, "updated": time.time() - 30}
        fast = me._mood._decay_arousal(dict(m))
        # 值域改 −1~1 之后衰减是"向 0 按比例回落"，不再减到 0.3 的旧中性线：
        # 0.9 × (1 − 30/60) = 0.45
        check("60 秒半衰 → 30 秒后约 0.45", abs(fast - 0.45) < 0.03, fast)
        me.set_config({"arousal_decay_seconds": 900})
        slow = me._mood._decay_arousal(dict(m))
        check("900 秒 → 30 秒后仍约 0.87", abs(slow - 0.87) < 0.03, slow)
        check("★ 慢衰减比快衰减更接近原值", slow > fast, (slow, fast))

        print("\n[4] <=0 = 不衰减（旧兜底语义不变）")
        me.set_config({"arousal_decay_seconds": 0})
        raw = me._mood._decay_arousal(dict(m))
        check("原样返回 0.9", abs(raw - 0.9) < 1e-6, raw)
    finally:
        me.MOOD_PATH = old_path
        if old_val is None:
            me._mood.config.pop("arousal_decay_seconds", None)
        else:
            me._mood.config["arousal_decay_seconds"] = old_val

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
