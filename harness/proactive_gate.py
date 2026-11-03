# -*- coding: utf-8 -*-
"""harness/proactive_gate.py —— 主动开口的三段式：Trigger → Eval → Decision（2026-11-03）

## 为什么要单独一层

`plugins/proactive_talk` 原来把"要不要开口"塞在一个 `_evaluate()` 里：挑人、算间隔、
看静默、掷概率、查 AI 全在一处，而且只有心跳一种触发。
执行总表 §7.2 第 2 条要的是三段分开：

    Trigger（谁因为什么被考虑） → Eval（该不该 / 能不能） → Decision（说、不说、稍后）

拆开之后三件事才有落点：

* **Trigger**：心跳（现在）、定时任务、事件（戳一戳 / 入站消息）、人工（`send_now`）
  产出同一形状的触发，共用一套 Eval —— 不再"每种触发各写一份闸"；
* **Decision.mode**：`live` 真发、`auto` **只记录不执行**；再加上
  `admission.rule` 里"推断来的候选"的判定，等于落实 §7.3 的验收原则
  ——凡是主动开口，都必须能指到**一条规则**或**一次明确请求**；
* **`admission()`**：把"谁允许被找"从隐式（只看配置里有谁）变成一条可打印的判据。

## 纪律（与 `plugins/autonomy/policy.py` 同一条）

纯函数：不 import 主程序、不起线程、不碰网络、不读盘。外部依赖
（静默时段、作息乘子、AI 可用性、随机源、时间）一律由调用方注入，
默认实现只在真机路径上**惰性导入** —— 所以能离线单测，也不会因为
某个模块没就绪就把主动开口永久关死。

## 行为不变

`evaluate()` 逐条复刻 2026-11-02 版 `proactive_talk._evaluate()` 的**判据顺序**与
**中文文案**，插件侧退化成转发（`project()` 压回旧返回形状）。
等价性由 `tests/test_proactive_gate.py` 对着"重构前那份实现"逐字段比对
（golden 思路同 `tests/_fixtures/life_dynamic_golden.json`：基准取自改造前的代码，
不是手抄的期望值）。
"""
from __future__ import annotations

import random
import re
import time
from datetime import datetime

# ── Trigger 种类 ──────────────────────────────────────────

#: 定时心跳（现在唯一在用的）
TRIGGER_HEARTBEAT = "heartbeat"
#: 定时任务/日程（1.6.1 的 scheduler 用）
TRIGGER_SCHEDULED = "scheduled"
#: 事件驱动（戳一戳 / 入站消息 / 撤回）
TRIGGER_EVENT = "event"
#: 人工触发（WebUI 的「现在就说一句」）
TRIGGER_MANUAL = "manual"

TRIGGER_KINDS = (TRIGGER_HEARTBEAT, TRIGGER_SCHEDULED, TRIGGER_EVENT, TRIGGER_MANUAL)

# ── Decision 模式 ─────────────────────────────────────────

#: 真发
MODE_LIVE = "live"
#: 只记录不执行（候选进日志，人工核对后再放行）
MODE_AUTO = "auto"

MODES = (MODE_LIVE, MODE_AUTO)

#: 允许"真发"的 admission 规则；其余规则一律只记录（§7.3）
EXECUTABLE_RULES = ("configured_target", "explicit_request")


def _parse_qqs(raw) -> list:
    """`target_qqs` 的各种形状（list / 逗号 / 换行）→ 去重列表。

    与 `plugins/proactive_talk._parse_qqs` 同源：这里保留一份是因为
    纯函数层不能反过来 import 插件（会成环），而"怎么解析目标"必须只有一种口径。
    """
    if isinstance(raw, str):
        raw = re.split(r"[,，;；\s]+", raw)
    if not isinstance(raw, (list, tuple, set)):
        return []
    seen, out = set(), []
    for x in raw:
        q = str(x or "").strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


# ── Trigger ───────────────────────────────────────────────


def make_trigger(kind: str, *, target: str = "", at: float | None = None,
                 source: str = "", meta: dict | None = None) -> dict:
    """造一条触发。

    认不出的 kind 按 `event` 记 —— 宁可记一笔，也不要静默丢掉一次触发
    （丢掉的触发在日志里查不出来，比多记一条更糟）。
    """
    k = str(kind or "").strip().lower()
    if k not in TRIGGER_KINDS:
        k = TRIGGER_EVENT
    return {
        "kind": k,
        "target": str(target or "").strip(),
        "at": float(at if at is not None else time.time()),
        "source": str(source or ""),
        "meta": dict(meta or {}),
    }


