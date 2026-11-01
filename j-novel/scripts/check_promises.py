#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""承诺账校验：这本书还欠读者什么、欠了多久、这一章该还哪一笔。

用法:
    python scripts/check_promises.py <项目目录>                 # 逾期 / 欠债数 / 账龄 / 类型分布
    python scripts/check_promises.py <项目目录> --chapter 12     # 另加：本章应还清单
    python scripts/check_promises.py <项目目录> --current 9      # 手动指定"写到第几章"
    python scripts/check_promises.py <项目目录> --brief          # 一章一行

退出码:
    0 = 通过（或"本项目尚未建承诺账"——历史项目不判，只提示）
    1 = 有阻塞项（逾期未兑现 / 字段写错 / 兑现了但没记章号）
    2 = 取不到项目

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 为什么会有这个脚本

本体系的"连贯"此前只做了一半：**状态一致性**（人物在哪/知道什么/受没受伤、
三态标记、接口契约、边界指纹、`check_continuity`、`check_contract`）。
它们全部回答"**世界有没有自相矛盾**"。

而读者说的"不连贯"极少是矛盾——**是他被许下的东西没人管了**：
「前面那个谜呢」「这书越看越乱」「赢了跟没赢一样」「感情线怎么突然就……」。
这四句话在旧体系里**一条机制都抓不到**：伏笔表只记"埋/收"，不记"谁该在第几章收"；
状态台账记"他受伤了"，不记"这笔账什么时候还"。

**状态台账管作者的世界，承诺账管读者的期待。** 两个视角，两套账。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 它判什么、不判什么

判：**逾期（应兑现 ≤ 当前章而仍未兑现）／欠债总数／账龄／类型分布／字段完整性**。
不判：**这条承诺写得好不好**（那归 `plot-co-creation.md`：钩子候选必须带代价）。
     也判不了"这一章是不是真的还了"——状态是作者填的，脚本只能查"有没有人负责、到期没到期"。

## 生效点自适应（同 `check_story.py`）

全书没有 `08-承诺账.md` → **只提示、exit 0**（历史项目不追溯）。
一旦建了 → 逾期即阻塞。理由：一个"上线当天全库报错"的闸门，唯一的结果是被整体关掉。
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import find_chapter_files, read_text, ensure_utf8_stdio  # noqa: E402

LEDGER = '08-承诺账.md'
COLS = ['#', '承诺', '类型', '许下', '应兑现', '状态', '兑现于']
KINDS = ('谜', '钩子', '代价', '情感', '机制', '身份')
OPEN = ('未兑现', '未还', 'open', 'Open')
DONE = ('✓', '√', '已兑现', '已还', 'done', 'Done', 'closed')
DEAD = ('✗', '×', 'x', 'X', '已废弃', '作废', 'cancelled')

# 读者记忆的容量，不是文学标准：人同时悬着的问题约 5–9 个（8 是上限）；
# 一个悬念拖过 15 章，读者已经忘了它许下过。
MAX_OPEN = 8
MAX_AGE = 15

CH_NO_RX = re.compile(r'第\s*0*(\d{1,4})\s*章')


def _split_row(line: str):
    """按 | 切表格行（去掉首尾空段）。Markdown 表格写法：| a | b |"""
    s = line.strip()
    if not s.startswith('|'):
        return None
    cells = [c.strip() for c in s.strip('|').split('|')]
    return cells


def parse(text: str):
    """返回 (rows, problems, header_ok)。

    解析规则是**跨 agent 的接口契约，不许改**：
    定位含 `承诺`/`类型`/`应兑现` 的表头行 → 其后所有 `|` 行都是数据行（跳过 `|---|`）。
    列名逐字：`#` / `承诺` / `类型` / `许下` / `应兑现` / `状态` / `兑现于`。
    """
    rows, probs, header_ok = [], [], False
    lines = text.split('\n')
    start = None
    for i, l in enumerate(lines):
        if l.strip().startswith('|') and '承诺' in l and '应兑现' in l:
            start = i
            hdr = _split_row(l) or []
            header_ok = hdr[:len(COLS)] == COLS
            if not header_ok:
                probs.append('表头不是逐字的七列（必须是 `# | 承诺 | 类型 | 许下 | 应兑现 | 状态 | 兑现于`）'
                             f'—— 读到的是：{" | ".join(hdr)}')
            break
    if start is None:
        return [], ['找不到表头（需要含 `承诺` 与 `应兑现` 的表格行）'], False

    for l in lines[start + 1:]:
        if not l.strip().startswith('|'):
            if rows:
                break
            continue
        cells = _split_row(l)
        if not cells or set(''.join(cells)) <= set('-: '):
            continue
        if len(cells) < len(COLS):
            cells += [''] * (len(COLS) - len(cells))
        d = dict(zip(COLS, cells[:len(COLS)]))
        rows.append(d)
    return rows, probs, header_ok


def _int(s: str):
    m = re.search(r'(\d{1,4})', s or '')
    return int(m.group(1)) if m else None


def _status(s: str):
    v = (s or '').strip()
    if any(k in v for k in DEAD):
        return 'dead'
    if any(k in v for k in DONE):
        return 'done'
    if any(k in v for k in OPEN) or not v:
        return 'open'
    return '?'


def current_chapter(proj: Path, override=None):
    if override:
        return override
    files = find_chapter_files(proj)
    nums = [int(m.group(1)) for f in files if (m := CH_NO_RX.search(f.name))]
    return max(nums) if nums else None


