# -*- coding: utf-8 -*-
"""harness/orchestra.py —— 子 Agent 三角色编排（1.6.0 P2 第①刀）

## 这一层解决什么

单模型一口气"想 + 做"最容易出的问题是**连环幻觉**：它说"我已经把文件整理好了"，
其实什么都没做；下一步又基于这个假前提继续编。三个角色分工是为了让"说"和"做"
分开，而 **trace** 是让"说"无法脱离"做"的唯一手段。

    Planner  只出计划（步骤 + 用什么工具 + 参数），不下结论
    Executor 只执行（调工具），每步必须留下 trace
    Critic   只验收（对着 trace 与实际产物挑毛病），能否决并打回

## 三条硬约束

① **产出必须带 trace**：任何没有 trace 的步骤在进 Critic 之前就被判为无效。
   这不是日志，是判据 —— 没执行过的步骤不许算完成。
② **否决要能打回**：Critic 说 ok=false 时带着 issues 回到 Executor 重做，
   轮数有上限，用尽仍不过 → `needs_human`，不硬凑。
③ **LLM 与工具都可注入**：测试用假的，不真调模型、不真动磁盘。

## 为什么不用一个大 prompt

因为"自己检查自己"在同一个上下文里几乎没有约束力 —— 模型会顺着自己上一步的说法
继续编。换一个只读 trace 的角色来挑毛病，判据才落得到事实上。
"""
from __future__ import annotations

import json
import re
import time

ROLE_PLANNER = "planner"
ROLE_EXECUTOR = "executor"
ROLE_CRITIC = "critic"

MAX_ROUNDS = 2          # Executor↔Critic 最多来回几轮
MAX_STEPS = 12          # 一份计划最多几步（防"计划本身发散"）


def _now():
    return time.time()


def _digest(x, limit=160):
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, sort_keys=True)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def extract_json(text):
    """从模型输出里取第一个 JSON 对象（模型常带 ```json 围栏或前后废话）。"""
    if not isinstance(text, str):
        return None
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if fence:
        t = fence.group(1)
    else:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            t = t[i:j + 1]
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


class Blackboard:
    """三角色共用的事实板。所有"结论"必须挂到某条 trace 上。"""

    def __init__(self, goal):
        self.goal = str(goal or "").strip()
        self.plan = []           # [{id, goal, tool, args}]
        self.trace = []          # [{tid, round, step_id, tool, ok, output, error}]
        self.issues = []         # Critic 的否决意见
        self.rounds = 0
        self.status = "planning"
        self.notes = []

    def add_trace(self, entry):
        entry = dict(entry)
        entry.setdefault("tid", "t%d" % (len(self.trace) + 1))
        entry.setdefault("at", _now())
        self.trace.append(entry)
        return entry["tid"]

    def traces_for(self, step_id):
        return [t for t in self.trace if t.get("step_id") == step_id]

    def to_dict(self):
        return {"goal": self.goal, "plan": self.plan, "trace": self.trace,
                "issues": self.issues, "rounds": self.rounds, "status": self.status,
                "notes": self.notes}


def _planner_prompt(goal):
    return (
        "你是 Planner，只输出计划，不要执行、不要下结论。\n"
        "输出严格 JSON：{\"steps\":[{\"id\":\"s1\",\"goal\":\"...\",\"tool\":\"工具名或空\","
        "\"args\":{}}]}\n"
        f"最多 {MAX_STEPS} 步。任务：{goal}\n"
    )


def _critic_prompt(goal, bb):
    facts = [{"step": t.get("step_id"), "tool": t.get("tool"),
              "ok": t.get("ok"), "output": _digest(t.get("output", "")),
              "error": _digest(t.get("error", ""))} for t in bb.trace]
    return (
        "你是 Critic，只根据下面的事实挑毛病，不要相信任何没有事实支持的完成声明。\n"
        "输出严格 JSON：{\"ok\":true/false,\"issues\":[\"...\"],\"fix\":\"给 Executor 的指示\"}\n"
        f"任务：{goal}\n计划：{json.dumps(bb.plan, ensure_ascii=False)}\n"
        f"实际执行事实：{json.dumps(facts, ensure_ascii=False)}\n"
    )


def plan(llm, bb):
    """Planner：出计划。解析失败返回 None，由调用方转人工。"""
    out = llm(_planner_prompt(bb.goal), role=ROLE_PLANNER)
    obj = extract_json(out) or {}
    steps = obj.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    clean = []
    for i, s in enumerate(steps[:MAX_STEPS]):
        if not isinstance(s, dict):
            continue
        clean.append({
            "id": str(s.get("id") or ("s%d" % (i + 1))),
            "goal": str(s.get("goal") or "").strip(),
            "tool": str(s.get("tool") or "").strip(),
            "args": s.get("args") if isinstance(s.get("args"), dict) else {},
        })
    return clean or None


