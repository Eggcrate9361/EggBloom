# -*- coding: utf-8 -*-
"""harness/tool_fs.py —— 工作区文件工具（read / write / edit / glob / grep）。

为什么是这几个、以及为什么这样设计
    对照 DeepSeek Harness（`D:\\deepseek` 的 dsh-tool-fs / dsh-tool-fs-search）
    与 Claude Code / Codex 的工具集，结论是：agent 真正的地基只有五件事
    —— **按模式找、按内容搜、读、写、改**。EggBloom 原本只有"按文件名模糊搜"
    （search_files），没有读/写/改，所以 agent 一旦要动真格的活就只能空转。

四项硬指标如何落地（这是本模块与"照抄 dsh"的区别）
    极高命中率
        1. `edit` **不猜**：`old_string` 在文件里出现的次数必须**恰好为 1**
           （除非显式 `replace_all`）。0 次 → 报"未找到"并给最接近的行；
           >1 次 → 报"出现 N 次"并给每处的行号，让模型带上下文重试。
           这一条把"改错文件/改错位置"这类不可逆错误直接消灭。
        2. 所有失败都**带下一步**：路径错 → 候选文件；匹配失败 → 行号与上下文。
           模型不需要重新猜，照着重调一次就成。
        3. `read` 带**行号**返回（模型引用行号即可精确 edit，不用复述原文）。
    极低成本
        1. `read` 输出带行号但**默认只给一段**，超预算截断并告知如何翻页
           （offset/limit），而不是把整个文件灌进上下文。
        2. `grep`/`glob` 结果**有界**（默认 50 条）并给出精确省略计数，
           和 harness 既有的 `truncated`/`spill` 纪律一致。
        3. `edit_many`（见 tool_workspace）让"改 N 处"只需一次往返。
    高效率
        1. 失败即返回可执行信息，不做无意义的重复读取。
        2. `glob` 结果按修改时间倒序（最近动过的文件优先），
           符合"我正在改的东西"这一最常见的真实意图。
    超级好用
        1. 路径写法宽容（见 workspace._norm_user_path）。
        2. 相对路径默认相对项目根，开箱即用。
        3. 错误信息是中文人话，不是 errno。

返回约定
    所有工具都返回 **str**（由 tool_runner 统一做 spill / 截断 / 回灌）。
    失败返回以 `[错误码]` 开头的说明文本，绝不抛异常。
"""
import os
import re
import fnmatch


def missing_arg(name, args, *, aliases=(), hint=""):
    """构造"缺参数"报错 —— **必须列出实际收到的键名**。

    为什么（极高命中率的关键一条）
        模型传错参数名（例如把 `old_string` 写成 `old`）是**最高频**的工具失败。
        若只回"缺少 old_string"，模型看不出自己错在哪，只能盲猜重试 —— 每次
        重试都是一次完整往返（贵且慢）。把"实际收到的键"回给模型，它下一轮就能
        自查纠正，**把重试从 2~3 次压到 1 次**。
    """
    got = sorted((args or {}).keys())
    got_s = "、".join(got) if got else "（无）"
    parts = [f"[错误] 缺少必需参数 {name}。"]
    if hint:
        parts.append(hint)
    parts.append(f"本次实际收到的参数：{got_s}")
    if aliases:
        near = [a for a in aliases if a in (args or {})]
        if near:
            parts.append(f"注意：你传的是 {near[0]}，正确名称是 {name}。")
    parts.append(f"请用正确名称重试一次：{name}=...")
    return " ".join(parts)


def _alias_hit(args, canonical, aliases=()):
    """参数别名容错：模型把 old_string 写成 old / old_str / search 等常见变体时，
    仍能取到值。返回 (值, 是否命中别名)。这是"超级好用"里最省事的一层。"""
    a = args or {}
    if canonical in a and a[canonical] is not None:
        return a[canonical], False
    for alt in aliases:
        if alt in a and a[alt] is not None:
            return a[alt], True
    return None, False


from . import workspace as _ws

# ── 预算（token 是钱，默认就要小）──────────────────────────────────
READ_DEFAULT_LINES = 200          # 不指定 limit 时读多少行
READ_MAX_LINES = 2000             # 单次上限
GLOB_DEFAULT_LIMIT = 50
GLOB_MAX_LIMIT = 300
GREP_DEFAULT_LIMIT = 50
GREP_MAX_LIMIT = 200

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".idea",
              ".vscode", "image_cache", ".workbuddy", "dist", "build"}
