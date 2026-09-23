#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节重复检测脚本
检测两类"重复"：
  1. **章内重复**（默认）：整段复制粘贴是 AI 生成的典型硬伤。
     - 精确重复：归一化后完全相同的段落（长度 > 20 字才报告，避免短句误报）
     - 近似重复：相似度 ≥ 0.9 的段落对
  2. **跨章开场同质**（`--all` 时）：连续 ≥3 章以「时间/环境空镜」或「对话」起手。
     这是批量写作特有的同构——**单章看不出来，连写才暴露**。
     事故来源：`rewrite-playbook` ② 招「时间/地点开头」×`chapter-craft`「日常进入型」两条规则叠加。

用法:
  python check_repetition.py <章节文件.md>        # 检测单章
  python check_repetition.py --all <项目目录>     # 检测全部章节 + 跨章开场/章末同质
  python check_repetition.py --all <项目目录> --window 3 --brief
                                                  # **窗闸**：流水线每章跑一次（滚动窗口）
退出码: 0 = 干净, 1 = 有重复或跨章同质, 2 = 没扫到章节（fail-closed，不是"通过"）
"""

import argparse
import contextlib
import io
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import ensure_utf8_stdio      # noqa: E402

ensure_utf8_stdio()

MIN_DUP_LEN = 15       # 段落归一化后至少 15 字才参与重复判定（避免"他说。"类短句误报）
SIM_THRESHOLD = 0.9    # 近似重复相似度阈值


def extract_body(text: str) -> str:
    """提取正文 —— **委托 `_shared.extract_body`**（2026-09-21 收归）。

    ⚠ 此前这里是**第五份**独立实现，而且口径明显偏窄：只剥
    `## 本章概要 / ## 章节备注 / ## 章节概要` 三种，
    而 `_shared` 那份还剥 伏笔标记 / AI 味自评 / 人味配额 / 叙述者插话 /
    质量评分 / 衔接检查 / 逻辑检查 / 专项检查 / 注册审计 / 本章质检摘要 / 成本记录。
    后果：正文文件里只要混了「## 伏笔标记」这类块，就会被当成正文参与重复判定
    → 同一份稿子在重复脚本和节律脚本里分母不同，两边结论打架。
    全 SKILL 只允许有一份 extract_body。
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from _shared import extract_body as _shared_extract_body
    return _shared_extract_body(text)


def read_text(p) -> str:
    """读文件 —— 同样走 `_shared`（编码口径必须一致）。"""
    sys.path.insert(0, str(Path(__file__).parent))
    from _shared import read_text as _r
    return _r(p)


def normalize(s: str) -> str:
    """归一化：去空白和标点，只留汉字/字母/数字，便于比较"""
    return re.sub(r'[\s\u3000\W_]+', '', s)


def split_paragraphs(body: str) -> list:
    """按空行切分段落，返回 [(原段落, 归一化文本)]"""
    paras = []
    for block in re.split(r'\n\s*\n', body):
        block = block.strip()
        norm = normalize(block)
        if len(norm) >= MIN_DUP_LEN:
            paras.append((block, norm))
    return paras


def find_duplicates(file_path: Path) -> list:
    """检测单章重复，返回 [(段A原文, 段B原文, 类型)]"""
    try:
        text = file_path.read_text(encoding='utf-8')
    except Exception as e:
        print(f'[错误] 读取失败 {file_path.name}: {e}')
        return []

    body = extract_body(text)
    paras = split_paragraphs(body)
    dupes = []
    n = len(paras)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = paras[i][1], paras[j][1]
            if a == b:
                dupes.append((paras[i][0], paras[j][0], '精确重复'))
            elif len(a) > 30 and len(b) > 30:
                r = SequenceMatcher(None, a, b).ratio()
                if r >= SIM_THRESHOLD:
                    dupes.append((paras[i][0], paras[j][0], f'近似重复 {r:.0%}'))
    return dupes


def check_file(file_path: Path) -> int:
    dupes = find_duplicates(file_path)
    if not dupes:
        print(f'[通过] {file_path.name}：无重复段落')
        return 0
    print(f'[失败] {file_path.name}：发现 {len(dupes)} 处重复')
    for k, (a, b, kind) in enumerate(dupes, 1):
        print(f'  ── 第 {k} 处（{kind}）──')
        print(f'  A：{a[:60]}{"…" if len(a) > 60 else ""}')
        print(f'  B：{b[:60]}{"…" if len(b) > 60 else ""}')
    return 1


