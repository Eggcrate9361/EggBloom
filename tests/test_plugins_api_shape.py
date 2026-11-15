# -*- coding: utf-8 -*-
"""tests/test_plugins_api_shape.py —— 插件页数据形状（2026-11-07 真机回归）

## 症状

插件页看不到「主动性」，两张已合并的老卡（proactive_talk / proactive_poke）又出现了。

## 根因

`/api/plugins` 有两条路：

  · bot_core 在跑  → 转发它的完整数据，行里有 `_manifest`（含 hidden/merged_into/title）
  · bot_core 没跑  → 退回本文件静态扫描：只解析 `__init__.py`，**没有 `_manifest`**

前端原来只读 `p._manifest.hidden` → 第二条路上恒为 undefined：
隐藏失效（老卡复现）、display_name 丢失（显示英文 id）、
enabled 默认 false（新插件显示「停用」）。

## 修法

后端把三件事提升成**顶层字段**（`_plugin_row`），两条路输出同构；
前端只认 `p.hidden / p.merged_into`（`_manifest` 仅兜底）。

判据：
  [A] `_plugin_row` 对完整行/静态行都产出 hidden/merged_into/display_name
  [B] `_plugin_enabled` 的优先级：state 显式 > 清单 default_enabled > 代码 enabled
  [C] 前端过滤只认顶层字段（源码级，防回退）
  [D] 两个老插件清单标了 hidden+merged_into；proactivity 默认启用
"""
import importlib.util
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


def load_webui():
    p = os.path.join(ROOT, "webui", "webui_server.py")
    spec = importlib.util.spec_from_file_location("_wb_probe", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    W = load_webui()

    print("=== A) 两条路输出同构 ===")
    full = {"name": "proactive_talk", "display_name": "主动消息引擎",
            "enabled": True, "_manifest": {"hidden": True,
                                           "merged_into": "proactivity",
                                           "title": "主动消息引擎"}}
    r1 = W._plugin_row(full)
    check("完整行：hidden 提升到顶层", r1.get("hidden") is True, r1)
    check("完整行：merged_into 提升", r1.get("merged_into") == "proactivity", r1)
    check("完整行：display_name 保留", r1.get("display_name") == "主动消息引擎", r1)

    static = {"name": "proactivity", "enabled": True}
    r2 = W._plugin_row(static, {"title": "主动性行为", "hidden": False,
                                "merged_into": ""})
    check("静态行：没有 _manifest 也能带上中文名",
          r2.get("display_name") == "主动性行为", r2)
    check("静态行：hidden 显式为 False", r2.get("hidden") is False, r2)
    check("静态行：merged_into 为空串不是 None", r2.get("merged_into") == "", r2)

    r3 = W._plugin_row({"name": "x", "hidden": True}, {"hidden": False})
    check("行里已有 hidden 时不被清单覆盖", r3.get("hidden") is True, r3)

    print("\n=== B) 启用状态优先级 ===")
    check("state 显式 false 压过 default_enabled",
          W._plugin_enabled({"x": False}, "x", {"default_enabled": True}, {}) is False)
    check("state 没记录 → 清单 default_enabled",
          W._plugin_enabled({}, "x", {"default_enabled": False}, {}) is False)
    check("两者都没有 → 代码里的 enabled",
          W._plugin_enabled({}, "x", {}, {"enabled": True}) is True)
    check("全都没有 → 默认停用（保守）",
          W._plugin_enabled({}, "x", {}, {}) is False)

    print("\n=== C) 前端只认顶层字段 ===")
    html = io.open(os.path.join(ROOT, "webui", "webui_artistic_v4.html"),
                   encoding="utf-8").read()
    check("有 isHidden 判定", "const isHidden = (p)" in html)
    check("有 mergedOf 判定", "const mergedOf = (p)" in html)
    check("过滤用 isHidden", "all.filter(p => !isHidden(p))" in html)
    check("不再只读 _manifest.hidden 做过滤",
          "all.filter(p => !(p._manifest && p._manifest.hidden))" not in html)

    print("\n=== C2) 卡片按钮不能拼出 NaN ===")
    # 真机截图：卡片底部显示 `NaNvitals" data-en="1">停用`。
    # 成因是三元的 else 分支以 `+ '<button...'` 开头 —— 没有左操作数，
    # `+` 被当成**一元加号**，字符串转数字得 NaN，再拼出一段残废 HTML。
    check("按钮先拼成独立变量（const toggle）", "const toggle =" in html)
    check("插入点用变量而不是内联三元",
          bool(__import__("re").search(r"\+\s*toggle\b", html)))
    check("没有把拼接 + 写进三元 else 分支",
          not __import__("re").search(r"\?\s*''\s*:\s*\n\s*\+\s*'<button", html))
    check("页面源码里没有裸露的 NaN 拼接",
          "'NaN" not in html and 'NaN"' not in html)

    print("\n=== D) 清单标记 ===")
    for n in ("proactive_talk", "proactive_poke"):
        m = json.load(io.open(os.path.join(ROOT, "plugins", n, "plugin.json"),
                              encoding="utf-8"))
        check(f"{n} hidden+merged_into",
              m.get("hidden") is True and m.get("merged_into") == "proactivity", m)
    pm = json.load(io.open(os.path.join(ROOT, "plugins", "proactivity", "plugin.json"),
                           encoding="utf-8"))
    check("proactivity 默认启用", pm.get("default_enabled") is True, pm)
    check("proactivity 有中文名", bool(pm.get("title")), pm)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