_TEXT_EXT_HINT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".html", ".htm", ".css", ".scss", ".sql", ".sh",
    ".ps1", ".bat", ".xml", ".csv", ".log", ".env", ".gitignore", ".example",
}


def _is_probably_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in _TEXT_EXT_HINT:
        return True
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
               ".mp3", ".mp4", ".wav", ".zip", ".7z", ".rar", ".exe", ".dll",
               ".pyc", ".so", ".dylib", ".woff", ".woff2", ".ttf", ".pdf"):
        return False
    return True   # 无扩展名默认当文本试


def _read_text(path):
    """读文本（多编码兜底）。返回 (text, encoding) 或抛异常。"""
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return f.read(), enc
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        return f.read(), "utf-8(replace)"


# ══════════════════════════════════════════════════════════════════
# read
# ══════════════════════════════════════════════════════════════════
def tool_read(args):
    """读文本文件，返回**带行号**的内容（模型可直接引用行号做 edit）。

    参数：path / offset（起始行，1-based）/ limit（行数）/ max_bytes
    """
    args = args or {}
    res = _ws.resolve(args.get("path"), must_exist=True)
    if not res.ok:
        return _ws.describe_error(res)
    path = res.path

    if os.path.isdir(path):
        return (f"[错误] 这是目录而不是文件：{_ws.rel(path)}\n"
                f"目录内可直接用 ws_glob（如 pattern='{os.path.basename(path)}/*'）"
                f"或 ws_grep（path='{_ws.rel(path)}'）来查看内容。")
    if not _is_probably_text(path):
        size = os.path.getsize(path)
        return (f"[跳过] {_ws.rel(path)} 看起来是二进制文件（{size} 字节），"
                f"不做文本读取。如需了解它，请改用相应工具或说明用途。")

    size = os.path.getsize(path)
    if size > _ws.MAX_READ_BYTES:
        return (f"[错误] 文件过大（{size} 字节，上限 {_ws.MAX_READ_BYTES}）。\n"
                f"请用 grep 定位你要的片段（path='{_ws.rel(path)}'），"
                f"或先说明你只想看哪一部分。")

    try:
        text, enc = _read_text(path)
    except Exception as e:
        return f"[读取失败] {_ws.rel(path)}: {type(e).__name__}: {e}"

    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return f"{_ws.rel(path)} 是空文件（0 行）。"

    try:
        offset = int(args.get("offset") or 1)
    except Exception:
        offset = 1
    offset = max(1, min(offset, total))
    try:
        limit = int(args.get("limit") or READ_DEFAULT_LINES)
    except Exception:
        limit = READ_DEFAULT_LINES
    limit = max(1, min(limit, READ_MAX_LINES))

    end = min(total, offset + limit - 1)
    # 行号宽度对齐（行号是模型精确定位的关键，必须稳定可读）
    width = len(str(end))
    body = []
    for i in range(offset - 1, end):
        body.append(f"{i + 1:>{width}}| {lines[i]}")

    head = f"{_ws.rel(path)}（共 {total} 行，编码 {enc}，本次显示 {offset}-{end} 行）"
    out = head + "\n" + "\n".join(body)
    if end < total:
        out += (f"\n\n（还有 {total - end} 行未显示。用 read(path, offset={end + 1}) "
                f"继续读取；行号即上文左侧数字，可直接用于 edit。）")
    return out


# ══════════════════════════════════════════════════════════════════
# write
# ══════════════════════════════════════════════════════════════════
def tool_write(args):
    """创建或**整体替换**一个文本文件。

    安全设计：整体替换一个已存在的文件是破坏性操作 → 默认要求显式确认
    （`overwrite=true`），否则拒绝并告知如何改（用 edit 或显式 overwrite）。
    这避免模型"以为在追加、实际把文件清空"。
    """
    args = args or {}
    res = _ws.resolve(args.get("path"), for_write=True)
    if not res.ok:
        return _ws.describe_error(res)
    path = res.path

    content = args.get("content")
    if content is None:
        return "[错误] 缺少 content 参数（要写入的完整文本）。"
    content = str(content)

    existed = os.path.exists(path)
    if existed and not args.get("overwrite"):
        old_size = os.path.getsize(path)
        return (f"[需要确认] {_ws.rel(path)} 已存在（{old_size} 字节）。\n"
                f"write 是**整体替换**，会清空原内容。\n"
                f"· 想改其中一部分 → 用 edit（精确替换，安全得多）\n"
                f"· 确实要整体重写 → 再调一次并带 overwrite=true")

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
    except Exception as e:
        return f"[写入失败] {_ws.rel(path)}: {type(e).__name__}: {e}"

    nlines = content.count("\n") + (1 if content else 0)
    verb = "已覆盖" if existed else "已创建"
    return f"{verb} {_ws.rel(path)}（{len(content.encode('utf-8'))} 字节，{nlines} 行）。"


