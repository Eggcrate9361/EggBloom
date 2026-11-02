# -*- coding: utf-8 -*-
"""主人身份认知（2026-11-02 真机）。

## 事故

用户在私聊里被 Bot 当普通用户对待，甚至回「你爹是谁我还没见着呢」。
查配置：三份名单各管一段 ——

| 键 | 值 | 谁在用 |
|---|---|---|
| `command_admins` | `["2011257385"]` | `_get_admin_qqs()` → 指令权限 |
| `admin_qqs` | `["3150007406"]` | 群管理指令 / 通知 |
| `private_tool_whitelist` | `["3150007406"]` | 私聊工具 |

**prompt 里一个字都没说"对方是主人"**，而"管理员"又恰好是另一个 QQ。

## 契约

1. `owner_qqs`（新）：主人名单；空则退回 `admin_qqs`（老配置不掉）；
2. `owner_titles`：该叫他什么（老大 / 爹 …）；
3. 只在**名单内**的人头上生效：`_owner_identity_line()` 对别人返回空串（零 token）；
4. 身份说明进动态上下文（system 级），不进用户消息。

运行：python tests/test_owner_identity.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core as bc  # noqa: E402
import config as cfg_mod  # noqa: E402

OWNER, OTHER = "3150007406", "2011257385"
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


backup = dict(bc._config)
try:
    # ── 1. 名单：owner_qqs 优先，空则退回 admin_qqs ─────────────────
    bc._config.update({"owner_qqs": [OWNER], "admin_qqs": [OTHER]})
    check("owner_qqs 优先", bc._owner_qq_set() == {OWNER}, bc._owner_qq_set())

    bc._config.update({"owner_qqs": [], "admin_qqs": [OWNER]})
    check("★ 空的 owner_qqs 退回 admin_qqs（老配置不掉）",
          bc._owner_qq_set() == {OWNER}, bc._owner_qq_set())

    bc._config.update({"owner_qqs": [], "admin_qqs": []})
    check("两边都空 → 空集合", bc._owner_qq_set() == set(), bc._owner_qq_set())

    # 容错：单字符串也能吃
    bc._config.update({"owner_qqs": OWNER})
    check("owner_qqs 写成字符串也认", bc._owner_qq_set() == {OWNER}, bc._owner_qq_set())

    # ── 2. 身份说明：只对主人生效 ───────────────────────────────────
    bc._config.update({"owner_qqs": [OWNER], "owner_titles": ["老大", "爹"]})
    line = bc._owner_identity_line(OWNER)
    check("★ 主人拿到身份说明", bool(line), line[:40])
    check("★ 说明里带 QQ", OWNER in line, line[:60])
    check("★ 说明里带两个称呼", "老大" in line and "爹" in line, line[:80])
    check("别人拿到空串（零 token）", bc._owner_identity_line(OTHER) == "",
          bc._owner_identity_line(OTHER))
    check("空 sender 不炸", bc._owner_identity_line("") == "" and
          bc._owner_identity_line(None) == "")

    # ── 3. 称呼缺省要有兜底 ────────────────────────────────────────
    bc._config.update({"owner_titles": []})
    check("没配称呼 → 兜底「老大」", "老大" in bc._owner_identity_line(OWNER))
    bc._config.update({"owner_titles": None})
    check("称呼为 None 不炸", "老大" in bc._owner_identity_line(OWNER))

    # ── 4. 配置默认值存在（新装也有这套）──────────────────────────
    d = cfg_mod.DEFAULT_CONFIG
    check("DEFAULT_CONFIG 有 owner_qqs", "owner_qqs" in d, sorted(d)[:3])
    check("DEFAULT_CONFIG 有 owner_titles", "owner_titles" in d)

    # ── 5. 主链真的注入了（防止只写函数不接线）───────────────────
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("★ 主链 _add(\"identity\", ...)", '_add("identity", _owner_identity_line(' in src)
    check("没有把身份拼进用户消息",
          "_owner_identity_line(user_msg" not in src)
finally:
    bc._config.clear()
    bc._config.update(backup)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
