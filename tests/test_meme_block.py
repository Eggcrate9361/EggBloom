# -*- coding: utf-8 -*-
"""表情包「不喜欢」+ 7 天新增（2026-11-02）。

## 需求

用户：「表情包有时候会存到我不喜欢的，表情包页改造，加入新增块放 7 天方便管理员调整」。

## 契约

1. `meme_collect.block(rel)` / `unblock(rel)` / `blocked_rels()`：
   黑名单落在 `collected/blocked.json`，**不动文件**（可恢复）；
2. 采集侧：sha 或感知哈希命中黑名单 → 不再收回来，并说明原因；
3. 匹配侧：`match_meme` 跳过黑名单（连模糊/兜底随机也不选它）；
4. 接口：`/api/memes` 每项带 `collected/collected_ts/blocked`；
   `/api/memes/recent?days=7` 只给新增；`/api/memes/block`、`/api/memes/unblock`；
5. 页面：顶部「最近 7 天新增」横排块 + 每张「不喜欢 / 恢复」；主网格也能拉黑。

运行：python tests/test_meme_block.py
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


# ── 1. 黑名单文件读写 ─────────────────────────────────────────────
tmpdir = tempfile.mkdtemp()
old_block = mc.BLOCK_FILE
old_dir = mc.COLLECT_DIR
mc.COLLECT_DIR = tmpdir
mc.BLOCK_FILE = os.path.join(tmpdir, "blocked.json")
try:
    check("初始为空", mc.blocked_rels() == set(), mc.blocked_rels())
    check("拉黑一张", mc.block("collected/aaa.jpg") is True)
    check("名单里有", "collected/aaa.jpg" in mc.blocked_rels(), mc.blocked_rels())
    check("is_blocked 按 rel 命中", mc.is_blocked(rel="collected/aaa.jpg") is True)
    check("别的图不受影响", mc.is_blocked(rel="collected/bbb.jpg") is False)
    check("恢复", mc.unblock("collected/aaa.jpg") is True)
    check("恢复后为空", mc.blocked_rels() == set(), mc.blocked_rels())
    # 感知哈希命中
    mc.block("collected/ccc.jpg")
    raw = json.loads(io.open(mc.BLOCK_FILE, encoding="utf-8").read())
    for k, v in raw.items():
        if isinstance(v, dict):
            v["phash"] = 123456789
    io.open(mc.BLOCK_FILE, "w", encoding="utf-8").write(json.dumps(raw, ensure_ascii=False))
    check("★ 感知哈希相近也算拉黑", mc.is_blocked(phash=123456789) is True)
    check("哈希差很远不算", mc.is_blocked(phash=999999999) is False)
finally:
    mc.BLOCK_FILE = old_block
    mc.COLLECT_DIR = old_dir
    shutil.rmtree(tmpdir, ignore_errors=True)

# ── 2. 采集侧会跳过黑名单 ─────────────────────────────────────────
src = io.open(os.path.join(ROOT, "meme_collect.py"), encoding="utf-8").read()
check("★ 采集里检查黑名单", "is_blocked(sha=sha" in src)
check("跳过原因写清是「不喜欢」", "不喜欢" in src)

# ── 3. 匹配侧会跳过黑名单 ─────────────────────────────────────────
eng = io.open(os.path.join(ROOT, "memes", "memes", "meme_engine.py"),
              encoding="utf-8").read()
check("★ match_meme 有 _blocked_rels", "_blocked_rels" in eng)
check("★ 候选统一走 _pick/_pick_path",
      eng.count("_pick_path(") + eng.count("_pick(") >= 4,
      eng.count("_pick_path("))
check("不再有裸 random.choice 兜底",
      "random.choice(all_files)" not in eng and "random.choice(filenames)" not in eng)

# ── 4. 接口与页面 ─────────────────────────────────────────────────
web = io.open(os.path.join(ROOT, "webui", "webui_server.py"), encoding="utf-8").read()
for ep in ("/api/memes/block", "/api/memes/unblock", "/api/memes/recent"):
    check("接口存在：" + ep, ep in web)
check("列表带 blocked 字段", "'blocked'] = _rel in _blk_rel" in web or "blocked" in web)

page = io.open(os.path.join(ROOT, "webui", "webui_artistic_v4.html"), encoding="utf-8").read()
check("★ 页面有「最近 7 天新增」块", "最近 7 天新增" in page)
check("★ 页面有「不喜欢」按钮", "不喜欢" in page)
check("页面拉 /memes/recent", "/memes/recent?days=7" in page)
check("页面能恢复", "unblock" in page)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
