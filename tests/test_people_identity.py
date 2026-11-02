# -*- coding: utf-8 -*-
"""记忆隔离 · 身份归属（2026-11-02）。

## 用户要的是什么

> 记忆隔离的意思是按私聊人分开记忆，就是 bot 知道谁是谁，
> 鸡蛋/开发者/老大/爹，Texas/朋友，他们做了什么，说了什么 bot 知道是谁做的。

所以隔离有两半：
1. **分开存/分开取**（§63/§68 已做：facts/events/messages 按 scope_id+user_id；
   沙漏日期召回私聊返回空）；
2. **知道谁是谁**（本条）：同一个人多个叫法要对上；说"鸡蛋"时 bot 知道是主人。

## 契约

- `people.add_alias(uid, name)`：追加别名（去重、最多 12 个）；
- `people.label(uid)`：记忆块短标签，如 `老大（鸡蛋/开发者）(3150007406)`；
- `people.resolve(text)`：一句话反查是谁；**多个候选命中就不猜**（返回空）；
- `people.roster_block()`：`【你认识的人】老大（鸡蛋/开发者）：主要管理员；Texas：朋友。`
  只列有名字/身份的人，私聊注入（群聊另有【群内人物】，不跨界带名字）。

运行：python tests/test_people_identity.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import people  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


# 用临时目录，别动真数据
OLD_EXTRA, OLD_BASE = people.EXTRA_FILE, people.PEOPLE_FILE
tmp = tempfile.mkdtemp()
people.EXTRA_FILE = os.path.join(tmp, "people_extra.json")
people.PEOPLE_FILE = os.path.join(tmp, "people.json")
people.reload()

OWNER, TEXAS = "3150007406", "2011257385"
try:
    # ── 1. 别名 + 主别名 ────────────────────────────────────────
    people.set_alias(OWNER, "老大")
    check("加别名 鸡蛋", people.add_alias(OWNER, "鸡蛋") is True)
    check("加别名 开发者", people.add_alias(OWNER, "开发者") is True)
    check("重复别名不重复加", people.add_alias(OWNER, "鸡蛋") is False)
    people.add_alias(OWNER, "爹")
    _lbl = people.label(OWNER)
    check("label 汇总三个叫法",
          all(x in _lbl for x in ("老大", "鸡蛋", "开发者")), _lbl)
    check("label 带 QQ", OWNER in people.label(OWNER), people.label(OWNER))

    # ── 2. 反查：谁是谁 ────────────────────────────────────────
    check("★ 提到「鸡蛋」→ 主人", people.resolve("鸡蛋说今晚不睡") == OWNER,
          people.resolve("鸡蛋说今晚不睡"))
    check("★ 提到「开发者」→ 主人", people.resolve("开发者又在改代码") == OWNER)
    check("提到「老大」→ 主人", people.resolve("老大呢？") == OWNER)
    check("无关句子不乱认", people.resolve("今天天气不错") == "")

    # ── 3. Texas = 朋友 ───────────────────────────────────────
    people.set_alias(TEXAS, "Texas")
    people.add_fact(TEXAS, "身份", "朋友（老大的朋友）")
    roster = people.roster_block()
    check("★ 小抄里有主人", "老大" in roster and "鸡蛋" in roster, roster)
    check("★ 小抄里 Texas 是朋友", "Texas" in roster and "朋友" in roster, roster)
    check("小抄一句话结尾", roster.startswith("【你认识的人】") and roster.endswith("。"),
          roster[:40])

    # ── 4. 反查不猜（两个人都叫「小」→ 空）───────────────────
    people.set_alias("999", "小a")
    people.add_alias(TEXAS, "小ab")
    check("多候选命中不猜", people.resolve("小abc") == "", people.resolve("小abc"))

    # ── 5. 主链接线 ────────────────────────────────────────────
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("★ 私聊注入了小抄", '_add("roster"' in src)
    check("★ 只在私聊（不在群聊分支）",
          'if not is_group:\n        try:\n            import people as _roster_pp' in src)
    check("小抄排在关系先验之后",
          src.find('_add("relation_prior"') < src.find('_add("roster"'))
finally:
    people.EXTRA_FILE, people.PEOPLE_FILE = OLD_EXTRA, OLD_BASE
    people.reload()

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
