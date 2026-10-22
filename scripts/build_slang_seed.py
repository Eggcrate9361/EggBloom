# -*- coding: utf-8 -*-
'''
scripts/build_slang_seed.py — 把 slang_seed.py 编译成运行时真源。

为什么要有这一步（而不是直接写 slang_dict.json）：
    slang_dict.json / slang_replies.json 被 .gitignore 排除（Lite 包不带词典）。
    直接手改它们，改的是「仓库里不存在的文件」—— 没法 review、没法回滚、
    换台机器就重来。所以人工数据在 scripts/slang_seed.py，本脚本负责：
        校验 → 剔除 → 合并 → 原子写入 → 打印差异

三个去向（--app 决定写哪）：
    1. 不带 --app：写源码库根（D:\\git\\EggBloom\\slang_dict.json）
       —— **仅用于开发自测**，这个文件不进 git
    2. --app "<resources\\app>"：写运行副本（E:\\EggBloom水星\\...\\resources\\app）
       —— 让正在跑的 Desktop v1.5.5 立刻吃到新词典

★ 本脚本**默认是加法**，不会删掉用户自己学来的词：
    现有词条里凡是 seed 没有、也不在剔除名单里的，一律原样保留。
    只有 seed 明确覆盖的（同名）才更新释义；剔除名单命中的才删。
    想只保留 seed（清空自学习成果）要显式加 --replace。

用法：
    python scripts/build_slang_seed.py --dry-run
    python scripts/build_slang_seed.py
    python scripts/build_slang_seed.py --app "E:\\EggBloom水星\\EggBloom Desktop-new\\resources\\app"
    python scripts/build_slang_seed.py --app "..." --dry-run      # 先看差异
    python scripts/build_slang_seed.py --replace                  # 危险：丢弃 seed 之外的词
'''
import argparse
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import slang_seed  # noqa: E402

# ══════════════════════════════════════════════════════════════════
# 剔除名单：曾经在运行版 slang_dict.json 里，但**不该留**的词。
#
# 分三类，判据不同，所以分开放：
#   DROP_TOO_SHORT  —— 单字/超短，子串匹配必然误伤（"乐"命中"音乐"）
#   DROP_GENERIC    —— 模型自带知识，收录只占位不增信息（"有道理"）
#   DROP_AMBIGUOUS  —— 在别的领域也高频，注入会答非所问（"基地"）
#
# 注意：这些词**不删**自 slang_dict.json 之外的东西 —— 只影响本次编译写入。
# ══════════════════════════════════════════════════════════════════
DROP_TOO_SHORT = {
    '乐': '单字，子串命中"音乐""快乐""娱乐"',
    '润': '单字，子串命中"利润""湿润""滋润"',
    '孝': '单字，子串命中"孝顺""孝子"（后者已单独收录）',
    '赢': '单字，子串命中"赢家""输赢"',
    '摆': '单字，子串命中"摆放""摆摊"',
}

DROP_GENERIC = {
    '有道理': '模型本来就懂，且没有特殊语感',
    '学到了': '同上；作为回复它本身不需要解释',
    '长知识': '同上，书面语，群里几乎不用',
    '涨姿势': '同上，已是旧梗',
    '开眼界': '同上，书面语',
    '真的假的': '模型会用；且四字太通用，命中率高但信息量低',
    '运营': '通用词，见 DROP_AMBIGUOUS 说明（原表里重复出现，此处兜底）',
    '意识': '通用词，见 DROP_AMBIGUOUS 说明',
    'base': '通用英文，不该进黑话库',
    'AI': '模型知道自己是什么；收进词典反而诱导它谈自己',
    '模型': '通用技术词，无黑话语感',
    '提示词': '同上',
    '上线': '通用词',
    '重构': '通用技术词',
    '发版': '通用词',
    '会议': '通用词',
    '需求': '通用词',
    '排期': '通用词',
}

DROP_AMBIGUOUS = {
    '基地': '游戏基地与日常基地/基地组织重叠，误触发后注入 MOBA 释义会答非所问',
    'timing': '游戏时机与通用时间安排重叠',
    '团战': '基本只在游戏语境，但团战也常被拿来比喻群聊混战，语义已泛化到不需要解释',
}

