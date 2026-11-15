# -*- coding: utf-8 -*-
"""docextract：办公文档抽文本的最小用例（只用标准库造样张）。"""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import docextract as d

OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if extra and not cond else ""))


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, body in files.items():
            z.writestr(n, body)
    return buf.getvalue()


def test_pptx():
    blob = make_zip({
        "ppt/slides/slide2.xml": "<p:sld><a:p><a:r><a:t>第二页</a:t></a:r></a:p></p:sld>",
        "ppt/slides/slide1.xml": "<p:sld><a:p><a:r><a:t>第一页</a:t></a:r></a:p></p:sld>",
        "ppt/notesSlides/notesSlide1.xml": "<p:sld><a:p><a:r><a:t>这是备注</a:t></a:r></a:p></p:sld>",
    })
    out = d.extract_text(blob, "x.pptx")
    check("pptx 按页排序", out.index("[第1页]") < out.index("[第2页]"), out)
    check("pptx 正文", "第一页" in out and "第二页" in out, out)
    check("pptx 读备注", "这是备注" in out, out)


def test_docx():
    blob = make_zip({
        "word/document.xml":
            "<w:document><w:p><w:r><w:t>甲方：小a</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>乙方：315</w:t></w:r></w:p></w:document>",
    })
    out = d.extract_text(blob, "合同.docx")
    check("docx 段落不粘连", "甲方：小a" in out and "乙方：315" in out, out)


def test_xlsx():
    shared = ("<sst><si><t>姓名</t></si><si><t>小a</t></si></sst>")
    sheet = ('<worksheet><sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
             "</sheetData></worksheet>")
    blob = make_zip({"xl/sharedStrings.xml": shared,
                     "xl/worksheets/sheet1.xml": sheet})
    out = d.extract_text(blob, "表.xlsx")
    check("xlsx 共串展开", "姓名 | 小a" in out, out)


def test_plain_and_limits():
    check("txt 直读", d.extract_text("你好 hello".encode("utf-8"), "a.txt") == "你好 hello")
    check("gbk 回落", "中文" in d.extract_text("中文".encode("gbk"), "a.log"))
    long_text = ("字" * 9000).encode("utf-8")
    out = d.extract_text(long_text, "big.txt")
    check("截断", len(out) < 4100 and out.endswith("略）"), str(len(out)))
    check("空字节", d.extract_text(b"", "a.pptx") == "")
    check("坏 zip 不抛", d.extract_text(b"not a zip", "a.pptx") == "")
    check("不支持格式", d.supported("a.mp4") is False and d.supported("a.pdf") is True)


def main():
    print("== docextract ==")
    for fn in (test_pptx, test_docx, test_xlsx, test_plain_and_limits):
        fn()
    print(f"\n通过 {len(OK)} / 失败 {len(FAIL)}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
