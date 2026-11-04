# -*- coding: utf-8 -*-
"""tests/test_snowluma_hook_autoload.py —— 注入开关（2026-11-04 真机）

## 现场

QQ 是提权进程。SnowLuma 拉起的 node 若是普通权限（Medium），注入**不报错、
静默跳过**——日志只有 "hook auto-load enabled"，永远等不到
"[Hook] initial connection started"，OneBot 的 3000/3001 也就永远不监听。
用户看到的就是「SnowLuma 找不到 QQ 进程」。

同时适配器把这行写死了：

    env["SNOWLUMA_HOOK_AUTOLOAD"] = "0"   # 禁止自动注入，由用户从 WebUI 手动选账号

后果是 EggBloom 拉起的 SnowLuma **永远不注入**，连手动在 WebUI 选都做不到
（它压根没连上 QQ）。改成配置项 `hook_autoload`，默认仍是 0（保持上游意图），
单机单账号的用户可以打开。

## 判据

① 配置没写 → 传 "0"（不改变老行为）
② hook_autoload=true → 传 "1"
③ 传的是字符串 "0"/"1"（SnowLuma 只认这两个值，不能是 True/False）
④ 其它环境变量原样保留（别把 HOME/PATH 之类弄丢）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

from protocols.snowluma_adapter import SnowLumaAdapter  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def env_for(cfg):
    """构造一个只带指定配置的适配器，取它给子进程的环境。"""
    a = SnowLumaAdapter()
    a._cfg = dict(cfg or {})
    return a._build_env()


def main():
    print("[A] 默认不自动注入（保持上游意图）")
    env = env_for({})
    check("★ 默认传 '0'", env.get("SNOWLUMA_HOOK_AUTOLOAD") == "0", env.get("SNOWLUMA_HOOK_AUTOLOAD"))
    check("是字符串而不是布尔", isinstance(env.get("SNOWLUMA_HOOK_AUTOLOAD"), str))

    print("\n[B] 打开开关")
    env = env_for({"hook_autoload": True})
    check("★ true → '1'", env.get("SNOWLUMA_HOOK_AUTOLOAD") == "1", env.get("SNOWLUMA_HOOK_AUTOLOAD"))

    print("\n[C] 显式 false / 别的真值形状")
    check("false → '0'", env_for({"hook_autoload": False}).get("SNOWLUMA_HOOK_AUTOLOAD") == "0")
    # ★ 字符串形状要按白名单认：bool("0") 是 True，直接 bool() 会把"关"变成"开"
    for off in ("0", "false", "False", "no", ""):
        check(f"字符串 {off!r} → '0'",
              env_for({"hook_autoload": off}).get("SNOWLUMA_HOOK_AUTOLOAD") == "0",
              env_for({"hook_autoload": off}).get("SNOWLUMA_HOOK_AUTOLOAD"))
    for on in ("1", "true", "True", "yes", "on"):
        check(f"字符串 {on!r} → '1'",
              env_for({"hook_autoload": on}).get("SNOWLUMA_HOOK_AUTOLOAD") == "1",
              env_for({"hook_autoload": on}).get("SNOWLUMA_HOOK_AUTOLOAD"))
    check("缺键缺配置也不炸", isinstance(env_for(None).get("SNOWLUMA_HOOK_AUTOLOAD"), str))

    print("\n[D] 不能把其它环境变量搞丢")
    env = env_for({"hook_autoload": True})
    check("★ PATH 还在", bool(env.get("PATH") or env.get("Path")), list(env)[:6])
    check("代理变量被清掉（既有行为）",
          "HTTP_PROXY" not in env and "http_proxy" not in env)

    print("\n[E] 源码层面：写死的 '0' 已经改成读配置")
    src = open(os.path.join(ROOT, "protocols", "snowluma_adapter.py"),
               encoding="utf-8", errors="replace").read()
    check("★ 不再写死", 'env["SNOWLUMA_HOOK_AUTOLOAD"] = "0"' not in src)
    check("★ 读了 hook_autoload", 'hook_autoload' in src)
    check("★ 有条件三元", '"1" if _auto else "0"' in src)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
