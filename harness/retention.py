# -*- coding: utf-8 -*-
"""harness/retention.py —— 有界保留原语（任务 5.2 上半）。

对齐 docs/plan/dsh-harness-design.md §7.3 与 §11.1 P5：

  ItemRetainer : 保留前 max_items 个**有序逻辑单元**。调用方**持续送入每一个已
                 观察到的单元**（包括超上限之后的），因此 `omitted` 是**精确计数**，
                 不是「大约丢了 20 条」。
  TextRetainer : **按字节** 保留 head / tail / headTail。内存里至多持有
                 head_bytes + tail_bytes + 一个分片 → 大流不会无界累积。
                 `finish()` **在每个切割处修剪不完整的码点**（中文 UTF-8 三字节，
                 切坏会出现 U+FFFD 替换字符）—— 返回的文本绝不会带替换字符。

── 硬纪律（务必守住）────────────────────────────────────────────────
`truncated` 是**预算事实**：因为上限而省略了**本可获得**的内容。
它**绝不**表示「上游没找到 / 没权限 / 读不了」。
权限失败、跳过二进制、提供方部分失败、不可读候选项 —— 都留在**调用方的领域字段**里，
绝不并入 `truncated`。EggBloom 里即：「因为太长被截断」与「因为没搜到/没权限」
必须是两个不同字段，否则模型会误判。
"""
import codecs


# ── 精确省略计数用的哨兵（对外不可见）──────────────────────────────
class _Omitted:
    __slots__ = ("count",)

    def __init__(self):
        self.count = 0


def format_retention_notice(truncated, omitted=None, *, noun="items", resume_hint=""):
    """标准化省略子句（对应 DSH 的 formatRetentionNotice）。

    只负责**省略这件事本身**的标准措辞；**恢复指引由调用方提供**
    ——「只有工具自己知道恢复动作」（比如 read with offset/limit、grep 该路径）。

    返回 '' 表示没有省略（此时调用方不应追加任何提示）。
    """
    if not truncated:
        return ""
    if omitted is not None and omitted > 0:
        clause = f"Omitted {omitted} {noun}."
    else:
        clause = f"Omitted some {noun}."
    if resume_hint:
        return f"{clause} {resume_hint}"
    return clause


class ItemRetainer:
    """保留前 max_items 个有序逻辑单元，并给出**精确**省略计数。

    用法（关键：即使超过上限也要继续 push，计数器才不会漏）::

        r = ItemRetainer(max_items=50)
        for entry in candidates:
            r.push(entry)          # 继续 drain 以得到精确计数
        items, truncated, omitted = r.finish()

    设计约束：**只支持 head**。tail / headTail / 分页 / 分组语义仍归调用方所有
    （对齐 DSH：「项保留只支持 head」）。
    """

    __slots__ = ("_max", "_items", "_omitted", "_finished")

    def __init__(self, max_items):
        if max_items is None or max_items < 0:
            raise ValueError("max_items 必须是非负整数")
        self._max = int(max_items)
        self._items = []
        self._omitted = 0
        self._finished = False

    def push(self, item):
        """送入一个**已观察到的**逻辑单元。超上限的也要送（只为精确计数）。"""
        if self._finished:
            raise RuntimeError("retainer 已 finish，不能继续 push")
        if len(self._items) < self._max:
            self._items.append(item)
        else:
            self._omitted += 1

    def extend(self, items):
        """批量送入，等价于逐个 push（保持精确计数）。"""
        for it in items:
            self.push(it)
        return self

    def finish(self):
        """返回 (items, truncated, omitted)。

        - items     : 保留下来的前 max_items 个（按送入顺序）
        - truncated : **预算事实** —— 是否因上限省略了本可获得的内容
        - omitted   : 精确省略计数
        """
        self._finished = True
        return list(self._items), self._omitted > 0, self._omitted

    @property
    def omitted(self):
        """此刻的精确省略计数（finish 前也可读）。"""
        return self._omitted

    def __len__(self):
        return len(self._items)


