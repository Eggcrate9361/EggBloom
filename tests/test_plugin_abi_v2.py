# -*- coding: utf-8 -*-
"""插件 ABI v2 自检：api_call / send_* / 事件总线 / 向后兼容 / 递归保护。

用法（在项目根目录）：
    python tests/test_plugin_abi_v2.py

不需要 pytest —— 直接 python 跑，退出码非零即失败。
不依赖 bot_core：宿主能力用一个假函数注入，因此可在任何环境跑。
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from plugins.abi import PLUGIN_API_VERSION, PluginContext, validate_manifest  # noqa: E402
from plugins.plugin_manager import PluginManager  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


# ══ 1. 版本与向后兼容 ══════════════════════════════════════════
print("=== 1. 版本与向后兼容 ===")
check("PLUGIN_API_VERSION == 2", PLUGIN_API_VERSION == 2, f"={PLUGIN_API_VERSION}")
check("v1 manifest 仍通过校验", validate_manifest({"name": "x", "api_version": 1}) is None)
check("v2 manifest 通过校验", validate_manifest({"name": "x", "api_version": 2}) is None)
check("v3 manifest 被拒（不能超版本）", validate_manifest({"name": "x", "api_version": 3}) is not None)

# ══ 2. 宿主能力注入 ══════════════════════════════════════════
print("\n=== 2. 宿主能力注入 ===")
pm = PluginManager.get_instance()
calls = []


def fake_api(action, params=None):
    calls.append((action, params))
    return {"status": "ok", "retcode": 0, "data": {"action": action}}


caps = pm.set_host_api(api_call=fake_api)
check("set_host_api 返回能力名", "api_call" in caps, str(caps))
check("get_host_api 可见", "api_call" in pm.get_host_api(), str(pm.get_host_api()))

ctx = PluginContext("abi_test", "1.0.0", manager=pm)

# ══ 3. api_call / send_group / send_private ══════════════════
print("\n=== 3. api_call 与消息发送 ===")
r = ctx.api_call("send_qzone_msg", {"content": "测试说说"})
check("api_call 返回宿主结果", isinstance(r, dict) and r.get("retcode") == 0, str(r))
check("api_call 原样透传 action/params", calls[-1] == ("send_qzone_msg", {"content": "测试说说"}), str(calls[-1]))

ctx.send_group(200000001, "群消息")
check("send_group → send_group_msg", calls[-1][0] == "send_group_msg", str(calls[-1]))
check("send_group 参数正确",
      calls[-1][1] == {"group_id": "200000001", "message": "群消息"}, str(calls[-1][1]))

ctx.send_private(10000002, "私聊消息")
check("send_private → send_private_msg", calls[-1][0] == "send_private_msg", str(calls[-1]))
check("send_private 参数正确",
      calls[-1][1] == {"user_id": "10000002", "message": "私聊消息"}, str(calls[-1][1]))

# 未注入时优雅降级
saved = pm._host_api.pop("api_call")
check("未注入 api_call 时返回 None（不抛异常）", ctx.api_call("get_status") is None)
pm._host_api["api_call"] = saved
check("重新注入后恢复可用", ctx.api_call("get_status") is not None)

# 无 manager 的裸 ctx 也不能崩
bare = PluginContext("bare", "0.0.0", manager=None)
check("无 manager 时 api_call 返回 None", bare.api_call("get_status") is None)
check("无 manager 时 emit 返回 []", bare.emit("nothing") == [])

# ══ 4. 事件总线 ══════════════════════════════════════════════
print("\n=== 4. 事件总线 ===")
received = []


def on_event(c, event_name, payload):
    received.append((event_name, payload))
    return "handled"


sub_mod = types.ModuleType("abi_test_sub")
sub_mod.PLUGIN_SPEC = 1
sub_mod.PLUGIN_MANIFEST = {"name": "abi_test_sub", "version": "1.0.0", "api_version": 2,
                           "priority": 40, "default_enabled": True}
sub_mod.PLUGIN_HOOKS = {"on_event": on_event}
pm._plugins["abi_test_sub"] = {
    "module": sub_mod,
    "meta": dict(sub_mod.PLUGIN_MANIFEST, enabled=True),
    "hooks": sub_mod.PLUGIN_HOOKS,
    "ctx": PluginContext("abi_test_sub", "1.0.0", manager=pm),
    "error": None,
    "tier": "spec",
}

check("has_typed_hook('on_event') 为真", pm.has_typed_hook("on_event") is True)
res = ctx.emit("distill_done", target="10000002", level="L2")
check("emit 派发到订阅方", received and received[-1][0] == "distill_done", str(received[-1:]))
check("payload 原样送达", received[-1][1] == {"target": "10000002", "level": "L2"}, str(received[-1][1]))
check("emit 返回 [(插件名, 返回值)]", res and res[0][0] == "abi_test_sub" and res[0][1] == "handled", str(res))

# ══ 5. 递归保护 ══════════════════════════════════════════════
print("\n=== 5. 递归保护 ===")
loops = []


def on_event_loop(c, event_name, payload):
    loops.append(event_name)
    c.emit("loop")          # 在 on_event 里再 emit —— 必须被深度保护拦住
    return None


sub_mod.PLUGIN_HOOKS["on_event"] = on_event_loop
ctx.emit("loop")
check("自递归被拦下（次数有限）", 0 < len(loops) <= PluginManager.MAX_EMIT_DEPTH + 1,
      f"触发 {len(loops)} 次，上限 {PluginManager.MAX_EMIT_DEPTH}")
sub_mod.PLUGIN_HOOKS["on_event"] = on_event

# 清理本次自检注入的假插件，避免污染同进程后续使用
pm._plugins.pop("abi_test_sub", None)

print("\n" + "=" * 46)
print(f"结果：{'全部通过' if not fails else '失败 ' + str(fails)}")
sys.exit(1 if fails else 0)
