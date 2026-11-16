# -*- coding: utf-8 -*-
"""对外可见面的验收（2026-11-15）。

为什么有它
----------
这个项目漏过三次，三次都不是「代码写错」，而是**某个面根本没人看**：

    2026-10-18  旧版 webui_artistic_v3.html 带真实 QQ 号进了 zip   （包没人看）
    2026-11-13  11 处身份号推上公开链                             （历史没人看）
    2026-11-15  同一段 ssh 地址在 23 个提交 + 三档 zip 里         （历史 + 包都没人看）

每次事后都能补一条判据，但补完还是靠人记得去跑。所以这个文件验的不是判据细节，
而是四件能证伪的事：

1. **包闸真的会拦**：造带地址 / 带运行态文件 / 提交号对不上的包，断言 `ok=False`；
2. **包闸不误伤**：干净的包必须过，`.example` 模板、生成物（memes/、VERSION）不算违规；
3. **历史闸真的会拦**：临时仓库里「工作树已删、历史还在」的地址必须被抓出来；
4. **三处共用同一套判据**：`check_package` / `check_history` 调的必须是
   `check_secrets.scan_text`，不许各自长一套 —— 那正是三次漏检的共同成因。

第 4 条是防复发的关键。判据搬家本身不危险，危险的是「新入口配了新判据」。
"""
import os
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

try:
    import check_secrets as cs
    import check_package as cp
    import check_history as ch
except ImportError as exc:  # 判据文件被删/改名要明说，不要静默跳过
    print(f"  [FAIL] 无法导入判据脚本：{exc}")
    sys.exit(1)

_fail = []


def check(label, ok, extra=""):
    print(f"  [{'OK' if ok else 'FAIL'}] {label}" + (f"  {extra}" if (extra and not ok) else ""))
    if not ok:
        _fail.append(label)


def _mkzip(d, name, entries):
    p = os.path.join(d, name)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, data in entries.items():
            z.writestr(arc, data)
    return p


def _head():
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True).stdout.decode().strip()


def main():
    tmp = tempfile.mkdtemp(prefix="surface-")
    ver = f"release=1.6.0\nsource_commit={_head()}\n"
    try:
        # ── [1] 包闸会拦 ───────────────────────────────────────────
        print("[1] 包闸：该拦的拦住")
        p = _mkzip(tmp, "addr.zip", {
            "EggBloom-Lite/VERSION": ver,
            "EggBloom-Lite/docs/x.md": "裸仓库在 ssh://ubuntu@192.0.2.10/home/ubuntu/x.git",
        })
        rep = cp.scan_zip(p)
        names = {f[2] for f in rep["findings"]}
        check("文档里的地址被抓到", not rep["ok"] and {"SSH 地址", "user@IP"} <= names, sorted(names))

        p = _mkzip(tmp, "runtime.zip", {
            "EggBloom-Lite/VERSION": ver,
            "EggBloom-Lite/bot_config.json": "{}",
        })
        rep = cp.scan_zip(p)
        check("运行态配置被抓到", not rep["ok"], rep["findings"])

        p = _mkzip(tmp, "stale.zip", {
            "EggBloom-Lite/VERSION": "release=1.6.0\nsource_commit=0000000\n",
        })
        rep = cp.scan_zip(p)
        check("source_commit 不可达被抓到",
              not rep["ok"] and any("source_commit" in f[2] for f in rep["findings"]))

        # ── [2] 包闸不误伤 ─────────────────────────────────────────
        print("\n[2] 包闸：不该拦的放过")
        p = _mkzip(tmp, "good.zip", {
            "EggBloom-Lite/VERSION": ver,
            "EggBloom-Lite/bot_core.py": "x = 1\n",
            "EggBloom-Lite/bot_config.json.example": "{}\n",
            "EggBloom-Lite/memes/memes/tags.json": '{"a": 1}\n',
        })
        rep = cp.scan_zip(p)
        check("干净包通过", rep["ok"], rep["findings"])
        check("`.example` 模板不算运行态配置",
              not any(".example" in f[0] for f in rep["findings"]))

        # 警告项：源码库没有的文件 —— 默认不拦，--strict 才拦
        p = _mkzip(tmp, "warn.zip", {
            "EggBloom-Lite/VERSION": ver,
            "EggBloom-Lite/notes-local.md": "本地笔记\n",
        })
        check("未跟踪文件默认只警告", cp.scan_zip(p)["ok"])
        check("未跟踪文件在 --strict 下被拦", not cp.scan_zip(p, strict=True)["ok"])
        check("生成物白名单不误报",
              not cp.scan_zip(_mkzip(tmp, "gen.zip", {
                  "EggBloom-Lite/VERSION": ver,
                  "EggBloom-Lite/memes/memes/tags.json": "{}\n"}), strict=True)["warns"])

        # ── [3] 历史闸会拦「工作树已删、历史还在」 ────────────────
        print("\n[3] 历史闸：工作树干净不等于历史干净")
        repo = os.path.join(tmp, "hist")
        os.makedirs(repo)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

        def g(*a):
            r = subprocess.run(["git"] + list(a), cwd=repo, capture_output=True, env=env)
            assert r.returncode == 0, (a, r.stderr)
            return r.stdout.decode("utf-8", "replace")

        g("init", "-q", "-b", "main")
        with open(os.path.join(repo, "notes.md"), "w", encoding="utf-8") as f:
            f.write("裸仓库在 ssh://ubuntu@192.0.2.10/home/ubuntu/x.git")
        g("add", "-A")
        g("commit", "-qm", "写入")
        with open(os.path.join(repo, "notes.md"), "w", encoding="utf-8") as f:
            f.write("地址不入库\n")
        g("add", "-A")
        g("commit", "-qm", "删除")
        hits, st = ch.scan_refs([("main", g("rev-parse", "main").strip())], cwd=repo)
        names = {h[3] for h in hits}
        check("旧提交里的地址被抓到", {"SSH 地址", "user@IP"} <= names, sorted(names))
        check("当前树那一版不再命中（只报历史）",
              sum(1 for h in hits if h[2] == 3) == 0, [(h[1], h[2]) for h in hits])
        # 只有 notes.md 两个版本 = 2 个 blob；错位的话这里会多算（tree 被当成 blob）
        check("blob 解析没被 tree 错位",
              st["scanned"] == 2 and {h[1] for h in hits} == {"notes.md"},
              (st, sorted({h[1] for h in hits})))

        # ── [4] 三处共用同一套判据 ─────────────────────────────────
        print("\n[4] 判据只有一份，三处都调它")
        check("check_package 用的是 check_secrets.scan_text",
              getattr(cp, "cs", None) is cs)
        check("check_history 用的是 check_secrets.scan_text",
              getattr(ch, "cs", None) is cs)
        import inspect
        for mod, fn in ((cp, "scan_zip"), (ch, "scan_refs")):
            src = inspect.getsource(getattr(mod, fn))
            check(f"{mod.__name__}.{fn} 里没有自己写正则判据",
                  "re.compile" not in src)

        print("\n" + "=" * 56)
        if _fail:
            print(f"失败 {len(_fail)}：{_fail}")
            return 1
        print("全部通过")
        return 0
    finally:
        for root, dirs, files in os.walk(tmp, topdown=False):
            for f in files:
                try:
                    os.unlink(os.path.join(root, f))
                except OSError:
                    pass
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError:
                    pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
