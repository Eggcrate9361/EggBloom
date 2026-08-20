# -*- coding: utf-8 -*-
"""protocols/_ws_client.py — 最小 WebSocket 客户端（仅标准库）

NapCat 的事件通道首选 OneBot v11 WebSocket 服务器（httpClient 在该版本
推送事件不可靠）。本模块用纯标准库实现 RFC6455 客户端，避免引入
websocket-client 等第三方依赖（EggBloom 自带 Python 未安装）。

用法：
    ws = WebSocketClient("ws://127.0.0.1:3101", timeout=10)
    ws.connect()
    for opcode, payload in ws.iter_frames():
        ...
    ws.close()
"""

import base64
import hashlib
import os
import socket
import struct
from urllib.parse import urlparse

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BIN = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


class WebSocketClient:
    def __init__(self, url: str, timeout: float = 10.0):
        self._url = url
        self._timeout = timeout
        self._sock: socket.socket = None

    # ── 握手 ─────────────────────────────────────────────────
    def connect(self):
        p = urlparse(self._url)
        host = p.hostname or "127.0.0.1"
        port = p.port or 80
        path = p.path or "/"
        if p.query:
            path = path + "?" + p.query
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock = socket.create_connection((host, port), timeout=self._timeout)
        self._sock.sendall(req.encode("latin-1"))
        resp = self._read_http_response()
        if "101" not in resp.split("\r\n", 1)[0]:
            self._sock.close()
            self._sock = None
            raise RuntimeError(f"WebSocket 握手失败: {resp.splitlines()[0] if resp else '空响应'}")
        accept = hashlib.sha1((key + _GUID).encode()).digest()
        expect = base64.b64encode(accept).decode()
        if expect not in resp:
            self._sock.close()
            self._sock = None
            raise RuntimeError("WebSocket Sec-WebSocket-Accept 校验失败")

    def _read_http_response(self) -> str:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data.decode("latin-1", "replace")

    # ── 帧收发 ───────────────────────────────────────────────
    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except socket.timeout:
                raise ConnectionError("读取超时")
            if not chunk:
                raise ConnectionError("连接已关闭")
            buf += chunk
        return buf

    def iter_frames(self):
        """逐帧读取。返回 (opcode, payload)。opcode=8 表示关闭。

        内部自动处理控制帧：
        - PING(0x9) → 回 PONG(0xA) 保持连接活跃（NapCat WS Server 30s 心跳），
          不回 PONG 会被服务端判定连接失效断开 → 事件桥周期性掉线
        - PONG(0xA) → 忽略
        调用方只看到 text / binary / close。
        """
        while True:
            try:
                b0, b1 = self._recv_exact(2)
            except ConnectionError:
                return
            _ = b0 >> 7 & 1  # fin（NapCat 事件均为单帧）
            opcode = b0 & 0x0F
            masked = b1 >> 7 & 1
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            mask_key = self._recv_exact(4) if masked else None
            payload = self._recv_exact(length) if length else b""
            if mask_key:
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
            if opcode == _OP_PING:
                # 收到心跳 PING → 必须回 PONG，否则服务端断开连接
                try:
                    self._send_frame(_OP_PONG, payload)
                except Exception:
                    pass
                continue
            if opcode == _OP_PONG:
                continue
            yield opcode, payload
            if opcode == _OP_CLOSE:
                return

    def _send_frame(self, opcode: int, payload: bytes):
        if not self._sock:
            raise ConnectionError("未连接")
        mask_key = os.urandom(4)
        header = bytes([0x80 | opcode, 0x80])
        n = len(payload)
        if n <= 125:
            header += bytes([n])
        elif n <= 0xFFFF:
            header += bytes([126]) + struct.pack(">H", n)
        else:
            header += bytes([127]) + struct.pack(">Q", n)
        header += mask_key
        self._sock.sendall(header + _mask(payload, mask_key))

    def send(self, payload: bytes):
        self._send_frame(_OP_TEXT, payload)

    def close(self):
        try:
            if self._sock:
                self._send_frame(_OP_CLOSE, b"")
        except Exception:
            pass
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _mask(payload: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % 4] for i, b in enumerate(payload))


def default_ws_url(port: int) -> str:
    return f"ws://127.0.0.1:{port}"
