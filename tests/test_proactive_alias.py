# -*- coding: utf-8 -*-
"""主动消息的称呼（2026-11-02 真机事故：把 A 叫成 B 的名字）。

## 事故现场

```
00:37:13 [Plugin:proactive_talk] 主动开口 → 3150007406:
         Texas 还醒着啊 / 凌晨了，你是不打算睡了是吧 …
```

而 `Texas` 是 **2011257385** 的名字（日志里 `私聊 → Texas(2011257385)`）。
配置里只有一个全局 `nickname: "Texas"`（多目标时代之前留下的），
多目标之后它被当成"通用称呼"套给所有人。

## 现在的契约

按可信度取第一个非空：
  1. `target_aliases[qq]`（插件页给这个人填的）
  2. `people_alias.get_alias(qq)`（他自己说过「叫我 X」）
  3. `people.display(qq)`（他自己的网名/群名片）
  4. 全局 `nickname` —— **只在只有一个目标时**才用
  5. 都没有 → 空串（宁可不称呼，也不叫错人）

运行：python tests/test_proactive_alias.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import plugins.proactive_talk as pt  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


A, B = "3150007406", "2011257385"

# 把 people / people_alias 的查询换成可控桩（不碰真实档案）
import people as _pp  # noqa: E402
import people_alias as _pa  # noqa: E402

_orig_display, _orig_alias = _pp.display, _pa.get_alias
_DISPLAY = {A: "水星", B: "Texas"}
_ALIAS = {B: "小德"}


def _setup():
    _pp.display = lambda uid, group_id="": _DISPLAY.get(str(uid), "")
    _pa.get_alias = lambda qq, **k: _ALIAS.get(str(qq), "")


def _teardown():
    _pp.display, _pa.get_alias = _orig_display, _orig_alias


_setup()
try:
    # ── 1. 事故本身：多目标时全局 nickname 不许套给别人 ──────────────
    cfg = {"target_qqs": [A, B], "nickname": "Texas"}
    check("★ 多目标 + 无别名：A 不再被叫成 B 的名字",
          pt._resolved_call_name(cfg, A) == "水星", pt._resolved_call_name(cfg, A))
    check("★ 多目标 + 无别名：B 用「叫我X」优先于网名",
          pt._resolved_call_name(cfg, B) == "小德", pt._resolved_call_name(cfg, B))

    # ── 2. 别名优先级 ────────────────────────────────────────────────
    cfg2 = {"target_qqs": [A, B], "nickname": "Texas",
            "target_aliases": {A: "老大", B: "小德"}}
    check("目标别名最优先（A）", pt._resolved_call_name(cfg2, A) == "老大",
          pt._resolved_call_name(cfg2, A))
    check("目标别名最优先（B，压过「叫我X」）",
          pt._resolved_call_name(cfg2, B) == "小德", pt._resolved_call_name(cfg2, B))

    # ── 3. 单目标：全局 nickname 仍然可用（老配置不掉）──────────────
    cfg3 = {"target_qqs": [A], "nickname": "水星宝"}
    check("单目标：全局 nickname 可用", pt._resolved_call_name(cfg3, A) == "水星宝",
          pt._resolved_call_name(cfg3, A))
    cfg4 = {"target_qq": A, "nickname": "水星宝"}
    check("单目标（旧键 target_qq）：也可用", pt._resolved_call_name(cfg4, A) == "水星宝",
          pt._resolved_call_name(cfg4, A))

    # ── 4. 谁都不认识 → 空，不猜 ─────────────────────────────────────
    _pp.display = lambda uid, group_id="": ""
    _pa.get_alias = lambda qq, **k: ""
    cfg5 = {"target_qqs": [A, B], "nickname": "Texas"}
    check("★ 一无所知时不硬套全局名字", pt._resolved_call_name(cfg5, A) == "",
          pt._resolved_call_name(cfg5, A))
    cfg6 = {"target_qqs": ["999999"], "nickname": "Texas"}
    check("单目标仍可用全局名", pt._resolved_call_name(cfg6, "999999") == "Texas",
          pt._resolved_call_name(cfg6, "999999"))

    # ── 5. 行为：prompt 里只出现这一个人的称呼 ───────────────────────
    _setup()
    prompts = {}

    def fake_prompt(cfg, target):
        # 只截取"你管他叫"那段所在的最小构建：直接调内部函数会真的去拉人设，
        # 所以这里复现它的赋值行，确保两者用的是同一个函数。
        return pt._resolved_call_name(cfg, target)

    for t in (A, B):
        prompts[t] = fake_prompt(cfg, t)
    check("★ prompt 侧 A 用 A 的名", prompts[A] == "水星", prompts[A])
    check("★ prompt 侧 B 用 B 的名", prompts[B] == "小德", prompts[B])
    check("两个人拿到的称呼不相同", prompts[A] != prompts[B], prompts)
finally:
    _teardown()

# ── 6. 源码里不再有"别名→全局nickname"的老写法 ──────────────────────
src = open(os.path.join(ROOT, "plugins", "proactive_talk", "__init__.py"),
           encoding="utf-8").read()
check("★ 不再直接 `or cfg.get('nickname')` 兜底",
      "_aliases.get(str(target)) if isinstance(_aliases, dict) else \"\") or" not in src)
check("称呼解析收在一个函数里", src.count("def _resolved_call_name(") == 1)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
