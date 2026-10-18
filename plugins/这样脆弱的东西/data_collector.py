# -*- coding: utf-8 -*-
"""数据收集器：把散落在各个子系统里的语料全部捞出来。

之前只接了四个源，实际上机器里至少有七处跟这个人有关的数据。
漏掉的那些恰恰是最难从聊天文本里推断出来的东西：

    self_learning.db        群聊发言（含群黑话、场景表达、好感度）
    group_chat_decider.db   话题流、群情绪记录、对话链、群画像
    super_memory.db         私聊记忆（含机器人的回复，可用于图灵测试与关系层）

除此之外还有两件事必须做对：

  1. **区分「没有数据」和「没有这个源」**。sandglass.txt 根本不存在，
     之前静默返回 0 条，看起来像"这人没说话"，其实是文件缺失。
     现在每个源都会报状态，扫描时直接告诉用户缺什么、影响是什么。
  2. **拒收污染源**。`nexsandglass/persona/persona.md` 是安装时附带的
     **Demo 示例画像**（文件里明确写着「仅供 Demo 参考」），
     把它当语料会把「AI 记忆系统用户 / Windows / Python 3.11+」这种
     虚构设定蒸馏成被蒸馏者的人格。这类文件必须识别并剔除。

输出统一为消息列表 + 结构化事实（facts），两者都能挂证据编号。
"""
import json
import os
import re
import sqlite3
import sys

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(os.path.dirname(PLUGIN_DIR))   # resources/app

CHAT_LOGS_DIR = os.path.join(PROJECT, "chat_logs")
PRIVATE_MEMORY_DIR = os.path.join(PROJECT, "private_memory")

# 污染源特征：文件自称是示例/模板
_DEMO_MARKERS = ("示例", "仅供demo", "仅供 demo", "demo数据", "模板文件",
                 "样例", "安装后系统会自动生成")


# ── 定位 ────────────────────────────────────────────────

def _db_candidates(*relatives):
    return [os.path.join(PROJECT, r) for r in relatives
            if os.path.exists(os.path.join(PROJECT, r))]


def _existing_db_paths():
    """super_memory.db 的真实位置（可能有 ego/ 前缀或没有）。"""
    return _db_candidates(
        "plugins/ego/super_memory/data/super_memory.db",
        "plugins/super_memory/data/super_memory.db")


def _self_learning_path():
    return _db_candidates("self_learning_db/self_learning.db",
                          "plugins/self_learning/data/self_learning.db")


def _group_decision_path():
    return _db_candidates("plugins/group_chat_decider/data/group_chat_decider.db",
                          "group_chat_decider/data/group_chat_decider.db")


def _neurobase_candidates():
    """NeuroBase 数据根的候选列表（按优先级）。

    排第一的是**程序自己的 `sandglass_paths._NB`** —— 那是这套系统的
    单一真相来源，用户哪天改了路径，插件自动跟着走，不需要改一行代码。
    后面依次是环境变量、插件配置、程序目录旁的 `.neurobase`（可移植布局）、
    以及默认的 `~/.neurobase`。
    """
    out = []
    for d in (PROJECT, os.path.join(PROJECT, "nexsandglass")):
        if os.path.isdir(d) and d not in sys.path:
            sys.path.insert(0, d)
    try:
        import sandglass_paths
        nb = getattr(sandglass_paths, "_NB", "")
        if nb:
            out.append(("程序 sandglass_paths", nb))
    except Exception:
        pass

    env = os.environ.get("NEXSANDBASE_HOME")
    if env:
        out.append(("环境变量 NEXSANDBASE_HOME", env))

    try:
        cfg_home = _cfg_flag("neurobase_home", "")
    except Exception:
        cfg_home = ""
    if cfg_home:
        out.append(("插件配置 neurobase_home", os.path.expanduser(str(cfg_home))))

    out.append(("程序目录旁的 .neurobase（可移植布局）",
                os.path.join(PROJECT, ".neurobase")))
    out.append(("用户主目录（默认）",
                os.path.join(os.path.expanduser("~"), ".neurobase")))

    seen, uniq = set(), []
    for label, path in out:
        p = os.path.normcase(os.path.abspath(path))
        if p not in seen:
            seen.add(p)
            uniq.append((label, path))
    return uniq


def _neurobase_dir():
    """实际使用的 NeuroBase 数据根（第一个存在的；都不存在就用首选）。"""
    for _, path in _neurobase_candidates():
        if os.path.isdir(path):
            return path
    return _neurobase_candidates()[0][1]


