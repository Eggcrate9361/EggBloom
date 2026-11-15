# -*- coding: utf-8 -*-
"""tests/test_filesafe_confirm.py —— 文件操作的"人工确认"闭环（1.6.0 P2）

`harness/filesafe.py` 只管"没 token 动不了文件"；**谁发 token** 是这一层的事：
  agent 调 fs_move / fs_delete
    → 后端 prepare 出摘要 → 走权限通道发 permission_request（前端弹窗）
    → 人点「允许」→ 执行；点「不」或超时 → cancel，文件一个字节都不动

判据：
  ① 没有确认通道 → 拒绝执行（不能"没人管就悄悄改文件"）
  ② 允许 → 真的执行了，且备份/回收站照旧
  ③ 拒绝 → 文件不动，token 被取消（拿不到二次机会）
  ④ 超时 → 同样不动
  ⑤ 前端有弹窗、每次必问（没有"不再询问"）
"""
import io
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402
from harness import filesafe as FS  # noqa: E402
from harness import plugin_tools as PT  # noqa: E402

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
    tmp = tempfile.mkdtemp(prefix="fsc_")
    src_dir = os.path.join(tmp, "src")
    dst_dir = os.path.join(tmp, "dst")
    os.makedirs(src_dir)
    os.makedirs(dst_dir)
    old_roots = bc._config.get("filesafe_allowed_roots")
    bc._config["filesafe_allowed_roots"] = tmp
    old_notifier = getattr(PT, "_PERM_NOTIFIER", None)

    asked = []

    def fake_notify(req):
        asked.append(dict(req))

    def answer_later(allow, delay=0.15):
        """模拟"人在几秒后点了按钮"。"""
        def _go():
            time.sleep(delay)
            if asked:
                bc.resolve_file_permission(asked[-1]["request_id"], allow)
        threading.Thread(target=_go, daemon=True).start()

    try:
        print("[A] 没有确认通道 → 拒绝执行")
        PT.set_permission_notifier(None)
        p0 = os.path.join(src_dir, "a.txt")
        io.open(p0, "w", encoding="utf-8").write("x")
        r = bc.tool_fs_move({"src": p0, "dst": os.path.join(dst_dir, "a.txt")})
        check("★ 没通道就拒绝", r.get("ok") is False and r.get("denied") is True, r)
        check("★ 文件原地未动", os.path.exists(p0) and not os.path.exists(os.path.join(dst_dir, "a.txt")))

        PT.set_permission_notifier(fake_notify)

        print("\n[B] 允许 → 执行")
        asked.clear()
        p1 = os.path.join(src_dir, "b.txt")
        io.open(p1, "w", encoding="utf-8").write("移动我")
        answer_later(True)
        r = bc.tool_fs_move({"src": p1, "dst": os.path.join(dst_dir, "b.txt")})
        check("★ 执行成功", r.get("ok") is True, r)
        check("★ 文件到位", os.path.exists(os.path.join(dst_dir, "b.txt")))
        check("★ 弹过窗", len(asked) == 1, asked)
        check("★ 请求带 file: 前缀", str(asked[0].get("request_id", "")).startswith("file:"), asked[:1])
        check("★ 带人话摘要", "移动" in str(asked[0].get("plan", "")), asked[:1])
        check("★ 标了 kind=filesafe", asked[0].get("kind") == "filesafe", asked[:1])

        print("\n[C] 拒绝 → 文件不动，token 作废")
        asked.clear()
        p2 = os.path.join(src_dir, "c.txt")
        io.open(p2, "w", encoding="utf-8").write("别动我")
        answer_later(False)
        r = bc.tool_fs_delete({"src": p2})
        check("★ 返回拒绝", r.get("ok") is False and r.get("denied") is True, r)
        check("★ 文件还在", os.path.exists(p2))
        tok = str(asked[0].get("request_id", ""))[len("file:"):]
        check("★ token 已作废", not FS.pending() or all(x.get("token") != tok for x in FS.pending()),
              FS.pending())

        print("\n[D] 超时 → 同样不动")
        asked.clear()
        p3 = os.path.join(src_dir, "d.txt")
        io.open(p3, "w", encoding="utf-8").write("超时测试")
        old_to = bc.FILE_CONFIRM_TIMEOUT
        bc.FILE_CONFIRM_TIMEOUT = 0.3
        try:
            r = bc._ask_file_permission("delete", p3, None, timeout=0.3)
        finally:
            bc.FILE_CONFIRM_TIMEOUT = old_to
        check("★ 超时拒绝", r.get("ok") is False and r.get("denied") is True, r)
        check("★ 超时后文件还在", os.path.exists(p3))

        print("\n[E] 参数缺失 / 非法路径")
        check("缺参数报错", bc.tool_fs_move({"src": p3}).get("ok") is False)
        r = bc.tool_fs_delete({"src": os.path.join(tmp, "不存在.txt")})
        check("源不存在被拒", r.get("ok") is False and "不存在" in str(r.get("error", "")), r)

        print("\n[F] 回收站仍然生效（delete 不真删）")
        asked.clear()
        p4 = os.path.join(src_dir, "e.txt")
        io.open(p4, "w", encoding="utf-8").write("进回收站")
        answer_later(True)
        r = bc.tool_fs_delete({"src": p4})
        check("★ 删除成功", r.get("ok") is True, r)
        check("★ 原位没了", not os.path.exists(p4))
        check("★ 有备份", bool(r.get("backup")) and os.path.exists(r["backup"]), r)
        check("★ 进了回收站", bool(r.get("recycle")) and os.path.exists(r["recycle"]), r)

        print("\n[G] 路由：file: 前缀进文件闸，其它进插件权限")
        src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8", errors="replace").read()
        check("★ 端点按前缀分派", 'startswith("file:")' in src and "resolve_file_permission" in src)
        check("★ 工具已注册", '"fs_move": tool_fs_move' in src and '"fs_delete": tool_fs_delete' in src)
        check("★ 模型能看到这两个工具",
              '"name": "fs_move"' in src and '"name": "fs_delete"' in src)

        print("\n[H] 前端弹窗")
        html = io.open(os.path.join(ROOT, "webui", "webui_artistic_v4.html"),
                       encoding="utf-8", errors="replace").read()
        check("★ 有 askFilePermission", "function askFilePermission" in html)
        check("★ kind=filesafe 会走它",
              "req.kind === 'filesafe'" in html and "return askFilePermission(req)" in html)
        check("★ 文案是人话（不是插件那套）", "本体想动你的文件" in html)
        check("★ 删=回收站要说清楚", "移进回收站" in html)
        check("★ 覆盖风险要提示", "覆盖前会先备份" in html)
        # 不给"不再询问"：文件每次都要人点
        blk = html[html.find("function askFilePermission"):]
        blk = blk[:blk.find("\n}")]
        check("★ 文件确认不给'不再询问'", "pmRemember" not in blk and "记住" not in blk)

    finally:
        PT.set_permission_notifier(old_notifier)
        if old_roots is None:
            bc._config.pop("filesafe_allowed_roots", None)
        else:
            bc._config["filesafe_allowed_roots"] = old_roots
        shutil.rmtree(tmp, ignore_errors=True)
        bc._FILE_PENDING.clear()

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
