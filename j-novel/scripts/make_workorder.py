#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节工单（J-Novel 新增，2026-09-23）
=====================================

**为什么需要它**：工业化第一铁律是

    总成本 = 单件成本 ÷ 一次合格率

而这条链此前**只有分子、没有分母**：

| 账 | 在哪 | 谁写 | 有没有返工列 |
|---|---|---|---|
| 质量账 | `04-质检档案.md` | **人肉抄** | ❌ |
| 成本账 | `~/.workbuddy/traces/` | 平台自动 | ❌ |

两账**没有共同的键**（一个是 markdown 行、一个是时间窗）→ "哪一章最贵""改了几轮"都答不出来。
返工的真实成本是 **额外调用轮次 × 每次调用的上下文**（实测中位 **149,675 tokens/次**），
所以"没有返工列"= **成本模型缺一半**。

## 本脚本做两件事

### ① `--archive <章号>` —— 每章质检那一步就调它（**一条命令 = 质检 + 归档**）

```bash
python scripts/make_workorder.py <项目> --archive 5 --retry 1
```

它**内部跑完全部机械脚本**（铁律九"一次跑完全部脚本"），然后：
- 按**退出码**判定缺陷类型（退出码是权威信号，不用去猜散文）
- 把一行**工单**追加进 `04-质检档案.md` 的汇总表（**消掉人肉转抄**）
- **`--retry`** 把 `retryCount` / 返工轮次**落盘**（此前它只存在于铁律七的规则描述里，从未被记录）

### ② `--report` —— 良率 + 缺陷帕累托 + 成本回填

```bash
python scripts/make_workorder.py <项目> --report
```

- **一次合格率**（首次归档即无缺陷的章占比）
- **缺陷帕累托**（哪几类缺陷占了大部分返工）→ **决定该改哪条规则**
- 成本列从平台轨迹按日期回填（可选 `--since`）

用法:
  python make_workorder.py <项目> --archive <章号> [--retry N]   # 质检+归档（每章一次）
  python make_workorder.py <项目> --report [--since YYYY-MM-DD]  # 良率+帕累托+成本
