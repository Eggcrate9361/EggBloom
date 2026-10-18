# -*- coding: utf-8 -*-
"""tests/test_tool_workspace.py —— 工作型工具集回归。

覆盖（每条都对应一个"提高命中率 / 降成本"的具体设计）：
  · workspace：路径归一、越界拒绝、候选文件、父目录检查
  · ws_read   ：带行号、offset/limit、目录拒绝、二进制跳过、大文件拒绝
  · ws_edit   ：**唯一性校验**（0 次给候选、>1 次拒绝并给行号）、replace_all、dry_run
  · ws_edit_many：原子语义（一项失败整批不落盘）、同文件多处累积
  · ws_write  ：已存在需 overwrite、父目录自动创建
  · ws_glob   ：模式匹配、按 mtime 倒序、无结果时的提示
  · ws_grep   ：正则、行号、glob 过滤、非法正则提示
  · py_run    ：**持久会话**（变量跨调用）、异常回传 traceback、表达式求值、超时重置
  · todo_write：整表替换、单 in_progress 约束、去重、状态校验
  · job_*     ：后台启动、**增量**输出、状态标注
  · freshness ：知识截止差距、时效判定、过期标注
  · tool_runner 集成：session_id 透传、时效戳

运行：python tests/test_tool_workspace.py
"""
import os
import sys
import time
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import workspace as ws          # noqa: E402
from harness import tool_fs as fs            # noqa: E402
from harness import tool_proc as tp          # noqa: E402
from harness import freshness as fr          # noqa: E402
from harness import tool_registry as tr      # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
_SANDBOX = None
_ORIG_ROOT = None


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} {extra}")
        print(f"  x {name} {extra}")


def setup():
    """把工作区根切到隔离沙箱 —— 绝不碰真实项目文件。"""
    global _SANDBOX, _ORIG_ROOT
    _ORIG_ROOT = ws.workspace_root()
    _SANDBOX = tempfile.mkdtemp(prefix="ws_test_")
    ws.set_root(_SANDBOX)
    os.makedirs(os.path.join(_SANDBOX, "sub"), exist_ok=True)
    with open(os.path.join(_SANDBOX, "a.txt"), "w", encoding="utf-8") as f:
        f.write("hello world\nfoo bar\nhello world\n")
    with open(os.path.join(_SANDBOX, "sub", "b.py"), "w", encoding="utf-8") as f:
        f.write("def target():\n    return 1\n")
    with open(os.path.join(_SANDBOX, "sub", "c.py"), "w", encoding="utf-8") as f:
        f.write("# target marker\nx = 2\n")


def teardown():
    ws.set_root(_ORIG_ROOT)
    tp.reset_sessions()
    if _SANDBOX and os.path.isdir(_SANDBOX):
        shutil.rmtree(_SANDBOX, ignore_errors=True)


# ── workspace ──────────────────────────────────────────────────────
def test_workspace():
    print("\n[workspace] 路径解析与边界")
    r = ws.resolve("a.txt", must_exist=True)
    check("相对路径可解析", r.ok and r.path.endswith("a.txt"), r.reason)

    r = ws.resolve("sub/../a.txt", must_exist=True)
    check(".. 归一化后可解析", r.ok, r.reason)

    r = ws.resolve("../../etc/passwd", must_exist=True)
    check("越界被拒绝", not r.ok and r.reason == "outside_workspace", r.reason)

    r = ws.resolve("C:/Windows/System32/cmd.exe", must_exist=True)
    check("盘符绝对路径越界被拒", not r.ok and r.reason == "outside_workspace", r.reason)

    r = ws.resolve("nonexistent_zzz.txt", must_exist=True)
    check("不存在→not_found", not r.ok and r.reason == "not_found", r.reason)

    # 候选文件机制（降重试成本的核心）
    r = ws.resolve("b.py", must_exist=True)
    check("同名文件给候选", not r.ok and any("b.py" in c for c in r.candidates),
          str(r.candidates))

    # 路径写法宽容
    r = ws.resolve("「a.txt」", must_exist=True)
    check("书名号包裹可解析", r.ok, r.detail)

    r = ws.resolve("", must_exist=True)
    check("空路径报 empty", not r.ok and r.reason == "empty", r.reason)

    # 写路径：父目录不存在 → **允许**（write 会逐级创建），但越界仍须拒绝
    r = ws.resolve("no_such_dir/x.txt", for_write=True)
    check("写路径允许新建父目录", r.ok, r.reason)
    r = ws.resolve("../outside_root/x.txt", for_write=True)
    check("写路径越界仍被拒", not r.ok and r.reason == "outside_workspace", r.reason)


