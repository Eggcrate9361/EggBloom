# -*- coding: utf-8 -*-
"""separation.hardwall —— 已停用（2026-11-07 改）。

原设计 §18 在这里做四条边界的模式化拦截。方向重定后（见
`D:\git\EggBloom-plan\1.6.0-分离方向.md` §1.2），分离态**不注册任何内容拦截**：
应用层零规则，输出取决于所用模型自身。

保留这个模块只为一件事：旧代码 / 旧插件里还可能在调 `check()` `guard()`，
它们必须**恒放行**，而不是抛异常。`provide("hardwall")` 也返回本模块。

不记录、不提示、不拒发。
"""
from __future__ import annotations

from typing import Tuple


def check(text: str) -> Tuple[bool, str]:
    """恒放行。保留签名只为兼容旧调用点。"""
    return True, ""


def guard(text: str, *, actor: str = "ai", target: str = "") -> Tuple[bool, str]:
    """恒放行；不写审计。"""
    return True, ""
