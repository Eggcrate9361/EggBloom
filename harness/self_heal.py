# -*- coding: utf-8 -*-
"""harness/self_heal.py —— 自愈 R1/R2（FIXPLAN 1.5.11 §4.5 M2）

> 用户主线：agent 要能自己修自己。M1（可观测）已经能回答"哪条链路老失败"，
> M2 从这里开始：**配置被改坏了，它自己发现并改回去。**

只做 R1/R2，不碰 R3（改代码）——R3 留给 1.6.0 的 M3（沙箱 worktree + 最小 diff）。

    R1 配置自检与回滚：类型/范围校验 + 上一份"跑通过的配置"快照
    R2 快照自愈：快照本身损坏/丢失时，用当前有效配置重建

## 安全边界（很重要）

- **只修"明显坏了"的值**：类型不对、超出硬范围。缺键不修（缺键有 DEFAULT_CONFIG 兜底）。
- **只动白名单里的键**（开关 + 预算 + 记忆共享）。api_key / target_qq / 群列表一律不碰。
- 每次自动修复都写 `data/repairs.jsonl`（kind=config），事后可查、可回滚。
- `self_heal_enable` 关掉 → 只报告不写文件。

## 人工入口

    python -m harness.self_heal            # 体检
    python -m harness.self_heal --fix      # 体检 + 自动修
"""
import io
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_PATH = os.environ.get("EGGBLOOM_CONFIG_SNAPSHOT") or os.path.join(
    _ROOT, "data", "config_snapshot.json")

#: 白名单：只校验/只修这些键。key -> (类型, 最小值, 最大值)
#: bool 用 None 表示无范围；int 用范围兜底（防止 0 或负数把整条链冻住）。
WATCHED: Dict[str, tuple] = {
    # 评测台开关（1.5.11 新增，配置页「功能 → 评测台」）
    "eval_enabled": (bool, None, None),
    "eval_ban_ai_self": (bool, None, None),
    "eval_ban_backstage": (bool, None, None),
    "eval_require_colloquial": (bool, None, None),
    "eval_strict_budget": (bool, None, None),
    "eval_live_enable": (bool, None, None),
    "eval_max_chars": (int, 1, 80),
    "eval_max_parts": (int, 1, 5),
    "eval_max_total": (int, 1, 300),
    # 记忆/开关：这几项一关，表现是"它突然不记得我了"，最难自查
    "memory_scope_share": (bool, None, None),
    "private_memory_enable": (bool, None, None),
    "image_cache_enable": (bool, None, None),
    # 预算：0 或负数会让回复/工具直接哑掉
    "max_tool_rounds": (int, 1, 10),
    "private_reply_max_length": (int, 10, 2000),
    "group_reply_max_length": (int, 10, 4000),
}


def _log(msg: str) -> None:
    try:
        from utils import log as _l
        _l(msg)
    except Exception:
        print(msg)


def _cfg_module():
    import config as _c
    return _c


# ── 校验 ────────────────────────────────────────────────────────────

