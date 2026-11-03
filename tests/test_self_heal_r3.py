# -*- coding: utf-8 -*-
"""tests/test_self_heal_r3.py —— 自愈 R3 代码级（1.6.0 P2.5）

用**真的 git 仓库**（临时目录里 init 一个）验隔离、提交、回滚；
用**注入的 patcher/runner** 代替真改代码与真跑 274 个用例。

钉住的是四条硬边界，不是"能不能修好某个 bug"：
  开关默认关 / 白名单 / 规模上限 / 绿了才提交、红了必回滚 / 熔断。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import self_heal_r3 as R3  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

NOW = 1793694000.0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def make_repo():
    """临时 git 仓库：一个 harness/demo.py 和一个 bot_core.py 的壳。"""
    d = tempfile.mkdtemp(prefix="r3_repo_")
    _git(d, "init")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    os.makedirs(os.path.join(d, "harness"))
    with open(os.path.join(d, "harness", "demo.py"), "w", encoding="utf-8") as f:
        f.write("x = 1\n")
    with open(os.path.join(d, "bot_core.py"), "w", encoding="utf-8") as f:
        f.write("y = 1\n")
    with open(os.path.join(d, ".env"), "w", encoding="utf-8") as f:
        f.write("SECRET=1\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-m", "init")
    return d


def diff_for(path, added_lines=1):
    body = "".join(f"+line{i}\n" for i in range(added_lines))
    return (f"diff --git a/{path} b/{path}\n"
            f"--- a/{path}\n+++ b/{path}\n@@ -1 +1,{added_lines + 1} @@\n" + body)


def patch_ok(path="harness/demo.py", lines=1):
    """在 worktree 里真的改文件，并返回 diff 文本。"""
    def _p(wt):
        p = os.path.join(wt, path)
        with open(p, "a", encoding="utf-8") as f:
            for i in range(lines):
                f.write(f"line{i}\n")
        out = _git(wt, "diff")
        return out.stdout
    return _p


def main():
    repo = make_repo()
    en = {"self_heal_r3_enable": True}

    try:
        print("[A] 开关关着时什么都不做")
        called = {"n": 0}

        def p_count(wt):
            called["n"] += 1
            return ""

        r = R3.run("issue-A", p_count, root=repo, cfg_getter=lambda: {}, now=NOW)
        check("★ 跳过", r.get("skipped") is True, r)
        check("★ 没建 worktree、没打补丁", called["n"] == 0 and not r.get("worktree"), r)
        check("默认值也是关", __import__("config").DEFAULT_CONFIG.get("self_heal_r3_enable") is False)

        print("\n[B] 白名单：白名单内的放行，外的拒绝")
        ok, why = R3.check_patch(diff_for("harness/demo.py"))
        check("harness/ 下放行", ok is True, why)
        ok, why = R3.check_patch(diff_for("bot_core.py"))
        check("bot_core.py 放行", ok is True, why)
        ok, why = R3.check_patch(diff_for("webui/webui_server.py"))
        check("★ 白名单外拒绝", ok is False and "白名单" in why, why)
        ok, why = R3.check_patch(diff_for(".env"))
        check("★ .env 命中红线", ok is False and "红线" in why, why)
        ok, why = R3.check_patch(diff_for("scripts/build.ps1"))
        check("★ 打包脚本命中红线", ok is False and "红线" in why, why)

        print("\n[C] 规模上限")
        ok, why = R3.check_patch(diff_for("harness/demo.py", R3.MAX_LINES))
        check(f"{R3.MAX_LINES} 行放行", ok is True, why)
        ok, why = R3.check_patch(diff_for("harness/demo.py", R3.MAX_LINES + 1))
        check("★ 超一行就拒绝", ok is False and "行" in why, why)
        big = "".join(diff_for(f"harness/f{i}.py") for i in range(R3.MAX_FILES + 1))
        ok, why = R3.check_patch(big)
        check("★ 超文件数就拒绝", ok is False and "文件" in why, why)
        ok, why = R3.check_patch("")
        check("空补丁拒绝", ok is False and "空" in why, why)

        print("\n[D] 验证通过 → 复制回主仓库并提交（不 push）")
        head0 = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
        r = R3.run("issue-green", patch_ok("harness/demo.py"),
                   root=repo, cfg_getter=lambda: en, now=NOW,
                   runner=lambda wt: (True, "all green"))
        check("★ 成功", r.get("ok") is True, r)
        head1 = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
        check("★ 主仓库多了一笔提交", head1 != head0, f"{head0} -> {head1}")
        with open(os.path.join(repo, "harness", "demo.py"), encoding="utf-8") as f:
            check("★ 改动真的落到主工作区", "line0" in f.read())
        check("账本记了 ok:true", any(x.get("issue") == "issue-green" and x.get("ok")
                                      for x in R3.read_ledger(repo)))
        check("★ worktree 已清理",
              not os.path.exists(os.path.join(repo, "data", "selfheal")) or
              not [d for d in os.listdir(os.path.join(repo, "data", "selfheal"))
                   if d.startswith("wt-")], os.listdir(os.path.join(repo, "data", "selfheal")))

        print("\n[E] 验证失败 → 主仓库一个字都不许动")
        head2 = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
        with open(os.path.join(repo, "harness", "demo.py"), encoding="utf-8") as f:
            before = f.read()
        r = R3.run("issue-red", patch_ok("harness/demo.py", 2),
                   root=repo, cfg_getter=lambda: en, now=NOW,
                   runner=lambda wt: (False, "2 failed"))
        check("★ 失败", r.get("ok") is False, r)
        check("★ 没有新提交",
              _git(repo, "rev-parse", "--short", "HEAD").stdout.strip() == head2)
        with open(os.path.join(repo, "harness", "demo.py"), encoding="utf-8") as f:
            check("★ 主工作区内容未变", f.read() == before)
        check("账本记了 ok:false", any(x.get("issue") == "issue-red" and not x.get("ok")
                                      for x in R3.read_ledger(repo)))

        print("\n[F] 熔断：同一问题连续两次失败 → 30 天内不再试")
        called2 = {"n": 0}

        def p2(wt):
            called2["n"] += 1
            return diff_for("harness/demo.py")

        R3.run("issue-boom", p2, root=repo, cfg_getter=lambda: en, now=NOW,
               runner=lambda wt: (False, "red"))
        R3.run("issue-boom", p2, root=repo, cfg_getter=lambda: en, now=NOW + 10,
               runner=lambda wt: (False, "red"))
        n_before = called2["n"]
        r = R3.run("issue-boom", p2, root=repo, cfg_getter=lambda: en, now=NOW + 20,
                   runner=lambda wt: (True, "green"))
        check("★ 第三次被熔断挡下", r.get("skipped") is True and "熔断" in str(r.get("reason")), r)
        check("★ 熔断后没再去打补丁", called2["n"] == n_before, called2)
        r2 = R3.run("issue-boom", p2, root=repo, cfg_getter=lambda: en,
                    now=NOW + 31 * 86400, runner=lambda wt: (True, "green"))
        check("★ 30 天后解封", r2.get("skipped") is not True, r2)

        print("\n[G] 工作区不干净时拒绝自动提交")
        with open(os.path.join(repo, "harness", "demo.py"), "a", encoding="utf-8") as f:
            f.write("# dirty\n")
        r = R3.run("issue-dirty", patch_ok(), root=repo, cfg_getter=lambda: en, now=NOW,
                   runner=lambda wt: (True, "green"))
        _why = str(r.get("reason"))
        check("★ 拒绝并说明原因", r.get("ok") is False
              and ("未提交" in _why or "不干净" in _why), r)
        _git(repo, "checkout", "--", "harness/demo.py")

    finally:
        shutil.rmtree(repo, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
