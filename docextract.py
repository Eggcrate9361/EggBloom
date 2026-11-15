"""docextract.py —— 把常见办公文档抽成纯文本（只用标准库，PDF 走可选依赖）

用途：QQ 里有人发文件（ppt/docx/xlsx/pdf/txt），bot 现在只会记一句「[文件]」，
看不到里面是什么。这里负责把内容读出来，拼进消息上下文。

设计原则：
· 不装新依赖也能用：pptx/docx/xlsx 本质是 zip + XML，标准库直接解。
· PDF 需要 pypdf（已装就用，没装就返回空串，由上层如实说"读不出来"）。
· 永远不抛异常：读不出来返回 ""，上层据此写占位，避免模型瞎编。
· 有长度上限：抽出来的正文截到 max_chars，防止把上下文撑爆。
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from typing import Iterable

#: 支持直接抽取的扩展名
TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".py", ".yaml", ".yml",
             ".ini", ".conf", ".html", ".htm", ".xml", ".sql", ".js", ".ts", ".css"}
OFFICE_EXTS = {".pptx", ".docx", ".xlsx", ".pptm", ".docm", ".xlsm"}
PDF_EXTS = {".pdf"}

#: 单个文件最多读多少字节（防止有人发 200MB 视频进来）
MAX_BYTES = 24 * 1024 * 1024
#: 抽出来的正文最多保留多少字符
MAX_CHARS = 4000

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u00a0]+")
_NL_RE = re.compile(r"\n{3,}")


def ext_of(name: str) -> str:
    try:
        return os.path.splitext(str(name or "").strip().lower())[1]
    except Exception:
        return ""


def _xml_text(xml_bytes: bytes) -> str:
    """把一段 OOXML 片段里的 <a:t>/<w:t>/<t> 文本抠出来。"""
    try:
        s = xml_bytes.decode("utf-8", "ignore")
    except Exception:
        return ""
    # 先在标签边界插入分隔，避免两段文字粘成一句
    s = re.sub(r"</(a:p|w:p|row|si)>", "\n", s)
    s = re.sub(r"<a:br\s*/>|<w:br\s*/>", "\n", s)
    s = _TAG_RE.sub("", s)
    s = (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
          .replace("&quot;", '"').replace("&apos;", "'").replace("&#10;", "\n"))
    s = _WS_RE.sub(" ", s)
    s = _NL_RE.sub("\n\n", s)
    return s.strip()


def _pptx_text(z: zipfile.ZipFile) -> str:
    """幻灯片刻按页码排序，页与页之间空一行；备注页也读（很多信息在那）。"""
    names = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]

    def _num(n: str) -> int:
        m = re.search(r"(\d+)", os.path.basename(n))
        return int(m.group(1)) if m else 0

    out = []
    for i, n in enumerate(sorted(names, key=_num), 1):
        body = _xml_text(z.read(n))
        if body:
            out.append(f"[第{i}页]\n{body}")
    for n in sorted([x for x in z.namelist()
                     if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", x)], key=_num):
        body = _xml_text(z.read(n))
        if body:
            out.append(f"[备注]\n{body}")
    return "\n\n".join(out)


def _docx_text(z: zipfile.ZipFile) -> str:
    out = []
    if "word/document.xml" in z.namelist():
        out.append(_xml_text(z.read("word/document.xml")))
    for n in z.namelist():
        if re.match(r"word/(header\d*|footer\d*)\.xml$", n):
            t = _xml_text(z.read(n))
            if t:
                out.append(t)
    return "\n\n".join(x for x in out if x)


def _xlsx_text(z: zipfile.ZipFile) -> str:
    """表格没有公式求值，只按行把单元格文字拼出来（够模型理解在说什么）。"""
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        s = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
        shared = [re.sub(r"<[^>]+>", "", m) for m in
                  re.findall(r"<si>(.*?)</si>", s, re.S)]
    out = []
    sheets = sorted([n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)])
    for n in sheets[:8]:
        body = z.read(n).decode("utf-8", "ignore")
        rows = []
        for row in re.findall(r"<row[^>]*>(.*?)</row>", body, re.S)[:400]:
            cells = []
            for m in re.finditer(r"<c\b([^>]*)>(.*?)</c>", row, re.S):
                attrs, inner = m.group(1), m.group(2)
                mt = re.search(r't="(\w+)"', attrs)
                ctype = mt.group(1) if mt else ""
                v = re.search(r"<v>(.*?)</v>", inner, re.S)
                val = v.group(1) if v else ""
                if ctype == "s" and val.isdigit() and int(val) < len(shared):
                    val = shared[int(val)]
                else:
                    t = re.search(r"<t[^>]*>(.*?)</t>", inner, re.S)
                    if t and not val:
                        val = t.group(1)
                val = _TAG_RE.sub("", str(val)).strip()
                if val:
                    cells.append(val)
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            out.append(f"[{os.path.basename(n)}]\n" + "\n".join(rows))
    return "\n\n".join(out)


def _pdf_text(data: bytes) -> str:
    try:
        import pypdf  # type: ignore
    except Exception:
        try:
            import PyPDF2 as pypdf  # type: ignore
        except Exception:
            return ""
    try:
        r = pypdf.PdfReader(io.BytesIO(data))
        pages = []
        for i, p in enumerate(r.pages[:40], 1):
            try:
                t = (p.extract_text() or "").strip()
            except Exception:
                t = ""
            if t:
                pages.append(f"[第{i}页]\n{t}")
        return "\n\n".join(pages)
    except Exception:
        return ""


def _plain_text(data: bytes) -> str:
    if data[:3] == b"\xef\xbb\xbf":
        try:
            return data[3:].decode("utf-8")
        except Exception:
            pass
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except Exception:
            pass
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return _plain_text(b"")


def extract_text(data: bytes, name: str = "", max_chars: int = MAX_CHARS) -> str:
    """把文件字节抽成文本；读不出来返回空串（不抛异常）。"""
    if not data:
        return ""
    ext = ext_of(name)
    text = ""
    try:
        if ext in OFFICE_EXTS:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if ext.startswith(".ppt"):
                    text = _pptx_text(z)
                elif ext.startswith(".doc"):
                    text = _docx_text(z)
                elif ext.startswith(".xls"):
                    text = _xlsx_text(z)
        elif ext in PDF_EXTS:
            text = _pdf_text(data)
        elif ext in TEXT_EXTS or not ext:
            text = _plain_text(data)
    except Exception:
        text = ""
    text = (text or "").strip()
    if len(text) > max_chars:
        text = text[:max_chars] + " …（后面还有，略）"
    return text


def supported(name: str) -> bool:
    return ext_of(name) in (TEXT_EXTS | OFFICE_EXTS | PDF_EXTS)
