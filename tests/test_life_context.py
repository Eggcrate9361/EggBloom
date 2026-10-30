# -*- coding: utf-8 -*-
"""harness/life_context 单测（1.6.0「一个大脑」地基，2026-10-30）。

这一层只做一件事：把散在各地的上下文块（记忆 / 情绪关系 / 场景 / 称呼 / 另一边）
按**固定顺序**拼成一段，超预算时**整块**丢。

钉死：
  1. 空输入 → 空串（零 token）
  2. 顺序固定且可断言（前缀缓存稳定）
  3. 同一段文字出现两次只留一次
  4. 超预算从后往前丢；只剩一块时**不丢**（宁可超一点，也不能什么都没注入）
  5. describe() 能说出丢了谁

运行：python tests/test_life_context.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import life_context as lc  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


# 1. 空
check("空 dict → 空串", lc.compose({}) == "", lc.compose({}))
check("None → 空串", lc.compose(None) == "")
check("全空白 → 空串", lc.compose({"ego": "  ", "memory": ""}) == "")

# 2. 顺序
parts = {"ego": "E", "memory": "M", "scene": "S", "alias": "A", "cross_scene": "C"}
out = lc.compose(parts)
check("顺序：记忆最前", out.index("M") < out.index("E"), out)
check("顺序：ego 在 scene 前", out.index("E") < out.index("S"), out)
check("顺序：场景在称呼前", out.index("S") < out.index("A"), out)
check("顺序：近况最后", out.index("A") < out.index("C"), out)

# 3. 去重
out2 = lc.compose({"ego": "同一段", "memory": "同一段"})
check("重复段只留一次", out2.count("同一段") == 1, out2)

# 4. 预算
small = lc.compose({"memory": "M" * 100, "ego": "E" * 100,
                    "scene": "S" * 100, "cross_scene": "C" * 100}, budget=250)
check("超预算丢尾块", "C" * 100 not in small, len(small))
check("超预算仍保留记忆", "M" * 100 in small, len(small))
one = lc.compose({"memory": "M" * 100}, budget=10)
check("只剩一块时不丢（宁超勿空）", "M" * 100 in one or len(one) > 0, len(one))

# 5. describe
d = lc.describe({"memory": "M" * 100, "ego": "E" * 100, "scene": "S" * 100,
                 "cross_scene": "C" * 100}, budget=250)
check("describe 报出保留项", "memory" in d["kept"], d)
check("describe 报出丢弃项", "cross_scene" in d["dropped"], d)
check("describe 有字数", isinstance(d["chars"], int) and d["chars"] > 0, d)

# 6. 未知 key 不进输出（防手滑把整段 dict 拼进去）
out3 = lc.compose({"ego": "E", "garbage": "G"})
check("未知来源不注入", "G" not in out3, out3)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
