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

它现在还多做一件同类的活：**实物覆盖率**（清单承诺 ↔ 正文兑现）。
「细纲承诺的东西，正文兑现了没有」和「_meta 里承诺的实物，正文出现了没有」是**同一类语义**
（都是"上游承诺 vs 下游兑现"的字符串级比对），所以放在同一个脚本里跑。
⚠️ 它是**只提示、不阻塞**的 —— 理由见下面「实物覆盖率」注释块（没有人类基线，
且脚本判断不了清单本身好不好）。**它不改变任何退出码。**

用法:
  python check_contract.py <项目目录>                     # 全量
  python check_contract.py <项目目录> --window 3 --brief   # 窗闸（只看最近 3 章，输出定长）
退出码: 0 = 齐备且无分叉；1 = 缺契约 / 状态分叉；2 = 没找到细纲（fail-closed，不是"通过"）
        实物覆盖率**从不**影响退出码（它只提示）。
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
from _shared import (META_NO_RX, find_meta_files, meta_body_path,  # noqa: E402
                     parse_concrete_list, read_text)

OUTLINE_DIRS = ('细纲', '细纲目录', '大纲细纲')
CHAP_HEAD_RX = re.compile(r'(?m)^#{1,3}\s*第\s*(\d{1,4})\s*章')
# ── 细纲目录里的「非细纲产物」过滤（2026-10-09 v7.1.1）────────────────
# 为什么需要：`细纲/` 里现在会放剧情卡、文风锚点等产物，而 '剧' U+5267 / '文' U+6587
# **都小于 '第' U+7B2C** → sorted() 之后它们排第一，抢走 `outlines[0]`，
# 于是任务包会从**剧情卡**里抽 `contract` / `voice_preset` / `batch_global`（实测复现）。
# ① 排除已知非细纲产物；② 优先"像细纲的"，未知新产物也不会抢位。
_NON_OUTLINE_RX = re.compile(r'^(剧情卡|文风锚点|锚点|作者画面|人物状态|任务包|交接)')
_OUTLINE_LIKE_RX = re.compile(r'^第|细纲')
# `- 进入·位置时间: xxx` / `- **退出·情绪**：xxx`
KEY_RX = re.compile(
    r'^\s*[-*]\s*\**\s*(进入·[\u4e00-\u9fff]{2,6}|退出·[\u4e00-\u9fff]{2,6}|承接要素|交付钩子)'
    r'\s*\**\s*[:：]\s*(.*?)\s*$')
REQ_IN = ('进入·位置时间', '进入·情绪', '进入·已知', '进入·身体')
REQ_OUT = ('退出·位置时间', '退出·情绪', '退出·已知', '退出·身体')

# ── 场景字数配额（2026-09-24 新增）────────────────────────────────
# 为什么查这个：字数占质检脚本调用的 **52%（737 次）**，是最大的一处返工。
# 而字数在动笔前**完全确定**——「整章 2200 字」对生成模型是一个 2200 字的区间搜索；
# 拆成「场景1 约500 / 场景2 约500 / 场景3 约500 / 场景4 约700」就变成 4 个小搜索，
# 且**每个场景写完就能对一次**，不必等整章。
# ⚠️ 真实项目实测：这条约定**只自发出现在第 1 章**（500+500+500+700=2200，加对了），
#    第 2–10 章全都没有 —— 所以它此前**既不是模板字段，也没有任何校验**。
SCENE_HEAD_RX = re.compile(r'(?m)^#{3,5}\s*场景\s*(\d+)')
QUOTA_LINE_RX = re.compile(r'字数配额\**\s*[:：]\s*约?\s*(\d+)')
QUOTA_HEAD_RX = re.compile(r'约\s*(\d+)\s*字')
TARGET_RX = re.compile(r'字数\**\s*[:：]\s*目标\s*(\d+)|字数\**\s*[:：]\s*(\d+)')
# 配额容差：允许 ±10% 或 ±60 字（取大者）——细纲是估算，不该要求精确到字
QUOTA_TOL_RATIO = 0.10
QUOTA_TOL_ABS = 60

