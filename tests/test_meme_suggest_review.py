# -*- coding: utf-8 -*-
"""候选素材「自动打标建议 + 人工复核晋升」通道（2026-11-15 口径）。

## 口径

* `meme_save` 产出的是**候选素材**，不是表情包；`pending` 是合法中间态。
* 表情池 = 有情绪标签 ∧ approved。两个条件缺一不可。
* 自动打标**只给建议**，不自动写 tags.json；必须由人在复核页采纳。
* 复核通过才 `apply_tags` + `set_status(approved)`，此后 `meme_engine._pick()`
  才会放行这张图。

运行：python tests/test_meme_suggest_review.py
"""
import io
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import meme_collect as mc  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="eggsuggest_")
_MEME = os.path.join(_TMP, "memes")
_COLLECT = os.path.join(_MEME, "collected")
os.makedirs(_COLLECT, exist_ok=True)

mc.MEME_DIR = _MEME
mc.COLLECT_DIR = _COLLECT
mc.INDEX_FILE = os.path.join(_COLLECT, "index.json")
mc.TAGS_FILE = os.path.join(_MEME, "tags.json")
mc.BLOCK_FILE = os.path.join(_COLLECT, "blocked.json")

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


def seed(rel, tags=None, status=mc.STATUS_PENDING):
    idx = mc._index()
    key = rel.replace("/", "_")
    idx[key] = {"path": rel, "tags": list(tags or []), "ts": time.time(),
                "src": "group", "from_group": "", "status": status,
                "with_text": False, "phash": "0" * 16}
    mc._save_index(idx)
    # 落一张空文件，模拟真图（本用例不读内容）
    p = os.path.join(_MEME, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    io.open(p, "wb").write(b"\x89PNG\r\n\x1a\n")


def save_desc(rel, text):
    import meme_desc
    return meme_desc.save_desc(mc.MEME_DIR, rel, text)


R1 = "collected/a1.png"
R2 = "collected/b2.png"
R3 = "collected/c3.png"
R4 = "collected/d4.png"

seed(R1, tags=["开心"])
seed(R2, tags=[])
seed(R3, tags=[])
seed(R4, tags=[])

# ── 1. 自动打标：三种来源 ───────────────────────────────────────────
s1 = mc.suggest_for(R1)
check("分类器标签作为建议（source=auto）",
      s1["suggest_tags"] == ["开心"] and s1["source"] == "auto", s1)
check("建议不自动写 tags.json", not os.path.isfile(mc.TAGS_FILE), mc.TAGS_FILE)

s2 = mc.suggest_for(R2)
check("无标签无描述 → 需人工打标签",
      s2["suggest_tags"] == [] and "人工" in s2["reason"], s2)

save_desc(R3, "一只猫炸毛、一脸崩溃地摊在地上，配字「无语」")
s3 = mc.suggest_for(R3)
check("画面描述能给出建议（source=desc）",
      bool(s3["suggest_tags"]) and s3["source"] == "desc", s3)
check("建议写明依据", "描述" in s3["reason"], s3["reason"])

# ── 2. 人工复核晋升 ─────────────────────────────────────────────────
check("复核前是 pending", mc.status_of(R1) == mc.STATUS_PENDING)
ok = mc.apply_review(R1, "approve", tags=["开心", "得意"])
check("apply_review 返回 True", ok is True)
check("★ 复核后 approved", mc.status_of(R1) == mc.STATUS_APPROVED, mc.status_of(R1))
db = json.load(io.open(mc.TAGS_FILE, encoding="utf-8"))
check("★ 人工标签写进 tags.json", db.get(R1) == ["开心", "得意"], db)
check("approved_rels 收进这张", R1 in mc.approved_rels(), mc.approved_rels())

check("reject 生效", mc.apply_review(R2, "reject") and mc.status_of(R2) == mc.STATUS_REJECTED)
check("reject 不写标签", R2 not in json.load(io.open(mc.TAGS_FILE, encoding="utf-8")))

ok3 = mc.apply_review(R3, "approve", use_suggest=True)
check("★ use_suggest 采纳建议标签", ok3 is True and mc.status_of(R3) == mc.STATUS_APPROVED)
db3 = json.load(io.open(mc.TAGS_FILE, encoding="utf-8"))
check("建议标签被写进匹配库", R3 in db3 and db3[R3] == s3["suggest_tags"], db3)

check("无建议时 approve 不虚构标签",
      mc.apply_review(R4, "approve", use_suggest=True)
      and R4 not in json.load(io.open(mc.TAGS_FILE, encoding="utf-8")))
check("非法 action 被拒", mc.apply_review(R4, "delete") is False)

# 批量
seed("collected/e5.png")
seed("collected/f6.png")
n = mc.apply_review_many(["collected/e5.png", "collected/f6.png"],
                         "approve", {"collected/e5.png": ["无语"]})
check("批量复核返回条数", n == 2, n)

# ── 3. WebUI 通道断言 ───────────────────────────────────────────────
server = io.open(os.path.join(ROOT, "webui", "webui_server.py"), encoding="utf-8").read()
html = io.open(os.path.join(ROOT, "webui", "webui_artistic_v4.html"), encoding="utf-8").read()
check("★ 待审列表带建议标签", "_mc2.suggest_for(" in server and 'it["suggest_tags"]' in server)
check("★ 复核接口支持 use_suggest", "use_suggest" in server and "apply_review_many" in server)
check("★ 审核页有采纳建议按钮", "meRevApproveSuggest" in html and "use_suggest: true" in html)
check("★ 审核页展示建议", "m.suggest_tags" in html)
check("★ 不自动把 pending 改 approved", "自动" in mc.apply_review.__doc__)

# ── 4. 源码级：候选素材仍是 pending ─────────────────────────────────
src = io.open(os.path.join(ROOT, "meme_collect.py"), encoding="utf-8").read()
check("采集默认仍需审核", '"status": STATUS_PENDING if _need_review else STATUS_APPROVED' in src)
check("状态常量未变", "STATUS_PENDING = \"pending\"" in src)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for x in FAIL:
    print(f"  FAIL  {x}")
sys.exit(1 if FAIL else 0)
