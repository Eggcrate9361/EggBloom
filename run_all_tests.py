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
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(ROOT, "tests")
KNOWN_RED_FILE = os.path.join(TESTS_DIR, "known_red.json")
DEFAULT_TIMEOUT = 180


def load_known_red():
    try:
        with open(KNOWN_RED_FILE, encoding="utf-8") as f:
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


def run_one(path, timeout):
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, path], cwd=ROOT, timeout=timeout,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        cost = time.time() - t0
        tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()][-3:]
        return {"ok": r.returncode == 0, "code": r.returncode, "secs": round(cost, 1),
                "tail": tail}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "TIMEOUT", "secs": round(time.time() - t0, 1),
                "tail": [f"超过 {timeout}s 未结束"]}


def run_evals():
    """跑 evals/ 下所有机械用例：reply 判据 + 自主性策略层。"""
    scripts = [("reply", os.path.join(ROOT, "evals", "run_eval.py")),
               ("autonomy", os.path.join(ROOT, "evals", "autonomy_eval.py"))]
    lines, ok_all, ran = [], True, 0
    for name, path in scripts:
        if not os.path.exists(path):
            continue
        ran += 1
        r = subprocess.run([sys.executable, path], cwd=ROOT,
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
    args = ap.parse_args(argv)

    known = {} if args.no_known_red else load_known_red()
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
        results.append(res)
        if res["ok"]:
            mark = "OK"
        elif res["known_red"]:
            mark = "KNOWN-RED"
        else:
            mark = "FAIL"
        print(f"{mark} ({res['secs']}s)")

    evals = run_evals() if args.evals else None

    hard_fail = [r for r in results if not r["ok"] and not r["known_red"]]
    expected_red = [r for r in results if not r["ok"] and r["known_red"]]
    fixed_red = [r for r in results if r["ok"] and r["known_red"]]

    if args.json:
        print(json.dumps({"total": len(results), "failed": [r["name"] for r in hard_fail],
                          "known_red": [r["name"] for r in expected_red],
                          "fixed_known_red": [r["name"] for r in fixed_red],
                          "evals": evals}, ensure_ascii=False, indent=2))
    else:
        print("\n" + "=" * 60)
        print(f"共 {len(results)} 个用例 | 通过 {len(results) - len(hard_fail) - len(expected_red)} "
              f"| 意外失败 {len(hard_fail)} | 已知红 {len(expected_red)}")
        if hard_fail:
            print("\n意外失败（必须修）：")
            for r in hard_fail:
                print(f"  - {r['name']}（exit={r['code']}）")
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
