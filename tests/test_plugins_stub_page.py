# -*- coding: utf-8 -*-
"""tests/test_plugins_stub_page.py —— 没有面板的插件要有说明页（2026-11-07）

## 症状

插件页每张卡都有「打开」。点没有 `ui.html` 的插件（recall_manager /
proactive_poke / echo_plugin…）只会看到一行
`{"error": "plugin ui not found"}` —— 既不知道它是干嘛的，也不知道去哪开关。

## 修法

`/page?plugin_id=` 找不到 `ui.html` 时不再回 JSON，而是生成一页说明：
**做什么 / 靠什么触发 / 在哪里调整**。插件想定制照旧放自己的 ui.html 覆盖。

判据：
  [A] 普通无面板插件：标题、描述、钩子解释、开关入口都在
  [B] 已合并插件：明确写「已并入 xxx」
  [C] 不存在的插件：给「找不到这个插件」，不是 JSON
  [D] 元信息读取：plugin.json 优先，__init__.py 兜底；钩子能解析
"""
import importlib.util
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
    spec = importlib.util.spec_from_file_location("_wb_stub", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    W = load_webui()

    print("=== A) 普通无面板插件 ===")
    html = W._plugin_stub_html("recall_manager")
    check("返回的是 HTML 不是 JSON", html.startswith("<!DOCTYPE html>"), html[:60])
    check("不再出现 plugin ui not found", "plugin ui not found" not in html)
    check("有标题", "<h1>" in html)
    check("有「这个插件做什么」", "这个插件做什么" in html)
    check("有「没有独立面板」", "没有独立面板" in html)
    check("有「在哪里调整」", "在哪里调整" in html)
    check("解释了 on_event 钩子", "订阅事件" in html, html[:400])
    check("写了插件页开关入口", "右下角" in html)
    check("描述来自清单/元信息",
          "撤回" in html or "recall" in html.lower(), html[:300])

    print("\n=== B) 已合并插件 ===")
    merged = W._plugin_stub_html("proactive_poke")
    check("写明已并入 proactivity", "已并入" in merged and "proactivity" in merged,
          merged[:400])
    check("合并后仍标出停用/启用状态",
          ("已启用" in merged) or ("已停用" in merged))
    check("合并后的调整说明不误导", "插件页这张卡" in merged or "保留入口" in merged)

    print("\n=== C) 不存在的插件 ===")
    none = W._plugin_stub_html("no_such_plugin_xyz")
    check("仍是 HTML", none.startswith("<!DOCTYPE html>"))
    check("给「找不到这个插件」", "找不到这个插件" in none)
    check("带上拼错的 id", "no_such_plugin_xyz" in none)

    print("\n=== D) 元信息读取 ===")
    m = W._plugin_file_meta(W.PROJECT / "plugins" / "recall_manager")
    check("读到了名字", bool(m.get("name")), m)
    check("读到了描述", bool(m.get("description")), list(m)[:6])
    check("解析出钩子 on_event", "on_event" in (m.get("hooks") or []), m.get("hooks"))
    # proactive_poke 先写 `PLUGIN_HOOKS = {}`、文件尾部再补全；只取第一个匹配会得到空表
    mp = W._plugin_file_meta(W.PROJECT / "plugins" / "proactive_poke")
    check("多处 PLUGIN_HOOKS 定义要合并（proactive_poke）",
          "on_event" in (mp.get("hooks") or []), mp.get("hooks"))
    check("合并钩子后占位页不再说「没有声明钩子」",
          "没有声明钩子" not in W._plugin_stub_html("proactive_poke"))
    m2 = W._plugin_file_meta(W.PROJECT / "plugins" / "proactivity")
    check("plugin.json 的 title 优先", m2.get("title") == "主动性行为", m2.get("title"))
    check("合并标记读到了", m2.get("merged_into") in (None, ""), m2.get("merged_into"))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
