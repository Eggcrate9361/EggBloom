# -*- coding: utf-8 -*-
"""「老大的朋友」关系先验（2026-11-02）。

## 背景

用户把「私聊 201 关系不明确」讲清楚了：**关系不是管理员写死的，是 Bot 自己判断的**；
按设计只有一个人是管理员，**一般能加到这台机器上的私人就是"老大的朋友"**。

所以这里给的不是档案，是**默认先验**（可以先按它对待，熟了再改）。

## 契约

- 主人本人 → 空串（走 `_owner_identity_line`）；
- 非主人 + 私聊 → 「老大的朋友」先验（称呼取 `owner_titles[0]`）；
- 群聊默认**不给**（进群 ≠ 老大朋友）；`assume_owner_friend_in_group=true` 才给；
- `assume_owner_friend=false` 整体关；
- 空 sender 不炸、零 token（返回空串）。

运行：python tests/test_owner_friend_prior.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core as bc  # noqa: E402

OWNER, OTHER = "3150007406", "2011257385"
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


backup = dict(bc._config)
try:
    bc._config.update({"owner_qqs": [OWNER], "owner_titles": ["老大", "爹"]})

    check("★ 主人本人不给先验", bc._owner_friend_prior(OWNER) == "",
          bc._owner_friend_prior(OWNER))
    line = bc._owner_friend_prior(OTHER)
    check("★ 非主人私聊给先验", bool(line), line[:40])
    check("先验里写明是「老大的朋友」", "老大的朋友" in line, line[:80])
    check("先验里带对方 QQ", OTHER in line, line[:60])
    check("先验明确「别当成主人本人」", "别把他当成主人" in line, line[:100])
    check("先验允许按实际改", "按实际改" in line, line[-40:])

    check("群聊默认不给", bc._owner_friend_prior(OTHER, True) == "",
          bc._owner_friend_prior(OTHER, True))
    bc._config.update({"assume_owner_friend_in_group": True})
    check("打开开关后群聊也给", bool(bc._owner_friend_prior(OTHER, True)))

    bc._config.update({"assume_owner_friend": False})
    check("★ 总开关关掉就不给", bc._owner_friend_prior(OTHER) == "")
    bc._config.update({"assume_owner_friend": True})

    check("空 sender 不炸", bc._owner_friend_prior("") == ""
          and bc._owner_friend_prior(None) == "")

    # 主人名单为空时：谁都不是主人 → 都给先验（不崩）
    bc._config.update({"owner_qqs": [], "admin_qqs": []})
    check("没有主人配置时也不崩", isinstance(bc._owner_friend_prior(OTHER), str))
finally:
    bc._config.clear()
    bc._config.update(backup)

src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
check("主链注入了 relation_prior", '_add("relation_prior", _owner_friend_prior(' in src)
check("先验在 identity 之后（顺序稳定）",
      src.find('_add("identity"') < src.find('_add("relation_prior"'))

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
