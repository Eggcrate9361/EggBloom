# -*- coding: utf-8 -*-
"""ai_client.py      AI 客户端 (DeepSeek/OpenAI/兼容, SSL 上下文)"""
import urllib.request, urllib.error, ssl, os, sys, base64, hashlib
from utils import log

try:
    import requests
    HAS_REQUESTS = True
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass
except ImportError:
    HAS_REQUESTS = False

# 自定义 SSL 上下文（urllib 降级路径使用）
_SSL_CTX = ssl.create_default_context()
# requests.Session 单例：连接池 + keep-alive，避免每次请求重建连接
_SESSION = None


def _get_session():
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers["User-Agent"] = "Mozilla/5.0"
    return _SESSION


def set_no_verify():
    """禁用 SSL 证书验证（本地调试/自签证书），统一入口"""
    global _SSL_CTX
    _SSL_CTX = ssl.create_default_context()
    _SSL_CTX.check_hostname = False
    _SSL_CTX.verify_mode = ssl.CERT_NONE
    if HAS_REQUESTS:
        _get_session().verify = False

if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

IMAGE_CACHE = os.path.join(SCRIPT_DIR, "image_cache")
os.makedirs(IMAGE_CACHE, exist_ok=True)

class AIClient:
    def __init__(self, api_url, api_key, model="deepseek-flash", personality="", max_retries=3, provider="openai", temperature=None):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.system_prompt = personality
        self.max_retries = max_retries
        self.temperature = temperature
        from providers import make_provider
        self._provider = make_provider(
            api_url=api_url, api_key=api_key, model=model,
            provider=provider, max_retries=max_retries, temperature=temperature,
        )

    def call(self, messages, tools=None, stream=False, max_tokens=800):
        """调用 AI API（返回 reply_text, tool_calls_list_or_None, usage_dict）"""
        return self._provider.chat(messages, tools=tools, stream=stream, max_tokens=max_tokens)

    def call_stream(self, messages, max_tokens=800):
        """流式调用 AI API（生成器）"""
        return self._provider.chat_stream(messages, max_tokens=max_tokens)

    def tool_loop(self, messages, tool_funcs, tools_def, max_rounds=3):
        """工具调用循环"""
        return self._provider.tool_loop(messages, tool_funcs, tools_def, max_rounds=max_rounds)


def extract_text(msg):
    raw = msg.get("message", "")
    if isinstance(raw, str): return raw
    if isinstance(raw, list):
        parts = []
        for seg in raw:
            if isinstance(seg, dict) and seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
        return " ".join(parts)
    return str(raw)

def extract_images(msg):
    """从 OneBot 消息中提取图片（支持 HTTP URL 和本地路径）"""
    raw = msg.get("message", [])
    if not isinstance(raw, list): return []
    images = []
    for seg in raw:
        if isinstance(seg, dict) and seg.get("type") == "image":
            data = seg.get("data", {})
            url = data.get("url", "")
            if url.startswith("http"):
                local = os.path.join(IMAGE_CACHE, f"img_{hashlib.md5(url.encode()).hexdigest()[:12]}.jpg")
                if not os.path.exists(local):
                    try:
                        if HAS_REQUESTS:
                            r = _get_session().get(url, timeout=15)
                            r.raise_for_status()
                            with open(local, "wb") as f:
                                f.write(r.content)
                        else:
                            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                            with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as r:
                                with open(local, "wb") as f: f.write(r.read())
                    except Exception as e:
                        log(f"image download: {e}")
                        continue
                images.append(local)
            elif data.get("file") and os.path.exists(data["file"]):
                images.append(data["file"])
    return images

def image_to_base64(file_path):
    """将图片文件转换为 base64 data URI"""
    try:
        with open(file_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(file_path)[1].lower().replace(".", "")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(ext, "jpeg")
        return f"data:image/{mime};base64,{b64}"
    except Exception:
        return None
