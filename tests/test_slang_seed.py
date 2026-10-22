# -*- coding: utf-8 -*-
'''tests/test_slang_seed.py —— 黑话基座的质量闸门。

回归的是**一类会静默伤害对话的问题**：词典里混进会误触发的词。
注入侧 slang_injector 用纯子串匹配（`if term in user_msg`），没有分词，
所以「一般」「版本」「真实」这类日常词会命中「一般来说」「这个版本的软件」
「挺真实的」，然后把这些 MOBA/二次元释义注入给模型 —— 比不注入更糟，
因为模型会真的按那段释义去回应一句普通的话。

本测试分三层：
  A. seed 文件本身的结构与卫生（重复、长度、单字、禁用字符）
  B. 噪声压测：**普通话**不许命中任何词条
  C. 命中覆盖：**黑话句子**必须命中（防止"为了降噪把词全删了"）

不依赖 pytest，跑法：python tests/test_slang_seed.py
'''
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, 'scripts')):
    if p not in sys.path:
        sys.path.insert(0, p)

import slang_seed  # noqa: E402
import build_slang_seed as B  # noqa: E402

fails = []


def check(name, cond, detail=''):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f'  {detail}' if detail else ''))
    if not cond:
        fails.append(name)


# ══ A. seed 结构卫生 ═══════════════════════════════════════════════
print('=== A. seed 结构与数据卫生 ===')

entries, replies, problems = B.validate()
check('seed 结构校验零问题', not problems,
      '；'.join(problems[:3]) if problems else '')
check('词条总量足够（>= 400）', len(entries) >= 400, f'{len(entries)} 条')
check('圈层种类 >= 10', len(slang_seed.CIRCLES) >= 10,
      str(list(slang_seed.CIRCLES)))

# 单字词必误伤 —— 这是硬红线，不允许任何例外
one_char = [t for t in entries if len(t) == 1]
check('没有单字词条（子串匹配必误伤）', not one_char, str(one_char))

# 释义必须单行、不能带论文腔
multiline = [t for t, m in entries.items() if '\n' in m or '\r' in m]
check('释义都是单行', not multiline, str(multiline[:5]))
verbose = [t for t, m in entries.items() if '文化影响' in m]
check('没有「文化影响：」这种论文腔', not verbose, str(verbose[:5]))

# 释义长度：注入是逐条拼进提示词，太长会挤掉上下文
over = [(t, len(m)) for t, m in entries.items() if len(m) > B.MAX_MEANING_LEN]
check(f'释义都不超过 {B.MAX_MEANING_LEN} 字', not over, str(over[:5]))
avg = sum(len(m) for m in entries.values()) / max(1, len(entries))
check('释义平均长度 < 25 字（原运行版是 80+）', avg < 25, f'实际 {avg:.1f}')

# 接话模板
check('过半词条带接话模板',
      len(replies) >= len(entries) * 0.9,
      f'{len(replies)}/{len(entries)}')
long_rl = [(t, r) for t, rs in replies.items() for r in rs
           if len(r) > B.MAX_REPLY_LEN]
check(f'接话都不超过 {B.MAX_REPLY_LEN} 字', not long_rl, str(long_rl[:3]))

# 重复词（validate 已查，这里再确认没有被静默覆盖）
raw_terms = []
for rows in slang_seed.CIRCLES.values():
    raw_terms.extend(str(r[0]).strip() for r in rows)
check('原始词表无重复', len(raw_terms) == len(set(raw_terms)),
      f'{len(raw_terms)} 项 → {len(set(raw_terms))} 唯一')

# ══ B. 噪声压测：普通话不许命中 ═══════════════════════════════════
print('\n=== B. 噪声压测（普通话不该命中任何词条）===')
check('探针句数量充足（>= 10）', len(B.NOISE_PROBES) >= 10,
      f'{len(B.NOISE_PROBES)} 句')

noise_bad = []
for msg, allow in B.NOISE_PROBES:
    hit = [t for t in entries if t in msg and t not in allow]
    if hit:
        noise_bad.append((msg, hit))
        print(f'    ✗ {msg!r} → {hit}')