# ══════════════════════════════════════════════════════════════════
# edit —— 本模块的**核心**：唯一性校验式精确替换
# ══════════════════════════════════════════════════════════════════
def _closest_lines(lines, needle, *, window=1, limit=5):
    """给"未找到匹配"时用的最接近行候选（按相似度）。

    这是"极高命中率"的关键一步：模型常见失败是缩进/空格差一点、
    或多行 old_string 少了一行。我们不丢一句"未找到"了事，而是把最像的地方
    连同**准确的行号与原样文本**摆出来，模型直接照抄即可成功。
    """
    n = needle.strip()
    if not n:
        return []
    first = n.splitlines()[0].strip()
    key = first[:40].lower()
    scored = []
    for i, line in enumerate(lines):
        s = line.strip().lower()
        score = 0
        if key and key in s:
            score = 100 - abs(len(s) - len(first))
        elif s and first:
            # 字符重叠率兜底
            common = sum(1 for a, b in zip(s, first.lower()) if a == b)
            score = common * 3
        if score > 20:
            scored.append((score, i))
    scored.sort(key=lambda t: (-t[0], t[1]))
    out = []
    for _, i in scored[:limit]:
        lo = max(0, i - window)
        hi = min(len(lines), i + window + 1)
        out.append((i + 1, lines[lo:hi], lo + 1))
    return out


def _apply_edit(path, old, new, replace_all, *, dry_run=False):
    """执行一次替换。返回 (ok, message, text_new)。"""
    try:
        text, _enc = _read_text(path)
    except Exception as e:
        return False, f"[读取失败] {_ws.rel(path)}: {type(e).__name__}: {e}", None

    if old == "":
        return False, "[错误] old_string 不能为空字符串。要新建文件请用 write。", None

    count = text.count(old)

    if count == 0:
        lines = text.splitlines()
        cands = _closest_lines(lines, old)
        msg = f"[未找到匹配] 在 {_ws.rel(path)} 里找不到 old_string 的原文。"
        if cands:
            msg += "\n最接近的位置如下（请**照抄**含缩进的原文，或改用行号定位）："
            for ln, chunk, start in cands:
                msg += f"\n  第 {ln} 行附近："
                for j, cl in enumerate(chunk):
                    msg += f"\n    {start + j:>6}| {cl}"
        else:
            msg += "\n（没有找到相似行。建议先用 grep 确认这段文本是否存在及其确切写法。）"
        return False, msg, None

    if count > 1 and not replace_all:
        lines = text.splitlines()
        positions = []
        idx = 0
        while True:
            idx = text.find(old, idx)
            if idx < 0:
                break
            positions.append(text.count("\n", 0, idx) + 1)
            idx += len(old)
            if len(positions) >= 10:
                break
        msg = (f"[匹配不唯一] old_string 在 {_ws.rel(path)} 里出现了 {count} 次"
               f"（行号：{', '.join(str(p) for p in positions)}"
               f"{'...' if count > len(positions) else ''}）。\n"
               f"为避免改错位置，已拒绝执行。请任选其一：\n"
               f"  · 扩大 old_string，把上下几行一起带上，使其唯一\n"
               f"  · 若确实要全部替换，带 replace_all=true 重调")
        for ln in positions[:3]:
            lo = max(0, ln - 2)
            hi = min(len(lines), ln + 1)
            msg += f"\n  第 {ln} 行附近："
            for j in range(lo, hi):
                msg += f"\n    {j + 1:>6}| {lines[j]}"
        return False, msg, None

    text_new = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    if dry_run:
        return True, f"（预演）将替换 {count if replace_all else 1} 处。", text_new

    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text_new)
    except Exception as e:
        return False, f"[写入失败] {_ws.rel(path)}: {type(e).__name__}: {e}", None
    return True, "", text_new