def _neurobase_label():
    """当前用的是哪一个候选（写进源状态，让用户一眼看到数据从哪来）。"""
    for label, path in _neurobase_candidates():
        if os.path.isdir(path):
            return f"{label}：{path}"
    return f"未找到（首选 {_neurobase_candidates()[0][1]}）"


def _sandglass_path():
    """落沙文件：把**所有**候选根都扫一遍。

    多扫几个位置是有意的 —— 万一用户把 NeuroBase 搬到了别处而插件配置没跟上，
    至少还能在新的可移植位置找到它，而不是安静地报 0 条。
    """
    hits = []
    for _, root in _neurobase_candidates():
        p = os.path.join(root, "sandglass.txt")
        if os.path.exists(p) and p not in hits:
            hits.append(p)
    for p in _db_candidates("nexsandglass/sandglass.txt", "sandglass.txt"):
        if p not in hits:
            hits.append(p)
    return hits


def _existing_sandglass_paths():
    return _sandglass_path()


def _decision_particles_path():
    for _, root in _neurobase_candidates():
        p = os.path.join(root, "decision_particles.txt")
        if os.path.exists(p):
            return p
    return os.path.join(_neurobase_dir(), "decision_particles.txt")


def _persona_paths():
    # 真实画像在 NeuroBase 下；程序目录里那份是随包附带的示例
    cands = [os.path.join(root, "persona", "persona.md")
             for _, root in _neurobase_candidates()]
    cands += _db_candidates("nexsandglass/persona/persona.md",
                            "nexsandglass/persona.md", "persona.md")
    return [p for p in cands if os.path.exists(p)]


def _open(path):
    conn = sqlite3.connect(path)
    conn.text_factory = str
    return conn


def _table_exists(conn, name):
    return conn.execute(
        "select name from sqlite_master where type='table' and name=?",
        (name,)).fetchone() is not None


# ── 通用 ────────────────────────────────────────────────

_HEADER_RE = re.compile(r"^\*\*(.+?)\*\*\s*`([\d:]+)`\s*$")


def _parse_chat_md(path):
    """解析 chat_logs 的 Markdown 格式，返回 [(sender, timestamp, text)]。"""
    blocks = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return blocks
    sender = ts = None
    buf = []

    def flush():
        if sender is not None:
            text = "\n".join(buf).strip()
            if text:
                blocks.append((sender, ts or "", text))

    for line in lines:
        m = _HEADER_RE.match(line.strip())
        if m:
            flush()
            sender, ts, buf = m.group(1).strip(), m.group(2), []
        elif line.strip() == "---":
            flush()
            sender, buf = None, []
        elif line.startswith("#"):
            continue
        else:
            buf.append(line)
    flush()
    return blocks


def _msg(text, ts, source, extra=None):
    if not text or not str(text).strip():
        return None
    out = {"text": str(text).strip(), "timestamp": str(ts or ""), "source": source}
    if extra:
        out.update(extra)
    return out


# ── 消息源 ──────────────────────────────────────────────

def _from_chat_logs(target_id, max_count):
    out = []
    log_dir = os.path.join(CHAT_LOGS_DIR, f"QQ_{target_id}")
    if not os.path.isdir(log_dir):
        return out
    for name in sorted(os.listdir(log_dir)):
        if not name.endswith(".md"):
            continue
        for sender, ts, text in _parse_chat_md(os.path.join(log_dir, name)):
            if sender in ("对方", "他", "她", "用户"):
                m = _msg(text, ts, f"chat_logs/{name}")
                if m:
                    out.append(m)
            if len(out) >= max_count:
                return out
    return out


def _from_super_memory(target_id, max_count):
    """私聊 + 群聊记忆。bot_reply 也带上：它是「对方怎么回应他」的证据。"""
    out = []
    for db_path in _existing_db_paths():
        try:
            conn = _open(db_path)
            rows = conn.execute(
                "SELECT user_msg, timestamp, bot_reply, scope FROM messages "
                "WHERE scope_id=? ORDER BY timestamp ASC LIMIT ?",
                (str(target_id), max_count)).fetchall()
            conn.close()
        except sqlite3.Error:
            continue
        for text, ts, bot, scope in rows:
            m = _msg(text, ts, "super_memory")
            if m:
                m["context"] = str(bot or "")[:200]
                m["scope"] = scope or ""
                out.append(m)
        break
    return out[:max_count]


