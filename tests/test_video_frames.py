# -*- coding: utf-8 -*-
"""harness/video_frames 单测（抽帧看视频，2026-10-30）。

重点：**没有 ffmpeg 也不能崩，更不能猜**。所以这里自己造一个 10 帧的 GIF
来验"动图路径"，再用一个假的 .mp4 验"没 ffmpeg 时如实失败"。

运行：python tests/test_video_frames.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402

from harness import video_frames as vf  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


_TMP = tempfile.mkdtemp(prefix="vframes_")


def make_gif(path, frames=10, size=(320, 240)):
    imgs = []
    for i in range(frames):
        im = Image.new("RGB", size, (i * 20 % 255, 40, 60))
        imgs.append(im)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=80, loop=0)
    return path


try:
    # ── 1. 编码器探测：找不到也不能抛 ───────────────────────────
    ff = vf.find_ffmpeg()
    check("find_ffmpeg 返回 str（找不到就是空串）", isinstance(ff, str), repr(ff))

    gif = make_gif(os.path.join(_TMP, "a.gif"))
    check("合成 GIF 是多帧", vf.is_animation(gif) is True)
    check("普通 JPEG 不是多帧", (lambda p: (
        Image.new("RGB", (10, 10)).save(p), vf.is_animation(p) is False)[1])(
        os.path.join(_TMP, "b.jpg")))

    # ── 2. 从动图抽帧（不需要 ffmpeg）─────────────────────────
    frames = vf.frames_from_animation(gif, 4)
    check("抽到 4 帧", len(frames) == 4, len(frames))
    check("帧是 RGB 图", all(f.mode == "RGB" for f in frames))
    check("首尾帧不一样（真的在变）", frames[0].getpixel((5, 5)) != frames[-1].getpixel((5, 5)))
    check("要的帧数多于总帧时不重复取",
          len(vf.frames_from_animation(gif, 99)) == 10,
          len(vf.frames_from_animation(gif, 99)))
    check("要 1 帧时取中段", len(vf.frames_from_animation(gif, 1)) == 1)

    # ── 3. 联系表 ─────────────────────────────────────────────
    sheet_path = os.path.join(_TMP, "sheet.jpg")
    got = vf.contact_sheet(frames, sheet_path, cols=2, size=640)
    check("拼贴成功落盘", got == sheet_path and os.path.isfile(sheet_path), got)
    with Image.open(sheet_path) as s:
        check("拼贴是 2×2 方图", s.size == (640, 640), s.size)
    check("空帧列表 → 返回空串", vf.contact_sheet([], sheet_path) == "")

    # ── 4. video_sheet 走动图路径 ─────────────────────────────
    r = vf.video_sheet(gif, os.path.join(_TMP, "out1.jpg"), n=4, size=512)
    check("video_sheet ok", r["ok"] is True, r)
    check("标了 animation 来源", r["source"] == "animation", r["source"])
    check("报出帧数", r["frames"] == 4, r["frames"])
    check("联系表存在", os.path.isfile(r["sheet"]))

    # ── 5. 真视频：没 ffmpeg 时必须如实失败，不许猜 ────────────
    fake_mp4 = os.path.join(_TMP, "fake.mp4")
    with open(fake_mp4, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    r2 = vf.video_sheet(fake_mp4, os.path.join(_TMP, "out2.jpg"))
    check("假视频 → ok=False", r2["ok"] is False, r2)
    check("假视频 → 有可转述的 error", bool(r2["error"]), r2)
    check("假视频 → 不返回图", r2["sheet"] == "")

    r3 = vf.video_sheet(os.path.join(_TMP, "nope.mp4"))
    check("文件不存在 → ok=False", r3["ok"] is False and "不存在" in r3["error"], r3)

    # ── 6. 帧下标策略 ────────────────────────────────────────
    check("均匀取 4 个下标含首尾", vf._even_indices(10, 4) == [0, 3, 6, 9],
          vf._even_indices(10, 4))
    check("总帧数=1 时不越界", vf._even_indices(1, 4) == [0], vf._even_indices(1, 4))
finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
