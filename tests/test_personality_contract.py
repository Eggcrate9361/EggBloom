# -*- coding: utf-8 -*-
"""人格规则边界：允许拒绝表演，但不能把正经求助也一并拒掉。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PATH = os.path.join(ROOT, "plugins", "personality.txt")

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if cond else f"{name}  {extra}")
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


def main():
    text = open(PATH, encoding="utf-8").read()
    check("允许真正拒绝表演",
          "表演类可以**真的**不干" in text)
    check("辩论/扮演/复读有明确拒绝边界",
          "辩论、互相诋毁、扮演角色、朗读、复读" in text)
    check("正经求助仍要执行",
          "正经求助基本都做" in text
          and "表演类按【什么不干】另算" in text)
    check("关系抱怨只约束正经求助",
          "这些只适用于正经求助" in text)
    check("不再保留无条件兜底句",
          "让我做事基本都做" not in text
          and "事从来没少办" not in text)
    check("查询结果直接接话，不报后台",
          "查到的事也一样" in text and "别提「搜到」" in text)
    check("禁止编造身体状态",
          "身体状态也一样" in text and "不说「正躺着」" in text)

    print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
    for x in FAIL:
        print("  FAIL", x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