def tool_edit(args):
    """精确替换文件中的字面量文本（**唯一性校验**）。

    参数：path / old_string / new_string / replace_all / dry_run
    """
    args = args or {}
    res = _ws.resolve(args.get("path"), must_exist=True)
    if not res.ok:
        return _ws.describe_error(res)
    path = res.path
    if os.path.isdir(path):
        return f"[错误] 这是目录：{_ws.rel(path)}"

    old, _aliased = _alias_hit(args, "old_string",
                               ("old", "old_str", "oldText", "old_text",
                                "search", "find", "target", "from"))
    if old is None:
        return missing_arg(
            "old_string", args,
            aliases=("old", "old_str", "old_text", "search", "find", "target", "from"),
            hint="old_string = 要替换的**原文**（必须与文件内容逐字一致，含缩进）。")
    new, _ = _alias_hit(args, "new_string",
                        ("new", "new_str", "newText", "new_text",
                         "replace", "replacement", "to", "with"))
    if new is None:
        new = ""

    ok, msg, text_new = _apply_edit(path, str(old), str(new),
                                    bool(args.get("replace_all")),
                                    dry_run=bool(args.get("dry_run")))
    if not ok:
        return msg

    n = text_new.count(str(new)) if str(new) else 0
    delta = len(text_new.encode("utf-8")) - len(_read_text(path)[0].encode("utf-8"))
    if args.get("dry_run"):
        return msg

    # 回灌 diff 摘要（模型可见即已记录；也方便前端页渲染轨迹卡）
    lines_add = str(new).count("\n") + (1 if str(new) else 0)
    lines_del = str(old).count("\n") + 1
    return (f"已修改 {_ws.rel(path)}（替换 {1 if not args.get('replace_all') else 'all'} 处，"
            f"约 -{lines_del} +{lines_add} 行，字节变化 {delta:+d}）。")


# ══════════════════════════════════════════════════════════════════
# 多动作批处理 —— 省往返的核心（一次调用把"找→读→改"串完）
# ══════════════════════════════════════════════════════════════════
def tool_edit_many(args):
    """在**一次调用**里对多个文件做多处精确替换（顺序执行，可回滚感知）。

    参数
      edits: [ {path, old_string, new_string, replace_all?} , ... ]
      dry_run: 只预演不落盘

    为什么要有它（三项收益）
      极低成本：N 处修改从 N 次往返压成 1 次 → 少 N-1 轮 LLM 调用（每轮都要付
                整个上下文的钱）。
      极高命中率：任一子项校验失败 → **整批不落盘**（默认原子），
                  模型一次就能看到所有问题，而不是改到第 3 个才失败。
      高效率：先全部预演（dry_run 内部），全部通过才真正写入。
    """
    args = args or {}
    edits = args.get("edits")
    if not isinstance(edits, list) or not edits:
        return "[错误] edits 必须是非空数组，每项形如 {path, old_string, new_string}。"

    dry = bool(args.get("dry_run"))
    prepared = []      # (path, old, new, replace_all)
    problems = []

    for i, e in enumerate(edits, 1):
        if not isinstance(e, dict):
            problems.append(f"第 {i} 项不是对象。")
            continue
        r = _ws.resolve(e.get("path"), must_exist=True)
        if not r.ok:
            problems.append(f"第 {i} 项：{_ws.describe_error(r)}")
            continue
        p = r.path
        if os.path.isdir(p):
            problems.append(f"第 {i} 项：{_ws.rel(p)} 是目录。")
            continue
        old, _ = _alias_hit(e, "old_string",
                            ("old", "old_str", "old_text", "search", "find",
                             "target", "from"))
        if old is None:
            problems.append(
                f"第 {i} 项缺少 old_string（该项实际键："
                f"{'、'.join(sorted(e.keys())) or '（无）'}）。")
            continue
        new_v, _ = _alias_hit(e, "new_string",
                              ("new", "new_str", "new_text", "replace",
                               "replacement", "to", "with"))
        prepared.append((p, str(old), str(new_v or ""),
                         bool(e.get("replace_all"))))

    if problems:
        return "[批处理未执行] 有 %d 项不合法：\n  - %s" % (
            len(problems), "\n  - ".join(problems))

    # ── 预演：全部通过才真写（原子语义）──
    staged = {}    # path -> text
    detail = []
    for i, (p, old, new, ra) in enumerate(prepared, 1):
        base = staged.get(p)
        if base is None:
            try:
                base, _ = _read_text(p)
            except Exception as ex:
                return f"[批处理未执行] 第 {i} 项读取失败 {_ws.rel(p)}: {ex}"
        # 在暂存文本上做校验（同一文件的多处修改可累积）
        tmp = os.path.join(os.path.dirname(p), ".____edit_many_tmp")
        cnt = base.count(old)
        if cnt == 0:
            lines = base.splitlines()
            cands = _closest_lines(lines, old)
            msg = f"[批处理未执行] 第 {i} 项在 {_ws.rel(p)} 里找不到 old_string。"
            if cands:
                msg += "\n最接近的位置："
                for ln, chunk, start in cands[:3]:
                    msg += f"\n  第 {ln} 行附近："
                    for j, cl in enumerate(chunk):
                        msg += f"\n    {start + j:>6}| {cl}"
            return msg
        if cnt > 1 and not ra:
            return (f"[批处理未执行] 第 {i} 项在 {_ws.rel(p)} 里匹配到 {cnt} 处"
                    f"（不唯一）。请扩大 old_string 或对该项加 replace_all=true。")
        staged[p] = base.replace(old, new) if ra else base.replace(old, new, 1)
        detail.append(f"  {i}. {_ws.rel(p)}（{'全部' if ra else '1'} 处）")
        del tmp

    if dry:
        return "（预演通过，未写入）共 %d 处修改：\n%s" % (len(prepared), "\n".join(detail))

    written = []
    for p, txt in staged.items():
        try:
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(txt)
            written.append(_ws.rel(p))
        except Exception as e:
            return (f"[部分失败] 已写入 {len(written)} 个文件后失败：{_ws.rel(p)} "
                    f"{type(e).__name__}: {e}\n已受影响：{', '.join(written) or '无'}")

    return "已批量修改 %d 处，涉及 %d 个文件：\n%s" % (
        len(prepared), len(written), "\n".join(detail))