# ══════════════════════════════════════════════════════════════════
# 跨章「开场同质」检测（2026-09-21 新增）
# ══════════════════════════════════════════════════════════════════
# 事故：作者发现"**三到五章的开头都变成了同一套「时间＋环境空镜」**"。
#
# 根因是两条规则叠加、互相放大：
#   ① `rewrite-playbook.md` 第 1 节 ② 招「时间/地点开头」
#      —— 它本是**降「句首代词占比」的手段**，门槛最低，模型会本能多用；
#      而它的两条真人范式本身就是"地点开头""时间开头"，进一步强化倾向。
#   ② `chapter-craft.md`「日常进入型」（日常章默认用它）
#      —— 示范恰好是"早上五点，港城的天还是蒙蒙亮…""克洛废品站，垃圾堆成小山…"。
#   两条叠加 → 每章都"先交代时间地点、再写环境" → 连写几章就是同一个模子。
#
# **单章看不出同构，连写才暴露**——所以只能在 --all（读全章）时查。
_TIME_OPEN = ('早上', '清晨', '早晨', '一大早', '天亮', '天刚亮', '夜里', '晚上', '傍晚',
              '午后', '下午', '上午', '中午', '深夜', '凌晨', '入夜', '天黑',
              '第二天', '隔天', '次日', '当天', '那天', '这天', '这天夜里',
              '三天后', '两天后', '一天后', '半小时后', '一小时后', '许久', '半晌')
_ENV_OPEN = ('天色', '天光', '阳光', '月光', '星光', '风雨', '风从', '风里', '风刮',
             '雨', '雪', '雾', '云', '空气', '窗外', '远处', '街上', '路上', '院里',
             '村里', '村子', '天空', '天边', '光线', '热气', '寒气', '潮气', '尘',
             '阳光', '影子', '光线', '声音从')
_QUOTE_OPEN = ('"', '"', '"', '「', '『', "'", "'")

# 「空镜起手」= 时间或环境开头——本次事故的直接形态
_EMPTY_SHOT = ('时间', '环境')


def classify_opening(body: str):
    """判断章节开头的「入口类型」。返回 (类别, 开头 40 字)。"""
    h = extract_body(body).strip()
    h = re.sub(r'(?m)^#\s.*$', '', h).strip()      # 去标题行
    h = re.sub(r'\s+', ' ', h)
    head = h[:40]
    probe = head[:14]

    if head[:1] in _QUOTE_OPEN:
        return '对话', head
    for w in _TIME_OPEN:
        if w in probe:
            return '时间', head
    for w in _ENV_OPEN:
        if w in probe:
            return '环境', head
    if head[:1] in ('他', '她', '我', '它'):
        return '人物', head
    return '其他', head


def check_openings(files, brief: bool = False) -> int:
    """跨章开场同质检测。返回问题数。"""
    rows = []
    for f in files:
        try:
            body = read_text(f)
        except Exception:
            continue
        cat, head = classify_opening(body)
        rows.append((Path(f).name, cat, head))

    if len(rows) < 3:
        return 0

    print('\n' + '=' * 60)
    print('跨章「开场同质」检测（连写才暴露，单章看不出来）')
    print('=' * 60)
    # brief（窗闸模式）下不打逐章明细：每章跑一次时，
    # 明细会随章数线性变长 → 每次重读一遍 = 把省下的上下文又花回去。
    if not brief:
        for name, cat, head in rows:
            mark = '★' if cat in _EMPTY_SHOT else ' '
            print(f'  {mark} {name[:22]:<24} [{cat}] {head[:26]}')

    problems = 0
    i = 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1][1] == rows[i][1]:
            j += 1
        run = rows[i:j + 1]
        # 只对「空镜起手」+「对话」判定：连续 ≥3 章同一入口是结构级同构
        if len(run) >= 3 and run[0][1] in _EMPTY_SHOT + ('对话',):
            problems += 1
            kind = ('时间/环境空镜' if run[0][1] in _EMPTY_SHOT else run[0][1])
            print(f'\n  ✗ 连续 {len(run)} 章以「{kind}」起手：'
                  + '、'.join(f'第{r[0].split("-")[0].replace("第", "")}' for r in run))
            if run[0][1] in _EMPTY_SHOT:
                print('     → 这是"三到五章开头同一套时间+环境空镜"的直接形态。')
                print('     → 根因通常是两条规则被同时过度执行：')
                print('        · rewrite-playbook ②「时间/地点开头」（它只是降句首代词的手段，不是文风）')
                print('        · chapter-craft「日常进入型」（示范恰好是"早上五点，天蒙蒙亮…"）')
                print('     → 改法：**换入口**——人物动作 / 对话先行 / 一件具体的物 /')
                print('        他人的反应 / 一个没做完的动作 / 直接从中段进场。')
                print('        入口应该是**人物**，不是天气。')
            else:
                print('     → 连续多章都用对话开场，也会被读者看出是同一个模子——轮换入口。')
        i = j + 1
    if problems == 0:
        print('\n  ✓ 未检出跨章开场同质')
    return problems