# ══════════════════════════════════════════════════════════════════
# 已退休词：曾在 seed 里（或曾进过运行版），后被改成复合形态或直接删。
#
# 为什么要单独一张表，而不是靠「seed 里没有就等于删掉」：
#   合并默认是**加法**（保住用户自学习的词），所以 seed 里删掉一个词
#   并不会让线上那份跟着消失 —— 旧词会一直躺在 slang_dict.json 里误报。
#   这张表就是显式的墓碑：写在这里等于「无论线上有没有，都清掉」。
#
# 判据统一是**已实测会误触发**（见 NOISE_PROBES），不是主观不喜欢。
# ══════════════════════════════════════════════════════════════════
RETIRED = {
    '一般': '命中「一般来说」「一般情况下」等日常副词；已改为「就一般」',
    '版本': '命中「这个版本的软件」「版本号」；已改为「版本答案」',
    '输出': '命中「输出日志」「电源输出」；已改为「输出拉满」',
    '真实': '命中「挺真实的」「真实情况」；已改为「太真实了」',
    '稿': '单字，无固定黑话语感',
}


def _is_dropped(term):
    '''该词是否属于要清掉的那一类。'''
    return (term in DROP_TOO_SHORT or term in DROP_GENERIC
            or term in DROP_AMBIGUOUS or term in RETIRED)


# 释义长度上限。注入时是逐个拼进提示词，太长会挤掉别的上下文。
MAX_MEANING_LEN = 60
# 接话模板长度上限。一句群里的话超过这个长度就不像"接话"了。
MAX_REPLY_LEN = 24

# 释义里不该出现的字符
BANNED_IN_MEANING = ['\n', '\r', '\t', '｜｜', '"""']


# ══════════════════════════════════════════════════════════════════
# 噪声探针：用**真实群聊里会出现的普通句子**压测子串匹配。
#
# 为什么需要这道检查（2026-10-22 加）：
#   注入侧 slang_injector.get_context_for_llm 用的是纯子串匹配
#   （`if term in user_msg`），没有分词、没有边界。所以一个词哪怕
#   在黑话语境里完全正确，只要它同时是日常词的一部分，就会在
#   「一般来说」「这个版本的软件」「输出一下」这些句子里被误触发，
#   然后把 MOBA/二次元释义注入给模型 —— 比不注入更糟。
#
# 判据：下面每句都是**不带任何黑话**的正常话。任何一句命中词条，
#       就说明该词条会误报。
# 注意：allow 里放的是「命中也不算错」的词 —— 那句话本身就是黑话。
# ══════════════════════════════════════════════════════════════════
NOISE_PROBES = [
    ('今天上课老师说这题一般来说有三种解法', set()),
    ('这个版本的软件好像有点问题', set()),
    ('这段代码要重构一下，顺便输出一份日志', set()),
    ('他这个人挺真实的，不装', set()),
    ('我在基地待着呢，晚上不回来', set()),
    ('帮我看看这个报错，我实在搞不定了', set()),
    ('有没有人去食堂，顺便带个饭', set()),
    ('明天早上八点的课，起不来啊', set()),
    ('这个项目的需求又改了，烦死了', set()),
    ('数学作业抄一下，求你了', set()),
    ('这天气热得受不了', set()),
    ('把这段文本输出成 utf8', set()),
    ('寄一份材料到教务处', set()),
    ('老师点名的时候帮我答到', {'点名'}),
    ('我室友昨天通宵打游戏', {'室友', '通宵'}),
]


def _norm(s):
    return str(s or '').strip()


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f'  ! 读取失败（将拒绝写回，避免清空）{path}: {e}')
        return None