# ── read ───────────────────────────────────────────────────────────
def test_read():
    print("\n[ws_read] 带行号读取")
    out = fs.tool_read({"path": "a.txt"})
    check("含行号前缀 '1| '", "1| hello world" in out, out[:80])
    check("报告总行数", "共 3 行" in out, "")

    out = fs.tool_read({"path": "a.txt", "offset": 2, "limit": 1})
    check("offset/limit 生效", "2| foo bar" in out and "1| " not in out, out[:100])
    check("提示如何翻页", "还有" in out, "")

    out = fs.tool_read({"path": "sub"})
    check("目录给出替代方案", "目录" in out and "ws_glob" in out, out[:80])

    out = fs.tool_read({"path": "nope.txt"})
    check("不存在时给出候选或提示", "[文件不存在]" in out, out[:80])


# ── edit：唯一性校验（本模块最重要的行为）────────────────────────────
def test_edit():
    print("\n[ws_edit] 唯一性校验")
    out = fs.tool_edit({"path": "a.txt", "old_string": "hello world", "new_string": "X"})
    check("不唯一时拒绝执行", "[匹配不唯一]" in out, out[:60])
    check("不唯一时给出所有行号", "行号：1, 3" in out, out[:200])
    check("不唯一时提示 replace_all", "replace_all=true" in out, "")
    # 确认**没有**改动文件（拒绝 = 真的拒绝）
    with open(os.path.join(_SANDBOX, "a.txt"), encoding="utf-8") as f:
        body = f.read()
    check("拒绝后文件未被改动", body.count("hello world") == 2, "")

    out = fs.tool_edit({"path": "a.txt", "old_string": "foo bar", "new_string": "FOO"})
    check("唯一匹配成功", "已修改" in out, out[:60])
    with open(os.path.join(_SANDBOX, "a.txt"), encoding="utf-8") as f:
        check("内容已替换", "FOO" in f.read(), "")

    out = fs.tool_edit({"path": "a.txt", "old_string": "hello  wrold", "new_string": "y"})
    check("未找到时给最接近行", "[未找到匹配]" in out and "最接近" in out, out[:100])

    out = fs.tool_edit({"path": "a.txt", "old_string": "hello world", "new_string": "Z",
                        "replace_all": True})
    check("replace_all 生效", "已修改" in out, out[:80])
    with open(os.path.join(_SANDBOX, "a.txt"), encoding="utf-8") as f:
        body = f.read()
    check("replace_all 两处都改", body.count("Z") == 2 and "hello world" not in body, body)

    # dry_run 不落盘
    before = body
    out = fs.tool_edit({"path": "a.txt", "old_string": "FOO", "new_string": "BAR",
                        "dry_run": True})
    after = open(os.path.join(_SANDBOX, "a.txt"), encoding="utf-8").read()
    check("dry_run 不落盘", before == after and "预演" in out, out[:60])

    out = fs.tool_edit({"path": "a.txt", "old_string": "", "new_string": "x"})
    check("空 old_string 被拒", "[错误]" in out, out[:50])


# ── 参数名容错：把"传错参数名"这一次往返省掉 ────────────────────────
def test_edit_arg_tolerance():
    print("\n[ws_edit] 参数名容错（降重试成本）")
    # 1) 常见别名应被接住，直接成功（不浪费一次往返）
    out = fs.tool_edit({"path": "a.txt", "old": "FOO", "new": "FOO2"})
    check("old/new 别名被接住", "已修改" in out, out[:80])
    with open(os.path.join(_SANDBOX, "a.txt"), encoding="utf-8") as f:
        check("别名调用确实改了内容", "FOO2" in f.read(), "")

    out = fs.tool_edit({"path": "a.txt", "search": "FOO2", "replace": "FOO3"})
    check("search/replace 别名被接住", "已修改" in out, out[:80])

    # 2) 完全陌生的参数名 → 报错**必须列出实际收到的键**，模型才能自纠
    out = fs.tool_edit({"path": "a.txt", "before": "x", "after": "y"})
    check("陌生参数名被拒", "[错误]" in out, out[:60])
    check("报错列出实际收到的键", "实际收到的参数" in out and "before" in out, out[:200])
    check("报错给出正确名称", "old_string" in out, out[:200])
    check("报错要求重试一次", "重试" in out, out[:200])

    # 3) 别名单里的键应被显式点名（在 missing_arg 的 aliases 提示里）
    out = fs.tool_edit({"path": "a.txt", "find": None, "new_string": "q"})
    check("值为 None 仍视为缺参数", "[错误]" in out and "old_string" in out, out[:200])


