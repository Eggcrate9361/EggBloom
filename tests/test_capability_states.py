# -*- coding: utf-8 -*-
"""能力清单三态（2026-10-27 · 1.5.9 §A）

## 为什么

`_capability_block` 原来只列「这轮真有的工具」，并在同一句里要求模型
「名单外的一律说干不了」。模型照做，于是「插件没开」「群白名单外」
全被答成「我没有这个能力」。

## 契约

1. 全量工具里：本轮给了 → ok；没给 → need_enable（措辞是"没挂上"，不是"不会"）
2. 插件未启用 → need_enable，且必须说清"让主人去插件页打开 xxx"
3. 群管理指令：主人 + 我是管理 → ok；缺任一条件 → need_admin
4. bot 在群里的角色查不到时，不许兜底成"普通成员"（宁缺勿编）
5. 动词表只有一个权威源：`capabilities_view.ADMIN_VERBS` 必须与
   `_handle_group_admin_command` 的正则逐一对应

运行：python tests/test_capability_states.py
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import capabilities_view as cv

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


def _snap(**kw):
    base = dict(
        offered_tools=["search_web"],
        all_tools=["search_web", "get_group_members"],
        plugins={"capabilities": False, "qzone": False},
        is_group=True,
        bot_role="member",
        sender_is_admin=False,
    )
    base.update(kw)
    return cv.build_snapshot(**base)


def test_tool_states():
    s = _snap()
    ok_names = {i["name"] for i in s[cv.STATE_OK]}
    need = {i["name"] for i in s[cv.STATE_NEED_ENABLE]}
    check("本轮给了的工具进 ok", ok_names == {"search_web"}, str(ok_names))
    check("全量里有、本轮没给的进 need_enable", "get_group_members" in need, str(need))
    how = [i.get("how", "") for i in s[cv.STATE_NEED_ENABLE]
           if i["name"] == "get_group_members"]
    check("need_enable 的措辞是没挂上，不是不会",
          bool(how) and "没挂上" in how[0], str(how))


def test_plugin_disabled():
    s = _snap()
    rows = {i["name"]: i for i in s[cv.STATE_NEED_ENABLE]}
    check("未启用插件进 need_enable", "capabilities" in rows and "qzone" in rows,
          str(list(rows)))
    how = rows.get("capabilities", {}).get("how", "")
    check("说清谁能解决（插件页 / 主人）",
          "插件没开" in how and "打开 capabilities" in how, how)
    check("插件条目带上人话", "群待办" in rows.get("capabilities", {}).get("label", ""),
          rows.get("capabilities", {}).get("label", ""))


def test_plugin_enabled():
    s = _snap(plugins={"capabilities": True, "qzone": False})
    ok_names = {i["name"] for i in s[cv.STATE_OK]}
    check("已启用插件进 ok", "capabilities" in ok_names, str(ok_names))


def test_admin_states():
    s = _snap(sender_is_admin=True, bot_role="admin")
    ok_names = {i["name"] for i in s[cv.STATE_OK]}
    check("主人 + 群管理 → 管理指令可执行", "kick" in ok_names and "ban" in ok_names,
          str(ok_names))
    s = _snap(sender_is_admin=True, bot_role="member")
    need = {i["name"]: i for i in s[cv.STATE_NEED_ADMIN]}
    check("不是群管理 → need_admin", "kick" in need, str(list(need)))
    check("说清缺口是我不是管理", "不是群管理" in need.get("kick", {}).get("how", ""),
          need.get("kick", {}).get("how", ""))
    s = _snap(sender_is_admin=False, bot_role="admin")
    need = {i["name"]: i for i in s[cv.STATE_NEED_ADMIN]}
    check("非主人 → need_admin", "kick" in need, str(list(need)))
    check("说清缺口是要主人发话",
          "主人发话" in need.get("kick", {}).get("how", ""),
          need.get("kick", {}).get("how", ""))
    s = _snap(is_group=False)
    all_names = {i["name"] for k in s for i in s[k]}
    check("私聊不列群管理指令", "kick" not in all_names, str(all_names))


def test_role_unknown():
    s = _snap(bot_role="")
    joined = "\n".join(cv.render(s, is_group=True, bot_role=""))
    check("角色查不到时不编身份", "我在这个群的身份" not in joined, joined)
    s2 = _snap(bot_role="admin")
    joined2 = "\n".join(cv.render(s2, is_group=True, bot_role="admin"))
    check("角色已知时给出事实依据", "管理员" in joined2 and "禁言" in joined2, joined2)


def test_render_guard():
    s = _snap()
    joined = "\n".join(cv.render(s, is_group=True, bot_role="member"))
    check("给出「清单外才是真没有」的判据", "清单里没提到的" in joined, joined)
    check("明确禁止说成没能力", "别说成「我没有这个能力」" in joined, joined)
    s_ok = _snap(offered_tools=["search_web", "get_group_members"],
                 plugins={"capabilities": True, "qzone": True},
                 sender_is_admin=True, bot_role="owner")
    joined_ok = "\n".join(cv.render(s_ok, is_group=True, bot_role="owner"))
    check("全都能做时不出现自我否认提示",
          "我没有这个能力" not in joined_ok, joined_ok)


def test_admin_verbs_single_source():
    """动词表与 bot_core 的正则必须同源，且长词优先（踢人 不被 踢 吃掉）。"""
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("bot_core 用统一正则", "_ADMIN_COMMAND_RE" in src)
    check("正则由 ADMIN_VERBS 生成", "capabilities_view.ADMIN_VERBS" in src)
    alts = sorted((re.escape(v) for v in cv.ADMIN_VERBS), key=len, reverse=True)
    pat = re.compile(r'^(' + '|'.join(alts) + r')(.*)$')
    for v in cv.ADMIN_VERBS:
        m = pat.match(v + " 10001")
        check(f"动词「{v}」匹配自身", bool(m) and m.group(1) == v,
              m.group(1) if m else "no match")


def test_admin_receipt():
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("回执开关存在", "admin_command_receipt" in src)
    check("失败要如实报 retcode", "没办成，驱动返回 retcode=" in src)
    check("拒绝口径改成要主人发话，不再说权限不足",
          "这得主人发话" in src and "你没有权限执行这个操作哦" not in src)
    check("退群后不回执", "退群后已不在群里" in src)


def test_role_cache_present():
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("有群角色缓存表", "_BOT_ROLE_CACHE" in src)
    check("缓存时长走配置键", "group_role_cache_sec" in src)
    check("查角色有短超时（不拖住 prompt 构建）",
          "get_group_member_info" in src and "timeout=3" in src)


def test_plugin_tools_wired():
    """A.4：主链必须有 plugin_list / plugin_call，且常驻不裁剪。"""
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("主链注册了 plugin_list 函数体", 'TOOL_FUNCTIONS["plugin_list"]' in src)
    check("主链注册了 plugin_call 函数体", 'TOOL_FUNCTIONS["plugin_call"]' in src)
    check("两个 schema 常驻注入工具集", "_PLUGIN_TOOL_SCHEMAS" in src and "插件工具常驻" in src)
    check("主链调用不阻塞等授权", "interactive=False" in src)
    check("全量清单包含这两个工具", "for sch in _PLUGIN_TOOL_SCHEMAS" in src)


def main():
    print("=" * 58)
    print("能力清单三态")
    print("=" * 58)
    for fn in (test_tool_states, test_plugin_disabled, test_plugin_enabled,
               test_admin_states, test_role_unknown, test_render_guard,
               test_admin_verbs_single_source, test_admin_receipt,
               test_role_cache_present, test_plugin_tools_wired):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
