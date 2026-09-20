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
# ⚠ 必须与 check_human_rhythm.py 的 EMOTION_WORDS 完全一致——
#    曾漂移（aistyle 多出「眼里/浑身/心如刀绞」等 5 词、少 3 词），
#    导致同一份稿子两个脚本算出不同情绪词密度。audit_release.py 已加一致性检查。
EMOTION_WORDS = [
    "愤怒",
    "悲伤",
    "恐惧",
    "痛苦",
    "绝望",
    "激动",
    "委屈",
    "欣喜",
    "慌乱",
    "愧疚",
    "心碎",
    "窒息",
    "崩溃",
    "震惊",
    "不安",
    "心动",
    "心疼",
    "难受",
]
FUZZY_WORDS = ['仿佛', '似乎', '好像', '宛如', '像是', '如同', '好比', '依稀', '隐约']
# 顿悟词（AI 爱用"忽然/猛地"制造顿悟感）
INSIGHT_WORDS = ['忽然', '猛地', '突然', '刹那间', '猛然', '倏地']
# 明喻标记词（全用明喻 = 比喻审美均质化）
#
# ⚠️ 2026-09-14 修：本文件此前**自己定义了一份** SIMILE_WORDS / _SIMILE_LIKE_RX /
#    count_similes，而且那份 count_similes 是**死代码**——下面算 simile_density 时
#    用的是 `sum(count_word(body, w) for w in SIMILE_WORDS)`，也就是裸 `str.count`。
#    于是「像」的负向断言修复**只对 check_human_rhythm 生效**，本脚本仍在把
#    图像/影像/偶像/摄像/雕像/对象 全算成明喻。
#    两份已收归 _shared，本文件从下面（正文提取那段）统一导入。

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

# 硬性句式：先否定再肯定（相位文档规定"单章出现 1 次即超标"）
# ⚠️ 实测教训：原版只匹配「不是…而是…」，结果 **AI 学会了绕开"而是"，改用"是"**——
#    人类 0.087/千字 vs AI 0.011/千字，规则完全失效（AI 反而更少）。
#    所以这里必须**同时覆盖同句变体与跨句变体**，并用「不是X，是Y」这个口径做主判据。
HARD_STYLE_PATTERNS = [
    ('不是X，是Y（同句）', r'不是[^。！？\n]{0,20}?，(?:是|要|在|得|能|会)'),
    ('不是X。Y是…（跨句）', r'不是[^。！？\n]{0,25}。[^。！？\n]{0,12}?(?:是|要|在|得|能|会|图)'),
    ('不是…而是…（原版）', r'不是[^。！？\n]{0,20}?而是'),
]
# 硬线（每千字）：人类「同句」0.013–0.031、「跨句」0.131–0.187；AI 分别到 1.257 / 1.257
HARD_STYLE_LIMITS = {'不是X，是Y（同句）': 0.12, '不是X。Y是…（跨句）': 0.40}

# 动作短语密度阈值（每千字）[占位, 中, 高]
# 人类基线 0.114。3000 字章节命中 1 次 = 0.33（已达人类均值）→ 中；≥0.70（6 倍）→ 高
ACTION_DENSITY_THRESHOLDS = [0.34, 0.34, 0.70]
# 明喻密度阈值（每千字）[占位, 中, 高] —— 人类基线 1.27，原阈值[2.5,4.0,6.0]太松，AI 的 2.0 会被判"低"
SIMILE_THRESHOLDS = [1.5, 1.5, 2.0]
# 情绪词密度区间（每千字）—— 不是越低越好！
# deai 第二步曾禁止情绪词，结果 AI 只有人类的 1/5–1/2，矫枉过正。低于下限同样不合格。
EMOTION_RANGE = (0.25, 0.50)   # ⚠ 必须与 check_human_rhythm.py 的 emotion hi 一致（曾漂移为 0.55）
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

