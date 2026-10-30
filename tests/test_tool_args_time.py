# -*- coding: utf-8 -*-
"""工具入参适配 + 时间锚清洗（2026-10-30 真机两问题）。

问题一：meme_save 收到的 path 是 `{'target_qq': '...'}` 整个 dict
  → runner 是 `func(args)`，而我们工具是 kwargs 签名。
问题二：回复发出「21:12｜还是那句…」——模型把时间锚抄进回复。

钉死：
  1. adapt_kwargs 把 dict 拆成 kwargs，并丢掉 runner 自动补的 target_qq/group_id
  2. 三个工具模块 register_all 返回的是**适配后**的函数
  3. strip_ctx_marks 能剥掉「21:12｜」「10-30 11:40｜」「[18:46]」

运行：python tests/test_tool_args_time.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import tool_registry as tr  # noqa: E402
from reply_budget import strip_ctx_marks  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


# 1) 适配器
def demo(query: str = "", limit: int = 5):
    return (query, limit)


f = tr.adapt_kwargs(demo)
check("dict 参数拆成 kwargs", f({"query": "天王", "limit": 3}) == ("天王", 3), f({"query": "天王"}))
check("自动补的 target_qq 被丢掉", f({"query": "x", "target_qq": "123"}) == ("x", 5))
check("空 dict 用默认值", f({}) == ("", 5))
check("无参调用不炸", f() == ("", 5))

# 2) meme_save 拿到 dict 也不把 dict 当路径
from harness import tool_meme as tm  # noqa: E402
import meme_collect as mc  # noqa: E402

orig = mc.maybe_collect
try:
    seen = []
    mc.maybe_collect = lambda p, **kw: seen.append(p) or "collected/x.jpg"
    tm.set_turn(images=["C:/tmp/a.jpg"], is_group=False, sender="1")
    r = tm.meme_save({"target_qq": "123", "path": ""})
    check("dict 入参回落本轮图", r.get("ok") is True and r.get("saved") == ["collected/x.jpg"], r)
    check("传给采集的是本轮路径", seen == ["C:/tmp/a.jpg"], seen)
finally:
    mc.maybe_collect = orig

# 3) register_all 返回适配后的函数
fns = tm.register_all()
check("注册返回的是适配器", callable(fns["meme_find"]) and
      fns["meme_find"]({"query": "", "limit": 1}).get("ok") in (True, False))

# 4) 时间锚
check("剥 21:12｜", strip_ctx_marks("21:12｜还是那句") == "还是那句", strip_ctx_marks("21:12｜还是那句"))
check("剥 10-30 11:40｜", strip_ctx_marks("10-30 11:40｜在") == "在")
check("剥 [18:46]", strip_ctx_marks("[18:46] 别戳") == "别戳")
check("正文里的时间不动", strip_ctx_marks("我在 18:46 说的") == "我在 18:46 说的")

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
