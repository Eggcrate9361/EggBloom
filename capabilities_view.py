# -*- coding: utf-8 -*-
"""能力清单：把「我能做什么」从「这轮给了我什么」里拆开。

问题（docs/FIXPLAN_1.5.9.md §A.2）：

    `_capability_block` 只拿得到按需裁剪后的工具名，并在同一句里要求模型
    「名单外的直接说干不了」。模型照做，于是「插件没开」「白名单外」全被
    答成「我没有这个能力」——它没有撒谎，它是在执行指令。

本模块只做合并与渲染，不 import bot_core，也不做 IO。原料由调用方传入，
状态判定只有四种：

    ok           现在就能做
    need_admin   能力在，但需要主人发话 / 需要我是群管理员
    need_enable  能力在，但这轮没挂上工具，或插件没启用
    absent       真没有

「能不能」和「想不想」是两条轴：本模块只管前者。后者见人格【干活的时候】
与 bot_core 的意愿参数，别在这里混进去。
"""

from typing import Any, Dict, Iterable, List, Sequence

STATE_OK = "ok"
STATE_NEED_ADMIN = "need_admin"
STATE_NEED_ENABLE = "need_enable"
STATE_ABSENT = "absent"

#: 工具名 → 人话。bot_core 的旧 _desc 搬到这里，避免两处各写一份。
TOOL_LABELS = {
    "search_web": "联网搜索",
    "web_operate": "真实操作网页（打开/点击/填表）",
    "take_screenshot": "截本机屏幕并发过去",
    "search_files": "在本机找文件",
    "send_user_file": "把本机文件发过去",
    "get_group_members": "读群成员名单",
    "generate_image": "生成图片",
    "open_app": "打开本机应用",
    "get_system_info": "读 CPU/内存等状态",
    "control_volume": "调音量",
    "control_power": "关机/重启/锁屏",
    "manage_process": "查/杀进程",
    "plugin_list": "列出已装插件与开关状态",
    "plugin_call": "调用插件动作（未启用会弹授权）",
}

#: 群管理指令：(键, 动词, 人话)。
#: 动词表必须与 `_handle_group_admin_command` 的正则一致；
#: `tests/test_capability_view.py` 会逐条比对，改一处漏另一处会红。
ADMIN_COMMANDS: Sequence[tuple] = (
    ("kick", ("踢", "踢出", "踢人"), "踢人"),
    ("ban", ("禁言",), "禁言"),
    ("unban", ("解除禁言", "解禁"), "解除禁言"),
    ("set_admin", ("设管理", "设管理员"), "设群管理"),
    ("del_admin", ("撤管理", "撤销管理"), "撤群管理"),
    ("card", ("改名片",), "改群名片"),
    ("group_name", ("改群名",), "改群名"),
    ("leave", ("退群",), "退群"),
)

#: 扁平动词表，供 bot_core 拼正则
ADMIN_VERBS = tuple(v for _, verbs, _ in ADMIN_COMMANDS for v in verbs)

#: 插件能力：(插件名, 人话)。只列「用户会主动要求、但默认没开」的。
#: 动作本身不在这里展开——那是 plugin_list 的活，写进 prompt 只会变长。
PLUGIN_CAPABILITIES: Sequence[tuple] = (
    ("capabilities", "群待办、群相册、改签名、改在线状态"),
    ("qzone", "发 QQ 空间"),
)

ROLE_LABEL = {
    "owner": "群主",
    "admin": "管理员",
    "member": "普通成员",
}


