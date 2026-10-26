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

⚠️ **2026-10-10 v7.3.0：`--retry` 不填时不再记 0，改记「未知」（`?`）。**
  为什么改：旧默认值 0 让"**没填**"与"**真的是 0**"在工单里**长得一样** →
  `check_batch_gate.py` 分不出这一章到底返工过没有，于是那条"返工过 → 必须交出
  改写方案表"的校验只能做提示、不能做闸门（一个软闸门 = 用户实测到的"跳过"）。
  分成两种写法之后：**`?` = 未知（判不了）／`0` = 明确知道没返工** ——
  前者既不当 0、也不当"返工过"（判据必须保守），只**留痕**（v7.1.1：跳过必须留痕）。
  ⚠️ **读取方必须一起改口径**：`report()` 里 `?` **不参与返工轮次统计**、
  也**不假装是 0**（两边口径不一致就是本 SKILL 最忌讳的"双载体不一致"）。

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
  ⚠️ `--retry` **不填 = 记「未知」（`?`），不是记 0** —— 返工轮次必须**当场**写，
     事后回想补不回来（一次合格率会永远按"未知"算）。
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
COLS = ['章节', '字数', '机械结论', '缺陷类型', '返工轮次', '分诊档', '备注']

# ══ 分诊档（2026-10-21 新增）══════════════════════════════════════════════
#   `deai-entry.md` 0.4 规定"档位必须落痕，没写 = 没分诊 = 动作 7 未做"。
#   **由谁写决定了它写不写得出来**：让人手填，跳过流程的 Agent 就不会填
#   （这正是 `--retry` 的教训——全靠自觉 = 等于没有）。
#   所以档位由**脚本按节律报告里的两个数自动算**，不新增任何人工字段：
#     · 硬门失败项数 —— 从报告末行 `✗ N 项未达标（硬）` 抓
#     · 人类区间命中率 —— 从 `人类区间命中率：a/b = P%` 抓，P < 25 → R2
#     · 返工轮次 —— `--retry ≥ 2` → R2
#   ⚠️ **本列只判 R1／R2**。R3（分布档，"同一项 ≥3 章同向偏"）**单章算不出来**，
#      要跨章统计，由批末 `check_batch_gate.py` 的 `--project` 体检判 ——
#      这里**不许**把它伪造成 R3（单章信息不足就写 R3 = 假数据）。
TIER_COL = '分诊档'
_TIER_FAIL_RX = re.compile(r'✗\s*(\d+)\s*项未达标')
_TIER_PCT_RX = re.compile(r'人类区间命中率：\s*\d+\s*/\s*\d+\s*=\s*(\d+)%')


_TIER_ORDER = {'R1': 1, 'R2': 2, 'R3': 3}


def tier_ceiling(auto, manual):
    """合并"脚本自动算的档"与"人工显式指定的档" → **只升不降**。

    为什么只升不降：**升档是安全方向**（多花一次子代理／一次全书视图），
    **降档才危险**（该走的工序被省掉）。所以人工值只能把档位往上顶，
    不许把它压下来 —— 这也让 `--tier` **无法被用来绕过闸门**。
    """
    if manual is None:
        return auto
    m = str(manual).strip().upper()
    if m not in _TIER_ORDER:
        return auto
    if auto not in _TIER_ORDER:
        return m          # 自动算不出来（`?`）→ 人工值就是唯一信息
    return m if _TIER_ORDER[m] > _TIER_ORDER[auto] else auto


