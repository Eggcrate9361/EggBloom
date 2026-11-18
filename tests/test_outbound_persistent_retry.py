# -*- coding: utf-8 -*-
"""明确失败的文本必须由落盘 outbox 自动重试，而不是等下一次重启。"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TD = tempfile.mkdtemp(prefix="eggobx_retry_")
os.environ["EGGBLOOM_OUTBOX_DB"] = os.path.join(TD, "outbox.sqlite3")

from harness import outbound as ob  # noqa: E402
from harness import outbox  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name + ("" if not extra else f"  {extra}"))


calls = {"n": 0}
logs = []
payloads = []
recovered = []


def sender(kind, target, payload, is_group):
    calls["n"] += 1
    payloads.append(str(payload.get("text") or ""))
    if calls["n"] <= 2:
        return {"retcode": 100, "msg": "temporary"}
    return {"retcode": 0, "data": {"message_id": 1}}


try:
    ob.set_logger(lambda m: logs.append(m))
    ob.set_config(lambda: {})
    ob.set_sender(sender)
    ob.set_retry_success_handler(lambda job: recovered.append(dict(job)))
    t0 = time.time()
    first = ob.send_text("原始回复", "12345", is_group=True, timeout=5)
    check("第一次调用如实返回失败", first is False, first)
    check("首次任务执行包含两次即时尝试", calls["n"] == 2, calls)
    deadline = time.time() + 5
    while time.time() < deadline and calls["n"] < 3:
        time.sleep(0.05)
    check("★ pending 在进程内自动续跑", calls["n"] >= 3, calls)
    deadline = time.time() + 2
    row = None
    while time.time() < deadline:
        rows = outbox.default().list_all()
        row = rows[0] if rows else None
        if row and row.get("status") == "sent":
            break
        time.sleep(0.05)
    check("★ 重试成功后落盘状态为 sent",
          bool(row) and row.get("status") == "sent", row)
    check("重试沿用原文，不生成新文本",
          payloads == ["原始回复"] * 3, payloads)
    check("重试有可观察日志",
          any("已排重试" in m for m in logs), logs[-8:])
    check("★ 重试成功通知宿主补账",
          len(recovered) == 1
          and recovered[0].get("payload", {}).get("text") == "原始回复",
          recovered)
    check("耗时在预期范围内", time.time() - t0 < 6, time.time() - t0)
finally:
    try:
        ob.shutdown(0.2)
    except Exception:
        pass
    outbox.reset_default()
    import shutil
    shutil.rmtree(TD, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for item in FAIL:
    print("  FAIL", item)
sys.exit(1 if FAIL else 0)
