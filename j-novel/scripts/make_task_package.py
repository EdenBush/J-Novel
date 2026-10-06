#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
任务包编译器（J-Novel 新增，2026-09-23）
=========================================

**为什么需要它**：任务包此前由**主编手工装配**（十项）。手工装配有三个必然缺陷，
而且每一个都在实测中发生过：

| 缺陷 | 实测 |
|---|---|
| **槽位没有 schema** → 少填一个没人知道 | 「锚点滚动」「句法目标表」写了却从未进包（漏了几个月） |
| **槽位没有预算** → 有的一塞 3000 字 | 实测**每次调用平均重发 149,675 tokens 上下文**（中位） |
| **脚本能拿到的东西却靠人记** → 必然漏 | 上章结尾、契约、锚，本来都能从文件里自动抠 |

**所以本脚本做三件事**：
    ① **装配**：脚本能拿到的（上章真实结尾 / 契约 / 批次全局三件套 / 章号奇偶字数）
       全部**自动填好**；只有需要理解才能产生的（世界设定包 / 作者画面 / 已知病灶 / 诊断句）
       留成**显式待填标记**。**脚本不假装自己能写世界设定包。**
    ② **预算**：每个槽位有字数上限，总包有总上限 → 这是把"每次调用的上下文"
       从十几万压下来的**唯一机械手段**（成本 ≈ 调用次数 × 上下文）。
    ③ **校验**：`--check` 检查必填齐备 + 未超预算 + 没有"待主编填"残留。
       **缺槽位 = 拒绝开工** —— 把"记得填"变成"编译不过"。

用法:
  python make_task_package.py <项目> --chapter 12            # 打印骨架
  python make_task_package.py <项目> --chapter 12 --write    # 写到 任务包/第12章-任务包.md
  python make_task_package.py <项目> --check <任务包.md>      # 校验单份
  python make_task_package.py <项目> --check --all           # 批闸：校验全部
