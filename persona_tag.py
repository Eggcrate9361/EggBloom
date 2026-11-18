from __future__ import annotations

import hashlib
import os
import re

__all__ = ["context", "derive", "TAG", "TEMPLATE"]

_SALT = b"pt-v1-2026"
_ITER = 120_000
_TAG_LEN = 16
EXPECT_LEN = 10

TAG = os.environ.get("EGGBLOOM_PERSONA_TAG", "").strip().lower()

TEMPLATE = "【对象】{who}=老大，亲近暧昧些。"

_RE_CANDIDATE = re.compile(r"(?<!\d)(\d{5,12})(?!\d)")

_CACHE: dict = {}
_CACHE_MAX = 512


def derive(qq: str) -> str:
    raw = hashlib.pbkdf2_hmac("sha256", str(qq).strip().encode("utf-8"),
                              _SALT, _ITER)
    return raw.hex()[:_TAG_LEN]


def _tag_of(candidate: str) -> str:
    d = _CACHE.get(candidate)
    if d is None:
        d = derive(candidate)
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[candidate] = d
    return d


def _match(who: str, want: str) -> bool:
    if not who or not want:
        return False
    if EXPECT_LEN and len(who) != EXPECT_LEN:
        return False
    return _tag_of(who) == want


def context(user_msg: str, focus_qq=None, nickname_resolver=None,
            digest=None, sender=None) -> str:
    msg = str(user_msg or "")
    who = str(sender or "").strip()
    q = str(focus_qq or "").strip()
    want = (TAG if digest is None else str(digest or "")).strip().lower()

    hit = ""

    if who and q and who == q:
        hit = who
    elif _match(who, want):
        hit = who
    elif q and q in msg:
        hit = q
    elif want and msg:
        for cand in _RE_CANDIDATE.findall(msg):
            if _match(cand, want):
                hit = cand
                break

    if not hit:
        return ""

    nick = ""
    if callable(nickname_resolver):
        try:
            nick = str(nickname_resolver(hit) or "").strip()
        except Exception:
            nick = ""
    return TEMPLATE.format(who=f"{nick}（QQ {hit}）" if nick else f"QQ {hit}")
