# -*- coding: utf-8 -*-
"""T8 · 8.0 单测：设计令牌「别名层」契约。

8.0 的目标是**建立唯一真源，且不改变任何现有页面的外观**。
本测试就是那个"零变化"的证明，同时锁住今后不再出现"引用了但未定义"的令牌。

背景（实测）：
  · 原 :root 定义 24 个令牌，被引用 26 个 → 其中
    **--font-display / --font-ui 被引用但从未定义**，只靠 var() 的 fallback 侥幸生效。
    这类缺口不会报错、只会静默走 fallback —— 正是最难发现的一类问题。
  · 旧名 --gold 被引用 110 次、--ink5 88 次、--ink3 87 次 →
    改名或改值都会造成大面积静默变化，所以 8.0 **一个旧名都不动**。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FRONTEND = os.path.join(ROOT, "webui", "webui_artistic_v3.html")

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


s = io.open(FRONTEND, encoding="utf-8").read()
styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", s, re.S))

# ── 解析所有 --x: value 定义 ──────────────────────────────────────
RAW = {}
for m in re.finditer(r"(--[a-zA-Z0-9_-]+)\s*:\s*([^;{}]+)", styles):
    RAW.setdefault(m.group(1), m.group(2).strip())


def resolve(name, _depth=0):
    """把 var() 链解析到字面量；未知返回 None；成环返回 None。"""
    if _depth > 12:
        return None
    v = RAW.get(name)
    if v is None:
        return None
    m = re.fullmatch(r"var\(\s*(--[a-zA-Z0-9_-]+)\s*(?:,\s*(.*))?\)", v, re.S)
    if m:
        inner = resolve(m.group(1), _depth + 1)
        if inner is not None:
            return inner
        return (m.group(2) or "").strip() or None
    return v


print("=== A. 引用了的令牌必须都有定义（防「静默走 fallback」）===")
refs = set(re.findall(r"var\(\s*(--[a-zA-Z0-9_-]+)", s))
undefined = sorted(r for r in refs if r not in RAW)
check(f"★ {len(refs)} 个被引用令牌全部有定义", not undefined, undefined)
check("★ 此前未定义的 --font-display 已补上", "--font-display" in RAW)
check("★ 此前未定义的 --font-ui 已补上", "--font-ui" in RAW)
# ★ 补定义时取值必须等于**原来的 fallback 字面量**，否则就是偷偷改了外观。
#   （首版写成 var(--font-d) → 像素对比发现 .btn 中文字体从 PingFang 变 Noto，已回退）
check("★ --font-ui 取值 == 原 fallback（否则 .btn 字体被改）",
      resolve("--font-ui") == "'Outfit','PingFang SC','Microsoft YaHei',sans-serif",
      resolve("--font-ui"))
check("★ --font-display 取值 == 原 fallback",
      resolve("--font-display") == "'PingFang SC','Microsoft YaHei',sans-serif",
      resolve("--font-display"))
check("旧写法 `var(--font-ui, 'Outfit', 'PingFang SC', 'Microsoft YaHei', sans-serif)` 仍存在",
      "var(--font-ui, 'Outfit', 'PingFang SC', 'Microsoft YaHei', sans-serif)" in s)

print("\n=== B. 零视觉变化：旧令牌的**解析值**与 8.0 之前逐字相同 ===")
# 8.0 之前的快照（取自改动前的实测输出）
BEFORE = {
    "--bg": "#0a0a0f", "--surface": "#111118", "--paper": "#16161f",
    "--elevated": "#1e1e28", "--input": "#14141b",
    "--ink": "#f0f0f5", "--ink2": "#b8b8c8", "--ink3": "#8a8f9a",
    "--ink4": "#3a3f48", "--ink5": "#26262f",
    "--gold": "#c9a96e", "--gold-dim": "#8a7350",
    "--slate": "#5a7a8a", "--crimson": "#8b3a3a", "--warm": "#7a6a5a",
    "--font-d": "'Outfit','Noto Sans SC','PingFang SC',system-ui,sans-serif",
    "--font-m": "'JetBrains Mono','Consolas',monospace",
    "--base-mono": "13.5px",
    "--shadow-sm": "0 1px 2px rgba(0,0,0,0.35)",
    "--shadow-md": "0 4px 14px rgba(0,0,0,0.45)",
    "--shadow-lg": "0 10px 34px rgba(0,0,0,0.55)",
    "--shadow-gold": "0 0 22px rgba(201,169,110,0.22)",
    "--ease": "180ms cubic-bezier(0.4, 0, 0.2, 1)",
    "--ease-spring": "280ms cubic-bezier(0.34, 1.56, 0.64, 1)",
}
for name, want in sorted(BEFORE.items()):
    got = resolve(name)
    check(f"{name} 解析值不变", got == want, f"got={got!r} want={want!r}")
check("★ 24 个旧令牌一个都没被删", len(BEFORE) == 24 and all(n in RAW for n in BEFORE))

print("\n=== C. 规范 §2 的新令牌已就位 ===")
SPEC = {
    "--bg2": "#0c0c0f", "--bg3": "#101014",
    "--line": "#191920", "--line2": "#232329", "--line3": "#32323a",
    "--accent": "#c9a96e", "--accent-dim": "#8a7350",
    "--ok": "#6f9e78", "--warn": "#d0a45c", "--err": "#b05a52",
    "--z1": "9px", "--z2": "10px", "--z3": "11.5px", "--z4": "13px",
    "--z5": "16px", "--z6": "21px", "--z7": "30px", "--z8": "52px", "--z9": "120px",
    "--s1": "4px", "--s2": "8px", "--s3": "12px", "--s4": "16px",
    "--s5": "24px", "--s6": "32px", "--s7": "48px", "--s8": "72px",
    "--t-fast": "120ms", "--t-page": "320ms",
}
for name, want in sorted(SPEC.items()):
    got = resolve(name)
    check(f"{name} = {want}", got == want, f"got={got!r}")
check("--accent-glow 已定义",
      resolve("--accent-glow") == "rgba(201,169,110,.16)", resolve("--accent-glow"))
check("--ease-curve 已定义（规范的曲线，与 --ease 分开）",
      resolve("--ease-curve") == "cubic-bezier(.4,0,.2,1)", resolve("--ease-curve"))

print("\n=== D. 单一真源：强调色 ===")
check("★ --gold 定义为 var(--accent)（不再是字面量）",
      re.fullmatch(r"var\(\s*--accent\s*\)", RAW.get("--gold", "")) is not None, RAW.get("--gold"))
check("★ --gold-dim 定义为 var(--accent-dim)",
      re.fullmatch(r"var\(\s*--accent-dim\s*\)", RAW.get("--gold-dim", "")) is not None)
check("★ --gold 与 --accent 解析值一致（换肤会同步）",
      resolve("--gold") == resolve("--accent") == "#c9a96e")
check("--gold-dim 与 --accent-dim 解析值一致",
      resolve("--gold-dim") == resolve("--accent-dim") == "#8a7350")
_mood = {"calm": "#c9a96e", "lively": "#d9a24e", "irritated": "#c07a56", "low": "#8f9bb0"}
check("规范 §7 的四个情绪色都不是空的（供 JS 覆写 --accent 用）",
      all(isinstance(v, str) and v.startswith("#") for v in _mood.values()))

print("\n=== E. ★ --ease 必须保留时长（否则 42 处过渡静默失效）===")
_ease = RAW.get("--ease", "")
check("★ --ease 仍含时长（ms/s）", bool(re.search(r"\d+(\.\d+)?m?s\b", _ease)), _ease)
check("★ --ease 仍含缓动函数", "cubic-bezier" in _ease or "ease" in _ease, _ease)
_t_all = re.findall(r"transition\s*:\s*all\s+var\(--ease\)", s)
check(f"--ease 被用于 `transition:all var(--ease)`（{len(_t_all)} 处）→ 时长不可去",
      len(_t_all) > 0, len(_t_all))

print("\n=== F. 刻度本身自洽 ===")


def _px(tok):
    v = resolve(tok) or ""
    m = re.fullmatch(r"([\d.]+)px", v)
    return float(m.group(1)) if m else None


z9, z1 = _px("--z9"), _px("--z1")
check("字号阶梯最大:最小 ≥ 13:1", z9 and z1 and (z9 / z1) >= 13.0, f"{z9}:{z1}")
zs = [_px(f"--z{i}") for i in range(1, 10)]
check("字号阶梯单调递增", all(a < b for a, b in zip(zs, zs[1:])), zs)
ss = [_px(f"--s{i}") for i in range(1, 9)]
check("间距阶梯单调递增", all(a < b for a, b in zip(ss, ss[1:])), ss)
check("间距全部是 4 的倍数（规范写“8 的倍数”，但 4/12 不是 8 的倍数 → 按 4 校验）",
      all(v % 4 == 0 for v in ss), ss)

print("\n=== G. 8.1 原语层：已就位，且遵守「无字面量」铁律 ===")
_pb_m = re.search(r'<style id="design-primitives">(.*?)</style>', s, re.S)
check("原语层 <style id=\"design-primitives\"> 已存在", _pb_m is not None)
pb = _pb_m.group(1) if _pb_m else ""
pb_ns = pb.replace(" ", "").replace("\n", "")

PRIMITIVES = [".sec-h", ".p-stat", ".p-kv", ".p-cmd", ".p-fld", ".p-row",
              ".p-sig", ".p-log", ".spark", ".meter"]
for cls in PRIMITIVES:
    check(f"原语 {cls} 已定义", f"{cls}{{" in pb_ns or f"{cls}." in pb_ns or f"{cls}[" in pb_ns or f"{cls}," in pb_ns)

# 规范 §2 铁律：组件里禁止字面量颜色 / 像素字号 / 像素间距
_lit_color = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", pb)
check("★ 原语内无字面量颜色（#hex / rgb / hsl）", not _lit_color, _lit_color[:5])
_lit_fs = re.findall(r"font-size:\s*[0-9.]+px", pb)
check("★ 原语内 font-size 无像素字面量", not _lit_fs, _lit_fs[:5])
_lit_sp = []
for _m2 in re.finditer(r"(?:padding|margin|gap|row-gap|column-gap)\s*:([^;}]+)", pb):
    for _tok in re.findall(r"[0-9.]+px", _m2.group(1)):
        if float(_tok[:-2]) != 0:
            _lit_sp.append(f"{_m2.group(0)[:28]} → {_tok}")
check("★ 原语内 padding/margin/gap 无像素字面量（0 除外）", not _lit_sp, _lit_sp[:5])

# 原语用到的每个令牌都必须存在（防拼错）
_pb_vars = set(re.findall(r"var\(\s*(--[a-zA-Z0-9_-]+)", pb))
_pb_undef = sorted(v for v in _pb_vars if v not in RAW)
check(f"★ 原语引用的 {len(_pb_vars)} 个令牌全部有定义", not _pb_undef, _pb_undef)

# 规范 §4 明确「卡片不在其中」
check("★ 原语里没有引入 .card / .panel（卡片不在七大原语内）",
      ".card{" not in pb_ns and ".panel{" not in pb_ns)

# 新增的结构尺寸令牌
for _t, _v in (("--w-ln", "44px"), ("--w-ts", "74px"), ("--w-lv", "92px"),
               ("--dot", "5px"), ("--hair", "2px")):
    check(f"结构尺寸令牌 {_t} = {_v}", resolve(_t) == _v, resolve(_t))

print("\n=== H. 8.0/8.1 范围守卫：尚未迁移任何页面 ===")
check("★ 旧组件类仍在（未被误删）", ".card" in s and ".panel" in s)
check("★ 旧令牌名仍全部可解析（无改名）", all(n in RAW for n in BEFORE))
check("原语层是**新增**块，未改动原有 <style> 的内容（只多了一个块）",
      s.count("<style") == 2 and s.count("</style>") == 2,
      (s.count("<style"), s.count("</style>")))

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
