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
import os
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

# ── 共享实现（2026-09-14 重构）────────────────────────────────────
# read_text    ：编码探测（旧实现会把 ASCII 偏多的 UTF-8 误判成 utf-16 → 静默乱码）
# extract_body ：正文提取（此前三个脚本各写一份，实测同一章分母差 12.6%）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import extract_body as _shared_extract_body, read_text as _shared_read_text  # noqa: E402

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
    'pron_head':    dict(kind='max',   hard=15.0,  soft=13.0,  humans='1.5–10.4', human=4.0,
                         desc='句首代词占比 %', why='人类几乎不用"我/他/她"开句（三作者 1.5–10.4%，AI 25.8–47.3%）'),
    'quote_head':   dict(kind='min',   hard=15.0,  soft=18.0,  humans='18.4–30.6', human=18.4,
                         desc='句首引号占比 %', why='人类让对话先行（三作者 18.4–30.6%，AI 0–1.4%）'),
    'sent_mean':    dict(kind='min',   hard=18.0,  soft=23.0,  humans='22.7–36.6', human=30.0,
                         desc='平均句长(字)', why='弱判据：hard=18 只拦最极端的碎句；**目标区间是 23–37（人类均值 30.8）**，soft=23 提示未进人类区间（惊悚乐园 24.4 亦达标）'),
    'dash':         dict(kind='max',   hard=1.5,   soft=1.3,   humans='0.05–0.97', human=0.47,
                         desc='破折号/千字', why='破折号是"句子写完了再补一刀"的痕迹（人类 ≤0.97，AI 3.8–5.0）'),
    'body':         dict(kind='max',   hard=1.0,   soft=0.85,  humans='0.31–0.55', human=0.32,
                         desc='身体部位/千字', why='SKILL 教"情绪身体化"后被顶格执行到人类的 7 倍'),
    'emotion':      dict(kind='range', lo=0.25,    hi=0.50,    humans='0.32–0.35', human=0.33,
                         desc='情绪词/千字', why='最稳定锚点：三作者惊人一致 0.32–0.35，AI 被禁到 0.07–0.17'),
    'simile':       dict(kind='range', lo=0.80,    hi=1.50,    humans='1.05–1.27', human=1.27,
                         desc='明喻/千字', why='比喻配额被顶格执行（人类 1.05–1.27，AI 2.0–3.0）'),
    'dialog':       dict(kind='max',   hard=40.0,  soft=35.0,  humans='20.4–33.5', human=20.4,
                         desc='对话占比 %', why='弱判据：人类 20.4–33.5 与 AI 29.5–38.4 有重叠，只做防堆对话护栏'),
    'act_density':  dict(kind='max',   hard=0.40,  soft=0.30,  humans='0.106–0.247', human=0.114,
                         desc='动作短语密度/千字', why='按密度算（非绝对次数）：AI 是人类的 6–8 倍'),

    # ---- 深度分布指标（第二轮诊断新增；基线 = 3 人类 + 4 AI 实测，均完全分离）----
    # 为什么加这一层：均值已能达标（句长/情绪词/破折号），但读起来仍有 AI 味。
    # 根因是"均值达标 ≠ 分布形状像人"。
    'long_sent_pct': dict(kind='min',  hard=3.0,   soft=5.0,   humans='4.7–19.0', human=10.0,
                         desc='超长句(≥60字)占比 %', why='**最强指标**：人类把多个信息单元打包进一个复合句（4.7–19.0%），AI 只会拆成一串短句（0.7–2.1%）——差 3–25 倍'),
    'sent_p90':     dict(kind='min',   hard=42,    soft=50,    humans='48–74', human=60,
                         desc='句长 p90(字)', why='长句能力的上沿：人类 48–74 字，AI 压在 31–41 —— 说明 AI 根本没能力写长'),
    'rhythm_cv':    dict(kind='min',   hard=0.19,  soft=0.25,  humans='0.251–0.321', human=0.30,
                         desc='节奏变异系数', why='500 字窗口句长均值的起伏：人类"该急则全短句、该缓则长句铺陈"（0.25–0.32），AI 全程一个节奏（0.17–0.26，与人类下沿有重叠，故取 0.19 只拦最极端）'),
    'cn_measure':   dict(kind='max',   hard=10.5,  soft=9.5,   humans='7.04–8.76', human=7.4,
                         desc='中文量词密度/千字', why='**反向指标**：AI 用"一+量词"做泛化指代（一身/一张/一群人）达 11.4–19.2，人类只有 7.04–8.76——人类用具体数字或专有名词'),
    'long_dialog':  dict(kind='min',   hard=8.0,   soft=12.0,  humans='15.8–27.7', human=19.2,
                         desc='长对话(≥30字)占比 %', why='人类对话里有"一段完整发言"（讲道理/诉苦/回忆/辩解），AI 全是短促的信息交换（4.1–14.6%）'),
    'digit_density': dict(kind='min',  hard=0.8,   soft=1.2,   humans='1.16–3.15', human=2.5,
                         desc='数字/千字（具体性）', why='人类用具体数字锚定世界（1.16–3.15），AI 几乎不用（0.05–0.83）——差 1.4–60 倍'),
    'real_measure': dict(kind='min',   hard=0.05,  soft=0.12,  humans='0.07–0.72（现实题材）', human=0.30,
                         desc='现实锚定计量/千字', why='**人类技法型指标（第 15 项，实测 19 倍、接近完全分离）**：人类用真实世界的数目锚定现实——"998 的套餐""9 月 1 号""602 宿舍""月薪六千"。'
                              'AI 几乎只用面板数值（7／100、17 秒），从不锚定现实。'
                              '⚠️ 两点注意：① **题材敏感**——架空/游戏世界豁免（人类《惊悚乐园》仅 0.07，因其世界观里没有钱），只在现实或半现实题材判；'
                              '② **余量薄**（人类最低 0.07 vs 硬线 0.05）——当提示用，**不许为了过线硬塞数字**（那是 hack）。'),

    # ---- 作者在场（第 16 项，2026-09-13 新增；A/B 盲测三位评委一致指出的头号缺口）----
    'author_presence': dict(kind='range', lo=2.5, hi=12.0,
                         humans='3.73–7.84', human=5.96,
                         desc='作者在场指数（叙述部分）',
                         why='**最强的人类/AI 判别器（实测约 2–10 倍分离）**：把引号里的对话剥掉，只统计**叙述部分**的叙述者人格痕迹——'
                              '评价副词（其实/居然/简直/毕竟）×1 + 口语插话（说白了/反正/怎么说呢）×3 + 吐槽贬称（这货/欠揍/要命）×3 + 语气词×1，'
                              '再加上**叙述句**里 ？和 ！的占比×0.5。'
                              '人类 3.73（惊悚乐园）/ 6.25（重生啊）/ 7.84（超神机械师）；'
                              'AI 全书 0.64 / 1.27 / 1.89；上轮 A/B 四篇 0.00 / 0.00 / 0.36 / 0.75。'
                              '**根因：AI 的叙述是透明的——只报告事件，不表态。**'
                              '⚠️ **上限 12 是防 hack**（把吐槽词塞满会变成另一种 AI 味）——它是**区间项**，不是"越高越好"。'),
}
# 弱证据：只提示，不计入退出码
WEAK = {
    'sent_cv':      dict(human='0.76–0.84', soft=None, desc='句长变异系数',
                         note='已证伪：三作者 0.76–0.84 与 AI 0.71–0.83 重叠，不作为判据'),
    'para_cv':      dict(human='0.63–1.12', soft=None, desc='段长变异系数',
                         note='不判：段落层已有 single_para 作判据（人类 40.8–99.0%），'
                              '段长 CV 与 single_para 高度相关，只作为观测值输出'),
    'single_para':  dict(human='40.8–99.0', soft=40.0, dir='lt', desc='单句成段占比 %',
                         note='**段落层的真判据**（2026-09-13 新增）：人类网文默认"一句一段"——惊悚乐园 40.8 / 超神机械师 83.4 / 重生啊 99.0，'
                              'AI 三个样本只有 30.3–37.2。低于 40 提示（弱证据、不阻塞；惊悚乐园 40.8 正好压在下沿，短句风格豁免）'),
    'sent_max':     dict(human='86–166（窗口 p10–p90）', soft=90.0, dir='lt', desc='最长句(字)',
                         note='**长度上沿**（2026-09-13 新增）：取全章最长的一句，看 AI 有没有把一整个复杂处境塞进一句的能力。'
                              '实测窗口级：人类中位 112（p10 86 / p90 166），非人类中位 79（p10 52 / p90 128）。'
                              '**阈值 100 字时：人类章 72.6% 通过、非人类章只有 22.0%** —— 这是**概率信号，不是干净闸门**，'
                              '所以只做非阻塞提示（低于 90 才提示）。写作目标：每章 1–2 句 ≥80 字，其中至少 1 句 ≥100 字。'),
    'tiny_para':    dict(human='26.9–43.8', soft=None, desc='短段(≤20字)占比 %',
                         note='⚠ 已证伪，不作为判据：原实现把它当"单句成段"用，但它算的是"段落 ≤20 中文字"。'
                              '实测人类 26.9–43.8 vs AI 35.9–53.7 —— **完全重叠、无区分度**，且原基线 15.2–37.3 亦不准。此处仅保留观测'),
    'conc50':       dict(human='9.7–15.8', soft=None, desc='top50 2gram 覆盖率 %',
                         note='弱证据：受题材影响太大（惊悚乐园 15.8 因系统面板），不作为判据'),
    'dialog':       dict(human='20.4–33.5', soft=10.0, dir='lt', desc='对话占比过低 %',
                         note='弱证据：人类 20.4–33.5。reasonix 旧作《禁欲之锁》全书对话 0.0%（全是叙述+心理），《暗处的狩猎》仅 10.1%——"对话洁癖"的反面。低于 10% 提示，题材敏感不阻塞'),
}

