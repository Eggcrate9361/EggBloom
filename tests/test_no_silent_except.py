# -*- coding: utf-8 -*-
"""主链不许再有「裸静默异常」（2026-11-09，§2.1 / §2.2 / §2.8）。

总账 §2 里最贵的一类不是写错一行，是 `except Exception: pass`：
  · `logger_setup` 的 ImportError 被吞 → 日志级别配了永远不生效；
  · `_id_block` 的 NameError 被吞 → 身份块从来没进过 prompt；
  · 群聊工具回调的 NameError 被吞 → 崩溃被完整伪装成「模型主动沉默」。

本用例钉死两件事：
  1. `bot_core.py` 里不允许出现「一行写完、既不打日志也不改状态」的 except；
  2. 流式回调抛异常时 SSE 解析照常走完（不中断），但一定留得下日志。

运行：python tests/test_no_silent_except.py
"""
from __future__ import annotations

import io
import os
import re
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


# ── 1. 静态闸：裸 except 不许回流 ────────────────────────────────
src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
lines = src.splitlines()
BARE = re.compile(r"^\s*except\b.*:\s*(pass|\.\.\.)\s*$")
bare = [(i + 1, s.strip()) for i, s in enumerate(lines) if BARE.match(s)]
check("★ bot_core.py 没有单行裸 except（既不记日志也不改状态）",
      not bare, bare[:8])

# 四处修复的观测位必须还在，别被"清理无用日志"顺手删掉
for marker in ("[环境] GPU 探测失败", "[环境] 进程列表跳过",
               "[流式] 思维链回调异常", "[流式] 正文回调异常",
               "[流式] 本轮回调异常"):
    check(f"观测位存在：{marker}", marker in src)


# ── 2. 行为闸：回调炸了，流不能断，但要留痕 ──────────────────────
class _FakeResp:
    """够用的 SSE 假响应：可迭代 + 上下文管理器。"""

    def __init__(self, chunks):
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._chunks)


def _sse(delta):
    import json
    return ("data: " + json.dumps({"choices": [{"delta": delta}]},
                                  ensure_ascii=False) + "\n").encode("utf-8")


def _stream_behavior() -> int:
    import bot_core

    chunks = [_sse({"reasoning_content": "想一下"}),
              _sse({"content": "你好"}),
              _sse({}),
              b"data: [DONE]\n"]
    logs = []

    def boom(_chunk):
        raise RuntimeError("回调故意炸")

    with mock.patch.object(bot_core, "_llm_config",
                           return_value={"api_url": "http://127.0.0.1:1/v1",
                                         "api_key": "k", "model_name": "m"}), \
         mock.patch.object(bot_core, "_effective_temperature", return_value=0.7), \
         mock.patch.object(bot_core, "_llm_request_headers", return_value={}), \
         mock.patch.object(bot_core.urllib.request, "urlopen",
                           return_value=_FakeResp(chunks)), \
         mock.patch.object(bot_core, "log", side_effect=logs.append):
        try:
            out = bot_core._agent_stream_chat([{"role": "user", "content": "在吗"}],
                                              on_reasoning=boom, on_content=boom)
        except Exception as e:
            print(f"FAIL [stream] 回调异常打断了流: {e}")
            return 1

    ok = (out.get("content") == "你好" and out.get("reasoning") == "想一下"
          and any("回调异常" in str(x) for x in logs)
          and any("本轮回调异常" in str(x) for x in logs))
    if not ok:
        print(f"FAIL [stream] out={out} logs={logs}")
        return 1
    print("PASS [stream] 回调异常被记录，SSE 解析照常返回完整结果")
    return 0


if __name__ == "__main__":
    rc = _stream_behavior()
    print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
    for n in FAIL:
        print(f"  FAIL  {n}")
    sys.exit(1 if FAIL or rc else 0)
