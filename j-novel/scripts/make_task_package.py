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
# CJK 约 1 token/字，故 5000 字 ≈ 5k tokens，相对 149k 是可控的量级。
SLOTS = [
    ('batch_global', '批次全局三件套（文风基因 / 锚点示例 / 句法基线）',
     900, True, 'script'),
    ('contract', '接口契约（冻结）', 420, True, 'script'),
    # ★ 前馈槽位：把"本批已用过的手法"交给写手 → 直接消掉「跨章同质」这一类返工。
    #   形态是关键：**给已发生的事实，不给目标值** ——
    #   项目实测过"底线当目标、配额当目标"被 hack；这里只陈述"已经用过什么"让写手避开，
    #   **没有任何可以"凑"的数字或指标**。
    ('used_devices', '本批已用过的开场 / 章末型（避开它们）', 220, True, 'script'),
    # ⚠️ 预算必须给**段落粒度**留余量：`_tail` 是按段往回拼到 ≥800 字，
    #    多带一段就会溢出（实测 882 > 820）。这种"超预算"是**度量口径**问题，
    #    不是包写胖了 —— 放宽容量的正确做法，而不是放宽判据。
    ('prev_tail', '上一章真实结尾', 900, True, 'script'),
    ('meta', '本章元信息', 60, True, 'script'),
    ('ledger', '状态台账·本章出场人物条目', 620, True, 'editor'),
    ('world_pack', '世界设定包（按本章裁剪）', 820, True, 'editor'),
    ('author_shot', '作者画面（脚手架原话）', 160, False, 'editor'),
    ('lesions', '本批已知病灶与占位指令', 220, False, 'editor'),
    ('diagnosis', '诊断句（仅重写任务）', 90, False, 'editor'),
]
TOTAL_BUDGET = 5000
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


def build(proj: Path, chapter_no: int):
    """返回 {slot_key: 内容}；脚本填不了的槽位返回 TODO 标记。"""
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
    fills['author_shot'] = ''
    fills['lesions'] = ''
    fills['diagnosis'] = ''
    return fills


def render(proj: Path, chapter_no: int, fills: dict) -> str:
    parts = [f'# 第 {chapter_no} 章 · 任务包（由 make_task_package.py 编译）',
             '',
             f'> 本包由脚本编译：**脚本能拿到的已填好**，其余是**显式待填标记**。',
             f'> 预算上限：单槽位见标记，**总上限 {TOTAL_BUDGET} 字**。',
             f'> 校验：`python scripts/make_task_package.py "{proj}" '
             f'--check <本文件>`（缺槽位 / 超预算 / 待填残留 → 退出码 1，**拒绝开工**）',
             '']
    for key, title, budget, req, who in SLOTS:
        body = fills.get(key, '')
        if not body and not req:
            continue                      # 可选槽位且本来没有 → 不出现在包里
        parts.append(SLOT_OPEN.format(key=key, budget=budget, req=int(req)))
        parts.append(f'## {title}')
        parts.append('')
        parts.append(body.strip())
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
    slots = parse_package(read_text(path))
    probs = []
    total = 0
    for key, title, budget, req, who in SLOTS:
        if key not in slots:
            if req:
                probs.append(f'缺必填槽位 `{key}`（{title}）—— **拒绝开工**：'
                             f'子代理拿不到它就只能猜')
            continue
        body, _b, r = slots[key]
        n = _zishu(body)
        total += n
        if req and (not body or TODO in body):
            probs.append(f'槽位 `{key}` 还是待填状态：{body[:60]}')
        if n > budget:
            probs.append(f'槽位 `{key}` 超预算：{n} > {budget} 字 —— '
                         f'"被乘数"就是这么涨起来的（实测每次调用平均重发 14.9 万 tokens）')
    if total > TOTAL_BUDGET:
        probs.append(f'**总包超预算**：{total} > {TOTAL_BUDGET} 字')
    ck = _PKG_CHAPTER.search(path.name)
    name = path.name
    if not brief:
        print(f'\n  {name}')
        print(f'    槽位 {len(slots)}/{len(SLOTS)}｜合计 {total} 字（上限 {TOTAL_BUDGET}）')
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
    fills = build(proj, args.chapter)
    text = render(proj, args.chapter, fills)
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
