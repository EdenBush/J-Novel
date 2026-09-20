#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成本审计：统计 LLM 调用次数、上下文峰值、微步循环占比、重复读文件。

用法:
    python audit_tokens.py <项目目录>          # 项目目录下应有 AGENT工作日志/
    python audit_tokens.py <项目目录> --json   # 输出 JSON

判据（见 references/guides/token-efficiency.md）：
    调用数 > 25/章        → 违规（掉进微步循环）
    脚本调用 > 6/章       → 违规
    同一文件改写 > 6 次   → 违规
    同一指南会话内读 ≥2 次 → 违规
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

CJK = re.compile(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]')
SKIP_TYPES = {
    'step/start', 'step/end', 'request/header', 'turn/start', 'turn/end',
    'assistant/chunk', 'tool-call-chunks', 'text-chunks',   # 流式分片，与 message/call 重复
}
GUIDE_RE = re.compile(r'references[\\/]{1,2}(guides|flows|prompts)[\\/]{1,2}([A-Za-z0-9_\-]+\.md)')
SCRIPT_RE = re.compile(r'check_[a-z_]+\.py|convert_to_txt\.py|audit_tokens\.py')
CHAP_RE = re.compile(r'第\s*\d+\s*章')


def est_tokens(s: str) -> int:
    """粗估 token：CJK 约 1 token/字，其余约 1 token/3.5 字符。"""
    if not s:
        return 0
    c = len(CJK.findall(s))
    return int(c + (len(s) - c) / 3.5)


def find_logs(root: Path):
    """定位 AGENT工作日志 下的全部 session.jsonl。"""
    for cand in (root / 'AGENT工作日志', root):
        if cand.is_dir():
            main = cand / 'session.jsonl'
            subs = sorted(cand.glob('subagents/*/session.jsonl'))
            if main.exists() or subs:
                return ([main] if main.exists() else []) + subs
    return sorted(root.rglob('session.jsonl'))


def read_lines(path: Path):
    for enc in ('utf-8', 'utf-8-sig', 'gb18030'):
        try:
            with open(path, encoding=enc, errors='strict') as fh:
                return fh.readlines()
        except (UnicodeDecodeError, LookupError):
            continue
    with open(path, encoding='utf-8', errors='replace') as fh:
        return fh.readlines()


def analyze_session(path: Path):
    """按 (turn, step) 分组还原真实 LLM 调用，统计各维度。

    优先使用日志中的真实 usage 字段（cacheReadTokens / inputTokens / outputTokens），
    缺失时才退回字符估算。真实数据是判据，估算是兜底。
    """
    groups = defaultdict(int)
    order = []
    calls = {}          # callId -> (tool_name, args)
    n_scripts = 0
    edits = Counter()   # 文件名 -> 改写次数
    guide_reads = Counter()
    qc_text = 0
    real = Counter()    # 真实 usage 累计
    has_usage = False

    for ln in read_lines(path):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        t = d.get('type', '')
        if t.startswith('session/'):
            continue
        data = d.get('data', {}) if isinstance(d.get('data'), dict) else {}
        u = data.get('usage')
        if isinstance(u, dict):
            has_usage = True
            for k in ('cacheReadTokens', 'inputTokens', 'outputTokens'):
                v = u.get(k)
                if isinstance(v, int):
                    real[k] += v
        if t in SKIP_TYPES:
            continue
        key = (data.get('turn'), data.get('step'))
        if key == (None, None):
            continue
        tk = est_tokens(ln)
        if key not in groups:
            order.append(key)
        groups[key] += tk

        if t == 'tool/call':
            nm = data.get('name', '')
            args = str(data.get('arguments', ''))
            calls[data.get('callId')] = (nm, args)
            if SCRIPT_RE.search(args):
                n_scripts += 1
            is_edit = nm in ('write', 'edit') or ('"command":"str_replace"' in args
                                                  or '"command":"create"' in args
                                                  or '"command":"insert"' in args)
            if is_edit:
                m = re.search(r'"path"\s*:\s*"([^"]+)"', args) or \
                    re.search(r'"file_path"\s*:\s*"([^"]+)"', args)
                if m:
                    edits[Path(m.group(1).replace('\\\\', '/')).name] += 1
            g = GUIDE_RE.search(args)
            if g:
                guide_reads[g.group(2)] += 1
        elif t in ('assistant/message', 'assistant/chunk'):
            txt = json.dumps(data.get('message', data), ensure_ascii=False)
            if re.search(r'超标|改写|复跑|重跑|质检|自评|不合格|FAIL', txt):
                qc_text += 1

    ticks = [groups[k] for k in order]
    n = len(ticks)
    content = sum(ticks)
    peak = content
    billed = sum(sum(ticks[:i + 1]) for i in range(n))
    return dict(calls=n, content=content, peak=peak, billed=billed, has_usage=has_usage,
                real_cache=real['cacheReadTokens'], real_input=real['inputTokens'],
                real_output=real['outputTokens'],
                scripts=n_scripts, edits=edits, guides=guide_reads, qc_text=qc_text)


