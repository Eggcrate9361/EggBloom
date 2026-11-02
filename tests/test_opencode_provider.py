# -*- coding: utf-8 -*-
"""OpenCode Go 接进 EggBloom（2026-11-02，用户：普通 LLM 卡片 + API key）。

## 官方口径（https://opencode.ai/v2/docs/console/go）

- OpenAI 兼容端点：`https://opencode.ai/inference/go/openai/v1/chat/completions`
- 鉴权：`Authorization: Bearer <Console API key>`
- 客户端要求：用自己的 User-Agent；每个会话带稳定的 `x-opencode-session`
  （用于路由和 prompt 缓存）

## 契约

- `config.API_PROVIDERS["opencode_go"]`：url + 模型目录（只收 OpenAI 兼容的那批）；
- `webui/providers.json` 有 `opencode_go` 卡片（界面下拉能看到）；
- `providers.make_provider(..., provider="opencode_go")` → `OpenCodeGoProvider`，
  带 UA 和 x-opencode-session；未知 provider 仍回退 OpenAI 兼容。

运行：python tests/test_opencode_provider.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config  # noqa: E402
import providers  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


URL = "https://opencode.ai/inference/go/openai/v1/chat/completions"

# ── 1. 配置条目 ──────────────────────────────────────────────────
prov = (config.API_PROVIDERS or {}).get("opencode_go") or {}
check("★ 有 opencode_go 条目", bool(prov), prov)
check("★ URL 指向 Go 网关", prov.get("url") == URL, prov.get("url"))
models = [str(m) for m in (prov.get("models") or [])]
check("模型目录非空", len(models) >= 5, len(models))
for m in ("deepseek-v4.1-flash", "glm-5.3-flash", "kimi-k3", "longcat-2.0"):
    check(f"模型 {m} 在目录里", m in models, models[:4])

# ── 2. UI 卡片 ───────────────────────────────────────────────────
cards = json.load(io.open(os.path.join(ROOT, "webui", "providers.json"), encoding="utf-8"))
card = next((c for c in cards if c.get("id") == "opencode_go"), None)
check("★ providers.json 有卡片", card is not None)
if card:
    check("卡片 base_url 正确", card.get("base_url") == URL, card.get("base_url"))
    check("卡片有模型", len(card.get("models") or []) >= 5, len(card.get("models") or []))

# ── 3. 适配器与请求头 ───────────────────────────────────────────
p = providers.make_provider(api_url=URL, api_key="test-key", model="glm-5.3-flash",
                            provider="opencode_go")
check("★ 选中 OpenCodeGoProvider", type(p).__name__ == "OpenCodeGoProvider",
      type(p).__name__)
hd = p.extra_headers() or {}
check("★ 带自家 UA", "EggBloom" in str(hd.get("User-Agent")), hd.get("User-Agent"))
check("★ 带 x-opencode-session", bool(hd.get("x-opencode-session")),
      hd.get("x-opencode-session"))
check("会话头稳定（同一进程不变）", hd.get("x-opencode-session") == p.extra_headers().get("x-opencode-session"))

other = providers.make_provider(api_url=URL, api_key="k", model="x", provider="deepseek")
check("别的 provider 不受影响", other.__class__.__name__ == "OpenAIProvider"
      and not (other.extra_headers() or {}))

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
