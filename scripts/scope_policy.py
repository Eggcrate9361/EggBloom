#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scope_policy —— 作用域能力档位：查看 / 切档 / 单能力覆盖 / 审计。

P4 的界面（S5）还没做，这是**在那之前唯一能改档位的途径**。
命令：

    python scripts/scope_policy.py show               # 看当前全局状态与留痕
    python scripts/scope_policy.py show group 123456  # 看某个群的生效档位
    python scripts/scope_policy.py set  group 123456 readonly --by 你
    python scripts/scope_policy.py override group 123456 generate_image off
    python scripts/scope_policy.py clear group 123456   # 离开自定义，回具名档
    python scripts/scope_policy.py switch on|off        # 总开关（写配置，需重启生效）

说明：
  · 切档/覆盖是**只追加的事件**，写进 logs/scope_policy_events.jsonl；
    主进程会**自动热重载**（比对文件 mtime），**不需要重启**。
  · 总开关 `capability_registry_enable` 写在 bot_config.json，**需重启**。
  · 默认档 `standard` 与今天行为完全一致 —— 不改任何东西时功能等于不存在。
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from harness.scope_registry import ScopeRegistry  # noqa: E402

CN = {"standard": "标准（= 今天的行为，不额外限制）",
      "readonly": "只读（写与执行类操作被拒）",
      "full": "完全放开（解除既有名单约束）",
      "custom": "自定义（在某个具名档之上有单能力覆盖）"}


def _reg():
    return ScopeRegistry()


def _load_cfg():
    import config as _cfg
    try:
        return _cfg.load_config()
    except Exception:
        p = os.path.join(_ROOT, "bot_config.json")
        with open(p, encoding="utf-8") as f:
            return json.load(f)


def cmd_show(args):
    reg = _reg()
    s = reg.stats()
    print("── 全局 ──")
    print("  总开关 capability_registry_enable：", "开" if _load_cfg().get("capability_registry_enable") else "关（默认）")
    print("  默认档位：", reg.default_preset(), "|", CN.get(reg.default_preset(), ""))
    print("  可选项  ：", "、".join(sorted(reg.presets())))
    print("  能力词汇： %d 个" % s["caps"])
    print("  作用域  ： %d 个已改过 | 事件 %d 条 | 单能力覆盖 %d 项 | 降级 %d 次"
          % (s["scopes"], s["events"], s["overrides"], s["degraded"]))
    if len(args) >= 2:
        kind, sid = args[0], args[1]
        r = reg.resolve(kind, sid)
        print("── %s:%s ──" % (kind, sid))
        print("  生效档位：", r["preset"], "（具名：%s）" % r.get("named_preset"),
              "|", CN.get(r["preset"], ""))
        ov = reg.overrides(kind, sid)
        print("  单能力覆盖：", ov if ov else "（无）")
        print("  允许的能力：", "、".join(sorted(r["capabilities"])))
    if s["events"]:
        print("── 最近留痕 ──")
        for e in reg.audit(10):
            import time as _t
            what = e.get("to") or ("覆盖 " + json.dumps(e.get("overrides"), ensure_ascii=False))
            print("  %s  %-12s → %-10s by %s"
                  % (_t.strftime("%m-%d %H:%M", _t.localtime(e["ts"])), e["scope"], what,
                     e.get("by") or "-"))


def cmd_set(args):
    kind, sid, preset = args[0], args[1], args[2]
    by = ""
    if "--by" in args:
        by = args[args.index("--by") + 1]
    out = _reg().set_preset(kind, sid, preset, by=by)
    print(json.dumps(out, ensure_ascii=False))
    if out.get("changed"):
        print("✓ 已生效（主进程会自动热重载，无需重启）")


def cmd_override(args):
    kind, sid, cap, state = args[0], args[1], args[2], args[3]
    allow = state.strip().lower() in ("on", "1", "true", "yes", "允许")
    out = _reg().set_capability(kind, sid, cap, allow)
    print(json.dumps(out, ensure_ascii=False))
    if out.get("changed"):
        print("✓ 已生效（无需重启）")


def cmd_clear(args):
    print(json.dumps(_reg().clear_overrides(args[0], args[1]), ensure_ascii=False))


def cmd_switch(args):
    want = args[0].strip().lower() in ("on", "1", "true", "yes", "开")
    import config as _cfg
    cfg = _load_cfg()
    cfg["capability_registry_enable"] = want
    _cfg.save_config(cfg)
    print("✓ capability_registry_enable =", want, "（**需重启后端生效**）")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, args = sys.argv[1], sys.argv[2:]
    fn = {"show": cmd_show, "set": cmd_set, "override": cmd_override,
          "clear": cmd_clear, "switch": cmd_switch}.get(cmd)
    if not fn:
        print("未知命令：%s\n" % cmd)
        print(__doc__)
        return
    fn(args)


if __name__ == "__main__":
    main()
