#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
接口契约校验：齐备性 + 相邻章「状态 diff」（J-Novel 新增）
==========================================================

**为什么需要这个脚本**

并行写作里，第 N+1 章的写手**看不到第 N 章的正文**（也不该看——看了就要把整章装进它的上下文）。
它只知道一件事：契约里写死的「第 N 章结束时，人物在哪、什么情绪、知道什么、受没受伤」。

于是契约成了**并行写作的接口定义**，而它此前有一个致命的空档：

    · 全库引用「接口契约」12 处（读点齐全）
    · 但细纲规格里**没有契约栏位**（生成点缺失）→ 主编自由发挥，或者干脆不写
    · `scripts/` 里**没有任何检查**（校验点缺失）→ 写没写、对不对，都没人知道

「读点齐全」恰恰是最危险的状态——它让人以为这条机制很可靠。
实测核对：`outline-template.md` 与 `scripts/` 里搜「契约」都是 0 结果。

**这个脚本补的是校验点**，而且刻意放在**最便宜的位置**：

    跑在**派发写手之前**。此刻还没有 3000 字要改，两个数字对不上就直接改两个词——
    而已写完之后才发现的状态分叉，要动的是两章正文 + 重做缝合。

契约格式（键值，一行一键，定义见 `flows/phase2-planning.md` 细纲规格）：

    - 进入·位置时间: 灰炉村外围，开服第 4 小时
    - 进入·情绪: 憋着
    - 进入·已知: 知道矿脉被封
    - 进入·身体: 左臂有伤
    - 退出·位置时间: 铸炉塔底层
    ...
    - 承接要素: 上一章末段那句"……"
    - 交付钩子: 塔底那盏灯

判据两级（闸门太吵就没人看）：
    ✗ 失败（exit 1）：缺键 / 相邻章同一字段**不一致**
    ⚠ 提示：相邻章同一字段**部分一致**（一方包含另一方）——可能是补写细节，也可能是分叉

用法:
  python check_contract.py <项目目录>                     # 全量
  python check_contract.py <项目目录> --window 3 --brief   # 窗闸（只看最近 3 章，输出定长）
