# -*- coding: utf-8 -*-
"""tests/test_memory_fact_gate.py —— 事实质量闸（读/写两侧共用）

2026-10-31 真机：facts 19 条里能用的不到 3 条（`身份=老大` / `身份=鸡蛋` /
`偏好=概率：多少`），每轮都进 prompt → 模型把**当前对话对象**当成「老大」，
用户原话「把其他用户当作我（老大）」。

闸只有一份（`_fact_is_usable`），写入和读侧共用。这个文件锁住它的边界：

  A. 身份：纯称谓词、2~3 字无词尾、含「老大」的残句 → 丢
  B. 身份：学生/程序员/上海人 这类有词尾的 → 留
  C. 偏好类：「X：疑问词」半截抽取 → 丢；「喜欢：X」→ 留
  D. 通用：超长、空 → 丢
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "eb_super_memory_test", os.path.join(ROOT, "plugins", "ego", "super_memory", "__init__.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)
ok_fact = M._fact_is_usable

PASS = 0
FAIL = 0
FAILURES = []


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
    print("[A] 身份类垃圾")
    for v in ("老大", "老板", "小狗", "鸡蛋", "绳子", "张雪峰", "程序啊", "你老大修一下"):
        check(f"丢 {v!r}", ok_fact("身份", v) is False)
    print("\n[B] 身份类正常值")
    for v in ("学生", "程序员", "上海人", "老师", "群主"):
        check(f"留 {v!r}", ok_fact("身份", v) is True, v)
    print("\n[C] 偏好/其它")
    check("概率：多少 丢掉", ok_fact("偏好", "概率：多少") is False)
    check("喜欢：真的 留着", ok_fact("偏好", "喜欢：真的") is True)
    check("城市：杭州 留着", ok_fact("偏好", "城市：杭州") is True)
    check("空值丢掉", ok_fact("偏好", "") is False)
    check("超长丢掉", ok_fact("偏好", "x" * 25) is False)
    print("\n[D] 事件/位置不被误杀")
    check("吃饭", ok_fact("事件", "吃饭") is True)
    check("上语文课", ok_fact("位置", "上语文课") is True)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