# ── edit_many：原子语义 ────────────────────────────────────────────
def test_edit_many():
    print("\n[ws_edit_many] 批处理原子性")
    # 一项失败 → 整批不落盘（用当前真实内容作锚，避免依赖前面用例的副作用）
    before_a = open(os.path.join(_SANDBOX, "a.txt"), encoding="utf-8").read()
    before_b = open(os.path.join(_SANDBOX, "sub", "b.py"), encoding="utf-8").read()
    out = fs.tool_edit_many({"edits": [
        {"path": "a.txt", "old_string": "Z", "new_string": "OK1", "replace_all": True},
        {"path": "sub/b.py", "old_string": "NOT_THERE", "new_string": "x"},
    ]})
    after_a = open(os.path.join(_SANDBOX, "a.txt"), encoding="utf-8").read()
    after_b = open(os.path.join(_SANDBOX, "sub", "b.py"), encoding="utf-8").read()
    check("一项失败→整批不落盘", before_a == after_a and before_b == after_b, out[:80])
    check("报告第几项失败", "第 2 项" in out, out[:100])

    out = fs.tool_edit_many({"edits": [
        {"path": "a.txt", "old_string": "Z", "new_string": "OK1", "replace_all": True},
        {"path": "sub/b.py", "old_string": "return 1", "new_string": "return 2"},
    ]})
    check("全部合法→成功", "已批量修改" in out, out[:80])
    check("两文件都已改",
          "OK1" in open(os.path.join(_SANDBOX, "a.txt"), encoding="utf-8").read()
          and "return 2" in open(os.path.join(_SANDBOX, "sub", "b.py"), encoding="utf-8").read(),
          "")

    out = fs.tool_edit_many({"edits": []})
    check("空 edits 被拒", "[错误]" in out, out[:50])


# ── write ──────────────────────────────────────────────────────────
def test_write():
    print("\n[ws_write] 创建与覆盖保护")
    out = fs.tool_write({"path": "new.txt", "content": "hi\n"})
    check("创建新文件", "已创建" in out, out[:60])

    out = fs.tool_write({"path": "new.txt", "content": "bye\n"})
    check("覆盖需 overwrite", "需要确认" in out, out[:60])
    check("未确认时内容不变",
          open(os.path.join(_SANDBOX, "new.txt"), encoding="utf-8").read() == "hi\n", "")

    out = fs.tool_write({"path": "new.txt", "content": "bye\n", "overwrite": True})
    check("显式 overwrite 成功", "已覆盖" in out, out[:60])

    out = fs.tool_write({"path": "deep/dir/x.txt", "content": "z"})
    check("自动建父目录", "已创建" in out, out[:60])


# ── glob / grep ────────────────────────────────────────────────────
def test_glob_grep():
    print("\n[ws_glob] / [ws_grep]")
    out = fs.tool_glob({"pattern": "*.py"})
    check("glob 匹配到 py 文件", "b.py" in out and "c.py" in out, out[:120])

    out = fs.tool_glob({"pattern": "*.nomatch"})
    check("无结果时有提示", "未找到" in out and "提示" in out, out[:80])

    out = fs.tool_grep({"pattern": "target"})
    check("grep 命中并带行号", "b.py:1:" in out or "b.py:1" in out, out[:160])
    check("grep 结果含文件路径", "sub" in out, "")

    out = fs.tool_grep({"pattern": "target", "glob": "c.py"})
    check("glob 过滤生效", "b.py" not in out and "c.py" in out, out[:120])

    out = fs.tool_grep({"pattern": "("})
    check("非法正则给人话提示", "[正则非法]" in out and "转义" in out, out[:80])

    out = fs.tool_grep({"pattern": "ZZZZ_NOT_HERE"})
    check("无命中时给调整建议", "未找到" in out and "ignore_case" in out, out[:120])