# ── 万能情绪 / 恐惧模板（novel-humanize 第五节；实测人类 0.00–0.02/千字，AI 0.00–0.12）──
# 单独成表的原因：这批是**小说专属标准件**，人类几乎从不使用；而上表 COMMON_TEMPLATE
# 里混着"一个人/走廊里"这类**伪具体**，两者性质不同（一个是修辞标准件，一个是伪具体指代）。
# ⚠️ 这张表是**逐词实测**筛出来的（人类 3 部 × 60 万字 vs AI 3 样本全书）。
# 来源清单（novel-humanize 第五节）原有 44 个词，实测后**只有 10 个能分离人类与 AI**：
#   · 11 个人类用得**比 AI 还多** → 必须剔除（否则又是"命中即换"型误伤）
#        忍不住(人类 0.25 vs AI 0.02) · 不禁(0.05 vs 0.00) · 头皮发麻 · 眼中闪过一丝 ·
#        忽然意识到 · 突然意识到 · 终于明白 · 脑海中闪过 · 良久 · 顿了几秒 · 身体一晃
#   · 23 个**两边都是 0**（词太罕见，无信号）→ 可作"写作时避免"的提示，
#        但**不能当判据**。见 ai-cliche-blacklist.md 第三节的三档分类。
# 下面 10 个是 human≈0 且 AI>0 的：human 列 → AI 列（/千字）
# ⚠️ **这是弱信号，不是闸门。** 实测：人类全书 0.007–0.032/千字、AI 0.081–0.201，
# 方向对（AI 高 3–30 倍），但绝对量极小——3000 字章通常 0–1 次命中，**不足以做逐章硬判**。
# 所以只做非阻塞提示。真正好用的地方是**写作时**（别写成标准件），不是**判稿时**。
#
# 另：来源清单里的 半晌 / 空气中弥漫 / 目光平静 已被剔除——人类三部都用了
#（超神 半晌×21、空气中弥漫×7；重生啊 目光平静×4），**属于人类正常用词**。
CLICHE_EMOTION = [
    '空气凝固',     # 人类 0.00 → AI 0.06
    '心跳漏了一拍',   # 人类 0.002 → AI 0.06
    '后背发凉',     # 人类 0.007 → AI 0.04
    '指节泛白',     # 人类 0.00 → AI 0.02
    '心跳如鼓',     # 人类 0.00 → AI 0.01
    '指节攥得发白',   # 人类 0.00 → AI 0.01
    '脊背发凉',     # 人类 0.00 → AI 0.01
]
# 密度提示线（**非阻塞**）：人类 ≈0、AI 0.01–0.09，绝对量都小，
# 所以不做硬闸门（会变成假闸门），只提示。出现 ≥3 处不同模板时额外警告"成套出现"。
CLICHE_SOFT_DENSITY = 0.15
CLICHE_SOFT_KINDS = 5
TEMPLATE_MIN_COUNT = 2        # 单个模板短语出现 ≥2 次才列出
TEMPLATE_SOFT_DENSITY = 1.5   # 总模板密度（/千字）超过此值提示模板化（人类 0.55–1.22，AI 1.36–2.07）

# n-gram 高频短语检测参数
PHRASE_MIN_COUNT = 3      # 出现 ≥3 次才报告
PHRASE_NGRAMS = (5, 6, 7, 8)
# 功能字（过滤含这些字的 n-gram，减少误报）
PHRASE_FUNC_CHARS = set('的了着在是不是也就都很还又要和会没说有把让对从到么吧吗呢啊这那')

# 半角引号（ASCII 直引号）—— **硬闸门，零容忍**
# 实测：人类三部参考 **0%**（0 处），AI 样本 0–6.45%。
# 这是**生产级事故**：子代理写 .md 时天然输出 ASCII `"`，
# 会导致 check_human_rhythm 的「句首引号占比」直接判 0.0%（脚本只认全角/直角引号）。
# 2026-09-16 起：正文里出现任何一个 ASCII `"` 即判不合格。
ASCII_QUOTE_CHARS = ('"', "'")      # 半角双引号 + 半角单引号（后者只在对话场景可疑）
ASCII_QUOTE_HARD = 1                # ≥1 处即不合格

# 汉字↔半角数字之间夹空格（2026-09-19 新增，来自一次盲评）
#   编辑评委靠肉眼抓到「第 12 回」「408 号」「死 4 回」。实测密度（每千字）：
#     人类三部 0.03 / 0.15 / **0.00**  ｜ AI 对照 0.04  ｜ 本体系产出 **1.77 / 3.39**
#   ——**差 12–100 倍**，是目前分离度最高的格式类指纹之一。
#   根因：英文排版习惯（数字两侧留空格）漏进中文正文。中文出版惯例是**不留**（"10版本""60w经验"）。
CJ_DIGIT_SPACE_HARD = 0.5           # 每千字；人类最高 0.15，取 3× 余量


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



# ── 正文提取 / 读文件 / 明喻计数 统一走 _shared.py（唯一实现；2026-09-14 重构）──
# 此前本脚本自带一份 extract_body，与另外两个脚本口径不同：
# 实测同一章三脚本分母 3351 / 3074 / 2976 字（最大差 12.6%），所有密度指标互相矛盾。
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import (extract_body as _shared_extract_body,
                     read_text as _shared_read_text,
                     SIMILE_WORDS, count_similes)  # noqa: E402,F401