# ══════════════════════════════════════════════════════════════════
# glob
# ══════════════════════════════════════════════════════════════════
def _iter_files(root, *, max_depth=12):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, root)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        if depth > max_depth:
            dirnames[:] = []
            continue
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def tool_glob(args):
    """按文件名模式查找文件（`*`、`**`、`?`；结果**按最近修改排序**）。

    参数：pattern / path（搜索起始目录，默认工作区根）/ limit
    """
    args = args or {}
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        return "[错误] 缺少 pattern，例如 '*.py' 或 'harness/**/*.py'。"

    if args.get("path"):
        res = _ws.resolve(args.get("path"), must_exist=True, want_dir=True)
        if not res.ok:
            return _ws.describe_error(res)
        root = res.path
    else:
        root = _ws.workspace_root()

    try:
        limit = int(args.get("limit") or GLOB_DEFAULT_LIMIT)
    except Exception:
        limit = GLOB_DEFAULT_LIMIT
    limit = max(1, min(limit, GLOB_MAX_LIMIT))

    # `**/x` 与 `x` 都支持：统一按 fnmatch 对"相对路径"与"文件名"两种口径匹配
    has_slash = "/" in pattern or "\\" in pattern
    pat = pattern.replace("\\", "/")
    pat_bare = pat[3:] if pat.startswith("**/") else pat

    hits = []
    for fp in _iter_files(root):
        rp = os.path.relpath(fp, root).replace("\\", "/")
        name = os.path.basename(fp)
        if has_slash:
            matched = fnmatch.fnmatch(rp, pat) or fnmatch.fnmatch(rp, pat_bare)
        else:
            matched = fnmatch.fnmatch(name, pat)
        if matched:
            try:
                mt = os.path.getmtime(fp)
            except Exception:
                mt = 0
            hits.append((mt, rp))

    total = len(hits)
    hits.sort(key=lambda t: -t[0])          # 最近修改的优先
    shown = hits[:limit]

    if not total:
        return (f"未找到匹配 '{pattern}' 的文件"
                f"{'（在 ' + str(args.get('path')) + ' 下）' if args.get('path') else ''}。\n"
                f"提示：pattern 只匹配**文件名**；要跨目录用 '**/{pattern}'。")

    head = f"匹配 '{pattern}' 共 {total} 个文件，按最近修改排序显示 {len(shown)} 个："
    body = "\n".join(f"  {rp}" for _mt, rp in shown)
    out = head + "\n" + body
    if total > len(shown):
        out += f"\n\n（因上限省略 {total - len(shown)} 个；可提高 limit 或收窄 pattern。）"
    return out


