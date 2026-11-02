# -*- coding: utf-8 -*-
"""回复里复述发言标签（2026-11-02 真机：像"说话被强行截断"）。

## 事故

16:19 私聊 315，实际发出去的一条气泡：

```
你要是真去群里问，我第一个答：我爹就一个，(/ω＼)(3150007406)
```

模型把历史里 `昵称(QQ): 内容` 的格式当成了**自己的输出格式**，
把发送者标签抄进了正文。用户看到的是"话说一半断了，后面挂一串乱码"。

## 契约

`_strip_speaker_labels(reply, *names)` 只处理**回复文本**（历史不动）：

1. 摘掉已知昵称本体（含 `(/ω＼)` 这种括号昵称）；
2. 摘掉 `(123456789)` / `（QQ：123456789）`；
3. 清理摘空后留下的空括号、连续标点、行首残留标点；
4. 群聊/私聊两条回复链都接上。

运行：python tests/test_reply_label_leak.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core as bc  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


S = bc._strip_speaker_labels

# ── 1. 真机原样 ────────────────────────────────────────────────────
real = "你要是真去群里问，我第一个答：我爹就一个，(/ω＼)(3150007406)\n\n行了吧，别委屈了"
out = S(real, "(/ω＼)", "Texas")
check("★ 摘掉括号昵称+QQ", "(/ω＼)" not in out and "3150007406" not in out, out)
check("★ 行尾残逗号收掉", "我爹就一个\n" in out or "我爹就一个" in out, out)
check("正文内容保留", "我第一个答" in out and "别委屈了" in out, out)

# ── 2. 其他形态 ────────────────────────────────────────────────────
check("名字+冒号开头", S("Texas: 我只说一遍", "Texas") == "我只说一遍",
      S("Texas: 我只说一遍", "Texas"))
check("QQ 括号", S("（QQ：3150007406）在的") == "在的", S("（QQ：3150007406）在的"))
check("空括号不残留", "（）" not in S("在的（3150007406）") and "()" not in S("在的(3150007406)"),
      S("在的（3150007406）"))

# ── 3. 正常文本不许被误伤 ─────────────────────────────────────────
for t in ("正常的一句：我爹就一个。", "我 2026 年在上班", "好的，明白！"):
    check(f"不动正常文本：{t}", S(t) == t, S(t))
check("空串/None 不炸", S("") == "" and not S(None))

# ── 4. 接线 ────────────────────────────────────────────────────────
src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
check("★ 群聊回复接了", src.count("_strip_speaker_labels(") >= 3,
      src.count("_strip_speaker_labels("))
check("两处调用都带 sender",
      src.count("_strip_speaker_labels(_strip_context_marks(sent_text), sender)") == 2,
      src.count("_strip_speaker_labels(_strip_context_marks(sent_text), sender)"))

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
