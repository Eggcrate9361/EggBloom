"""token_stats：按场景（群聊/私聊/网页任务）累计 LLM token 消耗，写 logs/token_stats.jsonl。

DeepSeek usage 字段：
  prompt_tokens / completion_tokens / total_tokens
  prompt_cache_hit_tokens / prompt_cache_miss_tokens（缓存命中/未命中）
目的：T5 观测基线，验证结构优化后缓存命中率与 token 降幅。
"""
import json
import os
import threading
import time

try:
    import model_caps as _caps
except Exception:                                    # 极端裁剪环境下的兜底
    _caps = None

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_STATS_FILE = os.path.join(_LOG_DIR, "token_stats.jsonl")

# ── P23：上下文压力的「锚点式」测量（T7 批二 · 对应 dsh-token-meter）────────
# 与上面的累加统计**互补**：累加回答「今天花了多少」，锚点回答「这一轮有多满」。
# 为什么要"锚点式"而不是靠累加推算：
#   · 累加会漂移（缓存命中、工具轮、重试都不好折算），推算出的"当前占用"不可信；
#   · 锚点取**真实一次调用的实测值**（API 回的 prompt_tokens），是硬事实。
# 不变量 A20：**是估算就必须明说** —— 拿不到实测时用字数启发式，并在记录里标
# estimated=True，绝不把启发式当精确值呈现给用户。
#
# 2026-10-21 修正：分母不再写死 65536。
#   实际模型（deepseek-v4-flash）窗口 1M，用 64k 当分母会让 prompt 在只占真实
#   窗口 3.7% 时就报 60% warn、5.3% 时报 85% high —— 全程误报，预警彻底失效。
#   现在分母统一问 model_caps（环境变量 EGGBLOOM_CTX_WINDOW 仍最优先）。
_CONTEXT_WINDOW = _caps.context_window() if _caps else int(
    os.environ.get("EGGBLOOM_CTX_WINDOW", "131072"))

# ── 双轨阈值（2026-10-21）──────────────────────────────────────────────
# 只按「占窗口百分比」报警在大窗口下没有意义：1M 窗口要 600k token 才 warn，
# 那时单次调用成本早已不可接受。所以再加一条**绝对量**轨道，表达成本语义：
#   · 窗口轨（ratio）：会不会撞上限 —— 85% 才算 high；
#   · 成本轨（tokens）：这一轮有多贵 —— 32k 就该留意，128k 已经很贵。
# 两轨取更严重的那一档。
PRESSURE_WARN = 0.60        # ≥60% 窗口：该留意了
PRESSURE_HIGH = 0.85        # ≥85% 窗口：该压缩/清理了
COST_WARN_TOKENS = 32000    # ≥32k prompt：成本上已经不便宜
COST_HIGH_TOKENS = 128000   # ≥128k prompt：单轮成本进入「该干预」区间
_CHARS_PER_TOKEN = 1.6      # 中文启发式（仅估算用，不用于实测路径）

_anchor_lock = threading.Lock()
_anchors = {}   # scope_key -> {prompt_tokens, window, ratio, level, drivers, estimated, at, scene}


def _pressure(ratio: float, tokens: int = 0):
    """返回 (level, drivers)。drivers 记录**所有**命中的轨道，便于排查是贵还是满。"""
    drivers = []
    if ratio >= PRESSURE_HIGH:
        level = "high"
    elif ratio >= PRESSURE_WARN:
        level = "warn"
    else:
        level = "low"
    if ratio >= PRESSURE_WARN:
        drivers.append("window")

    try:
        tok = int(tokens or 0)
    except (TypeError, ValueError):
        tok = 0
    if tok >= COST_HIGH_TOKENS:
        level = "high"
        drivers.append("cost")
    elif tok >= COST_WARN_TOKENS:
        if level == "low":
            level = "warn"
        drivers.append("cost")
    return level, drivers


def _pressure_level(ratio: float, tokens: int = 0) -> str:
    """兼容旧调用点：只给 ratio 时行为与之前一致（纯窗口轨）。"""
    return _pressure(ratio, tokens)[0]


