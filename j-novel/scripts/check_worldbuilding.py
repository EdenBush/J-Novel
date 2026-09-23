#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""世界设定体检（Worldbuilding Audit）
=====================================

**为什么需要这个脚本**（2026-09-20 真实事故）：

真实项目的世界观手册只有 **1236 字**（对比同项目细纲 9028 字、大纲 7433 字），
而 `00-故事圣经.md` 在整个 SKILL 里**只有一处提及**（项目结构树），**创作期零读点**。
于是落笔时手里没有"世界"这个维度 → 补出的是"一个村子"，不是"灰炉村"；
每个子代理补得还不一样。

本脚本把"世界观有没有被吃掉"变成可数的三个量：

  1. **手册密度** —— 手册字数 / 专有名词表条目 / 场景卡 是否够撑起篇幅
  2. **设定激活率** —— 手册里写下的实体，正文里用到了多少
     （**这是"被吃掉"的直接度量**：写进表里却从没出现在正文 = 白写了）
  3. **未登记新词** —— 正文里出现、手册里没有的疑似专名
     （不回流登记 → 下一个写手不知道 → 两章之后世界分裂）

用法:
    python check_worldbuilding.py <项目目录>
    python check_worldbuilding.py <项目目录> --json

退出码: 0=健康 / 1=有问题 / 2=找不到设定文件（fail-closed，不报"通过"）
"""
import argparse
import io
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import ensure_utf8_stdio      # noqa: E402

ensure_utf8_stdio()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import read_text, find_chapter_files  # noqa: E402

CJK = re.compile(r'[\u4e00-\u9fff]')

# ── 实体识别 ──────────────────────────────────────────────────
# ⚠️ 两档口径，**不能混用**（2026-09-20 实测调出来的）：
#
#   loose（用于**手册侧**）：后缀推断 + 加粗 + 引号。宁多勿少——
#     手册里多抓几个假实体，代价是"多列几条让人工看一眼"；
#     漏抓真实体，代价是"激活率虚高、问题被藏起来"。
#
#   strict（用于**正文侧**）：**只认加粗/引号/《》【】，不做后缀推断**。
#     初版对正文也用 loose，结果"未登记新词"报出 126 个，
#     里面全是"一个挥剑""一楼""举到眼""中午他在村"这类切片噪声——
#     真信号被噪声淹掉，报告就没人看了。**正文侧的实体名以「专有名词表」为准；
#     没有表时，只用高置信度来源。**
#
# 前缀断言 `(?<![\u4e00-\u9fff])` 是必需的：否则 "的灰炉村" / "省灰炉村"
# 会被当成两个不同实体（初版就是这么把 21 个真实体灌成 347 个噪声的）。
_ENT_SUFFIX = (
    # 地名
    '村|城|镇|塔|矿脉|矿洞|矿山|矿道|窟|洞|井|湖|谷|殿|神庙|祭坛|关口|'
    # 组织（**用双字**，否则"不会/学会/聚会"全被抓）
    '商会|公会|帮会|联盟|门派|宗门|军团|战队|协会|互娱|公司|集团|'
    # 物品/材料（**用双字**，否则"一把剑/三刀/举到眼"全被抓）
    '铁矿|矿石|晶石|血脉|谱系|功法|法器|丹药|'
    # 单字后缀：只留"几乎不会出现在普通词尾"的
    '矿|脉|窟|坊|观'
)
_ENT_RX = re.compile(r'(?<![\u4e00-\u9fff])([\u4e00-\u9fff]{1,4}(?:' + _ENT_SUFFIX + r'))')
_BRACKET_RX = re.compile(r'【([^】]{2,10})】|《([^》]{2,14})》|「([^」]{2,14})」')
_BOLD_RX = re.compile(r'\*\*([\u4e00-\u9fff]{2,6})\*\*')

# 明显不是"世界实体"的通用词（防噪声）
_STOP = set('一个 这个 那个 什么 时候 地方 东西 事情 问题 办法 之后 之前 现在 当时 已经 这样 那样 '
            '不能 可以 应该 因为 所以 但是 如果 虽然 而且 就是 还是 或者 只是 一样 一起 '
            '第一 第二 第三 最后 开始 结束 出现 发现 知道 觉得 认为 看到 听到 说道 问道'.split())

# 加粗词里混着大量"字段标签"（**视角透气规划** / **破折号预算** …），不是实体。
_LABEL_RX = re.compile(
    r'(规划|预算|机制|系统|规则|原则|标准|边界|代价|目标|手段|冲突|分层|视角|结构|'
    r'节奏|风格|基调|动机|缺陷|弧光|关系|背景|设定|清单|表格|字段|建议|要点|'
    r'阶段|层次|维度|要素|步骤|方法|说明|备注|概述|总览|分析|核心|关键|重点|'
    r'记录|警告|注意|提示|纪律|铁律|配额|指标|门槛|上限|下限|比例|密度|范围)$')

# 单字后缀的**动词前缀负面表**
_VERB_PREFIX = set('挥 拔 收 出 砍 刺 举 抬 拿 握 递 递 断 折 卷 磨 打 造 修 买 卖 换 '
                   '下 上 回 进 出 来 去 走 跑 站 坐 躺 看 听 说 问 答 想 记 忘'.split())

SETTING_FILES = ('00-故事圣经.md', '00-世界设定.md', '00-世界书.md')
MANUAL_HEADS = ('世界观手册', '世界观', '世界设定', '世界书')
# 「核心机制／力量体系」这类小节**也是世界设定**，而且往往是全书最硬的部分。
# 实测（2026-09-20）：某项目的【解构之眼】写在「三、核心机制」而非「一、世界观手册」，
# 只看手册节会把它误报成"未登记新词"。**世界层 = 手册 + 核心机制。**
MECHANIC_HEADS = ('核心机制', '力量体系', '体系设定', '机制设定', '规则体系')
GLOSSARY_HEADS = ('专有名词表', '专有名词', '名词表', '术语表', '词表')

# 除圣经外，哪些文件也算"已登记设定"（用于**防误报**）
OTHER_SETTING_FILES = ('00-人物档案.md', '01-大纲.md')
SETTING_DIRS = ('细纲', '大纲', '设定', '世界设定')


def _read(p):
    return read_text(p)


def find_setting(proj: Path):
    """找设定文件。返回 (path, text) 或 (None, '')。"""
    for name in SETTING_FILES:
        f = proj / name
        if f.is_file():
            return f, _read(f)
    # 兜底：任意含"圣经/世界"的 md
    for f in sorted(proj.glob('*.md')):
        if any(k in f.name for k in ('圣经', '世界')):
            return f, _read(f)
    return None, ''


def slice_section(text: str, heads) -> str:
    """切出标题含 `heads` 任一词的小节（到下一个同级或更高级标题为止）。"""
    for head in heads:
        m = re.search(r'(?m)^(#{1,4})\s*[^\n]*' + re.escape(head) + r'[^\n]*$', text)
        if not m:
            continue
        lvl = len(m.group(1))
        rest = text[m.end():]
        nxt = re.search(r'(?m)^#{1,' + str(lvl) + r'}\s', rest)
        return rest[:nxt.start()] if nxt else rest
    return ''


def slice_world(text: str) -> str:
    """**世界层** = 世界观手册 + 核心机制（激活率的分母取这一层）。"""
    return slice_section(text, MANUAL_HEADS) + '\n' + slice_section(text, MECHANIC_HEADS)


def collect_all_settings(proj: Path) -> str:
    """全部设定文本（圣经 + 人物档案 + 大纲 + 细纲）——用于**防误报**：
    一个词只要在任何设定文件里登记过，就不该算"未登记新词"。"""
    buf = []
    for name in OTHER_SETTING_FILES:
        f = proj / name
        if f.is_file():
            buf.append(_read(f))
    for d in SETTING_DIRS:
        dd = proj / d
        if dd.is_dir():
            for f in sorted(dd.glob('*.md')):
                buf.append(_read(f))
    return '\n'.join(buf)


def slice_manual(text: str) -> str:
    """「世界观手册」这一节（**密度判据只针对它**——它是"世界"的家）。"""
    return slice_section(text, MANUAL_HEADS)


def slice_glossary(text: str):
    """切出「专有名词表」并解析成表格行。返回 (section_text, [names])。"""
    for head in GLOSSARY_HEADS:
        m = re.search(r'(?m)^(#{1,4})\s*[^\n]*' + re.escape(head) + r'[^\n]*$', text)
        if not m:
            continue
        lvl = len(m.group(1))
        rest = text[m.end():]
        nxt = re.search(r'(?m)^#{1,' + str(lvl) + r'}\s', rest)
        sec = rest[:nxt.start()] if nxt else rest
        names = []
        for line in sec.split('\n'):
            line = line.strip()
            if not line.startswith('|') or set(line) <= set('|-: '):
                continue
            cells = [c.strip().strip('*').strip() for c in line.strip('|').split('|')]
            if len(cells) < 2:
                continue
            if cells[0] in ('类别', '类型') or cells[1] in ('名称', '专名'):
                continue
            name = cells[1] if len(cells) > 1 else cells[0]
            if name and CJK.search(name) and 2 <= len(name) <= 10:
                names.append(name)
        if names or sec.strip():
            return sec, names
    return '', []


def entities(text: str, loose: bool = True):
    """从一段文本提取"疑似世界实体"。

    `loose=True`（手册侧）：加后缀推断。宁多勿少。
    `loose=False`（正文侧）：只认高置信度来源（加粗 / 引号 / 《》【】）。
    """
    out = set()
    if loose:
        for m in _ENT_RX.finditer(text):
            w = m.group(1)
            if len(w) < 2 or w in _STOP or _LABEL_RX.search(w):
                continue
            # 含虚词的一定是短语切片，不是实体名（"被城市拒绝"→"被城"、
            # "低阶矿产"→"低阶矿"、"署名归公会"）。真实体名几乎不含这些字。
            if re.search(r'[的|被|给|把|和|与|或|归|让|使|是|在|从|到|了|这|那|每|各|其|之]', w):
                continue
            # 单字后缀的动词前缀（"挥剑"这种）挡掉
            if len(w) == 2 and w[0] in _VERB_PREFIX:
                continue
            out.add(w)
    for m in _BRACKET_RX.finditer(text):
        w = next(g for g in m.groups() if g)
        if CJK.search(w) and len(w) >= 2 and not _LABEL_RX.search(w):
            out.add(w)
    for m in _BOLD_RX.finditer(text):
        w = m.group(1)
        if w not in _STOP and not _LABEL_RX.search(w):
            out.add(w)
    return out


def chapters(proj: Path):
    files = find_chapter_files(proj)
    return [(f, _read(f)) for f in files if f.suffix == '.md']


# 密度判据：按章数分档
def density_rule(n_chapters: int):
    if n_chapters < 10:
        return dict(manual=3000, glossary=25, scene=4, faction=2, tier='短篇（<10 章）')
    if n_chapters <= 40:
        return dict(manual=5000, glossary=40, scene=8, faction=4, tier='中篇（10–40 章）')
    return dict(manual=8000, glossary=60, scene=15, faction=6, tier='长篇（40+ 章）')


def main():
    ap = argparse.ArgumentParser(description='世界设定体检')
    ap.add_argument('project', help='项目目录（含 00-故事圣经.md 与 chapters/）')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    args = ap.parse_args()

    proj = Path(args.project)
    if not proj.is_dir():
        print(f'错误: 目录不存在 - {proj}')
        return 2

    bible_path, bible = find_setting(proj)
    if not bible_path:
        print('错误: 找不到设定文件（00-故事圣经.md / 00-世界设定.md / 00-世界书.md）')
        print('  ⚠ 这不是"通过"——说明脚本没看见你的设定。先确认文件在哪。')
        return 2

    chs = chapters(proj)
    manual_sec = slice_manual(bible)          # 密度判据只看这一节
    world_sec = slice_world(bible)            # 激活率的分母取"世界层"（手册 + 核心机制）
    gloss_sec, gloss_names = slice_glossary(bible)

    rule = density_rule(max(1, len(chs)))
    manual_chars = len(CJK.findall(manual_sec))

    # ── 设定侧实体 ──
    # 实体来源优先专有名词表；没有表就从**世界层**（手册 + 核心机制）推断。
    # ⚠️ 只用「世界观手册」是错的：实测某项目把【解构之眼】写在「三、核心机制」，
    #    只看手册节会把它误报成"未登记新词"。
    if gloss_names:
        bible_ents = set(gloss_names)
        ent_source = '专有名词表'
    else:
        bible_ents = entities(world_sec, loose=True)
        ent_source = '（**无专有名词表**，从「手册＋核心机制」推断 —— 补表后评估会精确得多）'

    body_all = '\n'.join(t for _, t in chs)
    # 正文侧用 strict（只认加粗/引号/《》【】）：正文实体名**以专有名词表为准**，
    # 没有表时对正文做后缀推断会灌进"一个挥剑/一楼/举到眼"这类切片噪声，
    # 真信号被淹掉之后报告就没人看了。
    body_ents = entities(body_all, loose=False)

    hit = {e for e in bible_ents if e in body_all}
    miss = sorted(bible_ents - hit, key=lambda x: (-len(x), x))
    activation = (100 * len(hit) / len(bible_ents)) if bible_ents else 0

    # 「未登记」的对照集 = **全部设定文件**（圣经 + 人物档案 + 大纲 + 细纲）。
    # 一个词只要在任何设定文件里登记过就不算未登记——否则细纲里写过的词
    # 会被反复误报，报告就没法看了。
    setting_all_ents = entities(bible, loose=True) | entities(collect_all_settings(proj), loose=True)
    unregistered = sorted(e for e in body_ents if e not in setting_all_ents)

    # 场景卡 / 势力卡计数（粗判：手册里的 ### 级小标题数）
    scene_cards = len(re.findall(r'(?m)^#{3,4}\s', manual_sec))

    problems = []
    if not manual_sec.strip():
        problems.append('找不到「世界观手册/世界设定」小节——设定没有落点')
    if manual_chars < rule['manual']:
        problems.append(f'手册 {manual_chars} 字 < 判据 {rule["manual"]} 字（{rule["tier"]}）'
                        f' —— 差 {rule["manual"] - manual_chars} 字')
    if not gloss_names:
        problems.append('没有「专有名词表」——子代理必然各造一套词，世界会分裂')
    elif len(gloss_names) < rule['glossary']:
        problems.append(f'专有名词表 {len(gloss_names)} 条 < 判据 {rule["glossary"]} 条')
    if bible_ents and activation < 60:
        problems.append(f'设定激活率 {activation:.0f}% < 60% —— '
                        f'{len(miss)} 个设定写进设定却从没在正文出现过')
    if len(unregistered) > 8:
        problems.append(f'未登记新词 {len(unregistered)} 个 —— '
                        f'正文在造词，但没回流进「专有名词表」')

    if args.json:
        print(json.dumps({
            'project': proj.name, 'chapters': len(chs),
            'manual_chars': manual_chars, 'rule': rule,
            'glossary': len(gloss_names), 'entity_source': ent_source,
            'bible_entities': len(bible_ents), 'body_entities': len(body_ents),
            'activation_pct': round(activation, 1),
            'missed': miss[:50], 'unregistered': unregistered[:50],
            'problems': problems,
        }, ensure_ascii=False, indent=2))
        return 1 if problems else 0

    print('=' * 66)
    print(f'世界设定体检：{proj.name}')
    print('=' * 66)
    print(f'设定文件：{bible_path.name} ｜ 已完成正文：{len(chs)} 章 ｜ 分档：{rule["tier"]}')
    print()

    print('【1】手册密度')
    def _mark(v, need):
        return '✓' if v >= need else '✗'
    print(f'   世界观手册        {manual_chars:>6} 字   判据 ≥{rule["manual"]:<6} {_mark(manual_chars, rule["manual"])}')
    print(f'   专有名词表        {len(gloss_names):>6} 条   判据 ≥{rule["glossary"]:<6} '
          f'{_mark(len(gloss_names), rule["glossary"]) if gloss_names else "✗ 表不存在"}')
    print(f'   场景/小节卡       {scene_cards:>6} 个   判据 ≥{rule["scene"]:<6} {_mark(scene_cards, rule["scene"])}')
    print()

    print('【2】设定激活率（世界层设定 → 正文用到了多少）')
    print(f'   实体来源          {ent_source}')
    print(f'   世界层实体        {len(bible_ents):>6} 个')
    print(f'   正文命中          {len(hit):>6} 个')
    print(f'   设定激活率        {activation:>6.0f}%   建议 ≥60%   {_mark(activation, 60)}')
    if miss:
        print()
        print(f'   ⚠ 以下 {len(miss)} 个设定**写进了手册，但正文一次都没用过**：')
        for i in range(0, min(len(miss), 24), 6):
            print('      ' + '、'.join(miss[i:i + 6]))
        if len(miss) > 24:
            print(f'      …另有 {len(miss) - 24} 个')
        print('      → 逐条判断：① 计划后期才用（在表里标「首次出现：第X章」）')
        print('                   ② 该用了没用（补进正文/细纲）③ 用不上（从手册删掉）')
    print()

    print('【3】未登记新词（正文里有、手册里没有的疑似专名）')
    if unregistered:
        print(f'   共 {len(unregistered)} 个：')
        for i in range(0, min(len(unregistered), 30), 6):
            print('      ' + '、'.join(unregistered[i:i + 6]))
        if len(unregistered) > 30:
            print(f'      …另有 {len(unregistered) - 30} 个')
        print('   → 人工确认后登记回「专有名词表」（含首次出现章号）。')
        print('     **不回流 = 下一个写手不知道这个词 → 两章之后世界分裂。**')
    else:
        print('   ✓ 无')
    print()

    if problems:
        print('=' * 66)
        print(f'✗ {len(problems)} 项问题：')
        for p in problems:
            print(f'  • {p}')
        print()
        print('  修复指引：references/guides/bible-template.md「一、世界观手册」')
        print('            （写法铁律 / 9 项骨架 / 密度判据 / 类型专属补充 / 反模式）')
        print('=' * 66)
        return 1

    print('✓ 世界设定体检通过（密度达标 / 激活率达标 / 无大量未登记新词）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
