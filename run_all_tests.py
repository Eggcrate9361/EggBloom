# -*- coding: utf-8 -*-
"""run_all_tests.py —— 全量测试 + 评测台，一个入口（FIXPLAN 1.5.11 P0-1 前置）

    python run_all_tests.py                 # 全量，默认每条 180s 超时
    python run_all_tests.py --evals         # 顺带跑评测台（40 场机械判分）
    python run_all_tests.py --filter memory # 只跑名字含 memory 的
    python run_all_tests.py --json          # 机器可读（CI 用）
    python run_all_tests.py --no-known-red  # 已知红也算红（发版前核实用）

## 为什么要有它

1.5.10 之前是"一条条手动跑 + 肉眼记哪些红"，于是交接文档里写着"3 个长期红"，
实际一跑发现 7 个 —— 名单和事实对不上，门槛形同虚设。

本文件的判据是**真跑**，不是抄名单：
  · 期望全绿：`tests/test_*.py` 全部退出码 0 +（可选）评测台 40/40
  · 已知红：写在 `tests/known_red.json` 里，必须带原因；**它变绿了会被点名**
    （说明问题修好了，该从名单删掉，避免名单变成"永远豁免"）

退出码：0 = 除已知红外全绿；1 = 有意外失败。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(ROOT, "tests")
KNOWN_RED_FILE = os.path.join(TESTS_DIR, "known_red.json")
FLAKY_FILE = os.path.join(TESTS_DIR, "known_flaky.json")
FAIL_DIR = os.path.join(TESTS_DIR, "_last_fail")
DEFAULT_TIMEOUT = 180

#: 子进程统一走 UTF-8。Windows 控制台默认 cp936，父进程按 UTF-8 解码会拿到
#: 一屏乱码 + `\ufffd`，严重时 `print` 直接 UnicodeEncodeError 把整个入口打断
#: （2026-11-01 实测：5 个用例红的结果没打出来，反而死在最后一行汇总上）。
CHILD_ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")


def _safe_console():
    """把本进程的 stdout/stderr 切成 UTF-8，任何控制台都不会因为转义炸掉。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_known_red():
    try:
        with open(KNOWN_RED_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}


def load_known_flaky():
    """整跑时可以自动重试一次的用例（判据：今天整跑里**实际**抖过）。

    为什么要有：2026-11-01 三次整跑红了 5 个 / 1 个 / 1 个，名单还不一样，
    每次都靠"单独再跑一遍"才能确认是抖动还是真红 —— 人力白花，判断还容易错。
    为什么只列名单不全局重试：全局重试会把真红的发现时间翻倍，还会让人习惯性
    无视 FAIL。这里重跑通过会**单列「抖动」并打出首次退出码**，不静默吞红。
    """
    try:
        with open(FLAKY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}


def discover(pattern=""):
    files = sorted(x for x in os.listdir(TESTS_DIR)
                   if x.startswith("test_") and x.endswith(".py"))
    if pattern:
        p = pattern.lower()
        files = [x for x in files if p in x.lower()]
    return [os.path.join(TESTS_DIR, x) for x in files]


def _dump_failure(name, stdout, stderr, code):
    """失败现场留档：只留 3 行 tail 是查不出抖动的（2026-11-01 教训）。"""
    try:
        os.makedirs(FAIL_DIR, exist_ok=True)
        path = os.path.join(FAIL_DIR, name.replace(".py", "") + ".log")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"exit={code}\n{'=' * 60}\n")
            f.write("--- stdout ---\n" + (stdout or "") + "\n")
            if stderr:
                f.write("--- stderr ---\n" + stderr + "\n")
        return os.path.relpath(path, ROOT)
    except Exception:
        return ""


def run_one(path, timeout):
    t0 = time.time()
    # 每个用例一个**临时记忆库**（2026-11-01）：动作账本现在会写 super_memory 的
    # events 表，跑一遍测试就把假事件写进生产记忆（实测 6 条）。这里给每个用例
    # 一个独立库，谁也别碰真的。
    tmp = tempfile.mkdtemp(prefix="eggsandbox_")
    env = dict(CHILD_ENV,
               EGGBLOOM_SUPER_MEMORY_DB=os.path.join(tmp, "super_memory.db"),
               EGGBLOOM_PLUGIN_BUDGET=os.path.join(tmp, "plugin_budget.json"),
               EGGBLOOM_ALLOW_ACTION_EVENTS="1")
    try:
        r = subprocess.run([sys.executable, path], cwd=ROOT, timeout=timeout,
                           capture_output=True, text=True, env=env,
                           encoding="utf-8", errors="replace")
        cost = time.time() - t0
        tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()][-3:]
        res = {"ok": r.returncode == 0, "code": r.returncode, "secs": round(cost, 1),
               "tail": tail}
        if not res["ok"]:
            res["log"] = _dump_failure(os.path.basename(path), r.stdout, r.stderr,
                                       r.returncode)
            # 失败行里 often 就是判据本身（[FAIL] xxx :: 实际值），单独拎出来
            res["fails"] = [ln.strip() for ln in (r.stdout or "").splitlines()
                            if "[FAIL]" in ln][:6]
        return res
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "TIMEOUT", "secs": round(time.time() - t0, 1),
                "tail": [f"超过 {timeout}s 未结束"], "fails": []}


