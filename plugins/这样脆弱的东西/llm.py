# -*- coding: utf-8 -*-
"""LLM 调用层：把 AIClient 包成一支"守规矩的笔"。

蒸馏会连续发起十几次调用，所以这里必须管四件事：

  1. **限速** —— 实测上游中转只有约 3 请求/分钟，超了直接 quota_exceeded。
     所以所有调用先过一道匀速闸门，宁可慢，不可废。
  2. **空输出** —— 推理型模型（reasoning tokens）会把 max_tokens 吃光，
     返回 content 为空却不报错。这里把它识别出来并重试。
  3. **预算** —— 调用次数硬上限，超了立刻停，绝不无限烧钱。
  4. **稳健解析** —— 模型输出带围栏、尾逗号、中文引号、前后废话都能救回来。

不含任何平台耦合：client 只需实现 call(messages, max_tokens=...) -> (text, tools, usage)。
"""
import json
import re
import threading
import time


class BudgetExceeded(Exception):
    """调用预算用尽。"""


def _as_text(result):
    """AIClient.call 返回 (reply, tool_calls, usage)，也容忍直接返回字符串。"""
    if isinstance(result, tuple):
        return (result[0] or ""), (result[2] if len(result) > 2 else {}) or {}
    return (result or ""), {}


# ── 限速闸门 ────────────────────────────────────────────

class Pacer:
    """匀速闸门：保证任意 60 秒窗口内的调用数不超过 rpm。

    多线程共用一把锁并预约下一次可发时刻，所以并发调用会被自动摊平成
    一串匀速请求 —— 这比"并发然后集体撞墙重试"省时间也省额度。
    """

    def __init__(self, rpm=3, safety=1.2):
        rpm = max(1, int(rpm or 1))
        self.min_gap = 60.0 / rpm * max(1.0, float(safety))
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self):
        """阻塞到本线程获准发起下一次调用，返回实际等待秒数。"""
        with self._lock:
            now = time.time()
            start = max(now, self._next)
            self._next = start + self.min_gap
        delay = start - time.time()
        if delay > 0:
            time.sleep(delay)
        return round(max(0.0, delay), 1)


# ── JSON 抢救 ───────────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def parse_json(text):
    """从模型输出里抢救出 JSON。抢救不到返回 None。"""
    if not text:
        return None
    candidates = []
    m = _FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1))
    candidates.append(text)

    for cand in candidates:
        for opener, closer in (("{", "}"), ("[", "]")):
            start = cand.find(opener)
            end = cand.rfind(closer)
            if start == -1 or end <= start:
                continue
            blob = cand[start:end + 1]
            for attempt in (blob, _repair(blob)):
                try:
                    return json.loads(attempt)
                except Exception:
                    continue
    return None


def _repair(blob):
    """常见坏脾气：尾逗号、Python 字面量、中文引号、裸 key。"""
    s = blob
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    s = re.sub(r",\s*([}\]])", r"\1", s)
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bFalse\b", "false", s)
    s = re.sub(r"\bNone\b", "null", s)
    s = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', s)
    return s


# ── 调用器 ──────────────────────────────────────────────