def build_snapshot(
    *,
    offered_tools: Iterable[str] = (),
    all_tools: Iterable[str] = (),
    blocked_tools: Iterable[str] = (),
    plugins: Dict[str, bool] | None = None,
    is_group: bool = False,
    bot_role: str = "",
    sender_is_admin: bool = False,
    admin_enabled: bool = True,
    show_disabled: bool = True,
) -> Dict[str, List[Dict[str, str]]]:
    """合并三处来源，返回按状态分组的能力清单。

    plugins: {插件名: 是否启用}。缺省视为未启用（宁可说"没开"，也不要说"能"）。
    blocked_tools: 被群白名单挡在外面的工具名。与"按需加载没挂上"分开说，
                   因为前者用户改配置才能解决，后者下一轮自己会好。
    """
    snap: Dict[str, List[Dict[str, str]]] = {
        STATE_OK: [], STATE_NEED_ENABLE: [], STATE_NEED_ADMIN: [], STATE_ABSENT: [],
    }
    offered = {str(n) for n in (offered_tools or ()) if n}
    blocked = {str(n) for n in (blocked_tools or ()) if n}
    role_ok = bot_role in ("owner", "admin")

    # ── 主链工具 ──
    for name in (all_tools or ()):
        name = str(name or "")
        if not name:
            continue
        item = {"name": name, "label": TOOL_LABELS.get(name, ""), "kind": "tool"}
        if name in offered:
            snap[STATE_OK].append(item)
        else:
            item["how"] = ("群白名单没放它，让主人加进 group_tool_allowlist"
                           if name in blocked
                           else "这轮没挂上（按需加载），要的话直接说")
            snap[STATE_NEED_ENABLE].append(item)

    # ── 群管理指令：代码执行，不占工具位 ──
    if is_group and admin_enabled:
        for key, _verbs, label in ADMIN_COMMANDS:
            item = {"name": key, "label": label, "kind": "admin"}
            if sender_is_admin and role_ok:
                snap[STATE_OK].append(item)
            elif sender_is_admin and not role_ok:
                item["how"] = "我不是群管理，办不了；让群主或管理员来"
                snap[STATE_NEED_ADMIN].append(item)
            elif not sender_is_admin and role_ok:
                item["how"] = "能力有，但要主人发话"
                snap[STATE_NEED_ADMIN].append(item)
            else:
                item["how"] = "要主人发话，并且我得是群管理"
                snap[STATE_NEED_ADMIN].append(item)

    # ── 插件能力 ──
    if show_disabled:
        for pname, label in PLUGIN_CAPABILITIES:
            on = bool((plugins or {}).get(pname, False))
            item = {"name": pname, "label": label, "kind": "plugin"}
            if on:
                snap[STATE_OK].append(item)
            else:
                item["how"] = "插件没开，让主人去「插件」页打开 " + pname
                snap[STATE_NEED_ENABLE].append(item)
    else:
        for pname, label in PLUGIN_CAPABILITIES:
            if (plugins or {}).get(pname, False):
                snap[STATE_OK].append({"name": pname, "label": label, "kind": "plugin"})

    return snap


def render(
    snap: Dict[str, List[Dict[str, str]]],
    *,
    is_group: bool = False,
    bot_role: str = "",
) -> List[str]:
    """把清单渲染成若干行（不含标题）。空清单返回空列表。"""
    ok = snap.get(STATE_OK) or []
    need_enable = snap.get(STATE_NEED_ENABLE) or []
    need_admin = snap.get(STATE_NEED_ADMIN) or []

    def pick(items, kind):
        return [i for i in items if i.get("kind") == kind]

    lines: List[str] = []

    tools_ok = pick(ok, "tool")
    if tools_ok:
        lines.append("这轮真能用的工具：" + _fmt(tools_ok) + "。")
    plugins_ok = pick(ok, "plugin")
    if plugins_ok:
        lines.append("已启用的插件能力："
                     + "、".join(i.get("label") or i.get("name") for i in plugins_ok) + "。")
    admin_ok = pick(ok, "admin")
    if admin_ok:
        lines.append("群里直接对我说就能执行（不占工具位）："
                     + "、".join(i.get("label") or i.get("name") for i in admin_ok) + "。")

    tools_need = pick(need_enable, "tool")
    if tools_need:
        lines.append("这些工具这轮没挂上（按需加载或群白名单），要的话直接说，"
                     "**别说成「我没有这个能力」**："
                     + "、".join(i.get("label") or i.get("name") for i in tools_need) + "。")
    plugins_need = pick(need_enable, "plugin")
    if plugins_need:
        lines.append("这些能力有，只是插件没开（**同样不是「没能力」**）："
                     + _group_how(plugins_need) + "。")
    admin_need = pick(need_admin, "admin")
    if admin_need:
        lines.append("这些是管理指令，能力有、但要满足条件："
                     + _group_how(admin_need) + "。")

    if is_group:
        label = ROLE_LABEL.get(bot_role)
        if label:
            extra = ""
            if bot_role in ("owner", "admin"):
                extra = "，踢人/禁言/改名片/改群名我都能执行"
            lines.append(f"我在这个群的身份：{label}{extra}。")

    if need_enable or need_admin:
        # 判据给死：只有清单里没提到的，才允许说「干不了」。
        lines.append("清单里没提到的，才是真没有——那时候才可以说「这个我干不了」。")

    if not lines:
        return []
    return lines


def _fmt(items: List[Dict[str, str]]) -> str:
    out = []
    for it in items:
        label = it.get("label") or ""
        name = it.get("name") or ""
        out.append(f"{name}（{label}）" if label else name)
    return "、".join(out)


def _group_how(items: List[Dict[str, str]]) -> str:
    """同一句 how 的合成一组，避免重复二十遍「这轮没挂上」。"""
    buckets: Dict[str, List[str]] = {}
    for it in items:
        how = it.get("how") or ""
        buckets.setdefault(how, []).append(it.get("label") or it.get("name") or "")
    parts = []
    for how, labels in buckets.items():
        joined = "、".join(x for x in labels if x)
        parts.append(f"{joined} —— {how}" if how else joined)
    return "；".join(parts)


__all__ = [
    "STATE_OK", "STATE_NEED_ADMIN", "STATE_NEED_ENABLE", "STATE_ABSENT",
    "TOOL_LABELS", "ADMIN_COMMANDS", "ADMIN_VERBS", "PLUGIN_CAPABILITIES",
    "ROLE_LABEL", "build_snapshot", "render",
]
