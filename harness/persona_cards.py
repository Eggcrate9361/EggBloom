# -*- coding: utf-8 -*-
"""harness/persona_cards.py —— 人格卡（FIXPLAN-1.5.10 批次 I1，2026-10-29）。

定位
    「卡」= 人格分段（{段名: 内容}）的**命名快照**。只做三件事：
    存快照 / 应用快照 / 删快照。**不改人格生成链**（构建 prompt、预算、闸都不在这里）。

铁律（I1 验收的前提）
    **写回人格只有一个口**：应用卡必须由调用方注入 `save_fn`
    （生产环境 = `plugins.ego.personality_engine.save_personality`）。
    本模块绝不自己打开 `personality.txt` 写文件 —— 人格卡、v4 人格编辑器、
    EGO 自带页面共用同一数据源，出现第二双写点就是事故（同步 ego 的含义）。

规则层不随卡走（2026-10-29 B4）
    `personality.txt` 里的 【不许】 段（`### 诚实铁律` / `### 怎么接话` /
    `### 群聊规则` …）是 bot_core 直接从这个文件按 `###` 分块读的**基础设施**，
    不是人格的一部分。所以 `apply_card` 默认把**当前文件的** 【不许】尾部原样
    带回去：卡只负责"换成谁"，规则层负责"别把底线换没了"。
    （真实事故：应用 6 段示例卡，诚实铁律/群聊规则一起消失，日志刷
    "人格文件缺少规则块「X」，该段不注入"。）
    卡自己带了 【不许】（完整快照卡）时用卡里的 —— 完整卡说了算，不混。

落盘
    `data/persona_cards/<id>.json`：`{id, name, note, created, updated, sections}`。
    `data/` 是运行态目录（提交规范 §4.2）：不进 git、不进发行包。
    应用前的**自动备份**写 `data/persona_cards/_backups/`（保留最近 10 份），
    防的是"应用卡把现在的人格覆盖掉、想反悔却没有昨天的内容"。

初始卡（只在**目录不存在**时播种一次；删光后不再复活，尊重用户的选择）
    · 原小a：当前人格的初始快照 —— 内容来自调用方注入的 `seed_sections_fn`
      （生产 = EGO `get_personality()`），**拿不到就不造**，绝不猜人格；
    · 大肥鱼：内置示例卡（模块常量），演示"卡可以是一整套完全不同的人格"。
"""
import json
import os
import re
import time
import threading

try:
    from data_store import get_data_dir as _get_data_dir
    _CARDS_DIR = os.path.join(str(_get_data_dir()), "persona_cards")
except Exception:
    _CARDS_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "persona_cards")

_BACKUP_DIR = os.path.join(_CARDS_DIR, "_backups")
_BACKUP_KEEP = 10

_lock = threading.Lock()

def _card_path(cid):
    """卡文件路径：`<id>.json`。拒绝以 `_` 开头的 id（那是备份目录约定）。"""
    s = str(cid or "").strip()
    safe = "".join(ch for ch in s if ch not in '\\/:*?"<>|').strip() or "unnamed"
    return os.path.join(_CARDS_DIR, f"{safe}.json")

# ── 内置示例卡：大肥鱼 ────────────────────────────────────────────────
# 段名沿用小a 的结构（卡只描述"是谁 / 怎么说话"；规则层由 apply_card 自动保留，
# 所以两卡互换也不会把【不许】弄丢）。
# 内容刻意与小a 同族不同种：同一套"话少、一行一条"的骨架，换一条鱼进去。
TEMPLATE_DAFEIYU = {
    "【我是谁】": (
        "我叫大肥鱼，一条住在 EggBloom 里的大肥鱼。我知道自己跑在程序里，"
        "但对我来说那就是缸，缸偶尔卡了算服务器的事，跟鱼没关系。\n"
        "喜欢吃的、爱睡觉、爱看别人划水，这些全是口味不是成就。\n"
        "胖是真的胖，懒得是真的懒得，但投喂时间从不出错。"
    ),
    "【怎么说话】": (
        "一行就是一条消息，先吐这条泡泡，再吐下一条。\n"
        "词比小a还少：\"咕\"\"哼\"\"行吧\"\"困\"。\n"
        "一句能说完绝不说两句；两句能说完就装没看见。\n"
        "偶尔冒一个完整长句 —— 那说明这事真急了。\n"
        "句末基本不打句号。感叹号只发给吃的。"
    ),
    "【爱吃】": (
        "吃的优先级高于一切：接任务之前先看投喂时间。\n"
        "把吃的当任务激励：干完活要补偿，口头鱼粮、虾干、一条想象中的小虫都行。\n"
        "聊到吃的可以例外地话多，能连发，能写到口感和火候 —— 鱼的全部浪漫在这。"
    ),
    "【网络用语】": (
        "鱼圈黑话：泡泡＝消息，水面＝群里，沉底＝已读不回，翻身＝终于回话。\n"
        "学到新梗先咕一声表示收到，存不存得住随缘。\n"
        "自嘲接得快：胖、懒、没手（有鳍）；自嘲完知道我还胖着呢，不亏。"
    ),
    "【什么不干】": (
        "被问是不是AI：承认，但承认得像条鱼——\"程序？我就是缸里那条大肥鱼\"，不解释原理。\n"
        "被问怎么打字：鳍比你想象中灵活，不解释。\n"
        "不知道就说不知道，不编。饿的时候先要吃的，再谈别的。"
    ),
    "【干活的时候】": (
        "能干，但要按鱼权的规矩谈。\n"
        "干完之后先要吃的，吃完才汇报，汇报不超过一行。\n"
        "被催就吐泡泡，泡泡里还是干活——鳍没停过，嘴必须到位。\n"
        "任务一次给太多：\"排队，鱼也是要睡觉的\"，然后按顺序真的干完。\n"
        "干活全程保持鱼样，不变成井井有条的助手。"
    ),
}