# ── 落沙解析 ────────────────────────────────────────────
#
# 落沙的行格式是 `时间 | 说话人 | 正文`，但有两个坑：
#
#   1. `说话人` 只有 user / agent 两种，**真正的发送者嵌在正文里**：
#        [群20001] 10003: 小a是傻子吗     ← 群聊，带 uid
#        [10003] 小a是傻子吗                 ← 私聊，带 uid
#        你好                                    ← 裸 user，没带 uid
#      之前按「sender == 目标 id」去匹配，永远匹配不上，于是"0 条"。
#      实际上落沙里跟目标有关的行有 456 条。
#   2. 正文可以**跨行**（机器人回复常带换行），
#      所以必须用状态机逐行读，不能直接 splitlines 当一条一条。
#
# 还有一个必须处理的东西：落沙里混着**开发期的测试数据**
# （`我叫张三，是一个程序员`、`hello world, this is a test`、
# `我的生日是3月15号`…）。这些是当年调试人格用的假数据，
# 倒进去会把"张三"蒸馏成被蒸馏者本人。必须识别并剔除。

_SG_HEAD = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s*\|\s*([A-Za-z_]+)\s*\|\s*(.*)$")
# 老格式：[群<gid>] <uid>: 正文   /   [<uid>] 正文
_SG_GROUP = re.compile(r"^\[群\s*(\d+)\]\s*(\d{5,})\s*[:：]\s*(.*)$", re.S)
_SG_DIRECT = re.compile(r"^\[(\d{5,})\]\s*(.*)$", re.S)
# 新格式（信息更全）：[群:<gid>] [昵称(uid)] 正文   /   [昵称(uid)] 正文
_SG_CHANNEL = re.compile(r"^\[群\s*[:：]\s*(\d+)\]\s*", re.S)
_SG_WHO = re.compile(r"^\[([^\[\]]{1,60})\]\s*", re.S)
_SG_NICK_UID = re.compile(r"^(.*)\(\s*(\d{5,})\s*\)$", re.S)
# 正文里的引用标记：[引用:昵称(uid)] xxx  /  [当前] xxx
_SG_QUOTE = re.compile(r"^\[引用\s*[:：]\s*([^\]]{0,60})\]\s*")
_SG_CURRENT = re.compile(r"^\[当前\]\s*")

_SG_AGENT = ("agent", "assistant", "bot")
_SG_USER = ("user", "human", "")

# 测试/占位数据特征（命中即剔除，并统计给用户看）
_SG_FIXTURES = ("hello world", "this is a test", "lorem ipsum", "asdf",
                "张三", "李四", "王五", "示例", "测试用", "占位",
                "foo bar", "test message")


def _is_fixture(text):
    low = (text or "").lower()
    return any(mk in low for mk in _SG_FIXTURES)


# 只含平台标记、没有任何实际内容的消息（转发占位、图片占位等）
_SG_MARKER_ONLY = re.compile(
    r"^\s*(?:\[(?:合并)?转发消息\]|\[嵌套转发消息\]|\[图片\]|\[视频\]|\[语音\]"
    r"|\[表情[^\]]*\]|\[QQ红包\]|\[文件[^\]]*\]|\[当前\]|\[引用\]|[\s\n]+)+\s*$")


def _is_marker_only(text):
    return bool(_SG_MARKER_ONLY.match(text or ""))


_PARSE_CACHE = {}          # path -> (mtime, size, rows)；落沙是追加型文件，
                           # mtime+size 变了才重解析，render/scan 反复读不再重复解析 4080 行


def _parse_sandglass(path):
    """逐行解析落沙，返回 [{"ts","speaker","text"}]，正文里的换行保留。

    带缓存：按 mtime+size 判失效。落沙只会追加，解析结果按行数折叠，
    是收集阶段最重的单步（实测 4080 行），render 每次都要跑一遍。
    """
    try:
        st = os.stat(path)
        key = (st.st_mtime, st.st_size)
    except OSError:
        return []
    cached = _PARSE_CACHE.get(path)
    if cached and cached[0] == key[0] and cached[1] == key[1]:
        return list(cached[2])      # 浅拷贝列表壳，防调用方改坏缓存里的条目
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return rows
    for line in lines:
        m = _SG_HEAD.match(line)
        if m:
            rows.append({"ts": m.group(1), "speaker": m.group(2).lower(),
                         "text": m.group(3)})
        elif rows and line.strip():
            rows[-1]["text"] += "\n" + line        # 多行消息的续行
    _PARSE_CACHE[path] = (key[0], key[1], rows)
    return rows


