# -*- coding: utf-8 -*-
"""把 _fieldmeta.json 注入 webui_artistic_v4.html（内联常量 FIELD_META）。

为什么要内联而不引外部文件：v4 是**单文件** WebUI（可单独拷贝/打包），
引一个外部 JS 会让它不再自洽 —— 和「字体已经本地化打包」是同一个理由。

幂等：重复执行只会替换旧块，不会叠加。
用法：python scripts/inject_field_meta.py
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
V4 = os.path.join(ROOT, "webui", "webui_artistic_v4.html")
META = os.path.join(HERE, "_fieldmeta.json")

BEGIN = "/* ══ FIELD_META_BEGIN"
END = "/* ══ FIELD_META_END */"

d = json.load(io.open(META, encoding="utf-8"))
fields = d["fields"]
panels = d["panels"]
actions = d["actions"]

lines = []
lines.append(BEGIN + "（由 scripts/inject_field_meta.py 从 v3 自动抽取，请勿手改）")
lines.append("/* ★ 挂 window 而不是用 const：")
lines.append("   顶层 const FIELD_META 在这份文件里会因求值顺序/作用域出现")
lines.append("   `typeof FIELD_META === 'undefined'`（页面无报错，但配置页读不到），")
lines.append("   挂到 window 上这个问题就彻底不存在了。*/")
lines.append("window.FIELD_META = {")
lines.append("  panels: %s," % json.dumps(panels, ensure_ascii=False))
lines.append("  actions: %s," % json.dumps(actions, ensure_ascii=False))
lines.append("  fields: {")
for k, v in fields.items():
    lines.append("    %s: %s," % (json.dumps(k), json.dumps(v, ensure_ascii=False)))
lines.append("  }")
lines.append("};")
lines.append(END)
block = "\n".join(lines)

s = io.open(V4, encoding="utf-8").read()
old = re.search(re.escape(BEGIN) + r".*?" + re.escape(END), s, re.S)
if old:
    s = s[:old.start()] + block + s[old.end():]
    print("替换旧块（%d 字符）" % (old.end() - old.start()))
else:
    anchor = "async function pageConfig(el) {"
    i = s.index(anchor)
    s = s[:i] + block + "\n\n" + s[i:]
    print("插入到 pageConfig 之前")

io.open(V4, "w", encoding="utf-8").write(s)
print("注入字段 %d 个 / 面板 %d 个 / 动作按钮 %d 个"
      % (len(fields), len(panels), len(actions)))

# ★ 2026-10-24 加这条自检，因为我自己踩过：完整链路是
#     extract_field_meta.py → patch_field_defaults.py → inject_field_meta.py
#   中间那步补的是 `default` / `default_text`（配置页「默认 xxx ↺」按钮靠它
#   渲染）和 `restart`。跳过它，页面不报错，只是所有"恢复默认"按钮静默消失，
#   而且要等有人去点才发现。这里不替它补（补的判据在那边），只负责喊。
#
# 判据不能用"没 default 的比例"：约三分之一的字段是运行时读键
# （_config.get 直接取），本来就不在 DEFAULT_CONFIG 里，也不该有默认值。
# 准确的做法是拿 DEFAULT_CONFIG 逐键对：出现在里面的就**必须**带 default。
try:
    sys.path.insert(0, ROOT)
    import config as _cfg
    _need = [k for k in fields if k in _cfg.DEFAULT_CONFIG]
    _miss = [k for k in _need if "default" not in fields[k]]
    if _miss:
        print("\n[!] %d 个在 DEFAULT_CONFIG 里的字段没有 default："
              "先跑 patch_field_defaults.py 再注入，否则配置页的「恢复默认」"
              "按钮会消失。缺的键：%s"
              % (len(_miss), "、".join(_miss[:8]) + ("…" if len(_miss) > 8 else "")))
        print("    完整链路：extract_field_meta.py → patch_field_defaults.py → "
              "inject_field_meta.py")
except Exception as _e:
    print("[!] 默认值自检跳过（%s）" % _e)