# ══════════════════════════════════════════════════════════════════
# grep
# ══════════════════════════════════════════════════════════════════
def tool_grep(args):
    """用**正则**搜索文件内容（返回 `文件:行号: 内容`）。

    参数：pattern / path（文件或目录，默认工作区根）/ glob（文件名过滤）
          / ignore_case / limit / context（上下文行数）
    """
    args = args or {}
    pattern = str(args.get("pattern") or "")
    if not pattern:
        return "[错误] 缺少 pattern（正则表达式），例如 'def tool_read'。"

    flags = re.IGNORECASE if args.get("ignore_case") else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return (f"[正则非法] {e}\n建议：特殊字符要转义（如 '\\.'、'\\('）；"
                f"简单字面量搜索直接写原文即可。")

    if args.get("path"):
        res = _ws.resolve(args.get("path"), must_exist=True)
        if not res.ok:
            return _ws.describe_error(res)
        target = res.path
    else:
        target = _ws.workspace_root()

    try:
        limit = int(args.get("limit") or GREP_DEFAULT_LIMIT)
    except Exception:
        limit = GREP_DEFAULT_LIMIT
    limit = max(1, min(limit, GREP_MAX_LIMIT))
    try:
        ctx = max(0, min(int(args.get("context") or 0), 3))
    except Exception:
        ctx = 0

    gfilter = str(args.get("glob") or "").strip()

    files = []
    if os.path.isfile(target):
        files = [target]
    else:
        files = list(_iter_files(target))

    hits = []
    truncated_files = 0
    skipped_binary = 0
    for fp in files:
        if gfilter and not fnmatch.fnmatch(os.path.basename(fp), gfilter.replace("\\", "/").split("/")[-1]):
            continue
        if not _is_probably_text(fp):
            skipped_binary += 1
            continue
        try:
            if os.path.getsize(fp) > _ws.MAX_READ_BYTES:
                truncated_files += 1
                continue
            with open(fp, "r", encoding="utf-8", errors="replace", newline="") as f:
                lines = f.read().splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            if rx.search(line):
                rp = _ws.rel(fp)
                if ctx:
                    lo = max(0, i - ctx)
                    hi = min(len(lines), i + ctx + 1)
                    block = [f"{rp}:{j + 1}: {lines[j]}" for j in range(lo, hi)]
                    hits.append("\n".join(block))
                else:
                    hits.append(f"{rp}:{i + 1}: {line}")
                if len(hits) >= limit:
                    break
        if len(hits) >= limit:
            break

    if not hits:
        extra = ""
        if skipped_binary:
            extra += f"（跳过 {skipped_binary} 个二进制文件）"
        if truncated_files:
            extra += f"（跳过 {truncated_files} 个超大文件）"
        return (f"未找到匹配 /{pattern}/ 的内容{extra}。\n"
                f"提示：大小写敏感，可加 ignore_case=true；"
                f"限定类型可加 glob='*.py'。")

    out = f"匹配 /{pattern}/ 的结果（最多 {limit} 条）：\n" + "\n".join(hits)
    if len(hits) >= limit:
        out += f"\n\n（已达上限 {limit}，可能还有更多；请收窄 pattern 或加 glob 过滤。）"
    return out


# ══════════════════════════════════════════════════════════════════
# 工具注册数据（schema 与实现放一起 —— 单点定义，不会漂移）
# ══════════════════════════════════════════════════════════════════
_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_INT = {"type": "integer"}


