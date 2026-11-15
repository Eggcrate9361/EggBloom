# -*- coding: utf-8 -*-
"""cred_store.py - API Key 安全存储。

把敏感 API Key 存进 Windows 凭据管理器（Credential Manager / DPAPI 加密，
由操作系统保护），绝不落盘明文到程序目录。纯 ctypes 实现，无第三方依赖。
"""
import ctypes
import os
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2

# 业务命名空间前缀，避免与其他应用冲突
_PREFIX = "EggBloom"


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.c_void_p),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _target(name):
    return f"{_PREFIX}.{name}"


def _advapi32():
    if os.name != "nt":
        return None
    try:
        return ctypes.windll.advapi32
    except Exception:
        return None


def write_cred(name, value):
    """写入密钥到凭据管理器，成功返回 True。"""
    adv = _advapi32()
    if adv is None or not value:
        return False
    blob = value.encode("utf-16-le")
    try:
        blob_buf = ctypes.create_string_buffer(blob)
        cred = _CREDENTIALW(
            Flags=0,
            Type=CRED_TYPE_GENERIC,
            TargetName=ctypes.c_wchar_p(_target(name)),
            Comment=ctypes.c_wchar_p("eggbloom secret"),
            CredentialBlobSize=len(blob),
            CredentialBlob=ctypes.cast(blob_buf, ctypes.c_void_p),
            Persist=CRED_PERSIST_LOCAL_MACHINE,
            AttributeCount=0,
            Attributes=None,
            TargetAlias=None,
            UserName=ctypes.c_wchar_p("EggBloom"),
        )
        return bool(adv.CredWriteW(ctypes.byref(cred), 0))
    except Exception:
        return False


def read_cred(name):
    """从凭据管理器读取密钥，未找到/失败返回 None。"""
    adv = _advapi32()
    if adv is None:
        return None
    try:
        pcred = ctypes.POINTER(_CREDENTIALW)()
        ok = adv.CredReadW(ctypes.c_wchar_p(_target(name)), CRED_TYPE_GENERIC, 0, ctypes.byref(pcred))
        if not ok or not pcred:
            return None
        try:
            cred = pcred.contents
            size = int(cred.CredentialBlobSize or 0)
            if not size or not cred.CredentialBlob:
                return None
            raw = ctypes.string_at(cred.CredentialBlob, size)
            return raw.decode("utf-16-le", errors="ignore").strip() or None
        finally:
            adv.CredFree(pcred)
    except Exception:
        return None


def exists_cred(name):
    return read_cred(name) is not None


def delete_cred(name):
    adv = _advapi32()
    if adv is None:
        return False
    try:
        return bool(adv.CredDeleteW(ctypes.c_wchar_p(_target(name)), CRED_TYPE_GENERIC, 0))
    except Exception:
        return False


# 约定用途
MODEL_API_KEY_CRED = "MODEL_API_KEY"  # deepseek 等对话模型 api_key