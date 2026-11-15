# -*- coding: utf-8 -*-
"""「可干活」一个开关（1.6.0 刀 4）。

## 为什么

以前是 `agent_group_enable` / `agent_private_enable` 两个开关，语义还容易被
理解成"换人格"。刀 3b 把入口旁路关掉之后，它们实际只剩一个意思：
**能不能多两只手（deep_task）**。所以收成一个 `agent_can_work`。

## 钉死什么

1. `DEFAULT_CONFIG.agent_can_work` 存在且默认 False；
2. **老配置兼容**：用户文件里没有 `agent_can_work` 时，按旧键推导
   （任一为 True 即 True）—— 升级后行为不变；
3. **一旦文件里写了新键，旧键不再参与**（否则关不掉）；
4. 工具注入：`agent_as_tool` ∧ `agent_can_work` 才给 `deep_task`；
   私聊还要求过白名单，群聊不额外要求；
5. 配置页只暴露一个开关（旧的不要再出现在 page / collect 里）。

运行：python tests/test_agent_can_work.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core as bc  # noqa: E402
import config as cfgmod  # noqa: E402
from harness import tool_deep as td  # noqa: E402
from harness import tool_registry as tr  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


td.register_all()

# ── 1. 默认值与登记 ─────────────────────────────────────────────────
check("DEFAULT_CONFIG.agent_can_work 存在且为 False",
      cfgmod.DEFAULT_CONFIG.get("agent_can_work") is False,
      cfgmod.DEFAULT_CONFIG.get("agent_can_work"))
_ex = open(os.path.join(ROOT, "bot_config.json.example"), encoding="utf-8").read()
check("example 里登记了 agent_can_work", '"agent_can_work"' in _ex)

# ── 2. 老配置迁移（文件里没有新键 → 按旧键推导）──────────────────────
_tmp = tempfile.mkdtemp(prefix="egg_canwork_")
_old_app, _old_sig, _old_cache = cfgmod.APP_CONFIG_FILE, cfgmod._config_cache_sig, cfgmod._config_cache


def load_with(payload):
    """把一份配置写到临时文件、让 load_config 读它。"""
    p = os.path.join(_tmp, "cfg.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    cfgmod.APP_CONFIG_FILE = p
    cfgmod._config_cache = None
    cfgmod._config_cache_sig = None
    try:
        return cfgmod.load_config()
    finally:
        cfgmod.APP_CONFIG_FILE, cfgmod._config_cache, cfgmod._config_cache_sig = (
            _old_app, _old_cache, _old_sig)


c1 = load_with({"agent_group_enable": True, "agent_private_enable": False})
check("★ 老配置（群开、无私聊）→ 迁移为可干活", c1.get("agent_can_work") is True, c1.get("agent_can_work"))
c2 = load_with({"agent_group_enable": False, "agent_private_enable": True})
check("★ 老配置（只私聊开）→ 迁移为可干活", c2.get("agent_can_work") is True, c2.get("agent_can_work"))
c3 = load_with({"agent_group_enable": False, "agent_private_enable": False})
check("老配置（都关）→ 仍然不可干活", c3.get("agent_can_work") is False, c3.get("agent_can_work"))
c4 = load_with({})
check("空配置 → 默认不可干活", c4.get("agent_can_work") is False, c4.get("agent_can_work"))

# ── 3. 写了新键就以新键为准（旧键不再生效）──────────────────────────
c5 = load_with({"agent_can_work": False, "agent_group_enable": True})
check("★ 新键 False + 旧键 True → 以新键为准（关得掉）",
      c5.get("agent_can_work") is False, c5.get("agent_can_work"))
c6 = load_with({"agent_can_work": True, "agent_group_enable": False})
check("★ 新键 True + 旧键 False → 以新键为准（开得了）",
      c6.get("agent_can_work") is True, c6.get("agent_can_work"))

# ── 4. 工具注入 ──────────────────────────────────────────────────────
def names(is_group=False, privileged=True, **cfg):
    backup = dict(bc._config)
    try:
        bc._config.update(cfg)
        return bc._tools_subset_for_scene(is_group, privileged=privileged)
    finally:
        bc._config.clear()
        bc._config.update(backup)


check("可干活关：私聊没有 deep_task",
      "deep_task" not in names(agent_as_tool=True, agent_can_work=False), None)
check("可干活关：群聊没有 deep_task",
      "deep_task" not in names(is_group=True, agent_as_tool=True, agent_can_work=False), None)
check("可干活开：私聊（白名单内）有 deep_task",
      "deep_task" in names(agent_as_tool=True, agent_can_work=True), None)
check("可干活开：私聊未过白名单没有",
      "deep_task" not in names(agent_as_tool=True, agent_can_work=True, privileged=False), None)
check("★ 可干活开：群聊有 deep_task（不再单看 agent_group_enable）",
      "deep_task" in names(is_group=True, agent_as_tool=True, agent_can_work=True,
                           agent_group_enable=False), None)
check("agent_as_tool 关时，可干活开也不给",
      "deep_task" not in names(agent_as_tool=False, agent_can_work=True), None)

# ── 5. 配置页只暴露一个开关 ─────────────────────────────────────────
page = open(os.path.join(ROOT, "webui", "webui_artistic_v3.html"), encoding="utf-8").read()
check("★ 页面有新开关 sAgentCanWork", 'id="sAgentCanWork"' in page)
check("★ 页面不再有 sAgentGroupEnable", 'id="sAgentGroupEnable"' not in page)
check("★ 页面不再有 sAgentPrivateEnable", 'id="sAgentPrivateEnable"' not in page)
check("保存只提交新键", "agent_can_work: chk('sAgentCanWork')" in page)
check("保存不再提交旧键", "agent_group_enable: chk(" not in page
      and "agent_private_enable: chk(" not in page)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
