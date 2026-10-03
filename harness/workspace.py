# -*- coding: utf-8 -*-
"""harness/workspace.py —— 工作区（文件系统 + 进程）的**安全边界**。

为什么单独一层（而不是散在工具实现里）
    文件与进程类工具的最高风险不是"功能不够"，而是**越界**：
    模型给出 `../../`、绝对盘符、或一个符号链接，就能读写工作区之外的东西。
    如果把边界判断写在每个工具里，迟早会出现"某个工具忘了判"。
    所以：**边界只有一个实现**，所有工具都必须从 `resolve()` 拿路径。

设计（对齐现代 agent 的 sandbox 语义，但零依赖、纯 stdlib）
    - **根**：默认 = 项目根（bot_core.py 所在目录）。可用 data/config 覆盖。
    - **相对路径**：一律相对根解析（模型最自然的写法开箱即用）。
    - **绝对路径**：只有在根**之内**才接受；在之外 → 拒绝并给出根路径提示。
    - **越界尝试**：解析后 `realpath` 再比对根前缀（挡住 `..` 与符号链接逃逸）。
    - **拒绝是"领域事实"**：返回结构化原因，绝不并入 `truncated` 预算字段。

对外只暴露三件事
    resolve(path, *, must_exist=False, for_write=False) -> ResolveResult
    workspace_root() -> str
    describe_error(res) -> str      # 人话错误（带候选，降低重试成本）
"""
import os

# 根目录在首次使用时确定并缓存。bot_core 可用 set_root 覆盖。
_ROOT = {"path": None}
# 工作区允许的最大读取字节数（防"读一个 4GB 日志把内存打爆"）
MAX_READ_BYTES = 4 * 1024 * 1024


