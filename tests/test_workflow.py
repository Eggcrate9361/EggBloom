# -*- coding: utf-8 -*-
"""harness/workflow.py 单测（1.6.0 DAG 工作流）。

钉死：define / start / resume / status / cancel；节点依赖 DAG、
条件分支、同层并行、步骤级重试与回退、__final__ 提前终止、
流程定义与运行态落盘、**跨会话凭 run_id 恢复**（模拟新进程 + 真子进程读盘）。

不联网、不碰真实模型/ffmpeg；只用临时目录与极短线程/子进程。
运行：python tests/test_workflow.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import workflow as wf  # noqa: E402

TMP = tempfile.mkdtemp(prefix="workflow_test_")
SD = os.path.join(TMP, "wf")           # 本次所有流程定义/运行态都落这里

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


try:
    # ══ A. define + 线性 DAG + __final__ 提前终止 ═══════════════════
    def a(ctx):
        ctx["a"] = 1
        return "A"

    def b(ctx):
        ctx["b"] = 2
        return "B"

    def join(ctx):
        ctx["__final__"] = {"ok": True, "v": ctx["a"] + ctx["b"]}
        return "JOIN"

    def downstream(ctx):
        ctx["downstream"] = True
        return "D"

    defn = wf.define("linear", [
        {"id": "a", "run": a},
        {"id": "b", "run": b},
        {"id": "join", "depends": ["a", "b"], "run": join},
        {"id": "downstream", "depends": ["join"], "run": downstream},
    ], store_dir=SD)
    check("define 返回可序列化定义", isinstance(defn, dict) and defn["name"] == "linear"
          and all(n["has_run"] for n in defn["nodes"]))
    check("定义落盘 defs/<name>.json",
          os.path.isfile(os.path.join(SD, "defs", "linear.json")))

    st = wf.start("linear", context={}, run_id="run-linear", store_dir=SD)
    check("start 返回运行态含 run_id", st["run_id"] == "run-linear")
    check("线性 DAG 跑完 done", st["status"] == wf.RUN_DONE, st["status"])
    check("result 取 __final__", st["result"] == {"ok": True, "v": 3}, st["result"])
    check("a/b/join 都 done", all(st["steps"][k]["status"] == wf.ST_DONE
                                 for k in ("a", "b", "join")))
    check("★ __final__ 后下游被 skipped",
          st["steps"]["downstream"]["status"] == wf.ST_SKIPPED
          and "downstream" not in st["context"])

    # ══ B. 同层无依赖节点并行（用 Barrier 确定性验证）════════════════
    barrier = threading.Barrier(2)

    def left(ctx):
        barrier.wait(timeout=3)       # 串行执行会在这里超时抛错 → 判失败
        ctx["left"] = True
        return "L"

    def right(ctx):
        barrier.wait(timeout=3)
        ctx["right"] = True
        return "R"

    wf.define("parallel", [
        {"id": "left", "run": left},
        {"id": "right", "run": right},
    ], store_dir=SD)
    pst = wf.start("parallel", store_dir=SD)
    check("★ 并行：两个节点都 done（Barrier 通过）",
          pst["steps"]["left"]["status"] == wf.ST_DONE
          and pst["steps"]["right"]["status"] == wf.ST_DONE,
          {k: v["status"] for k, v in pst["steps"].items()})
    check("并行节点都写了 ctx", pst["context"].get("left") and pst["context"].get("right"))

    # ══ C. 条件分支：when 为假 → skipped ═══════════════════════════
    def yes(ctx):
        ctx["yes"] = True
        return "Y"

    def no(ctx):
        ctx["no"] = True
        return "N"

    wf.define("branch", [
        {"id": "root", "run": lambda ctx: "ROOT"},
        {"id": "yes", "depends": ["root"], "when": lambda c: True, "run": yes},
        {"id": "no", "depends": ["root"], "when": lambda c: False, "run": no},
    ], store_dir=SD)
    bst = wf.start("branch", store_dir=SD)
    check("条件为真 → done", bst["steps"]["yes"]["status"] == wf.ST_DONE)
    check("★ 条件为假 → skipped", bst["steps"]["no"]["status"] == wf.ST_SKIPPED
          and bst["steps"]["no"]["skip_reason"] == "条件分支未命中")
    check("条件假分支没执行", "no" not in bst["context"] and bst["context"].get("yes") is True)

    # ══ D. 步骤级重试 ════════════════════════════════════════════
    counter = {"n": 0}

    def flaky(ctx):
        counter["n"] += 1
        if counter["n"] < 3:
            raise RuntimeError("还没好")
        return "OK"

    wf.define("retry", [{"id": "flaky", "run": flaky, "retries": 2}], store_dir=SD)
    rst = wf.start("retry", store_dir=SD)
    check("★ 重试 3 次后成功", rst["steps"]["flaky"]["status"] == wf.ST_DONE
          and rst["steps"]["flaky"]["attempts"] == 3, rst["steps"]["flaky"])

    # ══ E. 步骤级回退（fallback）═══════════════════════════════════
    def always_fail(ctx):
        raise RuntimeError("boom")

    def cleanup(ctx):
        ctx["cleaned"] = True
        return "CLEAN"

    wf.define("fallback", [
        {"id": "bad", "run": always_fail, "fallback": "cleanup"},
        {"id": "cleanup", "run": cleanup},
    ], store_dir=SD)
    fst = wf.start("fallback", store_dir=SD)
    check("★ 失败节点触发回退节点", fst["steps"]["cleanup"]["status"] == wf.ST_DONE)
    check("回退来源被记录", fst["context"].get("__fallback_from__") == "bad")
    check("回退节点写了 ctx", fst["context"].get("cleaned") is True)
    check("失败节点仍是 failed（整个流程判 failed）",
          fst["steps"]["bad"]["status"] == wf.ST_FAILED and fst["status"] == wf.RUN_FAILED)

    # ══ F. 运行态落盘：status(run_id) 只读盘 + 真子进程读 ═════════════
    from_disk = wf.status("run-linear", store_dir=SD)
    check("status 凭 run_id 读回运行态", from_disk and from_disk["status"] == wf.RUN_DONE)
    check("运行态落在 runs/<run_id>.json",
          os.path.isfile(os.path.join(SD, "runs", "run-linear.json")))
    child_code = (
        "import sys\n"
        f"sys.path.insert(0, {ROOT!r})\n"
        "from harness import workflow as w\n"
        f"d = w.status('run-linear', store_dir={SD!r})\n"
        "print(d['status'] if d else 'NONE')\n"
    )
    cp = subprocess.run([sys.executable, "-c", child_code], cwd=ROOT,
                        capture_output=True, text=True, timeout=30)
    check("★ 另一个 OS 进程也读得到运行态", cp.stdout.strip() == "done", cp.stdout.strip() or cp.stderr[:160])

    # ══ G. resume：从 failed 恢复，已 done 节点不重跑 ═════════════════
    calls = {"a": 0}

    def count_a(ctx):
        calls["a"] += 1
        return "A"

    def bad_b(ctx):
        raise RuntimeError("b 崩了")

    wf.define("recoverable", [
        {"id": "s1", "run": count_a},
        {"id": "s2", "depends": ["s1"], "run": bad_b},
    ], store_dir=SD)
    gst = wf.start("recoverable", context={}, run_id="run-recover", store_dir=SD)
    check("恢复前是 failed", gst["status"] == wf.RUN_FAILED)
    fixed = wf.resume("run-recover", store_dir=SD, handlers={"s2": lambda c: "FIXED"})
    check("★ resume 后 done", fixed and fixed["status"] == wf.RUN_DONE, fixed and fixed["status"])
    check("★ s2 重跑成 done", fixed["steps"]["s2"]["status"] == wf.ST_DONE)
    check("★ 已 done 的 s1 没被重跑", calls["a"] == 1, calls)

    # ══ H. 跨会话恢复：模拟新进程（清注册表）后凭 run_id + 盘上定义恢复 ══
    def h1(ctx):
        ctx["h1"] = True
        return "H1"

    def h2_bad(ctx):
        raise RuntimeError("崩")

    wf.define("cross_session", [
        {"id": "s1", "run": h1},
        {"id": "s2", "depends": ["s1"], "run": h2_bad},
    ], store_dir=SD)
    hst = wf.start("cross_session", context={}, run_id="run-cross", store_dir=SD)
    check("跨会话：首跑 failed", hst["status"] == wf.RUN_FAILED)
    # 模拟「换一个进程」：内存里的定义/处理器注册表全空，只剩磁盘
    wf._DEFS.clear()
    wf._HANDLERS.clear()
    hres = wf.resume("run-cross", store_dir=SD,
                     handlers={"s1": h1, "s2": lambda c: "REBOUND"})
    check("★ 跨会话：定义从盘上恢复、处理器重新绑定后 done",
          hres and hres["status"] == wf.RUN_DONE, hres and hres["status"])
    check("跨会话：s2 用重新绑定的处理器跑成 done",
          hres["steps"]["s2"]["status"] == wf.ST_DONE)

    # ══ I. cancel / list_runs ═══════════════════════════════════════
    check("cancel 生效", wf.cancel("run-linear", "单测", store_dir=SD) is True
          and wf.status("run-linear", store_dir=SD)["status"] == wf.RUN_CANCELLED)
    check("cancel 不存在的 run 被拒", wf.cancel("run-nope", store_dir=SD) is False)
    runs = wf.list_runs(store_dir=SD)
    check("list_runs 列出多个 run", len(runs) >= 4, len(runs))
    check("已 done 的 run resume 是 noop",
          wf.resume("run-cross", store_dir=SD)["status"] == wf.RUN_DONE)

    # ══ J. 非法定义（依赖不存在 / 有环）应当被拒 ═════════════════════
    try:
        wf.define("bad_dep", [{"id": "x", "depends": ["y"]}], store_dir=SD)
        bad_dep = False
    except ValueError:
        bad_dep = True
    check("依赖不存在的节点被 define 拒绝", bad_dep)
    try:
        wf.define("cycle", [
            {"id": "x", "depends": ["y"]},
            {"id": "y", "depends": ["x"]},
        ], store_dir=SD)
        cyc = False
    except ValueError:
        cyc = True
    check("有环的 DAG 被 define 拒绝", cyc)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
