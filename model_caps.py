# -*- coding: utf-8 -*-
"""model_caps.py —— 「模型能装多少」的唯一真相源（上下文窗口 / 输出上限）。

为什么单独成模块（2026-10-21）
────────────────────────────────────────────────────────────────
在此之前，窗口假设散落在两处、彼此还不一致：

    token_stats._CONTEXT_WINDOW = 65536        # 压力分母
    harness/tool_runner.CONTEXT_CHARS = 48000  # 注释写「≈24k token 窗口」（按 1.6 字/token 实际 30k）

两个都是 64k 时代的数字。当实际模型换成 1M 上下文的 deepseek-v4-flash 后：

  · 压力预警：prompt 到 39k（占真实窗口 3.7%）就报 60% warn，到 56k（5.3%）报 85% high
    → 在还剩 94% 空间时喊「该压缩了」，预警彻底失真；
  · 工具结果预算与窗口脱钩（固定 1500 token）→ 1M 窗口兑现不了任何收益，
    超出的全部 spill 落盘，模型只能反复读文件。

所以把「模型能装多少」收敛成这里的一组函数，所有调用方都问它，不再各自拍数字。

设计约束
────────────────────────────────────────────────────────────────
· **零项目内依赖**：只 import 标准库。token_stats（根模块）与 harness.tool_runner（包内）
  都要反向引用它，带项目内依赖会制造循环导入。
· **不猜就明说**（沿用 A20）：表里的数字有出处；查不到时回落保守默认值并把
  source 标成 "default"/"env"，调用方可在日志里如实说明。
· **环境变量优先**：EGGBLOOM_CTX_WINDOW 仍然最优先 —— 换模型/换中转时不用改代码。
"""
import json
import os
import threading

# ── 默认与安全边际 ────────────────────────────────────────────────────
#: 表里查不到时的保守窗口（现代主流模型的常见值；宁可少给，不要溢出）
DEFAULT_CONTEXT_WINDOW = 131072
DEFAULT_MAX_OUTPUT = 16384

#: 窗口里允许给 prompt 用的比例。剩下 20% 留给：输出 token、
#: 分词器与启发式估算的偏差、以及服务端把 system/tools 打包时的额外开销。
SAFETY_RATIO = 0.8

#: 中文启发式：1 token ≈ 1.6 字符。必须与 token_stats / harness.spill 保持一致，
#: 否则「字符预算」和「token 预算」会在两套换算下打架。
CHARS_PER_TOKEN = 1.6

#: 官方文档出处：api-docs.deepseek.com「模型 & 价格」
#:   deepseek-v4-flash / deepseek-v4-pro：上下文 1M(1,048,576)，输出最大 384K(393,216)
#:   legacy 名 deepseek-flash 现由 V4.1-Flash 承接，窗口同为 1M
#: 其余为各厂商公开规格；匹配是「子串包含」，所以更具体的键必须排在更泛的键前面。
_TABLE = (
    # (子串, 窗口, 输出上限, 备注)
    ("moonshot-v1-8k",      8192,    4096,    "Moonshot 8k"),
    ("moonshot-v1-32k",     32768,   8192,    "Moonshot 32k"),
    ("moonshot-v1-128k",    131072,  16384,   "Moonshot 128k"),
    ("deepseek-v4",         1048576, 393216,  "DeepSeek V4 (1M/384K)"),
    ("deepseek-flash",      1048576, 393216,  "DeepSeek V4.1-Flash (1M/384K)"),
    ("deepseek-reasoner",   131072,  65536,   "DeepSeek R1 系"),
    ("deepseek-r1",         131072,  65536,   "DeepSeek R1 系"),
    ("deepseek-v3",         131072,  8192,    "DeepSeek V3 系"),
    ("deepseek-chat",       131072,  8192,    "DeepSeek V3 系"),
    ("claude",              200000,  32768,   "Claude 系"),
    ("gemini",              1048576, 65536,   "Gemini 系"),
    ("gpt-4.1",             1047576, 32768,   "GPT-4.1"),
    ("gpt-4o",              128000,  16384,   "GPT-4o"),
    ("gpt-5",               400000,  32768,   "GPT-5 系"),
    ("gpt",                 128000,  16384,   "OpenAI 通用回落"),
    ("o1",                  200000,  100000,  "o1 系"),
    ("o3",                  200000,  100000,  "o3 系"),
    ("qwen",                131072,  8192,    "Qwen 系"),
    ("glm",                 131072,  8192,    "GLM 系"),
    ("kimi",                262144,  16384,   "Kimi 系"),
    ("grok",                131072,  16384,   "Grok 系"),
    # 本地推理：Ollama / llama.cpp 默认 n_ctx 通常远小于云端，保守给 32k
    ("llama",               32768,   8192,    "本地 llama"),
    ("ollama",              32768,   8192,    "本地 Ollama"),
)

