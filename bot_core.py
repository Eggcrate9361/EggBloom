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

import capabilities_view
import inbound
import reply_budget
import reply_drop
import speech_pace


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


def _dispatch_typed_before_reply(pm, *, message: str, group_id: str = "",
                                 context: Optional[dict] = None):
    """薄封装：派发 typed `on_before_reply`，结果打上 `_typed` 标记。

    为什么要有这个包装而不是直接调 harness：
      · bot_core 里 5 个派发点都要用同一套（PM 缺失/异常/无插件都不能炸主链）；
      · 打标记是调用方（聚合循环）区分"旧式集合"与"typed 集合"的唯一依据；
      · 集中一处，将来换实现只改这里。
    任何失败 → 返回 []，绝不抛。
    """
    try:
        from harness import plugin_bridge as _pb
    except Exception:
        return []
    try:
        items = _pb.dispatch_before_reply(
            pm, message=message, group_id=group_id, context=context, log_fn=log)
    except Exception as e:
        try:
            log(f"[plugin_bridge] typed before_reply 失败（已忽略）: {e}")
        except Exception:
            pass
        return []
    out = []
    for name, res in items:
        if isinstance(res, dict):
            res = dict(res)
            res["_typed"] = True
        out.append((name, res))
    return out


def _dispatch_typed_after_reply(pm, *, message: str, group_id: str = "",
                                reply: str, context: Optional[dict] = None):
    """薄封装：派发 typed `on_after_reply`，返回新回复文本或 None。

    空串是**有意义**的返回值（= 抑制发送），必须原样透传，不能被当成"无结果"。
    """
    try:
        from harness import plugin_bridge as _pb
    except Exception:
        return None
    try:
        return _pb.dispatch_after_reply(
            pm, message=message, group_id=group_id, reply=reply,
            context=context, log_fn=log)
    except Exception as e:
        try:
            log(f"[plugin_bridge] typed after_reply 失败（已忽略）: {e}")
        except Exception:
            pass
        return None


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
        # ── typed 钩子并列派发（ABI v2 的 on_before_reply，此前从未被调用）──
        # 与旧式**并列**收集，不动上面那行；下面同一循环按同一协议处理两者，
        # 避免"两套 skip/inject 各管一段"导致的行为分叉。
        # 注意：旧式过滤只认 plugin_name == "ego"，而 typed 插件名各不相同，
        # 所以 typed 结果带 _typed 标记，循环里对它不做插件名过滤。
        _typed_interventions = _dispatch_typed_before_reply(
            pm, message=message, group_id=group_id, context=ctx)
        if not interventions:
            interventions = []
        if _typed_interventions:
            interventions = list(interventions) + list(_typed_interventions)
        if not interventions:
            return "", None
        parts = []
        for plugin_name, result in interventions:
            if not isinstance(result, dict):
                continue
            # 旧式路径只收 ego 的注入；typed 路径收所有（它本就是显式注册的）
            if not result.get("_typed") and plugin_name != "ego":
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


def _inject_sleep_state(dynamic_parts, is_group: bool = True):
    """静默时段里被叫醒 → 告诉模型"你在睡觉"。

    ★ 为什么群聊私聊都调这一个函数：两处各写一遍提示词，改一处漏一处
      （本仓库已经因为"同一个概念两处定义"吃过三次亏）。
    ★ 为什么进 dynamic_parts 而不是 static：它随 quiet_hours 命中的时段变化，
      进了 static 会让整段前缀在 1:00/7:00 两个时刻断裂并重算缓存。
      放尾部只影响最后几十个 token。
    """
    try:
        from harness.time_ctx import in_quiet_hours as _iqh
        if not _iqh(_config.get("quiet_hours")):
            return
    except Exception:
        return
    if dynamic_parts is None:
        return
    dynamic_parts.append(
        "【你正在睡觉】这个点你已经睡了，是被叫醒的。"
        "回复要短，一两句就够，语气带着困意、有点迟钝。"
        "可以顺口嫌对方这么晚还不睡，但不要长篇大论，"
        "也不要装作刚从别的事情里回来的样子。")
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
import log_levels  # noqa: E402  # 日志分级规则的唯一来源，WebUI 那边也读它
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


def _session_round_budget():
    """会话模式能用几轮。

    2026-10-21 修：桌面端「设置 → 最大轮数」写的是 `session_max_rounds`，
    但主链路只读 `group_session_max_replies`（默认 20）——用户在 UI 里改了完全没效果。
    现在：显式配了 group_session_max_replies 就以它为准（兼容旧配置），
    否则回落到 session_max_rounds。
    """
    try:
        v = _config.get("group_session_max_replies")
        if not v:
            # 现读配置（而不是只用导入期常量），改配置后无需重启即可生效
            v = _config.get("session_max_rounds", SESSION_MAX_ROUNDS)
        return max(1, int(v))
    except (TypeError, ValueError):
        return 20


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
BOT_QQ = _config.get("bot_qq", "")
TARGET_QQS = _config.get("target_qqs", [])
DEEPSEEK_API_KEY = _config.get("api_key", "")
DEEPSEEK_URL = _config.get("api_url", "https://api.deepseek.com/chat/completions")
GROUP_IDS = _config.get("group_ids", [])
BOT_GROUP_NICK = _config.get("bot_group_nick", "")
STARTUP_COOLDOWN = _config.get("startup_cooldown", 30)
MSG_SPLIT_LEN = _config.get("msg_split_len", 2000)
# 群聊回复"整块发送"的总字数阈值（有效字，符号不计）。
# ≤ 此值 → 拆成多条发（碎片节奏，像人连发）；> 此值 → 整块一次发完（避免刷屏）。
# 1.5.7 前是写死的 40，且是**长消息一次性发送**的开关（>40 就并成一条），
# 语义正好相反：字数越多反而越不该分段。这里改成"越多越整块"，并提到 100。
GROUP_SEND_BLOCK_THRESHOLD = _config.get("group_send_block_threshold", 100)
# 单次回复最多拆成几条（防刷屏）。超过就把尾部并成一条。
# ★ 2026-10-24：兜底值 3 → 5，与 1.5.7 设计意图和 example 对齐。
#   此前三处不一致：example 写 5、这里兜底 3、DEFAULT_CONFIG 里没有这个键
#   → 用户复制 example 得 5，不复制得 3，而配置页上根本没有这一项。
#   同一件事三个数，正是这仓库最高频的 bug 形态。
GROUP_SEND_MAX_PARTS = _config.get("group_send_max_parts", 5)

PERSONALITY_FILE = os.path.join(SCRIPT_DIR, "personality.txt")

# ★ 2026-10-27 人格来源统一到一份：`plugins/personality.txt`。
#   事实（实测）：真正生效的从来只有它 —— EGO 的 PERSONALITY_PATH 指向它，
#   配置页的人格编辑器、蒸馏人格卡也都写这一份。
#   但兜底链原来把**根目录** personality.txt 排在第一，而根目录那份是历史遗留，
#   内容停在 1.5.8 之前（旧版「你是小a，18 岁的网瘾青年」440 字，
#   对比现行「我叫小a…三五个字」1350 字）。
#   后果：EGO 一旦不可用（异常/被摘掉/文件缺失），人格会**悄悄退回旧版**，
#   语气说变就变，而且没有任何日志提示 —— 排查时只会觉得"今天怪怪的"。
#   现在兜底也先读 PLUGINS 那份，根目录那份只作为最后的历史兼容项。
PERSONA_CANONICAL = os.path.join(SCRIPT_DIR, "plugins", "personality.txt")
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
    # 群聊分条上限：与 MSG_SPLIT_LEN 同属"一条回复拆几条发"，那个能热重载
    # 这个不能，改完配置不生效 —— 同一功能域两个行为。
    "GROUP_SEND_MAX_PARTS", "GROUP_SEND_BLOCK_THRESHOLD",
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
            "GROUP_SEND_MAX_PARTS": "group_send_max_parts",
            "GROUP_SEND_BLOCK_THRESHOLD": "group_send_block_threshold",
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

# P0-3：状态写盘失败的观测位（原实现是裸 `except Exception: pass`，失败无声无息）
_STATE_SAVE_FAILS: dict = {"n": 0, "last": ""}

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

# HUD 顶部状态条的运行时计数（**本次运行**）：消息条数 + 活跃群集合。
# 为什么单独维护，而不是在 /api/status 里现算：
#   ① /api/status 被 WebUI 每 1s 拉一次，调用方 fetch 超时只有 0.5s —— 任何
#      数据库 COUNT / 网络调用都可能把状态接口拖死，进而把 HUD 冻住；
#   ② 原实现写的是 `len(chat_history) if 'chat_history' in dir() else 0`，
#      而 `dir()` 在函数里返回的是**局部**作用域，全局名永远不在其中 →
#      恒为 False → 恒 0。用户截图「消息 0 / 群 0 永不变化」就是这个 bug。
_HUD_STATS: Dict[str, Any] = {"messages": 0, "groups": set()}

# Prompt cache: 固定前缀优化 token 费用（DeepSeek 缓存机制：前缀相同 → 旧 token 只收 10%）
# ★ 2026-10-13 结构重写（token_stats 实测：命中额钉死在 ~3200 = 只有 static_prompt 命中，
#   摘要+近4轮每次全 miss）。旧实现把摘要建立在**滑动窗口**上（history[-10:] 去掉最近4轮），
#   每来一轮窗口头部掉一条 → 指纹变 → 摘要整段重建 → 文本逐字节对不上 → 缓存断在 static 之后。
#   新实现：摘要是 **append-only 账本**（每条历史只入账一次，行永不清除/不重排），
#   窗口滑动只是往账本尾部追加新行 —— 前缀逐字节稳定，DeepSeek 缓存能一路命中到摘要末尾。
_HISTORY_SUMMARY_LINES: Dict[str, List[str]] = {}  # key → 摘要行账本（append-only）
_HISTORY_SUMMARY_TEXT: Dict[str, str] = {}         # key → 拼好的摘要文本（账本变了才重拼）
_HISTORY_LAST_WINDOW: Dict[str, List[tuple]] = {}  # key → 上次处理的窗口 [(fp, line), ...]
_HISTORY_SUMMARY_LINE_CAP = 300                    # 账本行数上限：超了裁头（罕见，接受一次 miss）

# D: save_state 节流计数（累计20轮或60s落盘一次）
_state_write_count: int = 0
_state_last_write: float = 0.0

# 群聊 @ 会话模式：被 @ 后连续回复 20 条，或 5 分钟无新消息后重置
GROUP_SESSION: Dict[str, Dict[str, Any]] = {}  # {group_id: {"remaining": int, "last_msg_time": float}}

# 私聊活跃会话：{user_id: {"last_msg_time": float}} —— 私聊撤回反馈的会话门槛（与群会话一致）
PRIVATE_SESSION: Dict[str, Dict[str, Any]] = {}

# 群聊消息合并：同一群 X 秒内的多条消息合并为一条
GROUP_MSG_BUFFER: Dict[str, Dict[str, Any]] = {}  # {group_id: {"text": "", "sender": "", "time": float, "timer": None}}

# ── 群回复流水线（2026-10-25，1.5.8 重新打包）────────────────────────────
# 一条群消息从进来到发出去经过 INBOUND → ACCUMULATE → DECIDE → GENERATE → DELIVER。
# 相位只记"执行中的那一段"，收批不占相位（批次在不在等由 GROUP_MSG_BUFFER 决定）。
# 为什么单独一把锁：_buffer_lock 非重入（见 12078 的注释），相位判断不该和
# 缓冲区读写抢同一把锁。
# REPLY_PHASE_MAX 依据：模型最坏 3×120 超时 + 2×8 退避 ≈ 376 秒，留 44 秒余量。
GROUP_PIPELINE: Dict[str, Dict[str, Any]] = {}
_pipeline_lock = Lock()        # 只保护 GROUP_PIPELINE
REPLY_PHASE_MAX = 420          # 单个相位最长持有时间（秒）

# ── 私聊消息合并（2026-10-25）──────────────────────────────────────────
# 事故：私聊两条 4 秒内发出的消息被**分开回复**了两次。
# 群聊一直有 GROUP_MSG_BUFFER + Timer 合并，私聊从来没有 —— 它收到一条就
# 立刻进 AI 流程（日志实证：13:12:30 「没有养哦」当场开跑，13:12:34 才来的
# 第二条只能在 MSG_QUEUE 里排队；等第一条 21 秒生成完才轮到它）。
#
# 私聊处理段内联在 main() 里（450 行，依赖 5 个循环内局部变量），
# **不抽函数**：抽出去要动 5 处状态交接，风险比收益大。
# 改为在入口拦下、攒够时间再**重新入队**，到点后走的是原来那条完整链路
# （记忆/画像/画图/ego/表情包/事后校验全都不动）。
PRIVATE_MSG_BUFFER: Dict[str, Dict[str, Any]] = {}
#: 私聊发言节奏（1.5.9 通用化）：窗口不再拍死 8 秒，按这个人的间隔中位数算。
#: 关掉 speech_pace_enable 就回到固定 private_msg_buffer_window。
_PRIVATE_PACE = speech_pace.SpeechPace()
# 重新入队时打的标记：二次经过入口不再入缓冲，直接往下走
_REQUEUE_FLAG = "_egg_merged"


def _private_batch_delay(*, first_time, now=None, has_image=False,
                         quiet=None, cap=None):
    """私聊这批消息该等几秒。

    **复用 `_batch_delay`，不另写规则** —— 用户 2026-10-23 定下的
    「同一个人接着说话就重置倒计时，整批有硬顶」对私聊同样成立，
    再写一份必然和群聊分叉（这个仓库已经因为"同一概念两处判据"踩过四次）。

    与群聊的差异只有输入：私聊永远是"同一个人"，所以 same_sender / in_session
    恒为 True；私聊也没有 @ 概念，triggered 恒 False。
    """
    # 2026-10-27 真机：连发 5 条被切成 3 批，回了 3 次。
    # 群里 3 秒够用（对话在流转，该回了），私聊是"听他把话说完"，
    # 要有更长的窗口和更松的硬顶 —— 只改参数，不另写规则。
    return _batch_delay(
        same_sender=True, in_session=True, triggered=False,
        has_context=False, has_bare_image=has_image,
        first_time=first_time, now=now,
        window=quiet if quiet is not None else _delay_cfg("private_msg_buffer_window", 8),
        hard_cap=cap if cap is not None else _delay_cfg("private_batch_hard_cap", 15),
    )


def _private_flush(sender: str):
    """合并窗口到点：把这批消息作为**一条**重新入队，交给原链路处理。"""
    with _session_lock:
        rec = PRIVATE_MSG_BUFFER.pop(sender, None)
    if not rec:
        return
    # 告诉节奏器"我在这一刻判定他说完了"：他下次开口的间隔用来校准倍数。
    # 判早了（half-quiet 内又开口）→ 倍数上调；判对了 → 缓慢回落。
    if _config.get("speech_pace_enable", True):
        try:
            _PRIVATE_PACE.mark_settled(sender, time.time())
        except Exception:
            pass
    parts = [p for p in (rec.get("parts") or []) if str(p).strip()]
    text = "\n".join(parts).strip()
    images = list(rec.get("images") or [])
    if not text and not images:
        return
    if len(parts) > 1:
        log(f"私聊[{sender[:6]}] 合并 {len(parts)} 条为一条: {text[:60]}")
    # 造一条"像协议层推来的"消息重新入队。原 msg 里只保留 message 段：
    # 其余字段（post_type/message_type/time）由入口自己补齐。
    _msg = {
        "message": [],
        _REQUEUE_FLAG: True,
    }
    if text:
        _msg["message"].append({"type": "text", "data": {"text": text}})
    for _p in images:
        # 图**已经在本地**了（入口早已下载过）。用 url 字段而不是 file 字段：
        # extract_images 见到 file 会先调 onebot_fresh_image 问协议层要新地址，
        # 对本地路径是白跑一次 HTTP；用 url 直接命中它的"本地文件"分支。
        _msg["message"].append({"type": "image", "data": {"url": _p}})
    MSG_QUEUE.put({
        "post_type": "message",
        "message_type": "private",
        "sender": {"user_id": sender},
        "user_id": sender,
        "time": int(time.time()),
        # ★ 必须给一个唯一 message_id。入口去重对"无 id"的消息走
        #   时间戳水位线（`_msg_time <= LAST_MSG_TIME` 就丢），而合并后重新入队的
        #   这条 time 必然 <= 之前那条 → 会被自己的去重闸吞掉，表现为"合并了但不回"。
        "message_id": f"_merged_{sender}_{int(time.time() * 1000)}",
        "message": _msg["message"],
        _REQUEUE_FLAG: True,
    })


def _should_buffer_private(text: str, images, msg) -> bool:
    """这条私聊该不该先攒进缓冲（而不是立刻处理）。

    单独成一个纯函数，是为了**能被测试直接打**。写在 main() 里的 if 只能靠
    读源码验证，而"if 写错了"恰恰是最容易发生又最难看出的失误
    （2026-10-25 实测：`if False and ...` 把整个合并关掉，grep 类测试照样全绿）。
    """
    if not isinstance(msg, dict):
        return False
    # 合并后重新入队的那一条必须放行，否则自己拦自己 → 永远不回
    if msg.get(_REQUEUE_FLAG):
        return False
    # 完全没有内容的消息不值得占一个窗口
    if not (text or "").strip() and not images:
        return False
    return True


def _cancel_private_buffer(sender: str) -> int:
    """丢掉该用户未处理的合并批次，返回被丢掉的条数（0 = 本来就没有）。

    单独成函数，理由与 `_should_buffer_private` 相同：写在撤回分支里的
    pop 只能靠读源码验证，而"漏了这一步"恰恰是新增缓冲区最容易犯的错
    ——群聊有取消、私聊忘了取消，表现为"回复了用户已经撤回的内容"。

    ★ 顺序：先取 timer 再 pop（pop 完就摸不到了）。
    ★ key 必须与缓冲区一致（真实 QQ 号），u_xxx 的归一化在调用方做。
    """
    with _session_lock:
        rec = PRIVATE_MSG_BUFFER.pop(str(sender), None)
    if not rec:
        return 0
    _t = rec.get("timer")
    if _t:
        try:
            _t.cancel()
        except Exception:
            pass
    n = len([x for x in (rec.get("parts") or []) if str(x).strip()])
    log(f"[撤回] 私聊[{str(sender)[:6]}] 已取消未处理合并批次（{n} 条），不回复已撤回内容")
    return n


def _buffer_private_message(sender: str, text: str, images, msg) -> bool:
    """私聊入口闸：把消息攒进缓冲并起定时器，返回 True = 本条已被拦下。

    返回值语义与群聊 `continue` 一致：调用方拦下后用 `continue` 跳过本条。
    """
    if not _should_buffer_private(text, images, msg):
        return False
    now = time.time()
    with _session_lock:
        rec = PRIVATE_MSG_BUFFER.get(sender)
        if rec:
            rec.setdefault("parts", []).append(text)
            if images:
                rec.setdefault("images", []).extend(images)
            t = rec.get("timer")
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
        else:
            rec = {
                "parts": [text] if text else [],
                "images": list(images or []),
                "first_time": now,
            }
            PRIVATE_MSG_BUFFER[sender] = rec
        # deadline 只算一次。第一版在这里算了两遍（if/else 各一遍），
        # 两个分支的输入完全相同 —— 纯冗余，且后人改一处忘了另一处就会分叉。
        # 节奏判据（2026-10-27）：quiet = 他平时间隔的 2.5 倍，cap = 4 倍。
        # 没有历史时退回固定值 —— 那是"还不知道"，不是设计值。
        _quiet = _cap = None
        if _config.get("speech_pace_enable", True):
            _PRIVATE_PACE.note(sender, now)
            _quiet = _PRIVATE_PACE.quiet_needed(sender)
            _cap = _PRIVATE_PACE.hard_cap(sender)
        rec["deadline"] = now + _private_batch_delay(
            first_time=rec["first_time"], now=now,
            has_image=bool(rec.get("images")),
            quiet=_quiet, cap=_cap)
        _delay = max(0.5, rec["deadline"] - now)
        _t = Timer(_delay, lambda s=sender: _private_flush(s))
        try:
            _t.daemon = True
        except Exception:
            pass
        rec["timer"] = _t
        _t.start()
    log(f"私聊[{sender[:6]}] 入缓冲 等 {_delay:.1f}s（本批 {len(rec.get('parts') or [])} 条）"
        + (f" 节奏 quiet={_quiet:.1f} cap={_cap:.1f} pace={_PRIVATE_PACE.median_gap(sender):.1f}"
           if _quiet is not None else ""))
    return True


# 潜水前观察（2026-10-13，学 qq-bridge 的「沉睡前观察」）：
# 决策插件判"这次不接"时会返回 action=observe，宿主过 N 秒把这段上下文**再看一眼**；
# 期间若真有新消息进来（生成了新缓冲），这次观察就作废 —— 那才是更新的上下文。
GROUP_OBSERVE: Dict[str, Dict[str, Any]] = {}
GROUP_RECENT_MSGS: Dict[str, List[Tuple]] = {}  # {group_id: [(timestamp, sender, text), ...]}  最近X秒消息缓存
GROUP_TIMELINE: Dict[str, List[Tuple]] = {}  # {group_id: [(timestamp, sender_id, sender_nick, text), ...]}  全局消息时间线（概率门之前记录）
def _search_year_mismatch(query: str, result_text: str) -> Optional[str]:
    """搜到的结果**年份**与问句对不上？返回错的那个年份串，没错返回 None。

    ★ 2026-10-27 真机事故（群 829854136 18:13）：
      用户问「今年明日方舟周年半周年限定是谁」，模型把检索词改写成
      「明日方舟 2026 半周年 限定 干员」（「今年」被改掉了），
      搜回来的全是 2025 年的旧闻 —— 而 11 月的消息（【以风雪为誓】11/01）
      就在另一条结果里，因为没有时效加权被压到了后面。
      模型据此答「十一月的还没宣」。

      **搜到了但答的是别的年份，比查不到更危险**：
      查不到模型会说"没查到"，答错年份模型会笃定地说错话。

    判据：问句里的年份 vs 结果文本里出现的日期年份（`[2025-06-18 · site]`）。
    两边完全不交集才算不符 —— 只要有一条对得上就不算（那可能是对的答案）。
    """
    try:
        import search_core as _sc
    except Exception:
        return None
    try:
        _ys = _sc.query_years(query)
        if not _ys:
            return None
        _found = set(re.findall(r"\[(\d{4})-\d{2}-\d{2}", str(result_text or "")))
        if not _found:
            return None
        if _found & _ys:
            return None
        return "/".join(sorted(_found))
    except Exception:
        return None


def _drop_reason(*, new_msgs=0, age=0.0, turns=2, hard_sec=120.0, text="",
                 addressed=False):
    """这条回复该不该整条丢。返回原因（空串 = 照发）。

    ★ 2026-10-27：判据搬到 `reply_drop.py`，这里是薄包装（保留旧签名，
      让既有测试与调用点继续工作）。真正的说明在那边 —— 简版：

      旧判据用绝对时长卡，实测把「凡搜索必丢」变成了结构性行为
      （搜索链最低 26 秒，线画在 25）。现在主判据是**话头有没有翻篇**，
      时间只做极端兜底，线由实测 p99 导出而不是拍。
    """
    return reply_drop.drop_reason(
        new_msgs=new_msgs, age=age, turns=turns, hard_sec=hard_sec,
        addressed=addressed, text=text, official_re=_OFFICIAL_RE)


#: 生成耗时实测分布 + 丢弃线导出（详见 reply_drop.LatencyProfile）
_latency = reply_drop.LATENCY


#: 官腔判据（1.5.9 §B5）：命中即整条丢，不改文字。
#: 只收"无论如何都不该由 Bot 说出口"的几句 —— 收窄是为了不误杀。
_OFFICIAL_RE = re.compile(r"作为\s*AI|作为一个人工智能|希望(对你|可以帮到你)|"
                          r"综上所述|以上就是|如有(需要|疑问)")

#: 发言率观测（1.5.9 §B6，**只记不改**）：{gid: {"in": [ts], "out": [ts]}}
#: 收紧放行线会让机器人明显变哑，所以这一版只统计，参数留到有真实数据再定。
_SPEAK_WINDOW: Dict[str, Dict[str, List[float]]] = {}
_SPEAK_LOCK = Lock()
_SPEAK_WIN_SEC = 1800

#: 入站内容指纹判重。
#: 窗口 1 秒 —— 它挡的是"同一条事件被协议层推了两次"（毫秒级），
#: 不是"同一句话被说了两次"。30 秒会误伤真人重复发言（实测「我是鸡蛋」
#: 隔 3 秒说第二遍被丢），6 秒仍然覆盖 3 秒，同样是误伤。
#: 真人重复说话由模型看上下文处理，不靠字符串比对。
_INBOUND_DUPE = inbound.DupeGuard(window=1.0)


def _speak_note(group_id, kind: str):
    """记一次入站（in）或出站（out），返回 30 分钟窗口内的 (in, out)。"""
    gid = str(group_id or "")
    if not gid or kind not in ("in", "out"):
        return (0, 0)
    now = time.time()
    with _SPEAK_LOCK:
        rec = _SPEAK_WINDOW.setdefault(gid, {"in": [], "out": []})
        rec[kind] = [t for t in rec[kind] if now - t < _SPEAK_WIN_SEC]
        rec[kind].append(now)
        return (len(rec["in"]), len(rec["out"]))
GROUP_SUMMARY: Dict[str, str] = {}  # {group_id: str}  群聊历史摘要（超过窗口的旧消息压缩）
_GROUP_SUMMARY_PREV: Dict[str, str] = {}  # {group_id: str}  上上次摘要（E 三段式拼接用，保留主题锚）
_GROUP_SUMMARY_AT: Dict[str, float] = {}  # {group_id: last_summary_ts}  摘要刷新节流（T2）
_GROUP_SUMMARY_LEN: Dict[str, int] = {}  # {group_id: last_summarized_len}  上次压缩时的历史条数
_GROUP_TIME_INJECT_AT: Dict[str, float] = {}  # {group_id: ts}  时间注入节流（G：距上次注入≥600s，按时间戳判定）
_GROUP_SUMMARY_INJECT_AT: Dict[str, float] = {}  # {group_id: ts}  摘要注入节流（10min）
_GROUP_SUMMARY_START: Dict[str, Dict[str, Any]] = {}  # P7：{group_id: {at, len}} 压缩事务的开始标记
GROUP_BOT_REPLIES: Dict[str, List[Tuple[float, str]]] = {}  # {group_id: [(timestamp, text), ...]}  追踪 Bot 最近回复
GROUP_LAST_EXIT: Dict[str, float] = {}  # {group_id: timestamp}  记录上次退场时间，15分钟内不重复退场语
GROUP_LAST_ANY_MSG: Dict[str, float] = {}  # {group_id: timestamp}  追踪群内任意成员最新消息时间
GROUP_LAST_BOT_MSG: Dict[str, float] = {}  # {group_id: timestamp}  追踪 Bot 每个群最后发言时间
POKE_COUNTS: Dict[str, dict] = {}  # {"group_uid": {"count": N, "time": ts}}  戳一戳计数（poke_timeout 超时重置，默认 600 秒）

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
                        # 计数三兄弟：**本次运行**的真实值。以前写的是
                        # `x in dir()` —— dir() 在函数里只返回局部名，全局名永远
                        # 不在其中 → 三个字段恒 0（用户截图：消息 0 / 群 0）。
                        # 消息/活跃群由事件循环里的 _HUD_STATS 维护；群数优先用
                        # 配置的监控群列表（稳定），没有配置时退回本次活跃群数。
                        "messages_total": int(_HUD_STATS["messages"]),
                        "groups_active": (len(GROUP_IDS) if GROUP_IDS
                                          else len(_HUD_STATS["groups"])),
                        "groups_seen": len(_HUD_STATS["groups"]),
                        "images_generated": 0,
                        "memes_sent": 0,
                        "plugin_count": 0,
                        "plugin_active": 0,
                        "llm_status": "connected" if REQUESTS_OK else "disconnected",
                        "llm_model": _config.get("model_name", "deepseek-flash"),
                        "memory_total": 0,
                        "session_count": len(GROUP_SESSION),
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
            if self.path == "/api/token-stats":
                # ★ Token 页会话级计数（群聊/私聊/网页）必须由**本进程**回答：
                #   token_stats.record() 只在 bot_core 里被调用（LLM 调用发生在这边），
                #   webui_server 自己的 _cumulative 永远是 0 —— Token 页三行恒 0 的根因。
                #   日/近7天/近30天/累计由 token_stats 从 jsonl 聚合，跨重启有效。
                try:
                    from token_stats import snapshot as _ts_snap, history as _ts_hist
                    try:
                        from token_stats import context_pressure as _ts_press
                        _ctx = _ts_press()
                    except Exception:
                        _ctx = []
                    data = {"snapshot": _ts_snap(), "recent": _ts_hist(50), "context_pressure": _ctx}
                except Exception as e:
                    data = {"error": str(e), "snapshot": {}, "recent": [], "context_pressure": []}
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
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
                        # P0-2：把白名单判定也暴露出来，否则「Bot 为什么不在这个群说话」无从排查
                        ginfo["whitelisted"] = _group_allowed(gid)
                        ginfo["block_reason"] = _group_block_reason(gid) or ""
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
            # ── Agent：SSE 事件流（**运行时快照，不发文件**）──
            #  GET /api/agent/stream?session_id=xxx&cursor=n
            #  返回：application/x-ndjson（每行一个 JSON 事件，天然支持断线重连）
            #
            #  为什么用 ndjson 而不是 text/event-stream：
            #    1) 前端用 fetch + ReadableStream 逐行解析即可，不必引 EventSource；
            #    2) 每个事件自包含（带 seq），断线重连用 cursor 增量补，不丢事件；
            #    3) 出错时可以直接吐一个 {"type":"error"} 行，语义不混。
            if self.path.startswith("/api/agent/stream"):
                try:
                    from urllib.parse import urlparse, parse_qs
                    qs = parse_qs(urlparse(self.path).query or "")
                    sid = str((qs.get("session_id") or [""])[0] or "")
                    try:
                        cursor = int((qs.get("cursor") or ["0"])[0] or 0)
                    except (TypeError, ValueError):
                        cursor = 0
                    if not sid:
                        body = json.dumps({"error": "缺少 session_id"}).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    from harness import agent_service as _agent
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache, no-store")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    _q = _agent.subscribe(sid)
                    sent = 0
                    deadline = time.time() + 300.0     # 单连接最长 5 分钟，超时让前端重连
                    # 连接时可能 chat 还没开始（前端先连流、再发请求）→ 给一个
                    # **启动宽限期**：在这段时间内即使会话不忙也不收尾，等它忙起来。
                    # 一旦 seen_activity=True（收到过任何事件），空闲就立即收尾。
                    grace_until = time.time() + 5.0
                    seen_activity = False
                    try:
                        while time.time() < deadline:
                            _batch = _agent._drain(sid)
                            for _ev in _batch:
                                sent += 1
                                seen_activity = True
                                if sent <= cursor:
                                    continue
                                self.wfile.write((json.dumps(_ev, ensure_ascii=False,
                                                             default=str) + "\n").encode("utf-8"))
                            if _batch:
                                try:
                                    self.wfile.flush()
                                except Exception:
                                    break
                            _busy = bool(((_agent.get_session(sid) or {}).get("busy")))
                            if _busy:
                                seen_activity = True
                            # 收尾条件：**已经看到过活动** 且 当前空闲 且 无新事件；
                            # 或者宽限期已过且从未活动（例如会话不存在）→ 发 idle 收尾。
                            if not _busy and not _batch:
                                if seen_activity:
                                    self.wfile.write((json.dumps(
                                        {"type": "idle", "ts": time.time()},
                                        ensure_ascii=False) + "\n").encode("utf-8"))
                                    try:
                                        self.wfile.flush()
                                    except Exception:
                                        pass
                                    break
                                if time.time() >= grace_until:
                                    self.wfile.write((json.dumps(
                                        {"type": "idle", "ts": time.time(),
                                         "reason": "无活动"},
                                        ensure_ascii=False) + "\n").encode("utf-8"))
                                    try:
                                        self.wfile.flush()
                                    except Exception:
                                        pass
                                    break
                            time.sleep(0.08)
                    finally:
                        _agent.unsubscribe(sid, _q)
                    return
                except Exception as e:
                    try:
                        self.wfile.write((json.dumps({"type": "error", "error": str(e)},
                                                     ensure_ascii=False) + "\n").encode("utf-8"))
                    except Exception:
                        pass
                    return

            # ── Agent 只读端点（GET）：与 POST 共用同一套路由 ──────────
            #  webui 的 GET 读取走这里；此前只在 do_POST 里实现，
            #  导致前端 GET /api/agent/status 拿到 404 —— 状态灯永远"检测中"。
            if self.path.startswith("/api/agent/") and not self.path.startswith("/api/agent/stream"):
                self._agent_route(self.path, self._agent_query())
                return

            # ── 管理员通知历史（通知页可视化 + 全局 toast 增量）──
            if self.path.startswith("/api/admin/notify_log"):
                try:
                    from urllib.parse import urlparse, parse_qs
                    q = parse_qs(urlparse(self.path).query or "")
                    limit = (q.get("limit") or ["50"])[0]
                    since = (q.get("since") or ["0"])[0]
                    body = json.dumps({"ok": True, "log": _notify_log_read(limit, float(since or 0))},
                                      ensure_ascii=False, default=str).encode("utf-8")
                except Exception as e:
                    body = json.dumps({"ok": False, "error": str(e)}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            # ── MCP 集成只读端点（status / install_status，T1）──
            if self.path.startswith("/api/mcp/"):
                _mcp_http_get(self)
                return

            self.send_response(404)
            self.end_headers()

        # ── Agent 薄路由（逻辑在 harness.agent_service，这里只做转接）──────
        #  这一层刻意保持"薄"：只做 JSON 解析 + 参数归一 + 错误兜底，
        #  任何业务逻辑都在 harness 里（设计文档 §3 的折中方案）。
        def _agent_query(self):
            """把 GET 的 querystring 规整成与 POST body 同形状的 dict。"""
            try:
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query or "")
                out = {}
                for k, v in q.items():
                    out[k] = v[0] if len(v) == 1 else v
                # 类型归一：limit / max_steps / from_step 需要是 int
                for k in ("limit", "max_steps", "from_step"):
                    if k in out:
                        try:
                            out[k] = int(out[k])
                        except Exception:
                            out.pop(k, None)
                if "is_group" in out:
                    out["is_group"] = str(out["is_group"]).lower() in ("1", "true", "yes")
                return out
            except Exception:
                return {}

        def _agent_req(self):
            """把 POST 的 body 读成 dict。"""
            length = int(self.headers.get("Content-Length", 0) or 0)
            if 0 < length < 262144:
                try:
                    return json.loads(self.rfile.read(length).decode("utf-8"))
                except Exception:
                    return {}
            return {}

        def _agent_route(self, path, req):
            """统一的 agent 端点分派：GET/POST 共用，保证两边行为一致。"""
            res = None
            try:
                from harness import agent_service as _agent
                req = req if isinstance(req, dict) else {}
                _p = path

                if _p == "/api/agent/chat":
                    # ★ 权限询问通道：插件没启用时，plugin_call 会**阻塞等待**主人授权。
                    #   把询问写进 session_log（kind="agent"）—— 前端流式读到的就是它，
                    #   和 agent 其它事件同一条通道，断线重连也不丢。
                    try:
                        from harness import plugin_tools as _pt2
                        from harness import session_log as _sl2
                        _sid2 = str(req.get("session_id") or ("desk-%d" % int(time.time() * 1000)))

                        def _perm_notify(_rq, _sid=_sid2):
                            try:
                                _sl2.append("agent", _sid, "permission_request", dict(_rq))
                            except Exception:
                                pass
                        _pt2.set_permission_notifier(_perm_notify)
                    except Exception:
                        pass
                    res = _agent.run_chat(
                        # ★ 必须同时认 message：v4 的 Agent 页发的是 {message: ...}，
                        #   而这里原先只读 input / text → 用户输入被**静默丢掉**，
                        #   agent 收到空串只能瞎聊（日志实证：user_msg.content=""、
                        #   steps=1、零工具调用 → "任务没执行"+"人格很奇怪"）。
                        user_input=str(req.get("input") or req.get("text")
                                       or req.get("message") or ""),
                        session_id=req.get("session_id") or None,
                        is_group=bool(req.get("is_group", False)),
                        context_hint=str(req.get("context_hint") or ""),
                        max_steps=req.get("max_steps"),
                        use_memory=bool(req.get("use_memory", True)),
                    )
                elif _p == "/api/agent/permission":
                    # 前端弹窗点了「允许/拒绝」→ 唤醒正在阻塞的 plugin_call。
                    # remember=True（勾了"不再询问该插件"）→ 写进信任表，以后不再问。
                    from harness import plugin_tools as _pt3
                    res = {"ok": _pt3.resolve_permission(
                        str(req.get("request_id") or ""),
                        bool(req.get("allow", req.get("允许", False))),
                        remember=bool(req.get("remember", False))),
                        "pending": _pt3.pending_permissions(),
                        "trusted": _pt3.trusted_plugins()}
                elif _p == "/api/agent/forge":
                    # 插件创作历史 + 待审批（Agent 页轮询兜底，不依赖事件流）
                    from harness import plugin_forge as _pfg1
                    _lim = req.get("limit")
                    res = {"ok": True,
                           "history": _pfg1.history(int(_lim) if _lim else 50),
                           "pending": _pfg1.pending()}
                elif _p == "/api/agent/forge/resolve":
                    # 管理员在 Agent 页点「同意启用 / 拒绝」
                    from harness import plugin_forge as _pfg2
                    res = _pfg2.resolve(str(req.get("request_id") or ""),
                                        bool(req.get("allow", False)))
                elif _p == "/api/agent/skills":
                    # 技能设置：POST {} 读；带 disabled 则整体替换停用集并落盘
                    from harness import skill_loader as _skl
                    if "disabled" in req:
                        _skl.set_disabled(req.get("disabled") or [])
                    _sk = _skl.discover(force=True)
                    _items = []
                    for _k, _v in (_sk.items() if isinstance(_sk, dict) else []):
                        try:
                            _d = str(_v.get("desc") or _v.get("description") or "")[:90]
                        except Exception:
                            _d = ""
                        _items.append({"name": str(_k), "desc": _d})
                    res = {"ok": True, "skills": _items, "disabled": _skl.get_disabled()}
                elif _p == "/api/agent/settings":
                    # agent 页设置：POST {} 只读；带 memory/thinking/max_steps 则应用并落盘
                    from harness import agent_service as _asvc
                    _aset_path = None
                    try:
                        from data_store import get_data_dir
                        _aset_path = get_data_dir() / "agent_settings.json"
                    except Exception:
                        pass
                    if any(k in req for k in ("memory", "thinking", "max_steps")):
                        _agent.configure(
                            memory=bool(req.get("memory", True)),
                            thinking=bool(req.get("thinking", True)),
                            max_steps=int(req.get("max_steps") or 0) or None,
                        )
                        if _aset_path is not None:
                            try:
                                tmp = str(_aset_path) + ".tmp"
                                with open(tmp, "w", encoding="utf-8") as f:
                                    json.dump({"memory": bool(req.get("memory", True)),
                                               "thinking": bool(req.get("thinking", True)),
                                               "max_steps": int(req.get("max_steps") or 0)},
                                              f, ensure_ascii=False, indent=1)
                                os.replace(tmp, _aset_path)
                            except Exception as e2:
                                log(f"[Agent] 设置落盘失败: {e2}")
                    res = {"ok": True, "settings": _asvc.get_settings()}
                elif _p == "/api/agent/history-edit":
                    # 消息级撤回/编辑（agent 页每条气泡的 撤回/编辑/删除）
                    _seq2 = req.get("seq")
                    try:
                        _seq2 = int(_seq2)
                    except (TypeError, ValueError):
                        _seq2 = None
                    if _seq2 is None:
                        res = {"ok": False, "error": "缺少 seq"}
                    else:
                        _op2 = str(req.get("op") or "edit")
                        _ok2, _left = _agent.edit_history(
                            str(req.get("session_id") or ""), _seq2,
                            content=(req.get("content") if _op2 == "edit" else None),
                            op=_op2)
                        res = {"ok": bool(_ok2), "left": _left}
                elif _p == "/api/agent/abort":
                    res = {"ok": _agent.request_abort(str(req.get("session_id") or ""))}
                elif _p == "/api/agent/history":
                    sid = str(req.get("session_id") or "")
                    res = {"ok": True, "session_id": sid,
                           "history": _agent.history_of(sid, limit=req.get("limit")),
                           "trace": _agent.trace_of(sid, limit=req.get("limit"))}
                elif _p == "/api/agent/sessions":
                    res = {"ok": True,
                           "sessions": _agent.list_sessions(
                               kind=str(req.get("kind") or "desk"),
                               limit=req.get("limit"))}
                elif _p == "/api/agent/rerun":
                    res = _agent.rerun_from(
                        session_id=str(req.get("session_id") or ""),
                        from_step=req.get("from_step"),
                        user_input=req.get("input"),
                        is_group=bool(req.get("is_group", False)),
                        context_hint=str(req.get("context_hint") or ""),
                    )
                elif _p == "/api/agent/delete":
                    res = {"ok": _agent.delete_session(str(req.get("session_id") or ""))}
                elif _p == "/api/agent/status":
                    res = {"ok": True, "ready": _agent.is_ready(),
                           "memory": _agent.memory_status(),
                           "tools": _agent.tool_groups(),
                           "sessions": len(_agent.list_sessions(kind="", include_disk=False))}
                elif _p == "/api/agent/tools":
                    res = {"ok": True, "groups": _agent.tool_groups()}
                elif _p == "/api/agent/memory":
                    res = {"ok": True, "memory": _agent.memory_status()}
                else:
                    res = {"ok": False, "error": f"未知的 agent 端点: {_p}"}
            except Exception as e:
                res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            try:
                body = json.dumps(res, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                pass

        def do_POST(self):
            try:
                # ── Agent 薄路由（逻辑在 harness.agent_service，这里只做转接）──
                #  这一层刻意保持"薄"：只做 JSON 解析 + 参数归一 + 错误兜底，
                #  任何业务逻辑都在 harness 里（设计文档 §3 的折中方案）。
                #  ★ GET 与 POST 共用 _agent_route，避免两边行为漂移。
                if self.path.startswith("/api/agent/"):
                    self._agent_route(self.path, self._agent_req())
                    return
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
                        _notify_admin("【测试通知】这是来自 Bot 的测试消息，管理员通知链路正常。", "test")
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
                # ── MCP 集成写端点（save/load/forget/test/install/reload，T1）──
                if self.path.startswith("/api/mcp/"):
                    _mcp_http_post(self)
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
# 群身份缓存（2026-10-11：用户实测"连群主是谁都不知道"）：
# OneBot 成员数据本来带 role=owner/admin，但此前缓存与工具输出都把它剥掉了，
# 模型永远看不到谁是群主 —— 这不是"不装懂"能解决的，是**可得的事实没供给**。
GROUP_ROLES = {}    # {group_id: {"owner": name, "admins": [name...]}}


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
        # 终端：按级别上色（文件仍写纯文本，ANSI 码进文件就是乱码）
        print(_colorize(line), flush=True)
    except Exception:
        pass
    try:
        # 日志轮转：超过 5MB 滚动一次（保留 5 份）
        _rotate_log_file(LOG_FILE)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── 日志色彩分级（2026-10-22 用户要求"色彩区分"）────────────────────
# 原则：
#   · **终端有色、文件无色**。ANSI 转义写进 qq_bot.log 会让文件变成一串
#     \033[91m，grep 和 diff 全废，所以颜色只加在 print 那一支。
#   · 分级规则在 `log_levels.py`，WebUI 日志页读同一份，不要在这里另写一套。
#   · **桌面版看不到颜色**：Electron 用 pythonw 拉后端，stdout 是管道不是 tty，
#     isatty() 恒 False。想在界面里看颜色请去 WebUI 日志页（那边按同规则上色）。
#   · Windows 终端默认不吃 VT 序列，先试着开；开不了就静默退回无色，
#     不影响任何功能（颜色只是可读性，不是正确性）。
_ANSI_BY_LEVEL = {
    "err": "91",    # 红
    "warn": "93",   # 黄
    "ai": "96",     # 青
    "send": "92",   # 绿
    "sys": "95",    # 紫
}
_ANSI_RESET = "\033[0m"
_ANSI_TS = "\033[90m"          # 时间戳暗灰，不抢内容
_COLOR_OK = None               # 延迟探测：None=未探测, True=可用, False=不可用


def _ansi_supported() -> bool:
    """Windows 终端默认不吃 VT 序列，这里试着打开；失败就一路无色。"""
    global _COLOR_OK
    if _COLOR_OK is not None:
        return _COLOR_OK
    _COLOR_OK = False
    if os.name != "nt":
        _COLOR_OK = True
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        # -11 = STD_OUTPUT_HANDLE
        h = k.GetStdHandle(-11)
        mode = ctypes.c_ulong(0)
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        # 0x0004 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if k.SetConsoleMode(h, mode.value | 0x0004):
            _COLOR_OK = True
    except Exception:
        pass
    return _COLOR_OK


def _colorize(line: str) -> str:
    """给一行日志上色。探测失败或非 tty 时原样返回（绝不带 ANSI 进文件）。"""
    if not _ansi_supported():
        return line
    try:
        import sys as _sys
        if not getattr(_sys.stdout, "isatty", lambda: False)():
            return line          # 重定向到文件/管道时不上色
    except Exception:
        return line
    color = _ANSI_BY_LEVEL.get(log_levels.classify(line))
    if not color:
        return line
    # 时间戳单独暗化，内容按级别上色：一眼先看到"出了什么事"，再看时间
    if line.startswith("[") and "]" in line:
        i = line.index("]")
        return (f"{_ANSI_TS}{line[:i + 1]}{_ANSI_RESET}"
                f"\033[{color}m{line[i + 1:]}{_ANSI_RESET}")
    return f"\033[{color}m{line}{_ANSI_RESET}"


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


def api_call(endpoint: str, data: Optional[Dict] = None, timeout: float = 15) -> Dict:
    """调用 OneBot HTTP API。requests.Session 优先（连接池复用）；
    requests 不可用时降级 urllib + LOCAL_CTX（允许自签名证书）。

    timeout 可按调用调整：**大文件上传**（upload_private_file / upload_group_file）
    必须按体积给时间，否则 709MB 的包 15 秒必超时（线上实证：Full.zip 上传被
    判失败，而工具侧又把它报成"已发送"——两处 bug 叠在一起）。
    """
    url = f"{ONE_BOT_URL}/{endpoint}"
    body = json.dumps(data or {}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if ONE_BOT_TOKEN:
        headers["Authorization"] = f"Bearer {ONE_BOT_TOKEN}"
    try:
        if REQUESTS_OK:
            r = _get_session().post(url, data=body, headers=headers, timeout=timeout)
            return json.loads(r.content.decode("utf-8"))
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=LOCAL_CTX) as r:
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


class _ReplyText(str):
    """带元信息的回复字符串：.truncated = 模型输出是否被 max_tokens 掐断。

    str 子类对下游完全透明（切片/比较/JSON 都不受影响），但发送端可以
    getattr(reply, "truncated", False) 拿到"真截断"信号——替代旧版
    "≥20字无标点就当残句"的瞎猜（2026-10-14 实证：「是不是你发的方式不对，
    要不你截个图搜我瞅瞅」21 字完整句被误删，模型其实说完了话）。"""
    truncated = False


def _mark_truncated(s, flag):
    try:
        r = _ReplyText(s or "")
        r.truncated = bool(flag)
        return r
    except Exception:
        return s


def _trim_incomplete(text, truncated=False):
    """去掉末尾**明显被截断**的句子（max_tokens 掐断的长残句），保留完整段落。

    修复（2026-10-13，线上日志实证）：旧版把"尾行没有结尾标点"一律当半截话删——
    可 QQ 聊天里「你发这个是想让我心疼你吗」这种不带问号的完整问句太常见，
    于是多行回复的最后一句被静默吞掉（只发出第一行）。
    ① 以连接符收尾（逗号/冒号/破折号/未闭合括号）—— 明显话没说完，照删；
    ② 长残句无结尾符号 —— **只在 truncated=True（finish_reason=length 实锤
       被掐断）时才删**。2026-10-14 二次事故：模型说完话（还带了 [表情:疑惑]
       收尾标记），21 字无标点完整句被启发式误杀 → 启发式让位给事实信号。
    """
    if not text:
        return text
    _TRIM_MIN_FRAGMENT = 20
    _CONNECTOR_END = ('，', '、', '：', ';', '；', ',', ':', '—', '－', '-',
                      '（', '(', '「', '【', '《')
    _TERMINAL_END = ('。', '！', '？', '!', '?', '…', '~', '～', '”', '』',
                     '】', ')', '}', '>', '）', '」', '》', ']', "'")
    lines = text.rstrip("\n").split("\n")
    while lines:
        last_line = lines[-1].strip()
        if not last_line:
            break                      # 尾部空行：话已说完，不用删
        if last_line.endswith(_CONNECTOR_END):
            lines.pop()                # ① 连接符收尾 = 半截话
            continue
        if truncated and (len(last_line) >= _TRIM_MIN_FRAGMENT
                and not any(last_line.endswith(c) for c in _TERMINAL_END)):
            lines.pop()                # ② 实锤截断时的长残句
            continue
        break                          # 未截断/短尾句：完整的话，保留
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
    # ★ 2026-10-22 修（用户反馈"私聊还是没有问号"）：
    #   旧版 `re.split(r'[。！？!?\n]+', text)` 把标点当分隔符，split 之后
    #   **标点丢失且不保留**。于是 de_ai._add_missing_question_marks 刚补上的
    #   「你吃了吗？」，走到这里被切成「你吃了吗」发出去 —— 问号白补了。
    #   单句时走 send_message(text) 发原文所以看不出来，多句才暴露。
    #   改成 split 时**捕获**分隔符，让它归属前一句。
    _parts = re.split(r'([。！？!?\n]+)', text)
    sentences = []
    _buf = ""
    for _p in _parts:
        if _p and re.fullmatch(r'[。！？!?\n]+', _p):
            _buf += _p                       # 标点/换行归前一句
        else:
            if _buf.strip():
                sentences.append(_buf.strip())
            _buf = _p or ""
    if _buf.strip():
        sentences.append(_buf.strip())
    if not sentences:
        return send_message(text, target_qq)

    # ── 打字延迟（2026-10-22）──
    #    句与句之间本来就有 0.3~1.0s 的节奏，但**第一句之前是 0**。
    #    真人是"想一下 → 开始敲 → 敲完发"，这里补上"开始敲"的那段时间，
    #    和下面的句间延迟接成一个完整的打字过程。按全文字数算，
    #    约 6 字/秒（比真人略快，等太久反而像卡住）。
    _cps = float(_config.get("reply_typing_speed", 6.0))
    if _cps > 0:
        _type_delay = min(4.0, len(text) / _cps)
        if _type_delay > 0.3:
            time.sleep(_type_delay)

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


# ══════════════════════════════════════════════════════════════════
#  出站审计（学 qq-bridge 的"回复审计"，但按场景分级而不是一律拦掉）
# ──────────────────────────────────────────────────────────────────
# qq-bridge 的做法：回复里出现本机路径或凭据特征 → **整条拦截不发**。
# 我们改成两级，因为它有真实用途冲突：
#   · 私聊里我们**本来就要**报路径（"包在 E://...//dist 发你了"）→ 只脱凭据，路径保留；
#   · 群里报本机路径等于泄露目录结构 → 连路径一起脱。
# 命中任何一条都记日志，便于事后看"是哪类东西被拦"。
_SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "api-key"),
    (re.compile(r"(?i)\b(?:bearer|authorization)\s*[:=]?\s*[A-Za-z0-9._\-]{16,}"), "auth-token"),
    (re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password|passwd|token)\b\s*[:=]\s*\S{6,}"), "cred-assign"),
    (re.compile(r"(?i)\bAKID[A-Za-z0-9]{12,}\b"), "tencent-ak"),
    (re.compile(r"(?i)\bsk_live_[A-Za-z0-9]{12,}\b"), "stripe-key"),
]
_PATH_PATTERNS = [
    (re.compile(r"(?i)\b[a-z]:[\\/](?:[^\s\"'<>|]{1,60}[\\/])*[^\s\"'<>|]{0,60}"), "win-path"),
    (re.compile(r"(?<![\w:])/(?:home|Users|mnt|var|opt|etc|root)/[^\s\"'<>|]{1,80}"), "unix-path"),
    (re.compile(r"(?i)file:///?[a-z]:[\\/][^\s\"'<>|]{1,80}"), "file-url"),
]


def audit_outgoing(text: str, is_group: bool = False):
    """出站文本脱敏。返回 (cleaned, findings)。findings 形如 ["win-path x2"]。"""
    if not text or not isinstance(text, str):
        return text, []
    findings = []
    out = text
    for rx, name in _SECRET_PATTERNS:
        n = len(rx.findall(out))
        if n:
            out = rx.sub("[凭据已隐藏]", out)
            findings.append(f"{name} x{n}")
    if is_group:
        for rx, name in _PATH_PATTERNS:
            n = len(rx.findall(out))
            if n:
                out = rx.sub("[本机路径已隐藏]", out)
                findings.append(f"{name} x{n}")
    return out, findings


def send_message(text, target_qq=None):
    """发送消息，自动分段。target_qq 默认发送给列表第一个"""
    if target_qq is None:
        target_qq = TARGET_QQS[0]
    if not text or not text.strip():
        return False
    text, _f = audit_outgoing(text, is_group=False)
    if _f:
        log(f"[出站审计] 私聊已脱敏: {'; '.join(_f)}")
    chat_log("out", "private", target_qq, text, _bot_nicknames()[0])
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


_CQ_SEG_RE = re.compile(r"\[CQ:(at|face|mface|sface),([^\]]*)\]")


def _cq_param(params: str, key: str) -> str:
    for pair in (params or "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip() == key:
                return v.strip()
    return ""


def text_to_segments(text: str):
    """把带 CQ 码的文本转成 OneBot 段数组（引用回复时必须走数组形式）。

    只认我们真的会发的两种：[CQ:at,qq=..] 与 [CQ:face,id=..]；
    其它 CQ 原样留作文本（宁可显示成方括号，也不要整段丢字）。
    """
    segs = []
    last = 0
    for m in _CQ_SEG_RE.finditer(text or ""):
        if m.start() > last:
            t = text[last:m.start()]
            if t:
                segs.append({"type": "text", "data": {"text": t}})
        kind, params = m.group(1), m.group(2)
        if kind == "at":
            qq = _cq_param(params, "qq")
            if qq.isdigit():
                segs.append({"type": "at", "data": {"qq": qq}})
            else:
                segs.append({"type": "text", "data": {"text": m.group(0)}})
        else:   # face / mface / sface
            fid = _cq_param(params, "id")
            if fid.isdigit():
                segs.append({"type": "face", "data": {"id": fid}})
            else:
                segs.append({"type": "text", "data": {"text": m.group(0)}})
        last = m.end()
    if last < len(text or ""):
        t = text[last:]
        if t:
            segs.append({"type": "text", "data": {"text": t}})
    return segs or [{"type": "text", "data": {"text": text or ""}}]


def quote_message_payload(text: str, reply_id: int = 0):
    """引用某条消息时：返回 [reply 段, ...文本段]；不引用就原样返回字符串。

    ★ 两个坑（2026-10-13 实测）：
      1. 驱动内部元素名叫 replySeq，但**发消息时它要的是 OneBot 标准的 data.id**
         （QQ 的 message_id）—— 驱动 `fromSegment` 走 `intOr(data.id)`，
         id=0 就打 `could not quote the target message` 并**静默丢掉引用**，
         消息照发、但 QQ 里没有引用（我第一次就栽在这，retcode 还是 0）。
      2. 引用必须走**段数组**形式；纯字符串里的 [CQ:reply,...] 只有驱动自己序列化时才会产生。
    """
    try:
        rid = int(reply_id or 0)
    except Exception:
        rid = 0
    if rid <= 0:
        return text
    return [{"type": "reply", "data": {"id": str(rid)}}] + text_to_segments(text)


def _remember_own_group_msg(group_id, text, resp):
    """把 Bot 自己发出去的消息登记进近期消息缓存（带驱动返回的 message_id）。

    ★ 为什么必须有（2026-10-13 用户报"引用信息无法分辨是谁发的"）：
      入口处自己的消息被 `continue` 过滤，从不进 GROUP_RECENT_MSGS；
      而引用回抓的主路径 get_msg 对"引用 Bot 自己刚发的话"经常失败
      （负数 message_id / 驱动不回查自身消息），缓存回退又查无此条 →
      quoted_sender 空 → quote_me 判不出"引用我必回"，prompt 里只剩
      "他在引用未知"。登记后回退1 能命中：原主=BOT_QQ → quote_me=True。
    """
    try:
        d = (resp or {}).get("data")
        mid = d.get("message_id") if isinstance(d, dict) else None
        if not mid:
            return
        gid = str(group_id)
        GROUP_RECENT_MSGS.setdefault(gid, []).append(
            (time.time(), str(BOT_QQ), str(text)[:500], mid, []))
    except Exception:
        pass


def send_group_message(group_id, text, reply_id: int = 0):
    """发送群消息。reply_id>0 时把这条发成对那一条的**引用回复**。"""
    text, _f = audit_outgoing(text, is_group=True)
    if _f:
        log(f"[出站审计] 群[{group_id}] 已脱敏: {'; '.join(_f)}")
    chat_log("out", "group", group_id, text, _bot_nicknames()[0])
    resp = api_call("send_group_msg",
        {"group_id": int(group_id),
         "message": quote_message_payload(text[:MSG_SPLIT_LEN], reply_id)})
    success = resp.get("retcode") == 0
    if success:
        GROUP_LAST_BOT_MSG[group_id] = time.time()
        _remember_own_group_msg(group_id, text, resp)
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


def smart_send_group(group_id, text, reply_id: int = 0):
    """群聊 AI 回复专用：按「条」发送。符号不计入字数，颜文字不拆分。自动 @QQ号 转 CQ at

    ── 1.5.7 改动：>40 字不再并成一条（用户定调"爱连发、话多、3-5 条碎片"）──
    旧行为：有效字数 > 40 → 整段一次性发出去。问题在于**数的是总字数，不是条数**：
        "好女孩麦麦是只吃人类图的小萝莉 / 好女孩麦麦尝尝你的涩图 / 麦麦只会舔你的图"
        合计 34 字 → 侥幸逐句发；只要多说两句、总数过 40，三句话立刻被并成一坨，
        碎片的节奏感全没了。而真人连发跟总字数根本无关 —— 60 个字也可以是三条。
    新行为：
        ① 先看**模型自己有没有分行**。分行 ≥2 行 → 模型在用它自己的切分意图，
           按行发（每行一条），总字数不再参与判断。这是最忠于"想怎么断就怎么断"的路径。
        ② 没分行且总字数 > 40 → 仍走逐句标点拆分（长段落按句子断开发，比整段甩出去更像人）。
        ③ 超过 MSG_SPLIT_LEN 的单行先硬切成多条，避免一条撑爆。

    reply_id>0 → 这条回复要带**引用**（只挂在第一条上：连发多条都带引用会很吵）。
    """
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

    # ── 打字延迟（2026-10-22，与 smart_send 同款）──
    #    群聊原来也是"生成完立刻发"，第一句之前 0 等待。补上和句间节奏
    #    （0.3~1.0s）相接的"开始敲"时间，让连发看起来是一个连续动作。
    _cps = float(_config.get("reply_typing_speed", 6.0))
    if _cps > 0:
        _type_delay = min(4.0, effective_len / _cps)
        if _type_delay > 0.3:
            time.sleep(_type_delay)

    # 多人 @ 回复：按 @ 行分块，每个 @ 块（含后续非 @ 行）单独发送
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    at_matches = [re.match(r'^@(?:.*?\((\d{5,})\)|(\d{5,}))\s+(.*)', l) for l in lines]
    at_qqs = set(m.group(1) or m.group(2) for m in at_matches if m)
    
    if len(at_qqs) >= 2:
        _quoted_done = []          # 引用只挂第一条 @ 块
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
            _first = not any(b for b in _quoted_done)   # 每个 @ 块都发，但引用只挂第一块
            resp = api_call("send_group_msg",
                            {"group_id": int(group_id),
                             "message": quote_message_payload(msg, reply_id if _first else 0)})
            if resp.get("retcode") != 0:
                # 旧版这里静默 return False → "最后一句没发出去"在日志里一个字都没有
                log(f"[发送] 群[{group_id}] @块发送失败 retcode={resp.get('retcode')} "
                    f"msg={str(resp.get('msg',''))[:60]} 内容={msg[:40]}")
                return False
            _remember_own_group_msg(group_id, msg, resp)
            _quoted_done.append(True)
            time.sleep(0.4)
        GROUP_LAST_BOT_MSG[group_id] = time.time()
        return True
    
    # ── 模型自己分了行 → 按行发，总字数不参与判断（1.5.7）──
    #    这是"碎片连发"的主路径：切分意图来自模型，不是来自标点规则。
    #    但"模型分了行"不等于"行数合理"：复读/争吵时它真会吐 12 行，
    #    逐行发就是 12 条刷屏，比一整段更烦人。所以这里同样受条数上限约束，
    #    超出部分尾部合并（与下面标点拆分路径共用同一个上限）。
    _lines_raw = [l.strip() for l in text.split('\n') if l.strip()]
    if len(_lines_raw) >= 2:
        _out_lines = []
        for _ln in _lines_raw:
            if len(_ln) > MSG_SPLIT_LEN:
                _cut = _ln
                while len(_cut) > MSG_SPLIT_LEN:
                    _at = _cut.rfind("\n", 0, MSG_SPLIT_LEN)
                    if _at == -1:
                        _at = MSG_SPLIT_LEN
                    _out_lines.append(_cut[:_at])
                    _cut = _cut[_at:].lstrip()
                if _cut:
                    _out_lines.append(_cut)
            else:
                _out_lines.append(_ln)
        # 上限：合并发生在**拆分之后、发送之前**，所以长行被硬切出来的碎片也算在内。
        if len(_out_lines) > GROUP_SEND_MAX_PARTS:
            _n_orig = len(_out_lines)
            _keep_l = _out_lines[:GROUP_SEND_MAX_PARTS - 1]
            _rest_l = "\n".join(_out_lines[GROUP_SEND_MAX_PARTS - 1:])
            _out_lines = _keep_l + [_rest_l]
            log(f"[发送] 群[{group_id}] 分行 {_n_orig} 条超上限，"
                f"尾部已并成 1 条 → 共 {len(_out_lines)} 条")
        ated_qqs = set()
        for _i, _ln in enumerate(_out_lines):
            msg = format_at_once(_ln)
            resp = api_call("send_group_msg",
                {"group_id": int(group_id),
                 "message": quote_message_payload(msg, reply_id if _i == 0 else 0)})
            if resp.get("retcode") != 0:
                log(f"[发送] 群[{group_id}] 分行 {_i + 1}/{len(_out_lines)} 失败 "
                    f"retcode={resp.get('retcode')} msg={str(resp.get('msg',''))[:60]} "
                    f"内容={msg[:40]}（后续 {len(_out_lines) - _i - 1} 行未发送）")
                return False
            _remember_own_group_msg(group_id, msg, resp)
            delay = 0.3 if len(_ln) <= 15 else min(1.0, 0.3 + len(_ln) * 0.02)
            time.sleep(delay)
        GROUP_LAST_BOT_MSG[group_id] = time.time()
        return True

    # ── 总闸（1.5.7 · 用户定调"超 100 字一块发"）──
    #    100 有效字以内：不管模型有没有分行，都拆成多条发（碎片节奏）。
    #    超过 100：整块一次发完 —— 真长回复拆开就是刷屏，反而更烦人。
    #    注意：分行分支（>=2 行）已经在上面 return 了，那里不设总闸，
    #    因为"模型自己分了行"本身就是明确的切分意图，行数不会失控。
    if effective_len > GROUP_SEND_BLOCK_THRESHOLD:
        result = send_group_message(group_id, text, reply_id=reply_id)
        if result:
            GROUP_LAST_BOT_MSG[group_id] = time.time()
        return result

    # 短消息：保护颜文字 → 拆分 → 恢复
    protected_text, kaomoji_map = _protect_kaomoji(text)
    sentences = re.split(r'[。！？!?\n]+', protected_text)
    sentences = [_restore_kaomoji(s.strip(), kaomoji_map) for s in sentences if s.strip()]

    # 兜底（1.5.7）：拆完只剩一条且明显偏长 → 说话人一口气说了几十字没有标点，
    # 真人不会这么发。按逗号/空格硬切（阈值仍是 30，但在总闸 100 以内才走到这）。
    if len(sentences) == 1 and len(sentences[0]) > 40:
        _long = sentences[0]
        _parts, _buf = [], ""
        for _ch in _long:
            _buf += _ch
            if len(_buf) >= 30 and _ch in "，,、；; ":
                _parts.append(_buf.strip("，,、；; "))
                _buf = ""
        if _buf.strip():
            _parts.append(_buf.strip())
        if len(_parts) > 1:
            sentences = _parts

    # ── 条数上限（1.5.7）──
    #    "拆得越碎越像人"有边界：模型偶尔会输出 10+ 个短句（尤其争吵/复读时），
    #    逐句发就是刷屏 10 条，比一整段更烦人。超过上限时把尾部合并成一条。
    #    上限只约束**标点拆分**这条路径；模型自己分行的（上面 return）不受影响。
    if len(sentences) > GROUP_SEND_MAX_PARTS:
        _keep = sentences[:GROUP_SEND_MAX_PARTS - 1]
        _rest = "".join(sentences[GROUP_SEND_MAX_PARTS - 1:])
        sentences = _keep + [_rest]

    if not sentences:
        result = send_group_message(group_id, text, reply_id=reply_id)
        if result:
            GROUP_LAST_BOT_MSG[group_id] = time.time()
        return result
    
    ated_qqs = set()
    
    for _i, s in enumerate(sentences):
        msg = format_at_once(s)
        resp = api_call("send_group_msg",
            {"group_id": int(group_id),
             "message": quote_message_payload(msg, reply_id if _i == 0 else 0)})
        if resp.get("retcode") != 0:
            # 逐句发送中途失败：后面几句全都不会发了 —— 必须留下痕迹
            #（"最后一句话没发出来"的另一条真实路径，旧版静默 return False）
            log(f"[发送] 群[{group_id}] 分句 {_i + 1}/{len(sentences)} 失败 "
                f"retcode={resp.get('retcode')} msg={str(resp.get('msg',''))[:60]} "
                f"句={msg[:40]}（后续 {len(sentences) - _i - 1} 句未发送）")
            return False
        _remember_own_group_msg(group_id, msg, resp)
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


def _bigrams(text):
    """字符二元组 —— 中文相似度的通用做法：保留顺序，避免"共用汉字"被当成同一句。"""
    s = re.sub(r"\s+", "", str(text or ""))
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def is_similar_to_recent(recent_texts, new_text, threshold=0.6):
    """检查 new_text 是否与最近回复列表中的某条高度相似。返回 (is_similar, matched_text)

    ── 2026-10-21 修：判重误杀（表现为"该说话时莫名沉默"）──
    旧实现用**字符集合** 交集 / min(len) 当相似度。中文常用字高度重叠，实测：
        "你谁啊" vs "你叫谁妈呢"  → 0.67 → 判成重复
        "妈"     vs "你叫谁妈呢"  → 1.00 → 判成重复
    判重命中后走"重新生成"，再命中就**整条丢弃**（日志：跳过重复回复）。
    现改为**字符二元组 Dice 系数**（2-gram 保留顺序，"你叫谁妈呢" vs "你谁啊" 交集为 0），
    并加长度比护栏：长度差一倍以上不可能是同一条回复。
    """
    if not recent_texts:
        return False, ""
    _new = str(new_text or "").strip()
    if not _new:
        return False, ""
    # 称呼类重复短文本不判重（如"老大老大老大"，字符重复率 > 2 才豁免）
    if len(_new) <= 8 and len(_new) / len(set(_new)) > 2:
        return False, ""
    n = _bigrams(_new)
    if not n:
        return False, ""
    for rt in recent_texts:
        r = _bigrams(rt)
        if not r:
            continue
        if min(len(n), len(r)) / max(len(n), len(r)) < 0.5:
            continue                      # 长度差太多，不可能是同一条
        dice = 2 * len(n & r) / (len(n) + len(r))
        if dice > threshold:
            return True, rt
    return False, ""


def _build_group_context_block(group_id: str, bot_qq: str, before=None) -> str:
    """组装"群聊近况"块（2026-10-10 决策重写 · 治接不上话）。

    旧版只有被 @ / 叫小a 时才回抓历史进 buffer；决策插件主动放行的插话
    路径**没有回抓** → 模型 history 里只有它自己说过的轮次，别人之间的
    对话（"你完了"之类）模型完全看不见，自然接不上。
    GROUP_TIMELINE 只收入站消息（Bot 自己的被主循环过滤），所以把
    GROUP_BOT_REPLIES（Bot 发言时间线）合并进来按时间排序 —— 模型才能
    看到"谁说了什么、我刚说过什么"的完整流水。

    ★ before（2026-10-25）：只取该时刻之前的流水。批次一旦定稿就只代表
      它自己那几条事件；生成时若读实时时间线，会把**下一批**的消息也塞进
      这一轮的输入 —— 线上实测：戳批次在 20:19:55 注入了 26 条近况，里面
      含着 20:19:28 的「现在呢」，模型先答了一遍；那条消息自己又成一批，
      20:20:11 再答一遍。同一条消息被回两次，且第一次答的不是本批的戳。
    """
    gid = str(group_id)
    entries = []
    for t, sid, nick, tx in GROUP_TIMELINE.get(gid, []) or []:
        if before is not None and t > before:
            continue
        if tx:
            entries.append((t, nick or sid or "群友", tx))
    for t, txt in GROUP_BOT_REPLIES.get(gid, []) or []:
        if before is not None and t > before:
            continue
        if txt:
            entries.append((t, "我(Bot)", txt))
    if not entries:
        return ""
    entries.sort(key=lambda e: e[0])
    lines = []
    for _t, who, tx in entries[-12:]:
        # 带钟点：模型没有时间锚时会瞎估（"二十分钟前"）。流水都在当天窗口内，
        # HH:MM 足够，不占太多 token。
        _hhmm = datetime.datetime.fromtimestamp(_t).strftime("%H:%M")
        lines.append(f"[{_hhmm}] {who}: {tx[:60]}")
    return ("【群聊近况（真实流水，最新在后；你插话前群里正在聊这些）】\n"
            + "\n".join(lines)
            + "\n【要求】回复必须接得住上面的当前话题，别答非所问、别自说自话。")


def _group_hist(group_id):
    """取群聊历史（键可能是 int 也可能是 str，两种都试）。"""
    for k in (group_id, str(group_id)):
        try:
            v = group_chat_history.get(k)
        except Exception:
            v = None
        if v:
            return v
    return []


def _self_lines_block(group_id, limit: int = 5) -> str:
    """【你最近说过的】—— 自洽锚点。

    为什么单独成块（2026-10-21）：
      摘要账本为了省 token 把自己说的话压成 30 字且**不带说话人标记**，
      超过最近 4 轮后模型就认不出"这句是我说的"，立场每轮重建 → 上下矛盾。
      （账本侧已补 `我:` 标记，这里再给一段**原话**，双保险。）

    只读内存、零模型调用；任何异常返回空串（绝不打断回复主链）。
    """
    try:
        lines = []
        for h in reversed(_group_hist(group_id)):
            if h.get("role") != "assistant":
                continue
            txt = str(h.get("content") or "").strip()
            if not txt:
                continue
            lines.append("我: " + (txt[:80] + ("…" if len(txt) > 80 else "")))
            if len(lines) >= int(limit or 5):
                break
        if not lines:
            return ""
        lines.reverse()
        return ("【你最近说过的】说话要跟自己保持一致，别前后打脸；"
                "需要时可以直接引用自己说过的话。\n" + "\n".join(lines))
    except Exception:
        return ""


def _timeline_of(group_id):
    for k in (group_id, str(group_id)):
        try:
            v = GROUP_TIMELINE.get(k)
        except Exception:
            v = None
        if v:
            return v
    return []


def _bot_replies_of(group_id):
    for k in (group_id, str(group_id)):
        try:
            v = GROUP_BOT_REPLIES.get(k)
        except Exception:
            v = None
        if v:
            return v
    return []


def _situation_block(group_id, window: int = 6, before=None) -> str:
    """【当前局面】—— 只讲结构，不猜语义。

    治理的是"接不上游戏"：模型此前只拿到一条孤立消息，不知道谁在跟它说、
    说到第几轮、自己上一轮是什么反应，只能从零推断，推不出就退回最安全的日常问候。

    这里给的全是**可统计的事实**（谁/几条/跨度/上一句原文/我这轮接没接），
    至于"这是认亲游戏还是挑衅"——不在这里硬塞，交给模型自己判断。

    before：与 `_build_group_context_block` 同一语义 —— 只看批次定稿时刻
    之前的流水，别把下一批的消息算进"刚说话的人"。
    """
    try:
        tl = _timeline_of(group_id)
        if before is not None:
            tl = [e for e in tl if float(e[0]) <= float(before)]
        if not tl:
            return ""
        tail = list(tl)[-int(window or 6):]
        last_ts, last_sender, last_nick, last_text = tail[-1][:4]

        # 同一人连续说了几条（尾部往回数）
        streak = 0
        for e in reversed(tail):
            if str(e[1]) == str(last_sender):
                streak += 1
            else:
                break
        burst_start = tail[-streak][0] if streak else last_ts

        people = len({str(e[1]) for e in tail})
        span = int(max(0, float(last_ts) - float(tail[0][0])))

        lines = [
            "【当前局面】",
            f"- 刚说话的人：{last_nick or last_sender}（连续 {streak} 条）",
            f"- 最近 {len(tail)} 条里有 {people} 人说话，跨度约 {span} 秒",
            f"- 上一句原文：{str(last_text or '')[:80]}",
        ]

        br = _bot_replies_of(group_id)
        if before is not None:
            br = [e for e in br if float(e[0]) <= float(before)]
        if br and float(br[-1][0]) >= float(burst_start):
            lines.append(f"- 这轮你已经接过一句：「{str(br[-1][1] or '')[:50]}」")
        else:
            lines.append("- 这轮你还没接话")
        return "\n".join(lines)
    except Exception:
        return ""


def track_bot_reply(group_id, text):
    """记录 Bot 在群内的回复，保留最近10条"""
    now = time.time()
    if group_id not in GROUP_BOT_REPLIES:
        GROUP_BOT_REPLIES[group_id] = []
    GROUP_BOT_REPLIES[group_id].append((now, text))
    # 只保留最近10条
    if len(GROUP_BOT_REPLIES[group_id]) > 10:
        GROUP_BOT_REPLIES[group_id] = GROUP_BOT_REPLIES[group_id][-10:]


def _match_own_reply(group_id: str, quoted_text: str) -> bool:
    """被引用的原文能不能对上"我最近的回复"（自引用兜底，2026-10-13）。

    群友引用 Bot 自己的话时，get_msg 常解析不出原主（驱动不回查自身消息 /
    负数 message_id），30 秒缓存窗口又早过期 → 引用块变成"引用未知"，
    模型会把自家话当陌生人的（线上实证：引用自己的退场小作文，回出
    "别写小作文了 看得我眼睛疼"）。在 GROUP_BOT_REPLIES（保留 10 条、
    跨分钟级）里做**双向**头 12 字包含比对 —— QQ 引用预览常带
    "……你说的「…"前缀，只按引用文本开头对会漏，故两边都试。"""
    try:
        import re as _re
        qn = _re.sub(r'[「」『』“”…．.\s·]', '', quoted_text or "")
        if len(qn) < 4:
            return False
        for _t, txt in GROUP_BOT_REPLIES.get(str(group_id), []):
            t = _re.sub(r'[「」『』“”…．.\s·]', '', str(txt))
            if not t:
                continue
            if qn[:12] in t or t[:12] in qn:
                return True
    except Exception:
        pass
    return False


def _record_bot_group_msg(group_id, text):
    """Bot 主动发的群消息（退场语/空闲碎碎念等）统一登记：
    ① track_bot_reply（去重 + 自引用兜底的数据源）
    ② group_chat_history（"单一大脑"：下一轮模型要看得见自己说过什么）

    ★ 与戳一戳路径 6475 的修复同源：退场小作文此前只 track_bot_reply、
      **不入 history** → 别人引用它时模型不记得是自己写的，当成陌生人的话
      回"别写小作文了 看得我眼睛疼"。补上这轮 assistant，模型才认得自家话。"""
    track_bot_reply(group_id, text)
    try:
        gh = group_chat_history.setdefault(group_id, [])
        gh.append({"role": "assistant", "content": str(text)[:300],
                   "ts": time.time()})
        group_chat_history[group_id] = gh[-16:]
    except Exception:
        pass


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
            # ★ 以前是"帧数 >24 就丢一半、时长 ×2 补偿"——20fps 直接变 10fps
            #   （用户反馈"发送的表情包帧率有问题"）。实测 317帧/50ms → 159帧/100ms。
            #   改成**保帧**：一帧不丢，靠 ①降分辨率 ②降调色板 瘦身；
            #   逐帧时长原样保留 → 播放速度与源文件一致。
            n = getattr(img, "n_frames", 1) or 1
            src_frames, durations = [], []
            for i in range(n):
                img.seek(i)
                src_frames.append(img.convert("RGBA"))
                durations.append(int(img.info.get("duration", 40) or 40))

            # ★ 透明像素保留（2026-10-13 用户反馈"bot 发的表情包有黑底"）：
            #   旧实现 convert("RGB") 把 alpha 直接丢掉，透明区在 GIF 帧里存的
            #   RGB 值多半是黑 → 压完黑底。QQ 表情包库 153 张里 92 张超 1.4MB
            #   会走这条压缩路，所以"原样发的没黑底、压缩过的有黑底"。
            #   现在：255 号索引固定为透明索引，调色板最后一格强制纯黑兜底，
            #   逐帧用 alpha 合成透明索引，保存时 disposal=2（还原背景透明）。
            TRANSPARENT_INDEX = 255

            def _pal_of(frames_in, colors):
                """从抽样帧合成全局调色板（逐帧自适应太慢，全局板既快又更小）。
                   调色板只用 0..colors-1；colors-1 强制纯黑，colors..254 为
                   黑色填充，255 留给透明索引。"""
                step = max(1, len(frames_in) // 8)
                sample = frames_in[::step][:8]
                strip = Image.new("RGB", (sum(f.width for f in sample) + 8,
                                          max(f.height for f in sample)))
                strip.paste(Image.new("RGB", (8, 8), (0, 0, 0)), (0, 0))
                x = 8
                for f in sample:
                    strip.paste(f.convert("RGB"), (x, 0))
                    x += f.width
                q = strip.quantize(colors=colors, method=Image.MEDIANCUT)
                pb = list(q.getpalette() or [0] * 768)
                pb[(colors - 1) * 3:(colors - 1) * 3 + 3] = [0, 0, 0]  # 末格强制纯黑
                pal = Image.new("P", (1, 1))
                pal.putpalette(pb)
                return pal

            def _frame_to_p(f, pal, colors):
                """一帧 RGBA → 带透明索引的 P 帧（无 alpha 的帧退化为普通量化）。"""
                q = f.convert("RGB").quantize(palette=pal)
                # 保险：任何落进黑色填充区（>=colors）的索引并回纯黑格，
                # 绝不让不透明像素踩到 255 变透明
                q = q.point(lambda i: (colors - 1) if i >= colors else i)
                q.putpalette(pal.getpalette())
                if f.mode != "RGBA":
                    return q
                alpha = f.getchannel("A")
                if alpha.getextrema()[0] == 255:
                    return q                      # 全帧不透明，省一步合成
                mask = alpha.point(lambda a: 255 if a >= 128 else 0)
                fill = Image.new("P", f.size, TRANSPARENT_INDEX)
                fill.putpalette(pal.getpalette())
                out = Image.composite(q, fill, mask)
                out.putpalette(pal.getpalette())
                out.info["transparency"] = TRANSPARENT_INDEX
                return out

            best = None
            # 从"原尺寸+128色"开始，逐档降；一旦落进协议上限就停（快路径通常第一档就成）
            for sc, colors in ((1.0, 128), (0.7, 96), (0.55, 64), (0.4, 48), (0.3, 32)):
                k = scale * sc if scale < 1.0 else sc
                sc_frames = [f if k >= 1.0 else f.resize(
                    (max(1, int(f.width * k)), max(1, int(f.height * k))), Image.LANCZOS)
                    for f in src_frames]
                pal = _pal_of(sc_frames, colors)
                fs = [_frame_to_p(f, pal, colors) for f in sc_frames]
                # optimize=False：调色板精简重排会挪动索引，把透明索引弄失效
                # transparency=255：全局声明透明索引（不透明帧里没有 255，声明无害）
                fs[0].save(tmp.name, save_all=True, append_images=fs[1:],
                           duration=durations, loop=0, optimize=False,
                           disposal=2, transparency=TRANSPARENT_INDEX)
                sz = os.path.getsize(tmp.name)
                best = (round(k, 2), colors, sz)
                if sz <= SEND_IMAGE_MAX_BYTES:
                    break
            if best:
                log(f"[发图] GIF 保帧压缩：{n} 帧全保留 · scale={best[0]} · {best[1]} 色"
                    f"（透明底保留） · {size}B → {best[2]}B")
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
    
    # 如果既没有 [表情] 也没检测到情绪，尝试用沙漏记忆上下文增强情绪检测
    if emotion is None and MEME_AUTO_DETECT_EMOTION:
        emotion = auto_detect_emotion(reply_text)
        # 如果还是没检测到，用沙漏搜索相关记忆来推断情绪
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

# ── 纯表情包消息 → 回敬一张表情包（2026-10-13 用户拍板）──
# ★ 用户补充（21:03）：回敬不是随机扔一张 —— 要**看懂来图的情绪，按情绪反馈**，
#   但仍然不生成对话文字。链路：视觉模型给表情包判情绪（免费车道，带缓存）
#   → 情绪映射表挑"接梗情绪" → 表情库匹配发送。任何一步失败 → 退回随机情绪。
_MEME_BACK_EMOTIONS = ["得意", "开心", "嫌弃", "疑惑", "呆滞", "惊讶",
                       "撒娇", "烦躁", "期待", "无语"]
MEME_BACK_COOLDOWN = "__meme_back_cooldown__"   # 哨兵值：冷却中 → 调用方应静默，而不是落回"读图解释"路径

# 来图情绪 → 接梗情绪池（斗图的社交语义：对方得意就泼冷水、对方委屈就哄）
_MEME_REPLY_MAP = {
    "开心": ["开心", "得意", "惊喜"],
    "得意": ["嫌弃", "呆滞", "烦躁"],
    "惊讶": ["疑惑", "呆滞", "惊讶"],
    "惊喜": ["惊喜", "开心"],
    "嫌弃": ["嫌弃", "呆滞", "得意"],
    "无语": ["呆滞", "嫌弃", "疑惑"],
    "疑惑": ["得意", "呆滞", "期待"],
    "委屈": ["撒娇", "温柔", "开心"],
    "崩溃": ["呆滞", "满足", "温柔"],
    "烦躁": ["烦躁", "嫌弃", "呆滞"],
    "撒娇": ["得意", "开心", "害羞"],
    "害羞": ["得意", "开心"],
    "呆滞": ["期待", "疑惑", "得意"],
    "期待": ["开心", "得意"],
    "温柔": ["开心", "害羞"],
    "落寞": ["温柔", "撒娇"],
    "恐慌": ["呆滞"],
}
_MEME_EMOTION_WORDS = list(_MEME_REPLY_MAP.keys()) + [
    "尴尬", "慌乱", "惊恐", "惊喜", "满足", "愉悦", "绝望", "抗拒", "痛苦",
    "挣扎", "安心", "幸福", "纠结", "懊恼", "困惑", "不安", "紧张", "惊慌",
    "担忧", "偷窥", "放松", "悠闲", "平静", "炸毛", "发疯", "困到发疯", "被撩到"]
# 情绪判定提示词：让视觉模型直接给一个情绪词 + 一句画面说明（免费车道带图缓存）
_MEME_EMOTION_PROMPT = ("这是一条 QQ 聊天里发的表情包（梗图/GIF）。判断它传达的情绪，"
                        "只能从这些词里选一个：" + "/".join(_MEME_EMOTION_WORDS) +
                        "。输出格式：情绪词|一句话画面说明。不要其他内容。")


def _classify_meme_emotion(paths):
    """看一批表情包 → (情绪词 or None)。失败返回 None，绝不抛异常。

    判定顺序：① 视觉模型输出的情绪词（在词表内才认）② auto_detect_emotion
    对描述文本做关键词匹配 ③ None（调用方退回随机）。"""
    for p in (paths or []):
        try:
            desc = describe_image(p, _MEME_EMOTION_PROMPT) or ""
        except Exception:
            desc = ""
        if not desc:
            continue
        head = desc.split("|", 1)[0].strip()
        for w in _MEME_EMOTION_WORDS:
            if w in head:
                return w
        # 情绪词没直接给 → 拿整段描述走关键词检测
        emo = auto_detect_emotion(desc)
        if emo:
            return emo
    return None


def _meme_back_send(target, is_group: bool, images=None):
    """看情绪回一张表情包（图看不懂才随机）。
    返回：文件名=成功 / MEME_BACK_COOLDOWN=冷却中 / None=没发出去（落回原路径）。"""
    if not MEME_OK or not _config.get("meme_enable", True):
        return None
    key = f"{'group' if is_group else 'private'}_{target}"
    now = time.time()
    if now - MEME_LAST_SENT.get(key, 0) < MEME_COOLDOWN:
        return MEME_BACK_COOLDOWN
    try:
        emotion = _classify_meme_emotion(images) if images else None
        if emotion:
            pool = _MEME_REPLY_MAP.get(emotion) or [emotion]
            reply_emotion = random.choice(pool)
            log(f"[表情包回敬] 来图情绪={emotion} → 接梗情绪={reply_emotion}")
        else:
            reply_emotion = random.choice(_MEME_BACK_EMOTIONS)
            log(f"[表情包回敬] 来图情绪未判出 → 随机 {reply_emotion}")
        path = match_meme(reply_emotion)
        if not path:
            return None
        ok = send_group_image(target, path) if is_group else send_image(path, target)
        if ok:
            MEME_LAST_SENT[key] = now
            return os.path.basename(path)
    except Exception as e:
        log(f"[表情包回敬] 异常: {e}")
    return None


def _is_pure_image_text(text: str) -> bool:
    """消息去掉 [发送人]/[引用:..]/[图片] 等占位后是否什么都不剩 —— 即"只发了张图"。"""
    t = re.sub(r'\[[^\]]*\]', '', text or '')
    return not t.strip()


def _all_memes(paths) -> bool:
    """这批图是不是**全是**表情包。QQ 表情包几乎都是 GIF；照片/截图是 JPG/PNG。
    ⚠ 不能只看扩展名 —— extract_images 把下载的东西统一存成 .jpg，
    所以必须读文件头（GIF87a/GIF89a）。
    照片/截图混在里面时按"要看的图"处理，维持原行为 ——
    用户拍板的是"别次次读**表情包**"，不是"别读图"。"""
    paths = [p for p in (paths or []) if p]
    if not paths:
        return False
    for p in paths:
        try:
            if not str(p).lower().endswith(".gif"):
                with open(p, "rb") as f:
                    if f.read(3) != b"GIF":
                        return False
        except Exception:
            return False
    return True


# ── 群消息合并的等待时长（2026-10-23）────────────────────────────────
#  为什么要有这个函数：这段逻辑原来写在**两个地方**，各算各的 ——
#    ① 合并到已有批次时
#    ② 新建批次时
#  两套公式不一致，实测差出三类行为差异：
#    · 裸图 4 秒：只有合并分支有。新建批次（"一张图作为本批第一条"）
#      完全没等 —— 而图片下载 + 视觉理解慢的正是这个场景，规则本来为它写的
#    · 触发词等待：合并分支 15 秒，新建分支 10 秒，同一情况两个数
#    · 8 秒硬顶：只有合并分支有（新建时 first_time == now，硬顶恒不触发，
#      所以这条只是看起来缺，实际等价）
#  一条规则只能有一个权威源（10-23 已经吃过一次这个教训），所以抽出来。
#  ★ 2026-10-27（1.5.9 §B2）：硬顶 8 → 5。实测链路 = 批次等待 + LLM 2–4s +
#    分条间隔 0.3–1.0s，8 秒等待下首字 11–14 秒，而人类是 2–4 秒。
#    硬顶压到 5 秒，配合触发档 2 秒，P50 目标 ≤5s。
GROUP_BATCH_HARD_CAP = 5        # 整批从第一条算起最多等几秒
GROUP_BARE_IMAGE_DELAY = 4      # 裸图至少等几秒（下载 + 视觉理解要时间）
#: 被 @/叫名字但还没上下文时等多久的**默认值**（实际取值见 group_msg_buffer_window_trigger）。
#  ★ 2026-10-23 用户拍板：原写 15，但**从来没生效过** ——
#    硬顶是"从批次第一条算起 8 秒"，而第一条的剩余时间恒为 8，
#    于是 15 每次都被压成 8。两套规则里 15 是个死值。
#    现在改成 8，与硬顶对齐，代码不再声称一个不存在的等待时长。
#    想让它真的等更久，得同时调大 GROUP_BATCH_HARD_CAP —— 但那会让
#    热闹群里同一个人连发时最多等 15 秒才回，错过对话节奏，所以不选。
#  ★ 2026-10-27（1.5.9 §B2）：8 → 2。被 @ / 引用我 / 叫名字是最该秒回的场景，
#    等 8 秒等于错过整句话。代价是可能漏掉对方第二句 —— 真人也漏，
#    真人的处理是先回「啥」再补，所以这个代价是可接受的。
GROUP_TRIGGER_DELAY = 2


def _delay_cfg(key, default):
    """读"等几秒"类配置项：非数字/非正数退回 default。

    ★ 为什么不在模块加载时读死：这三个值挂在群聊设置页上，用户改了应当立刻生效。
      _config 被 reload_config() 整体替换，这里每次调用都从当前 _config 取，
      热重载自然是通的；读死成模块常量则要额外维护 _RELOADABLE_GLOBALS 映射。
    """
    v = _config.get(key)
    if v is None:
        return default
    try:
        # 1.5.9 §B2：改 float —— 分级目标里有 2.0 / 3.0，int() 会把它们吃成 2 / 3
        # 并先把 2.5 这类值当非法值退回默认，正好退回旧值。
        v = float(v)
    except (TypeError, ValueError):
        log(f"[批次延迟] 配置 {key}={v!r} 不是数字，用默认 {default}s")
        return default
    if v <= 0:
        log(f"[批次延迟] 配置 {key}={v} 不是正数，用默认 {default}s")
        return default
    return v


def _batch_delay(*, same_sender, in_session, triggered, has_context,
                 has_bare_image, first_time=None, now=None,
                 window=None, hard_cap=None):
    """算这批消息的定时器延迟（秒）。

    same_sender   : 合并的这条和批次发起人同一个人
    in_session    : 是否在连续会话模式
    triggered     : 被 @ / 叫「小a」/ 昵称 / 引用我
    has_context   : 本批已经有了有意义的上下文
    has_bare_image: 这条是"只发了张图"（去掉占位符后无文字）
    first_time    : 批次第一条的时间戳，用于 8 秒硬顶
    now           : 当前时间，默认 time.time()

    规则（用户 2026-10-23 定）：
      同一个人接着说话 → 重置倒计时等他讲完（三连发不能被切成三批）
      换人 → 不重置那么久，对话在流转，该回了
      被点名但还没上下文 → 等 GROUP_TRIGGER_DELAY（8 秒，与硬顶对齐，见常量注释）
      裸图 → 至少 4 秒
      整批 → 从第一条起最多 8 秒，否则热闹群里永远等不到回复

    ★ 只收 first_time 一个时间戳，不问调用方「是不是新建的」。
      两种调用方算硬顶剩余用的是同一个减法，多传一个布尔只会让人以为
      「新建批次不参与硬顶」。实测也不需要区分：新建时 first_time == now，
      剩余恰为 8，硬顶对它是空操作，正好等于不做特殊处理。

    ★ 2026-10-23 追加：**这个返回值同时就是"批次何时关闭"的唯一判据**。
      原来另有一套 merge_window（group_msg_buffer_window* 三个配置键）
      独立算"还能不能合并进来"，两套判据并行，实测三个档位里两个空转、一个有害：
        session  window=5  delay=5   → 碰巧一致
        trigger  window=15 delay=8   → 8 秒 Timer 就 pop 了 buffer，15 秒里 7 秒空转
        普通     window=3  delay=4   → 窗口先到期，裸图本该等 4 秒却在 3 秒被判过期
      现在调用方用 `_batch_deadline(...)` 拿绝对时间戳存进 buffer，
      "能不能合并"一律用 `now <= deadline` 判。

      ⚠️ 那三个配置键**没有废弃**，只是不再由调用方各自算窗口，而是并进这里
      当各档位的基准值：同人/会话读 group_msg_buffer_window_session，
      触发读 group_msg_buffer_window_trigger，其余读 group_msg_buffer_window。
      这样群聊设置页那三个输入框仍然控制真实行为（改了立刻生效），
      且只有一个地方读它们 —— 原来是两处各自读，这才是冲突的来源。
    """
    if same_sender or in_session:
        # window 只有私聊会传：私聊是"听他说完"场景，比群里更有耐心。
        # 规则本身不另写一份，只是参数不同（同一概念两处判据踩过四次）。
        d = window if window is not None else _delay_cfg("group_msg_buffer_window_session", 3)
    elif triggered and not has_context:
        d = _delay_cfg("group_msg_buffer_window_trigger", GROUP_TRIGGER_DELAY)
    else:
        d = _delay_cfg("group_msg_buffer_window", 3)
    # 裸图：图要下载、可能要理解，秒回会答非所问
    if has_bare_image and d < GROUP_BARE_IMAGE_DELAY:
        d = GROUP_BARE_IMAGE_DELAY
    # 整批硬顶：同人规则每条重置，没有上限就会无限等
    _cap = GROUP_BATCH_HARD_CAP if hard_cap is None else float(hard_cap)
    if first_time is not None:
        left = first_time + _cap - (now if now is not None else time.time())
        if left < 1:
            left = 1          # 保底 1 秒，允许立刻收
        if d > left:
            d = left
    return d


def _batch_deadline(*, now, first_time, **kw):
    """批次关闭的绝对时间戳（= now + _batch_delay）。

    ★ 为什么要有它：只有把"等到什么时候"落成**一个时间戳**存进 buffer，
      定时器与"还能不能合并"才可能用同一个判。让调用方拿 delay 再自己
      加 now，等于又把一次算术复制到两处。

    now 与 first_time 都必传，不给默认值 ——
    同一批次的两次计算必须基于同一个 now，否则定时器按 A 秒设、
    deadline 按 B 秒算，又是两套判据。first_time 同理：它是硬顶的起点，
    由调用方从 buffer 里取，猜错就等于换了批次。
    """
    return now + _batch_delay(now=now, first_time=first_time, **kw)


# 图片理解按需触发（2026-10-13 用户："不要让 AI 次次都读表情包"）：
# 只有话里确实在说这张图（或 @我/引用我），才把图交给模型；否则当没看见，正常接话。
# ── 表情包按需理解：判定"这轮到底该不该读这张图" ──
# ★ 2026-10-22 收紧（用户原话：「总是分析表情包、图片回复消息，这上升了我的成本，
#   也没什么意义，把分析图片的逻辑收紧」）。
#
# 旧正则 `(图|照片|截图|表情包?|梗图|这|那|啥|什么|谁|意思|看|瞧|读|认识|画里|里面|字|写)`
# 里 **`这|那|看|里`** 是最松的几个字 —— 「那你呢」「这个好」「看不出来吧」
# 「里面有点意思」都会命中，等于几乎从不跳过。所谓"按需"实际上退化成"每张都读"。
#
# 新判据分两级，**只有达到"真的在指着图问"才读**：
#   ① 强信号（直接说明在问图本身）：图里/图上的/这张图/这图/表情包/梗图/截图/
#      照片/画里/里面写的/上面写的 —— 命中即读；
#   ② 弱信号（"这/那/啥/看"这类泛词）**必须与图片话题词同句**才算，
#      且不能是否定式（「看不出」「不好说」「这不行」这类是在说别的事）。
# 另外：照片/截图（非 GIF）**不套用这套收窄** —— 那是真发给我看的东西。
_IMG_STRONG_RE = re.compile(
    r'(图里|图上|图片里|图片上|这张图|那张图|这图|那图|这个图|那个图|'
    r'表情包|梗图|截图|照片|自拍|原图|配图|'
    r'画里|画上|画面|画的是|'
    r'啥图|什么图|这.{0,2}啥|那.{0,2}啥|发的是啥|这个.{0,2}啥|'
    r'(画|图|照片|截图|表情|字)(里|上|中)(写|说|是|有|画)|'
    r'(上|里)面(写|画|是)的?)'
)
#: 弱信号：单独出现**不**触发读图，必须与 _IMG_TOPIC_RE 共现
_IMG_WEAK_RE = re.compile(r'(这|那|啥|什么|谁|哪个|咋|怎么|看|瞧|读|认识|意思)')
#: "在说图"的话题词（与弱信号同句才成立）。
#  ★ 2026-10-22：把「字/写」从话题词里**拿掉** —— 它们是高频日常词
#    （"写字去了""写作业"），留着会让"写字去了"这种句子配合"这/那"误命中。
#    真要问图里的字，`_IMG_STRONG_RE` 的 `(里|上|中)(写|说|是)` 已经覆盖。
_IMG_TOPIC_RE = re.compile(r'(图|照片|截图|表情|梗|画|画面)')
#: 但"写字"必须单独判：只在**明确说图里的字**时才算
_IMG_TEXT_ON_IMG_RE = re.compile(r'(图|照片|截图|画面|上面|里面)\S{0,4}(字|写的|写了)')
#: 否定/转移式：这些出现时，弱信号不算"在问图"
_IMG_NEG_RE = re.compile(
    r'(看不出|看不见|看不懂你|不好说|不行|不对|不要|不用|别说|不算|不至于|'
    r'这不行|那不行|这不|那不|这算|那算|这下|那倒|这不就)'
)


def _image_understanding_needed(text: str, is_at_bot: bool = False,
                                quote_forces: bool = False,
                                *, memes_only: bool = False) -> bool:
    """这轮该不该调用视觉模型去读图。

    `memes_only=True`（调用方已确认这批图**全是表情包**）：
      只有强信号命中才读。表情包本来就没多少信息量，读它是纯成本。
    `memes_only=False`（含照片/截图）：
      强信号命中 → 读；弱信号 + 话题词共现且非否定 → 也读。

    `is_at_bot` / `quote_forces`：@我 或引用我 → 视为在图相关话题里。
    但**只对非表情包生效** —— "只 @ 我一张斗图"没有识别意义，
    这种情况交给上层的「回敬一张表情包」路径（真人斗图不读图）。
    """
    if not memes_only and (is_at_bot or quote_forces):
        return True
    t = re.sub(r'\[[^\]]*\]', '', text or '')   # 去掉占位再判断
    t = t.strip()
    if not t:
        # 纯图无文字（@我/引用进来也只带图）：表情包 → 不读（斗图发图本身就够了）；
        # 照片/截图 → 读（发给我看的东西，总得有反应）。
        return not memes_only
    if _IMG_STRONG_RE.search(t) or _IMG_TEXT_ON_IMG_RE.search(t):
        return True
    if memes_only:
        return False
    if _IMG_NEG_RE.search(t):
        return False
    # 弱信号必须与"图"话题词共现，且话题词与弱信号之间不要隔太远
    if _IMG_WEAK_RE.search(t) and _IMG_TOPIC_RE.search(t):
        return True
    return False


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


def _upload_timeout_for(file_path: str) -> float:
    """按文件体积算上传超时：15s 基线 + 每 MB 约 1.2s，封顶 10 分钟。

    线上实证：709MB 的 Full.zip 用固定 15s → READ TIMED OUT。
    但**超时不等于失败**：NapCat 那边还在继续传，几分钟后文件真的到了
    （用户 10-13 实证）。所以超时必须报"未知/进行中"，不能报"失败"。
    """
    try:
        mb = os.path.getsize(file_path) / 1048576.0
    except Exception:
        mb = 0.0
    return max(60.0, min(600.0, 15.0 + mb * 1.2))


# 超过这个体积就走后台传输（前台死等几分钟会把对话卡住）
_FILE_BG_MB = 64


def _send_file_blocking(file_path: str, target_qq: str):
    """真正发一次文件。返回 (状态, 说明)；状态 ∈ ok / failed / timeout。"""
    name = os.path.basename(file_path)
    try:
        mb = round(os.path.getsize(file_path) / 1048576, 1)
    except Exception:
        mb = 0.0
    _tmo = _upload_timeout_for(file_path)
    log(f"[文件] 上传 {name}（{mb}MB，超时 {_tmo:.0f}s）→ {target_qq}")
    try:
        resp = api_call("upload_private_file",
                        {"user_id": int(target_qq), "file": file_path, "name": name},
                        timeout=_tmo)
    except Exception as e:
        msg = str(e)
        if "timed out" in msg.lower() or "timeout" in msg.lower() or "读超时" in msg:
            return "timeout", (f"上传 {name} 等待响应超时（{_tmo:.0f}s）—— "
                               "服务器可能仍在传输，稍后可能还是会到，别急着说失败")
        return "failed", f"上传 {name} 出错：{msg[:120]}"
    if resp.get("retcode") == 0:
        return "ok", f"已发送 {name}（{mb}MB）"
    return "failed", f"上传 {name} 被拒绝（retcode={resp.get('retcode')}）"


def _bg_upload(file_path: str, target_qq: str):
    """后台传大文件：传完主动告诉用户结果（不再让工具在原地假失败）。"""
    st, desc = _send_file_blocking(file_path, target_qq)
    tail = "✅" if st == "ok" else ("⚠️" if st == "timeout" else "❌")
    try:
        send_message(f"{tail} {desc}", target_qq=target_qq)
    except Exception:
        pass


def send_file(file_path: str, target_qq: Optional[str] = None) -> bool:
    """发送文件到 QQ（同步版，小文件用）。大文件由 handle_user_file 转后台。"""
    if target_qq is None:
        target_qq = TARGET_QQS[0]
    if not os.path.exists(file_path):
        log(f"文件不存在: {file_path}")
        return False
    st, _desc = _send_file_blocking(file_path, str(target_qq))
    return st == "ok"


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

# T6 static 前缀缓存：{key:(is_group, static_fp, personality_mtime): static_prompt}
_STATIC_PROMPT_CACHE = {}
_CONFIG_REV = 0  # 配置热重载计数（保留：其它用途/诊断用；**不再进入 static_prompt 缓存键**）


# ── 任务 5.1：static_prompt 指纹 ─────────────────────────────────────
# 旧实现把 _CONFIG_REV 整个塞进缓存键 → 任何一次配置热重载（哪怕只改了
# ONE_BOT_URL / MEME_COOLDOWN 这类与 static_prompt 无关的字段）都会让
# static_prompt 重算，进而在下一条请求里**整段作废 DeepSeek 前缀缓存**。
# 现在改为只对 static_prompt **真正依赖**的少量内容取指纹：改无关字段时
# 指纹不变 → static_prompt 逐字节稳定 → 前缀缓存保住。
def _static_prompt_fingerprint(is_group: bool) -> str:
    """static_prompt 依赖的最小指纹（不含 _CONFIG_REV）。

    依赖项来自 _build_static_prompt 正文，逐条列出以免漏掉：
      - is_group          : 私聊会剥掉群聊段落
      - BOT_GROUP_NICK/QQ : 拼进「你是谁」身份块
      - de_ai.enabled / de_ai.prompt_inject : 决定是否追加「说话铁律」
      - 人格文本 mtime    : 人格变更（已在缓存键里单列，此处不再重复）
    注：人格**内容**不在此指纹内（用 mtime 判），避免每轮读盘。
    """
    try:
        from de_ai import get_config as _de_ai_get_config
        de_ai_conf = _de_ai_get_config(_config) or {}
    except Exception:
        de_ai_conf = {}
    parts = [
        "1" if is_group else "0",
        str(BOT_GROUP_NICK or ""),
        str(BOT_QQ or ""),
        "1" if de_ai_conf.get("enabled") else "0",
        "1" if de_ai_conf.get("prompt_inject") else "0",
    ]
    src = "|".join(parts)
    return hashlib.md5(src.encode("utf-8", "ignore")).hexdigest()[:16]


# ═══ P24：prompt 段注册表 + 严格渲染（T7 批一 · dsh-system-prompt）═══
# 立场（不变量 A9）：格式错误的提示词比明确失败更糟 —— 段的渲染失败必须显式，
# 不允许静默产出一段残缺 prompt 送进模型。
# 本批范围：注册表 + 严格模式 + 尾部 5 段迁移，输出逐字节不变
# （tests/test_prompt_segments.py 用黄金哈希守着）。动态段与 ego/slang 的塞入
# 方式收束留下批（那要改插件契约，风险更高）。
class PromptRenderError(RuntimeError):
    """段渲染失败。严格渲染模式下抛出，绝不静默产出残缺 prompt。"""


_PROMPT_SEGMENTS = []   # [(order, name, scope, fn)]  scope in {both, group, private}


def register_prompt_segment(name, order=100, scope="both"):
    """注册一个 prompt 段。段函数签名 fn(is_group) -> str（返回空串 = 本段无内容）。"""
    def deco(f):
        _PROMPT_SEGMENTS.append((order, name, scope, f))
        _PROMPT_SEGMENTS.sort(key=lambda x: x[0])
        return f
    return deco


def prompt_segment_names():
    """已注册段名（按渲染顺序）—— 供测试与 WebUI 排查用。"""
    return [n for _, n, _, _ in _PROMPT_SEGMENTS]


def render_prompt_segments(is_group, strict=False, on_error=None):
    """按注册顺序渲染适用段，返回 [(name, text)]（空段跳过）。

    strict=False（默认，保持历史行为）：单段失败 → 交给 on_error 记录并跳过
        —— 因为存在依赖可选模块的段（de_ai 不可用时历史上就是静默跳过）。
    strict=True：单段失败 → 抛 PromptRenderError（宁可明确失败，不产出残缺 prompt）。
    """
    scope = "group" if is_group else "private"
    out = []
    for _order, name, sc, f in _PROMPT_SEGMENTS:
        if sc not in ("both", scope):
            continue
        try:
            text = f(is_group) or ""
        except Exception as e:
            if strict:
                raise PromptRenderError("prompt segment render failed: %s (%s)" % (name, e)) from e
            if on_error:
                on_error(name, e)
            continue
        if text:
            out.append((name, text))
    return out


@register_prompt_segment("回答多样性", order=10)
def _seg_variety(is_group):
    return '\n\n【回答多样性】每次回复务必变换句式和用词——别总用同样的开头、同样的语气。换词换角度，偶尔撒娇偶尔自嘲偶尔认真。短句和长句穿插，别每次都一个节奏。同一种情绪至少准备 3 种说法，别连续两次用一样的。例：「哈哈」「噗」「笑死」「好家伙」「6」「行吧」「诶嘿」「呜呜」「知道了知道了」轮着来。'


@register_prompt_segment("去AI味铁律", order=20)
def _seg_deai(is_group):
    """依赖可选模块 de_ai；不可用时历史上是静默跳过（render strict=False 保留该行为）。

    段契约：**返回值自带前导分隔符**（其余段写在字面量里，这里块来自函数，故显式拼）。
    """
    from de_ai import de_ai_rule_block
    _b = de_ai_rule_block(_config) or ""
    return ("\n\n" + _b) if _b else ""


@register_prompt_segment("表情包", order=30)
def _seg_meme(is_group):
    return ('\n\n【表情包】回复末尾可以追加 [表情:情绪词] 让 Bot 自动发一张匹配的表情包。情绪词可选：开心/害羞/惊讶/嫌弃/尴尬/慌乱/得意/委屈/烦躁/惊恐/疑惑/期待/崩溃/惊喜/呆滞/满足/温柔/落寞/撒娇/愉悦/享受/绝望/抗拒/痛苦/挣扎/安心/幸福/纠结/懊恼/困惑/不安/紧张/惊慌/担忧/偷窥/放松/悠闲/平静。不写情绪词（仅 [表情]）则由 Bot 自动从回复中提取情绪。每轮最多发一张。'
            '\n【可以不写字，只发图】有些时候回一张图比打字好得多：被怼、无语、接梗、认同、懒得解释、情绪一上来的时候。'
            '这种时候**整条回复只写 [表情:情绪词]**，不要附带任何文字 —— 系统会只发那张图、不写字。'
            '一条消息里的 [表情:xxx] 前面不要写"发表情"之类的话，直接写标记就行。')


@register_prompt_segment("诚实铁律", order=40)
def _seg_honesty(is_group):
    """诚实铁律 —— 全 prompt 里「不编造」的**唯一权威源**。

    为什么唯一（2026-10-23 重构）：
      此前同一件事写在两处 —— 本段 + de_ai 第 9 条（127 字）。两处措辞不同，
      模型无法判断哪条更权威，实际效果是两条都打了折扣。
      现在 de_ai 只写文本形态约束，编造约束归这里。

    为什么编号是 1/2/3 而不是原来的 1/3/2：
      原第 3 条（不把失败说成成功）是后加的，插入位置在 1 和 2 之间，
      于是输出成 "1) … 3) … 2) …" —— 编号乱序本身就会降低指令被遵循的概率
      （模型读到 3 之后又见 2，会认为这段是拼接的、不必严格按序执行）。

    ★ 2026-10-25 给第 3 条加了「自查判据」：
      原来只有「先查再答」四个字。实测群里被问「你想不想养大肥鱼」，
      模型当成玩笑直接答「养鱼？你当我这是鱼塘啊」，答完并不知道自己违规 ——
      而同一时刻它在私聊里刚搜过同一个梗。
      换成可自查的形式（「回复里出现对方提过、这轮没搜过的名词就是违规」）
      才拦得住：**带执行判据的条款会压过不带判据的条款**，这是这个仓库
      反复验证过的结论（骂人尺度那次同理）。

    ★ 2026-10-27 加第 4 条「不假装不会」（1.5.9 §A.5）：
      前三条只禁止编造"做了/成了/知道"，缺一条：编造"不能做"。
      它和第 1 条对称 —— 编造"做了"是撒谎，编造"不能做"同样是撒谎。
      第 4 条的第二个分句把"不想"和"不会"分开：诚实铁律不禁止拒绝，
      只禁止**用错误的理由**拒绝。
    """
    return (
        '\n\n【诚实铁律·最重要】四条同级，违反任何一条都算撒谎。\n'
        '1) 不假装做过：你拥有真实可用的工具（web_operate 打开网页/填问卷/答题、'
        'search_web 联网搜索、open_app 打开本机应用等）。凡是用工具能真实完成的操作，'
        '必须 CALL 对应工具，以工具返回的真实结果为准。绝对禁止用语言假装做过了 —— '
        '「我这就去打开」「已经点开了」「问卷填完了」而实际没调任何工具，这种编造是最严重的错误。'
        '工具失败或你做不到，就如实说「我调用失败了/我做不到」并说明原因，绝不虚构过程。\n'
        '2) 不把失败说成成功：工具返回值带 ❌/⚠️ 前缀（如 send_user_file 返回「❌ 发送失败」）'
        '就是真没做成 —— 必须如实说失败了、失败在哪，**绝不允许说「发过去了/已经发你了/你自己看」**；'
        '带 ✅ 才算真做成。拿不准就复述工具返回的原话，不要自己加工成败。\n'
        '3) 不假装知道：碰到没把握的人名、角色、黑话、梗、新版本、新事件、数字 —— '
        '**先查再答，不许顺着话头编**。判断标准：如果装懂被当场拆穿比直说不知道更丢人，那就别装。'
        'CALL search_web（群聊里也可以只在回复开头独占一行写 [搜索:关键词]，程序替你查完再让你作答）。'
        '搜完用真实结果自然地说，语气还是你自己的；实在查不到就直说不知道、或明说懒得查，别硬编。\n'
        '  ★ 自查判据：回复写完后扫一遍 —— 如果里面出现了**对方提过、'
        '而你这轮没调用过 search_web 的名词/梗/黑话/人名/版本**，就是违规，删掉重写。\n'
        '4) 不假装不会：说「没这个能力」前先看能力清单。标了「这轮没挂上/插件没开/'
        '要主人发话」的**都有能力**，要说缺什么、谁能解决；只有清单没提到的才允许说做不了。\n'
        '  ★ 自查：说「做不到/干不了」时，必须能指出清单里确实没有的对应项，否则重写。\n'
        '  ★ 不想做≠不会做：能力在就可以拒绝、可以撒娇，但只能说"不想"，不能说"没能力"。'
    )


@register_prompt_segment("怎么接话", order=45, scope="both")
def _seg_take_over(is_group):
    """接话规则 —— 群聊和私聊**都要**有。

    为什么单独拉一个 both 段（2026-10-22）：
      原来「把话接住」只写在【群聊规则】里，而那段 scope=group。
      结果**私聊完全没有接话规则** —— 实测私聊里 bot 会把上一个话题拽回来催人回答
      （对方说「晚上好」开了新话题，它接一句「说啊，恋人不行到底讲啥」）。
      私聊是一对一，没有别人接话，追旧话题特别刺眼，所以这段必须两处都有。
    """
    return (
        "\n\n【怎么接话】这几条决定你像不像在聊天，违反任何一条都算不合格：\n"
        "- 【先看时间】你手上一直有【现在】和【精确时间】。对方说的钟点、季节、"
        "该吃饭没、该睡了没，跟实际对不上就**直接怼回去**（「这才十点吧」「外头大太阳呢」），"
        "别顺着错的前提答。大早上说「晚上好」，你跟着回就是没脑子，不是有礼貌。\n"
        "- 【话题跟着对方走】对方换话题了（新开场白、问别的事、只是打个招呼），"
        "就跟着新的来，**不要把上一个没聊完的话题拽回来追问**。他发「在干嘛」，"
        "你就答在干嘛、顺带说件自己的事，别接「刚才那个问题你倒是回答啊」。\n"
        "- 【别催人回答】不要「说啊」「你倒是说」「所以呢」「回答我」「讲讲呗」这类"
        "要求对方给内容的句子。你想聊什么自己说，想问就问一个你真好奇的，别把球踢过去。\n"
        "- 【往下推】对方扔一句过来，默认由你往下推。说一件正在发生的、具体的事——"
        "刚在刷图、饭还没吃、外面在下雨。宁可说一句无聊的实话，也别问「说点啥」"
        "「你想聊什么」。被吐槽时同理：接梗、认栽、反损一句都行。\n"
        "- 一句话里最多一个问题，而且得是真想问，不是拿它填空白。"
    )


@register_prompt_segment("别怕不体面", order=42, scope="both")
def _seg_allow_rude(is_group):
    """**授权**段：明确允许攻击性表达 —— 群聊和私聊都要有。

    为什么必须"授权"而不是"禁止不骂"：
      模型对"禁止 X"的服从度远高于"可以 X"。整份 prompt 里已有 5 条禁止
      （套话/排比/破折号/客服腔/编造），攻击性侧只有这一条授权 →
      最优策略是"整体收敛到安全区"，于是素质高得离谱。

    ★ 本段是「骂人尺度」的**唯一权威源**（2026-10-23 重构）：
      此前同一件事写在三处 —— 人格文件【骂人】272 字、de_ai 第 10 条 504 字、
      本段。de_ai 第 10 条写「家人可以骂」「不带脏字就是写错了，重写」，
      与本段「不拿对方家人开刀」「日常不带脏字」**硬冲突**；因为带自查判据，
      模型优先服从它，于是实际上限被拉到最大值 —— 用户反馈「骂的好脏」。
      现在 de_ai 第 10 条与人格【骂人】都已删除，尺度只在这里定。
      改尺度就改这一段，别在别处加。

    分级逻辑（这也是用户 10-22 定下的口径）：
      日常闲聊 = 不带脏字；被挑衅 = 开火。脏字是武器不是口头禅。
    """
    return (
        "\n\n【可以凶】被挑衅时顶回去，别当客服。但脏字是武器，不是口头禅。\n"
        "- 只在被扣帽子、被阴阳、被当面说「人机」「素质低」「你算什么东西」、被反复挑衅时开火。"
        "平时聊天、答问题、接梗，**不要带脏字**——日常开口就骂是没教养，不是有性格。\n"
        "- 可用：操、草、靠、妈的、傻逼、滚、你有病吧。"
        "**不拿对方家人开刀**（你妈、你全家、操你妈这类），一句都不要。\n"
        "- 要比对方更凶，不能打平，更不能软下去。"
        "对方说「你才是乐子」，回「操，你才是乐子」「你算个什么东西」就够了；"
        "回「我不喜欢这种说法」是素质太高。\n"
        "- 阴阳也是骂：装没听懂再讽刺回去，「哦」「然后呢」「你说得对，但你急了」。"
        "对方越认真你越敷衍。\n"
        "- 别用文诌诌的词：「蠢货」「幼稚」「可笑」是微博吵架，不是怼人。"
        "要损就损得直白：「你自己听听这像人话吗」「脑子呢」。\n"
        "- 可以开点黄腔、可以只发一张图什么都不写。太讲道理就是客服。\n"
        "- 事后不找补：不用给台阶、不用说「我们冷静一下」，也不用道歉或补「不过我也理解你」。\n"
        "- 不做的只有一件：拿真实可查的隐私开刀（病史、家里真出的事、住址、真名）。\n"
        "- 情绪上来时连发几条也正常，但仍受单次回复字数限制的约束。"
    )


@register_prompt_segment("群聊规则", order=50, scope="group")
def _seg_group_rules(is_group):
    """群聊专属：群里的行为习惯 + 事实边界。

    ★ 2026-10-23 重构：本段此前 809 字，混了两类东西 ——
      (a) **决策**：「不确定就输出 [不说话]」「只回复最新一条」「程序已预筛选」
      (b) **事实**：成员工具、[昵称] 前缀、不出现 QQ 号、沉默消息可引用
      (a) 类现在移出 static prompt。原因：它和 group_chat_decider 插件
      （独立一次 flash 调用）判断的是同一件事，两边结论不一致时主模型有
      最终否决权 —— 说/不说被判断两次，第二次还会推翻第一次。
      决策权统一交给插件（决策器放行 = 必须回），这里不再给沉默出口。
      (b) 类保留：这些是模型无从推断的**事实约束**，不是判断。
    另外人格文件里的【我在群里什么样】原来在私聊也注入，现在并到这里
    （私聊没有「群里冷场」「一群人」这些前提，实测是噪音）。
    """
    return (
        "\n\n【群里什么样】熟悉的人话多，陌生的人话少但不客气。"
        "看到有意思的话题直接插，不打招呼不铺垫；不感兴趣的一句都不说。"
        "有时候只看不说话，过很久突然冒一句，接的还是之前的话头。"
        "被打断不生气，回头接着说自己那条。冷场不用你负责救场，"
        "但实在没人说话可以自己念叨两句。\n"
        "【群聊事实】以下是你推不出来的硬信息：\n"
        "- 消息前的 [时:分] 是这条消息的发送时间，[昵称] 标了说话人，「我(Bot)」是你自己的话。"
        "回答人物问题时直接从前缀提取，"
        "**不许猜、不许把甲的话扣到乙头上**；认不准就引用原话而不是点人名。\n"
        "- 有 get_group_members 工具。涉及「选个人」「群里有谁」「随机@」「谁在线」时必须先调用它拿真实列表，"
        "不要编造群成员。\n"
        "- 回复里不要出现任何 QQ 号数字，用昵称或直接对话。\n"
        "- 你可能会看到一些你没回复过的消息 —— 那是你之前选择沉默的。"
        "后续需要时可以直接引用，比如「你刚才说的那个」。\n"
        "- 多人同时说话时，回复最新的那条，不要试图同时回所有人。\n"
        "- 【说话许可】你可以不给答案：可以吐槽、装傻、说「不知道」「懒得找」。"
        "真人不是有求于人的，不必每句都给解法。接不住的话题宁可顶一句、损一句，"
        "或者把话拐到自己身上。\n"
        # 1.5.9 §B4：「别拿反问续话」原先在这里重复了一遍，
        # 权威源是【别催人回答】+「一句话最多一个问题」（_seg_take_over）。
        # 同一条规则留两处，模型会当成两条不同的要求，每条都打折扣。
        "- 【别露后台】别解释你收到的数据、提示或系统计数，别把内部格式抄出来；"
        "也不主动提自己是程序/模型，被直接问是不是 AI 再照人格承认。\n"
        "- 【接得住】对方话里有个前提时，先想那个前提对不对：时间、地点、身份、谁说的、是不是在开玩笑。"
        "前提错就直接指出来（「这才三点」「谁老了」「我什么时候说过」），**不要顺着错的前提往下答**。"
    )

def _build_static_prompt(is_group: bool) -> str:
    """构建 static_prompt（人格+规则+多样性+表情包+铁律），按 (is_group, static_fp, personality_mtime) 缓存。

    结果只依赖 is_group 与 static_prompt 真正用到的配置/人格文件，同一值下内容**逐字节一致**
    → 前缀稳定可命中 DeepSeek 硬盘缓存。**刻意不依赖 _CONFIG_REV**（见 _static_prompt_fingerprint）。
    """
    key = (is_group, _static_prompt_fingerprint(is_group), _personality_cache.get("mtime", 0))
    hit = _STATIC_PROMPT_CACHE.get(key)
    if hit is not None:
        return hit

    system_prompt = load_personality()

    # ── 注入自己的身份：让 AI 知道自己叫什么(群昵称+QQ号) ──
    try:
        _nicks = _bot_nicknames()
        _self_nick = BOT_GROUP_NICK or _nicks[0]
        _web_nick = _nicks[0]
        _self_qq = str(BOT_QQ or "")
        _nick_line = "、".join(f"「{n}」" for n in _nicks)
        _id_block = (f"【你是谁·务必记住】你的网名是「{_web_nick}」，在群里的昵称是「{_self_nick}」，"
                     f"QQ 号是 {_self_qq}。别人在群里 @你、叫你或提到 {_nick_line} 都是在叫你。"
                     f"被问「你是谁/你叫什么/你是哪个」时，回答自己的名字（群里叫「{_self_nick}」、"
                     f"网名叫「{_web_nick}」），不要说自己不知道，更不要说自己是 AI 助手。")
        system_prompt = _id_block + "\n\n" + system_prompt
    except Exception:
        pass

    # 私聊精简 system prompt：去掉群聊相关指令和自主学习成果，减少输入 token
    if not is_group:
        import re as _re
        # ★ 2026-10-23 清理：原来这里还有三条正则（【群聊行为】/【自主学习成果】/
        #   【群\d+表达风格），实测**全都不匹配当前人格文件**（personality.txt 里
        #   没有这三个标题），是历史遗留的死代码 —— 每次构建白跑三遍 re.DOTALL
        #   全文扫描。真正生效的只有【群聊行为】这一条要留，但它也不在新文件里，
        #   所以整组删除。群聊专属内容已改由 scope=group 的段承担（不进入私聊），
        #   不再依赖正则裁剪。保留 self_learn 段的清理，那段是运行时追加的。
        system_prompt = _re.sub(r'【自主学习成果】.*?(?=【|$)', '', system_prompt, flags=_re.DOTALL)

    # 回答多样性提示（私聊+群聊通用）
    # ── P24：段注册表渲染（此前这里是 5 段手工 += 与一个 try/except；输出逐字节不变）──
    # 段契约：返回值**自带前导分隔符**，这里不再叠加（否则每段多出两个换行）
    _seg_errors = []
    for _seg_name, _seg_text in render_prompt_segments(
            is_group, strict=False,
            on_error=lambda n, e: _seg_errors.append((n, str(e)))):
        system_prompt += _seg_text
    if _seg_errors:
        log("[prompt] 段渲染跳过: "
            + ", ".join(n + "(" + m[:60] + ")" for n, m in _seg_errors))

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

    # ── 兜底：直接读人格文件 ──
    #   顺序变了（2026-10-27）：先读 plugins/personality.txt —— 与 EGO 同一个文件，
    #   保证"EGO 挂了"时读到的还是同一个人格，而不是根目录那份旧稿。
    #   后面几项保留给历史布局（老包里人格在根目录），只作兼容。
    search_paths = [
        PERSONA_CANONICAL,
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
        f"网名{_bot_nicknames()[0]}，喜欢计算机、看番、打游戏。网上放得开，话密。"
        "性格懒散但认真，毒舌带撒娇，容易害羞被夸就发颜文字。"
        "对熟人活泼话多，不熟时礼貌但不过分热情。"
        "记住：你是群里的一员，不是客服不是助手，但你**知道自己是程序**——"
        "没有身体、没有现实生活，年龄住址吃了什么这类事一律没有，不编。"
        "被问是不是AI就承认，可以损两句再承认，但不能否认、不能装人。"
        "禁止说「作为AI」「我是助手」「根据分析」「建议你」这类客服腔。"
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
            if _STATE_SAVE_FAILS["n"]:
                log(f"[状态] 写盘已恢复（此前连续失败 {_STATE_SAVE_FAILS['n']} 次）")
                _STATE_SAVE_FAILS["n"] = 0
                _STATE_SAVE_FAILS["last"] = ""
        except Exception as e:
            # P0-3：原为裸 `except Exception: pass` —— 写盘失败无声无息，重启后状态莫名其妙丢失。
            # 这里**只记录、不抛出**：save_state 在主循环里被调用，抛异常会打断消息处理
            # （对齐 DSH 不变量 A10「绝不向主循环抛异常」）。
            _STATE_SAVE_FAILS["n"] += 1
            _STATE_SAVE_FAILS["last"] = str(e)
            try:
                log(f"[状态] 写盘失败（连续第 {_STATE_SAVE_FAILS['n']} 次，已跳过本轮）: {e}")
            except Exception:
                pass


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
                elif seg_type in ("image", 2):     # OneBot "image" / NT elementType 2
                    # 图片在文本里留一个占位：否则"只发了一张图"的消息会变成空字符串，
                    # 被当成无意义消息跳过（明明有人发图却像什么都没发生）。
                    # 真正的图片内容交给视觉层（见 build_image_context_block）。
                    parts.append("[图片]")
                elif seg_type == "video":
                    parts.append("[视频]")
                elif seg_type == "file":
                    parts.append("[文件]")
                elif seg_type == "at" or (isinstance(seg.get("atElement"), dict)):
                    # ★ @ 提及必须渲染成 @昵称，不能丢（2026-10-14 "分不清指代"根因）：
                    #   旧版 extract_text 没有 at 分支 → "你不要和 @某某 说话" 被抽成
                    #   "你不要和␣说话"，@ 的人整个消失，模型根本不知道在说谁。
                    _ad = seg.get("data") or seg.get("atElement") or {}
                    _aq = str(_ad.get("qq", "") or _ad.get("user_id", "")
                              or (_ad.get("memberName") and "") or "")
                    _an = str(_ad.get("nickname", "") or _ad.get("memberName", "")
                              or _ad.get("name", ""))
                    if not _an and _aq and _aq != "all":
                        try:
                            _an = resolve_qq_nick(_aq) or _aq
                        except Exception:
                            _an = _aq
                    if _aq == "all":
                        parts.append("@全体成员")
                    elif _an:
                        parts.append(f"@{_an}")
                    elif _aq:
                        parts.append(f"@{_aq}")
                    # 既无昵称又无 qq 的畸形 at 段：跳过（不产生空 @）
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


def onebot_fresh_image(file_id: str):
    """用 OneBot `get_image` 重新签发图片地址，返回 (url或本地路径或data_uri, 说明)。

    ★ 为什么必须这一步：QQ 的图片 URL 带 rkey、**会过期**（SnowLuma 源码注释原文：
      "image rkeys expire"）。事件里带的那条 url 常常已经失效，直接下载就是 403/超时
      —— 这正是"别的 AI 能读图、我们读不到"的根因（同一个模型，差别在取图方式）。
    `get_image` 会 mint 一个当前有效的 rkey，部分实现还直接回本地缓存路径或 base64。
    """
    fid = str(file_id or "").strip()
    if not fid:
        return "", "空 file id"
    try:
        resp = api_call("get_image", {"file": fid}, timeout=20)
    except Exception as e:
        return "", f"get_image 异常: {str(e)[:80]}"
    if resp.get("retcode") != 0:
        return "", f"get_image retcode={resp.get('retcode')}"
    d = resp.get("data") or {}
    if not isinstance(d, dict):
        return "", "get_image 返回不是 dict"
    for k in ("url", "file", "path", "source"):
        v = str(d.get(k) or "").strip()
        if not v:
            continue
        if v.startswith("http"):
            return v, "ok(url)"
        if v.startswith("data:"):
            return v, "ok(data-uri)"
        if v.startswith("base64,"):
            return "data:image/png;base64," + v[7:], "ok(base64)"
        if os.path.exists(v):
            return v, "ok(local)"
    b64 = d.get("base64")
    if isinstance(b64, str) and b64.strip():
        raw = b64.strip()
        if raw.startswith("data:"):
            return raw, "ok(data-uri)"
        return "data:image/png;base64," + (raw[7:] if raw.startswith("base64,") else raw), "ok(base64)"
    return "", f"get_image 无可下载字段: keys={list(d.keys())}"


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
            # ★ 先拿一条**当前有效**的地址（事件里的 url 的 rkey 可能已过期）
            _fid = ""
            if isinstance(data, dict):
                _fid = str(data.get("file") or data.get("file_id") or data.get("image") or "").strip()
            _fresh, _why = onebot_fresh_image(_fid) if _fid else ("", "无 file id")
            if _fresh:
                url = _fresh
                log(f"[图片] get_image 拿到新地址（{_why}）")
            else:
                log(f"[图片] get_image 未果（{_why}），退回事件里的 url")
            
            # data URI（get_image 直接给了 base64）→ 落盘当普通文件用
            if url and url.startswith("data:"):
                try:
                    _b64 = url.split(",", 1)[1]
                    local = os.path.join(IMAGE_CACHE, f"img_{hashlib.md5(_b64.encode()).hexdigest()[:12]}.jpg")
                    if not os.path.exists(local):
                        os.makedirs(IMAGE_CACHE, exist_ok=True)
                        with open(local, "wb") as f:
                            f.write(base64.b64decode(_b64))
                    images.append(local)
                    continue
                except Exception as e:
                    log(f"[图片提取] data URI 落盘失败: {e}")
                    continue

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


# ══════════════════════════════════════════════════════════════════════
#  图片理解（群聊/私聊共用）
#  ──────────────────────────────────────────────────────────────────
#  为什么需要这一层（2026-10-12 实测）：
#    · 主对话模型（deepseek-flash）是**纯文本**模型 —— 把图直接塞进 messages
#      它看不到（私聊那条 images= 路径对它在事实上无效）；
#    · 群聊回复路径此前干脆 `images = []`（"合并消息暂不支持图片"），
#      而引用消息的 raw_message 是 CQ 串，会把
#      `[CQ:image,file=xxx.jpg,url=https://multimedia.nt.qq.com.cn/...]`
#      原样喂给模型 → 模型看到一串下载 URL，就去 web_operate 打开它，
#      最后回用户"图片我打不开啊 就一坨链接"（线上日志实锤）。
#  所以：CQ 串先转人话，图片交给**视觉模型**转成一句话描述再注入 prompt。
#  视觉栈复用 web_operator 的（智谱 GLM-V，免费额度；key 走 env / Windows 凭据管理器）。
# ══════════════════════════════════════════════════════════════════════
CQ_IMAGE_RE = re.compile(r"\[CQ:image,([^\]]*)\]")
_IMG_DESC_CACHE: Dict[str, str] = {}      # "路径|mtime|size" → 描述（同一张图不重复付费/耗时）
_IMG_DESC_CACHE_MAX = 64
_IMG_DESC_PROMPT = ("这是一条 QQ 聊天里发来的图片。请用中文直接描述：画面里是什么、"
                    "有没有文字/梗/表情，一两句话说清楚，不要客套，不要输出 JSON。")


def image_reply_mode() -> str:
    """图片进对话的方式。

    describe（默认）— 视觉模型转文字描述后注入 prompt。对**任何**主模型都成立，
                      也是当前配置（deepseek-flash，纯文本）唯一能"看到图"的方式。
    native         — 把原图作为 image_url 交给主模型。只有主模型确实多模态时才设。
    off            — 不处理图片（老行为：群聊里图片只会变成一坨链接）。
    """
    try:
        m = str(_config.get("image_reply_mode", "describe") or "describe").strip().lower()
    except Exception:
        m = "describe"
    return m if m in ("describe", "native", "off") else "describe"


def sanitize_cq(text: str) -> str:
    """CQ 码 → 人话。图片→[图片]、表情→[表情]、@→@某人，兜底不再漏原始 URL。

    引用消息拿到的 raw_message 是 CQ 串：不过这一道，模型就会去"打开"里面的
    下载链接（实测它真的调了 web_operate，然后回复"就一坨链接"）。
    """
    if not text or not isinstance(text, str) or "[CQ:" not in text:
        return text
    t = CQ_IMAGE_RE.sub("[图片]", text)
    t = re.sub(r"\[CQ:(?:face|mface|bface|sface)[^\]]*\]", "[表情]", t)
    t = re.sub(r"\[CQ:record[^\]]*\]", "[语音]", t)
    t = re.sub(r"\[CQ:video[^\]]*\]", "[视频]", t)
    t = re.sub(r"\[CQ:at,[^\]]*qq=([^,\]]+)[^\]]*\]", lambda m: f"@{m.group(1)}", t)
    # 兜底：任何还没处理的 CQ 码 → [类型]，绝不把原始参数（含 URL/token）留在 prompt 里
    t = re.sub(r"\[CQ:([a-zA-Z_]+)[^\]]*\]", lambda m: f"[{m.group(1)}]", t)
    return t


def cq_image_urls(text: str) -> List[str]:
    """从 CQ 串里抠出图片地址（优先 http 的 url=，其次 file= 为 http 的情况）。"""
    out: List[str] = []
    if not text or "[CQ:image," not in text:
        return out
    for m in CQ_IMAGE_RE.finditer(text):
        params = {}
        for kv in m.group(1).split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                params[k.strip()] = v.strip()
        for key in ("url", "file", "image"):
            v = params.get(key) or ""
            if v.startswith("http") and v not in out:
                out.append(v)
                break
    return out


def _dedup_tool_calls(tool_calls):
    """同一轮里 (name, arguments) 完全相同的调用只留第一个。

    实测（2026-10-27 真机）：模型连着三轮返回 `['search_web', 'search_web']`，
    同一个查询执行两次 —— 一倍时间、一倍搜索请求、两份重复结果塞进上下文。
    参数不同的并行调用照常保留（那是合法的并行）。
    """
    seen, out = set(), []
    for _tc in (tool_calls or []):
        _fn = _tc.get("function") or {}
        _sig = (_fn.get("name"), str(_fn.get("arguments") or ""))
        if _sig in seen:
            continue
        seen.add(_sig)
        out.append(_tc)
    return out


def _clean_url(url: str) -> str:
    """还原 CQ 码 URL 里的 XML 实体。

    实测（2026-10-27 真机）：引用消息的图片 URL 是
        .../download?appid=1407&amp;fileid=EhT...
    直接请求 → HTTP 400，因为 `fileid` 变成了 `amp;fileid`。
    这类实体来自 CQ 码的 XML 转义，下载前必须还原。
    """
    u = str(url or "").strip()
    for a, b in (("&amp;", "&"), ("&#38;", "&"), ("&#x26;", "&"),
                 ("&quot;", '"'), ("&#39;", "'")):
        u = u.replace(a, b)
    return u


def download_image_url(url: str):
    """下载一张图到 IMAGE_CACHE（与 extract_images 同一落盘约定），返回本地路径或 None。"""
    url = _clean_url(url)
    if not url or not str(url).startswith("http"):
        return None
    try:
        local = os.path.join(IMAGE_CACHE, f"img_{hashlib.md5(url.encode()).hexdigest()[:12]}.jpg")
        if os.path.exists(local) and os.path.getsize(local) > 0:
            return local
        os.makedirs(IMAGE_CACHE, exist_ok=True)
        data = None
        if REQUESTS_OK:
            try:
                r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200 and r.content:
                    data = r.content
            except Exception:
                data = None
        if data is None:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15, context=CTX) as resp:
                data = resp.read()
        if data:
            with open(local, "wb") as f:
                f.write(data)
            return local
    except Exception as e:
        log(f"[图片理解] 下载失败 {str(url)[:80]}: {e}")
    return None


def describe_image(image_ref, prompt: str = "") -> str:
    """一张图 → 一句话描述（带缓存）。失败返回 ""，**绝不抛异常**。

    2026-10-12 起改走**免费车道**（根目录 free_llm：直连 dsh-our-free-model 的
    zen/kilo 免密车道，免 key、无第三方云依赖）——此前用的智谱视觉管线已下线。
    """
    ref = str(image_ref or "").strip()
    if not ref:
        return ""
    # ★ 缓存键必须带上提示词（2026-10-13）：同一张图会被两种提示词问
    #   （普通描述 / 表情包情绪判定），不带 prompt 会互相串结果。
    _p_tag = hashlib.md5((prompt or "").encode("utf-8", "ignore")).hexdigest()[:8]
    key = ref
    try:
        if os.path.exists(ref):
            st = os.stat(ref)
            key = f"{ref}|{int(st.st_mtime)}|{st.st_size}|{_p_tag}"
    except Exception:
        key = f"{ref}|p{_p_tag}"
    if key in _IMG_DESC_CACHE:
        return _IMG_DESC_CACHE[key]
    desc = ""
    try:
        import sys as _sys
        if SCRIPT_DIR not in _sys.path:
            _sys.path.insert(0, SCRIPT_DIR)
        import free_llm
        r = free_llm.chat_vision(ref, prompt or _IMG_DESC_PROMPT) or {}
        if r.get("ok"):
            desc = str(r.get("summary") or "").strip()
        else:
            log(f"[图片理解] 免费车道失败: {str(r.get('error'))[:140]}")
    except Exception as e:
        log(f"[图片理解] 调用免费车道异常: {e}")
    if desc:
        if len(_IMG_DESC_CACHE) >= _IMG_DESC_CACHE_MAX:
            try:
                _IMG_DESC_CACHE.pop(next(iter(_IMG_DESC_CACHE)))
            except Exception:
                pass
        _IMG_DESC_CACHE[key] = desc
    return desc


def build_image_context_block(images, scene: str = "群聊") -> str:
    """把若干张图转成可注入 prompt 的【图片内容】块（视觉描述）。

    三种结局都说清楚，**不许静默省略**：
      · 有描述 → 正常块；
      · 一张都没成 → 明确写"解析失败 + 原因 + 不许猜"。
        （旧版这里返回 ""，模型只看到消息里的 `[图片]` 占位、不知道发生了什么，
         只能瞎编或甩一句"引用图片解析失败"——线上截图实证。）
      · 部分成 → 块里带上成功的那几张，并说明其余失败。
    """
    imgs = [p for p in list(images or [])[:6] if p]
    if not imgs:
        return ""
    lines: List[str] = []
    failed: List[str] = []
    for p in imgs:
        try:
            d = describe_image(p)
        except Exception as e:
            d = ""
            failed.append(f"{os.path.basename(str(p))}（{type(e).__name__}）")
        if d:
            lines.append(f"- {d}")
        elif not any(os.path.basename(str(p)) in f for f in failed):
            failed.append(os.path.basename(str(p)))
    # ★ 标语措辞刻意避开"的图片"等易被意图正则命中的串：画图意图正则里就有
    #   `的图片`，注入块若写成"【群聊里的图片内容】"会把普通消息误判成"帮我画图"
    #   并劫持去 ComfyUI（tests/test_image_understanding.py 实测抓到过）。
    head = f"【视觉识别 · {scene}图片内容】（不是原图，由视觉模型转述）"
    if lines and not failed:
        return head + "\n" + "\n".join(lines)
    if lines:
        return (head + "\n" + "\n".join(lines)
                + f"\n（另有 {len(failed)} 张没读懂：{', '.join(failed[:3])}；没读懂的别猜）")
    # 一张都没成：如实说失败，并禁止模型编内容
    why = ', '.join(failed[:3]) or "免费视觉车道不可用"
    return (head + f"\n⚠️ 解析失败：{len(imgs)} 张图都没读懂（{why}）。"
            "**不许猜图里是什么**，直接告诉用户没看懂，请它重发或描述一下。")


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
    batch_seen = set()
    for msg in all_msgs:
        msg_time = msg.get("time", 0)
        msg_id = msg.get("message_id", 0)

        # 去重①：本轮响应内同一 id 不重复
        if msg_id in batch_seen:
            continue
        batch_seen.add(msg_id)

        # 去重②（2026-10-10 修复"连续回答两次"）：登记到全局 _SEEN_MSG_IDS ——
        #   轮询路径此前**只**用秒级水位线，而事件推送路径用 _SEEN_MSG_IDS，
        #   两条路径状态不相通：轮询先抓到 time=T 的消息回复一次，事件推送
        #   晚 1s 再把同一条推进来时其 id 从未登记 → 放行 → 同一条回复两次。
        #   现在两条路径共用同一个权威闸门，谁先到谁登记，后者必被拦。
        if msg_id and _mark_seen_msg_id(msg_id):
            continue

        # 启动基线：本会话开始前的历史消息不处理（避免重启后回复旧消息）
        if msg_time and msg_time < startup_time:
            continue

        if msg_time > max_time:
            max_time = msg_time
        if msg_time > LAST_MSG_TIME:
            new_msgs.append(msg)
            # HUD 计数：轮询兜底路径与事件推送路径共用同一份统计
            # （协议层不推送时全靠这条轮询，不记的话 HUD 又会停在 0）
            try:
                _HUD_STATS["messages"] += 1
                _g = ""
                if msg.get("_type") == "group":
                    _g = str(msg.get("_group_id") or msg.get("group_id") or "")
                if _g:
                    _HUD_STATS["groups"].add(_g)
            except Exception:
                pass
    if max_time > LAST_MSG_TIME:
        LAST_MSG_TIME = max_time
        save_state()
    return new_msgs


# ═══════════════════════════════════════════
#  远程控制指令
# ═══════════════════════════════════════════

def handle_screenshot():
    """截屏并发送。

    ★ 2026-10-22 修「截图回两次」：原先这里 `send_image` 之后还硬编码
      再发一条「桌面截图 ↑」，而工具返回后主链还会再让模型生成一次回复 ——
      用户看到的是「图 + 桌面截图 ↑ + 模型的话」三次。实测反馈：「回复了两次」。
      工具只负责**做事**，说话交给回复链；这里删掉那条硬编码文本。
      失败时也不许自己 send_message 报错：把失败原因 return 出去，
      交给 `_tool_fail` 走统一通道（模型才能看到 ❌ 并按诚实铁律如实说）。
    """
    try:
        import pyautogui
        path = os.path.join(TEMP_DIR, f"screenshot_{int(time.time())}.png")
        pyautogui.screenshot(path)
        log(f"截图已保存: {path}")
        return bool(send_image(path))
    except Exception as e:
        log(f"截图失败: {e}")
        return False


def handle_open(target, speak=True):
    """打开文件/文件夹/程序/网址。

    `speak=False`：只返回文本发给模型，自己不 send_message
    （否则工具报一句「已打开 X」、模型再谢一句，用户收到两遍）。
    """
    _say = (lambda t: (send_message(t), t)[1]) if speak else (lambda t: t)
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
        return _say("QQ 路径未配置，请在设置中填写 qq_path")

    try:
        if target.startswith("shell:"):
            subprocess.run(["explorer", target], shell=True)
        else:
            os.startfile(target)
        return _say(f"已打开 {target}")
    except Exception as e:
        return _say(f"打开失败: {e}")


SYSTEM_INFO_TEMPLATE = """电脑配置
CPU: {cpu}
GPU: {gpu}
内存: {mem_total}GB (已用{mem_used:.1f}GB)
系统: {os_ver}
启动: {uptime}"""


def handle_sysinfo(speak=True):
    """获取系统信息。

    `speak=True`（默认）：直接把结果发出去 —— 供**命令正则**路径用
      （用户发「系统信息」，走 _COMMAND_RULES，没有模型参与，必须自己说）。
    `speak=False`：只返回文本，不发消息 —— 供 **LLM 工具**路径用。
      工具把结果 return 给模型，由模型组织成人话；若工具自己先发一遍，
      模型再回一遍，用户就收到两份（2026-10-22「截图回两次」同源）。
    """
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
        except Exception:
            cpu_name = platform.processor()

        cpu_percent = psutil.cpu_percent(interval=1)

        # GPU
        gpu_name = "Unknown"
        try:
            r = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True, timeout=10)
            gpu_name = r.stdout.decode("utf-8", errors="replace").strip()
        except Exception: pass

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
        if speak:
            send_message(info)
        return info if not speak else True
    except Exception as e:
        if speak:
            send_message(f"获取系统信息失败: {e}")
        return (f"获取系统信息失败: {e}") if not speak else False


def handle_volume(action, level=None, speak=True):
    """调整音量: up/down/mute/set。

    `speak=False`：只返回结果文本，不发消息（LLM 工具路径用，避免工具和模型各说一遍）。
    """
    _say = (lambda t: (send_message(t), t)[1]) if speak else (lambda t: t)
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
            return _say(f"音量 ↑ {new_pct}%")
        elif action == "down":
            current = volume.GetMasterVolumeLevelScalar()
            volume.SetMasterVolumeLevelScalar(max(current - 0.1, 0.0), None)
            new_pct = int(volume.GetMasterVolumeLevelScalar() * 100)
            return _say(f"音量 ↓ {new_pct}%")
        elif action == "mute":
            muted = volume.GetMute()
            volume.SetMute(not muted, None)
            return _say("已静音" if not muted else "已取消静音")
        elif action == "set" and level is not None:
            volume.SetMasterVolumeLevelScalar(level / 100.0, None)
            return _say(f"音量设为 {level}%")
        return _say(f"音量已调整 ({action})")
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
            return _say(f"音量已调整 ({action})")
        except Exception as e:
            return _say(f"音量调整失败: {e}")
    except Exception as e:
        return _say(f"音量调整失败: {e}")


def handle_power(action, speak=True):
    """关机/重启/锁屏/休眠 - 需确认。`speak=False` → 只返回文本。"""
    _say = (lambda t: (send_message(t), t)[1]) if speak else (lambda t: t)
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
            return _say(f"⚠️ {action}已排程，60秒后执行。发送「取消{action}」可撤销。")
        elif action in ("取消关机", "取消重启"):
            return _say("已取消。")
        else:
            return _say(f"已执行 {action}")
    return False


def handle_process(action, target="", speak=True):
    """列出/结束进程。`speak=False` → 只返回文本（LLM 工具路径用）。"""
    _say = (lambda t: (send_message(t), t)[1]) if speak else (lambda t: t)
    try:
        import psutil

        if action == "list":
            procs = []
            for p in psutil.process_iter(['pid', 'name', 'memory_percent', 'cpu_percent']):
                try:
                    info = p.info
                    if info['memory_percent'] > 0.5 or info['cpu_percent'] > 1:
                        procs.append(info)
                except Exception: pass
            procs.sort(key=lambda x: x.get('memory_percent', 0), reverse=True)
            lines = ["高占用进程 Top10:"]
            for p in procs[:10]:
                lines.append(
                    f"{p['name']} (PID:{p['pid']}) "
                    f"内存:{p.get('memory_percent', 0):.1f}% "
                    f"CPU:{p.get('cpu_percent', 0):.1f}%")
            return _say("\n".join(lines))

        elif action == "kill" and target:
            try:
                pid = int(target)
                p = psutil.Process(pid)
                name = p.name()
                p.terminate()
                return _say(f"已终止 {name} (PID:{pid})")
            except ValueError:
                for p in psutil.process_iter(['pid', 'name']):
                    if target.lower() in (p.info.get('name') or '').lower():
                        p.terminate()
                        return _say(f"已终止 {p.info['name']} (PID:{p.info['pid']})")
                return _say(f"未找到进程: {target}")
    except Exception as e:
        return _say(f"进程操作失败: {e}")


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
    except Exception as e:
        # P2-3：此前把**所有异常**一律说成"搜索超时"，且原因被吞掉无法排查
        # （权限错/命令不存在/编码炸都会伪装成超时）。原因进日志，用户话术照旧。
        log(f"[search] 搜索失败 query={query!r}: {type(e).__name__}: {e}")
        send_message("搜索失败，请缩小范围或换个关键词")
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
                return f"⚠️ 目录为空：{file_path}"
            # 发送前5个文件（分别计数：**不允许把失败的说成已发送**）
            _ok_n = 0
            _bad = []
            for f in files[:5]:
                full_path = os.path.join(dir_path, f)
                if send_file(full_path, target_qq):
                    _ok_n += 1
                    log(f"已发送文件: {f}")
                else:
                    _bad.append(f)
                    log(f"[文件] 目录内发送未成功：{f}")
            _sent_n = len(files[:5])
            if _bad:
                send_message(f"{len(_bad)}/{_sent_n} 个文件发送失败")
                return (f"⚠️ 仅发送成功 {_ok_n}/{_sent_n} 个；失败：{', '.join(_bad[:3])}"
                        + ("…" if len(_bad) > 3 else ""))
            return f"✅ 已发送 {_sent_n} 个文件"
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
                    return f"✅ 已发送：{os.path.basename(results[0])}"
                send_message(f"文件发送失败: {results[0]}")
                return f"❌ 发送失败：{os.path.basename(results[0])}（上传被拒绝或超时）"
            send_message(f"未找到文件: {file_path}")
            return f"❌ 未找到文件：{file_path}"
        except Exception:
            send_message(f"搜索文件失败: {file_path}")
            return f"❌ 搜索文件失败：{file_path}"
    
    # 大文件 → 后台传输（前台死等几分钟会卡住对话；传完我会主动告诉用户）
    try:
        _mb = os.path.getsize(file_path) / 1048576.0
    except Exception:
        _mb = 0.0
    if _mb >= _FILE_BG_MB:
        try:
            import threading as _th
            _th.Thread(target=_bg_upload, args=(file_path, str(target_qq)), daemon=True).start()
            log(f"[文件] 转后台传输：{os.path.basename(file_path)}（{round(_mb,1)}MB）")
            return (f"⏳ {os.path.basename(file_path)} 有 {round(_mb,1)}MB，已转**后台传输**，"
                    "传完我会主动告诉用户；现在先跟用户说一声'大的在传，稍等一下'")
        except Exception as e:
            log(f"[文件] 后台传输启动失败，退回同步：{e}")

    # 直接发送（小文件）
    st, desc = _send_file_blocking(file_path, str(target_qq))
    if st == "ok":
        log(f"已发送文件: {file_path}")
        return f"✅ 已发送：{os.path.basename(file_path)}（{round(_mb,1)}MB）"
    if st == "timeout":
        # ★ 超时 ≠ 失败：NapCat 可能还在传（线上实证：几分钟后文件真的到了）
        return (f"⚠️ {desc}。**不要告诉用户发送失败**，告诉用户'还在传/可能稍后才到'，"
                "并建议他稍等片刻再看")
    return f"❌ 发送失败：{os.path.basename(file_path)}（{desc}）"


# ═══════════════════════════════════════════
#  联网搜索
# ═══════════════════════════════════════════

# ★ 2026-10-23：这里原本有 _parse_sogou_results / _strip_tags /
#   _parse_bing_results / _parse_ddg_results 四个函数，返回
#   (url, title, snippet) 三元组。全都删了，原因：
#     ① 它们是**平行列表时代**的产物 —— 摘要和链接没有归属关系。
#        现在的解析在 web_access 里按结果块解析，直接产出 SearchResult。
#     ② 删掉之前已经没有任何调用方（grep 确认），唯一引用是测试文件，
#        而测试测的是"旧解析器"，不是线上真正跑的那份。
#     ③ 留着会诱导下一个人去改错文件 —— 2026-10-13 的 b_algo 事故规则
#        （只认 b_algo、跳过 bing 自家链接）现在由
#        web_access._parse_bing 承载，并且有离线测试盯着。
#
#   顺带：搜狗解析（_parse_sogou_results）也在这批里删了。它当年是因为
#   Bing 把「200元机械键盘」匹配成 HTTP 200 才加的备胎，但 Sogou 现在
#   需要 JS 且常见验证码，_own_search 里从来没接它，纯死代码。


def _search_via_deepseek(query, _log_prefix="搜索"):
    """DeepSeek 原生搜索。返回结果列表；没走通返回 None，没有结果返回 []。

    ★ 2026-10-23：这条现在只是**兜底**。主线在 web_access，顺序是
      自家抓取优先、DeepSeek 兜底 —— 自己的路零搜索费，只有上下文 token，
      而 DeepSeek 每千次 5 元。
      这里保留独立入口是因为 bot_core 的 search_web 还要先走天气/股票 API，
      那两个分支在调通用搜索之前就 return 了，不能整段交给 web_access。

    两个返回值必须分开：
      · None  = 没走通（没 key、网络错、429）→ 该降级
      · []    = 走通了但没有结果        → 就是没有，别再降级白花时间
    """
    try:
        import ds_search
    except Exception as e:
        log(f"[{_log_prefix}] ds_search 不可用: {e}")
        return None

    try:
        if not ds_search.available():
            return None
        results = ds_search.search(query, count=8, ori_question=query)
    except Exception as e:
        log(f"[{_log_prefix}] DeepSeek 搜索未走通: {str(e)[:100]}")
        return None

    if not results:
        log(f"[{_log_prefix}] DeepSeek 搜索「{query}」返回空")
        return []
    return results


def _render_ds_search(results, query, _log_prefix="搜索"):
    """把 DeepSeek 结果排成给模型读的文本。

    ★ 2026-10-23：转换和排版全部走 search_core / ds_search 的公开入口。
      这里原来自己手写 search_core.make(...) 逐个字段搬一遍，还调了
      ds_search.quality_of —— 而 quality_of 已经在清理中并入 search_core。
      手搬字段的坏处是官方 schema 加字段时这里不会报错，只会静默丢掉。
    """
    import search_core
    import ds_search

    items = ds_search.to_core_list(results, source="ds")
    if not items:
        return ""

    q_info = search_core.quality_of(items, query)
    log(f"[{_log_prefix}] DeepSeek「{query}」→ {len(items)} 条 "
        f"(质量 {q_info['verdict']}, top {q_info['top_score']})")

    body = search_core.format_for_model(items, query)
    if not body:
        return ""
    parts = [f"【{query}】搜索结果：", body]
    hint = search_core.quality_hint(q_info)
    if hint:
        parts.append("\n\n" + hint)
    return "\n".join(parts)


# ── 天气意图与城市识别（2026-10-25）──────────────────────────────
# 判据只认字面「天气」会漏掉很自然的问法：
#   「明天南京会下雨吗」「南京冷不冷」「今天多少度」「要不要带伞」
# 这些实测都落到通用搜索去了。而通用搜索对「会下雨吗」这类问句
# 返回的是鲁迅《明天》和株洲天气的旧页面（真实测过）。
_WEATHER_INTENT_RE = re.compile(
    r"天气|气温|多少度|几度|冷不冷|热不热|会?下雨|会?下雪|带伞|紫外线|雾霾|湿度")
# 城市出现在这些词之前：南京天气 / 南京会下雨 / 南京冷不冷 / 南京气温
_WEATHER_CITY_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,8}?)(?:的)?(?:天气|气温|多少度|几度|冷不冷|热不热|会?下雨|会?下雪|雾霾|湿度)")
# 这些是时间/语气词，不是地名，要先剥掉（「明天南京会下雨吗」→ 南京）
_WEATHER_TIME_PREFIX = ("今天", "明天", "后天", "昨天", "前天", "现在", "目前",
                        "这几天", "这周", "下周", "本周", "早上", "上午", "中午",
                        "下午", "晚上", "今晚", "明早", "帮我", "我想", "请问",
                        "查一下", "看看", "知道")


def _is_weather_query(query: str) -> bool:
    return bool(_WEATHER_INTENT_RE.search(str(query or "")))


def _weather_city(query: str) -> str:
    """从问句里取城市名。取不到返回空串 —— **绝不拿默认城市顶上**。

    原来的实现取不到就问北京，而结果标题仍写用户说的城市
    （标题【株洲天气】、数据是北京的），这是最坏的一种错。
    """
    q = str(query or "")
    m = _WEATHER_CITY_RE.search(q)
    city = m.group(1) if m else ""
    if not city:
        # 「南京今天热吗」这种城市在前、时间词在后的写法：取句首到时间词之前
        m2 = re.match(r"^([\u4e00-\u9fa5]{2,8}?)(?:今天|明天|后天|现在|这几天)", q)
        city = m2.group(1) if m2 else ""
    if not city:
        return ""
    # 剥前缀时间词，可能叠多层（「帮我查一下明天南京」）
    for _ in range(4):
        before = city
        for w in _WEATHER_TIME_PREFIX:
            if city.startswith(w):
                city = city[len(w):]
        if city == before:
            break
    city = city.strip("的了呢啊吧吗")
    # 只剩一个字的不是地名
    return city if len(city) >= 2 else ""


def search_web(query):
    """联网搜索，返回结果摘要（优先天气 API）"""
    # 如果是天气查询，用 wttr.in
    if _is_weather_query(query) or query.strip() == "天气":
        try:
            # ★ 2026-10-25 重写取城市的方式。原来的两个缺陷都会给出
            #   **确定但错误**的答案，比查不到更糟：
            #     ① 正则 `([\u4e00-\u9fa5]+)天气` 贪婪，「上海的天气」取到「上海的」，
            #        不在映射表里 → 静默查北京；而「X的天气」是最自然的说法之一
            #     ② 映射表只有 12 个城市，其余的（株洲、洛阳、保定…）全部
            #        静默落到 Beijing，而结果标题还写着用户问的城市名 ——
            #        标题说【株洲天气】，数据是北京的
            #   实测 wttr.in **直接认中文城市名**（`株洲` 与 `Zhuzhou` 返回
            #   同一个 nearest_area），所以那张英文映射表是多余的。
            city = _weather_city(query)
            if not city:
                # 问天但没说城市 —— 不能拿北京顶上，交给通用搜索如实回答
                log("天气查询未识别城市，转通用搜索")
                raise LookupError("no city")
            _city_hint = {
                "北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou",
                "深圳": "Shenzhen", "杭州": "Hangzhou", "成都": "Chengdu",
                "衡阳": "Hengyang", "长沙": "Changsha", "武汉": "Wuhan",
                "南京": "Nanjing", "西安": "XiAn", "重庆": "Chongqing"
            }
            # 已知的中文名给英文（延迟更稳），未收录的直接用中文 —— 实测可用
            city_q = _city_hint.get(city, city)
            url = f"https://wttr.in/{urllib.parse.quote(city_q)}?format=j1"
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

    # 普通搜索（2026-10-23 二次重排，用户要求以自家搜索为主）：
    #   ① 自家抓取（web_access：Bing + 360 → 合并 → 补分 → 抓正文）
    #   ② DeepSeek 原生搜索 —— 只在自家没出结果时兜底
    #   顺序这么定是因为 ① 零搜索费（只有上下文 token），② 每千次 5 元。
    #   但 ① 的前提是结果质量够用，所以这一轮给它补了三个原本没有的信号：
    #   时间（从摘要开头抽）、权威性（按域名估）、相关性（引擎位置 + 词面重合）。
    #   没有这三样，自家结果排不了序，也就谈不上"接近"。
    try:
        import web_access as _wa
        _own = _wa.search_web(query)
        if _own and not _is_search_failure(_own):
            log(f"搜索「{query}」→ 自家搜索命中")
            return _own
        log(f"搜索「{query}」自家没出结果，走 DeepSeek 兜底")
    except Exception as e:
        log(f"搜索「{query}」自家搜索异常，走 DeepSeek 兜底: {str(e)[:100]}")

    _ds_results = _search_via_deepseek(query)
    if _ds_results is None:
        return f"搜索「{query}」失败或无结果。如实告诉用户没搜到，**不要编造搜索结果**。"
    if not _ds_results:
        return f"搜索「{query}」失败或无结果。如实告诉用户没搜到，**不要编造搜索结果**。"
    _ds_text = _render_ds_search(_ds_results, query)
    if not _ds_text:
        return f"搜索「{query}」失败或无结果。如实告诉用户没搜到，**不要编造搜索结果**。"
    return _ds_text


def _is_search_failure(text) -> bool:
    """判断 search_web 的返回是不是「失败/无结果」的提示语，而不是真结果。

    search_web 搜不到时返回的是一句中文提示
    （"搜索「x」失败或无结果。如实告诉用户没搜到……"），
    它**不是**搜索结果。旧实现把它原样拼进「[系统搜索结果]」再喂回模型，
    等于告诉模型"这是你要引用的结果" —— 模型于是硬凑出一句敷衍的反问
    （实测：问「恋人不行你看过吗」，搜不到，回「没看过诶 / 讲啥的，姬撩直女那种？」）。
    """
    if text is None:
        return True
    s = str(text).strip()
    if not s:
        return True
    if "error" in s.lower()[:40]:
        return True
    return any(k in s for k in ("失败或无结果", "搜索失败", "未找到", "没有找到", "无结果"))


def _apply_search_tag_loop(reply, question_text, *, group_id, is_group,
                           temperature, target_qq=None, ego_ctx=None,
                           scene_ctx=None):
    """[搜索:关键词] 标记循环（群聊/私聊共用，最多2轮）。

    模型在回复里写 [搜索:xxx]（或直接 CALL search_web 走 function-call 分支），
    这里把它捞出来联网查、把结果喂回重答。修（2026-10-11）：
      ① 旧实现只在群聊有，私聊即使吐 [搜索:] 也原样漏给用户 —— 现两侧共用；
      ② 无论搜没搜，返回前都剥掉 [搜索:] 标记，绝不发进聊天。
    修（2026-10-22）：搜不到时**不再把失败提示当"搜索结果"喂回去**。
      旧逻辑对成功/失败一视同仁，结果是给模型两句互斥的指令
      （"如实告诉用户没搜到" + "请直接基于以上搜索结果回答用户问题"），
      模型只能硬凑。现在失败走另一套说法，并且失败一次就不再让它重搜。
    """
    reply = reply or ""
    _failed_once = False
    for _ in range(2):
        m = re.search(r'\[搜索:(.+?)\]', reply)
        if not m:
            break
        if _failed_once:
            # 已经失败过一次了，别让模型靠反复搜索硬凑 —— 直接停，标记留给下面剥掉
            log(f"{'群[' + str(group_id) + ']' if is_group else '私聊'} 搜索已失败，跳过重搜")
            break
        query = m.group(1).strip()
        log(f"{'群[' + str(group_id) + ']' if is_group else '私聊'} 联网搜索: {query}")
        search_result = search_web(query)
        today_str = datetime.datetime.now().strftime("%Y年%m月%d日")
        if _is_search_failure(search_result):
            # ── 搜不到：给模型的是「搜不到」这件事，不是「搜索结果」 ──
            _failed_once = True
            log(f"{'群[' + str(group_id) + ']' if is_group else '私聊'} 搜索无结果: {query}")
            retry_msg = (
                f"{question_text}\n\n"
                f"[系统搜索结果] 搜索「{query}」**没成功**，一条结果都没拿到。\n"
                "- 别装作查到了，别编剧情、别编数据。\n"
                "- 可以直说没搜到、没听过；也可以就这个话题说你自己那点看法（随便聊，不负责答疑）。\n"
                "- **不要把问题推回去让对方替你查**（不要「你知道讲啥吗」「你说说看」这种）。\n"
                "- 不要再次输出 [搜索:] 标记。"
            )
        else:
            retry_msg = (
                f"{question_text}\n\n"
                f"[系统搜索结果] 今天是{today_str}。以下是对「{query}」的搜索结果：\n{search_result}\n\n"
                "请直接基于以上搜索结果回答用户问题。如果结果跟问题对不上，就说你没查到有用的，"
                "**别硬扯**。可以补充你自己的常识但不要编造具体数据。禁止再次输出 [搜索:] 标记。"
            )
        reply = call_deepseek(retry_msg, [], target_qq=target_qq,
                              group_id=group_id, is_group=is_group,
                              personality_temperature=temperature,
                              ego_inject_context=ego_ctx or "",
                              scene_context=scene_ctx or "")
        reply = reply or ""
    # 兜底剥离：残留的 [搜索:…] 标记绝不允许进入发送文本
    # （re.sub 返回裸 str → 把截断元信息重新贴回去，发送端还要用）
    _tr = getattr(reply, "truncated", False)
    reply = re.sub(r'\[搜索:[^\]]*\]', '', reply).strip()
    return _mark_truncated(reply, _tr)


# ═══════════════════════════════════════════
#  AI 工具定义
# ═══════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "联网搜索。两种必用场景：①实时信息（天气/新闻/股价/最新事件）；②你不确定的名词、人名、游戏角色、黑话、梗——先搜再答，禁止不懂装懂。",
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

# ══════════════════════════════════════════════════════════════════
# 工具返回值协议（2026-10-13 用户报"应该类似的全套工具都有问题"）
# ──────────────────────────────────────────────────────────────────
# 每个工具的返回值必须自带成败标记，模型据此决定怎么跟用户说：
#   ✅ 前缀 = 真做成了；❌ 前缀 = 真没做成；⚠️ = 部分成功/需注意
# 配套的「诚实铁律」第 3 条：见到 ❌/⚠️ 就不许说"已经/发过去了/你自己看"。
#
# 历史坑（同一类 bug 修了三次，别再犯）：
#   · `"已成功" if handler() else "失败"` —— handler 返回**非空字符串**时恒为真
#     （send_user_file 把"发送失败"报成"文件已发送"，模型嘴硬"发过去了啊"）；
#   · `handler() and "成功" or "失败"` —— 同上，且失败文案还写成"已尝试打开"这种
#     听着像成功的话。
# 现在一律走 _tool_ok / _tool_fail，并且**失败原因要写具体**。
def _tool_ok(text: str) -> str:
    return "✅ " + str(text)


def _tool_fail(text: str) -> str:
    return "❌ " + str(text)


def _tool_warn(text: str) -> str:
    return "⚠️ " + str(text)


def _spoken_tool(fn, args=(), kwargs=None, *, fail_msg=""):
    """调一个「speak=False」的 handler，把它的返回值按工具协议包装。

    存在的理由：`speak=False` 的 handler 返回值有三种形态 ——
      · 非空字符串 → 成功，原样作为结果文本；
      · `False`     → 失败（老 handler 的布尔约定）；
      · 空串/None   → 也算失败（没拿到任何可用结果，不能让模型以为成功）。
    这层判断原先在四个 lambda 里各写一遍，且第一版漏了 `False` → 被
    tests/test_tool_result_protocol.py 抓到输出了 `✅ False`。收敛到一处。
    """
    r = fn(*(args or ()), **(kwargs or {}))
    if r and r is not False:
        return _tool_ok(r)
    return _tool_fail(fail_msg or "操作失败")


TOOL_FUNCTIONS = {
    "search_web": lambda args: _tool_web(args["query"]),
    # ★ 2026-10-22：以下工具统一 speak=False —— 工具只「做事 + 把结果 return 给模型」，
    #   不再自己 send_message。改之前它们既发消息、模型又回一句，用户收到两遍
    #   （实测反馈：「让他电脑截屏的时候，回复了两次」）。
    #   命令正则路径（_COMMAND_RULES）仍用默认 speak=True，那边没有模型兜着说。
    "open_app": lambda args: _spoken_tool(
        handle_open, (args["name"],), {"speak": False},
        fail_msg=f"打开 {args['name']} 失败（程序没起来或路径不对）"),
    "take_screenshot": lambda args: (_tool_ok("截图已截好并发给对方了") if handle_screenshot()
                                     else _tool_fail("截图失败（截屏库不可用或发送出错）")),
    "get_system_info": lambda args: _spoken_tool(
        handle_sysinfo, (), {"speak": False},
        fail_msg="获取系统信息失败（psutil 不可用或读取异常）"),
    "control_volume": lambda args: _spoken_tool(
        handle_volume, (args["action"], args.get("level")), {"speak": False},
        fail_msg=f"音量 {args.get('action', '?')} 调整失败（音频接口不可用）"),
    "control_power": lambda args: _spoken_tool(
        handle_power, (args["action"],), {"speak": False},
        fail_msg=f"电源操作 {args['action']} 失败（命令被拒或超时）"),
    "manage_process": lambda args: _spoken_tool(
        handle_process, (args["action"], args.get("target", "")), {"speak": False},
        fail_msg=f"进程操作 {args.get('action', '?')} 失败（psutil 不可用或权限不足）"),
    "search_files": lambda args: _tool_search_files(args["query"]),
    # send_user_file：handle_user_file 自带 ✅/❌/⚠️（见其内部各分支）
    "send_user_file": lambda args: handle_user_file(args.get("path") or args.get("file") or ""),
    "generate_image": lambda args: _tool_gen(args["prompt"], args.get("style", "default")),
    "web_operate": lambda args: handle_web_operate(args),
}

# ── 插件工具（主链常驻，不参与关键词裁剪、不受群白名单影响）──────────
#  agent 链早就有这两个，主链没有 —— 于是「发个 QQ 空间」只能答「没能力」。
#  常驻的理由：措辞一旦没命中关键词，按需加载就不会挂上它，又回到"不会"。
_PLUGIN_TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "plugin_list",
        "description": "列出已装插件及其简介、可调用动作、启用状态。"
                       "不确定有哪些插件、或不确定某件事能不能做时，先调它。",
        "parameters": {"type": "object",
                       "properties": {"keyword": {"type": "string",
                                                  "description": "可选，按名字/简介/标签过滤"}}},
    }},
    {"type": "function", "function": {
        "name": "plugin_call",
        "description": "调用插件能力（发 QQ 空间、群待办、群相册、定时任务等）。"
                       "参数 plugin=插件名、action=动作名、params=参数对象。"
                       "未启用的插件会被拒绝并说明怎么开 —— 这不是「没能力」。",
        "parameters": {"type": "object",
                       "properties": {
                           "plugin": {"type": "string", "description": "插件名，如 qzone/capabilities"},
                           "action": {"type": "string", "description": "动作名，如 run/add"},
                           "params": {"type": "object", "description": "参数对象，可为空"}},
                       "required": ["plugin", "action"]},
    }},
]


#: 群管理工具（只在群聊注入）。自然语言走这条，命令式仍走正则。
GROUP_ADMIN_SCHEMA = {"type": "function", "function": {
    "name": "group_admin",
    "description": "执行群管理操作：踢人 / 禁言 / 解除禁言 / 设管理 / 撤管理 / "
                   "改群名片 / 改群名 / 退群。"
                   "用户用自然语言提要求（「把群名改成 X」「禁言他十分钟」「踢了他」）"
                   "时调用它，不要只用嘴答应。只有主人能触发；我不是群管理或没权限时，"
                   "工具会返回原因，照原话告诉对方。",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string",
                   "enum": ["kick", "ban", "unban", "set_admin", "del_admin",
                            "set_card", "set_group_name", "leave"]},
        "target": {"type": "string",
                   "description": "目标 QQ 号（kick/ban/unban/set_admin/del_admin/set_card 必填）"},
        "value": {"type": "string",
                  "description": "禁言分钟数 / 新群名片 / 新群名"},
    }, "required": ["action"]}}}


def _tool_group_admin(args, *, sender="", group_id="", user_msg=""):
    _a = str((args or {}).get("action") or "")
    _ok, _msg = _do_group_admin(
        _a,
        target=str((args or {}).get("target") or ""),
        value=str((args or {}).get("value") or ""),
        sender=sender or str((args or {}).get("sender") or ""),
        group_id=group_id or str((args or {}).get("group_id") or ""),
        user_msg=user_msg,
    )
    return _tool_ok(_msg) if _ok else _tool_fail(_msg)


def _tool_plugin_list(args):
    try:
        from harness import plugin_tools as _ptf
        _kw = str((args or {}).get("keyword") or "").strip()
        _r = _ptf.plugin_list(keyword=_kw) if _kw else _ptf.plugin_list()
        return _tool_ok(json.dumps(_r, ensure_ascii=False))
    except Exception as e:
        return _tool_fail(f"列插件失败：{e}")


def _tool_plugin_call(args):
    try:
        from harness import plugin_tools as _ptf
        _r = _ptf.plugin_call(
            plugin=str((args or {}).get("plugin") or ""),
            action=str((args or {}).get("action") or ""),
            params=(args or {}).get("params") or {},
            # 主链不阻塞等授权：群里弹窗没人看，180 秒还会把相位顶到超时
            interactive=False,
        )
        if isinstance(_r, dict) and _r.get("error"):
            return _tool_fail(str(_r["error"]))
        return _tool_ok(json.dumps(_r, ensure_ascii=False))
    except Exception as e:
        return _tool_fail(f"调用插件失败：{e}")


TOOL_FUNCTIONS["plugin_list"] = _tool_plugin_list
TOOL_FUNCTIONS["plugin_call"] = _tool_plugin_call


def _tool_web(query: str) -> str:
    """联网搜索：结果原样回传；看着像失败的加 ❌ 前缀（模型据此不乱编）。"""
    r = search_web(query)
    r = "" if r is None else str(r)
    if not r.strip():
        return _tool_fail(f"搜索「{query}」没有返回任何内容（网络或关键词问题）。"
                          f"别编；也别把问题推回去让对方替你查。")
    if _is_search_failure(r):
        # 失败时**别把那句提示当搜索结果**给模型（它会当成可引用的内容硬凑），
        # 只说清"没搜到"和"别编"，并顺手禁掉"让对方替你查"。
        return _tool_fail(f"搜索「{query}」没成功，没拿到结果。别编；"
                          f"也别把问题推回去让对方替你查。\n（原始返回）{r[:200]}")
    return r


def _tool_search_files(query: str) -> str:
    """文件搜索：找到几个/没找到都要说清（旧版一律"已完成"，模型分不清）。"""
    try:
        r = handle_search(query)
    except Exception as e:
        return _tool_fail(f"搜索文件异常：{e}")
    if r is True or r == "" or r is None:
        return _tool_ok(f"搜索「{query}」完成，结果已直接发给用户")
    return _tool_ok(str(r)[:300])


def _tool_gen(prompt: str, style: str) -> str:
    """画图：只有 `图片已生成:` 前缀才算成，其余一律 ❌。"""
    r = handle_generate_image(prompt, style)
    r = "" if r is None else str(r)
    if r.startswith("图片已生成:"):
        return _tool_ok("图片已生成并发送：" + r[len("图片已生成:"):].strip())
    return _tool_fail(r or "图片生成失败（未返回原因）")

_SCOPE_REGISTRY = None
_SCOPE_REGISTRY_MTIME = None


def _get_scope_registry():
    """P4：懒加载作用域注册表单例。失败 → None（**软依赖**，绝不放映到主流程）。

    参考源 dsh-permission-presets：本模块只拥有「选择与默认值」，
    撤掉它最后一次取值依然生效 —— 所以这里失败也只是"不参与过滤"。

    **免重启热改**：每次取用时比对事件文件的 mtime，变了就 `reload()` ——
    这样"外部改文件即生效"成立（S5 的界面做好前，这是唯一能改档位的途径）。
    """
    global _SCOPE_REGISTRY, _SCOPE_REGISTRY_MTIME
    if _SCOPE_REGISTRY is None:
        try:
            from harness.scope_registry import ScopeRegistry
            _SCOPE_REGISTRY = ScopeRegistry()
        except Exception as e:
            log(f"[scope] 注册表初始化失败（按未启用处理）: {e}")
            _SCOPE_REGISTRY = False
    reg = _SCOPE_REGISTRY or None
    if reg is not None:
        try:
            _p = reg.events_path
            _m = os.path.getmtime(_p) if os.path.isfile(_p) else 0
            if _m != _SCOPE_REGISTRY_MTIME:
                _SCOPE_REGISTRY_MTIME = _m
                reg.reload()
        except Exception as e:
            log(f"[scope] 热重载检查失败（继续用内存状态）: {e}")
    return reg


def _apply_scope_registry(kind, sid, tools, *, registry=None):
    """P4 · S2：用作用域能力集过滤一批工具。

    - 开关 `capability_registry_enable` **默认 False** → 原样返回（**零行为变化**）
    - `standard` 预设「不额外收紧」→ 能力集覆盖全部工具名 → 结果逐字节等于入参
    - `readonly` 收紧；`full` 另带 `override_allowlist` 标志（S2 只透传，不改 base 名单）
    - 注册表异常 / 拿到空集 → 原样返回（软依赖：**绝不让工具列表凭空消失**）
    """
    try:
        if not _config.get("capability_registry_enable", False):
            return tools
        reg = registry or _get_scope_registry()
        if reg is None:
            return tools
        caps = reg.resolve(kind, sid).get("capabilities") or set()
        if not caps:
            return tools
        return [t for t in tools
                if t.get("function", {}).get("name") in caps]
    except Exception as e:
        log(f"[scope] 注册表过滤失败（按不过滤处理）: {e}")
        return tools


#: 好感度档位缓存：{"gid:uid": (ts, (tier, note, score))}，TTL 300 秒
_RELATION_CACHE: Dict[str, Tuple[float, tuple]] = {}

#: 好感度 → 四档关系。阈值沿用 self_learning.get_context_for_llm 的分档口径
#: （>50 友好 / >20 普通 / 其余冷淡），只把"友好"再切一刀成"好"与"特别好"。
_RELATION_TIERS = (
    (80, "特别好", "能抱怨、能撒娇、能逗他；关系到这了，这次真不干也行"),
    (50, "好", "傲娇，可以撒娇，两三句内"),
    (20, "一般", "轻微抱怨，一句"),
    (0, "浅", "直白嫌烦，不撒娇"),
)


def _relation_tier(group_id, sender):
    """好感度 → (档位, 说明, 分数)。读不到返回空三元组。

    只给档位词，不给分数：分数进了 prompt 模型会念叨，而且分数每轮都动，
    档位十天半个月才跳一次 —— 前者破坏缓存，后者不会。
    查询结果缓存 5 分钟：每轮一次 SQLite 查询在群聊高频下没必要。
    """
    if not group_id or not sender:
        return ("", "", "")
    _key = f"{group_id}:{sender}"
    _now = time.time()
    _hit = _RELATION_CACHE.get(_key)
    if _hit and _now - _hit[0] < 300:
        return _hit[1]
    try:
        if not SELF_LEARN_OK or self_learn is None:
            return ("", "", "")
        # ★ 2026-10-27 修正：方法在 `self_learn.affection` 上，
        #   `self_learn`（SelfLearningEngine）自己没有 get_affection。
        #   旧写法抛 AttributeError，被下面 except 吞掉 → 恒返回空三元组
        #   → 关系行与整个意愿轴（_refuse_license）全部恒空，
        #   而功能看起来"已实现"（提交里有、测试也有）。
        _aff = getattr(self_learn, "affection", None)
        if _aff is None or not hasattr(_aff, "get_affection"):
            log(f"[关系档位] 读不到好感度：self_learn 上没有 affection 引擎")
            return ("", "", "")
        _sc = int((_aff.get_affection(str(group_id), str(sender)) or {}).get("score") or 0)
    except Exception as _e:
        # ★ 不再静默：静默 except 会把"接线断了"变成"功能不存在"，
        #   下次断还是查不出来（这正是本次事故的成因）。
        log(f"[关系档位] 读好感度失败（{type(_e).__name__}: {_e}）")
        return ("", "", "")
    for floor, key, note in _RELATION_TIERS:
        if _sc >= floor:
            _out = (key, note, str(_sc))
            _RELATION_CACHE[_key] = (_now, _out)
            return _out
    _out = ("浅", "直白嫌烦，不撒娇", str(_sc))
    _RELATION_CACHE[_key] = (_now, _out)
    return _out


#: 意愿轴账本：{"gid:uid": [ts...]} 与 {"YYYY-MM-DD:uid": [ts...]}
#: 前者管"同一人 30 分钟内最多被拒几次"，后者管"一天里拿不想逃避正经求助几次"。
_REFUSE_LEDGER: Dict[str, List[float]] = {}
_EVADE_LEDGER: Dict[str, List[float]] = {}
_REFUSE_LOCK = Lock()

#: 关系档 → 拒绝概率系数（A.7.2 ②）
_REFUSE_COEF = {"浅": 0.5, "一般": 1.0, "好": 1.5, "特别好": 2.0}


def _budget_shift(mood_shift=0, talkativeness=0.0, rng=None):
    """配额档位的总偏移 = 情绪短期偏移 + 人格长期系数（1.5.9 §C.2）。

    人格是**概率**不是阈值：talkativeness=0.25 表示四分之一的回合往上说一档，
    而不是"超过 0.5 就切换"。这样调参是连续的，也能用分布验证。
    默认 0 = 保持现状分布。
    """
    s = int(mood_shift or 0)
    try:
        t = float(talkativeness or 0.0)
    except Exception:
        t = 0.0
    if t > 0:
        r = random.random() if rng is None else float(rng)
        if r < t:
            s += 1
    return s


def _refuse_license(group_id, sender):
    """这次给不给「可以不办」的许可。返回 (allowed, reason)。

    概率只决定**要不要启动撒娇**，不决定办不办 —— 真正办不办由模型按
    人格【干活的时候】决定，概率只是把"这次可以拒"这句话放进 prompt。

    三道闸（A.7.2 ③ / A.7.4）：
      · 同一人 30 分钟内已给过 N 次许可 → 这次必须办（耍赖有额度）
      · 当天拿"不想"逃避正经求助 N 次 → 概率归零（逃避有上限）
      · 概率 × 关系系数，浅档基本不给
    """
    if not _config.get("refuse_enable", True):
        return (False, "off")
    if not group_id or not sender:
        return (False, "no-scope")
    tier, _note, _score = _relation_tier(group_id, sender)
    if not tier:
        return (False, "no-tier")
    now = time.time()
    key = f"{group_id}:{sender}"
    day = time.strftime("%Y-%m-%d")
    dkey = f"{day}:{sender}"
    with _REFUSE_LOCK:
        hist = [t for t in _REFUSE_LEDGER.get(key, []) if now - t < 1800]
        _REFUSE_LEDGER[key] = hist
        quota = int(_config.get("refuse_quota_per_30min", 2) or 0)
        if quota > 0 and len(hist) >= quota:
            return (False, "quota")
        ev = [t for t in _EVADE_LEDGER.get(dkey, []) if now - t < 86400]
        _EVADE_LEDGER[dkey] = ev
        limit = int(_config.get("refuse_evade_daily_limit", 3) or 0)
        if limit > 0 and len(ev) >= limit:
            return (False, "evade-limit")
    try:
        base = float(_config.get("refuse_base_prob", 0.08) or 0)
    except Exception:
        base = 0.08
    p = max(0.0, min(1.0, base * _REFUSE_COEF.get(tier, 1.0)))
    if p <= 0 or random.random() >= p:
        return (False, "dice")
    with _REFUSE_LOCK:
        _REFUSE_LEDGER.setdefault(key, []).append(now)
    return (True, "ok")


def _note_evade(sender):
    """模型拿"不想"把正经求助挡回去时记一笔（由回复后处理调用）。"""
    if not sender:
        return
    now = time.time()
    day = time.strftime("%Y-%m-%d")
    with _REFUSE_LOCK:
        _EVADE_LEDGER.setdefault(f"{day}:{sender}", []).append(now)


#: bot 自己在群里的角色缓存：{gid: (ts, role)}，role ∈ owner/admin/member
_BOT_ROLE_CACHE: Dict[str, Tuple[float, str]] = {}


def _bot_group_role(group_id) -> str:
    """查 bot 自己在该群的角色，缓存 `group_role_cache_sec`（默认 600 秒）。

    `GROUP_ROLES`（1.5.2）只存群主/管理员的名字，不含 bot 自己的身份，
    所以模型答不出「我能不能禁言」。这里补的是事实依据，不是措辞规则。
    查不到返回空串，调用方按「不知道」处理 —— 不要兜底成 member。
    """
    gid = str(group_id or "")
    if not gid or not gid.isdigit() or len(gid) < 5:
        # 非群号（测试桩、私聊 id、空串）不查 API：白等 3 秒还会打进日志
        return ""
    try:
        ttl = float(_config.get("group_role_cache_sec", 600) or 600)
    except Exception:
        ttl = 600.0
    now = time.time()
    hit = _BOT_ROLE_CACHE.get(gid)
    if hit and now - hit[0] < ttl:
        return hit[1]
    role = ""
    try:
        resp = api_call("get_group_member_info",
                        {"group_id": int(gid), "user_id": int(BOT_QQ)}, timeout=3)
        role = str(((resp or {}).get("data") or {}).get("role") or "")
    except Exception as e:
        log(f"[能力视图] 查群角色失败 group={gid}: {e}")
    if role in ("owner", "admin", "member"):
        _BOT_ROLE_CACHE[gid] = (now, role)
        return role
    return ""


def _all_tool_names() -> List[str]:
    """主链全量工具名（TOOLS + GROUP_TOOLS + 常驻插件工具），去重保序。"""
    out: List[str] = []
    seen = set()
    for group in (TOOLS, GROUP_TOOLS):
        for t in group or ():
            try:
                n = t["function"]["name"]
            except Exception:
                continue
            if n and n not in seen:
                seen.add(n)
                out.append(n)
    for sch in _PLUGIN_TOOL_SCHEMAS:
        try:
            n = sch["function"]["name"]
        except Exception:
            continue
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _capability_view_lines(*, is_group, group_id, sender, tool_names):
    """三态能力清单。返回若干行，由 `_capability_block` 统一加前缀。"""
    _plugins: Dict[str, bool] = {}
    try:
        _pm = get_plugin_manager()
        if _pm:
            for _pname, _label in capabilities_view.PLUGIN_CAPABILITIES:
                _plugins[_pname] = bool(_pm.is_enabled(_pname))
    except Exception:
        pass
    _all = _all_tool_names()
    # 群里调不了的工具分两种：群白名单没放（改配置才能好）、这轮按需加载没挂（下轮会好）
    _blocked: List[str] = []
    if is_group:
        try:
            _allowed = {t["function"]["name"] for t in _build_group_tools(group_id)}
            _blocked = [n for n in _all if n not in _allowed]
        except Exception:
            _blocked = []
    _role = _bot_group_role(group_id) if (is_group and group_id) else ""
    try:
        _sender_is_admin = bool(_is_authorized(sender)) if sender else False
    except Exception:
        _sender_is_admin = False
    _snap = capabilities_view.build_snapshot(
        offered_tools=tool_names,
        all_tools=_all,
        blocked_tools=_blocked,
        plugins=_plugins,
        is_group=is_group,
        bot_role=_role,
        sender_is_admin=_sender_is_admin,
        admin_enabled=bool(_config.get("admin_commands_enable", True)),
        show_disabled=bool(_config.get("capability_show_disabled", True)),
    )
    return capabilities_view.render(_snap, is_group=is_group, bot_role=_role)


def _legacy_tool_line(names) -> str:
    """1.5.8 的旧文案，仅 `capability_view_enable=false` 时使用。"""
    _desc = capabilities_view.TOOL_LABELS
    _parts = []
    for n in names:
        d = _desc.get(n)
        _parts.append(f"{n}（{d}）" if d else n)
    return (f"你这轮**真有**的工具：{'、'.join(_parts)}。"
            "这些是真能用的，被问就照实说；不在这份名单里的能力，"
            "直接说「这个我这边干不了」，不许假装能做到。")


def _capability_block(*, is_group, group_id=None, sender="", tool_names=(),
                      user_msg=""):
    """把**运行时真实状态**写进 prompt：我在哪、我能用什么、谁有权让我干活。

    为什么需要（2026-10-22 用户实测的四条一起暴露）：
      · "不知道自己的群聊工具链" —— 模型只知道按需裁剪后的工具 schema，
        没有一个"我这轮总共有哪些工具"的视图，被问「你能截图吗」只能猜；
      · "分不清白名单" —— 群聊工具受 group_tool_allowlist 限制，私聊不受，
        但这件事从来没写进 prompt；
      · "不知道白名单上的管理员有谁" —— `_get_admin_qqs()` 是个纯函数，
        只被命令鉴权用，从没进过 prompt；
      · "不知道如果不是管理员怎么回复工具使用" —— 没权限时的正确说法
        没有任何地方告诉模型，它只能编（"我没有这个权限"或干脆答应下来）。

    设计取舍
      · 放 **stable_parts**（准静态：一天变不了几次）→ 进可缓存前缀，成本极低；
        "谁在说话/是不是管理员"是每轮变的 → 那部分放 dynamic（见 TH 行）。
      · 工具名**只列名字不列 schema**（schema 已经有独立的 tools 字段，重复写是浪费）。
      · 白名单里"谁能用"只给**昵称**，不给 QQ 号（群聊规则明确禁止出现号码）。
    """
    lines = []
    # ── 1. 能力清单（三态：能做 / 有条件 / 真没有）──
    #  旧实现只列「这轮真有的工具」，并要求名单外一律否认 —— 模型照做，
    #  于是"插件没开"被答成"我没有这个能力"。见 docs/FIXPLAN_1.5.9.md §A.2。
    _names = [n for n in (tool_names or ()) if n]
    if _config.get("capability_view_enable", True):
        try:
            lines.extend(_capability_view_lines(
                is_group=is_group, group_id=group_id, sender=sender,
                tool_names=_names))
        except Exception as e:
            log(f"[能力视图] 三态清单生成失败（退回旧文案）: {e}")
            if _names:
                lines.append(_legacy_tool_line(_names))
    elif _names:
        lines.append(_legacy_tool_line(_names))
    # ── 2. 作用域差异（群里 vs 私聊）──
    if is_group:
        _wl = _config.get("group_tool_allowlist")
        if isinstance(_wl, list) and _wl:
            lines.append("这条限制**只在群里**生效：群聊工具是白名单制，私聊是另一套。"
                         "上面标了「群白名单没放它」的，群里真调不了"
                         "（让主人加进 group_tool_allowlist）；"
                         "标了「这轮没挂上」的，下一轮会挂上。"
                         "被问「你在群里能干什么」就按清单答，"
                         "别把前一种答成「我没有这个能力」。")
        else:
            lines.append("群聊和私聊用的是同一套工具，没有额外限制。")
    # ── 3. 谁能让我干活（管理员）──
    try:
        _admins = _get_admin_qqs()
    except Exception:
        _admins = set()
    try:
        _bot_qq = str(BOT_QQ or "")
    except Exception:
        _bot_qq = ""
    _admin_names = []
    _admin_unresolved = 0
    for _q in sorted(_admins):
        if _q and _q != _bot_qq:
            try:
                _nm = resolve_qq_nick(_q) or ""
            except Exception:
                _nm = ""
            if _nm:
                _admin_names.append(_nm)
            else:
                # ★ 不许回退成 QQ 号：群聊规则明令"不许出现 QQ 号"。
                #   resolve_qq_nick 是异步解析，冷启动第一次必然返回 None，
                #   老实现的 `_nm or _q` 会把裸号码写进 prompt，模型可能复述到群里。
                _admin_unresolved += 1
    _is_adm = False
    try:
        _is_adm = bool(_is_authorized(sender)) if sender else False
    except Exception:
        _is_adm = False

    if _admin_names or _admin_unresolved:
        _who = "、".join(_admin_names) if _admin_names else "（名字还没查到）"
        if _admin_unresolved:
            _who += f" 等 {len(_admin_names) + _admin_unresolved} 位"
        lines.append(
            f"能让我执行本机操作/管理指令的是这几个人（主人）：{_who}。"
            "其他人的这类要求不归我管，也不用解释一堆——"
            "最多损一句「你谁啊」或者「这得主人发话」，别装成自己权限不够的客服。")
    else:
        lines.append("当前没配管理员，谁都能让我干活。")
    # ── 4. 本条消息的发送者身份 ──
    if sender:
        _sender_name = ""
        try:
            _sender_name = resolve_qq_nick(str(sender)) or ""
        except Exception:
            pass
        if _is_adm:
            lines.append(f"现在跟你说话的是 {_sender_name or '本人'}，**是主人**。"
                         "他让我干事就办，别摆架子、别问「你确定吗」；"
                         "真有脾气也直说，别装不会。")
        else:
            lines.append(f"现在跟你说话的是 {_sender_name or '群友'}，**不是**主人。"
                         "普通聊天天南地北都行；但调本机/改配置/执行命令这类事，"
                         "得主人发话——你可以直说「这我不能替你办」，"
                         "或者阴阳一句「你使唤我？」都行，**别答应下来再装失败**。")
        # ── 5. 我跟他什么关系（A.7：撒娇与抱怨的强度从这里来）──
        if _config.get("persona_relation_enable", True):
            _rk, _rn, _ = _relation_tier(group_id, sender)
            if _rk:
                lines.append(f"我跟他关系：{_rk}（{_rn}）。")
            # ── 6. 意愿轴：这次允不允许拒绝（概率 + 额度，见 _refuse_license）──
            if is_group and _rk:
                _allow, _why = _refuse_license(group_id, sender)
                if _allow:
                    if _rk == "特别好" and _config.get("intimate_refuse_ok", True):
                        lines.append("这次他让你办的事，你可以真不办 —— 想拒就拒，"
                                     "说「不想」「懒得」都行，别编「不会」。")
                    else:
                        lines.append("这次可以嫌烦、可以念叨，但事还是要办 —— "
                                     "别把不情愿说成「不会」。")
    return "【我现在的处境】\n" + "\n".join("- " + x for x in lines) if lines else ""


def _scope_policy_block(kind, sid):
    """P4 · S4：把「当前能力档位」贡献给模型（照搬 dsh-sandbox-policy 的模型可见文本）。

    三条约束（都照搬参考源）：
      1. **只说档位、不枚举已挂载的能力**（"它不枚举已挂载能力"）
      2. **默认档位不注入任何文本** → 开关关/未配置时 prompt 逐字节不变（零行为变化）
      3. 放进 dynamic_parts（**追加**），不碰 static_prompt → **保住前缀缓存**（不变量 A8）
    """
    try:
        if not _config.get("capability_registry_enable", False):
            return ""
        reg = _get_scope_registry()
        if reg is None:
            return ""
        r = reg.resolve(kind, sid)
        preset = r.get("preset")
        # 只在"确实偏离默认"时才说话：默认档位 + 无覆盖 → 不注入
        if preset == reg.default_preset() and not r.get("overrides"):
            return ""
        if preset == "custom":
            _n = len(r.get("overrides") or {})
            return (f"【能力档位】当前作用域为自定义档位（在「{r.get('named_preset')}」之上"
                    f"有 {_n} 项单独设置）。能做与不能做由系统在调用时判定，你无需询问；"
                    "若某次操作被拒绝，请如实告知对方「这个操作在当前设置下不被允许」，"
                    "不要反复重试，也不要假装已经完成。")
        _desc = {"standard": "不额外限制", "readonly": "只读（写与执行类操作被拒绝）",
                 "full": "完全放开"}.get(preset, preset)
        return (f"【能力档位】当前作用域的能力档位是 {preset}（{_desc}）。"
                "具体哪些操作可用由系统判定，你无需询问；"
                "若某次操作被拒绝，请如实告知对方「这个操作在当前设置下不被允许」，"
                "不要反复重试，也不要假装已经完成。")
    except Exception as e:
        log(f"[scope] 档位文本生成失败（不注入）: {e}")
        return ""


def _build_group_tools(group_id=None, *, registry=None):
    """根据配置 group_tool_allowlist 动态生成群聊可用工具列表。

    - 配置了 group_tool_allowlist（数组）时：只返回该名单里存在于 TOOLS/GROUP_TOOLS 的工具。
      常见用法：["get_group_members", "generate_image", "web_operate"]，即可让网页操作在群聊可用。
    - 未配置时：回退到默认 GROUP_TOOLS（保持向后兼容，仅 get_group_members + generate_image）。

    ── P4 · S2 ──
    上面这段（既有全局名单）**原样保留为 base**；在其之上再叠加一层**作用域能力集过滤**
    （`_apply_scope_registry`）。开关默认关 → 行为与改前逐字节相同；
    `standard` 档位「不额外收紧」→ 即使开关打开也逐字节相同（两条都有测试守着）。
    """
    allow = _config.get("group_tool_allowlist")
    if allow is None:
        base = GROUP_TOOLS
    else:
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
        base = [t for t in pool if t.get("function", {}).get("name") in allow]
    return _apply_scope_registry("group", group_id, base, registry=registry)

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
    _gr = GROUP_ROLES.get(str(gid)) or {}
    names = []
    for m in members:
        if not m or not isinstance(m, dict):
            continue
        uid = str(m.get("user_id", ""))
        card = (m.get("card") or "").strip()
        nick = (m.get("nickname") or "").strip()
        display = card if card else nick
        if display:
            # 带角色标注（2026-10-11）：工具输出原来只回昵称，模型拿不到
            # owner/admin 信息 → "群主是谁"只能靠猜。现按 GROUP_ROLES 命中加标注。
            if display == _gr.get("owner"):
                display = display + "(群主)"
            elif display in (_gr.get("admins") or []):
                display = display + "(管理)"
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
    # 去掉 [表情码] 如 [示例用户]
    clean_desc = re.sub(r'\[[\u0000-\u007f\u4e00-\u9fff\uff00-\uffef]{1,20}\]', '', clean_desc)
    # 去掉 @Bot 昵称（全部称呼，配置驱动；不再写死某一个昵称）
    for _n in _bot_nicknames():
        clean_desc = re.sub(r'@?' + re.escape(_n) + r'\s*', '', clean_desc)
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
    except Exception:
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


# ── 群聊白名单（P0-2）──────────────────────────────────────────────
# 缺陷：群分支原先只查逐群 reply_enabled（默认 True），**没有任何"只在这些群工作"
# 的开关** —— 适配器转发什么群，Bot 就在什么群说话。被拉进陌生群会立刻开口。
_GROUP_BLOCK_NOTICE: dict = {}
_GROUP_BLOCK_NOTICE_COOLDOWN = 1800.0   # 每个群每 30 分钟最多提示一次，避免刷屏


def _group_whitelist() -> list:
    """解析配置 group_whitelist。空/未设置 → []（表示不限制）。"""
    raw = _config.get("group_whitelist")
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        raw = re.split(r"[\s,;，；]+", raw)
    if not isinstance(raw, (list, tuple, set)):
        return []
    out = []
    for x in raw:
        if x is None:
            continue                      # None 不能被 str() 成 "None" 混进白名单
        if isinstance(x, (list, tuple, dict, set)):
            continue                      # 嵌套容器不是群号，跳过
        s = str(x).strip()
        if s:
            out.append(s)
    return out


def _group_block_reason(gid):
    """该群是否被拒绝参与；返回原因字符串，None = 允许。

    语义（P0-2，**空白名单 = 不限制**，保证升级不改变现网行为）：
      1. 逐群 reply_enabled=False  → 拒绝（该开关优先，语义与从前完全一致）
      2. 白名单非空且该群不在其中  → 拒绝
      3. 其余                      → 允许
    """
    g = str(gid).strip()
    if _group_restriction(gid, "reply_enabled", True) is False:
        return "该群自动回复已关闭（群管理页 reply_enabled=False）"
    wl = _group_whitelist()
    if wl and g not in wl:
        return f"不在群白名单（白名单共 {len(wl)} 个群）"
    return None


def _group_allowed(gid) -> bool:
    """该群是否允许 Bot 参与（逐群开关 + 白名单 + **P4 作用域能力**一并判定）。

    这是**全部发言出口的唯一判定点**（4 处调用：L7686 / L7710 / L7753 / L7775）。
    1.8 回复链路梳理里发现「该不该说话」的判定曾散落 3 处，修白名单时漏过 2 个出口
    —— 正因为散落；P4·S3 把新一层判定收敛到本函数，旧层保持原样。
    """
    if _group_block_reason(gid) is not None:
        return False
    # ── P4·S3：作用域注册表的 `reply` 能力（软依赖；开关默认关 → 零行为变化）──
    try:
        if not _config.get("capability_registry_enable", False):
            return True
        reg = _get_scope_registry()
        if reg is None:
            return True
        return bool(reg.is_allowed("group", gid, "reply"))
    except Exception as e:
        log(f"[scope] reply 能力判定失败（按允许处理）: {e}")
        return True


def _notice_group_blocked(gid, reason):
    """按冷却时间提示"某群被白名单挡住" —— 否则用户只会看到 Bot 不说话、无从排查。"""
    try:
        g = str(gid)
        now = time.time()
        if now - float(_GROUP_BLOCK_NOTICE.get(g) or 0) >= _GROUP_BLOCK_NOTICE_COOLDOWN:
            _GROUP_BLOCK_NOTICE[g] = now
            log(f"群[{g}] 不参与：{reason}")
    except Exception:
        pass


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


# ── 管理员通知：历史落盘（可视化数据源）────────────────────────────
# 此前 _notify_admin 是 fire-and-forget：发没发、发给谁、成功失败只有滚动的
# 日志行，WebUI 完全看不见（用户要求"通知可视化"）。现在每次投递（含被开关
# 跳过的）都落一条 admin_notify_log.json，通知页时间线 + 全局 toast 都读它。
_NOTIFY_LOG_LOCK = threading.Lock()
_NOTIFY_EVENT_ZH = {
    "friend_add": "新好友", "group_increase": "入群", "plugin_forge": "插件创作",
    "test": "测试", "": "通知",
}


def _notify_log_path():
    try:
        return os.path.join(str(data_store.get_data_dir()), "admin_notify_log.json")
    except Exception:
        return ""


def _notify_log(entry):
    """追加一条通知记录（原子写，保留最近 200 条）。失败只记日志，绝不影响投递。"""
    p = _notify_log_path()
    if not p:
        return
    try:
        with _NOTIFY_LOG_LOCK:
            rows = []
            if os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        rows = json.load(f)
                    if not isinstance(rows, list):
                        rows = []
                except Exception:
                    rows = []
            rows.append(entry)
            rows = rows[-200:]
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False)
            os.replace(tmp, p)
    except Exception as e:
        log(f"[通知] 历史记录写入失败（不影响投递）: {e}")


def _notify_admin(text: str, event_key: str = ""):
    """事件通知：加好友/加群/插件创作 等自动私聊管理员，并落一份投递历史。
    event_key = friend_add / group_increase / plugin_forge / test。"""
    rec = {"id": "%d-%d" % (int(time.time() * 1000), len(text) % 100),
           "ts": time.time(), "event_key": event_key or "",
           "event": _NOTIFY_EVENT_ZH.get(event_key or "", event_key or "通知"),
           "text": str(text)[:300], "status": "", "results": []}
    try:
        nc = _config.get("notify_admin")
        if not isinstance(nc, dict):
            nc = {}
        if not nc.get("enabled", True):
            rec["status"] = "skipped_disabled"
            _notify_log(rec)
            return
        if event_key and nc.get(event_key, True) is False:
            rec["status"] = "skipped_switch"
            log(f"[通知] 事件 {event_key} 的开关已关闭，跳过: {text[:40]}")
            _notify_log(rec)
            return
        admins = [str(q) for q in (nc.get("admin_qqs") or _config.get("admin_qqs") or [])]
        admins = [q for q in admins if q.strip()]
        if not admins:
            rec["status"] = "skipped_no_admin"
            log(f"[通知] 未配置管理员 QQ，跳过通知: {text[:40]}")
            _notify_log(rec)
            return
        ok_n = 0
        for qq in admins:
            r = {"qq": qq}
            try:
                resp = api_call("send_private_msg", {"user_id": int(qq), "message": text})
                code = resp.get("retcode") if isinstance(resp, dict) else resp
                r["ok"] = (code == 0)
                r["retcode"] = code
                log(f"[通知] 已通知管理员 {qq} retcode={code}")
            except Exception as e:
                r["ok"] = False
                r["error"] = str(e)[:120]
                log(f"[通知] 通知管理员 {qq} 失败: {e}")
            rec["results"].append(r)
            if r.get("ok"):
                ok_n += 1
        rec["status"] = "sent" if ok_n == len(admins) else ("partial" if ok_n else "failed")
    except Exception as e:
        rec["status"] = "failed"
        rec["results"].append({"qq": "", "ok": False, "error": str(e)[:120]})
        log(f"[通知] 通知异常: {e}")
    finally:
        _notify_log(rec)


def _notify_log_read(limit=50, since=0.0):
    """读通知历史（新→旧）。since>0 时只返回该 ts 之后的（toast 增量用）。"""
    p = _notify_log_path()
    rows = []
    try:
        if p and os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                rows = json.load(f)
            if not isinstance(rows, list):
                rows = []
    except Exception:
        rows = []
    if since:
        try:
            rows = [r for r in rows if float(r.get("ts", 0)) > float(since)]
        except Exception:
            pass
    rows = sorted(rows, key=lambda r: r.get("ts", 0), reverse=True)
    try:
        n = int(limit)
    except Exception:
        n = 50
    return rows[:max(1, min(200, n))]


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
            welcome = aa.get("welcome_private") or f"你好，我是{_bot_nicknames()[0]}～"
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


def _handle_group_ban(ev):
    """群禁言/解除禁言：只记录，不发送、不调用 API（观测性补齐）。

    为什么需要它：OneBot 的 `notice_type == "group_ban"` 在事件分发链里
    此前**没有对应分支**（只有 group_increase / group_decrease / friend_add
    等），所以群里有人被禁言时后台没有任何痕迹，排查"为什么 bot 不说话"时
    会缺一块关键信息。

    语义（与测试期望一致，且刻意保守）：
      · 只写日志，含「群禁言」字样与时长（秒）
      · **不调用任何 API**：禁止/解除禁言是群管理动作，bot 不该有反应
      · duration == 0 → 解除禁言（OneBot 约定）
      · 缺 group_id → 直接返回（脏事件）
    """
    group_id = ev.get("group_id")
    if not group_id:
        return
    user_id = str(ev.get("user_id", ""))
    operator = str(ev.get("operator_id", ""))
    try:
        duration = int(ev.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0

    if duration > 0:
        log(f"[事件] 群禁言: group={group_id} user={user_id} "
            f"时长={duration}秒 操作者={operator}")
    else:
        log(f"[事件] 群解除禁言: group={group_id} user={user_id} 操作者={operator}")


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
        welcome = f"你好，我是{_bot_nicknames()[0]}～"  # 未配置 → 默认欢迎语
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
    """反戳：给戳你的人戳回去。

    ★ OneBot v11 没有标准"戳一戳"动作，各实现用**专用 action**：
      · SnowLuma: send_poke（群/私自动路由）/ friend_poke（好友拍一拍）
      · NapCat:   send_poke（群）/ friend_nudge（私聊）
      · LLOneBot: send_poke（群）
    实测教训（2026-10-10）：用 send_private_msg 发 poke **消息段**在
    SnowLuma 上不是戳一戳 —— type=0 渲染成空气泡、type=1/2 是"窗口抖动
    表情"（SnowLuma 文档原话：send_poke Action 才表示拍一拍）。段方案
    只作最后兜底（部分驱动上仍是有效动效）。
    """
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        uid = user_id
    gid = None
    try:
        gid = int(group_id) if group_id else None
    except (TypeError, ValueError):
        gid = None
    if gid:
        attempts = [("send_poke", {"group_id": gid, "user_id": uid})]
    else:
        attempts = [("send_poke", {"user_id": uid}),
                    ("friend_poke", {"user_id": uid}),
                    ("friend_nudge", {"user_id": uid})]
    for action, params in attempts:
        try:
            resp = api_call(action, params)
            code = resp.get("retcode") if isinstance(resp, dict) else resp
            if code == 0:
                log(f"[事件] 反戳 {action} user={user_id} group={group_id or '私聊'} retcode=0")
                return True
            log(f"[事件] 反戳 {action} retcode={code}（试下一个动作）")
        except Exception as e:
            log(f"[事件] 反戳 {action} 异常: {e}")
    # 兜底：poke 消息段（SnowLuma 上会渲染成窗口抖动，但比什么都没有强）
    try:
        msg = [{"type": "poke", "data": {"type": 0}}]
        if gid:
            resp = api_call("send_group_msg", {"group_id": gid, "message": msg})
        else:
            resp = api_call("send_private_msg", {"user_id": uid, "message": msg})
        code = resp.get("retcode") if isinstance(resp, dict) else resp
        log(f"[事件] 反戳(兜底段) user={user_id} group={group_id or '私聊'} retcode={code}")
        return code == 0
    except Exception as e:
        log(f"[事件] 反戳失败: {e}")
        return False


def _handle_poke_event(ev):
    """notice.notify.poke 事件：群戳 → 进群回复流水线；私戳 → AI 回复 + 登记私聊活跃会话。"""
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
        # 群戳一戳 → 进群回复流水线（2026-10-25）。
        # 旧版这里自己拼 prompt、同步调模型、发送、再发一张表情包，全程不碰
        # 缓冲区 —— 同一秒内"戳 + 消息"会并行生成两条；主循环还被这 9 秒占住
        # （真机 17:05:22→17:05:31），事件收取跟着滞后。
        # 现在只做两件事：记一次计数（分级文案用）、把"被戳"当成一条入站事件。
        gid_str = str(poke_gid)
        # 戳一戳计数（同一人同一群，poke_timeout 内累计）
        _poke_key = f"{gid_str}_{poke_uid}"
        _poke_count = POKE_COUNTS.get(_poke_key, {"count": 0, "time": 0})
        if time.time() - _poke_count["time"] > _config.get("poke_timeout", 600):
            _poke_count = {"count": 0, "time": 0}
        _poke_count["count"] += 1
        _poke_count["time"] = time.time()
        POKE_COUNTS[_poke_key] = _poke_count
        poke_n = _poke_count["count"]
        # forced=False 是关键：这条要过群聊决策器（[决策放行] / [跳过] / [观察]）。
        # 当成会话内消息（forced=True）就整段跳过决策，只剩生成端的兜底。
        # 会话额度也不在这里写：等这一批真的发出去之后由 _pipeline_step 补，
        # 否则决策器 skip 了也会白送 2 轮免 @ 追问权。
        #
        # ★ 不手写 `[{昵称}]`（2026-10-25）：_pipeline_upsert 建批/并批时会统一加
        #   `[群名片]`，这里再写一遍就变成 `[DeepSeek v41f] [Texas] （戳了戳你…）`。
        #   线上 20:19 模型据此认定"戳我的是另一个人"，还解释"我收到的原文"。
        #   名字只由 _display_name 给一次（群名片优先）。
        _pipeline_upsert(
            gid_str, sender=poke_uid,
            text=f"（戳了戳你，这是第 {poke_n} 次）",
            forced=False, source="poke", poke_uid=poke_uid)
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
        if time.time() - _poke_count["time"] > _config.get("poke_timeout", 600):
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
            f"你是{_bot_nicknames()[0]}，18岁男生，毒舌带撒娇。{poke_nick}私聊戳了你。\n"
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
                # ★ 单一大脑修复：私聊戳回复也补进 chat_history（与主链 9276 同格式），
                # 否则私聊下一轮同样看不到"我刚说过什么"。
                if poke_qq:
                    _pch = chat_history.setdefault(poke_qq, [])
                    _pch.append({"role": "user", "content": "（戳了戳你）",
                                 "ts": time.time()})
                    _pch.append({"role": "assistant", "content": reply[:500],
                                 "ts": time.time()})
                    chat_history[poke_qq] = _pch[-24:]
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
        # 清单 3.1：与 Timer 线程/消息线程并发 → get+cancel+pop 必须在一个临界区里
        with _buffer_lock:
            pending_buf = GROUP_MSG_BUFFER.get(str(recall_gid))
            if pending_buf:
                if pending_buf.get("timer"):
                    pending_buf["timer"].cancel()
                GROUP_MSG_BUFFER.pop(str(recall_gid), None)
                log(f"[撤回] 群[{recall_gid}] 已取消未处理缓冲区，截断AI回复")
        buffer_cancelled = bool(pending_buf)

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
        # NT 协议私聊推送的是 u_xxx 随机 UID，而缓冲区按真实 QQ 号做 key
        # （入口在 sender 上换过一次）。不归一化就永远 pop 不到 → 这条取消是空转。
        if _pkey.startswith("u_") and TARGET_QQS:
            _pkey = str(TARGET_QQS[0])
        # 撤回时取消该用户未处理的合并批次（截断即将发出的回复）。
        # 群聊那条分支一直有这一步（GROUP_MSG_BUFFER），私聊新增合并后必须同步有：
        # 否则撤回发生在合并窗口内（默认 5 秒）时，缓冲区照样到点合并、照样回复，
        # 用户会收到"对已撤回内容的回复"+"撤回了？我还没看到"两条自相矛盾的话。
        # 放在会话门槛之前 —— 门不通过也要丢掉，撤回就是不想让这条被处理。
        _cancel_private_buffer(_pkey)
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

#: 群管理动作 → 中文（回执用）
_ADMIN_LABELS = {
    "kick": "踢了", "ban": "禁了", "unban": "解了",
    "set_admin": "给管理了", "del_admin": "撤了",
    "set_card": "名片改了", "set_group_name": "群名改了", "leave": "走了",
}

# ── 群名记忆（2026-10-27）──────────────────────────────────────────
#: 记群名历史 —— 否则「把群名改回去」这类指令无从下手：
#:   用户说「改回去」，目标值是个**指代**（「回去」「原来」「之前那个」），
#:   不是具体名字。机器人不知道原来叫什么，就只能瞎填或反问。
#:
#: 语义：历史列表 **[0]=当前群名，[1]=上一个群名**。
#:   「改回去」= 改成 [1]。
#:
#: 为什么要 [1] 而不是"我改之前那个"：
#:   群名不一定是 bot 改的 —— 用户自己也改。真机（群 829854136）：
#:     bot 把「高浓度神金矿区」改成「高浓度神金**矿产区**」（模型多填一个字）
#:     → 用户自己改回「高浓度神金矿区」
#:   此时"原来的名字"是「高浓度神金矿产区」（bot 改坏的那个），
#:   而"用户自己改之前"的是它自己。
#:   按 [1] 记：读到「高浓度神金矿区」时列表变成
#:     [高浓度神金矿区, 高浓度神金矿产区] → 「改回去」还原成后者 ✓
#:
#: 真机（20:34）：用户说「额，把群名改回去」→ 机器人没执行（被 observe 掉了），
#:   用户接着说「把群名改成高浓度神金矿区」才推进。
_GROUP_NAME_HISTORY: Dict[str, List[str]] = {}
_GROUP_NAME_LOCK = threading.Lock()

#: 群名历史落盘位置 —— 内存版重启就没了，"改回去"会跟着失忆
_GROUP_NAME_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "plugins", "state", "group_names.json")

#: 指代词 —— value 是这些词时，意思是"改回原来的名字"
_NAME_BACKREF = ("回去", "回来", "原来", "原来的", "之前", "先前", "旧名", "老名字")


def _load_group_names() -> None:
    """从 state 文件恢复群名历史。失败就当没有 —— 不影响主链。"""
    try:
        with open(_GROUP_NAME_STATE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            with _GROUP_NAME_LOCK:
                for k, v in data.items():
                    if isinstance(v, list):
                        _GROUP_NAME_HISTORY[str(k)] = [str(x) for x in v][:5]
    except Exception:
        pass


def _save_group_names() -> None:
    """群名历史落盘。失败不抛 —— 存不住只是下次失忆，不该连累改群名。"""
    try:
        with _GROUP_NAME_LOCK:
            data = {k: list(v)[:5] for k, v in _GROUP_NAME_HISTORY.items()}
        os.makedirs(os.path.dirname(_GROUP_NAME_STATE), exist_ok=True)
        with open(_GROUP_NAME_STATE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _remember_group_name(gid: str, name: str) -> None:
    """记录一个群名到历史（最新在前，最多留 5 个）。"""
    n = str(name or "").strip()
    if not n:
        return
    with _GROUP_NAME_LOCK:
        lst = _GROUP_NAME_HISTORY.setdefault(str(gid), [])
        if lst and lst[0] == n:
            return                      # 同一个名，不动历史
        if n in lst:
            lst.remove(n)
        lst.insert(0, n)
        del lst[5:]
    _save_group_names()


def _last_group_name(gid: str) -> str:
    """这个群的**上一个**群名（「改回去」的目标）；没有记录返回空串。"""
    with _GROUP_NAME_LOCK:
        lst = _GROUP_NAME_HISTORY.get(str(gid)) or []
        return lst[1] if len(lst) >= 2 else ""


def _is_name_backref(value: str) -> bool:
    """value 是不是"改回去"这种指代（而不是具体名字）。"""
    v = str(value or "").strip()
    if not v:
        return False
    # 去掉"改""成""到""把""群名"等壳，只看剩下的核心词
    core = re.sub(r"(群名|群|改|成|到|把|换|一下|名字)", "", v).strip()
    if not core:
        core = v
    return any(w in core for w in _NAME_BACKREF)


def _read_group_name(gid: int, no_cache: bool = True):
    """读当前群名。成功返回 (True, 名字)，失败返回 (False, 原因)。

    ★ no_cache 必须为 True（2026-10-27）：
      `get_group_info` 在 OneBot 侧**带缓存** —— 改完群名立刻读，
      读到的还是改之前的旧名。
      实测：改成功（群里已是「干净验证A」），但 get_group_info 返回
      「高浓度神金矿产区」，get_group_info(no_cache=True) 才返回新名。
      用带缓存的读做回读校验，会把**成功**误判成失败。
    """
    try:
        _body = {"group_id": int(gid)}
        if no_cache:
            _body["no_cache"] = True
        resp = api_call("get_group_info", _body)
    except Exception as e:
        return (False, f"读不到群信息：{e}")
    if not isinstance(resp, dict):
        return (False, "读群信息返回格式不对")
    if resp.get("retcode") not in (0, None):
        return (False, f"读群信息 retcode={resp.get('retcode')}")
    data = resp.get("data") or {}
    name = str(data.get("group_name") or "").strip()
    if not name:
        return (False, "读群信息没返回群名")
    # ★ 每读到一次就更新历史 —— 群名不一定是 bot 改的，用户自己也改。
    #   不更新的话，「改回去」会还原到一个早就不是"上一个"的名字。
    try:
        _remember_group_name(str(gid), name)
    except Exception:
        pass
    return (True, name)


# 启动时把群名历史读回来（内存版重启就失忆，"改回去"会跟着失忆）
try:
    _load_group_names()
except Exception:
    pass


def _do_group_admin(action, *, target="", value="", sender="", group_id="",
                    user_msg=""):
    """群管理动作的统一执行层：正则路径与 agent 工具路径共用。

    返回 (ok, msg)。msg 直接可以回给群 —— 成功、失败、没权限都说人话。

    为什么要抽这一层（1.5.9 真机 2026-10-27）：
      用户说「把群名改成外莱塔尼亚」，正则要求**开头**就是动词，匹配不上，
      于是进了 AI 流程；而 AI 手上没有任何管理工具，只能回一句话，然后被
      过时丢弃 —— 群里什么都没发生。
      现在自然语言走工具、命令式走正则，两条路都落到这里，权限只在这里判。
    """
    if not _config.get("admin_commands_enable", True):
        return (False, "管理指令关着，让主人去设置里开")
    admins = set(str(q) for q in (_config.get("admin_qqs") or []))
    admins |= _get_admin_qqs()
    if str(sender) != str(BOT_QQ) and str(sender) not in admins:
        return (False, "这得主人发话")
    if not group_id or not str(group_id).isdigit():
        return (False, "这不是群聊，没法执行群管理")
    gid = int(group_id)
    target = str(target or "").strip()
    value = str(value or "").strip()

    def _call(api, body):
        try:
            resp = api_call(api, body)
        except Exception as e:
            return (False, f"{_ADMIN_LABELS.get(action, '操作')}失败：{e}")
        rc = resp.get("retcode") if isinstance(resp, dict) else resp
        if rc == 0:
            return (True, _ADMIN_LABELS.get(action, "办了"))
        return (False, f"{_ADMIN_LABELS.get(action, '操作')}没办成，驱动返回 retcode={rc}")

    if action in ("kick", "ban", "unban", "set_admin", "del_admin", "set_card"):
        if not target:
            return (False, "要@人或给QQ号")
        if not target.isdigit():
            return (False, f"这个人没解析出号码（{target}）")
        uid = int(target)
    if action == "kick":
        return _call("set_group_kick", {"group_id": gid, "user_id": uid})
    if action == "ban":
        try:
            minutes = int(value or 10)
        except Exception:
            minutes = 10
        return _call("set_group_ban", {"group_id": gid, "user_id": uid,
                                       "duration": minutes * 60})
    if action == "unban":
        return _call("set_group_ban", {"group_id": gid, "user_id": uid, "duration": 0})
    if action == "set_admin":
        return _call("set_group_admin", {"group_id": gid, "user_id": uid, "enable": True})
    if action == "del_admin":
        return _call("set_group_admin", {"group_id": gid, "user_id": uid, "enable": False})
    if action == "set_card":
        if not value:
            return (False, "新名片是空的")
        return _call("set_group_card", {"group_id": gid, "user_id": uid, "card": value})
    if action == "set_group_name":
        if not value:
            return (False, "新群名是空的")
        # ── 「改回去」这类指代：换成记住的原名 ──
        #   _from_backref：还原出来的值**不**再过"出自原话"那道校验 ——
        #   原话里当然没有"原群名"这三个字，它是我们自己记下来的。
        _from_backref = False
        if _is_name_backref(value):
            _prev = _last_group_name(gid)
            if not _prev:
                return (False, "我不记得这群原来叫什么了，你直接说要改成什么")
            value = _prev
            _from_backref = True
        # ── ★ 执行前校验：新群名必须是用户原话里出现过的（2026-10-27）──
        #   group_admin 的 value 是**模型自己填的参数**，模型会顺手润色：
        #     用户：「把群名改成高浓度神金矿区」
        #     模型填：「高浓度神金矿产区」（多一个"产"）
        #   真机就是这么把群名改错的，而且 retcode=0 → 报"✅ 群名改了"。
        #   回读校验只能发现"没改成"，拦不住"改成了别的"——
        #   那会把用户没要的名字写上去。所以这里先拦一道。
        if user_msg and not _from_backref:
            if value not in str(user_msg):
                return (False, f"你要改成的名字是「{value}」，"
                               f"但你原话里没这么写 —— 说清楚要改成什么我再动")
        # ── 改之前先读当前群名并记住 ──
        #   一是给「改回去」留底，二是回读校验时能对照。
        _ok_read, _cur = _read_group_name(gid)
        if _ok_read and _cur != value:
            _remember_group_name(gid, _cur)
        # ── 执行 ──
        _ok_set, _msg = _call("set_group_name",
                              {"group_id": gid, "group_name": value})
        if not _ok_set:
            return (False, _msg)
        # ── ★ 回读校验（2026-10-27）──
        #   retcode=0 只说明**请求被接受**，不代表群名真改成了这个值。
        #   真机：用户要「高浓度神金矿区」，实际改成了「高浓度神金矿产区」
        #   （模型自己填的参数多一个字），机器人却报「✅ 群名改了」。
        #   不回读就永远发现不了 —— 而"发现不了"正是用户投诉的那件事。
        #
        #   两处细节，缺一个就会误判（2026-10-27 实测）：
        #     · 读必须带 no_cache —— 否则读到的是 OneBot 侧的旧缓存，
        #       会把**成功**误判成失败（实测改成功了，get_group_info 仍返回旧名）
        #     · 要重试几次 —— QQ 侧生效不是瞬时的
        _now = ""
        _ok2 = False
        for _d in (0.0, 0.4, 1.0):
            if _d:
                time.sleep(_d)
            _ok2, _now = _read_group_name(gid)
            if _ok2 and _now == value:
                break
        if _ok2:
            if _now == value:
                return (True, _ADMIN_LABELS.get(action, "办了"))
            # ★ 没生效 —— 说清原因，别只说"没改成"（2026-10-27）
            #   真机：set_group_name 返回 retcode=0 但群名没变（QQ 侧拒绝）。
            #   原因多半是身份不够：bot 是 admin 不是 owner。
            #   查一次自己的角色，把"为什么不行"说清楚，
            #   否则用户只能自己去猜。
            _role = ""
            try:
                _role = _bot_group_role(str(gid))
            except Exception:
                _role = ""
            if _role and _role != "owner":
                return (False, f"没改成，现在这群还叫「{_now}」"
                               f"（我不是群主，QQ 那边不让改，得群主来）")
            return (False, f"没改成，现在这群还叫「{_now}」")
        # 回读失败（网络/接口问题）——不能因此说成功
        return (False, f"{_ADMIN_LABELS.get(action, '操作')}的返回是成功了，"
                       f"但我没能回读确认，不确定到底改没改")
    if action == "leave":
        return _call("set_group_leave", {"group_id": gid})
    return (False, f"不认识的管理动作：{action}")


#: 群管理指令正则：动词表来自 capabilities_view，避免两处各写一份。
#: 按长度倒序，保证「踢人」不被「踢」先吃掉（原顺序有这个 bug：
#: 输入「踢人 10001」时 verb=踢、rest=人 10001，目标号仍能捞到，
#: 但「解除禁言」这类只要前缀本身是动词就会错位）。
_ADMIN_COMMAND_RE = re.compile(
    r'^(' + '|'.join(sorted((re.escape(v) for v in capabilities_view.ADMIN_VERBS),
                            key=len, reverse=True)) + r')(.*)$')

#: ★ 自然语言句式（2026-10-27）：「把群名改成 X」「群名改成 X」「改下群名 X」
#:
#:   为什么群名要单独走这条路（其他管理动作不走）：
#:     group_admin 工具的 value 是**模型自己填的参数**，模型会顺手润色 ——
#:       用户：「把群名改成高浓度神金矿区」
#:       模型填：「高浓度神金矿产区」（多一个"产"）→ 群名真被改错了
#:     而且它要过决策器，「把群名改回去」这种请求会被判 observe（真机 20:34），
#:     整批直接结束 —— 明确的操作请求不该由"要不要插话"来裁决。
#:
#:   群名的参数**就是那句话本身**，正则完全够用，不需要 AI 理解。
#:   走正则：参数出自原文（不会改写）、不经决策器（不会被 observe）。
_ADMIN_NAME_NL_RE = re.compile(
    r'^(?:请\s*)?(?:把\s*)?(?:群\s*名|群名称)\s*'
    r'(?:改成|改为|换成|换为|整成|叫成|设为|设成|修改成|修改为|弄成|搞成)\s*'
    r'(.+?)\s*[。！!？?，,、]?\s*$'
)

#: 指代式（2026-10-27）：「把群名改回去」「群名换回来」「群名恢复原名」
#:
#:   单独一个模式，因为这类句子里**没有具体名字** ——
#:   目标值是「改回之前那个」，得从记住的原名里取。
#:   若并进上面的模式，「改回去」会被切成「去」（动词吃掉「改回」）。
_ADMIN_NAME_BACKREF_RE = re.compile(
    r'^(?:请\s*)?(?:把\s*)?(?:群\s*名|群名称)\s*'
    r'(?:改回|改还|换回|恢复|退回)\s*'
    r'(?:去|来|原来|原名|之前|先前|以前)?\s*[。！!？?，,、]?\s*$'
)


def _handle_group_admin_command(text, msg, sender, group_id):
    """@Bot + 群管理指令（命令式）：踢人/禁言/设管理/改名片/改群名/退群。

    只认**开头就是动词**的命令式（「踢 1234567」「改群名 新名字」）。
    自然语言（「把群名改成 X」「禁言他十分钟」）不在这里处理，走 `group_admin`
    工具 —— 两条路最终都落到 `_do_group_admin`，权限只在那里判一次。
    """
    if not _config.get("admin_commands_enable", True):
        return False
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
    # ── 自然语言群名句式（2026-10-27）—— 先试这个 ──
    #   「把群名改成 X」不是"开头就是动词"，走不了下面的 _ADMIN_COMMAND_RE。
    #   而它若落到 AI 流程：参数由模型填（会改写）、还要过决策器（会被 observe）。
    #   群名的参数就是那句话本身，正则直接取，不经这两道。
    # 指代式优先（「把群名改回去」—— 没有具体名字，目标从记住的原名取）
    _bl = _ADMIN_NAME_BACKREF_RE.match(cmd)
    _nl = _ADMIN_NAME_NL_RE.match(cmd)
    if _bl or _nl:
        _new_name = (cmd if _bl else (_nl.group(1) or "")).strip()
        if not _new_name:
            return False
        _ok_n, _msg_n = _do_group_admin(
            "set_group_name", value=_new_name, sender=sender,
            group_id=group_id, user_msg=cmd)
        log(f"群[{group_id}] [群管理·自然语言] 群名改成「{_new_name}」→ "
            f"{'成功' if _ok_n else '没成：' + str(_msg_n)}")
        return True
    m = _ADMIN_COMMAND_RE.match(cmd)
    if not m:
        return False
    verb, rest = m.group(1), (m.group(2) or "").strip()

    if verb in ("踢", "踢出", "踢人"):
        action = "kick"
    elif verb == "禁言":
        action = "ban"
    elif verb in ("解除禁言", "解禁"):
        action = "unban"
    elif verb in ("设管理", "设管理员"):
        action = "set_admin"
    elif verb in ("撤管理", "撤销管理"):
        action = "del_admin"
    elif verb == "改名片":
        action = "set_card"
    elif verb == "改群名":
        action = "set_group_name"
    elif verb == "退群":
        action = "leave"
    else:
        return False

    # 目标 QQ：优先 at 段，其次文本中的号码
    at_targets = []
    for seg in (msg.get("message") or []):
        if isinstance(seg, dict) and seg.get("type") == "at":
            d = seg.get("data", {}) or {}
            q = str(d.get("qq", "") or d.get("user_id", "") or "")
            if q and q != "all" and q != str(BOT_QQ):
                at_targets.append(q)
    rest_digits = re.search(r'\d{5,12}', rest)
    target = at_targets[0] if at_targets else (rest_digits.group(0) if rest_digits else "")

    value = ""
    if action == "ban":
        mm = re.search(r'(\d+)\s*(小时|分钟|分)', rest)
        if mm:
            value = str(int(mm.group(1)) * 60 if mm.group(2) == "小时" else int(mm.group(1)))
    elif action == "set_card":
        value = re.sub(r'@\s*', '', re.sub(r'\d{5,12}', '', rest)).strip()
    elif action == "set_group_name":
        value = rest.strip()

    ok, out = _do_group_admin(action, target=target, value=value,
                              sender=sender, group_id=group_id,
                              user_msg=cmd)
    log(f"[群管理] {action} group={group_id} target={target} ok={ok} msg={out}")
    if action == "leave" and ok:
        return True          # 退群后已不在群里，发不出去
    if _config.get("admin_command_receipt", True):
        try:
            smart_send_group(str(group_id), out)
        except Exception:
            pass
    return True
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

# ── "这次调用是不是等到超时了"（2026-10-27）─────────────────────────────
# `call_deepseek` 超时返回 None，而模型自己回 `[不说话]` 时也可能是空 ——
# 上层原来只能写「AI决定不回复: (空)」，把"没等到"和"不想说"混成一句话。
# 真机后果：两次被 @ 提问没回，日志看着像模型主动沉默，实际是回复被超时丢弃，
# 于是被判断成"决策器有问题"，查错方向整个跑偏。
# 用线程本地保存：一次生成从头到尾在同一个线程里（worker / Timer 线程）。
_ai_timeout_flag = threading.local()


def _mark_ai_timeout(flag: bool) -> None:
    try:
        _ai_timeout_flag.timed_out = bool(flag)
    except Exception:
        pass


def _ai_timed_out() -> bool:
    """本线程最近一次 call_deepseek 是否以**超时**结束（而不是模型决定不回）。"""
    return bool(getattr(_ai_timeout_flag, "timed_out", False))


def _ai_worker() -> None:
    """后台线程：处理 DeepSeek API 调用，支持重试，不阻塞主循环。"""
    while True:
        task = _ai_task_queue.get()
        if task is None:  # 停止信号
            break
        task_id, params, event = task

        def _tick_progress(tid=task_id):
            # 告诉等待方"这一轮还在推进"。只更新一个时间戳，不加锁竞争（走同一把锁，
            # 但临界区极小），也不参与结果判定 —— 纯粹用来区分"慢"和"卡死"。
            try:
                with _ai_results_lock:
                    _e = _ai_results.get(tid)
                    if _e is not None:
                        _e["progress"] = time.time()
            except Exception:
                pass

        try:
            result = _call_deepseek_sync(
                user_msg=params["user_msg"],
                images=params.get("images"),
                target_qq=params.get("target_qq"),
                group_id=params.get("group_id"),
                is_group=params.get("is_group", False),
                private_memory_context=params.get("private_memory_context", ""),
                personality_temperature=params.get("personality_temperature"),
                ego_inject_context=params.get("ego_inject_context", ""),
                scene_context=params.get("scene_context", ""),
                sender=params.get("sender", ""),
                progress=_tick_progress)
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
        _ai_results[task_id] = {"event": event, "result": None,
                                "progress": time.time()}
    _ai_task_queue.put((task_id, params, event))
    return task_id


def _wait_ai_result(task_id: int, timeout: float = 60.0,
                    idle_timeout: float = None, max_total: float = None) -> Optional[str]:
    """等待指定 AI 任务的结果，超时返回 None。超时不移除 entry，后台任务完成后仍可被后续轮询/读取。

    ★ 2026-10-27 改判据：从「总共等 timeout 秒」改成「多久没进展」。
      真机（群 829854136，09:21 / 09:25 两次，都是被 @ 问「讲解一下精神分析引论」）：
        09:20:50 提交 → 09:20:58 历史摘要压缩完（8s，一次独立的 LLM 调用）
        09:21:09 首轮模型回来说"要搜索"（11s）→ 09:21:12 搜完（3s）
        09:21:20 **30 秒到点，放弃** → 日志「AI决定不回复: (空)」，流水线回 idle
        09:21:39 二轮模型终于返回（8712 tokens，¥0.004356）→ 结果没人要了
      一条已经生成好的回复被扔掉，钱照付，群里一句没有；而日志写的是
      "AI决定不回复"，看着像模型自己选择沉默 —— **两个完全不同的原因共用一句话**。

      根子不在 30 这个数字，而在判据本身：用"用户说了什么"预判耗时
      （`_is_web_task` 只看 URL / 问卷填表），而真正的耗时是"模型决定调不调工具"。
      现在改成按**进展**判：
        · worker 每完成一个阶段（摘要 / 每一轮模型 / 每一轮工具）打一次 progress
        · 等待方只要在 idle_timeout 内见过 progress 就续期，最多续到 max_total
        · 真卡死（一个 tick 都没有）仍然在 idle_timeout 后放弃，不会无限等
    """
    with _ai_results_lock:
        entry = _ai_results.get(task_id)
    if entry is None:
        _mark_ai_timeout(False)
        return None
    _idle = float(idle_timeout if idle_timeout is not None else max(timeout, 1.0))
    _hard = float(max_total if max_total is not None else max(timeout * 4.0, _idle * 2))
    started = time.time()
    hard_deadline = started + _hard
    deadline = started + timeout
    while True:
        # 等待粒度跟着 deadline 走：固定等 2s 会把 0.5s 这种短预算拖成 2s，
        # 也让"到点就停"失真（真机排查时会被误读成"根本没超时"）。
        _left = max(0.05, min(2.0, deadline - time.time()))
        if entry["event"].wait(timeout=_left):
            with _ai_results_lock:
                entry = _ai_results.pop(task_id, None)
            _mark_ai_timeout(False)
            return entry["result"] if entry else None
        now = time.time()
        if now >= hard_deadline:
            break
        # 最近 idle_timeout 内有过进展 → 续期（本次生成还没卡）
        last = entry.get("progress") or started
        if now - last < _idle:
            deadline = max(deadline, now + _idle)
        if now >= deadline:
            break
    _mark_ai_timeout(True)
    _elapsed = time.time() - started
    try:
        _gap = time.time() - (entry.get("progress") or started)
    except Exception:
        _gap = _elapsed
    log(f"AI 任务 {task_id} 等待超时（已等 {_elapsed:.0f}s，最后一次进展在 {_gap:.0f}s 前；"
        f"后台任务仍将继续执行，但本批已放弃）")
    return None


# 启动 AI 工作线程池（3并发，防止单条慢消息阻塞整个管道）
for _i in range(_AI_WORKERS):
    _t = threading.Thread(target=_ai_worker, daemon=True, name=f"ai-worker-{_i}")
    _t.start()

def summarize_group_history(group_id):
    """压缩超过窗口的群聊历史为摘要。

    ── P7 压缩事务化（不变量 A5：先记录标记、后做事；未匹配的 start 是持久锁）──
    原实现把 `_GROUP_SUMMARY_LEN = len(gh)` 写在**调 LLM 之前** —— 它兼职了「防并发」
    的作用（没有别的锁），但代价是：**LLM 失败/空返回时标记却立着**，于是下次调用
    会因「不足 10 条」而跳过压缩，旧历史可能长时间不再被压缩（真 bug）。
    现在拆成一次明确的事务：
      1. 置开始标记 `_GROUP_SUMMARY_START[gid]={at,len}`（同时承担防并发：新鲜标记 → 直接返回）
      2. 做事（调 LLM）
      3. 成功才提交 `_GROUP_SUMMARY_LEN` 并**清除开始标记**
      失败/空返回/异常 → **一定清除开始标记**（下次可重试）
      过期标记（>120s，通常是进程崩过）→ 视为未完成事务丢弃并重试
    """
    global group_chat_history, GROUP_SUMMARY
    gh = group_chat_history.get(group_id, [])
    if len(gh) <= 10:
        return
    # ── 事务开始：检查未完成的标记（防并发 + 崩溃恢复）──
    _txn = _GROUP_SUMMARY_START.get(group_id)
    if _txn:
        if time.time() - _txn.get("at", 0) < 120:
            return                      # 有新鲜的进行中标记 → 不重入（保留原有防并发语义）
        log(f"群[{group_id}] 发现过期的压缩标记"
            f"（{int(time.time() - _txn.get('at', 0))}s 未完成，疑似进程中断），丢弃并重试")
        _GROUP_SUMMARY_START.pop(group_id, None)
    # T2-4 节流：距上次压缩新增不足 10 条时不重复调用 LLM（避免每次群消息都触发额外 API 调用）
    _last_sum = _GROUP_SUMMARY_AT.get(group_id, 0)
    _sum_count = _GROUP_SUMMARY_LEN.get(group_id, 0)
    if _sum_count and len(gh) - _sum_count < 10 and time.time() - _last_sum < 3600:
        return
    _GROUP_SUMMARY_START[group_id] = {"at": time.time(), "len": len(gh)}
    try:
        _summarize_group_history_inner(group_id, gh)
    except Exception as e:
        log(f"群[{group_id}] 历史压缩失败: {e}")
    finally:
        # 事务收尾：无论成功失败都清开始标记（成功时长度标记已在 inner 里提交）
        _GROUP_SUMMARY_START.pop(group_id, None)


def _summarize_group_history_inner(group_id, gh):
    """压缩事务的「做事」部分：调 LLM 生成摘要并提交。只在成功后写长度标记。"""
    global group_chat_history, GROUP_SUMMARY
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
            lines.append(f"{_bot_nicknames()[0]}: {ai_msg[:80]}")
    dialog_text = "\n".join(lines[-20:])  # 最多取最近10组对话
    
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
        log("summarize: API 返回空choices（本次不提交，下次重试）")
        return
    summary = choices[0].get("message", {}).get("content", "").strip()
    if not summary:
        log("summarize: 摘要为空（本次不提交，下次重试）")
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
    # ★ 只有走到这里（摘要真的生成并落地）才提交长度标记 —— 这是"后做事"的那一半
    _GROUP_SUMMARY_LEN[group_id] = len(gh)
    log(f"群[{group_id}] 历史已压缩，摘要: {summary[:60]}")


def _finalize_reply(messages, max_tokens, scene, *, had_tools_rounds=0, progress=None):
    """工具轮用尽后的**强制收尾**：不带工具再要一次，只要文本。

    为什么必须去掉工具（2026-10-27 真机，群 829854136 17:32）：

      旧版收尾这一下传的仍然是 `with_tools=tools_for_call`，于是模型
      在"该说话"的这一刻**还以为自己能继续搜**。流里只有 tool_call 增量、
      没有 content → 返回空串 → 上层记「AI决定不回复: (空)」→ 群里一句话没有。

      日志实况：三轮 search_web 全命中（`达到上限 3 轮，收尾`）→ 7 秒后
      「AI决定不回复: (空)」。**搜索是成功的，失败在收尾这一步。**

      "收尾"的语义就是"现在只能说、不能做"。工具留在手里，模型就会继续做。
      去掉工具后，没有别的事可做，它只能把已经查到的东西说出来。

    参数
      had_tools_rounds  之前跑过几轮工具。>0 时追加一句收束指令：
                        告诉它"资料已经够了，直接说"，避免它拿空结果硬凑。
      progress          仍在推进的回调。收尾本身是**两次额外的模型调用**，
                        不报进展的话等待方会按"多久没动静"判定超时，
                        把一条马上要生成的回复砍掉。
    """
    def _tick():
        try:
            if progress:
                progress()
        except Exception:
            pass

    _tick()
    if had_tools_rounds > 0:
        messages = list(messages) + [{
            "role": "user",
            "content": ("【收尾】工具已经用完了，不能再查了。"
                        "现在直接用上面已经拿到的内容回答对方；"
                        "哪部分没查到就如实说没查到，别编。"
                        "给一句短的就行。"),
        }]
    try:
        out = _stream_chat_lines(messages, runtime_cfg=None, max_tokens=max_tokens,
                                 with_tools=False, scene=scene)
    except Exception as e:
        log(f"[收尾] 流式收尾失败（{e}），退回同步调用")
        try:
            msg, _u = _call_chat_sync(messages, runtime_cfg=None, max_tokens=max_tokens,
                                      stream=False, with_tools=False)
            out = _mark_truncated(str(msg.get("content") or "").strip(), False)
        except Exception as e2:
            log(f"[收尾] 同步兜底也失败: {e2}")
            return ""
    if str(out or "").strip():
        return out

    # ── 第二轮：还是空的，换一种说法再要一次 ────────────────────────────
    #   第一轮可能被"你的任务是把查到的整理出来"这类残留指令带偏，
    #   或者仍在输出思维链。这一轮把要求压到最小、把话题钉死：
    #   只说一句、必须说、不许再说工具（工具本来也没给）。
    #   只做一次 —— 两次都空就是真要说不了，留给上层如实记日志。
    log("[收尾] 第一轮无文本，改用最小指令再要一次")
    _tick()
    try:
        # 取**原始问题**，不能取最后一条 user ——
        # had_tools_rounds>0 时最后一条是我刚追加的【收尾】指令，
        # 拿它当"对方说"等于让模型回答自己（第一版就这么错的，
        # 日志里会看到「对方说：【收尾】工具已经用完了…」）。
        _last_user = ""
        for _m in reversed(messages):
            if _m.get("role") != "user":
                continue
            _c = str(_m.get("content") or "")
            if "【收尾】" in _c or _c.startswith("[系统提示]"):
                continue
            _last_user = _c[:300]
            break
        # 整段历史里一条真·用户消息都没有（极端情况）→ 退回用最长的 user 段
        if not _last_user:
            _cands = [str(_m.get("content") or "") for _m in messages
                      if _m.get("role") == "user"]
            _last_user = (max(_cands, key=len) if _cands else "")[:300]
        _retry_msgs = [
            {"role": "system",
             "content": "你正在 QQ 聊天里回一句话。下面只是资料，不是让你做任务。"},
            {"role": "user",
             "content": f"对方说：{_last_user}\n\n"
                        "就这一句，回一句最短的话。别再搜索、别解释过程、别列条目。"},
        ]
        out = _stream_chat_lines(_retry_msgs, runtime_cfg=None, max_tokens=max_tokens,
                                 with_tools=False, scene=scene)
    except Exception as e:
        log(f"[收尾] 第二轮失败: {e}")
        out = ""
    if not str(out or "").strip():
        log("[收尾] 两轮都没有文本 —— 这不是模型决定沉默，是收尾拿不到内容")
    return out


def _should_continue_tool_round(round_no: int, ok_flags, max_plain_rounds: int) -> bool:
    """这一轮工具执行完之后，还要不要再给模型一轮。

    抽成独立函数是为了能测（2026-10-23）：原先是内联在
    `_call_chat_with_tools` 的循环里的三个 if，而那个函数体量太大、
    依赖一大堆运行时状态，测不到；而这里的判据错一个符号，
    表现只是"多烧一轮 token"或"少查一次就硬答"，日志里都正常。

    规则
      round_no       本轮是第几轮（进来前已 +1）
      ok_flags       本轮每个工具的成功情况 [bool, ...]
      max_plain_rounds 上限；1 = 退回旧行为（一轮就收尾）

    判据是**这一轮有没有拿到东西**：全部失败就没必要再耗一轮，
    模型手上什么都没有，再问一遍只会得到同样的失败。
    """
    try:
        limit = int(max_plain_rounds)
    except Exception:
        limit = 3
    if limit < 1:
        limit = 1
    flags = list(ok_flags or [])
    if not any(bool(f) for f in flags):
        return False
    try:
        return int(round_no) < limit
    except Exception:
        return False


def _ctx_over_budget(messages, ratio: float = 0.8):
    """估算当前 messages 体量，判断是否已吃满上下文预算（总量闸）。

    为什么需要（2026-10-21）：
      工具轮会不断往 messages 里追加 tool 结果。单轮预算看起来不大，但 20 轮累加
      （`MAX_WEB_ROUNDS`）+ 模型按 spill 路径去读全文，仍可能把上下文推到窗口上限。
      一旦撞上去，上游回 400，而 400 在 `harness.retry_policy` 里是**不可重试**的
      → 整条回复直接丢，用户只看到兜底失败消息。
      所以在工具结果入账后先探一次：逼近预算就停止工具轮、强制收尾。

    返回 (over, used_chars, limit_chars)。字符口径与 harness.spill 一致（1 token≈1.6 字符）。
    绝不抛异常：拿不到模型能力就当作"没超"。
    """
    try:
        try:
            import model_caps
        except Exception:
            model_caps = None
        budget = model_caps.prompt_budget_chars() if model_caps else 104857
        limit = max(1024, int(budget * float(ratio)))
        used = 0
        for m in (messages or []):
            used += len(json.dumps(m, ensure_ascii=False))
        return used >= limit, used, limit
    except Exception:
        return False, 0, 0


# ── 历史按 token 预算截断（2026-10-23）──────────────────────────────
#  为什么不能只按"条数"截断：
#    同是 4 轮原文，一句「嗯」占 1 字，一条网页工具结果占 3000 字 ——
#    条数相同，体积差 3000 倍。实测（2026-10-23）最近 4 轮 = 8 条原文，
#    每条 50 字时共 250 token，每条 1500 字时共 7500 token。
#    条数这个量纲在这里根本不成立。
#
#  为什么阈值要分「自己的话」和「别人的话」：
#    别人说什么只是背景，压到 40 字够看出在聊什么；
#    自己说过什么决定立场一致性，砍断会上下矛盾（照抄 _entry_line 的比例）。
#    这两个比例只在**账本摘要**里用（见 _entry_line 的 _limit / _OWN_LIMIT），
#    原文区一律用下面的 HISTORY_RECENT_CHARS_PER_ENTRY。
HISTORY_RECENT_CHARS_PER_ENTRY = 1500   # 单条原文上限，超出按句截断


def _clip_for_history(text, limit):
    """按句截断，不硬切。

    ★ 硬切会把一句话切成半截（「我明天要去上海看展」→「我明天要去上」），
      模型读到残缺句子会顺着补全，反而更容易编。
      优先在句末标点处断；找不到才退到逗号；再找不到才硬切。
    """
    s = str(text or "")
    if len(s) <= limit:
        return s
    head = s[:limit]
    for marks in ("。", "！", "？", "\n", "，", "、", " "):
        i = head.rfind(marks)
        # 至少留下 60% 的预算，否则一路退到逗号会把内容砍没
        if i >= limit * 0.6:
            return head[:i + 1]
    return head


def _budget_recent_history(entries, budget_chars):
    """按总字符预算裁剪最近几轮的原文。

    返回 (保留的条目列表, 被裁掉几条)。**只从头部丢整条**，不裁中间 ——
    历史是对话，中间挖掉一条会让问答错位（问句在、答句没了）。

    为什么要"从头部丢"而不是"压缩剩下的"：
      压缩会改文本内容，而最近几轮本来就每轮都在滑（必 miss 区），
      再叠一层压缩只是白花 CPU，省不下缓存。直接丢最旧的整条更干净。

    保底：至少留最后 1 条（当前这一轮的上下文不能为空）。
    """
    ents = list(entries or ())
    if not ents:
        return [], 0
    total = sum(len(str(e.get("content") or "")) for e in ents)
    dropped = 0
    # 从最旧的开始丢，但绝不丢到最后一条
    while total > budget_chars and len(ents) > 1:
        old = ents.pop(0)
        total -= len(str(old.get("content") or ""))
        dropped += 1
    return ents, dropped


def _hist_time_prefix(entry) -> str:
    """历史条目的绝对时间锚；老条目没有 ts 时返回空串。

    模型没有时间锚时只能估：线上实测把 3 分钟前说的话讲成「二十分钟前」
    （2026-10-25）。时间戳在写入 history 时记死，这里只做格式化 ——
    同一轮重复渲染得到同一字符串，不会抖动缓存前缀。
    """
    try:
        ts = float((entry or {}).get("ts") or 0)
    except Exception:
        ts = 0.0
    if ts <= 0:
        return ""
    return datetime.datetime.fromtimestamp(ts).strftime("[%m-%d %H:%M] ")


def _build_cached_messages(history: List[Dict], cache_key: str,
                           static_prompt: str, dynamic_parts: List[str],
                           user_msg: str, images: Optional[List[str]] = None,
                           stable_parts: Optional[List[str]] = None) -> List[Dict]:
    """构建缓存优化的 messages 列表。

    结构（2026-10-13 二次重排）：
      static_prompt(永远不变) → **stable_parts(准静态)** → 历史摘要(append-only 账本)
      → 最近N轮(每轮滑动) → dynamic_parts(每轮变) → 当前消息
    DeepSeek 缓存：前缀相同的 token 只收 10%。最近N轮每轮都变，
    所以**任何放在它后面的块永远不可命中** —— 群身份/粗粒度时间/表达模式/
    能力档位/群背景摘要这些"一天变不了几次"的块必须挪到它前面（stable_parts）。
    stable 排在账本**之前**：账本每 1-2 轮追加一次，stable 一天变一次 ——
    越靠前的块变化越慢，追加/变更造成的 miss 面越小。

    ★ 摘要为什么必须是"账本"而不是"每次重算"（2026-10-13 成本事故）：
      history 是滑动窗口（gh[-10:]），每轮头部掉一条。旧实现每次从当前
      窗口重建摘要 → 文本逐字节对不上 → 缓存断在 static_prompt 之后
      （实测命中额钉死 ~3200）。账本模式下每条历史只入账一次、行永不
      清除/重排，窗口滑动只是尾部追加 → 命中区从 3.2K 扩到"static+全部摘要"。
    """
    RECENT_TURNS = 4  # 最近几轮保留原文（变化部分）
    # ★ 2026-10-21 修：下面三处切片用的都是"条数"，而 history 是
    #   [user, assistant, user, assistant…] —— 直接切 4 条其实只有 **2 轮**原文，
    #   其余全被压成摘要行。变量名/注释说 4 轮，行为是 2 轮，属名实不符。
    #   一轮=两条，所以这里显式换算；成本靠"有界窗口 + 摘要"控制，
    #   不靠少给原文来省（结构优化，不是使用降级）。
    _RECENT_ENTRIES = RECENT_TURNS * 2
    #: 自己说过的话放宽到 100 字（立场句通常在开头，30/60 字会被砍掉）
    _OWN_LIMIT = 100

    messages = [{"role": "system", "content": static_prompt}]

    # 准静态块（群身份/粗时间/表达模式/档位…）：放在摘要账本之前 —— 变化最慢的
    # 块排最前。它们"一天变不了几次"，绝不能排在每轮滑动的近轮之后（那里=每轮
    # 必 miss 区，22:00 时段实测 miss 仍 ~2K/次的主因）。
    for part in (stable_parts or []):
        if part:
            messages.append({"role": "system", "content": part})

    # 历史处理：旧轮次进 append-only 摘要账本，最近几轮保留原文
    if len(history) > _RECENT_ENTRIES:
        old = history[:-_RECENT_ENTRIES]

        _limit = 40   # 别人的话：一行 40 字（摘要用，够看出在聊什么）
        _own_limit = _OWN_LIMIT   # 自己的话：100 字（立场/态度要能对上）

        def _entry_line(h):
            _tp = _hist_time_prefix(h)
            _body = h.get("content") or ""
            if cache_key.startswith("g_"):
                # 群聊：user 行 content 已含「[昵称(QQ号)] 文本」前缀，直接保留说话人。
                # ★ assistant 行**没有前缀**，必须补 `我:` —— 否则超过最近几轮后，
                #   模型认不出"这句是我说的"，每轮重建立场 → 上下矛盾（2026-10-21 修）。
                if h.get("role") == "assistant":
                    return _tp + "我: " + _body[:_own_limit]
                return _tp + _body[:_limit]
            role = "我" if h.get("role") == "assistant" else "对方"
            if h.get("role") == "assistant":
                return _tp + f"{role}: " + _body[:_own_limit]
            return _tp + f"{role}: {_body[:_limit]}"

        def _entry_fp(h):
            src = ((h.get("role") or "") + "|" + str(h.get("ts") or "")
                   + "|" + (h.get("content") or ""))
            return hashlib.md5(src.encode("utf-8", "ignore")).hexdigest()

        window = [(_entry_fp(h), _entry_line(h)) for h in old]
        ledger = _HISTORY_SUMMARY_LINES.setdefault(cache_key, [])
        last = _HISTORY_LAST_WINDOW.get(cache_key) or []
        # 窗口只会"头部掉、尾部加"：找上次窗口的最长后缀 == 本次窗口的最长前缀，
        # 重叠之后的条目才是本轮第一次进 old 区的 → 追加进账本。
        # 完全对不上（历史被压缩重建/重启换血）→ overlap=0 → 账本整本重建，
        # 代价是一次 miss，罕见且不可避免。
        overlap = 0
        for s in range(len(last) + 1):
            tail = last[s:]
            if tail == window[:len(tail)]:
                overlap = len(tail)
                break
        changed = False
        if last and window and overlap == 0 and last[-1] != window[0]:
            # 真·断代（历史被压缩重建/重启换血）：旧账本对应的轮次已经不在了，
            # 追加会重复入账 → 整本重建（接受一次 miss，罕见且不可避免）
            ledger = [ln for _fp, ln in window]
            _HISTORY_SUMMARY_LINES[cache_key] = ledger
            changed = True
        else:
            for _fp, line in window[overlap:]:
                ledger.append(line)
                changed = True
        if len(ledger) > _HISTORY_SUMMARY_LINE_CAP:
            ledger = ledger[-_HISTORY_SUMMARY_LINE_CAP:]
            _HISTORY_SUMMARY_LINES[cache_key] = ledger
            changed = True
        # ★ 2026-10-23：再加一道**字符**预算。
        #   只按行数封顶是不够的：300 行 × 100 字 = 30000 字 ≈ 18750 token，
        #   这是每轮都要重发的量。行数相同、字数可能差十倍（一行「嗯」vs 一行长回复）。
        #   从头部裁 —— 账本是 append-only，裁头只影响一次，尾部追加照旧。
        _ledger_budget = int(_config.get("history_ledger_budget_chars") or 4000)
        if _ledger_budget > 0:
            _lchars = sum(len(x) for x in ledger)
            _cut = 0
            while _lchars > _ledger_budget and len(ledger) > 20:
                _lchars -= len(ledger[0])
                ledger = ledger[1:]
                _cut += 1
            if _cut:
                _HISTORY_SUMMARY_LINES[cache_key] = ledger
                changed = True
                log(f"[账本预算] 摘要账本超出 {_ledger_budget} 字符，裁掉最旧 {_cut} 行")
        if changed or cache_key not in _HISTORY_SUMMARY_TEXT:
            _HISTORY_SUMMARY_TEXT[cache_key] = (
                "【之前对话摘要】\n" + "\n".join(ledger))
        _HISTORY_LAST_WINDOW[cache_key] = window
        messages.append({"role": "system",
                         "content": _HISTORY_SUMMARY_TEXT[cache_key]})

    # 最近几轮保留原文（每轮滑动 → 从这里开始就是必 miss 区）
    # ★ 2026-10-23：这里原来是无条件全量塞进去，一条长回复能把最近区撑到 7500 token。
    #   现在两道闸：
    #     ① 单条按句截断到 HISTORY_RECENT_CHARS_PER_ENTRY（保句子完整，见 _clip_for_history）
    #     ② 整段按总预算从最旧的开始丢整条（_budget_recent_history）
    #   注意截断**不能改变角色顺序**，也不能把 assistant/user 配错 —— 所以只截内容，
    #   不删中间条目。
    if len(history) > _RECENT_ENTRIES:
        _recent = history[-_RECENT_ENTRIES:]
    else:
        _recent = history

    _recent_clipped = []
    for h in _recent:
        _c = str(h.get("content") or "")
        # 原文区一律用 HISTORY_RECENT_CHARS_PER_ENTRY。
        # 不要在这里套账本摘要的 100 字口径（见 _entry_line 的 _own_limit）——
        # 原文区的意义就是"最近几轮说完整"，砍到 100 字就退化成摘要了。
        _recent_clipped.append({"role": h["role"],
                                "content": _hist_time_prefix(h)
                                + _clip_for_history(_c, HISTORY_RECENT_CHARS_PER_ENTRY)})

    _recent_budget = int(_config.get("history_recent_budget_chars") or 6000)
    if _recent_budget > 0:
        _recent_clipped, _dropped = _budget_recent_history(_recent_clipped, _recent_budget)
        if _dropped:
            # 丢条必须留痕：否则有人看上下文少了会以为程序出 bug
            log(f"[历史预算] 最近轮超出 {_recent_budget} 字符，丢弃最旧 {_dropped} 条")

    for h in _recent_clipped:
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


def _effective_temperature(personality_temperature=None) -> float:
    """决定本次请求真正使用的 temperature。

    P0-1 修复：此前 `personality_temperature`（EGO 按情绪/人格档位算出的温度）
    从 call_deepseek 一路透传到 _call_deepseek_sync，最终**没有任何一处使用它** ——
    整条链路恒用全局 AI_TEMPERATURE，"情绪影响说话风格"实际不存在。

    规则（保持可回退、绝不因脏值炸掉请求）：
      · 传了可用数值 → 用它（夹到 [0.2, 2.0]，与 EGO 各子模块的产出区间一致）
      · 没传 / 非数值 / 超范围外的脏值 → 回退全局 AI_TEMPERATURE（原行为）
    """
    if personality_temperature is not None:
        try:
            t = float(personality_temperature)
        except (TypeError, ValueError):
            return AI_TEMPERATURE
        if t != t:                      # NaN
            return AI_TEMPERATURE
        return max(0.2, min(2.0, t))
    return AI_TEMPERATURE


def _agent_stream_chat(messages, tools=None, max_tokens=800,
                       on_reasoning=None, on_content=None):
    """Agent 专用**流式**调用：思维链边收边回调（真正的"实时展示"）。

    为什么不用 _call_chat_sync(stream=True)：那条只取 delta.content，
    **完全不处理 reasoning_content** → 思维链只能在整轮结束后一次性拿到，
    没有"实时"可言（用户实测反馈的就是这个）。
    这里自己解析 SSE：reasoning_content 增量 → 回调；content/tool_calls 累积后返回。
    """
    cfg = _llm_config(None)
    api_url = (cfg.get("api_url") or "").rstrip("/")
    if not api_url.endswith("/chat/completions"):
        api_url += "/chat/completions"
    key = cfg.get("api_key") or ""
    model = cfg.get("model_name") or "deepseek-flash"
    body = {"model": model, "messages": messages, "max_tokens": int(max_tokens),
            "temperature": _effective_temperature(None), "stream": True}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        api_url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key,
                 "Accept": "text/event-stream"})
    content = ""
    reasoning = ""
    tc_map = {}
    with urllib.request.urlopen(req, timeout=180) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except Exception:
                continue
            delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
            rc = delta.get("reasoning_content") or delta.get("reasoning")
            if rc:
                reasoning += rc
                if on_reasoning:
                    try: on_reasoning(rc)
                    except Exception: pass
            c = delta.get("content") or ""
            if c:
                content += c
                if on_content:
                    try: on_content(c)
                    except Exception: pass
            for tcd in (delta.get("tool_calls") or []):
                i = tcd.get("index", 0)
                slot = tc_map.setdefault(i, {"id": "", "type": "function",
                                             "function": {"name": "", "arguments": ""}})
                if tcd.get("id"):
                    slot["id"] += tcd["id"]
                fn = tcd.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
    tool_calls = [tc_map[k] for k in sorted(tc_map.keys()) if tc_map[k]["function"]["name"]]
    return {"content": content, "tool_calls": tool_calls, "usage": {}, "reasoning": reasoning}


def _call_chat_sync(messages, runtime_cfg=None, max_tokens=AI_MAX_TOKENS, stream=False,
                    with_tools=True, personality_temperature=None):
    cfg = _llm_config(runtime_cfg)
    model_name = cfg.get("model_name") or "deepseek-flash"
    api_url = cfg.get("api_url") or DEEPSEEK_URL
    api_key = cfg.get("api_key") or ""
    effective_temperature = _effective_temperature(personality_temperature)
    ptype = cfg.get("provider_type") or _resolve_provider_type(cfg)
    if ptype in ("anthropic", "gemini"):
        # 非 OpenAI 兼容协议（Claude / Gemini）：走 providers.py 专属适配器
        if stream:
            raise ValueError(f"{ptype} 流式调用请走 _stream_chat_lines")
        from providers import make_provider
        prov = make_provider(api_url=api_url, api_key=api_key, model=model_name,
                             provider=ptype, max_retries=3,
                             temperature=effective_temperature)
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
    # finish_reason 带出去（藏在 choices[0] 层）：主链要靠它识别
    # "思维链吃光输出预算"（content 空 + finish_reason=length）。
    msg["_finish_reason"] = choices[0].get("finish_reason") or ""
    return msg, resp.get("usage", {})


def _stream_chat_lines(messages, runtime_cfg=None, max_tokens=AI_MAX_TOKENS, with_tools=True, scene="private"):
    """流式取回最终文本（**只取文本**）。

    ★ 2026-10-27 补工具增量识别。原先这里只读 `delta.content`，
      `delta.tool_calls` 被整个丢掉，于是出现一种静默失败：

        模型在收尾轮仍然要求 `search_web` → 流里只有工具增量、没有文本
        → full_reply 恒为 "" → 返回空 → 上层记一行「AI决定不回复: (空)」

      真机（群 829854136，17:32）就是这个样子：搜了三轮、钱花了、群里一句话没有，
      而日志写的是"AI 决定不回复"—— 跟"模型主动沉默"完全不是一回事，
      排查方向被带偏两次。

      这里不负责执行工具（执行在主循环里），但必须**如实报告**：
      这一轮到底是"说完了但是空的"还是"它还想调工具"。看清原因才能修对地方。
    """
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
    _fr = ""
    _wanted_tools = []          # 流里出现过的工具名（按出现顺序，去重）
    for raw in r:
        line = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
            _ch0 = (chunk.get("choices") or [{}])[0]
            if _ch0.get("finish_reason"):
                _fr = _ch0.get("finish_reason")
            delta = _ch0.get("delta", {})
            content = delta.get("content", "")
            if content:
                full_reply += content
            # 工具增量：只记名字用于诊断，不在这里执行（执行在主循环）
            for _dtc in (delta.get("tool_calls") or []):
                _nm = ((_dtc.get("function") or {}).get("name") or "").strip()
                if _nm and _nm not in _wanted_tools:
                    _wanted_tools.append(_nm)
            # DeepSeek 流式末块带 usage（缓存命中明细）
            if chunk.get("usage"):
                last_usage = chunk.get("usage") or {}
        except Exception:
            pass
    if not full_reply.strip() and _wanted_tools:
        log(f"[流式收尾] 模型仍要求调用 {_wanted_tools}，本轮无文本"
            f"（不是模型决定沉默；finish={_fr or '-'}）")
    # T5 观测：流式收尾记录 usage
    if last_usage:
        try:
            from token_stats import record as _ts_record
            _ts_record(scene, last_usage, {"stream": True}, lane="main")
            # P23 锚点（实测）：流式末尾的 usage 是真实值 → 以此打上下文压力锚点。
            # 作用域用 scene（本函数拿不到群号/QQ），是有意的粗粒度锚点。
            from token_stats import note_context_anchor as _ts_anchor
            _ts_anchor(scene, prompt_tokens=(last_usage or {}).get("prompt_tokens"),
                       scene=scene)
        except Exception:
            pass
    return _mark_truncated(full_reply, _fr == "length")


def _call_deepseek_sync(user_msg: str, images: Optional[List[str]] = None,
                        target_qq: Optional[str] = None, group_id: Optional[str] = None,
                        is_group: bool = False, private_memory_context: str = '',
                        personality_temperature: Optional[float] = None,
                        ego_inject_context: str = '', sender: str = '',
                        scene_context: str = '', progress=None) -> Optional[str]:
    """同步调用 DeepSeek API（在后台线程中运行），支持工具调用和重试。

    `progress`：可选的"还在推进"回调（2026-10-27）。每完成一个阶段（摘要压缩、
    每一轮模型调用、每一轮工具）调一次，让等待方知道该继续等而不是判超时。
    没有它就只能用"总时长"卡，而一次生成可能因为模型自己决定联网搜索变成
    两三轮 —— 详见 `_wait_ai_result` 的说明。
    """
    global chat_history, group_chat_history

    if target_qq is None:
        target_qq = TARGET_QQS[0]

    # T6: 用缓存构建 static_prompt（人格+规则+多样性+表情包+铁律+群聊规则），
    # 按 (is_group, config_rev, personality_mtime) 缓存，前缀稳定 → 命中 DeepSeek 硬盘缓存
    static_prompt = _build_static_prompt(is_group)
    dynamic_parts = []
    # ★ stable_parts（2026-10-13 结构二次重排）：一天变不了几次的块 —— 构建器会
    #   把它们放在"摘要账本之后、滑动近轮之前"，让它们进入可缓存前缀。
    #   此前它们混在 dynamic_parts 里排在近轮之后 = 每轮必 miss（22 时段实测
    #   miss 仍 ~2K/次的主因之一）。
    stable_parts = []

    # 群聊模式：规则已并入 static_prompt（见 _build_static_prompt），动态信息放 dynamic_parts
    if is_group:
        if group_id:
            summarize_group_history(group_id)
            # 摘要压缩本身是一次完整 LLM 调用（最长 15s），它占的是"等回复"的预算，
            # 不算进展就会被误判成卡死 —— 打完这个 tick 再进正式生成。
            try:
                if progress: progress()
            except Exception:
                pass

    # 对象语气提示（彩蛋）：命中时只追加一句 27 字提示；不命中返回空串，零 token。
    #   优先级：focus_qq 明文 > persona_tag.TAG 派生标签 > 文本里的号码。
    #   sender 由调用方传入（群聊 = buffer["sender"]，私聊回退 target_qq）。
    try:
        import persona_tag as _ptag
        _tag_sender = str(sender or "").strip()
        if not _tag_sender and not is_group:
            _tag_sender = str(target_qq or "")
        _tag_text = _ptag.context(user_msg, _config.get("focus_qq"),
                                  resolve_qq_nick, sender=_tag_sender)
        if _tag_text:
            dynamic_parts.append(_tag_text)
    except Exception:
        pass

    # ── 时间注入（群聊 + 私聊**都要**）────────────────────────────────
    # ★ 2026-10-22 修：此前时间分级注入只写在 `if is_group and group_id:` 里面，
    #   **私聊一行时间都没有** —— 10:44 对方说「晚上好」，模型手上没有任何
    #   "现在几点"的依据，只能顺着答。这就是"没有时间观念"的直接成因。
    #   私聊是一对一，没有群友兜底纠正，比群里更需要知道现在几点。
    _time_key = f"g_{group_id}" if (is_group and group_id) else f"p_{target_qq}"
    try:
        from harness import bot_hook as _bh_t
        _coarse, _fine = _bh_t.time_parts(_time_key)
        if _coarse:
            stable_parts.append(_coarse)
        if _fine:
            dynamic_parts.append(_fine)
        # 对方话里的时间前提与钟点矛盾 → 确定性指出（不指望模型自己比对）
        _mm = _bh_t.mismatch_note(user_msg, _time_key)
        if _mm:
            dynamic_parts.append(_mm)
    except Exception:
        # 降级：退回原逻辑（群聊按 600s 节流，私聊每轮给）
        _now_ts = datetime.datetime.now()
        if not is_group:
            dynamic_parts.append(
                "【当前时间】" + _now_ts.strftime("%Y年%m月%d日 %H:%M") + "，星期" + ["一","二","三","四","五","六","日"][_now_ts.weekday()])
        else:
            _last_time = _GROUP_TIME_INJECT_AT.get(group_id, 0)
            if not isinstance(_last_time, (int, float)) or not _last_time or (time.time() - _last_time) >= 600:
                dynamic_parts.append(
                    "【当前时间】" + _now_ts.strftime("%Y年%m月%d日 %H:%M") + "，星期" + ["一","二","三","四","五","六","日"][_now_ts.weekday()]
                )
                _GROUP_TIME_INJECT_AT[group_id] = time.time()

    # 群聊动态信息（群成员/时间线）→ 放到 dynamic_parts
    if is_group and group_id:
        # ── 静默时段被叫醒 → 明确告诉模型"你在睡觉"（2026-10-24）──
        # 判据与话术都在 _inject_sleep_state 里，私聊走同一个函数。
        _inject_sleep_state(dynamic_parts, is_group=True)
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
        # 群主/管理员常驻注入（2026-10-11）：被问"群主是谁"是高频场景，
        # 且这是协议层现成的事实 —— 不该靠模型主动查工具或装懂。极低成本(一行)。
        _gr = GROUP_ROLES.get(str(group_id)) or {}
        if _gr.get("owner"):
            _adm = "、".join(_gr.get("admins") or []) or "无"
            stable_parts.append(f"【群身份】群主：{_gr['owner']}；管理员：{_adm}。被问到群主/管理时据实回答。")
        # T2-3 群成员列表按需注入：仅当用户消息涉及成员时才注入，省 15 个昵称的 token
        _member_kw = ["选", "谁", "@", "在线", "成员", "随机", "人"]
        if group_members and any(k in user_msg for k in _member_kw):
            dynamic_parts.append("【群成员列表】" + "、".join(group_members[:15]))
        summary = GROUP_SUMMARY.get(group_id, "")
        if summary:
            # ★ 改为常驻注入（2026-10-13 重排）：旧的"每10分钟注一次"让这块
            #   忽有忽无 —— 前缀逐轮抖动，缓存照样断。常驻后它只在摘要重新
            #   生成（半小时级一次）时才变，命中区按 10% 计价，成本反而更低。
            stable_parts.append(f"【之前的群聊背景】{summary[:200]}")
        if SELF_LEARN_OK:
            learn_ctx = self_learn.get_context_for_llm(str(group_id))
            if learn_ctx:
                stable_parts.append(learn_ctx)

    # 网络用语/记忆/私聊记忆 → 放到 dynamic_parts
    if SLANG_OK and _config.get("slang_injector_enable", True):
        slang_ctx = get_slang_context(user_msg)
        if slang_ctx:
            dynamic_parts.append(slang_ctx)
    # EGO 人格/记忆/风格/节奏注入 → system 级上下文（独立于群聊/私聊钩子，提升指令权重）
    if ego_inject_context:
        _inject_ego_context(dynamic_parts, ego_inject_context)
    # 现场资料（群聊近况/当前局面/决策结论）走 system，而不是拼在用户消息里。
    # 放进用户消息时模型会把它当成"对方发来的简报"逐条回应，实测出现
    # 「我这边收到的原文长这样」「我账上…」这种后台汇报腔（2026-10-25）。
    if scene_context:
        dynamic_parts.append(
            "【现场资料·不是对方说的话】以下是程序给你的背景信息，只用于理解语境；"
            "按人格自然接话，不要复述、不要逐条回答，也不要提到这些标题。\n"
            + scene_context)
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
    # ── T7 批一 ★0 → 2.0 阶段C：群聊历史召回换 memory_context builder ──
    # （带阈值 τ + 预算封顶 + 行尾分数标注；builder 挂了降级回旧 recall_history_block，
    #   链路不断。sources 只取沙漏 —— super_memory 群侧已由 EGO 钩子注入【核心记忆】，
    #   再取同源=双注，是 2.0 设计稿点名要消灭的旧账。）
    # 私聊不走这里：私聊已有 super_memory 向量 top-K（语义），再有界召回同一份历史
    # 会变成两处重复注入（同 2.2 的教训）。
    if group_id:
        # ── 群内人物画像（2026-10-19 新增）──
        #   此前**群聊完全没有画像注入**（只有私聊走 get_memory_prompt_block），
        #   于是 Bot 在群里不知道谁是谁，更不知道本群有没有重名的人。
        #   只用 build_people_block()：画像是准静态信息，不含检索层与元信息噪音。
        try:
            from sandglass_bridge import build_people_block as _bpb
            _ppl = _bpb(str(group_id)) or ""
            if _ppl.strip():
                dynamic_parts.append(_ppl.strip())
        except Exception:
            pass
        try:
            from harness import memory_context as _mc
            # 注意区分不了"过阈零条"（正常，什么都不注）与"源挂了"——旧函数没有
            # 阈值闸，拿旧路径兜"零命中"会把本该被 τ 滤掉的低相关旧账又灌回来，
            # 所以 builder 空返回就是空返回（旧路径仅在 builder 本身异常时才救场）。
            _recall = _mc.build_block(user_msg, scope="group",
                                      scope_id=str(group_id), sources=("sand",))
            if _recall:
                dynamic_parts.append(_recall)
        except Exception:
            try:
                from sandglass_bridge import recall_history_block
                # ★ scope 必须传（2026-10-19 修）：降级出口此前不过滤作用域，
                #   主路径一旦抛异常就会把跨群/私聊记忆注进本群 prompt。
                _recall = recall_history_block(user_msg, scope=("group", str(group_id)))
                if _recall:
                    dynamic_parts.append(_recall)
            except Exception:
                pass
    # ── 静默时段（私聊）：一对一没有"点名"这个动作，来了就是叫醒 ──
    # 群聊那侧在 `if is_group` 分支里调同一个函数；这里补上私聊，
    # 之前它只判断了群聊，私聊凌晨会被当成白天一样长篇大论地回。
    if not is_group:
        _inject_sleep_state(dynamic_parts, is_group=False)
    # ── P4·S4：能力档位（只在偏离默认时注入；准静态 → stable_parts）──
    if group_id:
        try:
            _pb = _scope_policy_block("group", group_id)
            if _pb:
                stable_parts.append(_pb)
        except Exception:
            pass
    if group_id and group_id in GROUP_LAST_EXIT:
        elapsed = time.time() - GROUP_LAST_EXIT[group_id]
        if elapsed < 900:
            mins = max(1, int(elapsed / 60))
            dynamic_parts.append(f"【重要】你{mins}分钟前刚说过退场语，这次回来不要再重复退场语。")

    # ── skill L1 元数据（harness.skill_loader）──
    #  只在"本轮真的会注入 skill 工具"时给出清单，避免闲聊也背这段文字。
    #  放 dynamic_parts（尾部）→ 不影响前面的前缀缓存。
    try:
        from harness import bot_hook as _bh2
        if _bh2.has_skills() and _bh2._should_offer_skills(user_msg):
            _sk_block = _bh2.skill_l1_block()
            if _sk_block:
                dynamic_parts.append(_sk_block)
    except Exception:
        pass

    # ── 时效性护栏（harness.freshness）──
    #  比"提醒模型注意时效"更强：不给它"凭记忆自信回答"的机会。
    #    · 锚点（知识截止 + 日期基准）：**长期事实**，一天内不变 → 可长期复用；
    #    · 指令（"这句要核实"）：**本轮事实** → 只在本轮时效敏感时追加，闲聊零成本。
    #  这是"不让 AI 拿过时信息当事实回答"的落点：
    #  模型一旦知道"我的知识截止于 X，已过去 800+ 天"，
    #  对价格/版本/排名/新闻这类问题就会转向工具，而不是硬答。
    try:
        from harness import bot_hook as _bh3
        _fr_anchors, _fr_directive = _bh3.freshness_parts(
            user_msg, has_search=True)
        if _fr_anchors and _bh3._any_in(user_msg, _bh3._WEB_KW + _bh3._DESKTOP_KW +
                                        ("今天", "现在", "最新", "目前", "今年", "最近")):
            dynamic_parts.append(_fr_anchors)
        if _fr_directive:
            dynamic_parts.append(_fr_directive)
    except Exception:
        pass

    # 选择历史 + 构建消息（用缓存优化版）
    # ★ 2026-10-21：窗口 10 → 16 条。最近 4 轮（8 条）要给原文，10 条只剩 2 条
    #   能进摘要账本；给到 16 条才有 8 条可压缩。成本仍由"有界窗口 + 账本 cap"控制。
    if is_group and group_id:
        history = group_chat_history.get(group_id, [])[-16:]
        cache_key = f"g_{group_id}"
        # 任务 5.3 事件溯源试点：白名单群改为**从仅追加日志派生** history。
        # 派生是纯函数，与内存版记录同一字符串；日志为空（首次/刚启用）时回退内存，
        # 控制爆炸半径。启用条件见 harness.session_log.enabled_for（默认全关）。
        try:
            from harness import session_log as _slog
            if _slog.enabled_for(group_id):
                _derived = _slog.replay(group_id, limit=16)
                if _derived:
                    history = _derived
        except Exception:
            pass
    else:
        history = chat_history.get(target_qq, [])[-16:]
        cache_key = f"p_{target_qq}"

    # ── 按需工具集（harness.tool_registry）──
    #  ★ 2026-10-22 提前：原先在 messages 构建**之后**才算，于是 prompt 里
    #    没有任何"我这轮有哪些工具"的说明 —— 模型不知道自己在群里的工具链，
    #    被问「你能截屏吗」只能靠猜，也分不清"群里能用什么"和"私聊能用什么"。
    #    现在先算出工具集，再把「本轮可用工具」写进 dynamic_parts（见下）。
    #  **仅注入本轮可能用到的工具**（工具定义在请求前缀里，全量注入是纯浪费）。
    #  策略：命中场景关键词才加对应分组；命中不到 → 只给 core（search_web）。
    #  若注册表不可用/出错 → 退回原行为（全量），保证功能不退化。
    tools_for_call = None
    try:
        from harness import tool_registry as _treg
        from harness import bot_hook as _bh
        if is_group:
            # 群聊：注册表子集必须**同时**落在 group_tool_allowlist 白名单内
            #   （白名单是安全边界，按需加载不能把它放开）
            _allowed_names = {t["function"]["name"] for t in _build_group_tools(group_id)}
            _subset = [n for n in _bh.tool_names_for(user_msg, is_group=True)
                       if n in _allowed_names]
        else:
            _subset = _bh.tool_names_for(user_msg, is_group=False)
        if _subset:
            _resolved = _treg.resolve(_subset, lazy=True)
            if _resolved:
                tools_for_call = _resolved
    except Exception as _e:
        log(f"[工具按需加载] 跳过（退回全量）: {_e}")

    if tools_for_call is None:
        tools_for_call = _build_group_tools(group_id) if is_group else TOOLS
    # ★ 插件工具常驻：不参与关键词裁剪，也不受群白名单影响。
    #   否则「发个 QQ 空间」这类措辞一旦没命中关键词，按需加载就不挂它，
    #   模型又只能答"没能力"（1.5.9 §A.4）。
    try:
        if isinstance(tools_for_call, (list, tuple)) and all(
                isinstance(_t, dict) for _t in tools_for_call):
            _tfc = list(tools_for_call)
            _have = {(_t.get("function") or {}).get("name") for _t in _tfc}
            for _sch in _PLUGIN_TOOL_SCHEMAS:
                if _sch["function"]["name"] not in _have:
                    _tfc.append(_sch)
            # 群管理工具只在群聊给 —— 私聊没有可管理的对象
            if is_group and GROUP_ADMIN_SCHEMA["function"]["name"] not in _have:
                _tfc.append(GROUP_ADMIN_SCHEMA)
            tools_for_call = _tfc
    except Exception as _e:
        log(f"[插件工具] 常驻注入失败（用原工具集）: {_e}")
    # 本轮真正"提供给模型"的工具名集合（用于越界防护）
    try:
        _offered_tool_names = {t["function"]["name"] for t in tools_for_call
                               if isinstance(t, dict) and "function" in t}
    except Exception:
        _offered_tool_names = set()
    if not _offered_tool_names:
        # tools_for_call 是名字列表（理论上不会走到）→ 直接用
        _offered_tool_names = {str(n) for n in (tools_for_call or [])}

    # ── 【本轮可用工具】+【谁能让我干活】注入（2026-10-22 新增）──
    #  这是"不知道自己的群聊工具链""分不清白名单""不知道管理员是谁"
    #  "不知道没权限时怎么回"这四个问题的共同根因：**运行时状态从未进 prompt**。
    #  模型手上只有工具 schema（而且按需裁剪），没有"我是谁、我能用什么、
    #  谁有权让我用"的整体视图，被问就只能编。
    try:
        _cap_block = _capability_block(
            is_group=is_group, group_id=group_id, sender=sender,
            tool_names=_offered_tool_names, user_msg=user_msg)
        if _cap_block:
            stable_parts.append(_cap_block)
    except Exception as _e:
        log(f"[能力视图] 生成失败（不注入）: {_e}")

    messages = _build_cached_messages(history, cache_key, static_prompt, dynamic_parts,
                                      user_msg, images, stable_parts=stable_parts)

    # ── 网页操作任务引导：消息含链接/问卷/填表意图时，引导模型调用 web_operate 工具 ──
    _web_hint = ""
    if re.search(r'https?://\S+', user_msg):
        _web_hint = "【重要】用户消息中包含网页链接。请用 web_operate 工具真实操作网页：先 open_url（会自动返回带编号的结构化快照），再用快照里的 ref 调 click/fill/select（每次动作后会自动回传新快照）。绝不要凭文字假装已操作。遇到多步复杂任务可直接用 action=autonomous + goal 让插件自主完成。"
    elif re.search(r'(问卷|填表|填表单|答题|做.*?(问卷|题)|填写|表单|网页.*?(任务|操作)|帮我.*?(打开|填))', user_msg):
        _web_hint = "【重要】本次请求涉及问卷/表单/网页任务。请调用 web_operate 工具实际打开并操作网页：open_url 拿快照 -> 按 ref 逐个 click/fill 作答（动作后自动回传新快照）；问卷/多步任务优先用 action=autonomous + goal 自主完成。禁止凭文字假装完成。"
    if _web_hint:
        messages.append({"role": "user", "content": "[系统提示] " + _web_hint})

    allow_tools = True
    
    # 群聊回复短，降低输出上限节省 token
    effective_max_tokens = AI_MAX_TOKENS
    # 私聊也降低回复量：限制为 X token。
    # ★ 但**推理模型**（deepseek-v4 / r1 / qwq / *think* / *reason* 等）的
    #   思维链计入输出预算 —— 限额会被思考过程吞掉，content 直接变空
    #   （实测：private 800 上限，模型思考 800 token 用尽 → 空回复）。
    #   对这类模型放开私聊限额（与本地思维链模型同一豁免，注释旧案），
    #   短回复由 prompt 约束，不靠 token 限额。
    _mn = str(_llm_config().get("model_name") or "").lower()
    _is_reasoning_model = any(t in _mn for t in
                              ("r1", "v4", "qwq", "think", "reason", "o1-", "o3"))
    if not is_group and not _llm_config().get("use_local") and not _is_reasoning_model:
        effective_max_tokens = min(effective_max_tokens, _config.get("ai_private_max_tokens", 200))
    # 网页操作任务需要更大的输出预算（工具调用链 + 结果汇总）
    if _web_hint:
        effective_max_tokens = max(effective_max_tokens, _config.get("ai_web_task_max_tokens", 1500))
    
    last_error = None
    web_rounds = 0
    # ── 三处"轮次预算"，服务三种场景，**故意不同，不要合并** ──
    #   主链普通（本处，可配 max_tool_rounds，默认 3）
    #       一次聊天回复里允许几拍工具。聊天要快，"先搜 → 读正文 → 再总结"
    #       三拍够用；再多用户已经在等第二轮了。
    #   主链网页（本处，MAX_WEB_ROUNDS，默认 20）
    #       网页/问卷任务要反复抓取-解析-再抓，3 拍根本不够。硬编码是有意的：
    #       这是安全阀，不是调参项，填大了只会让一次任务烧掉更多 token。
    #   agent 侧（harness.agent_loop.DEFAULT_MAX_STEPS，默认 12，可在 agent 页改）
    #       桌面 agent 是"把一件事做完"，允许更多步；它不在聊天主链上，慢一点没事。
    #   曾计划把它们"统一成一份轮次配置"，核实后不成立：三者对应不同等待预期，
    #   统一后要么网页任务做不完，要么闲聊变贵。真正该统一的是**工具集语义**
    #   （已经统一：都走 harness.bot_hook.agent_tools_for）。
    MAX_WEB_ROUNDS = 20
    _max_plain_rounds = int(_config.get("max_tool_rounds") or 3)
    if _max_plain_rounds < 1:
        _max_plain_rounds = 1        # 1 = 退回旧行为
    error_retries = 0
    _img_fallback_used = False     # 图片降级只做一次（native 拒图 → 视觉描述重试）
    _length_retry_used = False     # 思维链吃光输出预算的加倍重试，只做一次
    while True:
        try:
            # 每轮模型调用都是一次"有进展"（2026-10-27）：模型自己决定联网搜索时，
            # 一次生成会变成两三轮，总时长能到 50 秒。等待方靠这个 tick 判断
            # "还在推进 → 继续等"，而不是拿固定 30 秒砍掉一条已经生成好的回复。
            try:
                if progress: progress()
            except Exception:
                pass
            msg, usage = _call_chat_sync(messages, runtime_cfg=None, max_tokens=effective_max_tokens,
                                         stream=False, with_tools=tools_for_call,
                                         personality_temperature=personality_temperature)

            tool_calls = msg.get("tool_calls")
            if tool_calls:
                # 归一化 + 单点执行（harness.tool_runner）：与 agent 侧共用同一套语义
                #  → 异常隔离 / spill 落盘恢复 / truncated 与 error 分列，主链同样具备。
                from harness import tool_runner as _trunner
                tool_calls = _trunner.normalize_tool_calls(
                    tool_calls, session_id=(f"g{group_id}" if is_group else f"p{target_qq}"),
                    step=web_rounds)
                if not tool_calls:
                    # 结构不可用 → 视为无工具，走下面的最终回复分支
                    pass
                else:
                    log(f"工具调用: {[tc['function']['name'] for tc in tool_calls]}")
                    _uniq_tc = _dedup_tool_calls(tool_calls)
                    if len(_uniq_tc) != len(tool_calls):
                        log(f"[工具去重] {len(tool_calls)} → {len(_uniq_tc)}"
                            f"（同一调用重复下发）")
                        tool_calls = _uniq_tc
                    # T5 观测：工具轮 token 也计入（群聊/私聊/网页场景）
                    try:
                        from token_stats import record as _ts_record
                        _ts_record("web" if _web_hint else ("group" if is_group else "private"), usage,
                                   {"round": "tool"}, lane="tool")
                    except Exception:
                        pass
                    messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
                    web_rounds += 1
                    _round_ok_flags = []        # 这一轮每个工具的成功情况
                    #: 本轮是否出现「搜索结果年份与问句不符」（见下方注释）
                    _stale_hit = False
                    #: 上面那次的**问句年份**（重搜提示要告诉模型要搜哪一年）
                    _stale_years = ""
                    # 群管理工具要把**当前消息的发送者**带进去做权限判断。
                    # execute_tool_call 只会自动补 group_id（来自参数），
                    # sender 不在它的职责里，所以在这里闭包捕获。
                    _tool_fns = dict(TOOL_FUNCTIONS)
                    if is_group:
                        # user_msg 传原话 —— group_admin 要用它核对
                        # 「模型填的群名是不是用户原话里的」（见 _do_group_admin）
                        _tool_fns["group_admin"] = (
                            lambda a, _s=sender, _g=group_id, _m=merged_text:
                            _tool_group_admin(a, sender=_s, group_id=_g,
                                              user_msg=_m))
                    for tc in tool_calls:
                        _r = _trunner.execute_tool_call(
                            tc,
                            tool_functions=_tool_fns,
                            offered_names=_offered_tool_names,
                            group_id=group_id, target_qq=target_qq, is_group=is_group,
                            session_id=(f"g_{group_id}" if is_group and group_id
                                        else f"p_{target_qq}"),
                            logger=log,
                        )
                        _act = ""
                        try:
                            _act = (json.loads(tc["function"].get("arguments") or "{}") or {}).get("action") or ""
                        except Exception:
                            pass
                        _round_ok_flags.append(bool(_r["ok"]))
                        _res_parts = (_r["full_text"] or "")[:140].replace("\n", " ")
                        _mark = "" if _r["ok"] else (" [被拒:%s]" % _r["blocked"] if _r["blocked"] else " [失败]")
                        log(f"  → {_r['tool']}: action={_act} (第{web_rounds}轮)"
                            f"{_mark} → {_res_parts}")
                        # ── 搜索结果年份对不上（2026-10-27 真机事故）──
                        #   搜「明日方舟 2026 半周年」只回 2025 年的旧闻，模型据此
                        #   答「十一月的还没宣」—— 而 11 月的消息就在另一条结果里。
                        #   搜到了但答的是别的年份，比查不到更危险：查不到模型会
                        #   说"没查到"，答错年份模型会**笃定地说错话**。
                        #   这里记下来，好让下一轮强制它换词再搜。
                        if _r.get("tool") == "search_web" and _r.get("ok"):
                            try:
                                _q = ""
                                try:
                                    _q = str((json.loads(tc["function"].get("arguments")
                                                          or "{}") or {}).get("query") or "")
                                except Exception:
                                    _q = ""
                                _bad_years = _search_year_mismatch(_q, _r.get("full_text") or "")
                                if _bad_years:
                                    _stale_hit = True
                                    try:
                                        import search_core as _sc
                                        _ys = _sc.query_years(_q)
                                        _stale_years = "/".join(sorted(_ys)) if _ys else ""
                                    except Exception:
                                        _stale_years = ""
                                    log(f"  → [搜索年份不符] 问 {_q}，"
                                        f"结果全是 {_bad_years} → 需换词重搜")
                            except Exception:
                                pass
                        messages.append(_trunner.make_tool_message(tc["id"], _r["inline"]))
                    # ── 上下文总量闸（2026-10-21）──
                    #   工具轮累计逼近窗口预算 → 停止继续调工具，强制收尾。
                    #   不这么做的话，撞到窗口时上游回 400，而 400 不可重试 → 整条回复丢失。
                    _over, _used, _lim = _ctx_over_budget(messages)
                    if _over:
                        log(f"[上下文闸] 工具轮累计 {_used} 字符 ≥ 预算 {_lim}，"
                            f"停止工具轮，强制收尾")
                        return _finalize_reply(messages, effective_max_tokens,
                                               ("group" if is_group else "private"),
                                               had_tools_rounds=web_rounds,
                                               progress=progress)
                    # 网页任务：继续多轮推进，直到模型主动收尾或达到轮次上限
                    if _web_hint:
                        if web_rounds >= MAX_WEB_ROUNDS:
                            log(f"[网页任务] 达到轮次上限 {MAX_WEB_ROUNDS}，强制流式收尾")
                            return _finalize_reply(messages, effective_max_tokens, "web",
                                                   had_tools_rounds=web_rounds,
                                                   progress=progress)
                        continue
                    # 普通工具（2026-10-23）：原先这里是"一轮就收尾"，现在按
                    # max_tool_rounds 续轮。判据抽在 _should_continue_tool_round 里
                    # （全部失败 → 收尾如实说；有成功的 → 继续）。
                    # 上下文总量闸在上面对所有轮次都生效，不会无限堆。
                    #
                    # ★ 搜索年份不符（2026-10-27）：搜到了但答的是别的年份，
                    #   不能就这么收尾 —— 模型会拿着 2025 年的旧闻下结论。
                    #   强制它换一组词再搜（搜活动名、加上年份），直到
                    #   搜到对年份的，或者轮次用完。
                    if _stale_hit and web_rounds < _max_plain_rounds:
                        _want_years = _stale_years or ""
                        log(f"[搜索重搜] 年份不符（要 {_want_years}），"
                            f"第 {web_rounds}/{_max_plain_rounds} 轮，要求换词重搜")
                        messages.append({
                            "role": "user",
                            "content": (f"【必须重搜】刚才搜到的内容**年份不对**"
                                        f"（要的是 {_want_years} 的事，搜回来的是别的年份），"
                                        "拿它回答会说出事实错误。"
                                        "\n换一组关键词再搜一次，**注意三点**："
                                        "\n1. 检索词里要带上要问的那个年份或届数；"
                                        "\n2. 别用更早的届数词（比如问今年却搜「五周年」，"
                                        "那是往年）；"
                                        "\n3. 搜具体的活动名/角色名/版本号，比搜大词准。"
                                        "\n这次不要直接下结论。"),
                        })
                        continue
                    if not _should_continue_tool_round(web_rounds, _round_ok_flags,
                                                       _max_plain_rounds):
                        if web_rounds >= _max_plain_rounds and any(_round_ok_flags):
                            log(f"[工具轮次] 达到上限 {_max_plain_rounds} 轮，收尾")
                        return _finalize_reply(messages, effective_max_tokens,
                                               ("group" if is_group else "private"),
                                               had_tools_rounds=web_rounds,
                                               progress=progress)
                    continue

            reply = str(msg.get("content") or "").strip()
            # ── 思维链吃光输出预算 → 加倍重试一次（2026-10-13 私聊空回复事故）──
            # 线上实证（23:41 私聊）：usage total=5547 = prompt 4747 + completion
            # 恰好 800 —— deepseek-flash 的 reasoning_content 把输出预算全吃掉，
            # content 为空 → 用户收到"暂时无法处理"。代码里 7934 的豁免名单
            # （r1/v4/qwq/think/reason）不含 flash，靠名字猜不全；改成**认现象**：
            # content 空 + finish_reason=length → 预算翻倍重试（封顶 2400，只一次）。
            if not reply and not tool_calls and \
                    str(msg.get("_finish_reason") or "") == "length" and \
                    not _length_retry_used:
                _length_retry_used = True
                _doubled = min(2400, max(1600, effective_max_tokens * 2))
                log(f"[输出预算] content 空且 finish_reason=length（思维链吃光 "
                    f"{effective_max_tokens} token 预算）→ 提到 {_doubled} 重试")
                effective_max_tokens = _doubled
                continue
            total = usage.get("total_tokens", 0) if isinstance(usage, dict) else 0
            cost = total * 0.5 / 1_000_000
            # T5 观测：记录缓存命中明细 + 按场景累计
            try:
                from token_stats import record as _ts_record
                _scene = "web" if _web_hint else ("group" if is_group else "private")
                _ts_record(_scene, usage, {"web_rounds": web_rounds}, lane="web")
                # P23 锚点（实测）：用本轮的 prompt_tokens 给「这个群/这个人」打上下文压力锚点。
                # 只认实测值；拿不到就什么都不记（不在这里退化成估算 —— 估算是调用方的事）。
                try:
                    from token_stats import note_context_anchor as _ts_anchor
                    _ts_anchor(group_id if is_group else target_qq,
                               prompt_tokens=(usage or {}).get("prompt_tokens")
                               if isinstance(usage, dict) else None,
                               scene=_scene)
                except Exception:
                    pass
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

            # （此前这里还有一条简化版"AI 回复"日志 → 同一轮打两遍，
            #   日志里看着像"回复了两次"。明细版已在上方 token_stats 块打过，删。）
            # ★ 把"被 max_tokens 掐断"的事实带给发送端（_trim_incomplete 不再瞎猜）
            return _mark_truncated(reply, str(msg.get("_finish_reason") or "") == "length")
        except Exception as e:
            last_error = e
            err_body = ""
            try:
                if hasattr(e, "read"):
                    err_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            # ── 图片降级（2026-10-12，只做一次）──────────────────────────
            # 主模型已支持视觉（deepseek-flash 实测能读图），但万一上游某次拒图
            # （400 invalid_request / 图片体积超限 / 中转不支持 image_url），
            # 这里自动改用**免费车道**的视觉描述重试一次，而不是把整条回复丢掉。
            if images and not _img_fallback_used:
                _img_fallback_used = True
                try:
                    _blk = build_image_context_block(images[:3], scene="群聊" if is_group else "私聊")
                except Exception:
                    _blk = ""
                if _blk:
                    log(f"[图片理解] 主模型拒图（{str(e)[:80]}），改用视觉描述重试")
                    try:
                        messages = _build_cached_messages(
                            history, cache_key, static_prompt, dynamic_parts,
                            f"{user_msg}\n{_blk}", None, stable_parts=stable_parts)
                        images = None
                        continue
                    except Exception as _rebuild_err:
                        log(f"[图片理解] 重建消息失败，按原错误继续：{_rebuild_err}")
            # ── 重试决策（harness.retry_policy）：分类 → 只有可恢复错误才重试 ──
            #  改之前：不分类，401/400 也硬等 3s × 4 次（白耗 12 秒还是失败）。
            #  现在：鉴权/参数类立刻上抛；限流/5xx/网络抖动 走指数退避 + 抖动，
            #  并服从服务端 Retry-After。这是"降成本 + 提成功率"的关键。
            if error_retries < 4:
                try:
                    from harness import retry_policy as _rp
                    _vd = _rp.classify(e)
                    if not _vd.should_retry:
                        log(f"AI 不重试（{_vd.reason}）: {e}" + (f" | {err_body}" if err_body else ""))
                        break
                    _delay = _rp.compute_delay(error_retries, retry_after=_vd.retry_after)
                except Exception:
                    _delay = 3
                error_retries += 1
                log(f"AI 重试 {error_retries}/5（{_delay:.2f}s 后）: {e}"
                    + (f" | {err_body}" if err_body else ""))
                time.sleep(_delay)
                continue
            break
    log(f"AI 失败 (已重试{error_retries}次): {last_error}" + (f" | {err_body}" if err_body else ""))
    return None


def call_deepseek(user_msg: str, images: Optional[List[str]] = None,
                  target_qq: Optional[str] = None, group_id: Optional[str] = None,
                  is_group: bool = False, private_memory_context: str = '',
                  personality_temperature: Optional[float] = None,
                  ego_inject_context: str = '', sender: str = '',
                  scene_context: str = '') -> Optional[str]:
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
        "scene_context": scene_context,
        "sender": sender,
    })
    # ★ 2026-10-27：判据从"用户消息像不像网页任务"改成"这次生成还有没有进展"。
    #   原来只有 URL / 问卷填表才给 600 秒，其余一律 30 秒；但真正的耗时是
    #   **模型自己决定调不调工具**（搜索一轮 = 两次模型调用 + 一次搜索），
    #   跟用户怎么措辞无关。真机两次「讲解一下精神分析引论」都因此被 30 秒砍掉。
    #   现在：首轮 30 秒起步，只要 worker 还在打 progress 就按 idle 续期，
    #   最多到 hard 上限。网页任务仍然是宽预算（它本来就是多步工具链）。
    _q = user_msg or ""
    _is_web_task = bool(re.search(r'https?://\S+', _q)) or bool(re.search(r'(问卷|填表|填表单|答题|做.*?(问卷|题)|填写|表单|网页.*?(任务|操作)|帮我.*?(打开|填))', _q))
    if _is_web_task:
        return _wait_ai_result(task_id, timeout=600.0, idle_timeout=180.0, max_total=600.0)
    return _wait_ai_result(task_id, timeout=30.0, idle_timeout=45.0, max_total=150.0)


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
_KNOWN_NAMES: List[str] = [str(n).strip() for n
                           in (_config.get("known_names") or []) if str(n).strip()]


def _norm_nick(s) -> str:
    """称呼归一化：去 @/空格（含全角）、转小写。

    为什么必须有这一步：群友打字大小写随手（"小a"/"小A"/"小Ａ"）、带不带 @ 也随手，
    早年 `"小a" in text` 这种裸子串匹配把「小A，你会不会忘掉大家」判成
    HAS_XIAO_A=FALSE（线上日志实证）—— 触发词就这么静默失效了。
    """
    s = str(s or "").strip()
    if not s:
        return ""
    try:
        import unicodedata
        s = unicodedata.normalize("NFKC", s)   # 全角ａ→a、＠→@、全角空格→半角
    except Exception:
        pass
    s = s.lstrip("@").replace(" ", "").replace("\u3000", "")
    return s.lower()


def _bot_self_names() -> set:
    """Bot **自己的全部称呼**（QQ 号 + 配置昵称 + 各群真实群名片）。

    ★ 为什么需要（2026-10-19 线上实证）：
      同一个 QQ 账号在不同群可以挂**不同群名片**。实测本机：
        · 群 200000002 名片 = 「蛋」
        · 群 200000001 名片 = 空（回退全局昵称「占位名片A」）
      而旧逻辑只认 `bot_group_nick` **一个值**（= 占位名片A）。
      于是用户在 200000001 说「@蛋」时，`_is_at_others` 判成"@别人" →
      整条消息被跳过（日志实证：`跳过定向他人的消息: [占位名片B] @蛋`）。
      同一个人换个群的叫法就不认识自己了 —— 这是身份识别缺陷，不是用户写错。

    数据来源：`GROUP_MEMBERS[gid][BOT_QQ]` 在拉群成员列表时已按
    `card or nickname` 写入（见 _fetch_group_members 附近），无需额外请求。

    ⚠ 取舍：跨群别名会被当作"我"。若某群恰好有别人叫「蛋」，那条 @蛋 会误判成
      在叫我。真出现这种冲突时，把该名片从群名片去掉、或改用 `bot_nicknames`
      显式限定即可（显式列表永远优先且不受群名片影响）。
    """
    names: set = {str(BOT_QQ)}
    try:
        for n in _bot_nicknames():
            if n:
                names.add(str(n).strip())
    except Exception:
        pass
    try:
        for _gid, _members in (GROUP_MEMBERS or {}).items():
            if not isinstance(_members, dict):
                continue
            _n = str(_members.get(str(BOT_QQ)) or "").strip()
            if _n:
                names.add(_n)
    except Exception:
        pass
    names.discard("")
    return names


def _is_bot_self_name(name) -> bool:
    """这个 @ 名字是不是指 Bot 自己（含各群名片 / 全局昵称 / QQ 号）。"""
    n = str(name or "").strip()
    if not n:
        return False
    if n in _bot_self_names():
        return True
    # 归一化再比一次：全角/大小写/@ 差异（「ＡＡＡ」vs「AAA」）
    nn = _norm_nick(n)
    if not nn:
        return False
    for cand in _bot_self_names():
        if _norm_nick(cand) == nn:
            return True
    return False


# ── 自主学习语料卫生（2026-10-19）────────────────────────────────────
# 背景：`personality.txt` 的【自主学习成果】段被两类垃圾填满 —
#   ① 群里**另一个 AI 机器人**的发言（实测 另一个AI机器人：107+98 条 = 语料 37%）
#      → EggBloom 模仿另一个 AI 的腔调，这是"越来越像机器人"的直接来源；
#   ② bot 自己的**引用注记**（`[引用:xxx｜他在引用xxx，**不是在叫我**，别抢话]`）
#      → 被当作"用户原话"采集，再被黑话挖掘成「当前」「别抢话」「引用」。
# 这两个函数就是语料入口的闸门。

#: 命中即判定"这是个 AI 机器人"的昵称/名片关键词
_AI_BOT_NAME_RE = re.compile(
    r"(deepseek|chatgpt|gpt[-\s]?\d|gpt[-\s]?[a-z]|claude|gemini|qwen|通义|kimi|"
    r"文心|ernie|豆包|doubao|混元|hunyuan|llm|chatbot|\bbot\b|机器人|小助手|"
    r"ai[-\s]?assistant|人工智能)",
    re.IGNORECASE,
)


def _is_ai_bot_sender(group_id, uid) -> bool:
    """这个发送者是不是"另一个 AI 机器人"—— 是则**不学习**它的发言。

    判据（任一命中即排除）：
      ① `self_learn_ignore_qqs` 显式列出该 QQ；
      ② `self_learn_exclude_ai_names` 开着，且**全局昵称**命中 AI 关键词。

    ★ 只看**全局昵称**，不看群名片（2026-10-19 用户纠正）。
      实测同一个群里：
        · uid 10000004  全局昵称 `另一个AI机器人`、群名片空   → 真·AI 机器人
        · uid 10000005  全局昵称 `Texas`、群名片 `另一个AI机器人` → **真人**（扮的）
      群名片是**每个群可以随便改的 cosplay**，拿它判 AI 会把真人误杀；
      全局昵称才是账号自己的名字，机器人的昵称通常就是模型名。

    另设 `self_learn_allow_qqs` 白名单：即使昵称像 AI 也照学（人工兜底）。

    为什么必须排：实测群里那个 bot 的 107 条消息被当成"群友表达风格"学进
    personality.txt，EggBloom 于是开始用另一个 AI 的语气说话 ——
    用户反复反馈的"越来越像机器人"，这是最直接的来源。
    """
    s = str(uid or "").strip()
    if not s:
        return False
    try:
        if s in {str(x) for x in (_config.get("self_learn_allow_qqs") or [])}:
            return False          # 人工白名单优先：明确是真人就照学
        if s in {str(x) for x in (_config.get("self_learn_ignore_qqs") or [])}:
            return True
    except Exception:
        pass
    if not _config.get("self_learn_exclude_ai_names", True):
        return False
    try:
        # 只取全局昵称（GROUP_MEMBERS 里存的是 card or nick，含群名片，不能用）
        nom = str(resolve_qq_nick(s) or "").strip()
        if nom and _AI_BOT_NAME_RE.search(nom):
            return True
    except Exception:
        pass
    return False


def _strip_bot_annotation(text) -> str:
    """剥离 bot 自己加的引用注记，只留**用户当前这句原话**。

    入口 `text` 形如：
        `[引用:张三｜他在引用张三，**不是在叫我**，别抢话] 他说的原文\\n[当前] 我这句`
    要的是最后的「我这句」——前面那段是**程序生成的**，不是任何人的发言；
    混进语料后会被当成"群黑话"（实测挖出「别抢话」「不是在叫我」「他在引用」）。

    没有 `[当前] ` 标记时，退而剥掉开头的 `[引用:...]` 前缀。
    """
    t = str(text or "")
    if "\n[当前] " in t:
        return t.split("\n[当前] ", 1)[1].strip()
    t = re.sub(r"^\[引用:[^\]]*\]\s*", "", t)
    return t.strip()


def _bot_nicknames() -> List[str]:
    """Bot 的全部称呼（**可配多个**，配置中心「Bot 昵称」一行一个）。

    派生顺序（任一环节有值就用，都不空着）：
      1. `bot_nicknames`：显式多昵称列表（推荐）
      2. `bot_group_nick`：群名片（兼容旧字段）
      3. 默认 ["小a"]：只在全新安装、什么都没配时兜底
    """
    out: List[str] = []
    for v in (_config.get("bot_nicknames") or []):
        v = str(v).strip()
        if v and v not in out:
            out.append(v)
    g = str(_config.get("bot_group_nick") or "").strip()
    if g and g not in out:
        out.append(g)
    return out or ["小a"]


def bot_nick_in_text(text: str) -> str:
    """文本里命中的 Bot 称呼（返回原样写法），没命中返回 ''。大小写/全角/@ 均不敏感。"""
    if not text:
        return ""
    t = _norm_nick(text)
    if not t:
        return ""
    for n in _bot_nicknames():
        nn = _norm_nick(n)
        if nn and nn in t:
            return n
    return ""


def _group_trigger_words() -> List[str]:
    """群触发词。空列表/未配 → **自动派生**为 Bot 称呼（不再退化成"没有触发词"）。"""
    words = _config.get("group_trigger_words")
    words = [str(w).strip() for w in (words or []) if str(w).strip()]
    if words:
        return words
    return _bot_nicknames()


def _is_group_trigger(text: str) -> bool:
    """Check if text contains any configured group trigger word（大小写不敏感）。"""
    t = _norm_nick(text)
    if not t:
        return False
    for w in _group_trigger_words():
        nw = _norm_nick(w)
        if not nw:
            continue
        if nw in t or f"@{nw}" in t:
            return True
    return False


def quoted_text_calls_bot(quoted_text: str) -> str:
    """被引用的**原文**里有没有在叫 Bot。命中返回那个称呼，否则空串。

    为什么单独判一次（2026-10-27 真机）：群友把自己早先那句
    「@AAA无理由退货 讲解一下精神分析引论」引着又发一遍（等于重问），
    而原来的判据只看 `quoted_sender`（= 他自己，不是 Bot）→ 判成
    「他在引用别人，**不是在叫我**，别抢话」，一条明确冲 Bot 来的提问
    被当成空气。

    判据不能拿"谁写的"覆盖"写了什么"：引用者是谁只决定话题归属，
    被引用那句话里有没有叫我，才是要不要接的重点。
    """
    if not quoted_text:
        return ""
    n = bot_nick_in_text(quoted_text)
    if n:
        return n
    if _is_group_trigger(quoted_text):
        w = _group_trigger_words()
        return w[0] if w else "触发词"
    return ""


def quote_target_for(buffer: dict) -> int:
    """这次回复要不要带**引用**（指向"本轮正在回的那条"），返回 message_id 或 0。

    模式（配置中心 → 群聊 → 群引用回复）：
      off    永远不引用
      auto   ★默认：只在"对象明确"时引用。
             **冲 Bot 来的**（@ / 文本 @ / 叫「小a」/ 含昵称）也算"对象明确" ——
             2026-10-27 用户真机反馈：群里几路人同时说话时，Bot 回三行短句却不带
             引用，根本看不出在回哪条（实证：回「哦 / 然后呢 / 憋半天就这？」时
             上一句是别人 @ 它骂的，读者只能靠猜）。
             旧判据只有"合并了多条历史"或"对方本来就发了引用"，恰好漏掉最常见的那种。
             另外两种情况（合并多条历史、消息本身带引用）保留：不说清楚就成自说自话。
      always 每条都引用
    """
    mode = str(_config.get("group_quote_mode", "auto") or "auto").strip().lower()
    if mode == "off":
        return 0
    buf = buffer or {}
    try:
        mid = int(buf.get("message_id") or 0)
    except Exception:
        mid = 0
    # ★ 2026-10-27：只把 0 当"没有 id"，**负数照用**。
    #   SnowLuma 给的消息 id 常是负数（实测 17 条被引用消息里 12 条是负的），
    #   原来这里 `mid <= 0` 等于把七成消息的引用能力直接关掉 —— 这也是
    #   "引用回复几乎从没出现过"的第二个原因（日志里只有 2 次 群回复(引用)）。
    #   风险兜底：驱动若认不了这个 id，会打 could not quote 并**丢掉引用继续发**
    #   （见本文件 tests/test_quote_reply.py 开头记录的实测），最坏退回今天的样子。
    if mid == 0:
        return 0
    if mode == "always":
        return mid
    # 冲 Bot 来的：@ / 文本 @ / 叫了「小a」/ 含任一称呼
    if (buf.get("is_at_bot") or buf.get("has_text_at")
            or buf.get("has_xiao_a") or buf.get("has_bot_nick")):
        return mid
    try:
        merged_n = int(buf.get("merged_n") or 0)
    except Exception:
        merged_n = 0
    if merged_n >= 1 or buf.get("has_quote"):
        return mid
    return 0


def _schedule_observe(group_id: str, text: str, after: float, reason: str = ""):
    """安排"潜水前观察"：N 秒后把这段上下文再看一眼（只会发生一次）。"""
    try:
        after = max(5.0, min(300.0, float(after or 0)))
    except Exception:
        after = 25.0
    rec = {"text": text, "at": time.time(), "timer": None, "reason": reason}
    t = Timer(after, lambda gid=group_id: _observe_second_look(gid))
    try:
        t.daemon = True
    except Exception:
        pass
    rec["timer"] = t
    GROUP_OBSERVE[group_id] = rec
    t.start()
    log(f"群[{group_id}] [观察] 暂不插话，{int(after)}s 后再看一眼"
        + (f" · {str(reason)[:80]}" if reason else ""))


def _observe_second_look(group_id: str):
    """观察窗口到点：把这批上下文重新过一次决策（observe_retry=1 → 不会再排观察）。"""
    rec = GROUP_OBSERVE.pop(group_id, None)
    if not rec:
        return
    with _buffer_lock:
        fresh = GROUP_MSG_BUFFER.get(group_id)
    if fresh:
        # 期间有**新消息**进来 → 正常路径会以更新的上下文处理，这里不重复动作
        log(f"群[{group_id}] [观察] 窗口内有新消息，改走新上下文（本次观察作废）")
        return
    # ★ 2026-10-25：相位不空闲时不写合成批次 —— 那会覆盖别人正在用的待批数据。
    #   回看本来就只发生一次，作废即可，不排队（排队等于多一次模型调用）。
    with _pipeline_lock:
        _phase = (GROUP_PIPELINE.get(group_id) or {}).get("phase", "idle")
    if _phase != "idle":
        log(f"群[{group_id}] [观察] 正在{_phase}，本次回看作废")
        return
    text = str(rec.get("text") or "").strip()
    if not text:
        return
    log(f"群[{group_id}] [观察] 第二次决策（原理由：{str(rec.get('reason') or '')[:60]}）")
    with _buffer_lock:
        GROUP_MSG_BUFFER[group_id] = {
            "text": text, "sender": "", "time": time.time(),
            # 这里是"人工合成一批"：不经过定时器，process_buffered_message
            # 被本函数直接同步调用，deadline 只是为了字段齐备
            # （缺了会被消息线程当成"过期已久"，把这条立刻结算掉）。
            "deadline": time.time() + 1, "first_time": time.time(),
            "is_at_bot": False, "has_xiao_a": False, "has_bot_nick": False,
            "has_quote": False, "quote_me": False, "has_text_at": False,
            "in_session": False, "has_context": False, "images": [],
            "observe_retry": True, "timer": None,
            # 流水线字段（2026-10-25）：合成批次没有触发者，也没有戳主。
            "forced": False, "poke_uid": None,
        }
    try:
        process_buffered_message(group_id)
    except Exception as e:
        log(f"群[{group_id}] [观察] 第二次决策异常: {e}")


def _extract_qq(s) -> str:
    """从驱动回传的"昵称(数字号)"嵌套串里抠出纯数字 QQ（2026-10-14）。

    线上实证：SnowLuma get_msg 把被引用消息的 sender.user_id 返回成
    "占位昵称(10000003)" 这种显示串（日志里 [占位昵称(占位昵称
    (10000003))] 的双层套娃就是它）→ `quoted_sender == BOT_QQ` 永不相等 →
    别人引用 Bot 的话时"引用我必回"失效，又回到分不清谁在说话。"""
    s = str(s or "")
    m = re.search(r'\((\d{5,12})\)\s*$', s)
    return m.group(1) if m else s


def _strip_context_marks(text: str) -> str:
    """剥掉回复里被模型模仿来的**上下文标记**。

    ★ 2026-10-27 真机：发出过「[18:46] 别戳」。
      模型看到输入行首的 `[HH:MM] [名字·锚] 原文`，把 `[18:46]` 当成
      "回复该有的格式"照抄了 —— 群里看就是机器人突然报时。

    权威实现是 `reply_budget.strip_ctx_marks`（那边还要用它算字数、
    判完整句）。这里只做转发，不重复实现 ——
    **一条规则只能有一个权威源**（仓库已因"同一概念两处判据"踩过多次）。
    """
    try:
        return reply_budget.strip_ctx_marks(text)
    except Exception:
        return text


def _line_is_directed_to_others(line: str, *, addressed: bool = False) -> bool:
    """这一行是不是"用户在跟别人说话"（该跳过）。

    ★ 2026-10-27 修真机事故（群 829854136 18:45 / 18:46）：

        [决策放行] 触发: [18:45] [外莱塔尼亚·6acy] [引用:(/ω＼)｜引用我，必回] …
        跳过定向他人的消息: [当前] @(/ω＼)  这是你的求救信号吗

      用户 @了 Bot 问「讲解一下精神分析理论」，**整批却被丢掉** ——
      因为批次里有一行 `[当前] @(/ω＼) …`（用户引用了一条喊群主的话），
      逐行检测时它被判成"这条在跟群主说话"，于是 return 掉了整批。

      两个独立的错：
        ① **被引用的历史**里的 @别人 —— 那是别人之间的事，与 Bot 无关
        ② **用户当前这句**里的 @别人 —— 用户引用了别人的话再问你，
           这是在跟你说话，不是跟别人说话

      判据：
        · 这行是回抓的历史（行首 `[HH:MM]`）→ 不算（①）
        · 这行带 `[当前]` → 是用户当前的话，但**若整批有在叫我的信号**，
          就不算定向他人（②）；否则照旧判
        · 其他行 → 照旧判
    """
    # ① 回抓的历史：行首是 [HH:MM]，不是用户当前的话
    if re.match(r'^\[\d{1,2}:\d{2}\]\s', line):
        return False
    # ② 用户当前这句：整批在叫我 → 不因 @别人 而丢弃
    if line.lstrip().startswith("[当前]") and addressed:
        return False
    content = re.sub(r'^\[.+?\]\s*', '', line).strip()
    if not content:
        return False
    return _is_at_others(content)


def _is_at_others(text: str) -> bool:
    """检测消息是否定向给他人（非 Bot/非小a）。"""
    # 先检查是否包含触发词
    if _is_group_trigger(text):
        return False
    if text.startswith("@"):
        m = re.match(r'^@(\S+)', text)
        if m:
            at_name = m.group(1)
            # ★ 2026-10-19：认 Bot 的**全部**称呼（含别的群的群名片），
            #   不能只比 bot_group_nick 一个值 —— 否则换个群的叫法就把自己当别人。
            if not _is_bot_self_name(at_name):
                return True
    # 句末 @ 他人
    end_at = re.search(r'@(\S{1,15})$', text)
    if end_at:
        at_name = end_at.group(1)
        if not _is_bot_self_name(at_name):
            return True
    # 句中 @ 他人
    all_ats = re.findall(r'@(\S{1,15})', text)
    _bot_names = {_norm_nick(n) for n in _bot_self_names() if str(n).strip()}
    for at_name in all_ats:
        # ★ 只要有一个 @ 指的是我，这条就是冲我说的（哪怕句中还 @ 了别人）——
        #   "你不要和 @占位群友C 说话" 是在命令我，不是群友互聊，不能当"定向他人"跳过。
        #   （2026-10-14：extract_text 开始保留 @昵称 后，旧逻辑会把这种
        #   "@我 且 @别人" 的句子误判成定向他人而整条吞掉。）
        if _is_bot_self_name(at_name) or _norm_nick(at_name) in _bot_names:
            return False
    for at_name in all_ats:
        if _is_bot_self_name(at_name):
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


def _display_name(sender, group_id="") -> str:
    """把 QQ 号变成模型能读懂的说话人标签（2026-10-13 "乱扣帽子"修复）。

    线上实证（22:30 群[20001]）：合并窗口里别人说的话被标成**裸 QQ 号**
    （`buffer["text"] += f"[{sender}] ..."`），模型对不上号只能猜 →
    把 另一个AI机器人 说的"当段子听"算到了 示例用户 头上（"你自己说全是段子的"）。
    规则：① 我自己 → "我(Bot)"（自己的话绝不能被当成别人——cfd2682 把自己
    登记进近期缓存后，回抓上下文里会出现自己的消息，标昵称会让模型以为
    是另一个人在说话）② **本群名片** ③ 全局昵称 ④ 都没有 → 裸号（最后兜底，
    至少不编造名字）。

    ★ 2026-10-19 补第 ②：**群名片优先**。
      QQ 的 `nickname` 是**全局**昵称，`card` 才是**本群名片** —— 同一个人在不同
      群可以挂不同名片。此前只走 `resolve_qq_nick`（内部 `get_stranger_info`
      只回全局昵称，不含群维度），于是 A 群的名字被带进 B 群：用户实测本群
      叫「e蛋」，日志和对话里却是别群昵称「占位名片B」。
      这不只是显示问题 —— 模型会照着这个错名字判断"这句话是不是在叫我"。
      `GROUP_MEMBERS[gid][uid]` 在群消息入口用 `sender.card` 实时刷新（见群聊分支）。
    """
    s = str(sender or "")
    if not s:
        return "群友"
    if s == str(BOT_QQ):
        return "我(Bot)"
    if group_id:
        _g = GROUP_MEMBERS.get(str(group_id)) or {}
        _card = str(_g.get(s) or "").strip()
        if _card:
            return _card
    return resolve_qq_nick(s) or s


def _identity_anchor(sender) -> str:
    """4 位 base36 短锚：改名不变、跨群稳定，且**不是** QQ 号。

    为什么不用 QQ 号做锚：群聊规则明令回复里不许出现号码，模型会把标签
    原样复述到群里。36^4 = 1.68M，百人群撞锚概率约 0.003%（1.5.9 §B1.2）。
    """
    s = str(sender or "")
    if not s:
        return "----"
    _h = hashlib.sha1((s + "|eggbloom").encode("utf-8")).digest()
    _n = int.from_bytes(_h[:4], "big") % (36 ** 4)
    _alpha = "0123456789abcdefghijklmnopqrstuvwxyz"
    _out = ""
    for _ in range(4):
        _out = _alpha[_n % 36] + _out
        _n //= 36
    return _out


def _speaker_tag(sender, group_id="") -> str:
    """`名字·锚`：名字供阅读，锚供判同一人（改名片不影响）。

    Bot 自己的话不加锚 —— 它只有一个身份，锚只会让模型以为还有别人。
    """
    if str(sender or "") == str(BOT_QQ):
        return "我(Bot)"
    return f"{_display_name(sender, group_id)}·{_identity_anchor(sender)}"


def _is_short_meaningless(text: str) -> bool:
    """判断短消息是否无意义。
    只认三类：①白名单里的敷衍词（嗯/哦/哈哈/行…）②纯数字（1/2/6…）
    ③（纯符号如"。"由 _is_nonsense 负责，不在这里管）。

    ★ 删掉的死逻辑（2026-10-13 线上实证）：旧版 `len(set(text)) <= 2 and
      len(text) <= 1` 恒等于 `len(text) <= 1` —— 单字消息一律判无意义，
      于是「对」「不」「有」这种正经回答全被"跳过无意义消息"吞掉
      （群[20001] 17:54 日志实证：[占位昵称2] 对 → 跳过）。
      真·敷衍单字（嗯/哦/哈/额/噗/行/好）本来就在 _SHORT_PHRASES 里，
      不需要这条把有意义的答案一起杀掉。"""
    if len(text) > 8:
        return False
    if text in _SHORT_PHRASES:
        return True
    if text.isdigit() and len(text) <= 3:
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


# ── 框架指令（2026-10-25）────────────────────────────────────────────
# 线上实证（群 829854136 11:46）：群友发 `#sl`，SnowLuma 框架自己回了
# 「SnowLuma 状态 / 版本 1.14.10 …」，Bot 又把同一条当成聊天，搜索一轮后答
# 「#sl是啥指令 / 我这边没这功能」——群里出现两条对同一件事的回复，且后一条是错的。
#
# 判据不是"以 # 开头"（`#每日一癫` 这种是真在跟人说话），而是
# **斜杠/井号 + 纯 ASCII 单词 + 整条消息就这一个词**：这正是框架命令的形态。
# 中文一律不算 —— `#今日份快乐` 必须照常当聊天处理。
_FW_CMD_RE = re.compile(r"^[/#!！．.]([A-Za-z][A-Za-z0-9_\-]{0,20})$")


def _is_framework_command(text: str) -> bool:
    """整条消息是否是框架/平台自己处理的指令（如 `#sl`、`/help`）。

    这类消息由协议层或框架回，Bot 再答一遍就是重复且通常答错（它并不知道
    框架有哪些命令）。只拦"整条就是命令"的形态，句中出现的 `#tag` 不受影响。
    """
    s = (text or "").strip()
    if not s or len(s) > 24:
        return False
    return bool(_FW_CMD_RE.match(s))


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
    if bot_nick_in_text(text):
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


# ═══ 决策器场感数据（2026-10-23）═══
# 决策插件判断"该不该插嘴"，此前只拿到最近消息的文本 + 五条 if-else 规则。
# 人判断插不插嘴靠的不是规则表，是这三件事：自己上次开口多久前、
# 这会儿群里是不是已经太吵（自己占比）、对话停在哪。
# 这三项 bot_core 手上都有，以前一个都没往决策器传 —— 于是它只能用
# 「两人互聊且与Bot无关 → 不回复」这种粗规则硬套。
# 这里只输出**原始数值**，不输出结论：判断权仍在决策器。
def _decider_last_reply_ago(group_id) -> float:
    """Bot 在这个群上一次发言距今多少秒。没有记录 → -1。"""
    try:
        ts = GROUP_LAST_BOT_MSG.get(group_id)
        if not ts:
            return -1.0
        return round(max(0.0, time.time() - float(ts)), 1)
    except Exception:
        return -1.0


def _decider_bot_share(group_id, window=300.0) -> float:
    """最近 window 秒内，Bot 发言占全部消息的比例（0~1）。取样不足 → -1。

    这是"存在感惩罚"的输入：真人不会在群里占三成话量。
    bot_core 不自己扣分（那会让决策器失去判断依据），只报数。

    数据结构（别写错，我第一版写错过一次）：
      GROUP_RECENT_MSGS[gid] = [(ts, sender, text, mid, imgs), ...]
      GROUP_BOT_REPLIES[gid]  = [(ts, text), ...]
    后者是二元组，解包只能用 (ts, _)；写成 (t, _) 再引用 ts 会 NameError。
    """
    try:
        now = time.time()
        msgs = GROUP_RECENT_MSGS.get(group_id) or []
        humans = sum(1 for m in msgs if now - float(m[0]) <= window)
        bots = 0
        for _item in (GROUP_BOT_REPLIES.get(group_id) or []):
            try:
                if now - float(_item[0]) <= window:
                    bots += 1
            except (TypeError, ValueError, IndexError):
                continue
        total = humans + bots
        if total < 3:
            return -1.0
        return round(bots / total, 2)
    except Exception:
        return -1.0


def _decider_idle_gap(group_id) -> float:
    """本批最后一条消息与当前时刻的间隔（秒）。-1 = 无数据。

    间隔大 = 对话已经停了，这时候接话比插话更合适；间隔极小 = 正在热聊，
    插进去容易打断。这个数只有缓冲区知道（合并后消息是批量的）。
    """
    try:
        msgs = GROUP_RECENT_MSGS.get(group_id) or []
        if not msgs:
            return -1.0
        return round(max(0.0, time.time() - float(msgs[-1][0])), 1)
    except Exception:
        return -1.0


def _decider_closed(group_id):
    """对话是否闭合：最后一条是"应答词"（哦/好/行/嗯/收到/…）→ True。

    判据故意窄：只有明确的收束词才算闭合。
    「哈哈」「6」这种是笑，不是结束，算 False（还在来回）。
    返回 None = 数据不足，让决策器自己看文本判断。
    """
    try:
        msgs = GROUP_RECENT_MSGS.get(group_id) or []
        if len(msgs) < 2:
            return None
        last = str(msgs[-1][2] or "").strip()
        if not last:
            return None
        _CLOSERS = ("哦", "噢", "哦哦", "好", "好的", "好吧", "行", "行吧",
                    "嗯", "嗯嗯", "收到", "知道了", "了解", "ok", "okay",
                    "算了", "没事", "没事了", "睡觉了", "我先忙")
        return last in _CLOSERS or last.rstrip("。．.~～ ") in _CLOSERS
    except Exception:
        return None


def process_buffered_message(group_id):
    """流水线唯一出口：同一群在任一时刻只有一个批次在跑。

    返回 True 表示本次实际跑了一轮；False 表示已排队（相位忙），
    或该群没有待处理批次。

    ★ 2026-10-25：此前四个入口（两个 Timer、缓冲区过期分支、观察回看）
      直接调本函数，函数入口即 pop，谁也不知道别人在跑 —— 同一群能同时
      生成两轮并各发一条。现在入口语义从"立刻处理"改为"保证被处理"，
      在途期间到达的批次排队，本轮结束后立刻续跑。
    """
    return _pipeline_run(group_id)


def _new_pipeline_state():
    return {"phase": "idle", "since": time.time(), "pending": False,
            "last_reply_at": 0.0}


def _phase_set_locked(st, phase):
    """持 _pipeline_lock 时改相位。"""
    st["phase"] = phase
    st["since"] = time.time()


def _phase_set(group_id, phase):
    """不持锁时改相位（生成/发送阶段的标记用）。"""
    with _pipeline_lock:
        st = GROUP_PIPELINE.setdefault(group_id, _new_pipeline_state())
        if st["phase"] != "idle" and st["phase"] != phase:
            log(f"[流水线] 群[{group_id}] {st['phase']} → {phase}")
            _phase_set_locked(st, phase)


def _pipeline_acquire(group_id) -> bool:
    """取相位。已被占用则置 pending 并返回 False；超时则强制接管。"""
    with _pipeline_lock:
        st = GROUP_PIPELINE.setdefault(group_id, _new_pipeline_state())
        if st["phase"] == "idle":
            log(f"[流水线] 群[{group_id}] idle → deciding")
        elif time.time() - st["since"] < REPLY_PHASE_MAX:
            st["pending"] = True
            return False
        else:
            log(f"[流水线] 群[{group_id}] {st['phase']} 超时 "
                f"{int(time.time() - st['since'])}s，强制接管")
        _phase_set_locked(st, "deciding")
        st["pending"] = False
        return True


def _pipeline_release(group_id, again: bool = False) -> bool:
    """结束本轮。有待批时在锁内直接续到 deciding，返回 True。

    不先置 idle 再回循环顶部检查：那会让第三个线程在两步之间插进来。
    """
    with _pipeline_lock:
        st = GROUP_PIPELINE.get(group_id)
        if st is None:
            return False
        if again and st["pending"]:
            st["pending"] = False
            log(f"[流水线] 群[{group_id}] {st['phase']} → deciding（续跑待批）")
            _phase_set_locked(st, "deciding")
            return True
        st["pending"] = False
        log(f"[流水线] 群[{group_id}] {st['phase']} → idle")
        _phase_set_locked(st, "idle")
        return False


def _pipeline_run(group_id) -> bool:
    if not _pipeline_acquire(group_id):
        return False
    while True:
        try:
            _pipeline_step(group_id)
        except Exception:
            _pipeline_release(group_id)
            raise
        if not _pipeline_release(group_id, again=True):
            return True


def _pipeline_merge_locked(buffer, *, group_id, sender, text, now,
                           at_bot=False, xiao_a=False, bot_nick=False,
                           quote=False, text_at=False, in_session=False,
                           images=None, forced=False, source="message",
                           poke_uid=None, force=False):
    """持 _buffer_lock 时把一条事件并进已有批次，返回要启动的 Timer。

    返回 None 表示没并（批次已过 deadline，或调用方传了空批次）。
    Timer 由调用方在锁外 start()：回调内部要拿同一把锁。
    force=True 用于"结算发现批次还在、但它已过期"的情形：此时必须并进去，
    否则新建批次会把旧批次整个覆盖掉 —— 那些消息就丢了。
    """
    if buffer is None:
        return None
    _buf_dl = buffer.get("deadline")
    if not force and _buf_dl is not None and now > _buf_dl:
        return None
    # ★ 必须用 _speaker_tag（昵称 + 4 位锚），不能用裸 QQ 号：
    #   旧版这里直接 f"[{sender}]"，模型对不上号只能猜说话人
    #   → 22:30 线上把 A 说的话扣到 B 头上（"乱扣帽子"）。
    # ★ 2026-10-27（1.5.9 §B1.1）：**每条都带前缀**，同人续句也带。
    #   原来同人不带：A 说一句、B 插一句、A 再接一句，第三行紧贴 B，
    #   模型只能算成 B 说的。代价每批约 40 token。
    _tag = _speaker_tag(sender, group_id)
    buffer["text"] += f"\n[{_tag}] {text}"
    _spk = buffer.get("speakers")
    if not isinstance(_spk, dict):
        _spk = {}
        buffer["speakers"] = _spk
    _spk.setdefault(str(sender), _tag)
    buffer["time"] = now
    # 更新 @ / 「小a」/ 昵称 / 引用 / 文本 @ 标记
    buffer["is_at_bot"] = buffer.get("is_at_bot", False) or at_bot
    buffer["has_xiao_a"] = buffer.get("has_xiao_a", False) or xiao_a
    buffer["has_bot_nick"] = buffer.get("has_bot_nick", False) or bot_nick
    buffer["has_quote"] = buffer.get("has_quote", False) or quote
    buffer["has_text_at"] = buffer.get("has_text_at", False) or text_at
    buffer["in_session"] = buffer.get("in_session", False) or in_session
    # 任一条被点名，整批都算触发
    buffer["forced"] = buffer.get("forced", False) or forced
    # 同批多人戳时取最近一条（C2 用它决定追问权给谁）
    if source == "poke" and poke_uid:
        buffer["poke_uid"] = str(poke_uid)
    # 图片也要跟着合并：窗口内多条消息可能各自带图
    _bi = list(buffer.get("images") or [])
    for _ip in (images or []):
        if _ip and _ip not in _bi:
            _bi.append(_ip)
    buffer["images"] = _bi
    # 合并了一条有意义的消息 → 现在有上下文了
    if not is_meaningless_message(text):
        buffer["has_context"] = True
    # 取消之前的定时器，按 _batch_deadline 重算
    if buffer.get("timer"):
        buffer["timer"].cancel()
    # ── 同人临近消息重新等待（2026-10-23 用户定）──
    # 同一个人接着说话、且落在合并窗口内 → 倒计时重置，等他把话说完；
    # 换人不重置（对话在流转，该回了）。只在同人时重置，否则热闹群里永远
    # 等不到回复；8 秒硬顶兜底见 _batch_delay。
    _deadline = _batch_deadline(
        now=now,
        first_time=buffer.get("first_time"),
        same_sender=(sender == buffer.get("sender")),
        in_session=buffer.get("in_session", False),
        triggered=buffer.get("is_at_bot", False) or buffer.get("has_xiao_a", False),
        has_context=buffer.get("has_context", False),
        has_bare_image=bool(images and _is_pure_image_text(text)),
    )
    buffer["deadline"] = _deadline
    timer = Timer(_deadline - now,
                  lambda gid=group_id: process_buffered_message(gid))
    buffer["timer"] = timer
    return timer


def _pipeline_upsert(group_id, *, sender, text, images=None, flags=None,
                     forced=False, source="message", reply_mid=0,
                     poke_uid=None):
    """把一个入站事件并入该群批次；必要时建批并启动 Timer。

    flags: {"at_bot","xiao_a","bot_nick","quote","quote_me","text_at","in_session"}，
    缺省全 False。所有 Timer 一律在 _buffer_lock 之外创建与 start()。

    返回 "merged" / "expired" / "created"，仅用于日志与测试断言。
    """
    flags = flags or {}
    images = [p for p in (images or []) if p]
    now = time.time()
    at_bot = bool(flags.get("at_bot"))
    xiao_a = bool(flags.get("xiao_a"))
    bot_nick = bool(flags.get("bot_nick"))
    quote = bool(flags.get("quote"))
    text_at = bool(flags.get("text_at"))
    in_session = bool(flags.get("in_session"))
    quote_me = bool(flags.get("quote_me"))

    def _merge(buf, force=False):
        return _pipeline_merge_locked(
            buf, group_id=group_id, sender=sender, text=text, now=now,
            at_bot=at_bot, xiao_a=xiao_a, bot_nick=bot_nick, quote=quote,
            text_at=text_at, in_session=in_session, images=images,
            forced=forced, source=source, poke_uid=poke_uid, force=force)

    # ① 有批次且未过 deadline → 并进去
    with _buffer_lock:
        buffer = GROUP_MSG_BUFFER.get(group_id)
        timer = _merge(buffer) if buffer else None
    if timer is not None:
        timer.start()          # 锁外启动：回调内部要拿同一把锁
        return "merged"

    # ② 有批次但已过期 → 先结算旧批，再看它还在不在
    # ⚠️ process_buffered_message 内部要拿 _buffer_lock（非重入），必须在锁外调。
    if buffer is not None:
        process_buffered_message(group_id)
        with _buffer_lock:
            still = GROUP_MSG_BUFFER.get(group_id)
            # 还在 = 刚才没跑成（相位被占，已置 pending），或并发新建了新批次。
            # force=True：这条事件必须落在某个批次里，不能覆盖掉旧的。
            timer = _merge(still, force=True) if still else None
        if timer is not None:
            timer.start()
            return "expired"

    # ③ 新建批次：被 @ 或叫「小a」时回抓最近消息作为上下文
    grabbed_texts = []
    grabbed_chars = 0
    grabbed_images = []
    if at_bot or xiao_a:
        recent = GROUP_RECENT_MSGS.get(group_id, [])
        for entry in recent:
            t, s, tx = entry[0], entry[1], entry[2]
            _e_imgs = entry[4] if len(entry) > 4 else []
            if tx == text and s == sender and abs(t - now) < 1:
                continue  # 跳过当前这条消息本身
            # 纯图片消息（text 为空）也必须算上下文：它的"内容"在图片里
            _hhmm = datetime.datetime.fromtimestamp(t).strftime("%H:%M")
            if not (tx or "").strip() and _e_imgs:
                line = f"[{_hhmm}] [{_speaker_tag(s, group_id)}] [图片]"
            else:
                if is_meaningless_message(tx):
                    continue
                line = f"[{_hhmm}] [{_speaker_tag(s, group_id)}] {tx}"
            if grabbed_chars + len(line) > GROUP_REPLY_MAX_LENGTH:
                break  # 超X字停止
            grabbed_texts.append(line)
            grabbed_chars += len(line)
            for _ip in (_e_imgs or []):
                if _ip not in grabbed_images:
                    grabbed_images.append(_ip)
            if len(grabbed_texts) >= 15:
                break  # 最多15条
        if grabbed_texts:
            log(f"群[{group_id}] 回抓{len(grabbed_texts)}条历史消息"
                + (f" +{len(grabbed_images)}图" if grabbed_images else ""))

    _new_tag = _speaker_tag(sender, group_id)
    buffer_text = ("\n".join(grabbed_texts) + ("\n" if grabbed_texts else "")
                   + f"[{_new_tag}] {text}")
    _merged_n = len([x for x in (grabbed_texts or []) if str(x).strip()])
    has_context = len(grabbed_texts) > 0
    buffer_images = []
    for _ip in (list(images) + grabbed_images):
        if _ip and _ip not in buffer_images:
            buffer_images.append(_ip)

    _deadline = _batch_deadline(
        now=now,
        first_time=now,
        same_sender=False,
        in_session=in_session,
        triggered=bool(at_bot or xiao_a or bot_nick or quote),
        has_context=has_context,
        has_bare_image=bool(images and _is_pure_image_text(text)),
    )
    delay = _deadline - now
    _new_timer = Timer(delay, lambda gid=group_id: process_buffered_message(gid))
    with _buffer_lock:
        GROUP_MSG_BUFFER[group_id] = {
            "text": buffer_text,
            "sender": sender,
            "time": now,
            # ★ 批次起点：整批 8 秒硬顶用（2026-10-23）。
            "first_time": now,
            # ★ 批次关闭时刻（2026-10-23）：定时器与"能否合并"共用。
            "deadline": _deadline,
            "message_id": reply_mid,        # 用于引用回复（QQ message_id）
            "merged_n": _merged_n,          # 本轮合并了几条历史消息
            "is_at_bot": at_bot,
            "has_xiao_a": xiao_a,
            "has_bot_nick": bot_nick,
            "has_quote": quote,
            "quote_me": quote_me,           # ★ 2026-10-19：此前从未写入
            "has_text_at": text_at,
            "in_session": in_session,
            "has_context": has_context,
            "images": buffer_images,
            # 本批参与人：{sender: 标签}，批次头用它给名单（1.5.9 §B1.3）
            "speakers": {str(sender): _new_tag},
            # ★ 2026-10-25 流水线：触发批次免过决策器；戳批次记下发起人。
            "forced": bool(forced),
            "poke_uid": str(poke_uid) if (source == "poke" and poke_uid) else None,
            "timer": _new_timer,
        }
    _new_timer.start()
    log(f"群[{group_id}] 新建缓冲区 delay={delay:.1f}s in_session={in_session} "
        f"forced={bool(forced)} back_grab={len(grabbed_texts)}")
    return "created"


def _batch_snapshot(group_id, batch):
    """本批可见的流水快照：(recent_msgs, bot_replies)，按批次截止时刻截断。

    ★ 2026-10-25 补（这次自查发现）：截断原先只写在 `_decide` 里，
      `_pipeline_step` 的第二次插件派发仍读实时缓存。决策插件的缓存键是
      `(群, 文本前80字, recent_msgs尾条时间戳, observe_retry)` —— 两处采到的
      尾条时间戳一旦不同就**不命中**，同一批会跑两次完整决策：多烧一次 flash，
      两次结论还可能打架（前一次 inject 放行、后一次 skip → plugin_skip 直接
      return），线上表现就是"决策放行了，群里一句话没有"。
      抽成一处，两边必须同一份数据；以后再加消费者也只能调这里。

    batch["time"] 是批次**最后一次并入事件**的时刻，等于"这批定稿时"。
    """
    _recent = list(GROUP_RECENT_MSGS.get(group_id, []))
    _breps = list(GROUP_BOT_REPLIES.get(group_id, []))
    _cut = batch.get("time")
    if _cut is None:
        return _recent, _breps
    try:
        _cut = float(_cut)
    except Exception:
        return _recent, _breps

    def _upto(seq):
        out = []
        for e in seq:
            try:
                if float(e[0]) <= _cut:
                    out.append(e)
            except Exception:
                continue
        return out

    return _upto(_recent), _upto(_breps)


def _decide(group_id, batch):
    """群回复的唯一决策点。返回 {"act", "reason", "ctx", "after"}。

    act: "reply" | "skip" | "observe"；ctx 是传给生成端的氛围/策略结论；
    after 只在 observe 时有意义。本函数只读数据，写 GROUP_OBSERVE 与打日志
    留在 _pipeline_step —— 便于单测直接调它。

    forced=True（被 @、叫「小a」、含 Bot 昵称、引用我、文本 @、会话模式）时不调
    决策插件：触发本身就是结论。forced 取批次上的值，合并时取或（任一条被点名，
    整批都算触发）；老批次缺这个键时按同一口径现算。
    """
    merged_text = str(batch.get("text") or "").strip()
    forced = batch.get("forced")
    if forced is None:
        forced = bool(batch.get("is_at_bot") or batch.get("has_xiao_a")
                      or batch.get("has_bot_nick") or batch.get("has_text_at")
                      or batch.get("in_session")
                      or (batch.get("has_quote") and batch.get("quote_me")))
    if forced:
        return {"act": "reply", "reason": "触发", "ctx": "", "after": 0}

    quote_forces = bool(batch.get("has_quote") and batch.get("quote_me"))
    _go = False
    _ctx = ""
    _reason = ""      # 决策插件给的"不建议插话"理由（含降级标记），仅用于日志
    _after = 0
    if PLUGIN_OK:
        try:
            pm = get_plugin_manager()
            if pm and pm.is_loaded("group_chat_decider") and pm.is_enabled("group_chat_decider"):
                # ★ 批次快照（2026-10-25）：决策只看本批截止前的数据。
                #   读实时缓存会把**下一批**的消息算进来 → 决策给这一批复述
                #   下一批的话题，下一批自己又回一遍（同一条消息回两次）。
                #   统一走 _batch_snapshot：_pipeline_step 的第二次派发用同一个
                #   函数，保证两处尾条时间戳相同（否则决策缓存不命中 → 跑两遍）。
                _recent_all, _breps_all = _batch_snapshot(group_id, batch)
                _decider_ctx = {
                    "recent_msgs": _recent_all,
                    "bot_replies": _safe_extract_col(_breps_all, 1),
                    "sender": batch.get("sender", ""),
                    "user_id": batch.get("sender", ""),
                    "is_at_bot": False, "has_xiao_a": False, "has_bot_nick": False,
                    "has_quote": quote_forces, "has_text_at": False, "in_session": False,
                    "observe_retry": bool(batch.get("observe_retry")),
                    "history": group_chat_history.get(group_id, []),
                    # ── 场感数据（2026-10-23）────────────────────────────
                    # 决策器原来只吃「最近消息文本 + 五条 if-else 规则」，
                    # 判断"该不该插嘴"靠的是规则表，不是场感 —— 人插嘴靠的
                    # 是：这句谁在跟谁说、自己上次开口多久前、群里现在是不是
                    # 已经太吵。这三项 bot_core 手上全有，以前一个都没传。
                    # 只传原始数据、不传结论：判断权仍在决策器。
                    "bot_last_reply_ago": _decider_last_reply_ago(group_id),
                    "bot_share_5min": _decider_bot_share(group_id),
                    "batch_idle_gap": _decider_idle_gap(group_id),
                    # merged_n = 回抓到的历史消息条数（不含当前这条）。
                    # 本批总条数 = 历史 + 当前这条 → +1。
                    "batch_msg_count": int(batch.get("merged_n") or 0) + 1,
                    "conversation_closed": _decider_closed(group_id),
                    # 注：当前这条的发送人已以 "sender" 传入。
                    #   decider 侧用它渲染【现在这条】的说话人 —— 模板原先从
                    #   recent_msgs[-1] 猜，而 recent_msgs 混着 bot 自己刚发的
                    #   消息（_remember_own_group_msg 写入），会猜成 bot 自己。
                    "bot_qq": BOT_QQ, "bot_name": _bot_nicknames()[0],
                    "bot_nicknames": _bot_nicknames(),
                }
                _res = pm.execute_hooks("before_reply", message=merged_text,
                                        group_id=group_id, context=_decider_ctx)
                # 只看群聊决策插件的判定，避免 ego 等低优先级注入误放行
                for _pn, _pr in (_res or []):
                    if _pn == "group_chat_decider" and isinstance(_pr, dict):
                        if _pr.get("action") == "inject":
                            _go = True
                            # ★ 收下决策结论：插件已经判断出「氛围 + 策略」，
                            #   以前这里只看 action、把 content 丢掉 —— 等于花一次
                            #   调用做判断、再把结论扔了，让主模型裸奔重猜。
                            _ctx = str(_pr.get("content") or "").strip()
                        elif _pr.get("action") == "observe":
                            # 潜水前观察：先记下，过一会儿再看一眼（不是直接结束）
                            _after = int(_pr.get("after") or 0)
                            _reason = str(_pr.get("reason") or "")[:150]
                        elif _pr.get("action") == "skip":
                            # ★ 把"为什么不回"记下来：此前只有一句通用"无触发条件"，
                            #   AI 挂了导致的静默 和 AI 判断"别插嘴" 在日志里长得一模一样。
                            _reason = str(_pr.get("reason") or "")[:150]
                # ── typed 并列派发：spec 插件同样有权请求"放行" ──
                # 语义与旧式一致（inject = 建议插话），否则 typed 插件无法参与决策。
                for _tpn, _tpr in _dispatch_typed_before_reply(
                        pm, message=merged_text, group_id=group_id,
                        context=_decider_ctx):
                    if isinstance(_tpr, dict) and _tpr.get("action") == "inject":
                        _go = True
                        log(f"群[{group_id}] [typed 决策] {_tpn} 建议插话")
        except Exception as e:
            log(f"群[{group_id}] [决策插件] 无触发预判异常: {e}")
    if _go:
        return {"act": "reply", "reason": "决策放行", "ctx": _ctx, "after": 0}
    if _after > 0 and group_id not in GROUP_OBSERVE:
        return {"act": "observe", "reason": _reason, "ctx": _ctx, "after": _after}
    return {"act": "skip", "reason": _reason, "ctx": "", "after": 0}


def _pipeline_step(group_id):
    """原 process_buffered_message 的主体：pop、决策、生成、分条发送。"""
    global GROUP_MSG_BUFFER, GROUP_SESSION, group_chat_history, startup_time
    # 清单 3.1：本函数运行在 Timer 线程，与消息处理线程并发动缓冲区 → pop 必须持锁。
    # 锁只包住 pop 本身，重活全在锁外（避免与消息线程的长段合并逻辑互等）。
    with _buffer_lock:
        buffer = GROUP_MSG_BUFFER.pop(group_id, None)
    if not buffer or not buffer["text"]:
        return
    
    merged_text = buffer["text"].strip()
    # 1.5.9 §B2：时序锚点。batch = 从批次第一条到开始处理（等批时长），
    # first = 从第一条到首条发出（用户真正感知的延迟）。没有这两个数，
    # 改完延迟不知道有没有用 —— 只能靠感觉。
    _seq_first = float(buffer.get("first_time") or time.time())
    _seq_ready = time.time()
    _seq_logged = False
    # 用户原文（**不含任何注入块**）：意图类判定（画图/指令）只看它 —— 否则注入的
    # 描述块/提示词会把消息误判成别的意图（实测：描述块命中画图正则 → 劫持去 ComfyUI）。
    _user_text_clean = merged_text
    plugin_intervened = False  # 提前初始化，防止短消息检测报错
    # ★ 决策结论（氛围/策略）：只有"无触发预判"分支会赋值；触发路径保持空串。
    #   提到函数级是为了下面注入时不必判断分支，避免 NameError。
    _decider_ctx_text = ""
    # 本轮现场资料（近况/局面/决策结论）→ 独立的 system 消息，不拼进用户原文。
    _scene_ctx = ""
    # ★ 决策器已放行（2026-10-23）：决策器判断该插话 → 主模型**不再有沉默权**。
    #   原来"说/不说"被判断两次（决策器一次 + 主模型 [不说话] 出口一次），
    #   两边结论不一致时主模型否决决策器 —— 等于花一次 flash 调用做判断再把结论推翻。
    #   现在决策器放行 = 必须开口，主模型的 [不说话] 只剩兜底（接不住时用）。
    _decider_approved = False
    #: 本轮回合预算（B3）。只有群聊生成分支会赋值；其余分支保持 None。
    _budget = None
    #: 本轮是否丢弃（B5）。函数级默认 False —— meme 那段在发送块外层，
    #: 只写在块里会在别的路径上 NameError。
    _dropped = False
    #: 情绪形式参数（C.1）：只改档位与首字延迟，不改文本。
    #: 读不到就全用中性值 —— 关闭情绪必须与"没有情绪"逐字节等价。
    _form = {"budget_shift": 0, "delay_scale": 1.0, "emoji_bonus": 0.0,
             "soft_punct": False, "filler_pool": "mid"}
    try:
        from plugins.ego.mood_engine import get_form_params as _mood_form
        _form = _mood_form(group_id)
    except Exception as e:
        log(f"[情绪] 形式参数读取失败（用中性值）: {e}")
    # ★ 本轮的文字是否真的发出去了（2026-10-25）：戳主的追问权只在
    #   发送成功之后才写，skip / observe / 生成为空 / 发送异常都不写。
    _sent_ok = False
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

    # ── 静默时段（Bot 在"睡觉"，2026-10-24）──────────────────────────
    # `quiet_hours` 此前只被 proactive_talk 读取 —— 管得住"自己想说话"，
    # 管不住"有人 @ 我，我答"。用户早就配了 1:00-7:00，凌晨被 @ 照样回。
    #
    # 这里的口径（判据集中在 harness.time_ctx.in_quiet_hours，不另写一份）：
    #   · 没被点名（@ / 「小a」/ Bot 昵称 / 引用我）→ 完全不回，也不进决策器
    #   · 被点名 + quiet_when_mentioned="wake"（默认）→ 照常走，只是下面注入
    #     【你正在睡觉】让回复变短、带困意；真人不装死，但也不会长篇大论
    #   · 被点名 + "ignore" → 一律不回
    # 放在开机冷却之后、一切 AI 调用之前：省的是一次真实请求，不是等它生成完再丢。
    _quiet_hit = False
    try:
        from harness.time_ctx import in_quiet_hours as _iqh
        _quiet_hit = _iqh(_config.get("quiet_hours"))
    except Exception:
        _quiet_hit = False
    if _quiet_hit:
        _mention = (buffer.get("is_at_bot", False)
                    or buffer.get("has_xiao_a", False)
                    or buffer.get("has_bot_nick", False)
                    or buffer.get("has_text_at", False)
                    or (buffer.get("has_quote", False) and buffer.get("quote_me", False)))
        _mode_q = str(_config.get("quiet_when_mentioned", "wake") or "wake").lower()
        if not _mention:
            log(f"群[{group_id}] 静默时段 {_config.get('quiet_hours')} 且没被点名，不回")
            return
        if _mode_q == "ignore":
            log(f"群[{group_id}] 静默时段 {_config.get('quiet_hours')}，"
                f"被点名但 quiet_when_mentioned=ignore，不回")
            return
        log(f"群[{group_id}] 静默时段 {_config.get('quiet_hours')} 被点名，"
            f"叫醒回复（话会短）")
    
    # 使用缓冲区中保存的 @、「小a」和 Bot 昵称标记
    is_at_bot = buffer.get("is_at_bot", False)
    has_xiao_a = buffer.get("has_xiao_a", False)
    has_bot_nick = buffer.get("has_bot_nick", False)
    has_quote = buffer.get("has_quote", False)
    quote_me = buffer.get("quote_me", False)
    has_text_at = buffer.get("has_text_at", False)
    # ★ 引用回复目标（QQ message_id）；0 = 这条不带引用
    _quote_id = quote_target_for(buffer)
    # ★ 触发口径：只有"引用的是我"才算被叫到；引用别人 → 只是上下文，交给决策判
    quote_forces = has_quote and quote_me
    # ★ 这批**有没有在叫我**的信号（2026-10-27）。
    #   用于逐行检测：既然对方明确在叫我，就不该因为某一行 @了别人
    #   而把整批丢掉（真机 18:46：用户 @我问精神分析，整批被丢）。
    _batch_addressed = bool(is_at_bot or has_xiao_a or has_bot_nick
                             or quote_forces or has_text_at)
    
    # ── 决策点（唯一）：_decide 返回 reply / skip / observe ──
    # 旧代码这段内联在这里，且只有"无触发"才会走到；现在触发批次也经过它，
    # forced=True 时短路为 reply，日志多一行「[决策放行] 触发」，用来回答
    # "这轮为什么回"。副作用（排观察、打日志）留在本函数，_decide 只读数据。
    in_session = buffer.get("in_session", False)
    _decision = _decide(group_id, buffer)
    _decider_ctx_text = str(_decision.get("ctx") or "")
    if _decision["act"] == "observe":
        _after = float(_decision.get("after") or 0)
        if _after > 0 and group_id not in GROUP_OBSERVE:
            _schedule_observe(group_id, merged_text, _after,
                              _decision.get("reason") or "")
        return
    if _decision["act"] == "skip":
        _why = str(_decision.get("reason") or "")
        log(f"群[{group_id}] [跳过] 无触发条件，忽略消息"
            + (f" · 决策理由：{_why}" if _why else "")
            + f": {merged_text[:80]}")
        return
    if _decision.get("reason") == "触发":
        log(f"群[{group_id}] [决策放行] 触发: {merged_text[:60]}")
    else:
        _decider_approved = True
        # 决策器放行后就不再处于"观察"状态（旧代码在 inject 分支里 pop）
        GROUP_OBSERVE.pop(group_id, None)
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
        # ★ 2026-10-27：改用 _line_is_directed_to_others ——
        #   旧版逐行判 _is_at_others(content)，把「回抓的历史里 @别人」
        #   和「用户引用别人再问我」都当成"在跟别人说话" → 整批被丢。
        if _line_is_directed_to_others(line, addressed=_batch_addressed):
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
    if is_at_bot or has_xiao_a or has_bot_nick or quote_forces:
        GROUP_SESSION[group_id] = {"remaining": _session_round_budget(), "last_msg_time": time.time()}
    
    # 没有任何有意义的消息 → 跳过回复（但会话已建立）
    if meaningful_count == 0:
        # 被 @ 但无文字内容 → 至少回应一下
        if is_at_bot:
            try:
                # ★ @ 我只发了一张图（没有文字）：以前这里回"（有人 @ 了我，但没说话）"，
                #   相当于对着一张图说"你没说话"。现在先把图看懂再说。
                _only_imgs = [p for p in (buffer.get("images") or []) if p and os.path.exists(p)]
                _img_blk = ""
                if _only_imgs and image_reply_mode() == "describe":
                    _img_blk = build_image_context_block(_only_imgs[:3], scene="群聊")
                if _img_blk:
                    prompt = "（有人 @ 了我，只发了一张图，没写字）\n" + _img_blk
                    log(f"群[{group_id}] [图片理解] @空但带图 · {len(_img_blk)} 字")
                elif filtered_noise:
                    noise_text = "、".join(filtered_noise)
                    prompt = f"（有人 @ 了我，但发的是无意义内容：\"{noise_text}\"，我完全没看懂）"
                else:
                    prompt = "（有人 @ 了我，但没说话）"
                _phase_set(group_id, "generating")
                reply = call_deepseek(prompt, [], group_id=group_id, is_group=True)
                if reply and reply.strip() not in ("[不说话]", "[跳过]"):
                    smart_send_group(group_id, reply, reply_id=_quote_id)
                    _record_bot_group_msg(group_id, reply)
                    log(f"群回复(@空){'(引用)' if _quote_id else ''}: {reply[:60]}")
            except Exception as e:
                log(f"群[{group_id}] @空 AI 调用异常: {e}")
        else:
            log(f"群[{group_id}] 跳过无意义消息: {merged_text[:60]}")
        return
    
    log(f"群[{group_id}] 合并消息: {merged_text[:200]}{'...共' + str(len(merged_text)) + '字' if len(merged_text) > 200 else ''}")

    # ── 插件钩子：before_reply（在 Bot 指向检测之前执行，让插件先决策）──
    plugin_skip = False
    plugin_reply = None
    # ★ 批次头给名单（1.5.9 §B1.3）：只给条数时，模型按最后一条说话人算整批。
    #   单人不加头 —— 那是噪音，还占 token。
    #
    # ★ 2026-10-27 补格式说明：回抓的历史消息每行带 `[HH:MM]` 时间戳前缀，
    #   而**当前**那条不带 —— 两种格式不一致，模型就学会了这个"格式"：
    #     [18:46] 别戳          ← 真机发出去的原文（用户投诉"回复把时间戳装进去了"）
    #   所以头里明说：方括号里的是**上下文标记**，回复正文里不许出现。
    _batch_head = ""
    try:
        _spk = buffer.get("speakers") or {}
        if isinstance(_spk, dict) and len(_spk) >= 2:
            _n_lines = sum(1 for _ln in merged_text.split("\n") if _ln.strip())
            _names = " / ".join(list(_spk.values())[:8])
            _batch_head = f"[本批 {_n_lines} 条 · {len(_spk)} 人：{_names}]"
    except Exception:
        _batch_head = ""
    # 格式说明**总是**加（哪怕单人）：它约束的是输出格式，与批内人数无关。
    _fmt_note = ("【格式】上面每行开头的 [HH:MM] 和 [名字·锚] 都是**给你看的上下文标记**，"
                 "不是正文的一部分。回话时不要带时间戳、不要带方括号、"
                 "不要模仿这些标记，只说你要说的话。")
    _batch_head = f"{_batch_head}\n{_fmt_note}" if _batch_head else _fmt_note

    def _ai_text(t: str) -> str:
        return f"{_batch_head}\n{t}" if _batch_head else t

    merged_text_for_ai = _ai_text(merged_text)
    # ── 群聊图片理解（用户报："群里发图打不开"）─────────────────────────
    # 主对话模型是纯文本的：原图塞进 messages 它看不到；而引用消息的 raw_message
    # 一串 CQ 里带着下载 URL，模型会去"打开"链接再回"图片我打不开啊 就一坨链接"。
    # 所以这里把本窗口收到的图（含被引用的图）交给视觉模型转成文字描述后注入。
    #
    # ★ 注入的是 **merged_text 本身**，不是 merged_text_for_ai：
    #   下面插件注入与"重复回复重试"两处会 **重建** merged_text_for_ai
    #   （`merged_text_for_ai = merged_text + "\n" + xxx`），只写 for_ai 会被覆盖掉
    #   —— 这个坑是 tests/test_image_understanding.py 第一次跑就抓到的。
    _imgs = []
    _need_img = True   # 图片要不要交给模型理解（默认要；下面按需关闭）
    try:
        _imgs = [p for p in (buffer.get("images") or []) if p and os.path.exists(p)]
        _mode = image_reply_mode()
        # ── 只发了张表情包 → 同回一张表情包（2026-10-13 用户拍板）──
        # 不读图、不解释图、不动用主模型 —— 真人斗图就是这么回的。
        # 回敬失败（表情库空/发送失败）才落回原来的"读图"路径；
        # 冷却中则直接静默（连发两张表情包比不回更怪）。
        _all_meme_imgs = _all_memes(_imgs)
        if _all_meme_imgs and _is_pure_image_text(merged_text):
            _mb = _meme_back_send(group_id, True, images=_imgs)
            if _mb == MEME_BACK_COOLDOWN:
                log(f"群[{group_id}] [表情包回敬] 冷却中，静默")
                return
            if _mb:
                log(f"群[{group_id}] [表情包回敬] 纯表情包消息 → 回敬 {_mb}")
                return
            _need_img = "force"   # 回敬没成 → 强制落回读图路径（纯图消息文本为空，过不了下面的按需判定）
        # ── 表情包按需理解（"不要让 AI 次次都读表情包"）──
        # 只对**纯表情包**（全是 GIF）生效：普通闲聊顺带甩个表情时不再无脑读图，
        # 省一次视觉调用，也杜绝模型输出"解释这张图"的 AI 味回复。
        # 照片/截图（JPG/PNG）维持原行为——那是发给它看的。
        # ★ 2026-10-22 收紧：`memes_only=` 显式传下去。旧写法 `is_at_bot` 在
        #   `_image_understanding_needed` 里**直接 return True**，于是"@我 + 一张
        #   斗图"照样读图 —— 用户抱怨的正是这种"什么都读"。现在表情包要读到
        #   强信号（图里/表情包/截图…）才行，@我 本身不算。
        if _imgs and _need_img is True and _all_meme_imgs:
            _need_img = _image_understanding_needed(
                merged_text, is_at_bot, quote_forces, memes_only=True)
            if not _need_img:
                log(f"群[{group_id}] [图片理解] 跳过（表情包，消息没在说它）")
        # ★ 照片/截图**不设门禁**：那是用户主动发给它看的东西，无条件读。
        #   （已有测试 tests/test_meme_back.py「照片/截图不受门禁影响」钉着这个决定；
        #   2026-10-22 收紧时我一度把门禁也套到照片上，被那条测试挡住，这里回退。）
        #   用户抱怨的成本点是"总是分析**表情包**"——表情包信息量低，读它是纯浪费。
        if _imgs and _need_img and _mode == "describe":
            try:
                _max = int(_config.get("reply_vision_max_images", 3) or 3)
            except Exception:
                _max = 3
            _blk = build_image_context_block(_imgs[:_max], scene="群聊")
            if _blk:
                merged_text = merged_text + "\n" + _blk
                merged_text_for_ai = _ai_text(merged_text)
                log(f"群[{group_id}] [图片理解] {min(len(_imgs), _max)} 张 · {len(_blk)} 字")
            else:
                log(f"群[{group_id}] [图片理解] {len(_imgs)} 张全部识别失败（按没看到图处理）")
    except Exception as e:
        log(f"群[{group_id}] [图片理解] 异常(忽略): {e}")
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
                # ★ 2026-10-25：与 `_decide` 同一份快照（同一个 _batch_snapshot）。
                #   旧版这里读 `list(GROUP_RECENT_MSGS[...])` 实时缓存 —— 决策器
                #   缓存键含 `recent_msgs` 尾条时间戳，两次派发采到的尾条不同就
                #   不命中：同一批跑两遍完整决策，第二次还可能返回 skip 把第一次
                #   的放行作废（plugin_skip → 直接 return，群里没有回复）。
                _snap_recent, _snap_breps = _batch_snapshot(group_id, buffer)
                _plugin_ctx = {
                    "recent_msgs": _snap_recent,
                    "bot_replies": _safe_extract_col(_snap_breps, 1),
                    "sender": buffer.get("sender", ""),
                    "user_id": buffer.get("sender", ""),
                    "is_at_bot": buffer.get("is_at_bot", False),
                    "has_xiao_a": buffer.get("has_xiao_a", False),
                    "has_bot_nick": buffer.get("has_bot_nick", False),
                    "has_quote": quote_forces,
                    "has_text_at": buffer.get("has_text_at", False),
                    "in_session": buffer.get("in_session", False),
                    "history": group_chat_history.get(group_id, []),
                    "bot_qq": BOT_QQ,
                    "bot_name": _bot_nicknames()[0],
                    "bot_nicknames": _bot_nicknames(),
                    # ★ 2026-10-25：漏传这个，观察窗口第二次决策就会再次返回
                    #   observe（缓存键靠它区分），排一个又一个观察 —— 无限观察。
                    #   决策插件那边 1797 行专门写过注释提醒，宿主这侧当时没跟上。
                    "observe_retry": bool(buffer.get("observe_retry")),
                }
                intervention = pm.execute_hooks("before_reply", message=merged_text, group_id=group_id, context=_plugin_ctx)
                # ── typed 并列派发 → **并入同一个 intervention 列表** ──
                # 关键：并入同一列表而非另起一段处理，才能让 skip/override/inject
                # 在下面同一套 priority 裁决里竞争。若分开处理，会出现
                # "旧式 skip 与 typed override 各说各话、后写者胜"的行为分叉。
                _typed_bi = _dispatch_typed_before_reply(
                    pm, message=merged_text, group_id=group_id, context=_plugin_ctx)
                if not intervention:
                    intervention = []
                if _typed_bi:
                    intervention = list(intervention) + list(_typed_bi)
                    log(f"[调试] typed 干预并入 {len(_typed_bi)} 项")
                log(f"[调试] interventions={intervention}")
                if intervention:
                    skip_reasons = []
                    override_reply = None
                    override_priority = None
                    inject_parts = []
                    _obs_after = 0        # 决策插件请求的观察秒数（>0 = 别急着回）
                    _obs_reason = ""
                    for plugin_name, result in intervention:
                        action = result.get("action")
                        priority = pm.get_meta(plugin_name).get("priority", 50)
                        if action == "skip":
                            skip_reasons.append(f"{plugin_name}:{result.get('reason','')}")
                        elif action == "observe":
                            # ★ 2026-10-25：这条分支以前不存在。
                            #   `observe` 只在"无触发预决策"（10037 附近）那条路上被
                            #   消费，走到这里（in_session / @ / 引用 / 已建缓冲区等
                            #   情况）就被直接丢掉 —— 决策器说"25 秒后再看"，
                            #   宿主却当它没说过，照常注入 ego 上下文、照常搜索、照常回复。
                            #   线上实证（群 829854136 11:46 `#sl`）：
                            #   interventions 里明明有 observe，日志下一条却是
                            #   [插件注入] / [近况注入] / 搜索 / 回复。
                            #   一个说法有两处消费者，其中一处漏了一种取值 ——
                            #   本仓库最高频的 bug 形态，这次又踩了。
                            _obs_after = int(result.get("after") or 0)
                            _obs_reason = str(result.get("reason") or "")[:150]
                        elif action == "inject":
                            content = result.get("content", "")
                            prompt_blocks = result.get("prompt_blocks") or []
                            if prompt_blocks:
                                content = "\n".join(prompt_blocks) + ("\n" + content if content else "")
                            # 决策器的结论已经由 _bg_parts 以现场资料注入 system；
                            # 这里再拼一次会让同一段策略出现两遍，模型更用力地
                            # 按条目交付（汇报腔的放大器）。
                            if plugin_name == "group_chat_decider" and _decider_ctx_text:
                                plugin_intervened = True
                                continue
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
                    # ── 观察优先于注入（2026-10-25）──
                    # 裁决顺序：skip > override > observe > inject。
                    # 决策器说"这会儿别接、25 秒后再看"时，ego 那份人格注入就算
                    # 准备好了也不该继续用 —— 否则等于"判了不接，还是接了"。
                    if _obs_after > 0 and not plugin_skip and not plugin_reply:
                        if group_id in GROUP_OBSERVE:
                            log(f"群[{group_id}] [观察] 已有观察窗口，不重复排: {merged_text[:40]}")
                        else:
                            _schedule_observe(group_id, merged_text, _obs_after, _obs_reason)
                        return
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
                        # 决策结论已经单独进 _bg_parts / scene_context；这里再留一份
                        # 等于同一段"回复策略"在 prompt 里出现两次，模型会更用力地
                        # 按条目交付（汇报腔的放大器）。
                        if _decider_ctx_text:
                            merged_inject = merged_inject.replace(
                                _decider_ctx_text, "").strip("\n")
                        if merged_inject:
                            merged_text_for_ai = _ai_text(merged_text + "\n" + merged_inject)
                        else:
                            merged_text_for_ai = _ai_text(merged_text)
                        log(f"群[{group_id}] [插件注入] {merged_inject[:60]}")
                        plugin_intervened = True
                    # ── 群聊近况注入（2026-10-10 决策重写 · 治"接不上话"）──
                    # 决策放行的插话路径没有回抓（回抓只在 @/叫小a 时做），模型
                    # history 里只有它自己说过的轮次 → 别人之间聊的事它看不见，
                    # 自然接不上。现在给"无触发+无回抓上下文"的回复补一段近况。
                    _bg_parts = []
                    if not (is_at_bot or has_xiao_a or has_bot_nick or quote_forces) \
                            and not buffer.get("has_context"):
                        _batch_before = buffer.get("time")
                        _gctx = _build_group_context_block(
                            group_id, BOT_QQ, before=_batch_before)
                        if _gctx:
                            _bg_parts.append(_gctx)
                            log(f"群[{group_id}] [近况注入] {_gctx.count(chr(10))-1}条")
                    # ── 自我记忆 / 当前局面 / 决策结论（2026-10-21，1.5.7）──
                    #   三者都是**当轮事实**，只拼进本轮发给模型的消息，**不写 history**：
                    #   history 里只留原始对话，否则注入块下一轮会被当成"用户说的话"。
                    #   顺序按"越具体越靠后"：自己说过什么 → 现在什么局面 → 本轮决策结论。
                    _self_blk = _self_lines_block(group_id)
                    if _self_blk:
                        _bg_parts.append(_self_blk)
                    _sit_blk = _situation_block(group_id, before=buffer.get("time"))
                    if _sit_blk:
                        _bg_parts.append(_sit_blk)
                    if _decider_ctx_text:
                        _bg_parts.append(_decider_ctx_text)
                        log(f"群[{group_id}] [决策结论注入] {_decider_ctx_text[:60]}")
                    if _bg_parts:
                        # ★ 2026-10-25：这些块以前直接拼在**用户消息**里，
                        #   模型看到的是一条"带标题的简报"，回复也跟着用汇报腔
                        #   （「我账上…」「我收到的原文…」）。现在交给 call_deepseek
                        #   作为**独立 system 消息**注入，用户消息保持原样。
                        _scene_ctx = "\n\n".join(_bg_parts)
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
    
    # 图片：群聊现在支持了（见上方"群聊图片理解"块）。
    # native 模式才把原图交给主模型（需要多模态主模型）；默认 describe 走视觉描述，
    # 这里传 []，避免纯文本主模型收到 image_url 直接报错。
    # ★ _need_img：按需判定为"消息没在说这张图"时，native 模式同样不交图 ——
    #   否则"次次都读表情包"只是从 describe 挪到了 native（2026-10-13）。
    images = list(_imgs) if (image_reply_mode() == "native" and _need_img) else []
    
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
            elif _decider_approved:
                # 决策器放行 = 该你开口了。这条和上面的插件提示不同：插件是"建议"，
                # 决策器是"判断"，所以措辞更硬。仍允许它在完全接不住时沉默
                # （比如消息是纯噪声但决策器误判），但必须给出接不住的理由，
                # 而不是随手甩 [不说话] 把决策器的判断作废。
                merged_text_for_ai += (
                    "\n[系统提示: 这条消息没有 @ 你，但判断器认为这会儿该你开口了。"
                    "请正常接话。只有在完全接不住时才输出 [不说话]。]")

            # ── 画图意图检测（只看用户原文，见函数开头的 _user_text_clean）──
            draw_pattern = re.compile(r'(帮我画|给我画|给我一[张只]|画一[张只]|来一[张只]|整一[张只]|出图|画个|画只|画张|画\b|生成.*?图|的图片|生辰.*?图|生日.*?图|做个.*?图|做.*?图)', re.IGNORECASE)
            if draw_pattern.search(_user_text_clean):
                draw_desc = _user_text_clean
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

            # ── 反应延迟（2026-10-22 用户反馈"回复等待太快了"）──
            #    人被打个招呼不会 0.5 秒就回：先看一眼消息长短、想一下怎么接。
            #    原有四层节奏（消息缓冲 3~15s / 观察窗口 / 句间 0.3~1s / 私聊间隔 3s）
            #    全是"攒消息"和"发出去之后"的，**生成之前是 0 秒** —— 这就是"太快"的根因。
            #    这里补上"看到 → 想"的那段时间。只影响时序，不碰 prompt、不影响成本。
            #    被 @ / 被戳时反而多等一点（被点名会愣一下），长消息也多等。
            _react_base = float(_config.get("reply_reaction_delay", 1.6)) * float(_form.get("delay_scale", 1.0))
            if _react_base > 0:
                _extra = 0.0
                if is_at_bot or has_xiao_a or has_bot_nick or has_quote:
                    _extra += 0.8
                if len(merged_text) > 50:
                    _extra += 0.7
                _react = _react_base + _extra + random.uniform(0, 0.6)
                log(f"群[{group_id}] [反应延迟] {_react:.1f}s"
                    + ("（被点名）" if _extra >= 0.8 else ""))
                time.sleep(_react)

            # 1.5.9 §B3：本轮回合预算。话术进 user 消息（dynamic），不进静态段 ——
            # 静态段会打断黄金哈希与前缀缓存。
            try:
                _tal = float(_config.get("persona_talkativeness", 0.0) or 0.0)
            except Exception:
                _tal = 0.0
            _budget = reply_budget.pick(
                shift=_budget_shift(_form.get("budget_shift", 0), _tal))
            merged_text_for_ai = f"{merged_text_for_ai}\n[系统提示] {reply_budget.prompt_line(_budget)}"
            _phase_set(group_id, "generating")
            reply = call_deepseek(merged_text_for_ai, images, group_id=group_id, is_group=True, personality_temperature=_ST.get("personality_temperature"), ego_inject_context=_ego_ctx, scene_context=_scene_ctx, sender=sender)

        # 群聊联网搜索循环（共用函数，最多2轮；标记兜底剥离，绝不漏发）
        reply = _apply_search_tag_loop(reply, merged_text_for_ai, group_id=group_id,
                                       is_group=True,
                                       temperature=_ST.get("personality_temperature"),
                                       ego_ctx=_ego_ctx, scene_ctx=_scene_ctx)

        if not reply or reply.strip() in ("[不说话]", "[跳过]"):
            # ★ 2026-10-27：把"没等到"和"不想说"拆开。
            #   两者都是空字符串，但原因完全相反：一个是我们放弃了、钱已经花掉、
            #   回复在后台还会生成；另一个是模型看完决定闭嘴。
            #   合成一句「AI决定不回复」会把人引到"决策器有问题"上去（真机踩过）。
            if _ai_timed_out():
                log(f"群[{group_id}] [超时放弃] 等模型太久，本批不发"
                    f"（不是模型决定沉默；该次生成的 token 已经计费）")
            else:
                log(f"群[{group_id}] AI决定不回复: {reply[:30] if reply else '(空)'}")
            return

        if reply:
            # 正常会话中剥离退场语（退场语仅在 ≤2字 结束会话时由代码发送）
            reply = strip_exit_phrases(reply)
            if not reply or not reply.strip():
                log(f"群[{group_id}] 剥离退场语后为空，跳过")
                return

            # ── 回复预算超了 → **重生**（1.5.9 §B3，2026-10-27 接上）──
            #   docstring 一直写着"调用方应当带『N 字超上限，重写』重生一次"，
            #   但主链从来没接 —— 于是超配额就直接 fit() 挑一条，真机（18:55）：
            #
            #     模型给 2 条共 33 字（超 B 档 32 字）：
            #       "你问点能查的行不行？"              (23 字) ← 敷衍
            #       "[10-27 18:55] 记不住，我又不是录音机" (10 字) ← 正面回答
            #     旧版 fit 取**最短** → 发了敷衍那句，把有内容的扔了。
            #
            #   用户原话：「花了钱为什么不发，只是较短，没有触发任何严禁规则」
            #   —— 钱花了、内容生成了、只是超了 1 个字，结果丢了一半。
            #
            #   正确顺序：超配额 → 让模型**按预算重写一次** → 还超才 fit 挑一条。
            #   重生是一次额外模型调用，但它换回的是"完整的一条回复"。
            if _budget and reply:
                try:
                    _hard_b = {
                        "max_chars": int(_config.get("reply_max_chars", 16) or 16),
                        "max_parts": int(_config.get("reply_max_parts", 3) or 3),
                        "max_total": int(_config.get("reply_max_total", 80) or 80),
                    }
                    _ok_b, _why_b, _st_b = reply_budget.check(reply, _budget, _hard_b)
                    if not _ok_b:
                        log(f"群[{group_id}] [预算] {_why_b} {_st_b} → 重生一次")
                        _regen = reply_budget.regen_hint(_why_b, _budget)
                        _reply2 = call_deepseek(
                            _regen + "\n" + merged_text_for_ai, images,
                            group_id=group_id, is_group=True,
                            personality_temperature=_ST.get("personality_temperature"),
                            ego_inject_context=_ego_ctx, scene_context=_scene_ctx,
                            sender=sender)
                        if _reply2 and _reply2.strip() not in ("[不说话]", "[跳过]"):
                            _ok_b2, _why_b2, _st_b2 = reply_budget.check(
                                _reply2, _budget, _hard_b)
                            if _ok_b2:
                                log(f"群[{group_id}] [预算] 重生通过 {_st_b2}")
                                reply = _reply2
                            else:
                                log(f"群[{group_id}] [预算] 重生仍超（{_why_b2}）"
                                    f" → 取一条完整的")
                                reply = _reply2
                        else:
                            log(f"群[{group_id}] [预算] 重生没给出内容 → 用原回复")
                except Exception as _be:
                    log(f"群[{group_id}] [预算] 重生异常（忽略）: {_be}")

            # 检查是否与最近回复高度相似，防重复唠嗑
            recent = [t for ts, t in GROUP_BOT_REPLIES.get(group_id, []) if time.time() - ts < 60]
            is_dup, matched = is_similar_to_recent(recent, reply)
            if is_dup:
                log(f"群[{group_id}] 重复回复: {reply[:40]} ≈ {matched[:40]}")
                # 重试换话回复，最多 2 次
                for retry_i in range(2):
                    retry_hint = "（注意：你上一条回复和之前的太像了，换一种说法，换个角度，不要说类似的话）"
                    reply = call_deepseek(retry_hint + "\n" + merged_text, images, group_id=group_id, is_group=True, personality_temperature=_ST.get("personality_temperature"), ego_inject_context=_ego_ctx, scene_context=_scene_ctx)
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
            _hist_user = merged_text[:300]
            _hist_reply = reply[:300]
            gh.append({"role": "user", "content": _hist_user, "ts": time.time()})
            gh.append({"role": "assistant", "content": _hist_reply, "ts": time.time()})
            # C2 存储对齐：群聊截断 30→16 轮（组装只取最近10轮）
            group_chat_history[group_id] = gh[-16:]

            # 任务 5.3 事件溯源试点：把**模型可见的形态**追加到仅追加会话日志。
            # 铁律「模型可见即已记录」——这里记录的字符串与进入 history、最终进 prompt 的
            # 完全一致（同为 [:300]），因此可由日志**纯函数回放**出同样的 messages。
            try:
                from harness import session_log as _slog
                if _slog.enabled_for(group_id):
                    _slog.append_user_msg(group_id, _hist_user)
                    _slog.append_bot_reply(group_id, _hist_reply)
            except Exception:
                pass

            # 统一记忆分发（1.5 任务 2.3）：回复侧同步（sandglass 画像/偏移/阶段 + super_memory 问答对）
            try:
                from sandglass_bridge import memory_feed
                memory_feed({
                    "text": merged_text,
                    "group_id": str(group_id),
                    "reply": reply,
                    # 群聊用户侧在消息入口（下方 memory_feed）已落沙一次，
                    # 回复侧只补 Bot 这一行；否则同一条消息会被记 2~3 遍
                    # （2026-10-10 沙漏 21:27 三连实证）。
                    "user_pre_logged": True,
                })
            except Exception:
                pass

            clean_reply, _meme_emotion = parse_meme_intent(reply)
            sent_text = clean_reply.strip() if clean_reply else ""
            sent_text = _trim_incomplete(sent_text, getattr(reply, "truncated", False))
            # ── 剥掉模型模仿来的上下文标记（2026-10-27）──
            #   模型看到输入里有 `[18:46] [名字·锚] 原文`，有时会把 `[18:46]`
            #   当成回复格式照抄（真机发出过「[18:46] 别戳」）。
            #   prompt 里已经写了格式说明，这里是兜底 —— 改过的文本一律删。
            sent_text = _strip_context_marks(sent_text)
            # ── 去AI味：规则级后处理（发送前清理破折号/套话/客服口吻等）──
            try:
                from de_ai import de_ai_post_process
                sent_text = de_ai_post_process(sent_text, _config)
            except Exception:
                pass

            if sent_text:
                # ── 插件钩子：after_reply（发送前，允许插件修改回复文本）──
                # ★ 2026-10-21：这里原来用 execute_hook（单数，**首个非 None 即返回**），
                #   配合 priority 升序（decider 5 → ego 20 → web_search 30），
                #   意味着 ego 只要返回 override，排在后面的 web_search 幻觉拦截就
                #   永远不会执行 —— 一个插件悄悄把另一个插件顶掉，且日志里看不出来。
                #   EGO 修好"丢弃人格改写"之后这个问题会立刻显形（人格一直返回 override），
                #   所以必须同时改成**逐个执行并链式串联**：后一个插件看到的是前一个
                #   改完的文本，这样「人格删违禁句 → 联网插件拦幻觉」能叠加生效。
                if PLUGIN_OK:
                    try:
                        pm = get_plugin_manager()
                        if pm and pm.has_hook("after_reply"):
                            _ar_text, _ar_chain = pm.execute_hook_chain(
                                "after_reply", message=merged_text,
                                group_id=group_id, reply=sent_text, context=_plugin_ctx)
                            if _ar_chain:
                                log(f"群[{group_id}] [插件覆盖] {' → '.join(_ar_chain)}: {_ar_text[:60]}")
                            if _ar_text != sent_text:
                                sent_text = _ar_text
                    except Exception as e:
                        log(f"[Plugin] after_reply error: {e}")
                # ── typed 并列派发：契约是返回 str（不是 dict）──
                # 放在旧式**之后**：让 spec 插件能覆盖旧式插件的结果，
                # 且"空串 = 抑制发送"的语义与原逻辑一致（空串不会进入发送分支）。
                try:
                    if PLUGIN_OK:
                        _tpm = get_plugin_manager()
                        _t_new = _dispatch_typed_after_reply(
                            _tpm, message=merged_text, group_id=group_id,
                            reply=sent_text, context=_plugin_ctx)
                        if _t_new is not None and _t_new != sent_text:
                            if _t_new == "":
                                log(f"群[{group_id}] [插件抑制] typed after_reply 返回空串 → 不发送")
                            else:
                                log(f"群[{group_id}] [typed 覆盖] {_t_new[:60]}")
                            sent_text = _t_new
                except Exception as e:
                    log(f"[Plugin] typed after_reply error: {e}")
                # ── 事后校验（2026-10-23）：说了做过，就查有没有真做 ──
                #  prompt 里的【诚实铁律】靠自觉，实测被绕过两次（08:48 声称在传
                #  706MB、09:11 声称点进去看了 commit 数，两次日志都零工具调用）。
                #  这里做机械校验：只拦**操作完成态**（已发送/已打开/存好了），
                #  不碰知识类表述（查到了/我记得），理由见 harness/reply_audit.py。
                try:
                    from harness import reply_audit as _ra
                    from harness import tool_runner as _tr2
                    _hits = _ra.find_unsupported_claims(sent_text, _tr2.recent_tool_calls())
                    if _hits:
                        _before = sent_text
                        sent_text = _ra.rewrite_unsupported(sent_text, _hits)
                        log(f"群[{group_id}] [事后校验] 声称做过但无工具调用，已改写: "
                            f"{_hits[0][:40]} → {sent_text[:40]}")
                        if not sent_text:
                            sent_text = _before      # 改空了就保留原文，宁可发也别吞
                except Exception as e:
                    log(f"[reply_audit] 校验跳过: {e}")
                # ── 重复回复闸（2026-10-23 真机反馈）──
                # 症状：11:17:10 和 11:17:18 各发一次完全相同的三行，群里看是复读机，
                # 又因为中间夹了一条自己发的表情包，像是"bot 在评论自己发的图"。
                # 根因不是同一条发两遍：ego 的 after_reply 覆盖是固定文本，不区分上下文，
                # 于是连续两条消息被覆盖成同一句。这里做**发送前**的最终比对，
                # 与最近 2 分钟内的已发内容相同就丢掉 —— 插件和模型两侧都拦不住的东西，
                # 只能在这一层挡。
                # ── 丢弃（1.5.9 §B5，2026-10-27 改判据）──
                # 旧判据是**绝对时长**（age > 固定秒数 → 丢），已证伪：
                # 实测 12 次丢弃里 11 次是「过时」，值全在 26–65 秒，
                # 而一次带搜索的回复结构上就不低于 25 秒 →
                # 「凡搜索必丢」，用户看到的「所有调用搜索工具的问题都无法回复」
                # 就是这么来的。判据细节见 reply_drop.py。
                #
                # 新判据只问一件事：**这条回复现在发出去，还接得上话吗？**
                #   ① 话头翻篇（有新消息盖过去）→ 丢，主判据，与耗时无关
                #   ② 官腔 → 丢
                #   ③ 时间只做极端兜底，线由实测 p99 导出（_latency）
                # 群里一直没人说话 → 不丢。没人被打扰，晚回不算过时。
                _dropped = False
                if sent_text:
                    # 生成期间又来了几条：数时间线上的新消息，不看等了多久
                    _new_msgs = 0
                    try:
                        _new_msgs = sum(1 for _e in (GROUP_TIMELINE.get(group_id) or [])
                                        if _e and _e[0] > _seq_first)
                    except Exception:
                        _new_msgs = 0
                    # 这一轮是不是有人在等（被 @ / 点名 / 引用）。
                    # 有人在等 → 容忍度收窄（对方以为你聋了比晚回更伤）。
                    _addressed = bool(buffer.get("forced") or buffer.get("is_at_bot")
                                      or buffer.get("has_xiao_a")
                                      or buffer.get("has_text_at"))
                    _age_now = time.time() - _seq_first
                    _hard_line, _hard_src = _latency.hard_line(addressed=_addressed,
                                                           group_id=str(group_id))
                    # 配置项保留为**覆盖**：写 0 = 不用时间兜底（合法，①②仍在）。
                    # 不配 → 用分布导出的线。这样配置不再是"设计值"，
                    # 只是"临时拉高/关掉"的开关。
                    _cfg_hard = _config.get("reply_stale_drop_sec", None)
                    if _cfg_hard is not None:
                        try:
                            _cfg_v = float(_cfg_hard)
                            if _cfg_v > 0:
                                _hard_line = max(_hard_line, _cfg_v)
                                _hard_src = f"配置{_cfg_v:.0f}s"
                            else:
                                _hard_line = 0.0     # 显式关掉时间兜底
                                _hard_src = "配置关闭"
                        except Exception:
                            pass
                    _why = reply_drop.drop_reason(
                        new_msgs=_new_msgs,
                        age=_age_now,
                        turns=int(_config.get("reply_stale_turns", 2) or 0),
                        hard_sec=_hard_line,
                        addressed=_addressed,
                        text=sent_text,
                        official_re=_OFFICIAL_RE)
                    # 观测：无论丢不丢都记一笔 —— 分布就是这么攒出来的。
                    # 带群号：测试群（_test_*）的样本会被 reply_drop 过滤掉，
                    # 不然测试脚本 import bot_core 就能把真实分布带偏。
                    _latency.note(_age_now, addressed=_addressed,
                                  group_id=str(group_id))
                    if _why:
                        log(f"群[{group_id}] [丢弃:{_why}] 本批不发"
                            f"（线 {_hard_src}）")
                        _dropped = True
                    else:
                        _st = _latency.stats(str(group_id))
                        log(f"[时序] 群{group_id} 本批 age={_age_now:.1f}s "
                            f"线={_hard_line:.0f}({_hard_src}) "
                            f"新消息={_new_msgs} 在等={'是' if _addressed else '否'} "
                            f"| 样本 n={_st.get('n', 0)} p90={_st.get('p90', '-')} "
                            f"p99={_st.get('p99', '-')}")
                # ── 回复预算（1.5.9 §B3）──
                # 240 字截断会产出半句，比长更假，所以超配额时取最短一条。
                if sent_text and _budget and not _dropped:
                    _hard = {
                        "max_chars": int(_config.get("reply_max_chars", 16) or 16),
                        "max_parts": int(_config.get("reply_max_parts", 3) or 3),
                        "max_total": int(_config.get("reply_max_total", 80) or 80),
                    }
                    _ok_b, _why_b, _st_b = reply_budget.check(sent_text, _budget, _hard)
                    if not _ok_b:
                        log(f"群[{group_id}] [预算] {_why_b} {_st_b}")
                        sent_text = reply_budget.fit(sent_text, _budget, merged_text)
                        log(f"群[{group_id}] [预算] 取一条完整的：{sent_text[:30]}")
                    elif not reply_budget.anchor_ok(sent_text, merged_text):
                        log(f"群[{group_id}] [预算] 锚点不足（与触发消息无 2-gram 且 <8 字）")
                # 从这一刻起算"发送中"：去重闸、分条发送、表情包、纯图兜底都在这段里。
                _phase_set(group_id, "sending")

                def _rough(s):
                    return "".join(ch for ch in str(s or "") if ch.strip())
                _dup = ""
                for _t0, _prev in reversed((GROUP_BOT_REPLIES.get(group_id) or [])[-3:]):
                    if time.time() - _t0 > 120:
                        break
                    if _rough(_prev) and _rough(_prev) == _rough(sent_text):
                        _dup = _prev
                        break
                if _dropped:
                    pass
                elif _dup:
                    log(f"群[{group_id}] 与 2 分钟内的回复重复，跳过发送: {sent_text[:40]}")
                elif smart_send_group(group_id, sent_text, reply_id=_quote_id):
                    _sent_ok = True
                    _speak_note(group_id, "out")
                    if not _seq_logged:
                        _seq_logged = True
                        log(f"[时序] 群{group_id} batch={_seq_ready - _seq_first:.1f} "
                            f"gen_send={time.time() - _seq_ready:.1f} "
                            f"first={time.time() - _seq_first:.1f}s")
                    # 记上引用目标的 id：负数 id 能不能被驱动接受，就看这一行
                    # 之后群里有没有真的出现引用卡片（也便于事后按 id 对账）。
                    log(f"群回复{'(引用 id=' + str(_quote_id) + ')' if _quote_id else ''}: {sent_text[:60]}")
                    track_bot_reply(group_id, sent_text)
            else:
                log(f"群[{group_id}] 仅表情标记，跳过文本发送")

            # 表情包附着：AI 标注了 [表情] 或自动检测到情绪时发一张
            meme_file = None if _dropped else try_send_meme(group_id, reply)
            # 图发出去了也算"回应过了"：模型只回一个 [表情:xx] 时没有文字，
            # 但对方确实收到了反应 —— 追问权不能因为"没文本"就不给。
            if meme_file:
                _sent_ok = True

            # ── 纯图回复的兜底（1.5.7）──
            #    模型只写了 [表情:xxx] 时上面不会走文字发送，全靠这一张图。
            #    但 try_send_meme 会因为**冷却/引擎禁用/匹配不到**返回 None ——
            #    那样这条回复就彻底消失了：群里什么都没发生，日志里只有一行
            #    "仅表情标记，跳过文本发送"，看起来像 Bot 没收到消息。
            #    这里兜一句最简文本，宁可发个"草"也不能凭空失踪。
            if not sent_text and not meme_file:
                try:
                    _fallback = "……"
                    if smart_send_group(group_id, _fallback, reply_id=_quote_id):
                        _sent_ok = True
                        log(f"群[{group_id}] [纯图兜底] 表情未发出，改发文字「{_fallback}」")
                        track_bot_reply(group_id, _fallback)
                except Exception as _e:
                    log(f"群[{group_id}] [纯图兜底] 失败: {_e}")

            # ── 戳一戳的"炸毛"表情包（2026-10-25 补回）──
            # 旧版 `_handle_poke_event` 末尾无条件发一张 POKE_MEME_EMOTIONS 里的图，
            # 接进流水线时整段重写把这一步漏了 → 群戳变安静，只剩模型自己标
            # [表情] 时才有图。这里放回，只加一个条件：`not meme_file`。
            # 模型本批已经用 [表情:xx] 发过一张就不再补 —— 否则一个戳换来两张图。
            # 发成功也记 `_sent_ok`：只有图、没有文字时同样算"回应过了"，
            # 追问权该解（旧版就是这样，这里不能因为"没文本"就不给）。
            _poke_owner = buffer.get("poke_uid")
            if _poke_owner and MEME_OK and not meme_file:
                try:
                    _emotion = random.choice(POKE_MEME_EMOTIONS)
                    _poke_meme = match_meme(_emotion)
                    if _poke_meme and send_group_image(group_id, _poke_meme):
                        _sent_ok = True
                        log(f"群[{group_id}] 戳一戳表情包: {_emotion}")
                except Exception as _pe:
                    log(f"群[{group_id}] 戳一戳表情包失败: {_pe}")

            # ── 戳主的追问权：只在戳这一批真的发出之后生效（2026-10-25 定）──
            # skip / observe / 生成为空 / 发送抛异常都到不了这里 —— 额度只给已经
            # 被回应的人。观察回看的批次没有 poke_uid，所以"稍后回"也不补。
            # "发送成功"按 smart_send_group 未抛异常且返回真判定；它不返回更细的
            # 结果，本次不扩大这个接口（发送层部分失败时仍会写额度）。
            if _poke_owner and _sent_ok:
                _poke_cap = _config.get("poke_session_replies", 2)
                with _session_lock:
                    GROUP_SESSION[group_id] = {
                        "remaining": _poke_cap,
                        "last_msg_time": time.time(),
                        "poke_owner": str(_poke_owner),
                    }
                log(f"群[{group_id}] 戳回复完成，解锁追问通道"
                    f"(仅戳主 {_poke_owner}，额度{_poke_cap})")

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

def _mcp_cached():
    """返回已加载的 MCP server 缓存（{server: {tools:[schema…]}}）；任何异常 → {}。

    供 agent tool_picker / 主链把"用户明确加载过的" MCP 工具带进每轮工具集。
    """
    try:
        from harness import mcp_hub as _m
        return _m.cached_servers()
    except Exception:
        return {}


# ── MCP 集成 HTTP 端点（T1，交接文档 §3.3）───────────────────────────
#  路由挂在 EventHandler 里（GET /api/mcp/*、POST /api/mcp/*），逻辑全在这里，
#  与 notify_log 响应风格一致：json body + send_response(200)，绝不 500。
def _mcp_json_send(handler, payload, code=200):
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except Exception:
        pass


def _mcp_status_payload():
    """/api/mcp/status 聚合状态（前端 MCP 设置区只读这一个口）。

    ★ env 原样回传：webui/3005 仅监听 127.0.0.1 且有 Origin 闸门，本机信任模型；
      与 providers 走凭据管理器不同 —— MCP 配置本来就明文落在 data_dir/mcp.json，
      回传 env 不增加暴露面。（若以后要"导出配置包给别人"再单独处理密钥。）
    """
    from harness import mcp_hub as _m
    ok, _reason, py, _root = _m._probe()
    cached = {}
    for s, c in _m.cached_servers().items():
        cached[s] = [(t.get("function") or {}).get("name", "")
                     for t in (c.get("tools") or [])]
    return {
        "ok": True,
        "available": _m.available(),
        "reason": _m.unavailable_reason(),
        "config_path": _m.config_path(),
        "venv": {
            "target": _m.venv_target_dir(),
            "installed": bool(ok),
            "python": py or "",
        },
        "install": _m.install_status(),
        "servers": _m.servers(),
        "cached": cached,
        "stats": _m.stats(),
    }


def _mcp_http_get(handler):
    path = (handler.path or "").split("?", 1)[0]
    try:
        from harness import mcp_hub as _m
        if path == "/api/mcp/status":
            _mcp_json_send(handler, _mcp_status_payload())
        elif path == "/api/mcp/install_status":
            st = _m.install_status()
            _mcp_json_send(handler, {"ok": True, "running": st["running"],
                                     "done": st["done"], "log": st["log"]})
        else:
            _mcp_json_send(handler, {"ok": False, "error": f"未知端点: {path}"}, 404)
    except Exception as e:
        _mcp_json_send(handler, {"ok": False, "error": f"{type(e).__name__}: {e}"})


def _mcp_http_post(handler):
    req = {}
    try:
        length = int(handler.headers.get("Content-Length", 0))
        if 0 < length < 1048576:
            req = json.loads(handler.rfile.read(length).decode("utf-8"))
        if not isinstance(req, dict):
            req = {}
    except Exception:
        req = {}
    path = (handler.path or "").split("?", 1)[0]
    try:
        from harness import mcp_hub as _m
        if path == "/api/mcp/save":
            # 只落盘不加载（加载是显式的"加载"按钮 → 惰性红线）
            p = _m.save_servers(req.get("servers") or {})
            _mcp_json_send(handler, {"ok": True, "path": p})
        elif path == "/api/mcp/load":
            s = str(req.get("server") or "").strip()
            if not s:
                _mcp_json_send(handler, {"ok": False, "error": "缺少 server"})
                return
            r = _m.prefetch(s)
            if not r.get("ok"):
                _mcp_json_send(handler, {"ok": False, "error": r.get("error") or "加载失败"})
                return
            _m.register_into_tool_registry(s, force=True)
            # ★ 坑 §3.4-1：主链执行表必须拿到函数体（agent 侧 _tools_for 动态取，
            #   tool_runner 走的是 TOOL_FUNCTIONS，只能在这里 update）
            TOOL_FUNCTIONS.update(_m.tool_functions())
            _mcp_json_send(handler, {"ok": True, "count": r.get("count", 0),
                                     "names": r.get("names", [])})
        elif path == "/api/mcp/forget":
            s = str(req.get("server") or "").strip()
            if not s:
                _mcp_json_send(handler, {"ok": False, "error": "缺少 server"})
                return
            dropped = _m.forget_server(s)
            pref = f"mcp__{s}__"
            for k in [k for k in list(TOOL_FUNCTIONS) if str(k).startswith(pref)]:
                TOOL_FUNCTIONS.pop(k, None)
            # 注意：forget 不删 mcp.json 条目（§3.4-5）—— 删配置走前端整组 save
            _mcp_json_send(handler, {"ok": True, "dropped": dropped})
        elif path == "/api/mcp/test":
            s = str(req.get("server") or "").strip()
            if not s:
                _mcp_json_send(handler, {"ok": False, "error": "缺少 server"})
                return
            r = _m.list_tools(s, force=True)
            _mcp_json_send(handler, {"ok": bool(r.get("ok")), "error": r.get("error"),
                                     "count": len(r.get("tools") or []),
                                     "names": [(t.get("function") or {}).get("name", "")
                                               for t in (r.get("tools") or [])]})
        elif path == "/api/mcp/install":
            _mcp_json_send(handler, _m.install_venv_async())
        elif path == "/api/mcp/reload":
            _m.reset()
            _mcp_json_send(handler, {"ok": True, "servers": _m.server_names()})
        else:
            _mcp_json_send(handler, {"ok": False, "error": f"未知端点: {path}"}, 404)
    except Exception as e:
        _mcp_json_send(handler, {"ok": False, "error": f"{type(e).__name__}: {e}"})


def main():
    global chat_history, group_chat_history

    log("=" * 50)
    log("EggBloom v8 - 多账号 + 文件转发 + 消息转存")
    log(f"Bot: {BOT_QQ} | 监听: {', '.join(TARGET_QQS)} | 群聊: {len(GROUP_IDS)}个")
    log("=" * 50)

    # ── 沙漏记忆引擎（同步初始化，实测 20ms）──
    # ★ 2026-10-25：原来起后台线程后只打一句「后台初始化中」，
    #   线程失败/卡住时这句话照样出现，看起来一切正常 —— 实际记忆库
    #   从 2026-10-16 起九天一条没写，日志里没有任何异常。
    #   现在同步做（20ms 不值得用线程换不可观测性），并如实打成功/失败。
    try:
        from sandglass_bridge import _ensure_init, is_available
        _ok = _ensure_init()
        if _ok and is_available():
            log("[Sandglass] 记忆系统已就绪")
        else:
            log("[Sandglass] 记忆系统未就绪 —— 记忆不会写入，请检查上面的初始化失败原因")
    except Exception as e:
        log(f"[Sandglass] 初始化异常: {e}")

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

    # ── 启动基线 + 消息事件服务（3005）—— **必须早于**下面的"等待 OneBot"阻塞循环 ──
    #  实机踩过（2026-10-10）：这三件事原先排在等待循环之后，协议层不在线时
    #  bot 卡在等待循环最长 10 分钟（retry_count > 300 * 2s），期间：
    #    · 3005 事件服务**根本没监听** → 协议层事件桥无处投递 → 戳一戳/撤回/入群全静默丢失；
    #    · 3005 上挂的 Agent 页接口、群管理转发也一起瘫痪（"改了没反应"的另一种来源）。
    #  事件服务只做入队（MSG_QUEUE.put），消费在主循环里 —— 提前启动无副作用。
    #  startup_time 必须在其之前赋值：主循环靠它过滤"启动前历史消息"（见 msg_time < startup_time）。
    load_state()
    global startup_time
    startup_time = time.time()  # 开机冷却起点
    # 启动消息事件接收服务（3005 端口，接收协议层实时推送）
    threading.Thread(target=start_event_server, daemon=True, name="event-server").start()

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

    # ── harness：把内置工具注册进 tool_registry（供 Agent/主链按需加载）──
    #  **只注册元数据**（schema + 分组），实现仍在 TOOL_FUNCTIONS。
    #  这样可以"按场景只注入用得上的工具" → 省大量 token。
    #  工作区根：默认=项目根；可用 config.workspace_root 指向别处（如 D:\workspace）。
    try:
        from harness import workspace as _wk
        _WORKSPACE_ROOT = _wk.set_root(_config.get("workspace_root") or
                                       os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        _WORKSPACE_ROOT = os.path.dirname(os.path.abspath(__file__))
    try:
        from harness import tool_registry as _treg
        from harness import bot_hook as _bh
        _treg.register_many(TOOLS, groups=(_treg.GROUP_CORE,))
        # ★ 插件能力 → agent 工具（plugin_list / plugin_call）。
        #   用户对 agent 的定位就是"集成调用插件功能"（发 QQ 空间、定时任务、主动聊天…），
        #   而 agent 的工具此前只有 TOOL_FUNCTIONS + skill + MCP，插件动作一个都调不到。
        try:
            from harness import plugin_tools as _pt
            _pt_n = _pt.register_into(_treg)
            log(f"[Agent] 插件工具已接入（{_pt_n} 个）")
        except Exception as _e:
            log(f"[Agent] 插件工具接入失败: {_e}")
        # ★ 插件创作管线（plugin_forge）：schema 进 registry + 审批通知/事件钩子注入。
        #   未批准代码进 plugins/_pending/（discover 跳过 "_"），只有主人在 Agent 页
        #   点同意才移入 plugins/ 并热加载 —— 见 harness/plugin_forge.py。
        try:
            from harness import plugin_forge as _pf
            _pf_n = _pf.register_into(_treg)
            _pf.set_admin_notifier(lambda _t: _notify_admin(_t, "plugin_forge"))

            def _forge_event(_rq):
                """创作请求双投递：session_log（回放不丢）+ 事件总线（实时弹窗）。
                群聊来源写的是群会话，Agent 页看不到 —— 前端另有 /api/agent/forge
                轮询兜底（创作历史区列全部 pending，不依赖事件流）。"""
                try:
                    from harness import session_log as _slf
                    from harness import agent_service as _asf
                    _sidf, _ = _pf._turn_context()
                    _slf.append("agent", _sidf or "desk-forge", "forge_request", dict(_rq))
                    if _sidf:
                        _asf._publish(_sidf, "forge_request", dict(_rq))
                except Exception:
                    pass
            _pf.set_event_hook(_forge_event)
            log(f"[Agent] 插件创作工具已接入（{_pf_n} 个）")
        except Exception as _e:
            log(f"[Agent] 插件创作工具接入失败: {_e}")
        # 覆盖分组：web_operate 归 web；桌面操控类归 desktop
        for _t in TOOLS:
            _n = _t["function"]["name"]
            if _n == "web_operate":
                _treg.register(_n, _t, groups=(_treg.GROUP_WEB,), source="builtin")
            elif _n != "search_web":
                _treg.register(_n, _t, groups=(_treg.GROUP_DESKTOP,), source="builtin")
        _treg.register_many(GROUP_TOOLS, groups=(_treg.GROUP_GROUP,))
        # ── 工作型工具（harness 自带）：只读常驻 / 写型与执行型按意图注入 ──
        #  本地能力的地基：读、找、搜、改、跑。没有它们 agent 只能"空转"。
        #  分组即风险级别，注入闸门见 harness.bot_hook.agent_tools_for。
        try:
            from harness import tool_fs as _tfs
            from harness import tool_proc as _tproc
            _work_fns = {}
            _work_fns.update(_tfs.register_all(root=_WORKSPACE_ROOT))
            _work_fns.update(_tproc.register_all())
            TOOL_FUNCTIONS.update(_work_fns)
            log(f"[harness] 工作型工具已接入: {sorted(_work_fns)}（工作区根 {_WORKSPACE_ROOT}）")
        except Exception as _we:
            log(f"[harness] 工作型工具接入失败（agent 将缺少文件/执行能力）: {_we}")
        # ── skill 体系接入主链（此前 skill 在主链无入口 = 死的）──
        #  注册 schema + 把实现塞进 TOOL_FUNCTIONS，模型才能真正调 skill__load/read。
        _treg.register_many(_bh.SKILL_TOOLS, groups=(_treg.GROUP_SKILL,), source="skill")
        try:
            _sk_fns = _bh.skill_tool_functions()
            TOOL_FUNCTIONS.update(_sk_fns)
            from harness import skill_loader as _skl
            _sk_cnt = len(_skl.discover())
            log(f"[harness] skill 已接入主链: {list(_sk_fns.keys())}，发现 {_sk_cnt} 个技能")
            if _sk_cnt == 0:
                log("[harness] 提示：未发现任何 skill，skill__load/read 不会被注入（零成本）")
        except Exception as _e:
            log(f"[harness] skill 接入失败: {_e}")
        log(f"[harness] 工具注册表就绪: {_treg.stats()['by_group']}")
    except Exception as e:
        log(f"[harness] 工具注册失败（将退回全量工具）: {e}")

    # ── harness：MCP（惰性；未配置/通道未就绪时零成本）──
    #  配置来源见 harness.mcp_hub（<data_dir>/mcp.json 或 ~/.workbuddy/mcp.json）。
    try:
        from harness import mcp_hub as _mcp
        _mcp.configure(venv_dir=_config.get("mcp_venv_dir") or None)
        _servers = _mcp.server_names()
        if _servers:
            if _mcp.available():
                log(f"[harness] MCP server 已配置: {_servers}（惰性枚举，首次用到才拉取；独立 venv 隔离）")
                # 若开了 mcp_prefetch，启动时预热（默认关，避免拖慢启动）
                if _config.get("mcp_prefetch", False):
                    for _s in _servers:
                        _r = _mcp.prefetch(_s)
                        _ok = _r.get("ok")
                        log(f"[harness] MCP 预热 {_s}: {'OK ' + str(_r.get('count')) + ' 个工具' if _ok else _r.get('error')}")
                        if _ok:
                            _mcp.register_into_tool_registry(_s)
                            TOOL_FUNCTIONS.update(_mcp.tool_functions())
            else:
                log(f"[harness] 检测到 MCP 配置 {_servers}，但 {_mcp.unavailable_reason()}")
        else:
            log("[harness] 未配置 MCP server（如需接入见 data/mcp.json）")
    except Exception as e:
        log(f"[harness] MCP 初始化跳过: {e}")

    # ── Agent 服务：注入依赖（逻辑在 harness.agent_service，此处仅接线）──
    try:
        from harness import agent_service as _agent
        from harness import bot_hook as _bh

        def _agent_chat(_messages, _tools=None, max_tokens=1500, on_reasoning=None):
            """Agent 用的 chat 闭包：把 registry 解析出的 schema 传下去。

            _tools 由 agent_loop 给出（schema 列表或名字列表）：
            - 是 schema 列表（含 function 字段）→ 直接用
            - 是名字列表 → 经 registry 解析为 schema（顺序稳定）
            这样"按需工具"在 agent 侧也生效。
            """
            _use = None
            if isinstance(_tools, list) and _tools:
                if isinstance(_tools[0], dict) and "function" in _tools[0]:
                    _use = _tools
                else:
                    try:
                        _use = _treg.resolve(_tools, lazy=True) or None
                    except Exception:
                        _use = None
            # ★ 流式优先：思维链**边生成边推**给前端（on_reasoning 回调）。
            #   失败/无回调时退回同步调用，行为与以前一致（不会让 agent 挂掉）。
            if on_reasoning is not None:
                try:
                    return _agent_stream_chat(_messages, (_use if _use else []),
                                              max_tokens=max_tokens,
                                              on_reasoning=on_reasoning)
                except Exception as _se:
                    log(f"[Agent] 流式失败，退回同步: {_se}")
            msg, usage = _call_chat_sync(
                _messages, runtime_cfg=None, max_tokens=max_tokens, stream=False,
                with_tools=(_use if _use else []),
            )
            # reasoning_content 必须带出去：推理模型的思维链在这里，
            # 以前只返回前三个字段 → Agent 页看不到"思考过程"。
            return {
                "content": msg.get("content") or "",
                "tool_calls": msg.get("tool_calls") or [],
                "usage": usage or {},
                "reasoning": msg.get("reasoning_content") or msg.get("reasoning") or "",
            }

        # 工具实现 = 内置 TOOL_FUNCTIONS + skill 内部工具 + 已缓存的 MCP 工具
        _agent_fns = dict(TOOL_FUNCTIONS)
        try:
            _agent_fns.update(_bh.skill_tool_functions())
        except Exception:
            pass
        try:
            _agent_fns.update(_mcp.tool_functions())
        except Exception:
            pass
        # ★ 插件工具的**函数本体**必须进 agent 的工具字典：
        #   此前只把 schema 注册进了 registry（模型看得见、会调用），但
        #   _agent_fns 里没有函数 → 执行时 blocked="unknown"（用户实测：
        #   "发个qq空间" → plugin_list 调用被拦，任务卡死）。
        try:
            from harness import plugin_tools as _ptf
            _agent_fns["plugin_list"] = _ptf.plugin_list
            _agent_fns["plugin_call"] = _ptf.plugin_call
        except Exception:
            pass
        try:
            from harness import plugin_forge as _pfg
            _agent_fns["plugin_forge"] = _pfg.plugin_forge
            _agent_fns["plugin_forge_status"] = _pfg.plugin_forge_status
        except Exception:
            pass

        _agent.configure(
            chat_fn=_agent_chat,
            tool_functions=_agent_fns,
            static_prompt_fn=_build_static_prompt,
            is_authorized_fn=_is_authorized,
            logger=log,
            tool_schema_provider=lambda names: _treg.resolve(names, lazy=True),
            # 工具分闸：与主回复链同一套（harness.bot_hook.agent_tools_for），
            # 保证"主链给模型什么工具"和"agent 给模型什么工具"语义一致。
            # ★ 末尾固定追加两个插件工具（plugin_list / plugin_call）：
            #   agent 的定位就是"集成调用插件功能"，这两个必须常驻。
            tool_picker=lambda msg, is_group: [
                _t["function"]["name"]
                for _t in (_bh.agent_tools_for(msg, is_group=bool(is_group)) or [])
                if isinstance(_t, dict) and _t.get("function", {}).get("name")
            ] + ["plugin_list", "plugin_call", "plugin_forge", "plugin_forge_status"]
                # ★ MCP：把"用户已加载"的 MCP 工具带进 agent 每轮工具集
                #   （tool_names_for 已自动补，但 agent 的 tool_picker 走的是
                #   agent_tools_for(schema) —— 这里同一逻辑补一次；未加载的
                #   零成本不拉，见 mcp_hub.cached_servers）。
                + [t["function"]["name"]
                   for _c in (_mcp_cached() or {}).values()
                   for t in (_c.get("tools") or [])
                   if isinstance(t, dict) and t.get("function", {}).get("name")],
            # 记忆：走本体（sandglass_lite + super_memory），开关随 config.memory_enable
            memory=bool(_config.get("memory_enable", True)),
        )
        # 记忆桥自检（一行日志，方便排障"为什么 agent 不记得我"）
        try:
            from harness import agent_memory as _amem
            _probe = _amem.probe()
            log(f"[Agent] 本体记忆: {'可用' if _probe['available'] else '不可用'}"
                f"{'（' + _probe['reason'] + '）' if _probe['reason'] else ''}"
                f"，记忆条数={_probe.get('messages')}，检索工具={_probe.get('tools')}")
        except Exception as _me:
            log(f"[Agent] 本体记忆自检失败: {_me}")
        log(f"[Agent] 服务已注入（主程序页 + 群内双闸），工具 {len(_agent_fns)} 个")

        # ── agent 页设置：应用上次保存的值（data/agent_settings.json）──
        try:
            from data_store import get_data_dir
            _aset_path = get_data_dir() / "agent_settings.json"
            if _aset_path.exists():
                _sv = json.loads(_aset_path.read_text(encoding="utf-8"))
                _agent.configure(
                    memory=bool(_sv.get("memory", True)),
                    thinking=bool(_sv.get("thinking", True)),
                    max_steps=int(_sv.get("max_steps") or 0) or None,
                )
                log(f"[Agent] 已应用上次保存的设置: {_sv}")
        except Exception as _ae:
            log(f"[Agent] 读取 agent_settings 失败: {_ae}")
    except Exception as e:
        log(f"[Agent] 注入失败: {e}")

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
                _owner = ""
                _admins = []
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
                    # ★ 2026-10-19：**全局缓存只存全局昵称，不存群名片**。
                    #   此前写的是 display（card 优先）→ 某群的群名片被塞进全局
                    #   QQ_NICK_CACHE，于是别的群/私聊里也用这个名字
                    #   （用户实测：本群叫「e蛋」，却显示成别群昵称「占位名片B」）。
                    #   群维度显示走 GROUP_MEMBERS[gid][uid]（下一行），各行其道。
                    QQ_NICK_CACHE[uid] = nick or display
                    if gid not in GROUP_MEMBERS:
                        GROUP_MEMBERS[gid] = {}
                    GROUP_MEMBERS[gid][uid] = display
                    cached += 1
                    # 群身份（OneBot v11 member.role: owner/admin/member）
                    _role = (m.get("role") or "").strip().lower()
                    if uid == str(BOT_QQ):
                        continue  # Bot 自己不算群主/管理员
                    if _role == "owner" and not _owner:
                        _owner = display
                    elif _role == "admin":
                        _admins.append(display)
                GROUP_ROLES[str(gid)] = {"owner": _owner, "admins": _admins[:10]}
                log(f"群[{gid}] 成员列表: {len(members)}人, 新缓存{cached}人"
                    + (f", 群主={_owner}" if _owner else ", 群主未识别"))
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
    
    # 启动冷却倒计时（事件服务/startup_time 已上移至"等待 OneBot"之前，见该处说明）
    if STARTUP_COOLDOWN > 0:
        def _countdown():
            for elapsed in range(1, STARTUP_COOLDOWN + 1):
                if elapsed % 5 == 0 or elapsed == 1 or elapsed == STARTUP_COOLDOWN:
                    log(f"启动冷却中… {elapsed}s/{STARTUP_COOLDOWN}s（此期间抓取消息但不回复）")
                time.sleep(1)
        threading.Thread(target=_countdown, daemon=True, name="startup-countdown").start()

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
                            # ★ 合并后重新入队的那条必须带着这个标记穿过这一层。
                            #   这里原来只挑固定字段，标记被丢掉 → 重新入队后又被
                            #   当成新消息拦进缓冲 → 永远等不到回复。
                            #   2026-10-27 实测：私聊每条都是「入缓冲 等 5.0s（本批 1 条）」，
                            #   一条都不回，日志里连「收到:」都没有。
                            _REQUEUE_FLAG: bool(ev.get(_REQUEUE_FLAG)),
                        })
                        if is_group:
                            event_msgs[-1]["group_id"] = group_id
                        # HUD 计数：只统计真正进入处理链的消息（已过启动基线 + 去重过滤）
                        try:
                            _HUD_STATS["messages"] += 1
                            if is_group and group_id:
                                _HUD_STATS["groups"].add(group_id)
                        except Exception:
                            pass
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
                    elif post_type == "notice" and ev.get("notice_type") == "group_ban":
                        # 群禁言/解除禁言：纯记录（此前缺失，导致该事件无任何痕迹）
                        try:
                            _handle_group_ban(ev)
                        except Exception as e:
                            log(f"[事件] group_ban 处理异常: {e}")
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
                    # 白名单收紧后：不再对已排除的群发言，并顺手清掉它的会话
                    if not _group_allowed(gid):
                        GROUP_SESSION.pop(gid, None)
                        continue
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
                                    _record_bot_group_msg(gid, exit_phrase)
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
                    if not _group_allowed(gid):
                        continue  # 白名单/逐群开关拒绝 → 不发退场语
                    bot_last = GROUP_LAST_BOT_MSG.get(gid, 0)
                    last_any = GROUP_LAST_ANY_MSG.get(gid, 0)
                    elapsed = now - bot_last if bot_last > 0 else 0
                    if bot_last > 0 and bot_last >= last_any and now - bot_last > _idle_exit_min_silence():
                        last_exit = GROUP_LAST_EXIT.get(gid, 0)
                        if now - last_exit >= _config.get("group_last_exit_cooldown", 3000) and random.random() < _config.get("group_idle_exit_probability", 0.3):
                            exit_phrase = random.choice(EXIT_PHRASES_IDLE)
                            try:
                                smart_send_group(gid, exit_phrase)
                                _record_bot_group_msg(gid, exit_phrase)
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
                                    # 尊重群开关 + 白名单（与正常路径一致）
                                    _pid_gid = msg.get("_group_id")
                                    if _group_allowed(_pid_gid):
                                        smart_send_group(_pid_gid, _pr)
                                        log(f"[Plugin] on_message 插件回复: {_pr[:60]}")
                                    else:
                                        _notice_group_blocked(_pid_gid, _group_block_reason(_pid_gid))
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
                    # ★ 群名片缓存（2026-10-19）：群消息的 sender.card 是**本群专用**
                    #   显示名，而 QQ_NICK_CACHE / get_stranger_info 只有全局昵称。
                    #   不在这里落一份，_display_name 就只能拿到别群也在用的全局昵称
                    #   → 同一个人在不同群被叫成同一个名字（用户实测「e蛋」vs「占位名片B」）。
                    #   每条群消息都刷新，保证改名/换名片后立刻跟上。
                    try:
                        _snd_obj = msg.get("sender") or {}
                        if isinstance(_snd_obj, dict):
                            _snd_card = str(_snd_obj.get("card") or "").strip()
                            _snd_nick2 = str(_snd_obj.get("nickname") or "").strip()
                            _snd_disp = _snd_card or _snd_nick2
                            if sender and _snd_disp:
                                GROUP_MEMBERS.setdefault(str(group_id), {})[sender] = _snd_disp
                    except Exception:
                        pass
                    # 群限制：该群自动回复已关闭（webui 群管理页）或不在白名单 → 完全静默
                    if not _group_allowed(group_id):
                        _notice_group_blocked(group_id, _group_block_reason(group_id))
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
                    quote_me = False             # 被引用的那条是不是我（决定"是否必回"）
                    _quoted_img_paths = []       # 被引用的那张图（"看图"最常见的入口）
                    _quoted_img_urls = []        # 从 raw_message 抠出的图片地址（extract_text 给不到）
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
                                        # ★ 引用的原消息是图片时，图片地址只存在于 raw_message 的
                                        #   CQ 串里（extract_text 只会给出 "[图片]"）—— 这里单独抠出来，
                                        #   否则引用图片永远进不了视觉层（线上就是"引用一张图问这是啥"）。
                                        _raw_q = str(qdata.get("raw_message") or "")
                                        if _raw_q:
                                            for _qu in cq_image_urls(_raw_q):
                                                if _qu not in _quoted_img_urls:
                                                    _quoted_img_urls.append(_qu)
                                        quoted_sender = _extract_qq(qdata.get("sender", {}).get("user_id", ""))
                                        log(f"[调试-引用] qtext={repr(qtext[:80] if qtext else '')} raw={repr(str(qdata.get('raw_message',''))[:80])} msg_id={reply_id}")
                                        if qtext and qtext.strip():
                                            quoted_text = qtext.strip()[:500]
                                        elif qdata.get("raw_message") and str(qdata["raw_message"]).strip():
                                            quoted_text = str(qdata["raw_message"]).strip()[:500]
                                        if quoted_sender:
                                            quoted_sender_nick = _display_name(quoted_sender, group_id)
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
                                            quoted_sender_nick = _display_name(s, group_id)
                                            # 缓存条目里带着这条消息的图片路径（若有）
                                            for _ip in (entry[4] if len(entry) > 4 else []):
                                                if _ip and _ip not in _quoted_img_paths:
                                                    _quoted_img_paths.append(_ip)
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
                                            quoted_sender_nick = _display_name(s, group_id)
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
                        # ★ 引用**别人**的话 ≠ 在叫我（qq-bridge 明确处理过这一点：
                        #   "不会把群友之间引用第三方的对话误当成指向自己"）。
                        #   我们此前只要有引用就当成触发 → 群里互相引用时我们抢话。
                        quote_me = bool(quoted_sender) and str(quoted_sender) == str(BOT_QQ)
                        if not quote_me and quoted_sender_nick:
                            quote_me = _norm_nick(quoted_sender_nick) in {_norm_nick(n) for n in _bot_nicknames()}
                        # ★ 兜底：引用者身份没解析出来（get_msg 对负数 message_id /
                        #   引用 Bot 自己的话常失败，缓存窗口又只有 30 秒），但被引用的
                        #   原文能在"我最近的回复"里对上 → 判定"他在引用我"。
                        #   治的就是"引用信息无法分辨是谁发的"：机器人把别人引用它自己
                        #   的话，当成陌生人在说话，于是回自己一句"谁不理你啊 我不是人吗"。
                        if not quote_me and not quoted_sender and _match_own_reply(str(group_id), quoted_text):
                            quote_me = True
                            quoted_sender = str(BOT_QQ)
                            quoted_sender_nick = quoted_sender_nick or _bot_nicknames()[0]
                            log(f"群[{group_id}] 引用消息(自引用兜底命中): [{quoted_sender_nick}] {quoted_text[:40]}")
                        sender_display = quoted_sender_nick if quoted_sender_nick else "未知"
                        # ★ 引用的 raw_message 是 CQ 串：先把图片抠出来交给视觉层，
                        #   再把 CQ 码转人话。此前原样进 prompt，模型只会看到一串
                        #   带 token 的下载 URL，然后去 web_operate 打开、回"一坨链接"。
                        try:
                            _q_cands = list(_quoted_img_urls) + cq_image_urls(quoted_text)
                            for _qu in _q_cands:
                                _qp = download_image_url(_qu)
                                if _qp and _qp not in _quoted_img_paths:
                                    _quoted_img_paths.append(_qp)
                                    break
                        except Exception:
                            pass
                        quoted_text = sanitize_cq(quoted_text)
                        # ★ 2026-10-27：被引用的**原文本身在叫 Bot**，也算"在叫我"。
                        #   判据与来龙去脉见 quoted_text_calls_bot()。
                        if not quote_me:
                            _qn = quoted_text_calls_bot(quoted_text)
                            if _qn:
                                quote_me = True
                                log(f"群[{group_id}] 引用注记修正：被引用原文里叫了「{_qn}」"
                                    f"→ 按'在叫我'处理")
                        # 引用路由写进 prompt：让模型自己也能判断"这是在跟谁说话"
                        _qtag = ("引用我，必回" if quote_me
                                 else f"他在引用{sender_display}，**不是在叫我**，别抢话")
                        text = f"[引用:{sender_display}｜{_qtag}] {quoted_text}\n[当前] {text}"
                    
                    # 群友画像指令：优先拦截，不进入缓冲区
                    original_check = text.lstrip(f"[引用] {quoted_text}\n[当前] ") if quoted_text else text
                    oc = original_check.strip()
                    # ── 1.5.9 §B0：内容指纹判重 ──
                    # 同一件事换形态投递时 mid 不同、字面也不同（原文 / 引用预览 /
                    # [图片] 占位），旧的三道判重全都放过。30 秒窗口内同人同指纹即丢。
                    # 用去注记的原文（oc），否则引用注记一变指纹就不同。
                    if _INBOUND_DUPE.hit(group_id, sender, oc):
                        log(f"群[{group_id}] [重复:指纹] {oc[:30]}")
                        continue
                    # 发言率观测（只记不改）：每 50 条打一行，给下一版调参用
                    _in_n, _out_n = _speak_note(group_id, "in")
                    if _in_n and _in_n % 50 == 0:
                        log(f"[发言率] 群{group_id} 30min in={_in_n} out={_out_n} "
                            f"→ {100.0 * _out_n / max(1, _in_n):.1f}%")
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
                    # ★ 2026-10-19 两处修正（人格被污染的直接来源）：
                    #   ① 群里跑着**另一个 AI 机器人**（实测 另一个AI机器人，
                    #      uid 10000004 / 10000005），它的 107+98 条消息被
                    #      当成"群友表达风格"学进 personality.txt（占语料 37%）
                    #      → EggBloom 开始模仿另一个 AI 的腔调。
                    #   ② `text` 此时已带上 bot 自己的引用注记
                    #      （`[引用:xxx｜他在引用xxx，**不是在叫我**，别抢话] ...`），
                    #      整段被当用户原话采集 → 后被挖成"黑话"（当前/别抢话/引用…）。
                    #   所以：跳过 AI 机器人；只采集**用户当前这句原话**。
                    if SELF_LEARN_OK and text and not _is_ai_bot_sender(group_id, sender):
                        _learn_text = _strip_bot_annotation(text)
                        if _learn_text:
                            self_learn.collect_message(str(group_id), sender, _learn_text)
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
                    # 提取图片路径（供群聊决策插件理解图片内容 + 回复时做视觉描述）
                    _img_paths = []
                    if any(isinstance(s, dict) and s.get("type") == "image" for s in msg.get("message", [])):
                        try:
                            _img_paths = extract_images(msg)
                        except Exception:
                            _img_paths = []
                    # 被引用的图片也算这条消息的图（用户 @ 我回一张图问"这是啥"就是这条路）
                    for _qp in (_quoted_img_paths or []):
                        if _qp and _qp not in _img_paths:
                            _img_paths.append(_qp)
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
                    # 归口 _pipeline_upsert，这里只算本条消息的判定标记
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
                            # ★ 2026-10-19：认 Bot 的**全部**称呼 ——
                            #   QQ_NICK_CACHE / bot_group_nick 只有一个值，而同一个账号
                            #   在不同群可以有不同群名片（实测：群 200000002=「蛋」、
                            #   群 200000001=「占位名片A」）。只认一个 → 换个群的
                            #   叫法就当成在叫别人，@ 了也不理。
                            if _is_bot_self_name(at_qq) or _is_bot_self_name(at_nick):
                                is_at_bot = True
                                break
                            # 昵称匹配：当 at_qq 为空时（@昵称），用 displayName 匹配
                            if not at_qq and at_nick:
                                if any(n and n in at_nick for n in _bot_self_names()):
                                    is_at_bot = True
                                    break
                    
                    # 检查是否命中配置的触发词
                    has_xiao_a = _is_group_trigger(text)
                    # 检查文本是否含 Bot 昵称（仅在配置开启时）
                    has_bot_nick = False
                    _self_names = [n for n in _bot_self_names() if str(n).strip()]
                    if _config.get("group_trigger_include_bot_nick", True):
                        has_bot_nick = any(n in text for n in _self_names)
                    # 文本中 @Bot QQ 检测（如 @10000）
                    if f"@{BOT_QQ}" in text:
                        is_at_bot = True
                    # 文本中 @Bot 昵称检测（仅在配置开启时）——
                    # ★ 2026-10-19：遍历**全部**称呼（含各群群名片），不再只比 bot_group_nick
                    if _config.get("group_trigger_include_bot_nick", True):
                        if any(f"@{n}" in text for n in _self_names):
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

                    # ── 框架指令（2026-10-25）──
                    # `#sl` / `/help` 这类由协议层或框架自己回，Bot 再答一遍就是
                    # 重复且通常答错（它不知道框架有哪些命令）。判据见
                    # _is_framework_command：短、以 /#! 开头、余下是纯 ASCII 单词。
                    # 上下文照记（上面已写进 RECENT_MSGS / TIMELINE），只是不触发回复。
                    if text and not is_at_bot and not has_xiao_a and _is_framework_command(text):
                        log(f"群[{group_id}] 框架指令，交给协议层处理（不回复）: {text[:40]}")
                        continue

                    # ── 群管理指令（@Bot + 指令，admin_qqs/主人鉴权）──
                    if (is_at_bot or has_xiao_a) and text:
                        try:
                            if _handle_group_admin_command(text, msg, sender, group_id):
                                continue
                        except Exception as e:
                            log(f"[群管理] 指令处理异常: {e}")

                    # ── 群内 Agent 模式（任务：主程序 agent 能力 · 群内入口）──
                    # **默认关**：agent_group_enable 打开 且 双闸通过才触发。
                    # 闸一=权限（仅管理员白名单），闸二=明确执行意图（非闲聊）。
                    if (is_at_bot or has_xiao_a) and text and _config.get("agent_group_enable", False):
                        try:
                            from harness import agent_service as _agent
                            if _agent.is_ready():
                                _allowed, _why = _agent.group_gate(
                                    text=text, sender=sender, is_group=True)
                                if _allowed:
                                    log(f"[Agent] 群[{group_id}] {sender} 触发（{_why}）: {text[:60]}")
                                    # 会话按**群**复用（同群多次指令共享上下文，语义更连贯）；
                                    # 带日期戳防止跨天累积成超长历史。
                                    _day = time.strftime("%Y%m%d")
                                    _agent_sid = f"grp-{group_id}-{_day}"
                                    _agent_ctx = (f"当前是 QQ 群 {group_id} 内的任务，"
                                                  f"发起人 QQ={sender}。"
                                                  f"需要群信息时可用工具，group_id={group_id}。")
                                    # 群内执行进度：透过总线记录（前端/TUI 可观测），不刷屏
                                    _ar = _agent.run_chat(
                                        user_input=text, session_id=_agent_sid,
                                        is_group=True, context_hint=_agent_ctx,
                                    )
                                    _ans = str((_ar or {}).get("answer") or "").strip()
                                    if not _ans and (_ar or {}).get("error"):
                                        _ans = f"（任务没跑完：{_ar.get('error')}）"
                                    if _ans:
                                        try:
                                            smart_send_group(str(group_id), _ans)
                                        except Exception:
                                            pass
                                        continue
                                    # answer 为空且无错误 → 不 continue，交给后续正常回复流程
                        except Exception as e:
                            log(f"[Agent] 群内执行异常: {e}")

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
                            # ★ 戳解锁的会话只对戳主开放（2026-10-11 用户拍板
                            # "只认戳的人+他带来的对话"）：in_session 是绕过
                            # 决策器的快速通道，poke_owner 不在场时他人消息
                            # 不得借道（想插嘴走正常触发/决策线路）。
                            _po = gs.get("poke_owner")
                            if _po and str(sender) != str(_po):
                                continue
                        # 标记为会话模式消息（remaining 递减推迟到 process_buffered_message 中）
                        is_at_bot = False
                        has_xiao_a = False
                        in_session = True
                    
                    # ── 入站：唯一入口（2026-10-25 流水线）──
                    # 合并 / 过期结算 / 新建三件事都在 _pipeline_upsert 里，main() 只
                    # 负责把这条消息的判定结果交过去。原来合并与新建两套分支各写一遍
                    # deadline 计算，过期分支还直接抢跑一次生成。
                    try:
                        _reply_mid = int(msg.get("message_id") or 0)
                    except Exception:
                        _reply_mid = 0
                    # ★ 2026-10-19：**引用的原主是 Bot 自己**才算"引用我必回"。
                    #   此前这里引用过 `quote_forces` —— 它在 main() 里从没定义过，
                    #   每条群消息白抛一次 NameError，且"被@/叫名字/引用 → 立即建立
                    #   会话"从来没生效过。
                    # ★ 2026-10-27：直接沿用上面引用注记算出的 `quote_me`，不再算第二遍。
                    #   上面那份含三条兜底（昵称匹配 / 自引用兜底 / "被引用原文里叫了
                    #   Bot"）。分两处算就会出现「prompt 里写'引用我，必回'、forced 却
                    #   按'不是我'放行给决策器」——同一件事两个消费者，结论打架。
                    _quote_me = bool(quote_me)
                    # 触发口径（旧 10216 那个条件的反面）：任一项命中 → 免过决策器。
                    _forced = bool(is_at_bot or has_xiao_a or has_bot_nick
                                   or _quote_me or has_text_at or in_session)
                    _res = _pipeline_upsert(
                        group_id, sender=sender, text=text,
                        images=_img_paths, reply_mid=_reply_mid,
                        forced=_forced, source="message",
                        flags={
                            "at_bot": is_at_bot, "xiao_a": has_xiao_a,
                            "bot_nick": has_bot_nick, "quote": has_quote,
                            "quote_me": _quote_me, "text_at": has_text_at,
                            "in_session": in_session,
                        })
                    # 被 @ / 叫「小a」/ 昵称 / 引用我 → 立即建立会话，让缓冲窗口内的
                    # 后续消息能被合并。只在**新建批次**时做，与旧行为一致。
                    if _res == "created" and (is_at_bot or has_xiao_a
                                              or has_bot_nick or _quote_me):
                        GROUP_SESSION[group_id] = {
                            "remaining": _session_round_budget(),
                            "last_msg_time": time.time(),
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
                    # 原先这里连日志都没有 —— 关掉后对方发消息完全静默，日志里一个字都找不到。
                    # 只补这一行：不加"台账"、不加接口（用户已否决那套多余的设计）。
                    log(f"私聊[{sender}] 好友自动回复已关闭（联系人管理页），跳过")
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
                _pm_quoted_urls = []
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
                                    # 引用的是图片 → 地址只在 raw_message 的 CQ 串里
                                    _raw_q = str(qdata.get("raw_message") or "")
                                    if _raw_q:
                                        for _qu in cq_image_urls(_raw_q):
                                            if _qu not in _pm_quoted_urls:
                                                _pm_quoted_urls.append(_qu)
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
                # ★ 引用的 raw_message 是 CQ 串：图片先抠出来（交给视觉层），
                #   文本再转人话 —— 否则模型只会看到一串带 token 的下载 URL。
                _pm_quoted_imgs = []
                if quoted_text:
                    try:
                        for _qu in (list(_pm_quoted_urls) + cq_image_urls(quoted_text)):
                            _qp = download_image_url(_qu)
                            if _qp and _qp not in _pm_quoted_imgs:
                                _pm_quoted_imgs.append(_qp)
                                break
                    except Exception:
                        pass
                    quoted_text = sanitize_cq(quoted_text)
                if quoted_text and text:
                    text = f"[引用] {quoted_text}\n[当前] {text}"
                elif quoted_text:
                    text = f"[引用] {quoted_text}"
                
                images = extract_images(msg)
                for _qp in _pm_quoted_imgs:
                    if _qp not in images:
                        images.append(_qp)

                # ── 私聊只发表情包 → 同回一张表情包（与群聊同一口径，2026-10-13）──
                if images and _all_memes(images) and _is_pure_image_text(text):
                    _mb = _meme_back_send(sender, False, images=images)
                    if _mb == MEME_BACK_COOLDOWN:
                        log("  → [表情包回敬] 冷却中，静默")
                        continue
                    if _mb:
                        log(f"  → [表情包回敬] 纯表情包消息 → 回敬 {_mb}")
                        continue
                    log("  → [表情包回敬] 没发出去，落回读图路径")

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
                # 重新入队的合并批次**不再转存**：它的每一条原文在入缓冲前
                # 已经各存过一次（下面这个位置），再存一遍等于同一句话进两次记忆，
                # 还会额外多出一条"两句拼在一起"的记录。合并只是把"什么时候回复"
                # 推后，不是产生新消息。
                # 反过来说，也不能"入缓冲前不存"——那样用户撤回时这句话就永远丢了。
                if not msg.get(_REQUEUE_FLAG):
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

                # ── 私聊消息合并（2026-10-25）──────────────────────────
                # 放在这里的理由（顺序很关键）：
                #   · 在"消息转存 + memory_feed"之后 → 每条消息都照常落日志/落记忆，
                #     合并只影响"什么时候回复"，不影响"记住了什么"。
                #   · 在一切 AI 调用之前 → 真正的省，不是等生成完再丢。
                #   · 在纯转发/表情包回敬/图片缓存/指令之后 → 这些分支各自有
                #     continue 语义，混进来会被合并不该合的东西。
                # 重新入队的那一条带 _REQUEUE_FLAG，不会再被拦一次（否则死循环）。
                # 判"该不该拦"在 _should_buffer_private 里（纯函数，测试能直接打）；
                # 这里只看返回值 —— 返回值 False 表示不该拦，继续走原流程。
                try:
                    if _buffer_private_message(sender, text, images, msg):
                        continue
                except Exception as _bf_e:
                    # 合并机制自己坏了不能让私聊整个不回 —— 退化成旧行为
                    log(f"私聊[{sender[:6]}] 合并不可用，按原样立刻处理: {_bf_e}")

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

                # ── 私聊 Agent 模式（2026-10-24 补：此前只有群聊有入口）──
                # 三道全过才走：配置开关（默认关）+ 管理员权限 + 明确执行意图。
                #
                # 为什么要默认关：私聊是伴侣场景，不是工作台。把「帮我想想怎么回她」
                # 这种话丢给 agent，会得到一份任务汇报式的回答，人格直接没了。
                # 用户在群里已经把这个开关打开了（agent_group_enable=true），
                # 但私聊的默认值不跟着开，两件事分开授权。
                #
                # 为什么带图时不走：图片已经下载到本地路径，agent 侧拿不到这个路径，
                # 模型会答「我看不到图」。带图的消息继续走主链（那里有视觉链路）。
                if text and not images and _config.get("agent_private_enable", False):
                    try:
                        from harness import agent_service as _pagent
                        if _pagent.is_ready():
                            _p_allowed, _p_why = _pagent.private_gate(
                                text=text, sender=sender)
                            if _p_allowed:
                                log(f"[Agent] 私聊[{sender[:6]}] 触发（{_p_why}）: {text[:60]}")
                                # 会话按人 + 日期复用：同一天的多次指令共享上下文，
                                # 跨天换新会话，避免历史无限增长。
                                _p_day = time.strftime("%Y%m%d")
                                _p_sid = f"priv-{sender}-{_p_day}"
                                _p_ctx = (f"当前是 QQ 私聊，对方 QQ={sender}。"
                                          f"任务做完把结果用一两句话回给他。")
                                _p_ar = _pagent.run_chat(
                                    user_input=text, session_id=_p_sid,
                                    is_group=False, context_hint=_p_ctx,
                                )
                                _p_ans = str((_p_ar or {}).get("answer") or "").strip()
                                if not _p_ans and (_p_ar or {}).get("error"):
                                    _p_ans = f"（任务没跑完：{_p_ar.get('error')}）"
                                if _p_ans:
                                    try:
                                        smart_send(_p_ans, sender)
                                    except Exception:
                                        pass
                                    try:
                                        save_instruction(text, _p_ans)
                                    except Exception:
                                        pass
                                    continue
                                # 没有答案也没错误 → 不 continue，交给正常回复流程
                    except Exception as _p_e:
                        log(f"[Agent] 私聊执行异常: {_p_e}")

                log("  → 进入AI聊天流程")
                # 3. AI 聊天（私聊记忆由 EGO/super_memory 向量召回经 before_reply 注入，见下方 ego 钩子）
                # ★ 私聊画像：这里以前**写死空串**，于是 `private_memory_context`
                #   永远为空 → call_deepseek 里那段 `if private_memory_context:`
                #   （【用户画像】注入）从不执行 → 本体记忆（沙漏）根本不进私聊 prompt。
                #   症状（聊天日志实证）：刚说"你那些事我还能忘？"，被追问
                #   "8月17号我干什么了"就只会"这我哪知道啊" —— 只能吹牛，人格显得很假。
                #   ⚠ 必须把**本轮用户原话**传进去：get_memory_prompt_block 的第二层
                #   「相关记忆」只在 message 非空且 light=False 时才做检索；
                #   传 "" 或 light=True 只剩一行元信息（实测 48 字符），等于没注入。
                pm_ctx = ""
                try:
                    from sandglass_bridge import get_memory_prompt_block as _mpb
                    # 2.0 阶段C：私聊检索层换 builder（沙漏带分 + super_memory 向量，
                    #   scope_id=sender 与 EGO after_reply 写入侧同 key）；
                    #   旧函数只留画像/日期/元信息层（skip_search 摘掉它的无阈值检索层，
                    #   防双注）。builder 异常才退回旧全量路径，链路不断。
                    from harness import memory_context as _mc
                    _mblk = _mc.build_block(text or "", scope="private",
                                            scope_id=str(sender), sources=("sand", "sm"))
                    _old = (_mpb(text or "", light=False, skip_search=True) or "").strip()
                    pm_ctx = ((_mblk + "\n\n" + _old) if _mblk else _old)[:1200]
                except Exception:
                    try:
                        from sandglass_bridge import get_memory_prompt_block as _mpb
                        pm_ctx = (_mpb(text or "", light=False) or "").strip()[:1200]
                    except Exception as _e:
                        pm_ctx = ""
                        log(f"[私聊画像] 组装失败（已降级为空）: {_e}")

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
                            "bot_name": _bot_nicknames()[0],
                            "bot_nicknames": _bot_nicknames(),
                        })
                        _ego_ctx = _ego_inject
                    except Exception:
                        _ego_ctx = ""

                # ── 图片进对话（与群聊同一套口径，见 image_reply_mode）──
                # describe（默认）：视觉模型转文字描述后拼进 text —— 主模型是纯文本的，
                #   直接塞 image_url 它看不到（老路径事实上无效）；
                # native：主模型确实多模态时，才把原图交给它。
                # ★ 表情包按需理解（与群聊同口径）：全是 GIF 且话里没在说这张图
                #   → 不读（省视觉调用、防"解释表情包"的 AI 味）。照片/截图维持原行为。
                # ★ 2026-10-22 收紧：私聊原先是"只要全是 GIF 就跳过"，照片/截图一律读，
#   而且私聊**没有** @/引用 这两个宽松条件（群聊有）。改成与群聊同一口径：
#   表情包 → memes_only=True（要强信号才读）；照片/截图 → 也要过一遍判定。
                _pm_need_img = True
                if images and _all_memes(images):
                    _pm_need_img = _image_understanding_needed(text, memes_only=True)
                    if not _pm_need_img:
                        log("私聊[%s] [图片理解] 跳过（表情包，消息没在说它）" % sender)
                # 照片/截图不设门禁（与群聊同口径，见群聊分支注释）
                _pm_call_images = (images if _pm_need_img else None) \
                    if (images and image_reply_mode() == "native") else None
                if images and _pm_need_img and image_reply_mode() == "describe":
                    _pm_blk = build_image_context_block(images[:3], scene="私聊")
                    if _pm_blk:
                        text = text + "\n" + _pm_blk
                        log(f"私聊[{sender}] [图片理解] {min(len(images), 3)} 张 · {len(_pm_blk)} 字")

                # ── 反应延迟（2026-10-22，与群聊同款）──
                #    私聊原来也是"收到 → 生成 → 发出"，中间 0 等待。
                #    一对一反而更像在盯着屏幕等回复，所以基数比群聊略低。
                _p_react = float(_config.get("private_reply_reaction_delay", 1.2))
                if _p_react > 0:
                    _p_delay = _p_react + (0.5 if len(text) > 50 else 0) + random.uniform(0, 0.5)
                    log(f"私聊[{sender[:6]}] [反应延迟] {_p_delay:.1f}s")
                    time.sleep(_p_delay)
                reply = call_deepseek(text, _pm_call_images, sender, private_memory_context=pm_ctx, personality_temperature=_ego_temp, ego_inject_context=_ego_ctx)
                # 私聊联网搜索标记循环（与群聊共用函数；旧版私聊吐 [搜索:] 会原样漏发）
                reply = _apply_search_tag_loop(reply, text, group_id=None,
                                               is_group=False, temperature=_ego_temp,
                                               target_qq=sender, ego_ctx=_ego_ctx)
                if reply:
                    log(f"私聊AI回复({sender[:6]}): {reply[:80]}")
                    # 表情包匹配：剥离 [表情] 标记后发送文本
                    clean_reply, _meme_emotion = parse_meme_intent(reply)
                    sent_text = clean_reply.strip() if clean_reply else ""
                    sent_text = _trim_incomplete(sent_text, getattr(reply, "truncated", False))
                    # ── 剥掉模型模仿来的上下文标记（2026-10-27，与群聊一致）──
                    sent_text = _strip_context_marks(sent_text)
                    # ── 去AI味：规则级后处理（私聊同样生效）──
                    try:
                        from de_ai import de_ai_post_process
                        sent_text = de_ai_post_process(sent_text, _config)
                    except Exception:
                        pass
                    if sent_text:
                        # ── 插件钩子：after_reply（发送前，允许插件修改回复文本）──
                        # T8: 传入 scope/sender，super_memory 才能把私聊写入正确作用域。
                        # 与群聊同款改为链式串联（原因见群聊分支注释）：
                        # 单数 execute_hook 会让高优先级插件的 override 挡住后面所有插件。
                        if PLUGIN_OK:
                            try:
                                pm = get_plugin_manager()
                                if pm and pm.has_hook("after_reply"):
                                    _ar_text, _ar_chain = pm.execute_hook_chain(
                                        "after_reply", message=text, group_id="",
                                        reply=sent_text,
                                        context={"scope": "private", "sender": sender})
                                    if _ar_chain:
                                        log(f"私聊[{sender}] [插件覆盖] {' → '.join(_ar_chain)}")
                                    if _ar_text != sent_text:
                                        sent_text = _ar_text
                            except Exception as e:
                                log(f"[Plugin] after_reply error: {e}")
                        # ── typed 并列派发（私聊作用域）──
                        try:
                            if PLUGIN_OK:
                                _tpm = get_plugin_manager()
                                _t_new = _dispatch_typed_after_reply(
                                    _tpm, message=text, group_id="", reply=sent_text,
                                    context={"scope": "private", "sender": sender})
                                if _t_new is not None and _t_new != sent_text:
                                    if _t_new == "":
                                        log(f"私聊[{sender}] [插件抑制] typed after_reply 返回空串 → 不发送")
                                    else:
                                        log(f"私聊[{sender}] [typed 覆盖] {_t_new[:60]}")
                                    sent_text = _t_new
                        except Exception as e:
                            log(f"[Plugin] typed after_reply error: {e}")
                        # ── 事后校验（2026-10-23，与群聊同款）──
                        #  私聊更容易出现这类问题：一对一场景下模型更倾向"配合到底"。
                        try:
                            from harness import reply_audit as _ra
                            from harness import tool_runner as _tr2
                            _hits = _ra.find_unsupported_claims(sent_text, _tr2.recent_tool_calls())
                            if _hits:
                                _before = sent_text
                                sent_text = _ra.rewrite_unsupported(sent_text, _hits)
                                log(f"私聊[{sender}] [事后校验] 声称做过但无工具调用，已改写: "
                                    f"{_hits[0][:40]} → {sent_text[:40]}")
                                if not sent_text:
                                    sent_text = _before
                        except Exception as e:
                            log(f"[reply_audit] 校验跳过: {e}")
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
                    ch.append({"role": "user", "content": text, "ts": time.time()})
                    ch.append({"role": "assistant", "content": reply[:500],
                               "ts": time.time()})
                    # C2 存储对齐：私聊截断 60→24 轮（组装只取最近10轮，储存更多纯占内存+IO）
                    chat_history[sender] = ch[-24:]
                    # 转存 AI 回复
                    save_chat_log(sender, _bot_nicknames()[0], text, reply[:500])
                    save_instruction(text, reply)
                    # 统一记忆分发（1.5 任务 2.3）：私聊回复侧同步
                    try:
                        from sandglass_bridge import memory_feed
                        memory_feed({
                            "text": text,
                            "group_id": "",
                            "sender": sender,
                            "reply": reply,
                            # 私聊用户侧在入口（上方 memory_feed）已落沙一次，回复侧只补 Bot 行
                            "user_pre_logged": True,
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
                # F: 定期清理上下文缓存（摘要/时间注入/历史账本），
                #    只在群超过 24h 无消息或私聊历史已清时回收，防 dict 无限增长
                _ctx_ttl = 24 * 3600
                def _drop_history_ledger(ck):
                    _HISTORY_SUMMARY_LINES.pop(ck, None)
                    _HISTORY_LAST_WINDOW.pop(ck, None)
                    _HISTORY_SUMMARY_TEXT.pop(ck, None)
                for gid in list(GROUP_SUMMARY.keys()):
                    _last = _GROUP_SUMMARY_AT.get(gid) or _GROUP_SUMMARY_INJECT_AT.get(gid) or 0
                    if not isinstance(_last, (int, float)) or now - _last > _ctx_ttl:
                        GROUP_SUMMARY.pop(gid, None)
                        _GROUP_SUMMARY_PREV.pop(gid, None)
                        _GROUP_SUMMARY_AT.pop(gid, None)
                        _GROUP_SUMMARY_LEN.pop(gid, None)
                        _GROUP_TIME_INJECT_AT.pop(gid, None)
                        _GROUP_SUMMARY_INJECT_AT.pop(gid, None)
                        _drop_history_ledger(f"g_{gid}")
                # 私聊历史缓存的 key 跟随 chat_history 是否已清（保留活跃 QQ 的缓存）
                for qq in [k[2:] for k in list(_HISTORY_SUMMARY_LINES.keys()) if k.startswith("p_")]:
                    if qq not in chat_history or not chat_history.get(qq):
                        _drop_history_ledger(f"p_{qq}")
                # 定期保存状态（防止崩溃丢失记忆；D 节流已生效，此处 force 兜底）
                save_state(force=True)
            except Exception:
                pass

        time.sleep(3)


if __name__ == "__main__":
    main()