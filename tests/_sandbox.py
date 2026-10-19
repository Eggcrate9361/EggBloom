# -*- coding: utf-8 -*-
"""tests/_sandbox.py —— 测试用沙漏路径隔离（**必须在 import 沙漏模块之前调用**）。

## 为什么需要

`test_memory_feed` / `test_memory_stage_a` / `test_memory_stage_b` /
`test_sandglass_dedup` 等测试会调用 `sandglass_bridge.memory_feed` /
`log_user_message`。这些函数在**记忆系统已就绪时会真的写盘** ——
而本机 `~/.neurobase/sandglass.txt` 是生产记忆（4080 行、跨度 4 个月）。

2026-10-19 实测踩到：跑一次全量回归，生产沙漏末尾多了 20 行探针
（`帮我搜一下天气` / `答案` / `u` / `a` …）—— 全是测试写进去的。

这些污染会进倒排索引与 FTS5 召回，等于**用测试数据训练生产记忆**。

## 用法

在各测试文件**最顶部**（import sandglass 相关模块之前）：

    from _sandbox import sandbox_neurobase
    sandbox_neurobase()

它会：
  1. 建立临时 NEXSANDBASE_HOME（tempfile.mkdtemp）
  2. 设进 os.environ（沙漏路径解析的第一优先级）
  3. 把环境变量写进 sandglass_lite / sandglass_paths 的模块级路径常量
  4. 注册 atexit 清理

若测试需要断言"格式与既有数据一致"，可用本模块返回的临时目录自己造样本。
"""
import atexit
import os
import shutil
import sys
import tempfile

_TMPDIR = None


def sandbox_neurobase(prefix="eggbloom_test_nb_") -> str:
    """把沙漏数据根指向临时目录，返回该目录。可重复调用（幂等）。"""
    global _TMPDIR
    if _TMPDIR is not None:
        return _TMPDIR

    td = tempfile.mkdtemp(prefix=prefix)
    _TMPDIR = td

    # 1. 环境变量（沙漏路径解析的最高优先级）
    os.environ["NEXSANDBASE_HOME"] = td

    # 2. 若模块已导入，改写其路径常量
    #    （正常情况下本函数在 import 之前调用，这一步是兜底）
    _repatch(td)

    # 3. 建最小目录结构
    for sub in ("", "persona", "archive", "scripts"):
        try:
            os.makedirs(os.path.join(td, sub), exist_ok=True)
        except OSError:
            pass

    atexit.register(_cleanup)
    return td


def _repatch(td: str) -> None:
    """改写已导入模块的路径常量（模块级 _NB / _SANDGLASS 等）。"""
    txt = os.path.join(td, "sandglass.txt")
    idx = os.path.join(td, "sandglass.idx")
    shadow = os.path.join(td, "shadow_sand.db")
    particles = os.path.join(td, "decision_particles.txt")

    for modname in ("sandglass_lite",):
        m = sys.modules.get(modname)
        if m is None:
            continue
        for attr, val in (("_NB", td), ("_SANDGLASS", txt), ("_INDEX", idx),
                          ("_TRUST_DB", shadow), ("_PARTICLES", particles)):
            if hasattr(m, attr):
                setattr(m, attr, val)
        m._idx_cache = None
        m._idx_mtime = 0.0


def temp_dir() -> str:
    """当前临时目录（未初始化则先初始化）。"""
    return _TMPDIR or sandbox_neurobase()


def _cleanup():
    global _TMPDIR
    if _TMPDIR and os.path.isdir(_TMPDIR):
        shutil.rmtree(_TMPDIR, ignore_errors=True)
    _TMPDIR = None