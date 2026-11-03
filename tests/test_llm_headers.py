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

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
