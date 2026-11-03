# -*- coding: utf-8 -*-
"""tests/test_agent_manual.py —— 手册必须由注册表渲染，且不许漂移（2026-11-03）

执行总表 P1-4：「手册从工具注册表渲染（`docs/agent_manual.md` 自动生成，不许手写）」。

本用例的价值在于 **[B] 防漂移**：每次都重新渲染一遍，与仓库里的文件逐字节比对。
所以"手改了手册"和"加了工具没重新生成"这两种情况都会当场红 ——
这比任何"记得同步"的约定都硬。

其余锁六件事：
  A. 离线能收集到工具（24 个，schema 全部加载）
  C. 渲染是**确定性**的（两次结果相同、不含时间戳），否则比对测试天天红
  D. 每个工具都有小节、描述、来源
  E. 分组总览的数字与正文段数对得上
  F. 惰性工具（只有 loader）不编参数，只在末尾列名字
  G. 描述里的竖线 / 换行不会把表格撑破
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import agent_manual as am  # noqa: E402
from harness import tool_registry as tr  # noqa: E402

MANUAL = os.path.join(ROOT, "docs", "agent_manual.md")

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


def main():
    print("[A] 离线收集：不启动 bot 也能拿到全部工具")
    stats = am.collect()
    names = tr.all_names()
    check(f"收集到 {len(names)} 个工具", len(names) >= 20, len(names))
    check("schema 全部加载（没有惰性漏网）",
          stats.get("schema_not_loaded") == [], stats.get("schema_not_loaded"))
    check("分组数 ≥ 4", len(stats.get("by_group") or {}) >= 4, stats.get("by_group"))
    check("关键工具都在",
          {"ws_read", "py_run", "job_wait", "todo_write", "dispatch_task", "deep_task"}
          <= set(names), sorted(set(names)))

    print("\n[B] ★ 防漂移：重新渲染 == 仓库里的文件")
    text = am.render()
    check("docs/agent_manual.md 存在", os.path.isfile(MANUAL), MANUAL)
    on_disk = io.open(MANUAL, encoding="utf-8").read() if os.path.isfile(MANUAL) else ""
    if on_disk == text:
        check("★ 逐字节一致", True)
    else:
        ol, nl = on_disk.splitlines(), text.splitlines()
        first = next((i for i in range(max(len(ol), len(nl)))
                      if (ol[i] if i < len(ol) else None) != (nl[i] if i < len(nl) else None)), 0)
        check("★ 逐字节一致（不一致请跑 python scripts/gen_agent_manual.py）", False,
              f"第 {first + 1} 行起不同："
              f"有={ol[first][:60] if first < len(ol) else '<EOF>'!r} "
              f"应是={nl[first][:60] if first < len(nl) else '<EOF>'!r}")

    print("\n[C] 确定性：两次渲染必须一模一样")
    check("两次渲染相同", am.render() == am.render())
    check("不含年份（不写时间戳，否则天天红）",
          not re.search(r"20\d\d-\d\d-\d\d", text), "发现日期串")
    check("结尾是单个换行", text.endswith("\n") and not text.endswith("\n\n"))

    print("\n[D] 每个工具都有小节、描述、来源")
    heads = re.findall(r"^### `([^`]+)`$", text, re.M)
    check(f"小节数 = 工具数（{len(names)}）", len(heads) == len(names), (len(heads), len(names)))
    check("★ 工具一个不多一个不少", set(heads) == set(names),
          sorted(set(heads) ^ set(names)))
    # 正文按**分组**排（分组按 GROUP_SEQ、组内按注册顺序），不是全局注册顺序 —— 分组内校验
    ok_order = True
    for g in re.findall(r"^## ([a-z_]+)（\d+）$", text, re.M):
        want = [n for n in names if g in tr.groups_of(n)]
        got = [n for n in heads if n in set(want)]
        if got != want:
            ok_order = False
    check("组内顺序 = 注册顺序", ok_order)
    missing_desc = []
    for n in names:
        seg = text.split(f"### `{n}`", 1)[-1].split("### ", 1)[0]
        body = [l for l in seg.splitlines() if l.strip() and not l.startswith("|")
                and not l.startswith("来源：") and not l.startswith("---")]
        if not body:
            missing_desc.append(n)
        if "来源：" not in seg:
            missing_desc.append(n + "(无来源)")
    check("★ 每个工具都有描述和来源", not missing_desc, missing_desc)

    print("\n[E] 分组总览的数字与正文段数对得上")
    overview = dict((g, int(c)) for g, c in
                    re.findall(r"^\| `([a-z_]+)` \| (\d+) \|", text, re.M))
    check("总览非空", bool(overview), overview)
    check("总览合计 = 工具总数", sum(overview.values()) == len(names),
          (sum(overview.values()), len(names)))
    ok_sections = True
    for g, c in overview.items():
        m = re.search(rf"^## {g}（(\d+)）$", text, re.M)
        if not m or int(m.group(1)) != c:
            ok_sections = False
    check("每个分组正文段的数量与总览一致", ok_sections, overview)

    print("\n[F] 惰性工具：不编参数，只列名字")
    tr.register_loader("zz_lazy_demo", lambda: None, groups=(tr.GROUP_MCP,))
    try:
        t2 = am.render()
        check("★ 惰性工具不进正文", "### `zz_lazy_demo`" not in t2)
        check("末尾列了名字", "- `zz_lazy_demo`" in t2)
        check("有惰性小节", "惰性工具" in t2)
    finally:
        tr.unregister("zz_lazy_demo")

    print("\n[G] 竖线与换行不破表")
    tr.register("zz_cell_demo", {
        "type": "function",
        "function": {
            "name": "zz_cell_demo",
            "description": "第一行\n第二行 | 带竖线",
            "parameters": {"type": "object", "properties": {
                "a": {"type": "string", "description": "参数里也有 | 竖线"}},
                "required": ["a"]},
        },
    }, groups=(tr.GROUP_CORE,), source="builtin")
    try:
        t3 = am.render()
        check("★ 换行被压平", "第一行 第二行" in t3, "未压平")
        check("★ 竖线被转义", "第二行 \\| 带竖线" in t3, "未转义")
        check("参数里的竖线也转义", "参数里也有 \\| 竖线" in t3)
        # 表格行必须仍是 4 列（只数**未转义**的竖线，`\|` 是正文不是分隔符）
        def cols(line):
            return len([p for p in re.split(r"(?<!\\)\|", line.strip())]) - 2
        bad = [l for l in t3.splitlines() if l.startswith("| `a`") and cols(l) != 4]
        check("参数行仍是 4 列", not bad, bad)
    finally:
        tr.unregister("zz_cell_demo")
        am.collect()

    print("\n[H] 文件本身就是『不许手写』的声明")
    check("★ 标题写了自动生成", "自动生成" in text and "勿手改" in text)
    check("★ 写了生成命令", "scripts/gen_agent_manual.py" in text)
    check("★ 写了检测方式", "test_agent_manual" in text)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