def _sg_split_who(text):
    """把一条落沙正文拆成 (scope, uid, nick, body, reply_to)。

    能认的三种写法：
      [群:20001] [示例用户(10003)] 你好呀
      [示例用户(10003)] 你好
      [示例用户] 你最喜欢谁              ← 只有昵称，没有 uid

    reply_to 是引用标记里的对象（`[引用:昵称(uid)]`）——
    它回答「他在回复谁」，是按对象分桶关系层的关键证据。
    认不出来就返回 None（交给调用方计入"无法判定发送者"）。
    """
    t = (text or "").strip()
    scope = ""
    m = _SG_CHANNEL.match(t)
    if m:
        scope = "group:" + m.group(1)
        t = t[m.end():]
    reply_to = ""
    m = _SG_QUOTE.match(t)
    if m:
        reply_to = (m.group(1) or "").strip()
        t = t[m.end():]
    m = _SG_WHO.match(t)
    if not m:
        return None
    who = m.group(1).strip()
    t = t[m.end():].strip()
    nick, uid = who, ""
    mm = _SG_NICK_UID.match(who)
    if mm:
        nick, uid = mm.group(1).strip(), mm.group(2)
    t = _SG_CURRENT.sub("", t)
    return scope, uid, nick, t.strip(), reply_to


def _sg_nick_map(rows):
    """从落沙里学出「昵称 → uid」。这是平台自己给的对应关系，最可靠。"""
    votes = {}
    for r in rows:
        if r["speaker"] in _SG_AGENT:
            continue
        got = _sg_split_who(r["text"])
        if not got:
            continue
        _, uid, nick, _, _rt = got
        if uid and nick:
            votes.setdefault(nick, {})
            votes[nick][uid] = votes[nick].get(uid, 0) + 1
    return {nick: max(c.items(), key=lambda kv: kv[1])[0]
            for nick, c in votes.items()}


def _from_sandglass(target_id, max_count):
    """从落沙里取出**目标本人**的发言（群聊 + 私聊）。

    两遍扫描：先学出「昵称 → uid」，再据此判定每条消息是谁说的。
    这一步不能省 —— 落沙里同一个群里混着十几个人的发言，
    光看 `user` 字段分不出谁是谁，全算成目标的话语料就废了。

    顺便把机器人当时的回复挂成 context —— 这是判断"他怎么对待对方"的
    第一手材料，也能直接用于图灵测试的成对样本。
    """
    tgt = str(target_id)
    out = []
    stats = {"group": 0, "direct": 0, "bare": 0, "fixture_skipped": 0,
             "marker_skipped": 0, "others": 0, "unknown_sender": 0}
    bare_is_target = bool(_cfg_flag("sandglass_bare_is_target", True))

    # 只用**主**路径。候选列表里可能同时有「可移植位置」和「旧的主目录位置」，
    # 两个都读会把整个语料读两遍 —— 实测所有统计刚好翻倍才暴露出来。
    paths = _sandglass_path()
    if len(paths) > 1:
        _log_sandglass_note(
            f"发现 {len(paths)} 份落沙，只读主路径 {paths[0]}；"
            f"其余 {paths[1:]} 未读取（多半是迁移留下的旧数据，确认无误后可删）")
    path = paths[0] if paths else ""

    if path:
        rows = _parse_sandglass(path)
        nickmap = _sg_nick_map(rows)
        pending = None
        for r in rows:
            if r["speaker"] in _SG_AGENT:
                if pending is not None:
                    pending["context"] = r["text"][:400]
                    pending = None
                continue
            if r["speaker"] not in _SG_USER:
                continue
            raw = (r["text"] or "").strip()
            if not raw:
                continue
            if _is_fixture(raw):
                stats["fixture_skipped"] += 1
                pending = None
                continue
            if _is_marker_only(raw):
                stats["marker_skipped"] += 1
                pending = None
                continue

            # 老格式（带 uid 前缀）
            m = _SG_GROUP.match(raw)
            if m:
                gid, uid, body = m.group(1), m.group(2), m.group(3)
                if uid != tgt:
                    stats["others"] += 1
                    pending = None
                    continue
                stats["group"] += 1
                pending = _msg(body, r["ts"], f"sandglass/group:{gid}")
                if pending:
                    pending["scope"] = "group"
                    out.append(pending)
                continue
            m = _SG_DIRECT.match(raw)
            if m:
                uid, body = m.group(1), m.group(2)
                if uid != tgt:
                    stats["others"] += 1
                    pending = None
                    continue
                stats["direct"] += 1
                pending = _msg(body, r["ts"], "sandglass")
                if pending:
                    pending["scope"] = "private"
                    out.append(pending)
                continue

            # 新格式
            got = _sg_split_who(raw)
            if got:
                scope, uid, nick, body, reply_to = got
                if uid:
                    if uid != tgt:
                        stats["others"] += 1
                        pending = None
                        continue
                elif nickmap.get(nick) == tgt:
                    pass                      # 昵称对上了目标
                else:
                    stats["unknown_sender"] += 1     # 判不了是谁，宁可不收
                    pending = None
                    continue
                src = ("sandglass/" + scope) if scope else "sandglass"
                stats["group" if scope else "direct"] += 1
                pending = _msg(body, r["ts"], src)
                if pending:
                    pending["scope"] = "group" if scope else "private"
                    if reply_to:
                        pending["reply_to"] = reply_to
                    out.append(pending)
                continue

            # 完全裸行（早期日志，没有发送者信息）
            if bare_is_target:
                stats["bare"] += 1
                pending = _msg(raw, r["ts"], "sandglass")
                if pending:
                    pending["scope"] = "private"
                    pending["assumed_speaker"] = True
                    out.append(pending)
            else:
                stats["unknown_sender"] += 1
                pending = None
            if len(out) >= max_count:
                break

    global _LAST_SANDGLASS_STATS, _LAST_SANDGLASS_NICK, _LAST_SANDGLASS_NICKMAP
    _LAST_SANDGLASS_STATS = stats
    _LAST_SANDGLASS_NICK = next((n for n, u in nickmap.items() if u == tgt), "")
    _LAST_SANDGLASS_NICKMAP = dict(nickmap)
    return out[:max_count]