PRON = set('我你他她它')
# 支持中英标准引号 与 日式括号「」『』（日式恐怖/轻小说风格常用，计数时须同等对待）
SENT_SPLIT = re.compile(r'[。！？…]+["”』」）]*')
BODY_WORDS = ['手指', '掌心', '指节', '肩膀', '后颈', '膝盖', '脚踝', '喉咙', '舌尖',
              '牙齿', '胃', '太阳穴', '后背', '肋骨', '手腕', '锁骨', '眼皮', '鼻腔',
              '耳膜', '额头', '眉心', '胸口', '手背', '脚背', '脖颈']
EMOTION_WORDS = ['愤怒', '悲伤', '恐惧', '痛苦', '绝望', '激动', '委屈', '欣喜', '慌乱',
                 '愧疚', '心碎', '窒息', '崩溃', '震惊', '不安', '心动', '心疼', '难受']
# ⚠️ 2026-09-14 收归共享：明喻词表与计数此前在 check_human_rhythm / check_aistyle
#    各写一份，而且 aistyle 那份是**死代码**（定义了带负向断言的 count_similes，
#    实际算密度却用裸 str.count）→「像」仍误匹配 图像/偶像/雕像。
#    现在只有 _shared 一份，本文件直接 import（见 _shared.py 同名注释）。
from _shared import SIMILE_WORDS, count_similes  # noqa: E402  （模块级导入见文件顶部约定）
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
    """读文件。**编码探测统一走 _shared.read_text**（2026-09-14 重构）。

    旧实现按各编码解出来的中文占比最高选编码 —— utf-16 能把任意偶数长度字节流
    解成中文乱码，在 ASCII 偏多的文件上会赢过 utf-8，导致**静默读成乱码**。
    实测 400 个真实 UTF-8 文件里 16 个（4%）会被选错。新实现：BOM → 严格 utf-8 → 遗留编码。
    """
    s = _shared_read_text(path)
    if len(_CJK.findall(s)) / max(1, len(s)) < _MIN_CJK_RATIO:
        raise SystemExit(
            f'[错误] 读不出中文内容（{path}）——可能是非文本文件或未知编码。\n'
            f'        注意：退出码 1 与检测不合格不可区分，请先确认文件本身可读。')
    return s