# ── 目标与归属 ────────────────────────────────────────────


def resolve_targets(cfg: dict, *, main_targets=None) -> list:
    """这一轮要考虑的全部目标（有序）。

    优先级：`target_qqs`（多目标）→ `target_qq`（旧单目标）→ 主配置 `target_qqs`。
    与插件侧同名函数同一口径；插件把自己的实现注入进来（因为它要读 `_ctx`）。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    out = _parse_qqs(cfg.get("target_qqs"))
    single = str(cfg.get("target_qq") or "").strip()
    if single and single not in out:
        out.append(single)
    if not out and main_targets is not None:
        out = _parse_qqs(main_targets)
    return out


def admission(target: str, *, targets=None, cfg: dict | None = None,
              explicit: bool = False) -> dict:
    """谁允许被主动找 —— 唯一判定点。

    返回 `{allowed, target, rule, reason}`；rule ∈

    * `configured_target` —— 在配置的目标名单里（允许真发）
    * `explicit_request` —— 本次调用点名叫它（`send_now(target=…)`），允许真发
    * `not_admitted`     —— 不在名单、也不是点名来的（**只记录，不执行**）
    * `none`             —— 根本没有目标

    今天就靠前两条放行：目标本来就只来自配置，所以行为与改造前一致；
    `not_admitted` 是给"将来会推断候选人"留的口子，且默认只记录。
    """
    t = str(target or "").strip()
    ts = [str(x) for x in (targets if targets is not None
                           else resolve_targets(cfg if isinstance(cfg, dict) else {}))]
    if not t:
        return {"allowed": False, "target": "", "rule": "none", "reason": "没有目标"}
    if t in ts:
        return {"allowed": True, "target": t, "rule": "configured_target", "reason": ""}
    if explicit:
        return {"allowed": True, "target": t, "rule": "explicit_request",
                "reason": "本次调用点名了这个目标"}
    return {"allowed": False, "target": t, "rule": "not_admitted",
            "reason": f"{t} 不在允许主动找的名单里"}


def last_user_ts(state: dict, target: str) -> float:
    """这个人最后一次私聊说话的时间（没有就退回全局旧字段）。"""
    st = state if isinstance(state, dict) else {}
    m = st.get("last_user_ts_by") or {}
    if isinstance(m, dict) and target:
        try:
            v = m.get(str(target))
            if v:
                return float(v)
        except Exception:
            pass
    return float(st.get("last_user_ts") or 0)


def waits_for_person(state: dict, target: str) -> bool:
    """我们最后说的话，对方还没回 → 正在等这个人。

    只给 Decision 的 **auto** 模式用（用来把"候选"标得更准）。
    live 路径不看它 —— 否则就改了既有行为。
    """
    st = state if isinstance(state, dict) else {}
    t = str(target or "").strip()
    if not t:
        return False
    by = st.get("last_proactive_by") or {}
    try:
        last_say = float((by.get(t) if isinstance(by, dict) else 0) or 0)
    except Exception:
        last_say = 0.0
    if not last_say:
        try:
            last_say = float(st.get("last_proactive_ts") or 0)
        except Exception:
            last_say = 0.0
    return bool(last_say) and last_say > last_user_ts(st, t)


# ── Eval ──────────────────────────────────────────────────

_NOTE_NO_TARGET = "未配置目标 QQ（插件 target_qqs/target_qq 与主配置 target_qqs 均为空）"


def _default_quiet_hours(spec, when) -> bool:
    from harness.time_ctx import in_quiet_hours
    return in_quiet_hours(spec, when)


def _default_probability_factor():
    from harness.bio_rhythm import proactivity_factor
    return proactivity_factor()


def _default_ai_ready() -> bool:
    """默认认为 AI 就绪：真机路径由插件注入它自己的检查（只看 api_url）。"""
    return True


def evaluate(trigger: dict, cfg: dict, state: dict, *, force: bool = False,
             now: float | None = None, rng=None, now_dt=None,
             resolve=None, last_user=None, quiet_hours=None,
             probability_factor=None, ai_ready=None) -> dict:
    """Eval：这条触发该不该、能不能开口。

    返回 **超集**：`{ok, target, blocked_by, target_known, trigger, admission, gate, gate_log}`。
    压回旧形状用 `project()`。

    判据顺序（**改动会改行为，别随手调**）：
    目标 → force → 按人最小间隔 → 用户发言冷却 → 静默时段 → 概率（含作息乘子）→ AI 就绪

    时间口径：`now` 不给就取**求值那一刻**（与改造前一致）；`trigger["at"]` 只作留痕，
    不参与判据 —— 免得"拿一条五分钟前的触发来求值"把间隔算成五分钟前。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    state = state if isinstance(state, dict) else {}
    trg = trigger if isinstance(trigger, dict) else make_trigger(TRIGGER_EVENT)
    kind = str(trg.get("kind") or TRIGGER_EVENT)
    now = float(time.time() if now is None else now)
    target = str(trg.get("target") or "").strip() or ""
    explicit = bool(target)

    resolver = resolve or (lambda c: resolve_targets(c))
    configured = list(resolver(cfg) or [])
    targets = list(configured)
    if target and target not in targets:
        targets = [target] + targets
    if not targets:
        return {
            "ok": False, "target": "", "blocked_by": _NOTE_NO_TARGET,
            "target_known": False, "trigger": kind,
            "admission": admission("", targets=[], explicit=explicit),
            "gate": "targets", "gate_log": [("targets", "block")],
        }

    target = target or targets[0]
    adm = admission(target, targets=targets, explicit=explicit)
    log = [("targets", "pass")]
    if explicit and target not in configured:
        log.append(("targets", "explicit"))

    if force:
        return {"ok": True, "target": target, "blocked_by": "", "target_known": True,
                "trigger": kind, "admission": adm, "gate": "force",
                "gate_log": log + [("force", "pass")]}

    # ① 最小间隔（**按人**算：A 刚被搭话不该连累 B；旧状态没有
    #    `last_proactive_by` 时退回全局 `last_proactive_ts`）
    by = state.get("last_proactive_by") or {}
    last_pro = float((by.get(target) if isinstance(by, dict) else 0) or 0)
    if not last_pro:
        last_pro = float(state.get("last_proactive_ts") or 0)
    if last_pro and now - last_pro < float(cfg.get("min_interval_min") or 180) * 60:
        left = int((float(cfg.get("min_interval_min") or 180) * 60 - (now - last_pro)) // 60)
        return {"ok": False, "target": target, "target_known": True, "trigger": kind,
                "admission": adm, "gate": "min_interval", "gate_log": log + [("min_interval", "block")],
                "blocked_by": f"距上次和 TA 说话不足最小间隔（还需 {left} 分钟）"}
    log.append(("min_interval", "pass"))

    # ② 用户刚说过话 → 冷却（**按人**算：旧版看全局 `last_user_ts`，
    #    B 刚说完话会把对 A 的主动开口一起挡掉，反过来也会误放行）
    lu = (last_user or last_user_ts)(state, target)
    if lu and now - lu < float(cfg.get("active_cooldown_min") or 30) * 60:
        left = int((float(cfg.get("active_cooldown_min") or 30) * 60 - (now - lu)) // 60)
        return {"ok": False, "target": target, "target_known": True, "trigger": kind,
                "admission": adm, "gate": "active_cooldown", "gate_log": log + [("active_cooldown", "block")],
                "blocked_by": f"用户刚说过话，冷却中（还需 {left} 分钟）"}
    log.append(("active_cooldown", "pass"))

    # ③ 静默时段（硬闸）
    qh = quiet_hours or _default_quiet_hours
    if qh(cfg.get("quiet_hours"), now_dt if now_dt is not None else datetime.now()):
        return {"ok": False, "target": target, "target_known": True, "trigger": kind,
                "admission": adm, "gate": "quiet_hours", "gate_log": log + [("quiet_hours", "block")],
                "blocked_by": f"处于静默时段 {cfg.get('quiet_hours')}"}
    log.append(("quiet_hours", "pass"))

    # ④ 概率闸门。作息乘子是**软闸**：`quiet_hours` 是硬闸（配了就完全不开口），
    #    但它默认为空 = 凌晨照样说话；乘子按时段给概率打折（凌晨 ×0.15、晚上 ×1.2）。
    #    乘法排在硬闸之后，所以永远绕不过硬闸；乘子取不到就按 1.0，
    #    不因为环境问题把主动开口永久关死。
    chance = float(cfg.get("speak_chance") or 0.35)
    _bio = 1.0
    if cfg.get("bio_rhythm", True):
        try:
            _bio = (probability_factor or _default_probability_factor)()
            chance = min(1.0, chance * _bio)
        except Exception:
            _bio = 1.0
    if (rng or random.random)() >= chance:
        reason = (f"概率闸门未通过（chance={chance:.3f}"
                  + (f"，作息乘子={_bio:.2f}）" if _bio != 1.0 else "）"))
        return {"ok": False, "target": target, "target_known": True, "trigger": kind,
                "admission": adm, "gate": "probability", "gate_log": log + [("probability", "block")],
                "blocked_by": reason}
    log.append(("probability", "pass"))

    # ⑤ AI 就绪（放在概率之后：只有"本来就要开口"的那部分才付探测成本）
    if not (ai_ready or _default_ai_ready)():
        return {"ok": False, "target": target, "target_known": True, "trigger": kind,
                "admission": adm, "gate": "ai_ready", "gate_log": log + [("ai_ready", "block")],
                "blocked_by": "AI 未配置，暂不主动开口"}
    log.append(("ai_ready", "pass"))

    return {"ok": True, "target": target, "blocked_by": "", "target_known": True,
            "trigger": kind, "admission": adm, "gate": "", "gate_log": log}


# ── Decision ──────────────────────────────────────────────


def decide(evaluation: dict, *, mode: str = MODE_LIVE,
           waits_for_person: bool = False) -> dict:
    """Eval 结果 → 动作。返回 `{act, target, mode, recorded, reason, ...}`。

    act ∈ `say` / `none`。三种"不说"要分清：

    * `ok=False`        —— 闸门拦了，`reason` 就是 `blocked_by`；
    * `mode=auto`       —— 不是不许说，是**这一轮只记录**（人工核对后再放行）；
    * `admission` 非白名单 —— 推断来的候选只记录（§7.3 验收原则）。
    """
    ev = evaluation if isinstance(evaluation, dict) else {}
    m = str(mode or MODE_LIVE).strip().lower()
    if m not in MODES:
        m = MODE_LIVE
    target = str(ev.get("target") or "")
    rule = str((ev.get("admission") or {}).get("rule") or "")
    base = {
        "target": target, "mode": m,
        "waits_for_person": bool(waits_for_person),
        "gate": str(ev.get("gate") or ""),
        "admission_rule": rule,
        "blocked_by": str(ev.get("blocked_by") or ""),
    }
    if not ev.get("ok"):
        return {**base, "act": "none", "recorded": False, "reason": base["blocked_by"]}
    if rule and rule not in EXECUTABLE_RULES:
        return {**base, "act": "none", "recorded": True,
                "reason": f"admission={rule}：推断来的候选只记录不执行"}
    if m == MODE_AUTO:
        reason = "auto 模式：只记录不执行"
        if waits_for_person:
            reason += "（对方还没回上一条）"
        return {**base, "act": "none", "recorded": True, "reason": reason}
    return {**base, "act": "say", "recorded": False, "reason": ""}


def gate(trigger: dict, cfg: dict, state: dict, *, mode: str = MODE_LIVE,
         force: bool = False, **eval_kw) -> dict:
    """`evaluate` + `decide` 一把过。返回 `{trigger, eval, decision}`。"""
    ev = evaluate(trigger, cfg, state, force=force, **eval_kw)
    dec = decide(ev, mode=mode, waits_for_person=waits_for_person(state, ev.get("target") or ""))
    return {"trigger": trigger, "eval": ev, "decision": dec}


# ── 与旧返回形状对齐 ──────────────────────────────────────


def project(evaluation: dict) -> dict:
    """压回 2026-11-02 版 `_evaluate()` 的返回形状。

    注意只有"没有目标"那一条**不带 `target` 键** —— 旧实现如此，保持逐字节一致，
    免得既有调用方从 `d.get("target")` 拿到空串而不是缺键。
    """
    ev = evaluation if isinstance(evaluation, dict) else {}
    out = {"ok": bool(ev.get("ok"))}
    if ev.get("target_known"):
        out["target"] = str(ev.get("target") or "")
    out["blocked_by"] = str(ev.get("blocked_by") or "")
    return out


__all__ = [
    "TRIGGER_HEARTBEAT", "TRIGGER_SCHEDULED", "TRIGGER_EVENT", "TRIGGER_MANUAL",
    "TRIGGER_KINDS", "MODE_LIVE", "MODE_AUTO", "MODES", "EXECUTABLE_RULES",
    "make_trigger", "resolve_targets", "admission", "last_user_ts", "waits_for_person",
    "evaluate", "decide", "gate", "project",
]
