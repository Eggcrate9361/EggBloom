# -*- coding: utf-8 -*-
"""
沙漏记忆桥接层 — 连接 EggBloom 与沙漏记忆引擎

**实现为 EggBloom 自研 `sandglass_lite`**（2026-10-19）：此前依赖 `nexsandglass/`
（上游 NexSandglass 项目，MIT）的全量内嵌，审计确认只用到 6 模块 16 符号，
其余（MCP Server / TTY / 思维层 / 偏移率 / 情绪熵 / 画像 / 织布机）从不触发。
现已**彻底移除上游目录**，由 `sandglass_lite.py`（仓库根）提供全部能力：
  ① 落沙写入   ② 原文检索（倒排 + 密度×信任融合分）   ③ 按日期回忆 + 决策粒子

**数据完全兼容**：读写 `~/.neurobase/sandglass.txt` / `sandglass.idx`，
格式与上游一致 —— 既有 4080 行数据无需迁移。
若需回退上游，`pip install nexsandglass` 或从 git 历史恢复 `nexsandglass/` 目录，
再把 `_USE_LITE` 置 False。

非阻塞初始化：后台线程懒加载，不卡主流程。
增强记忆注入：搜索相关上下文 + 人物画像 + 最近对话摘要。
"""

import os
import re
import sys
import time
import json
import threading
from collections import defaultdict
from datetime import datetime, timedelta

# ── 实现后端：自研 sandglass_lite（唯一实现）──
# 2026-10-19：上游 nexsandglass/ 已彻底移除，本开关仅作语义标记保留。
_USE_LITE = True

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_initialized = False
_init_attempted = False
_provider = None
_lock = threading.Lock()
_enabled = True

# 人物画像缓存：{user_id: {"name": ..., "last_seen": ...}}
# ⚠ 必须**落盘**：这张表以前只活在内存里 → 每次重启归零，
#   于是刚重启完 Bot"真的不知道你是谁"（人物画像层永远为空，只能靠猜、甚至吹牛）。
#   实测：私聊日志里"你真的知道我是谁吗 → 知道啊，你不就是天天@我的那个嘛"，
#   紧接着被问"8月17号我干什么了"就只能"这我哪知道啊"。
# 注：曾有 traits（特征标签）字段 —— **从无写入方**（P2-2，2026-10-10 用户拍板删除）：
#   persona_kernel 按 scope 不按 user 维度存事实，也没有特征抽取管线，该字段是
#   纯死数据；读取端"traits 为空则只显示名字"的分支恒走空侧。与其挂着一个
#   "看起来有人写"的字段误导后续开发，不如删干净。特征若要回归，从
#   super_memory 检索或 LLM 定期提炼重新设计，别复用这个空壳。
_PEOPLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "people.json")
_people_map = defaultdict(lambda: {"name": "", "last_seen": 0})
_people_save_ts = 0.0
_PEOPLE_SAVE_INTERVAL = 10.0     # 落盘节流（秒）：避免每条消息都写盘


def _load_people():
    """启动时把人物画像读回内存（不存在/损坏都只记一行日志，绝不影响主流程）。"""
    try:
        if not os.path.isfile(_PEOPLE_PATH):
            return
        with open(_PEOPLE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        for uid, info in data.items():
            if not isinstance(info, dict):
                continue
            _people_map[str(uid)] = {
                "name": str(info.get("name") or ""),
                "last_seen": info.get("last_seen") or 0,
            }
        _log(f"[Sandglass] 人物画像已载入 {len(_people_map)} 人")
    except Exception as e:
        _log(f"[Sandglass] 人物画像载入失败（已忽略）: {e}")


def _save_people(force=False):
    """人物画像落盘（原子写：先写 .tmp 再 replace）。默认 10s 节流。"""
    global _people_save_ts
    now = time.time()
    if not force and (now - _people_save_ts) < _PEOPLE_SAVE_INTERVAL:
        return
    _people_save_ts = now
    try:
        os.makedirs(os.path.dirname(_PEOPLE_PATH), exist_ok=True)
        tmp = _PEOPLE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(_people_map), f, ensure_ascii=False)
        os.replace(tmp, _PEOPLE_PATH)
    except Exception as e:
        _log(f"[Sandglass] 人物画像落盘失败（已忽略）: {e}")