# ══════════════════════════════════════════════════════════════════
# 跨章「章末同质」检测（2026-09-21 新增）
# ══════════════════════════════════════════════════════════════════
# 事故：作者报告"**不止开头，结尾好像也是僵硬公式化的**"。实测某项目 5 章结尾：
#   1 站在那道光里，他头一回觉得这个游戏有点意思。   ← 叙述·心境
#   2 证据。                                        ← 叙述·单句段
#   3 **反正**明天还得去挨打。                       ← 叙述·反正+自嘲
#   4 **反正**他自己知道就够了。                     ← 叙述·反正+自嘲
#   5 两息之后，低头继续拆。                         ← 叙述·动作
#
# **问题不在"型"，在"落笔"**：五章全部落在"叙述一句 + 留点余味"上，
# 四型里"对话收尾／面板收尾／吐槽收尾"一次都没出现。
# 而 `check_batch_gate` 的红线只查大纲的「章末型」**字段**（标签）——
# **标签轮换了，手感没换，闸门看不出来。**
#
# 判据分两级（保守，避免误报）：
#   · **失败**：**相邻两章以同一个词起句收尾**（"反正…"接着"反正…"）
#     —— 这是最硬的信号：人类作者不会连写两章同一个自嘲句式。
#   · **提示**：同一收束词全书 ≥3 次；连续 ≥5 章无对话/面板收尾。
_ENDING_OPEN_STOP = set('他 她 我 它 你 这 那 有 是 在 就 都 也 又 而 但 却 可 要 会 能 把 被 让 从'.split())


def _ending_head(last_para: str) -> str:
    """取章末段的前 2 字作为「收束起始词」（用于检测相邻章同构）。"""
    h = re.sub(r'^[\s　]+', '', last_para)
    if h[:1] and h[:1] in _QUOTE_OPEN:
        return '【对话】'
    w = h[:2]
    if len(w) < 2 or w[0] in _ENDING_OPEN_STOP or not re.match(r'^[\u4e00-\u9fff]{2}$', w):
        return ''
    return w


def _classify_ending(body: str):
    """判断章末落笔形态。返回 (形态, 末段前 34 字)。"""
    paras = [p.strip() for p in extract_body(body).split('\n') if p.strip()]
    if not paras:
        return '?', ''
    last = paras[-1]
    head = last[:34]
    if last[:1] and last[:1] in _QUOTE_OPEN:
        return '对话', head
    if re.search(r'[【\[][^】\]]{2,}[】\]]', last) or \
       re.search(r'(已收到|恭喜|获得|解锁|等级|属性)', last):
        return '面板·系统', head
    zh = len(re.findall(r'[\u4e00-\u9fff]', last))
    if zh <= 14:
        return '叙述·短句段', head
    return '叙述·长句', head


