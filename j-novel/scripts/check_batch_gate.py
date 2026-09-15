#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批次验收闸门（Batch Gate）
==========================

为什么需要这个脚本：
    parallel-workflow.md 里写了"下一批派发前必须跑完 5 项验收"，
    但那是「自查清单」——主编一句"差不多了"就能跳过。
    实测事故：主编把第 7-8 章和第 9-10 章两批同时派出去，第 9 章在第 8 章
    还没收尾时就写完了，章与章之间就断了。

    本脚本把这 5 项验收变成「可执行 + 退出码」：
        exit 0 = 闸门通过，可派发下一批
        exit 1 = 有阻塞项，不许派发

它查什么：
    1. 章节完成连续性 —— 有没有「跳号完成」（第3章完成但第2章没完成）← 治乱序
    2. 字数达标 —— wordCountPass
    3. 质检痕迹 —— 04-质检档案.md 每章是否有记录
    4. 台账推进 —— 03/05 台账是否推进到已完成末章
    5. 章节边界 —— 上章结尾钩子人物，本章开头是否接住

用法:
    python check_batch_gate.py <项目目录>
    python check_batch_gate.py <项目目录> --json
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

_CJK = re.compile(r'[\u4e00-\u9fff]')
_ENCODINGS = ('utf-8', 'gb18030', 'gbk', 'utf-16', 'big5')


def read_text(path: Path) -> str:
    """稳健读文本。

    ⚠ 编码探测的陷阱（实测踩到）：用"中文占比最高"选编码时，utf-16 会把 UTF-8
    字节流错位解码，产生的乱码里混入大量 CJK 区字符，占比（0.39）反而高于正确的
    utf-8（0.057）——于是选错编码，JSON 解析直接崩。
    修复：**utf-8 能干净解码就用 utf-8**（现代文件绝大多数是 UTF-8），解码失败才探测遗留编码。
    """
    raw = path.read_bytes()
    try:
        s = raw.decode('utf-8')
        if '\ufffd' not in s:
            return s
    except Exception:
        pass
    best, br = None, -1.0
    for e in ('gb18030', 'gbk', 'big5', 'utf-16'):
        try:
            s = raw.decode(e)
        except Exception:
            continue
        r = len(_CJK.findall(s)) / max(1, len(s))
        if r > br:
            best, br = s, r
    return best if best is not None else raw.decode('utf-8', errors='ignore')


def read_json(path: Path):
    """读 JSON（JSON 几乎总是 UTF-8，直接按 utf-8 读，不做编码探测）。"""
    raw = path.read_bytes()
    for enc in ('utf-8-sig', 'utf-8'):
        try:
            return json.loads(raw.decode(enc))
        except Exception:
            continue
    raise ValueError(f'JSON 解析失败（已试 utf-8-sig / utf-8）：{path}')


