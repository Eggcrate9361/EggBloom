# -*- coding: utf-8 -*-
"""tests/test_retry_policy.py —— LLM 重试策略回归。

覆盖：
  · classify：状态码分类（可重试/致命）、x-should-retry 头优先、连接层异常、
    编程性异常不重试、错误信息关键词兜底
  · parse_retry_after：毫秒/秒/HTTP-date
  · compute_delay：指数退避、封顶、抖动边界、服从 Retry-After、超长 Retry-After 保护
  · retry_call：成功即返回 / 可重试跑到成功 / 致命错误立即放弃（不空转）/
    deadline 保护 / should_abort / meta 字段正确
  · 与旧行为对比：致命错误不再浪费 sleep
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import retry_policy as rp  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")


class _HTTPErr(Exception):
    """模拟 urllib.error.HTTPError（有 .code/.headers/.read）。"""

    def __init__(self, code, headers=None, msg="err"):
        super().__init__(msg)
        self.code = code
        self.headers = headers or {}

    def read(self):
        return b"{}"


class _ReqErr(Exception):
    """模拟 requests.HTTPError（.response.status_code）。"""

    def __init__(self, code):
        super().__init__(f"{code} error")
        self.response = type("R", (), {"status_code": code, "headers": {}})()


# ══ classify ═══════════════════════════════════════════════════════
def test_classify_status():
    for c in (408, 409, 429, 500, 502, 503, 504, 529):
        v = rp.classify(_HTTPErr(c))
        check(f"classify: HTTP {c} 可重试", v.should_retry is True, v)
        check(f"classify: HTTP {c} code 正确", v.code == c, v)

    for c in (400, 401, 403, 404, 422):
        v = rp.classify(_HTTPErr(c))
        check(f"classify: HTTP {c} 不可重试", v.should_retry is False, v)

    # requests 风格
    v = rp.classify(_ReqErr(429))
    check("classify: requests 风格 429", v.should_retry and v.code == 429, v)
    v = rp.classify(_ReqErr(401))
    check("classify: requests 风格 401 不重试", not v.should_retry, v)


def test_classify_header_override():
    # 服务端显式说不重试 → 即使 503 也不重试
    v = rp.classify(_HTTPErr(503, {"x-should-retry": "false"}))
    check("classify: x-should-retry=false 覆盖 503", not v.should_retry, v)
    # 服务端显式说重试 → 即使 400 也重试
    v = rp.classify(_HTTPErr(400, {"x-should-retry": "true"}))
    check("classify: x-should-retry=true 覆盖 400", v.should_retry, v)


def test_classify_transport():
    for e in (TimeoutError("x"), ConnectionError("x"), ConnectionResetError("x")):
        v = rp.classify(e)
        check(f"classify: {type(e).__name__} 可重试", v.should_retry, v)

    class RemoteDisconnected(Exception):
        pass

    check("classify: RemoteDisconnected 可重试", rp.classify(RemoteDisconnected()).should_retry)

    class JSONDecodeError(Exception):
        pass

    check("classify: JSONDecodeError 可重试", rp.classify(JSONDecodeError()).should_retry)


def test_classify_programming_errors():
    for e in (KeyError("choices"), TypeError("bad"), ValueError("nope"),
              AttributeError("x"), ZeroDivisionError()):
        v = rp.classify(e)
        check(f"classify: {type(e).__name__} 不重试", not v.should_retry, v)


def test_classify_message_fallback():
    class Weird(Exception):
        pass

    v = rp.classify(Weird("upstream server error, please retry"))
    check("classify: 信息含 server error → 重试", v.should_retry, v)
    v = rp.classify(Weird("429 Too Many Requests"))
    check("classify: 信息含 429 → 重试且 code=429", v.should_retry and v.code == 429, v)


# ══ parse_retry_after ══════════════════════════════════════════════
def test_parse_retry_after():
    check("retry-after: 秒", rp.parse_retry_after({"retry-after": "5"}) == 5.0)
    check("retry-after: 毫秒优先", rp.parse_retry_after(
        {"retry-after": "99", "retry-after-ms": "1500"}) == 1.5)
    check("retry-after: 浮点秒", rp.parse_retry_after({"retry-after": "2.5"}) == 2.5)
    check("retry-after: 无头 → None", rp.parse_retry_after({}) is None)
    check("retry-after: None → None", rp.parse_retry_after(None) is None)
    check("retry-after: 垃圾值 → None", rp.parse_retry_after({"retry-after": "abc"}) is None)


# ══ compute_delay ══════════════════════════════════════════════════
def test_compute_delay():
    # 无抖动（jitter=0）→ 纯指数
    d0 = rp.compute_delay(0, base=1.0, max_delay=100, jitter=0.0)
    d1 = rp.compute_delay(1, base=1.0, max_delay=100, jitter=0.0)
    d2 = rp.compute_delay(2, base=1.0, max_delay=100, jitter=0.0)
    check("delay: 指数 1,2,4", (d0, d1, d2) == (1.0, 2.0, 4.0), (d0, d1, d2))

    # 封顶
    d = rp.compute_delay(10, base=1.0, max_delay=8.0, jitter=0.0)
    check("delay: 封顶到 max_delay", d == 8.0, d)

    # 抖动：rnd 固定 → 可预测；0.25 抖动最多削 25%
    lo = rp.compute_delay(3, base=1.0, max_delay=100, jitter=0.25, rnd=lambda: 1.0)
    hi = rp.compute_delay(3, base=1.0, max_delay=100, jitter=0.25, rnd=lambda: 0.0)
    check("delay: 抖动上界=指数值", hi == 8.0, hi)
    check("delay: 抖动下界=指数*0.75", lo == 6.0, lo)
    check("delay: 抖动在 [0.75x, 1x]", lo <= hi)

    # 服从 Retry-After
    d = rp.compute_delay(0, retry_after=4.0, jitter=0.0)
    check("delay: 服从 Retry-After=4", d == 4.0, d)

    # 超长 Retry-After → 不吊死，退化为 max_delay
    d = rp.compute_delay(0, retry_after=600.0, max_delay=8.0, jitter=0.0)
    check("delay: 超长 Retry-After 保护", d == 8.0, d)

    # 负/零 attempt 不炸
    check("delay: attempt 负数不炸", rp.compute_delay(-3, jitter=0.0) >= 0)


# ══ retry_call ═════════════════════════════════════════════════════
def test_retry_call_success_first():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok"

    ok, val, meta = rp.retry_call(fn, sleep=lambda s: None)
    check("retry_call: 首次成功", ok and val == "ok", (ok, val))
    check("retry_call: 只调一次", calls["n"] == 1, calls["n"])
    check("retry_call: meta.retried=False", meta["retried"] is False, meta)
    check("retry_call: meta.fatal=False", meta["fatal"] is False, meta)


def test_retry_call_eventual_success():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _HTTPErr(503)
        return "done"

    ok, val, meta = rp.retry_call(fn, sleep=lambda s: None)
    check("retry_call: 第 3 次成功", ok and val == "done", (ok, val))
    check("retry_call: attempts=3", meta["attempts"] == 3, meta)
    check("retry_call: retried=True", meta["retried"] is True, meta)


def test_retry_call_fatal_no_spin():
    """关键回归：致命错误**不能**空转 sleep（旧实现会白等 6~12 秒）。"""
    waited = []
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _HTTPErr(401)

    ok, val, meta = rp.retry_call(fn, max_attempts=5, sleep=waited.append)
    check("retry_call: 401 立即失败", ok is False, ok)
    check("retry_call: 401 只尝试 1 次", calls["n"] == 1, calls["n"])
    check("retry_call: 401 **没有** sleep", waited == [], waited)
    check("retry_call: meta.fatal=True", meta["fatal"] is True, meta)
    check("retry_call: meta.reason 含不可重试", "不可重试" in meta["reason"], meta)


def test_retry_call_exhausted():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _HTTPErr(503)

    ok, val, meta = rp.retry_call(fn, max_attempts=3, sleep=lambda s: None)
    check("retry_call: 3 次后放弃", not ok and calls["n"] == 3, (ok, calls["n"]))
    check("retry_call: meta.attempts=3", meta["attempts"] == 3, meta)
    check("retry_call: 有 last_error", bool(meta["last_error"]), meta)


def test_retry_call_deadline():
    import time as _t
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _HTTPErr(503)

    # deadline 已过 → 第一次失败后不再等
    ok, val, meta = rp.retry_call(fn, deadline=_t.time() - 1, sleep=lambda s: None)
    check("retry_call: deadline 到点放弃", not ok and meta["aborted"], meta)
    check("retry_call: deadline 不空转", calls["n"] == 1, calls["n"])


def test_retry_call_abort():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _HTTPErr(503)

    ok, val, meta = rp.retry_call(fn, should_abort=lambda: True, sleep=lambda s: None)
    check("retry_call: abort 生效", not ok and meta["aborted"], meta)


def test_retry_call_uses_computed_delay():
    """确认真的用了退避（而不是固定 3 秒）。"""
    waited = []

    def fn():
        raise _HTTPErr(503)

    rp.retry_call(fn, max_attempts=3, base=1.0, max_delay=100,
                  jitter=0.0, sleep=waited.append)
    check("retry_call: 退避 1,2（指数）", waited == [1.0, 2.0], waited)


def test_retry_call_honors_retry_after():
    waited = []

    def fn():
        raise _HTTPErr(429, {"retry-after": "2"})

    rp.retry_call(fn, max_attempts=2, sleep=waited.append)
    check("retry_call: 429 用 Retry-After=2", waited == [2.0], waited)


def test_verdict_shape():
    v = rp.classify(_HTTPErr(429, {"retry-after": "3"}))
    should, reason, code, ra = v     # 可解包
    check("verdict: 可解包", should is True and code == 429 and ra == 3.0, v)
    check("verdict: 属性访问", v.should_retry and v.code == 429 and v.retry_after == 3.0, v)
    check("verdict: repr 可读", "RetryVerdict" in repr(v), repr(v))


def main():
    for fn in (test_classify_status, test_classify_header_override,
               test_classify_transport, test_classify_programming_errors,
               test_classify_message_fallback, test_parse_retry_after,
               test_compute_delay, test_retry_call_success_first,
               test_retry_call_eventual_success, test_retry_call_fatal_no_spin,
               test_retry_call_exhausted, test_retry_call_deadline,
               test_retry_call_abort, test_retry_call_uses_computed_delay,
               test_retry_call_honors_retry_after, test_verdict_shape):
        try:
            fn()
        except Exception as e:
            import traceback
            global FAIL
            FAIL += 1
            FAILURES.append(f"{fn.__name__} 抛异常: {e}\n{traceback.format_exc()}")
    print(f"\n{'='*60}")
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("-" * 60)
        for f in FAILURES:
            print("  ✗ " + f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
