#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交接卡生成 / 校验（低消耗并行流水线的支点）
============================================

**为什么需要**

链式流水线把质量押在主编身上——定契约、转交"上一章真实结尾"、缝合章节边界。
而这三件事的传统做法都是**让主编读正文**。于是出现一个结构性矛盾：

    低消耗模式的全部收益来自"子代理上下文彼此隔离、不累积"
    （实测 cacheRead 占 97.8%，每一轮都要把整个上下文重发一遍），
    而主编是流水线里**活得最久**的角色——它每章要读两遍正文（一遍取结尾、一遍缝合），
    读过的每一章都留在它的上下文里，跟着它**之后每一次**调用被重发。
    → 子代理那边省下的钱，被主编一个人花回去。

`references/guides/parallel-workflow.md` 甚至白纸黑字写着主编"必持**全部真实章节**"，
理由是"100 万 token 窗口足够"——那是**窗口够不够**的论证，不是**成本**的论证。

**解法**：把"主编需要的信息"从正文里**裁出来**，裁的动作让脚本做（0 token）：

    · 上章真实结尾（原文，供本章开头承接）
    · 本章首段 / 末段（供缝合对照）
    · 三者的内容指纹（供**过期检测**）

主编从此只读这两小段（≤800 字）+ 退出码，**不读正文全文**。

**指纹的第二个作用：让「边界冻结」从纪律变成可检测的规则**

流水线里有个隐蔽的坑：笔手写第 N+1 章时，拿到的是第 N 章的**草稿**结尾；
而磨手随后打磨第 N 章，完全可能把结尾改写（"换落笔形态"这类指令恰恰鼓励改结尾）
→ **第 N 章定稿的结尾 ≠ 第 N+1 章开头所承接的结尾**。两个都"对"，拼起来断。

卡里存了指纹，`--check` 一比就知道边界有没有被动过：
    · 中间段落被改 → 指纹不变（正常，不影响边界，不必重做）
    · **边界段被改 → 指纹变化 → 判"卡已过期"** → 该章必须重跑缝合

用法:
  python make_handoff.py <项目目录> --chapter 12            # 打印第 12 章交接卡
  python make_handoff.py <项目目录> --chapter 12 --write    # 写入 chapters/_meta/handoff-12.md
  python make_handoff.py <项目目录> --all --write           # 为全部章节生成
  python make_handoff.py <项目目录> --check                 # 校验完备 + 过期（批闸用）
  python make_handoff.py <项目目录> --check --window 3      # 只校验最近 3 章（窗闸用）
退出码: 0 = 全部就绪；1 = 缺卡 / 卡已过期；2 = 没扫到章节（fail-closed，不是"通过"）
"""

import argparse
import hashlib
import io
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import ensure_utf8_stdio      # noqa: E402

ensure_utf8_stdio()

sys.path.insert(0, str(Path(__file__).parent))
from _shared import extract_body, find_chapter_files, read_text  # noqa: E402

META_DIR = '_meta'
DEFAULT_TAIL = 600          # 上章真实结尾取多少字（正文按规范是 500-800）
CARD_HEAD = re.compile(r'^#\s*交接卡\s*·\s*第\s*(\d{1,4})\s*章', re.M)
FP_LINE = re.compile(
    r'-\s*指纹:\s*first=(\w+)\s+last=(\w+)\s+prev=([\w-]+)')


def _chap_no(name: str) -> int:
    m = re.search(r'第\s*(\d{1,4})\s*章', name)
    return int(m.group(1)) if m else 10 ** 6


def _paras(text: str):
    """按**保留空行**的方式切段。

    ⚠ 不能用默认的 `extract_body(text)`——它压掉全部空行，整章会变成 1 段，
       "首段/末段"就都错了（这个坑让 check_aistyle 的段落 CV 挂了很久）。
    """
    body = extract_body(text, keep_blank=True)
    return [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]


def _fp(s: str) -> str:
    """段落的归一化指纹（去掉一切空白后取 sha1 前 8 位）。"""
    return hashlib.sha1(re.sub(r'\s+', '', s).encode('utf-8')).hexdigest()[:8]


def _tail(paras, n: int) -> str:
    """从末尾往回拼够 n 字，返回原文（供下章承接）。"""
    out, total = [], 0
    for p in reversed(paras):
        out.append(p)
        total += len(p)
        if total >= n:
            break
    return '\n\n'.join(reversed(out))


def build_card(files, idx: int, tail_chars: int) -> dict:
    """为一个章节算出交接卡的全部字段（纯计算，不写盘）。"""
    f = Path(files[idx])
    paras = _paras(read_text(f))
    if not paras:
        raise ValueError(f'{f.name} 取不到正文段落')
    first, last = paras[0], paras[-1]

    prev_last, prev_name = '', ''
    if idx > 0:
        pf = Path(files[idx - 1])
        pp = _paras(read_text(pf))
        if pp:
            prev_last, prev_name = pp[-1], pf.name

    ch = _chap_no(f.name)
    return {
        'chapter': ch,
        'name': f.name,
        'path': f,
        'first': first,
        'last': last,
        'prev_last': prev_last,
        'prev_name': prev_name,
        'tail': _tail(paras, tail_chars),
        'n_paras': len(paras),
        'fp_first': _fp(first),
        'fp_last': _fp(last),
        'fp_prev': _fp(prev_last) if prev_last else '-',
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }


def render_card(c: dict, tail_chars: int) -> str:
    prev_block = (f'### 上章真实结尾（原样抄自 `{c["prev_name"]}`，本章开头必须接住这里）\n\n'
                  f'{c["tail"]}\n'
                  if c['tail'] else
                  '### 上章真实结尾\n\n（本书首章，无上章）\n')
    return f"""# 交接卡 · 第{c['chapter']}章

