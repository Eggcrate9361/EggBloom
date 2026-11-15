# -*- coding: utf-8 -*-
"""工具按需加载的分组判定（2026-10-23 加）。

为什么单独立文件：
  这段逻辑决定「模型这一轮手上有没有某个工具」，而它的失败方式**不是报错**，
  是某个同义说法没被词表覆盖 → 工具整组缺席 → 模型如实回答「我手上没有发文件的
  工具」。这种失败在日志里看不出来，只能靠用例锁住。

实测踩过的坑（也是用例的由来）：
  · 私聊「…zip 发出来」→ 词表里没有「发出来」，desktop 组缺席，答「没这功能」
  · 改口「发给我」→ 命中词表，立刻能发。同一件事两种措辞，一次成一次不成
  · 反过来，如果补「发」单字，会命中「发现」「发布」，白烧 token

运行：python -m unittest tests.test_tool_groups -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from harness import bot_hook  # noqa: E402
from harness import tool_registry as tr  # noqa: E402


def _mk_schema(name):
    return {"type": "function", "function": {
        "name": name, "description": "d",
        "parameters": {"type": "object", "properties": {}, "required": []}}}


class _Reg(unittest.TestCase):
    """注册一个假的 send_user_file 到 desktop 组，用来看它出不出现。

    不用真工具名做断言目标是有意的：判据是「desktop 组在不在」，
    而不是「某个具体工具在不在」—— 组才是开关。
    """

    def setUp(self):
        tr.reset()
        tr.register("send_user_file", _mk_schema("send_user_file"),
                    groups=(tr.GROUP_DESKTOP,))
        tr.register("take_screenshot", _mk_schema("take_screenshot"),
                    groups=(tr.GROUP_DESKTOP,))
        tr.register("search_web", _mk_schema("search_web"),
                    groups=(tr.GROUP_CORE,))

    def _has_desktop(self, msg, is_group=False):
        return "send_user_file" in bot_hook.tool_names_for(msg, is_group=is_group)


class TestFileTransferSynonyms(_Reg):
    """「把东西给我」的各种说法都要能打开 desktop 组。"""

    def test_synonyms_hit(self):
        for msg in ("那个 zip 发出来",
                    "把包发过来",
                    "结果发一下",
                    "发我看看",
                    "传给我",
                    "传过来",
                    "这玩意儿传一下",
                    "给我看下那个文件",
                    "给我发一份",
                    "发份报告",
                    "发个文件过来",
                    "把日志发这个给我",
                    "拖出来给我",
                    "挪出来",
                    "导出来看看",
                    "导出成 csv",
                    "打包给我",
                    "整个压缩包",
                    "发我 xx.zip",
                    "要 .exe"):
            with self.subTest(msg=msg):
                self.assertTrue(self._has_desktop(msg),
                                f"这句要能发文件，但 desktop 组没进来: {msg}")


class TestNoFalsePositive(_Reg):
    """词表不能宽到把无关对话也拖进 desktop 组 —— 那是白烧 token。"""

    def test_plain_chat_does_not_load_desktop(self):
        for msg in ("你好呀",
                    "今天好累",
                    "刚发现这个番不错",
                    "新版本发布了",
                    "这游戏真好玩"):
            with self.subTest(msg=msg):
                self.assertFalse(self._has_desktop(msg),
                                 f"闲聊不该加载本机工具: {msg}")

    def test_group_never_gets_desktop(self):
        """群里不给本机能力，这条边界与措辞无关。"""
        for msg in ("那个 zip 发出来", "帮我截个图", "E:\\a\\b.zip 发一下"):
            with self.subTest(msg=msg):
                self.assertFalse(self._has_desktop(msg, is_group=True))


class TestPathLike(_Reg):
    """路径形态是硬信号，不依赖措辞表补全。"""

    def test_windows_path(self):
        self.assertTrue(bot_hook._mentions_local_path(r"看下 E:\EggBloom\dist\a.zip"))

    def test_unix_path(self):
        self.assertTrue(bot_hook._mentions_local_path("看下 /home/user/report.pdf"))

    def test_bare_filename(self):
        self.assertTrue(bot_hook._mentions_local_path("报告.docx 里写了什么"))

    def test_path_alone_opens_desktop(self):
        """没有「发」「给」这类动词，只给路径，也要能读到文件。"""
        self.assertTrue(self._has_desktop(r"E:\EggBloom\dist\EggBloom-Full.zip"))

    def test_not_path(self):
        # 「1.5.8」这种版本号不是扩展名；「比如」也不是路径
        for msg in ("1.5.8 更新了什么", "比如这样", "3.5 小时"):
            with self.subTest(msg=msg):
                self.assertFalse(bot_hook._mentions_local_path(msg))

    def test_empty_and_none(self):
        self.assertFalse(bot_hook._mentions_local_path(""))
        self.assertFalse(bot_hook._mentions_local_path(None))


class TestDesktopOff(_Reg):
    def test_enable_desktop_false(self):
        """调用方显式关掉本机能力时，词表命中也不给。"""
        names = bot_hook.tool_names_for("那个 zip 发出来", is_group=False,
                                       enable_desktop=False)
        self.assertNotIn("send_user_file", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)