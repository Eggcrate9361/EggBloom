# -*- coding: utf-8 -*-
"""tests/test_plugin_manifests_complete.py —— 每个插件都要有清单（1.6.0 P1-4 收尾）

## 为什么要这个用例

清单机制（拓扑排序 / 能力冲突 / 依赖拒绝）只对**写了 plugin.json** 的插件生效；
没有清单的老插件走兼容层照常加载，但**不参与排序、不被清单挡**。
只要还留着一个没有清单的插件，P1-4 的四条判据就是"部分生效"，而不是"全部生效"。

2026-11-04 补齐 17 个（最小清单：id/title/description/priority/default_enabled）。
**刻意不写 provides / requires**：
  · provides 撞车会让其中一个被标 standby（静默停用）；
  · requires 不满足会被直接拒载；
  · 老插件没有这个约定，贸然声明等于埋雷。要声明应该逐个评审，不是批量补。

## 判据

① 每个插件目录（有 __init__.py 的）都有 plugin.json，且能通过 validate；
② 全量计划里 refused / standby 为空 —— 补清单不能把任何插件挤下线；
③ 没有依赖环、没有缺失依赖。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from plugins import plugin_manifest as PM  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

PLUGINS = os.path.join(ROOT, "plugins")

#: 演示用插件也要有清单（仓库整齐），但它们是开关无关的样本
EXPECT_ALL = True


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
    dirs = [n for n in sorted(os.listdir(PLUGINS))
            if os.path.isdir(os.path.join(PLUGINS, n))
            and not n.startswith((".", "_"))
            and os.path.isfile(os.path.join(PLUGINS, n, "__init__.py"))]

    print("[A] 每个插件都有合法清单")
    missing, invalid = [], []
    for n in dirs:
        d = os.path.join(PLUGINS, n)
        m, errs = PM.load(d, fallback_id=n)
        if not os.path.isfile(os.path.join(d, PM.MANIFEST_NAME)):
            missing.append(n)
        elif m is None:
            invalid.append((n, errs))
    check(f"★ 覆盖率 100%（{len(dirs)} 个插件目录）", not missing, missing)
    check("★ 全部通过 validate", not invalid, invalid)

    print("\n[B] 清单字段是最小集（没有埋雷）")
    ms = PM.read_all(PLUGINS)
    bad_provides = [k for k, v in ms.items() if (v.get("provides") or [])]
    # 只有原本就写了 provides 的（spec 插件）才算正常
    check("老插件没有批量声明 provides",
          all(k in {"bili", "recall_manager", "schedule", "vitals",
                    "web_search", "proactive_poke"} for k in bad_provides),
          bad_provides)
    check("id 与目录名一致", all(v.get("id") == k for k, v in ms.items()),
          [(k, v.get("id")) for k, v in ms.items() if v.get("id") != k][:3])
    check("priority 都是整数", all(isinstance(v.get("priority", 50), int) for v in ms.values()),
          [k for k, v in ms.items() if not isinstance(v.get("priority", 50), int)])

    print("\n[C] 补清单不能把任何插件挤下线")
    plan = PM.plan(ms)
    check("★ refused 为空", not plan.get("refused"), plan.get("refused"))
    check("★ standby 为空", not plan.get("standby"), plan.get("standby"))
    check("无缺失依赖", not plan.get("missing_requires"), plan.get("missing_requires"))
    check("无依赖环", not plan.get("cycles"), plan.get("cycles"))
    check("排序覆盖全部插件", len(plan.get("order") or []) == len(ms),
          (len(plan.get("order") or []), len(ms)))

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
