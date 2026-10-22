# -*- coding: utf-8 -*-
"""tests/test_log_levels.py —— 日志分级回归

锁死：
  1. 五类级别各自的典型行判对
  2. 错误优先于标签（「[表情] 发送失败」算 err 不是 sys）
  3. 行首标签能穿过时间戳前缀被认出来（`[00:12:34] [Plugin:x]`）
  4. 标签按名字匹配，`[Plugin:xxx]` 和 `[Plugin]` 等价
  5. 老版漏掉的前缀（Plugin: / 事件 / 表情 / 调试）确实被判成 sys
  6. 空值与普通行落到 info
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import log_levels  # noqa: E402

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
        FAILURES.append(f"{name} {extra}")
        print(f"  [!!]   {name}  {extra}")


def main():
    c = log_levels.classify

    print("[1] 五级各自命中")
    check("发送 → send", c("[20:10:58] [发送] 群[1] 分行 12 条") == "send")
    check("Plugin → sys", c("[19:52:35] [Plugin:proactive_talk] 心跳线程启动") == "sys")
    check("AI 回复 → ai", c("[20:11:11] AI 回复 (100 tokens, ￥0.000050)") == "ai")
    check("超时重试 → warn", c("[20:10:04] summarize: 下次重试") == "warn")
    check("失败 → err", c("[19:52:33] 私聊 搜索已失败，跳过重搜") == "err")

    print("\n[2] 错误优先于标签")
    check("[表情] 发送失败 → err（不是 sys）",
          c("[表情] 发送失败") == "err", c("[表情] 发送失败"))
    check("[Plugin:x] 加载异常 → err",
          c("[Plugin:foo] 加载异常") == "err")

    print("\n[3] 穿过时间戳前缀识别标签")
    check("带 [HH:MM:SS] 仍能取到标签",
          log_levels.leading_tag("[00:12:34] [Plugin:x] hi") == "Plugin")
    check("无时间戳也能取", log_levels.leading_tag("[事件] 收到") == "事件")
    check("普通行没有标签", log_levels.leading_tag("随便一句话") == "")

    print("\n[4] 标签按名字匹配，带不带右括号都行")
    check("[Plugin] 等于 [Plugin:x]",
          c("[Plugin] x") == c("[Plugin:abc] x") == "sys")
    check("[群管理] → sys", c("[16:57:18] [群管理] set_group_ban") == "sys")

    print("\n[5] 老规则漏掉的前缀（真实日志高频）")
    for tag in ("Plugin:proactive_talk", "Plugin:scheduler", "事件", "表情", "调试", "搜索"):
        line = f"[12:00:00] [{tag}] 内容"
        check(f"[{tag}] → sys", c(line) == "sys", c(line))

    print("\n[6] 反向词（带「失败」但语义是恢复）")
    check("写盘已恢复 → info",
          c("[状态] 写盘已恢复（此前连续失败 2 次）") == "info",
          c("[状态] 写盘已恢复（此前连续失败 2 次）"))
    check("重连成功 → info", c("[状态] 重连成功") == "info")
    check("真失败仍然红", c("[状态] 写盘失败") == "err")

    print("\n[7] 空值与普通行")
    check("None → info", c(None) == "info")
    check("空串 → info", c("") == "info")
    check("普通行 → info", c("[19:52:33] 私聊 联网搜索: 恋人不行") == "info")
    check("标签未登记 → info", c("[未知标签] 内容") == "info")

    print("\n[8] 标签表与级别名合法")
    check("所有标签值都在 LEVELS 内",
          all(v in log_levels.LEVELS for v in log_levels.TAG_LEVELS.values()))
    check("所有 RULES 级别都在 LEVELS 内",
          all(lv in log_levels.LEVELS for lv, _ in log_levels.RULES))
    check("LABELS 覆盖 LEVELS + info",
          set(log_levels.LEVELS) | {"info"} == set(log_levels.LABELS))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
