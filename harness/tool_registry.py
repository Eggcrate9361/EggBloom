# -*- coding: utf-8 -*-
"""harness/tool_registry.py —— 工具注册表：**按需加载** + **固定序列化顺序** + MCP 命名空间。

为什么要有这一层（研究结论）
    1. **工具描述本身是巨大的固定开销**。业界实测：50+ 个 MCP 工具全量注入约 72K
       token；按需只注入当前用得上的 8~10 个 → 约 8.7K token，**省 85%**。
       本项目的 12 个工具（含 web_operate 的 31 个 action 枚举）同样不便宜，
       而且在"只是闲聊/群聊回复"的场景里**完全用不上**。
    2. **工具数组顺序必须固定**。多数厂商（含 DeepSeek）的 prompt 缓存是
       「前缀逐 token 比对」，工具定义在请求里的位置**在当前用户消息之前** →
       一旦工具集/顺序变化，整个前缀作废。所以：**只在需要时加载，加载时永远同序**。
    3. **MCP 工具必须命名空间隔离**，否则和内置工具撞名（`search` 之类）。

设计
    - 注册表只持有「元数据 + 加载器 + 分组标签」，**不持有实现**：
      内置工具的实现由 bot_core 通过 `register()` 注入（保持 harness 不依赖 bot_core）。
    - `resolve(names)` 返回**确定性顺序**的 OpenAI 工具 schema 列表。
    - `groups_of(tool)` / `names_in(group)` 供"按场景挑选工具子集"。
    - `serialize_signature(names)` 给出该子集的**稳定指纹**，可用于缓存键/诊断。

内置分组（对应 bot_core 的 TOOLS / GROUP_TOOLS，以及 harness 自带的工作型工具）
    core     : 永远可用（search_web）——"能联网"是底线能力
    work     : **只读工作型**（ws_read/ws_glob/ws_grep）——零副作用，默认常驻
    todo     : 规划型（todo_write）——多步任务才注入
    desktop  : 本机操控（open_app/take_screenshot/get_system_info/control_*/manage_process/search_files/send_user_file）
    web      : 网页操作（web_operate）
    group    : 群内安全（get_group_members/generate_image）
    write    : **有副作用**（ws_write/ws_edit/ws_edit_many）——私聊 + 明确意图才注入
    proc     : **执行型**（py_run/job_*）——成本最高，注入条件最严
    mcp      : 外部 MCP（名字前缀 server__）
    skill    : Skill 暴露的工具（名字前缀 skill__）
"""
import json
import threading

_LOCK = threading.RLock()

# name -> {"schema": {...}, "loader": callable|None, "fn": callable|None, "groups": set, "source": str}
_TOOLS = {}
# 注册顺序（用于**无显式顺序时的兜底排序**；正常情况下按注册顺序输出）
_ORDER = []

# ── 分组定义（顺序即"重要性"，core 最前 → 命中缓存最稳）──
GROUP_CORE = "core"
GROUP_WORK = "work"          # 只读工作型（读/找/搜）—— 零副作用，可常驻
GROUP_DESKTOP = "desktop"
GROUP_WEB = "web"
GROUP_GROUP = "group"
GROUP_WRITE = "write"        # 写型（写文件/改文件）—— 有副作用，需私聊+意图
GROUP_PROC = "proc"          # 执行型（py_run / job_*）—— 成本最高，最严格注入
GROUP_TODO = "todo"          # 规划型（todo_write）
GROUP_MCP = "mcp"
GROUP_SKILL = "skill"

#: 分组展示顺序（serialize 时按此排序，保证跨请求一致）。
#:  顺序 = 前缀缓存里的稳定位置；改动此元组等于**全局缓存失效一次**，慎重。
GROUP_SEQ = (GROUP_CORE, GROUP_WORK, GROUP_TODO, GROUP_GROUP, GROUP_WEB,
             GROUP_DESKTOP, GROUP_WRITE, GROUP_PROC, GROUP_SKILL, GROUP_MCP)

#: 默认"常驻"分组：只有这些分组会进**静态**工具列表（其余按需加载）。
#:  work 只读、零副作用、定义短 → 常驻；省下的往返远比它的定义贵。
DEFAULT_RESIDENT_GROUPS = (GROUP_CORE, GROUP_WORK)

MCP_PREFIX = "mcp__"
SKILL_PREFIX = "skill__"


class ToolNotFound(KeyError):
    pass


def _norm_schema(schema):
    """校验 + 归一化工具 schema。返回副本，避免调用方后续修改污染注册表。"""
    if not isinstance(schema, dict) or schema.get("type") != "function":
        raise ValueError("工具 schema 必须是 {'type':'function','function':{...}} 形式")
    fn = schema.get("function") or {}
    name = fn.get("name")
    if not name:
        raise ValueError("工具 schema 缺少 function.name")
    return schema


