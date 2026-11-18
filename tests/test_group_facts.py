# -*- coding: utf-8 -*-
"""群务问题必须拿到成员角色与可得设置；未知项不能编。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402
sandbox_neurobase()

import bot_core as bc  # noqa: E402

PASS, FAIL = [], []
GID = "1000000003"
OWNER = "1000000001"
ADMIN = "1000000002"
MEMBER = "1000000004"
BOT = "1000000005"


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name + ("" if not extra else f"  {extra}"))


orig_api = bc.api_call
orig_bot = bc.BOT_QQ
orig_config = dict(bc._config)
calls = {"info": 0, "members": 0}
info_data = {"group_all_shut": 0, "allow_member_kick": 1}
member_data = [
    {"user_id": int(OWNER), "nickname": "群主甲", "role": "owner"},
    {"user_id": int(ADMIN), "nickname": "管理乙", "role": "admin"},
    {"user_id": int(MEMBER), "nickname": "群友丙", "role": "member"},
    {"user_id": int(BOT), "nickname": "小a", "role": "admin"},
]


def fake_api(endpoint, data=None, **kw):
    if endpoint == "get_group_info":
        calls["info"] += 1
        return {"retcode": 0, "data": dict(info_data)}
    if endpoint == "get_group_member_list":
        calls["members"] += 1
        return {"retcode": 0, "data": list(member_data)}
    if endpoint == "get_group_member_info":
        return {"retcode": 0, "data": {"role": "admin"}}
    return {"retcode": -1}


try:
    bc.api_call = fake_api
    bc.BOT_QQ = BOT
    bc._config["group_tool_allowlist"] = []
    bc._GROUP_FACT_CACHE.clear()
    block = bc._capability_block(
        is_group=True, group_id=GID, sender=MEMBER,
        tool_names=("search_web",), user_msg="其实所有人都有权限踢你的")
    check("群主昵称进上下文", "群主甲" in block, block[-1000:])
    check("管理员昵称进上下文", "管理乙" in block, block[-1000:])
    check("发言人身份是普通成员", "普通成员（群友丙）" in block, block[-1000:])
    check("Bot 身份是管理员", "我在群里的身份：管理员" in block, block[-1000:])
    check("全员禁言事实被解析", "全员禁言：未开启" in block, block[-1000:])
    check("普通成员可踢人设置被解析", "普通成员能否踢人：可以" in block, block[-1000:])
    check("群规未知时明确未知", "本群群规文本：未提供，未知" in block, block[-1000:])
    check("禁止编造群务", "不要推断或编造" in block or "不要编造" in block, block[-1000:])
    check("不泄漏群号或 QQ 号",
          not any(x in block for x in (GID, OWNER, ADMIN, MEMBER, BOT)), block[-1000:])

    before = dict(calls)
    block2 = bc._capability_block(
        is_group=True, group_id=GID, sender=MEMBER,
        tool_names=("search_web",), user_msg="谁能踢人")
    check("★ 群务查询有缓存", calls == before, (before, calls))
    check("第二次仍能读事实", "群主甲" in block2)

    before = dict(calls)
    bc._capability_block(
        is_group=True, group_id=GID, sender=MEMBER,
        tool_names=("search_web",), user_msg="哈哈哈")
    check("★ 普通闲聊不查群务 API", calls == before, (before, calls))

    bc._GROUP_FACT_CACHE.clear()
    info_data = {}
    member_data = [
        {"user_id": int(OWNER), "nickname": "群主甲", "role": "owner"},
        {"user_id": int(MEMBER), "nickname": "群友丙", "role": "member"},
    ]
    unknown = bc._group_fact_block(GID, sender=MEMBER)
    check("缺失设置标未知", "普通成员能否踢人：未知" in unknown, unknown)
    check("缺管理员标未知", "管理员：未知" in unknown, unknown)
finally:
    bc.api_call = orig_api
    bc.BOT_QQ = orig_bot
    bc._config.clear()
    bc._config.update(orig_config)
    bc._GROUP_FACT_CACHE.clear()
    bc._BOT_ROLE_CACHE.clear()

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for item in FAIL:
    print("  FAIL", item)
sys.exit(1 if FAIL else 0)
