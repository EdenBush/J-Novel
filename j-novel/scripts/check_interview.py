#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采访深度闸门（Interview Depth Gate）
====================================

为什么需要这个脚本（2026-10-24 真实事故）：

    作者开了个新项目，说"感觉采访挖得太浅了，太快立项了"。
    实际发生了什么，Agent 自己的思考过程里写着：

      「The skill demands deep interview. But a practical compromise:
        use AskUserQuestion with 4 questions, then proceed.」
      「AskUserQuestion 4 questions max per call. I'll do a first round of 4, then proceed.」

    **它没有违反规则，而是自己造了第三档模式。** 文档只有两档：
    「深度采访」和「快速模式（仅限作者明确要求）」——它造了第三档"紧凑但真实"。

    一轮 4 道选择题之后，它交出了：4 个女角色、系统机制、代价设计、经济线、
    10 章节拍、标题推荐。**这些作者一个都没被问过。**

    根因不在"它不听话"，在**清单没有来源字段**：
    验收 12 项只要被"填满"就算过，而填满的人可以是被采访者，也可以是采访者自己。
    → 这和「招式依据」是同一个病：**只验形式，就一定会被形式满足。**

所以：采访必须有**留痕产物** `00-采访记录.md`，且每项结论都要标**来源**。
本脚本验的就是"这些结论是谁说的"。

用法：
    python scripts/check_interview.py <项目目录>
    exit 0 = 可以进 Phase 2
    exit 1 = 退回继续采访（逐条列出缺什么）
