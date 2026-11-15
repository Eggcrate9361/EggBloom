# -*- coding: utf-8 -*-
"""手册一致性（方案 v2 §9 风险表：手册与实际漂移）。

手册是 super prompt，写错一句模型就会照着错的那句去说。这里钉死三件事：
  1. 注册表里的每个原语，手册必须写到（漏写 = 模型不知道有这个动作）
  2. 手册里提到的每个原语，注册表必须有（多写 = 模型会去调不存在的工具）
  3. 手册第四章的路径必须能在磁盘上找到（运行态目录单独登记）

运行：python tests/test_manual_consistency.py
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness.qq_primitives import PrimitiveSet  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


MANUAL = os.path.join(ROOT, "docs", "qq_agent_manual.md")
text = io.open(MANUAL, encoding="utf-8").read()
specs = {s["name"] for s in PrimitiveSet().specs()}

# ── 1. 双向覆盖 ──────────────────────────────────────────────────
missing_in_manual = sorted(n for n in specs if f"`{n}`" not in text)
check("注册表里的原语手册都写了", not missing_in_manual, missing_in_manual)

# 手册里表格第一列的原语名（`| \`onebot\` |`）
mentioned = set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", text, re.M))
check("手册提到的原语注册表都有", mentioned <= specs, sorted(mentioned - specs))
check("原语数量对得上（9 个）", len(specs) == 9, len(specs))

# 每个原语都写了签名（回答"怎么调"而不是"能不能"）
for n in specs:
    check(f"{n} 写了签名", re.search(rf"\|\s*`{n}`\s*\|\s*`\(", text) is not None)

# ── 2. 六章齐全 ──────────────────────────────────────────────────
for ch in ("你在哪", "你手上有什么", "资源地图", "菜谱", "铁律", "失败长什么样"):
    check(f"第{ch}章在", f"· {ch}" in text)

# 菜谱至少 10 条
recipes = re.findall(r"^\*\*\d+\. ", text, re.M)
check("菜谱 ≥ 10 条", len(recipes) >= 10, len(recipes))

# ── 3. 路径真实存在 ──────────────────────────────────────────────
#: 手册承诺「磁盘上有」的路径（相对仓库根）。仓库里应有这些（数据/目录）。
MUST_EXIST = [
    "plugins/personality.txt",
    "data/people.json",
    "chat_logs",
    "memes",
    "docs/qq_agent_manual.md",
]
for rel in MUST_EXIST:
    check(f"路径存在：{rel}", os.path.exists(os.path.join(ROOT, rel)))

#: 运行态目录：只存在于装好的 app 里，仓库里没有是正常的（别这里报错，
#: 但也别让手册写死仓库里不存在、运行态也没有的东西）。
RUNTIME_ONLY = {
    ".neurobase/sandglass.txt": "沙漏记忆，运行时生成",
    "data/persona_cards/": "人格卡，运行时生成",
    "data/avatars/": "头像缓存，首次看图时创建",
}
for rel, why in RUNTIME_ONLY.items():
    check(f"运行态路径已在手册登记：{rel}", rel.rstrip("/") in text, why)

# 占位符路径必须能用通配符命中至少一个真实文件。
# ★ 这两类是**运行态数据**（gitignore 掉的）：仓库里没有是正常的，
#   所以先看仓库、再看装好的 app（EGGBLOOM_APP_ROOT），两边都没有就按"未安装"放过。
APP_ROOT = os.environ.get("EGGBLOOM_APP_ROOT") or ""

for pattern, hint in (
    ("chat_logs/QQ_*/chat_*.md", "私聊记录"),
    ("memes/*/*.gif", "表情包文件"),
    ("plugins/*/data", "插件数据目录"),
):
    import glob as _g
    roots = [ROOT] + ([APP_ROOT] if APP_ROOT and os.path.isdir(APP_ROOT) else [])
    hit = sum(len(_g.glob(os.path.join(r, pattern))) for r in roots)
    if hit == 0 and pattern != "plugins/*/data":
        check(f"占位路径（运行态，未安装则跳过）：{hint}", True, "仓库与 app 都没有")
    else:
        check(f"占位路径能命中真实文件：{hint}", hit >= 1, f"{pattern} -> {hit}")

# ── 4. 铁律与话术 ───────────────────────────────────────────────
check("五条铁律都在", len(re.findall(r"^\d\. \*\*", text, re.M)) >= 5)
for phrase in ("拿不到说拿不到", "不解释机制", "最多 3 步", "不改口"):
    check(f"铁律含「{phrase}」", phrase in text)
for phrase in ("图我这儿没取到", "这张我没看出来", "我这边没记录到"):
    check(f"失败话术含「{phrase}」", phrase in text)
check("写了『不许说没有』", "不许说" in text and "没有" in text)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