def calc_tier(code: int, out: str, retry):
    """按 `deai-entry.md` 0.4 的判据算分诊档 → `(档位, 理由)`。

    ⚠️ 判不了的档位写 `?`，**绝不默认 R1** —— 与 `--retry` 写 `?` 同一条纪律：
    "0/R1 是一个断言（我知道这章不重），? 才是事实（判不了）"。
    """
    out = out or ''
    mf = _TIER_FAIL_RX.search(out)
    mp = _TIER_PCT_RX.search(out)
    fails = int(mf.group(1)) if mf else (0 if code == 0 else 1)
    pct = int(mp.group(1)) if mp else None
    if fails >= 3:
        return 'R2', f'硬门失败 {fails} 项（≥3）'
    if pct is not None and pct < 25:
        return 'R2', f'命中率 {pct}%（<25%）'
    # ⚠️ retry 现在可能是 `?`（未知标记）或 int —— `'?' >= 2` 会 TypeError。
    if isinstance(retry, int) and retry >= 2:
        return 'R2', f'返工 {retry} 轮（≥2）'
    if fails >= 1:
        return 'R1', f'硬门失败 {fails} 项（1–2）'
    return 'R1', '硬门全过'
CH_RX = re.compile(r'第\s*(\d{1,4})\s*章')
# 返工轮次的**未知标记**（v7.3.0）：`--retry` 未指定时写它。
# ⚠️ 必须与 `check_batch_gate.UNKNOWN_RETRY` 逐字一致 —— 两边不一致 =
#    "双载体不一致"（本 SKILL 零容忍）：闸门会认不出这个标记，把它当非数字，
#    于是"未知"在报告里彻底消失（静默失败）。
# ⚠️ 它**不与任何合法轮次数冲突**（合法值是 0,1,2,… 的十进制整数）。
UNKNOWN_RETRY = '?'


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


RETRY_HEAD = '返工轮次'


def _split_wo_row(line: str):
    """工单表格行拆列。

    ⚠️ 刻意**不**用 check_batch_gate._split_md_row：那一份要处理竖线转义与多表，
    而工单表是**本脚本自己生成**的（列内容不含裸 `|`），引进来只会多一个跨脚本依赖
    （本项目对"双载体"零容忍，跨脚本复用同理——改一处要想另一处）。
    """
    return [c.strip() for c in line.strip().strip('|').split('|')]


def read_last_retry(wo_p, n: int):
    """从工单读该章**上一次**归档的返工轮次 → `str | None`（`None` = 没有该章的行）。

    ⚠️ 按**表头**定位列，不写死索引 —— 工单列将来若增减（v7.7.0 就加过「分诊档」），
       写死索引会静默读错列，那是本项目最忌的"看起来正常"的错。
    """
    p = Path(wo_p)
    if not p.is_file():
        return None
    idx = None
    for ln in read_text(p).split('\n'):
        s_ = ln.strip()
        if not s_.startswith('|'):
            continue
        cells = _split_wo_row(ln)
        if idx is None:
            for i, c in enumerate(cells):
                if RETRY_HEAD in c:
                    idx = i
                    break
            continue
        if (s_.startswith(f'| 第{n}章 ') or s_.startswith(f'| 第{n}章|')) and len(cells) > idx:
            return cells[idx].strip()
    return None


def infer_retry(last, has_defects: bool):
    """自动推断返工轮次 → `(值, 理由)`。

    **为什么能自动**（2026-10-21）：此前 `--retry` 全靠人手填，而会跳过流程的 Agent
    也不会老实填它 → 闸门 B（"返工过 → 必须交方案表"）长期空转。
    而"这一章未通过质检"是**脚本的客观结论**，不是判断 —— 退出码非 0 = 有硬门失败 =
    按 SKILL 硬规定一，这一章**必然要走动作 7 改写** = 必然返工过。
    **所以硬门失败的归档，retry 至少是 1，这个推断没有假阳性。**

    ⚠️ `last` 是 `?`（历史未知）时：本次失败 → 至少记 1；本次全绿 → **仍记 `?`**
       （不能把"未知"洗成 0 —— 那是用一个假数据混进良率统计）。
    """
    if has_defects:
        if last is None:
            return 1, '首次归档即未通过质检 → 记 1 轮'
        if last == UNKNOWN_RETRY:
            return 1, '历史轮次未知，但本次未通过 → 至少记 1 轮'
        if last.isdigit():
            return int(last) + 1, f'上次归档记 {last} 轮，本次仍未通过 → +1'
        return 1, f'上次轮次无法解析（{last!r}）→ 记 1 轮'
    # 本次全绿
    if last is None:
        return 0, '首次归档且全绿 → 记 0'
    if last == UNKNOWN_RETRY:
        return UNKNOWN_RETRY, '历史未知且本次全绿 → 保持"未知"，**不许洗成 0**'
    if last.isdigit():
        return int(last), f'本次全绿 → 沿用上次的 {last} 轮（返工发生在改之前那一轮）'
    return 0, f'上次轮次无法解析（{last!r}）→ 记 0'



