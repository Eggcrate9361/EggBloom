# -*- coding: utf-8 -*-
"""scripts/patch_field_defaults.py —— 给 _fieldmeta.json 补 `default` 与 `restart`。

为什么要单独一步
    配置页要告诉用户两件事：这个键的**默认值**是什么、改了**要不要重启**。
    这两条都是代码事实，不该在前端硬编码一份 —— 硬编码的那份一定会过期。

判据（不靠人工维护清单）
    default  : config.DEFAULT_CONFIG 里的值。
    restart  : bot_core 里被读成**模块级常量**的键，且不在 `_RELOADABLE_GLOBALS`
               的映射表里。因为在 `reload_config()` 里没有刷新入口的常量，
               改了配置也不会重新取，必须重启进程。

用法
    python scripts/patch_field_defaults.py
    python scripts/inject_field_meta.py      # 之后照常注入 v4

幂等：重复跑结果一致。
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config  # noqa: E402


def restart_keys():
    """返回必须重启才生效的配置键集合。"""
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    # 模块级：CONST = _config.get("key", ...)
    mods = dict(re.findall(
        r'^([A-Z_][A-Z0-9_]*) *= *_config\.get\("([a-z0-9_]+)"', src, re.M))
    # reload_config() 里能刷新的：CONST → cfg_key
    mapping = dict(re.findall(
        r'"([A-Z_][A-Z0-9_]*)":\s*"([a-z0-9_]+)"', src))
    reloadable = set(mapping.values())
    return {k for k in mods.values() if k not in reloadable}


def _display_default(v):
    """把默认值转成能显示的短文本；结构太复杂的返回 None（页面就不显示）。"""
    if isinstance(v, bool):
        return "开启" if v else "关闭"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        if not v:
            return "空"
        return v if len(v) <= 24 else None
    if isinstance(v, list):
        if not v:
            return "空"
        if all(isinstance(x, str) for x in v) and sum(len(x) for x in v) <= 40:
            return "、".join(v)
        return "%d 项" % len(v)
    return None


def main():
    path = os.path.join(HERE, "_fieldmeta.json")
    meta = json.load(io.open(path, encoding="utf-8"))
    fields = meta["fields"]
    defaults = config.DEFAULT_CONFIG
    need_restart = restart_keys()

    n_def = n_res = 0
    for key, m in fields.items():
        if key in defaults:
            # 保留原始值，页面"恢复默认"要按类型写回去
            m["default"] = defaults[key]
            shown = _display_default(defaults[key])
            if shown is not None:
                m["default_text"] = shown
            n_def += 1
        else:
            m.pop("default", None)
            m.pop("default_text", None)
        if key in need_restart:
            m["restart"] = True
            n_res += 1
        else:
            m.pop("restart", None)

    io.open(path, "w", encoding="utf-8").write(
        json.dumps(meta, ensure_ascii=False, indent=1))
    print("已补默认值 %d 项 / 需重启 %d 项，写出 %s" % (n_def, n_res, path))
    if n_res:
        print("需重启的键:", sorted(k for k, v in fields.items() if v.get("restart")))


if __name__ == "__main__":
    main()