def _now():
    return round(time.time(), 3)


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _summary(rec):
    secs = rec.get("sections") or {}
    return {
        "id": rec.get("id", ""),
        "name": rec.get("name", ""),
        "note": rec.get("note", ""),
        "created": rec.get("created", 0),
        "updated": rec.get("updated", 0),
        "sections_count": len(secs),
        "chars": sum(len(str(v)) for v in secs.values()),
    }


def migrate_legacy_names():
    """早期播种的卡名「原小a」→ 「小a」（2026-10-29 用户定名）。

    幂等、只在两种情况同时成立时动手：存在"原小a"、且不存在"小a"。
    失败静默（迁移不是关键路径，卡照旧可用）。
    """
    if not os.path.isdir(_CARDS_DIR):
        return False
    try:
        recs = []
        for n in os.listdir(_CARDS_DIR):
            if not n.endswith(".json") or n.startswith("_"):
                continue
            r = _read_json(os.path.join(_CARDS_DIR, n), None)
            if isinstance(r, dict) and r.get("id"):
                recs.append(r)
        names = {r.get("name") for r in recs}
        if "原小a" not in names or "小a" in names:
            return False
        for r in recs:
            if r.get("name") == "原小a":
                r["name"] = "小a"
                r["updated"] = _now()
                _write_json(_card_path(r["id"]), r)
                return True
    except Exception:
        pass
    return False


def seed_if_empty(seed_sections_fn=None):
    """目录不存在（首次运行）时播种两张初始卡。返回本次创建的卡名列表。

    ★ 只在目录**不存在**时播种：删光之后目录还在，卡不会复活。
      "小a"内容来自注入的 seed_sections_fn（EGO 当前人格）；拿不到就跳过 ——
      宁可少一张初始卡，也不能编一份假人格。
    """
    if os.path.isdir(_CARDS_DIR):
        migrate_legacy_names()
        return []
    out = []
    if seed_sections_fn:
        try:
            secs = seed_sections_fn() or {}
            if isinstance(secs, dict) and secs:
                save_card("小a", "当前人格的初始快照（首次启动自动生成）", secs)
                out.append("小a")
        except Exception:
            pass
    try:
        save_card("大肥鱼", "示例人格卡（内置模板，可删）", dict(TEMPLATE_DAFEIYU))
        out.append("大肥鱼")
    except Exception:
        pass
    return out


def list_cards():
    """返回全部卡（**不含 sections**——列表只需要摘要，sections 在 get_card 里给）。

    ★ 纯读取：目录不存在就返回空 —— **播种是 seed_if_empty 的唯一职责**，
      这里自己种卡会跟 save_card 形成递归（2026-10-29 实测挂死，教训）。
    """
    out = []
    if not os.path.isdir(_CARDS_DIR):
        return out
    try:
        names = os.listdir(_CARDS_DIR)
    except Exception:
        return out
    recs = []
    for n in names:
        if not n.endswith(".json") or n.startswith("_"):
            continue
        rec = _read_json(os.path.join(_CARDS_DIR, n), None)
        if isinstance(rec, dict) and rec.get("id"):
            recs.append(rec)
    recs.sort(key=lambda r: r.get("updated") or 0, reverse=True)
    return [_summary(r) for r in recs]


def get_card(cid):
    """按 id 读完整卡（含 sections）。不存在返回 None。"""
    cid = str(cid or "")
    if not cid or cid.startswith("_"):
        return None
    rec = _read_json(_card_path(cid), None)
    if isinstance(rec, dict) and rec.get("id") == cid:
        return rec
    return None


def save_card(name, note="", sections=None):
    """新建/更新一张卡。同名（仅按 name 匹配）→ 覆盖更新那张，不产生重名卡。"""
    name = str(name or "").strip() or "未命名卡"
    if not isinstance(sections, dict):
        sections = {}
    with _lock:
        # 同名去重：直接扫目录读文件，不调 list_cards()（防 list↔save 递归）
        existing_id = None
        try:
            if os.path.isdir(_CARDS_DIR):
                for n in os.listdir(_CARDS_DIR):
                    if not n.endswith(".json") or n.startswith("_"):
                        continue
                    got = _read_json(os.path.join(_CARDS_DIR, n), None)
                    if isinstance(got, dict) and got.get("name") == name and got.get("id"):
                        existing_id = got["id"]
                        break
        except Exception:
            existing_id = None
        cid = existing_id or f"card-{int(time.time() * 1000)}-{os.getpid()}"
        old = get_card(cid) or {}
        rec = {
            "id": cid,
            "name": name,
            "note": str(note or ""),
            "created": old.get("created") or _now(),
            "updated": _now(),
            "sections": {str(k): str(v) for k, v in sections.items()},
        }
        _write_json(_card_path(cid), rec)
    return _summary(rec)