def _try_init():
    global _provider, _initialized, _init_attempted
    _init_attempted = True
    try:
        import sandglass_lite as _sl
        _sl.validate()                      # 建目录（幂等）
        os.environ.setdefault("NEXSANDBASE_HOME", _sl._NB)
        # 瘦 provider：只保留 bridge 真正用到的两个方法（元信息层 + 动态信号）
        _provider = _LiteProvider(_sl)
        _initialized = True
        _log(f"[Sandglass] 记忆系统初始化完成（自研 sandglass_lite，数据根 {_sl._NB}）")
    except Exception as e:
        _log(f"[Sandglass] 初始化失败: {e}")


class _LiteProvider:
    """轻量 provider —— 只提供 bridge 真正用到的两个方法。

    （原上游 `NexSandglassProvider` 有 516 行，含偏移率/情绪熵/画像等思层；
     2026-10-19 用户确认那些对群聊自然度无可证明贡献，随薄适配一并去掉。）
    """

    def __init__(self, core):
        self._core = core

    def initialize(self):
        self._core.validate()

    def shutdown(self):
        pass

    def is_available(self):
        return self._core.available()

    def system_prompt_block(self):
        """元信息层：沙漏总量 + 数据根。

        ⚠ 上游此处的【你在往哪走】+ 偏移率 + 阶段已按用户决定（2026-10-19）**去掉**
        —— 那是思层输出，对群聊自然度无可证明贡献，且需要整条 sandglass_think（1518 行）。
        """
        try:
            n = self._core.count()
            return f"沙漏: {n} 条"
        except Exception:
            return ""

    def prefetch(self, query=""):
        """每轮动态信号。原上游给"偏移 + 情绪 + 场景"，现只给最近几条的压缩提示。

        放 user 同轮（见 agent_memory.prefetch 的注释）：这些值每轮都变，
        塞进 system 会让前缀缓存每轮失效。
        """
        try:
            rows = self._core.recent(3)
            if not rows:
                return ""
            lines = []
            for _ln, ts, text in rows:
                t = str(text).strip().replace("\n", " ")
                if t:
                    lines.append(f"- {t[:40]}")
            return "\n".join(lines[-3:])
        except Exception:
            return ""


def _log(msg):
    """写入 bot_core 的日志文件"""
    try:
        from utils import log
        log(msg)
    except Exception:
        print(msg)


# P2-3：记忆检索在**每条消息的热路径**上，其 except 此前把异常无声吞成 ""。
# 直接 _log 会刷屏（同一故障每 3s 一条）。节流：同类错误每 60s 只记一条。
_ERRLOG_LAST: dict = {}


def _log_err_throttled(tag, e):
    now = time.time()
    if now - _ERRLOG_LAST.get(tag, 0) >= 60:
        _ERRLOG_LAST[tag] = now
        _log(f"[Sandglass] {tag} 异常（已降级为空结果，60s 内不再重复记）: {type(e).__name__}: {e}")


def _ensure_init():
    global _init_attempted
    if _initialized:
        return True
    if not _init_attempted:
        _init_attempted = True
        t = threading.Thread(target=_try_init, daemon=True, name="sandglass-init")
        t.start()
    return False


# 模块导入即载入人物画像（纯本地 json，毫秒级；失败只记日志）
_load_people()


def is_available():
    return _initialized and _provider is not None and _provider.is_available()


