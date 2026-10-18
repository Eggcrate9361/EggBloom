# -*- coding: utf-8 -*-
"""plugins/types.py — 插件系统正式类型定义

定义插件契约的 TypedDict 和 Protocol，供 IDE 类型检查和 AI Agent 参考。
插件开发者可 `from plugins.types import PluginMeta, BeforeReplyResult` 获取类型提示。

注意：此文件为类型声明，不改变运行时行为。现有插件无需修改即可继续工作。
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict, Protocol, runtime_checkable


class PluginMeta(TypedDict, total=False):
    """插件元数据声明（__init__.py 顶部的 PLUGIN_META）"""

    name: str                    # 与目录名一致，小写 + 下划线
    version: str                 # 语义版本号
    description: str             # 一句话描述
    author: str                  # 作者
    group_only: bool             # True = 仅群聊生效
    enabled: bool                # 默认启用
    dependencies: list[str]      # 依赖的其他插件名
    hooks: list[str]             # 注册的钩子：["before_reply"] / ["after_reply"]
    priority: int                # 数字越小越先执行
    estimated_tokens: int        # 预估消耗 token
    page: str                    # 独立 UI 路径，如 "plugins/my_plugin/ui.html"


class InjectAction(TypedDict):
    """before_reply 返回注入 prompt"""
    action: str                  # "inject"
    content: str                 # 注入的 prompt 内容
    meta: dict[str, Any]         # 附加元数据


class SkipAction(TypedDict):
    """before_reply 返回跳过本次回复"""
    action: str                  # "skip"
    reason: str                  # 跳过原因


class OverrideAction(TypedDict):
    """before_reply / after_reply 返回覆盖回复"""
    action: str                  # "override"
    reply: str                   # 替换后的回复内容
    meta: dict[str, Any]         # 附加元数据（可选）


# before_reply 的返回值联合类型
BeforeReplyResult = Optional[InjectAction | SkipAction | OverrideAction]

# after_reply 的返回值联合类型
AfterReplyResult = Optional[OverrideAction]


@runtime_checkable
class PluginProtocol(Protocol):
    """插件模块的正式接口协议

    每个插件 __init__.py 应实现此协议中声明的函数。
    PluginManager 在加载时会检查 PLUGIN_META 是否存在。
    """

    PLUGIN_META: PluginMeta

    def on_load(self) -> None:
        """插件启用时调用一次。打印日志、初始化资源。"""
        ...

    def on_unload(self) -> None:
        """插件卸载/禁用时调用一次。清理定时器、关闭连接。"""
        ...

    def set_ai_client(self, client: Any) -> None:
        """可选。插件管理器传播 AI 客户端。"""
        ...

    def before_reply(
        self,
        message: str = "",
        group_id: str = "",
        context: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> BeforeReplyResult:
        """AI 生成回复前调用。

        Returns:
            None — 放行，不做任何事
            InjectAction — 注入额外 prompt
            SkipAction — 跳过本次回复
            OverrideAction — 用插件生成的回复覆盖
        """
        ...

    def after_reply(
        self,
        message: str = "",
        group_id: str = "",
        context: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> AfterReplyResult:
        """消息发送后调用。

        Returns:
            None — 不干预
            OverrideAction — 拦截并替换已生成的回复
        """
        ...


def validate_plugin_meta(meta: dict[str, Any]) -> list[str]:
    """校验 PLUGIN_META 的必需字段，返回错误列表（空 = 通过）"""
    errors = []
    required = ["name", "version", "hooks"]
    for field in required:
        if field not in meta:
            errors.append(f"PLUGIN_META 缺少必需字段: {field}")

    if "hooks" in meta:
        valid_hooks = {"before_reply", "after_reply"}
        for hook in meta["hooks"]:
            if hook not in valid_hooks:
                errors.append(f"无效钩子: {hook}（可用: {valid_hooks}）")

    if "priority" in meta and not isinstance(meta["priority"], int):
        errors.append("priority 必须为整数")

    if "name" in meta and " " in meta["name"]:
        errors.append("name 不能包含空格（应使用下划线）")

    return errors
