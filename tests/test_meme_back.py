# -*- coding: utf-8 -*-
"""tests/test_meme_back.py —— 表情包三件事（2026-10-13 用户拍板）

  1. **只发一张表情包 → 同回一张表情包**：不读图、不解释、不动用主模型；
     回敬失败（库空/发送失败）才落回读图路径；冷却中直接静默。
  2. **不让 AI 次次都读表情包**：群里顺带甩个表情、话里没在说这张图
     → 不交视觉、不交主模型（describe/native 两条模式都封住）。
  3. **照片/截图维持原行为**：非 GIF 的图照旧会被读 —— 那是发给它看的。

外加 GIF 透明底：压缩链路必须保留透明索引（"bot 发的表情包有黑底"回归钉）。
不调真实模型/网络：视觉、主模型、发送全部打桩。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    from PIL import Image  # noqa: E402
except ImportError:
    print("SKIP: 需要 Pillow（pip install Pillow）")
    sys.exit(0)

import bot_core  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
TMP = os.path.join(ROOT, "tests", "_meme_back_tmp")


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


_PALETTE = []
for i in range(256):
    _PALETTE += [(i * 7) % 256, (i * 13) % 256, (i * 29 + 40) % 256]
_PALETTE[0:3] = [0, 0, 0]   # 0 号 = 透明索引（黑兜底）


def _make_gif(path, transparent=True, w=40, h=40, n=3, noise=False):
    """造一张真 GIF（P 模式 + 显式透明索引 0，扩展名与文件头一致）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    frames = []
    for i in range(n):
        f = Image.new("P", (w, h), (i * 37 + 5) % 251 + 4)
        f.putpalette(_PALETTE)
        if noise:   # 真随机索引撑体积（规律条纹会被 LZW 压没了）
            import random as _rnd
            _rnd.seed(20261013 + i)
            px = f.load()
            for y in range(h):
                for x in range(w):
                    px[x, y] = _rnd.randint(4, 253)
        if transparent:
            px = f.load()
            for y in range(h):
                for x in range(w):
                    if x < max(4, w // 12) or y < max(4, h // 12):
                        px[x, y] = 0
        frames.append(f)
    frames[0].save(path, format="GIF", save_all=True, append_images=frames[1:],
                   duration=60, loop=0, disposal=2, transparency=0)
    return path


def _make_jpg(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (40, 40), (90, 120, 200)).save(path, "JPEG")
    return path


def test_helpers():
    print("\n=== ① 判定函数 ===")
    check("纯图消息（[小明] [图片]）→ True",
          bot_core._is_pure_image_text("[小明] [图片]"))
    check("纯图消息（多用户合并）→ True",
          bot_core._is_pure_image_text("[小明] [图片]\n[小红] [图片]"))
    check("图 + 一句话 → False",
          not bot_core._is_pure_image_text("[小明] [图片]\n这啥啊"))
    check("只有文字 → False", not bot_core._is_pure_image_text("[小明] 哈哈哈"))

    g = _make_gif(os.path.join(TMP, "a.gif"))
    j = _make_jpg(os.path.join(TMP, "b.jpg"))
    check("全 GIF → 是表情包", bot_core._all_memes([g]))
    check("JPG（照片）→ 不是表情包", not bot_core._all_memes([j]))
    check("GIF+JPG 混合 → 按要看的图处理", not bot_core._all_memes([g, j]))
    check("空列表 → False", not bot_core._all_memes([]))
    # 下载链路会把图统一存成 .jpg —— 必须靠文件头而不是扩展名
    fake = _make_gif(os.path.join(TMP, "fake.jpg"))  # 内容是 GIF，扩展名是 .jpg
    check("扩展名骗人（GIF 内容存成 .jpg）仍认表情包", bot_core._all_memes([fake]))

    check("@我 → 需要读图", bot_core._image_understanding_needed("哈哈", True, False))
    check("引用我 → 需要读图", bot_core._image_understanding_needed("", False, True))
    check("话里在说图（这是啥）→ 需要读",
          bot_core._image_understanding_needed("[小明] 这是啥"))
    check("话里没提图（你和他们去吃饭了吗）→ 不读",
          not bot_core._image_understanding_needed("[小明] 你和他们去吃饭了吗"))


def _stub_env():
    """公共打桩：主模型/发送/表情库。返回 (记录本, 还原函数)。"""
    seen = {"ai": [], "sent_text": [], "meme_img": []}
    orig = (bot_core.call_deepseek, bot_core.smart_send_group,
            bot_core.send_group_image, bot_core.match_meme,
            bot_core.describe_image, bot_core.startup_time)
    orig_cfg = dict(bot_core._config)
    orig_sess = dict(bot_core.GROUP_SESSION)
    bot_core.call_deepseek = lambda user_msg, images=None, *a, **kw: (
        seen["ai"].append((user_msg, images)) or "好图！")
    bot_core.smart_send_group = lambda gid, text, *a, **kw: (
        seen["sent_text"].append(text) or True)
    bot_core.send_group_image = lambda gid, path: (
        seen["meme_img"].append(path) or True)
    bot_core.match_meme = lambda emo: os.path.join(TMP, "a.gif")
    bot_core.describe_image = lambda ref, prompt="": "一张猫猫表情包（桩描述）"
    bot_core.startup_time = time.time() - 10 ** 6
    bot_core._config["meme_enable"] = True
    bot_core._config["image_reply_mode"] = "native"
    bot_core.MEME_LAST_SENT.clear()

    def restore():
        (bot_core.call_deepseek, bot_core.smart_send_group,
         bot_core.send_group_image, bot_core.match_meme,
         bot_core.describe_image, bot_core.startup_time) = orig
        bot_core._config.clear(); bot_core._config.update(orig_cfg)
        bot_core.GROUP_SESSION.clear(); bot_core.GROUP_SESSION.update(orig_sess)
        bot_core.MEME_LAST_SENT.clear()
    return seen, restore


def _run_group(gid, text, imgs, mode="native", session=True, **buf_extra):
    bot_core._config["image_reply_mode"] = mode
    if session:
        bot_core.GROUP_SESSION[gid] = {"remaining": 9, "last_msg_time": time.time()}
    buf = {"text": text, "sender": "10001", "time": time.time(),
           "is_at_bot": False, "has_xiao_a": False, "has_bot_nick": False,
           "has_quote": False, "has_text_at": False, "in_session": True,
           "has_context": True, "images": imgs, "timer": None,
           "message_id": 0, "merged_n": 0}
    buf.update(buf_extra)
    bot_core.GROUP_MSG_BUFFER[gid] = buf
    try:
        bot_core.process_buffered_message(gid)
    finally:
        bot_core.GROUP_MSG_BUFFER.pop(gid, None)


def test_group_pure_meme():
    print("\n=== ② 群聊：只发表情包 → 同回一张，不读不解释 ===")
    g = _make_gif(os.path.join(TMP, "a.gif"))
    seen, restore = _stub_env()
    try:
        _run_group("_t_meme_pure", "[小明] [图片]", [g], is_at_bot=True)
        check("★ 回敬了一张表情包", len(seen["meme_img"]) == 1, str(seen["meme_img"]))
        check("★ 没调用主模型（不解释）", not seen["ai"], str(seen["ai"])[:120])
        check("没发文字", not seen["sent_text"], str(seen["sent_text"]))

        # 冷却中 → 静默（既不回敬也不读图）
        seen["meme_img"].clear()
        _run_group("_t_meme_pure", "[小红] [图片]", [g], is_at_bot=True)
        check("★ 冷却中：不再回敬", not seen["meme_img"], str(seen["meme_img"]))
        check("★ 冷却中：也没落回读图", not seen["ai"], str(seen["ai"])[:120])
    finally:
        restore()

    print("\n=== ②b 回敬要看情绪：来图判「嫌弃」→ 接梗池里挑（不随机乱甩）===")
    seen, restore = _stub_env()
    _orig_auto = bot_core.auto_detect_emotion
    try:
        bot_core.describe_image = lambda ref, prompt="": "嫌弃|猫猫翻白眼甩尾巴"
        picked = []
        bot_core.match_meme = lambda emo: (picked.append(emo) or
                                           os.path.join(TMP, "a.gif"))
        _run_group("_t_meme_emo", "[小明] [图片]", [g], is_at_bot=True)
        check("★ 情绪判定进了匹配链（classify→reply_map→match）",
              bool(picked) and picked[0] in ("嫌弃", "呆滞", "得意"), str(picked))
        check("回敬仍不发文字、不动主模型",
              not seen["sent_text"] and not seen["ai"], str(seen)[:80])
        # 判不出情绪 → 退回随机池（match_meme 仍被调用，说明没卡死）
        picked.clear()
        bot_core.MEME_LAST_SENT.clear()
        bot_core.describe_image = lambda ref, prompt="": ""
        bot_core.auto_detect_emotion = lambda t: None
        _run_group("_t_meme_emo2", "[小明] [图片]", [g], is_at_bot=True)
        check("判不出情绪 → 仍随机回敬一张", bool(picked), str(picked))
    finally:
        bot_core.auto_detect_emotion = _orig_auto
        restore()

    print("\n=== ③ 群聊：回敬失败 → 落回读图路径（看懂再说）===")
    seen, restore = _stub_env()
    try:
        bot_core.match_meme = lambda emo: None      # 表情库空
        _run_group("_t_meme_fallback", "[小明] [图片]", [g],
                   mode="describe", is_at_bot=True)
        check("★ 落回了主模型", bool(seen["ai"]), str(seen["ai"])[:80])
        if seen["ai"]:
            check("落回时 prompt 带视觉描述块",
                  "视觉识别" in seen["ai"][0][0], seen["ai"][0][0][:120])
    finally:
        restore()


def test_group_vision_gate():
    print("\n=== ④ 群聊：顺带甩表情、话里没说图 → 不读（照片不受影响）===")
    g = _make_gif(os.path.join(TMP, "a.gif"))
    j = _make_jpg(os.path.join(TMP, "b.jpg"))
    seen, restore = _stub_env()
    try:
        # native：表情包 + 无关文字 → 原图不交主模型
        _run_group("_t_gate_native", "[小明] 你和他们去吃饭了吗 [图片]", [g],
                   mode="native")
        check("★ 无关闲聊+表情包：主模型没收到图",
              bool(seen["ai"]) and not seen["ai"][0][1], str(seen["ai"][0])[:100])
        # 话里在说图 → 照常读
        seen["ai"].clear()
        _run_group("_t_gate_ask", "[小明] 你收到这个表情包了吗", [g], mode="native")
        check("★ 问表情包本身：仍然交图理解",
              bool(seen["ai"]) and bool(seen["ai"][0][1]), str(seen["ai"][0])[:100])
        # 照片（JPG）→ 维持原行为，无条件读
        seen["ai"].clear()
        _run_group("_t_gate_photo", "[小明] 你和他们去吃饭了吗 [图片]", [j],
                   mode="native")
        check("★ 照片/截图不受门禁影响（照旧交图）",
              bool(seen["ai"]) and bool(seen["ai"][0][1]), str(seen["ai"][0])[:100])
        # describe 模式同样受门禁：不注入视觉块
        seen["ai"].clear()
        _run_group("_t_gate_desc", "[小明] 你和他们去吃饭了吗 [图片]", [g],
                   mode="describe")
        check("★ describe 模式：无关表情包不注入视觉块",
              bool(seen["ai"]) and "视觉识别" not in seen["ai"][0][0],
              seen["ai"][0][0][:120] if seen["ai"] else "没调模型")
    finally:
        restore()


def test_gif_transparency():
    print("\n=== ⑤ GIF 压缩保留透明底（黑底 bug 回归钉）===")
    src = _make_gif(os.path.join(TMP, "big_trans.gif"),
                    transparent=True, w=480, h=480, n=30, noise=True)
    if os.path.getsize(src) <= bot_core.SEND_IMAGE_MAX_BYTES:
        check("测试素材太小，跳过（不误报失败）", True, str(os.path.getsize(src)))
        return
    send_path, is_temp = bot_core._prepare_image_for_send(src)
    try:
        check("压缩成功（未跳过）", send_path is not None, "")
        if send_path:
            im = Image.open(send_path)
            check("★ 输出声明了透明索引", im.info.get("transparency") is not None,
                  str(im.info.get("transparency")))
            im.seek(0)
            rgba = im.convert("RGBA")
            corner = rgba.getpixel((2, 2))
            check("★ 透明区压完仍是透明（alpha=0，不是黑底）",
                  corner[3] == 0, str(corner))
            check("★ 帧数全保留", getattr(im, "n_frames", 1) == 30,
                  str(getattr(im, "n_frames", 1)))
    finally:
        if send_path and is_temp:
            try:
                os.unlink(send_path)
            except OSError:
                pass


def main():
    try:
        test_helpers()
        test_group_pure_meme()
        test_group_vision_gate()
        test_gif_transparency()
    finally:
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)
    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
