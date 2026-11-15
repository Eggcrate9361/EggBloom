# -*- coding: utf-8 -*-
"""配置页字段链的一致性体检（只读，不写任何文件）。

链路：extract_field_meta.py -> patch_field_defaults.py -> inject_field_meta.py

为什么要有它
------------
这条链每个环节漏一步都是**静默**的，页面照样渲染得挺好看：
  · 跳过 patch_field_defaults -> 「恢复默认」按钮整片消失
  · 跳过 inject 里的"收编"     -> v4 独有的字段（drive_* / proactivity_*）
                                 被 meta 老快照单向覆盖，设置项从页面上没了
inject_field_meta.py 结尾会喊一嗓子"你没跑 patch"，但那是**注入完之后**才喊，
而且只管 default，不管"v4 独有字段有没有进 meta"。所以补这个前置检查：
动任何一步之前先看三边对不对得上，不对就别注入。

三边
----
  (1) webui/webui_artistic_v4.html 内联的 window.FIELD_META —— 页面实际看到的
  (2) scripts/_fieldmeta.json —— 下一次注入的源
  (3) config.DEFAULT_CONFIG —— 判断某键该不该有 default 的依据

判据
----
  A. (2) 必须覆盖 (1)：v4 有、meta 没有的键，跑一次 extract 就被抹掉
  B. (1) 里每个字段的 panel 必须在 (1) 的 panels 表里，否则页面无处安放
  C. (1) 里凡在 DEFAULT_CONFIG 中的键都必须带 default，否则按钮不渲染
  D. (1) 与 (2) 描述不一致的只报数量（不算失败，可能刚改还没重新注入）

用法：python scripts/check_fieldmeta_chain.py [-v]
退出码：0 = 通过；1 = A/B/C 有问题。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BEGIN = "FIELD_META_BEGIN"
END = "FIELD_META_END */"

VERBOSE = ("-v" in sys.argv) or ("--verbose" in sys.argv)


def _arg(name, default):
    """极简 --name 取值（不引 argparse：本脚本要能在最小环境里跑）。"""
    a = sys.argv
    for i, x in enumerate(a):
        if x == name and i + 1 < len(a):
            return a[i + 1]
        if x.startswith(name + "="):
            return x.split("=", 1)[1]
    return default


# --v4 / --meta / --root 只为自检时喂临时样本，正常跑不用给
V4 = _arg("--v4", os.path.join(ROOT, "webui", "webui_artistic_v4.html"))
META = _arg("--meta", os.path.join(HERE, "_fieldmeta.json"))
if _arg("--root", None):
    ROOT = _arg("--root", ROOT)


def _arr_from_block(name, blk):
    """取 `  panels: [ ... ],` 这种一行一个 JSON 数组的值。"""
    for line in blk.splitlines():
        t = line.strip()
        if t.startswith(name + ":"):
            raw = t[len(name) + 1:].strip()
            if raw.endswith(","):
                raw = raw[:-1]
            return json.loads(raw)
    return []


def parse_inline(html):
    """从 v4 里抠出内联 FIELD_META：panels / actions / fields。

    不用复杂正则：内联块里每个字段都严格是 `"key": {...},` 一行一个 JSON 对象，
    直接把冒号后面那截当 JSON 解，比正则拼反斜杠稳得多。
    """
    i = html.find(BEGIN)
    if i < 0:
        raise SystemExit("v4 里没有 FIELD_META 内联块（BEGIN 标记都找不到）")
    j = html.find(END, i)
    if j < 0:
        raise SystemExit("v4 里 FIELD_META 内联块没有 END 标记")
    blk = html[i:j]

    panels = _arr_from_block("panels", blk)
    actions = _arr_from_block("actions", blk)

    fields = {}
    bad = 0
    started = False
    for line in blk.splitlines():
        if not started:
            if "fields: {" in line:
                started = True
            continue
        t = line.strip()
        if not t.startswith('"'):
            continue
        pos = t.find('":')          # "key": {...},
        if pos < 1:
            continue
        try:
            key = json.loads(t[:pos + 1])
        except Exception:
            bad += 1
            continue
        rest = t[pos + 2:].strip()
        if rest.endswith(","):
            rest = rest[:-1]
        try:
            fields[key] = json.loads(rest)
        except Exception:
            bad += 1
    if bad:
        print("[!] 内联块里有 %d 行没解析成 JSON（下面数字可能偏小）" % bad)
    return panels, actions, fields


def main():
    html = io.open(V4, encoding="utf-8").read()
    v4_panels, v4_actions, v4_fields = parse_inline(html)

    meta = json.load(io.open(META, encoding="utf-8"))
    meta_panels = [tuple(p) for p in (meta.get("panels") or [])]
    meta_fields = meta.get("fields") or {}

    sys.path.insert(0, ROOT)
    import config as _cfg
    defaults = getattr(_cfg, "DEFAULT_CONFIG", {})

    fail = []
    print("(1) v4 内联：%d 字段 / %d 面板 / %d 动作按钮"
          % (len(v4_fields), len(v4_panels), len(v4_actions)))
    print("(2) meta 源 ：%d 字段 / %d 面板 / %d 动作按钮"
          % (len(meta_fields), len(meta_panels), len(meta.get("actions") or [])))

    # ── A. meta 必须覆盖 v4 ────────────────────────────────
    only_v4 = [k for k in v4_fields if k not in meta_fields]
    if only_v4:
        fail.append("A 类：%d 个字段只在 v4 里、meta 里没有 —— 跑一次 "
                    "extract_field_meta.py 就会从页面上消失：%s"
                    % (len(only_v4), "、".join(only_v4)))
    else:
        print("[OK] A. meta 覆盖了 v4 的全部字段")

    # ── B. 面板归属 ────────────────────────────────────────
    pids = set(p[0] for p in v4_panels)
    orphan = {}
    for k, v in v4_fields.items():
        pn = v.get("panel")
        if not pn or pn not in pids:
            orphan.setdefault(pn or "(无 panel)", []).append(k)
    if orphan:
        fail.append("B 类：%d 个 panel 在 panels 表里不存在，字段无处安放：%s"
                    % (len(orphan), "；".join(
                        "%s<-%s" % (p, "、".join(ks)) for p, ks in orphan.items())))
    else:
        print("[OK] B. 每个字段的 panel 都在 panels 表里")

    meta_pids = set(p[0] for p in meta_panels)
    lost_panel = [p for p in meta_pids if p not in pids]
    if lost_panel:
        print("[提示] meta 里还有 %d 个面板 v4 没引用：%s"
              % (len(lost_panel), "、".join(sorted(lost_panel))))

    # ── C. DEFAULT_CONFIG 里的键必须有 default ─────────────
    need = [k for k in v4_fields if k in defaults]
    miss = [k for k in need if "default" not in v4_fields[k]]
    if miss:
        fail.append("C 类：%d 个在 DEFAULT_CONFIG 里的字段没有 default —— "
                    "配置页的「恢复默认」按钮不会渲染，先跑 "
                    "patch_field_defaults.py 再注入：%s"
                    % (len(miss), "、".join(miss[:20]) + ("…" if len(miss) > 20 else "")))
    else:
        print("[OK] C. %d 个有默认值的字段都带 default" % len(need))

    # ── D. 描述漂移（只报数，不算失败）─────────────────────
    drift = [k for k in v4_fields
             if k in meta_fields and
             (v4_fields[k].get("label"), v4_fields[k].get("desc")) !=
             (meta_fields[k].get("label"), meta_fields[k].get("desc"))]
    if drift:
        msg = "%d 个字段的 label/desc 与 meta 不一致" % len(drift)
        if VERBOSE:
            msg += "：" + "、".join(drift[:20])
        print("[提示] " + msg + "（改完记得 extract -> patch -> inject 三连）")

    print("-" * 60)
    if fail:
        for f in fail:
            print("[FAIL] " + f)
        return 1
    print("[OK] 字段链三边一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