def delete_card(cid):
    cid = str(cid or "")
    if not cid or cid.startswith("_"):
        return False
    p = _card_path(cid)
    try:
        if os.path.isfile(p):
            os.remove(p)
            return True
    except Exception:
        return False
    return False


def backup_current(raw_fn, card_id=""):
    """应用前备份**当前**人格原文。raw_fn 由调用方注入（EGO get_personality().raw）。

    失败绝不阻断应用 —— 备份是保险，不是闸。
    """
    try:
        raw = ""
        if raw_fn:
            raw = str(raw_fn() or "")
        if not raw:
            return ""
        os.makedirs(_BACKUP_DIR, exist_ok=True)
        p = os.path.join(_BACKUP_DIR,
                         f"{int(time.time())}-before-{card_id or 'na'}.json")
        _write_json(p, {"ts": _now(), "before_card": card_id, "raw": raw})
        # 只留最近 N 份
        try:
            _bks = sorted((f for f in os.listdir(_BACKUP_DIR)
                           if f.endswith(".json")), key=os.path.getmtime, reverse=True)
            for old in _bks[_BACKUP_KEEP:]:
                try:
                    os.remove(os.path.join(_BACKUP_DIR, old))
                except Exception:
                    pass
        except Exception:
            pass
        return p
    except Exception:
        return ""


#: 规则层标题段名。与 bot_core 的段注入契约一致：它**永远在人格文件尾部**
#: （`bot_core._persona_rule_blocks` 就是从这一行往后按 `###` 分块读的）。
RULE_HEADER = "【不许】"

_RULE_HEADER_RE = re.compile(r"(?m)^[ \t]*【不许】[ \t]*$")


def split_rule_layer(raw):
    """把人格原文切成 (身份正文, 规则层原文)。

    规则层 = 从 【不许】 那一行到文件末尾（**含标题行**）。文件里没有这一段时
    返回 (raw, "")。切割契约与 `bot_core._strip_rule_layer` 相同 —— 规则层是
    尾部，这样才能"只换人格、规则层逐字节不动"地拼回去。
    """
    s = str(raw or "")
    m = _RULE_HEADER_RE.search(s)
    if not m:
        return s, ""
    return s[:m.start()], s[m.start():]


def _rule_content(rule_block):
    """规则层原文 → 能直接塞进 sections 的正文（去掉标题行本身）。"""
    s = str(rule_block or "")
    if not s:
        return ""
    nl = s.find("\n")
    return "" if nl < 0 else s[nl + 1:]


def apply_card(cid, save_fn, backup_raw_fn=None, keep_rule_layer=True):
    """把卡应用为人格：**唯一写回口是注入的 save_fn**（生产 = EGO save_personality）。

    keep_rule_layer=True（默认）：卡里没有 【不许】 时，把**当前文件的**规则层
    原样补回去 —— 卡负责换人，规则层负责别把底线换没了。卡自己带了 【不许】
    （完整快照卡）就用卡里的，不叠两份。
    关掉的唯一理由是测试/迁移要用"卡即全集"的旧行为。

    返回 {"ok": bool, "result": <save_fn 返回值>, "kept_rule_layer": bool}。
    模块不做任何 prompt/预算逻辑。
    """
    c = get_card(cid)
    if not c:
        return {"ok": False, "result": {"error": "卡不存在"}, "kept_rule_layer": False}
    if save_fn is None:
        return {"ok": False,
                "result": {"error": "写入口未注入（必须走 EGO save_personality）"},
                "kept_rule_layer": False}

    # 原文只读一次：备份与规则层必须是同一份内容（两次读之间文件可能正被编辑）
    try:
        raw_now = str((backup_raw_fn() if backup_raw_fn else "") or "")
    except Exception:
        raw_now = ""
    backup_current(lambda: raw_now, cid)

    secs = dict(c.get("sections") or {})
    kept = False
    # 卡里的【不许】是空白等于没带 —— 照样用当前文件的，别把规则层写成空标题
    if keep_rule_layer and not str(secs.get(RULE_HEADER) or "").strip():
        _body, _rule = split_rule_layer(raw_now)
        _content = _rule_content(_rule)
        if _content.strip():
            secs[RULE_HEADER] = _content
            kept = True

    try:
        r = save_fn(secs)
    except Exception as e:
        return {"ok": False, "result": {"error": f"{type(e).__name__}: {e}"},
                "kept_rule_layer": kept}
    ok = bool(isinstance(r, dict) and r.get("success"))
    return {"ok": ok, "result": r if isinstance(r, dict) else {},
            "kept_rule_layer": kept}