# ── 实物覆盖率（本体层 · 具体性闸门，v7.0.0 新增）────────────────────
# 为什么查这个：现有 17 项硬指标 + 成本配额卡**全是频率指标**（多长 / 多密 / 多少次 /
# 多大比例）。频率满分 ≠ 本体像人 —— 而"这一章里的东西是不是只属于这本书"这一层，
# 此前**只有说明文字，没有生成点、也没有校验点**。
# v7.0.0 把它补成三站齐备：
#   生成点 = `chapters/_meta/<章名>.meta.md` 的「实物清单」段（动笔前定，3–8 项）
#   交付点 = 任务包 `concrete_list` 槽位（make_task_package.py 抽取）
#   校验点 = 本函数（字符串级覆盖率）+ 质检子代理的四问（理解级，见 specificity-gate.md 第三节）
#
# ★★ 为什么**只提示、不阻塞**（谁都不许把它改成硬失败）★★
#   ① **没有人类基线**：清单写了 5 项、正文兑现 3 项 —— 这到底是"写手漏用"还是"合理裁剪"？
#      没有任何实测数据能回答。凭感觉定一个 60% 的阈值，那是在凭空造数字。
#   ② **脚本判断不了清单本身好不好**：本函数是**字符串级**的"承诺 vs 兑现"，
#      它数不出这 5 项是不是专有物。一份**全是通用件**的清单也能 100% 兑现 ——
#      所以覆盖率低 ≠ 这一章差，覆盖率高 ≠ 这一章好。
#   ③ **铁律「不造假闸门」**：v6.9.0 造过一次"闲笔密度"判据 —— 实测好坏章节完全重叠
#      （22.0% vs 14.4%，方向甚至是反的），最终弃用并在 CHANGELOG 里诚实标注。
#      **宁可承认这一层只能提示，也不要造一个"看起来在工作"的正则判据。**
#   → 有牙齿的那一半在 `check_batch_gate.py` 的 `concrete_list_missing`：它只查**事实**
#     （清单在不在、够不够 3 项），不查**判断**（兑现率、清单质量）。
#
# 解析规则（跨 agent 的接口契约，不许改）：
#   定位 `## 实物清单` 标题行 → 向下取 `- ` 开头的列表项 → 遇到下一个 `## ` 或文件结束为止。
#   条目文本去掉首尾空白与全部空格后用于正文匹配。
# ★ 2026-10-09 v7.1.1：解析器与常量已**收归 `_shared`**（此前 `parse_concrete_list`
#   有 3 份实现、`CHAPTER_DIRS` 有 4 份 —— 本项目最忌讳的「双载体同名必漂」）。
#   这里只留本脚本专有的东西：下面的 `_squash`（本文档口径的匹配用）。


def find_outline_files(proj: Path):
    """找细纲文件。支持「一章一文件」与「一批文件装多章」两种布局。

    ⚠️ 真实项目用的是**批次文件**（`细纲/第01-10章细纲.md`），不是一章一文件。
       所以下面要按章级标题把文件切开——只按文件切会得到"0 章"。

    ⚠️⚠️ **必须排除"细纲目录里的非细纲产物"（2026-10-09 v7.1.1 修的真实回归）**
       判据不是"名字好记"，是**排序**：`细纲/` 里会放剧情卡、文风锚点等产物，而

           '剧' U+5267 、 '文' U+6587   **都小于**   '第' U+7B2C

       于是 `sorted(sub.glob('*.md'))` 之后它们**必然排在第一**，而
       `make_task_package.build()` 取的是 `outlines[0]` →
       **编译出的任务包会从剧情卡里抽 `contract` / `voice_preset` / `batch_global`**，
       表现为"明明细纲里有，却报 3 个【待填】"（实测复现；`文风锚点-*.md` 是同款旧坑）。

       两道防线：
       ① **排除**已知的非细纲产物（`_NON_OUTLINE_RX`）
       ② **优先**"像细纲的"（名字以 `第` 开头，或含"细纲"）—— 这样**新增未知产物时也不会抢位**；
          若一个都不像，则退回"排除后的全部"（老项目不受伤）
    """
    out = []
    for d in OUTLINE_DIRS:
        sub = proj / d
        if sub.is_dir():
            cands = [p for p in sorted(sub.glob('*.md'))
                     if not p.name.startswith('_')
                     and not p.name.endswith(('.meta.md', '.bak.md'))
                     and not _NON_OUTLINE_RX.match(p.name)]
            if not cands:
                continue
            pref = [p for p in cands if _OUTLINE_LIKE_RX.search(p.name)]
            out = pref or cands
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


