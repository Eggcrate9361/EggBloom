# -*- coding: utf-8 -*-
"""
group_chat_decider free_models.py
=================================
**免费模型档位注册表** —— 群聊决策插件的"用哪个模型读空气"。

2026-10-12 用户定调：「决策取消智谱管线，换 dsh-our-free-model」。
此前的实现是自带四家第三方免费云预设（智谱 GLM / 阿里百炼 / 硅基流动 / Ollama），
每家一个 key、一个 base_url、各自限流——key 分散、模型分散、还多几条外部依赖。

现在**直连** `dsh-our-free-model` 用的两条免密车道（zen / kilo），见根目录
`free_llm.py`：免 key、免登录、不经任何本地路由进程。本文件退化成一张
"档位表"：档位只挑模型（`车道:模型`），不再管 key。

对外 API 与旧版保持一致（UI / providers.py / 测试都依赖它）：
  load_plugin_config / save_plugin_config / save_secret_key / get_active_model /
  get_provider / is_configured / list_models ；另加 gateway_status() 供诊断。
"""
import json
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
DATA_DIR = PLUGIN_DIR / "data"
SECRET_FILE = DATA_DIR / "secret.json"
CONFIG_FILE = DATA_DIR / "config.json"

# ═══════════════════════════════════════════════════════════
# 档位表（模型 id 来自 free_llm 的免费车道清单；写形 `车道:模型`）
# 2026-10-12 实测延迟：mimo 1.4s · space-bunny 3.9s · nemotron-3.5-lightning 6.5s
#                      视觉 openrouter/free 8~22s（中文描述正确）
# ═══════════════════════════════════════════════════════════
FREE_MODELS = {
    "ofm-fast": {
        "name": "免费车道 · space-bunny（默认）",
        "text_model": "zen:space-bunny-free",
        "vision_model": "zen:space-bunny-free",
        "note": "zen 免密车道 · 文本 2~5s / 视觉 2.8s（2026-10-12 实测）",
    },
    "ofm-kilo": {
        "name": "免费车道 · step-5-preview（Kilo）",
        "text_model": "kilo:stepfun/step-5-preview-free",
        "vision_model": "zen:space-bunny-free",
        "note": "kilo 免鉴权车道 · 文本 ~1.2s / 1M 上下文",
    },
    "ofm-mimo": {
        "name": "免费车道 · mimo-flash",
        "text_model": "zen:mimo-v2.6-flash-free",
        "vision_model": "zen:mimo-v2.6-flash-free",
        "note": "zen 免密车道 · 文本 1.4~24s（波动大）/ 视觉 3.6s",
    },
}

DEFAULT_MODEL = "ofm-fast"

# 兼容旧配置：把历史值映射到新档位，避免用户升级后 active_model 失效
_LEGACY_ALIASES = {
    "zhipu": "ofm-fast", "aliyun": "ofm-fast", "siliconflow": "ofm-fast",
    "ollama": "ofm-fast",
}


def _free_llm():
    """惰性导入根目录 free_llm（源码库/发行包都是根目录下的同名模块）。"""
    try:
        import sys
        import os
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if root not in sys.path:
            sys.path.insert(0, root)
        import free_llm
        return free_llm
    except Exception:
        return None


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


def save_plugin_config(updates: dict) -> dict:
    """合并写入插件 data/config.json"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        cur = load_plugin_config()
        cur.update({k: v for k, v in (updates or {}).items() if v is not None})
        CONFIG_FILE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        return cur
    except Exception as e:
        print(f"[free_models] 保存 config 失败: {e}")
        return {}


def save_secret_key(provider_id: str, api_key: str) -> bool:
    """已废弃：改走免密车道后**不需要**任何第三方 key。

    保留同名函数只为不改 UI 调用点；总是返回 False，让界面如实显示"不需要 key"。
    """
    print(f"[free_models] {provider_id}: 走免密车道（zen/kilo），无需配置 key（已忽略）")
    return False


def get_active_model() -> str:
    """当前激活的档位 id（旧版预设名自动迁移到 ofm-fast）。"""
    cfg = load_plugin_config()
    m = str(cfg.get("active_model", DEFAULT_MODEL) or DEFAULT_MODEL)
    if m in FREE_MODELS:
        return m
    return _LEGACY_ALIASES.get(m, DEFAULT_MODEL)


def get_provider(provider_id: str = None) -> dict:
    """取档位配置。

    与旧版同形，但 base_url/api_key 已无意义（直连免密车道）：
    保留字段只为不破坏调用方与 UI；`usable` 表示车道模块是否可用。
    """
    pid = provider_id or get_active_model()
    if pid not in FREE_MODELS:
        pid = DEFAULT_MODEL
    base = dict(FREE_MODELS[pid])
    base.update({
        "provider_id": pid,
        "base_url": "",           # 直连上游，无本地网关
        "api_key": "",            # 免密车道，无 key
        "usable": _free_llm() is not None,
    })
    return base


def is_configured(provider_id: str = None) -> bool:
    """免密车道：模块在即视为已配置（不再有"某家 key 没填"这回事）。

    真正的可用性由调用时的降级治理兜底（失败 → 交主模型自裁 → 断路器转保守）。
    """
    return _free_llm() is not None


def list_models() -> list:
    """列出所有档位及其状态（用于 UI 展示）"""
    out = []
    active = get_active_model()
    for pid, meta in FREE_MODELS.items():
        out.append({
            "id": pid,
            "name": meta["name"],
            "text_model": meta["text_model"],
            "vision_model": meta["vision_model"],
            "configured": is_configured(pid),
            "active": pid == active,
            "note": meta["note"],
        })
    return out


def lanes_status() -> dict:
    """诊断：模型链（给日志与 UI 用）。probe=True 时会真发一次请求。"""
    fl = _free_llm()
    if fl is None:
        return {"ok": False, "error": "free_llm 模块不可用"}
    try:
        return {"ok": True, "sources": fl.sources()}
    except Exception as e:
        return {"ok": False, "error": str(e)}