def check_endings(files, brief: bool = False) -> int:
    """跨章章末同质检测。返回问题数。"""
    from collections import Counter
    rows = []
    for f in files:
        try:
            body = read_text(f)
        except Exception:
            continue
        paras = [p.strip() for p in extract_body(body).split('\n') if p.strip()]
        last = paras[-1] if paras else ''
        cat, head = _classify_ending(body)
        rows.append((Path(f).name, cat, head, _ending_head(last)))

    if len(rows) < 3:
        return 0

    print('\n' + '=' * 60)
    print('跨章「章末同质」检测（选对"型"≠写对"落笔"）')
    print('=' * 60)
    if not brief:
        for name, cat, head, _ in rows:
            print(f'   {name[:22]:<24} [{cat}] {head[:26]}')

    problems = 0
    # ① 失败级：相邻两章同一个收束起始词
    pairs = []
    for i in range(len(rows) - 1):
        a, b = rows[i], rows[i + 1]
        if a[3] and a[3] == b[3] and a[3] != '【对话】':
            pairs.append((a[0], b[0], a[3]))
    for a, b, w in pairs:
        problems += 1
        print(f'\n  ✗ 相邻两章都以「{w}」起句收尾：'
              f'{a.split("-")[0]}、{b.split("-")[0]}')
    if pairs:
        print('     → 这是**章末公式化**最硬的信号：同一个收束词连用两章，'
              '读者立刻会感到"又是这一套"。')
        print('     → 改法：换落笔形态，别在**收束句**上复用同一个词。')
        print('        可选：对话收尾／面板·文件·消息／一个没做完的动作／'
              '一件物品的特写／时间·地点的客观推进／他人的反应。')

    # ② 提示级：全书同一收束词 ≥3 次
    cnt = Counter(r[3] for r in rows if r[3] and r[3] != '【对话】')
    hot = [(w, n) for w, n in cnt.items() if n >= 3]
    if hot and not pairs:
        print('\n  ⚠ 收束词复用：' + '、'.join(f'{w}×{n}' for w, n in hot) +
              ' —— 虽然不相邻，同一个词反复收尾仍会显公式。')

    # ③ 提示级：连续 ≥5 章无对话/面板收尾
    run, worst = 0, 0
    for _, cat, _, _ in rows:
        if cat.startswith('叙述'):
            run += 1
            worst = max(worst, run)
        else:
            run = 0
    if worst >= 5:
        print(f'\n  ⚠ 连续 {worst} 章都是「叙述」类落笔，没有任何对话/面板收尾。')
        print('     → 四型可能换过了，但**落笔全回到"叙述一句+余味"**——'
              '这正是"僵硬公式化"的形态。')

    if problems == 0 and not hot and worst < 5:
        print('\n  ✓ 未检出跨章章末同质')
    return problems


# ══════════════════════════════════════════════════════════════════
# 意象配额检测（2026-09-23 新增）
# ══════════════════════════════════════════════════════════════════
# 为什么放进这个脚本：它本身就是"重复"的一种——**反复用同一个意象** →
# 读者疲劳 → 像在自我复制。这恰好是长篇最容易犯、单章又看不出来的毛病。
#
# 为什么现在做：`bible-template.md` 的「四·五、意象使用记录」早就定义了规则
#   （**同一意象每卷 ≤4 次，且至少有一次"该出现时缺席"**），
#   但全库检索只有那一处定义、**零读点、零检查**——是个孤儿。
#   而它**天然可脚本化**（数词频），成本 0。
#
# 判据（两级）：
#   ✗ 最长**连续出现** ≥5 章 → 违反"至少有一次该出现时缺席"（它从不缺席）
#   ⚠ 出现章占比 ≥50%      → 过密（读者会开始注意到"又来了"）
#   ⚠ 表里登记的"已出现次数"与实测差 ≥2 → 表没跟上正文（登记失效）
_IMAGERY_SECTION = re.compile(r'(?m)^#{1,4}[^\n]*意象[^\n]*$')
_IMAGERY_ROW_SKIP = {'意象', '---', '------', '--------', ''}


def imagery_names(bible_text: str) -> list:
    """从圣经的「意象使用记录」表里抽出意象名。"""
    m = _IMAGERY_SECTION.search(bible_text)
    if not m:
        return []
    rest = bible_text[m.end():]
    nxt = re.search(r'(?m)^#{1,4}\s', rest)
    sec = rest[:nxt.start()] if nxt else rest
    names = []
    for line in sec.split('\n'):
        if not line.strip().startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 3:
            continue
        name = re.sub(r'[\[\]【】*`\s]', '', cells[0])
        name = re.sub(r'^例[:：]', '', name)
        if name in _IMAGERY_ROW_SKIP or set(name) <= {'-'}:
            continue
        if name and name not in names:
            names.append(name)
    return names


