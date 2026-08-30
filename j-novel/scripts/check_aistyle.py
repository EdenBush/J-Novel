#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 统计指纹检测（软指纹，分布层面）
识别"分布均匀"型 AI 痕迹——单句检查抓不到，但统计会露馅：
1. 段落长度分布：人类长段短段交替（方差大），AI 均匀（方差小）
2. 对话比例：人类各章对话比例波动大，AI 稳定在 30-40%
3. 转折词密度：然而/但是/却/竟——AI 爱用转折制造张力
4. 直接情绪词密度：愤怒/悲伤/恐惧——AI 爱直接写出情绪
5. 高频动作短语："皱起眉头/握紧拳头"——AI 反复调用同一动作标签
6. 模糊词密度：仿佛/似乎/好像/宛如——AI 爱用模糊词加"文学感"

注意：这是"软指纹"，只给风险提示，不判死刑（人类也可能有一项偏高）。
用法:
  python check_aistyle.py <章节文件.md>          # 单章统计
  python check_aistyle.py --all <项目目录>       # 全书统计（含各章对话比例一致性）
"""

import argparse
import io
import re
import statistics
import sys
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 词典
TRANSITION_WORDS = ['然而', '但是', '可是', '却', '竟', '反倒', '反而', '不过', '然而事实上']
EMOTION_WORDS = ['愤怒', '悲伤', '恐惧', '痛苦', '绝望', '激动', '委屈', '欣喜', '慌乱', '愧疚',
                 '心碎', '窒息', '崩溃', '震惊', '不安', '愤怒到', '心如刀绞', '心头一紧', '眼里', '浑身']
FUZZY_WORDS = ['仿佛', '似乎', '好像', '宛如', '像是', '如同', '好比', '依稀', '隐约']
# 顿悟词（AI 爱用"忽然/猛地"制造顿悟感）
INSIGHT_WORDS = ['忽然', '猛地', '突然', '刹那间', '猛然', '倏地']
# 明喻标记词（全用明喻 = 比喻审美均质化）
SIMILE_WORDS = ['像', '如同', '宛如', '犹如', '好似', '仿佛是']
# 动作短语（4-6 字高频动作标签，检测重复调用）
#
# ⚠⚠ 这是一份「违禁品清单」，不是「素材库」。⚠⚠
#
# 曾经有 Agent 把这张表当成"怎么加毛边"的参考答案，照着往文里塞，结果：
#     动作短语密度 0.65–0.905/千字，而人类基线（齐佩甲全书 540 万字）只有 0.114/千字
#     —— 6–8 倍。对比：人类全书最高频的一个动作短语也只有 121 次（0.022/千字）。
#
# 正确用法：写完之后拿这张表**扫**自己的稿子，命中就换掉。
#           写作过程中不要想起它——想起它就会用，用了就是标签，不是动作。
#           要写具体动作："他把烟按灭在扶手上的烟灰缸里"，而不是"他低下头"。
ACTION_PATTERNS = ['皱起眉头', '握紧拳头', '深吸一口气', '低下头', '抬起头', '别过脸', '转过身',
                   '握了握', '张了张嘴', '抿了抿嘴', '叹了口气', '眨了眨眼', '攥紧', '垂下眼',
                   '攥了攥', '心头一颤', '瞳孔一缩', '嘴角勾起', '喉结动了动']
# 动作短语密度阈值（每千字）[占位, 中, 高]
# 人类基线 0.114。3000 字章节命中 1 次 = 0.33（已达人类均值）→ 中；≥0.70（6 倍）→ 高
ACTION_DENSITY_THRESHOLDS = [0.34, 0.34, 0.70]
# 明喻密度阈值（每千字）[占位, 中, 高] —— 人类基线 1.27，原阈值[2.5,4.0,6.0]太松，AI 的 2.0 会被判"低"
SIMILE_THRESHOLDS = [1.5, 1.5, 2.0]
# 情绪词密度区间（每千字）—— 不是越低越好！
# deai 第二步曾禁止情绪词，结果 AI 只有人类的 1/5–1/2，矫枉过正。低于下限同样不合格。
EMOTION_RANGE = (0.25, 0.55)
# 通用模板短语（"任何小说都能套的通用词"）
#
# 全景诊断（11 部 AI vs 3 部人类）发现：人类的高频短语是**专有名词**（陈汉升/三大文明/黑星军团），
# AI 的高频短语是**通用模板词**（一个人/低下头/走廊里/这一刻）。
# "一个人"这个模板出现在至少 5 部 AI 小说里，是 LLM 写"孤独/独处/主角在场"的万能填充。
#
# 这是「伪具体」的外在表现：SKILL 说"写具体物"，LLM 把"具体"理解成"一个+N"，
# 而不是"有名字、有独特特征、不可替换"的东西。真具体 = 专有名词，伪具体 = 一个+N。
#
# 检测规则：单个模板短语 ≥2 次才列出；总密度超过软阈值 1.5/千字提示"模板化"。
# 注意：这是软证据（内容决策层，题材敏感），不像句式硬指标那样一票否决。
COMMON_TEMPLATE = [
    # 伪具体的数量/指代（"一个+N"式泛化）
    '一个人', '一个东西', '一个问题', '一件事', '一种感觉', '那个身影', '那道身影',
    # 场景模板（任何小说都能套的通用场景）
    '走廊里', '走廊上', '教室里', '楼梯间', '窗边', '门边',
    # 时间模板（AI 爱用"这一刻/那一刻"制造瞬间感）
    '这一刻', '那一刻', '此时', '此刻',
    # 情感/声音模板（人物情绪/说话时的万能配音）
    '声音闷闷的', '声音很轻', '声音低低', '眼睛一亮', '心头一紧', '心头一颤',
    '微微一怔', '微微一愣', '深吸一口气',
    # 高频连接模板
    '一边一边',
]
TEMPLATE_MIN_COUNT = 2        # 单个模板短语出现 ≥2 次才列出
TEMPLATE_SOFT_DENSITY = 1.5   # 总模板密度（/千字）超过此值提示模板化（人类 0.55–1.22，AI 1.36–2.07）

# n-gram 高频短语检测参数
PHRASE_MIN_COUNT = 3      # 出现 ≥3 次才报告
PHRASE_NGRAMS = (5, 6, 7, 8)
# 功能字（过滤含这些字的 n-gram，减少误报）
PHRASE_FUNC_CHARS = set('的了着在是不是也就都很还又要和会没说有把让对从到么吧吗呢啊这那')


def find_repeated_phrases(body: str, top_n: int = 8) -> list:
    """检测高频重复短语（"青草被晒了一整天""眼睛弯成月牙"式自我复制）"""
    from collections import Counter
    text = re.sub(r'[\s\u3000，。！？、；：""''（）《》…—.-]', '', body)
    counter = Counter()
    for n in PHRASE_NGRAMS:
        for i in range(len(text) - n + 1):
            gram = text[i:i + n]
            # 功能字占比 > 40% 才过滤（如"他看了看"），允许"晒了一整天"这类含少量助词的短语
            func_ratio = sum(1 for c in gram if c in PHRASE_FUNC_CHARS) / len(gram)
            if func_ratio > 0.4:
                continue
            counter[gram] += 1
    dupes = {k: v for k, v in counter.items() if v >= PHRASE_MIN_COUNT}
    items = sorted(dupes.items(), key=lambda x: -x[1])
    result, covered = [], []
    for k, v in items:
        if any(k in c for c in covered):
            continue
        result.append((k, v))
        covered.append(k)
        if len(result) >= top_n:
            break
    return result



def extract_body(text: str) -> str:
    lines = text.split('\n')
    out, skip_block, skip_qc = [], False, False
    for raw in lines:
        line = raw.strip()
        if line.startswith('【本章质检摘要】'):
            skip_qc = True
            continue
        if skip_qc:
            if line == '---':
                skip_qc = False
            continue
        if any(line.startswith(b) for b in ('## 本章概要', '## 章节备注', '## 章节概要')):
            skip_block = True
            continue
        if skip_block:
            if line.startswith('#') or line == '---':
                skip_block = False
            if skip_block:
                continue
        if line.startswith('## '):
            continue
        if line == '---':
            continue
        # 合订本常用 ==== 做章节分隔线，不剔除会污染 n-gram 统计
        if re.fullmatch(r'[=\-—_*·]{3,}', line):
            continue
        if re.fullmatch(r'第.{0,8}章.{0,24}', line):
            continue
        out.append(line)
    return '\n'.join(out)


def count_word(text: str, word: str) -> int:
    return text.count(word)


def analyze_chapter(file_path: Path) -> dict:
    text = file_path.read_text(encoding='utf-8')
    body = extract_body(text)
    if not body.strip():
        return None

    # 1. 段落长度分布
    paras = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    para_lens = [len(re.sub(r'\s', '', p)) for p in paras]
    cv_para = (statistics.stdev(para_lens) / statistics.mean(para_lens)) if len(para_lens) > 3 and statistics.mean(para_lens) > 0 else 0

    # 2. 对话比例（引号内容占比）
    quotes = re.findall(r'["“]([^"”]{2,})["”]', body)
    quote_chars = sum(len(q) for q in quotes)
    body_chars = len(re.sub(r'\s', '', body))
    dialog_ratio = (quote_chars / body_chars) if body_chars > 0 else 0

    # 3-6. 词频（每千字）
    total = body_chars / 1000.0
    if total <= 0:
        total = 0.001
    trans_density = sum(count_word(body, w) for w in TRANSITION_WORDS) / total
    emotion_density = sum(count_word(body, w) for w in EMOTION_WORDS) / total
    fuzzy_density = sum(count_word(body, w) for w in FUZZY_WORDS) / total
    insight_density = sum(count_word(body, w) for w in INSIGHT_WORDS) / total
    simile_density = sum(count_word(body, w) for w in SIMILE_WORDS) / total

    # 7. 动作短语（按密度判定，不是绝对次数——长章节自然会多命中几次）
    action_hits = {}
    for pat in ACTION_PATTERNS:
        n = body.count(pat)
        if n >= 1:
            action_hits[pat] = n
    repeated_actions = sum(action_hits.values())
    action_density = repeated_actions / total if total else 0

    # 8. 高频短语自我复制
    repeated_phrases = find_repeated_phrases(body)

    # 9. 通用模板短语（"一个人/这一刻/走廊里"这类任何小说都能套的通用词）
    template_hits = {}
    for w in COMMON_TEMPLATE:
        n = body.count(w)
        if n >= TEMPLATE_MIN_COUNT:
            template_hits[w] = n
    template_density = sum(template_hits.values()) / total if total else 0

    return {
        'file': file_path.name,
        'para_cv': round(cv_para, 2),
        'para_n': len(para_lens),
        'dialog_ratio': round(dialog_ratio, 3),
        'trans_density': round(trans_density, 1),
        'emotion_density': round(emotion_density, 1),
        'fuzzy_density': round(fuzzy_density, 1),
        'insight_density': round(insight_density, 1),
        'simile_density': round(simile_density, 1),
        'action_hits': action_hits,
        'repeated_actions': repeated_actions,
        'action_density': round(action_density, 3),
        'repeated_phrases': repeated_phrases,
        'template_hits': template_hits,
        'template_density': round(template_density, 3),
    }


def flag_level(metric, value, thresholds):
    """根据 [低, 中, 高] 阈值返回风险等级"""
    if value >= thresholds[2]:
        return '高'
    if value >= thresholds[1]:
        return '中'
    return '低'


def print_chapter(r: dict, full: bool = False):
    print(f'\n===== {r["file"]} =====')
    print(f'段落长度变异系数 CV={r["para_cv"]}（{r["para_n"]} 段）'
          f' → {flag_level(None, r["para_cv"], [0.6, 0.8, 1.0])}'
          f' [CV 低=段长均匀(AI), 高=长短交替(人)]')
    print(f'对话占比 {r["dialog_ratio"]*100:.0f}%'
          f' → {flag_level(None, r["dialog_ratio"], [0.55, 0.7, 0.9])}'
          f' [过高=全功能性对话风险]')
    print(f'转折词密度 {r["trans_density"]}/千字'
          f' → {flag_level(None, r["trans_density"], [2.0, 3.5, 5.0])}'
          f' [高=爱用"然而/但是"制造张力]')
    lo, hi = EMOTION_RANGE
    if r['emotion_density'] < lo:
        eflag = f'✗ 低于下限 {lo}（情绪词被禁过头了，要补直说/动作，不是删）'
    elif r['emotion_density'] > hi:
        eflag = f'△ 超上限 {hi}'
    else:
        eflag = '✓ 区间内'
    print(f'直接情绪词密度 {r["emotion_density"]}/千字 → {eflag}'
          f' [区间 {lo}–{hi}；人类基线 0.33——**不是越低越好**]')
    print(f'模糊词密度 {r["fuzzy_density"]}/千字'
          f' → {flag_level(None, r["fuzzy_density"], [1.5, 2.5, 4.0])}'
          f' [高=仿佛/似乎堆砌]')
    print(f'顿悟词密度 {r["insight_density"]}/千字'
          f' → {flag_level(None, r["insight_density"], [1.0, 2.0, 3.0])}'
          f' [高=忽然/猛地依赖，AI 制造顿悟感的习惯]')
    print(f'明喻密度 {r["simile_density"]}/千字'
          f' → {flag_level(None, r["simile_density"], SIMILE_THRESHOLDS)}'
          f' [人类基线 1.27；≥2.0 = 比喻审美均质化，全明喻无白描]')
    if r['action_hits']:
        print(f'动作短语：{"、".join(f"{k}×{v}" for k, v in r["action_hits"].items())}')
        print(f'  → 密度 {r["action_density"]}/千字'
              f' {flag_level(None, r["action_density"], ACTION_DENSITY_THRESHOLDS)}'
              f'（人类基线 0.114；3000字章命中1次=0.33 已达人类均值，2次=6倍，3次=9倍）'
              f'\n     ⚠ 这张表是违禁品清单不是素材库——命中就换具体动作，不要照着它往文里塞')
    else:
        print('动作短语：无命中 ✓（最好状态）')
    if r['repeated_phrases']:
        print(f'高频短语自我复制：{"、".join(f"{k}×{v}" for k, v in r["repeated_phrases"][:5])}'
              f' [出现≥3次=意象/语句复制，人类会写腻]')
    else:
        print('高频短语复制：无')
    if r['template_hits']:
        top_tpl = sorted(r['template_hits'].items(), key=lambda x: -x[1])[:6]
        flag = '△ 模板化' if r['template_density'] > TEMPLATE_SOFT_DENSITY else '提示'
        print(f'通用模板短语：{"、".join(f"{k}×{v}" for k, v in top_tpl)}')
        print(f'  → 密度 {r["template_density"]}/千字 {flag}'
              f'（人类 0.55–1.22，AI 1.36–2.07）'
              f'\n     ⚠ 人类高频词是专有名词（陈汉升/三大文明），AI 是通用词（一个人/这一刻）——'
              f'"一个人"换成"一个 XX 的人"只是推迟问题，要换成独一无二的东西')
    else:
        print('通用模板短语：无命中 ✓')


def main():
    parser = argparse.ArgumentParser(description='AI 统计指纹检测（分布层面软指纹）')
    parser.add_argument('path', help='章节 .md 文件，或 --all 时的项目目录')
    parser.add_argument('--all', action='store_true', help='全书统计（含各章对话比例一致性）')
    args = parser.parse_args()

    if args.all:
        project = Path(args.path)
        files = sorted(project.glob('第*.md'), key=lambda p: p.name)
        if not files:
            print('[错误] 未找到章节文件'); sys.exit(1)
        results = []
        for f in files:
            r = analyze_chapter(f)
            if r:
                results.append(r)
                print_chapter(r)
        # 全书对话比例一致性
        if len(results) >= 3:
            ratios = [r['dialog_ratio'] for r in results]
            cv = statistics.stdev(ratios) / statistics.mean(ratios) if statistics.mean(ratios) > 0 else 0
            print(f'\n===== 全书对话比例一致性 =====')
            print(f'各章对话占比：{"、".join(f"{x*100:.0f}%" for x in ratios)}')
            print(f'变异系数 CV={cv:.2f} → {flag_level(None, cv, [0.15, 0.25, 0.4])}'
                  f' [CV 低=各章对话比例过匀(AI)，高=波动自然(人)]')
    else:
        f = Path(args.path)
        if not f.exists():
            print(f'[错误] 文件不存在：{args.path}'); sys.exit(1)
        r = analyze_chapter(f)
        if r:
            print_chapter(r)
        else:
            print('[提示] 无可统计的正文内容')


if __name__ == '__main__':
    main()
