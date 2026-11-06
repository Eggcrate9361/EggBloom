# -*- coding: utf-8 -*-
"""tests/test_tool_action_ledger.py —— 工具动作也要进行为账本（2026-11-06）

## 为什么

用户原话「bot 不知道自己干了什么说了什么」。上一刀把**插件发送**和**发文件**
补进了账本；这一刀补工具层：

  · `ws_write` / `ws_edit` / `ws_edit_many` 改过哪些文件
  · `py_run` 跑过什么代码
  · 群管理（踢人 / 禁言 / 改名片 …）

以前这些只在**当轮的工具消息**里，下一轮就没了 —— 问它"你刚才改的是哪个文件"
只能复述不出来，或者干脆否认。

## 两个设计点

1. **记在成功出口**：写失败、找不到 old_string、需要确认、retcode!=0 一律不记。
   账本里不能有没发生的事（这个仓库已经因为"把失败说成成功"吃过亏）。
2. **场景由主链告诉它**：工具函数拿不到"这是群聊还是私聊、对谁"，
   各自猜必然猜错。`self_actions.set_turn()` 每轮由主链调一次，
   工具只调 `record_tool()`。

判据：
  [A] set_turn → record_tool 带上正确的 scene / target / group_id
  [B] ws_write 成功 → 账本记 edit（带文件名）
  [C] ws_write 被"已存在要确认"挡住 → 不记
  [D] ws_edit 成功 → edit；找不到 old_string → 不记
  [E] py_run 跑完 → 记 run（带状态）
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import self_actions as sa  # noqa: E402
from harness import tool_fs as fs  # noqa: E402
from harness import tool_proc as tp  # noqa: E402
from harness import workspace as ws  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
CALLS = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def main():
    global CALLS
    orig_record = sa.record
    orig_root = ws.workspace_root()
    td = tempfile.mkdtemp(prefix="ledger_")
    try:
        # 打桩打在**最内层** record 上：record_tool 要真的跑，才能验证
        # "场景从 set_turn 来"这段逻辑（打在 record_tool 上就把被测逻辑换掉了）
        sa.record = lambda kind, **kw: (
            CALLS.append((kind, kw.get("detail", ""), kw)), {"kind": kind})[1]
        ws.set_root(td)

        print("=== A) 场景由 set_turn 决定 ===")
        sa.set_turn(scene="group", target="829854136", group_id="829854136")
        CALLS = []
        sa.record_tool("edit", "改了 a.txt")
        check("scene=group", CALLS and CALLS[0][2].get("scene") == "group", CALLS)
        check("target=群号", CALLS and CALLS[0][2].get("target") == "829854136", CALLS)
        check("group_id 同步", CALLS and CALLS[0][2].get("group_id") == "829854136", CALLS)

        sa.set_turn(scene="private", target="2011257385", group_id="")
        CALLS = []
        sa.record_tool("run", "py_run OK")
        check("私聊 scene/target 正确",
              CALLS and CALLS[0][2].get("scene") == "private"
              and CALLS[0][2].get("target") == "2011257385", CALLS)

        print("\n=== B) ws_write 成功记 edit ===")
        CALLS = []
        out = fs.tool_write({"path": "a.txt", "content": "第一行\n第二行\n"})
        check("工具返回成功", "已创建" in out, out)
        kinds = [c[0] for c in CALLS]
        check("账本记了一条 edit", kinds == ["edit"], CALLS)
        check("detail 带文件名", "a.txt" in str(CALLS[0][1]), CALLS)

        print("\n=== C) 需要确认时不记 ===")
        CALLS = []
        out = fs.tool_write({"path": "a.txt", "content": "覆盖"})
        check("返回需要确认", "需要确认" in out, out)
        check("账本没有新记录", CALLS == [], CALLS)

        print("\n=== D) ws_edit ===")
        CALLS = []
        out = fs.tool_edit({"path": "a.txt", "old_string": "第一行", "new_string": "第1行"})
        check("编辑成功", "已修改" in out, out)
        check("记了一条 edit", [c[0] for c in CALLS] == ["edit"], CALLS)

        CALLS = []
        out = fs.tool_edit({"path": "a.txt", "old_string": "压根没有这句", "new_string": "x"})
        check("找不到原文 → 失败返回", "找不到" in out or "未" in out, out)
        check("失败不记", CALLS == [], CALLS)

        print("\n=== E) py_run 记 run ===")
        CALLS = []
        out = tp.tool_py_run({"code": "print(1+1)"})
        check("执行有输出", "2" in out, out[:120])
        kinds = [c[0] for c in CALLS]
        check("账本记了一条 run", kinds == ["run"], CALLS)
        check("detail 带状态", any(k in str(CALLS[0][1]) for k in ("OK", "ERROR", "TIMEOUT")),
              CALLS)
    finally:
        sa.record = orig_record
        sa.set_turn(scene="", target="", group_id="")
        ws.set_root(orig_root)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for n in FAILURES:
        print(f"  FAIL  {n}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
