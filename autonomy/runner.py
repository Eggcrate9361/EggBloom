# -*- coding: utf-8 -*-
"""autonomy.runner —— 长任务入口（设计 §11.1：走 job，不阻塞 agent）。

由 `autonomy_install` 用 `job_start` 拉起；job_wait 一次等到结束。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import loop, plan as plan_mod, probe as probe_mod
from .capability_gap import Gap
from .tools import RealBackend


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="自主获取长任务")
    ap.add_argument("--need", required=True)
    ap.add_argument("--kind", default="")
    ap.add_argument("--package", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--sha256", default="")
    ap.add_argument("--size-bytes", type=int, default=0)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--data", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--skip-net", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if args.data:
        dd = Path(args.data).resolve()
    else:
        try:
            from data_store import get_data_dir
            dd = Path(get_data_dir())
        except Exception:
            dd = root / "data"

    g = Gap("self_report", str(args.need), "runner")
    env = probe_mod.env_report(data_dir=dd, disk_paths=[str(dd), str(root)],
                               net_check=(lambda _n: {"status": "skipped"})
                               if args.skip_net else None)
    p = plan_mod.make_plan(g, env, data_dir=dd, plugins_dir=root / "plugins",
                           kind=str(args.kind or ""), package=str(args.package or ""),
                           url=str(args.url or ""), sha256=str(args.sha256 or ""),
                           size_bytes=int(args.size_bytes or 0))
    if not p.get("ok"):
        print(p.get("error") or "计划不可行")
        return 1
    r = loop.run(g, data_dir=dd, root=root, backend=RealBackend(),
                 plan_override=p)
    out = {"ok": bool(r.get("ok")), "need": args.need, "step": r.get("step"),
           "report": r.get("report"), "error": r.get("error"),
           "plan": p, "checks": r.get("checks")}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str) if args.json
          else (r.get("report") or r.get("error") or "未知"))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
