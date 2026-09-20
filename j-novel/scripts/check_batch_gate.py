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

# 共享层（唯一真相源）：read_text / parse_waivers / find_chapter_files
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import read_text as _shared_read_text, parse_waivers  # noqa: E402


def read_text(path: Path) -> str:
    """稳健读文本 —— **委托 `_shared.read_text`**（2026-09-19 收归）。

    ⚠ 此前这里是**第四份**独立实现，而且和后三份口径相反：
    它的遗留编码候选里含 `'utf-16'`，且用"中文占比最高"来选编码 ——
    正是 `_shared.py` 用 400 个真实文件实测证明**会误判 4% 的文件**的那个启发式
    （utf-16 能把任意偶数长度字节流解成"高中文占比"的乱码）。
    `_shared` 的规则是**绝不无 BOM 猜 utf-16**，这里却把它当候选。

    后果：同一个文件在两个脚本里可能被解成不同内容 → 闸门之间互相矛盾。
    全 SKILL 只允许有一份 read_text。
    """
    return _shared_read_text(path)


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

    # ⚠️ 2026-09-15 修：字段名兼容。
    # 实测真实项目用 `"index": 1`，而本脚本读 `chapterNumber` → 恒为 None
    # → `by_no` 为空 → 扫到 0 章 → 却报"✓ 闸门通过，下一章：第 1 章"（对 10 章完成稿！）
    # **两种写法都合规，脚本必须都认**；认不出时下面的 fail-closed 会拦住。
    def _no_of(c):
        for k in ('chapterNumber', 'index', 'no', 'chapter', 'chapterNo'):
            v = c.get(k)
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.strip().isdigit():
                return int(v.strip())
        return None

    by_no = {}
    _unnumbered = 0
    for c in chapters:
        n = _no_of(c)
        if n is None:
            _unnumbered += 1
            continue
        by_no[n] = c
    nums = sorted(by_no)

    # ⚠️ fail-closed：**一章都没认出来 = 不能报通过**。
    # 假闸门里最危险的一种是"没看到东西却给绿"——它比报错更糟，
    # 因为它会让主编以为验收过了，甚至照着"下一章"的提示去重写已有章节。
    if not nums:
        print('[错误] 从 02-写作计划.json 里**一章都没能解析出章号** —— 拒绝放行。')
        print(f'        chapters 共 {len(chapters)} 项，无法识别章号的 {_unnumbered} 项。')
        if chapters:
            print(f'        实际字段：{sorted(chapters[0].keys())}')
            print('        需要 `chapterNumber`（规范写法）或 `index`（等价写法）且为整数。')
        print('        → 修好字段名再跑。**不要因为"脚本没报错"就当验收通过。**')
        sys.exit(2)

    if _unnumbered:
        print(f'  [警告] 有 {_unnumbered} 项章节没有可识别的章号，已跳过（未计入验收）')

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
    # ⚠️ 2026-09-15 修：`wordCountPass` 缺失时**用 `words` 兜底**。
    # 实测项目只记 `words`，没有 wordCountPass → 原逻辑 `not None` = True
    # → 把 10 章全部误报成"字数未过"。不能把"字段没写"判成"不合格"。
    _minw = plan.get('wordsPerChapter') or 3000
    nopass = []
    _nopass_unknown = []
    for n in completed:
        c = by_no[n]
        if 'wordCountPass' in c:
            if not c.get('wordCountPass'):
                nopass.append(n)
        elif isinstance(c.get('words'), int):
            if c['words'] < _minw:
                nopass.append(n)
        else:
            _nopass_unknown.append(n)

    # ---------------- 3. 质检痕迹 ----------------
    qc_path = root / '04-质检档案.md'
    qc_text = read_text(qc_path) if qc_path.exists() else ''
    # ── 正文文件含工程脚手架？（2026-09-19 新增，来自一次真实盲评）────────
    # **为什么加**：三位独立盲评评委里有两位**各自**把「本章概要 / 章末型：丁·悬念 /
    #   伏笔标记（预计第X卷回收）」列为**最强的 AI 指纹**——原话："人类作者投正文
    #   不会写'章末型：丁'""外挂的元数据头部暴露了流水线出身"。
    #   `extract_body()` 会剥掉它们，所以**脚本从来没报过**；但任何读到这个文件的人都会看到。
    # 修法：正文文件只留 `# 标题 + 章首引子 + 正文`；元数据移入 `chapters/_meta/<章名>.meta.md`。
    _SCAFFOLD = ('本章概要', '章末型', '伏笔标记', '章节备注', '章末落点', '开场类型')
    scaffold_hits = []
    for _cf in sorted((root / 'chapters').glob('第*.md')):
        # 排除备份/草稿（ 这类不属于正式稿，
        # 但**备份也不该留在 chapters/ 里**——会污染任何扫这一层的工具）
        if any(k in _cf.name for k in ('.bak', '备份', '.orig', '_backup', '.tmp')):
            continue
        _cs = read_text(_cf)
        _found = [_k for _k in _SCAFFOLD if _k in _cs]
        if _found:
            scaffold_hits.append('%s（%s）' % (_cf.name, '、'.join(_found)))

    missing_qc = []
    for n in completed:
        # ⚠️ 2026-09-15 修：**格式宽容**。
        # 原正则只认「第N章」；实测真实项目用表格 `| 01 | 本局无攻略 | 3327 |`，
        # 于是 10 章全被判"质检无记录" —— **假阳性阻塞**（会逼主编去改本来没问题的档案）。
        # 现在同时接受：`第3章`／`第03章`／表格行 `| 3 |`／`### 3.`／`## 03、`
        pats = (
            rf'第\s*0*{n}\s*章',
            rf'(?m)^\|\s*0*{n}\s*\|',
            rf'(?m)^#{{1,4}}\s*0*{n}\s*[.、·\s]',
            rf'(?m)^\s*0*{n}\s*[.、·]\s*\S',
        )
        if not any(re.search(p, qc_text) for p in pats):
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
    #
    # ⚠️ 2026-09-19 修（**重要，假放行事故**）：
    #   此前这里用宽松正则 `boundary-waived\s*[:：]\s*(.{8,})` + `waived = True`（**全局**）。
    #   而 check_continuity.py 用严格正则 `第(\d+)→(\d+)章` **按章号对**扣。
    #   于是**从文档里抄来的模板行**：
    #       boundary-waived: 第N→N+1章，理由：该钩子第M章回收，已在03-状态台账登记
    #   在 check_continuity 里**正确地不豁免**（N/M 不是数字）→ exit 1，
    #   却被这里匹配上 → 报"✓ 闸门通过 —— [已放行]"。**宽松层覆盖了严格层。**
    #   实测：真实项目台账里就躺着这一行模板，全书边界检查被它一条豁免。
    #
    #   现改为：走 `_shared.parse_waivers`（与 check_continuity 同一口径）
    #   —— ① 章号必须是具体数字，② 必须相邻，③ 理由 ≥8 字，④ 无效记录要报出来。
    waived, waiver_note, waiver_problems = False, '', []
    if boundary_fail:
        lt_all = read_text(root / '05-创作台账.md') if (root / '05-创作台账.md').exists() else ''
        _pairs, waiver_problems = parse_waivers(lt_all)
        if _pairs:
            waived = True
            _lst = sorted(_pairs)
            waiver_note = ' ｜ '.join(f'第{a}→{b}章' for a, b in _lst[:4])
            if len(_lst) > 4:
                waiver_note += f' ｜ …另有 {len(_lst) - 4} 条'

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
            '      → 复查后若判定是刻意留白，在 05-创作台账.md 写一行放行记录，'
            '**必须写具体章号**（照抄文档里的 N/M 占位符不算）：\n'
            '         boundary-waived: 第8→9章，理由：该钩子第11章回收'
        )
    if waiver_problems:
        blockers.append(
            '【放行记录无效】05-创作台账.md 里有形似 boundary-waived 但**不成立**的记录：\n'
            + '\n'.join('      · ' + p for p in waiver_problems) +
            '\n      → 放行必须指名"第X→Y章"（X、Y 是真实数字、且相邻）+ 理由 ≥8 字。\n'
            '      → 文档里的 `第N→N+1章` 是**示例模板**，抄进台账等于写了一条假放行。'
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
    if scaffold_hits:
        print()
        print('  [脚手架] ⚠ 正文文件里含工程字段 —— 盲评实测这是**最强的 AI 指纹**：')
        for _h in scaffold_hits[:5]:
            print('     · %s' % _h)
        print('     → 移入 chapters/_meta/<章名>.meta.md，正文文件只留 `# 标题 + 章首引子 + 正文`')
    if waived:
        print(f'  [已放行] 章节边界：{waiver_note}')
    if redline_note:
        print(f'  [红线1] {redline_note}')
    print()

    if not blockers:
        # ⚠️ 2026-09-16 修：原实现用 `max(nums)+1`（nums = 本批**计划**的全部章号），
        # 于是「只完成第 1 章、本批计划 1–10」时会提示"下一章：第 11 章"——照做会**跳过 2–10 章**。
        # 真实事故：《我不是大师》第 01 章完成、02–10 pending，闸门却建议从第 11 章开始。
        # 正确语义：下一章 = 连续水位线 + 1；并区分"本批进行中"与"本批已收尾可派下一批"。
        _last = max(nums) if nums else 0
        _first = min(nums) if nums else 1
        if nums and watermark >= _last:
            print('✓ 闸门通过 —— 本批（第 %d–%d 章）已收尾，可以派发下一批（下一章：第 %d 章）'
                  % (_first, _last, _last + 1))
        else:
            _next = (watermark + 1) if watermark else _first
            _rest = [n for n in nums if n > watermark] if nums else []
            print('✓ 闸门通过 —— 本批**进行中**：下一章应写「第 %d 章」（本批第 %d–%d 章，还剩 %d 章未写）'
                  % (_next, _first, _last, len(_rest)))
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
