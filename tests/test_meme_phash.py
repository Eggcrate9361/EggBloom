# -*- coding: utf-8 -*-
"""tests/test_meme_phash.py —— 表情包"重复存"的防线

2026-11-01 用户问：表情包重复存有没有可能？
答：sha1 只认**字节**。同一张图经 QQ 转发/重新压缩/换格式后字节变了，
sha 就变了 —— 会再收一份，库里看着是两个一样的表情。

这里加的是**感知哈希**（dHash 8x8）去重。锁五件事：

  A. 同一张图换格式（jpg ↔ png）→ 判为重复
  B. 同一张图缩放后 → 判为重复
  C. 不同的图 → 不判重复（宁漏不误杀）
  D. 旧索引条目没有 phash → 不崩、不判重复（向后兼容）
  E. 阈值可配：0 时连"重新编码"都不算重复
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

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


def _pic(kind: str, n: int = 120) -> Image.Image:
    im = Image.new("RGB", (n, n), "white")
    d = ImageDraw.Draw(im)
    if kind == "face":          # 大圆 + 眼睛 + 嘴（表情包常见构图）
        d.ellipse([20, 20, 100, 100], fill=(255, 210, 120), outline="black", width=3)
        d.ellipse([40, 50, 55, 65], fill="black")
        d.ellipse([70, 50, 85, 65], fill="black")
        d.arc([40, 65, 85, 95], 10, 170, fill="black", width=3)
    elif kind == "noise":       # 完全不同的东西
        for i in range(0, n, 7):
            d.line([0, i, n, (i * 3) % n], fill=(i % 255, 90, 200), width=3)
    return im


def main():
    td = tempfile.mkdtemp(prefix="eb_phash_")
    face = _pic("face")
    p_jpg = os.path.join(td, "a.jpg")
    p_png = os.path.join(td, "a.png")
    p_small = os.path.join(td, "a_small.jpg")
    p_other = os.path.join(td, "b.jpg")
    face.save(p_jpg, quality=95)
    face.save(p_png)
    face.resize((60, 60)).save(p_small, quality=70)
    _pic("noise").save(p_other)

    print("[A] 同图换格式")
    h1, h2 = mc._dhash(p_jpg), mc._dhash(p_png)
    check("都算得出来", h1 is not None and h2 is not None)
    check("距离很小", mc._hamming(h1, h2) <= 4, mc._hamming(h1, h2))
    idx = {"k1": {"path": "collected/x.jpg", "phash": h1}}
    check("判为重复", mc.visual_dup_of(p_png, idx)[0] == "k1")

    print("\n[B] 缩放 + 降质量")
    check("判为重复", mc.visual_dup_of(p_small, idx)[0] == "k1")

    print("\n[C] 不同的图")
    check("不判重复", mc.visual_dup_of(p_other, idx)[0] is None,
          mc._hamming(mc._dhash(p_other), h1))

    print("\n[D] 旧索引（没有 phash 字段）")
    check("不崩且不判重复", mc.visual_dup_of(p_png, {"old": {"path": "old.jpg"}})[0] is None)
    check("脏数据不崩", mc.visual_dup_of(p_png, {"x": "not-a-dict", "y": {"phash": "abc"}})[0] is None)

    print("\n[E] 阈值可配")
    # 轻微模糊：dHash 只变 1~3 位。阈值 0 → 不算重复；阈值 4 → 算
    blurred = face.filter(ImageFilter.GaussianBlur(1.0))
    p_edit = os.path.join(td, "a_blur.png")
    blurred.save(p_edit)
    d_edit = mc._hamming(h1, mc._dhash(p_edit))
    check("模糊后距离 1~4", 1 <= d_edit <= 4, d_edit)
    check("阈值 0 时不判重复", mc.visual_dup_of(p_edit, idx, max_dist=0)[0] is None, d_edit)
    check("阈值 4 时判重复", mc.visual_dup_of(p_edit, idx, max_dist=4)[0] == "k1", d_edit)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
