# -*- coding: utf-8 -*-
"""tests/test_plugin_manifest_enforce.py —— 清单**启动期强制**（2026-11-03）

执行总表 §8.1 #1 的四条判据：
  ① 拓扑排序 ② 能力冲突选主 + standby ③ 缺依赖拒载 ④ hook 多订阅必须链式

验收原话（FIXPLAN §2.2）：
  · 写两个都声明 `provides: reply.policy` 的插件 → **启动报警且只生效一个**
  · 写一个 `requires: [不存在的]` → **拒绝加载**，`/api/plugins` 里能看到原因

另外 [J] 是**回归护栏**：拿仓库里现有的真清单跑一遍，必须 0 拒载 0 冲突 ——
强制上线当天不该误伤任何一个已经在跑的插件。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from plugins import plugin_manifest as pmf  # noqa: E402

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


def main():
    print("[A] 空输入不崩")
    check("空清单", pmf.plan({})["order"] == [])
    check("None", pmf.plan(None)["refused"] == {})
    check("load_order 空", pmf.load_order([], {})["order"] == [])

    print("\n[①] 拓扑排序：被依赖的先上线")
    ms = {
        "b": {"id": "b", "provides": ["cap.b"], "requires": []},
        "a": {"id": "a", "provides": ["cap.a"], "requires": ["cap.b"]},
    }
    pl = pmf.plan(ms)
    check("b 排在 a 前面", pl["order"].index("b") < pl["order"].index("a"), pl["order"])
    check("两个都加载", set(pl["order"]) == {"a", "b"} and pl["refused"] == {})
    ms3 = {
        "c": {"provides": ["cap.c"], "requires": ["cap.b"]},
        "b": {"provides": ["cap.b"], "requires": ["cap.a"]},
        "a": {"provides": ["cap.a"], "requires": []},
    }
    order = pmf.plan(ms3)["order"]
    check("三层依赖：a → b → c", order.index("a") < order.index("b") < order.index("c"), order)
    check("自给自足不算环",
          pmf.plan({"s": {"provides": ["cap.s"], "requires": ["cap.s"]}})["refused"] == {})

    print("\n[②] 能力冲突：选主 + standby（不许静默覆盖）")
    two = {
        "p1": {"id": "p1", "provides": ["reply.policy"], "priority": 10},
        "p2": {"id": "p2", "provides": ["reply.policy"], "priority": 90},
    }
    pl = pmf.plan(two)
    check("只有一个是主", pl["owners"].get("reply.policy") == "p1", pl["owners"])
    check("另一个进 standby", pl["standby"] == {"p2": ["reply.policy"]}, pl["standby"])
    check("两个都照常加载（只是不都当主）",
          set(pl["order"]) == {"p1", "p2"} and pl["refused"] == {})
    check("启动报警说了这件事",
          any("能力冲突" in w for w in pl["warnings"]), pl["warnings"])
    tie = {"b": {"provides": ["x"]}, "a": {"provides": ["x"]}}
    check("同优先级按 id 定主（结果可复现）", pmf.plan(tie)["owners"]["x"] == "a")
    check("显式 priorities 覆盖清单",
          pmf.plan(tie, {"a": 90, "b": 10})["owners"]["x"] == "b")

    print("\n[③] 缺依赖 → 拒载，且原因看得懂")
    miss = {"a": {"id": "a", "requires": ["no.such.cap"]}}
    pl = pmf.plan(miss)
    check("★ 拒载", "a" in pl["refused"], pl["refused"])
    check("原因点名缺的能力", "no.such.cap" in pl["refused"]["a"], pl["refused"]["a"])
    lay = pmf.load_order(["a", "legacy_one"], pl)
    check("★ load_order 跳过它", lay["order"] == ["legacy_one"], lay["order"])
    check("skip 里带着原因", bool(lay["skip"].get("a")))

    print("\n[③] 依赖环 → 环上全拒")
    cyc = {
        "a": {"provides": ["cap.a"], "requires": ["cap.b"]},
        "b": {"provides": ["cap.b"], "requires": ["cap.a"]},
    }
    pl = pmf.plan(cyc)
    check("★ 两个都拒", set(pl["refused"]) == {"a", "b"}, pl["refused"])
    check("原因写依赖环", all("依赖环" in r for r in pl["refused"].values()), pl["refused"])
    check("箭头能看出环", any("→" in r for r in pl["refused"].values()))
    check("cycles 字段兼容旧报告", bool(pmf.plan(cyc)["cycles"]))

    print("\n[③] 级联：提供者被拒 → 依赖它的也别加载")
    cas = {
        "bad": {"provides": ["cap.x"], "requires": ["no.way"]},
        "user": {"requires": ["cap.x"]},
    }
    pl = pmf.plan(cas)
    check("★ 级联拒载", "user" in pl["refused"], pl["refused"])
    check("原因点名是谁被拒了", "bad" in pl["refused"]["user"], pl["refused"]["user"])

    print("\n[④] hook 多订阅 → 必须能排出链")
    chains = pmf.hook_chains({"before_reply": ["z", "a", "m"]},
                             {"z": 90, "a": 10, "m": 50})
    check("★ 多订阅被标出来", chains["before_reply"]["multi"] is True)
    check("链按 priority 升序", chains["before_reply"]["chain"] == ["a", "m", "z"],
          chains["before_reply"]["chain"])
    one = pmf.hook_chains({"x": ["a"]})
    check("单订阅 multi=False", one["x"]["multi"] is False)
    dup = pmf.hook_chains({"y": ["a", "a"]})
    check("重复订阅去重", dup["y"]["chain"] == ["a"], dup["y"]["chain"])

    print("\n[H] 兼容层：没有清单的老插件照常加载")
    lay = pmf.load_order(["legacy_a", "b", "a"], {"order": ["b", "a"], "refused": {}})
    check("计划内的先、老插件排后面", lay["order"] == ["b", "a", "legacy_a"], lay["order"])
    check("老插件不会被跳过", "legacy_a" not in lay["skip"])
    lay2 = pmf.load_order(["x"], {"order": [], "refused": {"x": "缺依赖：zzz"}})
    check("被拒的即使没清单也跳过", lay2["order"] == [] and "x" in lay2["skip"])

    print("\n[K] 清单里多了一个 priority 字段")
    check("非法 priority 被拦", pmf.validate({"id": "x", "priority": "abc"}) != [])
    check("整数 priority 放行", pmf.validate({"id": "x", "priority": 10}) == [])
    check("不写 priority 也放行", pmf.validate({"id": "x"}) == [])

    print("\n[J] ★ 回归护栏：现网真清单必须 0 拒载 / 0 冲突")
    real = pmf.read_all(os.path.join(ROOT, "plugins"))
    pl = pmf.plan(real)
    check(f"读到 {len(real)} 份真清单（{sorted(real)}）", len(real) >= 4, sorted(real))
    check("★ 0 拒载（强制上线不误伤在跑的插件）", pl["refused"] == {}, pl["refused"])
    check("★ 0 能力冲突", pl["duplicate_provides"] == {}, pl["duplicate_provides"])
    check("全部进入加载计划", set(pl["order"]) == set(real), (pl["order"], sorted(real)))

    print("\n[I] 接线：被拒的插件真的不加载，且 /api/plugins 能看到原因")
    try:
        from plugins.plugin_manager import PluginManager
        mgr = PluginManager()
        mgr._manifests = {"bad": {"id": "bad", "requires": ["no.way"]}}
        mgr._manifest_errors = {}
        mgr._reload_manifests = lambda: None          # 用上面那份合成清单，不读盘
        mgr.discover = lambda: ["bad", "good"]
        loaded = []

        def _fake_load(n):
            loaded.append(n)
            mgr._plugins[n] = {"module": object(), "meta": {"name": n, "enabled": True,
                                                           "priority": 50},
                               "hooks": {}, "error": None}
            return True

        mgr.load = _fake_load
        mgr.load_all()
        check("★ 被拒的没有进加载队列", "bad" not in loaded, loaded)
        check("★ 正常的照常加载", "good" in loaded, loaded)
        ent = mgr._plugins.get("bad") or {}
        check("★ 被拒的挂在 _plugins 上（否则页面根本看不到）", bool(ent), list(mgr._plugins))
        check("★ 原因写进 error", str(ent.get("error") or "").startswith("清单拒载"),
              ent.get("error"))
        rows = {r["id"]: r for r in mgr.list_plugins()}
        check("★ /api/plugins 数据里带 _refused", bool(rows.get("bad", {}).get("_refused")),
              rows.get("bad", {}).get("_refused"))
        check("_refused 里点名缺的能力", "no.way" in rows["bad"]["_refused"],
              rows["bad"]["_refused"])
        check("正常插件 _refused 为空", rows["good"]["_refused"] == "")
        rep = mgr.get_manifest_report()
        check("报告里带 plan", "plan" in rep and bool(rep["plan"]))
        check("hook_report 能算", isinstance(mgr.hook_report().get("multi"), list))
    except Exception as e:
        check("接线测试跑通", False, f"{type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
