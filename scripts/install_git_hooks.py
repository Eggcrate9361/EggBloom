#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 .githooks/ 里的版本化钩子装到 .git/hooks/。

为什么要装而不是切 core.hooksPath：本机 `.git/hooks/pre-commit`（10-29 起的
敏感信息扫描）是手放的，切 `core.hooksPath` 会让它失效 —— 一个能悄悄关掉
既有闸的操作，不能作为默认动作。所以这里做的是「复制 + 覆盖」，两边始终一致。

    python scripts/install_git_hooks.py            # 安装
    python scripts/install_git_hooks.py --check    # 只检查是否一致（CI/自检用）

Windows 上 sh 钩子由 Git for Windows 自带的 sh 执行，不需要另装 bash；
但钩子里的 `python` 必须在 hook 的 PATH 上，否则 commit-msg 会打印警告后放行。
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, ".githooks")
#: pre-push 是 11-13 补的：本地 legacy/*、backup/* 是 orphan 重建前的完整开发史，
#: 里面还躺着真实号码。`git push --all` 一条命令就能把它们推进公开仓库，
#: 所以「只许推 main」也得是道闸，不能靠记得。
HOOKS = ("pre-commit", "commit-msg", "pre-push")


def git_dir() -> str:
    r = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("[hooks] 不在 git 仓库里")
    p = r.stdout.strip()
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def install(check_only: bool) -> int:
    dst = os.path.join(git_dir(), "hooks")
    hooks_path = subprocess.run(["git", "config", "--get", "core.hooksPath"],
                                cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if hooks_path:
        print(f"[hooks] ⚠ core.hooksPath={hooks_path} 已设置，.git/hooks 不会被调用。"
              f"先 git config --unset core.hooksPath")
        return 1

    bad = 0
    for name in HOOKS:
        src = os.path.join(SRC, name)
        target = os.path.join(dst, name)
        if not os.path.exists(src):
            print(f"  [FAIL] 源文件缺失 .githooks/{name}")
            bad += 1
            continue
        if check_only:
            same = (os.path.exists(target)
                    and open(target, "rb").read() == open(src, "rb").read())
            print(f"  [{'OK' if same else 'FAIL'}] .git/hooks/{name} "
                  f"{'与源一致' if same else '缺失或不一致'}")
            bad += 0 if same else 1
            continue
        shutil.copyfile(src, target)
        os.chmod(target, os.stat(target).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        print(f"  [OK]   .githooks/{name} → .git/hooks/{name}")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对，不写")
    a = ap.parse_args(argv)
    return install(a.check)


if __name__ == "__main__":
    sys.exit(main())