def _default_root():
    """默认根 = 仓库根（本文件上一级的上一级）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def set_root(path):
    """覆盖工作区根（bot_core 启动时按配置调用）。返回最终根。"""
    p = os.path.abspath(str(path)) if path else _default_root()
    _ROOT["path"] = os.path.realpath(p)
    return _ROOT["path"]


def workspace_root():
    if _ROOT["path"] is None:
        _ROOT["path"] = os.path.realpath(_default_root())
    return _ROOT["path"]


class ResolveResult:
    """一次路径解析的结果。

    字段分离原则（重要）
      ok      : 是否解析成功
      path    : 成功时的**绝对真实路径**
      reason  : 失败原因码（outside_workspace / not_found / is_dir / not_dir / empty）
      detail  : 给模型看的人话说明（可直接回灌）
      candidates : 失败时给出的候选路径（**这是"极高命中率"的关键**：
                   模型不用重新猜，直接照着候选再调一次即可成功）
    """

    __slots__ = ("ok", "path", "reason", "detail", "candidates")

    def __init__(self, ok, path=None, reason="", detail="", candidates=None):
        self.ok = ok
        self.path = path
        self.reason = reason
        self.detail = detail
        self.candidates = candidates or []

    def __repr__(self):
        return f"<ResolveResult ok={self.ok} path={self.path} reason={self.reason}>"


def _norm_user_path(raw):
    """归一化模型常写的路径写法。

    模型（尤其中文语境）常见变体：
      "文件 D:\\a\\b.txt"  → 去掉"文件 "前缀
      「C:/a/b」            → 去掉书名号/引号
      `./x`、`x`            → 保留（相对）
    这一步是"超级好用"的廉价实现：模型不用记规矩。
    """
    p = str(raw or "").strip()
    if not p:
        return ""
    # 去掉包裹的引号/书名号/反引号
    for a, b in (("\"", "\""), ("'", "'"), ("`", "`"), ("\u201c", "\u201d"),
                 ("\u300c", "\u300d"), ("\u300a", "\u300b")):
        if len(p) >= 2 and p.startswith(a) and p.endswith(b):
            p = p[1:-1].strip()
    # 去掉常见中文前缀
    for pre in ("\u6587\u4ef6", "\u8def\u5f84", "\u76ee\u5f55", "\u6587\u4ef6\u5939",
                "file", "path", "dir"):
        if p.lower().startswith(pre) and len(p) > len(pre):
            rest = p[len(pre):].lstrip(":： \t")
            if rest and (rest[0] in "/\\" or (len(rest) > 1 and rest[1] == ":")):
                p = rest
                break
    return p.strip()


def _inside(root, target):
    """target（已 realpath）是否在 root 之内（含自身）。"""
    if target == root:
        return True
    return target.startswith(root + os.sep)


def _suggest(root, name, *, limit=5):
    """在工作区里找与 name 同名的文件 → 作为候选返回（相对路径）。

    用途：模型给了"配置文件.json"但实际叫 "bot_config.json" 时，
    我们直接把候选摆出来，而不是让它再猜一轮。**这是省往返的核心手段。**
    """
    base = os.path.basename(name).lower()
    if not base:
        return []
    stem = os.path.splitext(base)[0]
    hits = []
    skip = {".git", "__pycache__", "node_modules", ".venv", "venv", "image_cache"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, root)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        if depth > 3:                     # 太深的候选没有参考价值
            dirnames[:] = []
            continue
        for fn in filenames:
            fl = fn.lower()
            if fl == base or (stem and stem in fl and len(stem) >= 3):
                rel = fn if rel_dir == "." else os.path.join(rel_dir, fn)
                hits.append(rel)
                if len(hits) >= limit:
                    return hits
    return hits


def resolve(path, *, must_exist=False, for_write=False, want_dir=False):
    """把模型给的路径解析成工作区内的绝对真实路径。

    参数
      must_exist : True 时要求目标存在（读/改/搜）
      for_write  : True 表示要写 → 允许目标不存在（但**父目录**必须存在且在界内）
      want_dir   : True 时要求目标必须是目录

    返回 ResolveResult（永不抛异常 —— 所有失败都变成结构化原因）。
    """
    root = workspace_root()
    raw = _norm_user_path(path)
    if not raw:
        return ResolveResult(False, reason="empty",
                             detail="未提供路径。请给出文件路径（可直接写相对路径，如 bot_core.py）。")

    cand = _suggest(root, raw) if must_exist else []

    try:
        if os.path.isabs(raw):
            target = os.path.realpath(raw)
        else:
            target = os.path.realpath(os.path.join(root, raw))
    except Exception as e:
        return ResolveResult(False, reason="bad_path",
                             detail=f"路径无法解析（{type(e).__name__}）: {raw}")

    # ── 越界判定（先判界，再判存在）──
    #  顺序很重要：先越界就回"越界"，避免用"不存在"泄露界外信息。
    if not _inside(root, target):
        return ResolveResult(
            False, reason="outside_workspace",
            detail=(f"路径超出工作区范围。工作区根目录是：{root}\n"
                    f"只能用根目录之内（含子目录）的路径；相对路径即相对该根目录。"))

    if want_dir and os.path.exists(target) and not os.path.isdir(target):
        return ResolveResult(False, reason="not_dir",
                             detail=f"该路径是文件而非目录：{target}",
                             candidates=cand)

    if must_exist and not os.path.exists(target):
        detail = f"文件不存在：{target}"
        if cand:
            detail += "\n工作区内存在以下相似文件，请从中选择或在其中修改：\n" + \
                      "\n".join(f"  - {c}" for c in cand)
        else:
            # 给出该目录下实际有什么，避免模型继续瞎猜
            parent = os.path.dirname(target)
            if os.path.isdir(parent):
                try:
                    names = sorted(os.listdir(parent))[:12]
                except Exception:
                    names = []
                if names:
                    detail += "\n但该目录下存在：\n" + "\n".join(f"  - {n}" for n in names)
            else:
                detail += f"\n（其父目录也不存在：{parent}）"
        return ResolveResult(False, reason="not_found", detail=detail, candidates=cand)

    if for_write:
        parent = os.path.dirname(target)
        # 允许**逐级新建**父目录（write 的语义是"创建文件"，
        # 因为父目录不存在就拒绝，会让模型被迫先调一次建目录 —— 多一轮往返）。
        # 但仍必须守住边界：逐级向上找到第一个已存在的祖先，确认它在界内。
        probe = parent
        while probe and not os.path.exists(probe):
            nxt = os.path.dirname(probe)
            if nxt == probe:
                break
            probe = nxt
        if probe and not _inside(root, os.path.realpath(probe)):
            return ResolveResult(False, reason="outside_workspace",
                                 detail=f"写入位置超出工作区：{parent}")
        if os.path.exists(parent) and not _inside(root, os.path.realpath(parent)):
            return ResolveResult(False, reason="outside_workspace",
                                 detail=f"写入目录超出工作区：{parent}")

    if want_dir and os.path.exists(target) and not os.access(target, os.R_OK):
        return ResolveResult(False, reason="no_permission",
                             detail=f"没有读取权限：{target}")

    return ResolveResult(True, path=target)


def rel(path):
    """把绝对路径转成相对根目录的展示路径（回灌给模型时更短、更好读）。"""
    try:
        r = os.path.relpath(path, workspace_root())
        return path if r.startswith("..") else r
    except Exception:
        return path


def describe_error(res):
    """把 ResolveResult 的失败翻译成**可直接回灌给模型**的文本。

    硬纪律：这是"领域失败"（没找到/越界/无权限），**不是** `truncated`（预算事实）。
    """
    if res.ok:
        return ""
    head = {
        "empty": "路径为空",
        "bad_path": "路径非法",
        "outside_workspace": "越界拒绝",
        "not_found": "文件不存在",
        "not_dir": "不是目录",
        "no_parent": "目录不存在",
        "no_permission": "无权限",
    }.get(res.reason, "路径错误")
    return f"[{head}] {res.detail}"
