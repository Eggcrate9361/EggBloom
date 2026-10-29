# -*- coding: utf-8 -*-
"""search_prefs 回归测试（2026-10-29，开关：优先模型搜索）。

注意（维护者纪律）：单元测试只防回归，**不算真机验收**。
真机验收口径 = 群里/Agent 页真实搜一次，日志出现「模型搜索(DeepSeek)命中」，
并且返回结果里能读到当年的信息。
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import search_prefs as sp  # noqa: E402

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)


_tmp = tempfile.mkdtemp(prefix="sprefs_")
_orig_cfg = sp._CONFIG_PATH
_orig_env = os.environ.pop("EGGBLOOM_SEARCH_PREFER", None)

try:
    # ── 1. 默认开（用户 2026-10-29 要求的行为）────────────────────
    os.remove(_orig_cfg) if os.path.exists(_orig_cfg) else None
    check("配置文件缺失时默认优先模型搜索", sp.prefer_model_search() is True)

    # ── 2. 配置里显式关掉 → 自家优先 ─────────────────────────────
    p = os.path.join(_tmp, "config.json")
    sp._CONFIG_PATH = p
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump({"prefer_model_search": False}, f)
    check("配置 false → 自家优先", sp.prefer_model_search() is False)

    with io.open(p, "w", encoding="utf-8") as f:
        json.dump({"prefer_model_search": True}, f)
    check("配置 true → 模型优先", sp.prefer_model_search() is True)

    # ── 3. 环境变量优先级最高（排障用）───────────────────────────
    os.environ["EGGBLOOM_SEARCH_PREFER"] = "own"
    check("env=own 压过配置 true", sp.prefer_model_search() is False)
    os.environ["EGGBLOOM_SEARCH_PREFER"] = "model"
    check("env=model 压过配置 false", sp.prefer_model_search() is True)
    os.environ.pop("EGGBLOOM_SEARCH_PREFER", None)

    # ── 4. 损坏的配置不炸、回默认 ─────────────────────────────────
    with io.open(p, "w", encoding="utf-8") as f:
        f.write("{ not json")
    check("配置损坏 → 回默认（开）", sp.prefer_model_search() is True)

    # ── 5. 插件 get_config 把开关吐出来 ───────────────────────────
    sp._CONFIG_PATH = _orig_cfg
    import plugins.web_search as ws
    cfg = ws.get_config()
    check("插件 get_config 含 prefer_model_search", "prefer_model_search" in cfg)
    check("插件默认值 = True", cfg["prefer_model_search"] is True)

finally:
    sp._CONFIG_PATH = _orig_cfg
    if _orig_env is not None:
        os.environ["EGGBLOOM_SEARCH_PREFER"] = _orig_env
    import shutil
    shutil.rmtree(_tmp, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
