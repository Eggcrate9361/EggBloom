# -*- coding: utf-8 -*-
"""group_chat_decider providers 自检（2026-10-12 改写：直连免费车道）

验证：
  1. 公开 API 签名不变（call_text / call_vision）—— UI 与 __init__.py 不用改
  2. providers.py 自己不含 HTTP 实现，HTTP 全在根目录 free_llm.py（唯一入口）
  3. 档位表 → 模型链：写形 `车道:模型` 能解析成 (lane, model)，且不依赖任何 key
  4. 容错：未知档位 / 坏图片路径都不抛异常

用法（项目根目录）：
    python plugins/group_chat_decider/tests_providers.py
只有第 4 组会真的出网（免费车道，无 key）；连不上时也会优雅返回空串。
"""
import importlib
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


pkg = importlib.import_module("plugins.group_chat_decider")
P = importlib.import_module("plugins.group_chat_decider.providers")
F = importlib.import_module("plugins.group_chat_decider.free_models")

print("=== 1. 公开 API ===")
check("call_text 存在且可调用", callable(getattr(P, "call_text", None)))
check("call_vision 存在且可调用", callable(getattr(P, "call_vision", None)))
sig_t = inspect.signature(P.call_text)
sig_v = inspect.signature(P.call_vision)
check("call_text 签名不变 (prompt, max_tokens, provider_id)",
      list(sig_t.parameters) == ["prompt", "max_tokens", "provider_id"], str(sig_t))
check("call_vision 签名不变 (image_ref, prompt, provider_id)",
      list(sig_v.parameters) == ["image_ref", "prompt", "provider_id"], str(sig_v))

print("\n=== 2. HTTP 只在 free_llm（唯一入口）===")
src = inspect.getsource(P)
check("providers.py 不再自带 _post_once", not hasattr(P, "_post_once"))
check("providers.py 不再自带 _pick_http_lib", not hasattr(P, "_pick_http_lib"))
check("providers.py 不再直接使用 urllib", "urllib.request" not in src)
check("providers.py 不再手拼 Authorization", "Authorization" not in src)
check("providers.py 不再引用智谱预设", "zhipu" not in src)
check("HTTP 实现在根 free_llm.py", os.path.exists(os.path.join(ROOT, "free_llm.py")))

print("\n=== 3. 档位 → 模型链（免 key）===")
fl = P._free_llm()
check("free_llm 可导入", fl is not None)
check("档位表已切到车道模型", all(
    str(m["text_model"]).split(":")[0] in ("zen", "kilo") for m in F.list_models()),
    str([m["text_model"] for m in F.list_models()]))
check("免 key 也视为已配置", F.is_configured() is True)
chain = P._model_chain(F.get_active_model(), "text")
check("文本链非空且形如 (lane, model)", bool(chain) and all(
    isinstance(x, tuple) and len(x) == 2 for x in chain), str(chain[:2]))
check("链首来自档位", chain and chain[0] == fl._parse_spec(F.FREE_MODELS[F.get_active_model()]["text_model"]),
      str(chain[:1]))
vchain = P._model_chain(F.get_active_model(), "vision")
check("视觉链非空", bool(vchain), str(vchain[:1]))
check("kilo 自带的冒号后缀不被切断",
      fl._parse_spec("kilo:thinkingmachines/inkling-small:free") == ("kilo", "thinkingmachines/inkling-small:free"),
      str(fl._parse_spec("kilo:thinkingmachines/inkling-small:free")))

print("\n=== 4. 容错（会出网，免费车道）===")
try:
    r = P.call_text("你好", max_tokens=8, provider_id="__nonexistent__")
    check("未知档位不抛异常、返回 str", isinstance(r, str), repr(r)[:60])
except Exception as e:
    check("未知档位不抛异常、返回 str", False, f"raised {type(e).__name__}: {e}")

try:
    v = P.call_vision("/nonexistent/path.png", "描述", provider_id="__nonexistent__")
    check("非法图片返回 {ok:False}", isinstance(v, dict) and v.get("ok") is False, str(v)[:70])
    check("非法图片带可读 error", bool(v.get("error")), str(v.get("error"))[:60])
except Exception as e:
    check("非法图片不抛异常", False, f"raised {type(e).__name__}: {e}")

print("\n" + "=" * 58)
if fails:
    print(f"失败 {len(fails)} 项：")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("全部通过：providers 直连免费车道，公开 API 不变")
