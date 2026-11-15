# -*- coding: utf-8 -*-
"""任务 5.2 单测：有界保留原语 + spill 落盘。

手册验收要求：
  - [ ] 写单测覆盖「中文在切割点被切成半个字」这个 case
  - [ ] truncated 与"未找到"是不同字段

本测试不依赖 LLM，纯原语 + 落盘行为验证。
"""
import os
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import ItemRetainer, TextRetainer, format_retention_notice  # noqa: E402
from harness import spill_text, SPILL_DIR  # noqa: E402
from harness.retention import retain_text_inline  # noqa: E402

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)


# ── ItemRetainer：精确省略计数 ──────────────────────────────────────
r = ItemRetainer(max_items=3)
for i in range(10):
    r.push(i)                      # 持续 drain（含超上限的）→ 计数才精确
items, trunc, om = r.finish()
check("ItemRetainer 保留前 N 个", items == [0, 1, 2])
check("ItemRetainer truncated=True", trunc is True)
check("ItemRetainer ★精确省略计数 = 7", om == 7)

r2 = ItemRetainer(max_items=5)
r2.extend(range(2))
it2, tr2, om2 = r2.finish()
check("ItemRetainer 未超上限 truncated=False", tr2 is False)
check("ItemRetainer 未超上限 omitted=0", om2 == 0)

# finish 后禁止 push
_raised = False
try:
    r2.push(99)
except RuntimeError:
    _raised = True
check("ItemRetainer finish 后 push 报错", _raised)

# ── TextRetainer：按字节 + 内存有界 ─────────────────────────────────
big = "字" * 2000                 # 每字 3 字节 UTF-8 → 6000 字节
tr = TextRetainer("headTail", head_bytes=300, tail_bytes=300)
tr.push(big)
txt, t_tr, t_om = tr.finish()
check("TextRetainer headTail truncated=True", t_tr is True)
check("TextRetainer 省略字节数 > 0", t_om > 0)
# 内存有界：内部累积不超过 head+tail+一片(shard=6000) → 我们只检查最终文本长度合理
check("TextRetainer headTail 含省略分隔", "…" in txt)
# head 部分应是开头，tail 部分应是结尾
check("TextRetainer head 是原文开头", txt.startswith("字"))
check("TextRetainer tail 是原文结尾", txt.rstrip().endswith("字") or txt.endswith("字）".rstrip("）")) or txt.endswith("字"))

# ── ★核心 case：中文在切割点被切成半个字 ────────────────────────────
# 构造：让 head 预算刚好落在某个中文字的中间（3 字节的 1 或 2 字节处）
src = "中文测试内容" * 100          # 每字 3 字节
bad_cut = False
for cut in range(1, 12):            # 1..11 字节，必然切在某个多字节字符中间
    tb = TextRetainer("head", head_bytes=cut)
    tb.push(src)
    out, _t, _o = tb.finish()
    # 关键断言：输出里绝不能出现 U+FFFD 替换字符
    if "\ufffd" in out:
        bad_cut = True
        break
check("★中文切割点不产生替换字符(U+FFFD)", not bad_cut)

# 反向验证：如果按字符直接按字节切，会产生替换字符（证明测试有效）
raw = src.encode("utf-8")[:5].decode("utf-8", errors="replace")
check("（对照）裸切确实会产生替换字符", "\ufffd" in raw)

# tail 方向同理
bad_tail = False
for cut in range(1, 12):
    tb = TextRetainer("tail", tail_bytes=cut)
    tb.push(src)
    out, _t, _o = tb.finish()
    if "\ufffd" in out:
        bad_tail = True
        break
check("★中文 tail 切割点不产生替换字符", not bad_tail)

# headTail 两端都不产生替换字符
tb = TextRetainer("headTail", head_bytes=5, tail_bytes=7)
tb.push(src)
out, _t, _o = tb.finish()
check("★中文 headTail 两端均无替换字符", "\ufffd" not in out)

# 内存有界：tail 累积不超过 tail_bytes
tb = TextRetainer("tail", tail_bytes=64)
for _ in range(1000):
    tb.push("数据" * 50)
check("TextRetainer tail 内存有界(<=tail_bytes)", len(tb._tail) <= 64)

# ── ★穷举回归：任意 head/tail 切点都不产生替换字符 ──────────────────
_exhaustive_bad = 0
for _h in range(0, 40):
    _t = TextRetainer("head", head_bytes=_h)
    _t.push(src)
    if "\ufffd" in _t.finish()[0]:
        _exhaustive_bad += 1
for _h in range(0, 20):
    for _tb in range(0, 20):
        _t = TextRetainer("headTail", head_bytes=_h, tail_bytes=_tb)
        _t.push(src)
        if "\ufffd" in _t.finish()[0]:
            _exhaustive_bad += 1
check("★穷举 head/headTail 切点无替换字符(440 组合)", _exhaustive_bad == 0)

