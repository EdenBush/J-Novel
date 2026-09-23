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

sys.path.insert(0, str(Path(__file__).parent))
from _shared import ensure_utf8_stdio      # noqa: E402

ensure_utf8_stdio()

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

# 人名的**反向词表**：以这些词结尾的候选是"设定术语/字段标签"，不是人。
# 实测污染源：内在需求 / 外在目标 / 四种冲突轴 / 人物弧光 / 核心缺陷 …
# 保守做法：只按**结尾**判（真名以"目标/需求/轴"结尾的概率≈0），
# 避免误杀同时含这些字的人名（如"陈目标"不会出现，但"马斯克"也不受影响）。
_TERM_SUFFIX = re.compile(
    r'(需求|目标|冲突|弧光|结构|逻辑|设定|规则|主题|母题|视角|节奏|基调|风格|'
    r'机制|体系|线索|反转|悬念|伏笔|张力|动机|性格|缺陷|成长|转变|阶段|层次|'
    r'维度|要素|步骤|方法|原则|清单|表格|模板|字段|说明|备注|概述|总览|分析|'
    r'建议|要点|核心|关键|重点|关系|背景|经历|外貌|身材|能力|技能|结局|定位|轴)$')


def load_names(char_file):
    """从 00-人物档案.md 提取角色名（### 标题 + **加粗名** 两种来源）。
    只取"名"不取"姓"——'诺瓦·艾瑟兰' 只取 '诺瓦'，避免 '灰石/晨誓/暮影' 这类
    西式姓和地名/概念（灰石镇/晨誓骑士团）冲突造成误报。"""
    if not char_file or not Path(char_file).exists():
        _warn_names_missing('人物档案文件不存在')
        return set()
    t = read_text(char_file)
    names = set()
    block = re.compile(r'反派|主角|配角|龙套|后宫|阵容|关系网|对手|阵营|势力|组织|团队|角色|其他|登场|总览|设定|关系')
    for line in t.split('\n'):
        cand = None
        # ⚠️ 2026-09-15 修：**层级要放宽到 ##–####**。
        # 实测真实项目用 `## 林迟（主角）· 27 岁 · 男`（H2），而这里只认 `###` →
        # **四个主角一个都没解析到**，边界检测因此全程"钩子区未检测到已知角色名 → 跳过"，
        # 却因为另有一个垃圾名（从加粗字段误抓）而**不触发"空集"警告** —— 静默失效。
        m = re.match(r'^#{2,4}\s+(.+?)\s*$', line.strip())
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
        # 人名不该带这些字（防"床上的逻辑"这类从散文里误抓的短语）
        if re.search(r'[的地得了着过说想著想要是]', cand) or len(cand) < 2:
            continue
        # ── 2026-09-19 修：把「设定术语」挡在人名之外 ──
        # 实测某项目从人物档案里抓出 5 个假人名，直接污染边界报告：
        #   '先给结论，再给理由'（含逗号）· '星穹互娱 / 系统'（含空格斜杠）
        #   '内在需求' · '外在目标' · '四种冲突轴'（纯中文的**字段标签**）
        # 前者靠"人名不含标点"挡，后者靠"术语后缀"挡。
        if re.search(r'[，,、。；;：:！!？?/\\|（）()\[\]【】\s0-9]', cand):
            continue
        if _TERM_SUFFIX.search(cand):
            continue
        if len(cand) < 2 or len(cand) > 6:      # 中文人名 2–4 字为主，放宽到 6
            continue
        names.add(cand)
    if not names:
        _warn_names_missing('格式不匹配或角色名均被过滤')
    return names


def chapter_files(proj):
    """找章节文件。**递归子目录**——SKILL 从未规定章节目录（实测真实项目放 `正文/`），
    只扫根目录会让边界检测"看不到章节"，进而误报"章节文件不足 2 个"。

    ⚠️ 2026-09-19 修：此前用 `re.match(r'第\\d+章', f.name)` 过滤，
    会把**备份稿**和**元数据文件**一起收进来：
        · `第01章-xxx.原稿备份.md` → 同一个"第01章"出现两次
          → 边界报告里冒出 `第01章 结尾 → 第01章 开头`（自己接自己）这种假边界
        · `chapters/_meta/第01章-xxx.meta.md` → 元数据被当成正文
    而 `seen` 按**文件名**去重，备份稿与正文不同名 → 去重挡不住。
    现改为委托 `_shared.find_chapter_files`（唯一真相源，自带排除规则）。
    """
    proj = Path(proj)
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from _shared import find_chapter_files as _find
        files = _find(proj)
        if files:
            return files
    except Exception:
        pass
    # 兜底（_shared 不可用时）：保留旧行为，但补上备份/元数据排除
    files, seen = [], set()
    for pat in ('第*.md', '正文/第*.md', 'chapters/第*.md', '*/第*.md'):
        for f in sorted(proj.glob(pat), key=lambda x: x.name):
            if not f.is_file() or f.name in seen:
                continue
            if not re.match(r'第\d+章', f.name):
                continue
            if f.name.endswith(('.meta.md', '.原稿备份.md', '.bak.md', '.旧.md', '.orig.md')):
                continue
            if any(part.startswith('_') for part in f.relative_to(proj).parts[:-1]):
                continue
            seen.add(f.name)
            files.append(f)
    return sorted(files, key=lambda x: x.name)


def find_char_file(proj):
    """找人物档案。兼容 `00-人物档案.md`（规范）/ `01-人物档案.md`（实测项目用这个）
    以及任意含"人物档案"的 md——**找不到才是问题，命名不该是问题**。"""
    proj = Path(proj)
    for name in ('00-人物档案.md', '01-人物档案.md', '人物档案.md'):
        f = proj / name
        if f.exists():
            return f
    hits = [f for f in proj.glob('*人物*档案*.md') if f.is_file()]
    if hits:
        return sorted(hits)[0]
    hits = [f for f in (proj / '正文').glob('*人物*.md')] if (proj / '正文').is_dir() else []
    return sorted(hits)[0] if hits else None


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

    names = load_names(find_char_file(proj))
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
        # ⚠️ 2026-09-19：这里的示例**必须是本次真实命中的章号**。
        # 此前硬编码 `第N→N+1章，理由：该钩子第M章回收` —— Agent 会把提示文字**原样抄进台账**，
        # 而那条模板在 check_batch_gate 的宽松正则下匹配成功 → 全书边界检查被一条
        # 从提示文字里抄来的**假放行**整条豁免（实测真实项目就是这么中招的）。
        # **提示文字不能教人写占位符**，否则它就是事故的源头。
        if unmatched:
            _a, _b = unmatched[0]
            print(f'     boundary-waived: 第{_a}→{_b}章，理由：该钩子第{_b + 3}章回收（理由 ≥8 字，章号必须是数字）')
        else:
            print('     boundary-waived: 第8→9章，理由：该钩子第11章回收（理由 ≥8 字，章号必须是数字）')
    elif not truly_waived:
        print('✓ 未发现明显的钩子悬空。')
    sys.exit(1 if hard_hits else 0)


if __name__ == '__main__':
    main()