def archive(proj: Path, n: int, retry, quiet: bool, tier_manual=None) -> int:
    f = chapter_file(proj, n)
    if f is None:
        print(f'[错误] 找不到第 {n} 章的正文文件（期望 chapters/第{n:02d}章-*.md）。')
        print('       这不等于"通过"——先确认章节落盘了再归档。')
        return 2

    defects, results, words = [], [], ''
    rhythm_code, rhythm_out = None, ''
    for label, script in MECH:
        code, out = _run(script, [f], proj)
        if code is None:
            continue
        results.append((label, code))
        if code != 0:
            defects.append(label)
        if label == '节律':
            rhythm_code, rhythm_out = code, out
        if label == '字数':
            m = re.search(r'字数\s*[:：]\s*([\d,]+)', out)
            if m:
                words = m.group(1).replace(',', '')

    # ⚠️ 工单写**独立文件**，绝不改动 `04-质检档案.md`。
    #    初版直接 upsert 进 04 的汇总表，结果把用户项目里原有那一行的
    #    **9 列质量数据覆盖成 6 列**（实测踩到，已还原）。
    #    两份记录是**不同性质**的，不该混写：
    #      · `04-质检档案` = **人写的判断**（AI味自评 / 人味配额 / 五项测试 / 杠精结论）
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
    # ★★ 2026-10-21：返工轮次**自动推断**（未显式指定 `--retry` 时）。
    #   不填 = 让脚本按"本次有没有硬门失败 + 上次归档记了几轮"算，理由见 infer_retry()。
    #   显式填 `--retry N` 仍然**直接覆盖**（人知道真实轮次时，人的判断优先）。
    _retry_why = '人工指定'
    if retry is None:
        _last = read_last_retry(wo, n)
        retry, _retry_why = infer_retry(_last, bool(defects))
    _retry_cell = UNKNOWN_RETRY if retry is None else str(retry)
    # ★ 分诊档：节律脚本没跑起来 → `?`（判不了），绝不默认 R1（见 calc_tier docstring）
    _tier, _tier_why = (('?', '节律脚本缺失，判不了档') if rhythm_code is None
                        else calc_tier(rhythm_code, rhythm_out, retry))
    # ★ 2026-10-21：杠精自检报"结构性硬伤"时，故事层的病改句子治不了 →
    #   执行者用 `--tier R2` 升档。本函数**只升不降**（见 docstring）。
    _auto = _tier
    _tier = tier_ceiling(_tier, tier_manual)
    if _tier != _auto:
        _tier_why = f'{_tier_why}｜人工升档（{tier_manual}）'
    row = (f'| 第{n}章 | {words or "?"} | '
           f'{"全绿" if not defects else f"{len(defects)} 项"} | '
           f'{"—" if not defects else "、".join(defects)} | {_retry_cell} | '
           f'{_tier} | |')
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
        # ⚠️ 2026-10-21 修：文件末尾带 `\n`，`split('\n')` 后最后一个是空串，
        #    直接 append 会在表头和数据行之间插一个空行 —— markdown 表格会被截断
        #    （闸门读得懂因为它逐行筛 `|`，但人读起来是断的，且"每章一行"不再连续）。
        while lines and lines[-1] == '':
            lines.pop()
        lines.append(row)
        lines.append('')
        action = '追加'
    wo.write_text('\n'.join(lines), encoding='utf-8')

    # ⚠️ 这条警告**不受 `--quiet` 抑制**：它是"数据永久丢失"的警告，不是例行输出。
    if _retry_why != '人工指定':
        print(f'  ★ 返工轮次：自动推断为 {_retry_cell}　—　{_retry_why}')
        print(f'    （依据：本次归档有没有硬门失败 + 该章上次归档记的轮次；'
              f'显式 `--retry N` 可覆盖）')

    if not quiet:
        print(f'  {action}工单：第{n}章')
        for label, code in results:
            print(f'    {"✓" if code == 0 else "✗"} {label}')
        print(f'    → {"全绿" if not defects else "缺陷：" + "、".join(defects)}'
              f'｜返工轮次 {_retry_cell}（{_retry_why}）')
        print(f'    ★ 分诊档（已写入工单「{TIER_COL}」列）：{_tier}　—　{_tier_why}')
        print(f'      → 下一动作（6.5 之后就是 7）：按 {_tier} 读 `guides/deai-entry.md` 第 0.4 节，'
              f'走第二节对应步。**改写是常态工序，没有"这章不用进入口"。**')
        if isinstance(retry, int) and retry >= 2:
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
    unknown_n = 0       # ★ v7.3.0：返工轮次为"未知"（`?`／空／非数字）的章数
    for r in rows:
        if r[3] not in ('—', '', '-'):
            for d in re.split(r'[、,，]', r[3]):
                if d.strip():
                    defects[d.strip()] += 1
        _raw = (r[4] if len(r) > 4 else '').strip()
        if _raw in ('', UNKNOWN_RETRY, '-'):
            # ⚠️ 未知 ≠ 0：既不进 `retries`（会拉低中位）、也不当成"首次合格"。
            unknown_n += 1
            continue
        try:
            retries.append(int(_raw))
        except Exception:
            unknown_n += 1
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
    if unknown_n:
        print(f'  ⚠ 返工轮次「未知」       {unknown_n}/{n} 章'
              f'（`{UNKNOWN_RETRY}` = 归档时未指定 `--retry`）')
        print('    → 这 %d 章**不按 0 算、也不按"返工过"算**：它们不进下面的返工轮次统计，'
              '也不能算"首次合格"。' % unknown_n)
        print('      补法：重跑 `--archive <章号> --retry N` 覆盖那一行（轮次只能当场记）。')
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
    print(f'     ⚠️ v7.3.0 起**不填 `--retry` 记的是"未知"（`{UNKNOWN_RETRY}`），不是 0**：')
    print('        "没填"与"真的是 0"是两件事，前者判不了、只能留痕（否则闸门会看不出来）。')
    return 0


