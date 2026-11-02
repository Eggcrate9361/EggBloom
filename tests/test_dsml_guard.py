# -*- coding: utf-8 -*-
"""工具调用格式泄漏到正文（2026-11-02 真机，16:31 私聊）。

## 事故

模型调 `meme_send` 时参数名不对（写成了 id/name 之类），runner 只按签名过滤
→ `TypeError: meme_send() missing 1 required positional argument: 'meme_id'`；
模型下一轮把**原生工具调用格式**当正文吐了出来，用户收到 5 条气泡：

```
<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="meme_send">
<｜｜DSML｜｜ parameter name="meme_id" string="true">collected/f8404cfc9caa.jpg</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>
```

## 契约

1. `_strip_dsml(text)`：剥掉 DSML 块，返回 (干净文本, 是否命中)；混排时保留正文；
2. 群聊/私聊两条回复链都接上；剥空后给一句人话兜底，绝不发标记；
3. `meme_send` 对 id/name/file/path 等别名容错；缺参**不抛异常**，返回可读错误
   （抛异常会诱导模型继续乱试格式）。

运行：python tests/test_dsml_guard.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core as bc  # noqa: E402
from harness import tool_meme as tm  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


RAW = (
    '<｜｜DSML｜｜ calls>\n'
    '<｜｜DSML｜｜ invoke name="meme_send">\n'
    '<｜｜DSML｜｜ parameter name="meme_id" string="true">collected/f8404cfc9caa.jpg</｜｜DSML｜｜ parameter>\n'
    '</｜｜DSML｜｜ invoke>\n'
    '</｜｜DSML｜｜ calls>'
)

# ── 1. 剥离 ────────────────────────────────────────────────────────
clean, hit = bc._strip_dsml(RAW)
check("★ 纯 DSML 全剥掉", hit and clean == "", (hit, clean))
mixed, hit2 = bc._strip_dsml("先说一句\n" + RAW)
check("★ 混排保留正文", mixed == "先说一句", mixed)
check("命中标记正确", hit2 is True)
same, hit3 = bc._strip_dsml("正常的一句话")
check("正常文本不动", same == "正常的一句话" and hit3 is False, (same, hit3))
check("空串不炸", bc._strip_dsml("") == ("", False))

src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
check("★ 群/私聊两处都接上", src.count("_strip_dsml(sent_text)") == 2,
      src.count("_strip_dsml(sent_text)"))
check("剥空有人话兜底", "工具调用没走通" in src)

# ── 2. meme_send 容错 ─────────────────────────────────────────────
r = tm.meme_send()
check("★ 缺参不再抛异常", isinstance(r, dict) and r.get("ok") is False, r)
check("缺参错误可读（教模型怎么传）", "meme_id" in str(r.get("error")), r.get("error"))

for alias in ("id", "name", "file", "path"):
    r2 = tm.meme_send(**{alias: "collected/__not_exist__.jpg"})
    check(f"别名 {alias} 被认（走到'没这张图'而不是缺参）",
          isinstance(r2, dict) and "没这张图" in str(r2.get("error")),
          r2.get("error"))

import inspect  # noqa: E402

sig = inspect.signature(tm.meme_send)
check("签名有默认值（可无参调用）", sig.parameters["meme_id"].default == "", sig)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
