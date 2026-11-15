# -*- coding: utf-8 -*-
"""tests/test_scenario_tools.py —— 三大场景的工具面必须够用（1.6.0 P2）

P2 的验收是三个场景能跑通，而"能不能跑通"的第一道门是**工具有没有给模型**：
工具没注入，模型再聪明也只能说"我做不了"。

三个场景各自的最小工具集：
  ① 文件管理：「把这个文件整理到 D 盘并发我」
       search_files / ws_glob（找到）→ fs_move（搬，弹窗确认）→ send_user_file（发）
  ② 编码开发：「改一下这个脚本然后跑测试」
       ws_read / ws_edit / ws_write（看改）→ py_run（跑）→ trace_*（查）
  ③ 浏览器：「打开网页把表填了」→ web_operate

判据：
  · 私聊 privileged 场景：三套都在
  · 群聊：**本机能力一律不给**（write/proc/desktop 整组不进群），这是既有边界
  · fs_move / fs_delete 必须落在 desktop 组（跟着"本机能力"走，不能漏到群里）

注意：本用例自己复刻 main() 里的注册语义（`for _t in TOOLS` 那段）。
main() 是巨函数，注册内联在里面，测试进程不会执行它 —— 所以这里显式重建，
并且**断言 TOOLS 里确实有这些 schema**，防止 schema 被删掉而注册在、工具不在。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402
from harness import tool_registry as tr  # noqa: E402

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


def _register_like_main(root):
    """复刻 main() 里的注册：TOOLS → web / desktop，工作型工具分组注册。"""
    for t in bc.TOOLS:
        n = t["function"]["name"]
        if n == "web_operate":
            tr.register(n, t, groups=(tr.GROUP_WEB,), source="builtin")
        elif n != "search_web":
            tr.register(n, t, groups=(tr.GROUP_DESKTOP,), source="builtin")
    from harness import tool_fs as _tfs
    from harness import tool_proc as _tproc
    _tfs.register_all(root=root)
    _tproc.register_all()
    for mod_name in ("tool_self", "tool_task", "tool_meme", "tool_trace"):
        try:
            mod = __import__(f"harness.{mod_name}", fromlist=["register_all"])
            mod.register_all()
        except Exception:
            pass


def main():
    tmp = tempfile.mkdtemp(prefix="scenario_")
    _register_like_main(tmp)

    priv = set(bc._tools_subset_for_scene(False, privileged=True))
    grp = set(bc._tools_subset_for_scene(True))

    print("[A] 文件管理场景的工具够不够")
    for n in ("search_files", "ws_glob", "ws_read", "fs_move", "fs_delete", "send_user_file"):
        check(f"★ 私聊有 {n}", n in priv, sorted(priv)[:12])

    print("\n[B] 编码开发场景")
    for n in ("ws_read", "ws_edit", "ws_write", "ws_glob", "ws_grep", "py_run"):
        check(f"★ 私聊有 {n}", n in priv, sorted(priv)[:12])

    print("\n[C] 浏览器场景")
    check("★ 私聊有 web_operate", "web_operate" in priv)

    print("\n[D] 群聊边界：本机能力一律不进群")
    for n in ("fs_move", "fs_delete", "ws_write", "ws_edit", "py_run", "send_user_file"):
        check(f"★ 群里没有 {n}", n not in grp, sorted(grp)[:12])
    # 群里保留的是**只读/公开**能力：work 组（读找搜）+ web_operate。
    # search_web 不在这批注册里（它在 GROUP_CORE，另有闸门）。
    check("★ 群里仍有只读能力", {"ws_read", "ws_glob", "web_operate"} <= grp, sorted(grp)[:12])

    print("\n[E] 分组归属（决定跟着谁进群）")
    check("★ fs_move 在 desktop 组", "fs_move" in set(tr.names_in(tr.GROUP_DESKTOP)))
    check("★ fs_delete 在 desktop 组", "fs_delete" in set(tr.names_in(tr.GROUP_DESKTOP)))
    check("fs_move 不在 group 组", "fs_move" not in set(tr.names_in(tr.GROUP_GROUP)))
    check("ws_write 在 write 组", "ws_write" in set(tr.names_in(tr.GROUP_WRITE)))
    check("web_operate 在 web 组", "web_operate" in set(tr.names_in(tr.GROUP_WEB)))

    print("\n[F] schema 与注册不能脱节")
    schema_names = {t["function"]["name"] for t in bc.TOOLS}
    for n in ("fs_move", "fs_delete", "send_user_file", "search_files", "web_operate"):
        check(f"TOOLS 里有 {n} 的 schema", n in schema_names, sorted(schema_names)[:12])
    for n in ("fs_move", "fs_delete"):
        check(f"{n} 有实现", n in bc.TOOL_FUNCTIONS, sorted(bc.TOOL_FUNCTIONS)[:12])

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
