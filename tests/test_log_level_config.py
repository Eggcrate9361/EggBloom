# -*- coding: utf-8 -*-
"""日志级别：读得到、真的生效、非法值不静默。

2026-10-24 修的 bug：logger_setup.get_logger 里写着
`from config import get_log_level`，但 config.py 里从来没有这个函数。
ImportError 被 `except Exception: level = logging.INFO` 吞掉，
于是无论怎么配，日志级别都是 INFO，且没有任何提示。

这个文件盯四件事：

  [1] 函数存在且四个合法值各自返回正确的 logging 常量
  [2] 大小写 / 前后空白不敏感（配置是人手写的，不能要求大小写精确）
  [3] 非法值回 INFO，但**必须在日志里点名** —— 静默回退正是这个 bug 的成因
  [4] logger_setup 真的把级别用上了（不是只有 config 侧能读）
"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config  # noqa: E402
import logger_setup  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


class _Cap(logging.Handler):
    """把发给 eggbloom logger 的记录抓下来，用来验证"非法值有发声"。"""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def main():
    print("[1] 四个合法值")
    for text, want in (("DEBUG", logging.DEBUG), ("INFO", logging.INFO),
                       ("WARNING", logging.WARNING), ("ERROR", logging.ERROR)):
        cfg = {"log_level": text}
        _orig = config.load_config
        config.load_config = lambda: cfg
        try:
            got = config.get_log_level()
        finally:
            config.load_config = _orig
        check(f"log_level={text} → {logging.getLevelName(want)}", got == want, got)

    print("\n[2] 大小写与空白不敏感")
    for raw, want in (("debug", logging.DEBUG), (" Debug ", logging.DEBUG),
                      ("WARNING", logging.WARNING), ("warning ", logging.WARNING)):
        cfg = {"log_level": raw}
        _orig = config.load_config
        config.load_config = lambda: cfg
        try:
            got = config.get_log_level()
        finally:
            config.load_config = _orig
        check(f"{raw!r} → {logging.getLevelName(want)}", got == want, got)

    print("\n[3] 空串与缺键：默认状态，不报错")
    for cfg in ({"log_level": ""}, {"log_level": None}, {}):
        _orig = config.load_config
        config.load_config = lambda c=cfg: c
        try:
            got = config.get_log_level()
        finally:
            config.load_config = _orig
        check(f"{cfg} → INFO", got == logging.INFO, got)

    print("\n[4] 非法值：回 INFO，但要发声")
    cap = _Cap()
    lg = logging.getLogger("eggbloom")
    lg.addHandler(cap)
    cfg = {"log_level": "LOUD"}
    _orig = config.load_config
    config.load_config = lambda: cfg
    try:
        got = config.get_log_level()
    finally:
        config.load_config = _orig
        lg.removeHandler(cap)
    check("非法值回 INFO（不抛异常）", got == logging.INFO, got)
    named = [r for r in cap.records if "log_level" in str(r.msg % r.args if r.args else r.msg)]
    check("非法值在日志里点名了", bool(named), [str(r.msg) for r in cap.records])

    print("\n[5] logger_setup 真的用上了这个级别")
    # 这一步是整个修复的意义：config 侧能读不算修好，logger 得真的 setLevel
    cfg = {"log_level": "ERROR"}
    _orig = config.load_config
    config.load_config = lambda: cfg
    try:
        lg2 = logger_setup.get_logger("test_log_level_probe")
        check("logger.setLevel 收到 ERROR", lg2.level == logging.ERROR, lg2.level)
    finally:
        config.load_config = _orig
    # 还原，别让这个探针 logger 影响同进程其他测试
    lg2.setLevel(logging.INFO)

    print("\n[6] DEFAULT_CONFIG 登记（否则用户没有入口）")
    check("DEFAULT_CONFIG 有 log_level", "log_level" in config.DEFAULT_CONFIG)
    check("默认 INFO", config.DEFAULT_CONFIG.get("log_level") == "INFO")
    ex_path = os.path.join(ROOT, "bot_config.json.example")
    with open(ex_path, encoding="utf-8-sig") as f:
        ex = f.read()
    check("example 登记了 log_level", '"log_level"' in ex)

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())