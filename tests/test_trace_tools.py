# -*- coding: utf-8 -*-
"""tests/test_trace_tools.py —— 可观测三件套（FIXPLAN 1.5.11 §4.5 M1）

用户主线是"agent 能自己修自己"。自修复的第一步不是改代码，是**看得见**：
今天最常失败的是哪条链路、某次失败的全链路长什么样。

本文件锁六件事：
  A. failure_top：按链路聚合、按次数排序、取前 N；**只算时间窗内**
  B. log_search：正则命中 / 非法正则退回字面量 / limit 生效
  C. trace_get：按 id 拉全链路；不传 id 用日志里最后一个；没有 trace 不炸
  D. repairs.jsonl：写→读往返、时间窗过滤、文件不存在返回空
  E. 日志文件不存在时返回空（不抛异常，主链不能因为"没日志"挂掉）
  F. 工具注册进 work 组（只读常驻）

全程用临时文件，不碰真实日志/台账。
"""
import io
import json
import os
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import trace_tools as T  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def _ts(hours_ago):
    return datetime.fromtimestamp(time.time() - hours_ago * 3600).strftime(
        "%Y-%m-%d %H:%M:%S")


def main():
    tmp = tempfile.mkdtemp(prefix="eb_trace_")
    log = os.path.join(tmp, "qq_bot.log")
    lines = [
        f"[{_ts(1)}] [工具] search_web 失败: timeout trace=abc123456789",
        f"[{_ts(1.2)}] [工具] py_run 异常: Traceback (most recent call last)",
        f"[{_ts(2)}] [图片理解] 视觉调用失败: 400",
        f"[{_ts(3)}] [协议] WebSocket 断开 retcode=3",
        f"[{_ts(1.5)}] [Sandglass] 人物画像已载入 3 人",
        f"[{_ts(30)}] [工具] 窗口外的失败不该算",
        f"[{_ts(2.5)}] [记忆] recall 异常，本轮不注入: boom",
    ]
    io.open(log, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    print("[A] failure_top")
    top = T.failure_top(hours=24, n=3, path=log)
    check("返回 3 条链路", len(top) == 3, top)
    check("第一名是工具调用（2 次）", top[0]["channel"] == "工具调用" and top[0]["count"] == 2, top[0])
    names = [x["channel"] for x in top]
    check("窗口外的失败不计入", "其他" not in names, names)
    total = sum(x["count"] for x in T.failure_top(hours=24, n=99, path=log))
    check("24h 内共 5 条失败（不含窗口外）", total == 5, total)
    check("样例行带 trace", any("trace=" in s for s in top[0]["samples"]), top[0]["samples"])

    print("\n[B] log_search")
    r = T.log_search("py_run", hours=24, path=log)
    check("命中 1 行", r["total"] == 1 and len(r["lines"]) == 1, r)
    check("命中行是异常那条", "异常" in r["lines"][0], r["lines"])
    r = T.log_search("(*", hours=24, path=log)   # 非法正则
    check("非法正则不炸（按字面量）", r["total"] == 0, r)
    r = T.log_search("", hours=24, limit=2, path=log)
    check("空 pattern = 不过滤，limit 生效", r["total"] == 6 and len(r["lines"]) == 2, r["total"])
    r = T.log_search("失败", hours=24, path=log)
    check("窗口外那条不算（只命中窗口内 2 条）", r["total"] == 2, r["lines"])

    print("\n[C] trace_get")
    r = T.trace_get("abc123456789", path=log)
    check("按 id 拉到 1 行", r["count"] == 1 and "search_web" in r["lines"][0], r)
    r = T.trace_get("", path=log)
    check("不传 id 用最后一个 trace", r["trace_id"] == "abc123456789", r)
    r = T.trace_get("deadbeefdead", path=log)
    check("查不到的 id 返回空而不是报错", r["count"] == 0 and r["lines"] == [], r)

    print("\n[D] repairs 台账")
    T.REPAIRS_PATH = os.path.join(tmp, "repairs.jsonl")
    ok1 = T.record_repair("config", "eval_enabled", "改回 true", True, "手滑关掉了")
    ok2 = T.record_repair("cache", "static_prompt", "清缓存", False, "权限不足")
    check("写入成功", ok1 and ok2, (ok1, ok2))
    rows = T.list_repairs(hours=168)
    check("读回 2 条", len(rows) == 2, rows)
    check("字段完整", {"ts", "kind", "target", "action", "ok", "detail"} <= set(rows[0]), rows[0])
    check("最新的在前", rows[0]["target"] == "static_prompt", [r["target"] for r in rows])
    # 时间窗：手动写入一条 10 天前的记录，hours=24 不该读到它
    old = {"ts": time.time() - 10 * 86400, "kind": "config", "target": "old",
           "action": "很久以前", "ok": True, "detail": ""}
    with io.open(T.REPAIRS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(old, ensure_ascii=False) + "\n")
    recent = T.list_repairs(hours=24)
    check("时间窗只留 24h 内（2 条）", len(recent) == 2, [r["target"] for r in recent])
    check("10 天前那条被滤掉", all(r["target"] != "old" for r in recent))

    print("\n[E] 文件不存在不炸")
    check("日志不存在 → 空列表", T.failure_top(path=os.path.join(tmp, "nope.log")) == [])
    check("日志不存在 → 搜索返回 0", T.log_search("x", path=os.path.join(tmp, "nope.log"))["total"] == 0)
    T.REPAIRS_PATH = os.path.join(tmp, "nope_repairs.jsonl")
    check("台账不存在 → 空列表", T.list_repairs() == [])

    print("\n[F] 工具注册")
    fns = T.register_all()
    check("注册 4 个工具", set(fns) == {"failure_top", "log_search", "trace_get",
                                        "repairs_list"}, sorted(fns))
    from harness import tool_registry as tr
    names = [s["function"]["name"] for s in tr.resolve(sorted(fns))]
    check("registry 里能解析到", set(names) == set(fns), names)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
