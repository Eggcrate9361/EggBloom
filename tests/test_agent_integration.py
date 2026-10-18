# -*- coding: utf-8 -*-
"""tests/test_agent_integration.py —— Agent 端到端集成（注册表 → 服务层 → 循环 → 回放）。

不启动 bot_core 主循环，只核对"接线是否真的连上、按需工具是否真的生效"。
运行：python tests/test_agent_integration.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from harness import tool_registry as tr, agent_service, agent_loop as al, bot_hook, session_log

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


def _sch(name):
    return {"type": "function", "function": {
        "name": name, "description": "d",
        "parameters": {"type": "object", "properties": {}, "required": []}}}


# 模拟 bot_core 的注册接线（与 main() 里的逻辑保持一致）
def _simulate_bot_core_registration():
    tr.reset()
    TOOLS = [_sch("search_web"), _sch("open_app"), _sch("take_screenshot"),
             _sch("get_system_info"), _sch("control_volume"), _sch("control_power"),
             _sch("manage_process"), _sch("search_files"), _sch("send_user_file"),
             _sch("web_operate")]
    GROUP_TOOLS = [_sch("get_group_members"), _sch("generate_image")]
    tr.register_many(TOOLS, groups=(tr.GROUP_CORE,))
    for t in TOOLS:
        n = t["function"]["name"]
        if n == "web_operate":
            tr.register(n, t, groups=(tr.GROUP_WEB,), source="builtin")
        elif n != "search_web":
            tr.register(n, t, groups=(tr.GROUP_DESKTOP,), source="builtin")
    tr.register_many(GROUP_TOOLS, groups=(tr.GROUP_GROUP,))
    tr.register_many(bot_hook.SKILL_TOOLS, groups=(tr.GROUP_SKILL,), source="skill")
    return TOOLS, GROUP_TOOLS


def test_registration_shape():
    TOOLS, GROUP_TOOLS = _simulate_bot_core_registration()
    st = tr.stats()
    check("集成: 注册总数 = 10 + 2 + 2", st["total"] == 14, st["total"])
    check("集成: core 分组=1", st["by_group"].get(tr.GROUP_CORE) == 1, st["by_group"])
    check("集成: desktop 分组=8", st["by_group"].get(tr.GROUP_DESKTOP) == 8, st["by_group"])
    check("集成: web 分组=1", st["by_group"].get(tr.GROUP_WEB) == 1, st["by_group"])
    check("集成: group 分组=2", st["by_group"].get(tr.GROUP_GROUP) == 2, st["by_group"])
    check("集成: skill 分组=2", st["by_group"].get(tr.GROUP_SKILL) == 2, st["by_group"])

    # resolve 顺序：core → group → web → desktop → skill
    names = ([t["function"]["name"] for t in TOOLS]
             + [t["function"]["name"] for t in GROUP_TOOLS]
             + ["skill__load", "skill__read"])
    r = tr.resolve(names)
    seq = [s["function"]["name"] for s in r]
    check("集成: 顺序 core 第一", seq[0] == "search_web", seq)
    check("集成: 顺序 group 第二", seq[1] == "get_group_members", seq)
    # group 分组两项连续
    check("集成: group 两项相邻", seq[1:3] == ["get_group_members", "generate_image"], seq)
    check("集成: 顺序 web 在 desktop 前",
          seq.index("web_operate") < seq.index("open_app"), seq)
    check("集成: 顺序 skill 最后", seq[-2:] == ["skill__load", "skill__read"], seq)
    # 两次 resolve 完全一致（前缀缓存前提）
    r2 = tr.resolve(list(reversed(names)))
    check("集成: 顺序完全确定", [s["function"]["name"] for s in r2] == seq,
          [s["function"]["name"] for s in r2])


def test_service_per_scene_tools():
    """服务层：注册表 provider 生效 → 工具 schema 按需给出。"""
    _simulate_bot_core_registration()

    seen_tools = []

    def _chat(messages, tools=None, max_tokens=1500):
        seen_tools.append([t["function"]["name"] for t in (tools or [])
                           if isinstance(t, dict) and "function" in t])
        if len(seen_tools) == 1:
            return ("", [{"id": "c1", "function": {"name": "search_web",
                                                  "arguments": '{"query":"x"}'}}], {})
        return ("答案", [], {})

    fns = {"search_web": lambda a: "搜索结果",
           "get_group_members": lambda a: "成员",
           "web_operate": lambda a: "网页"}

    agent_service.configure(
        chat_fn=_chat, tool_functions=fns, logger=lambda m: None,
        static_prompt_fn=lambda is_group: "SYS",
        is_authorized_fn=lambda sender: True,
        tool_schema_provider=lambda names: tr.resolve(names, lazy=True),
    )
    check("服务: is_ready", agent_service.is_ready() is True)

    res = agent_service.run_chat(user_input="帮我搜一下天气", session_id="it-1",
                                 is_group=False)
    check("服务: 跑通", res.get("ok") is True and res.get("answer") == "答案", res)
    check("服务: 工具名齐全（全量 provider）", "search_web" in (seen_tools[0] or []),
          seen_tools[:1])
    check("服务: 两次调用工具集一致", seen_tools[0] == seen_tools[1],
          f"{seen_tools}")

    # 回放（事件溯源）
    tr_events = agent_service.trace_of("it-1")
    types = [e["type"] for e in tr_events]
    check("服务: 轨迹含 user_msg", al.EV_USER in types, types)
    check("服务: 轨迹含 tool_call", al.EV_TOOL_CALL in types, types)
    check("服务: 轨迹含 tool_result", al.EV_TOOL_RESULT in types, types)
    check("服务: 轨迹含 final", al.EV_FINAL in types, types)
    hist = agent_service.history_of("it-1")
    check("服务: 派生历史可回放", any(h["role"] == "user" for h in hist), hist)


def test_skill_tools_work_in_loop():
    """skill__load 在真实 agent 循环里可用。"""
    _simulate_bot_core_registration()
    fns = dict(bot_hook.skill_tool_functions())

    def _chat(messages, tools=None, max_tokens=None):
        if not any(m.get("role") == "tool" for m in messages):
            return ("", [{"id": "s1", "function": {
                "name": "skill__load", "arguments": '{"name":"不存在的技能"}'}}], {})
        return ("技能不可用，我直接回答", [], {})

    res = al.run_agent(
        session_id="it-skill", user_input="用某技能",
        chat_fn=_chat, tool_functions=fns,
        tool_schemas=tr.resolve(["skill__load"]),
        persist=False, logger=lambda m: None,
    )
    check("集成: skill__load 在循环中可调", res.ok, res.to_dict())
    check("集成: skill__load 失败信息回灌模型",
          res.answer == "技能不可用，我直接回答", res.answer)


def test_group_gate_with_registry():
    """群内双闸：权限 + 意图；并通过 provider 只给群安全工具。"""
    _simulate_bot_core_registration()
    agent_service.configure(
        chat_fn=lambda m, t=None, max_tokens=1500: ("群答", [], {}),
        tool_functions={"get_group_members": lambda a: "成员"},
        logger=lambda m: None,
        static_prompt_fn=lambda is_group: "SYS",
        is_authorized_fn=lambda sender: str(sender) == "10001",
        tool_schema_provider=lambda names: tr.resolve(names, lazy=True),
    )
    ok, why = agent_service.group_gate(text="帮我截图", sender="10001", is_group=True)
    check("集成: 管理员+强意图 → 通过", ok is True, why)
    ok2, why2 = agent_service.group_gate(text="哈哈你好", sender="10001", is_group=True)
    check("集成: 管理员+闲聊 → 拒绝", ok2 is False, why2)
    ok3, why3 = agent_service.group_gate(text="帮我截图", sender="99999", is_group=True)
    check("集成: 非管理员 → 拒绝", ok3 is False and "权限" in why3, why3)


def test_no_mcp_no_cost():
    """没有 MCP 配置时零成本（server_names 为空，不抛）。"""
    from harness import mcp_hub
    mcp_hub.reset()
    mcp_hub.set_servers({})
    check("集成: 无 MCP 配置零成本", mcp_hub.server_names() == [], mcp_hub.server_names())
    check("集成: MCP tool_functions 空", mcp_hub.tool_functions() == {}, mcp_hub.tool_functions())


def main():
    for fn in (test_registration_shape, test_service_per_scene_tools,
               test_skill_tools_work_in_loop, test_group_gate_with_registry,
               test_no_mcp_no_cost):
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
