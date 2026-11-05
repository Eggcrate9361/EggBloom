# -*- coding: utf-8 -*-
"""tests/test_permission_stream.py —— 权限确认必须真的能弹到前端（1.6.0 P1-1）

2026-11-05 真机端到端（Agent 页「把桌面 txt 整理到 D 盘并发我」）撞出来的坑：

  _perm_notify 只把 permission_request 写进 session_log，
  但前端 askPermission 只订阅 /api/agent/stream 的实时总线（_BUS）。
  结果：日志里有请求、后端阻塞等 180s、页面上一个弹窗都没有。
  当时是靠直接读 session_log 文件才把请求批掉的 —— 真人用户做不到。

判据：
  ① 权限事件同时进实时总线（当场弹窗）
  ② SSE 晚连 / 重连时，仍在 pending 的请求能从日志补发
  ③ 已解决的请求不会被补发（避免旧弹窗诈尸）
  ④ 静态钉住三处接线，防止再退化成「只写日志」
"""
import io
import json
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
from harness import agent_service as AS  # noqa: E402
from harness import session_log as SL  # noqa: E402

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
    print("\n[A] 权限事件进实时总线")
    sid = "permstream-bus-%d" % os.getpid()
    q = AS.subscribe(sid)
    try:
        req = {"request_id": "file:deadbeef", "kind": "filesafe",
               "op": "move", "plan": "移动 a → b"}
        AS.publish_event(sid, "permission_request", req)
        got = AS._drain(sid)
        check("★ subscribe 后能收到 permission_request",
              len(got) == 1 and got[0].get("type") == "permission_request",
              repr(got))
        check("★ 载荷完整（request_id / kind 不丢）",
              bool(got) and got[0].get("data", {}).get("request_id") == "file:deadbeef"
              and got[0].get("data", {}).get("kind") == "filesafe")
        check("★ 无人订阅时不抛异常", AS.publish_event("nobody", "permission_request", req) is None)
    finally:
        AS.unsubscribe(sid, q)

    print("\n[B] SSE 晚连补发：只补仍在 pending 的")
    tmp = tempfile.mkdtemp(prefix="permstream_")
    old_dir = SL.SESSION_LOG_DIR
    sid2 = "permstream-replay-%d" % os.getpid()
    rid = "file:replay-token"
    try:
        SL.SESSION_LOG_DIR = tmp
        path = os.path.join(tmp, "agent-%s.jsonl" % sid2)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"seq": 0, "ts": 1.0, "type": "user_msg",
                                "payload": {"content": "整理文件"}}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"seq": 1, "ts": 2.0, "type": "permission_request",
                                "payload": {"request_id": rid, "kind": "filesafe",
                                            "op": "move", "plan": "移动 a → b"}},
                               ensure_ascii=False) + "\n")
        check("★ 没有 pending 时不补发", bc.pending_permission_events(sid2) == [])

        with bc._FILE_PENDING_LOCK:
            bc._FILE_PENDING[rid] = {"ev": None, "allow": False}
        try:
            evs = bc.pending_permission_events(sid2)
            check("★ pending 的补发 1 条", len(evs) == 1, repr(evs))
            check("★ 补发事件形状与总线一致（前端归一化能认）",
                  bool(evs) and evs[0].get("type") == "permission_request"
                  and evs[0].get("data", {}).get("request_id") == rid)
        finally:
            with bc._FILE_PENDING_LOCK:
                bc._FILE_PENDING.pop(rid, None)
        check("★ 已解决后不再补发（旧弹窗不诈尸）",
              bc.pending_permission_events(sid2) == [])
    finally:
        SL.SESSION_LOG_DIR = old_dir
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n[C] 静态接线：三处不许再退化")
    core = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8", errors="replace").read()
    svc = io.open(os.path.join(ROOT, "harness", "agent_service.py"),
                  encoding="utf-8", errors="replace").read()
    html = io.open(os.path.join(ROOT, "webui", "webui_artistic_v4.html"),
                   encoding="utf-8", errors="replace").read()
    check("★ agent_service 暴露 publish_event", "def publish_event(" in svc)
    check("★ _perm_notify 双写（日志 + 总线）",
          "_sl2.append(\"agent\", _sid, \"permission_request\"" in core
          and "_as2.publish_event(_sid, \"permission_request\"" in core)
    check("★ SSE 连接时补发 pending", "pending_permission_events(sid)" in core
          and "_replay = pending_permission_events(sid)" in core)
    check("★ 前端只认流里的 permission_request（这正是原先漏掉的通道）",
          "ev.type === 'permission_request'" in html
          and "askPermission(ev.payload || ev.data || {})" in html)
    check("★ 文件确认仍走 filesafe 分支",
          "req.kind === 'filesafe'" in html and "return askFilePermission(req)" in html)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  - " + x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
