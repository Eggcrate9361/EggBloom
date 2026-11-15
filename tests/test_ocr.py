# -*- coding: utf-8 -*-
"""tests/test_ocr.py —— 本地 OCR 后端契约（2026-11-06）。"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

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
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


def main():
    from harness import ocr

    td = tempfile.mkdtemp(prefix="eb-ocr-")
    img = os.path.join(td, "x.png")
    open(img, "wb").write(b"not-a-real-png-but-path-exists")

    class Fake:
        def __call__(self, path):
            return ([[[[0, 0], [10, 0], [10, 10], [0, 10]], "第一行", 0.99],
                     [[[0, 20], [10, 20], [10, 30], [0, 30]], "第二行", 0.88]], 0.1)

    r = ocr.ocr_image(img, engine=Fake())
    check("OCR 成功", r.get("ok") is True, r)
    check("OCR 两行", r.get("text") == "第一行\n第二行", r)
    check("OCR 带框", len(r.get("boxes") or []) == 2, r)

    r2 = ocr.ocr_image(os.path.join(td, "no.png"))
    check("缺失图片拒绝", r2.get("ok") is False and "不存在" in r2.get("error", ""), r2)

    class Empty:
        def __call__(self, path):
            return []

    r3 = ocr.ocr_image(img, engine=Empty())
    check("无文字不算失败", r3.get("ok") is True and r3.get("text") == "", r3)

    def vision(path, prompt):
        return "视觉读到的字"

    r4 = ocr.ocr_or_vision(img, vision_fn=vision)
    check("视觉兜底", r4.get("ok") is True and r4.get("source") == "vision", r4)

    print("=" * 58)
    if FAIL:
        print(f"失败 {FAIL} 条：")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print(f"通过 {PASS} / 失败 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
