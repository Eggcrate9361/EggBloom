# -*- coding: utf-8 -*-
"""plugin_manifest —— 插件清单（plugin.json）读取 / 校验 / 冲突检查（1.5.10 H1/H2）。

只读层：本版**不接运行行为**，只让清单可用（启动日志 + /api/plugins 展示）；
冲突只警告不拒绝 —— 1.6.0 才改成拒绝加载。

清单字段（全部可选，除 id）：
    id           插件标识（默认取目录名）
    version      版本
    description  一句话
    provides     能力列表，如 ["web.search", "reply.policy"]
    requires     依赖能力列表
    budget       预算声明，如 {"max_chars": 80, "daily_chars": 600}
    permissions  需要的外部权限，如 ["network", "fs"]
    scope        global / group / private
"""
from __future__ import annotations

import io
import json
import os

MANIFEST_NAME = "plugin.json"
_SCOPES = ("global", "group", "private")


def validate(m: dict):
    """校验清单结构，返回错误列表（空 = 通过）。只查类型，不查业务语义。"""
    errs = []
    if not isinstance(m, dict):
        return ["清单不是对象"]
    if not isinstance(m.get("id"), str) or not m.get("id", "").strip():
        errs.append("缺少 id")
    if m.get("title") is not None and not isinstance(m.get("title"), str):
        errs.append("title 必须是字符串")
    for key in ("provides", "requires", "permissions"):
        v = m.get(key, [])
        if v is None:
            continue
        if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
            errs.append(f"{key} 必须是字符串列表")
    b = m.get("budget")
    if b is not None and not isinstance(b, dict):
        errs.append("budget 必须是对象")
    sc = m.get("scope")
    if sc is not None and sc not in _SCOPES:
        errs.append(f"scope 只能是 {_SCOPES}")
    if m.get("priority") is not None:
        try:
            int(m.get("priority"))
        except (TypeError, ValueError):
            errs.append("priority 必须是整数（小的先上线、小的当能力主）")
    return errs


def load(plugin_dir: str, fallback_id: str = ""):
    """读一个插件目录的 plugin.json，返回 (manifest|None, errors)。"""
    p = os.path.join(plugin_dir, MANIFEST_NAME)
    if not os.path.isfile(p):
        return None, []
    try:
        m = json.loads(io.open(p, encoding="utf-8").read())
    except Exception as e:
        return None, [f"解析失败: {e}"]
    if not isinstance(m, dict):
        return None, ["清单不是 JSON 对象"]
    m.setdefault("id", fallback_id or os.path.basename(os.path.normpath(plugin_dir)))
    errs = validate(m)
    return (None if errs else m), errs


def read_all(root: str) -> dict:
    """读 root 下所有插件的清单：{id: manifest}（无清单的插件不出现）。"""
    out = {}
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if name.startswith((".", "_")) or not os.path.isdir(d):
            continue
        m, _errs = load(d, fallback_id=name)
        if m:
            out[m["id"]] = m
    return out


def check(manifests: dict) -> dict:
    """冲突检查（纯函数）：重复能力、缺失依赖、依赖环。

    返回 {"duplicate_provides": {cap: [ids]}, "missing_requires": [[id, cap]],
          "cycles": [[id, ...]]}
    """
    dup = {}
    providers = {}
    for pid, m in (manifests or {}).items():
        for cap in m.get("provides") or []:
            providers.setdefault(cap, []).append(pid)
    for cap, ids in providers.items():
        if len(ids) > 1:
            dup[cap] = sorted(ids)

    missing = []
    for pid, m in (manifests or {}).items():
        for cap in m.get("requires") or []:
            if cap not in providers:
                missing.append([pid, cap])

    # 依赖环：插件 → 该能力的所有提供者
    edges = {}
    for pid, m in (manifests or {}).items():
        edges[pid] = []
        for cap in m.get("requires") or []:
            edges[pid].extend(providers.get(cap, []))
    cycles = []
    state = {}

    def dfs(node, stack):
        state[node] = 1
        stack.append(node)
        for nxt in edges.get(node, []):
            if state.get(nxt) == 1:
                i = stack.index(nxt) if nxt in stack else -1
                cycles.append(stack[i:] if i >= 0 else stack[:] + [nxt])
            elif state.get(nxt, 0) == 0:
                dfs(nxt, stack)
        stack.pop()
        state[node] = 2

    for pid in edges:
        if state.get(pid, 0) == 0:
            dfs(pid, [])
    # 去重（同一环从不同起点被发现）
    uniq = []
    seen = set()
    for c in cycles:
        key = tuple(sorted(c))
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return {"duplicate_provides": dup, "missing_requires": missing, "cycles": uniq}


