# -*- coding: utf-8 -*-
"""身份号判据的验收（2026-11-13）。

11-13 那天把 11 处真实号推上公开链，事后复盘发现**两条闸一条都没命中**：
`check_secrets.py` 的号码规则只认 11 位手机号和「配置键 + 数字」（还只在 config
类文件里查），`commit_lint.py` 的 CM-03 只读提交信息、不看文件内容。
判据写在两个地方、口径还不一样，等于没有判据。

所以这个文件验的不是「规则表里写了什么」，而是四件能证伪的事：

1. 判据自己正反都能判（`identity_numbers.selftest` 的断言在这里再跑一遍）；
2. **活的工作树 0 命中** —— 不是跑一次看一眼，是每次改完都判；
3. 拦名单与本机真实配置对得上（换号后忘了 `--add`，这里会红）；
4. `check_secrets.py` 真的在调这份判据，而不是自己又长了一条号码规则。

第 4 条是防复发的关键：11-13 之前的形态就是「每个脚本各有一套号码规则」。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

try:
    import identity_numbers as idn
except ImportError as exc:  # 判据文件被删/改名时要明说，不要静默跳过
    print(f"  [FAIL] 无法导入 tools/identity_numbers.py：{exc}")
    sys.exit(1)
try:
    import check_secrets as cs
except ImportError as exc:
    print(f"  [FAIL] 无法导入 scripts/check_secrets.py：{exc}")
    sys.exit(1)

_fail = []

#: 夹具一律用编出来的号。这个文件本身进库，写真号等于把要拦的东西写进拦它的仓库。
FAKE_NEW = "2468135791"       # 不在拦名单、也不属于任何合成段
FAKE_BLOCKED = "9999990123"   # 只在本文件里当作「已拦」的号


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def main():
    # ── [1] 判据自检 ───────────────────────────────────────────
    print("[1] 判据正反例（identity_numbers.selftest）")
    rc = idn.selftest()
    check("selftest 全过", rc == 0, f"退出码 {rc}")

    check("身份位新号被判", bool(idn.scan_line(f'"target_qqs": ["{FAKE_NEW}"]', {})))
    check("拦名单里的号在注释里也被判",
          bool(idn.scan_line(f"# 群里 {FAKE_BLOCKED} 那次", {idn.digest(FAKE_BLOCKED): "夹具"})))
    # 初版判据漏了这一种：真号被拼在 15 位数字串中间，独立匹配看不见。
    check("被拼进长数字串的号也抓（滑窗）",
          bool(idn.scan_line(f"x = 77{FAKE_BLOCKED}77",
                             {idn.digest(FAKE_BLOCKED): "夹具"})))
    check("合成段放行", not idn.scan_line('"user_id": "1000000001"', {}))
    check("非身份位的裸号不报", not idn.scan_line('{"ts": 1793426150}', {}))

    # ── [2] 活的工作树必须 0 命中 ──────────────────────────────
    #  这一节的产出是「现在没有泄漏」，不是「判据写得好看」。
    print("\n[2] 活的工作树（0 命中才算过）")
    rc = idn._scan_repo()
    check("git 文件清单可枚举", rc != 2, f"退出码 {rc}")
    check("工作树 0 命中", rc == 0, f"退出码 {rc}，逐条见上方输出")

    # ── [3] 拦名单 vs 本机真实配置 ─────────────────────────────
    #  换 bot 号 / 加群之后忘了 --add，这条会红。没配置文件（CI）时跳过。
    print("\n[3] 拦名单与本机配置一致")
    local = idn.local_identities()
    if not local:
        print("  [SKIP] 没有本机配置（CI / 新克隆），只有哈希清单这一层")
    else:
        bl = idn.load_blocklist()
        miss = [(n, t) for n, t in local if idn.digest(n) not in bl]
        check(f"本机 {len(local)} 个身份号都在拦名单里", not miss,
              [f"{t}={idn.mask(n)}" for n, t in miss])
    check("拦名单非空（空文件等于闸没装）", len(idn.load_blocklist()) >= 4,
          len(idn.load_blocklist()))
    check("拦名单每行都写了说明",
          all(v != "（未写说明）" for v in idn.load_blocklist().values()))

    # ── [4] check_secrets 真的在调这份判据 ─────────────────────
    print("\n[4] check_secrets 端到端（写入临时文件 → 必须命中）")
    tmp = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    tmp.write(f'cfg = {{"target_qqs": ["{FAKE_NEW}"], "bot_qq": "1000000002"}}\n')
    tmp.close()
    orig = idn.load_blocklist
    try:
        hits = cs.scan([tmp.name], False)
        kinds = {h[2] for h in hits}
        check("身份位夹具被 check_secrets 报出来", bool(hits), hits)
        check("归类正确（status 是身份位）",
              any("身份位" in k for k in kinds), kinds)

        # 把 FAKE_BLOCKED 临时塞进拦名单，验第一层也真的接上了
        idn.load_blocklist = lambda path=idn.BLOCKLIST_FILE: {idn.digest(FAKE_BLOCKED): "夹具"}
        with open(tmp.name, "w", encoding="utf-8") as fh:
            fh.write(f'note = "群里 {FAKE_BLOCKED} 那次"\n')
        hits2 = cs.scan([tmp.name], False)
        check("拦名单命中也被 check_secrets 报出来",
              any("拦名单" in h[2] for h in hits2), hits2)
    finally:
        idn.load_blocklist = orig
        os.unlink(tmp.name)

    # 假值不该被报：合成号写进同一个位置，必须一声不响
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as t2:
        t2.write('cfg = {"target_qqs": ["1000000001"], "bot_qq": "1000000002"}\n'
                 'gid = "900000005"\n')
    try:
        check("同一个位置上的合成号不误报", not cs.scan([t2.name], False),
              cs.scan([t2.name], False))
    finally:
        os.unlink(t2.name)

    # ── [5] 号码规则只有一份 ───────────────────────────────────
    #  11-13 之前的形态是「每个脚本各有一套号码正则」。这条挡住回退。
    print("\n[5] 号码规则不许散回各处")
    import re as _re
    bad = [(name, pat.pattern) for name, pat in cs.PATTERNS
           if _re.search(r"(?i)qq|群号|group_id|uin", name + " " + pat.pattern)]
    check("check_secrets.PATTERNS 里没有号码规则（只剩手机号键名那一条）", not bad, bad)
    check("check_secrets 引用了同一份判据",
          getattr(cs, "idn", None) is idn)
    check("判据里没有明文号段表（拦名单只存哈希）",
          all(len(k) == 16 for k in idn.load_blocklist()))

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