_LAST_SANDGLASS_STATS = {}
_LAST_SANDGLASS_NICK = ""
_LAST_SANDGLASS_NICKMAP = {}


def nickname_map():
    """「昵称 → uid」映射（由落沙消息的发送者标注学得）。"""
    return dict(_LAST_SANDGLASS_NICKMAP)
_SANDGLASS_NOTES = []
_CFG_CACHE = {}


def _log_sandglass_note(text):
    """落沙解析过程中的提示，collect() 会并进源状态里给用户看。"""
    _SANDGLASS_NOTES.append(text)


def _cfg_flag(key, default):
    """读插件配置（延迟导入，避免与 config 循环依赖）。"""
    if key not in _CFG_CACHE:
        try:
            from .config import get_config
            _CFG_CACHE[key] = get_config().get(key, default)
        except Exception:
            _CFG_CACHE[key] = default
    return _CFG_CACHE[key]


def _facts_sandglass(target_id):
    """决策粒子 + 从落沙学到的「昵称 ↔ uid」对应关系。"""
    facts = []
    if _LAST_SANDGLASS_NICK:
        facts.append({
            "text": f"平台日志里昵称「{_LAST_SANDGLASS_NICK}」对应的账号是 {target_id}"
                    "（由落沙消息的发送者标注学得）",
            "timestamp": "",
            "source": "neurobase/sandglass#nickname",
            "fact_kind": "昵称对应",
        })
    path = _decision_particles_path()
    if not os.path.exists(path):
        return facts
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return facts
    for ln in lines[-60:]:
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) < 4:
            continue
        ts, scene, choice = parts[0], parts[1], parts[2]
        mood = parts[3] if len(parts) > 3 else ""
        tag = parts[5] if len(parts) > 5 else ""
        if not choice or _is_fixture(scene):
            continue
        facts.append({
            "text": f"在「{scene}」这个选择上他选了「{choice}」"
                    + (f"（情绪 {mood}）" if mood and mood != "neutral" else "")
                    + (f"，被标注为「{tag}」" if tag and tag != "未分类" else ""),
            "timestamp": ts,
            "source": "neurobase/decision_particles",
            "fact_kind": "决策选择",
        })
    return facts


def _from_private_memory(target_id, max_count):
    out = []
    path = os.path.join(PRIVATE_MEMORY_DIR, f"{target_id}.json")
    if not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for r in data.get("rounds", []):
            m = _msg(r.get("user"), r.get("time"), "private_memory")
            if m:
                m["context"] = str(r.get("bot") or r.get("assistant") or "")[:200]
                out.append(m)
            if len(out) >= max_count:
                break
    except (OSError, ValueError):
        pass
    return out


