# -*- coding: utf-8 -*-
"""发行包出厂闸（2026-11-15）。

为什么有它
----------
在此之前，三道闸全都只守到**源码树**为止：

    工作树   check_secrets.py        ✅
    历史     （没人管）              ❌  ← 地址在 23 个提交里躺了两天
    发行包   （没人管）              ❌  ← 三档 zip 里各带一份带地址的文档

`pack_all.py` 只验 zip 能不能解开、顶层目录对不对、关键文件在不在 —— 它验的是
「包坏了没」，不是「包里有没有不该有的东西」。于是：

* 2026-10-18：旧版 `webui_artistic_v3.html` 带着真实 QQ 号进了包；
* 2026-11-15：`docs/RELEASE_CHECKLIST.md` 带着 ssh 地址进了三档包，
  `docs/规范表.md`（.gitignore 排除的身份索引）也跟着 `docs/` 整目录进了暂存树。

两次都是**包本身**的问题，两次都没有闸。这个脚本补的就是这一段。

判据（前三条硬失败，后两条默认只警告）
======================================
1. **内容**：包内每个文本条目过一遍 `check_secrets.scan_text()` —— 与工作树、历史
   **同一套判据**。这一点是有意的：包扫描如果自己长一套规则，迟早和树扫描漂移，
   而「换个入口就换套判据」正是历史漏检的成因。
2. **结构**：不得含 `.git/`、`data/`、`chat_logs/`、`logs/`、`bot_config.json*`、
   `.env`、`SEPARATION.lock`、`*.pyc`、`*.log` —— 这些是运行态或本地配置。
3. **溯源**：`VERSION` 里的 `source_commit` 必须等于当前 HEAD。不等说明包是旧树打的，
   包内代码与公开源码对不上，出问题没法回溯（`--allow-commit` 用于复验历史包）。
4. **来源**（warn，`--strict` 时 fail）：包内出现「源码库没有、也不在生成物白名单」
   的路径。这一条专抓 `docs/规范表.md` 那类 —— 仓库故意不收、robocopy 却整目录搬。
5. **文件名**（warn，`--strict` 时 fail）：路径含 6 位以上连续数字。`make_meme_sample.py`
   早有同样一条（`BAD_NUM = re.compile(r"\\d{6,}")`，躲开 `zhenxun_rl_<群号>_*.gif`），
   但那是采样期过滤，包里的存量素材没人拦。

用法
====
    python scripts/check_package.py                      # 验 dist/ 下全部 zip
    python scripts/check_package.py --zip D:\\x\\a.zip ...  # 指定文件
    python scripts/check_package.py --strict             # 4/5 也当失败（发布前用）
    python scripts/check_package.py --selftest           # 判据自检（造坏包，断言能抓）
    python scripts/check_package.py --list-rules         # 只打印判据

`pack_all.py` 在替换 `.zip.new` 之前会直接调用 `scan_zip()`：不合格的包**进不了
dist/**，会留在 `.zip.new` 供排查。这样「泄漏包出不了厂」不依赖任何人的记性。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import check_secrets as cs  # noqa: E402

#: 顶层目录 → 包内 app 根。Lite 是纯源码树（根就是 app），另两档在 resources/app 下。
APP_ROOT = {
    "EggBloom-Lite": "",
    "EggBloom-Standard": "resources/app/",
    "EggBloom-Full": "resources/app/",
}

#: 打包期生成、源码库里本来就没有的路径（不是泄漏，是产物）。按前缀放行。
GENERATED = (
    "VERSION",
    "licenses/",
    "memes/",                          # 样本图 / 全量图库 / tags.json / descriptions.json
    "plugins/ego/super_memory/models/",  # gguf / dll（体积原因不进 git）
)

DENY_DIRS = {".git", "data", "chat_logs", "logs", "__pycache__", ".venv", "venv"}
DENY_FILES = {"bot_config.json", "bot_config.json.bak", "bot_config.json.old",
              ".env", "SEPARATION.lock", "SEPARATION.pending", "constraints.archive"}
DENY_SUFFIX = (".pyc", ".log")

#: 路径名里的 6 位以上数字 = 疑似群号/QQ 号（与 make_meme_sample.BAD_NUM 同一条）
NAME_NUM = re.compile(r"\d{6,}")

#: 条目里只读这么多字节做内容判据；超过的当二进制，跳过（文本条目不会有这么大）
MAX_TEXT = 4 * 1024 * 1024

#: 会进包的源码目录 —— 这些自 source_commit 起有改动 = 包里的代码是旧的，硬失败
SOURCE_PREFIX = ("harness/", "webui/", "plugins/", "components/", "separation/",
                 "autonomy/", "skills/", "protocols/", "licenses/")
#: 不进包的工具链 —— 改了不影响包内容，不参与「落后」判定
TOOLING_PREFIX = ("scripts/", "tests/", "tools/", ".github/", ".githooks/", ".gitignore")


def _drift(commit):
    """自 commit 到 HEAD 的差异。返回 (是否祖先, [(状态, 路径)] 源码, [路径] 其余)。

    为什么不要求 `source_commit == HEAD`：那要求「打包之后一个字都不能再提交」，
    改个错别字就得重打三档包。实际风险也不是「包旧了」，而是**包里的代码/文档
    与它在仓库里声称的那次提交对不上** —— 所以按文件类别分开判：
      * 源码变了 → 包跑的是旧代码，硬失败（这种包发出去就是「说一套跑一套」）；
      * 只有工具链/文档变了 → 包内容仍然自洽，只提示「重打才完全一致」。

    状态位要留着（`A`/`M`/`D`）：**新增**的文件如果包里已经有同一份字节，
    说明是「仓库刚补上、包早就带着」，不算落后 —— 这一条由 `scan_zip` 复核。
    """
    anc = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"],
                         cwd=ROOT, capture_output=True).returncode == 0
    if not anc:
        return False, [], []
    changed = _git("diff", "--name-status", "%s..HEAD" % commit).decode(
        "utf-8", "replace").splitlines()
    src, other = [], []
    for line in changed:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        st, p = parts[0][:1], parts[-1]
        if p.startswith(TOOLING_PREFIX):
            continue
        if p.startswith(SOURCE_PREFIX) or ("/" not in p and p.endswith((".py", ".json", ".toml"))):
            src.append((st, p))
        else:
            other.append(p)
    return True, src, other


def _pkg_has_same(z, top, rel):
    """包里那份 `rel` 与 HEAD 是否逐字节一致（行尾归一后比）。"""
    prefix = APP_ROOT.get(top, "")
    arc = "%s/%s%s" % (top, prefix, rel)
    try:
        data = z.read(arc)
    except Exception:
        return False
    try:
        head = _git("show", "HEAD:%s" % rel)
    except SystemExit:
        return False
    norm = lambda b: b.replace(b"\r\n", b"\n")
    return norm(data) == norm(head)


def _git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        raise SystemExit("[check_package] git %s 失败: %r" % (" ".join(args), r.stderr[:200]))
    return r.stdout


_TRACKED = None


def tracked_files():
    """源码库跟踪的文件集合（相对仓库根，正斜杠）。结果缓存，一次打包要验三档。"""
    global _TRACKED
    if _TRACKED is None:
        out = _git("-c", "core.quotepath=false", "ls-files")
        _TRACKED = set(out.decode("utf-8", "replace").splitlines())
    return _TRACKED


def head_commit():
    return _git("rev-parse", "--short", "HEAD").decode().strip()


def _split(arcname):
    """条目名 → (顶层目录, app 相对路径)。不在 app 根下的返回 (top, None)。"""
    parts = arcname.split("/")
    top = parts[0] if parts else ""
    prefix = APP_ROOT.get(top)
    if prefix is None:
        return top, None
    rest = arcname[len(top) + 1:]
    if not rest.startswith(prefix):
        return top, None
    rel = rest[len(prefix):]
    return top, (rel or None)


def _is_denied_dir(rel):
    return any(seg in DENY_DIRS for seg in rel.split("/")[:-1])


def _generated(rel):
    return any(rel == g or rel.startswith(g) for g in GENERATED)


def scan_zip(path, commit=None, strict=False, check_commit=True):
    """验一个发行包。返回报告 dict；`ok=False` 表示不该出厂。"""
    rep = {"path": path, "entries": 0, "findings": [], "warns": [], "notes": [],
           "ok": True, "commit": "", "top": ""}
    commit = commit or head_commit()
    tracked = tracked_files()
    blocked = cs.idn.blocked_digests()
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        rep["findings"].append(("(包)", 0, "无法打开", repr(e)[:80]))
        rep["ok"] = False
        return rep

    with z:
        names = [i.filename for i in z.infolist()]
        rep["entries"] = sum(1 for i in z.infolist() if not i.is_dir())
        tops = sorted({n.split("/")[0] for n in names if n})
        rep["top"] = tops[0] if len(tops) == 1 else ",".join(tops)

        for info in z.infolist():
            if info.is_dir():
                continue
            arc = info.filename
            top, rel = _split(arc)
            where = rel or arc

            # ── 判据 2：结构黑名单（照 app 相对路径判，外壳文件不参与）──
            if rel:
                base = rel.rsplit("/", 1)[-1]
                # `.example` 是仓库里的模板（bot_config.json.example），本来就该随包发
                if ".example" in base or ".sample" in base:
                    pass
                elif (_is_denied_dir(rel) or base in DENY_FILES
                      or base.startswith("bot_config.json")):
                    rep["findings"].append((where, 0, "不该随包分发", base))
                    continue
                elif base.endswith(DENY_SUFFIX):
                    rep["findings"].append((where, 0, "运行时产物", base))
                    continue

            # ── 判据 3：溯源（Lite 的 VERSION 在包根，另两档在包根、app 在 resources/app）──
            if arc == "%s/VERSION" % top or rel == "VERSION":
                txt = z.read(arc).decode("utf-8", "replace")
                for line in txt.splitlines():
                    if line.startswith("source_commit="):
                        rep["commit"] = line.split("=", 1)[1].strip()
                continue

            # ── 判据 1 的边界：只扫 app 根里的内容 ──
            # 外壳与内嵌 Python 运行时（python/Lib/** 里有 `13.2.5.8` 这种版本号、
            # getpass.py 里有 `Password:` 提示串）是第三方产物，扫了全是假阳性，
            # 而且它们逐字节来自官方安装包，不是本项目产生的文本。
            if rel is None:
                continue

            # ── 判据 4：包内有、源码库没有（且不是生成物）──
            if rel not in tracked and not _generated(rel):
                rep["warns"].append((where, 0, "源码库没有这个文件", ""))

            # ── 判据 5：文件名里的长数字 ──
            # `memes/` 下的是第三方表情包库，文件名里的号是**上游素材自带的群号**
            # （`zhenxun_rl_<群号>_*.gif`），不是本项目泄漏的东西 —— 降为提示。
            # 别处的长数字仍按警告算：`make_meme_sample.py` 的 BAD_NUM 也是同一口径。
            if NAME_NUM.search(arc.rsplit("/", 1)[-1]):
                if rel.startswith("memes/"):
                    rep["notes"].append((where, 0, "素材文件名含 6 位以上数字（上游自带）", ""))
                else:
                    rep["warns"].append((where, 0, "文件名含 6 位以上数字", ""))

            # ── 判据 1：内容（与工作树/历史同一套判据）──
            if info.file_size > MAX_TEXT or arc.lower().endswith(cs.BIN_EXTS):
                continue
            try:
                raw = z.read(arc)
            except Exception:
                continue
            if b"\x00" in raw[:4000]:
                continue
            text = raw.decode("utf-8", "replace")
            # 判据里的路径用 app 相对路径：INFRA_ALLOW / JUDGE_FILES 都按仓库路径写
            for p, ln, name, val in cs.scan_text(where, text, blocked):
                rep["findings"].append((p, ln, name, val))

        if check_commit:
            if not rep["commit"]:
                rep["findings"].append(("VERSION", 0, "缺 VERSION", "无法确认包对应哪次提交"))
            else:
                anc, src, other = _drift(rep["commit"])
                # 新增文件若包里已有同一份字节，是「仓库刚补上、包早就带着」，不算落后
                late = [(st, p) for st, p in src
                        if not (st == "A" and _pkg_has_same(z, rep["top"], p))]
                if not anc:
                    rep["findings"].append(("VERSION", 0, "source_commit 不在当前历史里",
                                            rep["commit"]))
                elif late:
                    rep["findings"].append(("VERSION", 0, "包内代码落后于 HEAD",
                                            "%s 之后改了 %d 个源文件：%s"
                                            % (rep["commit"], len(late),
                                               [p for _, p in late][:4])))
                if other:
                    # 只做提示，不升级成警告：文档比 HEAD 旧不是缺陷，重打一次就齐；
                    # 让它去卡 --strict，等于训练人忽略 --strict。
                    rep["notes"].append(("VERSION", 0, "包内文档/资源比 HEAD 旧",
                                         "%d 个文件，重打才完全一致" % len(other)))

    if strict and rep["warns"]:
        rep["ok"] = False
    if rep["findings"]:
        rep["ok"] = False
    return rep


def fmt(rep, limit=12):
    lines = []
    head = "%s（%d 条目，顶层 %s，commit %s）" % (
        os.path.basename(rep["path"]), rep["entries"], rep["top"] or "?", rep["commit"] or "无")
    lines.append(head)
    if rep["findings"]:
        lines.append("  ✗ 硬失败 %d 处：" % len(rep["findings"]))
        for p, ln, name, val in rep["findings"][:limit]:
            lines.append("      %s:%s  [%s]  %s" % (p, ln, name, val))
        if len(rep["findings"]) > limit:
            lines.append("      …… 还有 %d 处" % (len(rep["findings"]) - limit))
    if rep["notes"]:
        lines.append("  · 提示 %d 条：" % len(rep["notes"]))
        for p, ln, name, val in rep["notes"][:4]:
            lines.append("      [%s] %s %s" % (name, p, val))
    if rep["warns"]:
        lines.append("  ! 警告 %d 处（--strict 时算失败）：" % len(rep["warns"]))
        seen = set()
        for p, ln, name, val in rep["warns"]:
            if name in seen:
                continue
            seen.add(name)
            lines.append("      [%s] %s:%s" % (name, p, ln))
        if len(seen) < len({w[2] for w in rep["warns"]}):
            lines.append("      ……")
    if not rep["findings"] and not rep["warns"] and not rep["notes"]:
        lines.append("  ✓ 干净")
    return "\n".join(lines)


# ── 判据自检：造几个坏包，断言能抓到 ──────────────────────────────────────
def _selftest():
    """不依赖 dist/：临时造包，注入已知坏形态，断言判据抓得住。

    这是「判据是活的」的证明。上一轮 infra 判据加完没自检，结果它自己的夹具
    就踩了 UA 版本号 —— 判据也会有 bug，得拿它去跑已知答案。
    """
    cases = []
    tmp = tempfile.mkdtemp(prefix="pkgchk-")
    try:
        def mk(name, entries):
            p = os.path.join(tmp, name)
            with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
                for arc, data in entries.items():
                    z.writestr(arc, data)
            return p

        ver_ok = "release=1.6.0\nsource_commit=%s\n" % head_commit()

        # 1) 干净包：应 ok
        p = mk("good.zip", {
            "EggBloom-Lite/VERSION": ver_ok,
            "EggBloom-Lite/bot_core.py": "x = 1\n",
        })
        cases.append(("干净包", p, False, True, {"findings": 0}))

        # 2) 文档里带真实形态的地址（文档段 IP，命中 ssh:// 与 user@IP）
        p = mk("addr.zip", {
            "EggBloom-Lite/VERSION": ver_ok,
            "EggBloom-Lite/docs/x.md": "裸仓库在 ssh://ubuntu@192.0.2.10/home/ubuntu/x.git\n",
        })
        cases.append(("文档带地址", p, False, False, {"names": {"SSH 地址", "user@IP"}}))

        # 3) 运行态文件混进包
        p = mk("runtime.zip", {
            "EggBloom-Lite/VERSION": ver_ok,
            "EggBloom-Lite/bot_config.json": "{}",
            "EggBloom-Lite/logs/a.log": "x",
        })
        # 两个条目都撞「目录/文件名黑名单」，先命中的那条就 continue 了，
        # 所以这里只断言「抓到了」，不细分名字
        cases.append(("运行态文件", p, False, False, {"names": {"不该随包分发"}}))

        # 4) 提交号对不上
        p = mk("stale.zip", {
            "EggBloom-Lite/VERSION": "release=1.6.0\nsource_commit=0000000\n",
        })
        cases.append(("source_commit 不可达", p, False, False,
                      {"names": {"source_commit 不在当前历史里"}}))

        # 5) 警告项：源码库没有的文件 + 文件名带群号（strict 才失败）
        p = mk("warn.zip", {
            "EggBloom-Lite/VERSION": ver_ok,
            "EggBloom-Lite/notes-local.md": "本地笔记\n",
            "EggBloom-Lite/memes/zhenxun_rl_22625868_002.gif": "GIF89a",
        })
        # 未跟踪的本地文档 = 警告；memes/ 下的群号文件名 = 提示（上游素材自带）
        cases.append(("本地文档+素材群号名", p, False, True,
                      {"warns_min": 1, "notes_min": 1}))
        cases.append(("同上但 --strict", p, True, False, {"warns_min": 1}))

        bad = 0
        for label, path, strict, want_ok, want in cases:
            rep = scan_zip(path, strict=strict)
            names = {f[2] for f in rep["findings"]}
            wnames = {w[2] for w in rep["warns"]}
            nnames = {n[2] for n in rep["notes"]}
            ok = rep["ok"] == want_ok
            if "names" in want:
                ok = ok and want["names"] <= names
            if "findings" in want:
                ok = ok and len(rep["findings"]) == want["findings"]
            if "warns_min" in want:
                ok = ok and len(wnames) >= want["warns_min"]
            if "notes_min" in want:
                ok = ok and len(nnames) >= want["notes_min"]
            print("  [%s] %-18s ok=%-5s 命中 %s %s" % (
                "OK" if ok else "FAIL", label, rep["ok"],
                sorted(names | wnames), "" if ok else "← 期望不符"))
            bad += 0 if ok else 1
        print("[check_package --selftest] %d/%d 通过" % (len(cases) - bad, len(cases)))
        return 0 if not bad else 1
    finally:
        for f in os.listdir(tmp):
            try:
                os.unlink(os.path.join(tmp, f))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="发行包出厂闸")
    ap.add_argument("--zip", action="append", default=None, help="要验的 zip（可重复）")
    ap.add_argument("--dist", default="", help="验这个目录下所有 zip")
    ap.add_argument("--strict", action="store_true", help="来源/文件名警告也算失败")
    ap.add_argument("--allow-commit", action="store_true", help="不校验 source_commit == HEAD")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list-rules", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()
    if a.list_rules:
        print(__doc__)
        return 0

    targets = list(a.zip or [])
    if a.dist:
        targets += [os.path.join(a.dist, f) for f in sorted(os.listdir(a.dist))
                    if f.lower().endswith(".zip")]
    if not targets:
        print("[check_package] 没有指定包（--zip / --dist）")
        return 2

    bad = 0
    for p in targets:
        rep = scan_zip(p, strict=a.strict, check_commit=not a.allow_commit)
        print(fmt(rep))
        bad += 0 if rep["ok"] else 1
    print("\n[check_package] %d 个包，%d 个不合格" % (len(targets), bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