# ══════════════════════════════════════════════════════════════
# 启动期强制：把"只警告"升级成"算出计划再动手"（2026-11-03）
# ══════════════════════════════════════════════════════════════
#
# 执行总表 P1-3 / §8.1 #1 的四条判据，全部在这里算完，`plugin_manager.load_all()`
# 只负责照单执行 —— 判定只有一处，避免"页面说拒载、实际又加载了"。
#
#   ① 拓扑排序：依赖先上线
#   ② 能力冲突：同一 capability 多提供者 → 按 priority 选主，其余标 standby
#   ③ 缺失依赖 / 依赖环 / 提供者被拒 → **拒载**（并给出人能看懂的原因）
#   ④ 同一 hook 多订阅 → 只报告；调用方必须用 execute_hook_chain（见 hook_chains）
#
# 判据都是纯函数：不读盘、不 import 插件、不碰 manager，所以能离线单测。

#: 清单没写 priority 时的默认值（与 plugin_manager 的 meta 默认一致）
DEFAULT_PRIORITY = 50


def priority_of(pid: str, manifest: dict = None, priorities: dict = None) -> int:
    """取某个插件的优先级。显式传入 > 清单里的 priority > 默认 50。

    方向约定：**小的优先** —— 与钩子按 priority 升序派发一致，
    也让"谁先跑谁当能力主"这件事只有一种解释。
    """
    src = None
    if priorities and pid in priorities:
        src = priorities.get(pid)
    elif isinstance(manifest, dict) and manifest.get("priority") is not None:
        src = manifest.get("priority")
    try:
        return int(src) if src is not None else DEFAULT_PRIORITY
    except (TypeError, ValueError):
        return DEFAULT_PRIORITY


def _find_cycles(edges: dict) -> list:
    """有向图上的环（每个环只报一次）。edges: {node: [node...]}。"""
    cycles, state = [], {}

    def dfs(node, stack):
        state[node] = 1
        stack.append(node)
        for nxt in edges.get(node, []):
            if nxt not in edges:          # 依赖的提供者不是本程序里的插件
                continue
            if state.get(nxt) == 1:
                i = stack.index(nxt) if nxt in stack else -1
                cycles.append(stack[i:] if i >= 0 else stack[:] + [nxt])
            elif state.get(nxt, 0) == 0:
                dfs(nxt, stack)
        stack.pop()
        state[node] = 2

    for pid in list(edges):
        if state.get(pid, 0) == 0:
            dfs(pid, [])
    uniq, seen = [], set()
    for c in cycles:
        key = tuple(sorted(c))
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def _topo_order(nodes: list, edges: dict, refused: dict, prio: dict) -> list:
    """Kahn 拓扑排序；同层按 (priority, id) 排，保证每次启动顺序一致。"""
    live = [n for n in nodes if n not in refused]
    indeg = {n: 0 for n in live}
    radj = {n: [] for n in live}
    for n in live:
        for dep in edges.get(n, []):
            if dep in indeg and dep != n:
                indeg[n] += 1
                radj[dep].append(n)
    ready = sorted([n for n in live if indeg[n] == 0],
                   key=lambda x: (prio.get(x, DEFAULT_PRIORITY), x))
    out = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for nxt in radj[n]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
        ready.sort(key=lambda x: (prio.get(x, DEFAULT_PRIORITY), x))
    out.extend([n for n in live if n not in out])      # 兜底（理论上不会剩）
    return out