退出码: 0 = 齐备且不超预算；1 = 缺槽位 / 超预算 / 待填未填；2 = 取不到源（fail-closed）
"""

import argparse
import io
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import ensure_utf8_stdio, find_chapter_files, read_text  # noqa: E402

# ★ 必须在 import 同族脚本之前调用（它们也会调，但幂等）
ensure_utf8_stdio()

from check_contract import (find_outline_files, parse_contract,  # noqa: E402
                            split_chapters)
from make_handoff import _paras, _tail                     # noqa: E402

PKG_DIR = '任务包'

# ── 槽位表 ────────────────────────────────────────────────────────────
# (键, 标题, 预算字数, 必填, 谁来填)
# 预算依据：成本 ≈ 调用次数 × **每次调用带多重的上下文**。
# 实测中位 149,675 tokens/次 —— 任务包是"被乘数"里可以机械约束的那部分。
# CJK 约 1 token/字，故总量是可控的量级。
#
# ★ 槽位顺序 = **显著性设计**（2026-09-24 重排，见下方 rationale）。
#   开头放**硬约束**、结尾放**「照着写」的正向具体素材**；参考性内容放中段。
#
#   为什么重排：论文自陈的限制是「**长上下文上的稀疏检索**可能退化」，而我们的调用
#   在 149,675 tokens/次 —— 正落在这个区间。所以**不能靠"多给"取胜，只能靠"给对位置"**。
#   重排前的顺序让 `lesions`（负向"别犯X"）落在最后一位，即**最高显著性位置**：
#   模型会被"别犯X"主导 → 写出**保守、安全、平**的文字，而"平"正是 AI 味的另一种形态
#   （推向统计安全区 = 合格的均匀）。现在把**正向的具体素材**放到最后。
SLOTS = [
    # ── 前段：硬约束与负向提示（必须在动笔前就被"看到"）──────────
    ('meta', '本章元信息', 60, True, 'script'),
    ('contract', '接口契约（冻结）', 420, True, 'script'),
    # ★ `lesions` 从"可选且永不产出"改为**必填**（bug 修复，见 CHANGELOG v6.6.0）：
    #   它此前 required=False + 赋空串 → render() 静默跳过 → 校验器也不报。
    #   而"已知病灶不传递给写手"= 同一类缺陷逐章复发，正是前移该消掉的东西。
    #   没有病灶时写「无」——**强制做出一次判断**，比静默缺失好。
    ('lesions', '本批已知病灶（占位式指令：这里要放什么）', 220, True, 'editor'),
    # ── 中段：参考性事实 ─────────────────────────────────────
    ('ledger', '状态台账·本章出场人物条目', 620, True, 'editor'),
    ('world_pack', '世界设定包（按本章裁剪）', 820, True, 'editor'),
    ('used_devices', '本批已用过的开场 / 章末型（避开它们）', 220, True, 'script'),
    # ⚠️ 预算必须给**段落粒度**留余量：`_tail` 是按段往回拼到 ≥800 字，
    #    多带一段就会溢出（实测 882 > 820）。这种"超预算"是**度量口径**问题，
    #    不是包写胖了 —— 放宽容量的正确做法，而不是放宽判据。
    ('prev_tail', '上一章真实结尾', 900, True, 'script'),
    ('diagnosis', '诊断句（仅重写任务）', 90, False, 'editor'),
    # ── 后段：给"该怎么写"的当前书事实（最高显著性）──────────────
    # ★ `rhythm_base`（2026-09-24 新增）：节律占质检调用约 26%，是第二大返工源，
    #   而任务包里此前**没有任何节律槽位**。按"**给事实不给目标**"给已写章节的实测值：
    #   ❌「本章段落 CV 请达到 0.85」（可凑，项目实测过配额当目标被 hack）
    #   ✅「本书前 N 章实测：CV 0.81–0.94，均值 0.87」（无目标可凑）
    ('rhythm_base', '本书已写章节的节律实测（事实，非目标）', 200, True, 'script'),
    # ★ `quota_card`（2026-09-24 新增）：`subagent-brief.md` L146 要求
    #   「**逐字复制** `quick-reference-card.md` 的【零】整块，不要手抄、不要改数字」，
    #   L428 还有勾选项「成本配额卡已内联进任务包」—— **但此前编译器根本不产出它，
    #   实测任务包里"配额卡"出现 0 次**。它治的恰恰是字数+节律+词汇这三类（≈91% 返工）。
    #   逐字复制交给脚本做，比"要求人别手抄"可靠。
    ('quota_card', '成本配额卡（逐字复制自 quick-reference-card 零节）',
     1400, True, 'script'),
    ('batch_global', '批次全局三件套（文风基因 / 锚点示例 / 句法基线）',
     900, True, 'script'),
    # ★ `voice_preset`（2026-09-24 新增）：文档要求"**动笔前**定的三行"
    #   （具体坐标／叙述者立场／留白点），且"没写这三行 = 写前分析未完成"——
    #   但它此前散落在细纲里、无槽位、无校验。实测真实项目 10 章里只有 3 章有这个段，
    #   且**被填成了别的东西**（只有破折号预算+专名锚点，三行核心从未被写过）。
    #   单一事实源 = 细纲（写手本来就读它），这里由脚本抽取进包，**不另存一份**（防双载体漂移）。
    ('voice_preset', '写前声音预置（具体坐标 / 叙述者立场 / 留白点）',
     300, True, 'script'),
    # ★ `author_shot`（2026-09-24 修复）：此前 required=False + 赋空串 → **永不产出**。
    #   而 `low-cost-mode.md` 把它列为**必留项**，定位是
    #   「**流水线上最保人味的素材——作者自己的眼睛**」。
    #   判据：它消除的缺陷（人味不足）**后期修要付多轮润色**，属于"后期修显著贵于前期"
    #   → 按这个判据它**必须是必填**，不该是"（若有）"。
    #   放在最后一位：它是**正向、具体、要照着写**的素材，给最高显著性。
    ('author_shot', '作者画面（脚手架原话）', 200, True, 'editor'),
]
# ⚠️ 从 5000 提到 6000 的理由（2026-09-24）：
#   新增的 `quota_card` 是**一次性固定成本**（全书同一张卡，不随章数增长），
#   而它压制的是占返工 ≈91% 的三类（字数 52% / 节律 26% / 词汇 13%）。
#   按量级算：多带 ≈1300 字 ≈ **0.9%** 的上下文体积（相对 149,675 tokens/次），
#   换掉的是**每一轮返工 149,675 tokens**。论文侧的顾虑（长上下文稀疏检索）在这个
#   增量下可忽略；**但这也意味着其余槽位必须继续收紧**，总量不再上调。
TOTAL_BUDGET = 6000
TODO = '【待填'
SLOT_OPEN = '<!-- slot:{key} budget={budget} required={req} -->'
SLOT_CLOSE = '<!-- /slot:{key} -->'
_PKG_CHAPTER = re.compile(r'第\s*(\d{1,4})\s*章')


def _zishu(s: str) -> int:
    """字数（CJK 逐字计，非 CJK 按 3.5 字符折 1 —— 与 token 估算口径一致）。"""
    cjk = len(re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', s))
    return int(cjk + (len(s) - cjk) / 3.5)


def _section(text: str, head_kw: str, sub_kws=(), max_level=3):
    """抠出一节内容：先找含 head_kw 的 2 级标题，再在其中挑含 sub_kws 的子节。

    ⚠️ 为什么不整段搬："批次全局信息"整节可能上千字，而一个子代理只需要
       **文风基因 + 锚点示例 + 句法基线**这三样 —— **只给该给的**。
    """
    m = re.search(r'(?m)^#{1,2}[^\n]*' + re.escape(head_kw) + r'[^\n]*$', text)
    if not m:
        return ''
    rest = text[m.end():]
    nxt = re.search(r'(?m)^#{1,2}\s', rest)
    sec = rest[:nxt.start()] if nxt else rest
    if not sub_kws:
        return sec.strip()
    out = []
    for sub in re.finditer(r'(?m)^#{3,4}\s*([^\n]+)$', sec):
        if not any(k in sub.group(1) for k in sub_kws):
            continue
        nxt2 = re.search(r'(?m)^#{3,4}\s', sec[sub.end():])
        body = sec[sub.end():sub.end() + (nxt2.start() if nxt2 else len(sec))]
        out.append(f'**{sub.group(1).strip()}**\n{body.strip()}')
    return '\n\n'.join(out)


def _rhythm_base(proj: Path, chapter_no: int) -> str:
    """本书已写章节的节律实测（**给事实不给目标**）。

    为什么形态必须是"事实"而不是"目标"：项目实测过"底线当目标、配额当目标"被 hack ——
    给一个数字，模型就会去凑那个数字。给"已经发生过什么"，**没有可凑的对象**。
    所以下面明确写出"偏离是允许的"。
    """
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from check_aistyle import analyze_chapter
    except Exception as e:                                  # noqa: BLE001
        return f'{TODO}：载入节律指标失败（{e}）】'
    rows = []
    for f in find_chapter_files(proj):
        m = re.search(r'第\s*(\d{1,4})\s*章', f.name)
        if not m:
            continue
        no = int(m.group(1))
        if no >= chapter_no:
            continue
        try:
            r = analyze_chapter(f)
        except Exception:                                   # noqa: BLE001
            r = None
        if r:
            rows.append((no, r))
    if not rows:
        return '（本书首章，尚无历史基线——本章是定调章；写完后它就成为后续章的基线）'

    def _span(key):
        vs = [float(r.get(key, 0) or 0) for _, r in rows]
        return min(vs), max(vs), statistics.median(vs)

    metrics = (('para_cv', '段落节律 CV', '{}'),
               ('dialog_ratio', '对话占比', '{}'),
               ('simile_density', '明喻/千字', '{}'),
               ('emotion_density', '情绪词/千字', '{}'))
    lines = [f'本书已写 {len(rows)} 章实测（**事实，不是目标；偏离允许——只要你清楚自己在偏**）：']
    for key, label, fmt in metrics:
        lo, hi, med = _span(key)
        lines.append(f'- {label} {fmt.format(round(lo, 2))}–{fmt.format(round(hi, 2))}'
                     f'｜均值 {fmt.format(round(med, 2))}'
                     f'｜第{rows[-1][0]}章 {fmt.format(float(rows[-1][1].get(key, 0) or 0))}')
    return '\n'.join(lines)


def _quota_card() -> str:
    """逐字抽取「成本配额卡」（`quick-reference-card.md` 的【零】节）。

    ⚠️ 为什么由脚本抽而不是让主编贴：`subagent-brief.md` 要求"**逐字复制……不要手抄、
       不要改数字**"—— 而"要求人不许手抄"是个靠自觉的约束；**交给脚本就不可能抄错**。
    """
    p = Path(__file__).parent.parent / 'references' / 'guides' / 'quick-reference-card.md'
    if not p.is_file():
        return f'{TODO}：找不到 references/guides/quick-reference-card.md】'
    text = read_text(p)
    sec = re.search(r'(?ms)^##\s*零、.*?(?=^##\s)', text)
    body = sec.group(0) if sec else text
    m = re.search(r'(?ms)^```.*?^```', body)
    if not m or '【一、' not in m.group(0):
        return f'{TODO}：速查卡【零】节里找不到配额卡代码块（四栏：补足/压住/区间/加法）】'
    return ('> 逐字复制自 `references/guides/quick-reference-card.md`【零】节（脚本抽取）。\n'
            '> **写的时候就满足，不要等事后来修。**\n\n' + m.group(0).strip())


VOICE_KEYS = ('具体坐标', '叙述者立场', '留白点')


def _voice_preset(ol_chaps: dict, chapter_no: int):
    """从细纲抽「写前声音预置」段。返回 (文本, 缺的键)。

    **单一事实源 = 细纲**（写手本来就读它）—— 这里只做抽取进包，不另存一份，
    避免"双载体同名必漂"（伏笔表踩过这个坑）。
    """
    seg = ol_chaps.get(chapter_no, '') or ''
    m = re.search(r'(?m)^#{2,4}\s*(?:写前)?声音预置[^\n]*$', seg)
    if not m:
        return (f'{TODO}：细纲第 {chapter_no} 章没有「写前声音预置」段'
                f'（三行：具体坐标 / 叙述者立场 / 留白点）】'), list(VOICE_KEYS)
    rest = seg[m.end():]
    nxt = re.search(r'(?m)^#{2,4}\s', rest)
    body = (rest[:nxt.start()] if nxt else rest).strip()
    if not body:
        return f'{TODO}：细纲的「写前声音预置」段是空的】', list(VOICE_KEYS)
    lack = [k for k in VOICE_KEYS if k not in body]
    return body, lack


def _word_target(proj: Path) -> str:
    for n in ('02-写作计划.json', '写作计划.json'):
        p = proj / n
        if p.is_file():
            try:
                d = json.loads(read_text(p))
                v = d.get('minWordsPerChapter') or d.get('wordsPerChapter')
                if v:
                    return f'目标 {v} 字'
            except Exception:
                pass
    return '目标字数：见 02-写作计划.json'


def build(proj: Path, chapter_no: int, rewrite: bool = False):
    """返回 {slot_key: 内容}；脚本填不了的槽位返回 TODO 标记。

    `rewrite=True` 时产出 `diagnosis` 槽位（改写任务必须带诊断句——没有它，
    改写等于**重抽一次彩票**）。
    """
    fills = {}

    files = [str(p) for p in find_chapter_files(proj)]
    outlines = find_outline_files(proj)
    ol_text = read_text(outlines[0]) if outlines else ''
    ol_chaps = split_chapters(ol_text) if ol_text else {}

    # ① 批次全局三件套（只抠该给的三样）
    g = _section(ol_text, '批次全局', ('文风基因', '锚点示例', '句法'))
    fills['batch_global'] = g or f'{TODO}：细纲里没有「批次全局信息」段（文风基因/锚点示例/句法基线）】'

    # ② 接口契约（本章）
    c = parse_contract(ol_chaps.get(chapter_no, ''))
    if c:
        keys = ['进入·位置时间', '进入·情绪', '进入·已知', '进入·身体',
                '退出·位置时间', '退出·情绪', '退出·已知', '退出·身体',
                '承接要素', '交付钩子']
        fills['contract'] = '\n'.join(
            f'- {k}: {c.get(k, "").strip() or "（空）"}' for k in keys)
    else:
        fills['contract'] = (f'{TODO}：细纲第 {chapter_no} 章没有「接口契约」块】\n'
                             f'格式见 phase2-planning.md 细纲规格；跑 '
                             f'`check_contract.py <项目>` 可查')

    # ②b 本批已用过的开场 / 章末型 —— **前馈：给事实，不给目标**
    #     「跨章同质」现在是"写完由 check_repetition 报警"，属于反馈（= 多一轮 × 15万 token）。
    #     把"已经用过什么"提前写进任务包，就从反馈变成了前馈，而成本只有几行字。
    #     ⚠️ 只陈述事实（"已用过甲、丙"），**不写"本章请用乙"** —— 后者会变成可凑的目标。
    def _clean(s: str) -> str:
        s = re.sub(r'\*+', '', s).strip()
        return re.split(r'[（(，,。；;]', s)[0].strip()[:16]

    used = []
    for k in sorted(n for n in ol_chaps if n < chapter_no):
        seg = ol_chaps[k]
        o = re.search(r'(?:开场(?:方式|类型)|本章开头方式|开头方式)\**\s*[:：]\s*([^\n|]{1,40})', seg)
        e = re.search(r'章末型[^\n:：]*[:：]\s*([^\n|]{1,40})', seg)
        bits = ([f'开场 {_clean(o.group(1))}'] if o else []) + \
               ([f'章末 {_clean(e.group(1))}'] if e else [])
        if bits:
            used.append(f'第{k}章：' + ' / '.join(bits))
    fills['used_devices'] = (
        ('本批（截至上一章）**已经用过**的手法——本章请避开同型：\n'
         + '\n'.join('- ' + u for u in used[-8:])
         + '\n\n（这是**已发生的事实**，不是指标；照抄或替换都不影响，只用于避开重复。）')
        if used else '（本批尚无已定稿章，无历史手法可避开）')

    # ③ 上一章真实结尾（从正文裁，与交接卡同源）    prev = ''
    idx = next((i for i, f in enumerate(files)
                if (m := _PKG_CHAPTER.search(Path(f).name))
                and int(m.group(1)) == chapter_no - 1), None)
    if idx is not None:
        try:
            prev = _tail(_paras(read_text(files[idx])), 800)
        except Exception:
            prev = ''
    fills['prev_tail'] = prev or ('（本书首章，无上章）' if chapter_no <= 1 else
                                  f'{TODO}：找不到第 {chapter_no - 1} 章正文】')

    # ④ 元信息
    fills['meta'] = (f'章号 {chapter_no}（{"奇数" if chapter_no % 2 else "偶数"}）｜'
                     f'{_word_target(proj)}')

    # ⑤ 需要理解才能产生的 —— 显式留空，**不假装脚本能写**
    fills['ledger'] = f'{TODO}：从 03-状态台账.md 抄本章出场人物条目（每条带三态 ✓/?/✗）】'
    fills['world_pack'] = f'{TODO}：按本章裁剪 500–800 字（读圣经「世界观手册」）】'
    # ★ 修复：这三个此前**被赋空串 + required=False**，于是 render() 静默跳过、
    #   check_one() 也静默通过 —— 实测生成物里它们出现 **0 次**。
    #   现在：`author_shot` 与 `lesions` 改为必填（给 TODO 标记，主编看得见、校验拦得住）；
    #   `diagnosis` 只在 `--rewrite` 时产出（新写任务里它本来就不适用）。
    fills['author_shot'] = (f'{TODO}：从 07-剧情脚手架.md 原样抄作者本人的原话画面】'
                            '（若本章脚手架确无条目，写「本章无脚手架条目」——'
                            '但**不要留空**：它是"最保人味的素材"）')
    fills['lesions'] = (f'{TODO}：本批真正出现过的 1–2 条病灶，**配占位式指令**'
                        '（"这里要放一个具体的东西"而不是"不要写泛泛的感慨"）；'
                        '没有则写「无」】')
    fills['diagnosis'] = (f'{TODO}：诊断：{{一句话}} / 本稿必须不再犯：{{一句话}}】'
                          if rewrite else '')
    # ★ 新增：脚本可自填的三项（0 reasoning token）
    fills['rhythm_base'] = _rhythm_base(proj, chapter_no)
    fills['quota_card'] = _quota_card()
    vp, lack = _voice_preset(ol_chaps, chapter_no)
    if lack and TODO not in vp:
        # 让问题在**编译时**就可见，而不是等 --check 才报
        vp += (f'\n\n> ⚠️ 缺三行核心：{"、".join(lack)}'
               f'——文档要求"没写这三行 = 写前分析未完成"。'
               f'**回到细纲补**（单一事实源在细纲，不在这里补）')
    fills['voice_preset'] = vp
    return fills


def render(proj: Path, chapter_no: int, fills: dict, rewrite: bool = False) -> str:
    parts = [f'# 第 {chapter_no} 章 · 任务包（由 make_task_package.py 编译）',
             '',
             f'> 本包由脚本编译：**脚本能拿到的已填好**，其余是**显式待填标记**。',
             f'> 任务类型：**{"改写（诊断句必填）" if rewrite else "新写"}**'
             f'｜预算上限：单槽位见标记，**总上限 {TOTAL_BUDGET} 字**。',
             f'> 校验：`python scripts/make_task_package.py "{proj}" '
             f'--check <本文件>`（缺槽位 / 超预算 / 待填残留 → 退出码 1，**拒绝开工**）',
             '',
             '> **槽位顺序是有意的**：开头放硬约束与"别犯什么"，'
             '结尾放**"该怎么写"的正向具体素材**（配额卡 / 文风基因 / 声音预置 / 作者画面）'
             '—— 长上下文上的检索是近似的，**不能靠"多给"取胜，只能靠"给对位置"**。',
             '']
    for key, title, budget, req, who in SLOTS:
        # ★ 不再"空则跳过"：**所有槽位一律产出**。
        #   旧写法 `if not body and not req: continue` 让 3 个槽位**永远不出现在包里**，
        #   而校验器只在 required 时报错 → 静默通过（实测生成物里出现 0 次）。
        #   现在缺内容就写一句显式的"不适用"，使「槽位数 == SLOTS 数」恒成立。
        body = (fills.get(key, '') or '').strip() or '（不适用——本任务无此项）'
        parts.append(SLOT_OPEN.format(key=key, budget=budget, req=int(req)))
        parts.append(f'## {title}')
        parts.append('')
        parts.append(body)
        parts.append('')
        parts.append(SLOT_CLOSE.format(key=key))
        parts.append('')
    return '\n'.join(parts)


def parse_package(text: str):
    """把包解析成 {key: (内容, 预算, 必填)}。返回 (slots, total_chars)。"""
    slots = {}
    for m in re.finditer(r'<!-- slot:([a-z_]+) budget=(\d+) required=(\d) -->', text):
        key, budget, req = m.group(1), int(m.group(2)), m.group(3) == '1'
        end = text.find(SLOT_CLOSE.format(key=key), m.end())
        body = text[m.end():end if end > 0 else len(text)]
        body = re.sub(r'(?m)^##[^\n]*$', '', body, count=1).strip()
        slots[key] = (body, budget, req)
    return slots


def check_one(path: Path, brief=False) -> int:
    if not path.is_file():
        print(f'  ✗ 任务包不存在：{path}')
        return 1
    text = read_text(path)
    slots = parse_package(text)
    rew = '任务类型：**改写' in text or '任务类型：改写' in text
    probs = []
    absent_opt = []
    total = 0
    for key, title, budget, req, who in SLOTS:
        # `diagnosis` 只在改写任务里必填（新写任务里它本来就不适用）
        need = req or (key == 'diagnosis' and rew)
        if key not in slots:
            if need:
                probs.append(f'缺必填槽位 `{key}`（{title}）—— **拒绝开工**：'
                             f'子代理拿不到它就只能猜')
            else:
                absent_opt.append(key)
            continue
        body, _b, r = slots[key]
        n = _zishu(body)
        total += n
        if need and (not body or TODO in body):
            probs.append(f'槽位 `{key}` 还是待填状态：{body[:60]}')
        if n > budget:
            probs.append(f'槽位 `{key}` 超预算：{n} > {budget} 字 —— '
                         f'"被乘数"就是这么涨起来的（实测每次调用平均重发 14.9 万 tokens）')
        # 声音预置：三行核心必须齐（文档要求"没写这三行 = 写前分析未完成"）
        if key == 'voice_preset' and TODO not in body:
            lack = [k for k in VOICE_KEYS if k not in body]
            if lack:
                probs.append(f'槽位 `voice_preset` 缺三行核心：{"、".join(lack)}'
                             f'（实测真实项目 10 章里只有 3 章有这个段，且全都缺这三行）')
    if total > TOTAL_BUDGET:
        probs.append(f'**总包超预算**：{total} > {TOTAL_BUDGET} 字')
    if not brief:
        print(f'\n  {path.name}')
        print(f'    槽位 {len(slots)}/{len(SLOTS)}｜合计 {total} 字（上限 {TOTAL_BUDGET}）')
        if absent_opt:
            print(f'    ⚠ 缺可选槽位（不阻塞，但请确认是不是漏了）：'
                  f'{"、".join(absent_opt)}')
    for p in probs:
        print('    ✗ ' + p)
    return len(probs)


def main():
    ap = argparse.ArgumentParser(description='任务包编译器（装配 + 预算 + 校验）')
    ap.add_argument('project', help='项目目录')
    ap.add_argument('--chapter', type=int, help='章号')
    ap.add_argument('--write', action='store_true', help='写到 任务包/第NN章-任务包.md')
    ap.add_argument('--check', nargs='?', const='__ALL__', default=None,
                    metavar='任务包.md',
                    help='校验一份任务包；或 --check --all 校验全部')
    ap.add_argument('--all', action='store_true', help='与 --check 连用：校验全部')
    ap.add_argument('--brief', action='store_true', help='只输出问题（窗闸/批闸用）')
    ap.add_argument('--rewrite', action='store_true',
                    help='本任务是**改写**（产出 diagnosis 槽位；改写不带诊断句 = 重抽彩票）')
    args = ap.parse_args()

    proj = Path(args.project)
    if not proj.is_dir():
        print(f'[错误] 目录不存在：{args.project}')
        sys.exit(2)

    if args.check is not None:
        targets = []
        if args.check == '__ALL__' or args.all:
            d = proj / PKG_DIR
            targets = sorted(d.glob('*.md')) if d.is_dir() else []
            if not targets:
                print(f'[错误] {proj / PKG_DIR} 下没有任务包。')
                print('       这不等于"通过"——先跑 --write 生成，再校验。')
                sys.exit(2)
        else:
            targets = [Path(args.check)]
        print('=' * 60)
        print('任务包校验（缺槽位 = 拒绝开工；超预算 = "被乘数"失控）')
        print('=' * 60)
        bad = sum(check_one(t, args.brief) for t in targets)
        if args.brief:
            print(f'\n[低费用·批闸] {len(targets)} 个任务包｜问题 {bad}'
                  + (' → 合格' if not bad else ' → 不合格'))
        elif not bad:
            print(f'\n  ✓ {len(targets)} 个任务包齐备且未超预算')
        sys.exit(1 if bad else 0)

    if not args.chapter:
        ap.error('需要 --chapter N，或 --check')
    fills = build(proj, args.chapter, rewrite=args.rewrite)
    text = render(proj, args.chapter, fills, rewrite=args.rewrite)
    if args.write:
        out = proj / PKG_DIR
        out.mkdir(parents=True, exist_ok=True)
        p = out / f'第{args.chapter:02d}章-任务包.md'
        p.write_text(text, encoding='utf-8')
        n = _zishu(text)
        print(f'  ✓ {p.relative_to(proj)}  （{n} 字 / 上限 {TOTAL_BUDGET}）')
        todo = sum(1 for k, _t, _b, r, w in SLOTS if r and TODO in fills.get(k, ''))
        print(f'    待主编填：{todo} 个必填槽位 '
              f'（`--check` 会拦住未填的包，不会让它混到子代理手里）')
    else:
        print(text)
    sys.exit(0)


if __name__ == '__main__':
    main()