# ── 便捷函数 ────────────────────────────────────────────────────────
t, tr_flag, om_b = retain_text_inline("a" * 100 + "中" * 100, 30, 30)
check("retain_text_inline 返回三元组", isinstance(t, str) and tr_flag is True and om_b > 0)

# ── format_retention_notice ─────────────────────────────────────────
check("notice 未截断返回空串", format_retention_notice(False, 0) == "")
n = format_retention_notice(True, 3, noun="items")
check("notice 含精确计数", "Omitted 3 items." in n)
n2 = format_retention_notice(True, 3, resume_hint="Use read with offset/limit.")
check("notice 含恢复指引", "Use read" in n2)

# ── spill：预算内原样通过 ───────────────────────────────────────────
short = "短内容"
res = spill_text(short, kind="t", max_inline_tokens=1000)
check("spill 预算内不落盘", res["spilled"] is False)
check("spill 预算内 inline=原文", res["inline"] == short)
check("spill 预算内 truncated=False", res["truncated"] is False)
check("spill 预算内 path=None", res["path"] is None)

# ── spill：超预算落盘 + 首尾预览 + 绝对路径 ─────────────────────────
tmpday = "_test_spill_" + str(os.getpid())
long_text = ("这是很长的一段中文内容，用于测试溢出落盘。" * 500)
try:
    res = spill_text(long_text, kind="web_fetch", tag="g123",
                     max_inline_tokens=200, date_str=tmpday)
    check("spill 超预算已落盘", res["spilled"] is True)
    check("spill 超预算 truncated=True", res["truncated"] is True)
    check("spill 省略字节数 > 0", res["omitted_bytes"] > 0)
    check("spill path 是绝对路径", bool(res["path"]) and os.path.isabs(res["path"]))
    check("spill 文件真实存在", os.path.isfile(res["path"]))
    # 落盘文件保存的是**完整**内容
    with open(res["path"], "r", encoding="utf-8") as f:
        saved = f.read()
    check("★spill 落盘保存完整内容", saved == long_text)
    # 内联含路径提示
    check("spill inline 含绝对路径", res["path"] in res["inline"])
    check("spill inline 含读取提示", "read with offset/limit" in res["inline"])
    # 内联明显小于原文
    check("spill inline 远小于原文", len(res["inline"]) < len(long_text) // 2)
    # 内联不含替换字符
    check("★spill inline 无替换字符", "\ufffd" not in res["inline"])
    # 同内容二次落盘 → 同路径（去重）
    res2 = spill_text(long_text, kind="web_fetch", tag="g123",
                      max_inline_tokens=200, date_str=tmpday)
    check("spill 同内容去重(同路径)", res2["path"] == res["path"])
finally:
    d = os.path.join(SPILL_DIR, tmpday)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)

# ── spill：落盘失败要退回原文（绝不给坏路径）────────────────────────
# 用一个不可能创建目录的路径模拟失败
_backup = None
try:
    import harness.spill as _sp
    _backup = _sp.SPILL_DIR
    # 指向一个非法路径（Windows 下 NUL 下无法建目录）
    _sp.SPILL_DIR = os.path.join("\x00bad", "spill")
    res_fail = _sp.spill_text(long_text, kind="x", max_inline_tokens=10, date_str="d")
    check("★spill 失败时 error 非空", res_fail["error"] is not None)
    check("★spill 失败时 inline 退回原文", res_fail["inline"] == long_text)
    check("★spill 失败时 path=None（不给读不到的路径）", res_fail["path"] is None)
    check("★spill 失败时 truncated=False（预算事实未被污染）", res_fail["truncated"] is False)
finally:
    if _backup is not None:
        import harness.spill as _sp
        _sp.SPILL_DIR = _backup

# ── ★硬纪律：truncated 是预算事实，与"未找到/无权限"是不同字段 ──────
# 语义验证：预算内但内容为空的场景，truncated 必须为 False（不是"没找到"）
res_empty = spill_text("", kind="t", max_inline_tokens=100)
check("★空内容 truncated=False（预算事实≠未找到）", res_empty["truncated"] is False)
check("★空内容 spilled=False", res_empty["spilled"] is False)
check("★空内容 error=None（未找到不属于 error 字段）", res_empty["error"] is None)

# 字段集合验证：truncated 与 error/path 是**并列**不同字段
fields = set(spill_text(long_text, kind="x", max_inline_tokens=5,
                        date_str="_f_" + str(os.getpid())).keys())
check("★truncated/error/path 是并列不同字段",
      {"truncated", "error", "path", "spilled"} <= fields)
d = os.path.join(SPILL_DIR, "_f_" + str(os.getpid()))
if os.path.isdir(d):
    shutil.rmtree(d, ignore_errors=True)

# ── 输出 ────────────────────────────────────────────────────────────
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in PASS:
    print(f"  PASS  {n}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
