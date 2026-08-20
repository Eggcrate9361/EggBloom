#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EggBloom v3 组件库
专业级UI组件，遵循设计系统规范
"""

from .card import Card, StatCard, ActionCard
from .button import Button, IconButton, ToggleButton, ButtonGroup
from .input import Input, TextArea, Select, Toggle
from .table import DataTable, TableWithControls
from .navbar import Navbar, NavItem
from .sidebar import Sidebar
from .status import StatusBadge, StatusDot, ProgressBar
from .modal import Modal, Dialog, Toast
from .tabs import TabContainer, TabBar, TabButton
from .tooltip import Tooltip
from .avatar import Avatar
from .badge import Badge
from .divider import Divider
from .empty import EmptyState
from .loading import LoadingSpinner, Skeleton

__all__ = [
    # 布局组件
    "Card", "StatCard", "ActionCard",
    "Navbar", "NavItem",
    "Sidebar",
    "TabContainer", "TabBar", "TabButton",
    "Divider",

    # 交互组件
    "Button", "IconButton", "ToggleButton", "ButtonGroup",
    "Input", "TextArea", "Select", "Toggle",
    "DataTable", "TableWithControls",

    # 状态组件
    "StatusBadge", "StatusDot", "ProgressBar",
    "LoadingSpinner", "Skeleton",

    # 反馈组件
    "Modal", "Dialog", "Toast",
    "Tooltip",

    # 展示组件
    "Avatar", "Badge", "EmptyState",
]

# 设计系统导入
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from design_system import ds, C, S, R, T, SH, A, I