# -*- coding: utf-8 -*-
"""config.py - Configuration with env override, API provider selection"""
import json
import os
import sys

try:
    from cred_store import MODEL_API_KEY_CRED, read_cred, exists_cred
except Exception:
    MODEL_API_KEY_CRED = "MODEL_API_KEY"

    def read_cred(name):
        return None

    def exists_cred(name):
        return False

if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

_APPDATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "EggBloom_v3")
os.makedirs(_APPDATA_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(_APPDATA_DIR, "bot_config.json")
ENV_FILE = os.path.join(_APPDATA_DIR, ".env")

# 程序目录（eggbloom/resources/app）下的 bot_config.json，优先于 AppData
APP_CONFIG_FILE = os.path.join(SCRIPT_DIR, "bot_config.json")

API_PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "url": "https://api.deepseek.com/chat/completions",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "note": "官方直连，性价比高",
    },
    "openai": {
        "name": "OpenAI",
        "url": "https://api.openai.com/v1/chat/completions",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
        "note": "国际，需网络可达",
    },
    "ollama": {
        "name": "Ollama (本地)",
        "url": "http://127.0.0.1:11434",
        "models": ["qwen2.5:7b", "llama3.1:8b", "deepseek-r1:7b"],
        "note": "本地免费，无需 Key",
    },

    # ── 国产官方（均已核实 OpenAI 兼容 + 工具调用）──
    "aliyun": {
        "name": "阿里云百炼 Qwen",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-long", "qwen-coder-plus"],
        "note": "新域名需业务空间ID：https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "models": ["glm-5.2", "glm-5", "glm-4.7", "glm-4.6", "glm-4.5", "glm-5v-turbo"],
        "note": "GLM 最新 5.x 系列",
    },
    "kimi": {
        "name": "Kimi (月之暗面)",
        "url": "https://api.moonshot.cn/v1/chat/completions",
        "models": ["kimi-k3", "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5", "moonshot-v1-128k"],
        "note": "K3 始终推理；K2.6 可关思考",
    },
    "step": {
        "name": "Step (阶跃星辰)",
        "url": "https://api.stepfun.com/v1/chat/completions",
        "models": ["step-3.7-flash", "step-3.5-flash", "step-2", "step-1"],
        "note": "原生多模态 + 高可靠工具调用",
    },
    "minimax": {
        "name": "MiniMax",
        "url": "https://api.minimax.chat/v1/chat/completions",
        "models": ["MiniMax-M2", "abab6.5s", "abab6.5", "abab5.5"],
        "note": "国内端点 api.minimax.chat",
    },
    "volcengine": {
        "name": "火山引擎 豆包",
        "url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "models": ["doubao-1.5-pro-32k", "doubao-1.5-lite-32k", "doubao-seed-1.6"],
        "note": "需在火山方舟控制台开通并获取 Endpoint ID",
    },
    "qianfan": {
        "name": "百度千帆 ERNIE",
        "url": "https://qianfan.baidubce.com/v2/chat/completions",
        "models": ["ernie-4.5", "ernie-4.0-turbo-8k", "ernie-3.5"],
        "note": "文心大模型，千帆 v2 端点",
    },
    "hunyuan": {
        "name": "腾讯混元",
        "url": "https://api.hunyuan.cloud.tencent.com/v1/chat/completions",
        "models": ["hunyuan-turbo", "hunyuan-standard", "hunyuan-lite"],
        "note": "腾讯云混元",
    },
    "siliconflow": {
        "name": "硅基流动 SiliconFlow",
        "url": "https://api.siliconflow.cn/v1/chat/completions",
        "models": ["Qwen/Qwen3-235B-A22B-Instruct", "deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"],
        "note": "聚合平台，国产模型+开源模型，新用户送额度",
    },

    # ── 聚合 / 中转站（适量，需自行注册获取 Key）──
    "openrouter": {
        "name": "OpenRouter (国际聚合)",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "models": ["deepseek/deepseek-chat", "anthropic/claude-sonnet-4", "openai/gpt-4o", "qwen/qwen-max"],
        "note": "数百模型一站调用，国际节点",
    },
    "custom": {
        "name": "Custom / 自建中转",
        "url": "",
        "models": [],
        "note": "one-api / new-api 等自建中转填这里，任意 OpenAI 兼容地址",
    },
}

