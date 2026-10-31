# -*- coding: utf-8 -*-
"""tests/test_meme_skip_reason.py —— “存不进去”要说出为什么

2026-10-31 真机：用户连说三次「存表情包」，模型只能反复回"存不进去"
—— 因为 `meme_collect.maybe_collect` 是全静默的（跳过一律返回空串）。

`skip_reason()` 是同一套判据的人话版，工具层用它回话。锁五件事：

  A. 路径不存在 → 图没落地
  B. 格式不收 → 说清扩展名
  C. 太小 / 太大 → 给具体数字
  D. 已经在库里 → 这张已经存过了
  E. 都通过 → 空串（可以收）
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import meme_collect as mc  # noqa: E402

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


def main():
    td = tempfile.mkdtemp(prefix="eb_skip_")

    def w(name, data):
        p = os.path.join(td, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    print("[A] 路径不存在")
    r = mc.skip_reason(os.path.join(td, "nope.jpg"))
    check("说清了", "找不到" in r or "没落地" in r, r)

    print("\n[B] 格式不收")
    r = mc.skip_reason(w("x.txt", b"z" * 5000))
    check("提到扩展名", ".txt" in r, r)

    print("\n[C] 大小")
    r = mc.skip_reason(w("tiny.jpg", b"x" * 100))
    check("太小有数字", "太小" in r and "100" in r, r)
    big = w("big.jpg", b"x" * (mc.MAX_BYTES + 1024))
    r = mc.skip_reason(big)
    check("太大有 MB", "太大" in r and "MB" in r, r)

    print("\n[D] 已在库里（把索引打桩）")
    ok = w("ok.jpg", b"y" * 5000)
    import hashlib
    sha = hashlib.sha1(open(ok, "rb").read(mc.MAX_BYTES + 1)).hexdigest()
    orig = mc._index
    try:
        mc._index = lambda: {sha: {"path": "collected/abc.jpg", "ts": 0}}
        r = mc.skip_reason(ok)
        check("说已经存过", "存过" in r, r)
    finally:
        mc._index = orig

    print("\n[E] 正常图")
    check("空串 = 可以收", mc.skip_reason(ok) == "", mc.skip_reason(ok))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