退出码: 0 = 正常；1 = 本次归档存在机械缺陷（不等于失败，是"这一章有缺陷，已登记"）；2 = 环境问题
"""

import argparse
import io
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import ensure_utf8_stdio, find_chapter_files, read_text  # noqa: E402

ensure_utf8_stdio()

ARCHIVE = '04-质检档案.md'
WORKORDER = '06-章节工单.md'
PROBE_DIR = '~/.workbuddy/traces'
# 机械质检全套（顺序 = 报告顺序）。退出码非 0 = 该类缺陷。
MECH = [
    ('字数', 'check_chapter_wordcount.py'),
    ('节律', 'check_human_rhythm.py'),
    ('词汇', 'check_aistyle.py'),
    ('重复', 'check_repetition.py'),
    ('连续性', 'check_continuity.py'),
    ('世界', 'check_worldbuilding.py'),
    ('契约', 'check_contract.py'),
]
COLS = ['章节', '字数', '机械结论', '缺陷类型', '返工轮次', '备注']
CH_RX = re.compile(r'第\s*(\d{1,4})\s*章')


def _run(script: str, args, cwd: Path):
    """跑一个同族脚本，返回 (退出码, 输出)。"""
    p = Path(__file__).parent / script
    if not p.is_file():
        return None, ''
    try:
        r = subprocess.run([sys.executable, str(p)] + [str(a) for a in args],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', cwd=str(cwd), timeout=180)
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        return None, f'[执行失败] {e}'


def chapter_file(proj: Path, n: int):
    for f in find_chapter_files(proj):
        m = CH_RX.search(Path(f).name)
        if m and int(m.group(1)) == n:
            return Path(f)
    return None


def archive(proj: Path, n: int, retry: int, quiet: bool) -> int:
    f = chapter_file(proj, n)
    if f is None:
        print(f'[错误] 找不到第 {n} 章的正文文件（期望 chapters/第{n:02d}章-*.md）。')
        print('       这不等于"通过"——先确认章节落盘了再归档。')
        return 2

    defects, results, words = [], [], ''
    for label, script in MECH:
        code, out = _run(script, [f], proj)
        if code is None:
            continue
        results.append((label, code))
        if code != 0:
            defects.append(label)
        if label == '字数':
            m = re.search(r'字数\s*[:：]\s*([\d,]+)', out)
            if m:
                words = m.group(1).replace(',', '')

    # ⚠️ 工单写**独立文件**，绝不改动 `04-质检档案.md`。
    #    初版直接 upsert 进 04 的汇总表，结果把用户项目里原有那一行的
    #    **9 列质量数据覆盖成 6 列**（实测踩到，已还原）。
    #    两份记录是**不同性质**的，不该混写：
    #      · `04-质检档案` = **人写的判断**（AI味自评 / 人味配额 / 四项测试 / 杠精结论）
    #      · `06-章节工单` = **机器写的度量**（机械结论 / 缺陷类型 / 返工轮次 / 成本）
    #    **教训：向"别人的文件"追加前，先看清它现有的列；覆盖式更新一个你没定义过 schema 的表，
    #    就是数据丢失。非破坏性的做法是"另建一张表 + join"，不是"改人家的表"。**
    wo = proj / WORKORDER
    if not wo.is_file():
        wo.write_text(
            f'# 章节工单（《{proj.name}》）—— 机器写的度量，人写的判断在 `{ARCHIVE}`\n\n'
            '> 由 `scripts/make_workorder.py` 追加。**不要手工改这张表**，\n'
            '> 它的价值在于"每章一行、字段固定"，手工改会破坏可统计性。\n\n'
            f'| {" | ".join(COLS)} |\n'
            f'|{"---|" * len(COLS)}\n',
            encoding='utf-8')

    txt = read_text(wo)
    row = (f'| 第{n}章 | {words or "?"} | '
           f'{"全绿" if not defects else f"{len(defects)} 项"} | '
           f'{"—" if not defects else "、".join(defects)} | {retry} | |')
    # 幂等：同章已有行 → 替换（重跑归档不叠加）
    lines = txt.split('\n')
    hit = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith(f'| 第{n}章 ') or ln.strip().startswith(f'| 第{n}章|'):
            hit = i
            break
    if hit is not None:
        lines[hit] = row
        action = '更新'
    else:
        lines.append(row)
        action = '追加'
    wo.write_text('\n'.join(lines), encoding='utf-8')

    if not quiet:
        print(f'  {action}工单：第{n}章')
        for label, code in results:
            print(f'    {"✓" if code == 0 else "✗"} {label}')
        print(f'    → {"全绿" if not defects else "缺陷：" + "、".join(defects)}'
              f'｜返工轮次 {retry}')
        if retry >= 2:
            print('    ⚠️ 返工轮次 ≥2 → 按铁律七**禁止原路重走**（换路或上报）。')
    return 1 if defects else 0


def report(proj: Path, since: str) -> int:
    wo = proj / WORKORDER
    if not wo.is_file():
        print(f'[提示] 还没有 {WORKORDER}（`--archive` 会在每章质检时自动创建并追加）。')
        return 0
    rows = []
    for ln in read_text(wo).split('\n'):
        c = [x.strip() for x in ln.strip().strip('|').split('|')]
        if len(c) >= 5 and re.match(r'^第\s*\d+\s*章$', c[0]):
            rows.append(c)
    if not rows:
        print('  ⚠ 工单还没有数据（`--archive` 会在每章质检时自动追加）。')
        return 0

    ok = sum(1 for r in rows if r[2] == '全绿')
    defects = Counter()
    retries = []
    for r in rows:
        if r[3] not in ('—', '', '-'):
            for d in re.split(r'[、,，]', r[3]):
                if d.strip():
                    defects[d.strip()] += 1
        try:
            retries.append(int(r[4]))
        except Exception:
            pass
    n = len(rows)

    print('=' * 70)
    print(f'章节工单汇总：{proj.name}')
    print('=' * 70)
    print(f'  已归档章数            {n}')
    fpy = ok / n * 100
    flag = '✓' if fpy >= 70 else '✗'
    print(f'  一次合格率（FPY）      {ok}/{n} = {fpy:.0f}%  {flag}')
    if fpy < 70:
        print('    ✗ 判据「≥70%」未通过 —— **停线分析**：先看下面的帕累托，')
        print('      只改头部那 1–2 类缺陷，不要同时动多条规则。')
    else:
        print('    ✓ 判据「≥70%」通过')
    if retries:
        sr = sorted(retries)
        print(f'  返工轮次 中位/p90/最大  {sr[len(sr) // 2]} / '
              f'{sr[int(len(sr) * 0.9)]} / {sr[-1]}')

    if defects:
        print('\n  【缺陷帕累托】—— 决定"该改哪条规则"')
        tot = sum(defects.values())
        for k, v in defects.most_common(8):
            bar = '█' * max(1, int(v / max(1, defects.most_common(1)[0][1]) * 24))
            print(f'    {k:<8} {v:>3} 次 ({v / tot * 100:>4.0f}%)  {bar}')
        head = [k for k, v in defects.most_common(2)]
        print(f'    → 头部两类：{"、".join(head)}（占 {sum(defects[k] for k in head) / tot * 100:.0f}%）'
              f' —— **先只改这两类**')
    else:
        print('\n  ✓ 尚未记录到机械缺陷')

    # 成本回填（可选：平台轨迹）
    probes = Path(PROBE_DIR).expanduser()
    if probes.is_dir():
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from audit_tokens import analyze_trace, find_traces
            tr = [analyze_trace(d) for _p, d in find_traces(probes, since)]
            if tr:
                calls = sum(t['calls'] for t in tr)
                ctxs = sorted(t['ctx_per_call'] for t in tr if t['calls'])
                med = ctxs[len(ctxs) // 2] if ctxs else 0
                print(f'\n  【成本（平台轨迹，{since or "全部"}）】')
                print(f'    会话 {len(tr)}｜调用 {calls:,}｜每次调用平均重发上下文（中位）{med:,.0f} tokens')
                if n and calls:
                    print(f'    → 折算 ≈ {calls / n:,.0f} 次调用/章（含质检与返工）')
        except Exception:
            pass
    print('\n  ⚠️ 说明：本表是"工单"。**返工轮次由 `--retry` 在执行时落盘**——')
    print('     不能靠事后回想，否则一次合格率就永远是估算。')
    return 0


def main():
    ap = argparse.ArgumentParser(description='章节工单（质检+归档 / 良率+帕累托）')
    ap.add_argument('project')
    ap.add_argument('--archive', type=int, metavar='章号',
                    help='★ 每章质检时调：跑全套机械脚本 + 归档一行工单')
    ap.add_argument('--retry', type=int, default=0,
                    help='本章的返工轮次（retryCount）。**必须当场写**，否则良率不可统计')
    ap.add_argument('--report', action='store_true', help='良率 + 缺陷帕累托 + 成本回填')
    ap.add_argument('--since', default='', help='--report 时只统计该日期之后的轨迹')
    ap.add_argument('--quiet', action='store_true', help='只在有缺陷时输出')
    args = ap.parse_args()

    proj = Path(args.project)
    if not proj.is_dir():
        print(f'[错误] 目录不存在：{args.project}')
        sys.exit(2)
    if args.archive:
        sys.exit(archive(proj, args.archive, args.retry, args.quiet))
    sys.exit(report(proj, args.since))


if __name__ == '__main__':
    main()