def _fn(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


WORK_FS_TOOLS = [
    _fn("ws_read",
        "读取工作区内的文本文件，返回带行号的内容。行号可直接用于 edit。"
        "长文件用 offset/limit 翻页。",
        {"path": dict(_STR, description="文件路径（相对路径即相对项目根）"),
         "offset": dict(_INT, description="起始行号，1 起，默认 1"),
         "limit": dict(_INT, description=f"读取行数，默认 {READ_DEFAULT_LINES}")},
        ["path"]),
    _fn("ws_edit",
        "精确替换文件中的一段原文（含缩进必须完全一致）。"
        "要求 old_string 在文件中唯一，否则拒绝执行并给出候选位置 —— 这是为了不改错地方。",
        {"path": dict(_STR, description="文件路径"),
         "old_string": dict(_STR, description="要替换的原文（须与文件内容完全一致）"),
         "new_string": dict(_STR, description="替换后的文本；留空表示删除该段"),
         "replace_all": dict(_BOOL, description="为 true 时替换全部匹配（默认 false，要求唯一）"),
         "dry_run": dict(_BOOL, description="只预演不落盘")},
        ["path", "old_string"]),
    _fn("ws_glob",
        "按文件名模式查找文件（如 '*.py'、'**/*.json'）。结果按最近修改时间倒序。",
        {"pattern": dict(_STR, description="文件名模式，支持 * 和 ?"),
         "path": dict(_STR, description="搜索起始目录，默认项目根"),
         "limit": dict(_INT, description=f"最多返回条数，默认 {GLOB_DEFAULT_LIMIT}")},
        ["pattern"]),
    _fn("ws_grep",
        "用正则搜索工作区文件内容，返回 文件:行号: 内容。定位代码/日志时首选。",
        {"pattern": dict(_STR, description="正则表达式"),
         "path": dict(_STR, description="搜索的文件或目录，默认项目根"),
         "glob": dict(_STR, description="只搜匹配该模式的文件，如 '*.py'"),
         "ignore_case": dict(_BOOL, description="忽略大小写"),
         "context": dict(_INT, description="上下文字行数，0-3"),
         "limit": dict(_INT, description=f"最多返回条数，默认 {GREP_DEFAULT_LIMIT}")},
        ["pattern"]),
]

WORK_WRITE_TOOLS = [
    _fn("ws_write",
        "创建新文件，或整体重写已存在的文件（会清空原内容，需显式 overwrite=true）。"
        "只改一部分请用 ws_edit。",
        {"path": dict(_STR, description="文件路径"),
         "content": dict(_STR, description="要写入的完整文本"),
         "overwrite": dict(_BOOL, description="目标已存在时必须显式给 true 才会覆盖")},
        ["path", "content"]),
    _fn("ws_edit_many",
        "一次调用对多个文件做多处精确替换（原子：任一项校验失败则整批不落盘）。"
        "改多处时用它，比反复调用 ws_edit 省很多往返。",
        {"edits": {"type": "array", "description": "修改项列表", "items": {
            "type": "object", "properties": {
                "path": _STR, "old_string": _STR, "new_string": _STR,
                "replace_all": _BOOL}, "required": ["path", "old_string"]}},
         "dry_run": dict(_BOOL, description="只预演不落盘")},
        ["edits"]),
]

WORK_FUNCTIONS = {
    "ws_read": tool_read,
    "ws_edit": tool_edit,
    "ws_glob": tool_glob,
    "ws_grep": tool_grep,
    "ws_write": tool_write,
    "ws_edit_many": tool_edit_many,
}


# ══════════════════════════════════════════════════════════════════
# 一把注册（bot_core 只需调这一句 —— 接线越薄，越不会漏）
# ══════════════════════════════════════════════════════════════════
def register_all(*, root=None):
    """把工作型工具注册进 tool_registry，并返回实现字典。

    分组策略（对应四种风险级别）
      work  : ws_read / ws_glob / ws_grep —— 只读、零副作用 → 默认常驻
      write : ws_write / ws_edit / ws_edit_many —— 有副作用 → 私聊 + 写意图
    这样"注册"与"注入条件"在同处定义，不会出现"注册了但忘了给闸门"。
    """
    from . import tool_registry as _tr
    if root:
        _ws.set_root(root)
    read_only = {"ws_read", "ws_glob", "ws_grep"}
    fns = dict(WORK_FUNCTIONS)
    for sch in (WORK_FS_TOOLS + WORK_WRITE_TOOLS):
        name = sch["function"]["name"]
        _tr.register(name, sch,
                     fn=fns.get(name),
                     groups=(_tr.GROUP_WORK,) if name in read_only else (_tr.GROUP_WRITE,),
                     source="harness")
    return fns
