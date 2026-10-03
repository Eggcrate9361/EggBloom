# -*- coding: utf-8 -*-
"""harness/retry_policy.py —— LLM 调用的**重试策略单点实现**。

为什么要有它（用户原话："解决现有 agent 重试的问题来提高近乎 100 的调用成功率。
降低重试和决策成本"）
    改之前，项目里有 5 处各自 `for attempt in range(3): ... time.sleep(3)`：
      providers.py:117/199/401/565（4 个 provider）
      bot_core.py:3823（画图）/6005（主链）
    它们有同样的三个毛病：
      1) **不区分错误类型**——401 密钥错、400 参数错也重试 3 次，
         白白等 6~12 秒，最后还是失败（"无效重试"）。
      2) **固定 sleep(3)**——不遵循服务端 `Retry-After`，也不做指数退避；
         429 限流时等 3 秒再撞墙，反而加重限流。
      3) **没有抖动**——多个会话/任务同时失败会同时重试（惊群），
         把已经过载的服务再打一遍。
    → 三条加起来就是"重试成本高、成功率还不高"。

本模块给出的策略（对齐业界主流实现，见下）
    · OpenAI Python SDK `_base_client.py`：
        retry 状态码 {408, 409, 429, >=500}；`x-should-retry` 头优先；
        `Retry-After`（ms/s/HTTP-date）优先于自算退避；
        `delay = min(INITIAL * 2^n, MAX) * (1 - 0.25*random())`（0~25% 抖动）。
    · GoAgent `common.RetryConfig` / `MapHTTPError`：
        {400,401,403,404} → 不重试（non-retryable）；{429,5xx} → 重试。
    · agent-air：429 与 529(Anthropic overloaded) 重试，其余 4xx 不重试。
    · LangGraph RetryPolicy：`backoff_factor=2, initial_interval=.5, jitter=True`。
    · agentsurface.dev：`delay = base*2^attempt + random(0,base)`，60s 封顶。

设计取舍（**为什么不是无脑拉长重试**）
    · "近乎 100% 成功率"靠的是**分类**而不是次数：把可恢复的（网络抖动/限流/
      5xx/超时）重试够，把不可恢复的（鉴权/参数/内容审核）立刻上抛让上层
      换 Provider 或降级，避免把预算烧在必败的请求上。
    · 因为主链有硬超时（30s/600s）与"不阻塞主循环"约束，本模块默认总预算
      偏保守（`max_attempts=4`、`max_delay=8s`），并支持传入 deadline。

对外接口
    classify(exc_or_response)          -> RetryVerdict  判定该类错误是否值得重试
    compute_delay(attempt, ...)        -> float         退避秒数（含抖动）
    retry_call(fn, ...)                -> (ok, value, meta) 直接可用的重试执行器
    RetryVerdict                       -> namedtuple(should_retry, reason, code, retry_after)
"""

import email.utils
import math
import random
import time

# ── 可重试 / 不可重试的状态码（对齐 OpenAI SDK + GoAgent + agent-air）──
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
#: 明确不重试（重试只会浪费时间和配额）
FATAL_STATUS = frozenset({400, 401, 403, 404, 405, 406, 413, 414, 415, 422, 451})

# Anthropic 用 529 表示 overloaded；部分网关用 599
_OVERLOAD_STATUS = frozenset({529, 599})

DEFAULT_MAX_ATTEMPTS = 4        # 含首次；比原 3 次多一点，真正需要时不至于早退
DEFAULT_BASE_DELAY = 0.5        # 业界常用 0.5~1s（LangGraph 默认 0.5）
DEFAULT_MAX_DELAY = 8.0         # 主链有超时约束，8s 封顶（比业界的 60s 保守）
DEFAULT_JITTER = 0.25           # 0~25% 削减抖动（OpenAI SDK 同款）
MAX_RETRY_AFTER_HONOR = 30.0    # Retry-After 超过这个值就不等了（避免吊死主链）


class RetryVerdict(tuple):
    """重试判定结果。用 tuple 子类保持"可解包"的轻量感，同时可读。"""

    __slots__ = ()

    def __new__(cls, should_retry, reason="", code=None, retry_after=None):
        return super().__new__(cls, (bool(should_retry), reason, code, retry_after))

    should_retry = property(lambda self: self[0])
    reason = property(lambda self: self[1])
    code = property(lambda self: self[2])
    retry_after = property(lambda self: self[3])

    def __repr__(self):
        return (f"RetryVerdict(should_retry={self.should_retry}, "
                f"reason={self.reason!r}, code={self.code!r}, "
                f"retry_after={self.retry_after!r})")