def imagery_variants(name: str) -> list:
    """把意象名拆成**可计数的变体**。

    ⚠️ 两个真实踩到的坑（都来自真实项目的表）：
      · `炉火/火光` —— 表里用斜杠列同义写法。整串拿去做 `str.count` 恒为 0，
        于是这个意象**永远"合格"**（静默通过，比报错更糟）。
      · `门` —— 单字在中文里无法可靠计数（门口/部门/门票…），
        会被严重高估。
    所以：按斜杠/顿号拆变体；**单字变体丢弃并在报告里点名**（宁可明说"这项没查"，
    也不要给一个看似正常的假数字）。
    """
    raw = re.sub(r'[（(][^）)]*[）)]', '', name)       # 去掉括号里的说明
    parts = [p.strip() for p in re.split(r'[/／、|]', raw) if p.strip()]
    return [p for p in parts if len(p) >= 2]


def check_imagery(project: Path, brief: bool = False) -> int:
    """意象配额。返回失败级问题数。"""
    bible = None
    for cand in ('00-故事圣经.md', '00-世界设定.md', '00-世界书.md'):
        p = project / cand
        if p.is_file():
            bible = p
            break
    if bible is None:
        for p in sorted(project.glob('*.md')):
            if '圣经' in p.name:
                bible = p
                break
    if bible is None:
        if not brief:
            print('\n  ⚠ 没找到故事圣经 —— 意象配额无法检查（跳过，不是"通过"）')
        return 0

    names = imagery_names(read_text(bible))
    if not names:
        if not brief:
            print('\n  ⚠ 圣经里没有「意象使用记录」表 —— 配额机制没有数据源，'
                  '`bible-template.md` 四·五给出了表格式')
        return 0

    sys.path.insert(0, str(Path(__file__).parent))
    from _shared import find_chapter_files
    files = find_chapter_files(project)
    if not files:
        return 0
    bodies = []
    for f in files:
        try:
            bodies.append(extract_body(read_text(f)))
        except Exception:
            bodies.append('')

    print('\n' + '=' * 60)
    print('意象配额检测（同一意象反复出现 = 长篇特有的自我复制）')
    print('=' * 60)
    print('  意象'.ljust(16) + '出现章数  总次数  最长连续')
    fails, warns, skipped = [], [], []
    for nm in names:
        vs = imagery_variants(nm)
        if not vs:
            skipped.append(nm)
            continue
        hits = [sum(b.count(v) for v in vs) for b in bodies]
        chap_n = sum(1 for h in hits if h)
        total = sum(hits)
        run = best = 0
        for h in hits:
            run = run + 1 if h else 0
            best = max(best, run)
        ratio = chap_n / len(bodies)
        flag = ''
        if best >= 5:
            fails.append((nm, best, ratio))
            flag = '  ✗ 从不缺席'
        elif ratio >= 0.5:
            warns.append((nm, '过密', f'出现在 {chap_n}/{len(bodies)} 章'))
            flag = '  ⚠ 过密'
        print(f'  {nm[:14]:<16}{chap_n:>6}/{len(bodies):<5}{total:>6}{best:>8}{flag}')

    for nm, best, _r in fails:
        print(f'\n  ✗ 「{nm}」连续 {best} 章都出现 —— '
              f'违反「至少有一次**该出现时缺席**」：从不缺席的意象不是意象，是背景噪音。')
    for nm, kind, detail in warns:
        print(f'\n  ⚠ 「{nm}」{kind}（{detail}）—— 读者会开始注意到"又来了"。')
    if skipped:
        print(f'\n  ⚠ 无法计数、已跳过 {len(skipped)} 项：{"、".join(skipped[:6])} —— '
              f'单字/纯符号在中文里不可靠计数。**这些意象本轮没有被检查**，'
              f'要么改成多字写法，要么人工看。')

    if not fails and not warns:
        print('\n  ✓ 意象配额正常（无"从不缺席"、无过密）')
    if brief:
        print(f'\n[低费用·窗闸] 意象 {len(names)} 个｜从不缺席 {len(fails)}｜'
              f'过密 {len(warns)}｜无法计数 {len(skipped)}'
              + (' → 合格' if not fails else ' → 不合格'))
    return len(fails)


