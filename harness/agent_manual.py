# -*- coding: utf-8 -*-
"""harness/agent_manual.py —— 从工具注册表渲染 `docs/agent_manual.md`（2026-11-03）

执行总表 P1-4：**手册从工具注册表渲染，不许手写**。

## 为什么

`docs/qq_agent_manual.md` 是手写的。手写的说明书与代码之间只有"人记得同步"这一道防线，
`test_manual_consistency.py` 能补一半（它校验 9 个原语都在手册里），但补不了
"新增工具忘了写"和"描述改了手册没改"。

这里换成单向生成：注册表是**唯一来源**，手册是它的渲染结果。
`tests/test_agent_manual.py` 每次跑都重新渲染一遍并与仓库里的文件逐字节比对 ——
谁手改了、谁加了工具没重新生成，当场红。

## 纪律

- **确定性**：不写时间戳、不写随机数、排序固定（分组按 `GROUP_SEQ`，组内按注册顺序）。
  少一条，比对测试就会天天红。
- **只读**：不联网、不起线程、不读配置。`collect()` 会**清空并重建**注册表，
  所以只许在生成脚本和测试里调用，别在运行中的 bot 里调。
- 惰性工具（只注册了 loader、schema 为空）不写进手册正文，只在末尾列个名单 ——
  它们的参数得等首次调用才知道，硬编就是编造。
"""
from __future__ import annotations

import importlib

from harness import tool_registry as tr

#: 会往注册表里塞工具、且**离线可导入**的模块（顺序即注册顺序）。
#: 插件工具（plugin:<id>）、MCP（mcp:<server>）、技能（skill:<name>）都是运行期发现的，
#: 不进这份手册 —— 它们的数量取决于用户装了什么，写进静态文件必漂移。
TOOL_MODULES = (
    "harness.tool_fs",
    "harness.tool_proc",
    "harness.tool_self",
    "harness.tool_task",
    "harness.tool_meme",
    "harness.tool_deep",
    "harness.trace_tools",
)

#: 分组的中文说明（写给人看，所以留在渲染层，不塞进注册表）
GROUP_DOC = {
    tr.GROUP_CORE: "常驻基础工具",
    tr.GROUP_WORK: "只读工作型 —— 读 / 找 / 搜，零副作用，可常驻",
    tr.GROUP_TODO: "规划型 —— 记待办",
    tr.GROUP_GROUP: "群管理 —— 动的是群，权限最敏感",
    tr.GROUP_WEB: "联网 —— 兜底搜索",
    tr.GROUP_DESKTOP: "桌面操作 —— 截图 / 鼠标键盘",
    tr.GROUP_WRITE: "写型 —— 写文件 / 改文件，有副作用",
    tr.GROUP_PROC: "执行型 —— 起进程 / 跑代码，成本最高",
    tr.GROUP_DEEP: "多步任务 —— 只在「可干活」打开时注入",
    tr.GROUP_MCP: "MCP 外部工具",
    tr.GROUP_SKILL: "技能加载器",
}


def collect() -> dict:
    """清空注册表并按 `TOOL_MODULES` 重新注册，返回 `tool_registry.stats()`。

    ⚠ 会**清空全局注册表**。只给生成脚本与测试用。
    """
    tr.reset()
    for mod_name in TOOL_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        fn = getattr(mod, "register_all", None)
        if not callable(fn):
            continue
        try:
            fn()
        except Exception:
            continue
    return tr.stats()