def note_context_anchor(scope_key, *, prompt_tokens=None, prompt_chars=None,
                        window=None, scene=""):
    """在真实调用点给某个会话打一个「上下文体量」锚点。

    prompt_tokens：API 实测值（**优先**，anchored，estimated=False）
    prompt_chars：纯字数（仅在拿不到实测时才用，estimated=**True**，A20）
    返回本次锚点记录；参数不足或异常时返回 None（**绝不向调用方抛异常**）。
    """
    try:
        w = int(window or 0)
        if w <= 0:
            # 每次调用现问一次：换模型/改 EGGBLOOM_CTX_WINDOW 后不用重启模块
            w = _caps.context_window() if _caps else _CONTEXT_WINDOW
        if w <= 0:
            w = 131072
        est = False
        tok = int(prompt_tokens or 0)
        if tok <= 0:
            ch = int(prompt_chars or 0)
            if ch <= 0:
                return None
            tok = int(ch / _CHARS_PER_TOKEN)
            est = True
        if tok <= 0:
            return None
        ratio = round(tok / float(w), 4)
        _lvl, _drivers = _pressure(ratio, tok)
        rec = {
            "prompt_tokens": tok,
            "window": w,
            "ratio": ratio,
            "level": _lvl,
            "drivers": _drivers,       # 哪条轨道触发的：window / cost
            "window_source": (_caps.resolve().get("source") if _caps else "env"),
            "estimated": est,          # ★ A20：估算就明说
            "at": time.strftime("%H:%M:%S"),
            "scene": str(scene or ""),
        }
        with _anchor_lock:
            _anchors[str(scope_key or "-")] = rec
        return rec
    except Exception:
        return None


def context_pressure(scope_key=None, window=None):
    """查上下文压力。

    scope_key 给定 → 返回该会话的锚点（无数据返回 {}）；
    scope_key 为 None → 返回全部会话，**按压力降序**（排查"谁快满了"）。
    window 给定 → 用该窗口重算 ratio/level（不改变已存的锚点）。
    """
    try:
        with _anchor_lock:
            if scope_key is not None:
                rec = _anchors.get(str(scope_key))
                if not rec:
                    return {}
                rec = dict(rec)
                if window:
                    w = int(window)
                    if w > 0:
                        rec["window"] = w
                        rec["ratio"] = round(rec["prompt_tokens"] / float(w), 4)
                        rec["level"], rec["drivers"] = _pressure(
                            rec["ratio"], rec["prompt_tokens"])
                return rec
            out = []
            for k, v in sorted(_anchors.items(), key=lambda kv: -kv[1].get("ratio", 0)):
                rec = dict(v)
                if window:
                    w = int(window)
                    if w > 0:
                        rec["window"] = w
                        rec["ratio"] = round(rec["prompt_tokens"] / float(w), 4)
                        rec["level"], rec["drivers"] = _pressure(
                            rec["ratio"], rec["prompt_tokens"])
                rec["scope"] = k
                out.append(rec)
            return out
    except Exception:
        return {} if scope_key is not None else []


# 内存累计（webui 查询用）
_lock = threading.Lock()
_cumulative = {
    "group": {"requests": 0, "input": 0, "output": 0, "cache_hit": 0, "cache_miss": 0},
    "private": {"requests": 0, "input": 0, "output": 0, "cache_hit": 0, "cache_miss": 0},
    "web": {"requests": 0, "input": 0, "output": 0, "cache_hit": 0, "cache_miss": 0},
}


def _ensure_log_dir():
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
    except Exception:
        pass