def log_user_message(text, group_id="", sender="", sender_nick="", timestamp=None):
    """落沙：用户消息写入沙漏（含完整上下文）"""
    if not _enabled or not is_available():
        return
    try:
        from sandglass_lite import log_message
        ts = timestamp or ""
        parts = []
        if group_id:
            parts.append(f"[群:{group_id}]")
        if sender_nick:
            parts.append(f"[{sender_nick}({sender})]")
        elif sender:
            parts.append(f"[{sender}]")
        if ts:
            parts.append(f"[{ts}]")
        prefix = " ".join(parts)
        line_text = f"{prefix} {text}" if prefix else text

        # 重复写保险丝（2026-10-10）：同一 (发送者+正文) 组成的整行在 2s 内落两次
        # = 同一条消息被重复派发。主修复在 memory_feed 的 user_pre_logged 路径闸门
        # （回复侧不再落用户行），这里只兜"未知的双派发"。
        # 注意判据是**整行相等**：不同人同一秒发同一句话 → 行首昵称不同 → 不会误杀。
        global _LAST_USER_LOG
        now = time.time()
        with _lock:
            last_line, last_ts = _LAST_USER_LOG
            if line_text == last_line and now - last_ts < 2.0:
                return
            _LAST_USER_LOG = (line_text, now)
        log_message(line_text, "user")

        # 更新人物画像（并落盘 —— 不然每次重启就归零）
        if sender:
            p = _people_map[sender]
            if sender_nick:
                p["name"] = sender_nick
            p["last_seen"] = time.time()
            _save_people()
    except Exception:
        pass


# 上一次 log_user_message 的整行与时间（保险丝用）
_LAST_USER_LOG: tuple = ("", 0.0)


def log_bot_reply(text, group_id=""):
    """落沙：Bot 回复写入沙漏。

    2026-10-10 格式收敛：
      · role 用 "agent"，**不再用 "assistant"** —— recall_by_date 按
        `agent=我 / 其它=对方` 判说话人，"assistant" 会把 Bot 自己的话
        当成对方；蒸馏侧 _SG_AGENT 同时认 agent/assistant/bot，无下游依赖这个旧标签。
      · 正文不再叠 "[Bot回复] [群xxx]" 前缀 —— 行首时间戳+role 已是分隔，
        群归属信息对用户行有、对 Bot 行价值低，且双格式正是污染源头之一。
    """
    if not _enabled or not is_available():
        return
    try:
        from sandglass_lite import log_message
        log_message(text, "agent")
    except Exception:
        pass


def extract_decision_particles(text):
    """从用户消息中提取决策粒子"""
    if not _enabled or not is_available():
        return
    try:
        from sandglass_lite import log_particle
        m = re.search(r'(.{2,20})还是(.{2,20})', text)
        if m:
            log_particle(f'{m.group(1).strip()}还是{m.group(2).strip()}', m.group(2).strip())
    except Exception:
        pass


def _build_people_context():
    """构建人物画像上下文

    （P2-2 拍板：traits 字段已删 —— 从无写入方的死字段。画像只输出名字。）"""
    lines = []
    for uid, info in _people_map.items():
        name = info.get("name", "")
        if not name:
            continue
        lines.append(f"  {name}")
    if lines:
        return "人物: " + "; ".join(lines[:5])
    return ""


def _scope_match(text, scope):
    """按会话作用域过滤沙漏检索结果（2026-10-11：私聊记忆泄漏进群聊实证修复）。

    写入格式：群用户行 `[群:gid] [昵称(uid)] 正文`；私聊用户行 `[昵称(uid)] 正文`；
    agent 行**无归属标记**（log_bot_reply 有意不加，见其注释）。
    scope=None → 不过滤（旧调用方行为逐字节不变）。
    ("group", gid)   → 只留本群用户行：私聊行/他群行/agent裸行全部出局
                       （Bot 在群里的话由会话历史与 timeline 提供，不缺这一路）；
    ("private", uid) → 只留该用户的私聊行：含 [群: 的行排除；agent 裸行排除
                       （无法归属是谁的对话，宁缺勿漏别人隐私）。
    """
    if not scope:
        return True
    kind, key = scope
    key = str(key or "")
    if not key:
        return True
    t = str(text)
    if kind == "group":
        return f"[群:{key}]" in t
    if kind == "private":
        if "[群:" in t:
            return False
        return f"({key})]" in t or f"[{key}]" in t
    return True