def extract_body(text):
    """委托给共享实现（保号，供本脚本内部调用）。"""
    return _shared_extract_body(text)


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

    sent_max = max(slens) if slens else 0
    plen = [len(p) for p in paras]
    para_cv = statistics.stdev(plen) / statistics.mean(plen) if len(plen) > 3 else 0
    # 段落层两个量（2026-09-13 修正：原实现只算 tiny_para 却叫它"单句成段"）
    #   tiny_para   = 短段（≤20 中文字）—— 人类 26.9–43.8 vs AI 35.9–53.7，完全重叠，已证伪
    #   single_para = 单句成段（段内只有 1 句）—— 人类 40.8–99.0 vs AI 30.3–37.2，接近完全分离
    tiny_para = sum(1 for x in plen if x <= 20) / len(plen) * 100
    para_sentn = [max(1, len([s for s in SENT_SPLIT.split(p) if len(s) >= 2])) for p in paras]
    single_para = sum(1 for x in para_sentn if x <= 1) / len(para_sentn) * 100

    quotes = re.findall(r'[“"「『]([^”"」』]{2,})[”"」』]', flat)
    dialog = sum(len(q) for q in quotes) / n * 100

    d = lambda ws: sum(flat.count(w) for w in ws) / k
    acts = {a: flat.count(a) for a in ACTION_PATTERNS if flat.count(a) >= 2}
    # 用密度而非绝对次数：长篇自然会重复，密度才可比
    act_density = round(sum(acts.values()) / k, 3)
    # 数字密度（具体性信号）：人类用具体数字锚定世界（1.46–3.13/千字），AI 几乎不用（0.05–0.11）
    digit_density = round(len(re.findall(r'[0-9]+', flat)) / k, 2)
    # 现实锚定计量（第 15 项，人类技法型）：真实世界的数目——"998 的套餐""9 月 1 号""602 宿舍"
    # 与 digit_density 的区别：digit_density 只数阿拉伯数字；这一项要求数字**绑定现实单位/物件**。
    # 实测人类 0.08–0.72 / AI 0.00–0.07（19 倍，接近完全分离）。仅现实题材判，架空题材豁免。
    real_measure = round(len(re.findall(r'[0-9]{2,4}\s*(?:元|块|万|年|版|号|套餐|级|岁)', flat)) / k, 2)

    # ---- 作者在场（第 16 项，2026-09-13 新增）----
    # 方法：把「对话部分」（引号内）剥掉，只在**叙述部分**统计叙述者的人格痕迹。
    # 依据：A/B 盲测三位评委一致指出 AI 稿"缺少作者在场的感觉"——问题不在对话，在叙述者。
    # 实测 6 部全书 + 4 篇 A/B：人类 3.73–7.84 vs AI 0.64–1.89 vs A/B 0.00–0.75（约 2–10 倍分离）。
    # 人类叙述者会：评价（其实/居然/简直）、口语插话（毕竟/反正/怎么说呢）、吐槽（这货/欠揍）、
    #               以及在**叙述句**里用 ？和 ！（AI 把 ?/! 全放进引号里，叙述部分几乎为 0）
    # 上限从 400 放宽到 2000：一段「完整发言」可能远超 400 字，剥不掉会被算成叙述
    # （仍不设无限量词——无界 +? 在无配对引号的文本上会退化成 O(n²)）
    narr = re.sub(r'[“"「『][^”"」』]{0,2000}?[”"」』]', '　', flat)
    narr = re.sub(r'[^\u4e00-\u9fff。！？…，、；：]', '', narr)
    nn = len(narr)
    if nn >= 800:
        nk = nn / 1000.0
        nsents = [s for s in re.findall(r'[^。！？…]*[。！？…]', narr) if len(s) >= 3]
        _eval = len(re.findall(r'其实|才是|根本|简直|偏偏|倒是|分明|居然|竟然|果然|说到底|无非|实在是|算不上|充其量', narr))
        _informal = len(re.findall(r'说白了|说实话|老实说|反正|毕竟|好歹|怎么说呢|这么说吧|别的不说|换句话说', narr))
        _mock = len(re.findall(r'这货|这家伙|这小子|这厮|这孙子|蠢|离谱|要命|见鬼|鬼才|有病|缺德|不要脸|作死|欠揍|活该|真香|牛逼|丢人', narr))
        _interj = len(re.findall(r'[嘿哈哎唉啧喔诶哟喂嘶]', narr))
        narr_q = round(sum(1 for s in nsents if s.endswith('？')) / len(nsents) * 100, 1) if nsents else 0.0
        narr_ex = round(sum(1 for s in nsents if s.endswith('！')) / len(nsents) * 100, 1) if nsents else 0.0
        author_presence = round((_eval + _informal * 3 + _mock * 3 + _interj) / nk + (narr_q + narr_ex) * 0.5, 2)
    else:
        # 叙述部分不足 800 字（对话极密集的短章）：样本太小，判不出来。
        # 原本返回 0.0 → 会撞上 lo=2.5 误判 FAIL。改为标记为"跳过"。
        narr_q = narr_ex = 0.0
        author_presence = None

    f2 = re.sub(r'[^\u4e00-\u9fff]', '', flat)
    grams = Counter()
    for i in range(len(f2) - 1):
        g = f2[i:i + 2]
        if any(c in FUNC_CHARS for c in g):
            continue
        grams[g] += 1
    tot = sum(grams.values())
    conc50 = sum(c for _, c in grams.most_common(50)) / tot * 100 if tot else 0

    # ---- 深度分布指标（第二轮诊断新增）----
    # 动机：均值已能达标（句长/情绪词/破折号），但读起来仍有 AI 味。
    #      实测发现——"均值达标 ≠ 分布形状像人"。以下 5 项是分布层的强区分指标。
    #
    # 1. 超长句占比（≥60 字）：人类 4.7–19.0%，AI 0.7–2.1% —— 最强指标（差 2–25 倍）
    #    本质：人类把多个信息单元"打包"进一个复合句，AI 倾向拆成多个短句。
    long_sent_pct = sum(1 for x in slens if x >= 60) / len(slens) * 100 if slens else 0
    # 2. 句长 p90：人类 48–74 字，AI 31–41 字
    slens_sorted = sorted(slens)
    sent_p90 = slens_sorted[min(len(slens_sorted) - 1, int(len(slens_sorted) * 0.9))] if slens_sorted else 0
    # 3. 节奏 CV（500 字窗口内句长均值的变异系数）：人类 0.251–0.321，AI 0.165–0.258
    #    本质：人类"该急的地方全是短句、该缓的地方用长句铺陈"，AI 全程保持同一节奏。
    win, win_means = 500, []
    for i in range(0, max(1, n - win), win):
        ss = [len(x) for x in SENT_SPLIT.split(flat[i:i + win]) if len(x) >= 2]
        if len(ss) >= 3:
            win_means.append(statistics.mean(ss))
    rhythm_cv = statistics.stdev(win_means) / statistics.mean(win_means) if len(win_means) > 3 else 0
    # 4. 中文「数字+量词」密度：人类 7.04–8.76，AI 11.4–19.0（**AI 反而是人类的 2–3 倍**）
    #    本质：AI 大量用"一+量词"做泛化指代（一身大红/一张脸/一群人），
    #          人类用"具体数字+量词"或专有名词（三根烟/五分钟/那件洗得发白的褂子）。
    cn_measure = len(re.findall(r'[0-9一二三四五六七八九十百千两]\s*[个只条把张杯辆本支颗根块件次年岁天月分钟秒点级层步句笔名页章套枚台架艘座间]', flat)) / k
    # 5. 长对话占比（≥30 字的引号内容）：人类 15.8–27.7%，AI 4.1–14.6%
    #    本质：AI 对话全是"短促的信息交换"，缺少"一段完整发言"（讲道理/诉苦/回忆/辩解）。
    long_dialog = sum(1 for q in quotes if len(q) >= 30) / len(quotes) * 100 if quotes else 0

    return dict(
        chars=n, sent_n=len(sentences), para_n=len(paras),
        pron_head=round(pron_head, 1), quote_head=round(quote_head, 1),
        sent_mean=round(sent_mean, 1), sent_cv=round(sent_cv, 2),
        para_cv=round(para_cv, 2), tiny_para=round(tiny_para, 1),
        single_para=round(single_para, 1),
        dialog=round(dialog, 1), dash=round(flat.count('——') / k, 2),
        body=round(d(BODY_WORDS), 2), emotion=round(d(EMOTION_WORDS), 2),
        simile=round(count_similes(flat) / k, 2), conc50=round(conc50, 1),
        act_density=act_density, acts=acts, digit_density=digit_density,
        real_measure=real_measure,
        long_sent_pct=round(long_sent_pct, 2), sent_p90=sent_p90, sent_max=sent_max,
        rhythm_cv=round(rhythm_cv, 3), cn_measure=round(cn_measure, 2),
        long_dialog=round(long_dialog, 1),
        author_presence=author_presence, narr_q=narr_q, narr_ex=narr_ex,
    )


def grade(key, val):
    t = THRESHOLDS[key]
    if val is None:
        return 'SKIP', '样本不足（叙述部分 <800 字），本项跳过'
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
        mark = {'PASS': '✓', 'WARN': '△', 'FAIL': '✗', 'SKIP': '–'}[st]
        shown = "—" if val is None else val
        print(f"{t['desc']:<20}{shown:>9}{t['humans']:>14}{goal:>12}   {mark} {note}")
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