def main():
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('project')
    ap.add_argument('--chapter', type=int, default=None, help='只看这一章应还的债')
    ap.add_argument('--current', type=int, default=None, help='写到第几章（默认取最大章节文件号）')
    ap.add_argument('--brief', action='store_true')
    a = ap.parse_args()

    proj = Path(a.project).expanduser()
    if not proj.exists():
        print(f'✗ 项目路径不存在：{proj}')
        return 2
    f = proj / LEDGER
    if not f.is_file():
        print(f'ℹ 本项目尚未建承诺账（{LEDGER} 不存在）。历史项目不追溯。')
        print('  → 建法：`guides/promises.md` 第一节（七列表格 + 六种类型）')
        print('  → 它的作用：**逾期 = 0**，这是"连贯"唯一可执行的判据。')
        return 0

    rows, probs, _ok = parse(read_text(f))
    cur = current_chapter(proj, a.current)
    if cur is None:
        print('✗ 找不到任何章节文件，也没有给 --current —— 无法判断"逾期"。')
        return 2

    errs, warns = list(probs), []
    open_rows, overdue, pay_now = [], [], []
    kinds = {k: 0 for k in KINDS}
    unknown_kind = 0

    for r in rows:
        tag = (r.get('#') or '?').strip() or '?'
        st = _status(r.get('状态', ''))
        due = _int(r.get('应兑现', ''))
        born = _int(r.get('许下', ''))
        kd = (r.get('类型') or '').strip()
        if kd in KINDS:
            kinds[kd] += 1
        elif kd:
            unknown_kind += 1
            warns.append(f'{tag}：类型「{kd}」不在六类里（谜/钩子/代价/情感/机制/身份）')
        if due is None:
            errs.append(f'{tag}：`应兑现` 不是章号 —— 没有到期日 = 永远不会逾期 = 这条账不存在')
        if st == 'done' and _int(r.get('兑现于', '')) is None:
            errs.append(f'{tag}：状态是「已兑现」但 `兑现于` 没写章号 —— 三章之后没人知道它到底还了没')
        if st == 'open':
            open_rows.append((tag, r, due, born))
            if due is not None and due <= cur:
                overdue.append((tag, r, due))
        if a.chapter and due == a.chapter and st == 'open':
            pay_now.append((tag, r))

    ages = [cur - b for _t, _r, _d, b in open_rows if b is not None]
    age_max = max(ages) if ages else 0

    if not a.brief:
        print(f'承诺账 · {proj.name}')
        print(f'  当前写到的章：第 {cur} 章 ｜ 条目 {len(rows)} 笔 ｜ 未兑现 {len(open_rows)} 笔 ｜ 最老账龄 {age_max} 章')
        dist = '／'.join(f'{k} {v}' for k, v in kinds.items() if v)
        print(f'  类型分布：{dist or "（空）"}{f"　＋未识别 {unknown_kind}" if unknown_kind else ""}')
    else:
        print(f'第{cur}章前 未兑现 {len(open_rows)}｜逾期 {len(overdue)}｜账龄 {age_max}')

    if overdue:
        print('\n✗ 逾期未兑现（读者最恨的一类）—— 还掉，或**显式改期**（在「承诺」列写一句为什么延）')
        for tag, r, due in overdue[:12]:
            print(f'  · {tag} 应兑现第 {due} 章 → 已到第 {cur} 章：{r.get("承诺", "")[:44]}')
        if len(overdue) > 12:
            print(f'  …… 还有 {len(overdue) - 12} 笔')

    if len(open_rows) > MAX_OPEN:
        warns.append(f'未兑现 {len(open_rows)} 笔 > 上限 {MAX_OPEN} —— '
                     f'同时悬着这么多，读者对任何一条都不再投入（"反正你也不会讲"）。先还 2 笔，再开新线')
    if age_max > MAX_AGE:
        warns.append(f'最老一笔欠了 {age_max} 章 > {MAX_AGE} —— 要么兑现，要么降级成长期背景并从账上撤下')
    if rows and not kinds.get('代价'):
        warns.append('一笔「代价」都没有 —— 主角从来没真正付过账，读者不会为他高兴')
    if rows and not kinds.get('情感'):
        warns.append('一笔「情感」都没有 —— 读者不会为人物着急，只会为情节好奇')

    if a.chapter:
        print(f'\n本章（第 {a.chapter} 章）必须还的债')
        if pay_now:
            for tag, r in pay_now:
                print(f'  · {tag}〔{r.get("类型", "")}〕{r.get("承诺", "")}'
                      f'（第 {r.get("许下", "?")} 章许下）')
            print('  → 抄进 brief 的交由写手兑现；**一章最多还 2 笔**（还 5 笔 = 交作业，像结算清单）')
        else:
            print('  （无。那本章只需**欠债**：写完把读者会记着的那件事登记成新条目）')

    for m in errs:
        print(f'  [阻塞] {m}')
    for m in warns:
        print(f'  [提示] {m}')

    print('-' * 62)
    if errs or overdue:
        print(f'合计：阻塞 {len(errs) + len(overdue)} 项 ｜ 提示 {len(warns)} 项')
        print('→ 逾期是硬阻塞：**还掉或显式改期**。无声拖过去 = 读者弃书的第一原因。')
        return 1
    print(f'合计：阻塞 0 项 ｜ 提示 {len(warns)} 项 ｜ 未兑现 {len(open_rows)}／{MAX_OPEN}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