class LLM:
    """带限速、重试与记账的调用器。线程安全。"""

    def __init__(self, client, max_calls=24, max_tokens=3000, rpm=3,
                 empty_retries=6, token_ceiling=16000, on_log=None):
        self.client = client
        self.max_calls = int(max_calls)
        self.max_tokens = int(max_tokens)
        self.token_ceiling = int(token_ceiling)
        self.empty_retries = int(empty_retries)
        self.pacer = Pacer(rpm=rpm)
        self.calls = 0
        self.tokens = 0
        self.failures = 0
        self.empty = 0
        self.started = time.time()
        self._lock = threading.Lock()
        self._log = on_log

    def set_logger(self, fn):
        self._log = fn

    # -- 记账 --------------------------------------------------
    def _take(self):
        with self._lock:
            if self.calls >= self.max_calls:
                raise BudgetExceeded(f"LLM 调用预算已用尽（{self.max_calls} 次）")
            self.calls += 1
            return self.calls

    def _account(self, usage):
        total = int((usage or {}).get("total_tokens") or 0)
        with self._lock:
            self.tokens += total
        return total

    def summary(self):
        return {
            "calls": self.calls,
            "max_calls": self.max_calls,
            "tokens": self.tokens,
            "failures": self.failures,
            "empty_replies": self.empty,
            "elapsed": round(time.time() - self.started, 1),
        }

    # -- 单次调用 ----------------------------------------------
    def text(self, system, user, max_tokens=None, tag=""):
        """一次文本调用。

        推理型模型有个坑：它会一直"想"，直到把 max_tokens 用光，
        于是 completion=cap、reasoning=cap、content 为空 —— 但接口不报错。
        这里识别出这种情况后，**自动抬高预算重试**，而不是原样重试等死。
        """
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": user})

        mt = int(max_tokens or self.max_tokens)
        ceiling = max(mt, int(self.token_ceiling))
        for attempt in range(self.empty_retries + 1):
            self._take()
            waited = self.pacer.wait()
            if waited:
                self._note(f"限速等待 {waited}s" + (f"（{tag}）" if tag else ""))
            try:
                reply, usage = _as_text(self.client.call(msgs, max_tokens=mt))
            except BudgetExceeded:
                raise
            except Exception as e:
                with self._lock:
                    self.failures += 1
                self._note(f"调用异常{self._where(tag)}：{e}")
                continue
            self._account(usage)
            if reply:
                return reply

            # 空输出：要么被限流，要么推理把预算吃光了
            with self._lock:
                self.empty += 1
            completion = int((usage or {}).get("completion_tokens") or 0)
            reasoning = int(((usage or {}).get("completion_tokens_details") or {})
                            .get("reasoning_tokens") or 0)
            exhausted = bool(completion) and reasoning >= completion * 0.95

            if exhausted and mt < ceiling:
                mt = min(ceiling, max(mt * 2, mt + 2000))
                self._note(f"空输出{self._where(tag)}（推理吃满 {reasoning}/{mt}），"
                           f"把预算抬到 {mt} 重试")
                wait = self.pacer.min_gap * 0.5
            elif not completion:
                # completion=0 是限流的典型特征。
                # 这里要**退避**而不是固定间隔：TPM 冷却往往需要好几分钟，
                # 固定 20 秒重试会把 5 次机会在 2 分钟内烧光，然后整批观察白丢。
                wait = min(90.0, self.pacer.min_gap * (attempt + 1))
                self._note(f"空输出{self._where(tag)}（疑似被限流），"
                           f"退避 {wait:.0f}s 后重试（第 {attempt + 1}/"
                           f"{self.empty_retries + 1} 次）")
            else:
                self._note(f"空输出{self._where(tag)}（模型返回空内容，"
                           f"completion={completion}），准备重试")
                wait = self.pacer.min_gap * 0.5
            if attempt < self.empty_retries:
                time.sleep(wait)

        with self._lock:
            self.failures += 1
        return ""

    def _where(self, tag):
        return f"（{tag}）" if tag else ""

    def _note(self, line):
        if self._log:
            try:
                self._log(line)
            except Exception:
                pass

    def json(self, system, user, max_tokens=None, tag=""):
        """一次 JSON 调用，返回 (data, raw)。data 可能是 dict/list/None。"""
        raw = self.text(system, user, max_tokens=max_tokens, tag=tag)
        data = parse_json(raw)
        if data is None and raw:
            raw2 = self.text(
                system,
                user + "\n\n（上一轮输出无法解析。请只输出一个 JSON 对象，"
                       "不要任何解释、不要代码块围栏、不要 Markdown。）",
                max_tokens=max_tokens, tag=tag + "/retry")
            data = parse_json(raw2)
            raw = raw2 or raw
        return data, raw

    # -- 批量（顺序执行，闸门负责匀速） ------------------------
    def batch(self, jobs):
        """jobs = [(key, system, user, max_tokens)]，返回 {key: data}。

        刻意不并发：上游是 3 请求/分钟，并发只会换来一串 quota_exceeded。
        """
        out = {}
        for key, system, user, mt in jobs:
            try:
                data, _ = self.json(system, user, max_tokens=mt, tag=str(key))
            except BudgetExceeded as e:
                self._note(f"预算用尽，停止后续调用：{e}")
                out[str(key)] = None
                break
            out[str(key)] = data
        return out
