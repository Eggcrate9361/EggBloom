# -*- coding: utf-8 -*-
"""onebot_client.py   OneBot HTTP 客户端 (SSL 支持)"""
import json, urllib.request, urllib.error, ssl, base64, os
from concurrent.futures import ThreadPoolExecutor
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

# 全局 SSL 上下文（urllib 降级路径使用）
_SSL_CTX = ssl.create_default_context()
# requests.Session 单例：连接池 + keep-alive，统一 HTTP 出口
_SESSION = None


def _get_session():
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
    return _SESSION


def set_no_verify():
    """禁用 SSL 证书验证（本地调试/自签证书），统一入口"""
    global _SSL_CTX
    _SSL_CTX = ssl.create_default_context()
    _SSL_CTX.check_hostname = False
    _SSL_CTX.verify_mode = ssl.CERT_NONE
    if HAS_REQUESTS:
        _get_session().verify = False


def _http_post_json(url, body, timeout=15):
    """统一 JSON POST：requests.Session 优先（连接池复用），urllib 降级"""
    if HAS_REQUESTS:
        r = _get_session().post(url, data=body,
            headers={"Content-Type": "application/json"}, timeout=timeout)
        return json.loads(r.content.decode("utf-8"))
    req = urllib.request.Request(url, data=body,
        headers={"Content-Type": "application/json"})
    ctx = _SSL_CTX if url.startswith("https") else None
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8"))

class OneBotClient:
    def __init__(self, base_url="http://127.0.0.1:3000", bot_qq=""):
        self.base_url = base_url.rstrip("/")
        self.bot_qq = str(bot_qq) if bot_qq else ""

    def api_call(self, endpoint, data=None):
        """OneBot HTTP API 调用（requests.Session 连接池，urllib 降级）"""
        url = f"{self.base_url}/{endpoint}"
        body = json.dumps(data or {}).encode("utf-8")
        try:
            return _http_post_json(url, body)
        except Exception as e:
            log(f"OneBot API [{endpoint}]: {e}")
            return {"retcode": -1}

    def send_private_msg(self, target_qq, text):
        """发送私聊消息"""
        resp = self.api_call("send_private_msg",
            {"user_id": int(target_qq), "message": text})
        return resp.get("retcode") == 0

    def send_group_msg(self, group_id, text):
        """发送群聊消息"""
        resp = self.api_call("send_group_msg",
            {"group_id": int(group_id), "message": text})
        return resp.get("retcode") == 0

    def send_image(self, file_path, target_qq):
        """发送私聊图片"""
        try:
            with open(file_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            msg = [{"type": "image", "data": {"file": f"base64://{b64}"}}]
            resp = self.api_call("send_private_msg",
                {"user_id": int(target_qq), "message": msg})
            return resp.get("retcode") == 0
        except Exception as e:
            log(f"send image: {e}")
            return False

    def send_group_image(self, group_id, file_path):
        """发送群聊图片"""
        if not os.path.exists(file_path): return False
        try:
            with open(file_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            msg = [{"type": "image", "data": {"file": f"base64://{b64}"}}]
            resp = self.api_call("send_group_msg",
                {"group_id": int(group_id), "message": msg})
            return resp.get("retcode") == 0
        except Exception as e:
            log(f"send group image: {e}")
            return False

    def get_new_messages(self, target_qqs, group_ids, last_msg_time):
        """拉取好友和群聊的新消息（并发拉取，降低多目标轮询延迟）"""
        all_msgs = []

        def _fetch_private(tqq):
            out = []
            resp = self.api_call("get_friend_msg_history",
                {"user_id": tqq, "count": 10})
            if resp.get("retcode") == 0:
                for msg in resp.get("data", {}).get("messages", []):
                    msg["_type"] = "private"
                out = resp.get("data", {}).get("messages", [])
            return out

        def _fetch_group(gid):
            out = []
            resp = self.api_call("get_group_msg_history",
                {"group_id": int(gid), "count": 10})
            if resp.get("retcode") == 0:
                for msg in resp.get("data", {}).get("messages", []):
                    msg["_type"] = "group"
                    msg["_group_id"] = gid
                out = resp.get("data", {}).get("messages", [])
            return out

        tasks = [(_fetch_private, tqq) for tqq in target_qqs] + [(_fetch_group, gid) for gid in group_ids]
        if tasks:
            workers = min(len(tasks), 8)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = [ex.submit(fn, arg) for fn, arg in tasks]
                for fut in futures:
                    all_msgs.extend(fut.result())
        all_msgs.sort(key=lambda m: m.get("time", 0))
        new_msgs, max_t, seen = [], last_msg_time, set()
        for msg in all_msgs:
            mt = msg.get("time", 0)
            mid = msg.get("message_id", 0)
            if mid in seen: continue
            seen.add(mid)
            if mt > max_t: max_t = mt
            if mt > last_msg_time: new_msgs.append(msg)
        return new_msgs, max_t

    def get_login_info(self):
        """获取登录信息"""
        return self.api_call("get_login_info")

    def get_msg(self, message_id):
        """获取指定消息详情"""
        return self.api_call("get_msg", {"message_id": int(message_id)})

    def poke(self, group_id=None, user_id=None):
        """戳一戳"""
        if group_id:
            self.api_call("group_poke", {"group_id": int(group_id), "user_id": user_id})
        else:
            self.api_call("friend_poke", {"user_id": user_id})

    def get_stranger_info(self, user_id):
        """获取陌生人信息"""
        return self.api_call("get_stranger_info", {"user_id": int(user_id)})
