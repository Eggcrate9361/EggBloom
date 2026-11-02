# -*- coding: utf-8 -*-
"""表情包：普通图片不进库 + 删除要真删（2026-11-02）。

用户两条：
1. 「表情包会把普通图片放进去」——群里随手发的手机照片/长截图也进了库；
2. 「表情包页删不掉」——页面点了删除，返回 success，但图还在。

## 事故（第 2 条）

`/api/memes/delete` 用 `Path(raw_name).name` 取**basename**：
`collected/f8404cfc9caa.jpg` → `f8404cfc9caa.jpg` → 去 `memes/memes/` 根下找，
找不到 → 直接回 `{"success": true}`。于是**静默假成功**。

## 契约

- 删除按安全**相对路径**删（`_safe_meme_rel` + within 校验），并同步从
  `collected/index.json`、`collected/blocked.json` 里摘掉；
- 采集侧两道闸：
  · `meme_collect_require_pure_image`：消息里除图之外 >12 字 → 不收；
  · `meme_collect_max_dim`：最长边 > 1600（或长宽比 >3/<1÷3）→ 像照片/长截图，不收。

运行：python tests/test_meme_cleanup.py
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


# ── 1. 采集闸：普通图片不收 ───────────────────────────────────────
tmpdir = tempfile.mkdtemp()
OLD_COLLECT, OLD_INDEX = mc.COLLECT_DIR, mc.INDEX_FILE
mc.COLLECT_DIR = tmpdir
mc.INDEX_FILE = os.path.join(tmpdir, "index.json")
try:
    from PIL import Image

    def mk(name, w, h):
        p = os.path.join(tmpdir, name)
        Image.new("RGB", (w, h), "white").save(p)
        return p

    photo = mk("photo.jpg", 3000, 4000)
    check("★ 3000×4000 照片被拦", bool(mc._looks_like_meme(photo)),
          mc._looks_like_meme(photo))
    ok = mk("meme.jpg", 400, 400)
    check("★ 400×400 表情包放行", mc._looks_like_meme(ok) == "",
          mc._looks_like_meme(ok))
    longpic = mk("long.jpg", 4000, 800)
    check("长截图被拦", bool(mc._looks_like_meme(longpic)),
          mc._looks_like_meme(longpic))

    r1 = mc.maybe_collect(photo, pure_image=True, source="test")
    check("★ 采集：照片被跳过", r1 == "", r1)
    # 2026-11-02（用户纠正）：**不能因为消息字多就拒收**
    r2 = mc.maybe_collect(ok, pure_image=False, source="test")
    check("★ 采集：带文字的图照样收", r2 != "", r2)
    check("★ 带文字只作审核提示（with_text）",
          mc.status_of(r2) == mc.STATUS_PENDING and
          any(bool(v.get("with_text")) for v in mc._index().values()
              if isinstance(v, dict) and v.get("path") == r2), mc._index())
finally:
    mc.COLLECT_DIR, mc.INDEX_FILE = OLD_COLLECT, OLD_INDEX
    shutil.rmtree(tmpdir, ignore_errors=True)

# ── 2. 删除接口：相对路径 + 清索引（源码约束 + 逻辑模拟）────────
web = io.open(os.path.join(ROOT, "webui", "webui_server.py"), encoding="utf-8").read()
check("★ 删除用 _safe_meme_rel", "safe = _safe_meme_rel(data.get('name', ''))" in web)
check("★ 删除会清 index.json", "idx = {k: v for k, v in idx.items()" in web)
check("★ 删除会清 blocked.json", "blk = {k: v for k, v in blk.items()" in web)
check("删除返回 deleted 标记", "'deleted': deleted" in web)
_del = web[web.find('/api/memes/delete'):]
_del = _del[:_del.find('/api/memes/block')] if '/api/memes/block' in _del else _del
check("★ 删除不再用 basename（upload 用它是对的，别混为一谈）",
      "name = Path(raw_name).name.strip()" not in _del)

# 逻辑模拟：造一个 collected/xxx.jpg，按新规则删
d = tempfile.mkdtemp()
try:
    base = os.path.join(d, "collected")
    os.makedirs(base)
    f = os.path.join(base, "x.jpg")
    io.open(f, "wb").write(b"img")
    idx = os.path.join(base, "index.json")
    io.open(idx, "w", encoding="utf-8").write(json.dumps(
        {"sha1": {"path": "collected/x.jpg", "ts": 1}}, ensure_ascii=False))
    # 与后端同款：按相对路径删 + 清索引
    rel = "collected/x.jpg"
    p = os.path.join(d, rel)
    deleted = os.path.exists(p)
    if deleted:
        os.unlink(p)
    data = json.loads(io.open(idx, encoding="utf-8").read())
    data = {k: v for k, v in data.items()
            if not (isinstance(v, dict) and str(v.get("path")) == rel)}
    io.open(idx, "w", encoding="utf-8").write(json.dumps(data))
    check("★ 相对路径能删到 collected/ 下的图", (not os.path.exists(p)) and deleted)
    check("★ 索引同步清掉", data == {}, data)
finally:
    shutil.rmtree(d, ignore_errors=True)

# ── 3. 配置项 ────────────────────────────────────────────────────
import config as cfg  # noqa: E402

D = cfg.DEFAULT_CONFIG
check("★ 默认不按文字多少拒收", D.get("meme_collect_require_pure_image") is False,
      D.get("meme_collect_require_pure_image"))
check("默认尺寸上限 1600", int(D.get("meme_collect_max_dim") or 0) == 1600,
      D.get("meme_collect_max_dim"))

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
