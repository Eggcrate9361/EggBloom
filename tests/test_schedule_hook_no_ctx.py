# -*- coding: utf-8 -*-
"""1.6.0 三条工作线补测试 · ① schedule 插件 hook 不传 ctx 不抛。

背景：`before_reply` 是链式钩子，宿主在某些路径上只给 message / group_id / context，
不保证一定带 ctx。旧实现里 `_cfg()` 会在 `_CTX` 为 None 时抛 AttributeError，
修复后 `_CTX` 为空时退回默认值。

判据写死（不看真实日程表内容，表是配置、不是契约）：
  A. 无参调用不抛
  B. 只给 message / group_id / context 不抛
  C. 显式 ctx=None 不抛
  D. 返回值只能是 None 或 `{action:"inject", content:str}`
  E. `schedule_enable=False` 时一律返回 None（零注入）
  F. hook 不写盘：`save_config` 被换成「调用即抛」也不影响
  G. PLUGIN_HOOKS 仍登记 before_reply / on_load

只读：不碰 plugins/schedule/data/config.json，不启插件系统，不联网。
运行：python tests/test_schedule_hook_no_ctx.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import plugins.schedule as S  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


def _shape_ok(r):
    if r is None:
        return True
    return (isinstance(r, dict)
            and r.get("action") == "inject"
            and isinstance(r.get("content"), str))


S._CTX = None

print("\n[1] 不传 ctx 的四种调法")
CASES = [
    ("无参", lambda: S._before_reply()),
    ("只给 message", lambda: S._before_reply(message="在吗")),
    ("message + group_id + context",
     lambda: S._before_reply(message="在吗", group_id="123", context={})),
    ("显式 ctx=None", lambda: S._before_reply(ctx=None, message="在吗")),
]
for name, fn in CASES:
    try:
        r = fn()
        check(f"{name} 不抛", True)
        check(f"{name} 返回结构合法", _shape_ok(r), repr(r))
    except Exception as e:
        check(f"{name} 不抛", False, f"{type(e).__name__}: {e}")
        check(f"{name} 返回结构合法", False, "没拿到返回值")

print("\n[2] 关闭开关后零注入")


class _OffCtx:
    def get_config(self, key, default=None):
        if key == "schedule_enable":
            return False
        return default


try:
    r = S._before_reply(ctx=_OffCtx(), message="在吗", group_id="123")
    check("schedule_enable=False → None", r is None, repr(r))
except Exception as e:
    check("schedule_enable=False → None", False, f"{type(e).__name__}: {e}")

print("\n[3] hook 不写盘")
_orig_save = S.save_config


def _boom(*a, **kw):
    raise AssertionError("before_reply 不该调用 save_config")


S.save_config = _boom
try:
    S._CTX = None
    r = S._before_reply(message="在吗")
    check("save_config 被调用即抛时仍不抛", True)
    check("返回结构仍合法", _shape_ok(r), repr(r))
except AssertionError as e:
    check("save_config 被调用即抛时仍不抛", False, str(e))
except Exception as e:
    check("save_config 被调用即抛时仍不抛", False, f"{type(e).__name__}: {e}")
finally:
    S.save_config = _orig_save
    S._CTX = None

print("\n[4] 钩子登记")
check("PLUGIN_HOOKS 有 before_reply", "before_reply" in S.PLUGIN_HOOKS)
check("PLUGIN_HOOKS 有 on_load", "on_load" in S.PLUGIN_HOOKS)
check("before_reply 可调用", callable(S.PLUGIN_HOOKS.get("before_reply")))

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
