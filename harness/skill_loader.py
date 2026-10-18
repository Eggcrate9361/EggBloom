# -*- coding: utf-8 -*-
"""harness/skill_loader.py —— Skill **三级渐进披露**（progressive disclosure）。

为什么
    Skill 全量注入是最贵也最蠢的做法（每个 skill 的完整说明动辄几千 token）。
    业界共识的三级披露（本项目采用）：
      **L1 元数据**（name + 一句话描述 + 触发词）→ 常驻，50~100 token/skill；
      **L2 正文**（SKILL.md 主体，含流程/命令/示例）→ 仅当被触发时**才**注入；
      **L3 资源**（references/ + scripts/ + assets/）→ 模型显式要读时才读文件。

    这是"省 token"的第二大来源（第一大是工具按需加载）。

目录约定（与 WorkBuddy / 常见 skill 生态一致，同时兼容 .workbuddy/skills）
    <root>/<skill-name>/SKILL.md          # L2 正文（frontmatter 可选）
    <root>/<skill-name>/references/*       # L3
    <root>/<skill-name>/scripts/*          # L3
    <root>/<skill-name>/assets/*           # L3

搜索根目录（按顺序，取存在的）
    1. 显式传入的 roots
    2. <cwd>/skills
    3. <cwd>/.workbuddy/skills
    4. ~/.workbuddy/skills
    5. <data_dir>/skills

frontmatter 支持极简 YAML（name / description / when_to_use / triggers），
解析失败不报错（用正文首行兜底）——**永不因为元数据不规范而丢掉 skill**。
"""
import json
import os
import re
import threading

_LOCK = threading.RLock()
_CACHE = {"roots": None, "skills": None, "mtime_sig": None}

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

#: L1 元数据中 description 的硬上限（字符）——常驻内容的预算红线
L1_DESC_MAX = 120
#: L2 正文注入时的默认字符上限
L2_BODY_MAX = 6000


def _data_dir():
    try:
        from data_store import get_data_dir
        return get_data_dir()
    except Exception:
        return os.path.join(os.getcwd(), "data")


# ── 技能停用集（技能设置页用）────────────────────────────────────────
def _disabled_path():
    try:
        return os.path.join(_data_dir(), "skill_disabled.json")
    except Exception:
        return os.path.join(os.getcwd(), "data", "skill_disabled.json")


_DISABLED = None


def _disabled_set():
    global _DISABLED
    if _DISABLED is None:
        try:
            with open(_disabled_path(), "r", encoding="utf-8") as f:
                d = json.load(f)
            _DISABLED = {str(x) for x in (d.get("disabled") or [])}
        except Exception:
            _DISABLED = set()
    return _DISABLED


def get_disabled():
    """当前被停用的技能名（排序列表）。"""
    return sorted(_disabled_set())


def set_disabled(names):
    """整体替换停用集并落盘；立即生效（清缓存）。"""
    global _DISABLED
    _DISABLED = {str(x) for x in (names or [])}
    try:
        os.makedirs(os.path.dirname(_disabled_path()), exist_ok=True)
        with open(_disabled_path(), "w", encoding="utf-8") as f:
            json.dump({"disabled": sorted(_DISABLED)}, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    reset_cache()


def default_roots():
    """技能扫描根目录。

    ★ 优先级：**应用自有目录**（<cwd>/skills、<cwd>/.workbuddy/skills、<data>/skills）
      只要有一个存在，就**只**扫它们 —— 不再回退到 ~/.workbuddy/skills。
    原因（用户反馈"skill 列表不对"）：主目录那份是别的工具链（WorkBuddy）的技能，
    会被扫进来混在 App 自己的技能里；App 没有自己的 skills/ 时列表就全是不相干的。"""
    cwd = os.getcwd()
    # ★ 程序**自带**技能目录（发行包：resources/app/skills）——
    #   服务进程的工作目录不一定是程序根（start.bat 从发行根启动），
    #   所以用本文件位置反推：harness/../skills。
    bundle = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           os.pardir, "skills"))
    app_level = [
        os.path.join(cwd, "skills"),
        bundle,
        os.path.join(cwd, ".workbuddy", "skills"),
        os.path.join(_data_dir(), "skills"),
    ]
    out = []
    for c in app_level:
        if os.path.isdir(c) and c not in out:
            out.append(c)
    if out:
        return out
    home = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills")
    return [home] if os.path.isdir(home) else []


