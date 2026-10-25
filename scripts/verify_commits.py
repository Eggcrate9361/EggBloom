"""逐个提交回放，只跑该提交改到的测试文件。

判据：**每个提交都要能单独运行**，否则是"只在最终状态能跑"，
中间某个提交可能是坏的，将来 bisect 会踩到。

用法：python scripts/verify_commits.py                    # 回放最近 10 个提交
      python scripts/verify_commits.py <commit>...        # 回放指定提交

★★ 用临时 worktree，**不碰当前仓库的 HEAD**。
   2026-10-25 踩过两次：
   1. 脚本用 `git checkout <hash>` 回放，结束时又 checkout 回"起始 HEAD 的 hash"
      —— hash 不是分支，恢复后 HEAD 仍然 detached（VS Code 顶栏显示
      "Detached HEAD / Cannot publish detached HEAD"），用户当场发现。
   2. 就算改成切回分支名，回放期间用户的编辑器看到的也是别的提交，
      工作区状态在变 —— 不该动用户正在看的仓库。

   `git worktree add` 在另一个目录检出目标提交，主仓库完全不动。
   worktree 目录放在系统临时目录，跑完 `git worktree remove --force` 清掉。
"""
import os
import shutil
import subprocess
import sys
import tempfile


def sh(*args, cwd=None):
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd)


def main():
    commits = sys.argv[1:]
    if not commits:
        r = sh("git", "log", "--pretty=format:%h", "-10")
        commits = [x.strip() for x in r.stdout.split("\n") if x.strip()]
        # 从旧到新回放（时间顺序上更容易看出是哪个改动引入的）
        commits.reverse()

    if sh("git", "rev-parse", "--git-dir").returncode != 0:
        print("不在 git 仓库里")
        return 2

    bad = []
    tmp_root = tempfile.mkdtemp(prefix="eggbloom_verify_")
    try:
        for c in commits:
            wt = os.path.join(tmp_root, c[:10])
            r = sh("git", "worktree", "add", "--detach", "--quiet", wt, c)
            if r.returncode != 0:
                print(f"[WORKTREE FAIL] {c}: {r.stderr[:200]}")
                bad.append((c, "worktree"))
                continue
            try:
                names = sh("git", "show", "--name-only", "--pretty=format:", c).stdout
                files = [n.strip() for n in names.split("\n")
                         if n.strip().startswith("tests/test_") and n.strip().endswith(".py")]
                tests = sorted(set(files))
                if not tests:
                    print(f"[SKIP] {c}  无测试改动", flush=True)
                    continue
                for t in tests:
                    rr = subprocess.run([sys.executable, os.path.join(wt, t)],
                                        capture_output=True, text=True,
                                        encoding="utf-8", errors="replace",
                                        timeout=300, cwd=wt)
                    ok = rr.returncode == 0
                    print(f"[{'OK  ' if ok else 'FAIL'}] {c}  {t}", flush=True)
                    if not ok:
                        bad.append((c, t))
                        print((rr.stdout or "")[-700:], flush=True)
                        print((rr.stderr or "")[-400:], flush=True)
            finally:
                sh("git", "worktree", "remove", "--force", wt)
    finally:
        sh("git", "worktree", "prune")
        shutil.rmtree(tmp_root, ignore_errors=True)
        # 断言主仓库没被动过
        state = sh("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        dirty = sh("git", "status", "--porcelain").stdout.strip()
        print(f"主仓库仍在 {state}，工作区{'有' if dirty else '无'}未提交改动")

    print("=" * 56)
    if bad:
        print("失败：")
        for c, t in bad:
            print("  ", c, t)
        return 1
    print("每个提交都单独可运行")
    return 0


if __name__ == "__main__":
    sys.exit(main())