# -*- coding: utf-8 -*-
"""表情包审核区（2026-11-02，用户："表情包直接进入审核区，不直接用，再给我一个多选功能"）。

## 背景

- 池子里混进了游戏截图、老土低质量图；
- 新收的图**不该直接能发**，要先过审；
- 审核要能**多选**（一次勾一批，通过/不喜欢/删除）。

## 契约

1. 采集写入 `status=pending`（`meme_require_approval` 默认开）；
2. `match_meme` 只从 `approved` 里挑；老图库（不在索引里）不受影响；
3. `set_status` / `set_status_many` / `list_by_status` / `approved_rels`；
4. `migrate_status()` 一次性把旧条目也送进审核区（用户要"不直接用"）；
5. 横屏闸：宽高比 > `meme_collect_landscape_ratio`（默认 1.35）→ 当游戏/横屏截图，不收；
6. 接口：`GET /api/memes/pending`、`POST /api/memes/review`
   （`{names:[...], action:"approve|reject|delete"}`），页面有多选 + 批量按钮。

运行：python tests/test_meme_review.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import meme_collect as mc  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


# ── 1. 状态机 ────────────────────────────────────────────────────
tmp = tempfile.mkdtemp()
old_dir, old_idx = mc.COLLECT_DIR, mc.INDEX_FILE
mc.COLLECT_DIR = tmp
mc.INDEX_FILE = os.path.join(tmp, "index.json")
try:
    idx = {
        "s1": {"path": "collected/a.jpg", "tags": ["呆滞"], "ts": 3},
        "s2": {"path": "collected/b.jpg", "tags": [], "ts": 2},
        "s3": {"path": "collected/c.jpg", "tags": [], "ts": 1},
    }
    io.open(mc.INDEX_FILE, "w", encoding="utf-8").write(json.dumps(idx, ensure_ascii=False))

    check("旧条目默认 approved（不改老行为）",
          mc.status_of("collected/a.jpg") == mc.STATUS_APPROVED, mc.status_of("collected/a.jpg"))

    check("★ 能标成 pending", mc.set_status("collected/a.jpg", mc.STATUS_PENDING) is True)
    check("标完了读得到", mc.status_of("collected/a.jpg") == mc.STATUS_PENDING)

    n = mc.set_status_many(["collected/a.jpg", "collected/b.jpg"], mc.STATUS_APPROVED)
    check("★ 批量改状态", n == 2, n)
    check("批量生效", mc.status_of("collected/b.jpg") == mc.STATUS_APPROVED)

    pend = mc.set_status_many(["collected/c.jpg"], mc.STATUS_PENDING)
    check("单张进审核区", pend == 1)
    lst = mc.list_by_status("pending")
    check("★ 待审列表只含 pending", [x["path"] for x in lst] == ["collected/c.jpg"],
          [x["path"] for x in lst])
    check("待审列表带元信息", lst[0]["tags"] == [] and lst[0]["status"] == "pending")
    check("approved_rels 不含 pending", "collected/c.jpg" not in mc.approved_rels())
    check("approved_rels 含 others", "collected/a.jpg" in mc.approved_rels())

    # ── 2. 一次性迁移 ───────────────────────────────────────
    idx2 = {"s9": {"path": "collected/d.jpg", "tags": [], "ts": 0}}
    io.open(mc.INDEX_FILE, "w", encoding="utf-8").write(json.dumps(idx2))
    migrated = mc.migrate_status()
    check("★ 迁移把没状态的也送进审核区", migrated == 1, migrated)
    check("迁移后是 pending", mc.status_of("collected/d.jpg") == mc.STATUS_PENDING)
    check("迁移只跑一次", mc.migrate_status() == 0)
finally:
    mc.COLLECT_DIR, mc.INDEX_FILE = old_dir, old_idx
    shutil.rmtree(tmp, ignore_errors=True)

# ── 3. 横屏截图闸 ───────────────────────────────────────────────
tmp2 = tempfile.mkdtemp()
try:
    from PIL import Image
    for name, w, h in (("game.jpg", 1920, 1080), ("meme.jpg", 500, 500),
                       ("tall.jpg", 400, 700)):
        p = os.path.join(tmp2, name)
        Image.new("RGB", (w, h), "white").save(p)
        r = mc._looks_like_meme(p)
        if name == "game.jpg":
            check("★ 16:9 游戏截图被拦", bool(r), r)
        else:
            check(f"{name} 放行", r == "", r)
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

# ── 4. 配置 / 接口 / 页面 ────────────────────────────────────────
import config as cfg  # noqa: E402

D = cfg.DEFAULT_CONFIG
check("★ 默认要审核", D.get("meme_require_approval") is True, D.get("meme_require_approval"))
check("横屏比例默认 1.35", float(D.get("meme_collect_landscape_ratio") or 0) == 1.35,
      D.get("meme_collect_landscape_ratio"))

web = io.open(os.path.join(ROOT, "webui", "webui_server.py"), encoding="utf-8").read()
check("接口 /api/memes/pending", "/api/memes/pending" in web)
check("接口 /api/memes/review", "/api/memes/review" in web)
check("review 支持批量 names", 'data.get("names")' in web)

page = io.open(os.path.join(ROOT, "webui", "webui_artistic_v4.html"), encoding="utf-8").read()
check("★ 页面有审核区", "审核区" in page)
check("★ 页面有多选", "data-rev" in page)
check("页面有批量按钮", "meRevApprove" in page and "meRevReject" in page)
check("页面调用 review 接口", "/memes/review" in page)

eng = io.open(os.path.join(ROOT, "memes", "memes", "meme_engine.py"), encoding="utf-8").read()
check("★ 匹配只挑审核通过的", "_approved_rels" in eng and "collected/" in eng)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
