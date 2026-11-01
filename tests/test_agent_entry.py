# -*- coding: utf-8 -*-
"""入口旁路的闸（1.6.0 刀 3b）：agent 不再"顶回复"，只当工具。

## 为什么

FIXPLAN §1.1 点名的病灶：群里 `@它 看下今天新闻` 会被入口那条 agent 分支接走，
跑完 `smart_send_group` **直接发** —— 人格、预算、闸、审计、认账全绕过。
所以同一句话，"闲聊"和"任务"给出两个答案。

刀 3a 已经把 `agent_service.run_chat` 包成工具 `deep_task`；这一刀把入口那条
旁路压上闸 `agent_legacy_entry`（**默认关**）：

- 关（默认）：入口只走主链，多步工具由 `deep_task` 承接；
- 开：退回老行为（还要同时开 `agent_group_enable` / `agent_private_enable`）。

## 钉死什么

1. `DEFAULT_CONFIG.agent_legacy_entry` 是 False（默认不走旁路）；
2. 闸是**合取**：闸关时，即使 `agent_group_enable=true` 也不触发；
3. 群/私聊各看各的开关；
4. 入口代码确实用了这个函数，而不是又直接读裸配置（防止以后被改回去）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core as bc  # noqa: E402
import config as cfgmod  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


def with_cfg(**kw):
    """临时改 bot_core._config 跑一次，返回闸的结果。"""
    backup = dict(bc._config)
    try:
        bc._config.update(kw)
        return bc._agent_legacy_entry_enabled(kw.pop("_is_group", True))
    finally:
        bc._config.clear()
        bc._config.update(backup)


# ── 1. 默认值 ────────────────────────────────────────────────────────
check("★ 默认关：DEFAULT_CONFIG.agent_legacy_entry=False",
      cfgmod.DEFAULT_CONFIG.get("agent_legacy_entry") is False,
      cfgmod.DEFAULT_CONFIG.get("agent_legacy_entry"))
check("默认值还在 example 里登记",
      '"agent_legacy_entry"' in open(os.path.join(ROOT, "bot_config.json.example"),
                                     encoding="utf-8").read())

# ── 2. 闸是合取：闸关时，就算群开关开着也不走旁路 ────────────────────
backup = dict(bc._config)
try:
    bc._config.update({"agent_legacy_entry": False, "agent_group_enable": True,
                       "agent_private_enable": True})
    check("★ 闸关 + 群开关开 → 不触发", bc._agent_legacy_entry_enabled(True) is False)
    check("★ 闸关 + 私聊开关开 → 不触发", bc._agent_legacy_entry_enabled(False) is False)

    bc._config.update({"agent_legacy_entry": True,
                       "agent_group_enable": False, "agent_private_enable": False})
    check("闸开 + 群开关关 → 不触发（群）",
          bc._agent_legacy_entry_enabled(True) is False)
    bc._config.update({"agent_group_enable": True})
    check("闸开 + 群开关开 → 触发（群）", bc._agent_legacy_entry_enabled(True) is True)
    check("群开关不连带私聊", bc._agent_legacy_entry_enabled(False) is False)
    bc._config.update({"agent_private_enable": True})
    check("闸开 + 私聊开关开 → 触发（私聊）", bc._agent_legacy_entry_enabled(False) is True)
finally:
    bc._config.clear()
    bc._config.update(backup)

# ── 3. 入口确实用这个函数（防回退）──────────────────────────────────
src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
check("群入口用了闸函数",
      "if _agent_legacy_entry_enabled(True) and (is_at_bot or has_xiao_a)" in src,
      src.count("_agent_legacy_entry_enabled(True)"))
check("私聊入口用了闸函数",
      "if text and not images and _agent_legacy_entry_enabled(False)" in src,
      src.count("_agent_legacy_entry_enabled(False)"))
check("群入口不再直接裸读 agent_group_enable 就动手",
      'if (is_at_bot or has_xiao_a) and text and _config.get("agent_group_enable", False):'
      not in src)
check("私聊入口不再裸读 agent_private_enable 就动手",
      'if text and not images and _config.get("agent_private_enable", False):' not in src)

# ── 4. 旁路关掉之后，deep_task 仍在（能力不丢）──────────────────────
from harness import tool_deep as td  # noqa: E402
from harness import tool_registry as tr  # noqa: E402

td.register_all()
check("deep_task 仍在注册表里（能力由工具承接）", tr.has("deep_task"))
check("deep_task 归 GROUP_DEEP（仍受 agent_as_tool 注入闸）",
      tr.GROUP_DEEP in tr.groups_of("deep_task"))

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
