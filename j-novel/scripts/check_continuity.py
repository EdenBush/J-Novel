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
import os
import re
import sys
from pathlib import Path

# ── 共享实现（2026-09-14 重构）────────────────────────────────────
# 旧实现的 read_text 用"中文占比最高"选编码：utf-16 能把任意偶数长度字节流
# 解成中文乱码，在 ASCII 偏多的文件上会赢过 utf-8 → 角色名解成乱码 →
# 边界检测**静默全部通过**。现统一走 _shared.read_text（BOM → 严格 utf-8 → 遗留编码）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import extract_body as _shared_extract_body, read_text as _shared_read_text  # noqa: E402

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
    """读文件。**编码探测统一走 _shared.read_text**（2026-09-14 重构）。

    旧实现的中文占比最高启发式会把 ASCII 偏多的 UTF-8 文件误判成 utf-16，
    解出乱码 → 角色名匹配不上 → 边界检测**静默全部通过**（最危险的一种失败）。
    """
    return _shared_read_text(path)


def extract_body(text):
    """取正文。**委托给 `_shared.extract_body`**（2026-09-14 二次修）。

    ⚠️ 这里原本是一份**本地实现**，把上面 import 进来的 `_shared_extract_body`
    整个覆盖掉了——于是 v4.6「三脚本统一 extract_body」的改动对本脚本**完全没生效**。

    本地版少剥的东西（会让"钩子区/承接区"混进元数据）：
      · `## 本章概要` / `## 章节备注` / `【本章质检摘要】` 的**整块散文内容**
        （本地版只跳标记行和 `- **字段**` 行，块内其他文字照收）
      · 面板行、纯符号行、章节号+标题行
    后果：角色名可能来自"本章概要"而不是正文 → 边界判断错位。

    现已纳入 `audit_release.py` 的 `_SHARED_FILES` 名单，并有"import 后被本地
    def 覆盖"检测守着——写回去会直接 QA 失败。
    """
    return _shared_extract_body(text)


def _warn_names_missing(why):
    """角色名解析失败时必须**出声**——否则边界检测会静默全部通过。

    2026-09-14 修：原实现在解析不出名字时返回空集，导致 in_hook 恒为空、
    analyze_boundary 全部通过、退出码 0 —— 最危险的一种静默失败。
    """
    global _NAMES_WARNED
    if not _NAMES_WARNED:
        _NAMES_WARNED = True
        print(f'[警告] 未能从 00-人物档案.md 解析出角色名（{why}）——'
              f'边界检测将全部通过，等于没有检查。请确认档案用了 “### 名字” 或 “**名字（描述）**” 格式。')


_NAMES_WARNED = False


def load_names(char_file):
    """从 00-人物档案.md 提取角色名（### 标题 + **加粗名** 两种来源）。
    只取"名"不取"姓"——'诺瓦·艾瑟兰' 只取 '诺瓦'，避免 '灰石/晨誓/暮影' 这类
    西式姓和地名/概念（灰石镇/晨誓骑士团）冲突造成误报。"""
    if not char_file or not Path(char_file).exists():
        _warn_names_missing('人物档案文件不存在')
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
    if not names:
        _warn_names_missing('格式不匹配')
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

    report, hard_hits, hit_pairs = [], 0, []
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
            hit_pairs.append((int(prev_name), int(cur_name)))
            print(f'   ⚠ 这些人物在本章开头 {OPEN_LEN} 字内未再出现：{"、".join(r["absent"])}')
            print(f'     → 上章结尾的钩子可能被晾了一章。人工复查：本章是否需要给 TA 一句交代？')
            if r['time_jump']:
                print(f'     → 且本章开头有跳变词（{"、".join(r["time_jump"])}），跳接章更需要桥接钩子')
        else:
            print(f'   ✓ 钩子人物均在本章开头出现，承接正常')
        if r['time_jump'] and not r['absent']:
            print(f'   （本章开头有跳变词：{"、".join(r["time_jump"])}，已接住钩子则无碍）')
        print()

    # ── 已放行的边界：读项目里的 05-创作台账.md 的 boundary-waived 记录 ──
    # 2026-09-14 修①：此前只有 check_batch_gate 会读 waiver，Phase 4 直跑本脚本时
    # 已放行的边界会被重新报一遍，编辑只好每次手工比对台账。放行是**业务规则**，
    # 应该在检查工具本身生效，而不是在包装层生效。
    #
    # 2026-09-14 修②（**重要**）：此前是"一票全放行"——只要台账里有 ≥1 条
    # boundary-waived，就 `raw_waived = hard_hits; hard_hits = 0` 把所有命中清零。
    # 实测：3 章 2 处违规，只 waive 了「第1→2章」，脚本却报"**2 条**边界原本会被
    # 报出…已跳过"，把没放行的「第2→3章」也静默吞掉，退出码 1→0。
    # **它会撒谎**——这比不检查更危险。现改为**按「第N→M章」逐条匹配**。
    waived_pairs, waived_note = set(), ''
    if hard_hits:
        _ledger = None
        for cand in (proj / '05-创作台账.md', proj.parent / '05-创作台账.md'):
            if cand.exists():
                _ledger = cand
                break
        if _ledger:
            _lt = read_text(_ledger)
            _ws = re.findall(
                r'boundary-waived\s*[:：]\s*第\s*(\d+)\s*[→\->—]{1,2}\s*(\d+)\s*章', _lt)
            waived_pairs = {(int(a), int(b)) for a, b in _ws}
            if _ws:
                waived_note = ' ｜ '.join(
                    '第%s→%s章' % (a, b) for a, b in _ws[:4])

    # 逐条扣除：只有**被显式写进台账的那一对章号**才算放行
    unmatched = [p for p in hit_pairs if p not in waived_pairs]
    truly_waived = len(hit_pairs) - len(unmatched)
    hard_hits = len(unmatched)

    print('=' * 60)
    if truly_waived:
        print(f'⚠ {truly_waived} 条边界已在 05-创作台账.md 显式放行，已跳过：{waived_note}')
        print('   （放行是显式声明，不是默许——想撤销就在台账里删掉那一行。）')
    if unmatched:
        if truly_waived:
            print(f'⚠ **但仍有 {len(unmatched)} 条未放行**，必须逐条处理：')
        else:
            print(f'⚠ {len(unmatched)} 条边界存在"钩子人物在本章开头缺席"，需主编逐条复查：')
        for a, b in unmatched:
            print(f'     · 第{a}→{b}章')
        print('  判断标准：这个钩子是"该接没接"（bug），还是"刻意留到后章"（正常）？')
        print('  **判定为正常的，在 05-创作台账.md 写一行放行记录**（不要绕过闸门）：')
        print('     boundary-waived: 第N→N+1章，理由：该钩子第M章回收（理由 ≥8 字）')
    elif not truly_waived:
        print('✓ 未发现明显的钩子悬空。')
    sys.exit(1 if hard_hits else 0)


if __name__ == '__main__':
    main()
