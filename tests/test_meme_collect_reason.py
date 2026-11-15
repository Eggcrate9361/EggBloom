# -*- coding: utf-8 -*-
"""群聊表情包"拿了但其实没拿"（2026-11-02）。

## 现场

今天群里 41 条图片消息，`[表情采集] 入库` 只有 28 条 —— 剩下 13 张
**一声不吭**就没了。`maybe_collect()` 的每个 `return ""` 都是静默的，
只有"视觉重复"那一支会打日志。用户体感正是"应该是拿了，但是是没拿"。

## 契约

1. 每条跳过都说原因（`[表情采集] 跳过 <文件>：<原因>`）；
2. 同一 (文件, 原因) 60 秒内只报一次（防刷屏）；
3. 原因覆盖：总开关 / 没落地 / 格式 / 太小 / 太大 / 已存过 / 视觉重复 /
   今日额度满 / 写不进图库；
4. `skip_reason()`（给模型看的"人话版"）与 `maybe_collect()` 判据保持一致。

运行：python tests/test_meme_collect_reason.py
"""
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import meme_collect as mc  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


logs = []
mc.set_logger(lambda m: logs.append(m))
mc._SKIP_SEEN.clear()

tmp = tempfile.mkdtemp()
try:
    # ── 1. 不存在 → 打日志 ──────────────────────────────────────────
    logs.clear()
    r = mc.maybe_collect(os.path.join(tmp, "nope.jpg"))
    check("不存在的图不入库", r == "", r)
    check("★ 跳过了但说了原因", bool(logs) and "跳过" in logs[-1], logs[-1:] or "(无日志)")

    # ── 2. 太小 ─────────────────────────────────────────────────────
    tiny = os.path.join(tmp, "tiny.png")
    open(tiny, "wb").write(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    logs.clear()
    mc.maybe_collect(tiny)
    check("★ 太小的图说清下限", bool(logs) and "图太小" in logs[-1], logs[-1:] or "")

    # ── 3. 格式 ─────────────────────────────────────────────────────
    bad = os.path.join(tmp, "a.txt")
    open(bad, "w").write("not an image")
    logs.clear()
    mc.maybe_collect(bad)
    check("★ 不支持的格式说清", bool(logs) and "格式" in logs[-1], logs[-1:] or "")

    # ── 4. 节流：同一 (文件,原因) 不重复刷 ─────────────────────────
    logs.clear()
    mc._SKIP_SEEN.clear()          # 前面第 1 项已经报过同一条，先清掉再看节流
    for _ in range(5):
        mc.maybe_collect(os.path.join(tmp, "nope.jpg"))
    check("★ 同一原因 60 秒内只报一次", len(logs) == 1, len(logs))

    # ── 5. skip_reason 与 maybe_collect 口径一致 ────────────────────
    check("skip_reason 对不存在给同一句",
          "没落地" in mc.skip_reason(os.path.join(tmp, "nope.jpg")),
          mc.skip_reason(os.path.join(tmp, "nope.jpg")))
    check("skip_reason 对太小给下限",
          "太小" in mc.skip_reason(tiny), mc.skip_reason(tiny))
    check("skip_reason 对格式给说明",
          "格式" in mc.skip_reason(bad), mc.skip_reason(bad))
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

# ── 6. 源码里不该再有静默 return "" ─────────────────────────────────
src = io.open(os.path.join(ROOT, "meme_collect.py"), encoding="utf-8").read()
body = src[src.find("def maybe_collect("):src.find("def _dhash(")]
silent = [ln.strip() for ln in body.splitlines()
          if ln.strip() == 'return ""']
check("★ maybe_collect 里没有裸 return \"\"", not silent, silent)
check("_log_skip 有节流表", "_SKIP_SEEN" in src and "_SKIP_LOCK" in src)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
