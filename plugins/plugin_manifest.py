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