_MODEL_CACHE = {"mtime": None, "model": "", "path": None}
_CACHE_LOCK = threading.Lock()


def _config_candidates():
    """bot_config.json 的候选路径，顺序与 config.load_config 一致（程序目录优先）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    yield os.path.join(here, "bot_config.json")
    yield os.path.join(os.path.expanduser("~"), "AppData", "Local",
                       "EggBloom_v3", "bot_config.json")


def configured_model():
    """当前配置里的模型名。读不到就返回空串（绝不抛）。"""
    override = (os.environ.get("EGGBLOOM_MODEL") or "").strip()
    if override:
        return override
    for path in _config_candidates():
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        with _CACHE_LOCK:
            if _MODEL_CACHE["path"] == path and _MODEL_CACHE["mtime"] == mtime:
                return _MODEL_CACHE["model"]
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            model = str(data.get("model_name") or "").strip()
        except Exception:
            model = ""
        with _CACHE_LOCK:
            _MODEL_CACHE.update({"mtime": mtime, "model": model, "path": path})
        return model
    return ""


def resolve(model=None):
    """解析成 {window, max_output, source, model}。

    source 取值：env / table / default —— 调用方可以据此判断「这是实测规格还是回落值」。
    """
    env_window = (os.environ.get("EGGBLOOM_CTX_WINDOW") or "").strip()
    name = (model or configured_model() or "").strip()
    low = name.lower()

    if env_window:
        try:
            win = int(env_window)
            if win > 0:
                out = max_output(model=name, _skip_env=True)
                return {"window": win, "max_output": out,
                        "source": "env", "model": name}
        except (TypeError, ValueError):
            pass

    for needle, win, out, _note in _TABLE:
        if needle and needle in low:
            return {"window": win, "max_output": out,
                    "source": "table", "model": name}
    return {"window": DEFAULT_CONTEXT_WINDOW, "max_output": DEFAULT_MAX_OUTPUT,
            "source": "default", "model": name}


def context_window(model=None):
    """上下文窗口（token）。环境变量 EGGBLOOM_CTX_WINDOW 优先。"""
    try:
        return int(resolve(model)["window"])
    except Exception:
        return DEFAULT_CONTEXT_WINDOW


def max_output(model=None, _skip_env=False):
    """单次输出上限（token）。"""
    try:
        if _skip_env:
            low = (model or "").lower()
            for needle, _win, out, _note in _TABLE:
                if needle and needle in low:
                    return out
            return DEFAULT_MAX_OUTPUT
        name = (model or configured_model() or "").strip().lower()
        for needle, _win, out, _note in _TABLE:
            if needle and needle in name:
                return out
        return DEFAULT_MAX_OUTPUT
    except Exception:
        return DEFAULT_MAX_OUTPUT


def prompt_budget_tokens(model=None):
    """这一轮 prompt 允许占用的 token 上限（窗口 × 安全边际）。"""
    return max(1024, int(context_window(model) * SAFETY_RATIO))


def prompt_budget_chars(model=None):
    """同上，换算成字符（中文启发式），给按字符估算的调用方用。"""
    return int(prompt_budget_tokens(model) * CHARS_PER_TOKEN)


def chars_to_tokens(chars):
    """字符 → token 的启发式换算（与 token_stats / spill 同口径）。"""
    try:
        return int(int(chars) / CHARS_PER_TOKEN) + 1 if int(chars) > 0 else 0
    except (TypeError, ValueError):
        return 0


def describe(model=None):
    """一行可读摘要，专供日志。"""
    r = resolve(model)
    return (f"model={r['model'] or '-'} window={r['window']} "
            f"output={r['max_output']} budget={prompt_budget_tokens(model)} "
            f"source={r['source']}")


if __name__ == "__main__":
    print(describe())
