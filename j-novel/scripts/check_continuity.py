#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节边界连续性检测（J-Novel 新增）
====================================

为什么需要：并行写作最容易崩的是"章与章之间"——上一章的悬念钩子，下一章没接住。
单章质检（check_human_rhythm / check_aistyle）看的是"这一章内部像不像人"，
但"第 4 章开头有没有接住第 3 章结尾的钩子"这种跨章问题，它们看不见。

本脚本做两件事：
1. 钩子悬空检测：上一章结尾 300 字的"钩子人物"（出场角色名），本章开头 600 字里有没有再出现？
   没出现 → 标记"这个钩子可能被晾了一章"。
2. 时间跳变提示：本章开头是否有"第二天/当夜/次日/三天后"等跳变词，配合钩子悬空一起判断。

⚠ 这是**提示工具，不是硬闸门**——它给主编"该人工查哪条边界"的清单，不做自动判死刑。
   真正的连续性判断（这个钩子是不是刻意留到后章）仍需主编读原文裁决。

用法:
    python check_continuity.py <项目目录>
    python check_continuity.py <项目目录> --json
项目目录需包含：00-人物档案.md（取角色名）+ 第XX章-*.md（章节文件）

退出码：0 = 无硬问题；1 = 存在"钩子人物在本章开头完全缺席"的边界（需人工复查）
"""

import argparse
import glob
import io
import json
import re
import sys
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

_CJK = re.compile(r'[\u4e00-\u9fff]')
_ENCODINGS = ('utf-8', 'gb18030', 'gbk', 'utf-16', 'big5')

# 时间跳变词：本章开头出现这些，说明"跳过了一段时间"，此时上一章钩子更需要一句交代
TIME_JUMP = ['第二天', '次日', '当天夜里', '当夜', '当夜', '三天后', '数日后', '几天后',
             '一周后', '半个月后', '一个月后', '翌日', '隔天', '次日一早', '第二天一早',
             '次日清晨', '三天前', '第二天一早']

HOOK_LEN = 300     # 上一章结尾取多少字作"钩子区"
OPEN_LEN = 600     # 本章开头取多少字作"承接区"


def read_text(path):
    raw = Path(path).read_bytes()
    best, br = None, -1.0
    for e in _ENCODINGS:
        try:
            s = raw.decode(e)
        except (UnicodeDecodeError, LookupError):
            continue
        r = len(_CJK.findall(s)) / max(1, len(s))
        if r > br:
            best, br = s, r
    return best or ''


def extract_body(text):
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
        if re.match(r'^(#|## |### )', line):
            continue
        if re.match(r'^第.{0,8}章', line):
            continue
        if line == '---' or re.match(r'^[=\-—_*·]{3,}$', line):
            continue
        if re.match(r'^[-*] \*\*', line):  # 概要/备注里的字段行
            continue
        if re.match(r'^(本章概要|核心事件|承接上章|开场类型|悬念钩子|章节备注|本章悬念|下章预告|伏笔标记|开场类型|正文)$', line):
            continue
        out.append(line)
    return ''.join(out)


def load_names(char_file):
    """从 00-人物档案.md 提取角色名（### 标题 + **加粗名** 两种来源）。
    只取"名"不取"姓"——'诺瓦·艾瑟兰' 只取 '诺瓦'，避免 '灰石/晨誓/暮影' 这类
    西式姓和地名/概念（灰石镇/晨誓骑士团）冲突造成误报。"""
    if not char_file or not Path(char_file).exists():
        return set()
    t = read_text(char_file)
    names = set()
    block = re.compile(r'反派|主角|配角|龙套|后宫|阵容|关系网|对手|阵营|势力|组织|团队|角色')
    for line in t.split('\n'):
        cand = None
        m = re.match(r'^###\s+(.+?)\s*$', line.strip())
        if m:
            cand = m.group(1).strip()
        else:
            # 加粗名只看"后面跟（"的（疤脸霍恩（第一卷…）），
            # 避免把 **性格核心**：**动机**： 这类字段标签当成人名
            m = re.search(r'\*\*([^*]+?)\*\*[（(]', line)
            if m:
                cand = m.group(1).strip()
        if not cand:
            continue
        cand = re.split(r'[（(]', cand)[0].strip()          # 去（主敌）（第一卷…）后缀
        cand = re.split(r'[·]', cand)[0].strip()            # 只取"名"，不取"姓"
        if not cand or block.search(cand) or len(cand) > 12:
            continue
        names.add(cand)
    return names


def chapter_files(proj):
    files = sorted(Path(proj).glob('第*.md'), key=lambda p: p.name)
    return [f for f in files if re.match(r'第\d+章', f.name)]


def analyze_boundary(prev_body, cur_body, names):
    hook_zone = prev_body[-HOOK_LEN:] if len(prev_body) > HOOK_LEN else prev_body
    open_zone = cur_body[:OPEN_LEN]
    in_hook = [n for n in names if n in hook_zone]
    absent = [n for n in in_hook if n not in open_zone]
    time_jump = [w for w in TIME_JUMP if w in open_zone[:80]]
    return dict(hook_names=in_hook, absent=absent, time_jump=time_jump)


def main():
    ap = argparse.ArgumentParser(description='章节边界连续性检测')
    ap.add_argument('project', help='项目目录（含 00-人物档案.md + 第XX章-*.md）')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    proj = Path(args.project)
    if not proj.is_dir():
        print(f'[错误] 目录不存在：{proj}')
        sys.exit(2)

    names = load_names(proj / '00-人物档案.md')
    files = chapter_files(proj)
    if len(files) < 2:
        print('[错误] 章节文件不足 2 个')
        sys.exit(2)

    print(f'# 章节边界连续性检测（角色名 {len(names)} 个：{"、".join(sorted(names)[:15])}{"…" if len(names) > 15 else ""}）\n')

    report, hard_hits = [], 0
    for i in range(len(files) - 1):
        prev = extract_body(read_text(files[i]))
        cur = extract_body(read_text(files[i + 1]))
        r = analyze_boundary(prev, cur, names)
        report.append((files[i].name, files[i + 1].name, r))

        prev_name = re.match(r'第(\d+)章', files[i].name).group(1)
        cur_name = re.match(r'第(\d+)章', files[i + 1].name).group(1)

        print(f'── 第{prev_name}章 结尾 → 第{cur_name}章 开头 ──')
        if not r['hook_names']:
            print(f'   钩子区未检测到已知角色名（结尾是纯描写/环境）→ 跳过')
            continue
        print(f'   上章结尾钩子人物：{"、".join(r["hook_names"])}')
        if r['absent']:
            hard_hits += 1
            print(f'   ⚠ 这些人物在本章开头 {OPEN_LEN} 字内未再出现：{"、".join(r["absent"])}')
            print(f'     → 上章结尾的钩子可能被晾了一章。人工复查：本章是否需要给 TA 一句交代？')
            if r['time_jump']:
                print(f'     → 且本章开头有跳变词（{"、".join(r["time_jump"])}），跳接章更需要桥接钩子')
        else:
            print(f'   ✓ 钩子人物均在本章开头出现，承接正常')
        if r['time_jump'] and not r['absent']:
            print(f'   （本章开头有跳变词：{"、".join(r["time_jump"])}，已接住钩子则无碍）')
        print()

    print('=' * 60)
    if hard_hits:
        print(f'⚠ {hard_hits} 条边界存在"钩子人物在本章开头缺席"，需主编逐条复查：')
        print('  判断标准：这个钩子是"该接没接"（bug），还是"刻意留到后章"（正常）？')
        print('  刻意留后章的，在大纲/细纲里标注"此钩子第X章回收"，否则下一批还会误报。')
    else:
        print('✓ 未发现明显的钩子悬空。')
    sys.exit(1 if hard_hits else 0)


if __name__ == '__main__':
    main()
