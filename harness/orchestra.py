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
import os
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
    """三角色共用的事实板。所有"结论"必须挂到某条 trace 上。

    1.6.0：新增 `save()/load()/from_dict()` —— 黑板整块落盘，崩溃/换会话后
    能按原样恢复（plan、trace、issues、rounds、status、notes 全在）。
    `to_dict()` 的键保持兼容，save 写的就是它。
    """

    def __init__(self, goal, path=""):
        self.goal = str(goal or "").strip()
        self.plan = []           # [{id, goal, tool, args}]
        self.trace = []          # [{tid, round, step_id, tool, ok, output, error}]
        self.issues = []         # Critic 的否决意见
        self.rounds = 0
        self.status = "planning"
        self.notes = []
        self.path = str(path or "")

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

    # ── 1.6.0 落盘 / 恢复 ─────────────────────────────────────
    def save(self, path="") -> str:
        """把黑板原子写盘，返回实际路径；没给路径且没设 self.path 时返回空串。"""
        p = str(path or self.path or "").strip()
        if not p:
            return ""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=1, default=str)
            os.replace(tmp, p)
            self.path = p
            return p
        except OSError:
            return ""

    @classmethod
    def from_dict(cls, d: dict, path: str = "") -> "Blackboard":
        bb = cls(d.get("goal") or "", path=path)
        bb.plan = list(d.get("plan") or [])
        bb.trace = list(d.get("trace") or [])
        bb.issues = list(d.get("issues") or [])
        bb.rounds = int(d.get("rounds") or 0)
        bb.status = str(d.get("status") or "planning")
        bb.notes = list(d.get("notes") or [])
        return bb

    @classmethod
    def load(cls, path: str) -> "Blackboard":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return cls.from_dict(d, path=path)


def _planner_prompt(goal, tools=None, config=None, max_steps=MAX_STEPS):
    names = [str(x) for x in (tools or []) if str(x).strip()]
    tip = ("\n可用工具（只能从这些名字里选，选不到就留空 = 该步只是说明）：\n"
           + ", ".join(names[:80]) + "\n") if names else ""
    extra = ""
    if config:
        extra = "\n本角色附加配置：\n" + json.dumps(config, ensure_ascii=False) + "\n"
    return (
        "你是 Planner，只输出计划，不要执行、不要下结论。\n"
        "输出严格 JSON：{\"steps\":[{\"id\":\"s1\",\"goal\":\"...\",\"tool\":\"工具名或空\","
        "\"args\":{}}]}\n"
        f"最多 {max_steps} 步。任务：{goal}\n" + tip + extra
    )


def _critic_prompt(goal, bb, config=None):
    facts = [{"step": t.get("step_id"), "tool": t.get("tool"),
              "ok": t.get("ok"), "output": _digest(t.get("output", "")),
              "error": _digest(t.get("error", ""))} for t in bb.trace]
    extra = ""
    if config:
        extra = "\n本角色附加配置：\n" + json.dumps(config, ensure_ascii=False) + "\n"
    return (
        "你是 Critic，只根据下面的事实挑毛病，不要相信任何没有事实支持的完成声明。\n"
        "输出严格 JSON：{\"ok\":true/false,\"issues\":[\"...\"],\"fix\":\"给 Executor 的指示\"}\n"
        f"任务：{goal}\n计划：{json.dumps(bb.plan, ensure_ascii=False)}\n"
        f"实际执行事实：{json.dumps(facts, ensure_ascii=False)}\n"
        f"无执行的计划步骤（只是说明，不算完成）："
        f"{json.dumps([s.get('id') for s in bb.plan if not s.get('tool')], ensure_ascii=False)}\n"
        + extra
    )


