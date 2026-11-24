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
    # 2026-11-24：契约改了。原来钉的是「正经求助基本都做」——实测表现为
    # 「收到要求必定同意」，请求一来就应下，即便在忙也硬接。真人不是这样。
    # 现在允许拒绝，但要求给具体理由、不用"我不会"装傻。
    check("正经求助不是有求必应",
          "正经求助**不是有求必应**" in text)
    check("拒绝要给具体理由、不拿不会装傻",
          '"我不会""我做不到"装傻' in text)
    check("关系只影响语气、不影响办不办",
          '关系只决定"抱怨多重、拒绝多软"' in text)
    check("表演类仍单独另算",
          "表演类按【什么不干】另算" in text)
    # 八股来源：四档关系各给一排引号台词，模型会当台词库原样复用
    # （实测"行吧"在 151 条出站里出现 7 次）。2026-11-24 起整段删除。
    check("四档关系的台词表已删除",
          "这玩意你自己不能找啊" not in text
          and "哼，就这一次" not in text
          and "叫声哥" not in text)
    check("保留禁止背句子的约束",
          "不要背句子" in text and "二十轮内不要出现第二次" in text)
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
