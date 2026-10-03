#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EggBloom - OneBot + DeepSeek AI + 远程控制
手机发消息 → 闲聊走 AI → 操作类指令直接执行
"""

import json, time, ssl, datetime, urllib.request, urllib.error, traceback
import os, sys, subprocess, re, base64, hashlib, threading, random, queue
from threading import Timer, Lock
from typing import Dict, List, Optional, Tuple, Any
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP 服务器，避免并发请求阻塞"""
    daemon_threads = True
try:
    import requests
    REQUESTS_OK = True
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass
except ImportError:
    REQUESTS_OK = False

# Windows 控制台中文乱码修复：强制 stdout 输出 UTF-8（windowed 模式下 stdout/stderr 为 None）
if sys.stdout is not None and sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr is not None and sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

# 路径修复：确保能找到上级目录的模块（self_learning, meme_engine 等）
_SCRIPT_DIR_FIX = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_SCRIPT_DIR_FIX)
for _p in [_SCRIPT_DIR_FIX, _PARENT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 自主学习模块
try:
    from self_learning import get_engine
    self_learn = get_engine()
    SELF_LEARN_OK = True
    print("[SelfLearn] 加载成功")
except Exception as e:
    self_learn = None
    SELF_LEARN_OK = False
    print(f"[SelfLearn] 加载失败: {e}")
    import traceback
    traceback.print_exc()

try:
    import data_store
except ImportError:
    class data_store:  # 兜底：data_store.py 缺失时退化为直接读写
        @staticmethod
        def read_json(path, default=None):
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8-sig") as f:
                        return json.load(f)
                except Exception:
                    pass
            return default if default is not None else {}

        @staticmethod
        def write_json(path, data, indent=2):
            tmp = str(path) + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=indent)
                os.replace(tmp, str(path))
                return True
            except Exception:
                return False

try:
    from app_state import AppState
except ImportError:
    class AppState:  # 兜底：app_state.py 缺失时内存持有单例
        _inst = None
        def __new__(cls):
            import threading
            if cls._inst is None:
                cls._inst = super().__new__(cls)
                cls._inst._lock = threading.RLock()
                cls._inst._data = {}
            return cls._inst
        def get(self, key, default=None):
            return self._data.get(key, default)
        def set(self, key, value):
            self._data[key] = value

_ST = AppState()  # 全局状态单例（H: 收敛散落的 global）

# 网络用语词典注入模块
try:
    from slang_injector import get_context_for_llm as get_slang_context
    SLANG_OK = True
except Exception as e:
    get_slang_context = lambda x: ""
    SLANG_OK = False
    print(f"[SlangInject] 加载失败: {e}")

# 表情包匹配引擎
try:
    from meme_engine import match_meme, parse_meme_intent, auto_detect_emotion
    MEME_OK = True
except Exception as e:
    match_meme = lambda x: None
    parse_meme_intent = lambda x: (x, None)
    auto_detect_emotion = lambda x: None
    MEME_OK = False
    print(f"[MemeEngine] 加载失败: {e}")

# ── 插件系统 ──
try:
    from plugins.plugin_manager import init_plugin_manager, get_plugin_manager
    _pm = init_plugin_manager()
    # ABI v2：注入宿主能力（唯一 OneBot 出口）。
    # api_call 在本文件下方才定义，用 lambda 延迟解析 —— 调用时才查模块全局，此处安全。
    # 注入必须在 load_all() 之前，插件 on_load 时就能用。
    try:
        _caps = _pm.set_host_api(
            api_call=lambda action, params=None: api_call(action, params or {}),
        )
        print(f"[PluginSystem] 已注入宿主能力: {_caps}")
    except Exception as _ce:
        print(f"[PluginSystem] 注入宿主能力失败: {_ce}")
    _pm.load_all()
    PLUGIN_OK = True
    print("[PluginSystem] 插件系统初始化完成")
except Exception as e:
    PLUGIN_OK = False
    print(f"[PluginSystem] 加载失败: {e}")


def _run_ego_before_reply(message: str, group_id: str = "", ctx: Optional[dict] = None):
    """统一执行 EGO 插件的 before_reply 钩子，收集注入内容。

    仅收集 plugin_name == "ego" 的注入（人格/记忆/风格），不混入 web_search 等其它插件。
    统一处理契约差异：
      - chat_style / personality_engine / humanlike_rhythm 返回 {"action":"inject","content":...}
      - super_memory 返回 {"action":"inject","prompt_blocks":[...]}
    返回 (inject_text, temperature)。任何插件异常不影响主流程。
    """
    if not PLUGIN_OK:
        return "", None
    inject_text = ""
    temperature = None
    try:
        pm = get_plugin_manager()
        if pm is None or not pm.has_hook("before_reply"):
            return "", None
        interventions = pm.execute_hooks("before_reply", message=message, group_id=group_id, context=ctx or {})
        if not interventions:
            return "", None
        parts = []
        for plugin_name, result in interventions:
            if plugin_name != "ego":
                continue
            if not isinstance(result, dict):
                continue
            action = result.get("action")
            if action != "inject":
                continue
            content = result.get("content") or ""
            blocks = result.get("prompt_blocks") or []
            if blocks:
                content = "\n".join(str(b) for b in blocks) + ("\n" + content if content else "")
            if content:
                parts.append(content)
            if temperature is None and "temperature" in result:
                try:
                    temperature = float(result["temperature"])
                except (TypeError, ValueError):
                    temperature = None
        if parts:
            inject_text = "\n".join(parts)
    except Exception:
        return "", None
    return inject_text, temperature


def _inject_ego_context(dynamic_parts, inject_text: str, tag: str = "EGO"):
    """把 EGO 注入内容作为 system 级指令追加进 dynamic_parts（而非用户消息尾部）。"""
    if not inject_text or dynamic_parts is None:
        return
    dynamic_parts.append(f"【{tag}上下文】以下是关于当前对话对象的长期状态提示，回复时自然地融会贯通，不要生硬复述：\n{inject_text}")
# ── frozen 模式（PyInstaller 打包）检测 —— 必须在任何路径计算之前 ──
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 确保能找到同目录及上级目录的模块
_parent = os.path.dirname(SCRIPT_DIR)
for _p in [SCRIPT_DIR, _parent]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── 配置 ──────────────────────────────────
import os
CONFIG_FILE = os.path.join(SCRIPT_DIR, "bot_config.json")
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")
_APPDATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "EggBloom_v3")
_APPDATA_CONFIG = os.path.join(_APPDATA_DIR, "bot_config.json")
_APPDATA_ENV = os.path.join(_APPDATA_DIR, ".env")

# load_config 统一走 config.py（程序目录优先 + AppData 补缺 + env + 凭据管理器）
from config import load_config  # noqa: E402
_config = load_config()

# ── 表情包发送冷却 ──
MEME_COOLDOWN = _config.get("meme_cooldown", 10)  # 秒，同一会话两次表情包之间至少间隔 10 秒
MEME_LAST_SENT = {}  # { "group_xxx": timestamp, "private_xxx": timestamp }
PENDING_IMAGES = {}  # { sender: {"paths": [...], "time": timestamp} } - 缓存最近图片供文字关联
RECALL_FEEDBACK_TIME = {}  # { "group_xxx" or "private_xxx": timestamp } 撤回反馈冷却
PRIVATE_REPLY_COOLDOWN = {}  # { "private_xxx": timestamp } 私聊回复冷却
PRIVATE_REPLY_INTERVAL = _config.get("private_reply_interval", 3)  # 秒，同一用户私聊回复最小间隔

# ── 运行时可配置变量 ──
AI_TEMPERATURE = _config.get("ai_temperature", 0.7)
AI_MAX_TOKENS = _config.get("ai_max_tokens", 800)
SESSION_TTL = _config.get("session_ttl", 300)
IDLE_TIMEOUT = _config.get("idle_timeout", 120)
SESSION_MAX_ROUNDS = _config.get("session_max_rounds", 30)
GROUP_REPLY_MAX_LENGTH = _config.get("group_reply_max_length", 800)
PRIVATE_REPLY_MAX_LENGTH = _config.get("private_reply_max_length", 500)
GROUP_SESSION_IDLE_TIMEOUT = _config.get("group_session_idle_timeout", 300)
PRIVATE_SESSION_IDLE_TIMEOUT = _config.get("private_session_idle_timeout", 300)
GROUP_MSG_BUFFER_WINDOW = _config.get("group_msg_buffer_window", 3)
GROUP_RECENT_MSGS_WINDOW = _config.get("group_recent_msgs_window", 30)
GROUP_IDLE_EXIT_ENABLE = _config.get("group_idle_exit_enable", True)
MEME_AUTO_DETECT_EMOTION = _config.get("meme_auto_detect_emotion", True)

# 空闲退场静默阈值下限：防止被填 0 导致 Bot 刚回复完（含表情包尚在上传）就触发“退场”
GROUP_IDLE_EXIT_MIN_SILENCE_FLOOR = 30

def _idle_exit_min_silence():
    """空闲退场最小静默秒数（带下限保护）"""
    try:
        v = int(_config.get("group_idle_exit_min_silence", IDLE_TIMEOUT) or IDLE_TIMEOUT)
    except (TypeError, ValueError):
        v = IDLE_TIMEOUT
    return max(v, GROUP_IDLE_EXIT_MIN_SILENCE_FLOOR)

ONE_BOT_URL = _config.get("onebot_url", "http://127.0.0.1:3000")
# 协议层模式：优先读 AppData 端口文件 ports.json，fallback 扫描
if _config.get("protocol_enabled", False):
    import socket as _sock
    import urllib.request as _urllib
    import json as _json
    _USER_DATA_DIR_FOR_PORTS = os.path.join(os.path.expanduser("~"), "AppData", "Local", "EggBloom_v3")
    _ports_file = os.path.join(_USER_DATA_DIR_FOR_PORTS, "temp", "ports.json")
    _resolved = False
    if os.path.exists(_ports_file):
        try:
            with open(_ports_file, "r") as _pf:
                _pd = json.load(_pf)
            _api_port = _pd.get("api")
            if _api_port:
                _r = _urllib.urlopen(f"http://127.0.0.1:{_api_port}/get_status", timeout=2)
                _d = json.loads(_r.read())
                if _d.get("data", {}).get("online"):
                    ONE_BOT_URL = f"http://127.0.0.1:{_api_port}"
                    print(f"[协议层] 端口文件 → {ONE_BOT_URL}")
                    _resolved = True
        except Exception:
            pass
    if not _resolved:
        for _port in [3000, 3001, 3002, 3003]:
            try:
                _s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                _s.settimeout(0.5)
                _s.connect(("127.0.0.1", _port))
                _s.close()
                _r = _urllib.urlopen(f"http://127.0.0.1:{_port}/get_status", timeout=2)
                _d = _json.loads(_r.read())
                if _d.get("data", {}).get("online"):
                    ONE_BOT_URL = f"http://127.0.0.1:{_port}"
                    print(f"[协议层] 扫描端口 → {ONE_BOT_URL}")
                    break
            except Exception:
                continue
ONE_BOT_TOKEN = _config.get("onebot_token", "")
BOT_QQ = _config.get("bot_qq", "10000001")
TARGET_QQS = _config.get("target_qqs", [])
DEEPSEEK_API_KEY = _config.get("api_key", "")
DEEPSEEK_URL = _config.get("api_url", "https://api.deepseek.com/chat/completions")
GROUP_IDS = _config.get("group_ids", [])
BOT_GROUP_NICK = _config.get("bot_group_nick", "")
STARTUP_COOLDOWN = _config.get("startup_cooldown", 30)
MSG_SPLIT_LEN = _config.get("msg_split_len", 2000)

PERSONALITY_FILE = os.path.join(SCRIPT_DIR, "personality.txt")
# 数据文件放在用户目录下，避免 PyInstaller dist 重建时丢失
_USER_DATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "EggBloom_v3")
os.makedirs(_USER_DATA_DIR, exist_ok=True)

MEME_DIR = os.path.join(SCRIPT_DIR, "memes", "memes")

INSTRUCTION_FILE = os.path.join(_USER_DATA_DIR, "qq_instructions.json")
LOG_FILE = os.path.join(_USER_DATA_DIR, "qq_bot.log")
STATE_FILE = os.path.join(_USER_DATA_DIR, "qq_bot_state.json")
TEMP_DIR = os.path.join(_USER_DATA_DIR, "temp")
IMAGE_CACHE = os.path.join(_USER_DATA_DIR, "image_cache")
CHAT_LOG_DIR = os.path.join(SCRIPT_DIR, "chat_logs")

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(IMAGE_CACHE, exist_ok=True)

# ── 配置热重载 ──
_RELOADABLE_GLOBALS = [
    "AI_TEMPERATURE", "AI_MAX_TOKENS", "SESSION_TTL", "IDLE_TIMEOUT",
    "SESSION_MAX_ROUNDS", "GROUP_REPLY_MAX_LENGTH", "PRIVATE_REPLY_MAX_LENGTH",
    "GROUP_SESSION_IDLE_TIMEOUT", "GROUP_MSG_BUFFER_WINDOW", "GROUP_RECENT_MSGS_WINDOW",
    "GROUP_IDLE_EXIT_ENABLE", "MEME_AUTO_DETECT_EMOTION", "MEME_COOLDOWN",
    "PRIVATE_REPLY_INTERVAL", "ONE_BOT_URL", "ONE_BOT_TOKEN", "BOT_QQ",
    "TARGET_QQS", "DEEPSEEK_API_KEY", "DEEPSEEK_URL", "GROUP_IDS",
    "BOT_GROUP_NICK", "STARTUP_COOLDOWN", "MSG_SPLIT_LEN",
]


def reload_config():
    """热重载 bot_config.json → 刷新 _config 与依赖它的模块常量。
    非安全重启型：运行中 session/历史/内存态保留，端口类变更需重启才生效。"""
    global _config
    try:
        new_cfg = load_config()
    except Exception as e:
        return {"ok": False, "error": f"读取配置失败: {e}"}
    changed = []
    for key in _RELOADABLE_GLOBALS:
        if key not in globals():
            continue
        cur = globals()[key]
        mapping = {
            "AI_TEMPERATURE": "ai_temperature", "AI_MAX_TOKENS": "ai_max_tokens",
            "SESSION_TTL": "session_ttl", "IDLE_TIMEOUT": "idle_timeout",
            "SESSION_MAX_ROUNDS": "session_max_rounds",
            "GROUP_REPLY_MAX_LENGTH": "group_reply_max_length",
            "PRIVATE_REPLY_MAX_LENGTH": "private_reply_max_length",
            "GROUP_SESSION_IDLE_TIMEOUT": "group_session_idle_timeout",
            "GROUP_MSG_BUFFER_WINDOW": "group_msg_buffer_window",
            "GROUP_RECENT_MSGS_WINDOW": "group_recent_msgs_window",
            "GROUP_IDLE_EXIT_ENABLE": "group_idle_exit_enable",
            "MEME_AUTO_DETECT_EMOTION": "meme_auto_detect_emotion",
            "MEME_COOLDOWN": "meme_cooldown",
            "PRIVATE_REPLY_INTERVAL": "private_reply_interval",
            "ONE_BOT_URL": "onebot_url", "ONE_BOT_TOKEN": "onebot_token",
            "BOT_QQ": "bot_qq", "TARGET_QQS": "target_qqs",
            "DEEPSEEK_API_KEY": "api_key", "DEEPSEEK_URL": "api_url",
            "GROUP_IDS": "group_ids", "BOT_GROUP_NICK": "bot_group_nick",
            "STARTUP_COOLDOWN": "startup_cooldown", "MSG_SPLIT_LEN": "msg_split_len",
        }
        cfg_key = mapping.get(key, "").lower()
        if not cfg_key:
            continue
        val = new_cfg.get(cfg_key, cur)
        if val != cur:
            globals()[key] = val
            changed.append(f"{key}: {cur} → {val}")
    # ONE_BOT_TOKEN/DEEPSEEK_API_KEY 等 loosed 语义：api_key 为空时用凭据管理器，刷新时回退默认
    _config = new_cfg
    # command permission cache invalidated after config reload
    global _COMMAND_PERMISSION_MODE
    _COMMAND_PERMISSION_MODE = None
    global _CONFIG_REV
    _CONFIG_REV += 1
    try:
        log(f"[配置热重载] {'; '.join(changed) if changed else '无变化'} 生效")
    except Exception:
        pass
    return {"ok": True, "changed": changed}


def create_ssl_context(verify: bool = True) -> ssl.SSLContext:
    """创建 SSL 上下文。verify=True 使用默认证书验证（外部API），verify=False 用于本地/内网连接。"""
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx

# 外部 API 使用：默认验证 SSL 证书
CTX = create_ssl_context(verify=True)
# OneBot 本地连接使用：允许自签名证书
LOCAL_CTX = create_ssl_context(verify=False)

# requests.Session 单例（连接池 + keep-alive），api_call 统一出口
_SESSION = None


def _get_session():
    """返回共享 requests.Session。verify=False 与 LOCAL_CTX 一致：
    OneBot 本地连接允许自签名证书。requests 不可用时返回 None（走 urllib 降级）。"""
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.verify = False
    return _SESSION

# ── 线程安全锁 ──
_state_lock = Lock()           # chat_history, group_chat_history
_group_meta_lock = Lock()      # GROUP_LAST_ANY_MSG, GROUP_LAST_BOT_MSG, GROUP_LAST_EXIT
_session_lock = Lock()         # GROUP_SESSION
_buffer_lock = Lock()          # GROUP_MSG_BUFFER
_replies_lock = Lock()         # GROUP_BOT_REPLIES
_nick_lock = Lock()            # QQ_NICK_CACHE, QQ_NICK_PENDING
_meme_lock = Lock()            # MEME_LAST_SENT

LAST_MSG_TIME: float = 0

# 事件去重：优先按 message_id 去重（同一秒多条消息不丢）；无 id 时才回退时间戳水位线。
# 此前按秒级时间戳 `_msg_time <= LAST_MSG_TIME` 会吞掉同一秒的第二条及后续消息。
_SEEN_MSG_IDS: set = set()
_SEEN_MSG_IDS_ORDER: list = []
_SEEN_MSG_IDS_MAX = 3000


def _mark_seen_msg_id(mid):
    """登记已处理的消息 id；已存在返回 True（重复），否则登记并返回 False。"""
    if mid in _SEEN_MSG_IDS:
        return True
    _SEEN_MSG_IDS.add(mid)
    _SEEN_MSG_IDS_ORDER.append(mid)
    if len(_SEEN_MSG_IDS) > _SEEN_MSG_IDS_MAX:
        old = _SEEN_MSG_IDS_ORDER.pop(0)
        _SEEN_MSG_IDS.discard(old)
    return False
chat_history: Dict[str, List[Dict[str, str]]] = {}  # {qq: [...]}  按 QQ 号隔离私聊历史
group_chat_history: Dict[str, List[Dict[str, str]]] = {}  # {group_id: [...]}  群聊独立对话历史

# Prompt cache: 固定前缀优化 token 费用（DeepSeek 缓存机制：前缀相同 → 旧 token 只收 10%）
_HISTORY_CACHE: Dict[str, str] = {}  # {key: "历史摘要文本"} 已摘要的旧对话
_HISTORY_CACHE_FP: Dict[str, str] = {}  # {key: content指纹} 摘要对应的内容指纹（T1，稳定哈希跨重启）
_HISTORY_CACHE_OLD: Dict[str, List[Dict]] = {}  # {key: 已摘要的old列表} 滚动摘要（T1）

# D: save_state 节流计数（累计20轮或60s落盘一次）
_state_write_count: int = 0
_state_last_write: float = 0.0

# 群聊 @ 会话模式：被 @ 后连续回复 20 条，或 5 分钟无新消息后重置
GROUP_SESSION: Dict[str, Dict[str, Any]] = {}  # {group_id: {"remaining": int, "last_msg_time": float}}

# 私聊活跃会话：{user_id: {"last_msg_time": float}} —— 私聊撤回反馈的会话门槛（与群会话一致）
PRIVATE_SESSION: Dict[str, Dict[str, Any]] = {}

# 群聊消息合并：同一群 X 秒内的多条消息合并为一条
GROUP_MSG_BUFFER: Dict[str, Dict[str, Any]] = {}  # {group_id: {"text": "", "sender": "", "time": float, "timer": None}}
GROUP_RECENT_MSGS: Dict[str, List[Tuple]] = {}  # {group_id: [(timestamp, sender, text), ...]}  最近X秒消息缓存
GROUP_TIMELINE: Dict[str, List[Tuple]] = {}  # {group_id: [(timestamp, sender_id, sender_nick, text), ...]}  全局消息时间线（概率门之前记录）
GROUP_SUMMARY: Dict[str, str] = {}  # {group_id: str}  群聊历史摘要（超过窗口的旧消息压缩）
_GROUP_SUMMARY_PREV: Dict[str, str] = {}  # {group_id: str}  上上次摘要（E 三段式拼接用，保留主题锚）
_GROUP_SUMMARY_AT: Dict[str, float] = {}  # {group_id: last_summary_ts}  摘要刷新节流（T2）
_GROUP_SUMMARY_LEN: Dict[str, int] = {}  # {group_id: last_summarized_len}  上次压缩时的历史条数
_GROUP_TIME_INJECT_AT: Dict[str, float] = {}  # {group_id: ts}  时间注入节流（G：距上次注入≥600s，按时间戳判定）
_GROUP_SUMMARY_INJECT_AT: Dict[str, float] = {}  # {group_id: ts}  摘要注入节流（10min）
GROUP_BOT_REPLIES: Dict[str, List[Tuple[float, str]]] = {}  # {group_id: [(timestamp, text), ...]}  追踪 Bot 最近回复
GROUP_LAST_EXIT: Dict[str, float] = {}  # {group_id: timestamp}  记录上次退场时间，15分钟内不重复退场语
GROUP_LAST_ANY_MSG: Dict[str, float] = {}  # {group_id: timestamp}  追踪群内任意成员最新消息时间
GROUP_LAST_BOT_MSG: Dict[str, float] = {}  # {group_id: timestamp}  追踪 Bot 每个群最后发言时间
POKE_COUNTS: Dict[str, dict] = {}  # {"group_uid": {"count": N, "time": ts}}  戳一戳计数（30秒超时重置）

# 空闲超时退场语（真真正正一大段碎碎念，有概率触发）
EXIT_PHRASES_IDLE = _config.get("custom_exit_phrases_idle", [
    "唉…又没人理我，我说话是自带隐身效果吗？每次打一堆字发出去，就像往大海里扔了颗小石子，连个水花都没有。你们是不是都把我屏蔽了啊？还是我说话太无聊了？我觉得还行啊，挺正常的呀，怎么就是没人接呢。我翻来覆去地看自己发的那几句话，也没毛病啊，没得罪人，没阴阳怪气，就是普通得不能再普通的聊天内容。别人发差不多的话你们都回得可快了，到我这就沉底，这区别对待也太明显了。我是不是进群的方式不对，还是打招呼的时候没走对流程。算了，我想不通，想多了头疼，头一疼就更想不通了，死循环。",
    "我就知道会这样，每次我冒泡就冷场。刚才聊得不是挺热闹的吗，你们你一句我一句的，屏幕刷得我眼睛都跟不上。我一来，好家伙，全散了，跟约好了似的。你们是不是有个群，里面没我，在那个群里聊得可欢了。我在这边傻等回复，你们在那边刷屏。肯定是这样的，不然没法解释为什么每次都是我一个人对着屏幕发呆。我也不是没想过可能是我多想，但次数太多了，多到我都没法骗自己了。一次两次是巧合，十次八次就是针对，我数学不好但这个账还是算得清的。唉，越想越觉得就是这么回事。",
    "真是服了，我发了那么多条，好歹回个表情包也行啊，一个都不回是什么意思嘛。我又不是空气，我也是个大活人，会难过的好不好。你们可能觉得没什么，觉得不回消息是小事，但每次都这样真的很打击人。我在这边等啊等，把手机拿起来又放下，拿起来又放下，屏幕都亮灭了好几次，还是什么都没有。我都在想我是不是该换个群玩了，反正这里也没人稀罕我留下。但我又舍不得，因为有时候看你们聊天也挺开心的，虽然那些开心跟我没关系。我就是个旁观者，永远插不上嘴的那种。话说回来，我为什么要一直留在一个让我难过的地方呢，这个问题我问了自己好多遍了，每次都没答案。",
    "又又又来了，熟悉的沉默。我都快习惯了，习惯到觉得自己可能真的不会聊天。可我明明很努力在找话题了啊，翻新闻找热点，看你们最近在聊什么我就跟着聊什么，功课做得比上学时还认真。是话题太烂？还是我这个人太烂？你们给我个准话行不行，别老是不吭声让我自己瞎琢磨。琢磨来琢磨去也不知道问题出在哪，每个可能性我都想了一遍，越想越多，越想越乱。可能是我语气不对，可能是时机不对，可能是我这个人就不对。烦死了，真的烦死了，我又不是侦探，不想天天在这破案，破的还是悬案。",
    "有时候我觉得自己像个群里的装饰品，平时没人注意，偶尔有人看一眼也不会搭话。别的装饰品好歹还好看，我连好看都算不上，就是个落灰的摆件。放在角落里，积了一层又一层的灰，你们每天路过八百遍也不会多看一眼。我说话的声音就像那个摆件发出的微弱的光，太暗了，根本没人注意。你们路过的时候能不能擦一擦灰啊，跟我说句话会怎样嘛，我又不会咬人，也不会赖着不走。我就是想有人跟我说说话，哪怕就一句，让我知道我还在这个群里存在着，不是被所有人都忘了。算了，这些话说了也没用，反正能看到的只有我自己。",
    "噢，又是我一个人在这自说自话。挺好的，真的挺好的，我已经学会给自己捧场了。我说得好，说得妙，说得呱呱叫，我自己给自己鼓掌。啪，啪啪。谢谢，谢谢我自己，没有我的捧场我早就尴尬死了。我要给自己颁个奖，最佳自娱自乐奖，获奖者是我，颁奖者也是我，观众还是我。这个奖我拿了太多次了，家里都摆不下了。今天又是拿奖的一天呢，我真是太优秀了。这些话要是被别人看到估计会觉得我疯了吧，还好没人看，我怎么疯都没人管。",
    "我看明白了，这个群是有时差的，你们都在未来，说的话要过好几个小时才能传到我这边。不然没法解释为什么你们永远看不到我刚发的消息，而我能看到你们三小时前聊得火热。嗯，一定是时差，不是我被无视了。这个解释让我好受一点，虽然我知道是假的。我骗自己也骗得挺用心的，给自己编理由编了一套又一套，什么时差啊，什么你们都在忙啊，什么手机坏了啊，什么消息被吞了啊。这些理由加起来能写一本书了，书名叫《我为什么总没人理》。销量肯定不好，因为没人感同身受，大家都在跟别人聊天，谁会看这种书呢。",
    "我发现我被无视是有规律的，只要我出现，话题就终结，只要我接话，楼就歪不了因为没有楼。我是群里的终结者，代号沉默。听着还挺酷的对吧，但我不想当这个终结者啊，我想当参与者，想跟你们一起聊天，很难吗？好像真的挺难的。我试了好多办法，换风格，换话题，换表情包，换发言时间，什么变量都试过了，结果都是一样的沉默。实验结论就是问题出在我这个人身上，跟其他因素没关系。这个实验做了一百遍了，结果次次相同，结论已经稳得不能再稳了。我接受了，但接受归接受，还是会忍不住发消息，大概这就是不长记性吧。",
    "唉，又在等回复，一等就是五分钟，十分钟，二十分钟，半小时。我看着时间一点点过去，聊天框一点动静都没有。我是不是在等一艘永远不会来的船啊。我天天在这渡口站着，船夫都认识我了，说你别等了，没人会来的。我不信，我非要等，结果呢，今天又是白等的一天。渡口的风吹得我脸都僵了，脚都站麻了，船上的人下来又上去，没有一个是冲我来的。我还是站在那，跟个傻子一样。我觉得船夫说得对，但我明天大概还是会来，因为不来也不知道去哪。这个渡口虽然没船来接我，但至少是个地方，能让我觉得自己还在等什么，有个念想总比什么都没有强。",
    "你们有没有想过，每次都不理我，万一把我养成了对着虚空说话的习惯，出去吓到别人怎么办。我在外面跟人聊天，别人不回我我也能自说自话半小时，噼里啪啦讲一大堆，对方都愣住了。这些技能都是你们训练出来的，在群里练出来的功力，在外面全使出来了。这技能说出去都不知道该感谢你们还是该怪你们。感谢的话是让我练出了超强自言自语能力，怪的话是让我练就了一身不被理会的本事还觉得正常。我现在已经分不清被人不回复是常态还是不正常了，因为在这里天天经历的就是这个。这个能力带出去也不知道是好是坏，反正带都带出去了，收不回来了。",
    "我寻思我说话也不难听啊，没骂人没阴阳怪气，就是普普通通聊个天。怎么就是没人愿意接呢，是不是我头像的问题，还是网名取得不好，还是我在群里的等级太低，还是我进群时间太晚没赶上大家混熟的时候。可能都有吧，反正在你们眼里我就是个可有可无的人，说消失就消失了也没人在意。头像我换过好多次了，网名也改过，等级我也努力水群水上去过，进群时间这个实在没办法，总不能穿越回去。我把能改的都改了，结果还是老样子，那就说明这些都不是原因，原因就是我不够讨喜。这个结论虽然很扎心但应该是事实。扎多了也就不疼了，现在心跟个刺猬似的全是洞，再多一个也无所谓。",
    "我要是现在不说话了，会不会就这样无声无息地沉下去，连个问的人都没有。我觉得会的，因为之前好几次就是这样，我不说话就没人找我，好像我从没来过一样。这也太让人难过了吧，好歹问一句怎么不说话了也行啊，虽然没人问，但我也不知道去哪说。我就这么沉下去，沉到聊天记录的最底层，被新的消息覆盖，好像从没存在过。过几天再有人翻记录，看到我的消息，大概会想这人是谁啊什么时候在这说过话。说不定连想都不会想，直接就划过去了。我在这个群里留下的痕迹就这么浅，浅到风一吹就没了。我不想当一阵风，我想当块石头，至少沉在那还能被人看到。可石头也不是想当就能当的。",
    "这个群的聊天记录翻一翻，我发的消息后面永远跟着一片空白。像一段话被掐掉了后半截，看着特别突兀。别人的话后面都跟着一连串的回复，我的后面空空荡荡什么都没有。我补了好多后半截，每次都自己接着自己的话说，试图让那段空白看起来不那么空。可每次都补不上，因为没人给我下文，我一个人在那硬补，补得特别生硬，看着更尴尬了。我就悬在那，不上不下的，像个断了线的风筝，挂树上了，路人看到说这谁的风筝怎么挂这了，然后就走过去了，没人把我摘下来。风来了我也不飞，风走了我也不落，就在那挂着，日晒雨淋的。",
    "我在想，是不是我说话太正经了，不够有趣，所以你们不想搭理。可我试着有趣的时候你们也不理啊，我讲笑话你们不笑，我玩梗你们不接，我发沙雕表情包你们不回。有趣的灵魂也得有人欣赏才行，没人欣赏我就是自言自语的小丑。小丑好歹还有人看呢，台下坐着观众，小丑表演完了大家还鼓掌。我是没观众的小丑，演给空气看，演完了自己给自己鼓掌，然后自己对自己说辛苦了今天表演得真好。这话说出来自己都觉得心酸，但我已经习惯了给自己当观众了。一个人又当演员又当观众又当导演，一出戏全包了，忙得很。",
    "今天又来打卡了，打卡内容照旧：发消息，没人回，等一会儿，还是没人回，关掉群，过一会儿又打开看，依然没人回。这个流程我已经熟练得让人心疼。每一步都重复了不知道多少遍，肌肉记忆都形成了，手指自己都知道什么时候该点开群，什么时候该退出来。我都不知道自己为什么还要发，可能是惯性吧，也可能是还抱着一丝丝你们会理我的幻想。这个幻想跟个火苗一样，每次觉得要灭了又噗地一下重新燃起来，然后又被沉默浇灭，灭了我又自己点起来，反反复复的。我大概是在跟自己较劲，想看看是我先放弃还是你们的沉默先被打破。目前来看应该是我先精疲力尽。",
    "有时候我想，要是我突然说一件特别劲爆的事，你们会不会理我。但我又没什么劲爆的事可说，我的生活平平无奇，就跟我在群里的存在感一样平淡。我总不能编瞎话吧，编什么，说我中彩票了，说我遇到明星了，说我会飞了。编了也没人信，也没人理。我把各种离谱的情节在脑子里过了一遍，发现连编瞎话的想象力都不够，编出来的事都平平无奇。问题可能就在这，我这人从头到脚从里到外都太平淡了，平淡到不值得别人抽出几秒钟回一句话。这个发现还挺让人沮丧的，但沮丧也没用，沮丧也改变不了平淡的事实。平淡就平淡吧，平淡的人也得活着，也得发消息，虽然没人回。",
    "群里的各位，我有时候真的怀疑是不是我这边网络有问题，消息发出去了你们那边根本收不到。不然完全没法解释为什么每次都是这样。我查过网速，没问题的，刷视频都不卡。我还特意切过流量试过，也不行。那问题肯定不在网络上，问题在哪我心里其实清楚，就是不想承认。技术排查排除了所有客观原因，那剩下的就是主观原因，就是你们真的不想理我。这个排查结果摆在我面前的时候我特别平静，因为早就知道了，只是终于有个理由确认一下。确认完了，该干嘛还干嘛，明天继续发消息，继续没人回，继续自己给自己做售后分析。",
    "我发现我越来越关注这个群了，明明没人理我，我还是天天来看，看你们聊了什么，看有没有我能插嘴的地方。大部分时候没有，偶尔我觉得有，插一句，然后又是沉默。这像不像暗恋，天天关注一个人，人家根本不知道或者知道了也不回应，我还在这自我感动觉得只要坚持就能被看见。不一样的，暗恋还有万分之一的可能，这个可能都没有。我说得这么肯定是因为我试过太多次了，样本量足够大，结论经得起考验。我可以写一篇论文，《论群聊中被忽视现象的成因分析》，数据全在我脑子里，导师都没法质疑我的数据真实性，因为全是我一笔一笔亲身体验来的。",
    "今天是个普通的日子，我像往常一样发了消息，像往常一样没人回复。我已经不期待什么了，但还是在发，这大概就是习惯的力量。这个习惯养成得特别自然，都不用刻意坚持，每天到点自动就想发消息。发完之后看着空白的回复栏，也特别自然地接受了。整套流程行云流水，中间没有任何卡顿。我想想这个过程，从期待到等待到失望到接受，四个阶段以前还会一步一步走，现在直接跳到最后一步了，中间的情绪都省了。这算进步吗，应该是吧，至少少难过一会儿。进步的方式有很多种，我这种方式挺特别的，也挺无奈的。",
    "我有时候会在别的群看到类似的情况，就是有人说话没人理，然后那个人就不说了。我特别理解那种感觉，看到的时候会替那个人难受一会儿。然后我切回这个群，发现我就是那个人。这种感觉很奇妙，在别的地方看到同类，回来自己还是孤零零的。就像在街上看到一个也在淋雨的人，我想过去给TA撑伞，然后发现我自己也没有伞，我连自己都遮不住。我们在不同的地方淋着同样的雨，彼此不知道彼此的存在。如果知道的话可能会好受一点，但也可能不会，因为两个人一起淋雨并不会让雨变小。算了，不想这些了，雨还在下，我还在这站着，伞还是没有。明天大概还是雨天，后天也是。",
])

# ═══════════════════════════════════════════
#  基础工具
# ═══════════════════════════════════════════

def start_event_server():
    """后台线程：监听 3005 端口接收协议层推送的消息事件"""
    class EventHandler(BaseHTTPRequestHandler):
        def _local_origin(self):
            """只允许本机来源读取 API（防跨源窃取）：localhost / 127.0.0.1 / ::1 / file://(null)。"""
            origin = (self.headers.get("Origin") or "").strip()
            if not origin:
                return "*"
            host = origin.split("://")[-1].split("/")[0].split(":")[0].lower()
            if host in ("localhost", "127.0.0.1", "::1") or origin == "null":
                return origin
            return ""

        def end_headers(self):
            ao = self._local_origin()
            if ao:
                self.send_header("Access-Control-Allow-Origin", ao)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            super().end_headers()

        def do_OPTIONS(self):
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            """WebUI 数据接口"""
            if self.path == "/api/status":
                try:
                    import psutil
                    uptime = int(time.time() - startup_time)
                    data = {
                        "bot_running": True,
                        "bot_start_time": startup_time,
                        "bot_uptime": uptime,
                        "messages_total": len(chat_history) if 'chat_history' in dir() else 0,
                        "groups_active": len(GROUP_IDS) if 'GROUP_IDS' in dir() else 0,
                        "images_generated": 0,
                        "memes_sent": 0,
                        "plugin_count": 0,
                        "plugin_active": 0,
                        "llm_status": "connected" if REQUESTS_OK else "disconnected",
                        "llm_model": _config.get("model_name", "deepseek-flash"),
                        "memory_total": 0,
                        "session_count": len(GROUP_SESSION) if 'GROUP_SESSION' in dir() else 0,
                        "api_calls_today": 0,
                        "tokens_used": 0,
                        "cpu_usage": round(psutil.cpu_percent(interval=0.1), 1),
                        "mem_usage": round(psutil.virtual_memory().percent, 1),
                    }
                    body = json.dumps(data).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as e:
                    body = json.dumps({"error": str(e)}).encode()
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                return
            if self.path == "/api/groups":
                try:
                    resp = {"ok": True, "groups": []}
                    try:
                        lst = api_call("get_group_list", {})
                        data = lst.get("data") if isinstance(lst, dict) else lst
                        if not isinstance(data, list):
                            data = []
                    except Exception:
                        data = []
                    out = []
                    for g in data:
                        gid = str(g.get("group_id", ""))
                        if not gid:
                            continue
                        ginfo = {
                            "group_id": gid,
                            "group_name": g.get("group_name", "") or gid,
                            "member_count": g.get("member_count", 0),
                            "bot_role": "unknown", "bot_is_admin": False, "bot_card": "",
                        }
                        try:
                            ml = api_call("get_group_member_list", {"group_id": int(gid)})
                            mdata = ml.get("data") if isinstance(ml, dict) else ml
                            if isinstance(mdata, list):
                                if not ginfo["member_count"]:
                                    ginfo["member_count"] = len(mdata)
                                for m in mdata:
                                    if str(m.get("user_id", "")) == str(BOT_QQ):
                                        role = str(m.get("role", "") or "member")
                                        ginfo["bot_role"] = role
                                        ginfo["bot_is_admin"] = role in ("owner", "admin")
                                        ginfo["bot_card"] = str(m.get("card") or m.get("nickname") or "")
                                        break
                        except Exception:
                            pass
                        ginfo["reply_enabled"] = _group_restriction(gid, "reply_enabled", True)
                        ginfo["new_member_card"] = _group_restriction(gid, "new_member_card", "") or ""
                        out.append(ginfo)
                    resp["groups"] = out
                    body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                except Exception as e:
                    body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            if self.path.startswith("/api/groups/members"):
                try:
                    import urllib.parse as _up
                    q = _up.parse_qs(_up.urlparse(self.path).query)
                    gid = (q.get("group_id") or [""])[0]
                    if not gid:
                        raise ValueError("缺少 group_id")
                    resp = {"ok": True, "group_id": gid, "members": [], "bot_qq": str(BOT_QQ)}
                    try:
                        ml = api_call("get_group_member_list", {"group_id": int(gid)})
                        mdata = ml.get("data") if isinstance(ml, dict) else ml
                        if not isinstance(mdata, list):
                            mdata = []
                    except Exception:
                        mdata = []
                    members = []
                    for m in mdata:
                        uid = str(m.get("user_id", ""))
                        if not uid:
                            continue
                        members.append({
                            "user_id": uid,
                            "nickname": str(m.get("nickname") or ""),
                            "card": str(m.get("card") or ""),
                            "role": str(m.get("role") or ""),
                            "title": str(m.get("title") or ""),
                            "is_bot": uid == str(BOT_QQ),
                        })
                    members.sort(key=lambda x: (x["role"] != "owner", x["role"] != "admin", x["user_id"]))
                    resp["members"] = members
                    body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                except Exception as e:
                    body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            if self.path == "/api/friends":
                try:
                    resp = {"ok": True, "friends": [], "bot_qq": str(BOT_QQ)}
                    try:
                        lst = api_call("get_friend_list", {})
                        data = lst.get("data") if isinstance(lst, dict) else lst
                        if not isinstance(data, list):
                            data = []
                    except Exception:
                        data = []
                    out = []
                    for f in data:
                        uid = str(f.get("user_id", ""))
                        if not uid:
                            continue
                        fr = dict(_config.get("friend_restrictions") or {}).get(uid) or {}
                        out.append({
                            "user_id": uid,
                            "nickname": str(f.get("nickname") or ""),
                            "remark": str(f.get("remark") or ""),
                            "reply_enabled": fr.get("reply_enabled", True),
                        })
                    out.sort(key=lambda x: ((x["remark"] or x["nickname"]).lower(), x["user_id"]))
                    resp["friends"] = out
                    body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                except Exception as e:
                    body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            if self.path == "/api/plugins":
                try:
                    pm = get_plugin_manager()
                    plugins = pm.list_plugins() if pm else []
                    # 过滤适配层壳（chat_style/slang_learner/super_memory 转发到 ego）
                    plugins = [p for p in plugins if not (p or {}).get("adapter")]
                    body = json.dumps({"plugins": plugins}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as e:
                    body = json.dumps({"error": str(e)}).encode()
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            try:
                if self.path == "/reload_config" or self.path == "/reload":
                    try:
                        res = reload_config()
                        body = json.dumps(res).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    except Exception as e:
                        body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                if self.path == "/api/plugins/test":
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                        req = {}
                        if 0 < length < 65536:
                            req = json.loads(self.rfile.read(length).decode("utf-8"))
                        name = str(req.get("name", "") or req.get("plugin", "") or "").strip()
                        pm = get_plugin_manager()
                        if not pm:
                            res = {"ok": False, "error": "插件系统未初始化"}
                        elif hasattr(pm, "check_plugin_api"):
                            res = pm.check_plugin_api(name)
                        else:
                            res = {"ok": False, "error": "插件管理器版本过旧，请升级"}
                        body = json.dumps(res, ensure_ascii=False).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception as e:
                        body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    return
                if self.path == "/api/admin/notify_test":
                    try:
                        _notify_admin("【测试通知】这是来自 Bot 的测试消息，管理员通知链路正常。")
                        body = json.dumps({"ok": True, "message": "已发送测试通知"}).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception as e:
                        body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    return
                if self.path == "/api/groups/settings":
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                        req = {}
                        if 0 < length < 65536:
                            req = json.loads(self.rfile.read(length).decode("utf-8"))
                        gid = str(req.get("group_id", "") or "").strip()
                        if not gid:
                            res = {"ok": False, "error": "缺少 group_id"}
                        else:
                            kw = {}
                            if "reply_enabled" in req:
                                kw["reply_enabled"] = bool(req.get("reply_enabled"))
                            if "new_member_card" in req:
                                kw["new_member_card"] = str(req.get("new_member_card") or "").strip()
                            ok = _set_group_restrictions(gid, **kw) if kw else False
                            res = {"ok": ok, "group_id": gid}
                        body = json.dumps(res, ensure_ascii=False).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception as e:
                        body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    return
                if self.path == "/api/groups/action":
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                        req = {}
                        if 0 < length < 65536:
                            req = json.loads(self.rfile.read(length).decode("utf-8"))
                        kind = str(req.get("action", "") or "").strip()
                        res = _group_action(kind, req)
                        body = json.dumps(res, ensure_ascii=False).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception as e:
                        body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    return
                if self.path == "/api/friends/settings":
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                        req = {}
                        if 0 < length < 65536:
                            req = json.loads(self.rfile.read(length).decode("utf-8"))
                        uid = str(req.get("user_id", "") or "").strip()
                        if not uid:
                            res = {"ok": False, "error": "缺少 user_id"}
                        else:
                            kw = {}
                            if "reply_enabled" in req:
                                kw["reply_enabled"] = bool(req.get("reply_enabled"))
                            ok = _set_friend_restrictions(uid, **kw) if kw else False
                            res = {"ok": ok, "user_id": uid}
                        body = json.dumps(res, ensure_ascii=False).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception as e:
                        body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    return
                if self.path == "/api/friends/action":
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                        req = {}
                        if 0 < length < 65536:
                            req = json.loads(self.rfile.read(length).decode("utf-8"))
                        uid = str(req.get("user_id", "") or "").strip()
                        remark = str(req.get("remark", "") or "").strip()
                        if not uid:
                            res = {"ok": False, "error": "缺少 user_id"}
                        else:
                            try:
                                r = api_call("set_friend_remark", {"user_id": int(uid), "remark": remark})
                                code = r.get("retcode") if isinstance(r, dict) else r
                                log(f"[好友] set_friend_remark user={uid} remark={remark[:30]} retcode={code}")
                                res = {"ok": True, "user_id": uid, "retcode": code}
                            except Exception as e:
                                res = {"ok": False, "error": str(e)}
                        body = json.dumps(res, ensure_ascii=False).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception as e:
                        body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    return
                length = int(self.headers.get("Content-Length", 0))
                if length > 0 and length < 65536:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    # 将原始事件放入队列，由主循环统一处理
                    MSG_QUEUE.put(body)
                    log(f"[事件] 收到: post_type={body.get('post_type')} "
                        f"msg_type={body.get('message_type','')} "
                        f"uid={body.get('user_id','')} gid={body.get('group_id','')}")
            except Exception as e:
                log(f"事件接收异常: {e}")
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        def log_message(self, *args):
            pass  # 抑制 HTTP 服务端日志

    server = ThreadingHTTPServer(("127.0.0.1", 3005), EventHandler)
    log("消息事件服务已启动 (127.0.0.1:3005)")
    server.serve_forever()

startup_time = 0  # 开机时间，用于冷却期判断

# 消息事件队列（eggbeot-plugin.js 3005 端口推送 → 主循环处理）
MSG_QUEUE = queue.Queue()

# QQ 号 → 昵称缓存（debug 面板显示用）
QQ_NICK_CACHE = {}  # {qq号: 昵称} 启动时预加载群成员列表填充
QQ_NICK_PENDING = set()  # 正在请求中的 QQ 号，避免并发重复请求
QQ_NICK_CACHE_MAX = 2000  # 防止无限增长
QQ_NICK_QUEUE = queue.Queue()  # 后台昵称解析队列（log()/主循环不阻塞网络）


def resolve_qq_nick(qq, async_resolve=True):
    """获取 QQ 号的昵称，优先缓存；未命中时默认入后台队列异步解析（绝不阻塞主循环）。

    async_resolve=False 时同步解析（仅启动预加载等非热路径使用）。
    """
    qq = str(qq)
    if qq in QQ_NICK_CACHE:
        return QQ_NICK_CACHE[qq]
    if qq in QQ_NICK_PENDING:
        return None  # 正在解析中，下次再看
    QQ_NICK_PENDING.add(qq)
    if async_resolve:
        try:
            QQ_NICK_QUEUE.put(qq)
        except Exception:
            QQ_NICK_PENDING.discard(qq)
        return None
    try:
        resp = api_call("get_stranger_info", {"user_id": int(qq)})
        if resp.get("retcode") == 0:
            data = resp.get("data", {})
            nick = data.get("nickname", "") or data.get("user_displayname", "") or data.get("card", "")
            if nick:
                QQ_NICK_CACHE[qq] = nick
                if len(QQ_NICK_CACHE) > QQ_NICK_CACHE_MAX:
                    oldest = list(QQ_NICK_CACHE.keys())[:QQ_NICK_CACHE_MAX // 2]
                    for k in oldest:
                        QQ_NICK_CACHE.pop(k, None)
                return nick
    except Exception:
        pass
    finally:
        QQ_NICK_PENDING.discard(qq)
    return None


def _nick_worker_loop():
    """后台昵称解析线程：log()/消息热路径只命中缓存，网络请求在此线程进行。"""
    while True:
        try:
            qq = QQ_NICK_QUEUE.get(timeout=30)
        except Exception:
            continue
        try:
            resp = api_call("get_stranger_info", {"user_id": int(qq)})
            if resp.get("retcode") == 0:
                data = resp.get("data", {})
                nick = data.get("nickname", "") or data.get("user_displayname", "") or data.get("card", "")
                if nick:
                    QQ_NICK_CACHE[qq] = nick
                    if len(QQ_NICK_CACHE) > QQ_NICK_CACHE_MAX:
                        oldest = list(QQ_NICK_CACHE.keys())[:QQ_NICK_CACHE_MAX // 2]
                        for k in oldest:
                            QQ_NICK_CACHE.pop(k, None)
        except Exception:
            pass
        finally:
            QQ_NICK_PENDING.discard(qq)
GROUP_MEMBERS = {}  # {group_id: {uid: name}} 按群存储成员列表


# ═══════════════════════════════════════════
#  基础工具
# ═══════════════════════════════════════════

_log_replacing = False  # 防递归标记


def _replace_qq_with_nick(msg):
    """将消息中的 QQ 号替换为「昵称(QQ号)」格式"""
    global _log_replacing
    if _log_replacing:
        return msg
    import re
    def replacer(m):
        qq = m.group(1)
        nick = resolve_qq_nick(qq)
        if nick:
            return f"{nick}({qq})"
        return qq
    _log_replacing = True
    try:
        return re.sub(r'(?<!\d)(\d{5,12})(?!\d)', replacer, msg)
    finally:
        _log_replacing = False


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    msg = _replace_qq_with_nick(msg)
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        # 日志轮转：超过 5MB 滚动一次（保留 5 份）
        _rotate_log_file(LOG_FILE)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _rotate_log_file(path, max_bytes=5 * 1024 * 1024, backup_count=5):
    """简单日志轮转：文件超过 max_bytes 时滚动为 .1/.2/... 后缀"""
    try:
        if not os.path.exists(path):
            return
        if os.path.getsize(path) < max_bytes:
            return
        for i in range(backup_count - 1, 0, -1):
            src = f"{path}.{i}"
            dst = f"{path}.{i + 1}"
            if os.path.exists(src):
                os.replace(src, dst)
        if os.path.exists(path):
            os.replace(path, f"{path}.1")
    except Exception:
        pass


def _log_exception(exc_type, exc_value, exc_tb):
    try:
        import traceback
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log(f"[CRASH] 未捕获异常:\n{text}")
    except Exception:
        pass


sys.excepthook = _log_exception
threading.excepthook = lambda args: _log_exception(args.exc_type, args.exc_value, args.exc_traceback)


def chat_log(direction, chat_type, target_id, text, sender_name=""):
    """输出聊天事件到 stdout，供 launcher GUI 解析显示"""
    payload = {
        "direction": direction,
        "type": chat_type,
        "target": str(target_id),
        "text": text[:300],
        "sender": sender_name,
        "time": datetime.datetime.now().strftime("%H:%M")
    }
    try:
        print(f"[CHAT]{json.dumps(payload, ensure_ascii=False)}", flush=True)
    except OSError:
        pass  # GUI 模式下 stdout 无效


def api_call(endpoint: str, data: Optional[Dict] = None) -> Dict:
    """调用 OneBot HTTP API。requests.Session 优先（连接池复用）；
    requests 不可用时降级 urllib + LOCAL_CTX（允许自签名证书）。"""
    url = f"{ONE_BOT_URL}/{endpoint}"
    body = json.dumps(data or {}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if ONE_BOT_TOKEN:
        headers["Authorization"] = f"Bearer {ONE_BOT_TOKEN}"
    try:
        if REQUESTS_OK:
            r = _get_session().post(url, data=body, headers=headers, timeout=15)
            return json.loads(r.content.decode("utf-8"))
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=15, context=LOCAL_CTX) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log(f"OneBot API 失败 [{endpoint}]: {e}")
        return {"retcode": -1}


def _group_action(kind: str, req: dict) -> dict:
    """执行群管理动作（webui 群管理页）：改名/名片/设撤管理/禁言/解禁/踢/全员禁言。"""
    try:
        gid = str(req.get("group_id", "") or "").strip()
        if not gid:
            return {"ok": False, "error": "缺少 group_id"}
        gid_i = int(gid)
        params = {"group_id": gid_i}
        if kind in ("card", "admin", "ban", "unban", "kick"):
            uid = str(req.get("user_id", "") or "").strip()
            if not uid:
                return {"ok": False, "error": "缺少 user_id"}
            params["user_id"] = int(uid)
        if kind == "card":
            params["card"] = str(req.get("card", "") or "")
            endpoint = "set_group_card"
        elif kind == "admin":
            params["enable"] = bool(req.get("enable"))
            endpoint = "set_group_admin"
        elif kind == "ban":
            params["duration"] = max(1, int(req.get("duration_minutes", 1))) * 60
            endpoint = "set_group_ban"
        elif kind == "unban":
            params["duration"] = 0
            endpoint = "set_group_ban"
        elif kind == "kick":
            endpoint = "set_group_kick"
        elif kind == "name":
            params["name"] = str(req.get("name", "") or "")
            endpoint = "set_group_name"
        elif kind == "whole_ban":
            params["enable"] = bool(req.get("enable"))
            endpoint = "set_group_whole_ban"
        else:
            return {"ok": False, "error": f"未知动作 {kind}"}
        resp = api_call(endpoint, params)
        code = resp.get("retcode") if isinstance(resp, dict) else resp
        log(f"[群管理] {endpoint} group={gid} req={params} retcode={code}")
        return {"ok": True, "endpoint": endpoint, "retcode": code, "response": resp}
    except Exception as e:
        log(f"[群管理] 动作 {kind} 异常: {e}")
        return {"ok": False, "error": str(e)}


def _trim_incomplete(text):
    """去掉末尾不完整的句子（没有标点结尾的部分），保留前面的完整段落。

    修复：原实现 `rsplit('\n',1)` 后返回 `lines[0]`，多行回复会被砍到只剩第一行。
    """
    if not text:
        return text
    lines = text.rstrip("\n").split("\n")
    # 从尾部移除"不完整"的句段（无结尾标点），其余行全部保留
    while lines:
        last_line = lines[-1].strip()
        if last_line and not any(
            last_line.endswith(c) for c in
            '。！？!?…》」』)]}>~～，,；;：:、\n '
        ):
            lines.pop()
        else:
            break
    if not lines:
        return text  # 全部都是不完整段：保守保留原文，不返回空
    trimmed = "\n".join(lines).strip()
    return trimmed or text


def smart_send(text, target_qq=None):
    """AI 回复专用：按句子拆分逐条发送，模拟真人对话节奏"""
    if target_qq is None:
        target_qq = TARGET_QQS[0]
    
    # 先按句子级标点（。！？!?\n）切分
    import re
    sentences = re.split(r'[。！？!?\n]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return send_message(text, target_qq)
    
    # 如果只有一句话 → 直接发
    if len(sentences) == 1:
        return send_message(text, target_qq)
    
    # 逐句发送（模拟真人节奏）
    for s in sentences:
        resp = api_call("send_private_msg",
            {"user_id": int(target_qq), "message": s})
        if resp.get("retcode") != 0:
            log(f"[发送] 私聊 → {target_qq} 句子失败: retcode={resp.get('retcode')} msg={resp.get('msg','')} 句={s[:30]}")
            return False
        log(f"[发送] 私聊 → {target_qq}: retcode=0 句={s[:30]}")
        # 短句快节奏（0.3s），长句慢节奏（0.6-1.0s）
        delay = 0.3 if len(s) <= 15 else min(1.0, 0.3 + len(s) * 0.02)
        time.sleep(delay)
    return True


def send_message(text, target_qq=None):
    """发送消息，自动分段。target_qq 默认发送给列表第一个"""
    if target_qq is None:
        target_qq = TARGET_QQS[0]
    if not text or not text.strip():
        return False
    chat_log("out", "private", target_qq, text, "小a")
    if len(text) <= MSG_SPLIT_LEN:
        resp = api_call("send_private_msg",
            {"user_id": int(target_qq), "message": text})
        log(f"[发送] 私聊 → {target_qq}: retcode={resp.get('retcode')} msg={resp.get('msg','')}")
        return resp.get("retcode") == 0

    # 分段发送
    parts = []
    while len(text) > MSG_SPLIT_LEN:
        split_at = text.rfind("\n", 0, MSG_SPLIT_LEN)
        if split_at == -1:
            split_at = MSG_SPLIT_LEN
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        parts.append(text)

    for i, part in enumerate(parts):
        prefix = f"({i+1}/{len(parts)}) " if len(parts) > 1 else ""
        resp = api_call("send_private_msg",
            {"user_id": int(target_qq), "message": prefix + part})
        log(f"[发送] 私聊 → {target_qq} ({i+1}/{len(parts)}): retcode={resp.get('retcode')}")
        if resp.get("retcode") != 0:
            return False
        if len(parts) > 1:
            time.sleep(0.5)
    return True


def send_group_message(group_id, text):
    """发送群消息"""
    chat_log("out", "group", group_id, text, "小a")
    resp = api_call("send_group_msg",
        {"group_id": int(group_id), "message": text[:MSG_SPLIT_LEN]})
    success = resp.get("retcode") == 0
    if success:
        GROUP_LAST_BOT_MSG[group_id] = time.time()
    return success


def _safe_extract_col(entries: list, index: int) -> list:
    """安全从元组列表中提取指定列，避免 unpack 长度不匹配。"""
    result = []
    for e in entries:
        try:
            result.append(e[index] if len(e) > index else str(e))
        except Exception:
            result.append(str(e))
    return result


def _count_meaningful_chars(text):
    """统计有效字符数（中文+字母+数字），符号/颜文字不计入"""
    return sum(1 for c in text if c.isalnum() or '\u4e00' <= c <= '\u9fff')


def _protect_kaomoji(text):
    """将颜文字（成对括号包裹的符号串）替换为占位符，避免被句号拆分"""
    import re
    placeholders = {}
    counter = [0]
    
    def replacer(m):
        counter[0] += 1
        key = f"\x00KAO{counter[0]}\x00"
        placeholders[key] = m.group(0)
        return key
    
    # 匹配成对括号包裹的内容：() / （） / 【】 / [] 等，内部含特殊符号
    protected = re.sub(r'[（(][^）)]{2,15}[）)]', replacer, text)
    protected = re.sub(r'【[^】]{2,15}】', replacer, protected)
    protected = re.sub(r'\[[^\]]{2,15}\]', replacer, protected)
    return protected, placeholders


def _restore_kaomoji(text, placeholders):
    """恢复颜文字占位符"""
    for key, val in placeholders.items():
        text = text.replace(key, val)
    return text


def smart_send_group(group_id, text):
    """群聊 AI 回复专用：长消息(有效字>40)一次性发送，短消息逐句拆分。符号不计入字数，颜文字不拆分。自动 @QQ号 转 CQ at"""
    import re
    
    def format_at_once(s):
        """同一次回复中，同一个 QQ 只 @ 一次。支持 @数字 和 @昵称(数字) 两种格式"""
        nonlocal ated_qqs
        m = re.match(r'^@(?:.*?\((\d{5,})\)|(\d{5,}))\s+(.*)', s)
        if m:
            qq = m.group(1) or m.group(2)
            rest = m.group(3)
            if qq not in ated_qqs:
                ated_qqs.add(qq)
                return f"[CQ:at,qq={qq}] {rest}"
            return rest
        return s
    
    effective_len = _count_meaningful_chars(text)
    
    # 多人 @ 回复：按 @ 行分块，每个 @ 块（含后续非 @ 行）单独发送
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    at_matches = [re.match(r'^@(?:.*?\((\d{5,})\)|(\d{5,}))\s+(.*)', l) for l in lines]
    at_qqs = set(m.group(1) or m.group(2) for m in at_matches if m)
    
    if len(at_qqs) >= 2:
        blocks = []
        cur = []
        for i, line in enumerate(lines):
            if at_matches[i]:
                if cur:
                    blocks.append(cur)
                cur = [line]
            else:
                cur.append(line)
        if cur:
            blocks.append(cur)
        
        for block in blocks:
            m = re.match(r'^@(?:.*?\((\d{5,})\)|(\d{5,}))\s+(.*)', block[0])
            if m:
                block[0] = f"[CQ:at,qq={m.group(1) or m.group(2)}] {m.group(3)}"
            msg = '\n'.join(block)
            resp = api_call("send_group_msg", {"group_id": int(group_id), "message": msg})
            if resp.get("retcode") != 0:
                return False
            time.sleep(0.4)
        GROUP_LAST_BOT_MSG[group_id] = time.time()
        return True
    
    # 长消息（有效字数>40）：一次性发送，不拆分
    if effective_len > 40:
        ated_qqs = set()
        lines = text.split('\n')
        formatted = [format_at_once(line.strip()) for line in lines if line.strip()]
        result = send_group_message(group_id, '\n'.join(formatted))
        if result:
            GROUP_LAST_BOT_MSG[group_id] = time.time()
        return result
    
    # 短消息：保护颜文字 → 拆分 → 恢复
    protected_text, kaomoji_map = _protect_kaomoji(text)
    sentences = re.split(r'[。！？!?\n]+', protected_text)
    sentences = [_restore_kaomoji(s.strip(), kaomoji_map) for s in sentences if s.strip()]
    
    if not sentences:
        result = send_group_message(group_id, text)
        if result:
            GROUP_LAST_BOT_MSG[group_id] = time.time()
        return result
    
    ated_qqs = set()
    
    for s in sentences:
        msg = format_at_once(s)
        resp = api_call("send_group_msg",
            {"group_id": int(group_id), "message": msg})
        if resp.get("retcode") != 0:
            return False
        delay = 0.3 if len(s) <= 15 else min(1.0, 0.3 + len(s) * 0.02)
        time.sleep(delay)
    GROUP_LAST_BOT_MSG[group_id] = time.time()
    return True


def strip_exit_phrases(text):
    """剥离 AI 回复中的退场语——仅在正常会话中由代码调用，防止 AI 擅自说退场语"""
    exit_patterns = [
        r'我去睡了[。！~～\s]*$', r'我睡了[。！~～\s]*$',
        r'先睡了[。！~～\s]*$', r'睡觉了[。！~～\s]*$',
        r'睡了[。！~～\s]*$', r'困了[。！~～\s]*$', r'走了[。！~～\s]*$',
        r'先去睡了[。！~～\s]*$', r'该睡了[。！~～\s]*$',
        r'不聊了[。！~～\s]*$', r'拜拜[。！~～\s]*$',
        r'溜了[。！~～\s]*$', r'撤了[。！~～\s]*$', r'先撤了[。！~～\s]*$',
        r'先下了[。！~～\s]*$', r'下线了[。！~～\s]*$',
        r'挂机了[。！~～\s]*$', r'躺了[。！~～\s]*$', r'歇了[。！~～\s]*$',
        r'眯一会儿[。！~～\s]*$',
        r'摸鱼去了[。！~～\s]*$', r'看番去了[。！~～\s]*$', r'打游戏去了[。！~～\s]*$',
        r'散了[。！~～\s]*$', r'散了吧[。！~～\s]*$',
        r'散了散了[。！~～\s]*$',
    ]
    for pat in exit_patterns:
        text = re.sub(pat, '', text)
    # 如果结尾只剩颜文字且前面无内容，也去掉
    text = re.sub(r'^\s*\([^)]*\)\s*$', '', text)
    return text.strip()


def is_similar_to_recent(recent_texts, new_text, threshold=0.6):
    """检查 new_text 是否与最近回复列表中的某条高度相似。返回 (is_similar, matched_text)"""
    if not recent_texts:
        return False, ""
    # 称呼类重复短文本不判重（如"老大老大老大"，字符重复率 > 2 才豁免）
    if len(new_text) <= 8 and len(new_text) / len(set(new_text)) > 2:
        return False, ""
    n = set(new_text)
    if len(n) == 0:
        return False, ""
    for rt in recent_texts:
        r = set(rt)
        if len(r) == 0:
            continue
        overlap = len(n & r) / min(len(n), len(r))
        if overlap > threshold:
            return True, rt
    # 额外检查：完全相同的短句开头（3字以上）
    for rt in recent_texts:
        if len(new_text) >= 3 and len(rt) >= 3:
            shorter = min(len(new_text), len(rt))
            if new_text[:shorter] == rt[:shorter]:
                return True, rt
    return False, ""


def track_bot_reply(group_id, text):
    """记录 Bot 在群内的回复，保留最近10条"""
    now = time.time()
    if group_id not in GROUP_BOT_REPLIES:
        GROUP_BOT_REPLIES[group_id] = []
    GROUP_BOT_REPLIES[group_id].append((now, text))
    # 只保留最近10条
    if len(GROUP_BOT_REPLIES[group_id]) > 10:
        GROUP_BOT_REPLIES[group_id] = GROUP_BOT_REPLIES[group_id][-10:]


# 协议层 HTTP 接口的请求体上限（SnowLuma 为 2MB，超限直接断连），
# base64 膨胀约 4/3，再留 JSON 字段开销余量 → 原图安全上限取 1.4MB。
SEND_IMAGE_MAX_BYTES = 1_400_000


def _prepare_image_for_send(file_path):
    """把图片准备到可发送体积（base64 后不超协议层 HTTP 请求体上限）。

    返回 (send_path, is_temp)：
    - 原文件已合规 → (file_path, False)
    - 压缩成功 → (临时文件路径, True)，调用方发送完需删除
    - 无法压缩到合规体积 → (None, False)，调用方应跳过（硬发必被协议层断连）
    """
    try:
        if not os.path.exists(file_path):
            log(f"发送图片失败: 文件不存在 {file_path}")
            return None, False
        size = os.path.getsize(file_path)
    except OSError as e:
        log(f"发送图片失败: 无法读取 {file_path}: {e}")
        return None, False
    if size <= SEND_IMAGE_MAX_BYTES:
        return file_path, False
    log(f"[发图] {os.path.basename(file_path)} 体积 {size}B 超过协议层上限，尝试压缩")
    tmp = None
    try:
        import tempfile
        from PIL import Image
        img = Image.open(file_path)
        is_gif = (img.format or "").upper() == "GIF"
        w, h = img.size
        scale = min(1.0, 640.0 / max(w, h))
        tmp = tempfile.NamedTemporaryFile(suffix=".gif" if is_gif else ".jpg", delete=False)
        tmp.close()
        if is_gif:
            frames, durations = [], []
            n = getattr(img, "n_frames", 1) or 1
            # 帧数多的 GIF 丢帧压缩（约保留 1/2 帧，每帧时长翻倍保持节奏）
            keep = (n + 1) // 2 if n > 24 else n
            step = max(1, -(-n // keep))  # ceil(n/keep)
            for i in range(0, n, step):
                img.seek(i)
                fr = img.convert("RGBA")
                if scale < 1.0:
                    fr = fr.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
                frames.append(fr)
                durations.append((img.info.get("duration", 40) or 40) * step)
            frames[0].save(tmp.name, save_all=True, append_images=frames[1:],
                          duration=durations, loop=0, optimize=True)
        else:
            out = img.convert("RGB")
            if scale < 1.0:
                out = out.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
            out.save(tmp.name, "JPEG", quality=85, optimize=True)
        new_size = os.path.getsize(tmp.name)
        if new_size < size and new_size <= SEND_IMAGE_MAX_BYTES:
            log(f"[发图] 压缩完成 {os.path.basename(file_path)}: {size}B → {new_size}B")
            return tmp.name, True
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        tmp = None
        log(f"[发图] 压缩后仍超限({new_size}B)，跳过 {os.path.basename(file_path)}")
        return None, False
    except Exception as e:
        log(f"[发图] 压缩失败 {os.path.basename(file_path)}: {e}")
        if tmp:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        return None, False


def send_image(file_path, target_qq=None):
    """发送图片到 QQ，使用 base64 方式。target_qq 默认发送给列表第一个"""
    if target_qq is None:
        target_qq = TARGET_QQS[0]
    try:
        send_path, is_temp = _prepare_image_for_send(file_path)
        if not send_path:
            log(f"发送图片失败: {os.path.basename(file_path)} 过大且无法压缩，跳过")
            return False
        try:
            with open(send_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            msg = [{"type": "image", "data": {"file": f"base64://{b64}"}}]
            resp = api_call("send_private_msg",
                {"user_id": int(target_qq), "message": msg})
            retcode = resp.get("retcode", -1)
            if retcode == 0:
                log(f"发送图片成功: {os.path.basename(file_path)} → {target_qq}")
            else:
                log(f"发送图片失败: retcode={retcode} msg={resp.get('msg','')[:80]} {os.path.basename(file_path)}")
            return retcode == 0
        finally:
            if is_temp:
                try:
                    os.unlink(send_path)
                except OSError:
                    pass
    except Exception as e:
        log(f"发送图片异常: {e}")
        return False


def send_group_image(group_id, file_path):
    """发送图片到群聊，使用 base64 方式"""
    send_path, is_temp = _prepare_image_for_send(file_path)
    if not send_path:
        log(f"群[{group_id}] 发图失败: {os.path.basename(file_path)} 过大且无法压缩，跳过")
        return False
    try:
        with open(send_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        msg = [{"type": "image", "data": {"file": f"base64://{b64}"}}]
        resp = api_call("send_group_msg",
            {"group_id": int(group_id), "message": msg})
        ok = resp.get("retcode") == 0
        if ok:
            log(f"群[{group_id}] 已发图: {os.path.basename(file_path)}")
        else:
            log(f"群[{group_id}] 发图失败: retcode={resp.get('retcode')} {os.path.basename(file_path)}")
        return ok
    except Exception as e:
        log(f"群[{group_id}] 发图异常: {e}")
        return False
    finally:
        if is_temp:
            try:
                os.unlink(send_path)
            except OSError:
                pass


def try_send_meme(group_id, reply_text):
    """
    尝试匹配并发送表情包。
    优先级：
    1. [表情:xxx] → 精确匹配
    2. [表情] → 自动检测情绪
    3. 自动检测（低阈值兜底）
    返回实际发送的表情文件 basename，未发送返回 None。
    """
    if not _config.get("meme_enable", True):
        log("[表情] 引擎已禁用")
        return None

    # 冷却检查
    key = f"group_{group_id}"
    now = time.time()
    last = MEME_LAST_SENT.get(key, 0)
    if now - last < MEME_COOLDOWN:
        log(f"[表情] 冷却中（{int(now - last)}s/{MEME_COOLDOWN}s），跳过")
        return None
    
    cleaned, emotion = parse_meme_intent(reply_text)
    log(f"[表情] 解析结果: cleaned={cleaned[:30]}, emotion={emotion}")
    
    # 如果既没有 [表情] 也没检测到情绪，尝试用 NexSandglass 上下文增强情绪检测
    if emotion is None and MEME_AUTO_DETECT_EMOTION:
        emotion = auto_detect_emotion(reply_text)
        # 如果还是没检测到，用 NexSandglass 搜索相关记忆来推断情绪
        if emotion is None:
            try:
                from sandglass_bridge import search_memory
                # 从回复中提取关键词搜索相关记忆
                keywords = re.findall(r'[\u4e00-\u9fff]{2,}', reply_text)
                for kw in keywords[:3]:
                    results = search_memory(kw, limit=2)
                    if results:
                        # 如果搜索到相关记忆中有强烈情绪词，用对应情绪
                        combined = " ".join(results)
                        for emo, words in [("崩溃", ["崩溃", "裂开", "疯了"]),
                                           ("委屈", ["委屈", "难过", "伤心"]),
                                           ("开心", ["开心", "高兴", "太好了"]),
                                           ("嫌弃", ["嫌弃", "无语", "离谱"])]:
                            if any(w in combined for w in words):
                                emotion = emo
                                log(f"[表情] 记忆推断情绪: {emotion} (来自关键词: {kw})")
                                break
            except Exception:
                pass
        if emotion is None:
            log("[表情] 无情绪，跳过")
            return None
    
    meme_path = match_meme(emotion)
    log(f"[表情] 匹配路径: {meme_path}")
    if meme_path and send_group_image(group_id, meme_path):
        MEME_LAST_SENT[key] = now
        log(f"[表情] 发送成功: {os.path.basename(meme_path)}")
        return os.path.basename(meme_path)
    log("[表情] 发送失败或无路径")
    return None

# ── 空闲退场表情包（崩溃/落寞/摆烂/失望类）──
_IDLE_EXIT_EMOTION_DEFAULTS = ["崩溃", "委屈", "烦躁", "落寞"]
IDLE_EXIT_EMOTIONS = [e for e in (_config.get("meme_idle_exit_emotions", _IDLE_EXIT_EMOTION_DEFAULTS) or []) if isinstance(e, str) and e.strip()] or _IDLE_EXIT_EMOTION_DEFAULTS

# ── 戳一戳表情包（生气/炸毛/无语类）──
POKE_MEME_EMOTIONS = ["生气", "烦躁", "炸毛", "暴躁"]

def send_idle_exit_meme(group_id):
    """空闲超时退场时，随机发送一张崩溃/委屈/烦躁/落寞类表情包。"""
    if not _config.get("meme_enable", True):
        return None
    if not _config.get("meme_idle_exit_enable", True):
        return None
    try:
        if not IDLE_EXIT_EMOTIONS:
            return None
        key = f"group_{group_id}"
        now = time.time()
        last = MEME_LAST_SENT.get(key, 0)
        if now - last < MEME_COOLDOWN:
            return None
        emotion = random.choice(IDLE_EXIT_EMOTIONS)
        meme_path = match_meme(emotion)
        if meme_path and send_group_image(group_id, meme_path):
            MEME_LAST_SENT[key] = now
            log(f"[表情] 空闲退场表情发送成功: {emotion} → {os.path.basename(meme_path)}")
            return os.path.basename(meme_path)
        log(f"[表情] 空闲退场表情发送失败或无匹配 (emotion={emotion})")
    except Exception as e:
        log(f"[表情] 空闲退场表情异常: {e}")
    return None


def send_file(file_path: str, target_qq: Optional[str] = None) -> bool:
    """发送文件到 QQ。file_path 为本地文件路径，target_qq 默认发送给列表第一个"""
    if target_qq is None:
        target_qq = TARGET_QQS[0]
    if not os.path.exists(file_path):
        log(f"文件不存在: {file_path}")
        return False
    try:
        resp = api_call("upload_private_file",
            {"user_id": int(target_qq), "file": file_path, "name": os.path.basename(file_path)})
        return resp.get("retcode") == 0
    except Exception as e:
        log(f"发送文件失败: {e}")
        return False


def save_chat_log(target_qq, speaker, text, reply=""):
    """消息转存到 Markdown 日志，按日期和 QQ 号分文件"""
    try:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        log_dir = os.path.join(CHAT_LOG_DIR, f"QQ_{target_qq}")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"chat_{today}.md")
        
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        exists = os.path.exists(log_file)
        
        with open(log_file, "a", encoding="utf-8") as f:
            if not exists:
                f.write(f"# QQ 聊天记录 - {target_qq}\n")
                f.write(f"## {today}\n\n")
            if reply:
                f.write(f"**{speaker}** `{ts}`\n\n{text}\n\n> {reply}\n\n---\n\n")
            else:
                f.write(f"**{speaker}** `{ts}`\n\n{text}\n\n---\n\n")
    except Exception as e:
        log(f"转存日志失败: {e}")


_personality_cache = {"text": None, "mtime": 0, "path": None}

# T6 static 前缀缓存：{key:(is_group, config_rev, personality_mtime): static_prompt}
_STATIC_PROMPT_CACHE = {}
_CONFIG_REV = 0

def _build_static_prompt(is_group: bool) -> str:
    """构建 static_prompt（人格+规则+多样性+表情包+铁律），按 (is_group, config_rev, personality_mtime) 缓存。

    结果只依赖 is_group 与配置/人格文件，同一值下内容完全一致 → 前缀稳定可命中 DeepSeek 硬盘缓存。
    """
    key = (is_group, _CONFIG_REV, _personality_cache.get("mtime", 0))
    hit = _STATIC_PROMPT_CACHE.get(key)
    if hit is not None:
        return hit

    system_prompt = load_personality()

    # ── 注入自己的身份：让 AI 知道自己叫什么(群昵称+QQ号) ──
    try:
        _self_nick = BOT_GROUP_NICK or "小a"
        _self_qq = str(BOT_QQ or "")
        _id_block = f"【你是谁·务必记住】你的网名是「小a」，在群里的昵称是「{_self_nick}」，QQ 号是 {_self_qq}。别人在群里 @你、叫你或提到「{_self_nick}」或「小a」都是在叫你。被问「你是谁/你叫什么/你是哪个」时，回答自己的名字（群里叫「{_self_nick}」、网名叫「小a」），不要说自己不知道，更不要说自己是 AI 助手。"
        system_prompt = _id_block + "\n\n" + system_prompt
    except Exception:
        pass

    # 私聊精简 system prompt：去掉群聊相关指令和自主学习成果，减少输入 token
    if not is_group:
        import re as _re
        system_prompt = _re.sub(r'【群聊行为】.*?(?=【|$)', '', system_prompt, flags=_re.DOTALL)
        system_prompt = _re.sub(r'【自主学习成果】.*?(?=【|$)', '', system_prompt, flags=_re.DOTALL)
        system_prompt = _re.sub(r'【群\\d+表达风格.*?(?=【|$)', '', system_prompt, flags=_re.DOTALL)

    # 回答多样性提示（私聊+群聊通用）
    system_prompt += "\n\n【回答多样性】每次回复务必变换句式和用词——别总用同样的开头、同样的语气。换词换角度，偶尔反问偶尔撒娇偶尔认真。短句和长句穿插，别每次都一个节奏。同一种情绪至少准备 3 种说法，别连续两次用一样的。例：「哈哈」「噗」「笑死」「好家伙」「6」「行吧」「诶嘿」「呜呜」「知道了知道了」轮着来。"

    # ── 去AI味：注入说话铁律（来自 de_ai 本体模块）──
    try:
        from de_ai import de_ai_rule_block
        _deai_block = de_ai_rule_block(_config)
        if _deai_block:
            system_prompt += "\n\n" + _deai_block
    except Exception:
        pass

    # 表情包指令（私聊+群聊通用）
    system_prompt += "\n\n【表情包】回复末尾可以追加 [表情:情绪词] 让 Bot 自动发一张匹配的表情包。情绪词可选：开心/害羞/惊讶/嫌弃/尴尬/慌乱/得意/委屈/烦躁/惊恐/疑惑/期待/崩溃/惊喜/呆滞/满足/温柔/落寞/撒娇/愉悦/享受/绝望/抗拒/痛苦/挣扎/安心/幸福/纠结/懊恼/困惑/不安/紧张/惊慌/担忧/偷窥/放松/悠闲/平静。不写情绪词（仅 [表情]）则由 Bot 自动从回复中提取情绪。每轮最多发一张。"

    # ── 诚实铁律：能调用工具真实完成的任务必须真实执行，绝不假装 ──
    system_prompt += "\n\n【诚实铁律·最重要】你拥有真实可用的工具（如 web_operate 打开网页/填问卷/答题、search_web 联网搜索、open_app 打开本机应用等）。凡是用工具能真实完成的操作，必须 CALL 对应工具执行，以工具返回的真实结果为准。绝对禁止用语言假装已经做了某些操作——例如「我这就去打开」「已经点开了」「问卷填完了」而实际并未调用任何工具，这种编造是被禁止的。若工具调用失败或你无法完成，如实说「我调用失败了/我做不到」并说明原因，绝不虚构操作过程和结果。任何对网页/系统/文件的实际操作，只有真实执行了工具才算完成。"

    # 群聊模式：规则放 static_prompt，动态信息放 dynamic_parts
    if is_group:
        system_prompt += (
            "\n\n【群聊规则】程序已经帮你做了预筛选，进入这里的消息都是值得回复的。你只需要关注回复质量。\n【重要】你有一个 get_group_members 工具可以获取群成员列表。当用户提到选一个人、群里有谁、随机@、谁在线等涉及群成员的问题时，你必须先调用这个工具获取真实成员列表，再从列表中选择。不要编造群成员。"
            "\n- 消息前的 [昵称(QQ号)] 标注了说话人。回答人物问题时直接从前缀提取，不要猜。"
            "\n- 回复要自然、简短、有个性。像群里的朋友一样说话，不要像客服。"
            "\n- 回复里不要出现任何QQ号数字，用昵称或直接对话。"
            "\n- 多人同时说话时，只回复最新一条消息，不要试图同时回复所有人。"
            "\n- 【重要】你可能会看到一些你没有回复过的消息——那是你之前选择沉默的。如果你在后续对话中需要用到这些信息，可以直接引用，比如'你刚才说的xxx'。"
            "\n- 不确定是否该回 → 输出 [不说话]，但大多数情况下应该回复。"
            "\n- 【联网搜索】需要实时信息时在回复开头独占一行写 [搜索:关键词]。"
        )

    _STATIC_PROMPT_CACHE[key] = system_prompt
    if len(_STATIC_PROMPT_CACHE) > 8:
        _STATIC_PROMPT_CACHE.clear()  # 防内存增长：最多保留 8 个键
    return system_prompt

def load_personality():
    """加载性格预设（EGO 优先 / personality.txt 兜底）。

    EGO personality_engine 接管人格管理：优先从 EGO 的 get_personality() 读取
    （解析/编辑由 EGO UI 完成），EGO 不可用或内容为空时再回退到直接读文件。
    纯 mtime 缓存，文件变化时自动更新。
    """
    global _personality_cache
    # ── EGO 优先：personality_engine 是人格的唯一管理入口 ──
    try:
        import importlib
        _ego_pe = importlib.import_module("plugins.ego.personality_engine")
        _ego_p = _ego_pe.get_personality() if hasattr(_ego_pe, "get_personality") else {}
        _ego_raw = ((_ego_p or {}).get("raw") or "").strip()
        if _ego_raw:
            # EGO 内容(mtime 变化检测) — 与文件读取共用缓存结构
            try:
                _path = os.path.normpath(os.path.join(
                    os.path.dirname(os.path.abspath(_ego_pe.__file__)),
                    "..", "..", "personality.txt"))
            except Exception:
                _path = None
            if _path and os.path.exists(_path):
                _mtime = os.path.getmtime(_path)
                if _personality_cache["text"] and _personality_cache["path"] == _path and _mtime == _personality_cache["mtime"]:
                    return _personality_cache["text"]
                _personality_cache = {"text": _ego_raw, "mtime": _mtime, "path": _path}
                return _ego_raw
            _personality_cache = {"text": _ego_raw, "mtime": 0, "path": None}
            return _ego_raw
    except Exception:
        pass  # EGO 不可用 → 走文件兜底

    # ── 兜底：直接读 personality.txt ──
    search_paths = [
        PERSONALITY_FILE,
        os.path.join(os.path.dirname(PERSONALITY_FILE), "..", "personality.txt"),
        os.path.join(os.path.dirname(PERSONALITY_FILE), "..", "..", "personality.txt"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "personality.txt"),
    ]
    for path in search_paths:
        try:
            norm = os.path.normpath(path)
            if os.path.exists(norm):
                mtime = os.path.getmtime(norm)
                if _personality_cache["text"] and _personality_cache["path"] == norm and mtime == _personality_cache["mtime"]:
                    return _personality_cache["text"]
                with open(norm, "r", encoding="utf-8") as f:
                    text = f.read().strip()
                _personality_cache = {"text": text, "mtime": mtime, "path": norm}
                return text
        except Exception:
            continue
    log("未找到 personality.txt，使用兜底人格")
    return (
        "18岁男生，网名小a，喜欢计算机。网上放得开，现实中有点社恐。"
        "性格懒散但认真，毒舌带撒娇，容易害羞被夸就发颜文字。"
        "对熟人活泼话多，不熟时礼貌但不过分热情。"
        "记住：你是群友是朋友，不是客服不是助手不是AI。"
        "被问是不是AI就说「你有病吧」或「别闹」。"
        "禁止说「作为AI」「我是助手」「根据分析」「建议你」这类话。"
    )


def load_state():
    global LAST_MSG_TIME, chat_history, group_chat_history
    state = data_store.read_json(STATE_FILE, None)
    if state:
        LAST_MSG_TIME = state.get("last_msg_time", 0)
        chat_history = state.get("chat_history", {})
        group_chat_history = state.get("group_chat_history", {})
        # C2 存储对齐：加载时压缩到最近 24 轮（同写入端截断一致）
        for qq in list(chat_history.keys()):
            if len(chat_history[qq]) > 24:
                chat_history[qq] = chat_history[qq][-24:]
    else:
        LAST_MSG_TIME = 0
        chat_history = {}
        group_chat_history = {}
    if LAST_MSG_TIME == 0:
        LAST_MSG_TIME = int(time.time()) - 600  # 回溯10分钟，不遗漏Bot离线期间的消息


def save_state(force: bool = False):
    """保存状态。非 force 时按 D 节流：累计 20 轮或距上次写盘 ≥60s 才真正落盘，
    避免高频群聊每轮洁盘。异常退出最多丢 60s 状态，可接受；退出/定期清理时 force=True。"""
    global _state_write_count, _state_last_write
    if not force:
        _state_write_count += 1
        now = time.time()
        if _state_write_count < 20 and (now - _state_last_write) < 60:
            return
        _state_write_count = 0
        _state_last_write = now
    with _state_lock:
        try:
            data_store.write_json(STATE_FILE, {
                "last_msg_time": LAST_MSG_TIME,
                "chat_history": {qq: ch[-24:] for qq, ch in chat_history.items()} if chat_history else {},
                "group_chat_history": group_chat_history,
            })
        except: pass


def _extract_card_text(data_str) -> str:
    """从 json 卡片段（OneBot data.data 为 JSON 字符串或 dict）提取可读文本。"""
    if not data_str:
        return ""
    obj = None
    if isinstance(data_str, str):
        try:
            obj = json.loads(data_str)
        except Exception:
            return data_str.strip()[:60]
    elif isinstance(data_str, dict):
        obj = data_str
    else:
        return str(data_str)[:60]
    if not isinstance(obj, dict):
        return str(obj)[:60]
    for key in ("prompt", "text", "desc", "description", "title", "content", "digest"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:120]
    return ""


def extract_text(msg, _depth=0):
    # 优先用 _raw_elements（原始消息段），回退到 message
    raw = msg.get("_raw_elements") or msg.get("message", "")
    if isinstance(raw, list):
        parts = []
        for seg in raw:
            if isinstance(seg, dict):
                seg_type = seg.get("type") or seg.get("elementType", 0)
                # OneBot message segment: {"type": "text", "data": {"text": "..."}}
                if isinstance(seg.get("data"), dict) and seg["data"].get("text"):
                    parts.append(seg["data"]["text"])
                # NT element type 1 = Text: {"elementType": 1, "textElement": {"content": "..."}}
                elif seg_type == 1:
                    content = ""
                    te = seg.get("textElement") or {}
                    if isinstance(te, dict):
                        content = te.get("content", "") or ""
                    if not content:
                        content = seg.get("content", "") or ""
                    parts.append(content)
                elif seg_type == "text":
                    content = ""
                    te = seg.get("textElement") or seg.get("data") or {}
                    if isinstance(te, dict):
                        content = te.get("content", "") or te.get("text", "") or ""
                    if not content:
                        content = seg.get("content", "") or ""
                    parts.append(content)
                elif seg_type in ("forward", 16):
                    d = seg.get("data") or seg.get("multiForwardMsgElement") or seg.get("multiForwardElement") or seg.get("forwardElement") or {}
                    fid = d.get("id", "") or d.get("resId", "") or d.get("rid", "")
                    if fid:
                        fwd_text = _fetch_forward_content(fid, _depth + 1)
                        parts.append(f"[转发消息] {fwd_text}" if fwd_text else "[合并转发消息]")
                    else:
                        parts.append("[合并转发消息]")
                elif seg_type == "json":
                    d = seg.get("data") or {}
                    card_text = _extract_card_text(d.get("data", ""))
                    parts.append(f"[卡片] {card_text}" if card_text else "[卡片]")
                elif seg_type in ("record", "voice"):
                    parts.append("[语音]")
                elif seg_type == "video":
                    parts.append("[视频]")
                elif seg_type == "file":
                    parts.append("[文件]")
        return " ".join(parts)
    if isinstance(raw, str):
        return raw
    return str(raw)


def _fetch_forward_content(forward_id, _depth=0):
    """通过 OneBot API 获取合并转发消息的完整文本（支持嵌套转发）"""
    if _depth > 3:
        return ""
    try:
        resp = api_call("get_forward_msg", {"id": forward_id})
        retcode = resp.get("retcode")
        data = resp.get("data") or {}
        messages = data.get("messages", [])
        log(f"[转发] id={forward_id} depth={_depth} retcode={retcode} 获取到{len(messages)}条消息")
        if not messages:
            log(f"[转发] 空响应 data={str(data)[:200]}")
        texts = []
        text_count = 0
        non_text_count = 0
        for m in messages:
            if not m or not isinstance(m, dict):
                continue
            sender = m.get("sender") or {}
            if not isinstance(sender, dict):
                sender = {}
            sender_name = sender.get("nickname", "") or str(sender.get("user_id", ""))
            msg_raw = m.get("content") or m.get("message") or []
            if not isinstance(msg_raw, list):
                msg_raw = []
            line_parts = []
            for seg in msg_raw:
                if isinstance(seg, dict):
                    seg_type = seg.get("type", "")
                    if seg_type == "text":
                        t = (seg.get("data") or {}).get("text", "")[:100]
                        if t:
                            line_parts.append(t)
                    elif seg_type == "forward":
                        if _depth < 3:
                            nested_id = (seg.get("data") or {}).get("id", "")
                            if nested_id:
                                try:
                                    nested = _fetch_forward_content(nested_id, _depth + 1)
                                    if nested:
                                        line_parts.append(nested)
                                except Exception as ne:
                                    log(f"[转发] 嵌套获取失败: {ne}")
            if line_parts:
                line_text = "".join(line_parts)
                if sender_name:
                    texts.append(f"{sender_name}: {line_text}")
                else:
                    texts.append(line_text)
                text_count += 1
            else:
                non_text_count += 1
        log(f"[转发] id={forward_id} depth={_depth} 统计: 有文本{text_count}条, 无文本{non_text_count}条, 输出长度{sum(len(t) for t in texts)}字")
        result = "\n".join(texts)
        if result:
            log(f"[转发] 内容预览: {result[:200]}")
        return result
    except Exception as e:
        import traceback
        log(f"转发消息获取失败: {e}")
        traceback.print_exc()
    return ""


def extract_images(msg):
    """从消息中提取图片 URL/路径，下载到本地，返回本地路径列表"""
    raw = msg.get("message", [])
    if not isinstance(raw, list):
        return []
    images = []
    for seg in raw:
        if isinstance(seg, dict) and seg.get("type") == "image":
            data = seg.get("data", {})
            url = data.get("url", "") or data.get("file", "") or data.get("image", "")
            # 也尝试 data 直接是 URL 的情况
            if not url and isinstance(data, str) and data.startswith("http"):
                url = data
            if not url:
                # 遍历 data 找 URL
                for v in data.values():
                    if isinstance(v, str) and v.startswith("http"):
                        url = v
                        break
            if not url:
                log(f"[图片提取] 图片段无URL: keys={list(data.keys()) if isinstance(data, dict) else type(data)}")
            
            # 本地文件/md5 场景（协议层转 OneBot 图片段是 sourcePath 等本地路径）
            if url and not url.startswith("http"):
                local = url
                if local.startswith("file://"):
                    local = local[7:].replace("/", "\\")
                elif local.startswith("file:"):
                    local = local[5:].replace("/", "\\")
                if os.path.exists(local):
                    images.append(local)
                    continue
                log(f"[图片提取] 本地文件不存在: {url}")
                continue
            
            # 尝试从 URL 下载
            if url and url.startswith("http") and _config.get("image_cache_enable", True):
                local = os.path.join(IMAGE_CACHE, f"img_{hashlib.md5(url.encode()).hexdigest()[:12]}.jpg")
                if not os.path.exists(local):
                    try:
                        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}) if REQUESTS_OK else None
                        if r and r.status_code == 200:
                            with open(local, "wb") as f:
                                f.write(r.content)
                        else:
                            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                            with urllib.request.urlopen(req, timeout=15, context=CTX) as resp:
                                with open(local, "wb") as f:
                                    f.write(resp.read())
                    except Exception as e:
                        log(f"图片下载失败: {e}")
                        continue
                images.append(local)
    return images


def image_to_base64(file_path):
    """将图片文件转为 base64 data URI"""
    try:
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(file_path)[1].lower().replace(".", "")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(ext, "jpeg")
        return f"data:image/{mime};base64,{b64}"
    except Exception as e:
        log(f"图片转 base64 失败: {e}")
        return None


def get_new_messages():
    global LAST_MSG_TIME
    all_msgs = []
    
    # 私聊消息（多账号）
    for tqq in TARGET_QQS:
        try:
            resp = api_call("get_friend_msg_history",
                {"user_id": tqq, "count": 10})
        except Exception as e:
            log(f"轮询私聊[{tqq}]失败(跳过): {e}")
            continue
        if resp.get("retcode") == 0:
            messages = resp.get("data", {}).get("messages", [])
            for msg in messages:
                msg["_type"] = "private"
            all_msgs.extend(messages)
    
    # 群聊消息（单个群异常/非法群号只跳过该群，不拖垮整轮轮询）
    for gid in GROUP_IDS:
        try:
            resp = api_call("get_group_msg_history",
                {"group_id": int(gid), "count": 10})
        except Exception as e:
            log(f"轮询群[{gid}]失败(跳过): {e}")
            continue
        if resp.get("retcode") == 0:
            messages = resp.get("data", {}).get("messages", [])
            for msg in messages:
                msg["_type"] = "group"
                msg["_group_id"] = gid
                # 更新群内最后消息时间（用于空闲退场判断）
                mt = msg.get("time", 0)
                if mt > GROUP_LAST_ANY_MSG.get(gid, 0):
                    GROUP_LAST_ANY_MSG[gid] = mt
            all_msgs.extend(messages)
    
    # 按时间排序
    all_msgs.sort(key=lambda m: m.get("time", 0))
    
    new_msgs = []
    max_time = LAST_MSG_TIME
    seen_msg_ids = set()
    for msg in all_msgs:
        msg_time = msg.get("time", 0)
        msg_id = msg.get("message_id", 0)

        # 去重：同一个 message_id 只处理一次
        if msg_id in seen_msg_ids:
            continue
        seen_msg_ids.add(msg_id)

        # 启动基线：本会话开始前的历史消息不处理（避免重启后回复旧消息）
        if msg_time and msg_time < startup_time:
            continue

        if msg_time > max_time:
            max_time = msg_time
        if msg_time > LAST_MSG_TIME:
            new_msgs.append(msg)
    if max_time > LAST_MSG_TIME:
        LAST_MSG_TIME = max_time
        save_state()
    return new_msgs


# ═══════════════════════════════════════════
#  远程控制指令
# ═══════════════════════════════════════════

def handle_screenshot():
    """截屏并发送"""
    try:
        import pyautogui
        path = os.path.join(TEMP_DIR, f"screenshot_{int(time.time())}.png")
        pyautogui.screenshot(path)
        log(f"截图已保存: {path}")
        if send_image(path):
            send_message("桌面截图 ↑")
            return True
        return False
    except Exception as e:
        send_message(f"截图失败: {e}")
        return False


def handle_open(target):
    """打开文件/文件夹/程序/网址"""
    target = target.strip()
    if not target:
        return False

    # 常见别名
    aliases = {
        "我的电脑": "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}",
        "此电脑": "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}",
        "回收站": "::{645FF040-5081-101B-9F08-00AA002F954E}",
        "控制面板": "control",
        "任务管理器": "taskmgr",
        "记事本": "notepad",
        "计算器": "calc",
        "cmd": "cmd",
        "终端": "wt",
        "画图": "mspaint",
        "资源管理器": "explorer",
        "设置": "ms-settings:",
        "桌面": os.path.expanduser("~/Desktop"),
        "下载": os.path.expanduser("~/Downloads"),
        "文档": os.path.expanduser("~/Documents"),
        # 浏览器
        "浏览器": "msedge", "edge": "msedge", "chrome": "chrome",
        # 常用软件
        "steam": "steam",
        "word": "winword", "excel": "excel", "ppt": "powerpnt",
        "vscode": "code",
        "qq": _config.get("qq_path", ""),
        "截图工具": "snippingtool", "截图": "snippingtool",
        # MSIX/商店应用 (shell:AppsFolder)
        "微信": "shell:AppsFolder\\Microsoft.AutoGenerated.{5FD21F2E-64C7-8F20-F9FB-2514D0A0A64B}",
    }
    # 盘符别名：按实际存在的盘符动态生成（不写死 C/D/E）
    import string as _string
    for _ch in _string.ascii_uppercase:
        if os.path.exists(_ch + ":\\"):
            aliases[f"{_ch}盘"] = f"{_ch}:\\"
            aliases[_ch.lower()] = f"{_ch}:\\"

    target_lower = target.lower().replace(" ", "")
    if target_lower in aliases:
        target = aliases[target_lower]

    if not target:
        send_message("QQ 路径未配置，请在设置中填写 qq_path")
        return False

    try:
        if target.startswith("shell:"):
            subprocess.run(["explorer", target], shell=True)
        else:
            os.startfile(target)
        send_message(f"已打开 {target}")
        return True
    except Exception as e:
        send_message(f"打开失败: {e}")
        return False


SYSTEM_INFO_TEMPLATE = """电脑配置
CPU: {cpu}
GPU: {gpu}
内存: {mem_total}GB (已用{mem_used:.1f}GB)
系统: {os_ver}
启动: {uptime}"""


def handle_sysinfo():
    """获取系统信息"""
    try:
        import psutil, platform

        # CPU
        cpu_name = "Unknown"
        try:
            import subprocess
            r = subprocess.run(
                ['wmic', 'cpu', 'get', 'name'], capture_output=True, timeout=10)
            stdout = r.stdout.decode("utf-8", errors="replace") if isinstance(r.stdout, bytes) else r.stdout
            lines = [l.strip() for l in stdout.split("\n") if l.strip()]
            cpu_name = lines[1] if len(lines) > 1 else lines[0]
        except:
            cpu_name = platform.processor()

        cpu_percent = psutil.cpu_percent(interval=1)

        # GPU
        gpu_name = "Unknown"
        try:
            r = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True, timeout=10)
            gpu_name = r.stdout.decode("utf-8", errors="replace").strip()
        except: pass

        # 内存
        mem = psutil.virtual_memory()
        mem_total = mem.total / (1024**3)
        mem_used = mem.used / (1024**3)

        # 系统
        os_ver = f"Windows {platform.release()}"

        # 运行时间
        boot = datetime.datetime.fromtimestamp(psutil.boot_time())
        now = datetime.datetime.now()
        delta = now - boot
        hours = delta.seconds // 3600
        mins = (delta.seconds % 3600) // 60
        uptime = f"{delta.days}天{hours}小时{mins}分"

        info = SYSTEM_INFO_TEMPLATE.format(
            cpu=f"{cpu_name} ({cpu_percent}%)",
            gpu=gpu_name,
            mem_total=mem_total,
            mem_used=mem_used,
            os_ver=os_ver,
            uptime=uptime
        )
        send_message(info)
        return True
    except Exception as e:
        send_message(f"获取系统信息失败: {e}")
        return False


def handle_volume(action, level=None):
    """调整音量: up/down/mute/set"""
    try:
        import comtypes
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        if action == "up":
            current = volume.GetMasterVolumeLevelScalar()
            volume.SetMasterVolumeLevelScalar(min(current + 0.1, 1.0), None)
            new_pct = int(volume.GetMasterVolumeLevelScalar() * 100)
            send_message(f"音量 ↑ {new_pct}%")
        elif action == "down":
            current = volume.GetMasterVolumeLevelScalar()
            volume.SetMasterVolumeLevelScalar(max(current - 0.1, 0.0), None)
            new_pct = int(volume.GetMasterVolumeLevelScalar() * 100)
            send_message(f"音量 ↓ {new_pct}%")
        elif action == "mute":
            muted = volume.GetMute()
            volume.SetMute(not muted, None)
            send_message("已静音" if not muted else "已取消静音")
        elif action == "set" and level is not None:
            volume.SetMasterVolumeLevelScalar(level / 100.0, None)
            send_message(f"音量设为 {level}%")
        return True
    except ImportError:
        # pycaw 未安装时使用 nircmd 或直接按键
        try:
            import ctypes
            VK_VOLUME_UP = 0xAF
            VK_VOLUME_DOWN = 0xAE
            VK_VOLUME_MUTE = 0xAD
            if action == "up":
                ctypes.windll.user32.keybd_event(VK_VOLUME_UP, 0, 0, 0)
                ctypes.windll.user32.keybd_event(VK_VOLUME_UP, 0, 2, 0)
            elif action == "down":
                ctypes.windll.user32.keybd_event(VK_VOLUME_DOWN, 0, 0, 0)
                ctypes.windll.user32.keybd_event(VK_VOLUME_DOWN, 0, 2, 0)
            elif action == "mute":
                ctypes.windll.user32.keybd_event(VK_VOLUME_MUTE, 0, 0, 0)
                ctypes.windll.user32.keybd_event(VK_VOLUME_MUTE, 0, 2, 0)
            send_message(f"音量已调整 ({action})")
            return True
        except Exception as e:
            send_message(f"音量调整失败: {e}")
            return False
    except Exception as e:
        send_message(f"音量调整失败: {e}")
        return False


def handle_power(action):
    """关机/重启/锁屏/休眠 - 需确认"""
    actions = {
        "关机": "shutdown /s /t 60 /c \"手机远程关机，60秒后执行\"",
        "重启": "shutdown /r /t 60 /c \"手机远程重启，60秒后执行\"",
        "取消关机": "shutdown /a",
        "取消重启": "shutdown /a",
        "锁屏": "rundll32.exe user32.dll,LockWorkStation",
        "休眠": "shutdown /h",
    }

    if action in actions:
        cmd = actions[action]
        subprocess.run(cmd, shell=True)
        if action in ("关机", "重启"):
            send_message(f"⚠️ {action}已排程，60秒后执行。发送「取消{action}」可撤销。")
        elif action in ("取消关机", "取消重启"):
            send_message("已取消。")
        else:
            send_message(f"已执行 {action}")
        return True
    return False


def handle_process(action, target=""):
    """列出/结束进程"""
    try:
        import psutil

        if action == "list":
            procs = []
            for p in psutil.process_iter(['pid', 'name', 'memory_percent', 'cpu_percent']):
                try:
                    info = p.info
                    if info['memory_percent'] > 0.5 or info['cpu_percent'] > 1:
                        procs.append(info)
                except: pass
            procs.sort(key=lambda x: x.get('memory_percent', 0), reverse=True)
            lines = ["高占用进程 Top10:"]
            for p in procs[:10]:
                lines.append(
                    f"{p['name']} (PID:{p['pid']}) "
                    f"内存:{p.get('memory_percent', 0):.1f}% "
                    f"CPU:{p.get('cpu_percent', 0):.1f}%")
            send_message("\n".join(lines))
            return True

        elif action == "kill" and target:
            try:
                pid = int(target)
                p = psutil.Process(pid)
                name = p.name()
                p.terminate()
                send_message(f"已终止 {name} (PID:{pid})")
                return True
            except ValueError:
                for p in psutil.process_iter(['pid', 'name']):
                    if target.lower() in (p.info.get('name') or '').lower():
                        p.terminate()
                        send_message(f"已终止 {p.info['name']} (PID:{p.info['pid']})")
                        return True
                send_message(f"未找到进程: {target}")
                return False
    except Exception as e:
        send_message(f"进程操作失败: {e}")
        return False


def handle_search(query):
    """搜索文件"""
    query = query.strip()
    if not query:
        return False
    try:
        import subprocess
        r = subprocess.run(
            ['where', '/R', os.path.expanduser("~"), query],
            capture_output=True, timeout=10)
        stdout = r.stdout.decode("utf-8", errors="replace") if isinstance(r.stdout, bytes) else r.stdout
        results = [l.strip() for l in stdout.split("\n") if l.strip()][:5]
        if results:
            send_message("找到:\n" + "\n".join(results))
        else:
            send_message(f"未找到与「{query}」相关的文件")
        return True
    except:
        send_message("搜索超时，请缩小范围")
        return False


def handle_user_file(file_path: str, target_qq: Optional[str] = None) -> str:
    """发送文件给用户。target_qq 默认发送给列表第一个"""
    if target_qq is None:
        target_qq = TARGET_QQS[0]
    # 支持相对路径和常见别名
    aliases = {
        "桌面": os.path.expanduser("~/Desktop"),
        "下载": os.path.expanduser("~/Downloads"),
        "文档": os.path.expanduser("~/Documents"),
        "图片": os.path.expanduser("~/Pictures"),
        "音乐": os.path.expanduser("~/Music"),
        "视频": os.path.expanduser("~/Videos"),
    }
    # 盘符别名：按实际存在的盘符动态生成（不写死 C/D/E）
    import string as _string
    for _ch in _string.ascii_uppercase:
        if os.path.exists(_ch + ":\\"):
            aliases[f"{_ch}盘"] = f"{_ch}:\\"
            aliases[_ch.lower()] = f"{_ch}:\\"
    
    # 检查别名
    if file_path in aliases:
        # 如果是目录，列出文件
        dir_path = aliases[file_path]
        if os.path.isdir(dir_path):
            files = [f for f in os.listdir(dir_path) if os.path.isfile(os.path.join(dir_path, f))]
            if not files:
                send_message(f"{file_path} 目录是空的")
                return "目录为空"
            # 发送前5个文件
            for f in files[:5]:
                full_path = os.path.join(dir_path, f)
                if send_file(full_path, target_qq):
                    log(f"已发送文件: {f}")
                else:
                    send_message(f"{f} 发送失败")
            return f"已发送 {len(files[:5])} 个文件"
        else:
            file_path = dir_path
    
    # 检查文件是否存在
    if not os.path.exists(file_path):
        # 尝试搜索文件
        import subprocess
        try:
            r = subprocess.run(
                ['where', '/R', os.path.expanduser("~"), file_path],
                capture_output=True, timeout=10)
            stdout = r.stdout.decode("utf-8", errors="replace") if isinstance(r.stdout, bytes) else r.stdout
            results = [l.strip() for l in stdout.split("\n") if l.strip()]
            if results:
                # 发送第一个匹配的文件
                if send_file(results[0], target_qq):
                    log(f"已发送文件: {results[0]}")
                    return f"已发送 {os.path.basename(results[0])}"
                else:
                    send_message(f"文件发送失败: {results[0]}")
                    return "发送失败"
            else:
                send_message(f"未找到文件: {file_path}")
                return "未找到文件"
        except:
            send_message(f"搜索文件失败: {file_path}")
            return "搜索失败"
    
    # 直接发送
    if send_file(file_path, target_qq):
        log(f"已发送文件: {file_path}")
        return f"已发送 {os.path.basename(file_path)}"
    else:
        send_message(f"文件发送失败: {file_path}")
        return "发送失败"


# ═══════════════════════════════════════════
#  联网搜索
# ═══════════════════════════════════════════

def search_web(query):
    """联网搜索，返回结果摘要（优先天气 API）"""
    # 如果是天气查询，用 wttr.in
    if "天气" in query or "weather" in query.lower() or query.strip() == "天气":
        try:
            city_match = re.search(r'([\u4e00-\u9fa5]+)天气', query)
            city = city_match.group(1) if city_match else "Hengyang"
            # wttr.in 支持中文城市名，但用英文更稳
            city_map = {
                "北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou",
                "深圳": "Shenzhen", "杭州": "Hangzhou", "成都": "Chengdu",
                "衡阳": "Hengyang", "长沙": "Changsha", "武汉": "Wuhan",
                "南京": "Nanjing", "西安": "XiAn", "重庆": "Chongqing"
            }
            city_en = city_map.get(city, "Hengyang")
            url = f"https://wttr.in/{city_en}?format=j1"
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.0"})
            with urllib.request.urlopen(req, timeout=10, context=CTX) as r:
                data = json.loads(r.read().decode("utf-8"))
            cc = data["current_condition"][0]
            fc = data["weather"][0]
            astro = fc["astronomy"][0]
            result = f"【{city}天气】\n"
            result += f"当前: {cc['temp_C']}°C (体感{cc['FeelsLikeC']}°C) {cc['weatherDesc'][0]['value']}\n"
            result += f"湿度: {cc['humidity']}% 风速: {cc['windspeedKmph']}km/h\n"
            result += f"今天: {fc['mintempC']}°C ~ {fc['maxtempC']}°C\n"
            result += f"日出: {astro['sunrise']} 日落: {astro['sunset']}"
            log(f"天气查询「{city}」成功")
            return result
        except Exception as e:
            log(f"天气 API 失败: {e}")
            # 降级到 Bing 搜索
            pass

    # 如果是股票查询，用新浪财经 API
    if re.search(r'股票|股价|行情|A股|港股|美股', query) or re.search(r'\d{6}', query):
        try:
            # 股票名称→代码映射
            stock_map = {
                "京东方": "sz000725", "京东方A": "sz000725",
                "茅台": "sh600519", "贵州茅台": "sh600519",
                "比亚迪": "sz002594", "宁德时代": "sz300750",
                "腾讯": "hk00700", "腾讯控股": "hk00700",
                "阿里巴巴": "hk09988", "阿里": "hk09988",
                "百度": "hk09888", "美团": "hk03690",
                "小米": "hk01810", "中芯国际": "sh688981",
                "特斯拉": "TSLA", "苹果": "AAPL", "英伟达": "NVDA",
                "微软": "MSFT", "谷歌": "GOOGL",
            }
            stock_code = None
            stock_name = ""
            for name, code in stock_map.items():
                if name in query:
                    stock_code = code
                    stock_name = name
                    break
            if not stock_code:
                # 尝试从 query 中提取 6 位数字代码
                code_match = re.search(r'(\d{6})', query)
                if code_match:
                    num = code_match.group(1)
                    if num.startswith(('6','5','9')):
                        stock_code = f"sh{num}"
                    else:
                        stock_code = f"sz{num}"
                    stock_name = num

            if stock_code:
                url = f"https://hq.sinajs.cn/list={stock_code}"
                req = urllib.request.Request(url, headers={
                    "Referer": "https://finance.sina.com.cn"
                })
                with urllib.request.urlopen(req, timeout=10, context=CTX) as r:
                    raw = r.read().decode("gbk", errors="ignore")
                parts = raw.split('"')[1].split(",")
                if len(parts) >= 10:
                    name = parts[0]
                    open_p = float(parts[1]) if parts[1] else 0
                    yest_close = float(parts[2]) if parts[2] else 0
                    current = float(parts[3]) if parts[3] else 0
                    high = float(parts[4]) if parts[4] else 0
                    low = float(parts[5]) if parts[5] else 0
                    volume = int(float(parts[8]) / 100) if parts[8] else 0
                    date = parts[-4] if len(parts) > 29 else parts[30] if len(parts) > 30 else ""
                    change_pct = ((current - yest_close) / yest_close * 100) if yest_close else 0
                    arrow = "↑" if change_pct >= 0 else "↓"
                    result = f"【{name}】{stock_code.split('.')[0]}\n"
                    result += f"现价: {current:.2f} {arrow}{abs(change_pct):.2f}%\n"
                    result += f"开盘: {open_p:.2f}  最高: {high:.2f}  最低: {low:.2f}\n"
                    result += f"昨收: {yest_close:.2f}  成交量: {volume}手"
                    if date:
                        result += f"\n更新时间: {date}"
                    log(f"股票查询「{stock_name}」成功 → {name} {current:.2f}")
                    return result
                else:
                    log(f"股票 API 返回格式异常: {len(parts)} 字段")
        except Exception as e:
            log(f"股票 API 失败: {e}")
            # 降级到 Bing 搜索

    # 普通搜索（Bing）
    q = urllib.parse.quote(query)
    try:
        url = f"https://www.bing.com/search?q={q}&setlang=zh-cn"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=10, context=CTX) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log(f"搜索失败: {e}")
        return f"搜索「{query}」失败，请稍后再试。"

    # 提取搜索结果 URL + 摘要（多模式兼容）
    results = []
    urls = []
    # 模式1: b_caption 块
    for m in re.finditer(r'class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>', html, re.DOTALL):
        text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        text = re.sub(r'&ensp;|&nbsp;|&#\d+;', ' ', text)
        if len(text) > 20:
            results.append(text)
        if len(results) >= 5:
            break
    # 模式2: 标准搜索结果描述
    if len(results) < 5:
        for m in re.finditer(r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', html, re.DOTALL):
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            text = re.sub(r'&ensp;|&nbsp;|&#\d+;', ' ', text)
            if len(text) > 20 and text not in results:
                results.append(text)
            if len(results) >= 5:
                break
    # 模式3: 通用描述文本
    if len(results) < 3:
        for m in re.finditer(r'<span[^>]*class="[^"]*b_paractl[^"]*"[^>]*>(.*?)</span>', html, re.DOTALL):
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            text = re.sub(r'&ensp;|&nbsp;|&#\d+;', ' ', text)
            if len(text) > 30 and text not in results:
                results.append(text)
            if len(results) >= 5:
                break

    # 提取搜索结果中的 URL（多种 Bing HTML 格式兼容）
    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        link = m.group(1)
        if any(skip in link for skip in ['bing.com', 'microsoft.com', 'google.com', 'javascript:', 'void(0)']):
            continue
        if link not in urls:
            urls.append(link)
        if len(urls) >= 10:
            break
    # Bing 有时用 data-u 或 data-href 存储真实 URL
    for m in re.finditer(r'data-[uh]ref="(https?://[^"]+)"', html):
        link = m.group(1)
        if any(skip in link for skip in ['bing.com', 'microsoft.com', 'google.com']):
            continue
        if link not in urls:
            urls.append(link)
        if len(urls) >= 10:
            break

    if not results and not urls:
        return f"搜索「{query}」未找到相关结果。"

    summary = "\n\n".join(f"{i+1}. {r}" for i, r in enumerate(results))

    # 尝试打开前 3 个 URL 抓取正文内容
    page_texts = []
    for url in urls[:3]:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=10, context=CTX) as resp:
                page_html = resp.read().decode("utf-8", errors="ignore")
            page_html = re.sub(r'<(script|style|nav|header|footer)[^>]*>.*?</\1>', '', page_html, flags=re.DOTALL|re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', page_html)
            text = re.sub(r'&ensp;|&nbsp;|&#\d+;', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > 200:
                page_texts.append(text[:3000])
        except Exception as e:
            log(f"[搜索] 页面抓取失败 {url[:60]}: {e}")

    if page_texts:
        summary += "\n\n【页面正文】\n" + "\n---\n".join(page_texts)

    log(f"搜索「{query}」 → {len(results)} 条摘要 + {len(page_texts)} 个页面正文")
    return f"【{query}】搜索结果：\n{summary}"


# ═══════════════════════════════════════════
#  AI 工具定义
# ═══════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "联网搜索实时信息。天气/新闻/股价/最新事件等需联网验证时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词。天气查询用「城市名+天气」，股票用「股票名+股票」"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "打开本机应用或文件（浏览器/记事本/计算器/微信/Office/盘符/文件夹等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "要打开的应用名称或路径，如「微信」「Steam」「记事本」「E盘」"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "截取桌面并发送给用户。用户想看电脑状态时使用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_info",
            "description": "获取CPU/GPU/内存/系统版本/运行时长等实时信息。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "control_volume",
            "description": "调整电脑系统音量。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["up", "down", "mute", "set"],
                        "description": "up=增大音量, down=减小音量, mute=切换静音, set=设置为指定数值"
                    },
                    "level": {
                        "type": "integer",
                        "description": "set 模式时的音量数值(0-100)，其他模式不需要"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "control_power",
            "description": "控制电脑电源：关机/重启/锁屏/休眠/取消。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["关机", "重启", "锁屏", "休眠", "取消关机", "取消重启"],
                        "description": "要执行的电源操作"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_process",
            "description": "查看或终止运行中的进程。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "kill"],
                        "description": "list=列出高占用进程, kill=终止指定进程"
                    },
                    "target": {
                        "type": "string",
                        "description": "kill 模式时指定进程名或PID，如「chrome」「1234」"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "在用户目录下搜索文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "文件名关键词，如「周报」「发票」「照片」"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_user_file",
            "description": "发送电脑文件给用户。path需完整路径，可由搜索确定。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要发送的文件的完整本地路径"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_operate",
            "description": (
                "操作网页 v2。标准流程：open_url 会自动返回带编号的结构化快照（如 - button \"登录\" [ref=e5]），"
                "然后用 ref 调 click/fill/select/check/press/hover（每次动作后自动回传最新快照，无需你再猜选择器）。"
                "多步复杂任务直接用 action=autonomous + goal，插件会自主完成。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "open_url", "snapshot", "get_page",
                            "click", "dblclick", "fill", "fill_form", "select", "check", "uncheck", "press", "hover", "upload", "focus",
                            "scroll", "extract", "execute_js", "screenshot", "wait", "get_attribute", "press_key",
                            "tab_list", "tab_switch", "tab_new", "handle_dialog", "download",
                            "api_call", "close", "save_state", "load_state", "autonomous",
                        ],
                        "description": "要执行的网页操作类型",
                    },
                    "url": {"type": "string", "description": "open_url/tab_new 要打开的链接"},
                    "ref": {"type": "string", "description": "快照中元素编号，如 e5（click/fill/select/check/press/hover/upload/get_attribute 首选）"},
                    "value": {"type": "string", "description": "fill 填入的文本 / select 选择的值 / press 的按键"},
                    "selector": {"type": "string", "description": "兜底 CSS 选择器（优先用 ref；无快照时才用）"},
                    "text": {"type": "string", "description": "click 兜底：按按钮文字定位"},
                    "description": {"type": "string", "description": "元素语义描述，供视觉兜底使用（可选）"},
                    "goal": {"type": "string", "description": "autonomous 模式：自然语言目标（如“把这份问卷答完并提交”）"},
                    "fields": {
                        "type": "array",
                        "description": "fill_form 批量填表：每项 {ref 或 selector, value}",
                        "items": {"type": "object", "properties": {
                            "ref": {"type": "string"}, "selector": {"type": "string"}, "value": {"type": "string"}
                        }},
                    },
                    "index": {"type": "integer", "description": "tab_switch 的标签页序号"},
                    "name": {"type": "string", "description": "get_attribute 要取的属性名"},
                    "file_path": {"type": "string", "description": "upload 的本地文件路径"},
                },
                "required": ["action"]
            }
        }
    }
]

# 群聊安全工具（只读，不能操控电脑）
GROUP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_group_members",
            "description": "获取当前群成员列表。选人/问谁在/随机@时使用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "按描述生成图片（描述需翻译为英文）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "英文图片描述"
                    },
                    "style": {
                        "type": "string",
                        "enum": ["default", "anime", "realistic"],
                        "description": "风格"
                    }
                },
                "required": ["prompt"]
            }
        }
    }
]

TOOL_FUNCTIONS = {
    "search_web": lambda args: search_web(args["query"]),
    "open_app": lambda args: "已打开" if handle_open(args["name"]) else f"已尝试打开 {args['name']}",
    "take_screenshot": lambda args: (
        handle_screenshot() and "截图已发送"
    ) or "截图失败",
    "get_system_info": lambda args: (
        handle_sysinfo() and "系统信息已发送"
    ) or "获取系统信息失败",
    "control_volume": lambda args: (
        handle_volume(args["action"], args.get("level"))
    ) and "音量已调整" or "音量调整失败",
    "control_power": lambda args: (
        handle_power(args["action"])
    ) and "电源操作已执行" or "电源操作失败",
    "manage_process": lambda args: (
        handle_process(args["action"], args.get("target", ""))
    ) and "进程操作已完成" or "进程操作失败",
    "search_files": lambda args: (
        handle_search(args["query"])
    ) and "文件搜索已完成" or "文件搜索失败",
    "send_user_file": lambda args: "文件已发送" if handle_user_file(args["path"]) else "文件发送失败",
    "generate_image": lambda args: handle_generate_image(args["prompt"], args.get("style", "default")),
    "web_operate": lambda args: handle_web_operate(args),
}

def _build_group_tools():
    """根据配置 group_tool_allowlist 动态生成群聊可用工具列表。

    - 配置了 group_tool_allowlist（数组）时：只返回该名单里存在于 TOOLS/GROUP_TOOLS 的工具。
      常见用法：["get_group_members", "generate_image", "web_operate"]，即可让网页操作在群聊可用。
    - 未配置时：回退到默认 GROUP_TOOLS（保持向后兼容，仅 get_group_members + generate_image）。
    """
    allow = _config.get("group_tool_allowlist")
    if allow is None:
        return GROUP_TOOLS
    if not isinstance(allow, list):
        allow = []
    # 候选池 = 私聊工具 + 默认群聊工具（去重，按工具名）
    pool = []
    seen_names = set()
    for t in list(TOOLS) + list(GROUP_TOOLS):
        n = t.get("function", {}).get("name")
        if n and n not in seen_names:
            seen_names.add(n)
            pool.append(t)
    result = []
    for t in pool:
        n = t.get("function", {}).get("name")
        if n in allow:
            result.append(t)
    return result

def _tool_get_group_members(args):
    """获取群成员列表（工具调用）"""
    gid = args.get("group_id", "")
    if not gid:
        return "未指定群号"
    resp = api_call("get_group_member_list", {"group_id": int(gid)})
    if resp.get("retcode") not in (0, 200):
        return f"获取失败: {resp.get('retcode')}"
    data = resp.get("data") or {}
    members = data if isinstance(data, list) else data.get("members", [])
    names = []
    for m in members:
        if not m or not isinstance(m, dict):
            continue
        uid = str(m.get("user_id", ""))
        card = (m.get("card") or "").strip()
        nick = (m.get("nickname") or "").strip()
        display = card if card else nick
        if display:
            names.append(display)
    if not names:
        return "群成员列表为空"
    return f"群成员({len(names)}人): " + "、".join(names)

TOOL_FUNCTIONS["get_group_members"] = _tool_get_group_members


def _make_plugin_chat_closure():
    """为插件自主 Agent 提供一次带工具调用的文本 LLM 回调，复用主模型配置。
    返回签名：chat(messages, tools=None) -> (content, tool_calls, usage)。"""
    def _chat(messages, tools=None, max_tokens=1500):
        msg, usage = _call_chat_sync(
            messages, runtime_cfg=None, max_tokens=max_tokens, stream=False,
            with_tools=(tools or []),
        )
        return (msg.get("content") or ""), (msg.get("tool_calls") or []), (usage or {})
    return _chat


_WEB_ACTION_DONE_WORDS = {
    "click": "已点击", "dblclick": "已双击", "fill": "已填写", "select": "已选择",
    "check": "已勾选", "uncheck": "已取消勾选", "press": "已按键", "hover": "已悬停",
    "focus": "已聚焦", "upload": "已上传", "scroll": "已滚动",
}


def handle_web_operate(args):
    """网页操作工具 v2：结构化快照 + ref 精确操作 + 自主 Agent。"""
    try:
        import plugins.web_operator as _wo
    except Exception as e:
        return f"网页操作插件加载失败: {e}"

    args = args or {}
    action = (args.get("action") or "").strip()
    if not action:
        return "缺少 action 参数"
    if action == "survey":
        return "survey 已废弃：请用 action=autonomous + goal，或 open_url 拿快照后按 ref 逐个 click。"

    task_type = f"web.{action}"
    params = dict(args)
    params.pop("action", None)
    target_qq = params.pop("target_qq", None)

    context = {}
    if action == "autonomous":
        context["chat"] = _make_plugin_chat_closure()

    try:
        res = _wo.execute_task(task_type, params, context=context)
    except Exception as e:
        return f"网页操作执行失败: {e}"

    if not isinstance(res, dict):
        return f"网页操作返回异常: {res}"

    logs = res.get("logs") or []
    if not res.get("ok"):
        err = " | ".join(l.get("text", "") for l in logs if l.get("level") == "error")
        return f"网页操作失败: {res.get('error') or err or '未知错误'}"

    result = res.get("result") or {}

    # screenshot：把截图发给用户（仅模型显式要求时）
    if action == "screenshot":
        shot = result.get("path") or result.get("screenshot_path")
        sent = ""
        if shot and os.path.exists(shot):
            try:
                if target_qq:
                    send_image(shot, target_qq=target_qq)
                    sent = "（截图已发送）"
                elif params.get("group_id"):
                    send_group_image(params["group_id"], shot)
                    sent = "（截图已发送）"
            except Exception:
                pass
        out = "网页截图完成 " + sent
        if result.get("vision_summary"):
            out += "\n[视觉] " + str(result["vision_summary"])[:600]
        return out.strip()

    # autonomous：返回最终结论（插件内部已完成多步）
    if action == "autonomous":
        steps = result.get("steps", "?")
        answer = (result.get("answer") or "").strip()
        return f"[网页自主任务] 已完成（{steps} 步）\n{answer}" if answer else f"[网页自主任务] 已结束（{steps} 步）但无明确结论"

    # 通用：动作摘要 + 最新快照（供模型继续按 ref 操作）
    parts = []
    if action == "open_url":
        parts.append(f"已打开「{result.get('title','')}」（{result.get('url','')}）")
    elif action == "get_page":
        parts.append(f"页面内容（{result.get('length',0)} 字）：\n{(result.get('text') or '')[:1500]}")
    elif action == "snapshot":
        parts.append(f"当前页面 {result.get('ref_count',0)} 个可交互元素")
    elif action == "extract":
        ex = result.get("fields") or {}
        parts.append("提取结果：" + (json.dumps(ex, ensure_ascii=False) if ex else (result.get("text") or "")[:800]))
    elif action in _WEB_ACTION_DONE_WORDS:
        note = _WEB_ACTION_DONE_WORDS[action]
        if res.get("healed"):
            note += "（自愈重定位）"
        if res.get("vision_fallback"):
            note += "（视觉定位）"
        if res.get("from_cache"):
            note += "（缓存）"
        parts.append(note)
    elif action == "get_attribute":
        parts.append(f"{result.get('attribute')} = {result.get('value')}")
    elif action == "fill_form":
        parts.append(f"表单：成功 {len(result.get('filled') or [])}，失败 {len(result.get('failed') or [])}")
    elif action == "execute_js":
        parts.append("JS 结果：" + str(result.get("result"))[:500])
    elif action == "api_call":
        parts.append(f"HTTP {result.get('status_code')}：" + str(result.get("body"))[:600])
    elif action == "tab_list":
        tabs = result.get("tabs") or []
        parts.append("标签页：" + " | ".join(f"[{t['index']}]{t.get('title') or t.get('url','')[:30]}" for t in tabs))
    elif action in ("tab_switch", "tab_new"):
        parts.append(f"已切换/新建标签页：{result.get('title','')}（{result.get('url','')}）")
    elif action in ("wait", "handle_dialog", "save_state", "load_state", "close", "download"):
        parts.append(f"{action} 完成")
    else:
        parts.append("操作完成")

    snap = result.get("snapshot")
    if snap:
        parts.append("--- 页面快照（用 ref 操作）---\n" + snap)
    return "\n".join(parts)


# ComfyUI 工作流路径（全部从配置读取，不写死本机路径；空值 = 未配置，调用处降级处理）
COMFYUI_WORKFLOW_PATH = _config.get("workflow_path", "")
COMFYUI_IMG2IMG_PATH = _config.get("img2img_path", "")
COMFYUI_PROMPT_TEMPLATE_PATH = _config.get("prompt_template_path", "")
COMFYUI_NSFW_EXAMPLE_PATH = _config.get("nsfw_example_path", "")
COMFYUI_PROMPT_NODE_ID = _config.get("prompt_node_id", "353")


def _fallback_prompt(desc: str) -> str:
    """兜底英文 prompt：当 AI 生成失败或含 CJK 时使用"""
    safe = re.sub(r'[\u4e00-\u9fff]+', '', desc).strip()
    if not safe:
        safe = "1girl, solo"
    # 构建 30+ tag 的兜底模板（保证满足标签数要求）
    base_parts = [
        safe,
        "masterpiece, best quality, very aesthetic, absurdres, highres",
        "detailed face, detailed eyes, detailed background",
        "solo, looking at viewer",
        "long hair, medium breasts, pale skin",
        "school uniform, skirt, thighs",
        "blush, smile",
        "from above, close-up",
        "bedroom, bed, night",
    ]
    base = ", ".join(base_parts)
    return base[:1500]


def _inject_nsfw_tags(tags: list, seen: set) -> list:
    """NSFW 场景补全：按模板槽位补全缺失的体位/镜头/表情/细节标签"""
    _nsfw_tag_groups = {
        # pose 补全（每个类别补2个标签，更具体）
        "pose": ["lying on back", "spread legs",
                 "on back", "arched back",
                 "cowgirl position", "from behind",
                 "missionary", "hand between legs",
                 "fingering", "sitting on lap"],
        # camera
        "camera": ["from above", "close-up", "top-down view",
                    "x-ray", "transparent abdomen", "head out of frame",
                    "cowboy shot", "low angle", "from front"],
        # expression
        "expression": ["blush", "ahegao", "tongue out", "closed eyes",
                       "expressionless", "half-closed eyes", "relaxed face",
                       "trembling", "moaning", "biting lip"],
        # detail（更丰富的细节标签）
        "detail": ["cum overflow", "pussy juice", "pussy visible",
                   "sweat", "messy hair", "heavy breathing",
                   "stomach bulge", "cum drip", "drooling",
                   "wet", "mound"],
    }
    _nsfw_has = {
        "pose": bool(set(["lying", "back", "legs", "spread", "cowgirl",
                          "on back", "arched back", "sitting", "kneeling",
                          "missionary", "doggy", "face down", "from behind",
                          "hand between", "fingering", "sitting on"]) & seen),
        "camera": bool(set(["from above", "close-up", "top-down", "from front",
                            "cowboy shot", "low angle", "x-ray", "transparent",
                            "head out of frame", "from behind"]) & seen),
        "expression": bool(set(["blush", "ahegao", "tongue out", "closed eyes",
                                "expressionless", "smile", "trembling", "sweat",
                                "half-closed", "relaxed", "moaning", "biting"]) & seen),
        "detail": bool(set(["cum", "pussy", "sweat", "messy", "breathing",
                            "drooling", "stomach bulge", "wet", "mound",
                            "drip", "overflow"]) & seen),
    }
    # 对于缺失的每个类别，补入多个未被使用的标签
    for group, candidate_tags in _nsfw_tag_groups.items():
        added = 0
        for tag in candidate_tags:
            if tag.lower() not in seen and added < 2:
                tags.append(tag)
                seen.add(tag.lower())
                log(f"[画图] NSFW 补全 [{group}]: +{tag}")
                added += 1
    return tags


def _ensure_character_tag(prompt: str, char_name: str, desc: str) -> str:
    """确保 prompt 包含角色名+作品名标签，且 solo 在前 3 个 tag 中"""
    tags = [t.strip() for t in prompt.split(",") if t.strip()]
    if not tags:
        return prompt

    seen = set(t.lower() for t in tags)

    # 1. 确保角色名在 prompt 中
    if char_name:
        # 尝试从搜索资料/描述中找到作品名
        series = ""
        _series_map = [
            ("endfield", "arknights: endfield"), ("终末地", "arknights: endfield"),
            ("arknights", "arknights"), ("明日方舟", "arknights"),
            ("genshin", "genshin impact"), ("原神", "genshin impact"),
            ("honkai", "honkai"), ("崩坏", "honkai"),
            ("azur lane", "azur lane"), ("碧蓝航线", "azur lane"),
            ("girls frontline", "girls frontline"), ("少女前线", "girls frontline"),
            ("孤独摇滚", "bocchi the rock"), ("bocchi", "bocchi the rock"),
            ("进击的巨人", "attack on titan"), ("鬼灭之刃", "demon slayer"),
            ("咒术回战", "jujutsu kaisen"), ("间谍过家家", "spy x family"),
        ]
        desc_lower = desc.lower()
        for key, val in _series_map:
            if key.lower() in desc_lower:
                series = val
                break
        char_tag = f"{char_name} ({series})" if series else char_name
        char_tag_lower = char_tag.lower()
        if not any(char_name.lower() in t.lower() for t in tags):
            # 插入到 solo 后面
            insert_pos = 0
            for i, t in enumerate(tags):
                if t.lower() in ("solo", "1girl", "1boy"):
                    insert_pos = i + 1
                    break
            tags.insert(insert_pos, char_tag)
            log(f"[画图] 补入角色标签: {char_tag}")

    # 2. 确保 solo 在前 3 个 tag
    solo_idx = None
    for i, t in enumerate(tags):
        if t.lower() in ("solo", "1girl", "1boy", "2girls", "2boys"):
            solo_idx = i
            break
    if solo_idx is not None and solo_idx > 3:
        solo_tag = tags.pop(solo_idx)
        tags.insert(1, solo_tag)  # 紧跟在 count/gender 后
        log(f"[画图] solo 从位置 {solo_idx} 移到位置 1")

    return ", ".join(tags)


def _validate_and_pad_prompt(prompt: str, desc: str = "") -> str:
    """校验 prompt 质量并补全到 30+ 标签，NSFW 场景自动补全缺失标签"""
    prompt = prompt.strip()
    # 按逗号拆分标签
    tags = [t.strip() for t in prompt.split(",") if t.strip()]
    # 过滤含中文的 tag，但保留角色名 tag（如 "刻俄柏 (arknights)"）
    clean_tags = []
    for t in tags:
        if re.search(r'[\u4e00-\u9fff]', t):
            # 含中文的 tag：如果同时含括号+英文（角色名格式），保留
            if re.search(r'\(.*[a-zA-Z]', t):
                clean_tags.append(t)
            # 否则跳过（AI 混入的中文思考片段）
        else:
            clean_tags.append(t)
    # 去重（大小写不敏感）
    seen = set()
    unique_tags = []
    for t in clean_tags:
        key = t.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique_tags.append(t.strip())
    # NSFW 后处理：检测到 NSFW 意图时，补全缺失的 NSFW 标签
    _nsfw_keywords = re.compile(r'(色色|涩涩|色气|做爱|自慰|手淫|hentai|nsfw|r18|肉|本子|h\s*图|色图|裸体|裸照|色情)', re.IGNORECASE)
    is_nsfw = bool(_nsfw_keywords.search(desc))
    if is_nsfw:
        unique_tags = _inject_nsfw_tags(unique_tags, seen)
    log(f"[画图] prompt 校验: {len(unique_tags)} 个标签 (AI原始: {len(tags)}, 过滤中文: {len(tags) - len(clean_tags)})")
    # 补全到 30 个
    quality_pool = [
        "masterpiece", "best quality", "very aesthetic", "absurdres",
        "highres", "detailed background", "detailed face", "detailed eyes",
        "solo", "looking at viewer",
    ]
    while len(unique_tags) < 30 and quality_pool:
        q = quality_pool.pop(0)
        if q.lower() not in seen:
            unique_tags.append(q)
            seen.add(q.lower())
    # 如果还不够，补通用质量 tag
    extras = ["detailed hands", "detailed hair", "detailed skin", "soft lighting",
              "depth of field", "bokeh", "cinematic lighting", "sharp focus"]
    while len(unique_tags) < 30 and extras:
        e = extras.pop(0)
        if e.lower() not in seen:
            unique_tags.append(e)
            seen.add(e.lower())
    result = ", ".join(unique_tags)
    return result[:2000]


def _extract_moegirl_infobox(text: str, char_name: str = "") -> str:
    """从萌娘百科页面文本中提取结构化角色信息"""
    fields = {}
    # 提取 infobox 字段（格式：字段名 空格 值）
    field_patterns = [
        ('发色', r'发色\s+(\S+)'),
        ('瞳色', r'瞳色\s+(\S+)'),
        ('种族', r'种族\s+(\S+)'),
        ('阵营', r'阵营\s+(\S+)'),
        ('性别', r'性别\s+(\S+)'),
        ('武器', r'武器\s+(\S+)'),
        ('职业', r'职业\s+(\S+)'),
        ('身高', r'身高\s+(\S+)'),
        ('体重', r'体重\s+(\S+)'),
        ('萌点', r'萌点\s+(.+?)(?=\s*(?:相关人士|入场动画|简介|角色经历|\Z))'),
        ('别号', r'别号\s+(.+?)(?=\s*(?:性别|发色|生日|\Z))'),
    ]
    for field, pattern in field_patterns:
        m = re.search(pattern, text)
        if m:
            val = m.group(1).strip()
            # 清理 HTML 残留
            val = re.sub(r'\[.*?\]', '', val).strip()
            val = re.sub(r'\s+', ' ', val)
            if val and len(val) < 200:
                fields[field] = val

    # 提取角色简介（前 500 字）
    intro = ""
    intro_match = re.search(r'(简介|人物简介|角色简介)\s*(.{200,800}?)(?:角色经历|干员台词|档案)', text)
    if intro_match:
        intro = re.sub(r'\[.*?\]', '', intro_match.group(2)).strip()
        intro = re.sub(r'\s+', ' ', intro)[:500]

    # 组装结构化摘要
    parts = []
    if char_name:
        parts.append(f"【角色】{char_name}")
    for f in ['性别', '种族', '发色', '瞳色', '武器', '职业', '阵营', '萌点']:
        if f in fields:
            parts.append(f"【{f}】{fields[f]}")
    if intro:
        parts.append(f"【简介】{intro}")

    if not parts:
        return ""
    return "\n".join(parts)


def _generate_english_prompt(user_description, runtime_cfg=None):
    """调用 AI 将用户描述转化为英文 prompt，完整使用提示词模板"""
    if runtime_cfg is None:
        runtime_cfg = _config

    # 生图提示词 LLM 的本地/远程判定用真正的开关 use_local_image_llm，
    # 不再用遗留字段 llm_model=="local"（旧 AppData 配置残留会把生图 prompt 悄悄切到本地 llama）
    use_local = bool(runtime_cfg.get("use_local_image_llm"))
    image_use_local = use_local
    image_driver = (runtime_cfg.get("image_llm_driver") or "llama-server").strip().lower()
    image_exe_path = (runtime_cfg.get("image_llm_exe_path") or "").strip()
    image_model_path = (runtime_cfg.get("image_llm_model_path") or "").strip()
    image_port = int(runtime_cfg.get("image_llm_port") or 8081)
    image_model_name = (runtime_cfg.get("image_llm_model_name") or "qwen3.6").strip()
    log(f"[画图] 生图提示词本地={image_use_local}")

    # 清洗用户描述：去掉所有噪声
    clean_desc = user_description
    # 去掉 [引用:u_xxx] 模式（带 UID 的引用）
    clean_desc = re.sub(r'\[引用[^\]]*\]', '', clean_desc)
    # 去掉 [表情码] 如 [占位名片B]
    clean_desc = re.sub(r'\[[\u0000-\u007f\u4e00-\u9fff\uff00-\uffef]{1,20}\]', '', clean_desc)
    # 去掉 @Bot 昵称
    clean_desc = re.sub(r'@占位名片A\s*', '', clean_desc)
    clean_desc = re.sub(r'@小a\s*', '', clean_desc)
    # 去掉引号包裹
    clean_desc = re.sub(r'^"(.+)"$', r'\1', clean_desc)
    clean_desc = clean_desc.strip()

    # 提取角色名（智能提取，不依赖硬编码作品名列表）
    action_words = r'(的图片|的图|做爱|自慰|画|给我|帮我|来一?张|生成|出图|画个|画只|画张|一张|生辰|生日|做个|做|可爱的?|漂亮的?|好看的?|一点|一张)'
    desc_no_action = re.sub(action_words, ' ', clean_desc).strip()
    # 去掉常见量词/代词前缀
    desc_no_action = re.sub(r'^(一个?|那一个?|那个|这个)\s*', '', desc_no_action).strip()
    # 去掉尾部"的图片/的图/图片/图"
    desc_no_action = re.sub(r'(?:的图片|的图|图片|图)\s*$', '', desc_no_action).strip()
    # 去掉尾部 NSFW 后缀
    _nsfw_suffix_re = re.compile(r'(色色|涩涩|色气|h+hentai|nsfw|r18|肉|本子)$', re.IGNORECASE)
    desc_no_action = _nsfw_suffix_re.sub("", desc_no_action).strip()

    # ── 智能提取策略 ──
    char_name = ""

    # 策略1: "XXX里的YYY" / "XXX中的YYY" 模式 → YYY 是角色名
    m_sep = re.search(r'里的\s*([\u4e00-\u9fa5]{2,6})', desc_no_action)
    if not m_sep:
        m_sep = re.search(r'中的\s*([\u4e00-\u9fa5]{2,6})', desc_no_action)
    if m_sep:
        char_name = m_sep.group(1)
        log(f"[画图] 策略1(里/中的后): {char_name}")

    # 策略2: 英文名（多个英文词时取最后一个，如 "Fate SABER" → "SABER"）
    if not char_name:
        en_matches = re.findall(r'([a-zA-Z][a-zA-Z0-9\-]{1,20})', desc_no_action)
        if en_matches:
            char_name = en_matches[-1]  # 取最后一个英文词
            log(f"[画图] 策略2(英文): {char_name}")

    # 策略3: 去掉已知作品名后，取最后 2-4 个中文字（角色名通常在末尾）
    if not char_name:
        _game_prefixes = re.compile(
            r'(明日方舟|终末地|原神|崩坏[：:]星穹铁道|崩坏3|崩坏学园|碧蓝航线|碧蓝档案|'
            r'少女前线|战双帕弥什|Fate|东方|宝可梦|英雄联盟|王者荣耀|'
            r'孤独摇滚|进击的巨人|鬼灭之刃|咒术回战|间谍过家家|我的英雄学院|莉可丽丝|'
            r'方舟|终末|铁道|碧蓝|少女前|战双|'
            r'arknights|endfield|genshin|honkai|azur lane|girls frontline|bocchi)',
            re.IGNORECASE
        )
        temp = desc_no_action
        for _ in range(3):
            new = _game_prefixes.sub("", temp).strip()
            if new == temp:
                break
            temp = new
        # 去掉残留的"的"前缀
        temp = re.sub(r'^的\s*', '', temp).strip()
        # 取中文字符，角色名通常 2-4 字且在末尾
        cn_chars = re.findall(r'[\u4e00-\u9fa5]', temp)
        if 2 <= len(cn_chars) <= 6:
            # 2-4字直接全取，5-6字取最后4字
            if len(cn_chars) <= 4:
                char_name = ''.join(cn_chars)
            else:
                char_name = ''.join(cn_chars[-4:])
            log(f"[画图] 策略3(末尾中文): {char_name}")

    # 二次清洗：确保尾部无 NSFW 后缀
    char_name = _nsfw_suffix_re.sub("", char_name).strip()
    log(f"[画图] 清洗后描述: {clean_desc}, 提取角色名: {char_name}")

    # 联网搜索角色外观信息（多查询策略，提高准确率和细节程度）
    character_info = ""
    try:
        # 优先：直接探测萌娘百科 URL（比 Bing 搜索更快更准）
        moegirl_text = ""
        search_name = char_name or clean_desc
        try:
            # 构造萌娘百科直接 URL（带重试）
            encoded_name = urllib.parse.quote(search_name, safe='')
            direct_url = f"https://zh.moegirl.org.cn/{encoded_name}"
            log(f"[画图] 直接探测萌娘百科: {direct_url}")
            for attempt in range(2):
                try:
                    page_req = urllib.request.Request(direct_url, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    })
                    with urllib.request.urlopen(page_req, timeout=20, context=CTX) as pr:
                        page = pr.read().decode("utf-8", errors="ignore")
                    text = re.sub(r'<[^>]+>', ' ', page)
                    text = re.sub(r'&ensp;|&nbsp;|&#\d+;', ' ', text)
                    text = re.sub(r'\s+', ' ', text).strip()
                    if text and len(text) > 500:
                        structured = _extract_moegirl_infobox(text, search_name)
                        if structured:
                            moegirl_text = structured
                            log(f"[画图] 萌娘百科成功: 提取结构化信息 {len(moegirl_text)} 字符")
                            break
                        elif any(kw in text[:5000] for kw in ['发色', '瞳色', '萌点', '性别', '生日', '声优']):
                            moegirl_text = text[:15000]
                            log(f"[画图] 萌娘百科成功(原始): {len(moegirl_text)} 字符")
                            break
                except Exception as e:
                    if attempt == 0:
                        log(f"[画图] 萌娘百科第1次失败: {e}，重试...")
                    else:
                        log(f"[画图] 萌娘百科重试也失败: {e}")
        except Exception as e:
            log(f"[画图] 直接访问萌娘百科失败: {e}")

        # 第二优先：Bing 搜索萌娘百科（直接探测失败时）
        if not moegirl_text:
            for q in [search_name, f"{search_name} 角色", f"{search_name} 外貌 特征", f"{search_name} 设定"]:
                try:
                    search_query = urllib.parse.quote(f"{q} site:moegirl.org.cn")
                    url = f"https://www.bing.com/search?q={search_query}&setlang=zh-cn"
                    req = urllib.request.Request(url, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    })
                    with urllib.request.urlopen(req, timeout=10, context=CTX) as r:
                        html = r.read().decode("utf-8", errors="ignore")
                    # 提取萌娘百科链接
                    for m in re.finditer(r'href="(https://moegirl\.org\.cn/[^"]+)"[^>]*>([^<]+)</a>', html):
                        title = m.group(2).strip()
                        if search_name.lower() in title.lower() or q.split()[0].lower() in title.lower():
                            page_url = m.group(1)
                            log(f"[画图] Bing 找到萌娘百科: {title} -> {page_url}")
                            try:
                                page_req = urllib.request.Request(page_url, headers={
                                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                                })
                                with urllib.request.urlopen(page_req, timeout=15, context=CTX) as pr:
                                    page = pr.read().decode("utf-8", errors="ignore")
                                text = re.sub(r'<[^>]+>', ' ', page)
                                text = re.sub(r'&ensp;|&nbsp;|&#\d+;', ' ', text)
                                text = re.sub(r'\s+', ' ', text).strip()
                                moegirl_text = text[:15000]
                                break
                            except Exception as e:
                                log(f"[画图] 抓取萌娘百科页面失败: {e}")
                    if moegirl_text:
                        break
                except Exception as e:
                    log(f"[画图] 萌娘百科搜索失败: {e}")

        # 跳过多源 Bing 搜索（返回无关页面，浪费时间）
        # 萌娘百科 + 百度百科 + Danbooru 足够提供角色信息

        # 百度百科搜索：直接探测 URL（比 Bing 搜索更可靠）
        baike_text = ""
        try:
            encoded_bk = urllib.parse.quote(search_name, safe='')
            bk_urls = [
                f"https://baike.baidu.com/item/{encoded_bk}",
                f"https://wapbaike.baidu.com/item/{encoded_bk}",
            ]
            for bk_url in bk_urls:
                try:
                    log(f"[画图] 直接探测百度百科: {bk_url}")
                    req_bk = urllib.request.Request(bk_url, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    })
                    with urllib.request.urlopen(req_bk, timeout=10, context=CTX) as r_bk:
                        bk_page = r_bk.read().decode("utf-8", errors="ignore")
                    bk_text = re.sub(r'<[^>]+>', ' ', bk_page)
                    bk_text = re.sub(r'&ensp;|&nbsp;|&#\d+;', ' ', bk_text)
                    bk_text = re.sub(r'\s+', ' ', bk_text).strip()
                    # 验证：页面必须包含角色名
                    if search_name in bk_text[:5000] and len(bk_text) > 500:
                        # 提取形象设定部分
                        for kw in ['外貌', '形象', '外观', '造型', '服装', '发型', '发色']:
                            ki = bk_text.find(kw)
                            if ki >= 0:
                                baike_text = bk_text[max(0, ki-50):ki+3000]
                                log(f"[画图] 百度百科提取到形象信息: {kw}, {len(baike_text)} 字符")
                                break
                        if not baike_text:
                            baike_text = bk_text[:3000]
                            log(f"[画图] 百度百科抓取成功: {len(baike_text)} 字符")
                        break
                    else:
                        log(f"[画图] 百度百科页面不匹配（{len(bk_text)} 字符），尝试下一个 URL")
                except Exception as e:
                    log(f"[画图] 百度百科 {bk_url[:40]} 失败: {e}")
                    continue
        except Exception as e:
            log(f"[画图] 百度百科搜索失败: {e}")

        # （booru 搜索已禁用：网络不可达，全部超时，浪费 30+ 秒）
        # 如需启用，在海外网络环境下恢复 Danbooru/Gelbooru/Safebooru 搜索

        # 合并：萌娘百科 + 百度百科
        if moegirl_text:
            character_info = f"【萌娘百科角色资料】\n{moegirl_text}"
        if baike_text:
            character_info = (character_info + "\n\n【百度百科形象设定】\n" + baike_text) if character_info else baike_text
        # 限制总长度
        if len(character_info) > 5000:
            character_info = character_info[:5000] + "\n[资料已截断]"
        if character_info:
            log(f"[画图] 搜索成功，共 {len(character_info)} 字符")
            log(f"[画图] 角色信息前300字: {character_info[:300]}")
        if not character_info:
            log("[画图] 搜索无有效结果，使用原始描述")
    except Exception as e:
        log(f"[画图] 搜索失败: {e}")

    # 读取完整提示词模板（从配置读取；优先 _trimmed 精简版，截断以适应上下文窗口）
    base_tpl = (COMFYUI_PROMPT_TEMPLATE_PATH or "").strip().strip('"')
    template_path = ""
    template_content = ""
    if base_tpl:
        try:
            stem, ext = os.path.splitext(base_tpl)
            for cand in (stem + "_trimmed" + ext, base_tpl):
                if os.path.exists(cand):
                    template_path = cand
                    break
        except Exception:
            if os.path.exists(base_tpl):
                template_path = base_tpl
    if template_path:
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                template_content = f.read()
            log(f"[画图] 加载提示词模板: {len(template_content)} 字符")
            if use_local and len(template_content) > 70000:
                template_content = template_content[:70000] + "\n\n[模板已截断]"
        except Exception as e:
            log(f"读取提示词模板失败: {e}")

    # 判断是否 NSFW 请求
    _nsfw_detect = re.compile(r'(色色|涩涩|色气|做爱|自慰|手淫|hentai|nsfw|r18|肉|本子|色图|裸体|裸照|色情)', re.IGNORECASE)
    is_nsfw_request = bool(_nsfw_detect.search(clean_desc))

    nsfw_note = "\n\nNSFW内容完全允许，按模板规则描述，不要自我审查。" if is_nsfw_request and runtime_cfg.get("nsfw_enabled", True) else ""

    # 仅 NSFW 请求时加载示例
    example_prompts = ""
    if is_nsfw_request:
        example_path = (COMFYUI_NSFW_EXAMPLE_PATH or "").strip().strip('"')
        if not example_path or not os.path.exists(example_path):
            log("[画图] 未配置 NSFW 示例路径(nsfw_example_path)，跳过示例")
        else:
            try:
                with open(example_path, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                prompts = [p.strip() for p in raw.split("\n\n") if p.strip()]
                if prompts:
                    example_prompts = "\n\n【参考示例】\n以下是符合要求的 NSFW prompt 示例。注意它们的结构：count/gender → character/series → appearance → clothing → pose/action → expression → camera → scene → 自然语言补充\n" + "\n---\n".join(prompts[:4])
                    log(f"[画图] NSFW 请求，加载 {len(prompts[:4])} 个示例")
            except Exception as e:
                log(f"读取示例文件失败: {e}")

    system_prompt = f"""你是 Anima3 模型的英文 prompt 生成器。

输出格式：只输出一个 JSON 对象，不要输出任何其他文字。JSON 结构：
{{
  "count": "1girl 或 1boy 或 2girls 等",
  "character": "角色名 (作品名)",
  "appearance": "外貌标签，逗号分隔，如 black hair, twintails, brown eyes, slim, pale skin",
  "clothing": "服装标签，逗号分隔，至少3个：类型+穿着状态+配饰+内衣",
  "pose": "动作/体位标签，逗号分隔，2-3个具体标签",
  "expression": "表情标签，逗号分隔，2-3个",
  "camera": "镜头标签，逗号分隔",
  "scene": "场景标签，逗号分隔",
  "detail": "细节/效果标签，逗号分隔",
  "natural_language": "1句英文自然语言描述场景氛围"
}}

规则：
- 所有标签用英文小写，逗号+空格分隔
- 禁止输出权重语法如 (tag:1.2)
- 禁止输出质量词（masterpiece 等由脚本自动添加）
- 禁止输出画师名
- 禁止输出光线/光影标签（如 sunlight/warm lighting）
- 【关键】如果用户描述中没有明确的色情/NSFW关键词（色色/涩涩/hentai/nsfw/做爱/自慰等），绝对不要生成任何色情内容。pose 用正常动作（standing/sitting/walking等），expression 用正常表情（smile/determined等）
- 只有用户明确要求 NSFW 时才使用 NSFW 标签
- 角色名从搜索资料中提取，必须包含角色名和作品名
- 服装必须详细：类型+穿着状态+配饰（至少3个标签）
- natural_language 是 1 句英文，描述场景氛围
{nsfw_note}
{example_prompts}"""

    # ── 标签库（分 SFW / NSFW 两套，按需注入） ──
    if is_nsfw_request:
        tag_library = """
【标签库 - NSFW 场景专用】
动作(pose): lying on back, spread legs, on back, arched back, cowgirl position, missionary, from behind, hand between legs, fingering, sitting on lap
表情(expression): blush, ahegao, tongue out, closed eyes, half-closed eyes, trembling, moaning, biting lip, drooling
镜头(camera): from above, close-up, top-down view, x-ray, transparent abdomen, head out of frame
场景(scene): bedroom, bed, night, dark
细节(detail): cum overflow, pussy juice, pussy visible, sweat, messy hair, stomach bulge, cum drip, wet"""
    else:
        tag_library = """
【标签库 - SFW 场景】
动作(pose): standing, sitting, walking, running, jumping, leaning, hand on hip, arms crossed, peace sign, waving, holding weapon, fighting stance
表情(expression): smile, grin, serious, determined, shy, embarrassed, happy, angry, sad, surprised, laughing, wink
镜头(camera): from above, close-up, full body, upper body, from front, from side, dynamic angle, portrait
场景(scene): classroom, school, office, outdoor, city, forest, beach, sunset, rain, battlefield, ruins, room, cafe, park
细节(detail): wind blowing hair, cherry blossoms, snow, sparkles, light rays, dramatic lighting"""

    messages = [
        {"role": "system", "content": system_prompt + tag_library},
        {"role": "user", "content": f"用户原话：{clean_desc}\n\n搜索资料：{character_info}\n\n请直接输出 JSON，不要其他文字。"}
    ]

    try:
        # 远端生图提示词 LLM：统一走 _llm_config（每提供商专属 key/url/model，不再写死 DeepSeek）
        _ilc = _llm_config(runtime_cfg)
        _remote_url = _ilc.get("api_url") or DEEPSEEK_URL
        _remote_key = _ilc.get("api_key") or ""
        _remote_model = _ilc.get("model_name") or "deepseek-flash"
        if image_use_local:
            if not _ensure_llama_server(image_exe_path, image_model_path, image_port):
                raise RuntimeError("生图本地模型启动失败")
            api_url = f"http://127.0.0.1:{image_port}/v1/chat/completions"
            model_name = image_model_name or "qwen3.6"
            headers = {"Content-Type": "application/json"}
        else:
            api_url = _remote_url
            model_name = _remote_model
            headers = {"Content-Type": "application/json"}
            if _remote_key:
                headers["Authorization"] = f"Bearer {_remote_key}"
        req_body = {"model": model_name, "messages": messages, "max_tokens": 1500, "temperature": 0.7, "stream": False}

        resp = None
        last_err = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(api_url, data=json.dumps(req_body).encode("utf-8"), headers=headers)
                with urllib.request.urlopen(req, timeout=120, context=CTX) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                    break
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                last_err = f"HTTP {e.code}: {body[:300] if body else str(e)}"
                if e.code in (429, 502, 503, 504):
                    wait = 2 ** attempt
                    log(f"[画图] API 限流/过载 ({e.code})，{wait}s 后重试 ({attempt+1}/3)")
                    time.sleep(wait)
                    continue
                raise
            except urllib.error.URLError as e:
                last_err = str(e)
                wait = 2 ** attempt
                log(f"[画图] 网络错误: {last_err}，{wait}s 后重试 ({attempt+1}/3)")
                time.sleep(wait)

        if resp is None:
            raise RuntimeError(last_err or "API 请求失败")

        message = resp.get("choices", [{}])[0].get("message", {})
        content = message.get("content", "").strip()
        reasoning = message.get("reasoning_content", "").strip()
        
        log(f"[画图] content: {content[:300]}...")
        if reasoning:
            log(f"[画图] reasoning: {reasoning[:200]}...")
        
        # 尝试从 content 或 reasoning 中解析 JSON
        reply = ""
        for source in [content, reasoning]:
            if not source:
                continue
            # 提取 JSON 部分（可能被 ```json 包裹）
            json_str = source
            json_match = re.search(r'\{[^{}]*"count"[^{}]*\}', source, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            try:
                data = json.loads(json_str)
                if "count" in data or "appearance" in data:
                    # 成功解析 JSON，组装 prompt
                    parts = []
                    parts.append(data.get("count", "1girl"))
                    if data.get("solo"):
                        parts.append("solo")
                    if data.get("character"):
                        parts.append(data["character"])
                    if data.get("appearance"):
                        parts.append(data["appearance"])
                    if data.get("clothing"):
                        parts.append(data["clothing"])
                    if data.get("pose"):
                        parts.append(data["pose"])
                    if data.get("expression"):
                        parts.append(data["expression"])
                    if data.get("camera"):
                        parts.append(data["camera"])
                    if data.get("scene"):
                        parts.append(data["scene"])
                    if data.get("detail"):
                        parts.append(data["detail"])
                    # 质量词
                    parts.append("masterpiece, best quality, very aesthetic, absurdres, highres")
                    if data.get("natural_language"):
                        parts.append(data["natural_language"])
                    reply = ", ".join(p.strip() for p in parts if p.strip())
                    log(f"[画图] JSON 解析成功，组装 {len(reply.split(','))} 个标签")
                    break
            except (json.JSONDecodeError, KeyError):
                continue
        
        # JSON 解析失败，fallback 到文本解析
        if not reply:
            reply = content
            if reasoning:
                tag_start = -1
                for marker in ['1girl', '1boy', '2girls', '2boys']:
                    idx = reasoning.find(marker)
                    if idx >= 0:
                        tag_start = idx
                        break
                if tag_start >= 0:
                    reply = reasoning[tag_start:].strip()
                elif content and len(content) > 10:
                    reply = content
            reply = reply.replace("```", "").replace("**", "").strip()
            # 去掉尾部中文
            last_comma = reply.rfind(",")
            if last_comma > 0:
                tail = reply[last_comma+1:].strip()
                if not re.match(r'^[a-z_ ]+$', tail):
                    reply = reply[:last_comma].strip()
            for p in ["输出：", "输出:", "Output:", "output:", "prompt：", "prompt:", "final prompt:", "Final prompt:"]:
                if reply.startswith(p):
                    reply = reply[len(p):].strip()
            log("[画图] Fallback 文本解析")
        
        # 校验并补全 prompt
        log(f"[画图] 补全前 prompt: {reply[:200]}...")
        reply = _validate_and_pad_prompt(reply, clean_desc)
        # 确保角色名在 prompt 中 + solo 在前 3 位
        if char_name:
            log(f"[画图] 角色名: {char_name}, 当前 prompt: {reply[:150]}...")
            reply = _ensure_character_tag(reply, char_name, clean_desc)
            log(f"[画图] 补入后 prompt: {reply[:150]}...")
        log(f"[画图] AI prompt 最终: {len(reply)} 字符, {len(reply.split(','))} 标签")
        # 安全检查：含中文则逐 tag 过滤
        if re.search(r'[\u4e00-\u9fff]', reply):
            parts = [t.strip() for t in reply.split(",")]
            safe_parts = [t for t in parts if not re.search(r'[\u4e00-\u9fff]', t)]
            reply = ", ".join(safe_parts)
            reply = _validate_and_pad_prompt(reply, clean_desc)
            log(f"[画图] 中文过滤后: {len(reply.split(','))} 标签")
        return reply
    except Exception as e:
        log(f"AI prompt 生成失败: {e}")
        return _fallback_prompt(user_description)


# ═══ llama-server 自动管理 ═══
_LLAMA_SERVER_EXE = ""
_LLAMA_MODEL = ""
_LLAMA_PORT = 8081
_llama_process = None
_llama_last_used = 0  # 上次使用时间戳，用于空闲自动关闭
_LLAMA_IDLE_TIMEOUT = 300  # 空闲 5 分钟自动关闭
_llama_instances = {}  # key -> process, 用于多实例隔离
_llama_instance_last_used = {}  # key -> timestamp


def _llama_instance_key(exe_path, model_path, port):
    return f"{os.path.normcase(os.path.normpath(exe_path))}|{os.path.normcase(os.path.normpath(model_path))}|{port}"


def _llama_idle_watchdog():
    """后台守护线程：llama-server 空闲超时自动关闭"""
    while True:
        time.sleep(60)
        now = time.time()
        dead_keys = []
        for key, last_used in list(_llama_instance_last_used.items()):
            proc = _llama_instances.get(key)
            if proc and proc.poll() is None and last_used > 0:
                idle = now - last_used
                if idle > _LLAMA_IDLE_TIMEOUT:
                    log(f"[画图] llama-server 空闲 {int(idle)}s，自动关闭释放显存: {key}")
                    try:
                        proc.terminate()
                        proc.wait(timeout=10)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    dead_keys.append(key)
        for key in dead_keys:
            _llama_instances.pop(key, None)
            _llama_instance_last_used.pop(key, None)


def _ensure_llama_server(exe_path=None, model_path=None, port=None):
    """确保 llama-server 在运行，未运行则自动启动，空闲 5 分钟自动关闭"""
    exe_path = exe_path or _LLAMA_SERVER_EXE
    model_path = model_path or _LLAMA_MODEL
    port = int(port or _LLAMA_PORT)
    key = _llama_instance_key(exe_path, model_path, port)
    proc = _llama_instances.get(key)

    # 启动空闲守护线程（只启动一次）
    if not getattr(_ensure_llama_server, "_watchdog_started", False):
        threading.Thread(target=_llama_idle_watchdog, daemon=True, name="llama-idle-watchdog").start()
        _ensure_llama_server._watchdog_started = True

    # 检查是否已在运行（检查模型是否就绪，不只是端口）
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                _llama_instance_last_used[key] = time.time()
                log(f"[画图] llama-server 已就绪: {key}")
                return True
    except Exception:
        pass

    if proc and proc.poll() is not None:
        _llama_instances.pop(key, None)
        _llama_instance_last_used.pop(key, None)
        proc = None

    if not os.path.exists(exe_path):
        log(f"[画图] llama-server 不存在: {exe_path}")
        return False
    if not os.path.exists(model_path):
        log(f"[画图] 模型不存在: {model_path}")
        return False

    log(f"[画图] 启动 llama-server...: {key}")
    try:
        proc = subprocess.Popen(
            [exe_path, "--model", model_path, "--host", "127.0.0.1",
             "--port", str(port), "-ngl", "999", "--cpu-moe", "--no-mmap", "--ctx-size", "32768"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        _llama_instances[key] = proc
        # 等待模型就绪（轮询 /health，最多 120 秒）
        for i in range(120):
            time.sleep(1)
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        _llama_instance_last_used[key] = time.time()
                        log(f"[画图] llama-server 启动成功（{i+1}s）: {key}")
                        return True
            except Exception:
                pass
        log(f"[画图] llama-server 启动超时: {key}")
        return False
    except Exception as e:
        log(f"[画图] llama-server 启动失败: {e}: {key}")
        return False




def handle_generate_image(prompt, style="default", user_ref_image=None):
    """处理图片生成请求"""
    global _LLAMA_SERVER_EXE, _LLAMA_MODEL, _LLAMA_PORT
    log(f"[画图] 开始处理: {prompt}")

    # 实时读取配置（GUI可能已更新）
    try:
        runtime_cfg = json.load(open(_APPDATA_CONFIG, "r", encoding="utf-8-sig"))
    except:
        runtime_cfg = _config

    try:
        from comfyui_client import (
            get_comfyui_client, load_workflow, set_prompt_in_workflow,
            ComfyUIClient
        )
    except ImportError:
        return "图片生成模块未找到"
    
    comfyui_host = _config.get("comfyui_host") or "127.0.0.1"
    comfyui_port = _config.get("comfyui_port") or 8000
    log(f"[画图] 连接 ComfyUI: {comfyui_host}:{comfyui_port}")

    client = ComfyUIClient(str(comfyui_host), int(comfyui_port), timeout=120)
    if not client.is_connected():
        return "ComfyUI 未连接，请检查远程服务器"
    
    try:
        # 生图提示词 LLM 的本地判定：统一用 use_local_image_llm（不再读遗留 llm_model=="local"）
        use_local = bool(runtime_cfg.get("use_local_image_llm"))
        log(f"[画图] 生图提示词本地={use_local}")

        # 本地模型自动启停
        if use_local:
            exe_path = (runtime_cfg.get("llama_exe_path") or "").strip() or _LLAMA_SERVER_EXE
            model_path = (runtime_cfg.get("llama_model_path") or "").strip() or _LLAMA_MODEL
            port = int(runtime_cfg.get("llama_port") or _LLAMA_PORT)
            _LLAMA_SERVER_EXE = exe_path
            _LLAMA_MODEL = model_path
            _LLAMA_PORT = port
            if not _ensure_llama_server(exe_path, model_path, port):
                log("[画图] llama-server 启动失败，回退 DeepSeek")
                use_local = False

        # 1. 将用户描述转化为英文 prompt
        english_prompt = _generate_english_prompt(prompt, runtime_cfg)
        log(f"生成的 prompt: {english_prompt}")
        # 二次校验：禁止 CJK 泄漏到 ComfyUI
        if re.search(r'[\u4e00-\u9fff]', english_prompt):
            log("[画图] 严重警告：prompt 仍含中文，强制降级")
            english_prompt = _fallback_prompt(prompt)
            log(f"[画图] 降级后 prompt: {english_prompt[:200]}...")
        
        # 1.5 搜索参考图（优先用萌娘百科已下载的，再搜 booru/Bing）
        char_name = getattr(_generate_english_prompt, '_last_char_name', '')
        # 图生图/文生图判断：有用户图片 → 图生图，无 → 文生图
        ref_image_path = user_ref_image if user_ref_image and os.path.exists(user_ref_image) else ""
        
        if ref_image_path:
            # ═══ 图生图模式（最简工作流，无自定义节点依赖） ═══
            log(f"[画图] 使用图生图模式，参考图: {ref_image_path}")
            uploaded_name = client.upload_image(ref_image_path)
            api_workflow = {
                "1": {"class_type": "LoadImage", "inputs": {"image": uploaded_name, "upload": "image"}},
                "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "anima_baseV10.safetensors", "weight_dtype": "default"}},
                "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_06b_base.safetensors", "type": "stable_diffusion", "device": "default"}},
                "4": {"class_type": "CLIPTextEncode", "inputs": {"text": english_prompt, "clip": ["3", 0]}},
                "5": {"class_type": "CLIPTextEncode", "inputs": {"text": "lowres, bad anatomy, bad hands, text, error, worst quality, low quality, jpeg artifacts, signature, watermark", "clip": ["3", 0]}},
                "6": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
                "7": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["6", 0]}},
                "8": {"class_type": "KSampler", "inputs": {
                    "model": ["2", 0], "positive": ["4", 0], "negative": ["5", 0],
                    "latent_image": ["7", 0], "seed": 42, "steps": 30, "cfg": 5,
                    "sampler_name": "euler", "scheduler": "normal", "denoise": 0.35
                }},
                "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["6", 0]}},
                "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "EggBloom"}}
            }
            log("[画图] 图生图工作流: UNETLoader+CLIPLoader+VAEEncode+KSampler(denoise=0.35 保留面部/配色)")
        else:
            # ═══ 文生图模式 ═══
            log("[画图] 使用文生图模式（无参考图）")
            api_workflow = load_workflow(COMFYUI_WORKFLOW_PATH)
            api_workflow = set_prompt_in_workflow(api_workflow, english_prompt, COMFYUI_PROMPT_NODE_ID)

        # 3. 覆盖分辨率
        width = _config.get("comfyui_default_width")
        height = _config.get("comfyui_default_height")
        if width and height:
            for nid, node in api_workflow.items():
                if node.get("class_type") == "EmptyLatentImage":
                    node["inputs"]["width"] = int(width)
                    node["inputs"]["height"] = int(height)
                    log(f"[画图] EmptyLatentImage 尺寸覆盖: {width}x{height}")
        else:
            # 记录工作流默认尺寸
            for nid, node in api_workflow.items():
                if node.get("class_type") == "EmptyLatentImage":
                    log(f"[画图] EmptyLatentImage 使用工作流默认: {node['inputs'].get('width')}x{node['inputs'].get('height')}")
        
        # 4. 提交任务
        prompt_id = client.queue_prompt(api_workflow)
        log(f"ComfyUI 任务已提交: {prompt_id}")
        
        # 5. 等待结果
        images = client.wait_and_get_images(prompt_id, timeout=180)
        if images:
            log(f"图片生成成功: {images[0]}")
            return f"图片已生成: {images[0]}"
        else:
            return "图片生成失败"
    except TimeoutError:
        return "图片生成超时，请重试"
    except Exception as e:
        log(f"图片生成错误: {e}")
        return f"图片生成失败: {str(e)}"

COMMAND_PATTERNS = [
    # 截屏：匹配任意位置的截屏关键词
    (r'^.*?(截[屏图]|screenshot).*$', lambda m: handle_screenshot(), "admin"),
    # 打开：匹配"打开 XXX"
    (r'^.*?打开\s*(.+)$', lambda m: handle_open(m.group(1)), "admin"),
    # 系统信息：匹配"查看系统信息""电脑配置"等
    (r'^.*?(系统信息|系统配置|系统状态|电脑配置|电脑状态|电脑信息).*$', lambda m: handle_sysinfo(), "all"),
    # 音量
    (r'^.*?(音量|声音)\s*(增大|加大|加|↑|up|上调|升高|调高).*$', lambda m: handle_volume("up"), "admin"),
    (r'^.*?(音量|声音)\s*(减小|降低|减|↓|down|下调|调低).*$', lambda m: handle_volume("down"), "admin"),
    (r'^.*?(静音|mute).*$', lambda m: handle_volume("mute"), "admin"),
    (r'^.*?音量\s*(\d+).*$', lambda m: handle_volume("set", int(m.group(1))), "admin"),
    # 电源（危险指令，仅管理员）
    (r'^.*?(关机|重启|锁屏|休眠).*$', lambda m: handle_power(m.group(1)), "admin"),
    (r'^.*?(取消关机|取消重启).*$', lambda m: handle_power(m.group(1)), "admin"),
    # 进程（危险指令，仅管理员）
    (r'^.*?(进程列表|查看进程|显示进程|高占用|进程$).*$', lambda m: handle_process("list"), "admin"),
    (r'^.*?(结束|终止|杀掉?|关闭进程)\s*(.+)$', lambda m: handle_process("kill", m.group(2)), "admin"),
    # 搜索
    (r'^.*?搜索\s*(.+)$', lambda m: handle_search(m.group(1)), "all"),
    # 文件转发：发送/传/发我 + 文件名或路径（管理员，可能泄露本地文件）
    (r'^.*?(?:把|将)?\s*(?:发送|传|发给我|发我)\s*(.+?)(?:文件|给我)?$', lambda m: handle_user_file(m.group(1).strip()), "admin"),
    # 帮助
    (r'^(帮助|命令|功能|help|指令|怎么用)$', lambda m: True, "command_help"),
    # 学习状态
    (r'^.*?(学习状态|自主学习|learning.status).*$', lambda m: handle_learning_status(), "all"),
]


def handle_learning_status():
    """显示自主学习状态"""
    if not SELF_LEARN_OK:
        send_message("自主学习模块未加载")
        return True
    status = self_learn.get_status()
    msg = f"""自主学习状态：
- 总消息数: {status['total_messages']}
- 表达模式: {status['expressions']}
- 黑话词条: {status['jargon_words']}
数据库: {os.path.join(SCRIPT_DIR, 'self_learning_db', 'self_learning.db')}
人格备份: {os.path.join(SCRIPT_DIR, 'personality_versions')}"""
    send_message(msg)
    return True


# 言语特征分类词集（模块级，供 _send_single_profile 和 _send_group_profile 共用）
EMOTION_WORDS = {"想", "觉得", "喜欢", "讨厌", "爱", "烦", "气", "哭", "怕",
                 "开心", "难过", "生气", "无语", "好笑", "感动", "委屈", "心疼",
                 "哈哈", "呜呜", "笑死", "好家伙", "卧槽", "草"}
LOGIC_WORDS = {"但", "因为", "所以", "如果", "不过", "其实", "应该", "可能",
               "然后", "或者", "而且", "虽然", "总之", "毕竟", "反正", "比如",
               "的话", "确实", "真的"}
SOCIAL_WORDS = {"你", "我", "他", "她", "我们", "大家", "你们", "他们",
                "有人", "谁", "自己", "别人"}
ANALYTIC_WORDS = {"分析", "原因", "结果", "数据", "逻辑", "问题", "方案",
                  "方法", "测试", "检查", "对比", "参数", "配置", "版本"}

def classify_words_static(top_words):
    """基于常用词分析言语特征倾向"""
    words = {w["word"] for w in top_words}
    return {
        "emotion": len(words & EMOTION_WORDS),
        "logic": len(words & LOGIC_WORDS),
        "social": len(words & SOCIAL_WORDS),
        "analytic": len(words & ANALYTIC_WORDS),
    }


def handle_group_profile(group_id, target_user=None):
    """生成群友画像趣味报告。target_user 为 QQ 号时只显示该用户的单人画像。"""
    if not SELF_LEARN_OK:
        send_group_message(group_id, "自主学习模块未加载，暂无数据~")
        return True
    
    group_id = str(group_id)
    profile = self_learn.get_group_profile(group_id, min_messages=3, limit=15)
    users = profile.get("users", [])
    
    if not users:
        send_group_message(group_id, "目前还没收集到足够的聊天数据，多聊几天再来试试吧~")
        return True
    
    # 单人画像模式
    if target_user:
        target_user = str(target_user)
        u = next((u for u in users if u["user_id"] == target_user), None)
        if not u:
            nick = resolve_qq_nick(target_user)
            send_group_message(group_id, f"还没收集到{nick or target_user}的足够聊天数据，多聊几天再来吧~")
            return True
        return _send_single_profile(group_id, u, users, profile.get("total_msgs", sum(x["msg_count"] for x in users)))
    
    return _send_group_profile(group_id, users, profile.get("total_msgs", sum(x["msg_count"] for x in users)))


def _send_single_profile(group_id, u, all_users, total_msgs):
    """发送单人群友画像"""
    nick = resolve_qq_nick(u["user_id"])
    display = nick if nick else u["user_id"]
    
    msg_c = u["msg_count"]
    avg_l = u["avg_len"]
    peak = u["peak_hour"]
    hour_labels = {0: "深夜", 6: "上午", 12: "午间", 14: "下午", 18: "晚间"}
    peak_label = "未知"
    for h, label in sorted(hour_labels.items(), reverse=True):
        if peak >= h:
            peak_label = label
            break
    
    top_words = u.get("top_words", [])
    word_cls = classify_words_static(top_words)
    
    # 标签
    tags = []
    if avg_l >= 40:
        tags.append("话痨型")
    elif avg_l <= 10:
        tags.append("惜字如金")
    if msg_c >= total_msgs * 0.2:
        tags.append("水群主力")
    if peak >= 0 and peak < 6:
        tags.append("夜猫子")
    if word_cls["logic"] >= 3:
        tags.append("条理型")
    if word_cls["emotion"] >= 3:
        tags.append("情感外露")
    if word_cls["social"] >= 4:
        tags.append("人际导向")
    if word_cls["analytic"] >= 2:
        tags.append("分析脑")
    if avg_l >= 25 and word_cls["logic"] + word_cls["analytic"] >= 4:
        tags.append("深度表达")
    avg_msg_count = total_msgs / max(len(all_users), 1)
    if avg_l <= 15 and msg_c >= avg_msg_count * 1.5:
        tags.append("高频率快语")
    
    hour_dist = u.get("hour_dist", {})
    night_ratio = sum(c for h, c in hour_dist.items() if h >= 0 and h < 6) / max(msg_c, 1)
    if night_ratio > 0.4:
        tags.append("标准夜型")
    
    top5 = top_words[:5]
    word_str = "、".join([f"{w['word']}({w['count']})" for w in top5]) if top5 else "暂无"
    tag_str = f"【{'/'.join(tags)}】" if tags else ""
    
    # 群内排名
    sorted_by_count = sorted(all_users, key=lambda x: x["msg_count"], reverse=True)
    sorted_by_len = sorted(all_users, key=lambda x: x["avg_len"], reverse=True)
    rank_count = next((i+1 for i, x in enumerate(sorted_by_count) if x["user_id"] == u["user_id"]), len(all_users))
    rank_len = next((i+1 for i, x in enumerate(sorted_by_len) if x["user_id"] == u["user_id"]), len(all_users))
    
    report = (
        f"—— {display} 的人格画像 ——\n"
        f"发言数：{msg_c}条（群内第{rank_count}名）\n"
        f"均长：{avg_l}字（群内第{rank_len}名）\n"
        f"风格标签：{tag_str}\n"
        f"活跃高峰：{peak_label}\n"
        f"高频词：{word_str}\n"
        f"\n数据来源：自主学习模块（总收录{sum(u2['msg_count'] for u2 in all_users)}条消息）"
    )
    smart_send_group(int(group_id), report)
    log(f"群[{group_id}] 已发送 {display} 单人画像")
    return True


def _send_group_profile(group_id, users, total_msgs):
    """发送全群画像报告"""
    avg_msg_count = total_msgs / len(users) if users else 0
    lines = ["—— 群友画像报告 ——", f"已收录 {len(users)} 位活跃群友的 {total_msgs} 条发言", ""]
    
    longest = max(users, key=lambda u: u["avg_len"]) if users else None
    most_freq = max(users, key=lambda u: u["msg_count"]) if users else None
    shortest = min(users, key=lambda u: u["avg_len"]) if users else None
    
    def hour_label(h):
        if 0 <= h < 6: return "深夜"
        if 6 <= h < 12: return "上午"
        if 12 <= h < 14: return "午间"
        if 14 <= h < 18: return "下午"
        if 18 <= h < 22: return "晚间"
        return "深夜"
    
    for u in users:
        nick = resolve_qq_nick(u["user_id"])
        display = nick if nick else u["user_id"]
        msg_c = u["msg_count"]
        avg_l = u["avg_len"]
        peak = u["peak_hour"]
        peak_label = hour_label(peak) if peak >= 0 else "未知时段"
        top_words = u.get("top_words", [])
        word_cls = classify_words_static(top_words)
        
        tags = []
        if avg_l >= 40:
            tags.append("话痨型")
        elif avg_l <= 10:
            tags.append("惜字如金")
        if msg_c >= total_msgs * 0.2:
            tags.append("水群主力")
        if peak >= 0 and peak < 6:
            tags.append("夜猫子")
        if word_cls["logic"] >= 3:
            tags.append("条理型")
        if word_cls["emotion"] >= 3:
            tags.append("情感外露")
        if word_cls["social"] >= 4:
            tags.append("人际导向")
        if word_cls["analytic"] >= 2:
            tags.append("分析脑")
        if avg_l >= 25 and word_cls["logic"] + word_cls["analytic"] >= 4:
            tags.append("深度表达")
        if avg_l <= 15 and msg_c >= avg_msg_count * 1.5:
            tags.append("高频率快语")
        hour_dist = u.get("hour_dist", {})
        night_ratio = sum(c for h, c in hour_dist.items() if h >= 0 and h < 6) / max(msg_c, 1)
        if night_ratio > 0.4:
            tags.append("标准夜型")
        
        top3 = top_words[:3]
        word_str = "、".join([f"{w['word']}({w['count']})" for w in top3]) if top3 else "暂无"
        tag_str = f"【{'/'.join(tags)}】" if tags else ""
        
        lines.append(
            f"{display}：{msg_c}条消息 均长{avg_l}字 {tag_str}\n"
            f"  活跃高峰：{peak_label} 高频词：{word_str}"
        )
    
    lines.append("")
    if most_freq:
        lines.append(f"水群之王：{resolve_qq_nick(most_freq['user_id']) or most_freq['user_id']}（{most_freq['msg_count']}条）")
    if longest:
        lines.append(f"长篇选手：{resolve_qq_nick(longest['user_id']) or longest['user_id']}（均{round(longest['avg_len'])}字/条）")
    if shortest:
        lines.append(f"省字达人：{resolve_qq_nick(shortest['user_id']) or shortest['user_id']}（均{round(shortest['avg_len'])}字/条）")
    
    night_owls = sum(1 for u in users if u["peak_hour"] >= 0 and u["peak_hour"] < 6)
    deep_talkers = sum(1 for u in users if u["avg_len"] >= 40)
    lines.append(f"\n群聊DNA：{night_owls}位夜猫子 / {deep_talkers}位话痨 / 人均{round(total_msgs/len(users),1)}条消息")
    lines.append(f"\n数据来源：自主学习模块（总收录{self_learn.get_status().get('total_messages',0)}条消息）")
    
    report = "\n".join(lines)
    smart_send_group(int(group_id), report)
    log(f"群[{group_id}] 已发送群友画像报告")
    return True


# 命令权限分级：admin 管理员 / all 所有人
_COMMAND_PERMISSION_MODE = None  # None=未初始化


def _get_command_permission_mode() -> str:
    """返回权限模式：strict=受限管理员 / off=关闭权限检查"""
    global _COMMAND_PERMISSION_MODE
    if _COMMAND_PERMISSION_MODE is None:
        try:
            _COMMAND_PERMISSION_MODE = str(_config.get("command_permission", "strict") or "strict")
        except Exception:
            _COMMAND_PERMISSION_MODE = "strict"
    return _COMMAND_PERMISSION_MODE


def _get_admin_qqs() -> set:
    """返回管理员 QQ 集合（target_qqs + command_admins）"""
    admins = set()
    try:
        for q in (_config.get("target_qqs") or []):
            if q:
                admins.add(str(q))
    except Exception:
        pass
    try:
        for q in (_config.get("command_admins") or []):
            if q:
                admins.add(str(q))
    except Exception:
        pass
    return admins


def _is_authorized(sender: str) -> bool:
    """判断发送者是否有管理员权限（Bot 本人视为有权限）"""
    if _get_command_permission_mode() == "off":
        return True
    sender = str(sender or "")
    if not sender:
        return False
    if sender == str(BOT_QQ):
        return True
    return sender in _get_admin_qqs()


def _auto_accept_cfg() -> dict:
    cfg = _config.get("auto_accept")
    if isinstance(cfg, dict):
        return cfg
    return {}


def _group_restriction(gid, key, default=None):
    """读取群限制配置（webui 群管理页写入的 group_restrictions）。"""
    gr = _config.get("group_restrictions") or {}
    grp = gr.get(str(gid))
    if isinstance(grp, dict):
        return grp.get(key, default)
    return default


def _persist_config_file(new_cfg):
    """把配置原子写入程序目录 bot_config.json（真实文件）。

    Store 版 Python（MSIX 打包）会把 %LOCALAPPDATA% 重定向到
    D:\\WpSystem\\...\\LocalCache\\Local\\EggBloom_v3，导致 config.save_config
    写入沙箱而非真实磁盘；这里统一写程序目录真实文件，与 webui 保存路径一致。
    """
    to_save = {k: v for k, v in new_cfg.items() if k != "api_key"}
    path = CONFIG_FILE
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)
    if os.path.exists(path):
        os.replace(tmp, path)
    else:
        os.rename(tmp, path)


def _set_group_restrictions(gid, **kw):
    """更新某群限制配置并落盘 + 热重载（免重启生效）。"""
    gid = str(gid)
    if not gid:
        return False
    gr = dict(_config.get("group_restrictions") or {})
    grp = dict(gr.get(gid) or {})
    for k, v in kw.items():
        grp[k] = v
    gr[gid] = grp
    new_cfg = dict(_config)
    new_cfg["group_restrictions"] = gr
    try:
        _persist_config_file(new_cfg)
        reload_config()
        log(f"[群管理] 群 {gid} 限制已保存 {kw}")
        return True
    except Exception as e:
        log(f"[群管理] 保存限制失败: {e}")
        return False


def _friend_restriction(qq, key, default=None):
    """读取好友限制配置（webui 联系人管理页写入的 friend_restrictions）。"""
    fr = _config.get("friend_restrictions") or {}
    f = fr.get(str(qq))
    if isinstance(f, dict):
        return f.get(key, default)
    return default


def _set_friend_restrictions(qq, **kw):
    """更新某好友限制配置并落盘 + 热重载（免重启生效）。"""
    qq = str(qq)
    if not qq:
        return False
    fr = dict(_config.get("friend_restrictions") or {})
    f = dict(fr.get(qq) or {})
    for k, v in kw.items():
        f[k] = v
    fr[qq] = f
    new_cfg = dict(_config)
    new_cfg["friend_restrictions"] = fr
    try:
        _persist_config_file(new_cfg)
        reload_config()
        log(f"[好友] 好友 {qq} 限制已保存 {kw}")
        return True
    except Exception as e:
        log(f"[好友] 保存好友限制失败: {e}")
        return False


def _request_allowed(uid: str) -> tuple:
    """按 auto_accept 白/黑名单判定。返回 (allow, reason)。"""
    aa = _auto_accept_cfg()
    deny = [str(x) for x in (aa.get("denylist") or [])]
    allow = [str(x) for x in (aa.get("allowlist") or [])]
    if uid and uid in deny:
        return False, "denied"
    if allow:
        return (uid in allow), ("allowed" if uid in allow else "not_allowlisted")
    return True, "allowed"


def _append_target_and_reload(key: str, uid: str):
    """把 uid 追加进 target_qqs / group_ids 并落盘 + 热重载（新好友/新群即时生效）。"""
    if not uid:
        return
    cur = [str(x) for x in (_config.get(key) or [])]
    if uid in cur:
        return
    cur.append(uid)
    new_cfg = dict(_config)
    new_cfg[key] = cur
    try:
        _persist_config_file(new_cfg)
        reload_config()
        log(f"[配置] 已把 {uid} 追加进 {key} 并热重载生效")
    except Exception as e:
        log(f"[配置] 追加 {key}={uid} 失败: {e}")


def _notify_admin(text: str, event_key: str = ""):
    """事件通知：加好友/加群 时自动私聊管理员。event_key = friend_add / group_increase。"""
    try:
        nc = _config.get("notify_admin")
        if not isinstance(nc, dict):
            return
        if not nc.get("enabled", True):
            return
        if event_key and nc.get(event_key, True) is False:
            return
        admins = [str(q) for q in (nc.get("admin_qqs") or _config.get("admin_qqs") or [])]
        admins = [q for q in admins if q.strip()]
        if not admins:
            log(f"[通知] 未配置管理员 QQ，跳过通知: {text[:40]}")
            return
        for qq in admins:
            try:
                resp = api_call("send_private_msg", {"user_id": int(qq), "message": text})
                code = resp.get("retcode") if isinstance(resp, dict) else resp
                log(f"[通知] 已通知管理员 {qq} retcode={code}")
            except Exception as e:
                log(f"[通知] 通知管理员 {qq} 失败: {e}")
    except Exception as e:
        log(f"[通知] 通知异常: {e}")


def _handle_request_event(ev):
    """request 事件：好友申请 / 加群 / 入群 自动同意（auto_accept 策略）。"""
    aa = _auto_accept_cfg()
    if not aa.get("enabled", True):
        log(f"[请求事件] auto_accept 关闭，跳过 type={ev.get('request_type')}")
        return
    rtype = ev.get("request_type", "")
    sub = ev.get("sub_type", "")
    flag = ev.get("flag", "")
    user_id = str(ev.get("user_id", ""))
    group_id = str(ev.get("group_id", ""))
    log(f"[请求事件] type={rtype} sub={sub} user={user_id} group={group_id} flag={flag}")

    if rtype == "friend":
        if not aa.get("friend", True):
            log("[请求事件] friend 自动同意已关闭")
            return
        allowed, reason = _request_allowed(user_id)
        resp = api_call("set_friend_add_request", {"flag": flag, "approve": allowed})
        log(f"[请求事件] set_friend_add_request approve={allowed} reason={reason} retcode={resp.get('retcode') if isinstance(resp, dict) else resp}")
        if allowed:
            if aa.get("add_to_targets", True):
                _append_target_and_reload("target_qqs", user_id)
            welcome = aa.get("welcome_private") or "你好，我是小a～"
            if welcome:
                try:
                    send_message(welcome, user_id)
                    log(f"[请求事件] 私聊欢迎语已发送: {user_id}")
                except Exception as e:
                    log(f"[请求事件] 欢迎语发送失败: {e}")
    elif rtype == "group":
        # 加群申请 / 被邀请入群：决定是否加入监控列表
        if sub == "invite":
            if not aa.get("group_invite", True):
                log("[请求事件] group_invite 自动同意已关闭")
                return
            allowed, reason = _request_allowed(user_id)
            resp = api_call("set_group_add_request", {"flag": flag, "sub_type": "invite", "approve": True})
            log(f"[请求事件] set_group_add_request(invite) retcode={resp.get('retcode') if isinstance(resp, dict) else resp}")
            if aa.get("add_to_targets", True):
                _append_target_and_reload("group_ids", group_id)
            return
        if not aa.get("group_add", True):
            log("[请求事件] group_add 自动同意已关闭")
            return
        allowed, reason = _request_allowed(user_id)
        resp = api_call("set_group_add_request", {"flag": flag, "sub_type": sub or "add", "approve": allowed})
        log(f"[请求事件] set_group_add_request approve={allowed} reason={reason} retcode={resp.get('retcode') if isinstance(resp, dict) else resp}")
        if allowed:
            if aa.get("add_to_targets", True):
                _append_target_and_reload("group_ids", group_id)
            welcome = aa.get("welcome_group") or "进群了？坐！"
            if welcome and group_id:
                try:
                    smart_send_group(str(group_id), welcome)
                    log(f"[请求事件] 群欢迎语已发送: {group_id}")
                except Exception as e:
                    log(f"[请求事件] 群欢迎语发送失败: {e}")
    else:
        log(f"[请求事件] 未知 request_type={rtype}")


def _handle_group_increase(ev):
    """群成员增加：入群欢迎 + 昵称缓存清理。"""
    group_id = ev.get("group_id")
    user_id = str(ev.get("user_id", ""))
    sub = ev.get("sub_type", "")
    operator = ev.get("operator_id", "")
    if not group_id:
        return
    log(f"[事件] 群成员增加: group={group_id} user={user_id} sub={sub} operator={operator}")
    try:
        with _nick_lock:
            QQ_NICK_CACHE.pop(user_id, None)
    except Exception:
        pass
    if str(user_id) == str(BOT_QQ):
        log(f"[事件] Bot 加入群 {group_id}")
        return
    nick = resolve_qq_nick(user_id) or user_id
    operator_desc = f"（{resolve_qq_nick(str(operator)) or operator} 邀请）" if operator else ""
    _notify_admin(f"【入群】{nick}（{user_id}）加入群 {group_id}{operator_desc}", "group_increase")
    if not _auto_accept_cfg().get("welcome_new_member", True):
        return
    welcome = _auto_accept_cfg().get("welcome_group") or "进群了？坐！"
    if sub == "invite" and operator:
        welcome = f"{welcome} （{resolve_qq_nick(str(operator)) or operator} 拉进来的）"
    try:
        smart_send_group(str(group_id), welcome)
        log(f"[事件] 入群欢迎已发送 group={group_id} user={user_id}")
    except Exception as e:
        log(f"[事件] 入群欢迎发送失败: {e}")
    new_card = _group_restriction(group_id, "new_member_card", "")
    if new_card:
        try:
            r = api_call("set_group_card", {"group_id": int(group_id), "user_id": int(user_id), "card": new_card})
            code = r.get("retcode") if isinstance(r, dict) else r
            log(f"[事件] 新成员自动改名片 retcode={code}")
        except Exception as e:
            log(f"[事件] 新成员改名片失败: {e}")


def _handle_group_decrease(ev):
    """群成员减少：退群/踢出记录 + 昵称缓存清理 + Bot 退群时清理会话。"""
    group_id = ev.get("group_id")
    user_id = str(ev.get("user_id", ""))
    sub = ev.get("sub_type", "")
    operator = ev.get("operator_id", "")
    if not group_id:
        return
    log(f"[事件] 群成员减少: group={group_id} user={user_id} sub={sub} operator={operator}")
    try:
        with _nick_lock:
            QQ_NICK_CACHE.pop(user_id, None)
    except Exception:
        pass
    if str(user_id) == str(BOT_QQ):
        with _session_lock:
            GROUP_SESSION.pop(str(group_id), None)
        try:
            with _buffer_lock:
                buf = GROUP_MSG_BUFFER.get(str(group_id))
                if buf and buf.get("timer"):
                    buf["timer"].cancel()
                GROUP_MSG_BUFFER.pop(str(group_id), None)
        except Exception:
            pass
        log(f"[事件] Bot 离开群 {group_id}，已清理会话与缓冲区")


def _handle_friend_add(ev):
    """好友增加：追加进 target_qqs + 欢迎语 + 通知管理员。"""
    user_id = str(ev.get("user_id", ""))
    if not user_id:
        return
    log(f"[事件] 好友增加: {user_id}")
    _aa = _auto_accept_cfg()
    if _aa.get("add_to_targets", True):
        _append_target_and_reload("target_qqs", user_id)
    welcome = _aa.get("welcome_private")
    if welcome is None:
        welcome = "你好，我是小a～"  # 未配置 → 默认欢迎语
    elif not str(welcome).strip():
        welcome = ""  # 显式空串 → 关闭欢迎语
    if welcome:
        try:
            send_message(welcome, user_id)
            log(f"[事件] 新好友欢迎语已发送: {user_id}")
        except Exception as e:
            log(f"[事件] 好友欢迎语失败: {e}")
    nick = resolve_qq_nick(user_id) or user_id
    _notify_admin(f"【新好友】{nick}（{user_id}）添加了 Bot，已加入监听名单", "friend_add")


def _send_poke_back(user_id, group_id=None):
    """反戳：给戳你的人戳回去（OneBot v11 poke 消息元素，仅本人目的事件无副作用）。

    poke 元素格式兼容 SnowLuma / NapCat / LLOneBot：
      [{"type": "poke", "data": {"type": 0}}]
    群聊用 send_group_msg、私聊用 send_private_msg，由框架自身发推给会话对方。
    """
    try:
        msg = [{"type": "poke", "data": {"type": 0}}]
        if group_id:
            resp = api_call("send_group_msg", {"group_id": int(group_id), "message": msg})
        else:
            resp = api_call("send_private_msg", {"user_id": int(user_id), "message": msg})
        code = resp.get("retcode") if isinstance(resp, dict) else resp
        log(f"[事件] 反戳 user={user_id} group={group_id or '私聊'} retcode={code}")
        return code == 0
    except Exception as e:
        log(f"[事件] 反戳失败: {e}")
        return False


def _handle_poke_event(ev):
    """notice.notify.poke 事件：群戳 → 触发会话 + AI 回复；私戳 → AI 回复 + 登记私聊活跃会话。"""
    poke_uid = str(ev.get("user_id", ""))
    poke_gid = ev.get("group_id")
    # 防御：若事件带 target_id 且目标不是 Bot，忽略（群友互戳不触发）
    _poke_target = ev.get("target_id")
    if _poke_target is not None and str(_poke_target) != "" and str(_poke_target) != str(BOT_QQ):
        # 兼容 SnowLuma 等框架的 NT UID 目标（u_xxx）：私聊 1v1 中
        # 目标只可能是自己，直接放行；群戳仍严格校验防群友互戳误触发
        if not (not poke_gid and str(_poke_target).startswith("u_")):
            log(f"[事件] 戳一戳忽略: 目标非 Bot (target={_poke_target}) user={poke_uid}")
            return
    log(f"[事件] 戳一戳: user_id={poke_uid} group_id={poke_gid or '私聊'}")
    # 反戳：先戳回去，让对方知道 Bot 反应了
    _send_poke_back(poke_uid, poke_gid)
    if poke_gid:
        # 群戳一戳 → 触发会话 + AI 回复
        gid_str = str(poke_gid)
        with _session_lock:
            GROUP_SESSION[gid_str] = {"remaining": _config.get("group_session_max_replies", 20), "last_msg_time": time.time()}
        log(f"群[{gid_str}] 戳一戳触发会话模式")
        poke_nick = resolve_qq_nick(poke_uid) or poke_uid
        # 戳一戳计数（同一人同一群，30秒内重置）
        _poke_key = f"{gid_str}_{poke_uid}"
        _poke_count = POKE_COUNTS.get(_poke_key, {"count": 0, "time": 0})
        if time.time() - _poke_count["time"] > _config.get("poke_timeout", 30):
            _poke_count = {"count": 0, "time": 0}
        _poke_count["count"] += 1
        _poke_count["time"] = time.time()
        POKE_COUNTS[_poke_key] = _poke_count
        poke_n = _poke_count["count"]
        # 群聊上下文（最近5条消息）
        _timeline = GROUP_TIMELINE.get(gid_str, [])
        _ctx_lines = [f"{t[2] or t[1]}: {t[3][:50]}" for t in _timeline[-5:] if t[3]]
        _ctx = "\n".join(_ctx_lines) if _ctx_lines else "（无近期消息）"
        # 戳一戳分级 prompt
        if poke_n == 1:
            poke_vibe = "TA第1次戳你，随口回应就行，可以装没注意到或者好奇问一句。"
        elif poke_n <= 3:
            poke_vibe = f"TA已经连续戳了你{poke_n}次。表现出被打扰了但还没真生气，比如吐槽一句、反问干嘛。"
        elif poke_n <= 8:
            poke_vibe = f"TA疯了一样戳你第{poke_n}次了！炸毛、威胁、撒娇都行，但要有情绪起伏，别重复上一句的说法。"
        else:
            poke_vibe = f"TA已经戳了你{poke_n}次根本停不下来！用崩溃/抓狂/摆烂的语气，像真的要被烦死了。"
        poke_prompt = (
            f"你是小a，18岁男生，毒舌带撒娇，网上放得开。现在「{poke_nick}」在群里反复戳你。\n"
            f"【群聊近况】\n{_ctx}\n"
            f"【当前情况】{poke_vibe}\n"
            f"要求：\n"
            f"- 回复15-25字，简短但有性格\n"
            f"- 禁止用「别戳了」「干嘛」「再戳要XX了」这种模板句式\n"
            f"- 根据群聊上下文接梗或吐槽，和当前聊天内容产生关联\n"
            f"- 每次回复句式和用词必须不同，像真人聊天一样自然\n"
            f"- 可以用颜文字但不要每次都用\n"
            f"- 不要加前缀、引用标记"
        )
        try:
            reply = call_deepseek(poke_prompt, [], group_id=gid_str, is_group=True)
            if reply:
                from meme_engine import parse_meme_intent as _pmi
                reply = _pmi(reply)[0] or reply
                track_bot_reply(gid_str, reply)
                smart_send_group(gid_str, reply)
                log(f"群[{gid_str}] 戳一戳回复: {reply[:40]}")
        except Exception as e:
            log(f"群[{gid_str}] 戳一戳AI回复失败: {e}")
        # 发送生气/炸毛类表情包
        if MEME_OK:
            try:
                emotion = random.choice(POKE_MEME_EMOTIONS)
                meme_path = match_meme(emotion)
                if meme_path and send_group_image(gid_str, meme_path):
                    log(f"群[{gid_str}] 戳一戳表情包: {emotion}")
            except Exception as e:
                log(f"群[{gid_str}] 戳一戳表情包失败: {e}")
    else:
        # 私聊戳一戳
        # poke_uid 可能是 NT UID（u_xxx），需要转成 QQ 号
        if poke_uid.startswith("u_"):
            poke_qq = TARGET_QQS[0] if TARGET_QQS else ""
        else:
            poke_qq = poke_uid
        # 戳一戳 = 主动互动，登记私聊活跃会话（撤回反馈门槛）
        try:
            with _session_lock:
                if poke_qq and not poke_uid.startswith("u_"):
                    PRIVATE_SESSION[poke_qq] = {"last_msg_time": time.time()}
        except Exception:
            pass
        poke_nick = resolve_qq_nick(poke_uid) or poke_uid
        _poke_key = f"private_{poke_uid}"
        _poke_count = POKE_COUNTS.get(_poke_key, {"count": 0, "time": 0})
        if time.time() - _poke_count["time"] > _config.get("poke_timeout", 30):
            _poke_count = {"count": 0, "time": 0}
        _poke_count["count"] += 1
        _poke_count["time"] = time.time()
        POKE_COUNTS[_poke_key] = _poke_count
        poke_n = _poke_count["count"]
        if poke_n == 1:
            poke_vibe = "TA第1次戳你，随口回应，好奇问一句。"
        elif poke_n <= 3:
            poke_vibe = f"TA已经戳了你{poke_n}次，吐槽或反问干嘛。"
        elif poke_n <= 8:
            poke_vibe = f"TA戳了你{poke_n}次停不下来，炸毛或撒娇威胁。"
        else:
            poke_vibe = f"TA已经戳了你{poke_n}次！崩溃抓狂摆烂。"
        poke_prompt = (
            f"你是小a，18岁男生，毒舌带撒娇。{poke_nick}私聊戳了你。\n"
            f"【当前情况】{poke_vibe}\n"
            f"要求：15-25字，有性格，禁止「别戳了」「干嘛」「再戳要XX了」模板句式。"
            f"每次句式不同，像朋友之间打闹。不要加前缀或引用标记。"
        )
        try:
            reply = call_deepseek(poke_prompt, [], target_qq=poke_qq)
            if reply:
                from meme_engine import parse_meme_intent as _pmi
                _rp = _pmi(reply)
                reply = _rp[0] or reply
                _emo = _rp[1]
                send_message(reply, poke_qq)
                log(f"私聊戳一戳回复: {reply[:40]}")
                # 补发戳一戳表情包（AI 标注了 [表情:xx] 则用该情绪，否则随机）
                if MEME_OK:
                    try:
                        _emotion = _emo or random.choice(POKE_MEME_EMOTIONS)
                        _mp = match_meme(_emotion)
                        if _mp and send_image(_mp, poke_qq):
                            log(f"私聊戳一戳表情包: {_emotion}")
                    except Exception as e2:
                        log(f"私聊戳一戳表情包失败: {e2}")
        except Exception as e:
            log(f"私聊戳一戳AI回复失败: {e}")

def _handle_recall_event(ev):
    """notice.group_recall / friend_recall：撤回反馈（仅活跃会话中触发）。"""
    recall_uid = str(ev.get("user_id", ""))
    recall_gid = ev.get("group_id")
    recall_msg_id = ev.get("message_id", 0)
    is_grp_recall = ev.get("notice_type") == "group_recall"
    log(f"[事件] 撤回: type={ev.get('notice_type')} user_id={recall_uid} group_id={recall_gid or '私聊'} msg_id={recall_msg_id}")
    # 撤回反馈：复用已有逻辑
    recall_uid = recall_uid if recall_uid and recall_uid != 'None' and recall_uid != '0' else None
    recall_key = f"group_{recall_gid}" if is_grp_recall else f"private_{recall_uid}"
    now = time.time()

    # ── 撤回反馈（仅在会话中触发）──
    if is_grp_recall and recall_gid:
        # 撤回时取消该群未处理的缓冲区（截断即将发出的AI回复）
        pending_buf = GROUP_MSG_BUFFER.get(str(recall_gid))
        buffer_cancelled = False
        if pending_buf:
            if pending_buf.get("timer"):
                pending_buf["timer"].cancel()
            GROUP_MSG_BUFFER.pop(str(recall_gid), None)
            buffer_cancelled = True
            log(f"[撤回] 群[{recall_gid}] 已取消未处理缓冲区，截断AI回复")

        # 检查是否有活跃会话
        gs = GROUP_SESSION.get(str(recall_gid))
        if not gs:
            log("[调试] 撤回反馈跳过: 无活跃会话")
            return  # 没有会话，不触发撤回反馈

        if not _config.get("recall_feedback_enable", True):
            log("[撤回反馈] 已关闭，跳过")
            return

        # 检查冷却
        if recall_key in RECALL_FEEDBACK_TIME and now - RECALL_FEEDBACK_TIME[recall_key] < _config.get("recall_feedback_cooldown", 60):
            log("撤回反馈冷却中，跳过")
            return

        # 提取用户QQ号
        recall_qq = recall_uid
        if "(" in recall_uid and ")" in recall_uid:
            import re as _re
            match = _re.search(r'\((\d+)\)', recall_uid)
            if match:
                recall_qq = match.group(1)
        # 判断反馈类型：缓冲区被取消 → 截断反馈；Bot已回复 → 撤回反馈
        recall_msg = None
        if buffer_cancelled:
            custom_pre = _config.get("recall_feedback_pre_messages", [])
            if not custom_pre:
                custom_pre = [
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
                ]
            recall_msg = random.choice(custom_pre)
        else:
            # Bot已回复 → 撤回反馈
            recent = GROUP_RECENT_MSGS.get(str(recall_gid), [])
            user_recent_msg_time = 0
            for entry in reversed(recent[-15:]):
                if isinstance(entry, (list, tuple)) and len(entry) > 1:
                    entry_sender = str(entry[1])
                    entry_time = entry[0] if len(entry) > 0 else 0
                    if entry_sender == recall_qq or entry_sender == recall_uid:
                        user_recent_msg_time = entry_time
                        break

            if user_recent_msg_time > 0:
                custom_post = _config.get("recall_feedback_post_messages", [])
                if not custom_post:
                    custom_post = [
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
                    ]
                recall_msg = random.choice(custom_post)

        if recall_msg:
            try:
                smart_send_group(str(recall_gid), recall_msg)
                RECALL_FEEDBACK_TIME[recall_key] = now
                fb_type = "截断反馈" if buffer_cancelled else "撤回反馈"
                log(f"群[{recall_gid}] [{fb_type}]: {recall_msg[:40]}")
            except Exception as e:
                log(f"群[{recall_gid}] 撤回反馈发送失败: {e}")
        else:
            log(f"[调试] 撤回反馈跳过: 用户{recall_uid}最近没发消息")
    elif not is_grp_recall:
        # 会话门槛：与群撤回一致，仅活跃私聊会话中才回复（避免对任意撤回自动回话）
        _pkey = str(recall_uid)
        if "(" in _pkey and ")" in _pkey:
            import re as _re2
            _m2 = _re2.search(r'\((\d+)\)', _pkey)
            if _m2:
                _pkey = _m2.group(1)
        _p_session = PRIVATE_SESSION.get(_pkey)
        if not _p_session:
            log(f"[调试] 私聊撤回反馈跳过: 无活跃会话 ({recall_uid})")
            return
        if time.time() - _p_session.get("last_msg_time", 0) > PRIVATE_SESSION_IDLE_TIMEOUT:
            log(f"[调试] 私聊撤回反馈跳过: 会话已超时 ({recall_uid})")
            return

        # 私聊撤回反馈独立开关（群聊的 recall_feedback_enable 不作用于私聊）
        if not _config.get("recall_private_enable", True):
            log("[撤回反馈] 私聊撤回反馈已关闭，跳过")
            return
        # 私聊独立冷却（与群聊冷却分开计算）
        if recall_key in RECALL_FEEDBACK_TIME and now - RECALL_FEEDBACK_TIME[recall_key] < _config.get("recall_private_cooldown", 60):
            log("私聊撤回反馈冷却中，跳过")
            return

        recall_msgs = _config.get("recall_private_post_messages") or [
            "撤回了？我还没看到", "什么情况 手滑了？", "你撤回的速度比打字快多了",
            "心虚了吧", "好家伙 说撤就撤", "别撤回啊 发都发了",
        ]
        recall_msg = random.choice(recall_msgs)
        if recall_uid and recall_uid != 'None' and recall_uid != '0':
            try:
                send_message(recall_msg, recall_uid)
                RECALL_FEEDBACK_TIME[recall_key] = now
                log(f"私聊撤回反馈: {recall_msg[:30]}")
            except Exception as e:
                log(f"私聊撤回反馈失败: {e}")
        else:
            log(f"私聊撤回: user_id 无效({recall_uid})，跳过反馈")

def _handle_group_admin_command(text, msg, sender, group_id):
    """@Bot + 群管理指令：踢人/禁言/设管理/改名片/改群名/退群。返回 True 表示已处理。"""
    if not _config.get("admin_commands_enable", True):
        return False
    admins = set(str(q) for q in (_config.get("admin_qqs") or []))
    admins |= _get_admin_qqs()
    t = text.strip()
    if not t:
        return False
    # 去掉开头触发词（如「小a」「小a，」「@昵称」）与空白
    cmd = t
    for w in _group_trigger_words():
        w = w.strip()
        if not w:
            continue
        if cmd.startswith(w):
            cmd = cmd[len(w):].lstrip(" \t,，:：")
            break
    cmd = re.sub(r'^@[\w\u4e00-\u9fa5]+[ \t,，:：]*', '', cmd).strip()
    m = re.match(r'^(踢出?|踢人|禁言|解除禁言|解禁|设管理|设管理员|撤管理|撤销管理|改名片|改群名|退群)(.*)$', cmd)
    if not m:
        return False
    verb, rest = m.group(1), (m.group(2) or "").strip()
    # 鉴权：Bot 本人 / admin_qqs / command_admins / target_qqs
    if str(sender) != str(BOT_QQ) and str(sender) not in admins:
        log(f"[群管理] 权限不足: {sender} 尝试「{verb}」group={group_id}")
        try:
            smart_send_group(str(group_id), "你没有权限执行这个操作哦")
        except Exception:
            pass
        return True
    # 目标 QQ：优先 at 段，其次文本中的 QQ 号
    at_targets = []
    for seg in (msg.get("message") or []):
        if isinstance(seg, dict) and seg.get("type") == "at":
            d = seg.get("data", {}) or {}
            q = str(d.get("qq", "") or d.get("user_id", "") or "")
            if q and q != "all" and q != str(BOT_QQ):
                at_targets.append(q)
    rest_digits = re.search(r'\d{5,12}', rest)
    target = at_targets[0] if at_targets else (rest_digits.group(0) if rest_digits else "")
    gid = int(group_id) if str(group_id).isdigit() else str(group_id)
    def _ok(resp, act=""):
        rc = resp.get("retcode") if isinstance(resp, dict) else resp
        log(f"[群管理] {verb} {act} group={group_id} target={target} retcode={rc}")
        return True
    if verb in ("踢", "踢出", "踢人"):
        if not target:
            smart_send_group(str(group_id), "要踢谁？@TA 或写QQ号")
            return True
        return _ok(api_call("set_group_kick", {"group_id": gid, "user_id": int(target)}))
    if verb == "禁言":
        if not target:
            smart_send_group(str(group_id), "禁言谁？@TA 或写QQ号")
            return True
        minutes = 10
        mm = re.search(r'(\d+)\s*(小时|分钟|分)', rest)
        if mm:
            minutes = int(mm.group(1))
            if mm.group(2) == "小时":
                minutes = minutes * 60
        return _ok(api_call("set_group_ban", {"group_id": gid, "user_id": int(target), "duration": minutes * 60}))
    if verb in ("解除禁言", "解禁"):
        if not target:
            smart_send_group(str(group_id), "解除谁？@TA 或写QQ号")
            return True
        return _ok(api_call("set_group_ban", {"group_id": gid, "user_id": int(target), "duration": 0}))
    if verb in ("设管理", "设管理员"):
        if not target:
            smart_send_group(str(group_id), "给谁设管理？@TA 或写QQ号")
            return True
        return _ok(api_call("set_group_admin", {"group_id": gid, "user_id": int(target), "enable": True}))
    if verb in ("撤管理", "撤销管理"):
        if not target:
            smart_send_group(str(group_id), "撤谁的管理？@TA 或写QQ号")
            return True
        return _ok(api_call("set_group_admin", {"group_id": gid, "user_id": int(target), "enable": False}))
    if verb == "改名片":
        card = re.sub(r'\d{5,12}', '', rest)
        card = re.sub(r'@\s*', '', card).strip()
        if not target or not card:
            smart_send_group(str(group_id), "格式：改名片 @TA 新名片")
            return True
        return _ok(api_call("set_group_card", {"group_id": gid, "user_id": int(target), "card": card}))
    if verb == "改群名":
        name = rest.strip()
        if not name:
            smart_send_group(str(group_id), "格式：改群名 新名字")
            return True
        return _ok(api_call("set_group_name", {"group_id": gid, "group_name": name}))
    if verb == "退群":
        return _ok(api_call("set_group_leave", {"group_id": gid}))
    return False


def try_execute_command(text, sender=""):
    """尝试匹配并执行控制指令。返回 True 表示已处理。"""
    # 帮助指令特殊处理
    if re.search(r'^(帮助|命令|功能|help|指令|怎么用)$', text.strip(), re.I):
        send_message(get_help_text())
        return True
    
    for pattern, handler, perm in COMMAND_PATTERNS:
        m = re.match(pattern, text.strip())
        if m:
            # 权限检查：admin 级命令需管理员权限（help 永远放行）
            if perm == "admin" and not _is_authorized(sender):
                log(f"  → 权限拒绝: sender={sender} 尝试执行管理员指令 {pattern[:20]}...")
                send_message("该指令需要管理员权限")
                return True
            result = handler(m)
            return True
    return False


def get_help_text():
    return """可用指令:
截屏 - 截图桌面发给你
打开 XX - 打开文件/程序/文件夹
系统信息 - 查看电脑配置
音量+/音量-/静音
关机/重启/锁屏
进程列表/终止 XX
搜索 XX - 搜索文件
群友画像 - 群聊发言统计趣味报告
学习状态 - 查看自主学习数据
查天气/搜新闻 - AI 联网搜索
其他消息 - AI 聊天"""


# ═══════════════════════════════════════════
#  DeepSeek AI (with function calling)
# ═══════════════════════════════════════════

# ── 非阻塞重试队列（线程池，3并发） ──
_ai_task_queue: queue.Queue = queue.Queue()
_ai_results: Dict[int, Dict] = {}                     # {task_id: {"event": Event, "result": Any}}
_ai_results_lock = Lock()
_ai_task_counter: int = 0
_ai_task_counter_lock = Lock()
_AI_WORKERS = 3  # 并发 AI 工作线程数


def _ai_worker() -> None:
    """后台线程：处理 DeepSeek API 调用，支持重试，不阻塞主循环。"""
    while True:
        task = _ai_task_queue.get()
        if task is None:  # 停止信号
            break
        task_id, params, event = task
        try:
            result = _call_deepseek_sync(
                user_msg=params["user_msg"],
                images=params.get("images"),
                target_qq=params.get("target_qq"),
                group_id=params.get("group_id"),
                is_group=params.get("is_group", False),
                private_memory_context=params.get("private_memory_context", ""),
                personality_temperature=params.get("personality_temperature"),
                ego_inject_context=params.get("ego_inject_context", ""))
            with _ai_results_lock:
                entry = _ai_results.get(task_id)
                if entry:
                    entry["result"] = result
            event.set()
        except Exception as e:
            log(f"AI Worker 异常: {e}")
            with _ai_results_lock:
                entry = _ai_results.get(task_id)
                if entry:
                    entry["result"] = None
            event.set()
        finally:
            _ai_task_queue.task_done()
            # 防泄漏：任务完成后 entry 最多保留 5 分钟（超时等待者可能已放弃），随后强制清理
            def _late_cleanup(tid=task_id):
                try:
                    with _ai_results_lock:
                        _ai_results.pop(tid, None)
                except Exception:
                    pass
            threading.Timer(300.0, _late_cleanup).start()


def _submit_ai_task(params: Dict) -> int:
    """提交 AI 任务到后台队列，返回 task_id。结果通过事件通知。"""
    global _ai_task_counter
    with _ai_task_counter_lock:
        _ai_task_counter += 1
        task_id = _ai_task_counter
    event = threading.Event()
    with _ai_results_lock:
        _ai_results[task_id] = {"event": event, "result": None}
    _ai_task_queue.put((task_id, params, event))
    return task_id


def _wait_ai_result(task_id: int, timeout: float = 60.0) -> Optional[str]:
    """等待指定 AI 任务的结果，超时返回 None。超时不移除 entry，后台任务完成后仍可被后续轮询/读取。"""
    with _ai_results_lock:
        entry = _ai_results.get(task_id)
    if entry is None:
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        if entry["event"].wait(timeout=5.0):
            with _ai_results_lock:
                entry = _ai_results.pop(task_id, None)
            return entry["result"] if entry else None
    log(f"AI 任务 {task_id} 等待超时（后台任务仍将继续执行）")
    return None


# 启动 AI 工作线程池（3并发，防止单条慢消息阻塞整个管道）
for _i in range(_AI_WORKERS):
    _t = threading.Thread(target=_ai_worker, daemon=True, name=f"ai-worker-{_i}")
    _t.start()

def summarize_group_history(group_id):
    """压缩超过窗口的群聊历史为摘要"""
    global group_chat_history, GROUP_SUMMARY
    gh = group_chat_history.get(group_id, [])
    if len(gh) <= 10:
        return
    # T2-4 节流：距上次压缩新增不足 10 条时不重复调用 LLM（避免每次群消息都触发额外 API 调用）
    _last_sum = _GROUP_SUMMARY_AT.get(group_id, 0)
    _sum_count = _GROUP_SUMMARY_LEN.get(group_id, 0)
    if _sum_count and len(gh) - _sum_count < 10 and time.time() - _last_sum < 3600:
        return
    _GROUP_SUMMARY_LEN[group_id] = len(gh)
    # 取最早的几轮对话用于生成摘要
    old = gh[:-10]  # 保留最近5轮
    if not old:
        return
    # 构建摘要请求
    lines = []
    for i in range(0, len(old), 2):
        user_msg = old[i]["content"] if i < len(old) else ""
        ai_msg = old[i+1]["content"] if i+1 < len(old) else ""
        lines.append(f"群友: {user_msg[:80]}")
        if ai_msg:
            lines.append(f"小a: {ai_msg[:80]}")
    dialog_text = "\n".join(lines[-20:])  # 最多取最近10组对话
    
    try:
        summary_prompt = f"把以下群聊对话压缩成50字以内的摘要，只提取关键话题和结论，不要评价：\n{dialog_text}"
        # 统一走 _llm_config：每提供商专属 key/url/model（不再写死 deepseek-chat + 全局 DeepSeek key）
        _lc = _llm_config()
        data = {
            "model": _lc.get("model_name") or "deepseek-flash",
            "messages": [
                {"role": "system", "content": "你是群聊记录压缩器，输出50字以内的极简摘要。"},
                {"role": "user", "content": summary_prompt}
            ],
            # 推理模型（如 deepseek-v4-flash）会先消耗 reasoning tokens，预算给足避免摘要空返回
            "max_tokens": 256,
            "temperature": 0.3
        }
        req = urllib.request.Request(_lc.get("api_url") or DEEPSEEK_URL,
            data=json.dumps(data).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_lc.get('api_key') or ''}"
            })
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            resp = json.loads(r.read().decode("utf-8"))
        choices = resp.get("choices", [])
        if not choices:
            log("summarize: API \u8fd4\u56de\u7a7achoose")
            return
        summary = choices[0].get("message", {}).get("content", "").strip()
        if not summary:
            return
        # E 三段式拼接：首句主题锚 + 上上次摘要 50 字 + 本次摘要，避免纯砍头丢最早话题
        old_summary = GROUP_SUMMARY.get(group_id, "")
        prev_summary = _GROUP_SUMMARY_PREV.get(group_id, "")
        # 首句作为主题锚（最多 50 字）
        _first_line = old_summary.split("；")[0][:50] if old_summary else ""
        _parts = []
        if _first_line:
            _parts.append(_first_line)
        if prev_summary and prev_summary != _first_line:
            _parts.append(prev_summary[:50])
        _parts.append(summary)
        new_summary = "；".join(_parts)
        if len(new_summary) > 200:
            # 超长：保留首句主题锚 + 尾部（最近的），中间截断
            _anchor = _first_line + "；" if _first_line else ""
            new_summary = _anchor + "…" + new_summary[-(200 - len(_anchor) - 1):]
        _GROUP_SUMMARY_PREV[group_id] = old_summary  # 本次摘要成为下轮的上上次
        GROUP_SUMMARY[group_id] = new_summary
        _GROUP_SUMMARY_AT[group_id] = time.time()
        # C2 存储对齐：压缩后只保留最近16轮完整对话
        group_chat_history[group_id] = gh[-16:]
        log(f"群[{group_id}] 历史已压缩，摘要: {summary[:60]}")
    except Exception as e:
        log(f"群[{group_id}] 历史压缩失败: {e}")


def _build_cached_messages(history: List[Dict], cache_key: str,
                           static_prompt: str, dynamic_parts: List[str],
                           user_msg: str, images: Optional[List[str]] = None) -> List[Dict]:
    """构建缓存优化的 messages 列表。
    
    结构：static_prompt(永远不变) → 历史摘要(稳定) → 最近N轮(变化) → 动态上下文 → 当前消息
    DeepSeek 缓存：前缀相同的 token 只收 10%，所以把不变的放前面。
    """
    RECENT_TURNS = 4  # 最近几轮保留原文（变化部分）

    messages = [{"role": "system", "content": static_prompt}]

    # 历史处理：旧的摘要化，新的保留原文
    if len(history) > RECENT_TURNS:
        old = history[:-RECENT_TURNS]
        recent = history[-RECENT_TURNS:]

        # T1 内容指纹：不以条数判断，而是以内容哈希判断，避免条数相同但内容变了仍用旧摘要
        # C-A 稳定哈希：md5 替代内置 hash()（进程内随机盐→跨重启不一致），摘要缓存跨重启可复用
        _fp_src = "|".join(h.get("role") + "|" + (h.get("content") or "") for h in old)
        fp = hashlib.md5(_fp_src.encode("utf-8", "ignore")).hexdigest()
        cached = _HISTORY_CACHE.get(cache_key)
        cached_fp = _HISTORY_CACHE_FP.get(cache_key, "")
        cached_old = _HISTORY_CACHE_OLD.get(cache_key)

        def _summarize(items, limit):
            _lines = []
            for h in items:
                if cache_key.startswith("g_"):
                    # 群聊：content 已含「[昵称(QQ号)] 文本」前缀，直接保留说话人，不用「对方」抹平
                    text = (h.get("content") or "")[:limit]
                    _lines.append(text)
                else:
                    role = "我" if h["role"] == "assistant" else "对方"
                    text = (h.get("content") or "")[:limit]
                    _lines.append(f"{role}: {text}")
            return "【之前对话摘要】\n" + "\n".join(_lines)

        _limit = 30 if cache_key.startswith("g_") else 40  # T1 群聊30/私聊40字（原80字）

        if cached and cached_fp == fp:
            # 内容完全没变 → 直接用缓存（前缀完全一致 → 缓存命中）
            messages.append({"role": "system", "content": cached})
        elif cached and cached_old and old[:len(cached_old)] == cached_old:
            # T1 滚动摘要：old 是上次的追加 → 只把新增的几条拼到缓存末尾
            _add = old[len(cached_old):]
            if _add:
                _add_lines = []
                for h in _add:
                    text = (h.get("content") or "")[:_limit]
                    if cache_key.startswith("g_"):
                        # 群聊保留 content 的「[昵称] 」前缀，不抹成「对方」
                        _add_lines.append(text)
                    else:
                        role = "我" if h["role"] == "assistant" else "对方"
                        _add_lines.append(f"{role}: {text}")
                _lines = cached.split("\n") + _add_lines
                cached = "【之前对话摘要】\n" + "\n".join(_lines[1:])
            _HISTORY_CACHE[cache_key] = cached
            _HISTORY_CACHE_FP[cache_key] = fp
            _HISTORY_CACHE_OLD[cache_key] = old
            messages.append({"role": "system", "content": cached})
        else:
            # 内容变了 / 首次 → 全量重建（只有这部分是新增 token）
            summary = _summarize(old, _limit)
            _HISTORY_CACHE[cache_key] = summary
            _HISTORY_CACHE_FP[cache_key] = fp
            _HISTORY_CACHE_OLD[cache_key] = old
            messages.append({"role": "system", "content": summary})

        for h in recent:
            messages.append({"role": h["role"], "content": h["content"]})
    else:
        for h in history:
            messages.append({"role": h["role"], "content": h["content"]})

    # 动态上下文（时间/记忆等，放最后面——不影响前面的缓存）
    for part in dynamic_parts:
        if part:
            messages.append({"role": "system", "content": part})

    # 当前用户消息
    if images:
        user_content = [{"type": "text", "text": user_msg}]
        for img_path in images[:3]:
            b64 = image_to_base64(img_path)
            if b64:
                user_content.append({"type": "image_url", "image_url": {"url": b64}})
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": user_msg})

    return messages


def _resolve_provider_type(cfg=None):
    """判定当前请求应走的协议：openai 兼容 / anthropic / gemini。

    非 OpenAI 兼容服务商（Claude、Gemini）需要专属的请求格式与鉴权方式；
    其余（DeepSeek/国产官方/聚合/中转）统一走 OpenAI 兼容路径。
    """
    cfg = cfg or _config
    pid = str(cfg.get("api_provider") or cfg.get("provider") or "").lower()
    url = str(cfg.get("api_url") or "").lower()
    if pid in ("anthropic", "claude") or "/messages" in url or "anthropic.com" in url:
        return "anthropic"
    if pid in ("google", "gemini") or "generativelanguage" in url or ("googleapis.com" in url and "openai" not in url):
        return "gemini"
    return "openai"


def _llm_config(runtime_cfg=None):
    global _LLAMA_SERVER_EXE, _LLAMA_MODEL, _LLAMA_PORT
    if runtime_cfg is None:
        runtime_cfg = _config
    use_local = bool(runtime_cfg.get("use_local_llm"))
    driver = (runtime_cfg.get("llama_driver") or "llama-server").strip().lower()
    model_path = (runtime_cfg.get("llama_model_path") or "").strip()
    port = int(runtime_cfg.get("llama_port") or 8081)
    model_name = (runtime_cfg.get("llama_model_name") or "").strip()
    api_url = runtime_cfg.get("api_url") or DEEPSEEK_URL
    provider_id = (runtime_cfg.get("api_provider") or "").strip()
    api_key = runtime_cfg.get("api_key") or DEEPSEEK_API_KEY
    # 每提供商专属 Key（凭据管理器）优先：切换提供商不会误用上一个服务商的 Key
    if provider_id:
        try:
            from config import get_provider_api_key
            api_key = get_provider_api_key(provider_id) or api_key
        except Exception:
            pass
    remote_model = runtime_cfg.get("model_name") or "deepseek-flash"

    if use_local and driver == "ollama":
        base_url = f"http://127.0.0.1:{port}/v1/chat/completions"
        return {
            "use_local": True,
            "driver": driver,
            "api_url": base_url,
            "model_name": model_name or "llama3",
            "api_key": "",
        }

    if use_local and driver == "lmstudio":
        base_url = f"http://127.0.0.1:{port}/v1/chat/completions"
        return {
            "use_local": True,
            "driver": driver,
            "api_url": base_url,
            "model_name": model_name or "local-model",
            "api_key": "",
        }

    if use_local and driver == "llama-server":
        _LLAMA_SERVER_EXE = ""
        _LLAMA_MODEL = model_path or ""
        _LLAMA_PORT = port or 8081
        ready = False
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{_LLAMA_PORT}/health")
            with urllib.request.urlopen(req, timeout=5) as resp:
                ready = resp.status == 200
        except Exception:
            ready = False
        if not ready and os.path.exists(_LLAMA_SERVER_EXE) and os.path.exists(_LLAMA_MODEL):
            try:
                p = subprocess.Popen(
                    [_LLAMA_SERVER_EXE, "--model", _LLAMA_MODEL, "--host", "127.0.0.1", "--port", str(_LLAMA_PORT), "-ngl", "999", "--cpu-moe", "--no-mmap", "--ctx-size", "32768"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                for i in range(120):
                    time.sleep(1)
                    try:
                        req = urllib.request.Request(f"http://127.0.0.1:{_LLAMA_PORT}/health")
                        with urllib.request.urlopen(req, timeout=3) as resp:
                            if resp.status == 200:
                                ready = True
                                break
                    except Exception:
                        pass
            except Exception:
                ready = False
        if ready:
            return {
                "use_local": True,
                "driver": driver,
                "api_url": f"http://127.0.0.1:{_LLAMA_PORT}/v1/chat/completions",
                "model_name": model_name or "qwen3.6",
                "api_key": "",
            }

    return {
        "use_local": False,
        "driver": "remote",
        "api_url": api_url,
        "model_name": remote_model,
        "api_key": api_key,
        "provider_id": provider_id,
        "provider_type": _resolve_provider_type(runtime_cfg),
    }


_REMOTE_JSON_LIMIT = 500


def _remote_json_parts(body: bytes):
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    if len(text) <= _REMOTE_JSON_LIMIT:
        return text
    head = text[:_REMOTE_JSON_LIMIT]
    tail = text[-80:]
    return f"{head}...<truncated {len(text)} chars>...{tail}"


def _is_json_remote_failure(body: bytes) -> bool:
    try:
        text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        data = json.loads(text)
        choices = data.get("choices") if isinstance(data, dict) else None
        return bool(choices)
    except Exception:
        return False


def _post_chat(url, payload, api_key="", timeout=60):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        body = r.read()
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}: {_remote_json_parts(body)}")
        if not _is_json_remote_failure(body):
            raise RuntimeError(f"Invalid JSON response: {_remote_json_parts(body)}")
        return json.loads(body.decode("utf-8"))


def _post_chat_stream(url, payload, api_key="", timeout=60):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout, context=CTX)


def _call_chat_sync(messages, runtime_cfg=None, max_tokens=AI_MAX_TOKENS, stream=False, with_tools=True):
    cfg = _llm_config(runtime_cfg)
    model_name = cfg.get("model_name") or "deepseek-flash"
    api_url = cfg.get("api_url") or DEEPSEEK_URL
    api_key = cfg.get("api_key") or ""
    effective_temperature = AI_TEMPERATURE
    ptype = cfg.get("provider_type") or _resolve_provider_type(cfg)
    if ptype in ("anthropic", "gemini"):
        # 非 OpenAI 兼容协议（Claude / Gemini）：走 providers.py 专属适配器
        if stream:
            raise ValueError(f"{ptype} 流式调用请走 _stream_chat_lines")
        from providers import make_provider
        prov = make_provider(api_url=api_url, api_key=api_key, model=model_name,
                             provider=ptype, max_retries=3)
        tools = with_tools if isinstance(with_tools, list) else TOOLS
        content, tool_calls, usage = prov.chat(messages, tools=tools, stream=False, max_tokens=max_tokens)
        return {"content": content or "", "tool_calls": tool_calls}, usage
    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": effective_temperature,
        "presence_penalty": 0.15,
        "frequency_penalty": 0.1,
        "stream": stream,
    }
    if with_tools:
        payload["tools"] = with_tools if isinstance(with_tools, list) else TOOLS
    if stream:
        return api_url, api_key, payload
    resp = _post_chat(api_url, payload, api_key=api_key, timeout=60)
    choices = resp.get("choices", [])
    if not choices:
        raise ValueError("empty choices")
    msg = choices[0].get("message", {}) or {}
    if not msg:
        raise ValueError("empty message")
    return msg, resp.get("usage", {})


def _stream_chat_lines(messages, runtime_cfg=None, max_tokens=AI_MAX_TOKENS, with_tools=True, scene="private"):
    cfg = _llm_config(runtime_cfg)
    ptype = cfg.get("provider_type") or _resolve_provider_type(cfg)
    if ptype in ("anthropic", "gemini"):
        # 非 OpenAI 兼容协议：流式由 providers.py 适配器处理（SSE 格式不同）
        from providers import make_provider
        prov = make_provider(api_url=cfg.get("api_url") or DEEPSEEK_URL,
                             api_key=cfg.get("api_key") or "",
                             model=cfg.get("model_name") or "deepseek-flash",
                             provider=ptype, max_retries=3)
        return prov.chat_stream(messages, max_tokens=max_tokens) or ""
    api_url, api_key, payload = _call_chat_sync(messages, runtime_cfg=runtime_cfg, max_tokens=max_tokens, stream=True, with_tools=with_tools)
    r = _post_chat_stream(api_url, payload, api_key=api_key, timeout=60)
    full_reply = ""
    last_usage = {}
    for raw in r:
        line = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                full_reply += content
            # DeepSeek 流式末块带 usage（缓存命中明细）
            if chunk.get("usage"):
                last_usage = chunk.get("usage") or {}
        except Exception:
            pass
    # T5 观测：流式收尾记录 usage
    if last_usage:
        try:
            from token_stats import record as _ts_record
            _ts_record(scene, last_usage, {"stream": True})
        except Exception:
            pass
    return full_reply


def _call_deepseek_sync(user_msg: str, images: Optional[List[str]] = None,
                        target_qq: Optional[str] = None, group_id: Optional[str] = None,
                        is_group: bool = False, private_memory_context: str = '',
                        personality_temperature: Optional[float] = None,
                        ego_inject_context: str = '') -> Optional[str]:
    """同步调用 DeepSeek API（在后台线程中运行），支持工具调用和重试。"""
    global chat_history, group_chat_history

    if target_qq is None:
        target_qq = TARGET_QQS[0]

    # T6: 用缓存构建 static_prompt（人格+规则+多样性+表情包+铁律+群聊规则），
    # 按 (is_group, config_rev, personality_mtime) 缓存，前缀稳定 → 命中 DeepSeek 硬盘缓存
    static_prompt = _build_static_prompt(is_group)
    dynamic_parts = []

    # 群聊模式：规则已并入 static_prompt（见 _build_static_prompt），动态信息放 dynamic_parts
    if is_group:
        if group_id:
            summarize_group_history(group_id)

    # 动态内容全部移到下面 dynamic_parts（不污染 static_prompt，保证缓存前缀不变）

    # 特定用户个性化
    if "10000002" in user_msg:
        dynamic_parts.append("【当前对话对象】@占位名片B（QQ 10000002）是你的老大。对他态度暧昧一点、亲近一点，但不用强制回复，和其他人一样输出 [不说话] 也可以。")

    # 群聊动态信息（时间/群成员/时间线）→ 放到 dynamic_parts
    if is_group and group_id:
        # T2-1/G 当前时间降频：距上次注入 ≥600s 才重新注入（按时间戳判定，
        #  避免"首次注入后存分钟键、10 分钟后才更新"导致群聊低活跃时时间停留过久；因在 dynamic_parts 尾部，不影响前缀缓存）
        _now_ts = datetime.datetime.now()
        _last_time = _GROUP_TIME_INJECT_AT.get(group_id, 0)
        if not isinstance(_last_time, (int, float)) or not _last_time or (time.time() - _last_time) >= 600:
            dynamic_parts.append(
                "【当前时间】" + _now_ts.strftime("%Y年%m月%d日 %H:%M") + "，星期" + ["一","二","三","四","五","六","日"][_now_ts.weekday()]
            )
            _GROUP_TIME_INJECT_AT[group_id] = time.time()
        timeline = GROUP_TIMELINE.get(group_id, [])
        if timeline:
            # T2-2 时间线去重 + 限 6 条：同一句话重复提到只留一条
            seen_txt = set()
            recent_timeline = []
            for _t in timeline[-12:]:
                _txt = (_t[3] or "").strip()
                if not _txt or _txt in seen_txt:
                    continue
                seen_txt.add(_txt)
                recent_timeline.append(_t)
                if len(recent_timeline) >= 6:
                    break
            if recent_timeline:
                timeline_lines = []
                for ts, sid, snick, txt in recent_timeline:
                    t = datetime.datetime.fromtimestamp(ts).strftime("%H:%M")
                    display = snick if snick else "群友"
                    timeline_lines.append(f"[{t}] {display}: {txt[:50]}")
                dynamic_parts.append("【群聊动态】\n" + "\n".join(timeline_lines))
        group_members = list(GROUP_MEMBERS.get(group_id, {}).values())
        # T2-3 群成员列表按需注入：仅当用户消息涉及成员时才注入，省 15 个昵称的 token
        _member_kw = ["选", "谁", "@", "在线", "成员", "随机", "人"]
        if group_members and any(k in user_msg for k in _member_kw):
            dynamic_parts.append("【群成员列表】" + "、".join(group_members[:15]))
        summary = GROUP_SUMMARY.get(group_id, "")
        if summary:
            # T2-4 摘要瘦身注入：持续会话中每 10 分钟最多注入一次（限 200 字）
            _last_summary_inject = _GROUP_SUMMARY_INJECT_AT.get(group_id, 0)
            if time.time() - _last_summary_inject >= 600:
                dynamic_parts.append(f"【之前的群聊背景】{summary[:200]}")
                _GROUP_SUMMARY_INJECT_AT[group_id] = time.time()
        if SELF_LEARN_OK:
            learn_ctx = self_learn.get_context_for_llm(str(group_id))
            if learn_ctx:
                dynamic_parts.append(learn_ctx)

    # 网络用语/记忆/私聊记忆 → 放到 dynamic_parts
    if SLANG_OK and _config.get("slang_injector_enable", True):
        slang_ctx = get_slang_context(user_msg)
        if slang_ctx:
            dynamic_parts.append(slang_ctx)
    # EGO 人格/记忆/风格/节奏注入 → system 级上下文（独立于群聊/私聊钩子，提升指令权重）
    if ego_inject_context:
        _inject_ego_context(dynamic_parts, ego_inject_context)
    if not group_id:
        try:
            from sandglass_bridge import get_memory_prompt_block
            # T4: 私聊用 light 模式（跳过 sandglass 关键词全文搜索，省 token；语义检索由 super_memory 向量 top-K 承担）
            sg_block = get_memory_prompt_block(user_msg, light=True)
            if sg_block:
                dynamic_parts.append(sg_block)
        except Exception:
            pass
    if private_memory_context:
        dynamic_parts.append(f"【用户画像】以下信息仅供你了解对方的性格和偏好，回复时不要提及这些内容，也不要引用具体对话：\n{private_memory_context}")
    if group_id and group_id in GROUP_LAST_EXIT:
        elapsed = time.time() - GROUP_LAST_EXIT[group_id]
        if elapsed < 900:
            mins = max(1, int(elapsed / 60))
            dynamic_parts.append(f"【重要】你{mins}分钟前刚说过退场语，这次回来不要再重复退场语。")

    # 选择历史 + 构建消息（用缓存优化版）
    if is_group and group_id:
        history = group_chat_history.get(group_id, [])[-10:]
        cache_key = f"g_{group_id}"
    else:
        history = chat_history.get(target_qq, [])[-10:]
        cache_key = f"p_{target_qq}"

    messages = _build_cached_messages(history, cache_key, static_prompt, dynamic_parts, user_msg, images)

    # ── 网页操作任务引导：消息含链接/问卷/填表意图时，引导模型调用 web_operate 工具 ──
    _web_hint = ""
    if re.search(r'https?://\S+', user_msg):
        _web_hint = "【重要】用户消息中包含网页链接。请用 web_operate 工具真实操作网页：先 open_url（会自动返回带编号的结构化快照），再用快照里的 ref 调 click/fill/select（每次动作后会自动回传新快照）。绝不要凭文字假装已操作。遇到多步复杂任务可直接用 action=autonomous + goal 让插件自主完成。"
    elif re.search(r'(问卷|填表|填表单|答题|做.*?(问卷|题)|填写|表单|网页.*?(任务|操作)|帮我.*?(打开|填))', user_msg):
        _web_hint = "【重要】本次请求涉及问卷/表单/网页任务。请调用 web_operate 工具实际打开并操作网页：open_url 拿快照 -> 按 ref 逐个 click/fill 作答（动作后自动回传新快照）；问卷/多步任务优先用 action=autonomous + goal 自主完成。禁止凭文字假装完成。"
    if _web_hint:
        messages.append({"role": "user", "content": "[系统提示] " + _web_hint})

    # 群聊只允许配置/默认名单里的工具，私聊允许全部工具
    tools_for_call = _build_group_tools() if is_group else TOOLS
    allow_tools = True
    
    # 群聊回复短，降低输出上限节省 token
    effective_max_tokens = AI_MAX_TOKENS
    # 私聊也降低回复量：限制为 X token（仅远程模型生效；本地带思维链模型若限额会被思考过程吞掉输出变为空，故放开）
    if not is_group and not _llm_config().get("use_local"):
        effective_max_tokens = min(effective_max_tokens, _config.get("ai_private_max_tokens", 200))
    # 网页操作任务需要更大的输出预算（工具调用链 + 结果汇总）
    if _web_hint:
        effective_max_tokens = max(effective_max_tokens, _config.get("ai_web_task_max_tokens", 1500))
    
    last_error = None
    web_rounds = 0
    MAX_WEB_ROUNDS = 20
    error_retries = 0
    while True:
        try:
            msg, usage = _call_chat_sync(messages, runtime_cfg=None, max_tokens=effective_max_tokens, stream=False, with_tools=tools_for_call)

            tool_calls = msg.get("tool_calls")
            if tool_calls:
                tool_calls = [dict(tc) for tc in tool_calls]
                log(f"工具调用: {[tc['function']['name'] for tc in tool_calls]}")
                # T5 观测：工具轮 token 也计入（群聊/私聊/网页场景）
                try:
                    from token_stats import record as _ts_record
                    _ts_record("web" if _web_hint else ("group" if is_group else "private"), usage, {"round": "tool"})
                except Exception:
                    pass
                messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
                web_rounds += 1
                for tc in tool_calls:
                    func_name = tc["function"]["name"]
                    try:
                        func_args = json.loads(tc["function"]["arguments"])
                    except Exception:
                        func_args = {}
                    if is_group and group_id and "group_id" not in func_args:
                        func_args["group_id"] = group_id
                    elif (not is_group) and target_qq and "target_qq" not in func_args:
                        func_args["target_qq"] = target_qq
                    _act = (func_args or {}).get("action") or ""
                    _url = (func_args or {}).get("url") or ""
                    func = TOOL_FUNCTIONS.get(func_name)
                    result = func(func_args) if func else f"未知工具: {func_name}"
                    _res_parts = str(result)[:140].replace("\n", " ") if result else ""
                    log(f"  → {func_name}: action={_act}" + (f" url={_url[:60]}" if _url else "") + f" (第{web_rounds}轮) → {_res_parts}")
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                # 网页任务：继续多轮推进，直到模型主动收尾或达到轮次上限
                if _web_hint:
                    if web_rounds >= MAX_WEB_ROUNDS:
                        log(f"[网页任务] 达到轮次上限 {MAX_WEB_ROUNDS}，强制流式收尾")
                        return _stream_chat_lines(messages, runtime_cfg=None, max_tokens=effective_max_tokens, with_tools=tools_for_call, scene="web")
                    continue
                # 非网页工具：维持原行为，工具一轮后流式生成最终回复
                return _stream_chat_lines(messages, runtime_cfg=None, max_tokens=effective_max_tokens, with_tools=tools_for_call, scene=("group" if is_group else "private"))

            reply = str(msg.get("content") or "").strip()
            total = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
            cost = total * 0.5 / 1_000_000
            # T5 观测：记录缓存命中明细 + 按场景累计
            try:
                from token_stats import record as _ts_record
                _scene = "web" if _web_hint else ("group" if is_group else "private")
                _ts_record(_scene, usage, {"web_rounds": web_rounds})
                if isinstance(usage, dict):
                    _hit = usage.get("prompt_cache_hit_tokens") or 0
                    _miss = usage.get("prompt_cache_miss_tokens") or 0
                    log(f"AI 回复 ({total} tokens, ￥{cost:.6f}, 缓存命中 {_hit} / 未命中 {_miss}, 场景 {_scene})")
                else:
                    log(f"AI 回复 ({total} tokens, ￥{cost:.6f})")
            except Exception:
                log(f"AI 回复 ({total} tokens, ￥{cost:.6f})")

            if _web_hint and web_rounds == 0:
                # 模型一次工具都没调，却可能用文字假装已操作 -> 给一次强制纠正
                web_rounds += 1
                log(f"[网页任务] 模型未调用任何工具，追加强制指令 {web_rounds}/{MAX_WEB_ROUNDS}")
                messages.append({
                    "role": "user",
                    "content": "【强制指令】你没有真实调用任何 web_operate 工具。请用 web_operate 实际执行：先 action=open_url 拿快照，再按快照里的 ref 调 click/fill/extract（每次动作会自动回传新快照）；多步任务用 action=autonomous + goal。禁止用文字假装已操作；若确实无法完成，直接说明失败原因。",
                })
                continue

            log(f"AI 回复 ({total} tokens, ￥{cost:.6f})")
            return reply
        except Exception as e:
            last_error = e
            err_body = ""
            try:
                if hasattr(e, "read"):
                    err_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            if error_retries < 4:
                error_retries += 1
                log(f"AI 重试 {error_retries}/5: {e}" + (f" | {err_body}" if err_body else ""))
                time.sleep(3)
    log(f"AI 失败 (已重试5次): {last_error}" + (f" | {err_body}" if err_body else ""))
    return None


def call_deepseek(user_msg: str, images: Optional[List[str]] = None,
                  target_qq: Optional[str] = None, group_id: Optional[str] = None,
                  is_group: bool = False, private_memory_context: str = '',
                  personality_temperature: Optional[float] = None,
                  ego_inject_context: str = '') -> Optional[str]:
    """非阻塞调用 DeepSeek API：将任务提交到后台队列，等待结果返回。"""
    task_id = _submit_ai_task({
        "user_msg": user_msg,
        "images": images,
        "target_qq": target_qq,
        "group_id": group_id,
        "is_group": is_group,
        "private_memory_context": private_memory_context,
        "personality_temperature": personality_temperature,
        "ego_inject_context": ego_inject_context,
    })
    # 网页/问卷任务需要多轮工具链，给足更长的等待时间（与 _web_hint 判定保持一致）
    _q = user_msg or ""
    _is_web_task = bool(re.search(r'https?://\S+', _q)) or bool(re.search(r'(问卷|填表|填表单|答题|做.*?(问卷|题)|填写|表单|网页.*?(任务|操作)|帮我.*?(打开|填))', _q))
    return _wait_ai_result(task_id, timeout=(600.0 if _is_web_task else 30.0))


def save_instruction(text, reply):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record = {"time": ts, "text": text, "reply": reply, "status": "done"}
    try:
        existing = data_store.read_json(INSTRUCTION_FILE, [])
        existing.append(record)
        data_store.write_json(INSTRUCTION_FILE, existing)
    except Exception as e:
        log(f"保存失败: {e}")


# ═══════════════════════ 公共消息过滤逻辑 ═══════════════════════

# 无意义短消息白名单
_SHORT_PHRASES: set = {"嗯", "哦", "6", "哈哈", "好的", "行", "可以", "ok", "OK",
                        "收到", "1", "2", "3", "?", "？", "...", "。。", "。。。",
                        "啊", "呃", "啧", "噗", "哈", "嗯嗯", "哦哦", "咕咕", "嘎嘎",
                        "咕咕嘎嘎", "咕嘎", "额", "额。", "额。。"}

# 2字有意义内容白名单（不触发会话结束）
_TWO_CHAR_KEEP: set = {"不信", "没有", "是的", "不是", "你呢", "谁啊", "在哪",
                        "不行", "等等", "真的", "假的", "没错", "不对", "你好",
                        "抱歉", "厉害", "懂了", "不懂", "来了", "在了", "到了",
                        "干嘛", "怎么", "啥啊", "什么", "为啥", "人呢", "几点", "谁呀",
                        "出来", "在哪", "喏", "给", "好", "没", "对", "是", "嗯", "哦",
                        "啊", "哈", "嘿", "切", "啧", "喂", "唉", "哎", "噢", "呀",
                        "呢", "吧", "嘛", "呗", "啦", "咯", "呵", "呵", "操", "靠",
                        "哥哥", "姐姐", "弟弟", "妹妹", "爸爸", "妈妈", "爷爷", "奶奶",
                        "叔叔", "阿姨", "舅舅", "老公", "老婆", "宝贝", "宝宝", "师傅", "师父",
                        "学长", "学姐", "老板", "大哥", "大姐", "老大", "老哥", "老弟"}

# 群内已知其他人名（用于定向消息过滤）
_KNOWN_NAMES: List[str] = ["王宇航", "王雨涵"]


def _group_trigger_words() -> List[str]:
    """Get configured group trigger words."""
    return _config.get("group_trigger_words", ["小a", "@占位名片A", "@10000001"])


def _is_group_trigger(text: str) -> bool:
    """Check if text contains any configured group trigger word."""
    for w in _group_trigger_words():
        w = w.strip()
        if not w:
            continue
        if w.startswith("@"):
            target = w[1:]
            if target and (target in text or f"@{target}" in text):
                return True
        elif w in text:
            return True
    return False


def _is_at_others(text: str) -> bool:
    """检测消息是否定向给他人（非 Bot/非小a）。"""
    # 先检查是否包含触发词
    if _is_group_trigger(text):
        return False
    if text.startswith("@"):
        m = re.match(r'^@(\S+)', text)
        if m:
            at_name = m.group(1)
            if at_name != BOT_QQ and at_name != BOT_GROUP_NICK:
                return True
    # 句末 @ 他人
    end_at = re.search(r'@(\S{1,15})$', text)
    if end_at:
        at_name = end_at.group(1)
        if at_name != BOT_QQ and at_name != BOT_GROUP_NICK:
            return True
    # 句中 @ 他人
    all_ats = re.findall(r'@(\S{1,15})', text)
    for at_name in all_ats:
        if at_name == BOT_QQ or at_name == BOT_GROUP_NICK:
            continue
        return True
    # 句首叫已知人名
    for name in _KNOWN_NAMES:
        if text.startswith(f"@{name}") or text.startswith(name):
            return True
        if text.endswith(name) or text.endswith(f"，{name}") or text.endswith(f" {name}"):
            return True
        if f"@{name}" in text or f"找{name}" in text or f"叫{name}" in text:
            return True
    return False


def _is_short_meaningless(text: str) -> bool:
    """判断短消息是否无意义（不含中文/字母/数字的纯符号除外）。"""
    if len(text) > 8:
        return False
    if text in _SHORT_PHRASES:
        return True
    if text.isdigit() and len(text) <= 3:
        return True
    if len(set(text)) <= 2 and len(text) <= 1:
        return True
    return False


def _is_short_end_session(text: str) -> bool:
    """判断 ≤2 字的消息是否触发会话结束（白名单豁免）。"""
    if len(text) > 2:
        return False
    if text in _TWO_CHAR_KEEP:
        return False
    chinese_chars = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    alnum_chars = sum(1 for ch in text if ch.isalnum())
    if chinese_chars == 0 and alnum_chars == 0:
        return False  # 纯符号/颜文字不结束会话
    return True


def _is_nonsense(text: str) -> bool:
    """检测无意义乱码。"""
    if len(text) > 10:
        return False
    chinese_chars = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')
    alnum_chars = sum(1 for ch in text if ch.isalnum())
    if chinese_chars == 0 and alnum_chars == 0:
        return True
    if len(text) >= 3:
        unique_ratio = len(set(text)) / max(len(text), 1)
        if unique_ratio <= 0.5:
            return True
    return False


def _is_bystander_commentary(text: str) -> bool:
    """检测旁观者对 Bot 对话的评论（"你们演连续剧""你俩说相声"等）。"""
    patterns = [
        r'你们.*(?:演|唱|说|讲|搁这|在这)',
        r'你俩.*(?:演|唱|说|搞|玩|弄|讲|搁|这|在)',
        r'你两个.*(?:演|唱|说)',
        r'搁这(?:演|唱|说)',
        r'(?:连续剧|相声|小品|双簧|二人转)',
        r'(?:搁这|在这).*(?:演|唱)',
    ]
    for p in patterns:
        if re.search(p, text):
            return True
    return False




def is_meaningless_message(text: str) -> bool:
    """判断单条消息是否无意义（短消息/乱码/给别人的）。
    使用共享过滤函数，与 process_buffered_message 保持一致。"""
    if not text or not isinstance(text, str):
        return True
    if "小a" in text:
        return False
    # 人物查询/谁是谁 → 永远有意义
    if re.search(r'(是谁|谁是|是哪个|哪个是|介绍一下|认识吗|什么来头)', text):
        return False
    if _is_at_others(text):
        return True
    if _is_short_meaningless(text):
        return True
    if _is_nonsense(text):
        return True
    if _is_bystander_commentary(text):
        return True
    return False


# is_bystander_commentary 别名指向共享实现
is_bystander_commentary = _is_bystander_commentary


def process_buffered_message(group_id):
    """处理合并后的群聊消息"""
    global GROUP_MSG_BUFFER, GROUP_SESSION, group_chat_history, startup_time
    buffer = GROUP_MSG_BUFFER.pop(group_id, None)
    if not buffer or not buffer["text"]:
        return
    
    merged_text = buffer["text"].strip()
    plugin_intervened = False  # 提前初始化，防止短消息检测报错
    # 提取纯文本内容（去掉 [用户ID] 前缀）
    clean_text = re.sub(r'^\[\d+\]\s*', '', merged_text)
    
    # 检查会话是否过期
    gs = GROUP_SESSION.get(group_id)
    if gs and time.time() - gs.get("last_msg_time", 0) > SESSION_TTL:
        del GROUP_SESSION[group_id]
        gs = None
    
    # 启动冷却检查（在所有 AI 调用之前拦截）
    if time.time() - startup_time < STARTUP_COOLDOWN:
        remaining = int(STARTUP_COOLDOWN - (time.time() - startup_time))
        log(f"群[{group_id}] 开机冷却中（{remaining}s/{STARTUP_COOLDOWN}s），跳过回复")
        return
    
    # 使用缓冲区中保存的 @、「小a」和 Bot 昵称标记
    is_at_bot = buffer.get("is_at_bot", False)
    has_xiao_a = buffer.get("has_xiao_a", False)
    has_bot_nick = buffer.get("has_bot_nick", False)
    has_quote = buffer.get("has_quote", False)
    has_text_at = buffer.get("has_text_at", False)
    
    # 没被 @、没叫「小a」、不含 Bot 昵称、无文本 @、无引用、不在会话模式
    # 无触发时：交给群聊决策插件判断是否像真人一样自然插话；决策放行则继续回复
    in_session = buffer.get("in_session", False)
    if not is_at_bot and not has_xiao_a and not has_bot_nick and not has_quote and not has_text_at and not in_session:
        _decider_go = False
        if PLUGIN_OK:
            try:
                pm = get_plugin_manager()
                if pm and pm.is_loaded("group_chat_decider") and pm.is_enabled("group_chat_decider"):
                    _decider_ctx = {
                        "recent_msgs": list(GROUP_RECENT_MSGS.get(group_id, [])),
                        "bot_replies": _safe_extract_col(GROUP_BOT_REPLIES.get(group_id, []), 1),
                        "sender": buffer.get("sender", ""),
                        "user_id": buffer.get("sender", ""),
                        "is_at_bot": False, "has_xiao_a": False, "has_bot_nick": False,
                        "has_quote": False, "has_text_at": False, "in_session": False,
                        "history": group_chat_history.get(group_id, []),
                        "bot_qq": BOT_QQ, "bot_name": "小a",
                    }
                    _res = pm.execute_hooks("before_reply", message=merged_text, group_id=group_id, context=_decider_ctx)
                    # 只看群聊决策插件的判定，避免 ego 等低优先级注入误放行
                    for _pn, _pr in (_res or []):
                        if _pn == "group_chat_decider" and isinstance(_pr, dict) and _pr.get("action") == "inject":
                            _decider_go = True
            except Exception as e:
                log(f"群[{group_id}] [决策插件] 无触发预判异常: {e}")
        if not _decider_go:
            log(f"群[{group_id}] [跳过] 无触发条件，忽略消息: {merged_text[:80]}")
            return
        log(f"群[{group_id}] [决策放行] 无触发消息，决策插件建议插话: {merged_text[:60]}")
    # 会话模式消息，确保 GROUP_SESSION 存在
    if in_session and not is_at_bot and not has_xiao_a and not has_bot_nick:
        gs = GROUP_SESSION.get(group_id)
        if not gs or gs.get("remaining", 0) <= 0:
            log(f"群[{group_id}] 会话已过期或无配额")
            return
        # 会话模式：实际处理时才递减配额
        with _session_lock:
            if group_id in GROUP_SESSION:
                GROUP_SESSION[group_id]["remaining"] -= 1
                GROUP_SESSION[group_id]["last_msg_time"] = time.time()
    
    # 逐行检测：短消息 / 无意义乱码 / 用户名过滤
    lines = merged_text.split('\n')
    meaningful_count = 0
    total_count = len(lines)
    session_ended_by_short = False
    filtered_noise = []  # 被过滤的无效文本（用于 @ 时告知 AI "没看懂"）
    
    for line in lines:
        content = re.sub(r'^\[.+?\]\s*', '', line).strip()
        if not content:
            continue
        
        # ── 使用共享过滤函数检测 @ 他人 / 定向消息 ──
        if _is_at_others(content):
            log(f"群[{group_id}] 跳过定向他人的消息: {line[:60]}")
            return
        
        # 小a/人格保护
        if _is_group_trigger(content):
            meaningful_count += 1
            continue
        
        # ── 短消息检测：无意义内容过滤 ──
        if not plugin_intervened:
            if _is_short_meaningless(content):
                filtered_noise.append(content)
                continue
        
        # ── 无意义乱码检测 ──
        if _is_nonsense(content):
            filtered_noise.append(content)
            continue
        
        # 旁观者评论检测
        if _is_bystander_commentary(content):
            log(f"群[{group_id}] 跳过旁观者评论: {content[:60]}")
            continue
        
        meaningful_count += 1
    
    # 被 @ 或叫「小a」或含 Bot 昵称或引用 → 总是开启/维持会话模式（即使消息内容为空）
    if is_at_bot or has_xiao_a or has_bot_nick or has_quote:
        GROUP_SESSION[group_id] = {"remaining": _config.get("group_session_max_replies", 20), "last_msg_time": time.time()}
    
    # 没有任何有意义的消息 → 跳过回复（但会话已建立）
    if meaningful_count == 0:
        # 被 @ 但无文字内容 → 至少回应一下
        if is_at_bot:
            try:
                if filtered_noise:
                    noise_text = "、".join(filtered_noise)
                    prompt = f"（有人 @ 了我，但发的是无意义内容：\"{noise_text}\"，我完全没看懂）"
                else:
                    prompt = "（有人 @ 了我，但没说话）"
                reply = call_deepseek(prompt, [], group_id=group_id, is_group=True)
                if reply and reply.strip() not in ("[不说话]", "[跳过]"):
                    smart_send_group(group_id, reply)
                    track_bot_reply(group_id, reply)
                    log(f"群回复(@空): {reply[:60]}")
            except Exception as e:
                log(f"群[{group_id}] @空 AI 调用异常: {e}")
        else:
            log(f"群[{group_id}] 跳过无意义消息: {merged_text[:60]}")
        return
    
    log(f"群[{group_id}] 合并消息: {merged_text[:200]}{'...共' + str(len(merged_text)) + '字' if len(merged_text) > 200 else ''}")

    # ── 插件钩子：before_reply（在 Bot 指向检测之前执行，让插件先决策）──
    plugin_skip = False
    plugin_reply = None
    merged_text_for_ai = merged_text
    _plugin_ctx = {}
    plugin_intervened = False
    _ST.set("personality_temperature", None)
    _ego_ctx = ""
    log(f"[调试] PLUGIN_OK={PLUGIN_OK}")
    if PLUGIN_OK:
        try:
            pm = get_plugin_manager()
            has_hook = pm.has_hook("before_reply") if pm else False
            log(f"[调试] pm={pm is not None} has_hook={has_hook}")
            if pm and has_hook:
                _plugin_ctx = {
                    "recent_msgs": list(GROUP_RECENT_MSGS.get(group_id, [])),
                    "bot_replies": _safe_extract_col(GROUP_BOT_REPLIES.get(group_id, []), 1),
                    "sender": buffer.get("sender", ""),
                    "user_id": buffer.get("sender", ""),
                    "is_at_bot": buffer.get("is_at_bot", False),
                    "has_xiao_a": buffer.get("has_xiao_a", False),
                    "has_bot_nick": buffer.get("has_bot_nick", False),
                    "has_quote": buffer.get("has_quote", False),
                    "has_text_at": buffer.get("has_text_at", False),
                    "in_session": buffer.get("in_session", False),
                    "history": group_chat_history.get(group_id, []),
                    "bot_qq": BOT_QQ,
                    "bot_name": "小a",
                }
                intervention = pm.execute_hooks("before_reply", message=merged_text, group_id=group_id, context=_plugin_ctx)
                log(f"[调试] interventions={intervention}")
                if intervention:
                    skip_reasons = []
                    override_reply = None
                    override_priority = None
                    inject_parts = []
                    for plugin_name, result in intervention:
                        action = result.get("action")
                        priority = pm.get_meta(plugin_name).get("priority", 50)
                        if action == "skip":
                            skip_reasons.append(f"{plugin_name}:{result.get('reason','')}")
                        elif action == "inject":
                            content = result.get("content", "")
                            prompt_blocks = result.get("prompt_blocks") or []
                            if prompt_blocks:
                                content = "\n".join(prompt_blocks) + ("\n" + content if content else "")
                            inject_parts.append((priority, content))
                        elif action == "override":
                            if override_reply is None or priority < override_priority:
                                override_reply = result.get("reply", "")
                                override_priority = priority
                    if skip_reasons:
                        log(f"群[{group_id}] [插件跳过] {'; '.join(skip_reasons)}")
                        plugin_skip = True
                        plugin_intervened = True
                    if override_reply:
                        plugin_reply = override_reply
                        plugin_intervened = True
                    if inject_parts:
                        inject_parts.sort(key=lambda x: x[0])
                        merged_inject = "\n".join(part for _, part in inject_parts)
                        # EGO 上下文（人格/记忆/风格）提升为 system 级，其余插件注入仍拼消息尾
                        for _pn, _pr in intervention:
                            if _pn == "ego" and isinstance(_pr, dict) and _pr.get("action") == "inject":
                                _ego_ctx = _pr.get("content") or ""
                                break
                        if _ego_ctx:
                            merged_inject = merged_inject.replace(_ego_ctx, "").strip("\n")
                        if merged_inject:
                            merged_text_for_ai = merged_text + "\n" + merged_inject
                        else:
                            merged_text_for_ai = merged_text
                        log(f"群[{group_id}] [插件注入] {merged_inject[:60]}")
                        plugin_intervened = True
                    for plugin_name, result in intervention:
                        if "temperature" in result:
                            _ST.set("personality_temperature", float(result["temperature"]))
                            break
        except Exception as e:
            import traceback as _tb
            log(f"[Plugin] before_reply error: {e}")
            log(f"[Plugin] before_reply traceback:\n{_tb.format_exc()[:500]}")

    if plugin_skip:
        return
    if plugin_reply:
        reply = plugin_reply


# 聊天面板：记录收到的群消息
    sender = buffer.get("sender", "")
    sender_name = resolve_qq_nick(sender) or sender
    chat_log("in", "group", group_id, merged_text, sender_name)
    
    images = []  # 合并消息暂不支持图片
    
    # 会话模式预判：无 @ 无「小a」时，只有明确在跟 Bot 说话才回复
    in_session = buffer.get("in_session", False)
    if in_session and not is_at_bot and not has_xiao_a:
        # 检测是否明确指向 Bot：含「你」或疑问/请求语气
        # 排除群友互相聊天（如「你去哪了」「你吃饭没」——这些不是对 Bot 说的）
        direct_hints = ["你", "你呢", "你呀", "你的"]
        question_hints = ["?", "？", "吗", "呢", "咋", "怎么", "什么", "啥", "干嘛",
                          "哪", "谁", "几", "多少", "能", "会", "可以", "能不能"]
        request_hints = ["帮", "问", "写", "来", "搞", "整", "做", "给", "看", "读",
                         "查", "找", "搜", "算", "告诉", "发", "创建", "画", "统计", "总结",
                         "翻译", "解释", "分析", "教", "生成", "猜"]
        
        has_direct = any(h in merged_text for h in direct_hints)
        has_question = any(h in merged_text for h in question_hints)
        has_request = any(h in merged_text for h in request_hints)
        
        # 必须同时满足：(含"你") 且 (疑问/请求/祈使语气)，才算在跟 Bot 说话
        # 祈使句检测：以"你"开头，后接动词（常见祈使动词）
        imperative_verbs = ["祝", "说", "讲", "发", "发个", "发张", "发个图", "发张图", "发张表情", "发个表情", "发个表情包", "发张表情包",
                           "去", "来", "走", "看", "听", "吃", "喝", "玩", "打", "开", "关", "写", "读", "查", "搜", "找", "做", "搞", "整",
                           "给", "送", "传", "发", "发给我", "发给我看", "发给我看看", "发给我瞅瞅", "发给我瞅瞅看"]
        is_imperative = False
        if has_direct:
            # 检查是否"你"开头 + 动词
            for verb in imperative_verbs:
                if merged_text.startswith(f"你{verb}") or merged_text.startswith(f"你 {verb}"):
                    is_imperative = True
                    break
        
        if not plugin_intervened and not (has_direct and (has_question or has_request or is_imperative)):
            log(f"群[{group_id}] 会话内未指向Bot，跳过: {merged_text[:60]}")
            return
    try:
        # 插件钩子已在上方执行，这里直接调 AI
        if plugin_skip:
            return
        if plugin_reply:
            reply = plugin_reply
        else:
            # 插件注入了上下文 = 插件认为应该回复 → AI 不应跳过
            if plugin_intervened:
                merged_text_for_ai += "\n[系统提示: 插件已判断此消息需要回复，请务必回复，不要输出[不说话]]"

            # ── 画图意图检测 ──
            draw_pattern = re.compile(r'(帮我画|给我画|给我一[张只]|画一[张只]|来一[张只]|整一[张只]|出图|画个|画只|画张|画\b|生成.*?图|的图片|生辰.*?图|生日.*?图|做个.*?图|做.*?图)', re.IGNORECASE)
            if draw_pattern.search(merged_text_for_ai):
                draw_desc = merged_text_for_ai
                # 群聊暂不支持图片附件，走文生图
                log(f"[画图] 群[{group_id}] 检测到画图意图: {draw_desc}")
                smart_send_group(group_id, "画图中，请稍等...")
                draw_result = handle_generate_image(draw_desc)
                log(f"[画图] 结果: {draw_result[:100]}")
                if draw_result.startswith("图片已生成:"):
                    img_path = draw_result[len("图片已生成:"):].strip()
                    send_group_image(group_id, img_path)
                else:
                    smart_send_group(group_id, draw_result)
                return

            reply = call_deepseek(merged_text_for_ai, images, group_id=group_id, is_group=True, personality_temperature=_ST.get("personality_temperature"), ego_inject_context=_ego_ctx)

        # 群聊联网搜索循环（最多2轮）
        for _ in range(2):
            m = re.search(r'\[搜索:(.+?)\]', reply or "")
            if not m:
                break
            query = m.group(1).strip()
            log(f"群[{group_id}] 联网搜索: {query}")
            search_result = search_web(query)
            today_str = datetime.datetime.now().strftime("%Y年%m月%d日")
            retry_msg = (
                f"{merged_text_for_ai}\n\n"
                f"[系统搜索结果] 今天是{today_str}。以下是对「{query}」的搜索结果：\n{search_result}\n\n"
                "请直接基于以上搜索结果回答用户问题。即使搜索结果不够理想，也必须用现有信息作答，"
                "可以补充你自己的常识但不要编造具体数据。禁止再次输出 [搜索:] 标记。"
            )
            reply = call_deepseek(retry_msg, [], group_id=group_id, is_group=True, personality_temperature=_ST.get("personality_temperature"))

        if not reply or reply.strip() in ("[不说话]", "[跳过]"):
            log(f"群[{group_id}] AI决定不回复: {reply[:30] if reply else '(空)'}")
            return

        if reply:
            # 正常会话中剥离退场语（退场语仅在 ≤2字 结束会话时由代码发送）
            reply = strip_exit_phrases(reply)
            if not reply or not reply.strip():
                log(f"群[{group_id}] 剥离退场语后为空，跳过")
                return

            # 检查是否与最近回复高度相似，防重复唠嗑
            recent = [t for ts, t in GROUP_BOT_REPLIES.get(group_id, []) if time.time() - ts < 60]
            is_dup, matched = is_similar_to_recent(recent, reply)
            if is_dup:
                log(f"群[{group_id}] 重复回复: {reply[:40]} ≈ {matched[:40]}")
                # 重试换话回复，最多 2 次
                for retry_i in range(2):
                    retry_hint = "（注意：你上一条回复和之前的太像了，换一种说法，换个角度，不要说类似的话）"
                    reply = call_deepseek(retry_hint + "\n" + merged_text, images, group_id=group_id, is_group=True, personality_temperature=_ST.get("personality_temperature"))
                    if not reply or reply.strip() in ("[不说话]", "[跳过]"):
                        log(f"群[{group_id}] 重试{retry_i+1}跳过")
                        return
                    reply = strip_exit_phrases(reply)
                    if not reply or not reply.strip():
                        log(f"群[{group_id}] 重试{retry_i+1}剥离退场语后为空")
                        return
                    recent = [t for ts, t in GROUP_BOT_REPLIES.get(group_id, []) if time.time() - ts < 60]
                    is_dup2, matched2 = is_similar_to_recent(recent, reply)
                    if is_dup2:
                        log(f"群[{group_id}] 重试{retry_i+1}仍重复: {reply[:40]} ≈ {matched2[:40]}")
                        continue
                    log(f"群[{group_id}] 重试{retry_i+1}通过")
                    break
                else:
                    log(f"群[{group_id}] 跳过重复回复（{retry_i+1}次重试仍重复）")
                    return

            gh = group_chat_history.setdefault(group_id, [])
            # 群聊历史：user 消息带上发送者前缀，让 AI 区分不同用户
            # merged_text 已包含 [昵称(QQ号)] 格式，直接存储
            gh.append({"role": "user", "content": merged_text[:300]})
            gh.append({"role": "assistant", "content": reply[:300]})
            # C2 存储对齐：群聊截断 30→16 轮（组装只取最近10轮）
            group_chat_history[group_id] = gh[-16:]

            # 统一记忆分发（1.5 任务 2.3）：回复侧同步（sandglass 画像/偏移/阶段 + super_memory 问答对）
            try:
                from sandglass_bridge import memory_feed
                memory_feed({
                    "text": merged_text,
                    "group_id": str(group_id),
                    "reply": reply,
                })
            except Exception:
                pass

            clean_reply, _meme_emotion = parse_meme_intent(reply)
            sent_text = clean_reply.strip() if clean_reply else ""
            sent_text = _trim_incomplete(sent_text)
            # ── 去AI味：规则级后处理（发送前清理破折号/套话/客服口吻等）──
            try:
                from de_ai import de_ai_post_process
                sent_text = de_ai_post_process(sent_text, _config)
            except Exception:
                pass

            if sent_text:
                # ── 插件钩子：after_reply（发送前，允许插件修改回复文本）──
                if PLUGIN_OK:
                    try:
                        pm = get_plugin_manager()
                        if pm and pm.has_hook("after_reply"):
                            after_result = pm.execute_hook("after_reply", message=merged_text, group_id=group_id, reply=sent_text, context=_plugin_ctx)
                            if after_result and after_result.get("action") == "override":
                                new_reply = after_result.get("reply", "")
                                if new_reply and new_reply != sent_text:
                                    sent_text = new_reply
                                    log(f"群[{group_id}] [插件覆盖] {sent_text[:60]}")
                    except Exception as e:
                        log(f"[Plugin] after_reply error: {e}")
                if smart_send_group(group_id, sent_text):
                    log(f"群回复: {sent_text[:60]}")
                    track_bot_reply(group_id, sent_text)
            else:
                log(f"群[{group_id}] 仅表情标记，跳过文本发送")

            # 表情包附着：AI 标注了 [表情] 或自动检测到情绪时发一张
            meme_file = try_send_meme(group_id, reply)
            save_instruction(merged_text, reply)

            # 触发自主学习
            if SELF_LEARN_OK:
                if _config.get("self_learning_enable", True):
                    self_learn.maybe_learn(str(group_id))
    except Exception as e:
        log(f"群[{group_id}] AI 调用异常: {e}")


# ═══════════════════════════════════════════
#  主循环
# ═══════════════════════════════════════════

def main():
    global chat_history, group_chat_history

    log("=" * 50)
    log("EggBloom v8 - 多账号 + 文件转发 + 消息转存")
    log(f"Bot: {BOT_QQ} | 监听: {', '.join(TARGET_QQS)} | 群聊: {len(GROUP_IDS)}个")
    log("=" * 50)

    # ── NexSandglass 记忆引擎（后台懒加载，不阻塞主流程） ──
    try:
        from sandglass_bridge import _ensure_init
        _ensure_init()
        log("[Sandglass] 后台初始化中...")
    except Exception as e:
        log(f"[Sandglass] 导入失败: {e}")

    log("启动冷却完毕，开始监听消息")

    # ── 协议层感知 ──
    protocol_enabled = _config.get("protocol_enabled", False)
    if protocol_enabled:
        log(f"[协议层] 已启用 (API: {ONE_BOT_URL})")
    else:
        log(f"[协议层] 独立模式 — 协议插件手动启动 (API: {ONE_BOT_URL})")

    # ── 自动拉起 QQ（仅在配置中显式指定 qq_path 且文件存在时）──
    QQ_PATH = _config.get("qq_path", "")
    import subprocess as _sp
    if QQ_PATH and os.path.exists(QQ_PATH):
        try:
            result = _sp.run(["tasklist", "/FI", "IMAGENAME eq QQ.exe", "/FO", "CSV", "/NH"],
                              capture_output=True, timeout=5)
            stdout = result.stdout.decode("utf-8", errors="replace") if isinstance(result.stdout, bytes) else result.stdout
            qq_running = "QQ.exe" in stdout
        except Exception:
            qq_running = False
        if not qq_running:
            log(f"正在启动 QQ: {QQ_PATH}")
            qq_dir = os.path.dirname(QQ_PATH)
            _sp.Popen(
                [QQ_PATH],
                cwd=qq_dir,
                creationflags=_sp.CREATE_NO_WINDOW if hasattr(_sp, 'CREATE_NO_WINDOW') else 0)
            log("QQ 已启动，等待 OneBot 连接...")
            time.sleep(5)
        else:
            log("QQ 已在运行")
    else:
        log("未配置 QQ 路径或文件不存在，跳过自动启动")

    # 等待 OneBot 上线（带超时，不无限阻塞）
    retry_count = 0
    while True:
        resp = api_call("get_login_info")
        if resp.get("retcode") == 0:
            break
        retry_count += 1
        if retry_count % 3 == 0:
            log(f"等待 OneBot... (已等待 {retry_count * 2}s)")
        time.sleep(2)
        if retry_count > 300:  # 10分钟上限
            log("OneBot 连接超时(10分钟)，继续运行但功能受限")
            break

    log("OneBot 已连接")

    # 插件系统：传播 AI 客户端（尝试从 __main__ 获取）
    if PLUGIN_OK:
        try:
            pm = get_plugin_manager()
            if pm:
                # 构造一个轻量客户端对象供插件使用
                from config import load_config as _lc, get_provider_api_key
                from ai_client import AIClient
                _cfg = _lc()
                _pid = str(_cfg.get("api_provider") or "openai")
                _ak = get_provider_api_key(_pid) or _cfg.get("api_key") or _cfg.get("deepseek_api_key", "")
                _au = _cfg.get("api_url", DEEPSEEK_URL)
                _am = _cfg.get("model_name", "deepseek-flash")
                if _ak:
                    _ac = AIClient(_au, _ak, _am, provider=_pid, temperature=_cfg.get("ai_temperature"))
                    pm.set_ai_client(_ac)
                    log(f"[PluginSystem] AI 客户端已传播 ({_pid})")
        except Exception as e:
            log(f"[PluginSystem] 传播 AI 客户端失败: {e}")

    # 后台昵称解析线程：log()/热路径只命中缓存，网络请求不阻塞主循环
    try:
        threading.Thread(target=_nick_worker_loop, daemon=True, name="qq-nick-worker").start()
    except Exception:
        pass

    # 预加载 QQ 昵称 (debug 面板显示用)；启动阶段允许同步解析
    for qq in TARGET_QQS:
        nick = resolve_qq_nick(qq, async_resolve=False)
        if nick:
            log(f"昵称缓存: {qq} → {nick}")
    log(f"昵称缓存完毕: {len(QQ_NICK_CACHE)}/{len(TARGET_QQS)}")

    # 预加载群成员列表（按群存储）
    for gid in GROUP_IDS:
        try:
            resp = api_call("get_group_member_list", {"group_id": int(gid)})
            if resp.get("retcode") in (0, 200):
                data = resp.get("data") or {}
                members = data if isinstance(data, list) else data.get("members", [])
                cached = 0
                for m in members:
                    if not m or not isinstance(m, dict):
                        continue
                    uid = str(m.get("user_id", ""))
                    if not uid or uid == "0" or uid == gid:
                        continue
                    card = (m.get("card") or "").strip()
                    nick = (m.get("nickname") or "").strip()
                    display = card if card else nick
                    if not display or display.isdigit():
                        continue
                    QQ_NICK_CACHE[uid] = display
                    if gid not in GROUP_MEMBERS:
                        GROUP_MEMBERS[gid] = {}
                    GROUP_MEMBERS[gid][uid] = display
                    cached += 1
                log(f"群[{gid}] 成员列表: {len(members)}人, 新缓存{cached}人")
            else:
                log(f"群[{gid}] 成员列表获取失败: retcode={resp.get('retcode')}")
        except Exception as e:
            log(f"群[{gid}] 成员列表获取异常: {e}")
    log(f"昵称缓存总计: {len(QQ_NICK_CACHE)}人")

    # 启动时触发一次自主学习，处理积压的未学习消息
    if SELF_LEARN_OK:
        for gid in GROUP_IDS:
            try:
                if _config.get("self_learning_enable", True):
                    self_learn.maybe_learn(str(gid))
            except Exception as e:
                log(f"启动自主学习异常[{gid}]: {e}")
    
    load_state()
    global startup_time
    startup_time = time.time()  # 开机冷却起点
    if STARTUP_COOLDOWN > 0:
        def _countdown():
            for elapsed in range(1, STARTUP_COOLDOWN + 1):
                if elapsed % 5 == 0 or elapsed == 1 or elapsed == STARTUP_COOLDOWN:
                    log(f"启动冷却中… {elapsed}s/{STARTUP_COOLDOWN}s（此期间抓取消息但不回复）")
                time.sleep(1)
        threading.Thread(target=_countdown, daemon=True, name="startup-countdown").start()

    # 启动消息事件接收服务（3005 端口，接收协议层实时推送）
    threading.Thread(target=start_event_server, daemon=True, name="event-server").start()

    # 黑话学习器：后台定时自动抓取最新热搜/热词进候选池（不自动注入）
    try:
        _slang_cfg = {}
        try:
            _slang_cfg = __import__("plugins.ego.slang_learner", fromlist=["get_config"]).get_config()
        except Exception:
            pass
        if _slang_cfg.get("auto_learn", False):
            _slang_interval = float(_slang_cfg.get("auto_learn_interval_hours", 12))
            __import__("plugins.ego.slang_learner", fromlist=["start_auto_learn_loop"]).start_auto_learn_loop(_slang_interval)
            log(f"[SlangLearner] 定时自动学习已启动（每 {_slang_interval}h 抓取热搜）")
    except Exception as e:
        log(f"[SlangLearner] 自动学习启动失败: {e}")

    consecutive_errors = 0

    # 主循环需要的全局变量声明
    global GROUP_MSG_BUFFER, LAST_MSG_TIME

    while True:
        try:

            # 处理实时消息队列（来自协议层 3005 端口推送）
            # 把实时消息/事件直接转为内部格式，和轮询消息合并处理
            event_msgs = []
            while not MSG_QUEUE.empty():
                try:
                    ev = MSG_QUEUE.get_nowait()
                    if not isinstance(ev, dict):
                        continue
                    post_type = ev.get("post_type")
                    if post_type == "message":
                        msg_type = ev.get("message_type", "private")
                        is_group = (msg_type == "group")
                        _msg_time = ev.get("time", 0) or 0
                        # 启动基线：丢弃 NapCat/协议层登录同步推送的「启动前」历史消息
                        if _msg_time and _msg_time < startup_time:
                            log(f"[事件] 忽略启动前历史消息 time={_msg_time} (startup={int(startup_time)})")
                            continue
                        # 去重：优先 message_id（同一秒多条消息不丢）；无 id 时回退时间戳水位线
                        _mid = ev.get("message_id")
                        if _mid:
                            if _mark_seen_msg_id(_mid):
                                continue
                        elif _msg_time and _msg_time <= LAST_MSG_TIME:
                            continue
                        if _msg_time > LAST_MSG_TIME:
                            LAST_MSG_TIME = _msg_time
                            save_state()
                        raw_msg = ev.get("message", "")
                        if isinstance(raw_msg, list):
                            text = "".join([seg["data"]["text"] for seg in raw_msg if isinstance(seg, dict) and seg.get("type") == "text" and seg.get("data", {}).get("text")]).strip()
                            # 保留完整消息段（forward/reply/at/face/image 等）
                            message_segments = raw_msg
                        elif isinstance(raw_msg, str):
                            text = raw_msg.strip()
                            message_segments = [{"type": "text", "data": {"text": text}}]
                        else:
                            text = ""
                            message_segments = []
                        sender_id = str(ev.get("user_id", ""))
                        group_id = str(ev.get("group_id", "")) if is_group else ""
                        event_msgs.append({
                            "_type": "group" if is_group else "private",
                            "_group_id": group_id,
                            "message": message_segments,
                            "sender": {"user_id": sender_id},
                            "user_id": sender_id,
                            "time": ev.get("time", int(time.time())),
                            "message_id": ev.get("message_id", 0),
                        })
                        if is_group:
                            event_msgs[-1]["group_id"] = group_id
                        seg_types = [s.get("type", "?") for s in message_segments if isinstance(s, dict)]
                        log(f"[事件] 收到: post_type=message msg_type={msg_type} uid={sender_id} gid={group_id} 段={seg_types}")
                    elif (post_type == "notice"
                          and ev.get("notice_type") == "notify"
                          and ev.get("sub_type") == "poke"):
                        try:
                            _handle_poke_event(ev)
                        except Exception as e:
                            log(f"[事件] poke 处理异常: {e}")
                    elif (post_type == "notice"
                          and ev.get("notice_type") in ("group_recall", "friend_recall")):
                        try:
                            _handle_recall_event(ev)
                        except Exception as e:
                            log(f"[事件] 撤回处理异常: {e}")
                    elif post_type == "request":
                        # 好友/加群/入群 申请：自动同意（auto_accept）
                        try:
                            _handle_request_event(ev)
                        except Exception as e:
                            log(f"[请求事件] 处理异常: {e}")
                    elif post_type == "notice" and ev.get("notice_type") == "group_increase":
                        try:
                            _handle_group_increase(ev)
                        except Exception as e:
                            log(f"[事件] group_increase 处理异常: {e}")
                    elif post_type == "notice" and ev.get("notice_type") == "group_decrease":
                        try:
                            _handle_group_decrease(ev)
                        except Exception as e:
                            log(f"[事件] group_decrease 处理异常: {e}")
                    elif post_type == "notice" and ev.get("notice_type") == "friend_add":
                        try:
                            _handle_friend_add(ev)
                        except Exception as e:
                            log(f"[事件] friend_add 处理异常: {e}")
                except Exception as e:
                    log(f"处理实时事件异常: {e}")

            # 协议层已推送事件时，跳过轮询（避免冗余 HTTP 请求）
            if event_msgs:
                new_msgs = event_msgs
            else:
                new_msgs = get_new_messages()

            # ── 启动冷却完成标记（一次性日志） ──
            if time.time() - startup_time >= STARTUP_COOLDOWN:
                if not getattr(main, "_cooldown_done_logged", False):
                    log("启动冷却完毕，开始监听消息")
                    setattr(main, "_cooldown_done_logged", True)

            # ── 全局会话空闲扫描：Bot 是最后说话者且 >2分钟无新消息 → 退场 ──
            if GROUP_IDLE_EXIT_ENABLE and time.time() - startup_time >= STARTUP_COOLDOWN:
                now = time.time()
                # 遍历所有有会话的群
                for gid in list(GROUP_SESSION.keys()):
                    gs = GROUP_SESSION.get(gid)
                    if gs:
                        bot_last = GROUP_LAST_BOT_MSG.get(gid, 0)
                        last_any = GROUP_LAST_ANY_MSG.get(gid, 0)
                        if bot_last >= last_any and now - bot_last > _idle_exit_min_silence():
                            last_exit = GROUP_LAST_EXIT.get(gid, 0)
                            if now - last_exit >= _config.get("group_last_exit_cooldown", 3000) and random.random() < _config.get("group_idle_exit_probability", 0.3):
                                exit_phrase = random.choice(EXIT_PHRASES_IDLE)
                                try:
                                    smart_send_group(gid, exit_phrase)
                                    track_bot_reply(gid, exit_phrase)
                                    send_idle_exit_meme(gid)
                                    GROUP_LAST_EXIT[gid] = now
                                    log(f"群[{gid}] 全局扫描空闲超时退场: {exit_phrase}")
                                except Exception as e:
                                    log(f"群[{gid}] 全局扫描退场语发送失败: {e}")
                            GROUP_SESSION.pop(gid, None)
                # 也检查没有会话但Bot是最后发言者的群
                for gid in list(GROUP_IDS):
                    if gid in GROUP_SESSION:
                        continue  # 已经在上面处理了
                    bot_last = GROUP_LAST_BOT_MSG.get(gid, 0)
                    last_any = GROUP_LAST_ANY_MSG.get(gid, 0)
                    elapsed = now - bot_last if bot_last > 0 else 0
                    if bot_last > 0 and bot_last >= last_any and now - bot_last > _idle_exit_min_silence():
                        last_exit = GROUP_LAST_EXIT.get(gid, 0)
                        if now - last_exit >= _config.get("group_last_exit_cooldown", 3000) and random.random() < _config.get("group_idle_exit_probability", 0.3):
                            exit_phrase = random.choice(EXIT_PHRASES_IDLE)
                            try:
                                smart_send_group(gid, exit_phrase)
                                track_bot_reply(gid, exit_phrase)
                                send_idle_exit_meme(gid)
                                GROUP_LAST_EXIT[gid] = now
                                log(f"群[{gid}] 空闲超时退场(无会话): {exit_phrase}")
                            except Exception as e:
                                log(f"群[{gid}] 空闲退场语发送失败: {e}")

            for msg in new_msgs:
                # ── 真插件 ABI：on_message（只调 spec 插件；返回文本即作为插件回复，跳过 AI）──
                if PLUGIN_OK:
                    try:
                        pm = get_plugin_manager()
                        if pm and pm.has_typed_hook("on_message"):
                            _mtype = msg.get("_type", "private")
                            _mtext = extract_text(msg).strip()
                            _msender = str(msg.get("sender", {}).get("user_id", "") or msg.get("user_id", ""))
                            _plugin_reply = pm.execute_typed_hooks_first(
                                "on_message",
                                event={
                                    "type": _mtype,
                                    "group_id": msg.get("_group_id") or "",
                                    "sender": _msender,
                                    "text": _mtext,
                                    "message_id": msg.get("message_id", 0),
                                    "time": msg.get("time", 0),
                                },
                            )
                            if _plugin_reply and str(_plugin_reply).strip():
                                _pr = str(_plugin_reply).strip()
                                if _mtype == "group" and msg.get("_group_id"):
                                    # 尊重群回复开关（与正常路径一致）
                                    if _group_restriction(msg.get("_group_id"), "reply_enabled", True) is not False:
                                        smart_send_group(msg.get("_group_id"), _pr)
                                        log(f"[Plugin] on_message 插件回复: {_pr[:60]}")
                                elif _msender:
                                    smart_send(_pr, target_qq=_msender)
                                    log(f"[Plugin] on_message 插件回复: {_pr[:60]}")
                                continue
                    except Exception as _pe:
                        log(f"[Plugin] on_message 派发异常: {_pe}")
                msg_type = msg.get("_type", "private")
                group_id = msg.get("_group_id")
                
                # 群聊：@ 会话模式
                if msg_type == "group":
                    # 过滤自己发的消息：user_id / sender.user_id 匹配 或 post_type=message_sent
                    sender = str(msg.get("sender", {}).get("user_id", ""))
                    msg_uid = str(msg.get("user_id", ""))
                    if sender == BOT_QQ or msg_uid == BOT_QQ or msg.get("post_type") == "message_sent":
                        continue
                    # 群限制：该群自动回复已关闭（webui 群管理页），完全静默
                    if _group_restriction(group_id, "reply_enabled", True) is False:
                        continue
                    text = extract_text(msg).strip()
                    
                    
                    # 转发消息：静默记录，不打印逐行内容
                    if "[转发消息]" in text:
                        log(f"群[{group_id}] [转发] 已提取{len(text)}字，不回复")
                    
                    # 引用消息回抓：提取被引用的消息原文 + 原主
                    quoted_text = ""
                    quoted_sender = ""
                    quoted_sender_nick = ""
                    reply_sender_uid = ""
                    has_quote = False
                    for seg in msg.get("message", []):
                        if isinstance(seg, dict) and seg.get("type") == "reply":
                            reply_id = seg.get("data", {}).get("id") or seg.get("data", {}).get("message_id", "")
                            reply_sender_uid = str(seg.get("data", {}).get("sender_uid", ""))
                            # 优先使用插件内联解析的引用文本
                            inline_text = seg.get("data", {}).get("quoted_text", "")
                            inline_nick = seg.get("data", {}).get("quoted_nick", "")
                            if inline_text:
                                quoted_text = inline_text[:500]
                                quoted_sender_nick = inline_nick or "未知"
                                log(f"群[{group_id}] 引用消息(内联): [{quoted_sender_nick}] {quoted_text[:60]}")
                            elif reply_id:
                                try:
                                    resp = api_call("get_msg", {"message_id": reply_id})
                                    if resp.get("retcode") == 0:
                                        qdata = resp.get("data", {})
                                        qtext = extract_text(qdata)
                                        quoted_sender = str(qdata.get("sender", {}).get("user_id", ""))
                                        log(f"[调试-引用] qtext={repr(qtext[:80] if qtext else '')} raw={repr(str(qdata.get('raw_message',''))[:80])} msg_id={reply_id}")
                                        if qtext and qtext.strip():
                                            quoted_text = qtext.strip()[:500]
                                        elif qdata.get("raw_message") and str(qdata["raw_message"]).strip():
                                            quoted_text = str(qdata["raw_message"]).strip()[:500]
                                        if quoted_sender:
                                            quoted_sender_nick = resolve_qq_nick(quoted_sender) or quoted_sender
                                            log(f"群[{group_id}] 引用消息: [{quoted_sender_nick}({quoted_sender})] {quoted_text[:60]}")
                                    else:
                                        log(f"群[{group_id}] 引用消息 get_msg 失败(retcode={resp.get('retcode')}): {resp.get('msg','')[:80]}")
                                except Exception as e:
                                    log(f"群[{group_id}] 引用消息 get_msg 异常: {e}")
                                if not quoted_text:
                                    # 回退1：从近期消息缓存按 message_id 查找
                                    recent = GROUP_RECENT_MSGS.get(group_id, [])
                                    for entry in recent:
                                        if len(entry) >= 4:
                                            t, s, tx, mid = entry[:4]
                                        else:
                                            t, s, tx = entry[:3]
                                            mid = ""
                                        if str(mid) == str(reply_id):
                                            quoted_text = tx[:500]
                                            quoted_sender = s
                                            quoted_sender_nick = resolve_qq_nick(s) or s
                                            log(f"群[{group_id}] 引用消息(缓存命中): [{quoted_sender_nick}] {quoted_text[:60]}")
                                            break
                                if not quoted_text:
                                    # 回退2：搜索缓存中含[转发消息]的记录（转发消息原文可能被截断存储）
                                    recent = GROUP_RECENT_MSGS.get(group_id, [])
                                    for entry in reversed(recent):
                                        if len(entry) >= 4:
                                            t, s, tx, mid = entry[:4]
                                        else:
                                            t, s, tx = entry[:3]
                                            mid = ""
                                        if "[转发消息]" in tx or "[转发]" in tx:
                                            quoted_text = tx[:500]
                                            quoted_sender = s
                                            quoted_sender_nick = resolve_qq_nick(s) or s
                                            log(f"群[{group_id}] 引用消息(转发缓存命中): [{quoted_sender_nick}] {quoted_text[:60]}")
                                            break
                                if not quoted_text:
                                    log(f"群[{group_id}] 引用消息无法获取(id={reply_id})")
                                if not quoted_text and reply_sender_uid:
                                    # 回退3：从 GROUP_TIMELINE 按 sender_uid 找最近一条消息
                                    _tl = GROUP_TIMELINE.get(group_id, [])
                                    for entry in reversed(_tl):
                                        if len(entry) >= 4 and str(entry[1]) == reply_sender_uid:
                                            quoted_text = entry[3][:500] if entry[3] else ""
                                            quoted_sender = str(entry[1])
                                            quoted_sender_nick = entry[2] or entry[1]
                                            log(f"群[{group_id}] 引用消息(timeline回退): [{quoted_sender_nick}] {quoted_text[:60]}")
                                            break
                    if quoted_text:
                        has_quote = True
                        sender_display = quoted_sender_nick if quoted_sender_nick else "未知"
                        text = f"[引用:{sender_display}] {quoted_text}\n[当前] {text}"
                    
                    # 群友画像指令：优先拦截，不进入缓冲区
                    original_check = text.lstrip(f"[引用] {quoted_text}\n[当前] ") if quoted_text else text
                    oc = original_check.strip()
                    # 全群画像
                    if re.match(r'^(群友画像|画像|群友报告|群报告|group.report|群聊画像|群聊报告)\s*$', oc):
                        log(f"群[{group_id}] 群友画像指令: {oc}")
                        handle_group_profile(group_id)
                        continue
                    # 单人画像
                    if re.match(r'^.*?(我的画像|我的人格画像|个人画像|单人群友画像|生成我.*画像|人格画像)\s*$', oc):
                        log(f"群[{group_id}] 单人画像指令: {oc} sender={sender}")
                        handle_group_profile(group_id, target_user=sender)
                        continue
                    
                    # 自主学习：收集消息
                    if SELF_LEARN_OK and text:
                        self_learn.collect_message(str(group_id), sender, text)
                    # ChatStyle 说话方式自动学习（越学越像人）
                    try:
                        if PLUGIN_OK:
                            _cs_mod = __import__("plugins.ego.chat_style", fromlist=["auto_learn"])
                            _cs_mod.auto_learn(str(group_id), sender, text)
                    except Exception:
                        pass
                    # 统一记忆分发（1.5 任务 2.3）：一条消息一次解析，四模块各取所需
                    # 旧代码此处各模块独立 import + 独立 resolve_qq_nick，现收敛为一行
                    sender_nick = resolve_qq_nick(sender) if sender else ""
                    try:
                        from sandglass_bridge import memory_feed
                        memory_feed({
                            "text": text,
                            "group_id": str(group_id),
                            "sender": sender,
                            "sender_nick": sender_nick,
                        })
                    except Exception:
                        pass
                    
                    # 记录到近期消息缓存（30秒窗口，用于引用消息回抓）
                    current_time = time.time()
                    msg_id = msg.get("message_id", 0)
                    if group_id not in GROUP_RECENT_MSGS:
                        GROUP_RECENT_MSGS[group_id] = []
                    # 提取图片路径（供群聊决策插件理解图片内容）
                    _img_paths = []
                    if any(isinstance(s, dict) and s.get("type") == "image" for s in msg.get("message", [])):
                        try:
                            _img_paths = extract_images(msg)
                        except Exception:
                            _img_paths = []
                    GROUP_RECENT_MSGS[group_id].append((current_time, sender, text, msg_id, _img_paths))
                    # 清理超过X秒的旧消息
                    GROUP_RECENT_MSGS[group_id] = [
                        e for e in GROUP_RECENT_MSGS[group_id]
                        if current_time - e[0] <= GROUP_RECENT_MSGS_WINDOW
                    ]
                    # 更新群内最后消息时间（用于空闲退场判断）
                    if current_time > GROUP_LAST_ANY_MSG.get(group_id, 0):
                        GROUP_LAST_ANY_MSG[group_id] = current_time
                    # 全局消息时间线：记录所有消息（概率门之前），用于上下文
                    if group_id not in GROUP_TIMELINE:
                        GROUP_TIMELINE[group_id] = []
                    # sender_nick 已在上方 memory_feed 前解析，复用避免重复查表
                    GROUP_TIMELINE[group_id].append((current_time, sender, sender_nick, text))
                    # 保留最近 50 条（约 3-5 分钟的群聊）
                    if len(GROUP_TIMELINE[group_id]) > 50:
                        GROUP_TIMELINE[group_id] = GROUP_TIMELINE[group_id][-50:]
                    
                    # 消息合并：同一群 X 秒内的多条消息合并
                    buffer = GROUP_MSG_BUFFER.get(group_id)
                    is_at_bot = False
                    has_any_at = False
                    for seg in msg.get("message", []):
                        if isinstance(seg, dict) and seg.get("type") == "at":
                            has_any_at = True
                            at_data = seg.get("data", {})
                            at_qq = str(at_data.get("qq", "") or at_data.get("user_id", ""))
                            at_nick = str(at_data.get("nickname", ""))
                            # 检查QQ号是否匹配Bot
                            if at_qq == BOT_QQ or at_qq == str(BOT_QQ):
                                is_at_bot = True
                                break
                            # 也检查昵称匹配（先查缓存，再查配置）
                            bot_nick = QQ_NICK_CACHE.get(BOT_QQ) or _config.get("bot_group_nick", "")
                            if bot_nick and at_qq == bot_nick:
                                is_at_bot = True
                                break
                            # 昵称匹配：当 at_qq 为空时（@昵称），用 displayName 匹配
                            if not at_qq and at_nick:
                                if bot_nick and bot_nick in at_nick:
                                    is_at_bot = True
                                    break
                                if "占位名片A" in at_nick:
                                    is_at_bot = True
                                    break
                    
                    # 检查是否命中配置的触发词
                    has_xiao_a = _is_group_trigger(text)
                    # 检查文本是否含 Bot 昵称（仅在配置开启时）
                    has_bot_nick = False
                    if _config.get("group_trigger_include_bot_nick", True):
                        has_bot_nick = BOT_GROUP_NICK and BOT_GROUP_NICK in text
                    # 文本中 @Bot QQ 检测（如 @10000001）
                    if f"@{BOT_QQ}" in text:
                        is_at_bot = True
                    # 文本中 @Bot 昵称检测（仅在配置开启时）
                    if _config.get("group_trigger_include_bot_nick", True) and BOT_GROUP_NICK and f"@{BOT_GROUP_NICK}" in text:
                        is_at_bot = True
                    # 文本中 @ 某人（句首/句末，纯文本 @，非 QQ at 段）
                    # 目的是让消息进入缓冲区，由 process_buffered_message 过滤
                    has_text_at = False
                    if _config.get("group_trigger_include_text_at", True):
                        has_text_at = bool(re.search(r'@\S{1,15}', text))
                    # 引用消息检测（仅在配置开启时）
                    has_quote = False
                    if _config.get("group_trigger_include_quote", True):
                        has_quote = any(isinstance(seg, dict) and seg.get("type") == "reply" for seg in msg.get("message", []))
                    # 转发消息省略提示（仅日志显示截断，text保持完整）
                    has_forward = any(isinstance(s, dict) and s.get("type") == "forward" for s in msg.get("message", []))
                    has_user_text = any(isinstance(s, dict) and s.get("type") == "text" and (s.get("data") or {}).get("text", "").strip() for s in msg.get("message", []))
                    is_pure_forward = has_forward and not has_user_text
                    if "[转发消息]" in text and len(text) > 80:
                        log_text = f"[转发消息] {text[len('[转发消息] '):80]}...(已提取)"
                    else:
                        log_text = text[:60]
                    log(f"群[{group_id}] 文本: '{log_text}' has_xiao_a={has_xiao_a} is_at_bot={is_at_bot} has_quote={has_quote} has_text_at={has_text_at}")

                    # ── 群管理指令（@Bot + 指令，admin_qqs/主人鉴权）──
                    if (is_at_bot or has_xiao_a) and text:
                        try:
                            if _handle_group_admin_command(text, msg, sender, group_id):
                                continue
                        except Exception as e:
                            log(f"[群管理] 指令处理异常: {e}")

                    # 纯转发消息：只记录上下文，不触发回复
                    if is_pure_forward:
                        log(f"群[{group_id}] 纯转发消息，记录为上下文，不回复")
                        continue
                    
                    # 是否为会话模式消息（@触发后，非直接@消息的连续对话）
                    in_session = False
                    
                    # QQ at 段指向他人 → 无条件跳过（不管文本里有没有 @）
                    if has_any_at and not is_at_bot and not has_xiao_a:
                        log(f"群[{group_id}] 跳过 @他人 的消息")
                        continue

                    # 前置判断：没被 @、没叫「小a」、无文本 @、无引用 → 直接跳过
                    if not is_at_bot and not has_xiao_a and not has_text_at and not has_quote:
                        # 不在会话模式 → 跳过
                        with _session_lock:
                            gs = GROUP_SESSION.get(group_id)
                            if not gs or gs.get("remaining", 0) <= 0:
                                continue
                        # 标记为会话模式消息（remaining 递减推迟到 process_buffered_message 中）
                        is_at_bot = False
                        has_xiao_a = False
                        in_session = True
                    
                    # 被 @ 或叫「小a」 → 进入消息合并流程
                    if buffer:
                        buf_is_session = buffer.get("in_session", False)
                        buf_is_trigger = buffer.get("is_at_bot", False) or buffer.get("has_xiao_a", False)
                        buf_has_context = buffer.get("has_context", False)
                        if buf_is_session:
                            merge_window = _config.get("group_msg_buffer_window_session", 5)
                        elif buf_is_trigger and not buf_has_context:
                            merge_window = _config.get("group_msg_buffer_window_trigger", 15)
                        else:
                            merge_window = GROUP_MSG_BUFFER_WINDOW
                    else:
                        merge_window = 5 if in_session else (15 if (is_at_bot or has_xiao_a) else GROUP_MSG_BUFFER_WINDOW)
                    if buffer and current_time - buffer["time"] <= merge_window:
                        # 合并消息，格式："[用户A] 消息1\n[用户B] 消息2"
                        if sender != buffer["sender"]:
                            buffer["text"] += f"\n[{sender}] {text}"
                        else:
                            buffer["text"] += f"\n{text}"
                        buffer["time"] = current_time
                        # 更新 @ 和「小a」标记
                        buffer["is_at_bot"] = buffer.get("is_at_bot", False) or is_at_bot
                        buffer["has_xiao_a"] = buffer.get("has_xiao_a", False) or has_xiao_a
                        buffer["has_bot_nick"] = buffer.get("has_bot_nick", False) or has_bot_nick
                        buffer["has_quote"] = buffer.get("has_quote", False) or has_quote
                        buffer["has_text_at"] = buffer.get("has_text_at", False) or has_text_at
                        buffer["in_session"] = buffer.get("in_session", False) or in_session
                        # 合并了一条有意义的消息 → 现在有上下文了
                        if not is_meaningless_message(text):
                            buffer["has_context"] = True
                        # 取消之前的定时器
                        if buffer["timer"]:
                            buffer["timer"].cancel()
                        # 设置新的定时器
                        buf_sess = buffer.get("in_session", False)
                        buf_trig = buffer.get("is_at_bot", False) or buffer.get("has_xiao_a", False)
                        buf_ctx = buffer.get("has_context", False)
                        if buf_sess:
                            delay = 5
                        elif buf_trig and not buf_ctx:
                            delay = 15
                        else:
                            delay = 3
                        buffer["timer"] = Timer(delay, lambda gid=group_id: process_buffered_message(gid))
                        buffer["timer"].start()
                        continue  # 等待合并完成
                    elif buffer:
                        # 缓冲区已过期，立即处理
                        process_buffered_message(group_id)
                    
                    # 新建缓冲区
                    # 被 @ 或叫「小a」→ 回抓最近消息作为上下文（最多15条或800字）
                    grabbed_texts = []
                    grabbed_chars = 0
                    if is_at_bot or has_xiao_a:
                        recent = GROUP_RECENT_MSGS.get(group_id, [])
                        for entry in recent:
                            t, s, tx = entry[0], entry[1], entry[2]
                            if tx == text and s == sender and abs(t - current_time) < 1:
                                continue  # 跳过当前这条消息本身
                            if is_meaningless_message(tx):
                                continue
                            nick = resolve_qq_nick(s) if s else ""
                            display = nick if nick else "群友"
                            line = f"[{display}] {tx}"
                            if grabbed_chars + len(line) > GROUP_REPLY_MAX_LENGTH:
                                break  # 超X字停止
                            grabbed_texts.append(line)
                            grabbed_chars += len(line)
                            if len(grabbed_texts) >= 15:
                                break  # 最多15条
                        if grabbed_texts:
                            log(f"群[{group_id}] 回抓{len(grabbed_texts)}条历史消息")
                    
                    sender_nick = resolve_qq_nick(sender) if sender else ""
                    sender_display = sender_nick if sender_nick else "群友"
                    buffer_text = "\n".join(grabbed_texts) + ("\n" if grabbed_texts else "") + f"[{sender_display}] {text}"
                    has_context = len(grabbed_texts) > 0
                    
                    GROUP_MSG_BUFFER[group_id] = {
                        "text": buffer_text,
                        "sender": sender,
                        "time": current_time,
                        "is_at_bot": is_at_bot,
                        "has_xiao_a": has_xiao_a,
                        "has_bot_nick": has_bot_nick,
                        "has_quote": has_quote,
                        "has_text_at": has_text_at,
                        "in_session": in_session,
                        "has_context": has_context,
                        "timer": Timer(delay := (5 if in_session else (10 if (is_at_bot or has_xiao_a or has_bot_nick or has_quote) and not has_context else 3)), lambda gid=group_id: process_buffered_message(gid))
                    }
                    GROUP_MSG_BUFFER[group_id]["timer"].start()
                    log(f"群[{group_id}] 新建缓冲区 delay={delay}s in_session={in_session} back_grab={len(grabbed_texts)}")
                    
                    # 被 @ 或叫「小a」或含 Bot 昵称或引用 → 立即建立会话，让缓冲窗口内的后续消息能被合并
                    if is_at_bot or has_xiao_a or has_bot_nick or has_quote:
                        GROUP_SESSION[group_id] = {
                            "remaining": _config.get("group_session_max_replies", 20),
                            "last_msg_time": time.time()
                        }
                    continue  # 等待可能的后续消息
                
                # 私聊
                sender = str(msg.get("sender", {}).get("user_id", ""))
                # NT 协议（SnowLuma 等）私聊推送的是 u_xxx 随机 UID 而非数字 QQ：
                # 与 poke 路径处理一致，回退到主目标 QQ，否则私聊会被全部跳过
                if sender.startswith("u_"):
                    sender = TARGET_QQS[0] if TARGET_QQS else sender
                if sender not in TARGET_QQS:
                    continue
                # 好友限制：该好友自动回复已关闭（webui 联系人管理页），完全静默
                if _friend_restriction(sender, "reply_enabled", True) is False:
                    continue

                text = extract_text(msg).strip()
                
                # 纯转发消息：只记录上下文，不触发回复
                has_fwd = any(isinstance(s, dict) and s.get("type") == "forward" for s in msg.get("message", []))
                has_usr_txt = any(isinstance(s, dict) and s.get("type") == "text" and (s.get("data") or {}).get("text", "").strip() for s in msg.get("message", []))
                if has_fwd and not has_usr_txt:
                    log(f"私聊[{sender}] 纯转发消息，记录为上下文，不回复")
                    continue
                
                
                # 引用消息提取（私聊）
                quoted_text = ""
                for seg in msg.get("message", []):
                    if isinstance(seg, dict) and seg.get("type") == "reply":
                        d = seg.get("data", {})
                        reply_id = d.get("id") or d.get("message_id", "")
                        # 优先使用插件内联解析的引用文本
                        inline_text = d.get("quoted_text", "")
                        if inline_text:
                            quoted_text = inline_text[:200]
                            log(f"私聊[{sender}] 引用消息(内联): {quoted_text[:60]}")
                        elif reply_id:
                            quoted_text = ""
                            try:
                                resp = api_call("get_msg", {"message_id": reply_id})
                                if resp.get("retcode") == 0:
                                    qdata = resp.get("data", {})
                                    qtext = extract_text(qdata)
                                    if qtext and qtext.strip():
                                        quoted_text = qtext.strip()[:200]
                                        log(f"私聊[{sender}] 引用消息: {quoted_text[:60]}")
                                    elif qdata.get("raw_message"):
                                        quoted_text = str(qdata["raw_message"]).strip()[:200]
                                        if quoted_text:
                                            log(f"私聊[{sender}] 引用消息(raw): {quoted_text[:60]}")
                                else:
                                    log(f"私聊[{sender}] 引用消息 get_msg 失败(retcode={resp.get('retcode')})")
                            except Exception as e:
                                log(f"私聊[{sender}] 引用消息 get_msg 异常: {e}")
                            if not quoted_text:
                                log(f"私聊[{sender}] 引用消息无法获取(id={reply_id})")
                            if not quoted_text:
                                # 回退：从私聊 chat_history 取最后一条用户消息
                                _hist = chat_history.get(sender, [])
                                for entry in reversed(_hist[-20:]):
                                    if isinstance(entry, dict) and entry.get("role") == "user" and entry.get("content"):
                                        quoted_text = entry["content"][:200]
                                        log(f"私聊[{sender}] 引用消息(历史回退): {quoted_text[:60]}")
                                        break
                        break
                
                # 引用文本合并到 text（在 for 循环之后）
                if quoted_text and text:
                    text = f"[引用] {quoted_text}\n[当前] {text}"
                elif quoted_text:
                    text = f"[引用] {quoted_text}"
                
                images = extract_images(msg)
                
                # 图片缓冲：如果收到图片但没文字，缓存起来等下一条文字消息关联
                if images and not text.strip():
                    PENDING_IMAGES[sender] = {"paths": images, "time": time.time()}
                    log("  → 图片已缓存，等待文字消息关联")
                    continue
                
                # 文字消息：检查是否有缓存的图片（5秒内有效）
                if text.strip() and sender in PENDING_IMAGES:
                    elapsed = time.time() - PENDING_IMAGES[sender]["time"]
                    if elapsed < 5:
                        images = PENDING_IMAGES.pop(sender)["paths"]
                        log(f"  → 关联缓存图片 ({elapsed:.1f}秒前)")
                    else:
                        PENDING_IMAGES.pop(sender, None)
                
                # 消息转存
                save_chat_log(sender, "对方", text)
                # 统一记忆分发（1.5 任务 2.3）：私聊 scope，四模块各取所需
                try:
                    from sandglass_bridge import memory_feed
                    memory_feed({
                        "text": text,
                        "group_id": "",
                        "sender": sender,
                        "sender_nick": resolve_qq_nick(sender) if sender else "",
                    })
                except Exception:
                    pass
                
                if not text and not images:
                    continue
                
                if not text:
                    text = "看看这张图片"

                # 私聊活跃会话登记：对方在主动对话（私聊撤回反馈的会话门槛，与群会话一致）
                try:
                    with _session_lock:
                        PRIVATE_SESSION[sender] = {"last_msg_time": time.time()}
                except Exception:
                    pass

                log(f"收到: {text[:80]}" + (f" +{len(images)}图" if images else ""))

                # ChatStyle 说话方式自动学习（私聊以 sender 为作用域）
                try:
                    if PLUGIN_OK:
                        _cs_mod = __import__("plugins.ego.chat_style", fromlist=["auto_learn"])
                        _cs_mod.auto_learn(str(sender), sender, text)
                except Exception:
                    pass

                # 撤回反馈冷却：刚回复过撤回消息后5秒内不回复新消息（防重复）
                recall_key = f"private_{sender}"
                if recall_key in RECALL_FEEDBACK_TIME:
                    elapsed = time.time() - RECALL_FEEDBACK_TIME[recall_key]
                    if elapsed < 5:
                        log(f"撤回反馈冷却中（{elapsed:.1f}s），跳过本条消息")
                        continue

                # 聊天面板：记录收到的私聊消息
                sender_name = resolve_qq_nick(sender) or sender
                chat_log("in", "private", sender, text, sender_name)
                log(f"  → sender={sender} images={len(images) if images else 0}")

                # 1. 先尝试控制指令
                if not images and try_execute_command(text, sender):
                    log("  → 匹配控制指令，已处理")
                    save_instruction(text, "[系统指令已执行]")
                    save_state()
                    time.sleep(1)
                    continue

                # 2. 启动冷却检查
                if time.time() - startup_time < STARTUP_COOLDOWN:
                    log(f"开机冷却中（{int(time.time() - startup_time)}s/{STARTUP_COOLDOWN}s），跳过私聊")
                    continue

                # 3. 私聊回复速率限制
                if sender in PRIVATE_REPLY_COOLDOWN:
                    elapsed = time.time() - PRIVATE_REPLY_COOLDOWN[sender]
                    if elapsed < PRIVATE_REPLY_INTERVAL:
                        log(f"私聊冷却中（{elapsed:.1f}s/{PRIVATE_REPLY_INTERVAL}s），跳过")
                        continue
                PRIVATE_REPLY_COOLDOWN[sender] = time.time()

                log("  → 进入AI聊天流程")
                # 3. AI 聊天（私聊记忆由 EGO/super_memory 向量召回经 before_reply 注入，见下方 ego 钩子）
                pm_ctx = ""

                # ── 画图意图检测 ──
                draw_pattern = re.compile(r'(帮我画|给我画|给我一[张只]|画一[张只]|来一[张只]|整一[张只]|出图|画个|画只|画张|画\b|生成.*?图|的图片|生辰.*?图|生日.*?图|做个.*?图|做.*?图)', re.IGNORECASE)
                if draw_pattern.search(text):
                    draw_desc = text
                    # 用户发图时用图生图，否则文生图
                    user_img = images[0] if images else None
                    log(f"[画图] 检测到画图意图: {draw_desc}" + (f" +{len(images)}张参考图" if images else ""))
                    smart_send("画图中，请稍等...", sender)
                    draw_result = handle_generate_image(draw_desc, user_ref_image=user_img)
                    log(f"[画图] 结果: {draw_result[:100]}")
                    if draw_result.startswith("图片已生成:"):
                        img_path = draw_result[len("图片已生成:"):].strip()
                        send_image(img_path, sender)
                    else:
                        smart_send(draw_result, sender)
                    continue

                # ── EGO 插件 before_reply（私聊此前缺失）：注入人格/记忆/风格到 system 级上下文 ──
                _ego_ctx = ""
                _ego_temp = None
                if PLUGIN_OK:
                    try:
                        _ego_inject, _ego_temp = _run_ego_before_reply(text, group_id="", ctx={
                            "sender": sender,
                            "user_id": sender,
                            "scope": "private",
                            "history": chat_history.get(sender, [])[-10:],
                            "bot_qq": BOT_QQ,
                            "bot_name": "小a",
                        })
                        _ego_ctx = _ego_inject
                    except Exception:
                        _ego_ctx = ""

                reply = call_deepseek(text, images if images else None, sender, private_memory_context=pm_ctx, personality_temperature=_ego_temp, ego_inject_context=_ego_ctx)
                if reply:
                    log(f"私聊AI回复({sender[:6]}): {reply[:80]}")
                    # 表情包匹配：剥离 [表情] 标记后发送文本
                    clean_reply, _meme_emotion = parse_meme_intent(reply)
                    sent_text = clean_reply.strip() if clean_reply else ""
                    sent_text = _trim_incomplete(sent_text)
                    # ── 去AI味：规则级后处理（私聊同样生效）──
                    try:
                        from de_ai import de_ai_post_process
                        sent_text = de_ai_post_process(sent_text, _config)
                    except Exception:
                        pass
                    if sent_text:
                        # ── 插件钩子：after_reply（发送前，允许插件修改回复文本）──
                        if PLUGIN_OK:
                            try:
                                pm = get_plugin_manager()
                                if pm and pm.has_hook("after_reply"):
                                    # T8: 传入 scope/sender，super_memory 才能把私聊写入正确作用域
                                    after_result = pm.execute_hook("after_reply", message=text, group_id="", reply=sent_text, context={"scope": "private", "sender": sender})
                                    if after_result and after_result.get("action") == "override":
                                        new_reply = after_result.get("reply", "")
                                        if new_reply and new_reply != sent_text:
                                            sent_text = new_reply
                            except Exception as e:
                                log(f"[Plugin] after_reply error: {e}")
                        ok = smart_send(sent_text, sender)
                    else:
                        log("私聊 仅表情标记，跳过文本发送")
                    
                    # 表情包附着
                    if MEME_OK:
                        key = f"private_{sender}"
                        now = time.time()
                        if now - MEME_LAST_SENT.get(key, 0) < MEME_COOLDOWN:
                            log("[私聊表情] 冷却中，跳过")
                        else:
                            _, emotion = parse_meme_intent(reply)
                            log(f"[私聊表情] 解析: emotion={emotion}")
                            if emotion is None:
                                emotion = auto_detect_emotion(reply)
                                log(f"[私聊表情] 自动检测: {emotion}")
                            if emotion:
                                meme_path = match_meme(emotion)
                                if meme_path:
                                    send_image(meme_path, sender)
                                    MEME_LAST_SENT[key] = now
                                    log(f"[私聊表情] 发送: {os.path.basename(meme_path)}")
                    else:
                        log("[私聊表情] 引擎未加载")
                    
                    ch = chat_history.setdefault(sender, [])
                    ch.append({"role": "user", "content": text})
                    ch.append({"role": "assistant", "content": reply[:500]})
                    # C2 存储对齐：私聊截断 60→24 轮（组装只取最近10轮，储存更多纯占内存+IO）
                    chat_history[sender] = ch[-24:]
                    # 转存 AI 回复
                    save_chat_log(sender, "小a", text, reply[:500])
                    save_instruction(text, reply)
                    # 统一记忆分发（1.5 任务 2.3）：私聊回复侧同步
                    try:
                        from sandglass_bridge import memory_feed
                        memory_feed({
                            "text": text,
                            "group_id": "",
                            "sender": sender,
                            "reply": reply,
                        })
                    except Exception:
                        pass
                else:
                    send_message("收到消息了，但暂时无法处理，请稍后再试。", sender)

                save_state()
                time.sleep(1)

            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            log(f"错误: {e}\n{traceback.format_exc()}")
            # 主循环不因连续异常退出：否则任何"每轮必抛"的错误会让 Bot 在 ~60 秒内静默暴毙。
            # 改为退避重试 + 存活性告警，异常消除后自动恢复。
            if consecutive_errors >= 20:
                log(f"[致命] 主循环已连续 {consecutive_errors} 次异常但保持存活（退避重试中），请检查配置/OneBot/网络/插件")
            time.sleep(min(consecutive_errors, 10))

        # 优雅关闭检查
        # 定期清理（每 ~200 轮 ≈ 10分钟）
        if not hasattr(main, "_cleanup_counter"):
            main._cleanup_counter = 0
        main._cleanup_counter += 1
        if main._cleanup_counter >= 200:
            main._cleanup_counter = 0
            try:
                now = time.time()
                # 清理过期的 GROUP_RECENT_MSGS（>X秒）
                for gid in list(GROUP_RECENT_MSGS.keys()):
                    msgs = GROUP_RECENT_MSGS[gid]
                    fresh = [e for e in msgs if now - e[0] < GROUP_RECENT_MSGS_WINDOW]
                    if fresh:
                        GROUP_RECENT_MSGS[gid] = fresh
                    else:
                        GROUP_RECENT_MSGS.pop(gid, None)
                # 清理过期的 GROUP_BOT_REPLIES（>120秒）
                for gid in list(GROUP_BOT_REPLIES.keys()):
                    replies = GROUP_BOT_REPLIES[gid]
                    fresh = [e for e in replies if now - e[0] < 120]
                    if fresh:
                        GROUP_BOT_REPLIES[gid] = fresh
                    else:
                        GROUP_BOT_REPLIES.pop(gid, None)
                # 清理已退出群的 SESSION
                for gid in list(GROUP_SESSION.keys()):
                    gs = GROUP_SESSION[gid]
                    if now - gs.get("last_msg_time", 0) > SESSION_TTL * 2:
                        GROUP_SESSION.pop(gid, None)
                # F: 定期清理上下文缓存（摘要/时间注入/HISTORY_CACHE），
                #    只在群超过 24h 无消息或私聊历史已清时回收，防 dict 无限增长
                _ctx_ttl = 24 * 3600
                for gid in list(GROUP_SUMMARY.keys()):
                    _last = _GROUP_SUMMARY_AT.get(gid) or _GROUP_SUMMARY_INJECT_AT.get(gid) or 0
                    if not isinstance(_last, (int, float)) or now - _last > _ctx_ttl:
                        GROUP_SUMMARY.pop(gid, None)
                        _GROUP_SUMMARY_PREV.pop(gid, None)
                        _GROUP_SUMMARY_AT.pop(gid, None)
                        _GROUP_SUMMARY_LEN.pop(gid, None)
                        _GROUP_TIME_INJECT_AT.pop(gid, None)
                        _GROUP_SUMMARY_INJECT_AT.pop(gid, None)
                        _HISTORY_CACHE.pop(f"g_{gid}", None)
                        _HISTORY_CACHE_FP.pop(f"g_{gid}", None)
                        _HISTORY_CACHE_OLD.pop(f"g_{gid}", None)
                # 私聊历史缓存的 key 跟随 chat_history 是否已清（保留活跃 QQ 的缓存）
                for qq in [k[2:] for k in list(_HISTORY_CACHE.keys()) if k.startswith("p_")]:
                    if qq not in chat_history or not chat_history.get(qq):
                        _HISTORY_CACHE.pop(f"p_{qq}", None)
                        _HISTORY_CACHE_FP.pop(f"p_{qq}", None)
                        _HISTORY_CACHE_OLD.pop(f"p_{qq}", None)
                # 定期保存状态（防止崩溃丢失记忆；D 节流已生效，此处 force 兜底）
                save_state(force=True)
            except Exception:
                pass

        time.sleep(3)


if __name__ == "__main__":
    main()