def plan(manifests: dict, priorities: dict = None) -> dict:
    """算出一份"该加载谁、按什么顺序、谁被拒、谁 standby"的计划。

    返回：
        order            街生下来的插件，拓扑序（依赖先）
        refused          {pid: 原因}  —— 拒载，`load_all` 不加载且把原因挂到 /api/plugins
        standby          {pid: [capability...]} —— 加载但**不是**该能力的主
        owners           {capability: pid} —— 每个能力的主是谁（唯一判定点）
        duplicate_provides / missing_requires / cycles —— 兼容旧报告字段
        warnings         给人看的启动日志行
    """
    ms = {}
    for k, v in (manifests or {}).items():
        if isinstance(v, dict):
            ms[str(k)] = v
    if not ms:
        return {"order": [], "refused": {}, "standby": {}, "owners": {},
                "duplicate_provides": {}, "missing_requires": [], "cycles": [],
                "warnings": []}

    prio = {pid: priority_of(pid, m, priorities) for pid, m in ms.items()}

    # ── 提供者表 ──
    providers = {}
    for pid, m in ms.items():
        for cap in (m.get("provides") or []):
            c = str(cap).strip()
            if c and pid not in providers.setdefault(c, []):
                providers[c].append(pid)

    # ── ② 能力冲突：选主 + standby ──
    owners, standby, dup = {}, {}, {}
    for cap, ids in providers.items():
        ranked = sorted(ids, key=lambda x: (prio.get(x, DEFAULT_PRIORITY), x))
        owners[cap] = ranked[0]
        if len(ranked) > 1:
            dup[cap] = ranked
            for loser in ranked[1:]:
                standby.setdefault(loser, []).append(cap)

    # ── ③ 缺依赖 / 依赖环 ──
    missing, missing_flat = {}, []
    for pid, m in ms.items():
        for cap in (m.get("requires") or []):
            c = str(cap).strip()
            if c and c not in providers:
                missing.setdefault(pid, []).append(c)
                missing_flat.append([pid, c])

    edges = {}
    for pid, m in ms.items():
        edges[pid] = []
        for cap in (m.get("requires") or []):
            for p in providers.get(str(cap).strip(), []):
                if p != pid:                  # 自给自足不算环
                    edges[pid].append(p)
    cycles = _find_cycles(edges)
    in_cycle = {p for cyc in cycles for p in cyc}

    refused = {}
    for pid in ms:
        if pid in missing:
            refused[pid] = "缺依赖：" + "、".join(missing[pid]) + "（没有任何插件提供）"
        elif pid in in_cycle:
            cyc = next((c for c in cycles if pid in c), [pid])
            refused[pid] = "依赖环：" + " → ".join(list(cyc) + [cyc[0]])

    # 级联：依赖的能力，提供者全被拒载 → 自己也没法用
    changed = True
    while changed:
        changed = False
        for pid in ms:
            if pid in refused:
                continue
            for cap in (ms[pid].get("requires") or []):
                c = str(cap).strip()
                provs = providers.get(c) or []
                if provs and all(p in refused for p in provs):
                    refused[pid] = f"依赖 {c} 的提供者（{'、'.join(provs)}）全被拒载"
                    changed = True
                    break

    order = _topo_order(list(ms), edges, refused, prio)

    warnings = []
    for cap, ids in dup.items():
        warnings.append(f"能力冲突：{cap} 由 {ids} 声明 → 主={owners[cap]}，"
                        f"standby={[x for x in ids if x != owners[cap]]}")
    for pid, why in refused.items():
        warnings.append(f"拒载 {pid}：{why}")

    return {
        "order": order,
        "refused": refused,
        "standby": standby,
        "owners": owners,
        "providers": {k: list(v) for k, v in providers.items()},
        "duplicate_provides": dup,
        "missing_requires": missing_flat,
        "cycles": cycles,
        "priorities": prio,
        "warnings": warnings,
    }


def load_order(discovered: list, plan_result: dict) -> dict:
    """把计划落到"先加载谁、跳过谁"。

    计划里只有**写了 plugin.json** 的插件；没有清单的老插件照常加载（兼容层），
    排在计划里的幸存者后面 —— 它们不参与排序，也不该被清单机制挡住。
    """
    disc = [str(x) for x in (discovered or [])]
    pl = plan_result or {}
    refused = pl.get("refused") or {}
    planned = [n for n in (pl.get("order") or []) if n in disc and n not in refused]
    rest = [n for n in disc if n not in planned and n not in refused]
    return {"order": planned + rest, "skip": dict(refused),
            "planned": planned, "legacy": rest}


def hook_chains(subscribers: dict, priorities: dict = None) -> dict:
    """同一钩子多家订阅 → 排成一条链（priority 升序）。

    第 4 条判据是**要求链式**：`execute_hook()` 是"首个非 None 即返回"的短路语义，
    拿它跑多订阅的钩子，等于让 priority 靠后的订阅方永远不执行 —— 那正是
    §8.1 #1 说的"静默覆盖"。这里把链算出来，调用方据此走 `execute_hook_chain`。
    """
    out = {}
    for hook, ids in (subscribers or {}).items():
        uniq = []
        for p in (ids or []):
            p = str(p)
            if p and p not in uniq:
                uniq.append(p)
        uniq.sort(key=lambda x: (priority_of(x, None, priorities), x))
        out[str(hook)] = {"chain": uniq, "multi": len(uniq) > 1}
    return out
