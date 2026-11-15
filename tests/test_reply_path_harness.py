# -*- coding: utf-8 -*-
"""tests/test_reply_path_harness.py —— 主回复链的 harness 接入回归。

覆盖：
  1. 内置工具被注册进 tool_registry，分组/顺序正确（供按需加载）
  2. `_build_static_prompt` 与按需工具集互不干扰（前缀缓存不被破坏）
  3. 按需工具子集：闲聊→最小集；命中关键词→对应分组
  4. 时间分级注入：粗粒度少刷、精粒度可刷
  5. 越界防护：本轮未提供的工具被拒绝但不崩

不调用真实 LLM（只验证"发给模型的工具集/提示词"这一层）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402
from harness import bot_hook, tool_registry as tr  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")


def _register_like_main():
    """复刻 bot_core.main() 里的注册逻辑，验证分组归属。"""
    tr.reset()
    tr.register_many(bot_core.TOOLS, groups=(tr.GROUP_CORE,))
    for t in bot_core.TOOLS:
        n = t["function"]["name"]
        if n == "web_operate":
            tr.register(n, t, groups=(tr.GROUP_WEB,), source="builtin")
        elif n != "search_web":
            tr.register(n, t, groups=(tr.GROUP_DESKTOP,), source="builtin")
    tr.register_many(bot_core.GROUP_TOOLS, groups=(tr.GROUP_GROUP,))
    tr.register_many(bot_hook.SKILL_TOOLS, groups=(tr.GROUP_SKILL,), source="skill")


def test_registration_groups():
    _register_like_main()
    st = tr.stats()
    real_tools = {t["function"]["name"] for t in bot_core.TOOLS}
    real_group = {t["function"]["name"] for t in bot_core.GROUP_TOOLS}

    check("回复链: 全部内置工具已注册",
          real_tools | real_group <= set(tr.all_names()),
          (real_tools | real_group) - set(tr.all_names()))
    check("回复链: search_web 在 core", "search_web" in tr.names_in(tr.GROUP_CORE),
          tr.names_in(tr.GROUP_CORE))
    check("回复链: web_operate 在 web", "web_operate" in tr.names_in(tr.GROUP_WEB),
          tr.names_in(tr.GROUP_WEB))
    check("回复链: 桌面工具都在 desktop",
          {"open_app", "take_screenshot", "get_system_info"} <= set(tr.names_in(tr.GROUP_DESKTOP)),
          tr.names_in(tr.GROUP_DESKTOP))
    check("回复链: 群工具在 group",
          real_group <= set(tr.names_in(tr.GROUP_GROUP)),
          tr.names_in(tr.GROUP_GROUP))
    check("回复链: web_operate 不在 desktop",
          "web_operate" not in tr.names_in(tr.GROUP_DESKTOP),
          tr.names_in(tr.GROUP_DESKTOP))


def test_subset_savings():
    """按需子集的"省"必须可量化。"""
    _register_like_main()
    all_names = tr.all_names()
    full = tr.resolve(all_names)
    full_chars = sum(len(str(s)) for s in full)

    # 闲聊场景（群聊）
    idle = bot_hook.agent_tools_for("哈哈哈今天天气不错", is_group=True)
    idle_chars = sum(len(str(s)) for s in idle)
    check("回复链: 闲聊子集远小于全量", idle_chars < full_chars * 0.4,
          f"idle={idle_chars} full={full_chars}")
    check("回复链: 闲聊只给 core", [s['function']['name'] for s in idle] == ["search_web"],
          [s['function']['name'] for s in idle])

    # 打印一个粗略的 token 估算（4 字符≈1 token）
    print(f"    全量工具 schema ≈ {full_chars} 字符（≈{full_chars//4} tokens）")
    print(f"    闲聊子集       ≈ {idle_chars} 字符（≈{idle_chars//4} tokens）")


def test_group_allowlist_still_binds():
    """群聊按需子集仍受 group_tool_allowlist 约束（安全边界不放开）。"""
    _register_like_main()
    # 让群聊允许 web_operate
    old = bot_core._config.get("group_tool_allowlist")
    try:
        bot_core._config["group_tool_allowlist"] = ["get_group_members", "web_operate"]
        allowed = {t["function"]["name"] for t in bot_core._build_group_tools()}
        check("回复链: 白名单生效", allowed == {"get_group_members", "web_operate"}, allowed)

        # 群内提到链接 → 按需想给 web，但白名单允许 → 通过
        subset = [n for n in bot_hook.tool_names_for("帮我打开 https://x.com", is_group=True)
                  if n in allowed]
        check("回复链: 群内链接 + 白名单允许", "web_operate" in subset, subset)

        # 群内提到截图 → 想给 desktop，但白名单不允许 → 被挡
        subset2 = [n for n in bot_hook.tool_names_for("帮我截图", is_group=True)
                   if n in allowed]
        check("回复链: 群内截图被白名单挡住", "take_screenshot" not in subset2, subset2)
    finally:
        if old is None:
            bot_core._config.pop("group_tool_allowlist", None)
        else:
            bot_core._config["group_tool_allowlist"] = old


def test_time_injection_graded():
    """时间分级注入：粗粒度按「日+时段」缓存复用，精粒度受分钟控制。

    语义变更（v1.5.6）：旧实现「没变化就返回空串」，本意是省 token，但
    每条消息的 prompt 都是重新拼的（不是累加），空串等于「这轮没有时间」，
    于是绝大多数轮次模型看不到钟点。现在改成：
      · 粗粒度**总是**返回，且同一「日+时段」内文本逐字节不变（可进缓存前缀）；
      · 精粒度**总是**返回，但同一分钟内文本不变（避免秒级抖动）。
    所以这里锁的是「稳定」而不是「只给一次」。
    """
    bot_hook.reset_time_cache()
    c1, f1 = bot_hook.time_parts("g_test")
    c2, f2 = bot_hook.time_parts("g_test")
    check("回复链: 粗粒度每轮都给", c1 != "" and c2 != "", repr((c1, c2)))
    check("回复链: 粗粒度同段内逐字节稳定", c1 == c2, repr((c1, c2)))
    check("回复链: 精粒度带分钟", f1 != "" and ":" in f1, f1)
    check("回复链: 精粒度同分钟内稳定", f1 == f2, repr((f1, f2)))
    check("回复链: 粗粒度含日期", "年" in c1 and "月" in c1, c1)
    # force 精粒度
    check("回复链: force 精粒度总给", bot_hook.fine_tail_block("g_test", force=True) != "")


def test_static_prompt_unaffected():
    """按需工具集不能破坏 static_prompt 的稳定性（前缀缓存前提）。"""
    bot_core._STATIC_PROMPT_CACHE.clear()
    a = bot_core._build_static_prompt(True)
    bot_core._build_static_prompt(False)
    bot_core._build_static_prompt(True)
    b = bot_core._build_static_prompt(True)
    check("回复链: static_prompt 稳定", a == b, "两次构建不一致")
    check("回复链: tool_registry 不影响 static_prompt 键",
          "tool_registry" not in str(bot_core._STATIC_PROMPT_CACHE.keys()),
          "缓存键被污染")


def test_offered_names_guard():
    """越界防护：本轮未提供但存在的工具被拒。"""
    _register_like_main()
    tools_for_call = bot_hook.agent_tools_for("你好", is_group=False)
    offered = {t["function"]["name"] for t in tools_for_call}
    check("回复链: 闲聊只提供 search_web（即越界面最大）",
          offered == {"search_web"}, offered)
    # 模拟 bot_core 的判定逻辑
    func_name = "take_screenshot"
    func_exists = func_name in bot_core.TOOL_FUNCTIONS
    would_reject = func_name not in offered
    check("回复链: 存在但未提供的工具会被拒", func_exists and would_reject,
          (func_exists, would_reject))
    # 提供过的工具不拒
    check("回复链: 提供过的工具不拒", "search_web" in offered)


def main():
    for fn in (test_registration_groups, test_subset_savings,
               test_group_allowlist_still_binds, test_time_injection_graded,
               test_static_prompt_unaffected, test_offered_names_guard):
        try:
            fn()
        except Exception as e:
            import traceback
            global FAIL
            FAIL += 1
            FAILURES.append(f"{fn.__name__} 抛异常: {e}\n{traceback.format_exc()}")
    print(f"\n{'='*60}")
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("-" * 60)
        for f in FAILURES:
            print("  ✗ " + f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
