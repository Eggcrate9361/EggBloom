# -*- coding: utf-8 -*-
"""tests/test_tool_result_protocol.py —— 全套工具的成败上报协议

用户 2026-10-13：「应该类似的全套工具都有问题」+ 截图：被问引用图片时回了一句
"引用图片解析失败"（代码里根本没这句话，是模型对着 `[图片]` 占位瞎编的）。

审计结论（改前）：
  · `send_user_file`：包装 `"文件已发送" if handle_user_file(...)` —— 非空字符串恒真，
    连"发送失败"都被报成成功（已在 test_send_user_file.py 锁）；
  · `open_app`：失败文案是"已尝试打开 X"—— 听着像成功；
  · `take_screenshot/get_system_info/control_*/manage_process/search_files`：
    全是 `handler() and "成功" or "失败"`，依赖 handler 返回**严格布尔**，
    一旦有人让它返回字符串/路径就全线假成功；
  · `generate_image`：直接透传，失败串没有统一标记；
  · 图片没读懂时 `build_image_context_block` 返回 ""（静默省略）→ 模型只能瞎编。

现在锁死：
  1. 每个工具：handler 失败 → 返回值必须以 ❌ 开头，且**不含任何成功措辞**；
  2. 每个工具：handler 成功 → 以 ✅ 开头；
  3. 字符串型 handler 的失败分支也带 ❌；
  4. 图片一张都没读懂 → 注入明确的"解析失败 + 不许猜"，而不是静默省略。
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


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


GOOD_WORDS = ("已", "完成", "成功", "已发送", "已打开", "已调整", "已执行")


def looks_like_success(text: str) -> bool:
    """这段返回值是否会被读成'做成了'（❌/⚠️ 开头的不算）。"""
    t = str(text)
    if t.startswith(("❌", "⚠️")):
        return False
    return any(w in t for w in GOOD_WORDS)


def main():
    TF = bot_core.TOOL_FUNCTIONS
    print("=== ① 布尔型工具：失败必须 ❌，且不许听着像成功 ===")
    bool_cases = [
        ("open_app", {"name": "qq"}, "handle_open"),
        ("take_screenshot", {}, "handle_screenshot"),
        ("get_system_info", {}, "handle_sysinfo"),
        ("control_volume", {"action": "up"}, "handle_volume"),
        ("control_power", {"action": "锁屏"}, "handle_power"),
        ("manage_process", {"action": "list"}, "handle_process"),
    ]
    orig = {}
    for tool, args, handler in bool_cases:
        orig[handler] = getattr(bot_core, handler)
    try:
        for tool, args, handler in bool_cases:
            # 失败
            setattr(bot_core, handler, lambda *a, **k: False)
            r_fail = TF[tool](args)
            check(f"{tool} 失败 → ❌ 开头", str(r_fail).startswith("❌"), repr(r_fail)[:70])
            check(f"{tool} 失败 → 不带成功措辞", not looks_like_success(r_fail), repr(r_fail)[:70])
            check(f"{tool} 失败 → 写了具体原因", len(str(r_fail)) > 6, repr(r_fail)[:70])
            # 成功
            setattr(bot_core, handler, lambda *a, **k: True)
            r_ok = TF[tool](args)
            check(f"{tool} 成功 → ✅ 开头", str(r_ok).startswith("✅"), repr(r_ok)[:70])
    finally:
        for handler, fn in orig.items():
            setattr(bot_core, handler, fn)

    print("\n=== ② 字符串型工具的失败分支 ===")
    orig_gen = bot_core.handle_generate_image
    orig_search = bot_core.search_web
    orig_webop = bot_core.handle_web_operate
    try:
        for ret in ("ComfyUI 未连接，请检查远程服务器", "图片生成失败", "图片生成超时，请重试"):
            bot_core.handle_generate_image = lambda *a, _r=ret, **k: _r
            r = TF["generate_image"]({"prompt": "x"})
            check(f"画图失败({ret[:10]}…) → ❌", str(r).startswith("❌"), repr(r)[:70])
            check(f"画图失败({ret[:10]}…) → 不带成功措辞", not looks_like_success(r), repr(r)[:70])
        bot_core.handle_generate_image = lambda *a, **k: "图片已生成: E:/x.png"
        r = TF["generate_image"]({"prompt": "x"})
        check("画图成功 → ✅ 且带路径", str(r).startswith("✅") and "x.png" in str(r), repr(r)[:70])

        for ret in ("", None, "搜索失败：网络错误"):
            bot_core.search_web = lambda *a, _r=ret, **k: _r
            r = TF["search_web"]({"query": "天气"})
            check(f"搜索({ret!r}) → ❌", str(r).startswith("❌"), repr(r)[:70])
        bot_core.search_web = lambda *a, **k: "【某市天气】当前 20°C"
        r = TF["search_web"]({"query": "天气"})
        check("搜索有结果 → 原样透传不加前缀", str(r).startswith("【"), repr(r)[:60])
    finally:
        bot_core.handle_generate_image = orig_gen
        bot_core.search_web = orig_search

    print("\n=== ③ 文件搜索：没找到也要说清 ===")
    orig_hs = bot_core.handle_search
    try:
        bot_core.handle_search = lambda *a, **k: True
        r = TF["search_files"]({"query": "xxx"})
        check("搜索完成 → ✅", str(r).startswith("✅"), repr(r)[:60])
        check("搜索完成 → 告诉模型结果已直接发给用户", "发给用户" in str(r), repr(r)[:80])
    finally:
        bot_core.handle_search = orig_hs

    print("\n=== ④ 图片没读懂：明确说失败，不许静默省略 ===")
    orig_desc = bot_core.describe_image
    try:
        bot_core.describe_image = lambda *a, **k: ""       # 一张都没读懂
        blk = bot_core.build_image_context_block([r"E:\a.jpg", r"E:\b.jpg"], scene="群聊")
        check("★ 全失败也返回内容（不静默省略）", bool(blk), repr(blk)[:60])
        check("★ 明说解析失败", "解析失败" in blk, blk[:120])
        check("★ 告诉模型不许猜", "不许猜" in blk, blk[:120])
        check("★ 提示请用户重发/描述", ("重发" in blk) or ("描述" in blk), blk[:120])
        # 部分成功
        calls = {"n": 0}

        def one_ok(*a, **k):
            calls["n"] += 1
            return "第一张：一只猫" if calls["n"] == 1 else ""
        bot_core.describe_image = one_ok
        blk2 = bot_core.build_image_context_block([r"E:\a.jpg", r"E:\b.jpg"])
        check("部分成功 → 带上成功那张", "一只猫" in blk2, blk2[:120])
        check("部分成功 → 说明其余没读懂", "没读懂" in blk2, blk2[:160])
        # 全部成功
        bot_core.describe_image = lambda *a, **k: "一张图"
        blk3 = bot_core.build_image_context_block([r"E:\a.jpg"])
        check("全部成功 → 正常块", blk3.count("一张图") == 1 and "解析失败" not in blk3, blk3[:100])
        # 空列表
        check("空列表 → 空串", bot_core.build_image_context_block([]) == "")
    finally:
        bot_core.describe_image = orig_desc

    print("\n=== ⑤ 诚实铁律与协议对齐 ===")
    seg = bot_core._build_static_prompt(False)
    check("铁律点名 ❌/⚠️ 不许说成功", ("❌" in seg) and ("⚠️" in seg))
    # 2026-10-31：铁律原文改成了「不许说『发你了』」（人格文件第 69 行），
    # 这里卡死「发过去了」会假红 —— 判据改成"两种说法至少禁掉一种"。
    check("铁律禁说'发过去了/发你了'",
          ("发过去了" in seg) or ("发你了" in seg), seg[:0])

    print("\n" + "=" * 58)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