- 来源: `{c['name']}`
- 生成: {c['generated']}
- 指纹: first={c['fp_first']} last={c['fp_last']} prev={c['fp_prev']}
- 正文段落数: {c['n_paras']}（结尾取末 {tail_chars} 字）

## 供主编：

{prev_block}
### 本章首段（缝合时对照上章结尾）

{c['first']}

### 本章末段（下一章开头要接这里）

{c['last']}

---
本卡由 `scripts/make_handoff.py` 从正文裁出，**主编读本卡即够，不必读正文全文**。
正文的「首段 / 末段」被改动后本卡即**过期**（跑 `--check` 会报出）；
中间段落改动不影响本卡。边界段是**接口**，只有主编能改——改了就要重跑缝合。
"""


def card_path(files, idx: int) -> Path:
    f = Path(files[idx])
    return f.parent / META_DIR / f'handoff-{_chap_no(f.name):02d}.md'


def parse_card(path: Path):
    """读回卡片里的指纹。返回 dict 或 None。"""
    if not path.is_file():
        return None
    txt = read_text(path)
    m = FP_LINE.search(txt)
    if not m:
        return None
    return {'first': m.group(1), 'last': m.group(2), 'prev': m.group(3)}


def check_cards(files, window: int = 0, verbose: bool = True) -> int:
    """校验交接卡：存在性 + 是否过期。返回问题数。"""
    targets = list(range(len(files)))
    if window > 0:
        targets = targets[-window:]

    missing, stale = [], []
    for i in targets:
        cp = card_path(files, i)
        rec = parse_card(cp)
        if not rec:
            missing.append(Path(files[i]).name)
            continue
        c = build_card(files, i, DEFAULT_TAIL)
        diffs = []
        if rec['first'] != c['fp_first']:
            diffs.append('首段')
        if rec['last'] != c['fp_last']:
            diffs.append('末段')
        if rec['prev'] != c['fp_prev'] and c['fp_prev'] != '-':
            diffs.append('上章末段')
        if diffs:
            stale.append((Path(files[i]).name, diffs))

    print('\n' + '=' * 60)
    print('交接卡校验（缺卡 = 主编将被迫读正文；过期 = 边界被动过）')
    print('=' * 60)
    if verbose:
        print(f'  检查范围：最近 {len(targets)} 章'
              f'（共 {len(files)} 章）' if window > 0 else f'  检查范围：全部 {len(files)} 章')

    for n in missing:
        print(f'  ✗ 缺交接卡：{n}')
    for n, diffs in stale:
        print(f'  ✗ 卡已过期：{n} —— 被动过的是**边界段**（{"、".join(diffs)}）')
        print('     → 边界是接口。这一章的定稿结尾与下一章开头所承接的内容已经不一致，')
        print('       **必须重跑缝合**（`check_continuity.py`），不能只重生成卡。')

    if missing:
        print(f'\n  → 生成：`python scripts/make_handoff.py <项目目录> --all --write`')
    if not missing and not stale:
        print(f'\n  ✓ {len(targets)} 章交接卡齐备且未过期')
    return len(missing) + len(stale)


def main():
    p = argparse.ArgumentParser(description='交接卡生成 / 校验')
    p.add_argument('path', help='项目目录')
    p.add_argument('--chapter', type=int, help='只处理某一章（章号）')
    p.add_argument('--all', action='store_true', help='处理全部章节')
    p.add_argument('--write', action='store_true', help='写入 chapters/_meta/handoff-NN.md')
    p.add_argument('--check', action='store_true', help='校验完备性与过期（批闸 / 窗闸用）')
    p.add_argument('--window', type=int, default=0,
                   help='--check 时只校验最近 N 章（流水线里应与笔手领先上限同值）')
    p.add_argument('--tail', type=int, default=DEFAULT_TAIL,
                   help=f'上章真实结尾取多少字（默认 {DEFAULT_TAIL}）')
    args = p.parse_args()

    proj = Path(args.path)
    if not proj.is_dir():
        print(f'[错误] 目录不存在：{args.path}')
        sys.exit(2)
    files = find_chapter_files(proj)
    if not files:
        print('[错误] 未找到章节文件 —— 章节应放在 `chapters/第NN章-标题.md`。')
        print('       这不等于"通过"：脚本没看见你的稿子，先修结构再重跑。')
        sys.exit(2)

    if args.check:
        sys.exit(1 if check_cards(files, args.window) else 0)

    targets = []
    if args.chapter is not None:
        targets = [i for i, f in enumerate(files) if _chap_no(f.name) == args.chapter]
        if not targets:
            print(f'[错误] 没有第 {args.chapter} 章')
            sys.exit(2)
    elif args.all:
        targets = list(range(len(files)))
    else:
        print('[错误] 需要 --chapter N 或 --all（或 --check）')
        sys.exit(2)

    wrote = 0
    for i in targets:
        c = build_card(files, i, args.tail)
        text = render_card(c, args.tail)
        if args.write:
            cp = card_path(files, i)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(text, encoding='utf-8')
            wrote += 1
            print(f'  ✓ 第{c["chapter"]:02d}章 → {cp.relative_to(proj)}'
                  f'（{c["n_paras"]} 段，结尾 {len(c["tail"])} 字）')
        else:
            print(text)
    if args.write:
        print(f'\n共写出 {wrote} 张交接卡。'
              f'主编此后**只读卡、不读正文**；卡过期用 --check 发现。')
    sys.exit(0)


if __name__ == '__main__':
    main()