"""
import re
import sys
import argparse
from pathlib import Path

ROUND_RX = re.compile(r'^#{2,4}\s*第\s*(\d+)\s*轮')
AUTHOR_RX = re.compile(r'^\s*[-*>]?\s*作者\s*[:：]\s*(.+)$')
ASK_RX = re.compile(r'^\s*[-*>]?\s*问\s*[:：]')
PROBE_RX = re.compile(r'追问')
FAST_RX = re.compile(r'快速模式')
AUTH_OK_RX = re.compile(r'作者授权[:：]?\s*[「“"]')

ITEMS = [
    '核心钩子', '第一章承诺', '世界观规则', '主角想要', '对手', '升级阶梯',
    '中点反转', '伏笔', '主题', '叙述风格', '篇幅', '标题',
]
SRC_OK = ('作者原话', '作者选择', 'Agent提案', '默认值')
MIN_ROUNDS = 3          # 少于此 = 一轮选择就立项（本次事故是 1 轮）
MIN_QUOTES = 8          # 作者原话少于 8 句，基本等于"没被采访"
MIN_PROBES = 2          # 追问少于 2 次 = 没有往下挖过一层
MAX_AGENT_ITEMS = 6     # 12 项里超过一半是 Agent 自提，就是代填
MAX_DEFAULT_ITEMS = 2   # 未确认默认值只允许 2 项
MIN_MATERIAL = 2        # 素材四问至少落实 2 项


def read_text(p: Path) -> str:
    return p.read_text(encoding='utf-8')


def parse_rounds(lines):
    """→ [(起, 止, 编号)]；'## 第 N 轮' 到下一个同级标题。"""
    starts = [(i, int(m.group(1))) for i, l in enumerate(lines)
              for m in [ROUND_RX.match(l.strip())] if m]
    out = []
    for k, (i, n) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        # 只截到下一个 `## ` 级标题（避免把"素材/验收"段算进最后一轮）
        for j in range(i + 1, end):
            if lines[j].startswith('## ') and not ROUND_RX.match(lines[j].strip()):
                end = j
                break
        out.append((i, end, n))
    return out


def section(lines, keyword):
    """找含 keyword 的 `## ` 段 → (起, 止)。

    ⚠️ 必须跳过「## 第 N 轮 · 素材」这类**轮次标题** —— 第一版没跳，
    于是「素材四问」被解析成某一轮的正文，导致**真采访也被判 0 项**。
    会误报的闸门比没有闸门更糟：它会教人把真东西删掉来通过检查。
    """
    for i, l in enumerate(lines):
        if l.startswith('## ') and keyword in l and not ROUND_RX.match(l.strip()):
            for j in range(i + 1, len(lines)):
                if lines[j].startswith('## '):
                    return (i, j)
            return (i, len(lines))
    return None


def table_rows(lines, rng):
    rows = []
    for i in range(rng[0], rng[1]):
        st = lines[i].strip()
        if not st.startswith('|'):
            continue
        cells = [c.strip() for c in st.strip('|').split('|')]
        if all(set(c) <= set('-: ') for c in cells):
            continue
        rows.append(cells)
    return rows


def main():
    ap = argparse.ArgumentParser(description='采访深度闸门（Phase 1 → Phase 2）')
    ap.add_argument('project')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    proj = Path(args.project)
    f = proj / '00-采访记录.md'
    if not f.is_file():
        print('✗ 找不到 `00-采访记录.md` —— **采访没有留痕 = 无法验收深浅**')
        print('  → 按 `references/flows/phase1-interview.md` 的模板建一份，逐轮记录：')
        print('     · 每轮的开放问题（`- 问：…`）与追问（`- 追问：…`）')
        print('     · 作者的**逐字原话**（`- 作者：「…」`）—— 不许改写、不许总结')
        print('     · 末尾「素材四问」表 + 「验收 12 项」表（**每项必须写来源**）')
        sys.exit(1)

    lines = read_text(f).split('\n')
    problems, notes = [], []

    # ① 轮次
    rounds = parse_rounds(lines)
    if len(rounds) < MIN_ROUNDS:
        problems.append('采访只有 **%d 轮**（要求 ≥%d）。'
                        '一轮问完就出速写 = 立项太快；第 2 轮起才是"挖"，第 1 轮只是"配置"'
                        % (len(rounds), MIN_ROUNDS))

    # ② 每轮要有开放问题
    for i0, i1, n in rounds:
        blk = lines[i0:i1]
        if not any(ASK_RX.match(l) for l in blk):
            problems.append('第 %d 轮没有一条开放问题（`- 问：…`）—— '
                            '纯选择题只能收"配置"，收不到"素材"' % n)

    # ③ 追问次数
    probes = sum(1 for l in lines if PROBE_RX.search(l))
    if probes < MIN_PROBES:
        problems.append('追问只有 **%d 次**（要求 ≥%d）。'
                        '追问 = 针对他上一句的回答再挖一层；只换话题不追问 = 问卷' % (probes, MIN_PROBES))

    # ④ 作者原话
    quotes = []
    for l in lines:
        m = AUTHOR_RX.match(l)
        if m:
            q = m.group(1).strip()
            if len(re.sub(r'[「」“”"\'…\s]', '', q)) >= 4:
                quotes.append(q)
    if len(quotes) < MIN_QUOTES:
        problems.append('作者原话只有 **%d 条**（要求 ≥%d）。'
                        '原话是采访的唯一产物——没有原话，验收的 12 项就全是你的想象'
                        % (len(quotes), MIN_QUOTES))

    # ⑤ 素材四问
    mat = section(lines, '素材')
    filled = 0
    if mat:
        for r in table_rows(lines, mat)[1:]:
            if len(r) >= 2 and r[1] and '作者无' not in r[1] and '待填' not in r[1]:
                filled += 1
    if filled < MIN_MATERIAL:
        problems.append('「素材四问」只落实了 **%d 项**（要求 ≥%d）—— '
                        '最想写的画面／最讨厌的写法／喜欢的作品／自己的生活细节。'
                        '**这四问要在采访早期就问**，不能等 12 项验收完再提' % (filled, MIN_MATERIAL))

    # ⑥ 验收 12 项 + 来源
    acc = section(lines, '验收')
    if not acc:
        problems.append('没有「验收 12 项」段（表格需含 # / 项 / 状态 / **来源**）')
        agent_items = default_items = 0
    else:
        rows = [r for r in table_rows(lines, acc) if len(r) >= 4]
        hit = {}
        agent_items = default_items = 0
        for r in rows:
            name = r[1]
            src = r[3]
            key = next((k for k in ITEMS if k in name), None)
            if not key:
                continue
            if not any(s in src for s in SRC_OK):
                problems.append('验收项「%s」**没写来源**（必须写：%s 之一）—— '
                                '没有来源的结论 = 你自己填的' % (key, '／'.join(SRC_OK)))
            if 'Agent提案' in src:
                agent_items += 1
            if '默认值' in src:
                default_items += 1
            hit[key] = True
        missing = [k for k in ITEMS if k not in hit]
        if missing:
            problems.append('验收表缺 **%d 项**：%s' % (len(missing), '、'.join(missing)))
        if agent_items > MAX_AGENT_ITEMS:
            problems.append('12 项里有 **%d 项是 Agent 自提**（上限 %d）—— '
                            '采访的主角设定/角色阵容/机制/尺度是作者主权，你只能提议'
                            % (agent_items, MAX_AGENT_ITEMS))
        if default_items > MAX_DEFAULT_ITEMS:
            problems.append('**%d 项是"默认值·未确认"**（上限 %d）—— '
                            '未确认的设定不许带进 Phase 2' % (default_items, MAX_DEFAULT_ITEMS))

    # ⑦ 快速模式必须有作者授权原话
    text = '\n'.join(lines)
    if FAST_RX.search(text) and not AUTH_OK_RX.search(text):
        problems.append('标了"快速模式"，但没有 `作者授权：「原话」` —— '
                        '**跳过采访只有作者能批，不能自批**（本次事故就是 Agent 自己批的）')

    # ── 报告 ──
    if args.json:
        import json
        print(json.dumps({'rounds': len(rounds), 'quotes': len(quotes),
                          'probes': probes, 'material': filled,
                          'agent_items': agent_items, 'default_items': default_items,
                          'problems': problems}, ensure_ascii=False, indent=2))
        sys.exit(1 if problems else 0)

    print('采访画像：%d 轮 ／ 开放问 %d 轮 ／ 追问 %d 次 ／ 作者原话 %d 条 ／ '
          '素材 %d 项 ／ Agent自提 %d 项 ／ 未确认默认值 %d 项'
          % (len(rounds),
             sum(1 for i0, i1, _ in rounds if any(ASK_RX.match(l) for l in lines[i0:i1])),
             probes, len(quotes), filled, agent_items, default_items))
    if problems:
        print('\n✗ 采访深度不足，**不许进 Phase 2**（%d 条）：' % len(problems))
        for p in problems:
            print('  •', p)
        print('\n  提示：采访不设轮数上限，只设质量下限——但"下限"必须能被**别人**核对。')
        print('        原话、来源、追问，就是让别人能核对的三样东西。')
        sys.exit(1)

    print('\n✓ 采访通过：轮次/追问/原话/素材/来源 五项齐备，可以进 Phase 2')
    for n in notes:
        print('  ·', n)
    sys.exit(0)


if __name__ == '__main__':
    main()
