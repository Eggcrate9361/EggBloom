# -*- coding: utf-8 -*-
"""「莫名其妙的图片」两件事（2026-11-02 真机）。

## 事故

私聊日志里：

```
12:03:47 [画图] 检测到画图意图: 你哪来的图片
12:03:49 [画图] 连接 ComfyUI: 127.0.0.1:8000
12:03:51 [画图] 结果: ComfyUI 未连接，请检查远程服务器
12:07:23 [画图] 检测到画图意图: 不要发莫名其妙的图片给我
12:06:58 [私聊表情] 自动检测: 温柔 → 发送 2de2cbdc0788.jpg
```

两个问题：
1. **画图意图正则里有裸的「的图片」** —— 只要句子里出现"图片"两个字就触发，
   于是先冒一句"画图中，请稍等…"，再去连没开的 ComfyUI，白等 2 秒；
2. **私聊表情包自动配图**：模型没标 `[表情:x]` 时，靠情绪猜测硬塞一张。

## 契约

- `_is_draw_request(text)`：必须有"画/出图/生成图"**动词**，且不是否定/抱怨语境；
- `_draw_backend_ready()`：后端没开就不进画图分支（不白等、不打"未连接"）；
- 私聊自动配图默认关（`private_meme_auto_detect=False`）：
  只有模型自己标了 `[表情:xx]` 才发。

运行：python tests/test_draw_intent.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core as bc  # noqa: E402
import config as cfg_mod  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


# ── 1. 事故原话必须不触发 ──────────────────────────────────────────
for msg in ("你哪来的图片", "不要发莫名其妙的图片给我", "这图不错",
            "图片挺好看", "你发这么多图干嘛", "别发图了"):
    check(f"不该画：{msg}", bc._is_draw_request(msg) is False, bc._is_draw_request(msg))

# ── 2. 真画图请求要触发 ────────────────────────────────────────────
for msg in ("帮我画一张猫", "给我画只猫", "画个狗看看", "来一张风景图",
            "生成一张赛博朋克图", "画张图", "给我画幅山水"):
    check(f"该画：{msg}", bc._is_draw_request(msg) is True, bc._is_draw_request(msg))

# ── 3. 否定语境压过动词 ────────────────────────────────────────────
check("★「不要帮我画」不算请求", bc._is_draw_request("不要帮我画了") is False,
      bc._is_draw_request("不要帮我画了"))
check("★「谁让你画图了」不算请求", bc._is_draw_request("谁让你画图了") is False)
check("空串/None 不炸", bc._is_draw_request("") is False and
      bc._is_draw_request(None) is False)

# ── 4. 后端开关 ────────────────────────────────────────────────────
backup = dict(bc._config)
try:
    bc._config.update({"image_gen_enable": False})
    check("★ 后端关了就不画", bc._draw_backend_ready() is False, bc._draw_backend_ready())
    bc._config.update({"image_gen_enable": True})
    check("后端开着可以画", bc._draw_backend_ready() is True, bc._draw_backend_ready())
finally:
    bc._config.clear()
    bc._config.update(backup)

# ── 5. 自动配图默认关 ─────────────────────────────────────────────
check("★ DEFAULT_CONFIG 里 private_meme_auto_detect 默认 False",
      cfg_mod.DEFAULT_CONFIG.get("private_meme_auto_detect") is False,
      cfg_mod.DEFAULT_CONFIG.get("private_meme_auto_detect"))
src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
check("主链读这个开关", 'private_meme_auto_detect' in src)
check("两处画图检测都换成 _is_draw_request",
      src.count("_is_draw_request(") >= 2, src.count("_is_draw_request("))
check("旧的裸「的图片」正则已不在画图判断里",
      "|的图片|" not in src)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
