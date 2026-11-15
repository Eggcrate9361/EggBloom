# -*- coding: utf-8 -*-
"""私聊记忆隔离（2026-11-02 第二轮）。

用户：「私聊还是做对不同人的记忆隔离吧」。

第一轮（§63）修了 `recall_by_date` 本身，但**调用方还在漏**：

- `harness/memory_api._retrieval` 私聊分支 `get_memory_prompt_block(query, light=True)`
  **不传 scope** → 里面"日期类问题"那层照旧扫整份沙漏（沙漏只写群聊、不分人）；
- `harness/memory_context.build_block` 里 `recall_by_date(message)` 同样不传 scope，
  而私聊主链正是走这个 builder。

沙漏是全会话共用一份、只写群聊的，所以"不带作用域"= 私聊能读到群里别人的话。

## 契约（本用例钉死）

- `memory_api._retrieval(..., private=True, uid=X)` → 调用时带 `("private", X)`；
- `memory_context.build_block(..., scope="private", scope_id=X)` → 日期召回带作用域；
- 私聊（scope=private）日期召回**返回空**（沙漏里没有私聊数据，宁缺勿串）。

运行：python tests/test_memory_scope_isolation.py
"""
import datetime
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


# ── 1. 源码约束：两条调用链都带作用域 ────────────────────────────
api = io.open(os.path.join(ROOT, "harness", "memory_api.py"), encoding="utf-8").read()
ctx = io.open(os.path.join(ROOT, "harness", "memory_context.py"), encoding="utf-8").read()
sg = io.open(os.path.join(ROOT, "sandglass_bridge.py"), encoding="utf-8").read()

check("★ memory_api 私聊检索带 scope",
      'scope="private", scope_id=str(uid or "")' in api)
check("★ memory_api 私聊检索两个源都开",
      'sources=("sand", "sm")' in api)
check("★ memory_context 日期召回带 scope",
      "recall_by_date(message, scope=(scope, scope_id))" in ctx)
check("sandglass 私聊直接返回空", 'if _kind == "private":' in sg and
      "沙漏里没有私聊数据" in sg)

# ── 2. 行为：私聊日期召回为空、群聊只取本群 ──────────────────────
import sandglass_bridge as sb  # noqa: E402

OLD = sb._vault_file
import tempfile  # noqa: E402

tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False)
# ★ 2026-11-03：日期不能写死（问的是"昨天"，跨零点必红）。按运行时算。
_Y = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
tmp.write(
    f"{_Y} 10:00:00 | user | [群:111] 群里A说的话\n"
    f"{_Y} 10:01:00 | user | [群:222] 群里B说的话\n"
    f"{_Y} 10:02:00 | user | 无标记老行\n"
)
tmp.close()
sb._vault_file = lambda: tmp.name
try:
    check("★ 私聊问日期 → 空", sb.recall_by_date("昨天我干了什么",
                                                 scope=("private", "1000000001")) == "")
    g = sb.recall_by_date("昨天群里说了什么", scope=("group", "111"))
    check("★ 群聊只取本群", "群里A说的话" in g and "群里B说的话" not in g, g[:80])
    check("群聊不取无标记老行", "无标记老行" not in g, g[:80])

    from harness import memory_api as ma
    blk = ma.recall("1000000001", scene="private", query="昨天我干了什么", private=True)
    check("★ memory_api 私聊不注入群聊记录",
          "群里A说的话" not in blk and "群里B说的话" not in blk, blk[:100])

    from harness import memory_context as mc
    b2 = mc.build_block("昨天我干了什么", scope="private", scope_id="1000000001",
                        sources=("sand",))
    check("★ build_block 私聊不注入群聊记录",
          "群里A说的话" not in b2 and "群里B说的话" not in b2, b2[:100])
finally:
    sb._vault_file = OLD
    try:
        os.unlink(tmp.name)
    except OSError:
        pass

# ── 3. 人物档案按人取（源码约束 + 行为）─────────────────────────
import people  # noqa: E402

src_people = io.open(os.path.join(ROOT, "people.py"), encoding="utf-8").read()
check("people.card 按 uid 取", "def card(uid" in src_people)
check("facts 带 scope 过滤", 'f.get("scope") == "private" and not private' in src_people)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
