# -*- coding: utf-8 -*-
"""tests/test_self_heal_r3_patcher.py —— 自愈 R3 补上的 patcher 段（脚本式）

锁的是这几件事（全程临时目录 + monkeypatch，不碰本仓库、不 commit 到本仓库）：

  A. 注入点 set_patcher/get_patcher；安全默认（dry_run=True / 开关关 / 绝不 push /
     不调模型或网络）
  B. 可修失败 → 基于 failure_top()/log_search() 归因到具体文件与行 →
     生成**最小 diff** 且通过 check_patch；确定性、可复现、离线
  C. 白名单外路径被拒（归因层 + check_patch 层都不许碰）
  D. 归因不到 → 明确不修、不写文件，watch 走 run 后只记账、主仓库不动
  E. dry_run=True / 无 patcher 时绝不真修（保留原有安全默认）
  F. 连续 2 次失败触发 30 天熔断
  G. repairs.jsonl 新旧台账行都能读，两处写入共用统一字段

运行：python tests/test_self_heal_r3_patcher.py
"""
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import self_heal_r3 as R3  # noqa: E402
from harness import trace_tools as TT  # noqa: E402

PASS = []
FAIL = []
FAILURES = []

EN = {"self_heal_r3_enable": True}
NOW = 1793694000.0

#: 测试用的静态归因表（monkeypatch 掉默认的，保证离线可复现）
TEST_MAP = {
    "工具调用": ("harness/fixture_r3.py", r"^def buggy", r"\[工具\]|py_run"),
}
OUT_MAP = {
    "工具调用": ("webui/webui_server.py", r"^def app", r"\[工具\]"),
}
FIXTURE = "# fixture\ndef buggy():\n    return 1\n"


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