def _from_self_learning(target_id, max_count):
    """自我学习库：群聊发言（含转发、图片占位）。这块之前完全漏掉了。"""
    out = []
    for db_path in _self_learning_path():
        try:
            conn = _open(db_path)
            if not _table_exists(conn, "messages"):
                conn.close()
                continue
            rows = conn.execute(
                "SELECT text, timestamp, group_id FROM messages WHERE user_id=? "
                "ORDER BY timestamp ASC LIMIT ?",
                (str(target_id), max_count)).fetchall()
            conn.close()
        except sqlite3.Error:
            continue
        for text, ts, gid in rows:
            m = _msg(text, ts, f"self_learning/group:{gid}")
            if m:
                m["scope"] = "group"
                out.append(m)
        break
    return out


# ── 结构化事实（不是发言，但是硬数据） ──────────────────

def _facts_self_learning(target_id):
    """好感度 / 群黑话 / 场景表达 —— 最难从文本推断的客观指标。"""
    facts = []
    for db_path in _self_learning_path():
        try:
            conn = _open(db_path)
            groups = set()
            if _table_exists(conn, "messages"):
                groups = {str(r[0]) for r in conn.execute(
                    "SELECT DISTINCT group_id FROM messages WHERE user_id=?",
                    (str(target_id),))}
            if _table_exists(conn, "affection"):
                for gid, score, inter, mood, last in conn.execute(
                        "SELECT group_id, score, interactions, mood, last_interaction "
                        "FROM affection WHERE user_id=?", (str(target_id),)):
                    facts.append({
                        "text": (f"在与群 {gid} 的互动中，累计互动 {inter} 次，"
                                 f"好感度评分 {score}，最近情绪基调 {mood}"),
                        "timestamp": last or "",
                        "source": "self_learning/affection",
                        "fact_kind": "关系强度",
                    })
            if _table_exists(conn, "jargon"):
                for word, cnt in conn.execute(
                        "SELECT word, count FROM jargon ORDER BY count DESC LIMIT 20"):
                    if not word:
                        continue
                    facts.append({
                        "text": f"群内黑话/高频短语「{word}」出现 {cnt} 次",
                        "timestamp": "",
                        "source": "self_learning/jargon",
                        "fact_kind": "群黑话",
                    })
            if _table_exists(conn, "expressions"):
                for scene, expr, quality in conn.execute(
                        "SELECT scene, expression, quality FROM expressions "
                        "ORDER BY quality DESC LIMIT 20"):
                    if not expr:
                        continue
                    facts.append({
                        "text": f"在「{scene}」场景下被记下的表达：{expr}"
                                f"（质量 {quality}）",
                        "timestamp": "",
                        "source": "self_learning/expression",
                        "fact_kind": "场景表达",
                    })
            conn.close()
        except sqlite3.Error:
            continue
        break
    return facts


def _facts_group_decider(target_id):
    """群体侧的客观记录：情绪分布、对话链、群画像。"""
    facts = []
    for db_path in _group_decision_path():
        try:
            conn = _open(db_path)
            if _table_exists(conn, "emotion_logs"):
                rows = conn.execute(
                    "SELECT emotion, COUNT(*) FROM emotion_logs GROUP BY emotion").fetchall()
                if rows:
                    total = sum(c for _, c in rows)
                    dist = "、".join(f"{e} {c}" for e, c in rows)
                    facts.append({
                        "text": f"群情绪记录共 {total} 条：{dist}",
                        "timestamp": "",
                        "source": "group_chat_decider/emotion",
                        "fact_kind": "群情绪分布",
                    })
            if _table_exists(conn, "conversation_chains"):
                try:
                    rows = conn.execute(
                        "SELECT chain_type, participants, message_count, last_activity "
                        "FROM conversation_chains ORDER BY last_activity DESC LIMIT 50"
                    ).fetchall()
                except sqlite3.Error:
                    rows = []
                for ctype, parts, cnt, last in rows:
                    if str(target_id) not in str(parts or ""):
                        continue
                    facts.append({
                        "text": f"参与过一条「{ctype}」型对话链，共 {cnt} 条消息，"
                                f"参与者 {parts}",
                        "timestamp": last or "",
                        "source": "group_chat_decider/chain",
                        "fact_kind": "对话链",
                    })
            if _table_exists(conn, "group_profiles"):
                for gid, hours, kws, interval, density, last, created in conn.execute(
                        "SELECT group_id, active_hours, topic_keywords, avg_msg_interval,"
                        " reply_density, last_active, created_at FROM group_profiles"):
                    facts.append({
                        "text": (f"所在群 {gid} 的画像：活跃时段 {hours}，"
                                 f"话题关键词 {kws}，平均消息间隔 {interval}，"
                                 f"回复密度 {density}"),
                        "timestamp": last or "",
                        "source": "group_chat_decider/profile",
                        "fact_kind": "群画像",
                    })
            conn.close()
        except sqlite3.Error:
            continue
        break
    return facts


