#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「挑刺题」：让一个独立评委逐句找**站不住的地方**。

用法:
    python scripts/make_grill.py <项目目录> --chapter 7
    python scripts/make_grill.py <项目目录> --chapter 7 --no-world   # 只给正文（更严格）

产出:
    chapters/_meta/第NN章-挑刺题.md    ← 转交评委的全文

退出码: 0 = 已生成；1 = 章节太短/读不到；2 = 项目读不出

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 它和盲测是两件事（不许合并）

| | 盲测（`make_blindtest.py`） | 挑刺（本脚本） |
|---|---|---|
| 问什么 | **像不像人写的**（语感） | **站不站得住**（逻辑/事实/动机） |
| 怎么问 | 6 段混排，判哪 3 段是人写的 | 逐段找"这一处说不通" |
| 判据 | 人类置信度 + 评委可靠性 | **硬伤数**（时间/因果/物理/设定）+ 软伤数 |
| 能不能合并 | **不能**。合并后评委要在两种模式间切换，两种都做不好 |

读者说的"禁得起推敲"，落到可执行层面就是**七类证伪**：
时间对不上／因果断裂／动机不成立／物理与常识错／设定被违反／同一件事说了两遍／一段删掉什么也不少。

## 为什么"挑刺"必须由独立评委做

执行者自己挑刺时，它挑的是"我记得我担心过的地方"，而不是"真的站不住的地方"——
而且它知道自己的意图，会**自动为可疑处补上解释**（这是写作时的必要能力，
审稿时却正好是致命的：**作者永远能为自己辩护**）。
独立性在这里的作用不是"更客观"，是**"不知道你想干什么"**。

## 给评委什么、不给什么

给：**纯正文** + **世界专名表与规则**（判设定矛盾要有依据）+ **本章故事账**（判"该发生的事发生了没"）。
不给：细纲、剧情卡、规则包、配额卡、质检脚本、你的改动说明。
⚠️ **不给细纲**是刻意的：给了它就会去查"有没有按大纲写"，而我们要的是"这一章自己站不站得住"。
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import (  # noqa: E402
    ensure_utf8_stdio, extract_body, find_chapter_files, find_meta_file,
    read_text,
)
from make_task_package import _story_ledger, _world_pack_auto  # noqa: E402

CH_NO_RX = re.compile(r'第\s*0*(\d{1,4})\s*章')

SEVEN = [
    ('时间', '这一章里有没有两个时间点互相矛盾？（天黑了两次？伤是在前还是后？"三天前"和"昨天"打架？）'),
    ('因果', '有没有一个结果，前面找不到原因？或者一个人做了决定，却没有给出的理由？'),
    ('动机', '有没有一句话／一个动作，**这个人不会这么做**？（用他的处境替他辩护一次；辩护不了就是硬伤）'),
    ('物理常识', '身体、器物、距离、钱数、天气——有没有对不上的？（他断的是左手还是右手？三十文能买什么？）'),
    ('设定', '有没有出现这个世界不该有的东西，或者违反了专名表／规则里写死的边界？'),
    ('重复', '有没有同一件事被说了两遍（换了措辞、但读者收到的是同一个信息）？'),
    ('空转', '有没有一段，删掉它**情节不会少、人物不会变**——而且它不是闲笔，是"假装有内容"？'),
]


def main():
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('project')
    ap.add_argument('--chapter', type=int, required=True)
    ap.add_argument('--no-world', action='store_true', help='只给正文（更严格，但会误报设定类问题）')
    a = ap.parse_args()

    proj = Path(a.project).expanduser()
    if not proj.is_dir():
        print(f'✗ 项目路径不存在：{proj}')
        return 2
    files = [f for f in find_chapter_files(proj)
             if (m := CH_NO_RX.search(f.name)) and int(m.group(1)) == a.chapter]
    if not files:
        print(f'✗ 找不到第 {a.chapter} 章正文（chapters/ 下 `第NN章-*.md`）')
        return 2

    body = extract_body(read_text(files[0]), keep_blank=True).strip()
    if len(body) < 300:
        print(f'✗ 第 {a.chapter} 章正文只有 {len(body)} 字 —— 太短，挑刺没有意义')
        return 1

    world = '' if a.no_world else _world_pack_auto(proj, a.chapter, '')
    # 抽不到（待填）就不要把 TODO 文本塞给评委——那会污染它对正文的判断
    if world.startswith('【待填'):
        world = ''
    ledger = ''
    if find_meta_file(proj, a.chapter) is not None:
        ledger = _story_ledger(proj, a.chapter)
        if ledger.startswith('【待填'):
            ledger = ''

    L = []
    L.append(f'# 挑刺题（第 {a.chapter} 章）')
    L.append('')
    L.append('> 你的任务**不是**评价文笔，也**不是**判断它像不像 AI。')
    L.append('> 你的任务是：**找出这一章里站不住的地方。** 逐段读，只找"说不通"。')
    L.append('')
    L.append('**七类证伪（每一条都要主动去找，不是"如果发现"）**')
    L.append('')
    for i, (name, q) in enumerate(SEVEN, 1):
        L.append(f'{i}. **{name}** — {q}')
    L.append('')
    L.append('**输出格式（严格照这个写）**')
    L.append('```')
    L.append('【硬伤】')
    L.append('1. 原句：「<逐字引用>」')
    L.append('   类别：时间 / 因果 / 物理常识 / 设定')
    L.append('   为什么站不住：<一句话，要说清"和什么冲突">')
    L.append('   最小改法：<一句话，改哪儿、改成什么方向>')
    L.append('【软伤】')
    L.append('（格式同上；类别写 动机 / 重复 / 空转）')
    L.append('【结论】')
    L.append('硬伤 N 处 ／ 软伤 M 处')
    L.append('```')
    L.append('')
    L.append('**三条纪律**')
    L.append('1. **每一条都必须逐字引用原句**——没有原句的条目直接作废。')
    L.append('2. **"整体不错"「语言流畅」「人物立体」这类话不要写**，写了会被退回。')
    L.append('3. **找不到就写"0 处"**。宁可报 0，不要凑数；凑出来的条目比漏报更坏。')
    L.append('')
    if world:
        L.append('## 附：这个世界的设定（判"设定矛盾"要有依据）')
        L.append('')
        L.append(world)
        L.append('')
    if ledger:
        L.append('## 附：本章应该发生的事（判"该发生的发生了没"）')
        L.append('')
        L.append(ledger)
        L.append('')
    L.append('---')
    L.append('')
    L.append('## 正文')
    L.append('')
    L.append(body)

    out = proj / 'chapters' / '_meta'
    out.mkdir(parents=True, exist_ok=True)
    p = out / f'第{a.chapter:02d}章-挑刺题.md'
    p.write_text('\n'.join(L), encoding='utf-8')

    print(f'✓ 挑刺题：{p}')
    print(f'  正文 {len(body)} 字｜设定包 {"有" if world else "无"}｜故事账 {"有" if ledger else "无"}')
    print('  下一步：全文转交给一个**没读过本章、没见过细纲**的子代理。')
    print('  判读：**硬伤 > 0 必须改**；软伤由主编裁量（见 guides/grill.md）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