def register(name, schema, *, fn=None, loader=None, groups=(), source="builtin"):
    """注册（或覆盖）一个工具。

    name   : 工具名（必须与 schema.function.name 一致）
    schema : OpenAI 工具定义
    fn     : 可调用实现（可选；agent 侧由 agent_service 用 tool_functions 覆盖）
    loader : 惰性加载器（可选）。`resolve(..., lazy=True)` 时若 schema 为空则调用它取 schema。
    groups : 分组标签集合（用于按场景挑选）
    source : builtin / plugin:<name> / mcp:<server> / skill:<name>
    """
    sch = _norm_schema(schema)
    if sch["function"]["name"] != name:
        raise ValueError(f"name 与 schema.function.name 不一致: {name!r} != {sch['function']['name']!r}")
    if not groups:
        # 名字前缀推断来源分组
        if name.startswith(MCP_PREFIX):
            groups = (GROUP_MCP,)
        elif name.startswith(SKILL_PREFIX):
            groups = (GROUP_SKILL,)
        else:
            groups = (GROUP_CORE,)
    with _LOCK:
        if name not in _TOOLS:
            _ORDER.append(name)
        _TOOLS[name] = {
            "schema": sch,
            "fn": fn,
            "loader": loader,
            "groups": set(groups),
            "source": source,
        }
    return name


def register_many(schemas, *, groups=(), source="builtin"):
    """批量注册（schemas 为 OpenAI 工具数组）。"""
    for s in schemas or []:
        try:
            register(s["function"]["name"], s, groups=groups, source=source)
        except Exception:
            continue


def register_loader(name, loader, *, groups=(), source="builtin"):
    """只注册**加载器**（schema 惰性取）。用于 MCP / 大型工具集。

    loader() -> OpenAI schema（或 None 表示不可用）
    """
    with _LOCK:
        if name not in _TOOLS:
            _ORDER.append(name)
        _TOOLS[name] = {"schema": None, "fn": None, "loader": loader,
                        "groups": set(groups) or {GROUP_MCP}, "source": source}
    return name


def unregister(name):
    with _LOCK:
        _TOOLS.pop(name, None)
        try:
            _ORDER.remove(name)
        except ValueError:
            pass


def has(name):
    """工具是否已注册（不触发加载）。"""
    with _LOCK:
        return name in _TOOLS


def groups_of(name):
    with _LOCK:
        e = _TOOLS.get(name)
        return set(e["groups"]) if e else set()


def names_in(group):
    """该分组下所有已注册工具名（固定顺序）。"""
    with _LOCK:
        return [n for n in _ORDER if group in _TOOLS[n]["groups"]]


def all_names(group=None):
    with _LOCK:
        if group is None:
            return list(_ORDER)
        return names_in(group)


def _sort_key(items):
    """确定性排序：分组优先级 → 注册顺序。

    **排序必须稳定**，否则同一子集两次序列化顺序不同 → 缓存失效。
    """
    with _LOCK:
        order_idx = {n: i for i, n in enumerate(_ORDER)}

    def k(item):
        name, entry = item
        grp = 99
        for i, g in enumerate(GROUP_SEQ):
            if g in entry["groups"]:
                grp = i
                break
        return (grp, order_idx.get(name, 10 ** 6), name)

    return sorted(items, key=k)


def resolve(names, *, lazy=False, strict=False):
    """把工具名列表解析成 OpenAI 工具 schema 列表（**确定性顺序**）。

    names  : 工具名序列（可为 None/空 → 返回 [])
    lazy   : True 时，对仅有 loader 的工具触发加载（用于需要完整 schema 的场合）
    strict : True 时，遇到未知工具名抛 ToolNotFound；否则静默跳过
    """
    if not names:
        return []
    seen = set()
    picked = []
    with _LOCK:
        for n in names:
            if n in seen:
                continue
            seen.add(n)
            e = _TOOLS.get(n)
            if e is None:
                if strict:
                    raise ToolNotFound(n)
                continue
            picked.append((n, dict(e)))

    out = []
    for n, e in _sort_key(picked):
        sch = e.get("schema")
        if not sch and lazy and e.get("loader"):
            try:
                sch = e["loader"]()
            except Exception:
                sch = None
            if sch:
                sch = _norm_schema(sch)
                with _LOCK:
                    if n in _TOOLS:
                        _TOOLS[n]["schema"] = sch
        if not sch:
            if strict:
                raise ToolNotFound(f"{n}（schema 未加载）")
            continue
        out.append(json.loads(json.dumps(sch, ensure_ascii=False)))
    return out


def resolve_groups(groups, *, lazy=False):
    """按分组解析（固定顺序）。"""
    picked = []
    for g in groups or ():
        for n in names_in(g):
            if n not in picked:
                picked.append(n)
    return resolve(picked, lazy=lazy)


def make_loader(names):
    """生成一个 **零参** loader，返回给定名字的 schema 列表（确定性顺序）。

    这是给 `chat_fn` 用的关键接口：bot_core 注入的 chat 闭包只需在**轮内**调用
    它（并缓存当轮结果），就能实现"静态工具集 + 轮内动态工具集"的拼接。
    """
    names = list(names or ())

    def _loader(*, lazy=False):
        return resolve(names, lazy=lazy)

    return _loader


def serialize_signature(names):
    """工具子集的稳定指纹（用于缓存键 / 诊断 / 观测）。"""
    schs = resolve(names)
    names_sorted = [s["function"]["name"] for s in schs]
    return "|".join(names_sorted), len(schs)


def stats():
    """诊断用：已注册工具数与分组分布。"""
    with _LOCK:
        by_group = {}
        for n, e in _TOOLS.items():
            for g in e["groups"]:
                by_group[g] = by_group.get(g, 0) + 1
        unloaded = [n for n, e in _TOOLS.items() if not e.get("schema")]
        return {"total": len(_TOOLS), "by_group": by_group, "schema_not_loaded": unloaded}


def reset():
    """清空注册表（单测用）。"""
    with _LOCK:
        _TOOLS.clear()
        _ORDER.clear()
