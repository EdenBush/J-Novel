#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
评审提示词编译器（Review Brief Compiler）
=========================================

为什么需要它：
    2026-10-24 实测事故：主编派独立质检子代理时**手写**了提示词，结果三处走样 ——
      · 模板要求「按正文出现顺序等距抽 8 句」，它写成「每章抽一句」；
      · 模板要求「测试 3 只划对话」，它连面板也划了；
      · 模板的收尾两问（最该重写的一处 / 删 10% 后是否更好）**整段缺失**；
      · 锚点章要求 2–3 个独立评委，它只派了 1 个，却仍按「两个评委同指才必改」的
        口径采纳结论 —— 判定依据悬空。

    这四处都不是理解错误，而是**转述损耗**：模板一旦经过人的手抄，就会向"更省事"漂。
    这和「手抄必漂」（`subagent-brief.md`）是同一条规律，只是发生在提示词上。

所以：提示词**不许手写**，从 `review-agent.md` 原文机械抽取，一个字不改。

用法：
    python scripts/make_review_brief.py <项目> --chapter 5 [--judges 2]
    → 写 `chapters/_meta/第05章-评审提示词.md`，并打印路径
    然后把**该文件全文**交给子代理（只给纯正文，不给规则），不要自己再写一遍。

⚠️ 本脚本只抽模板，不判断"该不该派评委"——那是动作 5.5 的工序。
"""
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / 'references' / 'guides' / 'review-agent.md'
START = '## 二、任务模板（复制直接用）'


def read_text(p: Path) -> str:
    return p.read_text(encoding='utf-8')


def extract_template(text: str) -> str:
    """抽「任务模板」整节（到下一个同级 `## ` 标题为止），剔除代码围栏。"""
    i = text.index(START)
    j = text.find('\n## ', i + len(START))
    block = text[i:j if j > 0 else len(text)]
    # 去掉 ```` ``` ```` 围栏：它本来是给"复制正文"用的，留着会让人以为要连围栏一起贴
    lines = [ln for ln in block.split('\n')
             if ln.strip() not in ('```', '```text', '```markdown')]
    return '\n'.join(lines).strip()


def judges_for(tier: str, manual):
    """评委数：人工指定优先；否则按分诊档（R1=1，R2=2，R3=3）。"""
    if manual:
        return int(manual), '人工指定'
    t = (tier or '').strip().upper()
    return ({'R1': 1, 'R2': 2, 'R3': 3}.get(t, 1),
            '按分诊档 %s' % (t or '未判') if t in ('R1', 'R2', 'R3') else '档位未知，按 R1 起')


def read_tier(proj: Path, n: int):
    wo = proj / '06-章节工单.md'
    if not wo.is_file():
        return ''
    head, idx = None, None
    for ln in read_text(wo).split('\n'):
        st = ln.strip()
        if not st.startswith('|'):
            continue
        cells = [c.strip() for c in st.strip('|').split('|')]
        if head is None:
            if '分诊档' in cells:
                idx = cells.index('分诊档')
                head = True
            continue
        if idx is not None and (st.startswith('| 第%d章 ' % n) or st.startswith('| 第%d章|' % n)):
            return cells[idx] if len(cells) > idx else ''
    return ''


def main():
    ap = argparse.ArgumentParser(description='评审提示词编译器（从 review-agent.md 原文抽取）')
    ap.add_argument('project')
    ap.add_argument('--chapter', type=int, required=True)
    ap.add_argument('--judges', type=int, default=None, help='评委数；不填按分诊档')
    args = ap.parse_args()

    if not GUIDE.is_file():
        print('[错误] 找不到 references/guides/review-agent.md')
        sys.exit(2)
    proj = Path(args.project)
    if not proj.is_dir():
        print('[错误] 项目目录不存在：%s' % proj)
        sys.exit(2)

    n = args.chapter
    tier = read_tier(proj, n)
    k, why = judges_for(tier, args.judges)

    body = extract_template(read_text(GUIDE))
    head = '\n'.join([
        '# 第 %d 章 · 独立质检子代理提示词' % n,
        '',
        '> **本文件由 `scripts/make_review_brief.py` 从 `review-agent.md` 原文机器抽取，'
        '一个字未改。**',
        '> 派子代理时**整段转交**，不要重写、不要摘要、不要"按要点说"。',
        '> 2026-10-24 实测：手写提示词时漂了四处（等距 8 句→每章 1 句／测试 3 划了面板／'
        '收尾两问缺失／1 个评委当 2 个用）。**转述损耗不是理解问题，是流程问题。**',
        '',
        '- **本章分诊档**：%s' % (tier or '（未判，先跑 `make_workorder.py --archive %d`）' % n),
        '- **评委数**：**%d**（%s）—— 锚点章/用户抱怨章应按 2–3 个独立评委；'
        '不足 2 个时，**结论只能记"单评委实证"，不得按"两位同指必改"口径采纳**' % (k, why),
        '- **只给纯正文**：不给本 SKILL、不给指标、不说"我改过什么"（独立性是这套测试的全部价值）',
        '',
        '---',
        '',
        body,
    ])

    meta = proj / 'chapters' / '_meta'
    meta.mkdir(parents=True, exist_ok=True)
    out = meta / ('第%02d章-评审提示词.md' % n)
    out.write_text(head, encoding='utf-8')
    print('★ 评审提示词已生成：%s' % out)
    print('  分诊档 %s ／ 评委数 %d（%s）／ 模板 %d 字（原文抽取，未改写）'
          % (tier or '?', k, why, len(body)))
    print('  → 把这个文件**全文**交给子代理；不要自己再写一份。')


if __name__ == '__main__':
    main()
