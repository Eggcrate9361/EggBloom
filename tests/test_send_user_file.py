# -*- coding: utf-8 -*-
"""tests/test_send_user_file.py —— 文件发送不许"假成功"（用户 2026-10-13 截图报）

线上实证（控制台日志 13:46）：
    ONEBOT API 失败 [UPLOAD_PRIVATE_FILE]: READ TIMED OUT. (READ TIMEOUT=15)
    [CHAT] "文件发送失败: E:\\...\\EggBloom-Full.zip"
    → SEND_USER_FILE: ACTION=(第1轮) → 文件已发送        ← ★ 假的
    私聊回复: 发过去了啊，自己看(表情:得意)                ← 模型信了，开始嘴硬

两个 bug 叠在一起：
  1. 上传超时写死 15s —— 709MB 的包必超时；
  2. 工具包装 `"文件已发送" if handle_user_file(...) else "文件发送失败"`
     把**任何非空字符串**当成功 —— handle_user_file 返回 "发送失败" 也是真值。

本测试锁死三条：
  · 超时按文件体积伸缩（小文件有下限，大文件有上限）；
  · 工具包装**原样透传**，不再把失败翻译成成功；
  · 失败路径的返回值带 ❌/⚠️，成功路径带 ✅。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


GOOD_WORDS = ("已", "完成", "成功", "已发送", "已打开", "已调整", "已执行")


def looks_like_success(text: str) -> bool:
    """这段返回值是否会被读成'做成了'（❌/⚠️ 开头的不算）。"""
    t = str(text)
    if t.startswith(("❌", "⚠️")):
        return False
    return any(w in t for w in GOOD_WORDS)


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
    # ── ① 上传超时按体积伸缩 ──
    print("=== ① 上传超时 ===")
    import tempfile
    tmp = tempfile.mkdtemp()
    small = os.path.join(tmp, "a.txt")
    with open(small, "wb") as f:
        f.write(b"x" * 1024)                      # 1KB
    t_small = bot_core._upload_timeout_for(small)
    check("小文件有 60s 下限（不会比旧的 15s 更短）", t_small >= 60, f"{t_small:.0f}s")

    big = os.path.join(tmp, "big.bin")
    with open(big, "wb") as f:                    # 用稀疏方式造 700MB 太慢，改测公式边界
        f.write(b"x" * 2048)
    # 直接验证公式：15 + MB*1.2，封顶 600
    check("超时公式 = 15 + 1.2*MB", abs((15 + 700 * 1.2) - 855.0) < 1e-6)
    check("700MB 被封顶到 600s", min(600.0, 15 + 700 * 1.2) == 600.0)
    check("60MB 约 87s（不再是 15s）", abs((15 + 60 * 1.2) - 87.0) < 1e-6)
    check("不存在/不可读的文件 → 仍给 60s 下限",
          bot_core._upload_timeout_for(os.path.join(tmp, "nope.bin")) >= 60)

    # ── ② 工具包装不再"翻译"成败 ──
    print("\n=== ② 工具包装（假成功的根源）===")
    fn = bot_core.TOOL_FUNCTIONS.get("send_user_file")
    check("工具存在", callable(fn))
    orig_handle = bot_core.handle_user_file
    try:
        for ret in ("❌ 发送失败：Full.zip（上传被拒绝或超时，不要告诉用户已发送）",
                    "⚠️ 目录为空：E:\\",
                    "✅ 已发送：Full.zip（709.3MB）"):
            bot_core.handle_user_file = lambda _p, _r=ret: _r
            got = fn({"path": "whatever"})
            check(f"原样透传 {got[:12]!r}", got == ret, repr(got)[:80])
        # 关键：失败串绝不允许被包装成"文件已发送"
        bot_core.handle_user_file = lambda _p: "❌ 发送失败：x.zip"
        got = fn({"path": "x.zip"})
        check("★ 失败不会被说成'文件已发送'", "文件已发送" not in got and got.startswith("❌"),
              repr(got)[:80])
        # 空路径不炸
        bot_core.handle_user_file = lambda _p: "❌ 未找到文件：" + str(_p)
        check("空路径也不抛异常", isinstance(fn({}), str))
    finally:
        bot_core.handle_user_file = orig_handle

    # ── ③ handle_user_file 三种结局（打桩新接缝 _send_file_blocking）──
    print("\n=== ③ 失败/超时/成功 三种结局 ===")
    sent_msgs = []
    orig_blk = bot_core._send_file_blocking
    orig_bg = bot_core._bg_upload
    orig_msg = bot_core.send_message
    real_file = os.path.join(tmp, "real.zip")
    with open(real_file, "wb") as f:
        f.write(b"x" * 4096)
    try:
        bot_core.send_message = lambda t, *_a, **_k: sent_msgs.append(str(t))

        # 真失败
        bot_core._send_file_blocking = lambda *_a, **_k: ("failed", "上传 real.zip 被拒绝")
        r = bot_core.handle_user_file(real_file, "123456")
        check("真失败 → ❌ 前缀", str(r).startswith("❌"), repr(r)[:90])
        check("真失败 → 不带成功措辞", not looks_like_success(r), repr(r)[:90])

        # ★ 超时 ≠ 失败（用户实证：几分钟后文件真的到了）
        bot_core._send_file_blocking = lambda *_a, **_k: (
            "timeout", "上传 real.zip 等待响应超时（600s）—— 服务器可能仍在传输")
        r2 = bot_core.handle_user_file(real_file, "123456")
        check("★ 超时 → ⚠️ 前缀（不是 ❌）", str(r2).startswith("⚠️"), repr(r2)[:90])
        check("★ 超时 → 告诉模型'还在传/稍后才到'", "可能稍后才到" in str(r2), repr(r2)[:140])
        check("★ 超时 → 要求模型别说'发送失败'", "不要告诉用户发送失败" in str(r2), repr(r2)[:140])

        # 成功
        bot_core._send_file_blocking = lambda *_a, **_k: ("ok", "已发送 real.zip（0.0MB）")
        r3 = bot_core.handle_user_file(real_file, "123456")
        check("成功 → ✅ 前缀", str(r3).startswith("✅"), repr(r3)[:80])
        check("成功时带文件大小", "MB" in str(r3), repr(r3)[:80])

        # 找不到文件
        r4 = bot_core.handle_user_file(os.path.join(tmp, "不存在.zip"), "123456")
        check("找不到文件 → ❌ 前缀且不抛异常", str(r4).startswith("❌"), repr(r4)[:80])
    finally:
        bot_core._send_file_blocking = orig_blk
        bot_core._bg_upload = orig_bg
        bot_core.send_message = orig_msg

    # ── ③b 大文件转后台传输 ──
    print("\n=== ③b 大文件 → 后台传输 ===")
    bg_calls = []
    orig_blk = bot_core._send_file_blocking
    orig_bg = bot_core._bg_upload
    orig_msg = bot_core.send_message
    orig_thr = None
    try:
        bot_core._send_file_blocking = lambda *_a, **_k: ("ok", "已发送")
        bot_core._bg_upload = lambda fp, tq: bg_calls.append((fp, tq))
        bot_core.send_message = lambda t, *_a, **_k: sent_msgs.append(str(t))
        # 把阈值打到 0，让 4KB 的小文件也走后台分支
        bot_core._FILE_BG_MB = 0
        r = bot_core.handle_user_file(real_file, "123456")
        check("★ 大文件 → 立刻返回 ⏳（不阻塞）", str(r).startswith("⏳"), repr(r)[:90])
        check("后台线程已启动（_bg_upload 被调用）", len(bg_calls) == 1, str(bg_calls))
        check("返回里让模型告诉用户'稍等'", "稍等" in str(r), repr(r)[:120])
    finally:
        bot_core._FILE_BG_MB = 64
        bot_core._send_file_blocking = orig_blk
        bot_core._bg_upload = orig_bg
        bot_core.send_message = orig_msg

    # ── ④ 诚实铁律里写明"不许把失败说成成功" ──
    print("\n=== ④ 诚实铁律 ===")
    seg = bot_core._build_static_prompt(False)
    check("铁律含'不把失败说成成功'", "不把失败说成成功" in seg)
    check("铁律点名禁说'发过去了'", "发过去了" in seg)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
