# -*- coding: utf-8 -*-
"""tests/test_memory_group_vector.py —— 群聊主读口的向量源开关（2026-11-08）

只验一件事：`harness/memory_api.py::_retrieval()` 的群聊分支

  · 默认（无开关）→ sources=("sand",)：逐字节保持线上现状，避 EGO 双注；
  · 开关打开     → sources=("sand","sm")：向量源接进**同一个** builder；
  · 私聊不受影响 → 始终 ("sand","sm")；
  · 作用域不变   → 群聊恒 ("group", gid)，不会串私聊；
  · 开关走现有 config 读取口（patch `_cfg`），且键没有写死进 config.py。

全打桩：patch `memory_context.build_block` 记 kwargs，patch `_cfg` 控制配置，
环境变量用 `EGGBLOOM_GROUP_MEMORY_VECTOR` 覆盖。不碰真实沙漏 / 模型 / QQ / 网络。

运行：python tests/test_memory_group_vector.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import memory_api as M       # noqa: E402
from harness import memory_context as MC  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


class Patch:
    """临时替换模块属性，退出时按逆序还原。"""

    def __init__(self):
        self.old = []

    def set(self, mod, name, value):
        self.old.append((mod, name, getattr(mod, name, None)))
        setattr(mod, name, value)

    def undo(self):
        for mod, name, val in reversed(self.old):
            setattr(mod, name, val)
        self.old = []


_seen = []


def _fake_build_block(message, scope="group", scope_id="", ctx=None,
                      sources=("sand", "sm")):
    _seen.append({"message": message, "scope": scope, "scope_id": scope_id,
                  "sources": tuple(sources or ())})
    return f"【检索·{scope}】{message}"


def _call(scene="group", group_id="777", query="周末", private=False, uid="10001"):
    """调一次 _retrieval，返回 (结果, 最后一次 build_block 的 kwargs)。"""
    _seen.clear()
    rt = M._retrieval(query, scene, group_id, private, uid)
    return rt, (dict(_seen[-1]) if _seen else None)


def main():
    saved_env = os.environ.pop(M.GROUP_VECTOR_ENV, None)
    p = Patch()
    p.set(M, "_log", lambda *_a, **_k: None)       # 静音
    p.set(MC, "build_block", _fake_build_block)
    try:
        # ── [A] 默认关：群聊只取沙漏 ──────────────────────────────
        print('[A] 默认（无 env / 无 config 键）→ 群聊 sources=("sand",)')
        p.set(M, "_cfg", lambda: {})               # config 里没有任何相关键
        check("★ 模块默认常量是 False", M.GROUP_VECTOR_DEFAULT is False,
              M.GROUP_VECTOR_DEFAULT)
        check("★ 默认判定为关", M._group_vector_enabled() is False)
        rt, kw = _call()
        check("群聊返回检索段", isinstance(rt, tuple) and rt[0] == "检索", rt)
        check("★ 群聊默认 sources == ('sand',)",
              kw and kw["sources"] == ("sand",), kw and kw["sources"])
        check("群聊作用域没被改",
              kw and kw["scope"] == "group" and kw["scope_id"] == "777", kw)
        check("query 原样透传", kw and kw["message"] == "周末", kw)

        # ── [B] env 覆盖打开 ───────────────────────────────────────
        print("\n[B] 环境变量 EGGBLOOM_GROUP_MEMORY_VECTOR=1 → 群聊带 sm")
        os.environ[M.GROUP_VECTOR_ENV] = "1"
        check("开关判定为开", M._group_vector_enabled() is True)
        rt, kw = _call()
        check("★ 群聊开启后 sources == ('sand','sm')",
              kw and kw["sources"] == ("sand", "sm"), kw and kw["sources"])
        check("★ 仍是群作用域（不串私聊）",
              kw and kw["scope"] == "group" and kw["scope_id"] == "777", kw)
        check("返回仍是检索段", isinstance(rt, tuple) and rt[0] == "检索", rt)

        for v in ("true", "YES", "On"):
            os.environ[M.GROUP_VECTOR_ENV] = v
            _, kw = _call()
            check(f"env={v!r} 视为开",
                  kw and kw["sources"] == ("sand", "sm"), kw and kw["sources"])

        # ── [C] 关的写法 / 非法值一律保守按关 ──────────────────────
        print("\n[C] 关 / 非法值一律保守按关")
        for v in ("0", "false", "off", "maybe", ""):
            os.environ[M.GROUP_VECTOR_ENV] = v
            _, kw = _call()
            check(f"env={v!r} → 群聊仍 ('sand',)",
                  kw and kw["sources"] == ("sand",), kw and kw["sources"])
        os.environ.pop(M.GROUP_VECTOR_ENV, None)

        # ── [D] config 读取口（不改 config.py）─────────────────────
        print("\n[D] config.load_config().get(...) 读取口")
        cfg_calls = {"n": 0}

        def _cfg_true():
            cfg_calls["n"] += 1
            return {M.GROUP_VECTOR_CONFIG_KEY: True}

        p.set(M, "_cfg", _cfg_true)
        check("无 env 时开关读 config=True", M._group_vector_enabled() is True)
        check("★ 确实调用了现有 _cfg 读取口", cfg_calls["n"] >= 1, cfg_calls)
        _, kw = _call()
        check("★ config 打开 → 群聊 ('sand','sm')",
              kw and kw["sources"] == ("sand", "sm"), kw and kw["sources"])

        p.set(M, "_cfg", lambda: {M.GROUP_VECTOR_CONFIG_KEY: False})
        check("config=False → 关", M._group_vector_enabled() is False)
        _, kw = _call()
        check("config=False → 群聊 ('sand',)",
              kw and kw["sources"] == ("sand",), kw and kw["sources"])

        # env 优先于 config
        os.environ[M.GROUP_VECTOR_ENV] = "0"
        p.set(M, "_cfg", lambda: {M.GROUP_VECTOR_CONFIG_KEY: True})
        check("env 优先于 config（env=0 压住 config=True）",
              M._group_vector_enabled() is False)
        _, kw = _call()
        check("env=0 + config=True → 群聊仍 ('sand',)",
              kw and kw["sources"] == ("sand",), kw and kw["sources"])
        os.environ.pop(M.GROUP_VECTOR_ENV, None)

        # ── [E] 私聊不受开关影响 ──────────────────────────────────
        print("\n[E] 私聊始终双源，开关只管群聊")
        for enabled in (False, True):
            os.environ[M.GROUP_VECTOR_ENV] = "1" if enabled else "0"
            p.set(M, "_cfg", lambda: {})           # private 开关默认 True
            _, kw = _call(scene="private", group_id="", private=True, uid="10001")
            check(f"开关={'开' if enabled else '关'} → 私聊仍 ('sand','sm')",
                  kw and kw["sources"] == ("sand", "sm"), kw and kw["sources"])
            check(f"开关={'开' if enabled else '关'} → 私聊作用域 ('private','10001')",
                  kw and kw["scope"] == "private" and kw["scope_id"] == "10001", kw)
        os.environ.pop(M.GROUP_VECTOR_ENV, None)

        # ── [F] 边界：空 query / 空 group_id ──────────────────────
        print("\n[F] 边界")
        p.set(M, "_cfg", lambda: {})
        rt, kw = _call(query="")
        check("query 为空 → 不调 builder、返回 None", rt is None and kw is None, (rt, kw))
        rt, kw = _call(group_id="")
        check("group_id 为空 → 不调 builder、返回 None",
              rt is None and kw is None, (rt, kw))

        # ── [G] 源码级：键没有写死进 config.py ────────────────────
        print("\n[G] 源码级：开关靠 .get 默认值，没改 config.py")
        with open(os.path.join(ROOT, "config.py"), encoding="utf-8",
                  errors="replace") as f:
            cfg_src = f.read()
        check("★ config.py 里没有 group_memory_vector_enable（没改 config.py）",
              M.GROUP_VECTOR_CONFIG_KEY not in cfg_src)
        with open(M.__file__, encoding="utf-8", errors="replace") as f:
            api_src = f.read()
        check("memory_api.py 里声明了这个开关键",
              f'"{M.GROUP_VECTOR_CONFIG_KEY}"' in api_src)
        check("私聊 sources 没被改动（仍是双源）",
              'sources=("sand", "sm")' in api_src)
    finally:
        p.undo()
        os.environ.pop(M.GROUP_VECTOR_ENV, None)
        if saved_env is not None:
            os.environ[M.GROUP_VECTOR_ENV] = saved_env

    print("\n" + "=" * 56)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    for n in FAIL:
        print(f"  FAIL  {n}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
