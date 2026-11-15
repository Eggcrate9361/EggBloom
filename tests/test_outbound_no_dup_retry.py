# -*- coding: utf-8 -*-
"""出站「超时后别盲目重发」的回归测试（2026-11-09，§2 #32）。

症状：用户收到两条一模一样的回复。根因是 `harness/outbound.py::_do_job()`
对**任何**失败都无差别重发，而 `api_call` 超时/网络异常返回的是
`{"retcode": -1}` —— 那时候消息可能已经送到服务器，再发一遍就是重复。

契约（本用例钉死）：
  1. retcode=-1（不确定）→ 重发前先问 verifier；
  2. verifier=True  → 确认已送达，记 sent，**不重发**；
  3. verifier=False/None → 按未送达处理，重试一次；
  4. retcode 是明确业务错误码（非 0 非 -1）→ 确定没投递，不查、直接重发；
  5. 没注入 verifier → 行为与改动前逐字节一致（重试一次）。

每个场景独立子进程：模块级 worker / 去重表 / 落盘 outbox 互不污染。
运行：python tests/test_outbound_no_dup_retry.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SCENARIOS = ("verified", "unverified", "none_result", "no_verifier",
             "business_error", "bad_retcode")


def _scenario(name: str) -> int:
    """子进程里跑一个场景，返回 0=通过。"""
    from harness import outbound as ob

    calls = {"send": 0, "verify": 0}
    text = "这条只该发一次"

    def sender(kind, target, payload, is_group):
        calls["send"] += 1
        if calls["send"] == 1:
            if name == "business_error":
                # 协议层明确拒绝：确定没投递，重发是安全的
                return {"retcode": 1200, "msg": "业务拒绝"}
            if name == "bad_retcode":
                # 畸形返回值：既不能当成功，也不能让 int() 把 worker 打断
                return {"retcode": "not-an-int"}
            # 模拟 api_call 超时：统一返回值就是 retcode=-1
            return {"retcode": -1}
        return {"retcode": 0, "data": {"message_id": 1}}

    def verifier(kind, target, payload, is_group):
        calls["verify"] += 1
        return {"verified": True, "unverified": False, "none_result": None,
                "bad_retcode": False}.get(name)

    ob.set_sender(sender)
    if name != "no_verifier":
        ob.set_verifier(verifier)
    ob.set_logger(lambda m: None)
    ob.set_config(lambda: {})

    ok, resp = ob.send_job("text", "1000000005",
                           {"text": text, "message": text},
                           is_group=False, timeout=10)
    ob.shutdown(1.0)

    want = {
        "verified":       (1, 1, True, True),
        "unverified":     (2, 1, True, False),
        "none_result":    (2, 1, True, False),
        "no_verifier":    (2, 0, True, False),
        "business_error": (2, 0, True, False),
        "bad_retcode":    (2, 1, True, False),
    }[name]
    w_send, w_verify, w_ok, w_verified = want
    problems = []
    if calls["send"] != w_send:
        problems.append(f"发送次数 {calls['send']} != {w_send}")
    if calls["verify"] != w_verify:
        problems.append(f"校验次数 {calls['verify']} != {w_verify}")
    if bool(ok) != w_ok:
        problems.append(f"ok={ok} != {w_ok}")
    if bool((resp or {}).get("verified")) != w_verified:
        problems.append(f"resp.verified={resp} != {w_verified}")
    if problems:
        print(f"FAIL [{name}] " + "；".join(problems))
        return 1
    print(f"PASS [{name}] 发送 {calls['send']} 次 / 校验 {calls['verify']} 次 / ok={ok}")
    return 0


def _is_uncertain_cases() -> int:
    """纯函数判据：哪些失败算「不确定」。"""
    from harness import outbound as ob
    cases = [
        ({"retcode": -1}, True),
        ({}, True),
        ({"retcode": "not-an-int"}, True),
        ({"retcode": 1200}, False),
        ({"retcode": 100}, False),
        ({"retcode": 0}, False),
    ]
    bad = [(r, ob._is_uncertain(r), w) for r, w in cases if ob._is_uncertain(r) != w]
    if bad:
        print("FAIL [is_uncertain]", bad)
        return 1
    print("PASS [is_uncertain] 6 种失败形态判定正确")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in SCENARIOS:
        return _scenario(sys.argv[1])

    rc = _is_uncertain_cases()
    for name in SCENARIOS:
        tmp = tempfile.mkdtemp(prefix="eb_outbox_")
        env = dict(os.environ)
        env["EGGBLOOM_OUTBOX_DB"] = os.path.join(tmp, "outbox.sqlite3")
        env["PYTHONIOENCODING"] = "utf-8"
        p = subprocess.run([sys.executable, os.path.abspath(__file__), name],
                           cwd=ROOT, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        print(out)
        if p.returncode != 0:
            rc = 1
    print("ALL PASS" if rc == 0 else "SOME FAILED")
    return rc


if __name__ == "__main__":
    sys.exit(main())
