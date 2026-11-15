# -*- coding: utf-8 -*-
"""1.6.0 P3 · 分离态启动期验收（只在内存预演 / 临时目录，不落 lock）。

补的是 `test_separation.py` / `test_separation_acceptance.py` 没盖到的四件事：

  A. 产品态 `bootstrap()` 是 no-op：返回 False，真模块一个属性都不改
  B. 分离态全部替身属性挂上（无条件 + 条件挂载项），`provide()` 走替身
  C. 幂等：再调一次不抛、对象不换、不重复注册工具
  D. 重启后生效：新进程带 `EB_SEPARATION_FORCE=1` 才进分离态，不带就是产品态
  E. 审计 append-only；lock 里的 audit hash 对不上能被 `status()` 发现
  F. 源码库恒无 `SEPARATION.lock`

红线：全程只用 `EB_SEPARATION_FORCE` 预演，不调 `enable`、不写真 lock，
不碰真实人格 / 配置 / data。运行：python tests/test_separation_bootstrap.py
"""
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


TD = tempfile.mkdtemp(prefix="eb-sep-boot-")
os.environ["EB_SEPARATION_AUDIT"] = os.path.join(TD, "audit.jsonl")

import separation as sep  # noqa: E402
from separation import nulls  # noqa: E402
import speak_gate  # noqa: E402
import reply_gate  # noqa: E402
import reply_budget  # noqa: E402
import reply_drop  # noqa: E402
import persona_form  # noqa: E402
import de_ai  # noqa: E402

# (模块, 属性, 对应的 Null 对象) —— bootstrap() 无条件挂载的那批
PAIRS = [
    (speak_gate, "allow", nulls.NULL_GATE),
    (speak_gate, "prompt_note", nulls.NULL_GATE),
    (reply_gate, "should_regen", nulls.NULL_REPLY_GATE),
    (reply_gate, "regen_hint", nulls.NULL_REPLY_GATE),
    (reply_budget, "pick", nulls.NULL_BUDGET),
    (reply_budget, "check", nulls.NULL_BUDGET),
    (reply_budget, "fit", nulls.NULL_BUDGET),
    (reply_budget, "prompt_line", nulls.NULL_BUDGET),
    (reply_drop, "drop_reason", nulls.NULL_DROP),
    (persona_form, "apply", nulls.NULL_PERSONA),
    (persona_form, "decorate", nulls.NULL_PERSONA),
    (de_ai, "de_ai_rule_block", nulls.NULL_RULES),
]
# 条件挂载的几项：原模块有才验
CONDITIONAL = [
    (reply_gate, "habit_line", nulls.NULL_REPLY_GATE),
    (reply_budget, "regen_hint", nulls.NULL_BUDGET),
    (reply_budget, "apply_factors", nulls.NULL_BUDGET),
    (reply_budget, "ensure_statement_floor", nulls.NULL_BUDGET),
    (reply_budget, "anchor_ok", nulls.NULL_BUDGET),
    (de_ai, "de_ai_post_process", nulls.NULL_RULES),
]
ALL_PAIRS = PAIRS + [(m, a, n) for m, a, n in CONDITIONAL if hasattr(m, a)]
_SAVED = [(m, a, getattr(m, a, None)) for m, a, _ in ALL_PAIRS]
_FORCE_OLD = os.environ.get("EB_SEPARATION_FORCE")


def _restore():
    for m, a, v in _SAVED:
        if v is not None:
            setattr(m, a, v)
    if _FORCE_OLD is None:
        os.environ.pop("EB_SEPARATION_FORCE", None)
    else:
        os.environ["EB_SEPARATION_FORCE"] = _FORCE_OLD
    sep.reset_cache()


print("\n[A] 产品态 no-op")
try:
    os.environ["EB_SEPARATION_FORCE"] = "0"
    sep.reset_cache()
    before = [getattr(m, a, None) for m, a, _ in ALL_PAIRS]
    r = sep.bootstrap()
    after = [getattr(m, a, None) for m, a, _ in ALL_PAIRS]
    check("产品态 bootstrap() 返回 False", r is False, r)
    check("真模块属性一个都没动", all(x is y for x, y in zip(before, after)))
    check("provide('gate') 给真模块", sep.provide("gate") is speak_gate)
except Exception as e:
    check("产品态 bootstrap 不抛", False, f"{type(e).__name__}: {e}")