def _cell(text, limit: int = 200) -> str:
    """表格单元格安全化：换行压平、竖线转义、超长截断。"""
    s = str(text or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()
    s = " ".join(s.split())
    if len(s) > limit:
        s = s[:limit - 1] + "…"
    return s or "—"


def _order_groups(groups) -> list:
    """分组排序：先按 GROUP_SEQ，再按名字补在后面的（mcp / skill / 将来新增的）。"""
    known = [g for g in tr.GROUP_SEQ if g in groups]
    extra = sorted(g for g in groups if g not in tr.GROUP_SEQ)
    return known + extra


def _params_of(schema: dict) -> tuple:
    """拆出 (参数列表, 是否必填表)。参数顺序保持 schema 里的声明顺序。"""
    fn = (schema or {}).get("function") or {}
    params = fn.get("parameters") or {}
    props = params.get("properties") or {}
    required = set(params.get("required") or [])
    rows = []
    for pname, pdef in props.items():
        d = pdef if isinstance(pdef, dict) else {}
        ptype = str(d.get("type") or "any")
        enum = d.get("enum")
        if isinstance(enum, list) and enum:
            ptype += "（" + " / ".join(str(x) for x in enum[:6]) + "）"
        rows.append({"name": pname, "type": ptype,
                     "required": pname in required,
                     "desc": _cell(d.get("description"), 200)})
    return rows


def render(reg=None) -> str:
    """渲染整份手册。纯函数：同样输入必得同样输出（不含时间戳）。"""
    r = reg or tr
    names = r.all_names()
    entries = []
    lazy = []
    for n in names:
        sch = r.resolve([n], strict=False)
        schema = sch[0] if sch else None
        if not schema:
            lazy.append(n)
            continue
        entries.append({
            "name": n,
            "schema": schema,
            "groups": sorted(r.groups_of(n)),
            "source": getattr(r, "source_of", lambda _n: "builtin")(n) or "builtin",
        })

    by_group = {}
    for e in entries:
        for g in e["groups"] or [tr.GROUP_CORE]:
            by_group.setdefault(g, []).append(e)

    groups = _order_groups(by_group.keys())
    total = len(entries)

    out = []
    out.append("# 工具手册（自动生成，勿手改）")
    out.append("")
    out.append("> 本文件由 `python scripts/gen_agent_manual.py` 从 "
               "`harness/tool_registry.py` **渲染**而来，**不要手工编辑**。")
    out.append("> `tests/test_agent_manual.py` 每次都会重新渲染并与本文件逐字节比对："
               "手改了、或加了工具没重新生成，都会当场红。")
    out.append(">")
    out.append(f"> 收录 **{total}** 个工具、**{len(groups)}** 个分组。"
               "插件 / MCP / 技能工具是运行期发现的，不在本文件里。")
    out.append("")
    out.append("## 分组总览")
    out.append("")
    out.append("| 分组 | 工具数 | 用途 |")
    out.append("|---|---|---|")
    for g in groups:
        out.append(f"| `{g}` | {len(by_group[g])} | {_cell(GROUP_DOC.get(g, '（未登记说明）'))} |")
    out.append("")

    for g in groups:
        items = by_group[g]
        out.append(f"## {g}（{len(items)}）")
        out.append("")
        out.append(f"{GROUP_DOC.get(g, '（未登记说明）')}")
        out.append("")
        for e in items:
            fn = e["schema"].get("function") or {}
            out.append(f"### `{e['name']}`")
            out.append("")
            out.append(_cell(fn.get("description"), 400))
            out.append("")
            rows = _params_of(e["schema"])
            if rows:
                out.append("| 参数 | 类型 | 必填 | 说明 |")
                out.append("|---|---|---|---|")
                for p in rows:
                    out.append(f"| `{p['name']}` | {_cell(p['type'], 60)} | "
                               f"{'是' if p['required'] else '否'} | {p['desc']} |")
            else:
                out.append("_无参数_")
            out.append("")
            out.append(f"来源：`{e['source']}`"
                       + (f"；分组：{'、'.join(e['groups'])}" if len(e["groups"]) > 1 else ""))
            out.append("")

    if lazy:
        out.append("## 惰性工具（未收录参数）")
        out.append("")
        out.append("以下只注册了加载器，schema 要等首次调用才拿得到 —— "
                   "**不猜参数**，只列名字：")
        out.append("")
        for n in lazy:
            out.append(f"- `{n}`")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def write(path: str, *, reg=None) -> str:
    """渲染并落盘，返回写出的正文。"""
    text = render(reg)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return text


__all__ = ["TOOL_MODULES", "GROUP_DOC", "collect", "render", "write"]
