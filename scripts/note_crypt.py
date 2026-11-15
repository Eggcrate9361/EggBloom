# -*- coding: utf-8 -*-
"""scripts/note_crypt.py —— 给仓库里的文字做一层可逆混淆。

用途
    有些话只想给自己（或愿意动手解密的人）看，不想在公开仓库里明晃晃写着。
    本脚本把一段文字变成 `v1:<base64>`，解密需要同一个 key。
    典型用法：`persona_tag._FRAGMENTS` 里那几条碎碎念。

⚠ 这是混淆，不是加密
    · 密钥来自 config.focus_qq（或 --key），不写在仓库里；
    · 但拿到 key 的人一定能解开，且 10 位号码的 key 空间有限；
    · 它挡的是"随手读代码的人"，不是"专门研究你的人"。

用法
    # 加密（默认从发行包 bot_config.json 读 focus_qq）
    python scripts/note_crypt.py enc "要藏的文字"

    # 解密（把仓库里那串贴进来）
    python scripts/note_crypt.py dec "v1:xxxx"

    # 指定 key（不读配置）
    python scripts/note_crypt.py enc --key 1234567890 "文字"

    # 从文件读/写，避免命令行转义
    python scripts/note_crypt.py enc --in note.txt --out note.enc
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys

DEFAULT_CFG = r"E:\EggBloom水星\EggBloom Desktop-new\resources\app\bot_config.json"
PREFIX = "v1:"


def _key_material(key: str) -> bytes:
    return hashlib.sha256(("note|" + str(key)).encode("utf-8")).digest()


def _keystream(material: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(material + nonce + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(out[:n])


def encrypt(text: str, key: str) -> str:
    data = str(text).encode("utf-8")
    nonce = os.urandom(8)
    ks = _keystream(_key_material(key), nonce, len(data))
    ct = bytes(a ^ b for a, b in zip(data, ks))
    return PREFIX + base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt(token: str, key: str) -> str:
    token = str(token).strip()
    if not token.startswith(PREFIX):
        raise ValueError("不是 v1: 开头的密文")
    raw = base64.urlsafe_b64decode(token[len(PREFIX):].encode("ascii"))
    nonce, ct = raw[:8], raw[8:]
    ks = _keystream(_key_material(key), nonce, len(ct))
    return bytes(a ^ b for a, b in zip(ct, ks)).decode("utf-8")


def _read_key(args) -> str:
    if args.key:
        return str(args.key).strip()
    for p in (args.config, DEFAULT_CFG):
        try:
            d = json.load(open(p, encoding="utf-8"))
            k = str(d.get("focus_qq") or "").strip()
            if k:
                return k
        except Exception:
            continue
    raise SystemExit("找不到 key：请用 --key 指定，或在 bot_config.json 配 focus_qq")


def main() -> int:
    ap = argparse.ArgumentParser(description="维护说明的可逆混淆")
    ap.add_argument("action", choices=("enc", "dec"))
    ap.add_argument("text", nargs="?", default=None, help="要处理的文字")
    ap.add_argument("--key", default=None)
    ap.add_argument("--config", default=DEFAULT_CFG)
    ap.add_argument("--in", dest="infile", default=None)
    ap.add_argument("--out", dest="outfile", default=None)
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    key = _read_key(args)
    text = args.text
    if text is None and args.infile:
        text = open(args.infile, encoding="utf-8").read()
    if text is None:
        raise SystemExit("缺少文字：传位置参数或 --in 文件")

    result = encrypt(text, key) if args.action == "enc" else decrypt(text, key)
    if args.outfile:
        open(args.outfile, "w", encoding="utf-8").write(result)
        print(f"写入 {args.outfile}（{len(result)} 字符）")
    else:
        print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