def main():
    parser = argparse.ArgumentParser(description='章节重复检测（AI 硬伤扫描）')
    parser.add_argument('path', help='章节 .md 文件，或 --all 时的项目目录')
    parser.add_argument('--all', action='store_true', help='检测目录下所有章节')
    parser.add_argument('--window', type=int, default=0,
                        help='跨章判定只看最近 N 章（0 = 全部）。流水线窗闸每章跑时'
                             '应设为「笔手领先上限」同值——窗口与领先上限必须对齐，'
                             '否则发现会晚于定稿，修复成本从线性变超线性')
    parser.add_argument('--brief', action='store_true',
                        help='只输出问题（窗闸每章跑时用：保证报告定长，'
                             '否则明细随章数变长、每次重读一遍）')
    parser.add_argument('--imagery', action='store_true',
                        help='额外检查**意象配额**（读圣经「意象使用记录」表）：'
                             '同一意象"从不缺席"或过密 → 长篇特有的自我复制。'
                             '把 `bible-template.md` 四·五 那条孤儿规则变成可查的')
    args = parser.parse_args()

    if args.imagery:
        args.all = True      # 意象配额要扫全书 + 读圣经

    if args.all:
        project = Path(args.path)
        if not project.is_dir():
            print(f'[错误] 目录不存在：{args.path}')
            sys.exit(2)
        # 目录解析走共享层：此前是 `project.glob('第*.md')`（**只在项目根扫**），
        # 而规范要求正文放 `chapters/` → 对完全合规的项目一个文件都找不到。
        # 这与 2026-09-19/20 修过的另外两个脚本是同一个 bug 的三胞胎。
        sys.path.insert(0, str(Path(__file__).parent))
        from _shared import find_chapter_files
        files = [str(p) for p in find_chapter_files(project)]
        if not files:
            print('[错误] 未找到章节文件 —— 章节应放在 `chapters/第NN章-标题.md`。')
            print('       这不等于"通过"：脚本没看见你的稿子，先修结构再重跑。')
            sys.exit(2)
        # 窗闸：判定只看最近 N 章。早先的章在它进窗口时已经查过；
        # 批末不带 --window 的 --all 会做一次全量兜底。
        win = files[-args.window:] if args.window > 0 else files
        total_dupes = 0
        for f in win:
            if args.brief:
                # 静音但保留判定——不能为了省输出把闸门一起省掉
                with contextlib.redirect_stdout(io.StringIO()):
                    n = check_file(Path(f))
                total_dupes += n
                if n:
                    print(f'  ✗ {Path(f).name}：{n} 处段落重复')
            else:
                total_dupes += check_file(Path(f))
        same_open = check_openings(win, brief=args.brief)
        same_end = check_endings(win, brief=args.brief)
        imag = check_imagery(project, brief=args.brief) if args.imagery else 0
        if args.brief:
            bad = total_dupes or same_open or same_end or imag
            tail = (f'\n[低费用·窗闸] 窗口 {len(win)}/{len(files)} 章'
                    f'｜章内重复 {total_dupes}｜开场同质 {same_open}｜章末同质 {same_end}')
            if args.imagery:
                tail += f'｜意象配额 {imag}'
            print(tail + (' → 合格' if not bad else ' → 不合格'))
        else:
            extra = f'，{imag} 个意象从不缺席' if args.imagery else ''
            print(f'\n===== 汇总：{len(files)} 章，{total_dupes} 章含重复，'
                  f'{same_open} 处开场同质，{same_end} 处章末同质{extra} =====')
        sys.exit(1 if (total_dupes or same_open or same_end or imag) else 0)
    else:
        f = Path(args.path)
        if not f.exists():
            print(f'[错误] 文件不存在：{args.path}')
            sys.exit(2)
        sys.exit(check_file(f))


if __name__ == '__main__':
    main()