def record(scene: str = "private", usage: dict = None, extra: dict = None,
           model: str = "", lane: str = ""):
    """记录一次 LLM 调用的 token 消耗。

    scene ∈ group/private/web；`lane` 区分同一次对话里的不同车道
    （main 主链 / tool 工具轮 / web 网页任务 / decide 决策）。

    为什么加 lane（1.5.9 §C.3.2）：只有 scene 时，一次群回复里主模型、
    工具轮、搜索兜底全算在同一格，分不清钱花在哪 —— 而这三者的单价和
    调用次数差一个数量级。
    """
    usage = usage or {}
    if not isinstance(usage, dict):
        usage = {}
    prompt = int(usage.get("prompt_tokens") or 0)
    output = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or 0)
    if total <= 0:
        total = prompt + output
    cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    cache_miss = int(usage.get("prompt_cache_miss_tokens") or 0)
    if cache_miss <= 0 and prompt > 0 and cache_hit > 0:
        cache_miss = prompt - cache_hit

    scene = scene if scene in _cumulative else "private"

    with _lock:
        c = _cumulative[scene]
        c["requests"] += 1
        c["input"] += prompt
        c["output"] += output
        c["cache_hit"] += cache_hit
        c["cache_miss"] += cache_miss

    _ensure_log_dir()
    line = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scene": scene,
        "model": str(model or ""),
        "lane": str(lane or ""),
        "prompt_tokens": prompt,
        "completion_tokens": output,
        "total_tokens": total,
        "cache_hit_tokens": cache_hit,
        "cache_miss_tokens": cache_miss,
        "extra": extra or {},
    }
    try:
        with open(_STATS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return line


def snapshot() -> dict:
    """返回累计快照（webui /api/token-stats 用）。"""
    with _lock:
        out = {}
        for scene, c in _cumulative.items():
            total_in = c["input"]
            out[scene] = {
                "requests": c["requests"],
                "input_tokens": c["input"],
                "output_tokens": c["output"],
                "total_tokens": c["input"] + c["output"],
                "cache_hit_tokens": c["cache_hit"],
                "cache_miss_tokens": c["cache_miss"],
                "cache_hit_rate": round(c["cache_hit"] / total_in * 100, 1) if total_in else 0.0,
            }
        out["all"] = {
            "requests": sum(c["requests"] for c in _cumulative.values()),
            "input_tokens": sum(c["input"] for c in _cumulative.values()),
            "output_tokens": sum(c["output"] for c in _cumulative.values()),
            "total_tokens": sum(c["input"] + c["output"] for c in _cumulative.values()),
            "cache_hit_tokens": sum(c["cache_hit"] for c in _cumulative.values()),
            "cache_miss_tokens": sum(c["cache_miss"] for c in _cumulative.values()),
        }
        _ti = out["all"]["input_tokens"]
        out["all"]["cache_hit_rate"] = round(out["all"]["cache_hit_tokens"] / _ti * 100, 1) if _ti else 0.0

        # 从 jsonl 日志聚合日趋势（跨重启仍有效）
        days = {}
        _today = time.strftime("%Y-%m-%d")
        # ★ 真窗口：近 7 天 = 含今天往前 6 个自然日；近 30 天 = 往前 29 个自然日。
        #   原实现用 `_ts >= today 00:00:00` 算 d7（= 今天），用月初算 m30（= 本月）
        #   → 两个窗口名不副实（"近7天"与"今天"数值一样，"近30天"月初会突然归零）。
        _d7_from = time.strftime("%Y-%m-%d", time.localtime(time.time() - 6 * 86400))
        _m30_from = time.strftime("%Y-%m-%d", time.localtime(time.time() - 29 * 86400))
        _today_n = 0
        _d7 = _m30 = _total = 0
        for row in history(100000):
            _ts = row.get("ts") or ""
            _d = _ts[:10]
            _n = int(row.get("total_tokens") or 0)
            if _d:
                days.setdefault(_d, {"d": _d, "n": 0})["n"] += _n
            _total += _n
            if _d and _d == _today:
                _today_n += _n
            if _d and _d >= _d7_from:
                _d7 += _n
            if _d and _d >= _m30_from:
                _m30 += _n
        out["days"] = [days[k] for k in sorted(days)][-14:]
        out["today"] = _today_n
        out["d7"] = _d7
        out["m30"] = _m30
        out["total"] = _total
        return out


def history(limit: int = 100) -> list:
    """读取最近 N 条 jsonl 记录。"""
    _ensure_log_dir()
    rows = []
    try:
        with open(_STATS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return rows[-limit:]


if __name__ == "__main__":
    # 自测
    record("group", {"prompt_tokens": 500, "completion_tokens": 100, "prompt_cache_hit_tokens": 450, "prompt_cache_miss_tokens": 50})
    record("private", {"prompt_tokens": 300, "completion_tokens": 80})
    record("web", {"prompt_tokens": 2000, "completion_tokens": 500, "prompt_cache_hit_tokens": 1500, "prompt_cache_miss_tokens": 500})
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
    print("history len:", len(history()))