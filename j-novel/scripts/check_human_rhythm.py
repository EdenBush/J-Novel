#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人类写作节律检测（J-Novel 新增）
================================

为什么会有这个脚本：
    "读起来像不像人写的一"是主观判断，Agent 可以用"我觉得没问题"糊弄过去。
    数字不行。本脚本把"像不像人"变成"几个指标达没达标"，跟字数不够一样硬。

基线来源：齐佩甲《超神机械师》全书实测（约 540 万字，游戏异界系统流，男频快节奏）
    句首代词 4.0% / 句首引号 18.4% / 平均句长 36.6 字
    破折号 0.47 每千字 / 身体部位 0.32 每千字 / 明喻 1.27 每千字 / 情绪词 0.33 每千字

⚠ 校准说明（重要，改阈值前先读）：
    1. 阈值必须用**全书**基线，不能用 10 万字样本——样本切片给出的值不稳定。
       例：句长 CV 在 10 万样本上人类=1.00、AI=0.83（看似可分），
           全书上人类=0.76、AI=0.81（反向，不可分）→ CV 已降级为弱证据。
    2. 与篇幅相关的指标一律用**密度**（每千字），不能用绝对次数。
       例："深吸一口气"人类全书出现 121 次，AI 单部 18 次——
           换算成密度后人类 0.022/千字、AI 0.082/千字，方向才对。
    3. 人类基线是"游戏异界系统流"，换题材请用 --baseline 重新校准。

与 check_aistyle.py 的分工：
    check_aistyle.py      —— 词汇层AI指纹（转折词/模糊词/顿悟词/明喻/动作重复）
    check_human_rhythm.py —— 句法层人类节律（句子怎么组织、信息怎么打包、主语怎么用）
    两者都要跑，不互相替代。

用法:
    python check_human_rhythm.py <章节文件>
    python check_human_rhythm.py --all <项目目录>
    python check_human_rhythm.py <文件> --json          # 机器可读输出（写入质检档案用）
    python check_human_rhythm.py --baseline <人类样本文件>   # 用你自己的样本重算基线

