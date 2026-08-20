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

_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_STATS_FILE = os.path.join(_LOG_DIR, "token_stats.jsonl")

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


def record(scene: str = "private", usage: dict = None, extra: dict = None):
    """记录一次 LLM 调用的 token 消耗。scene ∈ group/private/web。usage 为 API 返回的 usage dict。"""
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
        _d7 = _m30 = _total = 0
        for row in history(100000):
            _ts = row.get("ts") or ""
            _d = _ts[:10]
            if _d:
                days.setdefault(_d, {"d": _d, "n": 0})["n"] += int(row.get("total_tokens") or 0)
            _total += int(row.get("total_tokens") or 0)
            if _ts >= (_today + " 00:00:00"):
                _d7 += int(row.get("total_tokens") or 0)
            if _d >= _today[:8] + "01":
                _m30 += int(row.get("total_tokens") or 0)
        out["days"] = [days[k] for k in sorted(days)][-14:]
        out["today"] = _d7
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