def _trim_incomplete_codepoints(head_bytes, tail_bytes, enc):
    """在每个切割处修剪不完整码点，保证与原文拼接后不产生替换字符。

    返回 (head_text, tail_text)：
      - head 从**开头**解码，丢弃末尾可能被切断的**不完整**字节序列（保留 U+FFFD 之下的字节）。
      - tail 从**结尾**解码，丢弃开头可能被切断的不完整字节序列。
    做法：用增量解码器逐段喂入，末段用 final=False 并回退到最后一个完整边界。
    """
    def _decode_prefix(b):
        # 从前往后解，只保留能完整解码的前缀；不完整尾巴丢弃
        dec = codecs.getincrementaldecoder(enc)(errors="strict")
        out = []
        try:
            out.append(dec.decode(b, final=False))
        except UnicodeDecodeError:
            # 极端：整体不合法，退化为逐字节安全解
            return b.decode(enc, errors="replace")
        return "".join(out)

    def _decode_suffix(b):
        # tail 场景：b 的**开头**可能落在某个多字节字符中间（是续字节 0b10xxxxxx）。
        # 正确做法：从开头逐个丢弃「续字节」，直到遇到一个合法的字符起始字节，
        # 再尝试严格解码；若仍失败则继续前移，全部失败则返回空串（丢掉半截字）。
        i = 0
        n = len(b)
        while i < n:
            byte = b[i]
            # 合法 UTF-8 起始字节：0xxxxxxx / 110xxxxx / 1110xxxx / 11110xxx
            if byte < 0x80 or byte >= 0xC0:
                try:
                    return b[i:].decode(enc, errors="strict")
                except UnicodeDecodeError:
                    i += 1
                    continue
            # 续字节 → 跳过
            i += 1
        return ""  # 全是续字节（整个 tail 都是半截字）→ 宁可丢空，绝不产生替换字符

    head = _decode_prefix(head_bytes) if head_bytes else ""
    tail = _decode_suffix(tail_bytes) if tail_bytes else ""
    return head, tail


class TextRetainer:
    """**按字节**保留文本的 head / tail / headTail，内存有界。

    - kind: 'head' | 'tail' | 'headTail'
    - head_bytes / tail_bytes: 各自的字节预算
    - 内存中至多持有 head_bytes + tail_bytes + 一个分片（送入的分片会被立即裁剪）
    - finish() 在每个切割处修剪不完整码点（中文关键）

    注意：`kind` 按**字节**而非字符或行计数 —— 子进程管道与 HTTP 正文都是字节流。
    """

    __slots__ = ("_kind", "_hb", "_tb", "_head", "_tail", "_head_size",
                 "_tail_size", "_total_bytes", "_finished", "_enc")

    def __init__(self, kind="head", head_bytes=0, tail_bytes=0, encoding="utf-8"):
        if kind not in ("head", "tail", "headTail"):
            raise ValueError("kind 必须是 head / tail / headTail")
        self._kind = kind
        self._hb = int(head_bytes or 0)
        self._tb = int(tail_bytes or 0)
        self._enc = encoding
        self._head = bytearray()
        self._tail = bytearray()
        self._total_bytes = 0
        self._finished = False

    def push(self, chunk):
        """送入一段 bytes（或 str，自动按 encoding 编码）。"""
        if self._finished:
            raise RuntimeError("retainer 已 finish，不能继续 push")
        if isinstance(chunk, str):
            chunk = chunk.encode(self._enc, errors="replace")
        if not chunk:
            return
        self._total_bytes += len(chunk)

        if self._kind in ("head", "headTail") and len(self._head) < self._hb:
            need = self._hb - len(self._head)
            self._head += chunk[:need]

        if self._kind in ("tail", "headTail") and self._tb > 0:
            self._tail += chunk
            # 只保留最后 tail_bytes 字节 → 内存有界
            if len(self._tail) > self._tb:
                del self._tail[: len(self._tail) - self._tb]

    def extend(self, chunks):
        for c in chunks:
            self.push(c)
        return self

    def finish(self):
        """返回 (text, truncated, omitted_bytes)。

        - text          : 修剪完整码点后的 head / tail / headTail 拼接结果
        - truncated     : **预算事实**（是否因上限省略了字节）
        - omitted_bytes : 精确省略的字节数
        """
        self._finished = True
        budget = 0
        if self._kind in ("head", "headTail"):
            budget += self._hb
        if self._kind in ("tail", "headTail"):
            budget += self._tb

        head_bytes = bytes(self._head) if self._kind in ("head", "headTail") else b""
        tail_bytes = bytes(self._tail) if self._kind in ("tail", "headTail") else b""

        # 省略计数：总字节 - 实际保留（避免 head/tail 在短输入时重复计数）
        kept = len(head_bytes) + len(tail_bytes)
        if self._kind == "headTail":
            # head 与 tail 在短输入下可能重叠 → 实际保留不超过 total
            kept = min(kept, self._total_bytes)
        else:
            kept = min(kept, self._total_bytes)
        omitted = max(0, self._total_bytes - kept)
        truncated = omitted > 0

        head_text, tail_text = _trim_incomplete_codepoints(head_bytes, tail_bytes, self._enc)

        if self._kind == "head":
            text = head_text
        elif self._kind == "tail":
            text = tail_text
        else:
            if head_text and tail_text:
                text = head_text + "\n…\n" + tail_text
            else:
                text = head_text or tail_text
        return text, truncated, omitted

    @property
    def total_bytes(self):
        return self._total_bytes


# 便捷函数：一次性对内存中的大字符串做 headTail 保留（不落盘时用）
def retain_text_inline(text, head_bytes, tail_bytes, encoding="utf-8"):
    """对已有字符串做 headTail 保留，返回 (text, truncated, omitted_bytes)。"""
    r = TextRetainer("headTail", head_bytes, tail_bytes, encoding=encoding)
    r.push(text)
    return r.finish()
