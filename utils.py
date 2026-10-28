# -*- coding: utf-8 -*-
"""utils.py         工具函数模块"""
import datetime, os, sys, re, json
import logging
from logging.handlers import RotatingFileHandler

if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from data_store import get_log_dir
    LOG_DIR = get_log_dir()
except Exception:
    LOG_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "EggBloom", "logs")
    os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "qq_bot.log")
CHAT_LOG_DIR = os.path.join(SCRIPT_DIR, "chat_logs")

os.makedirs(CHAT_LOG_DIR, exist_ok=True)

# 模块级 logging 配置（RotatingFileHandler：5MB × 5，线程安全，避免每次 open/close）
_logger = logging.getLogger("eggb_utils")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    try:
        _handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        # 2026-10-28：带日期（与 bot_core.log 统一）。日志轮转后跨天的行
        # 只靠 HH:MM:SS 分辨不出是哪天，排查时会串会话。
        _handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s",
                                                datefmt="%Y-%m-%d %H:%M:%S"))
        _logger.addHandler(_handler)
    except Exception:
        pass


def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try: print(line, flush=True)
    except Exception: pass
    try:
        _logger.info("%s", msg)
    except Exception: pass

def chat_log(direction, chat_type, target_id, text, sender_name=""):
    payload = {
        "direction": direction, "type": chat_type,
        "target": str(target_id), "text": text[:300],
        "sender": sender_name,
        "time": datetime.datetime.now().strftime("%H:%M")
    }
    print(f"[CHAT]{json.dumps(payload, ensure_ascii=False)}", flush=True)

def split_by_punctuation(text):
    """按中英文标点和空格拆分文本"""
    chunks = re.split(r'[ ,，。！？；!?;\n\t]', text)
    return [c.strip() for c in chunks if c.strip()]

def _count_meaningful_chars(text):
    return sum(1 for c in text if c.isalnum() or '\u4e00' <= c <= '\u9fff')

def _protect_kaomoji(text):
    placeholders = {}
    counter = [0]
    def replacer(m):
        counter[0] += 1
        key = f"\x00KAO{counter[0]}\x00"
        placeholders[key] = m.group(0)
        return key
    protected = re.sub(r'[ (][^ )]{2,15}[ )]', replacer, text)
    protected = re.sub(r' [^ ]{2,15} ', replacer, protected)
    protected = re.sub(r'\[[^\]]{2,15}\]', replacer, protected)
    return protected, placeholders

def _restore_kaomoji(text, placeholders):
    for key, val in placeholders.items():
        text = text.replace(key, val)
    return text

def save_chat_log(target_qq, speaker, text, reply=""):
    try:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        log_dir = os.path.join(CHAT_LOG_DIR, f"QQ_{target_qq}")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"chat_{today}.md")
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        exists = os.path.exists(log_file)
        with open(log_file, "a", encoding="utf-8") as f:
            if not exists:
                f.write(f"# QQ {target_qq}\n## {today}\n\n")
            if reply:
                f.write(f"**{speaker}** `{ts}`\n\n{text}\n\n> {reply}\n\n---\n\n")
            else:
                f.write(f"**{speaker}** `{ts}`\n\n{text}\n\n---\n\n")
    except Exception as e:
        log(f"chat log save failed: {e}")
