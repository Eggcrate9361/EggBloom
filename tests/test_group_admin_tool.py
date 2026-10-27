# -*- coding: utf-8 -*-
"""群管理动作的统一执行层（2026-10-27 · 1.5.9 真机）

## 为什么

用户说「把群名改成外莱塔尼亚」，结果群里什么都没发生。两条原因叠在一起：

  1. `_handle_group_admin_command` 的正则要求**开头就是动词**，
     「把群名改成 X」开头是「把」，匹配不上 → 进了 AI 流程；
  2. AI 手上没有任何管理工具，只能回一句话 —— 然后那句话还被过时丢弃。

也就是说：**能说的和能做到的不是同一套东西**。命令式有实现，自然语言没有。

## 契约

1. 动作只有一份实现（`_do_group_admin`），正则路径与工具路径都调它
2. 权限只在这一层判：非主人一律「这得主人发话」
3. 失败要说清是没权限、缺参数、还是驱动 retcode 非 0
4. `group_admin` 工具只在群聊注入；私聊没有可管理的对象
5. 工具的 sender 由调用方闭包注入 —— 模型不能自己声称是谁

运行：python tests/test_group_admin_tool.py
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


GID = "829854136"
MASTER = "3150007406"
OTHER = "2011257385"


def _stub(retcode=0, raise_exc=False):
    import bot_core
    calls = []

    def _api(ep, body=None, timeout=15):
        if raise_exc:
            raise RuntimeError("boom")
        calls.append((ep, body))
        return {"retcode": retcode, "data": {}}

    bot_core.api_call = _api
    return calls


def _with(retcode=0, raise_exc=False, admins=(MASTER,)):
    import bot_core
    old = (bot_core._get_admin_qqs, bot_core.api_call, dict(bot_core._config))
    bot_core._get_admin_qqs = lambda: set(admins)
    calls = _stub(retcode, raise_exc)
    return bot_core, calls, old


def _restore(old):
    import bot_core
    bot_core._get_admin_qqs, bot_core.api_call, cfg = old
    bot_core._config.clear()
    bot_core._config.update(cfg)


def test_permission():
    bc, calls, old = _with()
    try:
        ok, msg = bc._do_group_admin("set_group_name", value="外莱塔尼亚",
                                     sender=OTHER, group_id=GID)
        check("非主人被拒", not ok, (ok, msg))
        check("拒绝说人话（这得主人发话）", "主人发话" in msg, msg)
        check("拒绝时不打 API", not calls, calls)
    finally:
        _restore(old)


def test_master_ok():
    bc, calls, old = _with()
    try:
        ok, msg = bc._do_group_admin("set_group_name", value="外莱塔尼亚",
                                     sender=MASTER, group_id=GID)
        check("主人可执行", ok, (ok, msg))
        check("回执是人话", msg == "群名改了", msg)
        check("调到 set_group_name", calls and calls[0][0] == "set_group_name", calls)
        check("群名传对", calls[0][1].get("group_name") == "外莱塔尼亚", calls[0])
    finally:
        _restore(old)


def test_driver_fail():
    bc, calls, old = _with(retcode=1200)
    try:
        ok, msg = bc._do_group_admin("kick", target="2047271391",
                                     sender=MASTER, group_id=GID)
        check("驱动失败判失败", not ok, (ok, msg))
        check("失败带 retcode", "1200" in msg, msg)
    finally:
        _restore(old)
    bc, calls, old = _with(raise_exc=True)
    try:
        ok, msg = bc._do_group_admin("kick", target="2047271391",
                                     sender=MASTER, group_id=GID)
        check("异常不抛到调用方", not ok and "失败" in msg, (ok, msg))
    finally:
        _restore(old)


def test_missing_args():
    bc, calls, old = _with()
    try:
        ok, msg = bc._do_group_admin("kick", sender=MASTER, group_id=GID)
        check("缺目标要说清", not ok and "要@人" in msg, msg)
        ok, msg = bc._do_group_admin("set_group_name", value="", sender=MASTER, group_id=GID)
        check("空群名要说清", not ok and "空" in msg, msg)
        ok, msg = bc._do_group_admin("nonsense", sender=MASTER, group_id=GID)
        check("未知动作要说清", not ok and "不认识" in msg, msg)
    finally:
        _restore(old)


def test_ban_minutes():
    bc, calls, old = _with()
    try:
        ok, _ = bc._do_group_admin("ban", target="2047271391", value="10",
                                   sender=MASTER, group_id=GID)
        check("禁言 10 分钟 → 600 秒", ok and calls[0][1].get("duration") == 600, calls)
    finally:
        _restore(old)


def test_single_implementation():
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("正则路径调统一执行层", "_do_group_admin(action, target=target" in src)
    check("工具路径调统一执行层", "def _tool_group_admin" in src and "_do_group_admin(" in src)
    # 文案里也会出现这句（能力清单的例句），这里数的是**判断代码**
    check("权限只在执行层判一次",
          src.count('return (False, "这得主人发话")') == 1,
          src.count('return (False, "这得主人发话")'))
    check("工具只在群聊注入", "if is_group and GROUP_ADMIN_SCHEMA" in src)
    check("sender 由调用方闭包注入", "lambda a, _s=sender, _g=group_id:" in src)


def test_schema():
    import bot_core
    schema = bot_core.GROUP_ADMIN_SCHEMA["function"]
    check("工具名是 group_admin", schema["name"] == "group_admin", schema["name"])
    acts = schema["parameters"]["properties"]["action"]["enum"]
    check("动作覆盖全部管理指令", set(acts) == set(bot_core._ADMIN_LABELS), (acts, bot_core._ADMIN_LABELS))
    check("描述里要求真调用，不是嘴上答应", "不要只用嘴答应" in schema["description"], schema["description"])


def main():
    print("=" * 58)
    print("群管理统一执行层")
    print("=" * 58)
    for fn in (test_permission, test_master_ok, test_driver_fail, test_missing_args,
               test_ban_minutes, test_single_implementation, test_schema):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