def execute(bb, tool_runner, *, round_no=1):
    """Executor：逐步执行，每步必定留 trace（成功也留）。"""
    for step in bb.plan:
        if not step.get("tool"):
            # 没有工具的步骤只登记"没有可执行动作" —— 它不能被当成已完成
            bb.add_trace({"round": round_no, "step_id": step["id"], "tool": "",
                          "ok": False, "output": "", "error": "该步骤没有可执行的工具"})
            continue
        try:
            out = tool_runner(step["tool"], dict(step.get("args") or {}))
            ok = True
            err = ""
        except Exception as e:
            out, ok, err = "", False, f"{type(e).__name__}: {e}"
        bb.add_trace({"round": round_no, "step_id": step["id"], "tool": step["tool"],
                      "ok": ok, "output": out, "error": err})
    return bb.trace


def missing_trace(bb):
    """哪些计划步骤一条 trace 都没有 —— 幻觉的直接证据。"""
    have = {t.get("step_id") for t in bb.trace}
    return [s["id"] for s in bb.plan if s.get("id") not in have]


def invalid_steps(bb):
    """计划里"根本没有可执行动作"的步骤。

    为什么单列：这种步骤会留下一条 ok=False 的 trace，但它既不是"执行失败"
    （没有执行过），也不该被验收当成完成 —— 它是**计划本身的缺陷**。
    早先只记 trace 就放行，结果 Critic 说 ok 时整轮就被判 done（冒烟时抓到）。
    """
    return [t.get("step_id") for t in bb.trace
            if not t.get("ok") and not str(t.get("tool") or "").strip()]


def critique(llm, bb):
    out = llm(_critic_prompt(bb.goal, bb), role=ROLE_CRITIC)
    obj = extract_json(out) or {}
    issues = obj.get("issues")
    if not isinstance(issues, list):
        issues = [] if obj.get("ok") is True else ["Critic 输出无法解析为判据"]
    return {"ok": bool(obj.get("ok")) and not issues,
            "issues": [str(x) for x in issues],
            "fix": str(obj.get("fix") or "")}


def run(llm, goal, *, tool_runner=None, max_rounds=MAX_ROUNDS, require_trace=True):
    """跑一轮三角色。返回 report（含黑板书），任何异常都不往外抛。"""
    bb = Blackboard(goal)
    if not bb.goal:
        return {"ok": False, "status": "rejected", "reason": "目标为空", "blackboard": bb.to_dict()}

    # ① 计划
    try:
        steps = plan(llm, bb)
    except Exception as e:
        steps = None
        bb.notes.append(f"Planner 异常: {type(e).__name__}: {e}")
    if not steps:
        bb.status = "needs_human"
        return {"ok": False, "status": "needs_human",
                "reason": "计划无法解析", "blackboard": bb.to_dict()}
    bb.plan = steps
    bb.status = "executing"

    def _noop(name, args):
        raise RuntimeError(f"没有注入工具执行器，无法执行 {name}")

    runner = tool_runner or _noop

    verdict = None
    for r in range(1, max(1, int(max_rounds)) + 1):
        bb.rounds = r
        execute(bb, runner, round_no=r)

        # ② trace 强制：没执行的步骤不许送审
        if require_trace:
            miss = missing_trace(bb)
            if miss:
                bb.issues = [f"步骤 {','.join(miss)} 没有任何执行 trace"]
                verdict = {"ok": False, "issues": bb.issues, "fix": "补执行缺失步骤"}
                bb.notes.append(f"第 {r} 轮：{verdict['issues'][0]}（判为幻觉，不送审）")
                continue
            # 计划里存在"没有可执行动作"的步骤：计划缺陷，同样不送审
            bad = invalid_steps(bb)
            if bad:
                bb.issues = [f"步骤 {','.join(bad)} 没有可执行动作（计划缺陷）"]
                verdict = {"ok": False, "issues": bb.issues, "fix": "补上工具或删掉该步骤"}
                bb.notes.append(f"第 {r} 轮：{verdict['issues'][0]}（不送审）")
                continue

        # ③ 验收
        try:
            verdict = critique(llm, bb)
        except Exception as e:
            verdict = {"ok": False, "issues": [f"Critic 异常: {type(e).__name__}: {e}"], "fix": ""}
        bb.issues = verdict["issues"]
        if verdict["ok"]:
            bb.status = "done"
            return {"ok": True, "status": "done", "rounds": r,
                    "issues": [], "blackboard": bb.to_dict()}

        # ④ 打回：把 Critic 的指示挂到计划上，下一轮 Executor 会重新执行
        if verdict.get("fix"):
            bb.notes.append(f"第 {r} 轮打回：{verdict['fix']}")

    bb.status = "needs_human"
    return {"ok": False, "status": "needs_human", "rounds": bb.rounds,
            "issues": bb.issues, "blackboard": bb.to_dict()}


__all__ = ["ROLE_PLANNER", "ROLE_EXECUTOR", "ROLE_CRITIC", "Blackboard",
           "extract_json", "plan", "execute", "critique", "missing_trace", "run",
           "MAX_ROUNDS", "MAX_STEPS"]