def _save_json(path, data):
    '''原子写：临时文件 + fsync + os.replace。'''
    import tempfile
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.slangseed_', suffix='.tmp', dir=d)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as e:  # noqa: BLE001
        print(f'  ! 写入失败 {path}: {e}')
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def validate():
    '''校验 seed 的**结构性**问题。返回 (entries, replies, problems)。'''
    entries = {}
    replies = {}
    problems = []
    seen_in = {}

    for circle, rows in slang_seed.CIRCLES.items():
        for row in rows:
            if not isinstance(row, tuple) or len(row) != 3:
                problems.append(f'[{circle}] 形状错误（应为 3 元组）: {row!r}')
                continue
            term, meaning, rl = row
            term = _norm(term)
            meaning = _norm(meaning)

            if not term:
                problems.append(f'[{circle}] 词为空')
                continue
            if not meaning:
                problems.append(f'[{circle}] {term}：释义为空')
                continue

            # 重复词（跨圈层）
            if term in seen_in:
                problems.append(
                    f'{term}：重复收录（{seen_in[term]} 与 {circle}）')
                continue
            seen_in[term] = circle

            # 禁用字符
            for bad in BANNED_IN_MEANING:
                if bad in meaning:
                    problems.append(f'{term}：释义含禁用字符 {bad!r}')
                    meaning = meaning.replace(bad, ' ')

            # 长度
            if len(meaning) > MAX_MEANING_LEN:
                problems.append(
                    f'{term}：释义 {len(meaning)} 字 > {MAX_MEANING_LEN}（注入会挤占上下文）')

            # 单字风险
            if len(term) == 1:
                problems.append(
                    f'{term}：单词条，子串匹配必误伤。建议改成两字以上（如"太乐了"）')

            entries[term] = meaning

            # 接话：过滤 + 截断，不因为个别脏数据放弃整条词
            clean_rl = []
            if isinstance(rl, (list, tuple)):
                for r in rl:
                    r = _norm(r)
                    if not r:
                        continue
                    # 接话里不能含分隔符 —— 注入层用 ' / '.join 拼，含 / 会看着像两条
                    if '/' in r or '|' in r:
                        problems.append(f'{term}：接话含分隔符 / 或 |：{r!r}')
                        r = r.replace('/', ' ').replace('|', ' ')
                    if len(r) > MAX_REPLY_LEN:
                        problems.append(
                            f'{term}：接话 {len(r)} 字 > {MAX_REPLY_LEN}：{r!r}')
                        r = r[:MAX_REPLY_LEN]
                    clean_rl.append(r)
            elif rl:
                problems.append(f'{term}：接话不是列表: {rl!r}')

            if clean_rl:
                replies[term] = clean_rl[:3]   # 注入层最多用 3 条

    return entries, replies, problems


