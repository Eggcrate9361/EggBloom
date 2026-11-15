# -*- coding: utf-8 -*-
"""token 归因：model 是死字段（2026-10-27 核查报告第 3 项）

## 症状

`token_stats.record()` 的签名有 `model: str = ""`，但 bot_core 三个调用点
（主链 / 工具轮 / 网页轮）**都不传 model** —— 实测记录里 `model` 恒为 `""`。

于是成本分析只能说"花了多少"，不能说"花在哪个模型上"。

## 根因

模型名有多个来源（远程 deepseek / 本地 llama / ollama / qwen / lmstudio），
`_llm_config()` 是它们**唯一**的解析出口 —— 但它只 return，不留痕。

## 契约

1. `_llm_config()` 解析出模型名后要留下来
2. 三个记录点都要把 model 传进去
3. 记录里的 model 不能是空串
4. 各分支（远程/本地各驱动）都要记对名字

运行：python tests/test_token_model_attribution.py
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


def test_llm_config_records_model():
    """_llm_config 解析后要留下模型名。"""
    import bot_core as bc
    bc._LAST_MODEL_NAME = ""
    cfg = bc._llm_config()
    check("配置里有 model_name", bool(cfg.get("model_name")), cfg)
    check("_last_model_name() 与配置一致",
          bc._last_model_name() == cfg.get("model_name"),
          (bc._last_model_name(), cfg.get("model_name")))


def test_all_branches_record():
    """各驱动分支都要记上模型名。"""
    import bot_core as bc
    cases = [
        ("远程", {"use_local_llm": False}, None),
        ("ollama", {"use_local_llm": True, "llama_driver": "ollama",
                    "llama_model_name": "my-ollama"}, "my-ollama"),
        ("lmstudio", {"use_local_llm": True, "llama_driver": "lmstudio",
                      "llama_model_name": "my-lm"}, "my-lm"),
    ]
    for label, cfg_over, want in cases:
        base = dict(bc._config)
        base.update(cfg_over)
        bc._LAST_MODEL_NAME = ""
        try:
            cfg = bc._llm_config(base)
        except Exception as e:
            check(f"{label} 分支", False, e)
            continue
        if want is None:
            check(f"{label} 分支记了模型名",
                  bc._last_model_name() == cfg.get("model_name"),
                  (bc._last_model_name(), cfg.get("model_name")))
        else:
            check(f"{label} 分支记了模型名",
                  bc._last_model_name() == want,
                  (bc._last_model_name(), want))


def test_record_writes_model():
    """记录里 model 不能是空串。"""
    import bot_core as bc
    import token_stats
    bc._llm_config()
    tmp = os.path.join(tempfile.gettempdir(), "ts_model_probe.jsonl")
    old = token_stats._STATS_FILE
    token_stats._STATS_FILE = tmp
    try:
        token_stats.record("group", {"prompt_tokens": 100, "completion_tokens": 20},
                           lane="main", model=bc._last_model_name())
        line = open(tmp, encoding="utf-8").read().strip().split("\n")[-1]
        d = json.loads(line)
        check("记录里有 model 字段", "model" in d, sorted(d.keys()))
        check("model 非空", bool(d.get("model")), repr(d.get("model")))
    finally:
        token_stats._STATS_FILE = old
        try:
            os.remove(tmp)
        except OSError:
            pass


def test_call_sites_pass_model():
    """三个记录点都要传 model。"""
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    n_calls = src.count("_ts_record(")
    n_model = src.count("model=_last_model_name()")
    check("三个记录点都传了 model", n_model >= 3, f"调用 {n_calls} 个，传 model {n_model} 个")
    check("没有裸调用（漏传 model）", n_calls == n_model,
          f"调用 {n_calls} 个，传 model {n_model} 个")


def main():
    print("=" * 58)
    print("token 归因：model 字段")
    print("=" * 58)
    for fn in (test_llm_config_records_model,
               test_all_branches_record,
               test_record_writes_model,
               test_call_sites_pass_model):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()