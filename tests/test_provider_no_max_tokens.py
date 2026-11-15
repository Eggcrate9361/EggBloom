# -*- coding: utf-8 -*-
"""1.6.0 三条工作线补测试 · ② `max_tokens<=0` 时主链不发这个字段。

背景：`ai_max_tokens=0` 表示「不限输出，交给服务端按模型默认上限」。
旧实现把 `max_tokens: 0` 原样发给 OpenAI 兼容端点，部分后端会按 0 处理成空回复。
修复后 chat / chat_stream 都只在值 > 0 时才带字段。

判据写死（拦截请求体，不发真实网络请求）：
  A. chat(max_tokens=0 / -1 / None / 非数字) → payload 无 `max_tokens`
  B. chat(max_tokens=800 / "800") → payload 有 `max_tokens` 且是 int 800
  C. chat_stream 同样两条路径（強制注入假 requests，不依赖本机装没装）
  D. 其它字段（model / messages / temperature / stream）不受影响

只读：urlopen / requests.post 都被替换成内存假对象，不联网、不写日志到模型后端。
运行：python tests/test_provider_no_max_tokens.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import providers  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


class _Resp:
    """urlopen 的返回：上下文管理器 + read()。"""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 1},
        }).encode("utf-8")


CHAT_BODIES = []


def _fake_urlopen(req, timeout=None, context=None):
    CHAT_BODIES.append(json.loads(req.data.decode("utf-8")))
    return _Resp()


class _StreamResp:
    def iter_lines(self, decode_unicode=True):
        yield 'data: ' + json.dumps({"choices": [{"delta": {"content": "你好"}}]})
        yield 'data: [DONE]'


STREAM_BODIES = []


class _FakeRequests:
    @staticmethod
    def post(url, json=None, headers=None, stream=None, timeout=None):
        STREAM_BODIES.append(dict(json or {}))
        return _StreamResp()


_orig_urlopen = providers.urllib.request.urlopen
_orig_requests = getattr(providers, "requests", None)
_orig_has_requests = providers.HAS_REQUESTS
providers.urllib.request.urlopen = _fake_urlopen
providers.requests = _FakeRequests
providers.HAS_REQUESTS = True

try:
    p = providers.OpenAIProvider(
        api_url="https://invalid.example/v1/chat/completions",
        api_key="unit-test", model="deepseek-chat", max_retries=1,
    )
    MSG = [{"role": "user", "content": "hi"}]

    print("\n[1] chat：<=0 与非法值不带 max_tokens")
    for val in (0, -1, None, "not-a-number"):
        CHAT_BODIES.clear()
        try:
            reply, _, _ = p.chat(MSG, max_tokens=val)
            body = CHAT_BODIES[-1] if CHAT_BODIES else {}
            check(f"chat(max_tokens={val!r}) 无 max_tokens 字段",
                  "max_tokens" not in body, body)
            check(f"chat(max_tokens={val!r}) 其它字段不受影响",
                  body.get("model") == "deepseek-chat" and body.get("messages") == MSG
                  and "temperature" in body and body.get("stream") is False, body)
        except Exception as e:
            check(f"chat(max_tokens={val!r}) 无 max_tokens 字段", False,
                  f"{type(e).__name__}: {e}")
            check(f"chat(max_tokens={val!r}) 其它字段不受影响", False, "没拿到请求体")

    print("\n[2] chat：>0 正常带字段")
    for val in (800, "800"):
        CHAT_BODIES.clear()
        try:
            p.chat(MSG, max_tokens=val)
            body = CHAT_BODIES[-1] if CHAT_BODIES else {}
            check(f"chat(max_tokens={val!r}) 带 max_tokens=800",
                  body.get("max_tokens") == 800 and isinstance(body.get("max_tokens"), int),
                  body)
        except Exception as e:
            check(f"chat(max_tokens={val!r}) 带 max_tokens=800", False,
                  f"{type(e).__name__}: {e}")

    print("\n[3] chat_stream：同样口径")
    for val, should_have in ((0, False), (-1, False), (256, True)):
        STREAM_BODIES.clear()
        try:
            text = p.chat_stream(MSG, max_tokens=val)
            body = STREAM_BODIES[-1] if STREAM_BODIES else {}
            has = "max_tokens" in body
            check(f"chat_stream(max_tokens={val}) 字段存在性 = {should_have}",
                  has is should_have, body)
            if should_have:
                check("chat_stream 的 max_tokens 为 256", body.get("max_tokens") == 256, body)
            check(f"chat_stream(max_tokens={val}) 拿到流内容", text == "你好", repr(text))
        except Exception as e:
            check(f"chat_stream(max_tokens={val}) 字段存在性 = {should_have}", False,
                  f"{type(e).__name__}: {e}")
            check(f"chat_stream(max_tokens={val}) 拿到流内容", False, "没拿到流内容")
finally:
    providers.urllib.request.urlopen = _orig_urlopen
    providers.requests = _orig_requests
    providers.HAS_REQUESTS = _orig_has_requests

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