# ── 从异常里尽量挖出 HTTP 状态码 / Retry-After ─────────────────────
#  不依赖 requests 是否安装：urllib 的 HTTPError 有 .code，requests 有
#  .response.status_code，HTTPError 的 .headers 两边都有。
def _status_of(err):
    code = getattr(err, "code", None)                # urllib.error.HTTPError
    if isinstance(code, int):
        return code
    resp = getattr(err, "response", None)            # requests.HTTPError
    if resp is not None:
        sc = getattr(resp, "status_code", None)
        if isinstance(sc, int):
            return sc
    # 有些客户端把状态码塞在 message 里（"API error 429: ..."）
    text = str(err)
    for tok in ("429", "500", "502", "503", "504", "529",
                "401", "403", "400", "404"):
        if tok in text:
            return int(tok)
    return None


def _headers_of(err):
    h = getattr(err, "headers", None)
    if h is not None:
        return h
    resp = getattr(err, "response", None)
    return getattr(resp, "headers", None) if resp is not None else None


def parse_retry_after(headers):
    """解析 Retry-After / retry-after-ms。返回秒数或 None。

    格式支持（与 OpenAI SDK 一致）：毫秒头 > 秒头 > HTTP-date。
    """
    if not headers:
        return None
    try:
        get = headers.get
    except Exception:
        return None
    for key, div in (("retry-after-ms", 1000.0), ("x-retry-after-ms", 1000.0),
                     ("retry-after", 1.0), ("x-retry-after", 1.0)):
        raw = None
        try:
            raw = get(key)
        except Exception:
            raw = None
        if raw is None:
            continue
        try:
            val = float(raw)
            if math.isfinite(val) and val >= 0:
                return val / div
        except (TypeError, ValueError):
            pass
        # HTTP-date
        try:
            tup = email.utils.parsedate_tz(str(raw))
            if tup:
                return max(0.0, email.utils.mktime_tz(tup) - time.time())
        except Exception:
            pass
    return None


def classify(err):
    """判定一个异常是否值得重试 → RetryVerdict。

    规则（按优先级）
      1) 服务端显式表态：`x-should-retry: true/false` 一律服从。
      2) HTTP 状态码：在 RETRYABLE_STATUS → 重试；在 FATAL_STATUS → 不重试。
      3) 无状态码（连接层）：超时 / 连接重置 / DNS / TLS → 重试。
      4) 其余的编程性错误（KeyError/TypeError/JSON 解析）→ 不重试。
    """
    # 1) 服务端表态
    hdrs = _headers_of(err)
    if hdrs is not None:
        try:
            flag = hdrs.get("x-should-retry")
        except Exception:
            flag = None
        if flag is not None:
            f = str(flag).strip().lower()
            if f == "true":
                return RetryVerdict(True, "x-should-retry=true", _status_of(err),
                                    parse_retry_after(hdrs))
            if f == "false":
                return RetryVerdict(False, "x-should-retry=false", _status_of(err))

    code = _status_of(err)
    retry_after = parse_retry_after(hdrs)

    if code is not None:
        if code in RETRYABLE_STATUS or code in _OVERLOAD_STATUS:
            return RetryVerdict(True, f"HTTP {code} 可重试", code, retry_after)
        if code in FATAL_STATUS or (400 <= code < 500):
            return RetryVerdict(False, f"HTTP {code} 不可重试（客户端错误）", code)
        if code >= 500:
            return RetryVerdict(True, f"HTTP {code} 服务端错误", code, retry_after)
        return RetryVerdict(False, f"HTTP {code} 未知，保守不重试", code)

    # 3) 无状态码 → 看异常类型名（不 import requests/urllib，避免硬依赖）
    name = type(err).__name__
    if isinstance(err, (TimeoutError, ConnectionError)):
        return RetryVerdict(True, f"{name} 可重试（连接/超时）")
    transient_names = {
        "HTTPError", "URLError", "RemoteDisconnected", "ConnectionResetError",
        "ConnectionAbortedError", "ConnectionRefusedError", "IncompleteRead",
        "ChunkedEncodingError", "ReadTimeout", "ConnectTimeout", "Timeout",
        "ProtocolError", "SSLError", "SSLEOFError", "socket.timeout",
        "JSONDecodeError", "ContentDecodingError",
    }
    if name in transient_names:
        # JSON 解析失败通常是网关返回了半截/HTML 错误页 → 重试一次常有奇效
        return RetryVerdict(True, f"{name} 可重试（传输层）")

    msg = str(err).lower()
    for kw in ("timeout", "timed out", "connection reset", "connection aborted",
               "temporarily unavailable", "too many requests", "rate limit",
               "overloaded", "server error", "bad gateway", "service unavailable",
               "gateway timeout", "eof occurred", "remote end closed"):
        if kw in msg:
            return RetryVerdict(True, f"错误信息含 '{kw}' → 可重试")

    # 4) 编程性错误 → 不重试
    return RetryVerdict(False, f"{name} 不可重试（非传输层错误）")


