# -*- coding: utf-8 -*-
"""harness/spill.py —— 溢出到磁盘（任务 5.2 下半）。

对齐 docs/plan/dsh-harness-design.md §7.4 与 §11.1 P6：

  超过预算的内容**落盘**，模型只看到 **首尾预览 + 完整文件的绝对路径 + 读取提示**。
  好处：被截断的部分**可恢复**（模型可以再 `read(path, offset, limit)` 或用 grep 搜）。

设计取舍（照抄 DSH 的几条硬规则）：
  - **文字可切分，图片整张保留或省略** —— 本模块只处理**文字**；图片路径由调用方处理。
  - **存储失败就保留原内容并记录警告** —— 绝不用一个读不到的路径替换原内容
    （「策略不会用无法读取的路径替换图片」的同理推广）。
  - 落盘文件保存**完整**内容（不是预览），每张图片的位置由调用方以附件路径形式记录。

落盘位置：`<data_dir>/spill/<YYYY-MM-DD>/<hash>.txt`
"""
import os
import re
import hashlib
import datetime

try:
    from data_store import get_data_dir
    _DATA_DIR = str(get_data_dir())
except Exception:
    _DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

SPILL_DIR = os.path.join(_DATA_DIR, "spill")

# 保守的字符→token 估算（中文约 1 token/字，英文约 4 字符/token）。
# 只用于「是否超预算」的粗判，不作为计费依据。
_CHARS_PER_TOKEN = 1.6


def estimate_tokens(text):
    """粗略估算 token 数（够用即可，不做精确分词）。"""
    if not text:
        return 0
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def _safe_name(text):
    """由内容生成稳定的短哈希文件名（同内容 → 同文件，天然去重）。"""
    h = hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()[:16]
    return h


def spill_text(text, *, kind="text", max_inline_tokens=None,
               tag="", date_str=None, resume_hint_fmt=None):
    """把超预算文本落盘，返回内联预览字符串 + 元信息。

    参数：
      text              : 完整文本（**完整保存到磁盘**）
      kind              : 领域标签（如 'web_fetch' / 'group_log' / 'memory'），进文件名
      max_inline_tokens : 内联 token 预算；None 或 <=0 → 不落盘，原样返回
      tag               : 附加标识（如群号），进文件名便于人肉查找
      date_str          : 覆盖日期目录（测试用）
      resume_hint_fmt   : 自定义恢复指引模板，含 {path} 占位；缺省给通用提示

    返回 dict：
      {
        "inline":      内联文本（预览或原文）,
        "spilled":     是否真的落盘,
        "path":        落盘绝对路径 或 None,
        "truncated":   **预算事实**（是否因预算省略了内联内容）,
        "omitted_bytes": 精确省略字节数,
        "error":       落盘失败时的原因 或 None,
      }

    硬纪律：`truncated` 只表示**预算**问题；若落盘失败，`error` 单独记录，
    且 inline 退回**完整原文**（绝不给一个读不到的路径）。
    """
    text = text if isinstance(text, str) else str(text or "")
    if not max_inline_tokens or max_inline_tokens <= 0:
        return {"inline": text, "spilled": False, "path": None,
                "truncated": False, "omitted_bytes": 0, "error": None}

    if estimate_tokens(text) <= max_inline_tokens:
        # 预算内 → 原样通过（DSH「有意原样通过的」）
        return {"inline": text, "spilled": False, "path": None,
                "truncated": False, "omitted_bytes": 0, "error": None}

    # ── 落盘（失败则退回原文，绝不给坏路径）─────────────────────────
    day = date_str or datetime.datetime.now().strftime("%Y-%m-%d")
    day_dir = os.path.join(SPILL_DIR, day)
    fname = f"{_safe_name(text)}-{kind or 'text'}{('-' + str(tag)) if tag else ''}.txt"
    path = os.path.join(day_dir, fname)
    error = None
    try:
        os.makedirs(day_dir, exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
    except Exception as e:
        error = repr(e)
        return {"inline": text, "spilled": False, "path": None,
                "truncated": False, "omitted_bytes": 0, "error": error}

    # ── 内联首尾预览（两端各用剩余预算的一半）───────────────────────
    from .retention import TextRetainer
    text_bytes = text.encode("utf-8", "ignore")
    total_bytes = len(text_bytes)
    # 预留省略提示与路径行本身的空间
    notice_room = 220
    budget_bytes = max(64, max_inline_tokens * 2 - notice_room)  # 粗略：1 token≈2 字节(中文3/英1)
    head_b = budget_bytes // 2
    tail_b = budget_bytes - head_b
    r = TextRetainer("headTail", head_b, tail_b)
    r.push(text_bytes)
    preview, _trunc, omitted_bytes = r.finish()

    if resume_hint_fmt:
        hint = resume_hint_fmt.format(path=path)
    else:
        hint = (f"Full formatted result stored at: {path}. "
                f"Use read with offset/limit, or grep this path to search within it.")

    notice = (f"\n\n(Omitted {omitted_bytes} bytes. {hint})")
    inline = preview + notice
    return {"inline": inline, "spilled": True, "path": path,
            "truncated": True, "omitted_bytes": omitted_bytes, "error": None}


def list_spills(day=None):
    """列出某天的 spill 文件（诊断用）。"""
    d = os.path.join(SPILL_DIR, day) if day else SPILL_DIR
    if not os.path.isdir(d):
        return []
    out = []
    for root, _dirs, files in os.walk(d):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                out.append((p, os.path.getsize(p)))
            except Exception:
                out.append((p, 0))
    return sorted(out)