def _collect_results(message, limit=3, scored=False, scope=None):
    """关键词提取 + 多路检索 + 去重的共用体。

    scored=False：沿用旧行为（r[0] 当"分"排 —— 实际是行号，历史遗留，
      旧调用方对产出顺序有依赖，不动）。
    scored=True：走 vault.search_scored 真融合分，去重按行号保最高分。
    scope：会话作用域过滤（见 _scope_match），None=不过滤。
    返回 [(sort_key, text), ...]；scored 模式下 sort_key 是 [0,1] 相关性分。"""
    keywords = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', message)
    if not keywords:
        return []
    if scored:
        from sandglass_lite import search_scored
        by_ln = {}
        for kw in keywords[:3]:
            for ln, ts, text, score in search_scored(kw, limit=limit):
                t = str(text)[:150]
                if not _scope_match(t, scope):
                    continue
                if ln not in by_ln or score > by_ln[ln][0]:
                    by_ln[ln] = (score, t)
        return sorted(by_ln.values(), key=lambda x: -x[0])
    from sandglass_lite import search
    all_results = []
    for kw in keywords[:3]:
        results = search(kw, limit=limit)
        for r in results:
            if isinstance(r, tuple) and len(r) >= 3:
                text = str(r[2])[:150]
                if not _scope_match(text, scope):
                    continue
                if text not in [x[1] for x in all_results]:
                    all_results.append((r[0], text))
    return sorted(all_results, key=lambda x: -x[0])[:limit]


def _search_context_for_message(message, limit=3):
    """搜索与当前消息相关的记忆上下文"""
    if not _enabled or not is_available():
        return ""

    try:
        ranked = _collect_results(message, limit=limit, scored=False)
        if not ranked:
            return ""
        # 取最相关的几条
        context_lines = []
        for score, text in ranked[:limit]:
            context_lines.append(f"  - {text}")

        return "\n".join(context_lines)
    except Exception as e:
        _log_err_throttled("search_context", e)   # P2-3：不再无声吞掉（60s 节流）
        return ""


def search_scored_context(message, limit=5, scope=None):
    """2.0 阶段A：带相关性分数的检索出口（供 harness/memory_context builder 消费）。

    返回 [(score, text), ...] 按分数降序，score ∈ [0,1]；异常/未启用 → []。
    scope=(kind,key)：会话作用域过滤（2026-10-11 私聊泄漏群聊修复），见 _scope_match。"""
    if not _enabled or not is_available():
        return []
    try:
        return list(_collect_results(message, limit=limit, scored=True, scope=scope)[:limit])
    except Exception as e:
        _log_err_throttled("search_scored_context", e)
        return []


# ── 日期类召回（关键词检索对日期是瞎的）──────────────────────────────
# 实测：vault.search("8月17") 返回"2026-06-29 … 我才17岁"；search("2026-08-17") 返回
# 完全无关的"过线"。而且 vault.search() 虽然收了 month 参数，主路径 router.search(query,limit)
# **把 month 丢掉了**（只有 legacy 兜底才用）→ 按日期取数根本不通。
# 但沙漏是"每行一条、行首就是时间戳"的纯文本 → 直接扫文件按日期取，最稳。
def _vault_file():
    """沙漏数据文件。**必须用记忆子系统自己的路径解析**（sandglass_lite._NB，
    规则是"环境变量 > 程序目录旁 > 主目录"）—— 不要自己猜目录：
    实测本机同时存在两份且数据不一致，取错那份就会"记忆问不出来"。"""
    try:
        import sandglass_lite as _sl
        p = os.path.join(_sl._NB, "sandglass.txt")
        if os.path.isfile(p):
            return p
    except Exception:
        pass
    for c in (os.path.join(_ROOT, ".neurobase", "sandglass.txt"),
              os.path.join(os.path.expanduser("~"), ".neurobase", "sandglass.txt")):
        if os.path.isfile(c):
            return c
    return ""