def parse_scene_quotas(seg: str):
    """抽出一章的「场景字数配额」。返回 (配额列表, 章目标字数 or 0, 场景数)。

    配额取两个来源（优先显式栏位）：
      ① `- **字数配额**：约 800 字`（推荐写法）
      ② 场景标题行里的 `（约 500 字）`（真实项目自发形成的写法）
    """
    heads = list(SCENE_HEAD_RX.finditer(seg))
    if not heads:
        return [], 0, 0
    quotas = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(seg)
        block = seg[h.start():end]
        # ① 显式栏位优先（只看前 12 行，避免把场景正文里的"约 N 字"算进来）
        m = QUOTA_LINE_RX.search('\n'.join(block.split('\n')[:12]))
        if not m:
            # ② 退到场景标题行本身
            m = QUOTA_HEAD_RX.search(block.split('\n')[0])
        quotas.append(int(m.group(1)) if m else 0)
    mt = TARGET_RX.search(seg)
    target = int(mt.group(1) or mt.group(2)) if mt else 0
    return quotas, target, len(heads)


def _squash(s: str) -> str:
    """去掉**全部空白**。

    条目与正文**两侧都过一遍**：契约只规定"条目去空格"，但正文里一个换行就能把
    `半包受潮的` / `火柴` 拆成两行，从而把已经兑现的实物判成未兑现（假阴性）。
    消掉空白只会让判据更宽，不会引入假阳性 —— 对一个只提示不阻塞的检查，这是对的方向。
    """
    return re.sub(r'\s+', '', s or '')


def check_concrete_coverage(proj: Path, limit_nos=None, brief: bool = False):
    """实物覆盖率：**清单承诺 ↔ 正文兑现**（字符串级，**只提示、不阻塞**）。

    非阻塞的三条理由写在文件顶部「实物覆盖率」注释块里，一句话版：
    没有人类基线（不知道多少算合格）+ 脚本判断不了清单质量（通用件清单也能 100% 兑现）
    + 铁律「不造假闸门」（v6.9.0 的"闲笔密度"实测好坏章节完全重叠，已弃用）。

    `limit_nos` 给窗闸用（只看窗口内的章）；`brief` 时只打一行汇总，保证输出定长。
    返回 (已核对章数, 全兑现章数, 无清单章数, 找不到正文的章数)。
    """
    metas = find_meta_files(proj)
    rows, no_list, no_body = [], [], []
    for mp in metas:
        m = META_NO_RX.search(mp.name)
        no = int(m.group(1)) if m else 0
        if limit_nos is not None and no not in limit_nos:
            continue                       # 窗闸：窗口外的章不看不报
        label = f'第{no}章' if no else mp.stem
        items = parse_concrete_list(read_text(mp))
        if not items:
            no_list.append(label)
            continue
        bp = meta_body_path(mp)
        if not bp.is_file():
            no_body.append(label)          # 正文还没生成（或路径不符）→ 不核对，也不算错
            continue
        body = _squash(read_text(bp))
        hit = [it for it in items if _squash(it) in body]
        miss = [it for it in items if _squash(it) not in body]
        rows.append((label, items, hit, miss))

    if not (rows or no_list):
        return 0, 0, 0, 0

    if brief:
        _all = sum(1 for r in rows if not r[3])
        print(f'实物覆盖：{len(rows)} 章已核对｜全兑现 {_all}｜无清单 {len(no_list)}（不阻塞）')
        return len(rows), _all, len(no_list), len(no_body)

    print()
    print('-' * 60)
    print('实物覆盖率（清单承诺 ↔ 正文兑现；脚本只做字符串级比对，看不出清单本身好坏）')
    print('-' * 60)
    for label, items, hit, miss in rows:
        pct = int(round(100.0 * len(hit) / len(items))) if items else 0
        print(f'  {label}  清单 {len(items)} 项｜兑现 {len(hit)} 项（{pct}%）')
        if miss:
            print('        未兑现 %d 项：%s' % (len(miss), '、'.join(miss)))
    _all = sum(1 for r in rows if not r[3])
    if rows and _all == len(rows):
        print(f'  ✓ {len(rows)} 章的实物清单都在正文里兑现了')
    if no_body:
        print('  · 正文文件尚未生成，未核对：%s' % '、'.join(no_body))
    print(f'  {len(no_list)} 章没有实物清单（旧项目兼容，不算错）')
    if rows and _all < len(rows):
        print('  ⚠ 未兑现不一定是错（写手合理裁剪也算），这条**只提示不阻塞**：')
        print('     本层没有人类基线，且脚本判断不了"清单本身好不好"——'
              '质量判断在 `guides/specificity-gate.md` 第三节（质检子代理四问）。')
    return len(rows), _all, len(no_list), len(no_body)


