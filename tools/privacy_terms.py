# -*- coding: utf-8 -*-
"""源码与提交信息共用的本地隐私词来源。

真实昵称只保存在被 gitignore 的 `.nicklist`，或由环境变量临时补充。
仓库只保留通用的「老大」，不集中存放身份词清单。
"""
from __future__ import annotations

import hashlib
import os
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_NICK_FILE = os.path.join(ROOT, ".nicklist")
ENV_NAME = "EGGBLOOM_COMMIT_NICK_BLOCKLIST"


def nick_digest(value: str) -> str:
    """昵称归一化后的短摘要，仅用于历史精确豁免。"""
    norm = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def load_nicknames(nick_file: str | None = None, env_value: str | None = None) -> tuple:
    path = str(nick_file or os.environ.get("EGGBLOOM_NICKLIST") or DEFAULT_NICK_FILE)
    values = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                value = line.strip()
                if value and not value.startswith("#"):
                    values.append(value)
    except Exception:
        pass
    raw_env = (os.environ.get(ENV_NAME, "") if env_value is None else env_value)
    values.extend(x.strip() for x in str(raw_env or "").split(",") if x.strip())
    return tuple(dict.fromkeys(values))