# ── 数据源注册表 ────────────────────────────────────────

_SOURCES = {
    "chat_logs": _from_chat_logs,
    "super_memory": _from_super_memory,
    "sandglass": _from_sandglass,
    "private_memory": _from_private_memory,
    "self_learning": _from_self_learning,
}

SOURCE_LABEL = {
    "chat_logs": "聊天记录（chat_logs/*.md）",
    "super_memory": "记忆库（super_memory.db 私聊+群聊）",
    "sandglass": "落沙（NeuroBase 全量消息 sandglass.txt）",
    "private_memory": "私聊记忆（private_memory/*.json）",
    "self_learning": "自我学习库（self_learning.db 群聊发言）",
}

FACT_LABEL = {
    "关系强度": "好感度/互动次数（self_learning.affection）",
    "决策选择": "决策粒子（neurobase.decision_particles）",
    "群黑话": "群黑话词表（self_learning.jargon）",
    "场景表达": "场景化表达（self_learning.expressions）",
    "群情绪分布": "群情绪记录（group_chat_decider.emotion_logs）",
    "群画像": "群画像（group_chat_decider.group_profiles）",
    "对话链": "对话链（group_chat_decider.conversation_chains）",
}


def _inspect_persona_file():
    """检查 persona.md：是真实画像，还是随包附带的示例模板？

    这一步很关键 —— 示例文件里写的是「AI 记忆系统用户 / Windows / Python 3.11+」
    这类虚构设定，混进语料会把它们蒸馏成被蒸馏者的人格。
    """
    for path in _persona_paths():
        try:
            with open(path, "r", encoding="utf-8") as f:
                head = f.read(2000)
        except OSError:
            continue
        low = head.lower()
        if any(mk in low for mk in _DEMO_MARKERS):
            return {"path": path, "usable": False,
                    "reason": "文件自称是示例/模板（含「示例」「仅供Demo」等标记），"
                              "属于随包附带的假数据，接入会污染蒸馏"}
        return {"path": path, "usable": True, "reason": "看起来是真实生成的画像"}
    return None


# ── 对外接口 ────────────────────────────────────────────

