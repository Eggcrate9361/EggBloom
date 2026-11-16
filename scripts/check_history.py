# -*- coding: utf-8 -*-
"""历史闸：扫**已经提交过的东西**，不只是工作树（2026-11-15）。

为什么必须单独有它
------------------
`check_secrets.py` 扫的是工作树（已跟踪 + 未跟踪），`--staged` 扫暂存区。两者都看不见
「这个文件昨天写了什么」：

    2026-11-15  docs/RELEASE_CHECKLIST.md 里有一行 ssh 地址
                工作树：上一版已经删了，扫描干净 ✅
                历史：  23 个提交里每一个都带着它 ❌

`git log -p` 一翻就见，但没人会为了每次提交去翻。三道闸全绿过的那次，漏的就是这一段。

判据
====
与工作树、发行包**同一套**（`check_secrets.scan_text`）：
- blob 内容：地址 / 密钥 / 身份号
- 提交信息：同上（message 也是公开面，`git log` 直接看得到）
- tag 附注：同上

扫描范围
========
默认所有本地 ref（`refs/heads/*`、`refs/tags/*`）。`--refs` 可指定，`--all` 连
`refs/remotes/*` 一起。**只扫本地能到达的对象** —— 这正是发布前的口径：推上去的
就是这些对象。已经推到远端但本地删掉的（比如强推后的旧链）在这里看不见，
那是 `git bundle` 归档该管的事。

同一个 blob 只扫一次（内容相同，命中必然相同），但会记住它出现在哪个 ref / 路径，
报告按「ref → 路径 → 行号」给。

用法
====
    python scripts/check_history.py                  # 所有本地 ref
    python scripts/check_history.py --refs main      # 只扫 main
    python scripts/check_history.py --all            # 连远端跟踪分支
    python scripts/check_history.py --selftest       # 判据自检（临时仓库）
    python scripts/check_history.py --since <rev>    # 只扫这个提交之后

退出码：0 干净 / 1 命中 / 2 参数或环境问题。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import check_secrets as cs  # noqa: E402

#: 大文件/二进制：读了没意义，而且会把内存吃掉
MAX_TEXT = 8 * 1024 * 1024

#: 归档 ref：**不会推出去**，默认跳过。它们是强推前留的旧链与备份分支，
#: 里面本来就带着「已经被清掉」的东西 —— 扫了必然红，红了也不该拦发布。
#: 要审归档就显式 `--include-archive`。
ARCHIVAL = ("refs/heads/legacy/", "refs/heads/backup/",
            "refs/heads/public/", "refs/heads/archive/")


def _git(args, cwd=None, binary=False):
    r = subprocess.run(["git"] + list(args), cwd=cwd or ROOT, capture_output=True)
    if r.returncode != 0:
        raise SystemExit("[check_history] git %s 失败: %r"
                         % (" ".join(args), r.stderr[:300]))
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def list_refs(all_refs=False, only=None, include_archive=False):
    """([(refname, sha)], [跳过的归档 ref])。默认本地 heads + tags，跳过归档 ref。"""
    if only:
        out = []
        for r in only:
            out.append((r, _git(["rev-parse", r]).strip()))
        return out, []
    pats = ["refs/heads", "refs/tags"] + (["refs/remotes"] if all_refs else [])
    res, skipped = [], []
    for p in pats:
        for line in _git(["for-each-ref", "--format=%(refname) %(objectname)", p]).splitlines():
            if not line.strip():
                continue
            name, sha = line.rsplit(" ", 1)
            if not include_archive and name.startswith(ARCHIVAL):
                skipped.append(name)
                continue
            res.append((name, sha))
    return res, skipped


def _objects(rev, cwd=None, since=None):
    """rev 可达的对象清单 [(sha, path)]。path 可能为空（同一对象换个路径出现时）。"""
    args = ["rev-list", "--objects", rev]
    if since:
        args = ["rev-list", "--objects", "%s..%s" % (since, rev)]
    out = []
    for line in _git(args, cwd=cwd).splitlines():
        p = line.split(" ", 1)
        if len(p) == 2 and p[1] and len(p[0]) == 40:
            out.append((p[0], p[1]))
    return out


def read_blobs(pairs, cwd=None):
    """批量读 blob：{sha: bytes}。

    ★ 这里有个必须记住的坑（2026-11-15 踩过）：`git cat-file --batch` 对 **tree**
      也会吐原始内容，所以遇到非 blob 只跳一行会把后面全部错位 —— 我当时因此
      「发现」某测试文件里有公网 IP，实际那些内容是另一个文件（判据文件自己）的。
      正确做法是按 header 里的 size 读满再推进。
    """
    if not pairs:
        return {}
    shas = sorted({s for s, _ in pairs})
    proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=cwd or ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out, _ = proc.communicate(b"\n".join(s.encode() for s in shas) + b"\n")
    got, pos = {}, 0
    for sha in shas:
        nl = out.find(b"\n", pos)
        if nl < 0:
            break
        head = out[pos:nl].split(b" ")
        if len(head) < 3:                      # `<sha> missing`
            pos = nl + 1
            continue
        size = int(head[2])
        body = out[nl + 1: nl + 1 + size]
        pos = nl + 1 + size + 1
        if head[1] == b"blob":
            got[sha] = body
    return got


def _is_text(body):
    if len(body) > MAX_TEXT:
        return False
    if b"\x00" in body[:4000]:
        return False
    return True


def scan_refs(refs, since=None, cwd=None, verbose=True):
    """扫一组 ref。返回 (命中列表, 统计 dict)。"""
    blocked = cs.idn.blocked_digests()
    hits, stats = [], {"refs": len(refs), "blobs": 0, "scanned": 0, "commits": 0}
    seen = set()

    # 每个 ref 的可达对象（并集即可：同一 blob 只扫一次）
    pairs = {}
    for name, sha in refs:
        try:
            objs = _objects(sha, cwd=cwd, since=since)
        except SystemExit:
            continue
        for osha, path in objs:
            if osha not in pairs:
                pairs[osha] = (name, path)

    # 注意：pairs 里既有 blob 也有 tree（`rev-list --objects` 两种都给），
    # 所以「blob 数」要按 read_blobs 实际拿到的算，别用 len(pairs)。
    bodies = read_blobs(list(pairs.items()), cwd=cwd)
    stats["blobs"] = len(bodies)
    for sha, body in bodies.items():
        if not _is_text(body):
            continue
        stats["scanned"] += 1
        ref, path = pairs[sha]
        text = body.decode("utf-8", "replace")
        key = sha
        if key in seen:
            continue
        seen.add(key)
        for p, ln, name_, val in cs.scan_text(path or "(无路径)", text, blocked):
            hits.append((ref, path or "(无路径)", ln, name_, val))

    # 提交信息 / tag 附注
    for name, sha in refs:
        rng = ["%s..%s" % (since, sha)] if since else [sha]
        try:
            msgs = _git(["log", "--format=%H%x00%B%x00%x00"] + rng, cwd=cwd)
        except SystemExit:
            msgs = ""
        for chunk in msgs.split("\x00\x00"):
            if not chunk.strip():
                continue
            csha, _, body = chunk.partition("\x00")
            stats["commits"] += 1
            for p, ln, name_, val in cs.scan_text("(commit message)", body, blocked):
                hits.append((name, "commit %s" % csha[:7], ln, name_, val))
        try:
            tagmsg = _git(["for-each-ref", "--format=%(contents)", name], cwd=cwd)
        except SystemExit:
            tagmsg = ""
        for p, ln, name_, val in cs.scan_text("(tag message)", tagmsg, blocked):
            hits.append((name, "tag 附注", ln, name_, val))

    return hits, stats


def _selftest():
    """在临时仓库里造一条历史，断言：内容能扫到、且 blob 解析不会错位。"""
    tmp = tempfile.mkdtemp(prefix="histchk-")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    bad = 0
    try:
        def g(*a):
            r = subprocess.run(["git"] + list(a), cwd=tmp, capture_output=True, env=env)
            if r.returncode != 0:
                raise SystemExit("selftest git %s: %r" % (" ".join(a), r.stderr[:200]))
            return r.stdout.decode("utf-8", "replace")

        g("init", "-q", "-b", "main")
        # 两个文件：干净 + 带文档段地址的
        with open(os.path.join(tmp, "clean.py"), "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        with open(os.path.join(tmp, "notes.md"), "w", encoding="utf-8") as f:
            f.write("裸仓库在 ssh://ubuntu@192.0.2.10/home/ubuntu/x.git\n")
        g("add", "-A")
        g("commit", "-qm", "首个提交")
        # 第二个提交把地址删掉 —— 工作树干净，历史不干净（正是真实场景）
        with open(os.path.join(tmp, "notes.md"), "w", encoding="utf-8") as f:
            f.write("地址不入库\n")
        g("add", "-A")
        g("commit", "-qm", "删掉地址")

        refs = [("main", g("rev-parse", "main").strip())]
        hits, stats = scan_refs(refs, cwd=tmp, verbose=False)
        names = {h[3] for h in hits}
        # 期望：只抓到旧版本里的那两处；工作树那版干净，但历史里有 → 必须报出来
        ok1 = {"SSH 地址", "user@IP"} <= names
        print("  [%s] 历史命中旧地址：%s" % ("OK" if ok1 else "FAIL", sorted(names)))
        print("  [%s] 扫到 %d 个文本 blob（clean + notes 两版 = 3）"
              % ("OK" if stats["scanned"] == 3 else "FAIL", stats["scanned"]))
        bad += 0 if ok1 else 1
        bad += 0 if stats["scanned"] == 3 else 1

        # 解析器对齐：对象数应等于 `git rev-list --objects` 的行数（不含 tree 被误读）
        objs = _objects("main", cwd=tmp)
        bodies = read_blobs(objs, cwd=tmp)
        ok3 = len(bodies) == 3
        print("  [%s] --batch 解析拿到 %d 个 blob（应为 3，tree 不得错位）"
              % ("OK" if ok3 else "FAIL", len(bodies)))
        bad += 0 if ok3 else 1
        print("[check_history --selftest] %d/3 通过" % (3 - bad))
        return 0 if not bad else 1
    finally:
        for root, dirs, files in os.walk(tmp, topdown=False):
            for f in files:
                try:
                    os.unlink(os.path.join(root, f))
                except OSError:
                    pass
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError:
                    pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="历史闸：扫所有已提交内容")
    ap.add_argument("--refs", default="", help="逗号分隔的 ref（默认所有本地 heads+tags）")
    ap.add_argument("--all", action="store_true", help="连 refs/remotes 一起")
    ap.add_argument("--since", default="", help="只看这个提交之后的")
    ap.add_argument("--include-archive", action="store_true",
                    help="连 legacy/ backup/ public/ archive/ 这些归档 ref 一起扫")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()

    only = [x.strip() for x in a.refs.split(",") if x.strip()] or None
    refs, skipped = list_refs(a.all, only, a.include_archive)
    if not refs:
        print("[check_history] 没有可扫的 ref")
        return 2

    print("[check_history] ref %d 个：%s" % (len(refs), ", ".join(n for n, _ in refs[:8])
                                             + (" …" if len(refs) > 8 else "")))
    if skipped:
        print("[check_history] 跳过归档 ref %d 个（不推出去，--include-archive 可扫）：%s"
              % (len(skipped), ", ".join(skipped[:4]) + (" …" if len(skipped) > 4 else "")))
    hits, stats = scan_refs(refs, since=a.since or None)
    print("[check_history] blob %d 个（文本 %d），提交信息 %d 条"
          % (stats["blobs"], stats["scanned"], stats["commits"]))

    if not hits:
        print("[check_history] 干净")
        return 0

    print("\n[check_history] 命中 %d 处 —— **工作树干净不代表历史干净**：" % len(hits))
    for ref, path, ln, name, val in hits[:40]:
        print("  %-22s %s:%s  [%s]  %s" % (ref, path, ln, name, val))
    if len(hits) > 40:
        print("  …… 还有 %d 处" % (len(hits) - 40))
    print("\n怎么改：先确认它是不是真值；是的话要重建历史（tools/public_history.py），\n"
          "  只删当前文件不够 —— 旧提交里还在，`git log -p` 照样看得到。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
