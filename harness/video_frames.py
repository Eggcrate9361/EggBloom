# -*- coding: utf-8 -*-
"""harness/video_frames.py —— 抽帧看视频（2026-10-30）。

## 思路

视频不需要"看懂整段"：抽 4 帧拼成一张 **2×2 联系表**（contact sheet），
交给多模态模型一次读完。按 DeepSeek 的计费规则，一张图最多 1024 token，
所以 4 帧 = 1 次请求 / ≈1024 token；逐帧发就是 4 次请求。

## 两条解码路径（这台机器的现实）

1. **多帧图片**（GIF / 动图 WebP / APNG）：PIL 直接抽，**不需要 ffmpeg**。
   QQ 里的"动画表情"全是这类，占到视频请求的大头。
2. **真视频**（mp4 / mov / mkv…）：必须有 ffmpeg。找不到就**如实失败**，
   不许猜内容 —— 与【能力边界】一致。

ffmpeg 查找顺序：环境变量 `EGGBLOOM_FFMPEG` → PATH → `<app>/bin/ffmpeg.exe`
→ `imageio_ffmpeg` 自带的二进制。找不到就返回空串，调用方据此降级。

不 import bot_core；只依赖 PIL（已在 requirements 里）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

#: 默认抽几帧。4 帧 = 2×2 正好一张方图，覆盖"开头/中段/结尾"。
DEFAULT_FRAMES = 4

#: 联系表边长（像素）。服务端会把图缩到约 1300×1300 再计费，这里对齐它。
DEFAULT_SHEET_SIZE = 1280

#: 走 PIL 的"多帧图片"后缀。真视频不在这里。
_ANIMATION_EXT = (".gif", ".webp", ".png", ".apng")


def _autonomy_ffmpeg_binary() -> str:
    """直接找 autonomy venv 里 imageio-ffmpeg 自带的二进制。

    不 import imageio_ffmpeg、不把 venv site-packages 塞进主进程：
    ffmpeg 就是一个 exe，找到路径即可，避免依赖冲突。
    """
    try:
        from pathlib import Path
        env = (os.environ.get("EGGBLOOM_AUTONOMY_VENV") or "").strip()
        if env:
            dd = Path(env)
        else:
            try:
                from data_store import get_data_dir
                dd = Path(get_data_dir())
            except Exception:
                dd = Path(__file__).resolve().parent.parent / "data"
            dd = dd / "autonomy" / "venv"
        pats = []
        for base in (dd / "Lib" / "site-packages", *sorted((dd / "lib").glob("python*/site-packages"))):
            pats += sorted((base / "imageio_ffmpeg" / "binaries").glob("ffmpeg*"))
        for p in pats:
            if p.is_file() and p.suffix.lower() in (".exe", ""):
                return str(p)
    except Exception:
        pass
    return ""


def find_ffmpeg() -> str:
    """找一个可用的 ffmpeg 可执行文件；没有返回 ""。"""
    env = (os.environ.get("EGGBLOOM_FFMPEG") or "").strip()
    if env and os.path.isfile(env):
        return env
    p = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if p:
        return p
    p = _autonomy_ffmpeg_binary()
    if p:
        return p
    app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in (os.path.join("bin", "ffmpeg.exe"), os.path.join("tools", "ffmpeg.exe"),
                "ffmpeg.exe"):
        cand = os.path.join(app, rel)
        if os.path.isfile(cand):
            return cand
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe() or ""
    except Exception:
        return ""


def is_animation(path: str) -> bool:
    """这张图是不是多帧（GIF/动图 WebP/APNG）。"""
    if os.path.splitext(str(path or ""))[1].lower() not in _ANIMATION_EXT:
        return False
    try:
        from PIL import Image
        with Image.open(path) as im:
            return int(getattr(im, "n_frames", 1) or 1) > 1
    except Exception:
        return False


def _even_indices(total: int, n: int) -> List[int]:
    """从 total 帧里均匀取 n 个下标（含首尾附近，避开最后一帧的空白）。"""
    total = max(1, int(total))
    n = max(1, min(int(n), total))
    if n == 1:
        return [total // 2]
    step = (total - 1) / float(n - 1)
    return sorted({int(round(i * step)) for i in range(n)})


def frames_from_animation(path: str, n: int = DEFAULT_FRAMES) -> List[Any]:
    """多帧图片 → 若干 PIL 图（RGB）。失败返回 []。"""
    out = []
    try:
        from PIL import Image
        with Image.open(path) as im:
            total = int(getattr(im, "n_frames", 1) or 1)
            for idx in _even_indices(total, n):
                try:
                    im.seek(idx)
                    out.append(im.convert("RGB").copy())
                except Exception:
                    continue
    except Exception:
        return []
    return out


def _scan_hsv(im) -> List[float]:
    """缩到 64×36 后取 HSV，作为变化分的输入（不落盘、不依赖 cv2）。"""
    from PIL import Image
    small = im.convert("RGB").resize((64, 36), Image.BILINEAR)
    out: List[float] = []
    for r, g, b in small.getdata():
        rn, gn, bn = r / 255.0, g / 255.0, b / 255.0
        mx, mn = max(rn, gn, bn), min(rn, gn, bn)
        d = mx - mn
        h = 0.0
        if d:
            if mx == rn:
                h = ((gn - bn) / d) % 6
            elif mx == gn:
                h = (bn - rn) / d + 2
            else:
                h = (rn - gn) / d + 4
            h = (h * 60.0) % 360.0
        out.extend((h / 360.0 * 255.0,
                    (d / mx * 255.0) if mx else 0.0,
                    mx * 255.0))
    return out


def _score_pair(a: List[float], b: List[float]) -> float:
    """HSV 加权绝对差，0–255；色相环形处理，低饱和像素的色相降权。"""
    total = 0.0
    for i in range(0, len(b), 3):
        dh = abs(b[i] - a[i])
        hd = min(dh, 255.0 - dh)
        w = min(b[i + 1], a[i + 1]) / 255.0
        total += hd * w + abs(b[i + 1] - a[i + 1]) + abs(b[i + 2] - a[i + 2])
    return total / max(1, (len(b) // 3) * 3)


def _select_indices(frames: List[Any], n: int,
                    *, cut: float = 12.0, motion: float = 18.0) -> List[int]:
    """按变化量挑帧：硬切留一帧，持续运动攒够量留一帧。

    静止画面可能只回 1 帧——这是有意的，别为了"凑满 4 格"把重复画面喂给模型。
    """
    if not frames:
        return []
    n = max(1, int(n))
    if len(frames) <= n:
        return list(range(len(frames)))
    scans = [_scan_hsv(f) for f in frames]
    picked = [0]
    acc = 0.0
    for i in range(1, len(scans)):
        s = _score_pair(scans[i - 1], scans[i])
        if s >= cut:
            picked.append(i)
            acc = 0.0
        else:
            acc += s
            if acc >= motion:
                picked.append(i)
                acc = 0.0
    if len(picked) > n:
        # 保首尾，中间等距抽
        if n == 1:
            picked = [picked[len(picked) // 2]]
        else:
            idx = [round(i * (len(picked) - 1) / (n - 1)) for i in range(n)]
            picked = [picked[j] for j in idx]
    return picked or [0]


def _duration_seconds(path: str, ffmpeg: str, headers: str = "") -> float:
    """用 ffmpeg -i 的输出里那行 Duration 估时长（不需要 ffprobe）。"""
    try:
        args = [ffmpeg, "-hide_banner"]
        if headers:
            args += ["-headers", headers]
        args += ["-i", path]
        r = subprocess.run(args,
                           capture_output=True, text=True, errors="replace", timeout=20)
        text = (r.stderr or "") + (r.stdout or "")
        import re
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
        if not m:
            return 0.0
        h, mi, s = m.groups()
        return int(h) * 3600 + int(mi) * 60 + float(s)
    except Exception:
        return 0.0


def frames_via_ffmpeg(path: str, n: int = DEFAULT_FRAMES, headers: str = "") -> List[Any]:
    """真视频 → 若干 PIL 图。没 ffmpeg / 解不出来都返回 []。"""
    ff = find_ffmpeg()
    if not ff:
        return []
    dur = _duration_seconds(path, ff, headers)
    tmpdir = tempfile.mkdtemp(prefix="vframe_")
    out = []
    try:
        # 先按较高密度取候选帧，再按变化量挑；静止画面只留 1 帧，硬切每切留 1 帧。
        if dur > 0:
            cand_n = min(24, max(8, int(dur * 2) + 4))
            times = [dur * (i + 0.5) / cand_n for i in range(cand_n)]
        else:
            cand_n = 12
            times = [float(i) for i in range(cand_n)]
        for i, t in enumerate(times):
            jpg = os.path.join(tmpdir, f"f{i}.jpg")
            cmd = [ff, "-hide_banner", "-loglevel", "error"]
            if headers:
                cmd += ["-headers", headers]
            cmd += ["-ss", f"{max(0.0, t):.2f}",
                    "-i", path, "-frames:v", "1", "-vf", "scale=640:-2", jpg, "-y"]
            try:
                subprocess.run(cmd, capture_output=True, timeout=25)
            except Exception:
                continue
            if os.path.isfile(jpg) and os.path.getsize(jpg) > 0:
                try:
                    from PIL import Image
                    with Image.open(jpg) as im:
                        out.append(im.convert("RGB").copy())
                except Exception:
                    continue
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if not out:
        return []
    return [out[i] for i in _select_indices(out, n)]


def frames_via_ffmpeg_uniform(path: str, n: int = 16,
                               headers: str = "") -> List[Any]:
    """沿完整时长均匀抽 n 帧；不看变化量，保证覆盖全片。"""
    ff = find_ffmpeg()
    if not ff:
        return []
    dur = _duration_seconds(path, ff, headers)
    if dur <= 0:
        return frames_via_ffmpeg(path, n, headers)
    n = max(1, int(n))
    # 取每段的中间，避开片头黑场和片尾；至少覆盖到 95% 位置。
    times = [dur * (i + 0.5) / n for i in range(n)]
    tmpdir = tempfile.mkdtemp(prefix="vframe_u_")
    out = []
    try:
        for i, t in enumerate(times):
            jpg = os.path.join(tmpdir, f"f{i}.jpg")
            cmd = [ff, "-hide_banner", "-loglevel", "error"]
            if headers:
                cmd += ["-headers", headers]
            cmd += ["-ss", f"{max(0.0, t):.2f}",
                    "-i", path, "-frames:v", "1", "-vf", "scale=640:-2", jpg, "-y"]
            try:
                subprocess.run(cmd, capture_output=True, timeout=30)
            except Exception:
                continue
            if os.path.isfile(jpg) and os.path.getsize(jpg) > 0:
                try:
                    from PIL import Image
                    with Image.open(jpg) as im:
                        out.append(im.convert("RGB").copy())
                except Exception:
                    continue
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return out


def video_sheets(path: str, frames: int = 16, sheets: int = 4,
                 size: int = DEFAULT_SHEET_SIZE, headers: str = "") -> Dict[str, Any]:
    """视频 → 多张联系表；按完整时长均匀抽帧。

    默认 16 帧 / 4 张图（每张 2×2）；高精度 16 帧 / 16 张（每张 1 帧）；
    低精度 8 帧 / 1 张（4×2）。返回 {ok, sheets, frames, duration, source, error}。
    """
    p = str(path or "").strip()
    if not p or (not p.startswith(("http://", "https://")) and not os.path.isfile(p)):
        return {"ok": False, "sheets": [], "frames": 0, "source": "none",
                "duration": 0.0, "error": "文件不存在"}
    n = max(1, int(frames or 16))
    sheet_n = max(1, int(sheets or 4))
    src, imgs, dur = "none", [], 0.0
    if not p.startswith(("http://", "https://")) and is_animation(p):
        imgs = frames_from_animation(p, n)
        src = "animation"
    else:
        ff = find_ffmpeg()
        dur = _duration_seconds(p, ff, headers) if ff else 0.0
        imgs = frames_via_ffmpeg_uniform(p, n, headers)
        src = "ffmpeg" if imgs else "none"
    if not imgs:
        err = ("这台机器没有 ffmpeg，视频解不了" if not find_ffmpeg()
               else "抽帧失败（编码/文件/网络可能有问题）")
        return {"ok": False, "sheets": [], "frames": 0, "source": src,
                "duration": round(dur, 1), "error": err}
    # 每张图放多少帧；尽量均分，最后一张可能少一点
    per = max(1, (len(imgs) + sheet_n - 1) // sheet_n)
    out = []
    base = os.path.join(tempfile.gettempdir(), f"vs_{abs(hash(p)) % 10 ** 8}")
    for si in range(0, len(imgs), per):
        group = imgs[si:si + per]
        if not group:
            continue
        k = len(group)
        cols = 1 if k == 1 else (2 if k <= 4 else (3 if k <= 9 else 4))
        outp = f"{base}_{len(out) + 1}.jpg"
        sheet = contact_sheet(group, outp, cols=cols, size=size)
        if sheet:
            out.append(sheet)
    if not out:
        return {"ok": False, "sheets": [], "frames": len(imgs), "source": src,
                "duration": round(dur, 1), "error": "拼贴失败"}
    return {"ok": True, "sheets": out, "frames": len(imgs), "source": src,
            "duration": round(dur, 1), "error": ""}


def contact_sheet(frames: List[Any], out_path: str,
                  cols: int = 2, size: int = DEFAULT_SHEET_SIZE) -> str:
    """把若干帧拼成一张方图（等比缩放 + 黑底居中）。返回落盘路径，失败返回 ""。"""
    frames = [f for f in (frames or []) if f is not None]
    if not frames:
        return ""
    cols = max(1, int(cols))
    rows = (len(frames) + cols - 1) // cols
    cell = max(64, int(size) // cols)
    try:
        from PIL import Image
        sheet = Image.new("RGB", (cell * cols, cell * rows), (16, 14, 12))
        for i, fr in enumerate(frames[: cols * rows]):
            im = fr.copy()
            im.thumbnail((cell - 8, cell - 8))
            x = (i % cols) * cell + (cell - im.width) // 2
            y = (i // cols) * cell + (cell - im.height) // 2
            sheet.paste(im, (x, y))
        d = os.path.dirname(str(out_path))
        if d:
            os.makedirs(d, exist_ok=True)
        sheet.save(out_path, "JPEG", quality=90)
        return out_path
    except Exception:
        return ""


def video_sheet(path: str, out_path: Optional[str] = None,
                n: int = DEFAULT_FRAMES, size: int = DEFAULT_SHEET_SIZE) -> Dict[str, Any]:
    """视频/动图 → 联系表。返回 `{ok, sheet, frames, source, duration, error}`。

    `source` 说明走的是哪条路：animation（PIL） / ffmpeg / none。
    失败时 `error` 是给人看的一句话，调用方直接转述即可 —— 不许猜内容。
    """
    p = str(path or "").strip()
    if not p or not os.path.isfile(p):
        return {"ok": False, "sheet": "", "frames": 0, "source": "none",
                "duration": 0.0, "error": "文件不存在"}
    out_path = out_path or os.path.join(tempfile.gettempdir(),
                                        f"vs_{abs(hash(p)) % 10 ** 8}.jpg")

    src, frames, dur = "none", [], 0.0
    if is_animation(p):
        frames = frames_from_animation(p, n)
        src = "animation"
    else:
        dur = _duration_seconds(p, find_ffmpeg()) if find_ffmpeg() else 0.0
        frames = frames_via_ffmpeg(p, n)
        src = "ffmpeg" if frames else "none"

    if not frames:
        err = ("这台机器没有 ffmpeg，视频解不了" if not find_ffmpeg()
               else "抽帧失败（编码/文件可能损坏）")
        return {"ok": False, "sheet": "", "frames": 0, "source": src,
                "duration": round(dur, 1), "error": err}

    cols = 2 if len(frames) > 1 else 1
    sheet = contact_sheet(frames, out_path, cols=cols, size=size)
    if not sheet:
        return {"ok": False, "sheet": "", "frames": len(frames), "source": src,
                "duration": round(dur, 1), "error": "拼贴失败"}
    return {"ok": True, "sheet": sheet, "frames": len(frames), "source": src,
            "duration": round(dur, 1), "error": ""}


__all__ = ["DEFAULT_FRAMES", "DEFAULT_SHEET_SIZE", "find_ffmpeg", "is_animation",
           "frames_from_animation", "frames_via_ffmpeg", "contact_sheet", "video_sheet"]
