# -*- coding: utf-8 -*-
"""发布前一把过（2026-11-15）：把「对外可见的四个面」一次扫完。

为什么是这四个面
----------------
泄漏从来不是「某个文件写错了」，而是**某个面没人看**。这个项目前后漏过三次，
三次都发生在「没有闸的那个面」：

    工作树    ✅ 有闸              漏过：无
    历史      ❌ 没闸（11-15 前）   漏过：ssh 地址在 23 个提交里
    发行包    ❌ 没闸（11-15 前）   漏过：同一个地址在三档 zip 里
    Release   ❌ 没闸              漏过：无（但只是因为没人写进去，不是因为防住了）

所以这个脚本按面分组，每面一条判据，最后给一张表。少一面就报「没验」，不报绿 ——
「没扫」和「干净」必须能区分开，这是 11-13 那次假绿的教训。

四个面
======
1. **工作树**：`check_secrets.scan()`，已跟踪 + 未跟踪（跳过 ignored）
2. **历史**：`check_history.scan_refs()`，本地所有非归档 ref 的 blob + 提交信息 + tag 附注
3. **发行包**：`check_package.scan_zip()`，dist 下的 zip（`--strict` 时警告也算失败）
4. **Release**：正文 / 标题 / 附件名 / 仓库元信息（要 token，`--github` 开）

用法
====
    python scripts/prepublish.py                     # 树 + 历史
    python scripts/prepublish.py --dist "<dist-dir>"
    python scripts/prepublish.py --github --tag v1.6.0
    python scripts/prepublish.py --all --strict      # 发布前该跑的就是这条

退出码：0 全绿 / 1 有面不干净 / 2 有面没验成（依赖缺失、包不存在、拿不到 token）。
**退出码 2 不能当成通过。**
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import check_history as ch  # noqa: E402
import check_package as cp  # noqa: E402
import check_secrets as cs  # noqa: E402

REPO = "Eggcrate9361/EggBloom"


# ── 面 1：工作树 ────────────────────────────────────────────────────────────
def face_tree():
    paths = [p for p in cs._files(False) if not p.lower().endswith(cs.BIN_EXTS)]
    if not paths:
        return "SKIP", "没扫到文件（不是 git 仓库？）", []
    hits = cs.scan(paths, False)
    detail = ["%s:%s [%s] %s" % (h[0], h[1], h[2], h[3]) for h in hits[:20]]
    return ("FAIL" if hits else "OK"), "检查 %d 个文件，命中 %d 处" % (len(paths), len(hits)), detail


# ── 面 2：历史 ──────────────────────────────────────────────────────────────
def face_history(include_archive=False, since=""):
    refs, skipped = ch.list_refs(include_archive=include_archive)
    if not refs:
        return "SKIP", "没有可扫的 ref", []
    hits, st = ch.scan_refs(refs, since=since or None)
    detail = ["%s %s:%s [%s] %s" % (h[0], h[1], h[2], h[3], h[4]) for h in hits[:20]]
    note = "ref %d 个 / blob %d（文本 %d）/ 提交信息 %d 条，命中 %d 处" % (
        len(refs), st["blobs"], st["scanned"], st["commits"], len(hits))
    if skipped:
        note += "；跳过归档 ref %d 个" % len(skipped)
    return ("FAIL" if hits else "OK"), note, detail


# ── 面 3：发行包 ────────────────────────────────────────────────────────────
def face_packages(dist, strict=False):
    if not dist or not os.path.isdir(dist):
        return "SKIP", "没给 --dist（或目录不存在）", []
    zips = [os.path.join(dist, f) for f in sorted(os.listdir(dist))
            if f.lower().endswith(".zip")]
    if not zips:
        return "SKIP", "%s 下没有 zip" % dist, []
    detail, bad, warns = [], 0, 0
    for p in zips:
        rep = cp.scan_zip(p, strict=strict)
        bad += 0 if rep["ok"] else 1
        warns += len(rep["warns"])
        if not rep["ok"] or rep["findings"] or rep["warns"]:
            detail.append(cp.fmt(rep, limit=5))
    head = "%d 个包，%d 个不合格，警告 %d 处" % (len(zips), bad, warns)
    return ("FAIL" if bad else "OK"), head, detail


# ── 面 4：Release 元数据 ────────────────────────────────────────────────────
def _token():
    p = subprocess.run(["git", "credential", "fill"],
                       input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True, timeout=30)
    for line in (p.stdout or "").splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return ""


def _api(url, tok, timeout=60):
    env = dict(os.environ)
    for k in list(env):
        if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            env.pop(k)
    r = subprocess.run(["curl", "-sS", "--ssl-no-revoke", "--max-time", str(timeout),
                        "-H", "Authorization: Bearer " + tok,
                        "-H", "Accept: application/vnd.github+json", url],
                       capture_output=True, env=env, timeout=timeout + 30)
    try:
        return json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except Exception:
        return {}


def face_github(tag):
    tok = _token()
    if not tok:
        return "SKIP", "拿不到 GitHub 凭据（git credential 里没有）", []
    repo = _api("https://api.github.com/repos/%s" % REPO, tok)
    if not repo.get("full_name"):
        return "SKIP", "取不到仓库信息（凭据失效？）", []

    hits, detail = [], []

    def scan_meta(label, text, name_rule=False):
        """正文用内容判据；**文件名**额外加「6 位以上数字」—— 正文里有日期和大数字，
        拿文件名那套去筛正文只会产出噪声（第一版就这么误报了一次）。"""
        if not text:
            return
        for p, ln, name, val in cs.scan_text(label, text):
            hits.append((label, ln, name, val))
        if name_rule and cp.NAME_NUM.search(text):
            hits.append((label, 0, "文件名含 6 位以上数字", ""))

    scan_meta("(repo description)", repo.get("description") or "")
    scan_meta("(repo homepage)", repo.get("homepage") or "")

    rel = _api("https://api.github.com/repos/%s/releases/tags/%s" % (REPO, tag), tok)
    if rel.get("tag_name"):
        scan_meta("(release title)", rel.get("name") or "")
        scan_meta("(release body)", rel.get("body") or "")
        assets = _api("https://api.github.com/repos/%s/releases/%s/assets?per_page=100"
                      % (REPO, rel["id"]), tok)
        if not isinstance(assets, list):
            assets = []
        for item in assets:
            if isinstance(item, dict):
                scan_meta("(asset name)", item.get("name") or "", name_rule=True)
        detail.append("Release %s：%d 个附件，正文 %d 字"
                      % (tag, len(assets), len(rel.get("body") or "")))
    else:
        detail.append("Release %s 不存在（发布前正常）" % tag)

    detail += ["%s:%s [%s] %s" % (h[0], h[1], h[2], h[3]) for h in hits[:20]]
    return ("FAIL" if hits else "OK"), (
        "仓库描述/主页 + Release %s 正文与附件名，命中 %d 处" % (tag, len(hits))), detail


def main(argv=None):
    ap = argparse.ArgumentParser(description="发布前一把过")
    ap.add_argument("--dist", default="", help="发行包目录")
    ap.add_argument("--tag", default="v1.6.0", help="要验的 Release tag")
    ap.add_argument("--github", action="store_true", help="验 Release 正文与仓库元信息")
    ap.add_argument("--strict", action="store_true", help="包的警告也算失败")
    ap.add_argument("--include-archive", action="store_true", help="历史连归档 ref 一起扫")
    ap.add_argument("--since", default="")
    ap.add_argument("--all", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    if a.all:
        a.github = True

    rows = []
    print("=" * 70)
    print("发布前检查：%s" % subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                           cwd=ROOT, capture_output=True
                                           ).stdout.decode().strip())
    print("=" * 70)

    for label, fn in (
        ("工作树", lambda: face_tree()),
        ("历史", lambda: face_history(a.include_archive, a.since)),
        ("发行包", lambda: face_packages(a.dist, a.strict)),
        ("Release", lambda: face_github(a.tag) if a.github else
                   ("SKIP", "没加 --github", [])),
    ):
        state, note, detail = fn()
        rows.append((label, state, note))
        print("\n[%s] %-6s %s" % (state, label, note))
        for d in detail:
            for ln in str(d).splitlines():
                print("      " + ln)

    print("\n" + "=" * 70)
    for label, state, note in rows:
        print("  %-8s %-5s %s" % (label, state, note))
    bad = [r for r in rows if r[1] == "FAIL"]
    skip = [r for r in rows if r[1] == "SKIP"]
    if bad:
        print("[FAIL] " + "、".join(r[0] for r in bad) + " —— 不要发布")
        return 1
    if skip:
        print("[没验] " + "、".join(r[0] for r in skip) + " —— 不算通过")
        return 2
    print("[OK] 四个面都干净")
    return 0


if __name__ == "__main__":
    sys.exit(main())