def _resolve_date_targets(message):
    """从问句里解析要查的日期 → ['YYYY-MM-DD', ...]。只认第一个命中的表达。"""
    if not message:
        return []
    now = datetime.now()
    m = re.search(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})", message)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return ["%04d-%02d-%02d" % (y, mo, d)]
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", message) \
        or re.search(r"(?<!\d)(\d{1,2})[-/](\d{1,2})(?!\d)", message)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            y = now.year if (mo, d) <= (now.month, now.day) else now.year - 1
            return ["%04d-%02d-%02d" % (y, mo, d)]
    for kw, back in (("前天", 2), ("昨天", 1), ("今天", 0)):
        if kw in message:
            return [(now - timedelta(days=back)).strftime("%Y-%m-%d")]
    return []


def recall_by_date(message, limit=12):
    """问句里出现日期 → 把那天的记录捞出来；那天没记录就**明确说没有**（防编造）。

    limit：每天最多摘多少条；每条截 80 字，避免把 prompt 撑爆。"""
    dates = _resolve_date_targets(message)
    if not dates:
        return ""
    path = _vault_file()
    if not path:
        return ""
    hits = defaultdict(list)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                d10 = ln[:10]
                if d10 in dates:
                    parts = ln.rstrip("\r\n").split(" | ", 2)
                    if len(parts) == 3:
                        who = "我" if parts[1].strip() == "agent" else "对方"
                        hits[d10].append(parts[0][11:16] + " " + who + ": " + parts[2][:80])
    except Exception as e:
        _log(f"[Sandglass] 按日期召回失败（已忽略）: {e}")
        return ""
    out = []
    for d in dates:
        rows = hits.get(d) or []
        if rows:
            out.append("【%s 的记录（共 %d 条，摘前 %d）】\n%s"
                       % (d, len(rows), min(len(rows), limit),
                          "\n".join("- " + r for r in rows[:limit])))
        else:
            out.append("【%s】沙漏里没有这天的记录 —— 如实告诉对方「没找到这天的事」，不要编。" % d)
    return "\n\n".join(out)


def recall_history_block(message="", limit=3, width=140, min_score=0.0):
    """T7 批一 ★0：FTS5 历史召回（有界、只读、命中才产出）。

    手册《明日方舟》之外的调研结论里，这一项被标为「投入产出比最高的借鉴点」：
    沙漏的 FTS5 加速层早就存在（`sandglass_sqlite.py`，纯 stdlib、带自动降级），
    但一直没接到 prompt 组装链上 —— **零 LLM 成本的真实历史召回**。

    与 `get_memory_prompt_block(light=False)` 的区别：
      · 本函数**不带**人物画像与沙漏元信息，只做「相关历史」一小段，
        专供群聊路径按需取用（群聊此前完全没有历史召回）；
      · 私聊仍走 light 模式（语义检索已由 super_memory 向量 top-K 承担），
        避免同一份历史以两种来源重复注入。

    成本边界：最多 limit 条、每条截断 width 字符；无命中 / 未启用 / FTS 降级
    （`_search_context_for_message` 内部已 try/except 返回 ""）→ 返回 ""，零 token 成本。

    min_score>0（2.0 阶段A 阈值闸）：改走真融合分检索（search_scored_context），
    只注入 score≥min_score 的条目并按分数降序、行尾标注 `(0.xx)`；低于阈值一律不注。
    默认 0 = 旧行为逐字节不变（旧调用点与单测不受影响）。
    """
    if not _enabled or not is_available():
        return ""
    lines = []
    if min_score > 0:
        try:
            ranked = search_scored_context(message, limit=limit * 2)
        except Exception:
            return ""
        for score, text in ranked:
            if score < min_score:
                continue
            t = str(text).strip()
            if t:
                lines.append("  - %s (%.2f)" % (t[:width], min(1.0, score)))
            if len(lines) >= limit:
                break
        if not lines:
            return ""
    else:
        try:
            ctx = _search_context_for_message(message, limit=limit)
        except Exception:
            return ""
        if not ctx:
            return ""
        for line in str(ctx).splitlines():
            t = line.strip().lstrip("-").strip()
            if t:
                lines.append("  - " + t[:width])
        if not lines:
            return ""
    return ("【相关历史】以下是沙漏记忆中与当前话题相关的旧片段，"
            "仅供了解背景、可能不精确；不要主动提及你在检索记录，也不要复述原文：\n"
            + "\n".join(lines[:limit]))