def validate(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """返回问题列表（空 = 健康）。只查 WATCHED 里的键。"""
    cfg = cfg if cfg is not None else _cfg_module().load_config()
    problems = []
    for key, (typ, lo, hi) in WATCHED.items():
        if key not in cfg:
            continue          # 缺键交给 DEFAULT_CONFIG，不算坏
        val = cfg[key]
        if typ is bool:
            if not isinstance(val, bool):
                problems.append({"key": key, "problem": "类型不是布尔",
                                 "value": repr(val)[:40], "expect": "true/false"})
            continue
        if typ is int:
            ok_int = isinstance(val, int) and not isinstance(val, bool)
            if not ok_int:
                problems.append({"key": key, "problem": "类型不是整数",
                                 "value": repr(val)[:40], "expect": "整数"})
                continue
            if lo is not None and val < lo:
                problems.append({"key": key, "problem": f"小于下限 {lo}",
                                 "value": val, "expect": f">= {lo}"})
            elif hi is not None and val > hi:
                problems.append({"key": key, "problem": f"大于上限 {hi}",
                                 "value": val, "expect": f"<= {hi}"})
    return problems


# ── 快照（R1 的回滚依据 / R2 的自愈对象）──────────────────────────────

def save_snapshot(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """把"当前这份配置"存成快照。调用方应当只在**校验通过**时存。"""
    try:
        cfg = cfg if cfg is not None else _cfg_module().load_config()
        if validate(cfg):
            return False
        os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
        with io.open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        _log(f"[自愈] 写快照失败: {e}")
        return False


def load_snapshot() -> Optional[Dict[str, Any]]:
    """读快照；坏了（JSON 断掉/不是 dict）返回 None，由 R2 重建。"""
    try:
        with io.open(SNAPSHOT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def heal_snapshot(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """R2：快照缺失/损坏 → 用当前有效配置重建。返回 {rebuilt, reason}。"""
    cur = cfg if cfg is not None else _cfg_module().load_config()
    snap = load_snapshot()
    if snap is not None:
        return {"rebuilt": False, "reason": ""}
    reason = "快照缺失或损坏"
    ok = save_snapshot(cur)
    if ok:
        _log(f"[自愈] {reason}，已用当前配置重建快照")
        try:
            from harness.trace_tools import record_repair
            record_repair("snapshot", "config_snapshot", "重建快照", True, reason)
        except Exception:
            pass
    return {"rebuilt": ok, "reason": reason if ok else reason + "（重建失败）"}


# ── 自愈主流程 ──────────────────────────────────────────────────────

def heal(cfg: Optional[Dict[str, Any]] = None, *, apply: bool = True) -> Dict[str, Any]:
    """体检 + 自动修。返回 {problems, fixed, unfixable, applied}。

    - 有快照且快照里该键合法 → 用快照值回填
    - 没快照/快照也坏 → 退回 DEFAULT_CONFIG 的默认值
    - 修完写回 bot_config.json（save_config）并记一条 repairs
    """
    cm = _cfg_module()
    cur = dict(cfg if cfg is not None else cm.load_config())
    problems = validate(cur)
    if not problems:
        return {"problems": [], "fixed": [], "unfixable": [], "applied": False}

    snap = load_snapshot() or {}
    defaults = getattr(cm, "DEFAULT_CONFIG", {}) or {}
    fixed, unfixable = [], []
    for p in problems:
        key = p["key"]
        typ, lo, hi = WATCHED[key]
        candidate = snap.get(key, defaults.get(key))
        if candidate is None:
            unfixable.append({**p, "why": "快照和默认值里都没有这个键"})
            continue
        # 候选值本身也要过同一套校验，别拿坏值覆盖坏值
        probe = {key: candidate}
        if validate(probe):
            unfixable.append({**p, "why": "候选值也不合法"})
            continue
        cur[key] = candidate
        fixed.append({"key": key, "from": p["value"], "to": candidate,
                      "reason": p["problem"]})

    out = {"problems": problems, "fixed": fixed, "unfixable": unfixable,
           "applied": False}
    if not fixed:
        return out
    if not apply:
        return out

    try:
        ok = cm.save_config(cur)
    except Exception as e:
        ok = False
        _log(f"[自愈] 写回配置失败: {e}")
    out["applied"] = bool(ok)
    if ok:
        _log("[自愈] 已修正 " + "、".join(x["key"] for x in fixed))
        try:
            from harness.trace_tools import record_repair
            record_repair("config", ",".join(x["key"] for x in fixed),
                          "改回合法值", True,
                          "; ".join(f"{x['key']}: {x['from']} → {x['to']}" for x in fixed))
        except Exception:
            pass
    return out


def startup(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """启动时调一次：先自愈快照，再体检配置（可自动修），最后存一份好快照。"""
    out = {"snapshot": heal_snapshot(cfg)}
    out.update(heal(cfg))
    try:
        cm = _cfg_module()
        fresh = cm.load_config()
        if not validate(fresh):
            save_snapshot(fresh)
    except Exception:
        pass
    return out


if __name__ == "__main__":
    cm = _cfg_module()
    cfg = cm.load_config()
    probs = validate(cfg)
    print(f"体检 {len(cfg)} 个键（白名单 {len(WATCHED)} 项）：", end="")
    if not probs:
        print("健康")
    else:
        print(f"{len(probs)} 个问题")
        for p in probs:
            print(f"  - {p['key']}: {p['problem']}（当前 {p['value']}，应为 {p['expect']}）")
    if "--fix" in sys.argv:
        res = heal(cfg, apply=True)
        print(f"已修 {len(res['fixed'])} 项，修不了 {len(res['unfixable'])} 项")
        for f in res["fixed"]:
            print(f"  ✔ {f['key']}: {f['from']} → {f['to']}")
        for f in res["unfixable"]:
            print(f"  ✘ {f['key']}: {f['why']}")
    else:
        snap = load_snapshot()
        print("快照：", "有" if snap else "缺（跑 --fix 或用当前配置重建）")
