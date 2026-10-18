# -*- coding: utf-8 -*-
"""任务 5.1 单测：策略快照追加 —— static_prompt 前缀稳定（保住 KV 前缀缓存）。

验收对应手册 5.1：
  - 模式/权限/配置切换时，static_prompt 的**静态段**要保持逐字节稳定；
  - 旧实现把 _CONFIG_REV 塞进缓存键 → 任何配置热重载都全段重算并作废前缀缓存；
  - 现实现只对「static_prompt 真正依赖」的最小内容取指纹。

本测试不依赖真实 LLM，只验证「指纹/缓存键」的不变量。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)


def _reset_cache():
    bot_core._STATIC_PROMPT_CACHE.clear()


# ── 1. 指纹函数存在且稳定 ───────────────────────────────────────────
check("存在 _static_prompt_fingerprint", hasattr(bot_core, "_static_prompt_fingerprint"))
fp_group = bot_core._static_prompt_fingerprint(True)
fp_priv = bot_core._static_prompt_fingerprint(False)
check("指纹是字符串", isinstance(fp_group, str) and len(fp_group) == 16)
check("同参数指纹可复现", fp_group == bot_core._static_prompt_fingerprint(True))
check("群聊/私聊指纹不同", fp_group != fp_priv)

# ── 2. ★核心：_CONFIG_REV 不再影响 static_prompt 缓存键 ─────────────
_reset_cache()
sp_before = bot_core._build_static_prompt(True)
# 模拟一次配置热重载：只 bump 计数（旧实现会让缓存键失效）
bot_core._CONFIG_REV += 1
sp_after = bot_core._build_static_prompt(True)
check("★CONFIG_REV 自增后 static_prompt 逐字节相同", sp_before == sp_after)

# 旧键形态（含 config_rev）应无法命中新缓存 —— 证明键已换掉
old_style_key = (True, bot_core._CONFIG_REV, bot_core._personality_cache.get("mtime", 0))
check("★缓存键已不含裸 config_rev", old_style_key not in bot_core._STATIC_PROMPT_CACHE)

# ── 3. 缓存命中：同一 (is_group, fp, mtime) 直接复用对象 ────────────
_reset_cache()
a = bot_core._build_static_prompt(True)
b = bot_core._build_static_prompt(True)
check("同参数两次调用内容相同", a == b)
check("同参数两次调用命中缓存（长度=1）", len(bot_core._STATIC_PROMPT_CACHE) == 1)

# ── 4. 群聊/私聊各占一个缓存项 ──────────────────────────────────────
_reset_cache()
bot_core._build_static_prompt(True)
bot_core._build_static_prompt(False)
check("群/私聊各占一项（长度=2）", len(bot_core._STATIC_PROMPT_CACHE) == 2)

# ── 5. static_prompt 正文确实随 is_group 变化（指纹敏感度验证）────
check("群聊 prompt 含【群聊规则】", "【群聊规则】" in bot_core._build_static_prompt(True))
check("私聊 prompt 不含【群聊规则】", "【群聊规则】" not in bot_core._build_static_prompt(False))

# ── 6. ★无关配置变更不改变指纹（BOT_GROUP_NICK/BOT_QQ 未动时）────
fp1 = bot_core._static_prompt_fingerprint(True)
# 模拟改了无关字段（如 onebot_url / meme_cooldown）——这些不在指纹依赖里
_old_temp = getattr(bot_core, "AI_TEMPERATURE", None)
if _old_temp is not None:
    bot_core.AI_TEMPERATURE = 1.23
fp2 = bot_core._static_prompt_fingerprint(True)
check("★改 AI_TEMPERATURE 指纹不变", fp1 == fp2)
if _old_temp is not None:
    bot_core.AI_TEMPERATURE = _old_temp

# ── 7. ★相关配置变更（BOT_QQ）确实改变指纹 ──────────────────────────
_old_qq = bot_core.BOT_QQ
try:
    bot_core.BOT_QQ = "999999999"
    fp3 = bot_core._static_prompt_fingerprint(True)
    check("★改 BOT_QQ 指纹改变", fp3 != fp1)
finally:
    bot_core.BOT_QQ = _old_qq

# ── 8. 缓存上限保护仍在（>8 清空）───────────────────────────────────
_reset_cache()
for i in range(9):
    bot_core._STATIC_PROMPT_CACHE[(True, f"fake{i}", 0)] = "x"
# 直接触发一次构建，观察清空逻辑由函数内部执行
bot_core._build_static_prompt(True)
check("缓存有上限保护（长度<=9）", len(bot_core._STATIC_PROMPT_CACHE) <= 9)

# ── 输出 ────────────────────────────────────────────────────────────
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in PASS:
    print(f"  PASS  {n}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
