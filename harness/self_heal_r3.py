# -*- coding: utf-8 -*-
"""harness/self_heal_r3.py —— 自愈 R3：代码级（1.6.0 P2.5）

## 它做什么

一次完整的"发现问题 → 隔离修复 → 验证 → 提交/回滚"：

    问题单 ──▶ 建 worktree ──▶ 在隔离目录里打补丁 ──▶ 白名单/规模审查
           ──▶ 跑全量测试 ──▶ 绿：提交（不 push）
                            └─ 红：删 worktree，主仓库一个字没动

## 为什么必须用 worktree

跑着的 bot 直接读工作区文件。原地改代码 = 对线上热替换，改到一半就是半坏状态，
而且失败时没法干净回滚。worktree 是同一个仓库的另一个检出，测试在那里跑，
主工作区全程不动 —— 只有**验证通过**的补丁才复制回来提交。

## 四条硬边界（红线）

① 白名单：只有 `harness/*.py`、`bot_core.py`、`reply_guard.py`、`evals/rules.json`
   允许改；`.env`、密钥、`VERSION`、打包脚本一律不碰。
② 规模：≤40 行、≤3 文件。超了就转人工 —— 自动化改大面积代码没有可回滚的信心。
③ 验证：全量测试必须全绿（`run_all_tests.py --evals`）。
④ 熔断：同一问题连续两次失败 → 30 天内不再尝试。同一个坑反复自动试，
   是在用机器的耐心换用户的账号风险。

R4（装包 / 换模型 / 开权限）永远人工，不进本模块 —— 那不是"修"，是"扩权"。

## 可注入的两个点

`patcher(worktree_path) -> str`（返回 git diff 文本）与 `runner(worktree_path) -> (ok, detail)`。
测试用假的，不需要真的改代码、也不需要真跑 274 个用例。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

#: 允许自动修改的路径（前缀匹配）
WHITELIST_PREFIX = ("harness/",)
WHITELIST_EXACT = ("bot_core.py", "reply_guard.py", "evals/rules.json")

#: 永远不碰（命中即拒，哪怕在白名单里）
FORBIDDEN_PATTERN = (
    r"(^|/)\.env$", r"(^|/)VERSION$", r"(^|/)scripts/", r"\.pem$", r"\.key$",
    r"(^|/)bot_config\.json$", r"(^|/)plugin\.json$",
)

MAX_LINES = 40      # 增删行合计
MAX_FILES = 3

#: 熔断
BREAKER_FAILS = 2
BREAKER_DAYS = 30

DEFAULT_LEDGER = os.path.join("data", "repairs.jsonl")


def _now(ts=None):
    return float(ts) if ts is not None else time.time()


def _git(repo, *args, timeout=120):
    """跑一条 git 命令，返回 (ok, stdout+stderr)。"""
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode == 0, out
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ── 补丁审查 ────────────────────────────────────────────────
def parse_diff_files(diff: str) -> dict:
    """从 git diff 文本里取 {文件路径: {"added": n, "removed": m}}。"""
    files = {}
    cur = None
    for line in (diff or "").splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            if m:
                cur = m.group(2).strip()
                files.setdefault(cur, {"added": 0, "removed": 0})
        elif cur is not None:
            if line.startswith("+") and not line.startswith("+++"):
                files[cur]["added"] += 1
            elif line.startswith("-") and not line.startswith("---"):
                files[cur]["removed"] += 1
    return files


def check_patch(diff: str) -> tuple:
    """补丁审查：(ok, reason)。判据集中在这一处，别在别处另写一套。"""
    files = parse_diff_files(diff)
    if not files:
        return False, "补丁为空（没有可审的改动）"
    names = list(files)
    for name in names:
        norm = name.replace("\\", "/")
        for pat in FORBIDDEN_PATTERN:
            if re.search(pat, norm):
                return False, f"拒绝：命中红线路径 {name}"
        if norm not in WHITELIST_EXACT and not any(norm.startswith(p) for p in WHITELIST_PREFIX):
            return False, f"拒绝：{name} 不在白名单内"
    if len(names) > MAX_FILES:
        return False, f"拒绝：改了 {len(names)} 个文件（上限 {MAX_FILES}），转人工"
    total = sum(v["added"] + v["removed"] for v in files.values())
    if total > MAX_LINES:
        return False, f"拒绝：改动 {total} 行（上限 {MAX_LINES}），转人工"
    return True, f"通过：{len(names)} 个文件 / {total} 行"


# ── 账本与熔断 ──────────────────────────────────────────────
def ledger_path(root) -> str:
    return os.path.join(root, DEFAULT_LEDGER)


def read_ledger(root, limit=200) -> list:
    p = ledger_path(root)
    if not os.path.exists(p):
        return []
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        return []
    return out[-limit:]


def append_ledger(root, record: dict) -> bool:
    """追加一条账本。append-only：只写不改，出了事能复盘。"""
    p = ledger_path(root)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def breaker_blocked(root, issue_key, now=None) -> tuple:
    """同一问题连续失败是否熔断：(blocked, reason)。"""
    now = _now(now)
    same = [r for r in read_ledger(root) if r.get("issue") == issue_key]
    if len(same) < BREAKER_FAILS:
        return False, ""
    tail = same[-BREAKER_FAILS:]
    if any(r.get("ok") for r in tail):
        return False, ""      # 中间成功过，说明问题变了或已解决
    last_ts = max(float(r.get("ts") or 0) for r in tail)
    until = last_ts + BREAKER_DAYS * 86400
    if now < until:
        left = int((until - now) / 86400) + 1
        return True, f"熔断中：该问题连续 {BREAKER_FAILS} 次失败，还需 {left} 天"
    return False, ""


# ── 主流程 ──────────────────────────────────────────────────
def _worktree_path(root, ts):
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(ts))
    return os.path.join(root, "data", "selfheal", f"wt-{stamp}")


def _copy_back(worktree, root, names) -> list:
    """把验证通过的改动复制回主工作区（只复制白名单里的那几个文件）。"""
    done = []
    for name in names:
        src = os.path.join(worktree, name)
        dst = os.path.join(root, name)
        if not os.path.exists(src):
            continue
        os.makedirs(os.path.dirname(dst) or root, exist_ok=True)
        shutil.copy2(src, dst)
        done.append(name)
    return done


def run(issue_key, patcher, *, root, runner=None, cfg_getter=None, now=None,
        keep_worktree=False, worktree_factory=None) -> dict:
    """跑一次 R3 自愈。

    issue_key: 问题标识（用来算熔断，通常取 failure_top 里的 key）
    patcher:   fn(worktree) -> git diff 文本；在隔离目录里完成修改
    runner:    fn(worktree) -> (ok, detail)；默认跑全量测试
    """
    ts = _now(now)
    cfg = {}
    if cfg_getter:
        try:
            cfg = dict(cfg_getter() or {})
        except Exception:
            cfg = {}

    # ① 开关（默认关）
    if not cfg.get("self_heal_r3_enable", False):
        return {"ok": False, "skipped": True, "reason": "self_heal_r3_enable 关闭"}

    # ② 熔断
    blocked, why = breaker_blocked(root, issue_key, now=ts)
    if blocked:
        return {"ok": False, "skipped": True, "reason": why}

    # ③ 仓库要干净：脏工作区上做自动提交，等于把用户没提交的东西一起提交
    #   ★ 2026-11-03：这里必须只看**已跟踪**文件（--untracked-files=no）。
    #   账本自己写在 data/repairs.jsonl，是未跟踪文件 —— 用默认的
    #   `status --porcelain` 会把它算成"工作区不干净"，于是第一次跑完之后
    #   就永久拒绝再跑（自锁）。冒烟时就踩到了：第二次调用直接返回"不干净"。
    ok, out = _git(root, "status", "--porcelain", "--untracked-files=no")
    if not ok:
        return {"ok": False, "reason": f"不是 git 仓库或 git 不可用: {out[:120]}"}
    dirty = [ln for ln in out.splitlines() if ln.strip()]
    if dirty:
        return {"ok": False, "reason": "工作区有未提交的改动，R3 只在干净工作区上跑: "
                                       + "; ".join(dirty[:3])}

    # ④ 建 worktree
    wt = (worktree_factory or _worktree_path)(root, ts)
    try:
        os.makedirs(os.path.dirname(wt), exist_ok=True)
    except OSError:
        pass
    ok, out = _git(root, "worktree", "add", "--detach", wt)
    if not ok:
        return {"ok": False, "reason": f"建 worktree 失败: {out[:160]}"}

    result = {"ok": False, "worktree": wt, "issue": issue_key, "ts": ts}
    try:
        # ⑤ 在隔离目录里打补丁
        try:
            diff = patcher(wt) or ""
        except Exception as e:
            result.update(reason=f"打补丁失败: {type(e).__name__}: {e}")
            return result

        # ⑥ 审查
        ok, reason = check_patch(diff)
        result["review"] = reason
        if not ok:
            result.update(reason=reason)
            return result

        names = list(parse_diff_files(diff))

        # ⑦ 验证
        if runner is None:
            ok, detail = _run_full_tests(wt)
        else:
            ok, detail = runner(wt)
        result["verify"] = str(detail)[:400]
        if not ok:
            result.update(reason="验证未通过，已回滚")
            return result

        # ⑧ 复制回主工作区并提交（不 push）
        copied = _copy_back(wt, root, names)
        ok, out = _git(root, "add", *copied)
        if not ok:
            result.update(reason=f"git add 失败: {out[:160]}")
            return result
        msg = f"fix(selfheal): {issue_key}（R3 自动修复）"
        ok, out = _git(root, "commit", "-m", msg)
        if not ok:
            result.update(reason=f"git commit 失败: {out[:160]}")
            return result
        ok, sha = _git(root, "rev-parse", "--short", "HEAD")
        result.update(ok=True, sha=(sha or "").strip(), files=copied, reason="已提交（未推送）")
        return result
    finally:
        # ⑨ 收尾：账本 + 清 worktree（失败一定要删，别留垃圾目录）
        append_ledger(root, {
            "ts": ts, "kind": "code", "issue": issue_key,
            "ok": bool(result.get("ok")),
            "sha": result.get("sha", ""),
            "reason": result.get("reason", ""),
            "review": result.get("review", ""),
            "verify": result.get("verify", ""),
        })
        if not keep_worktree:
            _git(root, "worktree", "remove", "--force", wt)
            _git(root, "worktree", "prune")
            if os.path.isdir(wt):
                shutil.rmtree(wt, ignore_errors=True)


def _run_full_tests(worktree):
    """默认验证：在 worktree 里跑全量测试（含评测台）。"""
    try:
        p = subprocess.run(
            [os.sys.executable, "run_all_tests.py", "--evals"],
            cwd=worktree, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=3600)
        tail = (p.stdout or "")[-400:]
        return p.returncode == 0, tail
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def watch(root, failures, *, threshold=3, cfg_getter=None, now=None,
          dry_run=True, patcher=None, runner=None, hours=24) -> dict:
    """观察模式：把 failure_top 的输出接进 R3，决定"该不该修"。

    failures: `trace_tools.failure_top()` 的返回 —— [{channel, count, samples}]
    返回 {"candidates": [...], "acted": [...], "skipped": [...]}

    默认 dry_run=True：只记账、不建 worktree。
    真修需要 patcher（模型产补丁）；没有 patcher 时即使 dry_run=False 也只会记账 ——
    这一层永远不假装"修过了"。
    """
    ts = _now(now)
    cfg = {}
    if cfg_getter:
        try:
            cfg = dict(cfg_getter() or {})
        except Exception:
            cfg = {}
    if not cfg.get("self_heal_r3_enable", False):
        return {"skipped": [{"reason": "self_heal_r3_enable 关闭"}], "candidates": [], "acted": []}

    out = {"candidates": [], "acted": [], "skipped": []}
    for f in (failures or []):
        ch = str((f or {}).get("channel") or "").strip()
        cnt = int((f or {}).get("count") or 0)
        if not ch:
            continue
        if cnt < int(threshold or 3):
            out["skipped"].append({"issue": ch, "reason": f"次数未达阈值（{cnt} < {threshold}）"})
            continue
        # 熔断优先：连续失败过的问题别再碰
        blocked, why = breaker_blocked(root, ch, now=ts)
        if blocked:
            out["skipped"].append({"issue": ch, "reason": why})
            continue
        out["candidates"].append({"issue": ch, "count": cnt,
                                  "sample": ((f or {}).get("samples") or [""])[0][:120]})
        if dry_run or patcher is None:
            append_ledger(root, {
                "ts": ts, "kind": "code", "issue": ch, "ok": False,
                "reason": ("观察模式：命中候选但未自动修" if dry_run
                           else "缺少 patcher：只记账，不修"),
                "opens": cnt,
            })
            out["acted"].append({"issue": ch, "dry": True})
            continue
        r = run(ch, patcher, root=root, runner=runner, cfg_getter=cfg_getter, now=now)
        out["acted"].append({"issue": ch, "ok": bool(r.get("ok")), "reason": r.get("reason", "")})
    return out


def status(root) -> dict:
    """给 WebUI/诊断用：开关、最近账本、熔断状态。"""
    led = read_ledger(root)
    fails = [r for r in led if not r.get("ok")]
    return {
        "ledger": ledger_path(root),
        "total": len(led),
        "failed": len(fails),
        "recent": led[-5:],
    }