def extract_body(text):
    """委托给共享实现（保号，供本脚本内部调用）。"""
    return _shared_extract_body(text)


def count_word(text: str, word: str) -> int:
    return text.count(word)


def read_text_any(path: Path) -> str:
    """多编码容错读取。**委托给 `_shared.read_text`**（2026-09-14 收归）。

    历史：原实现硬编码 encoding='utf-8'，遇到 GBK/gb18030 的 txt（大量网文范本都是）
    会直接 UnicodeDecodeError 崩溃，而崩溃退出码 1 与"检测不合格"无法区分。

    ⚠️ 中间态版本自己写了一份"utf-8 优先 + 遗留编码探测"，**能跑但没走共享实现**：
    它没有 BOM 分支（utf-16/32 文件读不了），而且第 4 处独立编码实现本身就是漂移隐患。
    现在统一委托 —— `_shared.read_text` 就是"BOM → 严格 utf-8 → 遗留编码"这条链。
    """
    return _shared_read_text(path)


def analyze_chapter(file_path: Path) -> dict:
    text = read_text_any(file_path)
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
    # ⚠️ 明喻必须走 count_similes（「像」带负向断言），不能用 `count_word(body, '像')`：
    #    后者会把 图像/影像/偶像/摄像/雕像/对象 全算成明喻（本文件曾这么错了很久）。
    simile_density = count_similes(body) / total

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

    # 10. 硬性句式：先否定再肯定（不是X，是Y）
    # 实测（3 人类 vs 6 部 AI 成品）：
    #   原版只匹配「不是…而是…」→ 人类 0.087/千字 vs AI 0.011 —— **AI 反而更少，规则完全失效**
    #     （AI 学会了绕开"而是"，改用"是"）
    #   「同句 不是X，是Y」    → 人类 0.013–0.031 vs AI 0.000–1.257  ← 可用，取 hard 0.12
    #   「跨句 不是X。Y是…」  → 人类 0.131–0.187 vs AI 0.247–1.257  ← 可用，取 hard 0.40
    hard_style = {}
    for label, rx in HARD_STYLE_PATTERNS:
        hits = re.findall(rx, body)
        if hits:
            hard_style[label] = (len(hits), round(len(hits) / total, 3) if total else 0)

    # 11. 万能情绪/恐惧模板（小说专属标准件）
    cliche_hits = {}
    for w in CLICHE_EMOTION:
        n = body.count(w)
        if n >= 1:
            cliche_hits[w] = n
    cliche_density = sum(cliche_hits.values()) / total if total else 0

    # 12. 半角引号（ASCII 直引号）—— 硬闸门
    ascii_dq = body.count('"')
    cj_space = len(re.findall(r'[\u4e00-\u9fff]\s+[0-9]|[0-9]\s+[\u4e00-\u9fff]', body))
    cj_space_density = round(cj_space / max(1e-9, len(body) / 1000.0), 2)
    ascii_sq = body.count("'")
    # 全角/直角引号（用于对照：若全角也为 0 且 ASCII > 0，说明引号样式整体写错了）
    full_q = body.count('\u201c') + body.count('\u201d') + body.count('\u300c') + body.count('\u300d')

    return {
        'file': file_path.name,
        'ascii_dq': ascii_dq,
        'cj_space': cj_space, 'cj_space_density': cj_space_density,
        'ascii_sq': ascii_sq,
        'full_quote_n': full_q,
        'cliche_hits': cliche_hits,
        'cliche_density': round(cliche_density, 3),
        'hard_style': hard_style,
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

    # ── 半角引号（硬闸门）──
    dq, sq, fq = r.get('ascii_dq', 0), r.get('ascii_sq', 0), r.get('full_quote_n', 0)
    if dq:
        print(f'半角双引号 `"`  {dq} 处  ✗ **不合格（零容忍）**')
        print('  → 对话一律用中文引号 `“ ”`（或项目统一用 `「」`，但必须全书一致）。')
        print('    根因：写 .md 时天然输出 ASCII 引号，会让"句首引号占比"指标直接判 0.0%。')
        if fq == 0:
            print('    ⚠ 全角引号 0 处 + 半角 %d 处 = **整章引号样式全错**，不是漏改几处' % dq)
    else:
        print(f'引号样式 ✓（全角/直角引号 {fq} 处，半角 0 处）')

    # ── 汉字↔半角数字夹空格（硬闸门）──
    cjs, cjsd = r.get('cj_space', 0), r.get('cj_space_density', 0.0)
    if cjsd > CJ_DIGIT_SPACE_HARD:
        print('汉字/数字间夹半角空格  %d 处 / %s 千字  ✗ **不合格**（硬线 %s，人类最高 0.15）'
              % (cjs, cjsd, CJ_DIGIT_SPACE_HARD))
        print('  → 中文字与阿拉伯数字之间**不留空格**：写“第12回”“408号”“4回”，不写“第 12 回”。')
        print('    根因：英文排版习惯漏进中文正文；盲评时编辑评委一眼就看出来了。')
    else:
        print('汉字/数字夹空格 ✓（%d 处 / %s 千字）' % (cjs, cjsd))

    # ── 万能情绪/恐惧模板（提示，非阻塞）──
    ch = r.get('cliche_hits', {})
    if ch:
        top = sorted(ch.items(), key=lambda x: -x[1])[:8]
        over = r['cliche_density'] > CLICHE_SOFT_DENSITY or len(ch) >= CLICHE_SOFT_KINDS
        print(f'万能情绪模板：{"、".join(f"{k}×{v}" for k, v in top)}')
        print(f'  → {len(ch)} 种 / {r["cliche_density"]}/千字 '
              f'{"△ **成套出现**（这是最容易辨认的 AI 指纹）" if over else "提示"}'
              f'（人类基线 0.00–0.02，AI 0.00–0.12）')
        print('     ⚠ 这些是"标准件"——人类几乎不用。换成**只有这个人物会做的**特定动作：')
        print('       有人紧张时反复清嗓子，有人不停摸耳垂，有人把手机翻过来又翻回去。')
        print('       详见 ai-cliche-blacklist.md 第三节')
    else:
        print('万能情绪模板：无命中 ✓（最好状态）')

    hs = r.get('hard_style', {})
    if hs:
        bad = []
        print('硬性句式（先否定再肯定）:')
        for label, (cnt, dens) in sorted(hs.items()):
            lim = HARD_STYLE_LIMITS.get(label)
            over = lim is not None and dens > lim
            mark = '✗ 超标' if over else ('提示' if lim is not None else '参考')
            if over:
                bad.append(label)
            print(f'  {label}  {cnt} 次 / {dens}/千字  {mark}'
                  + (f'（硬线 {lim}）' if lim is not None else ''))
        if bad:
            print('  → **单章出现即超标，逐句重写**：把"不是X，是Y"改成直接陈述 Y，'
                  '或把否定句独立留白（见 human-exemplars.md 技法三「故意不解释」）')
            print('  ⚠ 注意：只匹配「而是」的原版规则已失效——AI 早已改用「是」绕开它')
        else:
            print('  未超标 ✓')
    bad_hard_style = bool(hs and any(
        dens > HARD_STYLE_LIMITS.get(label, 9e9) for label, (cnt, dens) in hs.items()))
    # 半角引号 = 生产级格式事故，零容忍（人类 0%）
    bad_quote = r.get('ascii_dq', 0) >= ASCII_QUOTE_HARD
    # 汉字/数字夹空格 = 中文排版惯例错误（人类 0.00–0.15/千字）
    bad_cjspace = r.get('cj_space_density', 0) > CJ_DIGIT_SPACE_HARD
    if bad_hard_style or bad_quote or bad_cjspace:
        print()
        if bad_quote:
            print('✗ 不合格项：半角双引号（零容忍）')
        if bad_cjspace:
            print('✗ 不合格项：汉字与数字间夹半角空格（中文出版惯例不留空格）')
        if bad_hard_style:
            print('✗ 不合格项：硬性句式（不是X，是Y）超标')
    return bad_hard_style or bad_quote or bad_cjspace


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
        hard_violations = 0
        for f in files:
            r = analyze_chapter(f)
            if r:
                results.append(r)
                if print_chapter(r):
                    hard_violations += 1
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
            hard_violations = 1 if print_chapter(r) else 0
        else:
            print('[提示] 无可统计的正文内容')
            hard_violations = 0

    # 退出码：只有「硬性句式（先否定再肯定）」是硬闸门——相位文档规定"单章出现 1 次即超标"。
    # 其余指标（转折词/模糊词/顿悟词/明喻/动作/模板短语）保持"报告 + 结合上下文处理"。
    if hard_violations:
        print(f'\n✗ 硬性句式超标 {hard_violations} 处 —— 须逐句重写后再提交')
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