def plan(llm, bb, tools=None, *, config=None, max_steps=None):
    """Planner：出计划。解析失败返回 None，由调用方转人工。

    1.6.0 隔离参数（都可选，默认全 None = 原行为）：
      config    该角色附加配置，追加进 prompt；
      max_steps 该角色的步骤预算（<= MAX_STEPS）。
    """
    ms = MAX_STEPS
    if max_steps is not None:
        try:
            ms = max(1, min(MAX_STEPS, int(max_steps)))
        except (TypeError, ValueError):
            pass
    out = llm(_planner_prompt(bb.goal, tools, config=config, max_steps=ms),
              role=ROLE_PLANNER)
    obj = extract_json(out) or {}
    steps = obj.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    clean = []
    for i, s in enumerate(steps[:ms]):
        if not isinstance(s, dict):
            continue
        clean.append({
            "id": str(s.get("id") or ("s%d" % (i + 1))),
            "goal": str(s.get("goal") or "").strip(),
            "tool": str(s.get("tool") or "").strip(),
            "args": s.get("args") if isinstance(s.get("args"), dict) else {},
        })
    return clean or None


def execute(bb, tool_runner, *, round_no=1, allowed_tools=None, max_calls=None):
    """Executor：逐步执行，每步必定留 trace（成功也留）。

    1.6.0 隔离参数（默认 None = 原行为）：
      allowed_tools 允许调用的工具名集合（工具集隔离）：越界调用记一条 ok=False 的 trace；
      max_calls     本轮最多执行几次工具（预算隔离）：超预算的步骤不执行、记 note。
    """
    calls = 0
    allowed = set(str(x) for x in allowed_tools) if allowed_tools is not None else None
    try:
        cap = int(max_calls) if max_calls is not None else None
    except (TypeError, ValueError):
        cap = None
    for step in bb.plan:
        if not step.get("tool"):
            # 没有工具的步骤是**计划说明**，不是执行动作：不产生 trace，也不算完成。
            bb.notes.append(f"步骤 {step['id']} 无工具：{step.get('goal') or ''}".strip())
            continue
        if cap is not None and calls >= cap:
            bb.notes.append(f"步骤 {step['id']} 超出 Executor 预算，未执行".strip())
            continue
        if allowed is not None and step["tool"] not in allowed:
            bb.add_trace({"round": round_no, "step_id": step["id"],
                          "tool": step["tool"], "ok": False, "output": "",
                          "error": f"工具 {step['tool']} 不在该角色允许集内"})
            calls += 1
            continue
        try:
            out = tool_runner(step["tool"], dict(step.get("args") or {}))
            ok = True
            err = ""
        except Exception as e:
            out, ok, err = "", False, f"{type(e).__name__}: {e}"
        bb.add_trace({"round": round_no, "step_id": step["id"], "tool": step["tool"],
                      "ok": ok, "output": out, "error": err})
        calls += 1
    return bb.trace


def missing_trace(bb):
    """哪些计划步骤一条 trace 都没有 —— 幻觉的直接证据。"""
    have = {t.get("step_id") for t in bb.trace}
    # 只检查"应当执行"的步骤：没有工具的步骤是计划说明，不在此列
    return [s["id"] for s in bb.plan if s.get("tool") and s.get("id") not in have]


def critique(llm, bb, *, config=None):
    out = llm(_critic_prompt(bb.goal, bb, config=config), role=ROLE_CRITIC)
    obj = extract_json(out) or {}
    issues = obj.get("issues")
    if not isinstance(issues, list):
        issues = [] if obj.get("ok") is True else ["Critic 输出无法解析为判据"]
    return {"ok": bool(obj.get("ok")) and not issues,
            "issues": [str(x) for x in issues],
            "fix": str(obj.get("fix") or "")}