def compute_delay(attempt, *, base=DEFAULT_BASE_DELAY, max_delay=DEFAULT_MAX_DELAY,
                  jitter=DEFAULT_JITTER, retry_after=None, rnd=None):
    """算第 attempt 次（0 起）失败后该等多久。

    `delay = min(base * 2^attempt, max_delay) * (1 - jitter*rand)`  ← OpenAI SDK 同款
    若服务端给了 Retry-After，**优先服从**（它比任何自算都准），但仍受
    MAX_RETRY_AFTER_HONOR 保护，避免被一个 600s 的 Retry-After 把主链吊死。
    """
    r = rnd or random.random
    if retry_after is not None:
        ra = float(retry_after)
        if 0 < ra <= MAX_RETRY_AFTER_HONOR:
            return ra
        if ra > MAX_RETRY_AFTER_HONOR:
            # 服务端要我们等太久：不等那么久，但也别 0 秒就冲
            return float(max_delay)
    exp = min(base * (2.0 ** max(0, int(attempt))), max_delay)
    j = 1.0 - float(jitter) * r()
    return max(0.0, exp * j)


def retry_call(fn, *, max_attempts=DEFAULT_MAX_ATTEMPTS, base=DEFAULT_BASE_DELAY,
               max_delay=DEFAULT_MAX_DELAY, jitter=DEFAULT_JITTER,
               deadline=None, sleep=time.sleep, log=None, rnd=None,
               should_abort=None):
    """执行 `fn()`，按本模块策略重试。返回 `(ok, value, meta)`。

    参数
      fn          : 无参可调用；**失败以抛异常表达**（与 SDK 习惯一致）
      max_attempts: 含首次的总尝试次数
      deadline    : 绝对时间戳（time.time() 尺度）；用尽则不再等
      sleep       : 注入用（测试传假 sleep，避免真等）
      should_abort: 可调用；返回 True 则立刻放弃（用于"用户已撤单/超时"）
      log         : 可调用(str)

    返回 meta = {
      "attempts": 实际尝试次数,
      "elapsed": 总耗时(秒，不含最后一次失败的后续等待),
      "reason": 最后一次失败的判定原因,
      "code": 最后状态码,
      "retried": 是否发生过重试,
      "aborted": 是否因 should_abort/deadline 提前放弃,
      "fatal": 是否因不可重试错误立即放弃,
    }
    """
    log = log or (lambda m: None)
    t0 = time.time()
    last_err = None
    last_verdict = RetryVerdict(False, "未执行")
    attempts = 0
    retried = False

    for attempt in range(max(1, int(max_attempts))):
        attempts += 1
        try:
            value = fn()
            return True, value, {
                "attempts": attempts, "elapsed": round(time.time() - t0, 3),
                "reason": "" if attempts == 1 else f"第 {attempts} 次成功",
                "code": None, "retried": retried, "aborted": False,
                "fatal": False, "last_error": None,
            }
        except Exception as e:  # noqa: BLE001 —— 这里就是要兜住一切，再分类
            last_err = e
            verdict = classify(e)
            last_verdict = verdict

            if not verdict.should_retry:
                log(f"[retry] 放弃重试（{verdict.reason}）: {e}")
                return False, None, {
                    "attempts": attempts, "elapsed": round(time.time() - t0, 3),
                    "reason": verdict.reason, "code": verdict.code,
                    "retried": retried, "aborted": False, "fatal": True,
                    "last_error": str(e),
                }

            # 还有下一次机会吗？
            is_last = (attempt >= max_attempts - 1)
            if is_last:
                break

            if should_abort is not None:
                try:
                    if should_abort():
                        return False, None, {
                            "attempts": attempts, "elapsed": round(time.time() - t0, 3),
                            "reason": "已取消", "code": verdict.code,
                            "retried": retried, "aborted": True, "fatal": False,
                            "last_error": str(e),
                        }
                except Exception:
                    pass

            delay = compute_delay(attempt, base=base, max_delay=max_delay,
                                  jitter=jitter, retry_after=verdict.retry_after, rnd=rnd)

            # deadline 保护：等不完就别等（主链有超时上限，等下去只会超时）
            if deadline is not None:
                remain = deadline - time.time()
                if remain <= 0:
                    return False, None, {
                        "attempts": attempts, "elapsed": round(time.time() - t0, 3),
                        "reason": "超出截止时间", "code": verdict.code,
                        "retried": retried, "aborted": True, "fatal": False,
                        "last_error": str(e),
                    }
                delay = min(delay, max(0.0, remain))

            retried = True
            log(f"[retry] 第 {attempt + 1}/{max_attempts} 次失败（{verdict.reason}），"
                f"{delay:.2f}s 后重试: {e}")
            try:
                sleep(delay)
            except Exception:
                pass

    return False, None, {
        "attempts": attempts, "elapsed": round(time.time() - t0, 3),
        "reason": last_verdict.reason, "code": last_verdict.code,
        "retried": retried, "aborted": False,
        "fatal": not last_verdict.should_retry,
        "last_error": str(last_err) if last_err else None,
    }