# ── py_run：持久会话 ───────────────────────────────────────────────
def test_py_run():
    print("\n[py_run] 持久 Python 会话")
    tp.reset_sessions()
    out = tp.tool_py_run({"code": "import math\nv = 7\nprint('ok')", "session": "t1"})
    check("首次执行成功", "OK" in out and "ok" in out, out[:100])

    out = tp.tool_py_run({"code": "print(v * 2, math.floor(3.7))", "session": "t1"})
    check("**变量与 import 跨调用保留**", "14 3" in out, out[:120])

    out = tp.tool_py_run({"code": "def g(n):\n    return n + 1\nprint(g(41))", "session": "t1"})
    check("函数定义也可保留/使用", "42" in out, out[:100])

    out = tp.tool_py_run({"code": "undefined_name_zzz", "session": "t1"})
    check("异常回传 traceback", "Traceback" in out and "NameError" in out, out[:160])
    check("异常时状态为 ERROR", "ERROR" in out, out[:60])

    out = tp.tool_py_run({"code": "3 * 4", "session": "t1"})
    check("表达式直接求值并打印", "12" in out, out[:100])

    out = tp.tool_py_run({"code": "1", "session": "t1", "reset": True})
    check("reset 可重启会话", "OK" in out, out[:80])

    out = tp.tool_py_run({"code": "print(oct(8))", "session": "t1"})
    check("重启后仍可用", "0o10" in out, out[:100])

    out = tp.tool_py_run({"code": "import time; time.sleep(5)", "timeout": 1, "session": "t2"})
    check("超时被截断并重置", "TIMEOUT" in out or "超时" in out, out[:100])

    out = tp.tool_py_run({"code": ""})
    check("空代码被拒", "[错误]" in out, out[:50])


# ── todo ───────────────────────────────────────────────────────────
def test_todo():
    print("\n[todo_write] 整表替换与约束")
    out = tp.todo_write({"todos": [
        {"content": "读代码", "status": "completed"},
        {"content": "改代码", "status": "in_progress"},
    ]})
    check("正常清单可写入", "任务清单已更新" in out and "1/2" in out, out[:80])
    check("渲染含状态标记", "x 读代码" in out and "> 改代码" in out, "")

    out = tp.todo_write({"todos": [
        {"content": "a", "status": "in_progress"},
        {"content": "b", "status": "in_progress"},
    ]})
    check("拒绝多个 in_progress", "只保留**一个**" in out or "in_progress" in out, out[:80])

    out = tp.todo_write({"todos": [
        {"content": "dup", "status": "pending"},
        {"content": "dup", "status": "pending"},
    ]})
    check("拒绝重复内容", "重复" in out, out[:60])

    out = tp.todo_write({"todos": [{"content": "a", "status": "done"}]})
    check("拒绝非法状态", "非法" in out, out[:60])

    out = tp.todo_write({"todos": [{"content": "", "status": "pending"}]})
    check("拒绝空内容", "为空" in out, out[:50])

    out = tp.todo_write({"todos": [{"content": "全部完成", "status": "completed"}]})
    check("整表替换（旧清单消失）", "全部完成" in out and "读代码" not in out, out[:80])


# ── jobs ───────────────────────────────────────────────────────────
def test_jobs():
    print("\n[job_*] 后台任务与增量读取")
    script = os.path.join(_SANDBOX, "tick.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write("import time\nfor i in range(6):\n    print('tick', i, flush=True)\n"
                "    time.sleep(0.15)\n")

    out = tp.job_start({"path": "tick.py", "label": "tick-test"})
    check("可启动后台任务", "已在后台启动" in out, out[:80])
    jid = out.split("（")[0].split()[-1] if "已在后台启动" in out else ""

    if jid:
        time.sleep(0.9)
        out1 = tp.job_output({"job_id": jid})
        check("首次读取有输出", "tick 0" in out1, out1[:120])
        check("响应带状态标注", "[status:" in out1, out1[-60:])
        time.sleep(0.6)
        out2 = tp.job_output({"job_id": jid})
        check("**增量**读取（不重复旧行）", "tick 0" not in out2.split("\n", 1)[-1], out2[:160])

        out3 = tp.job_list({})
        check("job_list 列出任务", jid in out3, out3[:120])

        out4 = tp.job_kill({"job_id": jid, "reason": "测试结束"})
        check("job_kill 成功", "已结束" in out4, out4[:60])

    out = tp.job_output({"job_id": "job-nonexistent"})
    check("未知 job 给人话错误", "[错误]" in out, out[:60])