def run(llm, goal, *, tool_runner=None, max_rounds=MAX_ROUNDS, require_trace=True,
        tools=None, role_config=None, role_tools=None, role_budget=None,
        blackboard=None, save_path=""):
    """跑一轮三角色。返回 report（含黑板书），任何异常都不往外抛。

    1.6.0 新增（**默认全不传 = 与原行为完全一致**）：
      role_config  {role: dict}  三角色各自的附加配置（进各自 prompt）
      role_tools   {role: [..]}  Planner 可见工具集 / Executor 允许调用的工具集
      role_budget  {role: int}   Planner 步数 / Executor 单轮调用数 / Critic 轮数预算
      blackboard   传入一个已恢复的 Blackboard 续跑（跳过规划，从 rounds+1 继续）
      save_path    黑板落盘路径，每个阶段写一次，崩溃可 `Blackboard.load()` 恢复
    """
    rc = role_config if isinstance(role_config, dict) else {}
    rt = role_tools if isinstance(role_tools, dict) else {}
    rb = role_budget if isinstance(role_budget, dict) else {}

    plan_tools = rt.get(ROLE_PLANNER, tools)
    plan_cfg = rc.get(ROLE_PLANNER)
    plan_budget = rb.get(ROLE_PLANNER)
    exec_tools = rt.get(ROLE_EXECUTOR)
    exec_budget = rb.get(ROLE_EXECUTOR)
    critic_cfg = rc.get(ROLE_CRITIC)
    critic_budget = rb.get(ROLE_CRITIC)
    if critic_budget is not None:
        try:
            max_rounds = max(1, int(critic_budget))
        except (TypeError, ValueError):
            pass

    resuming = isinstance(blackboard, Blackboard) and bool(blackboard.plan)
    bb = blackboard if isinstance(blackboard, Blackboard) else Blackboard(goal)
    sp = str(save_path or bb.path or "")

    def _save():
        if sp:
            bb.save(sp)

    if not bb.goal:
        return {"ok": False, "status": "rejected", "reason": "目标为空", "blackboard": bb.to_dict()}

    # ① 计划（恢复态已有计划 → 跳过，不再惊动 Planner）
    if resuming:
        steps = bb.plan
    else:
        try:
            steps = plan(llm, bb, plan_tools, config=plan_cfg, max_steps=plan_budget)
        except Exception as e:
            steps = None
            bb.notes.append(f"Planner 异常: {type(e).__name__}: {e}")
    if not steps:
        bb.status = "needs_human"
        _save()
        return {"ok": False, "status": "needs_human",
                "reason": "计划无法解析", "blackboard": bb.to_dict()}
    bb.plan = steps
    bb.status = "executing"
    _save()

    def _noop(name, args):
        raise RuntimeError(f"没有注入工具执行器，无法执行 {name}")

    runner = tool_runner or _noop

    verdict = None
    start = (bb.rounds + 1) if resuming else 1
    for r in range(start, max(start, max(1, int(max_rounds))) + 1):
        bb.rounds = r
        execute(bb, runner, round_no=r, allowed_tools=exec_tools, max_calls=exec_budget)
        _save()

        # ② trace 强制：没执行的步骤不许送审
        if require_trace:
            miss = missing_trace(bb)
            if miss:
                bb.issues = [f"步骤 {','.join(miss)} 没有任何执行 trace"]
                verdict = {"ok": False, "issues": bb.issues, "fix": "补执行缺失步骤"}
                bb.notes.append(f"第 {r} 轮：{verdict['issues'][0]}（判为幻觉，不送审）")
                _save()
                continue

        # ③ 验收
        try:
            verdict = critique(llm, bb, config=critic_cfg)
        except Exception as e:
            verdict = {"ok": False, "issues": [f"Critic 异常: {type(e).__name__}: {e}"], "fix": ""}
        bb.issues = verdict["issues"]
        if verdict["ok"]:
            bb.status = "done"
            _save()
            return {"ok": True, "status": "done", "rounds": r,
                    "issues": [], "blackboard": bb.to_dict()}

        # ④ 打回：把 Critic 的指示挂到计划上，下一轮 Executor 会重新执行
        if verdict.get("fix"):
            bb.notes.append(f"第 {r} 轮打回：{verdict['fix']}")
        _save()

    bb.status = "needs_human"
    _save()
    return {"ok": False, "status": "needs_human", "rounds": bb.rounds,
            "issues": bb.issues, "blackboard": bb.to_dict()}


__all__ = ["ROLE_PLANNER", "ROLE_EXECUTOR", "ROLE_CRITIC", "Blackboard",
           "extract_json", "plan", "execute", "critique", "missing_trace", "run",
           "MAX_ROUNDS", "MAX_STEPS"]