def _parse_frontmatter(text):
    """返回 (meta: dict, body: str)。刻意宽松。"""
    m = _FM_RE.match(text or "")
    if not m:
        return {}, (text or "")
    raw_fm, body = m.group(1), m.group(2)
    meta = {}
    cur_key = None
    for line in raw_fm.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t" and cur_key:
            # 极简列表续行: "  - xxx"
            v = line.strip()
            if v.startswith("-"):
                meta.setdefault(cur_key, [])
                if isinstance(meta[cur_key], list):
                    meta[cur_key].append(v.lstrip("-").strip().strip("\"'"))
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        cur_key = k
        if not v:
            meta[k] = []
        elif v.startswith("[") and v.endswith("]"):
            meta[k] = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
        else:
            meta[k] = v.strip("\"'")
    return meta, body


def _first_sentence(body, limit=L1_DESC_MAX):
    t = re.sub(r"^#+\s*", "", (body or "").strip(), flags=re.MULTILINE)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    for sep in ("。", "！", "？", ".", "!", "?", "\n"):
        i = t.find(sep)
        if 0 <= i < limit:
            return t[: i + 1].strip()
    return t[:limit].strip()


def _scan(roots):
    """扫描全部 root，返回 {name: entry}。后出现的 root 不覆盖先出现的同名 skill。"""
    found = {}
    for root in roots:
        try:
            entries = sorted(os.listdir(root))
        except Exception:
            continue
        for name in entries:
            d = os.path.join(root, name)
            if not os.path.isdir(d) or name.startswith("."):
                continue
            md = os.path.join(d, "SKILL.md")
            if not os.path.isfile(md):
                md2 = os.path.join(d, "skill.md")
                if not os.path.isfile(md2):
                    continue
                md = md2
            if name in found:
                continue
            try:
                with open(md, "r", encoding="utf-8", errors="replace") as f:
                    raw = f.read()
            except Exception:
                continue
            meta, body = _parse_frontmatter(raw)
            disp = str(meta.get("name") or name).strip() or name
            desc = str(meta.get("description") or "").strip()
            if not desc:
                desc = _first_sentence(body)
            if len(desc) > L1_DESC_MAX:
                desc = desc[: L1_DESC_MAX - 1] + "…"
            trig = meta.get("triggers") or meta.get("when_to_use") or []
            if isinstance(trig, str):
                trig = [x.strip() for x in re.split(r"[,，;；]", trig) if x.strip()]
            found[name] = {
                "name": name,
                "display_name": disp,
                "description": desc,
                "triggers": list(trig)[:8],
                "dir": d,
                "skill_md": md,
                "root": root,
                "has_references": os.path.isdir(os.path.join(d, "references")),
                "has_scripts": os.path.isdir(os.path.join(d, "scripts")),
                "has_assets": os.path.isdir(os.path.join(d, "assets")),
            }
    return found


def _roots_signature(roots):
    parts = []
    for r in roots:
        try:
            parts.append(f"{r}:{int(os.path.getmtime(r))}")
        except Exception:
            parts.append(r)
    return "|".join(parts)


def discover(roots=None, *, force=False):
    """发现所有 skill（带缓存；目录 mtime 变化才重扫）。

    roots=None 时用 default_roots()；但若之前已显式传入 roots，则**沿用**它，
    避免"显式扫描后，后续 get()/l2_body() 又滑回默认目录"的静默错配。
    """
    if roots is None:
        with _LOCK:
            cached_roots = _CACHE.get("roots")
        roots = list(cached_roots) if cached_roots else default_roots()
    else:
        roots = list(roots)
    sig = _roots_signature(roots)
    with _LOCK:
        if not force and _CACHE["skills"] is not None and _CACHE["mtime_sig"] == sig:
            return dict(_CACHE["skills"])
    skills = _scan(roots)
    dis = _disabled_set()
    if dis:
        skills = {k: v for k, v in skills.items() if k not in dis}
    with _LOCK:
        _CACHE["roots"] = roots
        _CACHE["skills"] = skills
        _CACHE["mtime_sig"] = sig
    return dict(skills)


