# -*- coding: utf-8 -*-
"""group_chat_decider providers 收敛自检（任务 2.2）。

验证：
  1. 公开 API 仍在（call_text / call_vision 签名不变）
  2. 本插件 providers.py 不再自带 HTTP 实现（无 _post_once / _pick_http_lib /
     urllib.request 直接调用）—— 即确实收敛到主层
  3. _build_client 能把预设翻译成**主层** provider 适配器（类型来自根 providers.py）
  4. 无 key 时 call_text 优雅返回空串（不抛异常），降级链不炸

用法（在项目根目录）：
    python plugins/group_chat_decider/tests_providers.py
不需要 pytest，也不会真的发网络请求（用未配置 key 的路径验证）。
"""
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


# 以包形式导入，模拟真实插件加载
import importlib
pkg = importlib.import_module("plugins.group_chat_decider")
P = importlib.import_module("plugins.group_chat_decider.providers")

print("=== 1. 公开 API ===")
check("call_text 存在且可调用", callable(getattr(P, "call_text", None)))
check("call_vision 存在且可调用", callable(getattr(P, "call_vision", None)))
sig_t = inspect.signature(P.call_text)
sig_v = inspect.signature(P.call_vision)
check("call_text 签名不变 (prompt, max_tokens, provider_id)",
      list(sig_t.parameters) == ["prompt", "max_tokens", "provider_id"], str(sig_t))
check("call_vision 签名不变 (image_ref, prompt, provider_id)",
      list(sig_v.parameters) == ["image_ref", "prompt", "provider_id"], str(sig_v))

print("\n=== 2. 确认已收敛（不再自带 HTTP 实现）===")
check("不再有 _post_once", not hasattr(P, "_post_once"))
check("不再有 _pick_http_lib", not hasattr(P, "_pick_http_lib"))
src = inspect.getsource(P)
check("源码里不再直接 import urllib.request", "urllib.request" not in src)
check("源码里不再手拼 Authorization 头", "Authorization" not in src)
check("仍保留插件特有的降级链 _fallback_chain", hasattr(P, "_fallback_chain"))
check("降级链仍是 zhipu/aliyun/siliconflow",
      P._fallback_chain("zhipu") == ["zhipu", "aliyun", "siliconflow"], str(P._fallback_chain("zhipu")))
check("ollama 不参与跨网降级", P._fallback_chain("ollama") == ["ollama"])

print("\n=== 3. 预设 → 主层适配器 ===")
try:
    from providers import Provider  # 主层基类
    check("主层 providers 可导入", True)
except Exception as e:
    check("主层 providers 可导入", False, str(e))
    Provider = None

# 用 ollama 预设（无需 key）验证翻译路径
adapter, model, err = P._build_client("ollama")
check("ollama 预设可构造适配器（无需 key）", adapter is not None, f"err={err}")
if adapter is not None and Provider is not None:
    check("适配器来自主层 providers.py", isinstance(adapter, Provider),
          type(adapter).__module__)
    check("api_url 指向 /chat/completions", str(adapter.api_url).endswith("/chat/completions"),
          adapter.api_url)
    check("模型名来自预设 text_model", model == adapter.model, model)

print("\n=== 4. 降级与容错 ===")
# 未知预设会走降级链；本机若已配置 zhipu 等 key，会真的拿到回复 —— 那正说明降级链工作。
# 这里只断言「不抛异常、返回 str」，不假设一定为空（是否可答取决于本机 key 配置）。
try:
    r = P.call_text("你好", max_tokens=8, provider_id="__nonexistent__")
    check("call_text 未知预设不抛异常、返回 str", isinstance(r, str), repr(r)[:70])
except Exception as e:
    check("call_text 未知预设不抛异常、返回 str", False, f"raised {type(e).__name__}: {e}")

# 无 key 场景：临时把降级链收窄到一个必然无 key 的预设，
# 用 free_models.get_provider 打桩，确保「无 key → 返回空串而非异常」这条路径被覆盖。
_orig_get = P.free_models.get_provider
try:
    P.free_models.get_provider = lambda pid=None: {
        "api_key": "", "base_url": "http://127.0.0.1:9/v1", "text_model": "x",
    }
    r2 = P.call_text("你好", max_tokens=8, provider_id="zhipu")
    check("无 key 时返回空串而非抛异常", r2 == "", repr(r2))
finally:
    P.free_models.get_provider = _orig_get

try:
    v = P.call_vision("/nonexistent/path.png", "描述", provider_id="__nonexistent__")
    check("call_vision 非法图片返回 {ok:False}", isinstance(v, dict) and v.get("ok") is False, str(v)[:80])
    check("call_vision 返回可读 error", bool(v.get("error")), str(v.get("error"))[:60])
except Exception as e:
    check("call_vision 非法图片不抛异常", False, f"raised {type(e).__name__}: {e}")

print("\n" + "=" * 58)
if fails:
    print(f"失败 {len(fails)} 项：")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("全部通过：group_chat_decider 已收敛到主层 providers，公开 API 不变")
