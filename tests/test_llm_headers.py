# -*- coding: utf-8 -*-
"""tests/test_llm_headers.py —— 模型请求头（2026-11-03）

## 为什么单独钉这个

线上真机日志：

```
AI 不重试（HTTP 403 不可重试（客户端错误））: HTTP Error 403: Forbidden | error code: 1010
AI 失败 (已重试0次): HTTP Error 403: Forbidden | error code: 1010
```

每条回复都撞 —— bot 只能退化成「收到消息了，但暂时无法处理」。

根因：`bot_core._post_chat` 只设了 `Content-Type` / `Authorization`，
urllib 于是自报 `Python-urllib/3.x`，Cloudflare 见一个拦一个（错误码 1010）；
OpenCode Go 网关还额外要求 `x-opencode-session`。

**同一个坑在 webui 那条链路上今早刚修过**（`webui._llm_headers`）——
但那是两个进程、两套客户端，只修一处等于没修。本用例的存在就是为了
下次改一处时能想起另一处。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

OC_URL = "https://opencode.ai/inference/go/openai/v1/chat/completions"
PLAIN_URL = "https://api.example.com/v1/chat/completions"


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
    print("[A] 基础头")
    h = bot_core._llm_request_headers("sk-test", OC_URL)
    check("★ 带自己的 User-Agent", "EggBloom" in str(h.get("User-Agent")), h.get("User-Agent"))
    check("★ 不是 Python-urllib 默认值",
          "Python-urllib" not in str(h.get("User-Agent")), h.get("User-Agent"))
    check("Content-Type 是 json", h.get("Content-Type") == "application/json")
    check("带 Authorization", h.get("Authorization") == "Bearer sk-test", h.get("Authorization"))
    check("带 Accept-Language", bool(h.get("Accept-Language")))

    print("\n[B] OpenCode 要会话头")
    check("★ opencode 地址带 x-opencode-session", bool(h.get("x-opencode-session")), sorted(h))
    h2 = bot_core._llm_request_headers("sk-test", PLAIN_URL)
    check("普通地址不带 x-opencode-session", "x-opencode-session" not in h2, sorted(h2))
    check("普通地址照样有 UA", "EggBloom" in str(h2.get("User-Agent")))

    print("\n[C] 空 key 不硬塞 Authorization")
    h3 = bot_core._llm_request_headers("", PLAIN_URL)
    check("无 key 时不带 Authorization", "Authorization" not in h3, sorted(h3))

    print("\n[D] 两条发送路径都走这套头")
    src = open(bot_core.__file__, encoding="utf-8").read()
    check("_post_chat 用 _llm_request_headers",
          "_post_chat" in src and src.count("headers = _llm_request_headers(") >= 2,
          src.count("headers = _llm_request_headers("))
    check("★ 没有残留的裸 headers = {\"Content-Type\": \"application/json\"}",
          'headers = {"Content-Type": "application/json"}\n    if api_key' not in src)

    print("\n[E] 摘要支线也必须走这套头（2026-11-05 第三条链路）")
    #   主链和 webui 都修过 1010，摘要自己拼 headers 于是每轮 403 Forbidden。
    #   [D] 当初只数了「两条发送路径」，漏了这条旁支 —— 这里补成三条。
    i = src.find("def _summarize_group_history_inner")
    inner = src[i:src.find("\ndef ", i + 10)] if i >= 0 else ""
    check("★ 摘要用 _llm_request_headers",
          "_llm_request_headers(" in inner, inner[:80])
    check("★ 摘要不再裸拼 Authorization", 'f"Bearer ' not in inner, inner[:80])

    print("\n[F] 流式链路（第四条）—— 11-05 真机 17:27 三次 403 就是它")
    #   前三条（主链 / webui / 摘要）都收口了，唯独 _agent_stream_chat 自己拼头。
    #   后果：每轮 Agent 工具调用先吃一个 403，再退回同步，思维链实时展示全废。
    j = src.find("def _agent_stream_chat")
    stream_fn = src[j:src.find("\ndef ", j + 10)] if j >= 0 else ""
    check("★ 流式用 _llm_request_headers",
          "headers = _llm_request_headers(" in stream_fn, stream_fn[:80])
    check("★ 流式不再裸拼 Authorization",
          '"Authorization": "Bearer " + key' not in stream_fn, stream_fn[:80])
    check("★ 但 Accept 仍是 text/event-stream",
          'headers["Accept"] = "text/event-stream"' in stream_fn)

    class _FakeSSE:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter([b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
                         b'data: [DONE]\n\n'])

    captured = {}
    _orig = (bot_core.urllib.request.urlopen, bot_core._llm_config,
             bot_core._effective_temperature)

    def _fake_urlopen(req, timeout=None):
        captured.update({k.lower(): v for k, v in req.header_items()})
        return _FakeSSE()

    try:
        bot_core.urllib.request.urlopen = _fake_urlopen
        bot_core._llm_config = lambda *a, **k: {
            "api_url": OC_URL, "api_key": "sk-test", "model_name": "m"}
        bot_core._effective_temperature = lambda *a, **k: 0.7
        out = bot_core._agent_stream_chat(
            [{"role": "user", "content": "x"}], on_reasoning=lambda s: None)
    finally:
        (bot_core.urllib.request.urlopen, bot_core._llm_config,
         bot_core._effective_temperature) = _orig
    check("★ 实发请求带自己的 UA（不是 Python-urllib）",
          "EggBloom" in str(captured.get("user-agent")), sorted(captured))
    check("★ 实发请求带 x-opencode-session",
          bool(captured.get("x-opencode-session")), sorted(captured))
    check("★ 实发请求 Accept 是 SSE",
          captured.get("accept") == "text/event-stream", captured.get("accept"))
    check("★ SSE 解析没被改坏", out.get("content") == "ok", repr(out)[:120])

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
