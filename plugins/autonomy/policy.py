# -*- coding: utf-8 -*-
"""兼容壳：自主性策略层的实现已归位到 `autonomy/policy.py`。

`evals/autonomy_eval.py` 按**文件路径**加载本模块（评测台要能单文件跑），
`tests/test_autonomy_policy.py` 把本目录塞进 sys.path 后 `import policy`。
两处都不经过包导入，所以这里显式铺开全部公开名字 + `__getattr__` 兜底，
保证判据只有一份（`autonomy/policy.py`），不会出现「评测台跑的是旧表」。

**不要在这里加规则**。等级表 / guard / 提示词只改 `autonomy/policy.py`。
"""
from __future__ import annotations

from autonomy import policy as _impl
from autonomy.policy import (
    LEVELS, LEVEL_ACTIONS, ACTION_LEVELS, UNKNOWN_ACTION_LEVEL, CONFIRM_LEVEL,
    level_of, guard, permission_prompt, heartbeat_prompt, parse_decision,
)

__all__ = [
    "LEVELS", "LEVEL_ACTIONS", "ACTION_LEVELS", "UNKNOWN_ACTION_LEVEL",
    "CONFIRM_LEVEL", "level_of", "guard", "permission_prompt",
    "heartbeat_prompt", "parse_decision",
]


def __getattr__(name):
    return getattr(_impl, name)
