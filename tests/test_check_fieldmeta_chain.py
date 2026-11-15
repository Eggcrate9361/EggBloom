# -*- coding: utf-8 -*-
"""check_fieldmeta_chain.py 的自检：拿假数据喂它，确认每条判据真能报出来。

为什么要写它：这个脚本本身是"用来防静默失败"的，如果它自己判错了，
那它就是**新的静默源** —— 而且比没有更危险（人会以为有防线）。
所以每条判据都要能被构造出来的坏数据触发一次。

用法：python tests/test_check_fieldmeta_chain.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # tests/ 上跳一层就是仓库根
CHK = os.path.join(ROOT, "scripts", "check_fieldmeta_chain.py")

BEGIN = "/* " + "\u2550\u2550 FIELD_META_BEGIN"
FAILED = []


def build(fields, panels, actions=()):
    """按 inject_field_meta.py 的真实写法拼一段内联块。"""
    lines = [BEGIN + "（测试样本）", "window.FIELD_META = {",
             "  panels: %s," % json.dumps(list(panels), ensure_ascii=False),
             "  actions: %s," % json.dumps(list(actions), ensure_ascii=False),
             "  fields: {"]
    for k, v in fields.items():
        lines.append('    "%s": %s,' % (k, json.dumps(v, ensure_ascii=False)))
    lines += ["  }", "};", "/* " + "\u2550\u2550 FIELD_META_END */"]
    return "\n".join(lines)


def run(v4_html, meta_obj):
    d = tempfile.mkdtemp(prefix="fmchain-")
    v4 = os.path.join(d, "v4.html")
    meta = os.path.join(d, "meta.json")
    io.open(v4, "w", encoding="utf-8").write(v4_html)
    io.open(meta, "w", encoding="utf-8").write(
        json.dumps(meta_obj, ensure_ascii=False))
    p = subprocess.run([sys.executable, CHK, "--v4", v4, "--meta", meta],
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)


def case(name, ok, cond, out=""):
    if cond:
        print("[OK]   " + name)
    else:
        print("[FAIL] " + name)
        FAILED.append(name)
        if out:
            print("       " + out.strip().replace("\n", "\n       ")[-1200:])


def main():
    # 样本键必须是**不在 DEFAULT_CONFIG 里**的，否则 C 判据会把它们判成"缺默认值"
    F = {
        "test_text_key": {"label": "名字", "panel": "bot", "default": "x", "ctl": "text"},
        "test_bool_key": {"label": "开关", "panel": "bot", "ctl": "bool"},
        "drive_enable": {"label": "长期主动", "panel": "drive", "ctl": "bool",
                         "default": False},
    }
    P = [["bot", "机器人"], ["drive", "长期主动"]]
    meta_ok = {"panels": P, "fields": F, "actions": []}

    # 1) 全绿
    rc, out = run(build(F, P), meta_ok)
    case("三边一致时退出码 0", True, rc == 0, out)

    # 2) A 类：v4 有、meta 没有（正是 11-08 那个「设置项被静默抹掉」）
    m2 = {"panels": P, "fields": {k: v for k, v in F.items() if k != "drive_enable"},
          "actions": []}
    rc, out = run(build(F, P), m2)
    case("A 类能被抓到（exit 1 + 点出键名）",
         True, rc == 1 and "drive_enable" in out and "A 类" in out, out)

    # 3) B 类：字段挂到不存在的 panel
    F3 = dict(F)
    F3["orphan_key"] = {"label": "孤儿", "panel": "nope", "ctl": "bool"}
    m3 = {"panels": P, "fields": F3, "actions": []}
    rc, out = run(build(F3, P), m3)
    case("B 类能被抓到（panel 不在表里）",
         True, rc == 1 and "nope" in out and "B 类" in out, out)

    # 4) C 类：DEFAULT_CONFIG 里的键缺 default（「恢复默认」按钮消失）
    F4 = {"drive_enable": {"label": "长期主动", "panel": "drive", "ctl": "bool"}}
    m4 = {"panels": P, "fields": F4, "actions": []}
    rc, out = run(build(F4, P), m4)
    case("C 类能被抓到（缺 default）",
         True, rc == 1 and "drive_enable" in out and "C 类" in out, out)
    # 4b) 不在 DEFAULT_CONFIG 里的键本来就不该有 default，别误报
    F4b = {"test_bool_key": {"label": "开关", "panel": "bot", "ctl": "bool"}}
    rc, out = run(build(F4b, P), {"panels": P, "fields": F4b, "actions": []})
    case("C 类不误报运行时读键（不在 DEFAULT_CONFIG）", True, rc == 0, out)

    # 5) 描述漂移只提示、不算失败
    F5 = dict(F)
    F5["test_text_key"] = dict(F["test_text_key"], label="改过的名字")
    m5 = {"panels": P, "fields": F, "actions": []}
    rc, out = run(build(F5, P), m5)
    case("D 类只提示不失败（exit 0）", True, rc == 0 and "不一致" in out, out)

    # 6) 解析器要能啃住真文件的转义（中文、引号、反斜杠）
    F6 = {"slash_key": {"label": "含\"引号\"与\\\\反斜杠", "panel": "bot",
                        "ctl": "text", "desc": "中文描述，带逗号, 带括号()"},
          "test_text_key": {"label": "名字", "panel": "bot", "default": "x", "ctl": "text"}}
    m6 = {"panels": P, "fields": F6, "actions": []}
    rc, out = run(build(F6, P), m6)
    case("内联块里的转义不会让字段被漏读",
         True, rc == 0 and "2 字段" in out, out)

    # 7) 真文件跑一遍（防止只对假数据有效）
    p = subprocess.run([sys.executable, CHK], capture_output=True, text=True)
    case("当前仓库真文件一致（exit 0）", True, p.returncode == 0,
         p.stdout + p.stderr)

    print("-" * 60)
    if FAILED:
        print("失败 %d 条：%s" % (len(FAILED), "、".join(FAILED)))
        return 1
    print("自检全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
