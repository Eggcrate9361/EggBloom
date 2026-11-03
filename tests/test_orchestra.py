# -*- coding: utf-8 -*-
"""tests/test_orchestra.py —— 子 Agent 三角色编排（1.6.0 P2 第①刀）

钉住的是**约束**，不是"模型有多聪明"：
  trace 强制（没执行过的步骤不许算完成）、否决能打回、轮数用尽转人工、
  Planner 乱输出不崩。LLM 与工具全部注入假的，不联网。

为什么这几条最重要：三角色的价值全在"说"不能脱离"做"。
一旦允许没有 trace 的步骤进验收，它就退化成一个大 prompt 自我检查 ——
那正是要避免的东西。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import orchestra as O  # noqa: E402

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


class FakeLLM:
    """按 role 回固定脚本，并可断言"Critic 看到的是事实"。"""

    def __init__(self, planner, critic, critic_seq=None):
        self.planner = planner
        self.critic = critic
        self.critic_seq = list(critic_seq or [])
        self.calls = []
        self.critic_prompts = []

    def __call__(self, prompt, role=""):
        self.calls.append(role)
        if role == O.ROLE_PLANNER:
            return self.planner
        if role == O.ROLE_CRITIC:
            self.critic_prompts.append(prompt)
            if self.critic_seq:
                return self.critic_seq.pop(0)
            return self.critic
        return "{}"


def main():
    print("[A] 正常一轮：计划 → 执行 → 验收通过")
    llm = FakeLLM(
        planner='{"steps":[{"id":"s1","goal":"建目录","tool":"mkdir","args":{"p":"d"}},'
                '{"id":"s2","goal":"写文件","tool":"write","args":{"p":"d/a.txt"}}]}',
        critic='{"ok":true,"issues":[]}')
    ran = []

    def runner(tool, args):
        ran.append((tool, args))
        return f"{tool} ok"

    r = O.run(llm, "把东西整理好", tool_runner=runner)
    check("★ 成功", r.get("ok") is True, r.get("status"))
    check("状态 done", r.get("status") == "done", r.get("status"))
    check("★ 两步都真的执行了", ran == [("mkdir", {"p": "d"}), ("write", {"p": "d/a.txt"})], ran)
    bb = r["blackboard"]
    check("★ trace 有两条", len(bb["trace"]) == 2, bb["trace"])
    check("trace 带轮次", all(t["round"] == 1 for t in bb["trace"]))
    check("★ 三个角色都出场过", set(llm.calls) == {O.ROLE_PLANNER, O.ROLE_CRITIC})

    print("\n[B] Critic 看到的是事实，不是 Executor 的自述")
    cp = llm.critic_prompts[0]
    check("★ 提示里带实际 trace", "mkdir" in cp and "trace" in cp.lower() or "实际执行事实" in cp, cp[:80])
    check("★ 带计划", "s1" in cp)

    print("\n[C] 否决能打回，第二轮通过")
    llm2 = FakeLLM(
        planner='{"steps":[{"id":"s1","goal":"x","tool":"t1","args":{}}]}',
        critic=None,
        critic_seq=['{"ok":false,"issues":["文件没真的落地"],"fix":"重写"}',
                    '{"ok":true,"issues":[]}'])
    r2 = O.run(llm2, "修一下", tool_runner=lambda t, a: "ok")
    check("★ 最终通过", r2.get("ok") is True, r2.get("status"))
    check("★ 跑了两轮", r2.get("rounds") == 2, r2.get("rounds"))
    check("★ trace 记了两轮", len({t["round"] for t in r2["blackboard"]["trace"]}) == 2)
    check("打了回并把 fix 记进 notes", any("打回" in n for n in r2["blackboard"]["notes"]),
          r2["blackboard"]["notes"])

    print("\n[D] 轮数用尽仍否决 → 转人工，不硬凑")
    llm3 = FakeLLM(planner='{"steps":[{"id":"s1","goal":"x","tool":"t1","args":{}}]}',
                   critic='{"ok":false,"issues":["还是不对"]}')
    r3 = O.run(llm3, "搞不定", tool_runner=lambda t, a: "ok", max_rounds=2)
    check("★ 不通过", r3.get("ok") is False)
    check("★ 状态 needs_human", r3.get("status") == "needs_human", r3.get("status"))
    check("带上了意见", bool(r3.get("issues")), r3.get("issues"))

    print("\n[E] trace 强制：没执行过的步骤不能被算完成")
    # Planner 给了两步，但 Executor 只执行第一步（模拟"跳步"）
    llm4 = FakeLLM(
        planner='{"steps":[{"id":"s1","goal":"a","tool":"t1","args":{}},'
                '{"id":"s2","goal":"b","tool":"t2","args":{}}]}',
        critic='{"ok":true,"issues":[]}')

    def half_runner(tool, args):
        return "ok"

    bb = O.Blackboard("x")
    bb.plan = O.plan(llm4, bb)
    O.execute(bb, lambda t, a: O.run.__self__ if False else half_runner, round_no=1)
    # 手工删掉第二步的 trace，等价于"它声称做了但没做"
    bb.trace = [t for t in bb.trace if t["step_id"] != "s2"]
    check("★ 能识别出缺 trace 的步骤", O.missing_trace(bb) == ["s2"], O.missing_trace(bb))

    # 走完整流程：缺 trace 时不送 Critic，直接判无效
    llm5 = FakeLLM(
        planner='{"steps":[{"id":"s1","goal":"a","tool":"","args":{}}]}',
        critic='{"ok":true,"issues":[]}')
    r5 = O.run(llm5, "跳过执行", tool_runner=lambda t, a: "ok")
    check("★ 没工具的空步骤不算完成", r5.get("ok") is False, r5.get("status"))
    check("★ 被判为需要人工", r5.get("status") == "needs_human", r5.get("status"))
    check("★ 没有把所有步骤当真（未误判为 done）",
          any("trace" in n or "没有可执行" in n for n in r5["blackboard"]["notes"])
          or not r5["blackboard"]["trace"] or
          all(not t["ok"] for t in r5["blackboard"]["trace"]),
          r5["blackboard"]["trace"])

    print("\n[F] Planner 乱输出不崩，转人工")
    for bad in ('我不是 JSON', '{"steps": []}', '{"nothing": 1}', ''):
        r = O.run(FakeLLM(bad, '{"ok":true}'), "目标", tool_runner=lambda t, a: "ok")
        check(f"非法计划 {bad[:14]!r} → needs_human",
              r.get("ok") is False and r.get("status") == "needs_human", r.get("status"))
    r = O.run(FakeLLM('{"steps":[]}', "{}"), "")
    check("空目标直接拒", r.get("status") == "rejected", r.get("status"))

    print("\n[G] 工具抛异常：记进 trace，Critic 能看到")
    llm6 = FakeLLM(planner='{"steps":[{"id":"s1","goal":"x","tool":"boom","args":{}}]}',
                   critic='{"ok":false,"issues":["工具报错"]}')

    def boom(t, a):
        raise RuntimeError("磁盘满了")

    r6 = O.run(llm6, "会失败的任务", tool_runner=boom, max_rounds=1)
    tr = r6["blackboard"]["trace"][0]
    check("★ trace 记了失败", tr["ok"] is False, tr)
    check("★ 错误信息保留", "磁盘满了" in tr["error"], tr["error"])
    check("★ 提示里带了 error", "磁盘满了" in llm6.critic_prompts[0], llm6.critic_prompts[0][-120:])

    print("\n[H] JSON 提取容错")
    check("围栏里的 JSON", (O.extract_json('```json\n{"a":1}\n```') or {}).get("a") == 1)
    check("前后有废话", (O.extract_json('好的：{"a":2} 以上') or {}).get("a") == 2)
    check("非 JSON 返回 None", O.extract_json("没有") is None)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