def main():
    ap = argparse.ArgumentParser(description='批次验收闸门')
    ap.add_argument('project', help='项目目录（含 02-写作计划.json）')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    args = ap.parse_args()

    root = Path(args.project)
    if not root.exists():
        print(f'[错误] 目录不存在：{root}')
        sys.exit(2)

    plan_path = root / '02-写作计划.json'
    if not plan_path.exists():
        print(f'[错误] 找不到 02-写作计划.json —— {plan_path}')
        sys.exit(2)

    plan = read_json(plan_path)
    chapters = plan.get('chapters', [])
    if not chapters:
        print('[错误] 02-写作计划.json 里没有 chapters')
        sys.exit(2)

    cost_mode = plan.get('costMode', 'standard')
    by_no = {c.get('chapterNumber'): c for c in chapters}
    nums = sorted(n for n in by_no if isinstance(n, int))

    # ---------------- 1. 完成连续性（治乱序） ----------------
    completed = [n for n in nums if by_no[n].get('status') == 'completed']
    watermark = 0
    for n in nums:
        if by_no[n].get('status') == 'completed':
            watermark = n
        else:
            break

    out_of_order = [n for n in completed if n > watermark]

    # ---------------- 2. 字数 ----------------
    nopass = [n for n in completed if not by_no[n].get('wordCountPass')]

    # ---------------- 3. 质检痕迹 ----------------
    qc_path = root / '04-质检档案.md'
    qc_text = read_text(qc_path) if qc_path.exists() else ''
    missing_qc = []
    for n in completed:
        # 容忍 "第3章" / "第 3 章" / "### 第3章" 等写法
        if not re.search(rf'第\s*{n}\s*章', qc_text):
            missing_qc.append(n)

    # ---------------- 4. 台账推进 ----------------
    ledger_path = root / '05-创作台账.md'
    state_path = root / '03-状态台账.md'
    ledger_lag = []
    if completed:
        last = max(completed)
        lt = read_text(ledger_path) if ledger_path.exists() else ''
        st = read_text(state_path) if state_path.exists() else ''
        m1 = re.search(r'最近重读章号[：:]\s*第\s*(\d+)\s*章', lt)
        if not m1:
            ledger_lag.append(f'05-创作台账 未找到"最近重读章号"（应为第 {last} 章）')
        elif int(m1.group(1)) < last:
            ledger_lag.append(f'05-创作台账"最近重读章号"={m1.group(1)}，落后于已完成第 {last} 章')
        m2 = re.findall(r'第\s*(\d+)\s*章', st)
        if st and not m2:
            ledger_lag.append('03-状态台账 未记录章号')
        elif m2 and max(int(x) for x in m2) < last:
            ledger_lag.append(f'03-状态台账最新章号={max(int(x) for x in m2)}，落后于第 {last} 章')

    # ---------------- 5. 章节边界 ----------------
    boundary_fail = False
    boundary_note = ''
    continuity_script = Path(__file__).parent / 'check_continuity.py'
    if continuity_script.exists():
        try:
            r = subprocess.run(
                [sys.executable, str(continuity_script), str(root)],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=180,
            )
            boundary_fail = (r.returncode == 1)
            boundary_note = (r.stdout or '')[-400:]
        except Exception as e:
            boundary_note = f'（连续性检查执行失败：{e}）'
    else:
        boundary_note = '（未找到 check_continuity.py，跳过）'

    # ---------------- 5.5 章节边界的人工放行 ----------------
    # 设计意图：check_continuity 是【提示工具】不是硬闸门——"刻意留后章"是正常写法。
    # 主编复查后若判定正常，在 05-创作台账.md 里写一行显式放行记录即可，不必绕过闸门。
    #   格式：boundary-waived: 第8→9章，理由：该钩子第11章回收（至少 8 字）
    waived, waiver_note = False, ''
    if boundary_fail:
        lt_all = read_text(root / '05-创作台账.md') if (root / '05-创作台账.md').exists() else ''
        # 支持**多条** waiver（一章台账里可能放行了多个边界）
        _ws = re.findall(r'boundary-waived\s*[:：]\s*(.{8,})', lt_all)
        if _ws:
            waived = True
            waiver_note = ' ｜ '.join(w.strip()[:48] for w in _ws[:3])
            if len(_ws) > 3:
                waiver_note += f' ｜ …另有 {len(_ws) - 3} 条'

    # ---------------- 5.6 崩坏红线 1：连续 3 章同章末型 / 同开场类型 ----------------
    # 原设计里这条红线只写在文档里，靠 Agent 自己数——会话一切断就数不了（03/05 台账都不记这个）。
    # 但数据其实在 01-大纲.md 的章节表里（章末型 / 开场类型两列），所以可以机械检查。
    redline_note, redline_hit = '', False
    outline = root / '01-大纲.md'
    if outline.exists():
        rows = []
        for line in read_text(outline).split('\n'):
            if not line.strip().startswith('|'):
                continue
            cells = [c.strip().strip('*').strip() for c in line.strip().strip('|').split('|')]
            rows.append(cells)
        head, hdr_i = None, -1
        for i, c in enumerate(rows):
            if any('章末型' in x for x in c):
                head, hdr_i = c, i
                break
        if head:
            def col(name):
                for j, x in enumerate(head):
                    if name in x:
                        return j
                return -1
            i_end, i_open = col('章末型'), col('开场类型')
            data, skewed = [], 0
            for c in rows[hdr_i + 1:]:
                mnum = re.match(r'^第\s*(\d+)\s*章', c[0]) if c else None
                if not mnum:
                    continue
                # ⚠ 列数必须与表头一致——否则列会错位，把「章末型」读成「开场类型」
                if len(c) != len(head):
                    skewed += 1
                    continue
                data.append((int(mnum.group(1)), c[i_end][:6], c[i_open][:8]))
            if skewed:
                print(f'  [警告] 01-大纲 有 {skewed} 行与表头列数不一致，已跳过（列错位会误报红线）')
            # 「续接 / 跳接」是 SKILL 规定的**默认与过渡**开场类型（"默认续接"），
            # 连续出现是正常写法，不算崩坏——否则每本书开局都会误报。
            # 「章末型」不豁免：SKILL 明确规定"连续两章不得同型，尤其不得连续两章悬念型"。
            #
            # 语义：红线 1 问的是"**当下**是不是崩坏了"（不是"历史上有没有出现过"），
            # 所以只看**最近的 3 行**。
            #
            # ⚠️ 2026-09-14 修：此前是 `[x for x in data if x[idx]][-3:]` —— 先按
            # "该字段非空"过滤，再取最后 3 条。若中间某章该字段为空（如第 3 章没填
            # 章末型），取到的会是**第 1/2/4 章**，把"不相邻的 3 章"当成连续同型报出来。
            # 现改为：取最后 3 行原始记录 + **校验章号严格连续**（空值即不满足）。
            _EXEMPT_OPEN = {'续接', '跳接'}
            for field, idx in (('章末型', 1), ('开场类型', 2)):
                last3 = data[-3:]
                if len(last3) != 3:
                    continue
                if not all(last3[i][0] + 1 == last3[i + 1][0] for i in range(2)):
                    continue          # 章号不连续（有缺章/跳号）→ 不判
                if any(not x[idx] for x in last3):
                    continue          # 有章没填这个字段 → 不判（避免拿空值当"同型"）
                v = last3[0][idx]
                if len({x[idx] for x in last3}) != 1:
                    continue
                if field == '开场类型' and any(e in v for e in _EXEMPT_OPEN):
                    continue
                redline_hit = True
                redline_note = (f'连续 3 章同一{field}（{v}）：'
                                f'第 {last3[0][0]}/{last3[1][0]}/{last3[2][0]} 章')

    # ---------------- 报告 ----------------
    blockers = []
    if out_of_order:
        blockers.append(
            f'【乱序完成】这些章已 completed，但它们之前的章还没完成：第 {out_of_order} 章\n'
            f'      → 说明它们是在"上游章未收尾"时写的，极可能基于错误的上一章结尾。\n'
            f'      → 处理：让上游章先完成，再把乱序章按『链式流水线』重做（或至少逐对复查边界）。'
        )
    if nopass:
        blockers.append(f'【字数未达标】第 {nopass} 章')
    if missing_qc:
        blockers.append(f'【质检无记录】04-质检档案.md 里找不到这些章的记录：第 {missing_qc} 章')
    if ledger_lag:
        blockers.append('【台账未推进】' + '；'.join(ledger_lag))
    if boundary_fail and not waived:
        blockers.append(
            '【章节边界有悬空钩子】check_continuity.py 退出码 1 —— 逐条复查：'
            '"该接没接"（补一句桥接）还是"刻意留后章"（在细纲标注回收章）。\n'
            '      → 复查后若判定是刻意留白，在 05-创作台账.md 写一行放行记录即可：\n'
            '         boundary-waived: 第N→N+1章，理由：该钩子第M章回收'
        )
    if redline_hit:
        blockers.append(
            '【崩坏红线 1】' + redline_note + '\n'
            '      → 触发即停（execution-contract.md 第六节五拍处置）：停 / 名 / 锚 / 新计划 / 记。\n'
            '        不要把第 4 章也写成同一型——换型比改文便宜。'
        )

    if args.json:
        print(json.dumps({
            'completed': completed, 'watermark': watermark,
            'out_of_order': out_of_order, 'wordcount_fail': nopass,
            'missing_qc': missing_qc, 'ledger_lag': ledger_lag,
            'boundary_fail': boundary_fail,
            'boundary_waived': waived, 'boundary_waive_reason': waiver_note,
            'redline1_hit': redline_hit, 'redline1_note': redline_note,
            'costMode': cost_mode,
            'pass': not blockers,
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if not blockers else 1)

    print('=' * 62)
    print('批次验收闸门')
    print('=' * 62)
    print(f'项目：{root.name}')
    print(f'费用模式：{cost_mode} ｜ 已完成：第 {completed} 章 ｜ 连续水位线：第 {watermark} 章')
    # ⚠️ 提示类信息放在标题**之后**（此前在标题之前，输出像"顶部有两行孤立文字"）
    if waived:
        print(f'  [已放行] 章节边界：{waiver_note}')
    if redline_note:
        print(f'  [红线1] {redline_note}')
    print()

    if not blockers:
        print('✓ 闸门通过 —— 可以派发下一批（下一章：第 %d 章）' % (max(nums) + 1 if nums else 1))
        sys.exit(0)

    print(f'✗ 闸门未通过（{len(blockers)} 项阻塞）—— 不许派发下一批：\n')
    for b in blockers:
        print('  •', b)
    print()
    print('  提示：闸门存在的意义是"把前批收尾从主编自觉变成可验证清单"。')
    print('        台账/质检档案停更、章节乱序完成 —— 都是主编在长跑中漂移的信号。')
    sys.exit(1)


if __name__ == '__main__':
    main()