def run_evals():
    """跑 evals/ 下所有机械用例：reply 判据 + 自主性策略层。"""
    scripts = [("reply", os.path.join(ROOT, "evals", "run_eval.py")),
               ("autonomy", os.path.join(ROOT, "evals", "autonomy_eval.py"))]
    lines, ok_all, ran = [], True, 0
    for name, path in scripts:
        if not os.path.exists(path):
            continue
        ran += 1
        r = subprocess.run([sys.executable, path], cwd=ROOT, env=CHILD_ENV,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        line = ""
        for ln in (r.stdout or "").splitlines():
            if ln.startswith("RESULT:"):
                line = ln.strip()
        line = line or (f"{name}: " + (r.stdout or r.stderr or "").strip()[-120:])
        lines.append(line)
        ok_all = ok_all and (r.returncode == 0)
    if not ran:
        return None
    return {"ok": ok_all, "lines": lines, "line": " · ".join(lines)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="EggBloom 全量测试 + 评测台")
    ap.add_argument("--filter", default="", help="只跑文件名含该子串的用例")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--evals", action="store_true", help="顺带跑评测台")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-known-red", action="store_true",
                    help="已知红也当失败（发版前核实）")
    ap.add_argument("--no-retry", action="store_true",
                    help="抖动用例也不重试（发版前核实：看原始结果）")
    args = ap.parse_args(argv)
    _safe_console()
    # 清掉上一轮的失败现场，避免拿旧日志当新证据
    try:
        for fn in os.listdir(FAIL_DIR):
            if fn.endswith(".log"):
                os.remove(os.path.join(FAIL_DIR, fn))
    except Exception:
        pass

    known = {} if args.no_known_red else load_known_red()
    flaky = {} if (args.no_retry or args.no_known_red) else load_known_flaky()
    files = discover(args.filter)
    if not files:
        print(f"没有匹配的用例（--filter {args.filter!r}）", file=sys.stderr)
        return 2
    results = []

    for i, path in enumerate(files, 1):
        name = os.path.basename(path)
        sys.stdout.write(f"[{i}/{len(files)}] {name} ... ")
        sys.stdout.flush()
        res = run_one(path, args.timeout)
        res["name"] = name
        res["known_red"] = name in known
        # 抖动用例：红了**当场重跑一次**，通过了就标「抖动」而不是红
        if not res["ok"] and not res["known_red"] and name in flaky:
            first_code = res.get("code")
            retry = run_one(path, args.timeout)
            retry["name"] = name
            retry["known_red"] = False
            if retry["ok"]:
                retry["flaky"] = True
                retry["first_code"] = first_code
                retry["note"] = flaky.get(name, "")
                res = retry
            else:
                res = retry
                res["first_code"] = first_code
        results.append(res)
        if res["ok"]:
            mark = "FLAKY→OK" if res.get("flaky") else "OK"
        elif res["known_red"]:
            mark = "KNOWN-RED"
        else:
            mark = "FAIL"
        print(f"{mark} ({res['secs']}s)")

    evals = run_evals() if args.evals else None

    hard_fail = [r for r in results if not r["ok"] and not r["known_red"]]
    expected_red = [r for r in results if not r["ok"] and r["known_red"]]
    fixed_red = [r for r in results if r["ok"] and r["known_red"]]
    flaked = [r for r in results if r["ok"] and r.get("flaky")]

    if args.json:
        print(json.dumps({"total": len(results), "failed": [r["name"] for r in hard_fail],
                          "known_red": [r["name"] for r in expected_red],
                          "fixed_known_red": [r["name"] for r in fixed_red],
                          "flaky": [{"name": r["name"], "first_code": r.get("first_code")}
                                    for r in flaked],
                          "evals": evals}, ensure_ascii=False, indent=2))
    else:
        print("\n" + "=" * 60)
        print(f"共 {len(results)} 个用例 | 通过 {len(results) - len(hard_fail) - len(expected_red)} "
              f"| 意外失败 {len(hard_fail)} | 已知红 {len(expected_red)}"
              + (f" | 抖动重跑通过 {len(flaked)}" if flaked else ""))
        if flaked:
            print("\n抖动（首次红、当场重跑通过 —— 不是真红，但记一笔）：")
            for r in flaked:
                print(f"  - {r['name']}（首次 exit={r.get('first_code')}）"
                      f" —— {r.get('note', '')}")
        if hard_fail:
            print("\n意外失败（必须修）：")
            for r in hard_fail:
                print(f"  - {r['name']}（exit={r['code']}）")
                for ln in (r.get("fails") or [])[:3]:
                    print(f"      {ln}")
                if r.get("log"):
                    print(f"      ↳ 完整输出：{r['log']}")
        if expected_red:
            print("\n已知红（tests/known_red.json 里登记过）：")
            for r in expected_red:
                print(f"  - {r['name']} —— {known.get(r['name'], '')}")
        if fixed_red:
            print("\n★ 这些已知红已经绿了，请从 known_red.json 删掉：")
            for r in fixed_red:
                print(f"  - {r['name']}")
        if evals:
            print("\n评测台：" + evals["line"])
        print("\n" + ("结果：通过" if not hard_fail else "结果：有意外失败"))

    ok = not hard_fail and (evals is None or evals["ok"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
