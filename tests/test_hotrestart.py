# -*- coding: utf-8 -*-
"""hotrestart 插件的回归测试（2026-11-09）。

三件事必须钉住，错一件就会出真事：

1. **重启不能在本进程 fork** —— bot 跑在 KillMode=control-group 的服务里，
   在进程内 fork 的 `sleep && systemctl restart` 会被自己的重启杀掉。
   所以必须走 `sudo -n systemd-run --collect --on-active=N`（独立瞬态 timer）。
2. **重载要 fail-closed** —— 有在途钩子就拒绝，不能硬摘模块。
3. **管理员闸门** —— 非管理员发 `!重启bot` 不能执行。

跑法：python scripts/tests/test_hotrestart.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # tests/ 上跳一层就是仓库根
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}  {detail}")
        FAILS.append(name)


class FakeCtx:
    """只实现插件真用到的那两个方法。"""

    def __init__(self, cfg):
        self._cfg = cfg

    def get_config(self, key=None, default=None):
        return self._cfg if key is None else self._cfg.get(key, default)

    def log(self, *a, **k):
        pass


def t_restart_gate():
    """平台闸：非 Linux / 无 systemctl 时明确拒绝，且**不写便条**。

    这条单独存在，是因为下面 t_restart_argv 要断言的是「命令怎么拼」，
    那部分跟跑在什么系统上无关 —— 它会把闸临时打开再测。
    """
    from plugins import hotrestart as hr
    from harness import restart_notice as rn
    import sys as _sys

    orig_sup, orig_write = hr._RESTART_SUPPORTED, rn.write_marker
    wrote = {"n": 0}
    rn.write_marker = lambda *a, **k: wrote.__setitem__("n", wrote["n"] + 1)
    try:
        hr._RESTART_SUPPORTED = False
        r = hr.do_restart(3, target="12345", is_group=False)
        check("闸关着时 ok=False", r.get("ok") is False, r)
        check("说清是平台不支持", "系统" in str(r.get("error") or "") or
              "systemd" in str(r.get("error") or ""), r)
        check("拒绝时不写重启便条（否则下次冒出一句莫名其妙的「我回来了」）",
              wrote["n"] == 0, wrote)
        check("闸只看常量，不改 _active_service 的结果",
              isinstance(hr._RESTART_SUPPORTED, bool))
    finally:
        hr._RESTART_SUPPORTED, rn.write_marker = orig_sup, orig_write

    if not _sys.platform.startswith("linux"):
        check("非 Linux 上 _RESTART_SUPPORTED 默认关", hr._RESTART_SUPPORTED is False,
              _sys.platform)


def t_restart_argv():
    """重启命令必须是 systemd-run 瞬态 timer，且延迟可调、有下限。"""
    from plugins import hotrestart as hr

    seen = {}

    def fake_run(argv, timeout=15.0):
        seen["argv"] = list(argv)
        return 0, "Running timer", ""

    orig_run, orig_active = hr._run, hr._active_service
    orig_sup, orig_write = hr._RESTART_SUPPORTED, None
    from harness import restart_notice as _rn
    orig_write = _rn.write_marker
    hr._run, hr._active_service = fake_run, lambda: "eggbloom-bot"
    hr._RESTART_SUPPORTED = True          # 本组测「命令怎么拼」，与平台无关
    _rn.write_marker = lambda *a, **k: None
    try:
        r = hr.do_restart(3)
        argv = seen.get("argv") or []
        check("restart ok", r.get("ok") is True, r)
        check("用 sudo -n", argv[:2] == ["sudo", "-n"], argv)
        check("走 systemd-run", "systemd-run" in argv, argv)
        check("--collect（单元自清）", "--collect" in argv, argv)
        check("独立瞬态 timer", any(a.startswith("--on-active=") for a in argv), argv)
        check("重启的是服务不是自己",
              argv[-2:] == ["restart", "eggbloom-bot"], argv)
        check("延迟生效", "--on-active=3" in argv, argv)

        # 下限：0 / 负数 / 垃圾值都要被夹到 >=1，别排成"立刻重启"
        hr.do_restart(0)
        check("delay=0 夹到 1", "--on-active=1" in seen["argv"], seen["argv"])
        hr.do_restart("abc")
        check("delay 非数字有默认值", any(a.startswith("--on-active=") for a in seen["argv"]),
              seen["argv"])
        hr.do_restart(99999)
        check("delay 上限 600", "--on-active=600" in seen["argv"], seen["argv"])
    finally:
        hr._run, hr._active_service = orig_run, orig_active
        hr._RESTART_SUPPORTED = orig_sup
        _rn.write_marker = orig_write


def t_restart_no_service():
    """不是 systemd 起的（探测不到服务）→ 明确说不行，别假装排上了。"""
    from plugins import hotrestart as hr

    orig, orig_sup = hr._active_service, hr._RESTART_SUPPORTED
    hr._active_service = lambda: ""
    hr._RESTART_SUPPORTED = True
    try:
        r = hr.do_restart(3)
        check("无服务时 ok=False", r.get("ok") is False, r)
        check("无服务时给原因", bool(r.get("error")), r)
    finally:
        hr._active_service = orig
        hr._RESTART_SUPPORTED = orig_sup


def t_reload_fail_closed():
    """重载：空名 / 未注册 / 有在途 —— 三种都得是明确失败，不硬来。"""
    from plugins import hotrestart as hr

    orig = hr._pm

    class PM:
        def __init__(self, loaded=True, inflight=0, reload_ok=True):
            self.loaded, self.inflight, self.reload_ok = loaded, inflight, reload_ok
            self.reload_called = False

        def is_loaded(self, n):
            return self.loaded

        def is_enabled(self, n):
            return True

        def inflight_count(self, n):
            return self.inflight

        def reload(self, n):
            self.reload_called = True
            return self.reload_ok

        def get_error(self, n):
            return ""

    try:
        hr._pm = lambda: PM(loaded=False)
        r = hr.do_reload("nope")
        check("未注册 → 失败", r.get("ok") is False, r)

        hr._pm = lambda: PM()
        check("空名 → 失败", hr.do_reload("").get("ok") is False)

        pm = PM(reload_ok=False, inflight=2)
        hr._pm = lambda: pm
        r = hr.do_reload("memewiki")
        check("重载失败要报出来", r.get("ok") is False, r)
        check("失败时给在途数", r.get("inflight") == 2, r)
        check("失败原因不含空串", bool(r.get("error")), r)

        pm = PM()
        hr._pm = lambda: pm
        r = hr.do_reload("memewiki")
        check("正常重载 ok", r.get("ok") is True, r)
        check("确实调了 pm.reload", pm.reload_called)
    finally:
        hr._pm = orig


def t_install_guard():
    """install 只认真的存在插件目录，别给不存在的名字建占位。"""
    from plugins import hotrestart as hr

    orig = hr._pm

    class PM:
        def __init__(self):
            self.plugins = {}
            self.calls = []

        def discover(self):
            return ["memewiki", "hotrestart"]

        def is_loaded(self, n):
            return n in self.plugins

        def is_enabled(self, n):
            return n in self.plugins

        def load(self, n):
            self.calls.append(n)
            self.plugins[n] = True
            return True

        def get_error(self, n):
            return ""

    pm = PM()
    hr._pm = lambda: pm
    try:
        r = hr.act_install({"name": "nosuchplugin"})
        check("不存在的插件 → 失败", r.get("ok") is False, r)
        check("不存在的插件不调用 load", pm.calls == [], pm.calls)

        r = hr.act_install({"name": "memewiki"})
        check("存在的插件能装", r.get("ok") is True, r)
        check("装完标记 loaded", r.get("loaded") is True, r)
        check("已经装了就不再重复 load", r.get("already_loaded") is False, r)
    finally:
        hr._pm = orig


def t_admin_gate():
    """非管理员发指令不能执行；管理员才放行。"""
    from plugins import hotrestart as hr

    cfg = {"target_qqs": ["1000000001"], "bot_qq": "1000000002"}
    ctx = FakeCtx(cfg)

    r = hr.on_message(ctx, {"text": "!重启bot", "sender": "999999"})
    check("非管理员被挡", isinstance(r, str) and "主人" in r, r)

    r = hr.on_message(ctx, {"text": "!重启bot", "sender": "1000000001"})
    check("管理员不被告知无权限", not (isinstance(r, str) and "主人" in r), r)

    check("无关消息不接管", hr.on_message(ctx, {"text": "今天天气不错",
                                                "sender": "1000000001"}) is None)


def main():
    print("hotrestart 插件测试")
    for fn in (t_restart_gate, t_restart_argv, t_restart_no_service,
               t_reload_fail_closed, t_install_guard, t_admin_gate):
        print(f"- {fn.__name__}")
        fn()
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
