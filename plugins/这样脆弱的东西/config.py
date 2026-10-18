# -*- coding: utf-8 -*-
"""这样脆弱的东西 配置管理。"""
import json
import os

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

DEFAULTS = {
    "enabled": True,
    "distill_level": "L1",
    "token_budget": 500000,
    "confidence_threshold": 0.7,
    "auto_review": True,               # 蒸馏完成后进入人工审核
    "data_sources": ["chat_logs", "super_memory", "sandglass", "private_memory"],
    "max_samples_per_source": 50000,   # 单源最多取多少条
    "ethics_enabled": True,            # 蒸馏前必须确认伦理
    "audit_enabled": True,             # 写审计日志
    "backup_count": 5,                 # 人格文件备份数

    # ── 深挖引擎（v2）──
    "llm_enabled": True,               # 允许调用 LLM 深挖
    "deep_levels": ["L2", "L3", "L4"],  # 从哪些等级开始做 LLM 深挖
    "max_llm_calls": 24,               # 本次蒸馏的 LLM 调用硬上限（防烧钱）
    "llm_rpm": 3,                      # 上游实测约 3 请求/分钟，超了直接被拒
    "llm_max_tokens": 3000,            # 单次输出上限（要留够空间给推理 token）
    "llm_token_ceiling": 16000,        # 推理吃满预算时，允许自动抬到的上限
    "empty_retries": 6,                # 空输出/被限流时的重试次数（退避等待，别省）
    "sections_per_call": 1,            # 每次取证问几层（1 = 每层单独问，最细）
    "deep_client_direct": True,        # 自建蒸馏专用客户端（不污染 bot 的思考开关）
    "deep_disable_thinking": True,     # 关掉推理型模型的思考过程（否则会吃光预算并超时）
    "deep_extra_body": "",             # 手动指定附加请求体（JSON 字符串，留空用上面的开关）
    "deep_temperature": 0.3,           # 蒸馏要稳，温度调低
    "corpus_chars": 60000,             # 一次投喂的语料字符上限
    "chunk_chars": 12000,              # 超长语料的分块粒度
    "critic_enabled": True,            # 终检：把结论送回原文对账
    "evidence_quotes": 3,              # 每条结论最多展示几段原文引证
    "evidence_map_limit": 5000,        # 落盘保存多少条原文引证（防 JSON 爆炸）
    "quote_chars": 200,                # 每条引证保存多长的原文（附卷要能核对全文）
    "evidence_appendix": True,         # 审核通过时同时生成「证据附卷」

    # ── 连续蒸馏（人格账本）──
    "continuous": True,                # 开启账本：只吃新增语料，结论累积演化
    "incremental_max_messages": 300,   # 新增语料不超过这个数就走增量模式
    "incremental_critic": True,        # 增量模式也做终检对账
    "suspect_after": 3,                # 连续多少轮（有新对话）没被证据提及 → 存疑
    "dormant_after": 8,                # 连续多少轮（有新对话）没被提及 → 沉眠（不等于否定）
    "inject_min_confidence": 0.4,      # 低于此置信度的条目不进人格注入卡
    "ledger_max_fingerprints": 200000,  # 水位指纹上限（防账本无限膨胀）
    "sandglass_bare_is_target": True,   # 落沙里没标发送者的行，按"私聊对象"处理
    "neurobase_home": "",               # NeuroBase 数据根覆盖（留空=跟随程序 sandglass_paths）
    "bot_names": [],                    # 机器人的名字（personality.txt 里的"你是X"会自动并入）
    "turing_auto": False,               # 审核通过后自动跑图灵盲测（花额度，默认关）
    "turing_rounds": 5,                 # 自动盲测的轮数
}

os.makedirs(DATA_DIR, exist_ok=True)


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[PersonaDistill] 保存失败 {path}: {e}")
        return False


def get_config():
    cfg = _load_json(CONFIG_PATH, {})
    for key, value in DEFAULTS.items():
        if key not in cfg or cfg[key] in ("", None):
            cfg[key] = value
    return cfg


def set_config(data):
    cfg = get_config()
    for key, value in (data or {}).items():
        if key in DEFAULTS:
            cfg[key] = value
    _save_json(CONFIG_PATH, cfg)
    return cfg
