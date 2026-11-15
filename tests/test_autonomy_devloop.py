# -*- coding: utf-8 -*-
"""1.6.0 P3 · 自主开发层三段闭环（改已有代码 → 重启 → 回归）。

钉死四件事：

  A. 默认只预演：`applied` 恒 False，PreviewBackend 的每个方法都不落盘
  B. 两道闸：confirm=True 但没注入 backend 一律拒绝；confirm=False 时
     就算给了真 backend 也不会执行它
  C. DAG 顺序 verify→patch→restart→regress→finalize；任何一段失败即
     rollback + 提前终止，后续段不跑
  D. 运行态凭 run_id 可查 / 可取消 / 可恢复，trace 逐段留痕

全程用临时 store_dir，不碰仓库 data/；不注入任何真实改码 / 重启 / 回归实现。
运行：python tests/test_autonomy_devloop.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from autonomy import devloop  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


class FakeBackend:
    """记录调用顺序、可注入单点失败。applied 由自己声明。"""

    name = "fake"

    def __init__(self, fail_at=""):
        self.calls = []
        self.fail_at = str(fail_at or "")

    def _r(self, stage, applied=True):
        self.calls.append(stage)
        if self.fail_at == stage:
            return {"ok": False, "applied": False, "detail": "injected failure"}
        return {"ok": True, "applied": applied, "detail": "done"}

    def snapshot(self, ctx):
        return self._r("verify")

    def apply_patch(self, ctx):
        return self._r("patch")

    def restart(self, ctx):
        return self._r("restart")

    def regression(self, ctx):
        return self._r("regress")

    def rollback(self, ctx):
        return self._r("rollback", applied=False)


DIFF = {"target": "bot_core.py",
        "files": [{"path": "bot_core.py", "reason": "调整出站顺序"}]}


def stages(out):
    return [t["stage"] for t in (out.get("trace") or [])]


with tempfile.TemporaryDirectory(prefix="eb-devloop-") as tmp:
    store = str(Path(tmp) / "workflows")

    print("\n[A] 默认预演")
    p = devloop.plan()
    check("plan 描述五段", p["stages"] == list(devloop.STAGES), p["stages"])
    check("plan 不跑就写盘", not (Path(tmp) / "workflows").exists())
    r = devloop.run(DIFF, store_dir=store, run_id="p3-dry")
    check("预演 ok", r.get("ok") is True, r)
    check("预演 dry_run=True", r.get("dry_run") is True)
    check("预演 applied=False", r.get("applied") is False, r.get("trace"))
    check("四段都留痕", stages(r) == ["verify", "patch", "restart", "regress"], stages(r))
    check("预演没有回滚", "rollback" not in stages(r))
    pb = devloop.PreviewBackend()
    all_false = all(not (getattr(pb, m)({})["applied"])
                    for m in ("snapshot", "apply_patch", "restart", "regression", "rollback"))
    check("PreviewBackend 每步 applied=False", all_false)
    check("run_id 落盘可查", devloop.status("p3-dry", store_dir=store) is not None)
    check("工作流状态 done", r.get("status") == "done", r.get("status"))

    print("\n[B] 两道闸")
    r2 = devloop.run(DIFF, confirm=True, store_dir=store, run_id="p3-gate")
    check("confirm 但无 backend → 拒绝", r2.get("ok") is False and "backend" in r2.get("error", ""), r2)
    check("拒绝时 applied=False", r2.get("applied") is False)
    be = FakeBackend()
    r3 = devloop.run(DIFF, backend=be, store_dir=store, run_id="p3-noconfirm")
    check("未 confirm 时不调真 backend", be.calls == [], be.calls)
    check("未 confirm 仍算预演", r3.get("dry_run") is True and r3.get("applied") is False)

    print("\n[C] 真 backend 与失败回滚")
    be2 = FakeBackend()
    r4 = devloop.run(DIFF, backend=be2, confirm=True,
                     store_dir=store, run_id="p3-real")
    check("真执行 ok", r4.get("ok") is True, r4)
    check("真执行 applied=True", r4.get("applied") is True, r4.get("trace"))
    check("调用顺序是四段", be2.calls == ["verify", "patch", "restart", "regress"], be2.calls)
    check("成功不回滚", "rollback" not in be2.calls)

    for stage in ("patch", "restart", "regress"):
        be3 = FakeBackend(fail_at=stage)
        out = devloop.run(DIFF, backend=be3, confirm=True,
                          store_dir=store, run_id="p3-fail-" + stage)
        check(f"{stage} 失败 → ok=False", out.get("ok") is False, out)
        check(f"{stage} 失败 → 立刻 rollback", "rollback" in be3.calls, be3.calls)
        check(f"{stage} 失败 → error 指向该段", stage in out.get("error", ""), out.get("error"))
        tail = {"patch": ["restart", "regress"],
                "restart": ["regress"], "regress": []}[stage]
        check(f"{stage} 失败 → 后续段不跑",
              not any(x in stages(out) for x in tail), stages(out))

    print("\n[D] 入参与运行态")
    bad = devloop.run({"files": [{"path": "../evil.py"}]}, store_dir=store)
    check("非法路径直接拒绝", bad.get("ok") is False and "非法路径" in bad.get("error", ""), bad)
    check("非法路径不产生 run_id", not bad.get("run_id"))
    check("cancel 生效", devloop.cancel("p3-real", reason="测试", store_dir=store) is True)
    st = devloop.status("p3-real", store_dir=store)
    check("cancel 后状态 cancelled", st and st.get("status") == "cancelled", st)
    rr = devloop.resume("p3-real", backend=FakeBackend(), confirm=True, store_dir=store)
    check("已取消的 run 不重跑", rr.get("run_id") == "p3-real" and rr.get("status") == "cancelled", rr)
    rr2 = devloop.resume("p3-real", confirm=True, store_dir=store)
    check("真执行恢复不注入 backend → 拒绝", rr2.get("ok") is False, rr2)
    check("找不到运行态有明确报错",
          devloop.resume("no-such-run", backend=FakeBackend(), confirm=True,
                         store_dir=store).get("ok") is False)

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
