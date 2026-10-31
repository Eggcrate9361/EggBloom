# -*- coding: utf-8 -*-
"""tests/test_reply_guard.py —— 回复出口硬约束（FIXPLAN 1.5.11 P0-2 第 2 条）

背景：人格文件写着"别露后台""别自报简历"，但提示词是概率的。1.5.10 真机反复
出现：被问身份就念简历、把工具/视觉中间态说出去、把自己的号码报出来。

这里锁四件事：
  A. 号码（6 位以上）连同它所在小句删掉
  B. 硬后台词（转述/原图/工具返回/系统提示）出现即丢整句
  C. 软后台词（接口/模型/识别结果）**只有**同句带运行信号才丢 —— 正常聊 AI 不误杀
  D. 简历式三段折叠成一句；干净文本原样返回（幂等）

纯字符串函数，不联网、不起插件。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from reply_guard import guard  # noqa: E402

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


def eq(name, got, want):
    check(name, got == want, f"got={got!r} want={want!r}")


def main():
    print("[A] 号码")
    eq("QQ 号连小句删掉", guard("有，3150007406，加我。"), "有，加我。")
    out = guard("群 123456789 挺热闹")
    check("群里号码不整段崩", "123456789" not in out and "热闹" in out, out)
    check("短数字不动", guard("我今年 20 了") == "我今年 20 了", guard("我今年 20 了"))

    print("\n[B] 硬后台词")
    eq("原图整句丢", guard("这是我看到的原图。"), "")
    eq("工具执行异常整句丢", guard("工具执行异常：timeout"), "")
    eq("多句里只丢坏句", guard("查不到。\n转述一下那个报错。\n网卡了。"), "查不到。\n网卡了。")

    print("\n[C] 软后台词（不误杀正常聊天）")
    eq("聊模型新闻保持原样", guard("我在看模型新闻。"), "我在看模型新闻。")
    eq("接口 + 运行信号要丢", guard("我从接口拿的。"), "")
    # 「识别结果」本身就是中间态，不需要再补运行信号
    eq("识别结果整句丢", guard("识别结果是这样。"), "")
    eq("识别结果 + 这轮也丢", guard("这轮识别结果是这样。"), "")

    print("\n[D] 简历与幂等")
    eq("三段简历折叠成一句",
       guard("我是小蛋。我今年二十。我住在你手机里。"), "我是小蛋。")
    eq("两句不折", guard("我是小蛋。我今年二十。"), "我是小蛋。我今年二十。")
    clean = "早，今天起这么早。"
    eq("干净文本原样返回", guard(clean), clean)
    eq("空串安全", guard(""), "")

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
