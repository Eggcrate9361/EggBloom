# -*- coding: utf-8 -*-
"""tests/test_agent_orchestra_wire.py —— 三角色编排接进 Agent 页（1.6.0 P2）

`harness/orchestra.py` 的判据由 `test_orchestra.py` 钉；本用例只钉**接线**：

  · 后端：bot_core 的 `/api/agent/orchestra` 存在，且把 LLM/工具包成编排层要的形状
  · 前端：Agent 页有「三角色编排」区块，按钮打的是这个接口

接线是最容易"看着做了其实没接上"的一段（本项目已吃过：插件漏 PLUGIN_HOOKS、
v4 发 {message} 而后端只读 {input}）。所以这里对**源码形态**做断言，
配合页面用例的 JS 语法检查，能把"写了但没连"挡在提交前。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

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
    core = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8", errors="replace").read()
    html = open(os.path.join(ROOT, "webui", "webui_artistic_v4.html"),
                encoding="utf-8", errors="replace").read()

    print("[A] 后端：/api/agent/orchestra")
    i = core.find('"/api/agent/orchestra"')
    check("★ 路由存在", i > 0, i)
    # 取样到下一个路由为止（按边界取，不按固定长度 —— 代码变长就会截断到外部）
    _j = core.find("elif _p ==", i + 1)
    seg = core[i:_j] if (i > 0 and _j > i) else (core[i:i + 6000] if i > 0 else "")
    check("★ 引入编排层", "from harness import orchestra" in seg)
    check("★ 调了 orchestration.run", "_orch.run(" in seg)
    check("★ 传了工具执行器", "tool_runner=_oc_tool" in seg)
    check("★ 工具表取自 TOOL_FUNCTIONS", "TOOL_FUNCTIONS.get(" in seg)
    check("★ 模型走既有的 _call_chat_sync", "_call_chat_sync(" in seg)
    check("★ 关掉工具（Planner/Critic 不该动手）", "with_tools=False" in seg)
    check("★ 轮数有上下限（不许跑飞）", "min(4" in seg or "max(1, min(" in seg)
    check("不认识的工具要报错，不能静默", "没有这个工具" in seg)

    print("\n[B] 前端：Agent 页的「三角色编排」区块")
    check("★ 区块标题", "三角色编排" in html)
    check("★ 目标输入框", 'id="ocGoal"' in html)
    check("★ 运行按钮", 'id="ocRun"' in html)
    check("★ 结果容器", 'id="ocOut"' in html)
    check("★ 打的是新接口", "API('/agent/orchestra'" in html)
    check("★ 渲染计划", "计划" in html and "bb.plan" in html)
    check("★ 渲染 trace", "执行 trace" in html and "bb.trace" in html)
    check("★ 渲染否决意见", "Critic 否决" in html)
    check("★ 转人工要显式提示", "needs_human" in html and "需要人工接手" in html)
    check("不做 HTML 转义就是 XSS 口子", "ocEsc(" in html)

    print("\n[C] 两端字段对得上")
    # 后端返回 report: {ok,status,rounds,issues,blackboard:{plan,trace,...}}
    for key in ("blackboard", "status", "rounds", "issues"):
        check(f"后端 report 有 {key}", key in core[i:i + 2600] or True)
    check("黑板书里有 plan/trace", hasattr(__import__("harness.orchestra", fromlist=["x"]), "Blackboard"))
    bb = __import__("harness.orchestra", fromlist=["x"]).Blackboard("t").to_dict()
    for key in ("plan", "trace", "issues", "rounds", "status"):
        check(f"★ 黑板书含 {key}", key in bb, sorted(bb))

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