def main():
    ap = argparse.ArgumentParser(description='Agent 工作流成本审计')
    ap.add_argument('project', help='项目目录（含 AGENT工作日志/）')
    ap.add_argument('--chapters', type=int, default=0, help='章数（用于按章折算）')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    args = ap.parse_args()

    root = Path(args.project)
    logs = find_logs(root)
    if not logs:
        print(f'✗ 未找到工作日志：{root}')
        print('  期望路径：<项目目录>/AGENT工作日志/session.jsonl')
        return 2

    totals = dict(calls=0, content=0, peak=0, billed=0, scripts=0, qc_text=0,
                  real_cache=0, real_input=0, real_output=0)
    has_usage = False
    edits = Counter()
    guides = Counter()
    per_session = []
    peaks = []
    for p in logs:
        r = analyze_session(p)
        per_session.append((p.parent.name, r))
        has_usage = has_usage or r['has_usage']
        for k in totals:
            totals[k] += r[k]
        peaks.append(max(r['peak'], r['real_cache']))
        edits.update(r['edits'])
        guides.update(r['guides'])

    n = max(1, totals['calls'])
    chapters = args.chapters or 0
    use_real = has_usage and totals['real_cache'] > 0
    real_total = totals['real_cache'] + totals['real_input'] + totals['real_output']

    if args.json:
        print(json.dumps({
            'sessions': len(logs), **totals,
            'used_real_usage': use_real,
            'real_total': real_total,
            'amplification': round(totals['real_cache'] / max(1, totals['real_output'])),
            'edits': edits.most_common(20),
            'guide_reads': guides.most_common(20),
            'chapters': chapters,
            'calls_per_chapter': round(totals['calls'] / chapters, 1) if chapters else None,
        }, ensure_ascii=False, indent=2))
        return 0

    print('=' * 70)
    print(f'成本审计：{root}')
    print('=' * 70)
    print(f'  会话数                {len(logs)}')
    print(f'  LLM 调用次数          {totals["calls"]}')
    print(f'  日志内新增内容        ≈ {totals["content"]/1000:.0f}k tokens')

    if use_real:
        print()
        print('  【真实 usage（来自日志）】')
        print(f'    cacheReadTokens（上下文重发） {totals["real_cache"]:>13,}  '
              f'{totals["real_cache"]/real_total*100:5.1f}%')
        print(f'    inputTokens（新增输入）       {totals["real_input"]:>13,}  '
              f'{totals["real_input"]/real_total*100:5.1f}%')
        print(f'    outputTokens（模型产出）      {totals["real_output"]:>13,}  '
              f'{totals["real_output"]/real_total*100:5.1f}%')
        print(f'    ─────────────────────────────────────────')
        print(f'    合计                          {real_total:>13,}  ≈ {real_total/1e6:.1f}M tokens')
        print(f'    放大倍数 = cacheRead/output   {totals["real_cache"]/max(1,totals["real_output"]):.0f}×')
        if chapters:
            print(f'    按 {chapters} 章折算              {real_total/chapters/1e6:.1f}M tokens / 章')
    else:
        print(f'  上下文流量（估算）    ≈ {totals["billed"]/1e6:.1f}M tokens'
              f'  （日志无 usage 字段，退回字符估算）')

    if chapters:
        print()
        print(f'  按 {chapters} 章折算        {totals["calls"]/chapters:.0f} 次调用/章 · '
              f'{totals["scripts"]/chapters:.1f} 次脚本/章')
    else:
        print(f'  质检脚本调用次数      {totals["scripts"]}'
              f'   （加 --chapters N 可折算到每章）')

    # ⚠️ 2026-09-14 修：qc_text 一直被统计、被塞进 totals、被返回，
    #    **却从未在报告里输出**——一个"算了但不用"的死统计（与 SKILL 自己诊断的
    #    "声明了没接上"同型）。它本身是有用信号：助手消息里出现"超标/改写/复跑/质检"
    #    的次数远超章数，说明在反复自评 → 微步循环。所以选择**输出它**而不是删掉。
    if totals.get('qc_text'):
        print(f'  质检/改写类发言        {totals["qc_text"]} 次'
              f'   （含"超标/改写/复跑/质检/不合格"的助手消息数）')
        if chapters and totals['qc_text'] / chapters > 12:
            print(f'    ← 约 {totals["qc_text"]/chapters:.0f} 次/章，偏高：'
                  f'多半是"改一项验一项"，应改为一次批量改写')
    print()

    print('  改写最多的文件（>6 次 = 微步循环信号）:')
    if edits:
        for k, v in edits.most_common(12):
            flag = '  ← 违规' if v > 6 else ''
            print(f'    {v:>3} 次  {k}{flag}')
    else:
        print('    （无改写记录）')
    print()

    print('  指南被读次数（同一会话内 ≥2 次 = 重复读）:')
    if guides:
        for k, v in guides.most_common(12):
            flag = '  ← 重复读' if v >= 2 else ''
            print(f'    {v:>3} 次  {k}{flag}')
    else:
        print('    （无指南读取记录）')
    print()

    verdict = []
    if chapters and totals['calls'] / chapters > 25:
        verdict.append(f'调用 {totals["calls"]/chapters:.0f} 次/章 > 25 —— 掉进微步循环')
    if chapters and totals['scripts'] / chapters > 6:
        verdict.append(f'脚本 {totals["scripts"]/chapters:.1f} 次/章 > 6 —— 未批量跑脚本')
    over = [(k, v) for k, v in edits.items() if v > 6]
    if over:
        verdict.append(f'{len(over)} 个文件被改写 >6 次 —— 未批量改写')
    dup = [(k, v) for k, v in guides.items() if v >= 2]
    if dup:
        verdict.append(f'{len(dup)} 本指南被重复读 —— 违反"读一次复用"')
    print('  判据：', '；'.join(verdict) if verdict else '未发现违规')
    return 1 if verdict else 0


if __name__ == '__main__':
    sys.exit(main())