def apply_drops(entries):
    '''按剔除名单清掉不该收录的。返回 (剩下来的, 剔掉的 [(词, 理由)])。'''
    dropped = []
    for table, kind in ((DROP_TOO_SHORT, '太短'),
                        (DROP_GENERIC, '太通用'),
                        (DROP_AMBIGUOUS, '有歧义'),
                        (RETIRED, '已退休')):
        for term, why in table.items():
            if term in entries:
                entries.pop(term)
                dropped.append((term, f'{kind}：{why}'))
    return entries, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default='', help='resources\\app 路径；省略则写源码库根')
    ap.add_argument('--dry-run', action='store_true', help='只报差异，不写文件')
    ap.add_argument('--replace', action='store_true',
                    help='危险：丢弃 seed 之外的所有现存词（含自学习成果）')
    ap.add_argument('--no-replies', action='store_true',
                    help='只写释义，不碰 slang_replies.json')
    a = ap.parse_args()

    app = os.path.abspath(a.app) if a.app else ROOT
    if a.app and not os.path.isdir(app):
        print(f'错误：{app} 不是目录')
        return 2

    dict_path = os.path.join(app, 'slang_dict.json')
    repl_path = os.path.join(app, 'slang_replies.json')
    print(f'目标应用根 : {app}')
    print(f'词典       : {dict_path}')
    print(f'接话       : {repl_path}')

    # ── 1. 校验 seed ────────────────────────────────────────────
    seed_entries, seed_replies, problems = validate()
    print(f'\n[1] seed 校验')
    print(f'    圈层 {len(slang_seed.CIRCLES)} 个，词条 {len(seed_entries)} 条，'
          f'带接话 {len(seed_replies)} 条')
    if problems:
        print(f'    ⚠ {len(problems)} 个问题：')
        for p in problems[:40]:
            print(f'      · {p}')
        if len(problems) > 40:
            print(f'      … 还有 {len(problems) - 40} 个')
    else:
        print('    ✓ 无问题')

    # ── 2. 剔除名单 ────────────────────────────────────────────
    before = len(seed_entries)
    seed_entries, dropped = apply_drops(seed_entries)
    print(f'\n[2] 剔除名单')
    if dropped:
        print(f'    剔除 {len(dropped)} 条（seed 里本有，按判据不收）：')
        for t, why in dropped:
            print(f'      - {t}（{why}）')
        # 被剔掉的接话也要跟着走
        for t, _ in dropped:
            seed_replies.pop(t, None)
    else:
        print('    seed 里没有需要剔除的词')
    print(f'    词条 {before} → {len(seed_entries)}')

    # ── 3. 读现存（读失败必须放弃） ─────────────────────────────
    print(f'\n[3] 现存数据')
    cur_dict = _load_json(dict_path, {'entries': {}})
    if cur_dict is None:
        print('    ✗ 现存词典读不出来，为免清空，本次放弃写入')
        return 1
    cur_entries = cur_dict.get('entries') if isinstance(cur_dict, dict) else None
    if not isinstance(cur_entries, dict):
        print('    ✗ 现存词典结构不对（entries 不是 dict），放弃写入')
        return 1
    print(f'    现存词条 {len(cur_entries)} 条')

    cur_replies = {}
    if not a.no_replies:
        cur_rj = _load_json(repl_path, {'entries': {}})
        if cur_rj is None:
            print('    ✗ 现存接话读不出来，本次只写词典、不动接话')
            a.no_replies = True
        else:
            cur_replies = (cur_rj.get('entries')
                           if isinstance(cur_rj, dict) else {})
            if not isinstance(cur_replies, dict):
                cur_replies = {}
        print(f'    现存接话 {len(cur_replies)} 条')

    # ── 4. 合并 ────────────────────────────────────────────────
    print(f'\n[4] 合并')
    merged = {} if a.replace else dict(cur_entries)

    added, updated, kept = [], [], []
    for t, m in seed_entries.items():
        if t in merged:
            if merged[t] != m:
                updated.append(t)
            else:
                kept.append(t)
        else:
            added.append(t)
        merged[t] = m

    # 现存里被剔除名单/退休名单命中的，也要清掉（用户自建的垃圾同样清）
    purged = []
    for t in list(merged):
        if _is_dropped(t):
            merged.pop(t)
            purged.append(t)

    print(f'    新增 {len(added)}｜更新释义 {len(updated)}｜原样保留 {len(kept)}'
          f'｜清掉脏条 {len(purged)}')
    print(f'    最终 {len(cur_entries)} → {len(merged)} 条')

    merged_replies = {} if a.no_replies else dict(cur_replies)
    if not a.no_replies:
        r_added = [t for t in seed_replies if t not in merged_replies]
        r_updated = [t for t, v in seed_replies.items()
                     if t in merged_replies and merged_replies[t] != v]
        merged_replies.update(seed_replies)
        # 词被剔了，接话也不该留
        for t in purged:
            merged_replies.pop(t, None)
        print(f'    接话 新增 {len(r_added)}｜更新 {len(r_updated)}'
              f'｜最终 {len(merged_replies)} 条')

    # ── 5. 噪声压测：普通句子不该命中 ──────────────────────────
    print(f'\n[5] 噪声压测（子串匹配的真实误报率）')
    noisy = []
    for msg, allow in NOISE_PROBES:
        hits = [t for t in merged if t and t in msg and t not in allow]
        if hits:
            noisy.append((msg, hits))
            print(f'    ✗ {msg!r} → 误命中 {hits}')
    if not noisy:
        print(f'    ✓ {len(NOISE_PROBES)} 句普通话全部无误命中')
    else:
        print(f'    共 {len(noisy)} 句出现误命中 —— 这些词建议改成复合形态或加进剔除名单')

    # ── 6. 命中抽样：黑话句子该命中 ────────────────────────────
    print(f'\n[6] 命中抽样（黑话句子应命中）')
    sample_hit = ['这新番真好看啊', '你是不是急了', '他这波操作太离谱了',
                  '今天上班好累 想摸鱼', '这活整得挺好 我笑死']
    for msg in sample_hit:
        hits = [t for t in merged if t and t in msg]
        print(f'    {msg!r} → {hits if hits else "（无）"}')

    if a.dry_run:
        print('\n(DRY RUN) 未写入任何文件')
        if added:
            print(f'\n将新增（前 30）：{added[:30]}')
        if updated:
            print(f'\n将更新（前 30）：{updated[:30]}')
        return 0

    # ── 6. 备份 + 写入 ─────────────────────────────────────────
    tag = time.strftime('%Y%m%d-%H%M%S')
    for p in (dict_path, repl_path):
        if os.path.exists(p):
            bak = f'{p}.bak-{tag}'
            try:
                shutil.copy2(p, bak)
                print(f'\n已备份 {os.path.basename(bak)}')
            except Exception as e:  # noqa: BLE001
                print(f'  ! 备份失败 {p}: {e}')
                return 1

    if not _save_json(dict_path, {'entries': merged}):
        return 1
    print(f'✔ 写入 {dict_path}（{len(merged)} 条）')

    if not a.no_replies:
        if not _save_json(repl_path, {'entries': merged_replies}):
            return 1
        print(f'✔ 写入 {repl_path}（{len(merged_replies)} 条）')

    print('\n完成。')
    return 0


if __name__ == '__main__':
    sys.exit(main())