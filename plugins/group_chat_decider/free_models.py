# -*- coding: utf-8 -*-
"""
group_chat_decider free_models.py
=================================
免费模型预设注册表——群聊决策插件内置多家免费/低成本的文本与视觉模型，
通过 data/config.json 的 `active_model` 切换，插件完全自包含，不依赖其他插件。

每家预设都是 OpenAI 兼容格式，密钥从插件自己的 data/secret.json 读取
（secret.json 已写入 .gitignore，不会入库）。

预设说明（2026-08 核实，均为免费或免费额度）：
  - zhipu    智谱 BigModel：glm-4-flash 文本免费 / glm-4v-flash 视觉免费，不限量
  - aliyun   阿里云百炼：qwen-turbo / qwen-vl-plus，新用户有免费额度
  - siliconflow 硅基流动：开源模型免费额度，需注册
  - ollama   本地 Ollama：qwen2.5 系列，完全免费无需 key（需本机安装 Ollama）
"""
import json
import os
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
DATA_DIR = PLUGIN_DIR / "data"
SECRET_FILE = DATA_DIR / "secret.json"
CONFIG_FILE = DATA_DIR / "config.json"

# ═══════════════════════════════════════════════════════════
# 免费模型预设注册表
# ═══════════════════════════════════════════════════════════
FREE_MODELS = {
    "zhipu": {
        "name": "智谱 GLM（免费）",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "text_model": "glm-4-flash",
        "vision_model": "glm-4v-flash",
        "key_env": "ZHIPU_API_KEY",          # 环境变量（可选）
        "key_name": "zhipu",                 # secret.json 里的键名
        "note": "文本+视觉均免费，不限量",
    },
    "aliyun": {
        "name": "阿里云百炼 Qwen（免费额度）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "text_model": "qwen-turbo",
        "vision_model": "qwen-vl-plus",
        "key_env": "DASHSCOPE_API_KEY",
        "key_name": "aliyun",
        "note": "新用户送免费额度，qwen-turbo 极便宜",
    },
    "siliconflow": {
        "name": "硅基流动 SiliconFlow（免费额度）",
        "base_url": "https://api.siliconflow.cn/v1",
        "text_model": "Qwen/Qwen3-8B",
        "vision_model": "Qwen/Qwen2.5-VL-7B-Instruct",
        "key_env": "SILICONFLOW_API_KEY",
        "key_name": "siliconflow",
        "note": "开源模型免费额度，注册即送",
    },
    "ollama": {
        "name": "Ollama 本地（免费）",
        "base_url": "http://127.0.0.1:11434/v1",
        "text_model": "qwen2.5:7b",
        "vision_model": "qwen2.5-vision:7b",
        "key_env": "",
        "key_name": "",
        "note": "无需 key，需本机安装 Ollama 并 pull 模型",
    },
}

# 默认激活的预设
DEFAULT_MODEL = "zhipu"


def load_plugin_config() -> dict:
    """读取插件 data/config.json，含 active_model 等"""
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _load_secret() -> dict:
    """读取插件 data/secret.json（各 provider 的 key）"""
    try:
        if SECRET_FILE.exists():
            data = json.loads(SECRET_FILE.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_plugin_config(updates: dict) -> dict:
    """合并写入插件 data/config.json，返回更新后的完整配置"""
    cfg = load_plugin_config()
    if updates:
        cfg.update(updates)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        print(f"[free_models] 保存 config 失败: {e}")
    return cfg


def save_secret_key(provider_id: str, api_key: str) -> bool:
    """写入某 provider 的 key 到 data/secret.json。key 为空表示清除。"""
    if provider_id not in FREE_MODELS:
        return False
    secret = _load_secret()
    secret[provider_id] = (api_key or "").strip()
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SECRET_FILE.write_text(
            json.dumps(secret, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except Exception as e:
        print(f"[free_models] 保存 secret 失败: {e}")
        return False


def get_active_model() -> str:
    """当前激活的预设 id"""
    cfg = load_plugin_config()
    m = cfg.get("active_model", DEFAULT_MODEL)
    return m if m in FREE_MODELS else DEFAULT_MODEL


def get_provider(provider_id: str = None) -> dict:
    """获取预设配置（含解析好的 key），provider_id 缺省用激活预设"""
    pid = provider_id or get_active_model()
    base = dict(FREE_MODELS.get(pid, FREE_MODELS[DEFAULT_MODEL]))
    if pid == "ollama":
        base["api_key"] = "ollama"  # Ollama 用占位 key
        return base
    # 解析 key：环境变量 > secret.json
    key = ""
    env_name = base.get("key_env") or ""
    if env_name:
        key = (os.environ.get(env_name) or "").strip()
    if not key:
        secret = _load_secret()
        key = (secret.get(base.get("key_name", "")) or "").strip()
    base["api_key"] = key
    base["provider_id"] = pid
    return base


def is_configured(provider_id: str = None) -> bool:
    """检查预设是否已配置 key（ollama 无需 key）"""
    pid = provider_id or get_active_model()
    if pid == "ollama":
        return True
    p = get_provider(pid)
    return bool(p.get("api_key"))


def list_models() -> list:
    """列出所有预设及其配置状态（用于 UI 展示）"""
    out = []
    for pid, meta in FREE_MODELS.items():
        out.append({
            "id": pid,
            "name": meta["name"],
            "text_model": meta["text_model"],
            "vision_model": meta["vision_model"],
            "configured": is_configured(pid),
            "active": pid == get_active_model(),
            "note": meta["note"],
        })
    return out