def memory_feed(msg, scope="group"):
    """统一记忆分发入口 —— 一条消息只解析一次，四个记忆模块各自只取关心的字段。

    背景（1.5 任务 2.3）：sandglass_lite / ego.super_memory / self_learning /
    这样脆弱的东西 四个模块存的东西确实不同，但此前同一条消息会被 bot_core
    里四条独立路径各自解析一遍（重复 resolve_qq_nick、重复 extract_decision_particles、
    重复 sync_conversation）。收益是性能与可维护性，不合并存储。

    msg 字段（全部可选，缺省即跳过该模块）：
        text:        str  消息正文
        group_id:    str  群号，空串=私聊
        sender:      str  发送者 QQ
        sender_nick: str  发送者昵称（不传则内部 resolve）
        timestamp:   Any  时间戳（可选）
        reply:       str  仅当这是"问答对"时传入，触发回复侧写入
        user_pre_logged: bool 置 True 表示用户行已在消息入口落过沙
            （bot_core 群聊 7658 / 私聊 9193 的回复侧调用传此标志）。
            回复侧**只补 Bot 一行**——此前这里会把 merged_text 再记一遍
            （无 uid 的 [昵称] 格式）+ sync_conversation 又记一遍裸文本
            + Bot 回复记 agent/assistant 两遍，一条消息在沙漏里出现 2~3 次
            （2026-10-10 沙漏 21:27 三连实证）。FTS5 召回被重复条目挤占、
            画像/话题统计被重复计数放大、体积无谓膨胀 —— 全部修在此处。

    每个子模块独立 try/except，一个模块出错不影响其它模块。
    返回 dict 记录各模块是否真正落盘，便于日志与测试。
    """
    text = msg.get("text") or ""
    text = str(text).strip() if text else ""
    group_id = str(msg.get("group_id") or "")
    sender = str(msg.get("sender") or "")
    sender_nick = str(msg.get("sender_nick") or "")
    timestamp = msg.get("timestamp")
    reply = msg.get("reply") or ""
    reply = str(reply).strip() if reply else ""
    user_pre_logged = bool(msg.get("user_pre_logged"))

    out = {"sandglass": False, "super_memory": False, "self_learning": False, "distill": False}

    # ── 1. 沙漏：用户行落沙 + 决策粒子（回复侧调用跳过，见 user_pre_logged）──
    if text and not user_pre_logged:
        try:
            if _enabled and is_available():
                log_user_message(text, group_id, sender, sender_nick, timestamp)
                # 决策粒子只在用户消息上提取一次
                extract_decision_particles(text)
                out["sandglass"] = True
        except Exception:
            pass

    # ── 1b. 回复侧：Bot 行只写一次（role=agent）──
    # 不再走 sync_conversation —— 它把用户行又记一遍（裸文本、无 [群] 前缀），
    # 是 21:27 三连记录里第三条的来源；且它记 agent、log_bot_reply 又记
    # assistant，Bot 的话同一条存两遍。
    if reply:
        try:
            if _enabled and is_available():
                log_bot_reply(reply[:200], group_id)
        except Exception:
            pass

    # ── 2. ego.super_memory：事实提取 + 主动召回（走插件 after_reply 钩子）──
    # 该模块的重活由 bot_core 的 after_reply 钩子完成；此处只做"是否记录"的显式声明，
    # 避免把插件的 sqlite 写入复制到桥接层（那是插件自己的职责边界）。
    if text and reply:
        out["super_memory"] = True

    # ── 3. self_learning：群聊自主学习收集（仅群聊，私聊不参与）──
    if text and group_id and sender:
        try:
            from self_learning import SelfLearningEngine  # noqa: F401  (探测可用性)
            out["self_learning"] = True
        except Exception:
            pass

    # ── 4. 这样脆弱的东西：人格蒸馏走离线 collect()，实时 feed 只登记候选 ──
    # 蒸馏是对整段语料做批量建模，不做逐条实时写入；此处仅声明该消息已进入沙漏，
    # 蒸馏侧从 sandglass 落沙文件读取，无需重复存一份。
    if text:
        out["distill"] = True

    return out