def _ts(hours_ago):
    return datetime.fromtimestamp(time.time() - hours_ago * 3600).strftime(
        "%Y-%m-%d %H:%M:%S")


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def make_log(path):
    """造一份本地日志：工具调用链路反复失败（够到 watch 默认阈值 3）。"""
    lines = [
        f"[{_ts(1)}] [工具] py_run 失败: timeout trace=aaaaaaaaaaaa",
        f"[{_ts(1.2)}] [工具] tool_x 异常: boom",
        f"[{_ts(1.5)}] [工具] job_y 失败: retcode=3",
        f"[{_ts(2)}] [工具] web_operate 超时",
        f"[{_ts(3)}] [联网搜索] search 失败: 503",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def make_repo():
    """临时 git 仓库（已提交一个白名单内 fixture 文件，供 worktree 检出）。"""
    d = tempfile.mkdtemp(prefix="r3p_repo_")
    _git(d, "init")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    os.makedirs(os.path.join(d, "harness"))
    with open(os.path.join(d, "harness", "fixture_r3.py"), "w", encoding="utf-8") as f:
        f.write(FIXTURE)
    _git(d, "add", "-A")
    _git(d, "commit", "-m", "init")
    return d


def diff_for(path):
    return (f"diff --git a/{path} b/{path}\n"
            f"--- a/{path}\n+++ b/{path}\n@@ -1 +1,2 @@\n+added\n")


def main():
    orig_map = R3.CHANNEL_MAP
    orig_repairs = TT.REPAIRS_PATH
    orig_patcher = R3.get_patcher()
    tmp_dirs = []
    try:
        print("[A] 注入点 / 安全默认")
        import config
        check("get_patcher() 默认 None（没有默认启用的 patcher）",
              R3.get_patcher() is None, R3.get_patcher())
        sentinel = lambda wt: ""  # noqa: E731
        check("set_patcher/get_patcher 往返",
              R3.set_patcher(sentinel) is sentinel and R3.get_patcher() is sentinel)
        R3.set_patcher(None)
        check("default_patcher 可调用", callable(R3.default_patcher()))
        check("watch 默认 dry_run=True",
              inspect.signature(R3.watch).parameters["dry_run"].default is True)
        check("开关 self_heal_r3_enable 默认关",
              config.DEFAULT_CONFIG.get("self_heal_r3_enable") is False)
        src = open(os.path.join(ROOT, "harness", "self_heal_r3.py"),
                   encoding="utf-8").read()
        check("★ 源码无模型/网络客户端调用",
              not re.search(r"requests\.|urllib|openai|http://|https://|socket\.", src))
        check("★ 源码无 git push（绝不 push）",
              '"push"' not in src and "git push" not in src)

        print("\n[B] 可修失败 → 最小 diff 且通过 check_patch")
        tmp = tempfile.mkdtemp(prefix="r3p_B_")
        tmp_dirs.append(tmp)
        wt = os.path.join(tmp, "wt")
        os.makedirs(os.path.join(wt, "harness"))
        with open(os.path.join(wt, "harness", "fixture_r3.py"), "w", encoding="utf-8") as f:
            f.write(FIXTURE)
        log = os.path.join(tmp, "qq_bot.log")
        make_log(log)
        R3.CHANNEL_MAP = dict(TEST_MAP)
        failures = TT.failure_top(hours=24, n=5, path=log)
        check("failure_top 取到候选", bool(failures) and failures[0]["channel"] == "工具调用", failures)

        attrs = R3.attribute_failure(failures, log_path=log)
        a0 = attrs[0] if attrs else {}
        check("★ 归因到具体文件", a0.get("file") == "harness/fixture_r3.py", a0)
        check("★ 有 log_search 佐证", len(a0.get("evidence") or []) >= 1, a0.get("evidence"))
        check("★ 归因可修", a0.get("fixable") is True, a0)

        plan = R3.plan_patch(wt, failures, log_path=log)
        check("★ 定位到具体行号", plan.get("line") == 2, plan)
        check("★ 生成前说明归因依据",
              "归因依据" in plan.get("reason", "") and "harness/fixture_r3.py" in plan.get("reason", ""),
              plan.get("reason"))

        patcher = R3.default_patcher(failures, log_path=log)
        diff = patcher(wt)
        check("★ patcher 产出非空 diff", bool(diff), diff[:160])
        ok, why = R3.check_patch(diff)
        check("★ 最小 diff 通过 check_patch", ok is True, why)
        files = R3.parse_diff_files(diff)
        total = sum(v["added"] + v["removed"] for v in files.values())
        check("★ 只改 1 文件 / 1 行", len(files) == 1 and total == 1, files)
        with open(os.path.join(wt, "harness", "fixture_r3.py"), encoding="utf-8") as f:
            content = f.read()
        check("★ 真的把最小改动写进 worktree 文件", "# R3 selfheal guard" in content, content)
        check("explain() 能说明归因依据",
              "harness/fixture_r3.py" in patcher.explain(), patcher.explain())

        wt2 = os.path.join(tmp, "wt2")
        os.makedirs(os.path.join(wt2, "harness"))
        with open(os.path.join(wt2, "harness", "fixture_r3.py"), "w", encoding="utf-8") as f:
            f.write(FIXTURE)
        diff2 = R3.default_patcher(failures, log_path=log)(wt2)
        check("★ 确定性/可复现：同样输入得到同样 diff", diff2 == diff, (diff[:80], diff2[:80]))
        p_off = R3.default_patcher(None, log_path=os.path.join(tmp, "nope.log"))
        check("★ 离线（无日志）也不崩、不瞎改", p_off(wt2) == "", p_off.skip_reason)

        print("\n[C] 白名单外路径被拒")
        R3.CHANNEL_MAP = dict(OUT_MAP)
        os.makedirs(os.path.join(wt, "webui"), exist_ok=True)
        outside = "def app():\n    pass\n"
        with open(os.path.join(wt, "webui", "webui_server.py"), "w", encoding="utf-8") as f:
            f.write(outside)
        attrs = R3.attribute_failure(failures, log_path=log)
        check("★ 白名单外归因标记不可修", bool(attrs) and attrs[0]["fixable"] is False, attrs)
        check("★ 原因写明白名单", "白名单" in (attrs[0]["reason"] if attrs else ""), attrs)
        plan = R3.plan_patch(wt, failures, log_path=log)
        check("★ 白名单外不产出 diff", plan["ok"] is False and plan["diff"] == "", plan)
        d = R3.default_patcher(failures, log_path=log)(wt)
        check("★ 白名单外 patcher 返回不修", d == "", d)
        with open(os.path.join(wt, "webui", "webui_server.py"), encoding="utf-8") as f:
            check("★ 白名单外文件一个字没动", f.read() == outside)
        ok, why = R3.check_patch(diff_for("webui/webui_server.py"))
        check("★ check_patch 也拒绝白名单外路径", ok is False and "白名单" in why, why)
        R3.CHANNEL_MAP = dict(TEST_MAP)

        print("\n[D] 归因不到 → 不修只记账")
        repo_d = make_repo()
        tmp_dirs.append(repo_d)
        unknown = [{"channel": "其他", "count": 9, "samples": ["mystery boom"]}]
        p3 = R3.default_patcher(unknown, log_path=log)
        probe_wt = os.path.join(tmp, "probe_wt")
        os.makedirs(probe_wt, exist_ok=True)
        check("★ 归因不到 patcher 返回空（明确不修）", p3(probe_wt) == "", p3.skip_reason)
        check("★ 明确说明不修原因",
              ("不修" in p3.skip_reason or "归因" in p3.skip_reason), p3.skip_reason)
        head0 = _git(repo_d, "rev-parse", "--short", "HEAD").stdout.strip()
        r = R3.watch(repo_d, unknown, threshold=3, cfg_getter=lambda: EN,
                     now=NOW, dry_run=False, patcher=p3,
                     runner=lambda wt: (True, "green"))
        check("★ watch 走了 run（非 dry、显式 patcher）",
              any(a.get("issue") == "其他" for a in r["acted"]), r)
        led = [x for x in R3.read_ledger(repo_d) if x.get("issue") == "其他"]
        check("★ 不修也记了账（ok:false）", bool(led) and led[-1]["ok"] is False, led)
        check("★ 账本写明归因不到/不修",
              any(k in (str(led[-1].get("reason", "")) + str(led[-1].get("detail", "")))
                  for k in ("归因", "不修")) if led else False, led[-1:] if led else [])
        check("★ 主仓库没有新提交",
              _git(repo_d, "rev-parse", "--short", "HEAD").stdout.strip() == head0)

        print("\n[E] 安全默认：dry_run=True / 无 patcher 时绝不真修")
        repo_e = make_repo()
        tmp_dirs.append(repo_e)
        R3.set_patcher(R3.default_patcher(failures, log_path=log))
        head0 = _git(repo_e, "rev-parse", "--short", "HEAD").stdout.strip()
        r = R3.watch(repo_e, failures, cfg_getter=lambda: EN, now=NOW, dry_run=True)
        check("★ dry_run=True 即使注入了 patcher 也只记账",
              bool(r["acted"]) and r["acted"][0].get("dry") is True, r)
        check("★ 没有新提交",
              _git(repo_e, "rev-parse", "--short", "HEAD").stdout.strip() == head0)
        R3.set_patcher(None)
        r = R3.watch(repo_e, failures, cfg_getter=lambda: EN, now=NOW + 60, dry_run=False)
        check("★ 无 patcher 时 dry_run=False 也只记账",
              bool(r["acted"]) and r["acted"][0].get("dry") is True, r)
        led = [x for x in R3.read_ledger(repo_e) if x.get("issue") == "工具调用"]
        check("★ 原因写明缺少 patcher",
              "缺少 patcher" in str(led[-1].get("reason", "")) if led else False, led[-1:])

        print("\n[F] 连续 2 次失败触发 30 天熔断")
        repo_f = make_repo()
        tmp_dirs.append(repo_f)
        R3.set_patcher(R3.default_patcher(failures, log_path=log))
        red = lambda wt: (False, "2 failed")  # noqa: E731
        r1 = R3.watch(repo_f, failures, cfg_getter=lambda: EN, now=NOW,
                      dry_run=False, runner=red)
        r2 = R3.watch(repo_f, failures, cfg_getter=lambda: EN, now=NOW + 10,
                      dry_run=False, runner=red)
        check("两次都实际走了 run 且都失败",
              len(r1["acted"]) == 1 and len(r2["acted"]) == 1
              and r1["acted"][0].get("ok") is False and r2["acted"][0].get("ok") is False,
              (r1["acted"], r2["acted"]))
        r3 = R3.watch(repo_f, failures, cfg_getter=lambda: EN, now=NOW + 20,
                      dry_run=False, runner=lambda wt: (True, "green"))
        check("★ 第三次被熔断挡下",
              not r3["candidates"] and
              any("熔断" in str(s.get("reason", "")) for s in r3["skipped"]), r3)
        check("★ 熔断后没再走 run", not r3["acted"], r3["acted"])
        r4 = R3.watch(repo_f, failures, cfg_getter=lambda: EN,
                      now=NOW + 31 * 86400, dry_run=True)
        check("★ 30 天后解封（重新进候选）",
              any(c.get("issue") == "工具调用" for c in r4["candidates"]), r4)
        R3.set_patcher(None)

        print("\n[G] 台账 schema 统一 / 新旧行都能读")
        tmp_g = tempfile.mkdtemp(prefix="r3p_G_")
        tmp_dirs.append(tmp_g)
        ledger = os.path.join(tmp_g, "data", "repairs.jsonl")
        os.makedirs(os.path.dirname(ledger), exist_ok=True)
        t_now = time.time()
        old_rr = {"ts": t_now - 5, "time": "2026-11-01 00:00:00",
                  "kind": "config", "target": "eval_enabled", "action": "改回 true",
                  "ok": True, "detail": "手滑关掉了"}
        old_al = {"ts": t_now - 4, "kind": "code", "issue": "工具调用", "ok": False,
                  "sha": "abc123", "reason": "验证未通过", "review": "通过：1/1", "verify": "red"}
        with open(ledger, "w", encoding="utf-8") as f:
            f.write(json.dumps(old_rr, ensure_ascii=False) + "\n")
            f.write(json.dumps(old_al, ensure_ascii=False) + "\n")
        UF = set(TT.UNIFIED_FIELDS)
        rows = R3.read_ledger(tmp_g)
        check("★ read_ledger 读到 2 条旧行", len(rows) == 2, rows)
        check("★ 旧行都补齐统一字段", all(UF <= set(x) for x in rows), rows)
        rr = next((x for x in rows if x.get("action") == "改回 true"), {})
        al = next((x for x in rows if x.get("sha") == "abc123"), {})
        check("★ 旧行缺 issue → 从 target 补", rr.get("issue") == "eval_enabled", rr)
        check("★ 旧行缺 target/detail → 从 issue/reason 补",
              al.get("target") == "工具调用" and al.get("detail") == "验证未通过", al)
        check("★ 旧行额外字段不丢",
              rr.get("action") == "改回 true" and al.get("review") == "通过：1/1", (rr, al))
        TT.REPAIRS_PATH = ledger
        rows2 = TT.list_repairs(hours=10 ** 9)
        check("★ list_repairs 也能读 R3 写的旧行",
              len(rows2) == 2 and all(UF <= set(x) for x in rows2), rows2)
        R3.append_ledger(tmp_g, {"ts": t_now - 3, "kind": "code", "issue": "新A",
                                 "ok": False, "reason": "观察模式", "opens": 9})
        TT.record_repair("cache", "新B", "清缓存", True, "ok")
        rows3 = R3.read_ledger(tmp_g)
        na = next((x for x in rows3 if x.get("issue") == "新A"), {})
        nb = next((x for x in rows3 if x.get("issue") == "新B"), {})
        check("★ append_ledger 新行统一字段齐全", UF <= set(na), na)
        check("★ record_repair 新行统一字段齐全", UF <= set(nb), nb)
        check("★ append_ledger 补了 target/detail 且保留 opens/reason",
              na.get("target") == "新A" and na.get("detail") == "观察模式"
              and na.get("opens") == 9 and na.get("reason") == "观察模式", na)
        check("★ record_repair 补了 issue 且保留 action/target",
              nb.get("issue") == "新B" and nb.get("target") == "新B"
              and nb.get("action") == "清缓存", nb)
        tmp_b = tempfile.mkdtemp(prefix="r3p_BRK_")
        tmp_dirs.append(tmp_b)
        R3.append_ledger(tmp_b, {"ts": t_now - 2, "kind": "code", "issue": "bj", "ok": False})
        R3.append_ledger(tmp_b, {"ts": t_now - 1, "kind": "code", "issue": "bj", "ok": False})
        blocked, why = R3.breaker_blocked(tmp_b, "bj", now=t_now)
        check("★ normalize 后熔断仍生效", blocked is True and "熔断" in why, why)
    except Exception as e:
        import traceback
        check("脚本执行未抛异常", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        R3.CHANNEL_MAP = orig_map
        TT.REPAIRS_PATH = orig_repairs
        R3.set_patcher(orig_patcher)
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    for n in FAIL:
        print(f"  FAIL  {n}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