退出码：0 = 全部达标；1 = 有硬指标未达标；2 = 无法分析
"""

import argparse
import io
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ---------------------------------------------------------------- 阈值
# 强度标记：HARD = 不过就是不合格；SOFT = 提示，不阻塞
# 方向：max = 越低越好；min = 越高越好；range = 在区间内最好（人味不是"越多越好"）
#
# 基线来源（三个人类作者，全书实测，取范围不是单一作者）：
#   齐佩甲《超神机械师》(系统流) / 三天两觉《惊悚乐园》(无限流吐槽) / 柳岸花又明《我真没想重生啊》(都市重生)
# 三样本覆盖了"男频快节奏 / 吐槽话痨 / 都市生活"三种风格，避免被单一作者带偏。
THRESHOLDS = {
    'pron_head':    dict(kind='max',   hard=15.0,  soft=25.0,  humans='1.5–10.4', human=4.0,
                         desc='句首代词占比 %', why='人类几乎不用"我/他/她"开句（三作者 1.5–10.4%，AI 25.8–47.3%）'),
    'quote_head':   dict(kind='min',   hard=15.0,  soft=12.0,  humans='18.4–30.6', human=18.4,
                         desc='句首引号占比 %', why='人类让对话先行（三作者 18.4–30.6%，AI 0–1.4%）'),
    'sent_mean':    dict(kind='min',   hard=18.0,  soft=22.0,  humans='22.7–36.6', human=30.0,
                         desc='平均句长(字)', why='弱判据：只拦最极端的碎句（惊悚乐园仅 22.7 字，AI-A 15 字）'),
    'dash':         dict(kind='max',   hard=1.5,   soft=2.5,   humans='0.05–0.97', human=0.47,
                         desc='破折号/千字', why='破折号是"句子写完了再补一刀"的痕迹（人类 ≤0.97，AI 3.8–5.0）'),
    'body':         dict(kind='max',   hard=1.0,   soft=1.6,   humans='0.31–0.55', human=0.32,
                         desc='身体部位/千字', why='SKILL 教"情绪身体化"后被顶格执行到人类的 7 倍'),
    'emotion':      dict(kind='range', lo=0.25,    hi=0.50,    humans='0.32–0.35', human=0.33,
                         desc='情绪词/千字', why='最稳定锚点：三作者惊人一致 0.32–0.35，AI 被禁到 0.07–0.17'),
    'simile':       dict(kind='range', lo=0.80,    hi=1.50,    humans='1.05–1.27', human=1.27,
                         desc='明喻/千字', why='比喻配额被顶格执行（人类 1.05–1.27，AI 2.0–3.0）'),
    'dialog':       dict(kind='max',   hard=40.0,  soft=35.0,  humans='20.4–33.5', human=20.4,
                         desc='对话占比 %', why='弱判据：人类 20.4–33.5 与 AI 29.5–38.4 有重叠，只做防堆对话护栏'),
    'act_density':  dict(kind='max',   hard=0.40,  soft=0.30,  humans='0.106–0.247', human=0.114,
                         desc='动作短语密度/千字', why='按密度算（非绝对次数）：AI 是人类的 6–8 倍'),
}
# 弱证据：只提示，不计入退出码
WEAK = {
    'sent_cv':      dict(human='0.76–0.84', soft=None, desc='句长变异系数',
                         note='已证伪：三作者 0.76–0.84 与 AI 0.71–0.83 重叠，不作为判据'),
    'tiny_para':    dict(human='15.2–37.3', soft=40.0, desc='单句成段占比 %',
                         note='弱证据：人类本身就有三成短段（惊悚乐园 71%、重生啊 37%），只在 >40% 时提示'),
    'conc50':       dict(human='9.7–15.8', soft=None, desc='top50 2gram 覆盖率 %',
                         note='弱证据：受题材影响太大（惊悚乐园 15.8 因系统面板），不作为判据'),
    'digit_density': dict(human='1.46–3.13', soft=0.50, dir='lt', desc='数字/千字',
                         note='具体性弱信号：人类 1.46–3.13，AI 0.05–0.11。题材敏感（系统流天然多数字），低于 0.5 提示"太泛"，但不阻塞——别让 Agent 机械撒数字'),
    'dialog':       dict(human='20.4–33.5', soft=10.0, dir='lt', desc='对话占比过低 %',
                         note='弱证据：人类 20.4–33.5。reasonix 旧作《禁欲之锁》全书对话 0.0%（全是叙述+心理），《暗处的狩猎》仅 10.1%——"对话洁癖"的反面。低于 10% 提示，题材敏感不阻塞'),
}

PRON = set('我你他她它')
# 支持中英标准引号 与 日式括号「」『』（日式恐怖/轻小说风格常用，计数时须同等对待）
QUOTE_CHARS = set('"“”「」『』')
SENT_SPLIT = re.compile(r'[。！？…]+["”』」）]*')
BODY_WORDS = ['手指', '掌心', '指节', '肩膀', '后颈', '膝盖', '脚踝', '喉咙', '舌尖',
              '牙齿', '胃', '太阳穴', '后背', '肋骨', '手腕', '锁骨', '眼皮', '鼻腔',
              '耳膜', '额头', '眉心', '胸口', '手背', '脚背', '脖颈']
EMOTION_WORDS = ['愤怒', '悲伤', '恐惧', '痛苦', '绝望', '激动', '委屈', '欣喜', '慌乱',
                 '愧疚', '心碎', '窒息', '崩溃', '震惊', '不安', '心动', '心疼', '难受']
SIMILE_WORDS = ['像', '如同', '宛如', '犹如', '好似', '仿佛是']
ACTION_PATTERNS = ['皱起眉头', '握紧拳头', '深吸一口气', '低下头', '抬起头', '别过脸',
                   '转过身', '张了张嘴', '抿了抿嘴', '叹了口气', '眨了眨眼', '攥紧',
                   '垂下眼', '攥了攥', '心头一颤', '瞳孔一缩', '嘴角勾起', '喉结动了动']
FUNC_CHARS = set('的了着在是不是也就都很还又要和会没说有把让对从到么吧吗呢啊这那我你他她它')

# ---------------------------------------------------------------- 编码探测
# 为什么需要：中文小说 txt 下载下来 GBK 极常见。按 UTF-8 读 GBK 不会报错，
# 只会静默产出乱码——实测《惊悚乐园》按 UTF-8 读，405 万中文字只剩 3.1 万（毁掉 98%），
# 脚本输出"全部指标 0.0"，Agent 会误以为"全达标"。**静默失败比报错危险得多。**
_CJK = re.compile(r'[\u4e00-\u9fff]')
_ENCODINGS = ('utf-8', 'gb18030', 'gbk', 'utf-16', 'big5')
_MIN_CJK_RATIO = 0.30      # 中文字符占比低于此值 → 判定为解码失败，报错退出


def read_text(path) -> str:
    """按 utf-8 → gb18030 → gbk → utf-16 → big5 顺序探测，取中文占比最高的成功解码。"""
    raw = Path(path).read_bytes()
    best, best_ratio = None, -1.0
    for enc in _ENCODINGS:
        try:
            s = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        ratio = len(_CJK.findall(s)) / max(1, len(s))
        if ratio > best_ratio:
            best, best_ratio = s, ratio
    if best is None or best_ratio < _MIN_CJK_RATIO:
        raise SystemExit(
            f'[错误] 无法解码或中文占比过低（{best_ratio:.1%}）：{path}\n'
            f'       已尝试 {"、".join(_ENCODINGS)}。请确认文件编码后转成 UTF-8 再跑。\n'
            f'       ⚠ 绝不要忽略这个错误继续执行——乱码会让所有指标显示为 0，看起来像"全部达标"。'
        )
    return best
# 需要剥离的非正文行（统计时也不能算进去）
# 不剔掉这些，章节标题/系统面板/作者打赏语会把句长分布拉歪——
# 人类样本里这类行很多，不处理会让"人类自己都过不了人类基线"
META_LINE = re.compile(r'^(#{1,6}\s|【本章质检摘要】|【本章概要】|---+$|={3,}|^\s*$)')
NON_PROSE = [
    re.compile(r'^\s*\d{2,4}\s+\S.{0,24}$'),      # 章节号 + 标题（"094 求援"）
    re.compile(r'^\s*p[sS]?[:：]'),                # 作者单章求票/感谢语
    re.compile(r'^\s*[\[【].{0,80}[\]】]\s*$'),    # 系统面板整行（"[你已杀死…]"）
    re.compile(r'^\s*[\d\W_]{1,10}$'),             # 纯数字/符号行
    re.compile(r'^\s*(作品相关|内容简介|后记|番外)\s*$'),
]


def extract_body(text: str) -> str:
    out, skip = [], False
    for raw in text.split('\n'):
        line = raw.strip()
        if line.startswith('【本章质检摘要】'):
            skip = True
            continue
        if skip:
            if line == '---':
                skip = False
            continue
        if META_LINE.match(line):
            continue
        if any(p.match(line) for p in NON_PROSE):
            continue
        out.append(line)
    return '\n'.join(out)


def analyze(text: str) -> dict:
    raw = extract_body(text)
    paras = [re.sub(r'\s', '', p) for p in raw.split('\n') if p.strip()]
    paras = [p for p in paras if p]
    flat = re.sub(r'\s', '', raw)
    n = len(flat)
    if n < 500:
        return None
    k = n / 1000.0

    # 句子切分：按句末标点 + 紧跟的右引号切分。右引号（"”』」））紧跟句号是
    # 句子结尾的一部分，切掉后下一句的句首才是真正的左引号，句首引号才测得准。
    sentences = [s for s in SENT_SPLIT.split(flat) if len(s) >= 2]
    slens = [len(s) for s in sentences]
    heads = [s[0] for s in sentences if s]

    pron_head = sum(1 for h in heads if h in PRON) / len(heads) * 100
    quote_head = sum(1 for h in heads if h in '“"「『') / len(heads) * 100
    sent_mean = statistics.mean(slens)
    sent_cv = statistics.stdev(slens) / sent_mean if len(slens) > 3 and sent_mean else 0

    plen = [len(p) for p in paras]
    para_cv = statistics.stdev(plen) / statistics.mean(plen) if len(plen) > 3 else 0
    tiny_para = sum(1 for x in plen if x <= 20) / len(plen) * 100

    quotes = re.findall(r'[“"「『]([^”"」』]{2,})[”"」』]', flat)
    dialog = sum(len(q) for q in quotes) / n * 100

    d = lambda ws: sum(flat.count(w) for w in ws) / k
    acts = {a: flat.count(a) for a in ACTION_PATTERNS if flat.count(a) >= 2}
    # 用密度而非绝对次数：长篇自然会重复，密度才可比
    act_density = round(sum(acts.values()) / k, 3)
    # 数字密度（具体性信号）：人类用具体数字锚定世界（1.46–3.13/千字），AI 几乎不用（0.05–0.11）
    digit_density = round(len(re.findall(r'[0-9]+', flat)) / k, 2)

    f2 = re.sub(r'[^\u4e00-\u9fff]', '', flat)
    grams = Counter()
    for i in range(len(f2) - 1):
        g = f2[i:i + 2]
        if any(c in FUNC_CHARS for c in g):
            continue
        grams[g] += 1
    tot = sum(grams.values())
    conc50 = sum(c for _, c in grams.most_common(50)) / tot * 100 if tot else 0

    return dict(
        chars=n, sent_n=len(sentences), para_n=len(paras),
        pron_head=round(pron_head, 1), quote_head=round(quote_head, 1),
        sent_mean=round(sent_mean, 1), sent_cv=round(sent_cv, 2),
        para_cv=round(para_cv, 2), tiny_para=round(tiny_para, 1),
        dialog=round(dialog, 1), dash=round(flat.count('——') / k, 2),
        body=round(d(BODY_WORDS), 2), emotion=round(d(EMOTION_WORDS), 2),
        simile=round(d(SIMILE_WORDS), 2), conc50=round(conc50, 1),
        act_density=act_density, acts=acts, digit_density=digit_density,
    )


def grade(key, val):
    t = THRESHOLDS[key]
    if t['kind'] == 'max':
        if val > t['hard']:
            return 'FAIL', f"超硬阈值 {t['hard']}"
        if val > t['soft']:
            return 'WARN', f"超软阈值 {t['soft']}"
        return 'PASS', ''
    if t['kind'] == 'min':
        if val < t['hard']:
            return 'FAIL', f"低于硬阈值 {t['hard']}"
        if val < t['soft']:
            return 'WARN', f"低于软阈值 {t['soft']}"
        return 'PASS', ''
    if t['kind'] == 'range':
        if val < t['lo']:
            return 'FAIL', f"低于下限 {t['lo']}（注意：这一项不是越低越好）"
        if val > t['hi']:
            return 'FAIL', f"超上限 {t['hi']}"
        return 'PASS', ''
    return 'PASS', ''


def report(name, r, as_json=False):
    if as_json:
        return dict(file=name, metrics=r, verdict={k: grade(k, r[k])[0] for k in THRESHOLDS})
    print(f'\n===== {name} =====')
    print(f"{'指标':<20}{'实测':>9}{'人类范围':>14}{'目标':>12}{'判定':>7}")
    print('-' * 66)
    fails = warns = 0
    for key, t in THRESHOLDS.items():
        val = r[key]
        st, note = grade(key, val)
        if st == 'FAIL':
            fails += 1
        elif st == 'WARN':
            warns += 1
        if t['kind'] == 'max':
            goal = f"≤{t['hard']}"
        elif t['kind'] == 'min':
            goal = f"≥{t['hard']}"
        else:
            goal = f"{t['lo']}–{t['hi']}"
        mark = {'PASS': '✓', 'WARN': '△', 'FAIL': '✗'}[st]
        print(f"{t['desc']:<20}{val:>9}{t['humans']:>14}{goal:>12}   {mark} {note}")
    print('-' * 66)
    for key, t in WEAK.items():
        if t.get('soft') is None:
            print(f"  [不判] {t['desc']} = {r[key]}（人类 {t['human']}）—— {t['note']}")
        else:
            hit = (r[key] < t['soft']) if t.get('dir') == 'lt' else (r[key] > t['soft'])
            op = '低于' if t.get('dir') == 'lt' else '高于'
            if hit:
                print(f"  [弱证据] {t['desc']} = {r[key]}（人类 {t['human']}，{op}{t['soft']} 才提示）—— {t['note']}")
    if r['acts']:
        print(f"  动作短语：{'  '.join(f'{k}×{v}' for k, v in sorted(r['acts'].items(), key=lambda x: -x[1])[:6])}")
    total = fails * 0 + warns
    if fails:
        print(f"  ✗ {fails} 项未达标（硬）—— 本章不合格，按置信度绑定读到「晃」处理")
    elif warns:
        print(f"  △ {warns} 项超软阈值 —— 建议修，不阻塞")
    else:
        print("  ✓ 全部达标")
    return fails, warns


def main():
    ap = argparse.ArgumentParser(description='人类写作节律检测')
    ap.add_argument('path', help='章节文件 / 或 --all 时的项目目录 / 或 --baseline 时的人类样本')
    ap.add_argument('--all', action='store_true', help='目录下所有 第*.md')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    ap.add_argument('--baseline', action='store_true', help='把该文件当作人类样本，打印其基线值')
    args = ap.parse_args()

    p = Path(args.path)
    if not p.exists():
        print(f'[错误] 文件不存在：{p}')
        sys.exit(2)

    if args.baseline:
        r = analyze(read_text(p))
        if not r:
            print('[错误] 样本过短')
            sys.exit(2)
        print('# 用以下代码替换 THRESHOLDS 中的 human 值，即可把该样本设为新基线：')
        for key in THRESHOLDS:
            print(f"    {key}: ... human={r[key]}")
        sys.exit(0)

    files = sorted(p.glob('第*.md')) if args.all else [p]
    if not files:
        print('[错误] 未找到章节文件')
        sys.exit(2)

    results, total_fail = [], 0
    for f in files:
        r = analyze(read_text(f))
        if not r:
            print(f'[跳过] {f.name} 内容过短')
            continue
        out = report(f.name, r, args.json)
        if args.json:
            results.append(out)
        else:
            total_fail += out[0]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    sys.exit(1 if total_fail else 0)


if __name__ == '__main__':
    main()