def get(name):
    return discover().get(name)


def l1_block(skills=None, *, limit=None):
    """L1：常驻的 skill 元数据清单（**极省 token**）。

    返回形如：
        【可用技能】按需调用 skill__load 获取详细步骤：
        - pdf: 处理 PDF 文件（读取/合并/拆分/表单）。触发词: pdf, 合并
        ...
    """
    sk = skills if skills is not None else discover()
    if not sk:
        return ""
    lines = []
    for name in sorted(sk.keys()):
        e = sk[name]
        line = f"- {e['display_name']}: {e['description']}"
        if e.get("triggers"):
            line += "（触发词：" + "、".join(e["triggers"][:5]) + "）"
        lines.append(line)
        if limit and len(lines) >= limit:
            break
    return ("【可用技能】下面是你可按需载入的专业技能。判断某个技能和当前任务相关时，"
            "调用 skill__load 工具并传 name 获取它的详细步骤（不要凭猜测操作）。\n"
            + "\n".join(lines))


def l2_body(name, *, max_chars=L2_BODY_MAX):
    """L2：载入某 skill 的正文（**触发后**才调用）。返回 dict。"""
    e = get(name)
    if not e:
        return {"ok": False, "error": f"技能不存在: {name}", "name": name}
    try:
        with open(e["skill_md"], "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except Exception as ex:
        return {"ok": False, "error": f"读取失败: {ex}", "name": name}
    _, body = _parse_frontmatter(raw)
    body = (body or "").strip()
    truncated = False
    if max_chars and len(body) > max_chars:
        body = body[:max_chars]
        truncated = True
    out = {
        "ok": True,
        "name": name,
        "display_name": e["display_name"],
        "description": e["description"],
        "body": body,
        "truncated": truncated,
        "dir": e["dir"],
        "resources": l3_index(name),
    }
    return out


def l3_index(name):
    """L3：列出可用资源（**只列路径**，不读内容）。"""
    e = get(name)
    if not e:
        return {}
    out = {}
    for kind in ("references", "scripts", "assets"):
        d = os.path.join(e["dir"], kind)
        if not os.path.isdir(d):
            continue
        files = []
        try:
            for fn in sorted(os.listdir(d)):
                p = os.path.join(d, fn)
                if os.path.isfile(p):
                    # 统一用正斜杠（跨平台稳定；也是模型更容易理解的写法）
                    files.append(kind + "/" + fn)
                elif os.path.isdir(p):
                    files.append(kind + "/" + fn + "/")
        except Exception:
            pass
        if files:
            out[kind] = files[:30]
    return out


def l3_read(name, rel_path, *, max_bytes=200000):
    """L3：读某个资源文件（**模型显式请求时**才调用）。相对 skill 目录，防越狱。"""
    e = get(name)
    if not e:
        return {"ok": False, "error": f"技能不存在: {name}"}
    base = os.path.abspath(e["dir"])
    target = os.path.abspath(os.path.join(base, rel_path or ""))
    if not target.startswith(base + os.sep) and target != base:
        return {"ok": False, "error": "路径越界（只允许读技能目录内的资源）"}
    if not os.path.isfile(target):
        return {"ok": False, "error": f"资源不存在: {rel_path}"}
    try:
        size = os.path.getsize(target)
        with open(target, "rb") as f:
            data = f.read(max_bytes)
        text = data.decode("utf-8", errors="replace")
        return {"ok": True, "path": rel_path, "size": size,
                "truncated": size > max_bytes, "content": text}
    except Exception as ex:
        return {"ok": False, "error": f"读取失败: {ex}"}


def reset_cache():
    with _LOCK:
        _CACHE["skills"] = None
        _CACHE["mtime_sig"] = None
        _CACHE["roots"] = None