DEFAULT_CONFIG = {
    "onebot_url": "http://127.0.0.1:3000",
    "onebot_token": "",
    "bot_qq": "",
    "target_qqs": [],
    "group_ids": [],
    "api_provider": "deepseek",
    "api_url": "https://api.deepseek.com/chat/completions",
    "model_name": "deepseek-flash",
    "bot_group_nick": "AAA",
    # Bot 的称呼（**可配多个**，配置中心「Bot 昵称」一行一个）。
    # 空列表 → 自动用 bot_group_nick；两者都空 → 兜底 ["小a"]。
    # 匹配大小写/全角/@ 不敏感（"小A"、＠小a 都算）。
    "bot_nicknames": [],
    "startup_cooldown": 60,
    "msg_split_len": 2000,
    "session_ttl": 300,
    "session_max_rounds": 30,
    "meme_cooldown": 60,

    # 协议层配置
    "protocol_enabled": False,
    "protocol_api_port": 3000,
    # 当前激活协议驱动：napcat | llonebot | snowluma
    "protocol_driver": "napcat",
    # 各驱动独立配置（与 protocol_driver 搭配）
    "protocol_drivers": {
        "napcat": {
            "exe_path": "",
            "qq_path": "",
            "api_url": "http://127.0.0.1:3100",
            "http_report_url": "http://127.0.0.1:3005",
            "ws_url": "ws://127.0.0.1:3101",
            "ws_port": 3101,
        },
        "llonebot": {
            "exe_path": "",
            "api_url": "http://127.0.0.1:3000/onebot/v11",
            "ws_port": 3001,
            "ws_url": "ws://127.0.0.1:3001",
            "webui_port": 7000,
            "http_report_url": "http://127.0.0.1:3005",
        },
        "snowluma": {
            "exe_path": "",
            "api_url": "http://127.0.0.1:3000",
            "ws_port": 3001,
            "ws_url": "ws://127.0.0.1:3001",
            "webui_port": 5099,
            "http_report_url": "http://127.0.0.1:3005",
        },
    },

    # 好友/加群/入群 请求自动同意策略
    "auto_accept": {
        "enabled": True,
        "friend": True,
        "group_add": True,
        "group_invite": True,
        "allowlist": [],
        "denylist": [],
        "welcome_private": "你好，我是小a～",
        "welcome_group": "进群了？坐！",
        "welcome_new_member": True,
        "add_to_targets": True,
    },
    # 群管理指令白名单（空 = 仅 Bot 本人 + command_admins + target_qqs）
    "admin_qqs": [],
    "admin_commands_enable": True,
    # 事件通知：加好友 / 加群 时自动私聊通知管理员 QQ（空 admin_qqs = 用上方 admin_qqs）
    "notify_admin": {
        "enabled": True,
        "friend_add": True,
        "group_increase": True,
        "admin_qqs": [],
    },
    # 群管理：每群限制配置（webui 群管理页管理）{"gid": {"reply_enabled": bool, "new_member_card": str}}
    "group_restrictions": {},
    # 好友管理：每好友限制配置（webui 联系人管理页管理）{"qq": {"reply_enabled": bool}}
    "friend_restrictions": {},

    # AI 行为
    "ai_temperature": 0.7,
    "ai_max_tokens": 800,
    "idle_timeout": 120,
    "ai_top_p": 0.9,
    "ai_frequency_penalty": 0.0,
    "ai_presence_penalty": 0.0,
    # 工具最多调几轮（2026-10-23）。此前非网页任务**只给一轮**：
    # 模型调完一个工具就强制收尾，「先搜 → 读正文 → 再总结」这种三拍活走不完。
    # 3 轮够覆盖绝大多数情况，且每轮都过上下文总量闸，不会失控。
    # 不想多花钱就设 1，行为退回旧逻辑。
    "max_tool_rounds": 3,

    # 历史进上下文的字符预算（2026-10-23）。
    # 原来最近几轮原文**不限长**：一句「嗯」和一条 3000 字网页结果同样算"一条"，
    # 实测 4 轮含长回复时最近区达 9556 token，压掉后 3364。
    # 只影响异常长的那几条，短对话体积一字不变。
    # 设为 0 关闭该闸（退回旧行为）。
    "history_recent_budget_chars": 6000,
    # 摘要账本的字符预算。行数上限（300 行）管不住体积：
    # 300 行 × 100 字 = 18750 token，是每轮都要重发的量。
    # 从头部裁，账本仍是 append-only，尾部追加不受影响。
    "history_ledger_budget_chars": 4000,

    # 去AI味
    "de_ai": {
        "enabled": True,
        "prompt_inject": True,
        "post_process": True,
        "ban_words": True,
        "ban_dashes": True,
        "ban_rule_of_three": True,
        "ban_filler": True,
        "ban_sycophancy": True,
        "max_sentence_len": 0,
    },

    # 会话管理
    "private_reply_interval": 3,

    # 群聊行为
    "group_msg_buffer_window": 3,
    "group_recent_msgs_window": 30,
    "group_session_max_replies": 20,
    "group_session_idle_timeout": 300,
    "group_msg_buffer_window_session": 5,
    # ★ 2026-10-23 15 → 8：这个值原来写 15，但整批 8 秒硬顶恒把它压成 8，
    #   于是 15 从来没生效过（第一次等 8 秒，之后每次最多 8 秒）。用户拍板统一为 8，
    #   配置与现实一致。想真的等更久要同时改 GROUP_BATCH_HARD_CAP。
    "group_msg_buffer_window_trigger": 8,
    "group_last_exit_cooldown": 3000,
    "poke_timeout": 30,
    "group_idle_exit_enable": True,
    "group_idle_exit_probability": 0.3,
    "group_idle_exit_min_silence": 600,

    # 群聊白名单（P0-2）：Bot 只在这些群里参与，其余群完全静默。
    #   [] 或未设置 = 不限制（兼容现状：所有群都参与，升级不改变现网行为）
    #   非空       = 只有列出的群号允许
    # 逐群开关 group_restrictions[gid].reply_enabled=False 优先级更高，始终拒绝。
    "group_whitelist": [],

    # 表情包
    "meme_enable": True,
    "meme_idle_exit_enable": True,
    "meme_auto_detect_emotion": True,

    # 撤回反馈
    "recall_feedback_enable": True,
    "recall_feedback_pre_messages": [
        "？",
        "你撤回了一条消息",
        "还好你撤了 我差点就回了",
        "撤回了？行 那我也不用回了",
        "还没看呢 你就撤了",
        "手速可以啊 我还没来得及回",
        "撤了就撤了吧 反正我也没打算回",
        "哎哟 差点就点发送了",
        "你撤回的速度比我打字快",
        "还好还好 还没发出去",
        "撤回+1 这条我当没看见",
        "不回了不回了 你都撤了我还回啥",
        "撤得好 我正愁怎么接",
        "你撤回 我省事",
        "这波属于是你跑得快",
        "撤都撤了 那就算了",
        "还没读呢就撤了 算你识相",
        "行吧 本AI的回复可以省了",
        "撤回=跳过 这局不算",
        "差点白打字了 幸好你撤了",
        "你撤你的 我当无事发生",
        "撤回成功 已为你拦截一条AI回复",
    ],
    "recall_feedback_post_messages": [
        "看到了 晚了",
        "看到了哦 不说",
        "太慢了 我已经看到了",
        "来不及了 嘿嘿",
        "撤回慢了 已截获",
        "晚了 我看到了",
        "回复已经发出去了 哈哈",
        "不好意思 你撤的时候我已经看到了",
        "看到了 不过我不说",
        "来不及啦 我已经读过了",
        "晚啦 眼睛比你手快",
        "撤回无效 本人已阅",
        "看到了看到了 别费劲了",
        "我看到了 你撤了个寂寞",
        "撤晚了 我已经知道了",
        "不巧 我刚好在看",
        "被我看到了吧 嘿嘿",
        "你撤回的速度还是不够快啊",
        "看到了 但我会装作没看到",
        "太迟了 消息已送达我脑子里",
    ],
    "recall_feedback_cooldown": 60,

    # 撤回反馈（私聊）：独立开关/冷却/文案，与群聊撤回互不影响
    "recall_private_enable": True,
    "recall_private_cooldown": 60,
    "recall_private_post_messages": [
        "撤回了？我还没看到",
        "什么情况 手滑了？",
        "你撤回的速度比打字快多了",
        "心虚了吧",
        "好家伙 说撤就撤",
        "别撤回啊 发都发了",
    ],

    # 功能开关
    "image_cache_enable": True,
    # 图片进对话的方式（2026-10-12；2026-10-19 改默认）：
    #   native   = 直接把图作为 image_url 交给主模型
    #              ⚠ 实测（经 sensenova 中转）：图片请求连续两次 HTTP 429
    #              （TPM/RPM 超限），模型随后自称"看不到图"；纯文本主模型更读不了。
    #   describe = 先用免费车道（free_llm）把图转成文字描述再注入 prompt
    #              —— 对**任何**主模型都成立。实测 `describe_image()` 能准确认出
    #              歌词截图并说出歌名，故改为默认。
    #   off      = 不处理（老行为：群聊里图片只剩一坨 CQ 链接）
    "image_reply_mode": "describe",
    # 群聊回复要不要带**引用**（像 qq-bridge 那样指着某条话说）
    #   off=不引用  auto=只在"对象明确"时引用（合并了多条历史/消息本身带引用）  always=每条都引用
    "group_quote_mode": "auto",
    "reply_vision_max_images": 3,
    "self_learning_enable": True,
    # ── 自主学习：排除"其他 AI 机器人"的发言（2026-10-19 线上实证）──
    #  群里跑着另一个 bot（实测 uid 10000004 / 10000005，名片都叫
    #  「另一个AI机器人」），它的 107+98 条消息被当成"群友表达风格"学进了
    #  personality.txt，占学习语料 37% —— EggBloom 开始模仿另一个 AI 的腔调，
    #  这是"越来越像机器人"的直接来源。
    #  ① 显式黑名单：填 QQ 号，绝不学习
    "self_learn_ignore_qqs": [],
    #  ② 人工白名单：即使昵称像 AI 也照学（真人故意起个 AI 名的情况）
    "self_learn_allow_qqs": [],
    #  ③ 自动识别：**只看全局昵称**（不看群名片 —— 名片是可以随便改的 cosplay，
    #     实测有人把名片改成「另一个AI机器人」但其实是真人，拿名片判会误杀）
    "self_learn_exclude_ai_names": True,
    #  ④ **严格化核心：表达必须在群里重复出现过，才准写进人格**
    #     实测被污染的 5 条"群表达风格"全是 count=1 的一次性句子。
    #     真实群习惯一定重复；AI 的漂亮话常常只出现一次。
    #     值=2 表示"至少被两次独立发言验证过"。调高更保守（3 更严）。
    "self_learn_min_count": 2,
    "private_memory_enable": True,
    "slang_injector_enable": True,
    "nsfw_enabled": True,

    # 消息长度
    "private_reply_max_length": 500,
    "group_reply_max_length": 800,

    # 自定义内容
    "custom_exit_phrases_short": [],
    "custom_exit_phrases_idle": [],

    # 群触发词
    "group_trigger_words": ["小a"],
    "group_trigger_include_bot_nick": True,
    "group_trigger_include_text_at": True,
    "group_trigger_include_quote": True,

    # 界面
    "ui_language": "zh",
    "ui_theme": "default",

    # 向量嵌入默认路径（可由 bot_config.json 覆盖，避免硬编码）
    "embed_exe": "",
    "embed_model_path": "",
    "embed_dll": "",
    "embed_model": "bge-small-zh",
    "embed_url": "http://127.0.0.1:8083/v1/embeddings",
}

