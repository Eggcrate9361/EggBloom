# -*- coding: utf-8 -*-
"""
group_chat_decider vision.py
============================
视觉辅助模块——群聊决策插件的图片理解入口。
真正的调用逻辑在 providers.py（统一调用层），这里只做薄封装，
暴露 analyze_image 供 decider.py 使用。

API Key 由 free_models.py 解析（环境变量 / 插件 data/secret.json），
与其他插件完全隔离。真实 key 永不打印。
"""
from . import providers


def analyze_image(image_ref: str, prompt: str, provider_id: str = None) -> dict:
    """
    分析一张图片，返回 VLM 文本描述。失败自动降级备用预设。

    :param image_ref: 本地路径 / http(s) URL / data URI
    :param prompt:    给模型的指令
    :param provider_id: 指定预设（缺省用激活预设）
    :return: {ok, result|error, provider, elapsed_ms}
    """
    return providers.call_vision(image_ref, prompt, provider_id)