def main():
    ap = argparse.ArgumentParser(description='章节工单（质检+归档 / 良率+帕累托）')
    ap.add_argument('project')
    ap.add_argument('--archive', type=int, metavar='章号',
                    help='★ 每章质检时调：跑全套机械脚本 + 归档一行工单')
    ap.add_argument('--tier', default=None, metavar='R1|R2|R3',
                    help='人工升档（只升不降）：杠精自检报「结构性硬伤」时用 --tier R2；'
                         '用户抱怨或批末用 --tier R3。脚本自动算的档位不会被它压低。')
    ap.add_argument('--retry', type=int, default=None,
                    metavar='N',
                    help='本章的返工轮次（retryCount）。**必须当场写**，否则良率不可统计；'
                         f'不填记为"{UNKNOWN_RETRY}"（未知），**不记为 0**')
    ap.add_argument('--report', action='store_true', help='良率 + 缺陷帕累托 + 成本回填')
    ap.add_argument('--since', default='', help='--report 时只统计该日期之后的轨迹')
    ap.add_argument('--quiet', action='store_true', help='只在有缺陷时输出')
    args = ap.parse_args()

    proj = Path(args.project)
    if not proj.is_dir():
        print(f'[错误] 目录不存在：{args.project}')
        sys.exit(2)
    if args.archive:
        sys.exit(archive(proj, args.archive, args.retry, args.quiet, args.tier))
    sys.exit(report(proj, args.since))


if __name__ == '__main__':
    main()
