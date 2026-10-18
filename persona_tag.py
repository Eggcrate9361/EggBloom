from __future__ import annotations

import hashlib
import re

__all__ = ["context", "derive", "TAG", "TEMPLATE"]

_SALT = b"pt-v1-2026"
_ITER = 120_000
_TAG_LEN = 16
EXPECT_LEN = 10

TAG = "ef6293d9520a7360"

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


_FRAGMENTS = (
    "v1:t8BpkbDxEQnmFdE_jGSWUt3n_OXE5IY24DfoVaADUskvn8QmkkQx2pwet2RGv2ghrLX9VYnb_RTUO0GEsbzfQhfU70Mo7G7_C6d2O3TKT3jCA_meY9tqNVIlbcn3W1nun1zFM8olyd2_dwS6y-E7nX6ljYjF5KBNuI2zSw7PhruSyZ2fHh4sEmKyBE0cwA07kwO17SfUd5uxcPMFZg==",
    "v1:QSHH1J43ovY4LNcFT_R4KoQl9eWGjEAHB1n1-2VDS-3ca1Y2k6NqjUNrX2R6m-4IZLy0PEAWLAiux9phEYlPcoYBbBeWqb4oSXnKHPmGT8GjGEYwsGwPjwTGoem_3rUBNG7SD49OGePUK3Wm7RS3G2IZ2R-8ZPvOtyyFq2uTmV5ZvpVf_7o9Jap-jFteRNmnyCeNoW-9HToSD8EsEHIWQ4OmgZnDO3pLxtrmM0m8ZbJ3NPS09oQCG4tJWRQpSaerArcvZt_Dh6JvuItTxmU3nBwmoSgyfNbf2Tp9xFUxJuisCbhe81Dp0nLK94gKJAqq6bW6VUFdX0DGaA==",
    "v1:JuyVUWlxduaSyjIjp4xw6sqUhapQ64qLoGVx8F3YRYGroNkZE-Arodrtc2rbY5Qmgcx4P2q_OtyfaHko8m0okiDXjo2YHjoSJsa-4TMh0TGXvVvvxTBHyr92KcRBqtJPy-o6Qln1iU2oPfGOWe--y6zTNse3C2LSbaWbqZIcVROXhKH49l9pOO-sk86W8WC5WMHfUh6Nxnn3ylb-sHMFg2vXDgdYLE5vSbkE016QnW72GFGBK2vIt0CU70u-BLKJ3IV4DesxZFnqQjU=",
    "v1:dx4J7Oa5e9CJvo0iE6irSi9Ix8tsA9ZQwpsUzP0MJxxbnxT_udyvS8mOLDhcZtVhkx4RZZTXyhFwEoItYJbAa-p61z59I_dMhUXd5pcE9cktbgigw-6rbraCdm5ac3tCATiWABrz5mThrLZNM8oiVMbjhidk8QyUz_SnupxM3qSJbCfVA6CpMlSXKA08bSQ8gDLQrlRIhyVrekdCdVywEBmI53WKrA==",
    "v1:SiLpJe2hNCkKPoHbza-wwc915Ex6cdL4v8sPdWbBhy93553cPQYGYG1L6_SB7W-yQdve4F0NuBu3Pfy14LSqB2hEoyHDyjbI1OFNVSuvOIoDDl4lGzNnjRPHbRzxGuvzPxurJohCnZB7VfwWFtgZ6FoHuN8RIpjNHzSc4mImSTJDmdc1oKbtICN-2GjJg0nGQ883sk5A1Iicz2Fp3kpmbcePJHEDE1FoLq2qW_SVEuPDUc5xaRs7i_Iqjq4BNXdwxsLWKMTpY7dR8fcK71yPU7H444d481HY7g_mP3nxW1ECzvCDPrFBpU8BqJb3CmPOWF9kqP-sn5Bvza49cNt_tA==",
)