_config_cache = None
# 配置文件签名（mtime, size），用于弱缓存：文件未变直接返回缓存，变了自动重读
_config_cache_sig = None


def invalidate_config_cache():
    """清除配置缓存：保存配置后调用，下次 load_config 强制重读磁盘。"""
    global _config_cache, _config_cache_sig
    _config_cache = None
    _config_cache_sig = None


def _file_sig():
    """返回三个配置文件的 (mtime, size) 签名；任一变化即视为配置变更。"""
    def _sig(p):
        try:
            st = os.stat(p)
            return (st.st_mtime, st.st_size)
        except OSError:
            return None
    return (_sig(APP_CONFIG_FILE), _sig(CONFIG_FILE), _sig(ENV_FILE))


def _load_env():
    """Read .env file"""
    env = {}
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, _, v = line.partition("=")
                        env[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
    return env


def load_config():
    """Load config: json + env override. API key NEVER in json.

    弱缓存：文件 (mtime, size) 未变化时直接返回上次结果（高频读取省 IO）；
    任一文件变更自动重读；保存后也可显式调 invalidate_config_cache() 强制刷新。
    """
    global _config_cache, _config_cache_sig
    sig = _file_sig()
    if _config_cache is not None and sig == _config_cache_sig:
        return _config_cache
    cfg = DEFAULT_CONFIG.copy()

    if os.path.exists(APP_CONFIG_FILE):
        try:
            with open(APP_CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                file_cfg = json.load(f)
            cfg.update(file_cfg)
        except Exception:
            pass

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                appdata_cfg = json.load(f)
            # AppData 仅补缺，不覆盖程序目录已有字段
            for k, v in appdata_cfg.items():
                if k not in cfg or cfg.get(k) in (None, "", [], {}):
                    cfg[k] = v
        except Exception:
            pass

    env = _load_env()
    # env 只补缺，不覆盖 bot_config.json 已有值：
    # 之前 save_env 会把 API_URL/MODEL_NAME 写进 .env，导致 WebUI 切换
    # 非 DeepSeek 提供商后，加载时又被 .env 里的 DeepSeek 残留值覆盖（配置"不生效"）。
    # 改为 json 优先、env 补缺；凭据管理器优先级最高（见下）。
    for ek, ck in [("API_KEY", "api_key"), ("DEEPSEEK_API_KEY", "api_key"),
                   ("API_URL", "api_url"), ("MODEL_NAME", "model_name"),
                   ("BOT_QQ", "bot_qq"), ("ONE_BOT_URL", "onebot_url"),
                   ("ONE_BOT_TOKEN", "onebot_token")]:
        if env.get(ek) and not cfg.get(ck):
            cfg[ck] = env[ek]
    # Windows 凭据管理器优先：避免明文 key 落盘
    cred_key = read_cred(MODEL_API_KEY_CRED)
    if cred_key:
        cfg["api_key"] = cred_key

    # Migrate old field
    if cfg.get("deepseek_api_key") and not cfg.get("api_key"):
        cfg["api_key"] = cfg.pop("deepseek_api_key")
    cfg.pop("deepseek_api_key", None)
    cfg.pop("deepseek_url", None)

    if not cfg.get("api_url"):
        prov = cfg.get("api_provider", "deepseek")
        cfg["api_url"] = API_PROVIDERS.get(prov, {}).get(
            "url", "https://api.deepseek.com/chat/completions")

    _config_cache = cfg
    _config_cache_sig = sig
    return cfg


def save_config(cfg, save_api_key=False):
    """Save config 到程序目录 bot_config.json（优先），失败回退 AppData。

    与 bot_core._persist_config_file / webui.save_config 保持一致，统一落
    程序目录真实文件，避免 Store 版 Python(MSIX) 把 %LOCALAPPDATA% 重定向
    到沙箱导致配置写入不可见。保存后自动失效缓存，下次读取立即生效。
    """
    to_save = {k: v for k, v in cfg.items() if k != "api_key"}
    if save_api_key and cfg.get("api_key"):
        to_save["api_key"] = cfg["api_key"]
    text = json.dumps(to_save, ensure_ascii=False, indent=2)
    # 写程序目录（与 load_config 的程序目录优先一致）
    try:
        with open(APP_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(text)
        invalidate_config_cache()
        return True
    except Exception:
        pass
    # 程序目录不可写（权限/只读）时回退 AppData
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(text)
        invalidate_config_cache()
        return True
    except Exception:
        return False


def save_env(api_key="", api_url="", model=""):
    """Save sensitive config：api_key 写入 Windows 凭据管理器（不落盘明文）。

    API_URL / MODEL_NAME 不再写入 .env：它们属于 bot_config.json 的普通配置，
    写进 .env 会在 load_config 时以残留值覆盖 WebUI 新保存的提供商配置
    （切换非 DeepSeek 提供商后 Bot 仍走 DeepSeek 的根因）。这里顺带清理旧残留。
    """
    if api_key and api_key.strip() and "YOUR" not in api_key:
        try:
            from cred_store import write_cred, MODEL_API_KEY_CRED
            write_cred(MODEL_API_KEY_CRED, api_key.strip())
        except Exception:
            pass

    lines = []
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            lines = []

    new_lines = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            new_lines.append(line.rstrip())
            continue
        if "=" in s:
            k = s.partition("=")[0].strip()
            if k in ("API_KEY", "DEEPSEEK_API_KEY", "API_URL", "MODEL_NAME"):
                continue  # 敏感项走凭据管理器；URL/模型名走 bot_config.json
            new_lines.append(line.rstrip())
        else:
            new_lines.append(line.rstrip())

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")
def get_api_key():
    """Get API key safely（凭据管理器 > env）"""
    cred_key = read_cred(MODEL_API_KEY_CRED)
    if cred_key:
        return cred_key
    cfg = load_config()
    return (cfg.get("api_key", "") or
            os.environ.get("API_KEY", "") or
            os.environ.get("DEEPSEEK_API_KEY", ""))


def migrate_plain_api_key(plain_key):
    """把旧明文 key 迁移进凭据管理器，成功返回 True。"""
    if not plain_key or not plain_key.strip():
        return False
    try:
        from cred_store import write_cred, MODEL_API_KEY_CRED
        return bool(write_cred(MODEL_API_KEY_CRED, plain_key.strip()))
    except Exception:
        return False


def provider_cred_name(provider_id):
    """每提供商专属凭据条目名：EggBloom.MODEL_API_KEY_{provider_id}。

    让每个提供商可以保存独立 API Key（互不覆盖），未指定时回退全局条目。
    """
    pid = (provider_id or "").strip()
    if not pid:
        return MODEL_API_KEY_CRED
    return f"{MODEL_API_KEY_CRED}_{pid}"


def get_provider_api_key(provider_id):
    """解析某提供商实际使用的 API Key：该提供商专属凭据 > 全局凭据。

    提供商切换时不会把上一个提供商的 Key 误发给新服务商。
    """
    try:
        from cred_store import read_cred
        k = read_cred(provider_cred_name(provider_id))
        if k:
            return k
    except Exception:
        pass
    return get_api_key()


def save_provider_api_key(provider_id, key):
    """把某提供商的 API Key 写入凭据管理器（专属条目），成功返回 True。"""
    key = (key or "").strip()
    pid = (provider_id or "").strip()
    if not pid or not key or "YOUR" in key:
        return False
    try:
        from cred_store import write_cred
        return bool(write_cred(provider_cred_name(pid), key))
    except Exception:
        return False


def clear_provider_api_key(provider_id):
    """删除某提供商的专属凭据（用于清除独立 Key / 删除提供商时清理）。"""
    pid = (provider_id or "").strip()
    if not pid:
        return False
    try:
        from cred_store import delete_cred
        return bool(delete_cred(provider_cred_name(pid)))
    except Exception:
        return False
