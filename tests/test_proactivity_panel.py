# -*- coding: utf-8 -*-
"""tests/test_proactivity_panel.py —— 主动性插件面板（2026-11-07）

## 为什么有这一组

插件页点「打开」原来直接回 `{"error":"plugin ui not found"}` —— proactivity
没有 `ui.html`。补面板的同时把两件事钉住：

  [A] 面板文件存在，且 action 名与插件实现对得上
  [B] `get_config` 返回 13 个白名单键 + 运行时状态（不依赖宿主 ctx）
  [C] `set_config` 只写白名单键、类型被钳制；`api_key` 这类键进不来
  [D] 值清洗：档位 0..3、分钟 1..1440、静默时段格式

`set_config` 会写盘，测试里把 `config.save_config` 换掉，只断言"它会写什么"。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import config as C  # noqa: E402
import plugins.proactivity as P  # noqa: E402

PASS, FAIL = 0, 0
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
    print("=== A) 面板与动作对得上 ===")
    ui = os.path.join(ROOT, "plugins", "proactivity", "ui.html")
    check("ui.html 存在", os.path.isfile(ui))
    html = io.open(ui, encoding="utf-8").read()
    check("有 pv-kit 注入占位", "/*PV-KIT*/" in html)
    check("调的是 /api/plugins/action", "/api/plugins/action" in html)
    check("调 get_config", "'get_config'" in html)
    check("调 set_config", "'set_config'" in html)
    for k in ("proactivity_enable", "proactivity_budget_level",
              "proactivity_no_reply_min", "proactivity_idle_contact_enable"):
        check(f"面板含控件 {k}", 'data-c="%s"' % k in html)
    check("滑条 0..3", 'type="range" min="0" max="3"' in html)
    check("四条行为 + 撤回都在",
          all(x in html for x in ("① 未回应", "② 被 @ 却没回", "③ 未兑现话头",
                                  "⑤ 冷场接触", "④ 撤回反馈")))
    check("插件导出 PLUGIN_ACTIONS（读 / 写 / 交棒）",
          isinstance(getattr(P, "PLUGIN_ACTIONS", None), dict)
          and {"get_config", "set_config", "take_recall"} <= set(P.PLUGIN_ACTIONS),
          getattr(P, "PLUGIN_ACTIONS", None))

    print("\n=== B) get_config ===")
    r = P.act_get_config()
    check("ok=True", r.get("ok") is True, r)
    check("13 个键", len(r.get("config") or {}) == len(P.CFG_KEYS), len(r.get("config") or {}))
    check("不依赖宿主 ctx 也能读到值",
          r["config"].get("proactivity_budget_level") is not None, r["config"])
    st = r.get("status") or {}
    check("status 有预算/会话/待仲裁",
          all(k in st for k in ("budget_level", "sessions", "pending", "recent")), st)

    print("\n=== C) set_config 白名单 ===")
    written = {}

    def fake_save(cfg, save_api_key=False):
        written.update(cfg)
        return True

    orig = C.save_config
    C.save_config = fake_save
    try:
        r2 = P.act_set_config({
            "proactivity_budget_level": 3,
            "proactivity_no_reply_min": 12,
            "api_key": "sk-should-not-be-written",
            "model_name": "别的模型",
        })
        check("ok=True", r2.get("ok") is True, r2)
        check("只应用白名单键", set(r2.get("applied") or {}) ==
              {"proactivity_budget_level", "proactivity_no_reply_min"},
              r2.get("applied"))
        check("api_key 没被写进配置", "api_key" not in written, list(written)[:6])
        # 写回的是"原文件 + 白名单增量"，所以别的键**照原样**回去是正常的；
        # 要防的是被面板 params 覆盖。
        check("别的配置键没被 params 覆盖", written.get("model_name") != "别的模型",
              written.get("model_name"))
        check("档位=3 落盘", written.get("proactivity_budget_level") == 3,
              written.get("proactivity_budget_level"))
        check("分钟=12 落盘", written.get("proactivity_no_reply_min") == 12)
    finally:
        C.save_config = orig

    print("\n=== D) 值清洗 ===")
    cv = P._clean_value
    check("档位钳到 3", cv("proactivity_budget_level", 99) == 3)
    check("档位钳到 0", cv("proactivity_budget_level", -5) == 0)
    check("档位脏值退回 1", cv("proactivity_budget_level", "abc") == 1)
    check("分钟最小 1", cv("proactivity_no_reply_min", 0) == 1)
    check("分钟最大 1440", cv("proactivity_no_reply_min", 99999) == 1440)
    check("静默时段合法保留", cv("proactivity_quiet_hours", "23-7") == "23-7")
    check("静默时段非法清空", cv("proactivity_quiet_hours", "晚上") == "")
    check("开关收成布尔", cv("proactivity_enable", 0) is False)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