check('所有普通话零误命中', not noise_bad,
      f'{len(noise_bad)} 句误报' if noise_bad else '')

# 退休词不得回潮（有人手滑把「一般」加回 seed，这条会拦下）
back = [t for t in B.RETIRED if t in entries]
check('已退休词没有被加回 seed', not back, str(back))

# 剔除名单也不该出现在 seed 里
in_seed = [t for t in B.DROP_TOO_SHORT if t in entries]
check('单字剔除名单没混进 seed', not in_seed, str(in_seed))

# ══ C. 命中覆盖：黑话句子必须命中 ═════════════════════════════════
print('\n=== C. 命中覆盖（黑话句子必须有反应）===')
MUST_HIT = [
    ('这新番真好看啊', '新番'),
    ('你是不是急了', '急了'),
    ('他这波操作太离谱了', '离谱'),
    ('今天上班好累 想摸鱼', '摸鱼'),
    ('这活整得挺好 我笑死', '笑死'),
    ('开黑吗 差一个人', '开黑'),
    ('yyds 这也能赢', 'yyds'),
    ('我直接破防了', '破防'),
    ('这也太抽象了', '抽象'),
    ('躺平了 不卷了', '躺平'),
]
miss = []
for msg, need in MUST_HIT:
    hit = [t for t in entries if t in msg]
    if need not in hit:
        miss.append((msg, need, hit))
check('10 个典型黑话句都能命中预期词', not miss, str(miss[:3]))

# ══ D. 同义形态去重（子串匹配的固有重复） ═════════════════════════
print('\n=== D. 同义形态去重（短词是长词的子串，不能重复注入）===')
import slang_injector as SI  # noqa: E402

# 构造一个自洽的小词典，避免依赖线上文件
FAKE = {
    '开摆了': '游戏｜放弃挣扎了',
    '开摆': '游戏｜放弃挣扎',
    '摆了': '评价｜不干了',
    '送人头': '游戏｜给对方送击杀',
    '新番': '二次元｜本季新动画',
    '追番': '二次元｜跟着更新看',
}
hits = SI._collect_hits('卧槽他又开摆了 直接送人头', FAKE)
terms = [t for t, _ in hits]
check('长词优先收下', terms and terms[0] == '开摆了', str(terms))
check('被包含的短词被跳过（开摆/摆了）',
      '开摆' not in terms and '摆了' not in terms, str(terms))
check('不同义的词各留一条（送人头）', '送人头' in terms, str(terms))
check('去重后条数正确（2 条）', len(terms) == 2, str(terms))

# 互不为子串的必须都留 —— 不能为了去重把不同概念也合掉
hits2 = SI._collect_hits('这季新番不错 我天天追番', FAKE)
terms2 = [t for t, _ in hits2]
check('互不为子串的词都保留（新番 + 追番）',
      set(terms2) == {'新番', '追番'}, str(terms2))

# 同一句话不能把同一个意思讲三遍
ctx = SI.get_context_for_llm('卧槽他又开摆了 直接送人头')
check('注入文案不含重复的「放弃挣扎」（< 2 次）',
      ctx.count('放弃挣扎') < 2, f'出现 {ctx.count(chr(25918)+chr(24323)+chr(25379)+chr(25166))} 次' if ctx else '')

# 每条词条的释义都该带圈层前缀，方便模型判断语域
no_prefix = [t for t, m in entries.items() if '｜' not in m]
check('释义都带「圈层｜」前缀', not no_prefix, str(no_prefix[:5]))

# ══ 汇总 ══════════════════════════════════════════════════════════
print('\n' + '=' * 58)
if fails:
    print(f'失败 {len(fails)} 项：')
    for f in fails:
        print('  - ' + f)
    sys.exit(1)
print(f'全部通过：{len(entries)} 条词条，{len(replies)} 条接话，'
      f'{len(B.NOISE_PROBES)} 句噪声零误报')