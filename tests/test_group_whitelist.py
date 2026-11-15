# -*- coding: utf-8 -*-
"""P0-2 单测：群聊白名单。

缺陷（手册 §1.3(7) 已记录、未修）：
  群分支原先只查逐群 `reply_enabled`（默认 True），**没有任何"只在这些群工作"的开关**。
  适配器转发什么群，Bot 就在什么群说话 —— 被拉进陌生群会立刻开口。

本次新增 group_whitelist。核心契约是「**空白名单 = 不限制**」，
这样升级不会让现网正在工作的群突然静默（那是破坏性变更）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


_saved_wl = bot_core._config.get("group_whitelist", "<absent>")
_saved_gr = bot_core._config.get("group_restrictions", None)


def set_wl(v):
    if v is None:
        bot_core._config.pop("group_whitelist", None)
    else:
        bot_core._config["group_whitelist"] = v


def set_gr(v):
    if v is None:
        bot_core._config.pop("group_restrictions", None)
    else:
        bot_core._config["group_restrictions"] = v


try:
    print("=== A. 空白名单 = 不限制（升级不改变现网行为）===")
    for desc, val in (("未设置(None)", None), ("空列表", []), ("空串", "")):
        set_wl(val)
        set_gr(None)
        check(f"★ {desc} → 任意群都允许",
              bot_core._group_allowed("111") and bot_core._group_allowed("999999"),
              desc)
    set_wl(None)
    check("★ 空白名单时 block_reason 为 None", bot_core._group_block_reason("111") is None)

    print("\n=== B. 非空白名单 = 只有列出的群允许 ===")
    set_wl(["111", "222"])
    check("★ 列出的群 111 → 允许", bot_core._group_allowed("111"))
    check("★ 列出的群 222 → 允许", bot_core._group_allowed("222"))
    check("★ 未列出的群 333 → 拒绝", not bot_core._group_allowed("333"))
    check("★ 拒绝原因含『白名单』", "白名单" in (bot_core._group_block_reason("333") or ""),
          bot_core._group_block_reason("333"))
    check("数字与字符串群号等价（int 111）", bot_core._group_allowed(111))
    check("带空格的群号被规整（' 111 '）", bot_core._group_allowed(" 111 "))

    print("\n=== C. 字符串写法（配置里常被写成 '111,222'）===")
    set_wl("111, 222;333，444 555")
    for g in ("111", "222", "333", "444", "555"):
        check(f"字符串写法解析出 {g}", bot_core._group_allowed(g))
    check("字符串写法下未列出群仍拒绝", not bot_core._group_allowed("666"))
    set_wl(["", "  ", None])
    check("全是空项 → 视为空白名单（不限制）", bot_core._group_allowed("any"))

    print("\n=== D. 逐群开关优先级（语义与从前一致）===")
    set_wl(["111", "222"])
    set_gr({"222": {"reply_enabled": False}})
    check("★ 在白名单内但 reply_enabled=False → 仍拒绝",
          not bot_core._group_allowed("222"))
    check("★ 拒绝原因指向 reply_enabled",
          "reply_enabled" in (bot_core._group_block_reason("222") or ""),
          bot_core._group_block_reason("222"))
    check("白名单内且未关闭 → 允许", bot_core._group_allowed("111"))
    set_wl(None)
    check("★ 空白名单时 reply_enabled=False 依旧拒绝（原有行为不变）",
          not bot_core._group_allowed("222"))
    check("空白名单时其他群不受影响", bot_core._group_allowed("111"))

    print("\n=== E. 脏配置不炸（绝不向主循环抛异常）===")
    for bad in (123, {"a": 1}, [None, {}, 1], object()):
        try:
            set_wl(bad)
            r = bot_core._group_allowed("111")
            ok = isinstance(r, bool)
        except Exception as e:
            ok = False
            r = f"{type(e).__name__}: {e}"
        check(f"脏配置 {type(bad).__name__} → 返回 bool 不抛异常", ok, r)

    print("\n=== F. 结构性证据：四个发言出口都接了白名单 ===")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("存在 _group_whitelist / _group_block_reason / _group_allowed",
          all(f"def {n}(" in src for n in ("_group_whitelist", "_group_block_reason", "_group_allowed")))
    check("★ 群消息主入口用 _group_allowed 把关",
          bool(re.search(r"if not _group_allowed\(group_id\):", src)))
    check("★ 插件 on_message 回复路径也把关",
          bool(re.search(r"if _group_allowed\(_pid_gid\):", src)))
    check("★ 空闲退场：会话扫描按白名单跳过并清会话",
          "GROUP_SESSION.pop(gid, None)" in src and "if not _group_allowed(gid):" in src)
    check("★ 空闲退场：无会话扫描也把关", src.count("if not _group_allowed(gid):") >= 2,
          src.count("if not _group_allowed(gid):"))
    _old_gates = [
        'if _group_restriction(group_id, "reply_enabled", True) is False:',
        'if _group_restriction(msg.get("_group_id"), "reply_enabled", True) is not False:',
    ]
    check("★ 两个旧的裸 reply_enabled 发言闸门已全部替换",
          all(g not in src for g in _old_gates),
          [g for g in _old_gates if g in src])
    check("_group_allowed 的调用点 ≥ 4（主入口/插件回复/两处退场扫描）",
          src.count("_group_allowed(") >= 4, src.count("_group_allowed("))
    check("配置默认值已登记 group_whitelist",
          "group_whitelist" in open(os.path.join(ROOT, "config.py"), encoding="utf-8").read())
    check("群列表 API 暴露 whitelisted / block_reason",
          "ginfo[\"whitelisted\"]" in src and "ginfo[\"block_reason\"]" in src)

    print("\n=== G. 提示有冷却（不刷屏）===")
    check("提示冷却常量存在且 > 0",
          getattr(bot_core, "_GROUP_BLOCK_NOTICE_COOLDOWN", 0) > 0,
          getattr(bot_core, "_GROUP_BLOCK_NOTICE_COOLDOWN", None))
    bot_core._GROUP_BLOCK_NOTICE.clear()
    set_wl(["111"])
    bot_core._notice_group_blocked("333", "测试")
    first = bot_core._GROUP_BLOCK_NOTICE.get("333")
    bot_core._notice_group_blocked("333", "测试")
    check("★ 冷却期内不重复记录", bot_core._GROUP_BLOCK_NOTICE.get("333") == first)
finally:
    set_wl(None if _saved_wl == "<absent>" else _saved_wl)
    set_gr(_saved_gr)
    bot_core._GROUP_BLOCK_NOTICE.clear()

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