def collect(proj: Path):
    """返回 {章号: (来源文件, 契约 dict, 该章细纲原文)}。"""
    merged = {}
    for f in find_outline_files(proj):
        text = read_text(f)
        for no, seg in split_chapters(text).items():
            c = parse_contract(seg)
            if no in merged and len(merged[no][1]) >= len(c):
                continue
            merged[no] = (f, c, seg)
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
        _, c, _seg = data[no]
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

    # ── 场景字数配额（2026-09-24 新增）──────────────────────────
    # 字数占质检脚本调用 52%（737 次），是最大的一处返工；而它在动笔前完全确定。
    # 整章目标 = 一个大搜索；拆到场景 = N 个小搜索，且每个场景写完就能对一次。
    quota_bad, quota_missing, quota_sum = [], [], 0
    for no in nums:
        _f, _c, seg = data[no]
        quotas, target, n_scene = parse_scene_quotas(seg)
        if not n_scene:
            continue                      # 没写「场景规划」的细纲（如串行任务卡）不判
        quota_sum += 1
        if not any(quotas):
            quota_missing.append((no, n_scene, target))
            continue
        got = sum(quotas)
        tol = max(QUOTA_TOL_ABS, int(target * QUOTA_TOL_RATIO))
        if target and abs(got - target) > tol:
            quota_bad.append((no, quotas, got, target, tol))
        elif 0 in quotas:
            zero = [i + 1 for i, q in enumerate(quotas) if q == 0]
            quota_bad.append((no, quotas, got, target, tol, zero))

    if quota_sum:
        print('\n' + '-' * 60)
        print('场景字数配额（把「整章一个大搜索」拆成「每场景一个小搜索」）')
        print('-' * 60)
        for no, n_scene, target in quota_missing:
            print(f'  ✗ 第{no}章：有 {n_scene} 个场景，但**一个字数配额都没有**'
                  f'（章目标 {target or "?"} 字）—— 写手只能整章写完再回头数字数')
        for item in quota_bad:
            no, quotas, got, target, tol = item[:5]
            extra = f'；场景 {item[5]} 缺配额' if len(item) > 5 else ''
            print(f'  ✗ 第{no}章：场景配额之和 {got} ≠ 章目标 {target}'
                  f'（差 {got - target:+d}，容差 ±{tol}）｜各场景 {quotas}{extra}')
        if not quota_missing and not quota_bad:
            print(f'  ✓ {quota_sum} 章的场景配额都与章目标一致')

    fail += len(quota_missing) + len(quota_bad)

    # ── 实物覆盖率（2026-10-07 v7.0.0 新增）────────────────────────
    # ⚠️ **这里的返回值一个都不进 `fail`** —— 覆盖率只提示、不阻塞（理由见文件顶部注释块）。
    #    它与上面的场景配额检查同属「上游承诺 ↔ 下游兑现」这一类，所以打印在一起。
    cov_n, cov_ok, cov_none, _cov_nb = check_concrete_coverage(
        proj, set(nums) if window > 0 else None, brief)

    if brief:
        print(f'\n[低费用·窗闸] 契约 {len(nums)} 章｜缺 {len(missing)}｜分叉 {len(mismatch)}'
              f'｜部分一致 {len(partial)}｜配额问题 {len(quota_missing) + len(quota_bad)}'
              f'｜实物覆盖 {cov_n} 章（全兑现 {cov_ok}）'
              + (' → 合格' if not fail else ' → 不合格'))
    elif not fail and not partial:
        print(f'\n  ✓ {len(nums)} 章契约齐备，相邻章状态一致')
    return 1 if fail else 0


def main():
    ap = argparse.ArgumentParser(
        description='接口契约校验（齐备性 + 相邻章状态 diff）',
        epilog=('另含「实物覆盖率」：读 chapters/_meta/*.meta.md 的「## 实物清单」段，'
                '数其中几项在正文里出现过（清单承诺 vs 正文兑现）。'
                '它是**提示项，不影响退出码** —— 这一层没有人类基线，'
                '且脚本判断不了清单本身好不好（那是质检子代理的活）。'))
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
