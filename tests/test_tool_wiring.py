# -*- coding: utf-8 -*-
"""工具"注册 ≠ 可执行"的守卫（2026-11-02 真机事故）。

## 事故

用户在私聊里让 Bot「看看蒸馏插件有没有问题」。模型决定派活：

```
→ deep_task: action= (第3轮) [被拒:unknown] → 未知工具: deep_task
```

`deep_task` 的 schema 已经注入给它（`agent_as_tool` 开着），
`harness.tool_deep.register_all()` 也把工具登记进了注册表 ——
但 bot_core 起工具表时**漏了 `_work_fns.update(_deep_fns)`**，
于是 runner 查执行表查不到函数，只能回「未知工具」。

## 这条用例守什么

把 bot_core 里所有 `xxx_fns = yyy.register_all(...)` 找出来，
要求随后几行内把它并进执行表（`update(xxx_fns)`）。
漏接线的必须进 `ALLOW` 并写清理由 —— 不允许"悄悄注册了但调不了"。

运行：python tests/test_tool_wiring.py
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


SRC = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
LINES = SRC.splitlines()

#: 故意不并进 `_work_fns` 的（必须写清理由）
ALLOW = {
    # 直接 update(_x.register_all(...)) 的形式不产生中间变量，不在此列
}

ASSIGN = re.compile(r"^\s*(_([a-z]+)_fns)\s*=\s*(\w+)\.register_all\(")
WINDOW = 14          # 允许"注册完 <=14 行内"接线（中间可能有日志/注释）

found, unwired = [], []
for i, line in enumerate(LINES):
    m = ASSIGN.match(line)
    if not m:
        continue
    var = m.group(1)
    found.append(var)
    window = "\n".join(LINES[i:i + WINDOW])
    wired = (f"update({var})" in window) or (f"update(**{var})" in window)
    if not wired and var not in ALLOW:
        unwired.append((i + 1, var))

check("bot_core 里能找到注册点（说明正则没失效）", len(found) >= 4, found)
check("★ 每个 register_all 的结果都并进了执行表", not unwired,
      [f"第{n}行 {v} 未接线" for n, v in unwired])

# 逐个点名，漏了哪个一眼看见
for var in sorted(set(found)):
    listed = any(f"update({var})" in "\n".join(LINES[i:i + WINDOW])
                 for i, l in enumerate(LINES) if ASSIGN.match(l)
                 and ASSIGN.match(l).group(1) == var)
    check(f"{var} 已接线", listed or var in ALLOW, var)

# deep_task 专项：注册结果里的函数必须真的可调用（不是 None / 不是 schema）
from harness import tool_deep as td  # noqa: E402

fns = td.register_all()
check("deep_task.register_all 返回可调用函数",
      callable(fns.get("deep_task")), type(fns.get("deep_task")).__name__)
import inspect  # noqa: E402

# 执行表里放的是 adapt_kwargs 包装（runner 传 dict），所以签名是 (args=None, **kw)；
# 真正要保证的是**底层函数**认 task —— 包装器本身可调用就够了。
check("执行表里的是包装器（runner 传 dict 也能调）",
      "args" in inspect.signature(fns["deep_task"]).parameters,
      str(inspect.signature(fns["deep_task"])))
check("底层函数签名认 task/context",
      {"task", "context"} <= set(inspect.signature(td.deep_task).parameters),
      str(inspect.signature(td.deep_task)))
check("注册表里能解析出 schema",
      td.DEEP_TOOLS[0]["function"]["name"] == "deep_task")

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