def get_memory_prompt_block(message="", light=False, skip_search=False):
    """获取增强版记忆注入上下文

    四层结构：
    1. 人物画像（群内成员信息）
    2. 相关记忆（基于当前消息搜索）
    3. 沙漏元信息（总量 / 数据根）

    light=True（T4）：只注入轻量元信息 system_prompt_block，跳过关键词全文搜索，
    用于私聊记忆统一召回层（语义检索已由 super_memory 向量 top-K 承担）。
    skip_search=True（2.0 阶段C）：仅摘掉第二层「相关记忆」，其余层保留 ——
    私聊检索层已换 harness.memory_context builder（带分+阈值+预算），
    这里再搜一遍就是双注。agent 侧（harness/agent_memory）暂未接线，继续走旧层。
    """
    if not _enabled or not is_available():
        return ""

    parts = []

    # 第一层：人物画像
    people = _build_people_context()
    if people:
        parts.append(f"【群内人物】\n{people}")

    # 第二层：相关记忆搜索（light 模式跳过，省 token；builder 接线后 skip_search 也跳）
    if message and not light and not skip_search:
        ctx = _search_context_for_message(message)
        if ctx:
            parts.append(f"【相关记忆】\n{ctx}")

    # 第二层半：**日期类问题**（不受 light 闸门限制 —— 只是本地扫文件，很便宜）
    #   关键词检索对日期是瞎的（搜"8月17"能给"我才17岁"），必须按时间戳取。
    if message:
        try:
            bydate = recall_by_date(message)
            if bydate:
                parts.append(bydate)
        except Exception:
            pass

    # 第三层：元信息（阶段/偏移）
    try:
        block = _provider.system_prompt_block()
        if block and len(block) > 20:
            parts.append(block)
    except Exception:
        pass

    if parts:
        return "\n\n【沙漏记忆】\n" + "\n\n".join(parts)
    return ""


def search_memory(query, limit=5):
    """搜索沙漏记忆"""
    if not _enabled or not is_available():
        return ""
    try:
        from sandglass_lite import search
        results = search(query, limit=limit)
        if results:
            lines = []
            for r in results:
                # vault.search 返回的是 (行号, 时间, 明文) 三元组，**不是 dict**
                # ——旧代码 r.get(...) 每次都抛 AttributeError 被吞成空结果
                # （2026-10-11 21:06 日志实证），等于这个情绪推断记忆源从未生效。
                if isinstance(r, dict):
                    text = str(r.get("text", ""))[:200]
                    score = float(r.get("score", 0) or 0)
                elif isinstance(r, (tuple, list)) and len(r) >= 3:
                    text = str(r[2])[:200]
                    score = 0.5  # 三元组路径没有分数，给中性值
                else:
                    text = str(r)[:200]
                    score = 0.5
                if text:
                    lines.append(f"- [{score:.2f}] {text}")
            return "\n".join(lines)
        return ""
    except Exception as e:
        _log_err_throttled("search_memory", e)    # P2-3：不再无声吞掉（60s 节流）
        return ""


def get_status():
    """获取沙漏运行状态。

    ⚠ `stage` / `offset` 两个字段已置空（2026-10-19 用户决定）：它们来自思层
    （sandglass_think 的偏移率 + 阶段），对群聊自然度无可证明贡献，
    随薄适配一起去掉。保留键名以免下游 KeyError（agent_memory.status 读 stage）。
    """
    if not is_available():
        return {"status": "unavailable"}
    try:
        from sandglass_lite import count
        return {
            "status": "active",
            "messages": count(),
            "stage": "",
            "offset": {},
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