print("\n[B] 分离态替身挂载")
try:
    os.environ["EB_SEPARATION_FORCE"] = "1"
    sep.reset_cache()
    r = sep.bootstrap()
    check("分离态 bootstrap() 返回 True", r is True, r)
    bad = []
    for m, a, n in ALL_PAIRS:
        got = getattr(m, a, None)
        want = getattr(n, a, None)
        if got != want or want is None:
            bad.append((m.__name__, a))
    check(f"{len(ALL_PAIRS)} 个替身属性全部挂上", not bad, bad)
    check("provide('gate') 给 NullGate", sep.provide("gate") is nulls.NULL_GATE)
    check("provide('hardwall') 恒放行", sep.provide("hardwall").check("任意内容") == (True, ""))
except Exception as e:
    check("分离态 bootstrap 不抛", False, f"{type(e).__name__}: {e}")

print("\n[C] 幂等")
try:
    snap = [getattr(m, a, None) for m, a, _ in ALL_PAIRS]
    sep.bootstrap()
    again = [getattr(m, a, None) for m, a, _ in ALL_PAIRS]
    check("二次调用不抛且对象不换", all(x == y for x, y in zip(snap, again)))
    from autonomy import tools as _at
    n1, n2 = _at.register(), _at.register()
    check("工具重复注册幂等（返回数量一致）", n1 == n2, (n1, n2))
except Exception as e:
    check("幂等不抛", False, f"{type(e).__name__}: {e}")

print("\n[D] 重启后生效（新进程）")
_CHILD = """
import sys
sys.path.insert(0, r"__ROOT__")
import separation
print("active=%s" % separation.active())
print("boot=%s" % separation.bootstrap())
import reply_budget
print("unbounded=%s" % (reply_budget.pick().get("max_chars", 0) >= 10 ** 9))
""".replace("__ROOT__", ROOT)
for force, want_active in (("1", "True"), ("0", "False")):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               EB_SEPARATION_FORCE=force,
               EB_SEPARATION_AUDIT=os.path.join(TD, "child-audit.jsonl"))
    try:
        out = subprocess.run([sys.executable, "-c", _CHILD], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding="utf-8",
                            timeout=60)
        kv = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
        check(f"新进程 force={force} → active={want_active}",
              kv.get("active") == want_active, (out.returncode, out.stdout[-200:], out.stderr[-200:]))
        if force == "1":
            check("新进程 bootstrap 后预算无上限", kv.get("unbounded") == "True", kv)
        else:
            check("新进程产品态预算仍有上限", kv.get("unbounded") == "False", kv)
    except Exception as e:
        check(f"新进程 force={force} 可跑", False, f"{type(e).__name__}: {e}")

print("\n[E] 审计 append-only 与篡改可发现")
try:
    _audit = importlib.import_module("separation.audit")
    _audit._AUDIT_PATH = Path(TD) / "append.jsonl"
    for i in range(3):
        _audit.log("ai", "unit", target=str(i))
    lines = _audit._AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    check("三次 log 追加成三行（不覆盖）", len(lines) == 3, len(lines))
    check("第一行仍是第 0 条", json.loads(lines[0]).get("target") == "0", lines[:1])
    check("tail() 读得到", len(_audit.tail(2)) == 2, _audit.tail(2))

    audit_py = Path(sep.__file__).with_name("audit.py")
    real_sha = hashlib.sha256(audit_py.read_bytes()).hexdigest()
    _orig_lock = sep.lock
    try:
        sep.lock = lambda: {"audit_sha256": "0" * 64}
        check("lock hash 对不上 → audit_ok=False", sep.status().get("audit_ok") is False)
        sep.lock = lambda: {"audit_sha256": real_sha}
        check("lock hash 对上 → audit_ok=True", sep.status().get("audit_ok") is True)
    finally:
        sep.lock = _orig_lock
except Exception as e:
    check("审计验收不抛", False, f"{type(e).__name__}: {e}")

print("\n[F] 源码库不落 lock")
for name in ("SEPARATION.lock", "SEPARATION.pending", "constraints.archive"):
    check(f"源码库无 {name}", not (Path(ROOT) / name).exists())

_restore()
os.environ["EB_SEPARATION_FORCE"] = "0"
sep.reset_cache()
check("预演结束后 active() 复位为 False", sep.active() is False)
if _FORCE_OLD is None:
    os.environ.pop("EB_SEPARATION_FORCE", None)
else:
    os.environ["EB_SEPARATION_FORCE"] = _FORCE_OLD

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