退出码: 0 = 齐备且无分叉；1 = 缺契约 / 状态分叉；2 = 没找到细纲（fail-closed，不是"通过"）
"""

import argparse
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import ensure_utf8_stdio      # noqa: E402

ensure_utf8_stdio()

sys.path.insert(0, str(Path(__file__).parent))
from _shared import read_text  # noqa: E402

OUTLINE_DIRS = ('细纲', '细纲目录', '大纲细纲')
CHAP_HEAD_RX = re.compile(r'(?m)^#{1,3}\s*第\s*(\d{1,4})\s*章')
# `- 进入·位置时间: xxx` / `- **退出·情绪**：xxx`
KEY_RX = re.compile(
    r'^\s*[-*]\s*\**\s*(进入·[\u4e00-\u9fff]{2,6}|退出·[\u4e00-\u9fff]{2,6}|承接要素|交付钩子)'
    r'\s*\**\s*[:：]\s*(.*?)\s*$')
REQ_IN = ('进入·位置时间', '进入·情绪', '进入·已知', '进入·身体')
REQ_OUT = ('退出·位置时间', '退出·情绪', '退出·已知', '退出·身体')


def find_outline_files(proj: Path):
    """找细纲文件。支持「一章一文件」与「一批文件装多章」两种布局。

    ⚠️ 真实项目用的是**批次文件**（`细纲/第01-10章细纲.md`），不是一章一文件。
       所以下面要按章级标题把文件切开——只按文件切会得到"0 章"。
    """
    out = []
    for d in OUTLINE_DIRS:
        sub = proj / d
        if sub.is_dir():
            out = [p for p in sorted(sub.glob('*.md'))
                   if not p.name.startswith('_') and not p.name.endswith(('.meta.md', '.bak.md'))]
            if out:
                return out
    return []


def split_chapters(text: str):
    """把一个细纲文件按章级标题切成 {章号: 该章正文}。"""
    hits = list(CHAP_HEAD_RX.finditer(text))
    chaps = {}
    for i, m in enumerate(hits):
        no = int(m.group(1))
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        seg = text[m.end():end]
        # 同一章号出现多次时取最长的那段（防止目录/引用行抢先）
        if no not in chaps or len(seg) > len(chaps[no]):
            chaps[no] = seg
    return chaps


def parse_contract(seg: str) -> dict:
    """从一章的正文里抽出契约键值。"""
    got = {}
    for line in seg.split('\n'):
        m = KEY_RX.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        val = re.sub(r'\*\*', '', val).strip()
        if key not in got or len(val) > len(got[key]):   # 取最长（模板行是空值）
            got[key] = val
    return got


def _norm(s: str) -> str:
    return re.sub(r'[\s，,、。；;：:·*`]', '', s or '')


def collect(proj: Path):
    """返回 {章号: (来源文件, 契约 dict)}。"""
    merged = {}
    for f in find_outline_files(proj):
        text = read_text(f)
        for no, seg in split_chapters(text).items():
            c = parse_contract(seg)
            if no in merged and len(merged[no][1]) >= len(c):
                continue
            merged[no] = (f, c)
    return merged


def check(proj: Path, window: int = 0, brief: bool = False) -> int:
    data = collect(proj)
    if not data:
        print('[错误] 没找到细纲文件（找过：细纲/ 细纲目录/ 大纲细纲/），或文件里没有章级标题。')
        print('       这不等于"通过"——脚本没看见细纲，先修结构再重跑。')
        return 2

    nums = sorted(data)
    if window > 0:
        nums = nums[-window:]

    print('\n' + '=' * 60)
    print('接口契约校验（并行写作的接口定义：缺了它，第 N+1 章只能猜第 N 章）')
    print('=' * 60)
    if not brief:
        print(f'  细纲章数：{len(data)}（校验最近 {len(nums)} 章）' if window
              else f'  细纲章数：{len(data)}')

    missing, partial, mismatch = [], [], []
    for no in nums:
        _, c = data[no]
        if not c:
            missing.append((no, '整块缺失', []))
            continue
        # 契约没写全但也不是完全没有 → 算半缺
        need = list(REQ_IN) + list(REQ_OUT)
        blanks = [k for k in need if k in c and not c[k]]
        absent = [k for k in need if k not in c]
        if blanks or absent:
            missing.append((no, '键缺', blanks + absent))

    for a, b in zip(nums, nums[1:]):
        ca, cb = data[a][1], data[b][1]
        if not ca or not cb:
            continue
        for kin, kout in zip(REQ_IN, REQ_OUT):
            va, vb = _norm(ca.get(kout, '')), _norm(cb.get(kin, ''))
            if not va or not vb:
                continue
            if va == vb:
                continue
            field = kout.split('·', 1)[1]
            if va in vb or vb in va:
                partial.append((a, b, field, ca.get(kout, ''), cb.get(kin, '')))
            else:
                mismatch.append((a, b, field, ca.get(kout, ''), cb.get(kin, '')))

    for no, kind, keys in missing:
        if kind == '整块缺失':
            print(f'  ✗ 第{no}章：**没有接口契约块** —— 写手只能靠猜（这正是"状态分叉"的来源）')
        else:
            print(f'  ✗ 第{no}章：契约缺 {len(keys)} 项 —— {("、".join(keys))[:70]}')

    for a, b, field, va, vb in mismatch:
        print(f'  ✗ 状态分叉：第{a}章退出·{field} ≠ 第{b}章进入·{field}')
        print(f'      第{a}章退出：{va[:46]}')
        print(f'      第{b}章进入：{vb[:46]}')

    for a, b, field, va, vb in partial:
        print(f'  ⚠ 部分一致（可能是补细节，也可能是分叉）：'
              f'第{a}章退出·{field} ↔ 第{b}章进入·{field}')

    fail = len(missing) + len(mismatch)
    if missing:
        print('\n  → 怎么补：见 `flows/phase2-planning.md` 细纲规格的「接口契约（冻结 · 机器可读）」'
              '——键名固定，一行一键。')
    if mismatch:
        print('\n  → 状态分叉必须在**派发写手之前**修：此刻还没有正文要改；'
              '写完再发现就要动两章 + 重做缝合。')

    if brief:
        print(f'\n[低费用·窗闸] 契约 {len(nums)} 章｜缺 {len(missing)}｜分叉 {len(mismatch)}'
              f'｜部分一致 {len(partial)}' + (' → 合格' if not fail else ' → 不合格'))
    elif not fail and not partial:
        print(f'\n  ✓ {len(nums)} 章契约齐备，相邻章状态一致')
    return 1 if fail else 0


def main():
    ap = argparse.ArgumentParser(description='接口契约校验（齐备性 + 相邻章状态 diff）')
    ap.add_argument('path', help='项目目录')
    ap.add_argument('--window', type=int, default=0,
                    help='只校验最近 N 章（0=全部）。流水线里应与「笔手领先上限」同值')
    ap.add_argument('--brief', action='store_true',
                    help='只输出问题（窗闸每章跑时用：保证报告定长）')
    args = ap.parse_args()

    proj = Path(args.path)
    if not proj.is_dir():
        print(f'[错误] 目录不存在：{args.path}')
        sys.exit(2)
    sys.exit(check(proj, args.window, args.brief))


if __name__ == '__main__':
    main()
