# -*- coding: utf-8 -*-
"""meme_desc 单测（P1-3，2026-10-30）。

钉死四件事：
  1. tags.json 两种历史形状（list / {tags:[...]}）都要读得出来。
  2. 描述缓存读写是幂等的、坏的 JSON 不会把链路带崩。
  3. note_line 只有路径也得能给出（宁可只有路径，也不要没有）。
  4. 没有视觉能力（describer=None）时不编描述。

运行：python tests/test_meme_desc.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import meme_desc as md  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


tmp = tempfile.mkdtemp(prefix="meme_desc_test_")
try:
    meme_dir = os.path.join(tmp, "memes", "memes")
    os.makedirs(os.path.join(meme_dir, "vrchat"), exist_ok=True)
    gif = os.path.join(meme_dir, "vrchat", "kipfel_001.gif")
    with open(gif, "wb") as f:
        f.write(b"GIF89a" + b"\x00" * 32)

    # ── 1. 标签两种形状 ─────────────────────────────────────────
    tags = {
        "vrchat/kipfel_001.gif": ["开心", "害羞"],
        "vrchat/kipfel_002.gif": {"tags": ["得意", "嫌弃"], "path": "x", "size": 1},
    }
    with open(os.path.join(meme_dir, "tags.json"), "w", encoding="utf-8") as f:
        json.dump(tags, f, ensure_ascii=False)

    check("list 形状读得出", md.tags_for(meme_dir, gif) == ["开心", "害羞"],
          md.tags_for(meme_dir, gif))
    gif2 = os.path.join(meme_dir, "vrchat", "kipfel_002.gif")
    with open(gif2, "wb") as f:
        f.write(b"GIF89a" + b"\x00" * 32)
    check("dict 形状读得出", md.tags_for(meme_dir, gif2) == ["得意", "嫌弃"],
          md.tags_for(meme_dir, gif2))
    check("查不到 = 空列表", md.tags_for(meme_dir, os.path.join(meme_dir, "nope.gif")) == [],
          md.tags_for(meme_dir, "nope.gif"))
    check("相对键是 posix 路径",
          md.rel_key(meme_dir, gif) == "vrchat/kipfel_001.gif", md.rel_key(meme_dir, gif))

    # ── 2. 描述缓存 ─────────────────────────────────────────────
    check("还没描述时 desc 为空", md.desc_for(meme_dir, gif) == "", md.desc_for(meme_dir, gif))
    ok = md.save_desc(meme_dir, "vrchat/kipfel_001.gif", "银发少女笑着比心")
    check("save_desc 成功", ok is True, ok)
    check("desc 读得回来", md.desc_for(meme_dir, gif) == "银发少女笑着比心",
          md.desc_for(meme_dir, gif))
    with open(md.descs_path(meme_dir), "w", encoding="utf-8") as f:
        f.write("{坏掉的 JSON")
    check("坏 JSON 不崩、按没有处理", md.desc_for(meme_dir, gif) == "",
          md.desc_for(meme_dir, gif))
    md.save_desc(meme_dir, "vrchat/kipfel_001.gif", "银发少女笑着比心")

    # ── 3. note_line ────────────────────────────────────────────
    line = md.note_line(meme_dir, gif)
    check("note 带路径", "【表情包】vrchat/kipfel_001.gif" in line, line)
    check("note 带标签", "标签：开心、害羞" in line, line)
    check("note 带画面", "画面：银发少女笑着比心" in line, line)

    lonely = os.path.join(meme_dir, "vrchat", "unknown.gif")
    line2 = md.note_line(meme_dir, lonely)
    check("没标签没描述时仍有路径", line2 == "【表情包】vrchat/unknown.gif", line2)

    # ── 4. warm ─────────────────────────────────────────────────
    calls = []

    def fake_describer(p):
        calls.append(p)
        return "一只走路猪"

    out = md.warm(meme_dir, lonely, fake_describer, background=False)
    check("warm 同步补描述", out == "一只走路猪", out)
    check("warm 落盘了", md.desc_for(meme_dir, lonely) == "一只走路猪",
          md.desc_for(meme_dir, lonely))
    md.warm(meme_dir, lonely, fake_describer, background=False)
    check("已有缓存不再打视觉", len(calls) == 1, calls)

    out = md.warm(meme_dir, os.path.join(meme_dir, "vrchat", "x2.gif"), None,
                  background=False)
    check("没有视觉能力 → 不编", out == "", out)

    # ── 5. bot_core 接线：history 里真的多一条 ──────────────────
    sys.path.insert(0, HERE)
    from _sandbox import sandbox_neurobase  # noqa: E402
    sandbox_neurobase()
    import bot_core  # noqa: E402

    _orig_desc = bot_core.describe_image
    bot_core.describe_image = lambda p, q="": "测试画面"   # 别真打视觉
    try:
        bot_core.chat_history["900001"] = []
        bot_core._note_sent_meme(gif, target_qq="900001")
        hist = bot_core.chat_history["900001"]
        check("私聊 history 多了一条表情包记录", len(hist) == 1, hist)
        check("记录以【表情包】开头",
              bool(hist) and hist[0]["content"].startswith("【表情包】"), hist)
        check("记录带文件名", bool(hist) and "kipfel_001.gif" in hist[0]["content"], hist)

        bot_core.group_chat_history["777"] = []
        bot_core._note_sent_meme(gif, group_id="777")
        gh = bot_core.group_chat_history["777"]
        check("群 history 也记了", len(gh) == 1 and "【表情包】" in gh[0]["content"], gh)

        # 台账坏了不能影响发送：坏路径只记日志
        bot_core._note_sent_meme(None, target_qq="900001")
        check("空路径不抛异常", True)
    finally:
        bot_core.describe_image = _orig_desc
        bot_core.chat_history.pop("900001", None)
        bot_core.group_chat_history.pop("777", None)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
