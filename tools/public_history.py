#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""公开历史起点：从当前树起一个全新的根，旧历史不迁移。

为什么不做"逐条改写旧 message"
================================
`提交历史体检-20261113.md` 给了两条路：A 重写 531 条 message、B orphan 全新历史。
先按 A 写了一版正则改写器跑 `--plan`，实测**会改坏 185 条**，典型：

    (cherry picked from commit 972173789c56…)   →  sha 里的 10 位数字被当成号，改成 ***
    已有 mismatch_note() 管"对方说的时段"        →  「对方说」被当成转述，删成「的时段」
    改为 10000001 / 10000002 占位（合成号）      →  合成号也被打码，测试语义读不出来

机械改写把 42 个真号问题换成 185 处语义损坏，还要重写全部 450 个已 push 提交的
hash、强推、同步处理 4 个 refs —— 代价比问题本身大。所以走 B：

* 新链只有**一个**提交，message 干净，树与私有仓库 HEAD **逐字节一致**（tree hash 相等）；
* 旧链原样留在私有仓库（`main` + 3 个 `backup/*`），一个 ref 都不动；
* 脚本只写 `refs/heads/public/main`，是否用它当 `main`、何时推，由人决定。

**2026-11-14 补**：上面说的「旧链留私有仓库」在本地已经执行完了 —— 迁移做成之后
那些 ref（`legacy/main` / `backup/*` / 指向旧提交的 tag）被整体归档成一个 bundle
再删掉，理由与归档位置见 CONTRIBUTING.md §6.8。本脚本的 `--switch` 语义没变，
只是它当年留下的 `legacy/main` 现在不存在了；要重跑 `--switch` 得先有那个 ref。

用法
====
    python tools/public_history.py --plan      # 打印将要创建的根 + 校验（只读）
    python tools/public_history.py --apply     # 创建 refs/heads/public/main
    python tools/public_history.py --switch    # main 改指向它（legacy/main 留旧链）

`--switch` 是唯一会动 `main` 的动作，必须显式给。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import commit_lint as cl  # noqa: E402

TARGET = "refs/heads/public/main"
LEGACY = "refs/heads/legacy/main"

ROOT_MSG = """\
chore(release): 11-13 公开历史起点

公开仓库的提交历史从这个根开始，此前的 531 个提交不迁移。

原因：旧链的提交信息里有 42 处真实身份号、6 处转述句式，逐条机械改写实测
会连带损坏 185 条 message（cherry-pick 记录里的 commit sha、mismatch_note()
里「对方的时段」这类正常叙述都会被误判），而重写又会改掉全部 450 个已 push
提交的 hash 并要求强推。两害相权，另起一个干净的根更可靠。

本提交的树与私有仓库 HEAD 完全一致（tree hash 相等，代码一行未改），差异只在
历史。树内不含真实 QQ 号与群号：已全部替换为 1000000xxx / 9000000xx 段，六个
已知真号在树内的命中数为 0。

规则见 CONTRIBUTING.md §6，闸见 tools/commit_lint.py。
"""


def _git(*args, text=True):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=text)
    if r.returncode != 0:
        raise SystemExit(f"[public] git {' '.join(args)} 失败: {r.stderr}")
    return r.stdout


def _exists(rev: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", rev],
                          cwd=ROOT, capture_output=True).returncode == 0


def _tree_of(rev: str) -> str:
    return _git("rev-parse", f"{rev}^{{tree}}").strip()


def _dirty() -> list:
    """未提交条目（含未跟踪、未忽略）。它们不在 HEAD 的树里。"""
    return [x for x in _git("status", "--porcelain").splitlines() if x.strip()]


def _build() -> tuple[str, str]:
    """返回 (新根 sha, 树 sha)。不改任何 ref。"""
    tree = _tree_of("HEAD")
    r = subprocess.run(["git", "commit-tree", tree], cwd=ROOT,
                       input=ROOT_MSG.encode("utf-8"), capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"[public] commit-tree 失败: {r.stderr.decode('utf-8', 'replace')}")
    return r.stdout.decode().strip(), tree


def plan() -> int:
    sha, tree = _build()
    dirty = _dirty()
    print(f"新根：{sha[:7]}  树：{tree[:12]}（= HEAD 的树）")
    print("      sha 是本次试算值，--apply 会再算一次（commit-tree 取当前时间戳），")
    print("      两次 sha 必然不同 —— 判据看「树一致」，不要比对 sha。")
    print(f"提交数：1（旧链 {_git('rev-list', '--count', 'HEAD').strip()} 个不迁移）")
    if dirty:
        print(f"⚠ 工作区有 {len(dirty)} 项未提交改动 —— **不在**这个根里。")
        print("   --apply 会直接拒绝：树取自 HEAD，这些改动不在快照里，建出来的根")
        print("   是改动之前的状态，历史里仍能捞到本该清掉的内容。先提交或 stash。")
    v = cl.lint_message(ROOT_MSG, cl.load_policy())
    print(f"根提交信息自检：{len(v)} 处问题" + ("" if not v else f" → {[x['detail'] for x in v]}"))
    print("\n创建（只写新 ref，不动 main）：python tools/public_history.py --apply")
    return 0 if not v else 1


def apply_ref() -> int:
    if _exists(TARGET):
        print(f"[public] {TARGET} 已存在，先删除再重建：git update-ref -d {TARGET}")
        return 1
    dirty = _dirty()
    if dirty:
        print(f"[public] 拒绝建根：工作区有 {len(dirty)} 项未提交改动。")
        print("  树快照取自 HEAD，这些改动不在里面 —— 建出来的根会是「改动之前」")
        print("  的状态，历史里仍能捞到本该清掉的内容（真号、临时路径等）。")
        print("  先提交或 stash，把工作区清空再跑 --apply。")
        return 1
    sha, tree = _build()
    _git("update-ref", TARGET, sha)
    print(f"已创建 {TARGET} → {sha[:7]}")
    print(f"校验：树 {'一致' if _tree_of(TARGET) == _tree_of('HEAD') else '不一致 ×'}"
          f"，提交数 {_git('rev-list', '--count', TARGET).strip()}")
    print(f"复扫：python tools/commit_lint.py --rev-range {TARGET} --strict")
    return 0


def switch() -> int:
    if not _exists(TARGET):
        print("[public] 还没有 public/main，先 --apply")
        return 1
    if not _exists(LEGACY):
        _git("update-ref", LEGACY, _git("rev-parse", "main").strip())
        print(f"旧链已存到 {LEGACY}（{_git('rev-parse', '--short', 'main').strip()}）")
    # 只动本地 ref；推不推、什么时候推，由人决定
    _git("update-ref", "refs/heads/main", _git("rev-parse", TARGET).strip())
    print(f"main 已指向 {_git('rev-parse', '--short', 'main').strip()}；"
          f"旧链在 {LEGACY}")
    print("远端仍是旧链，推送需要 --force-with-lease，属 §6.5.4 重写窗口动作。")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="公开历史起点（orphan 根）")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--switch", action="store_true", help="把 main 指过去（保留 legacy/main）")
    a = ap.parse_args(argv)
    if a.switch:
        return switch()
    if a.apply:
        return apply_ref()
    return plan()


if __name__ == "__main__":
    sys.exit(main())