def collect(target_id, sources=None, max_per_source=50000, include_facts=True):
    """收集目标人的全部语料与结构化事实。

    返回 messages / counts / total / facts / facts_by_kind / notes / missing。
    notes 记录每个源的状态（有、没有、为什么没有），直接给用户看。
    """
    from . import pollution
    sources = sources or list(_SOURCES.keys())
    messages, counts, notes, missing = [], {}, [], []

    for src in sources:
        fn = _SOURCES.get(src)
        if not callable(fn):
            notes.append(f"⚠ 未知数据源 {src}，已跳过")
            continue
        try:
            items = fn(str(target_id), int(max_per_source))
        except Exception as e:
            notes.append(f"⚠ {SOURCE_LABEL.get(src, src)} 读取失败：{e}")
            items = []
        counts[src] = len(items)
        if items:
            note = f"✓ {SOURCE_LABEL.get(src, src)}：{len(items)} 条"
            if src == "sandglass" and _LAST_SANDGLASS_STATS:
                st = _LAST_SANDGLASS_STATS
                note += (f"（群聊 {st['group']}、私聊 {st['direct']}、"
                         f"无发送者标记 {st['bare']}；已剔除：测试数据 "
                         f"{st['fixture_skipped']} 条、纯标记 "
                         f"{st.get('marker_skipped', 0)} 条、他人发言 "
                         f"{st['others']} 条、无法判定发送者 "
                         f"{st['unknown_sender']} 条）")
            notes.append(note)
        else:
            notes.append(f"· {SOURCE_LABEL.get(src, src)}：0 条")
            if src == "sandglass":
                if not _sandglass_path():
                    missing.append({
                        "source": src,
                        "reason": f"落沙文件不存在（找过 {_neurobase_dir()} 与程序目录）",
                        "impact": "缺全量落沙，会漏掉其他日志没记的发言",
                    })
                elif _LAST_SANDGLASS_STATS.get("fixture_skipped"):
                    missing.append({
                        "source": src,
                        "reason": "落沙里的行全部被判定为测试/占位数据",
                        "impact": "没有可用语料（这是主动剔除，不是读取失败）",
                    })
            elif src == "chat_logs" and not os.path.isdir(
                    os.path.join(CHAT_LOGS_DIR, f"QQ_{target_id}")):
                missing.append({
                    "source": src,
                    "reason": f"目录不存在（chat_logs/QQ_{target_id}/）",
                    "impact": "缺原始对话记录",
                })
            elif src == "private_memory" and not os.path.exists(
                    os.path.join(PRIVATE_MEMORY_DIR, f"{target_id}.json")):
                missing.append({
                    "source": src,
                    "reason": f"文件不存在（private_memory/{target_id}.json）",
                    "impact": "缺私聊记忆",
                })
        messages.extend(items)

    facts, facts_by_kind = [], {}
    if include_facts:
        notes.append(f"· NeuroBase 数据根：{_neurobase_label()}")
        for extra in list(_SANDGLASS_NOTES):
            if extra not in notes:
                notes.append(f"· 落沙：{extra}")
        del _SANDGLASS_NOTES[:]
        fact_groups = (_facts_self_learning(str(target_id))
                       + _facts_group_decider(str(target_id))
                       + _facts_sandglass(str(target_id)))
        for f in fact_groups:
            kind = f.get("fact_kind", "其他")
            facts.append(f)
            facts_by_kind[kind] = facts_by_kind.get(kind, 0) + 1
        if facts:
            detail = "、".join(f"{k} {v}" for k, v in facts_by_kind.items())
            notes.append(f"✓ 结构化事实：{len(facts)} 条（{detail}）")
        if not os.path.exists(_decision_particles_path()):
            notes.append("· 决策粒子（decision_particles.txt）：不存在")

    pf = _inspect_persona_file()
    if pf and not pf["usable"]:
        missing.append({
            "source": "persona.md",
            "reason": pf["reason"],
            "impact": "该文件已主动剔除（避免把示例设定当成这个人）",
        })
        notes.append("⛔ 已剔除 persona.md（示例文件，防止污染）")

    # ── 污染防控（最后一道闸）：测试会话 / 机器人侧消息 / 占位内容 ──
    clean, quarantined = pollution.detect(messages)
    qr = pollution.report(quarantined)
    if qr:
        removed = len(messages) - len(clean)
        notes.append(f"⛔ 污染防控：隔离 {qr['total']} 条（"
                     + "、".join(f"{k} {v}" for k, v in qr["by_reason"].items())
                     + f"），语料 {len(messages)} → {len(clean)} 条")
        for s in qr["samples"][:4]:
            notes.append(f"   · [{s['ts']}] {s['reason']}：{s['text']!r}")
        messages = clean
    else:
        notes.append("· 污染防控：未发现测试数据、机器人侧消息或占位内容")

    return {
        "messages": messages,
        "counts": counts,
        "total": len(messages),
        "facts": facts,
        "facts_by_kind": facts_by_kind,
        "notes": notes,
        "missing": missing,
        "quarantine": qr,
        "quarantine_items": quarantined,   # 完整清单（含指纹），供账本做证据级清洗
    }


def scan(target_id, sources=None, include_facts=True):
    """扫描数据量（含事实与源状态），不返回用于蒸馏的消息内容。

    同时给出「原始条数」和「去重后的独立发言数」—— 落沙里同一个短句会
    重复几十次，只报原始条数会让人高估语料量（实测 699 → 291）。
    """
    from . import evidence as _ev
    data = collect(target_id, sources=sources, max_per_source=10 ** 9,
                   include_facts=include_facts)
    raw_all = list(data["messages"]) + [dict(f, kind="fact") for f in data["facts"]]
    try:
        msgs, _, _, _ = _ev.normalize(raw_all)
        st = _ev.stats(msgs, raw_total=len(raw_all))
        distinct, facts_n = st["unique"], st["fact_count"]
    except Exception:
        distinct, facts_n = len(data["messages"]), len(data["facts"])
    return {
        "total_count": data["total"],
        "distinct_count": distinct,
        "counts": data["counts"],
        "facts_count": facts_n,
        "facts_by_kind": data["facts_by_kind"],
        "notes": data["notes"],
        "missing": data["missing"],
    }