# ── freshness：时效性 ──────────────────────────────────────────────
def test_freshness():
    print("\n[freshness] 时效性护栏")
    gap = fr.knowledge_cutoff_gap()
    check("给出知识截止差距", "截止于" in gap and "距今已" in gap, gap[:80])

    anchor = fr.date_anchor()
    check("日期锚含权威基准", "权威基准" in anchor, anchor[:80])

    for q, want in [("今天天气怎么样", True), ("最新款手机是哪个", True),
                    ("我好难受啊", False), ("帮我写一段代码", False),
                    ("讲个笑话", False), ("iPhone 16 发布了没", True)]:
        got, _why = fr.classify_query(q)
        check(f"时效判定 {q!r} -> {want}", got == want, f"got={got}")

    d = fr.freshness_directive("今天天气怎么样")
    check("时效敏感→要求先核实", "先核实" in d or "核实" in d, d[:100])

    d = fr.freshness_directive("我好难受啊")
    check("闲聊→零附加成本", d == "", repr(d))

    d = fr.freshness_directive("今天天气怎么样", has_search=False)
    check("无搜索工具→要求明说无法核实", "无法核实" in d, d[:100])

    # 过期标注
    fr.reset()
    fr.mark("sess1", "search_web", ts=time.time() - 7200)
    note = fr.stale_note("sess1", "search_web")
    check("过期结果被标注", "时效警告" in note and "小时前" in note, note[:80])
    fr.mark("sess2", "search_web")
    check("未过期不标注", fr.stale_note("sess2", "search_web") == "", "")


# ── registry 集成 ──────────────────────────────────────────────────
def test_registry():
    print("\n[tool_registry] 分组与顺序")
    fns = {}
    fns.update(fs.register_all())
    fns.update(tp.register_all())

    check("只读三个在 work 组",
          set(tr.names_in(tr.GROUP_WORK)) == {"ws_read", "ws_glob", "ws_grep"},
          str(tr.names_in(tr.GROUP_WORK)))
    check("写型三个在 write 组",
          set(tr.names_in(tr.GROUP_WRITE)) == {"ws_write", "ws_edit", "ws_edit_many"},
          str(tr.names_in(tr.GROUP_WRITE)))
    check("执行型在 proc 组",
          "py_run" in tr.names_in(tr.GROUP_PROC), "")
    check("todo 在 todo 组",
          tr.names_in(tr.GROUP_TODO) == ["todo_write"], "")
    check("work 是默认常驻组", tr.GROUP_WORK in tr.DEFAULT_RESIDENT_GROUPS, "")
    check("GROUP_SEQ 含全部新组",
          all(g in tr.GROUP_SEQ for g in
              (tr.GROUP_WORK, tr.GROUP_WRITE, tr.GROUP_PROC, tr.GROUP_TODO)), "")

    # 稳定顺序：两次解析结果一致（前缀缓存的前提）
    s1 = tr.resolve(tr.names_in(tr.GROUP_WORK))
    s2 = tr.resolve(tr.names_in(tr.GROUP_WORK))
    check("解析顺序稳定", [x["function"]["name"] for x in s1] ==
          [x["function"]["name"] for x in s2], "")


def main():
    print("=" * 60)
    print("工作型工具集回归测试")
    print("=" * 60)
    setup()
    try:
        test_workspace()
        test_read()
        test_edit()
        test_edit_arg_tolerance()
        test_edit_many()
        test_write()
        test_glob_grep()
        test_py_run()
        test_todo()
        test_jobs()
        test_freshness()
        test_registry()
    finally:
        teardown()
    print()
    print("=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
