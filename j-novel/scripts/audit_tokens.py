#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成本审计：统计 LLM 调用次数、**每次调用平均重发多少上下文**、微步循环占比、重复读文件。

## ⚠️ 数据源在 2026-09-23 被修正（此前指向一个不存在的地方）

原版找 `<项目目录>/AGENT工作日志/session.jsonl` —— 实测**全盘搜索该目录从未被任何环节产出**：
项目里没有，脚本里也没有任何代码写它。也就是说这个"成本审计工具"**自己就是个孤儿**：
它有校验点，却指向一个不存在的生成点（正是本轮审计的"三站"病灶）。

**真实数据在平台侧**：`~/.workbuddy/traces/<pid>/trace_*.json`，每个文件 = 一个 agent 会话，
其 `trace.modelInfo` 带**真实 usage**：

```json
{"callCount": 319, "totalInputTokens": 91202261,
 "totalCachedTokens": 90888576, "totalOutputTokens": 190659}
```

★ **本脚本现在默认读这里。** 老路径仍作兜底（万一将来有人真的落盘日志）。

## 能测什么 / 不能测什么（不要过度声称）

**能测**：每个会话的真实 cacheRead / input / output / 调用数；
派生指标 **每次调用平均重发上下文 = totalCachedTokens / callCount**（成本的主项就在这里）；
成本集中度（前 K 个会话占多少）。

**不能测**：**单次调用之间上下文是否在增长**。
（`generation` span 的 `toolInput` 被**截断在 100k 字符**，每次长度都相同；轨迹里没有逐次 usage。）
所以"某个角色是不是在累积上下文"**无法从轨迹直接证明**——它是一个机制论证，
本脚本只给量级（"平均每次要重发多少"）。

用法:
    python audit_tokens.py <项目目录>                    # 兜底：找 AGENT工作日志/
    python audit_tokens.py --traces                     # ★ 主路径：读平台轨迹
    python audit_tokens.py --traces --since 2026-09-01  # 只看这段日期之后
    python audit_tokens.py --traces --json

判据（见 references/guides/token-efficiency.md）：
    调用数 > 25/章        → 违规（掉进微步循环）
    脚本调用 > 6/章       → 违规
    同一文件改写 > 6 次   → 违规
    同一指南会话内读 ≥2 次 → 违规
★ 新判据（真实数据驱动）：**每次调用平均重发上下文 > 100k tokens** → 上下文纪律已失守
    （这个数才是"97% 成本在 cacheRead"的具体形态；它是**每个角色都要看**的指标）
