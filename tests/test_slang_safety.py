# -*- coding: utf-8 -*-
"""tests/test_slang_safety.py —— 黑话词典的**写安全**（防数据归零）。

回归的是一个真实事故（2026-10-03 深夜发现）：
  ``slang_injector._load()`` 把"读失败"和"文件不存在"都压成 ``{"entries": {}}``，
  紧接着 ``remove()`` 拿着这个空 dict 落盘 → 105 条真实词一次性归零。
  触发条件很常见：运行中的 bot 占着文件 / 半截写入 / 编码异常。

本测试用**临时目录**验证，不碰真源：
  1. 读失败时 remove/inject 必须放弃写回，文件原样不动。
  2. 文件不存在时允许空起步（这是正常首次使用）。
  3. 写入必须是原子的（不留半截文件、不丢旧条目）。
  4. 形状损坏（entries 不是 dict）也要被当成"不可信"。

运行：python tests/test_slang_safety.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import slang_injector as SI  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")


SAMPLES = {"崩了": "局势突变", "开摆了": "放弃挣扎", "降维打击": "实力碾压"}


def _write(path, obj, raw=None):
    with io.open(path, "w", encoding="utf-8") as f:
        if raw is not None:
            f.write(raw)
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)


def _read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class _Sandbox:
    """把 SI 的两个真源路径临时改到 tmp 目录，用完复原。"""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="slang_safety_")
        self._old = (SI.SLANG_DICT_PATH, SI.SLANG_REPLIES_PATH)
        SI.SLANG_DICT_PATH = os.path.join(self.tmp, "slang_dict.json")
        SI.SLANG_REPLIES_PATH = os.path.join(self.tmp, "slang_replies.json")
        return self.tmp

    def __exit__(self, *a):
        SI.SLANG_DICT_PATH, SI.SLANG_REPLIES_PATH = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False


def test_read_failure_must_not_clobber():
    """文件存在但读不动 → remove/inject 必须放弃，内容原样保留。

    注意 ``set_entries`` 是**显式全量覆盖**，它的契约就是"用你给的东西替换掉"，
    所以它不在保护范围内（真源要重置时本来就得靠它）。这里只检验
    "读改写"三兄弟：remove / inject / inject_reply。
    """
    with _Sandbox():
        _write(SI.SLANG_DICT_PATH, {"entries": dict(SAMPLES)})
        _write(SI.SLANG_REPLIES_PATH, {"entries": {"崩了": ["旧回复"]}})

        import builtins
        real_open = builtins.open
        state = {"fail": True}

        def _is_target(p):
            ap = os.path.abspath(str(p))
            return ap in (os.path.abspath(SI.SLANG_DICT_PATH),
                          os.path.abspath(SI.SLANG_REPLIES_PATH))

        def fake_open(p, mode="r", *a, **k):
            # 只拦"读"，不拦写：模拟文件被占用（能写不能读的典型现场）
            if state["fail"] and "r" in mode and "+" not in mode and _is_target(p):
                raise OSError("模拟文件被占用")
            return real_open(p, mode, *a, **k)

        builtins.open = fake_open
        try:
            r1 = SI.remove("崩了")
            r2 = SI.inject("新词", "不该写进去")
            r3 = SI.inject_reply("崩了", ["不该写"])
        finally:
            builtins.open = real_open
            state["fail"] = False

        check("安全: remove 读失败时返回 False", r1 is False, r1)
        check("安全: inject 读失败时返回 False", r2 is False, r2)
        check("安全: inject_reply 读失败时返回 False", r3 is False, r3)

        # 关键断言：三兄弟一个都没动文件
        after = _read(SI.SLANG_DICT_PATH)
        check("安全: 读失败后词典条目数不变",
              set(after.get("entries", {})) == set(SAMPLES),
              list(after.get("entries", {})))
        rep = _read(SI.SLANG_REPLIES_PATH)
        check("安全: 读失败后回复模板不变",
              rep.get("entries") == {"崩了": ["旧回复"]}, rep)


def test_missing_file_is_empty_ok():
    """文件不存在 = 首次使用，空起步是允许的（这是唯一合法的空）。"""
    with _Sandbox():
        check("安全: 不存在时 get_entries 为空", SI.get_entries() == {}, SI.get_entries())
        ok = SI.inject("首词", "首次写入")
        check("安全: 不存在时 inject 成功", ok is True, ok)
        check("安全: 首词已落地", SI.get_entries().get("首词") == "首次写入", SI.get_entries())


def test_corrupt_shape_is_untrusted():
    """entries 不是 dict（被写成 list / 缺包装层）→ 视为不可信，拒绝写回。"""
    with _Sandbox():
        _write(SI.SLANG_DICT_PATH, None, raw='{"entries": ["坏", "形状"]}')
        check("安全: 形状损坏时 get_entries 返回空", SI.get_entries() == {}, SI.get_entries())
        r = SI.remove("任意")
        check("安全: 形状损坏时 remove 放弃", r is False, r)
        raw = io.open(SI.SLANG_DICT_PATH, encoding="utf-8").read()
        check("安全: 形状损坏时文件未被覆盖", raw.strip().startswith('{"entries"'), raw)

    with _Sandbox():
        _write(SI.SLANG_DICT_PATH, None, raw='[1,2,3]')
        check("安全: 顶层不是 dict 时返回空", SI.get_entries() == {}, SI.get_entries())
        check("安全: 顶层不是 dict 时 remove 放弃", SI.remove("x") is False)


def test_atomic_write_no_partial():
    """写入必须是原子的：内容完整可解析，且临时文件不残留。"""
    with _Sandbox():
        _write(SI.SLANG_DICT_PATH, {"entries": dict(SAMPLES)})
        SI.set_entries({"a": "1", "b": "2"})
        got = _read(SI.SLANG_DICT_PATH)
        check("安全: 写后可解析", got.get("entries") == {"a": "1", "b": "2"}, got)
        leftovers = [f for f in os.listdir(os.path.dirname(SI.SLANG_DICT_PATH))
                     if f.startswith(".slang_") and f.endswith(".tmp")]
        check("安全: 无临时文件残留", not leftovers, leftovers)


def test_write_preserves_others():
    """单条增删不得影响其余条目。"""
    with _Sandbox():
        _write(SI.SLANG_DICT_PATH, {"entries": dict(SAMPLES)})
        SI.inject("新词", "释义")
        e = SI.get_entries()
        check("安全: 新增后旧词全在", all(w in e for w in SAMPLES), list(e.keys()))
        check("安全: 新增词存在", e.get("新词") == "释义", e.get("新词"))
        SI.remove("新词")
        e2 = SI.get_entries()
        check("安全: 删除后旧词仍全在", set(SAMPLES) <= set(e2.keys()), list(e2.keys()))

        # 删不存在的词 = 幂等成功，不改文件
        raw1 = io.open(SI.SLANG_DICT_PATH, encoding="utf-8").read()
        r = SI.remove("根本不存在的词")
        raw2 = io.open(SI.SLANG_DICT_PATH, encoding="utf-8").read()
        check("安全: 删不存在词返回 True（幂等）", r is True, r)
        check("安全: 删不存在词不改文件", raw1 == raw2)


def test_real_dict_intact():
    """真源（源码库根）此刻必须是完好且有内容的。"""
    p = os.path.join(ROOT, "slang_dict.json")
    if not os.path.exists(p):
        check("安全: 真源文件存在", False, p)
        return
    with io.open(p, "r", encoding="utf-8") as f:
        d = json.load(f)
    n = len(d.get("entries", {}))
    check("安全: 真源可解析", isinstance(d, dict), type(d).__name__)
    check("安全: 真源非空（未被测试清空）", n > 0, f"{n} 条")


def main():
    for fn in (test_read_failure_must_not_clobber, test_missing_file_is_empty_ok,
               test_corrupt_shape_is_untrusted, test_atomic_write_no_partial,
               test_write_preserves_others, test_real_dict_intact):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            global FAIL
            FAIL += 1
            FAILURES.append(f"{fn.__name__} 抛异常: {e}\n{traceback.format_exc()}")
    print(f"\n{'='*60}")
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("-" * 60)
        for f in FAILURES:
            print("  ✗ " + f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
