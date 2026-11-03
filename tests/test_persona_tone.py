# -*- coding: utf-8 -*-
"""tests/test_persona_tone.py —— 基调：少反问、别黏人（2026-11-03 用户反馈）

## 为什么单独一个用例

用户看过私聊记录后说：「bot 总是在反问，几乎每一轮回答」「基础性格冷漠一点」。

这不是某个功能坏了，是**基调**问题：提示词里写着「毒舌带撒娇」，主动说话的
提示词又没有任何"别问"的约束 —— 模型于是把"问一句"当成最省力的续话方式。
基调改动没有任何运行时错误，只能靠用例守住，否则下次谁改提示词就悄悄回去了。

查的时候要**分段**：`撒娇` 这个词在【表情包】的情绪词表、在关系分层里
（「全是傲娇和撒娇」）都是合法的，只有在【我是谁】的自我描述里出现才说明基调没改。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

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
    p = os.path.join(ROOT, "plugins", "personality.txt")
    raw = open(p, encoding="utf-8").read()

    print("[A] 基础基调：冷淡、不黏人")
    i0 = raw.find("【我是谁】")
    i1 = raw.find("【怎么说话】")
    who = raw[i0:i1] if 0 <= i0 < i1 else ""
    check("找到【我是谁】段", bool(who), (i0, i1))
    # 判"带撒娇"而不是"不含撒娇"：新文案里的"**不**撒娇"是我们要的表述
    check("★ 不再把撒娇当自我标签", "带撒娇" not in who, who[:120])
    check("★ 明确写了不撒娇", "不撒娇" in who, who[:120])
    check("★ 自我描述里不再写“害羞”", "害羞" not in who, who[:120])
    check("写明冷淡", "冷淡" in who or "嘴毒" in who, who[:120])
    check("写明不黏人", "不黏人" in who, who[:120])

    print("\n[B] 少反问：规则必须写在接话块里")
    check("★ 有【少反问】", "【少反问】" in raw)
    check("★ 默认用陈述收尾", "陈述收尾" in raw)
    check("★ 明确不许追问半句话", "不追问" in raw)
    check("★ 点名禁掉“说完整啊”", "说完整啊" in raw)
    check("★ 不许把话头推回去", "把话头推回去" in raw)
    check("反问只留两种情况", "怼人" in raw and "澄清" in raw)
    # 原来的"一句话里最多一个问题"必须还在（别把好的规则一起删了）
    check("保留“最多一个问题”", "最多一个问题" in raw)

    print("\n[C] 主动说话：别像查岗")
    pro = open(os.path.join(ROOT, "plugins", "proactive_talk", "__init__.py"),
               encoding="utf-8").read()
    check("★ 主动提示词含【别用问句】", "【别用问句】" in pro)
    check("★ 点名禁掉查岗式提问", "查岗" in pro and "吃了吗" in pro)
    check("★ 语气偏冷写进提示词", "语气偏冷" in pro)
    check("★ 禁掉反复催", "别装死" in pro)

    print("\n[D] 结构没被改坏（交给 test_persona_format 兜的结构前提）")
    check("【不许】规则层还在", "【不许】" in raw)
    check("### 块还在", raw.count("### ") >= 8, raw.count("### "))
    check("scope 标注还在", "scope=both" in raw)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