"""
import argparse
import glob
import json
import os
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
DEFAULT_TRACES = Path.home() / '.workbuddy' / 'traces'


def est_tokens(s: str) -> int:
    """粗估 token：CJK 约 1 token/字，其余约 1 token/3.5 字符。"""
    if not s:
        return 0
    c = len(CJK.findall(s))
    return int(c + (len(s) - c) / 3.5)


def find_logs(root: Path):
    """兜底路径：`AGENT工作日志` 下的 session.jsonl。

    ⚠️ 实测**没有任何环节会产出这个目录**（项目里没有、脚本里也没有写它的代码）。
       保留它只为兼容；真实数据请走 `find_traces()`。
    """
    for cand in (root / 'AGENT工作日志', root):
        if cand.is_dir():
            main = cand / 'session.jsonl'
            subs = sorted(cand.glob('subagents/*/session.jsonl'))
            if main.exists() or subs:
                return ([main] if main.exists() else []) + subs
    return sorted(root.rglob('session.jsonl'))


def find_traces(traces_dir: Path, since: str = ''):
    """定位平台轨迹文件，返回 [(路径, trace dict)]。"""
    out = []
    for f in sorted(glob.glob(str(traces_dir / '*' / 'trace_*.json'))):
        try:
            with open(f, encoding='utf-8') as fh:
                d = json.load(fh).get('trace', {})
        except Exception:
            continue
        started = (d.get('startedAt') or '')[:10]
        if since and started and started < since:
            continue
        if not d.get('modelInfo'):
            continue
        d['_file'] = f
        out.append((Path(f), d))
    return out


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


def analyze_trace(d: dict) -> dict:
    """从一个 trace 的 modelInfo 取真实 usage，并算派生指标。"""
    mi = d.get('modelInfo') or {}
    calls = int(mi.get('callCount') or 0)
    cached = int(mi.get('totalCachedTokens') or 0)
    inp = int(mi.get('totalInputTokens') or 0)
    out = int(mi.get('totalOutputTokens') or 0)
    return {
        'calls': calls, 'cached': cached, 'input': inp, 'output': out,
        # ★ 关键派生指标：**每次调用平均要重发多少上下文**
        #   成本的主项就在这里（cacheRead 占比 ~97%），
        #   而"每个角色每次调用带多重的上下文"正是本 SKILL 全部成本纪律的作用对象。
        'ctx_per_call': (cached / calls) if calls else 0,
        'amp': (cached / out) if out else 0,
        'date': (d.get('startedAt') or '')[:10],
        'agent': d.get('agentName') or '-',
    }


def report_traces(traces_dir: Path, since: str, as_json: bool) -> int:
    found = find_traces(traces_dir, since)
    if not found:
        print(f'✗ 没找到可用轨迹：{traces_dir}'
              + (f'（--since {since} 之后）' if since else ''))
        print('  期望 <traces>/<pid>/trace_*.json，且含 trace.modelInfo。')
        return 2

    rows = []
    for p, d in found:
        r = analyze_trace(d)
        r['file'] = f'{p.parent.name}/{p.name}'
        rows.append(r)

    tot = {k: sum(r[k] for r in rows) for k in ('calls', 'cached', 'input', 'output')}
    # ⚠️ `totalCachedTokens` 是 `totalInputTokens` 的**子集**（prompt 里命中缓存的那部分），
    #    **不是并列项**。初版把两者当成互斥去算占比 → 得出"cacheRead 49.6% / input 50.3%"，
    #    与文档里"97.8% 是 cacheRead"对不上，差点被当成"文档数字是错的"。
    #    实测核对：某轨迹 20,782,208 / 21,285,947 = **97.6%** —— 文档没写错。
    #    正确口径：prompt 总量 = inputTokens；其中 cached 是重发部分，新增 = input − cached。
    prompt = tot['input']
    fresh = max(0, tot['input'] - tot['cached'])
    grand = prompt + tot['output']
    ctxs = sorted(r['ctx_per_call'] for r in rows if r['calls'])
    ctx_sum = sum(r['cached'] for r in rows)

    def _pct(xs, q):
        if not xs:
            return 0
        return xs[min(len(xs) - 1, int(len(xs) * q))]

    if as_json:
        print(json.dumps({
            'source': str(traces_dir), 'since': since, 'sessions': len(rows),
            **tot,
            'prompt_total': prompt, 'fresh_input': fresh, 'grand_total': grand,
            'cache_share_of_prompt': round(tot['cached'] / max(1, prompt) * 100, 1),
            'output_share': round(tot['output'] / max(1, grand) * 100, 2),
            'amplification': round(prompt / max(1, tot['output']), 1),
            'ctx_per_call_median': round(_pct(ctxs, 0.5)),
            'ctx_per_call_p90': round(_pct(ctxs, 0.9)),
            'no_per_call_usage': True,
        }, ensure_ascii=False, indent=2))
        return 0

    print('=' * 74)
    print(f'成本基线（**真实 usage**，来自平台轨迹）')
    print('=' * 74)
    print(f'  数据源                        {traces_dir}' + (f'  （--since {since}）' if since else ''))
    print(f'  agent 会话（轨迹）数          {len(rows)}')
    print(f'  总 LLM 调用次数               {tot["calls"]:,}')
    print()
    print('  【真实 usage】口径：prompt = inputTokens（其中 cached 是命中缓存的**子集**）')
    print(f'    promptTokens（每轮发出的上下文）{prompt:>15,}  {prompt/max(1,grand)*100:5.1f}%')
    print(f'      ├ 其中 cacheRead（缓存命中）  {tot["cached"]:>15,}  '
          f'= prompt 的 {tot["cached"]/max(1,prompt)*100:.1f}%')
    print(f'      └ 其中新增输入（非缓存）      {fresh:>15,}  '
          f'= prompt 的 {fresh/max(1,prompt)*100:.1f}%')
    print(f'    outputTokens（模型产出）       {tot["output"]:>15,}  {tot["output"]/max(1,grand)*100:5.2f}%')
    print(f'    ★ 放大倍数（prompt/output）    {prompt/max(1,tot["output"]):>15,.0f}x')
    print()
    med, p90 = _pct(ctxs, 0.5), _pct(ctxs, 0.9)
    print('  【★ 每次调用平均重发上下文】—— 成本纪律的作用对象')
    print(f'    中位数                        {med:>15,.0f} tokens/次')
    print(f'    p90                           {p90:>15,.0f} tokens/次')
    if med > 100_000:
        print(f'    ✗ 判据「< 100k tokens/次」**未通过** —— 上下文纪律已失守：')
        print(f'      这意味着**每一轮都在重发十几万 token 的上下文**，而写入只有几百 token。')
    else:
        print(f'    ✓ 判据「< 100k tokens/次」通过')
    print()
    top = sorted(rows, key=lambda r: -r['cached'])[:5]
    if ctx_sum:
        print(f'  【成本集中度】前 5 个会话占 cacheRead 的 '
              f'{sum(r["cached"] for r in top)/ctx_sum*100:.0f}%')
    print('  【最贵的 5 个会话】')
    print('     调用    cacheRead     每次上下文   日期        （会话）')
    for r in top:
        print(f'    {r["calls"]:>5,}  {r["cached"]:>12,}  {r["ctx_per_call"]:>10,.0f}  '
              f'{r["date"]}  {r["file"][:18]}')
    print()
    print('  ⚠️ 本脚本**测不出**"单次调用之间上下文是否在增长"——')
    print('     `generation` span 的 toolInput 被截断在 100k 字符、每次等长，')
    print('     且轨迹里没有逐次 usage。所以"某个角色是否在累积上下文"仍是**机制论证**，')
    print('     这里只给量级（平均每次要重发多少）。')
    return 0


def main():
    ap = argparse.ArgumentParser(description='Agent 工作流成本审计')
    ap.add_argument('project', nargs='?',
                    help='项目目录（兜底路径，找 AGENT工作日志/）。'
                         '★ 主路径用 --traces')
    ap.add_argument('--traces', nargs='?', const=str(DEFAULT_TRACES), default=None,
                    help=f'读平台轨迹目录（默认为 {DEFAULT_TRACES}）——**真实 usage 在这里**')
    ap.add_argument('--since', default='', help='只看该日期（YYYY-MM-DD）之后的轨迹')
    ap.add_argument('--chapters', type=int, default=0, help='章数（用于按章折算）')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    args = ap.parse_args()

    # ★ 主路径：平台轨迹（真实 usage）。兜底：老的项目内日志目录。
    if args.traces is not None:
        return report_traces(Path(args.traces), args.since, args.json)

    if not args.project:
        ap.error('需要给项目目录，或用 --traces 读平台轨迹')

    root = Path(args.project)
    logs = find_logs(root)
    if not logs:
        print(f'✗ 未找到工作日志：{root}')
        print('  期望路径：<项目目录>/AGENT工作日志/session.jsonl')
        print()
        print('  ⚠️ 实测**没有任何环节会产出这个目录**（项目里没有、脚本里也没有写它的代码）。')
        print('     真实 usage 在平台侧 —— 请改用：')
        print(f'         python scripts/audit_tokens.py --traces')
        return 2
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
