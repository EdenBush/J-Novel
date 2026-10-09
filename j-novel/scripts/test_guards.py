# -*- coding: utf-8 -*-
"""守卫故障注入测试：验证 `audit_release.py` 的检查项**真的会响**。

用法:
    python scripts/test_guards.py

## 为什么要这个文件

> **"加了守卫"和"守卫有效"是两件事。**

2026-09-14 的教训：一个团队在 v4.5 明确写下"假闸门（看似做了检查、实际从未触发）
是最危险的一类失败"，v4.6 还是在新位置又犯了三次（声明比实现走得快）。
光靠人读代码发现不了——**唯一的办法是故意把代码改坏，看它是否报警**。

这个脚本把历次人工做的注入用例固化下来。**改完任何检查项后跑一次**，
把"我以为守卫有效"变成"我验证过守卫有效"。

## 它怎么工作

对每个用例：备份 → 注入故障 → 跑 `audit_release.py` → 检查是否报出预期的关键字 →
**无论成败都还原** → 最后确认还原后重新全绿（防止把技能改坏）。

## 实测记录（2026-09-14 首版 8/8；2026-09-19 扩到 17/17）

> ⚠️ **这里原本写的是"8 个注入用例"，而用例后来加到了 17 个**——文档没跟着走。
> 这类"数字漂移"正是 `audit_release.py` 有一条守卫在盯的东西，
> 它自己脚本文档里的数字反而漏了。**用例数以脚本运行输出的 `n/n` 为准，不要手写。**

本脚本的**前身**在人工跑时抓到过 4 个"守卫自身失效"：
  1. 守卫用 `in src` 判断 → 被注释掉的 `# from _shared import` 骗过
  2. 守卫扫原文 → continuity 的 **docstring** 里写着 `_shared_extract_body`，委托检测照样通过
  3. 调用计数把**注释**里 `` `grep -c '_all_docs('` `` 也算进去 → 定义未调用检测失效
  4. 数字漂移正则不认 markdown 强调 → 文档写 `**14 个**已废弃说法` 时**完全匹配不上**
"""
import io
import re
import shutil
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import ensure_utf8_stdio      # noqa: E402

ensure_utf8_stdio()

SKILL = Path(__file__).resolve().parent.parent
PY = sys.executable

# 会被注入的文档（执行层守卫的注入对象不只在 scripts/ 里）
DOC_TARGETS = ('SKILL.md',
               'references/guides/subagent-brief.md',
               'references/guides/quick-reference-card.md')

# ⚠️ 备份走**内存**，不落盘 —— 因此全过程无需创建/删除任何目录。
#    原实现在磁盘上建 `_guard_bak/` 再 rmtree，在受限沙箱里会因"回收站不可用"
#    被 safe-delete 拦截（实测踩到）。内存备份既更简单，也没有这个依赖。
_ORIG = {}


def run_audit():
    """跑一次 QA，返回 stdout。"""
    r = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'audit_release.py')],
                       cwd=str(SKILL), capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return r.stdout or ''


def backup_scripts():
    """把待注入文件的原文读进内存，并**落盘一份全量快照**（SIGPIPE 自愈用）。

    内存 `_ORIG` = 进程内 finally 还原；磁盘快照 = 进程被 SIGPIPE 杀掉后的启动自愈。
    """
    _ORIG.clear()
    for f in sorted((SKILL / 'scripts').glob('*.py')):
        if f.name == Path(__file__).name:      # 不备份自己（防递归）
            continue
        _ORIG[f] = f.read_text(encoding='utf-8')
    for rel in DOC_TARGETS:
        f = SKILL / rel
        if f.exists():
            _ORIG[f] = f.read_text(encoding='utf-8')
    _write_snapshot()


def _write_snapshot():
    """把快照范围（宁多勿漏）的原文**落盘一次**，供下次启动 self_heal 恢复。

    ⚠️ 只在**启动**时写一次、整跑确认全绿时删一次（见 89 行注释：一次跑 1 写 1 删，
    避免"每用例一写一删"触发宿主环境的批量删除保护）。
    """
    snap = {}
    for f in _snapshot_targets():
        try:
            snap[f.relative_to(SKILL).as_posix()] = f.read_text(encoding='utf-8')
        except Exception:
            continue
    try:
        _JOURNAL.write_text(json.dumps(snap, ensure_ascii=False), encoding='utf-8')
    except Exception as e:
        print(f'  ⚠ 快照落盘失败（{e!r}）—— 本跑若被 SIGPIPE 中断将无法自愈')


def restore_scripts():
    """从内存还原（幂等：可重复调用）。"""
    for f, txt in _ORIG.items():
        f.write_text(txt, encoding='utf-8')


# ── 落盘快照 + 启动自愈（2026-10-09 v7.2.0 新增）──────────────────────────
# ★ 为什么需要它（一次真实事故，是我自己造成的）：
#   本脚本的还原是**进程内的**（每个用例在 `finally` 里写回原文）。
#   一旦 stdout 被 `head` / `tail` / `grep -q` **提前关闭管道**，进程收到 **SIGPIPE 直接死掉**，
#   还原代码**根本不会执行** → 库里留下一个被注入的文件（本次是 `plot-scaffold.md` 的一个小标题
#   被替换成哨兵串 `QQQ`）→ **之后每次 audit 都报同一处错，而人还以为技能被改坏了**。
#
#   **修法不是"记得别接管道"（那是靠自觉），而是把还原从"靠进程活着"变成"机制保证"。**
#
# ★ 为什么是**整跑一次全量快照**，而不是"每个用例登记/销账"（第一版就是这么写的，已推翻）：
#   每用例一写一删 = 一次跑动几百次文件写删 → 触发宿主环境的**批量删除保护**
#   （实测：`SAFE_DELETE_BULK_CONFIRM_REQUIRED count=50/threshold=50`）→ 脚本被中途掐死，
#   于是"为了防中断而加的机制，自己成了新的中断源"。
#   现在的形态：**启动时写一次全量快照，全程不再动盘，整跑确认全绿时删一次** —— 一次跑 1 写 1 删。
#
#   快照覆盖 = 用例可能注入的**全部范围**（`SKILL.md` / `references/**/*.md` / `scripts/*.py`）。
#   文件名 `.test_guards_journal.json` 不是 `.md`/`.py`，因此不会被任何守卫的 glob 扫到。
_JOURNAL = Path(__file__).resolve().parent / '.test_guards_journal.json'


def _snapshot_targets():
    """快照范围 = 用例可能注入的全部文件（**宁多勿漏**：多快照一个只是多读一次盘）。"""
    out = [f for f in sorted(SKILL.glob('scripts/*.py')) if f.name != Path(__file__).name]
    out += sorted(SKILL.glob('references/**/*.md'))
    top = SKILL / 'SKILL.md'
    if top.is_file():
        out.append(top)
    return [f for f in out if f.is_file()]



def _journal_load() -> dict:
    if not _JOURNAL.is_file():
        return {}
    try:
        d = json.loads(_JOURNAL.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _journal_clear():
    """整跑确认全绿后清空快照 —— 树干净了，快照就没意义了（也免得它常驻误导下一跑）。"""
    try:
        _JOURNAL.unlink(missing_ok=True)
    except Exception:
        pass


def self_heal() -> tuple:
    """★ 启动自愈：把库恢复到上一跑开始前的样子。返回 (恢复数, 跳过数)。

    ⚠️ 必须在**任何注入之前**、**基线检查之前**调用 —— 否则上一次的残留会被当成真缺陷，
    于是"基线本来就不绿"，整个测试直接拒跑（这次事故的表象正是这个）。

    ⚠️ **代价要说清**：它会**覆盖**这些文件。若你在一跑被中断之后、又手工改过其中某个文件，
    那个改动**会被回滚**。所以：① 中断后**先重跑一次本脚本**（自愈），**再**动手改东西；
    ② 自愈会逐个打印被还原的文件名 —— **看着那份名单再决定要不要重做你的改动**。
    （无 git 可用，所以只能靠"打印 + 顺序约定"把风险降到可见，不能假装它不存在。）
    """
    d = _journal_load()
    if not d:
        return (0, 0)
    n, skipped = 0, 0
    for rel, orig in d.items():
        if not isinstance(orig, str):
            # 旧格式（rel -> {orig, inj}，v7.2.0 开发中途的残留）——无法可靠判定，跳过交人。
            skipped += 1
            print(f'  ⚠ 自愈跳过（旧格式快照条目，无法判定）：{rel} —— 建议手工确认该文件')
            continue
        f = SKILL / rel
        try:
            if not f.is_file():
                skipped += 1
                print(f'  ⚠ 自愈跳过（快照里有、库里无此文件）：{rel}')
                continue
            cur = f.read_text(encoding='utf-8')
            if cur == orig:
                continue                    # 没被动过 → 不算恢复，也不吭声（绝大多数是这种）
            f.write_text(orig, encoding='utf-8')
            n += 1
            print(f'  ↻ 自愈还原：{rel}')
        except Exception as e:
            skipped += 1
            print(f'  ⚠ 自愈失败：{rel} —— {e!r}（请手工检查该文件）')
    _journal_clear()
    return (n, skipped)


def inject_case(name, fname, mutate, expect_kw):
    """注入 → 跑 → 判定 → 还原。返回是否被抓到。

    fname 可以是 `scripts/xxx.py`，也可以是 `SKILL.md` / `references/guides/xxx.md`
    （执行层守卫要注入的是文档，不是脚本）。
    """
    p = (SKILL / fname) if ('/' in fname or fname.endswith('.md')) else (SKILL / 'scripts' / fname)
    orig = p.read_text(encoding='utf-8')
    new = mutate(orig)
    if new == orig:
        print(f'  ✗ {name}：**注入未生效**（锚点没匹配上）→ 这个用例是无效的')
        return False
    p.write_text(new, encoding='utf-8')
    try:
        out = run_audit()
    finally:
        p.write_text(orig, encoding='utf-8')
    hit = expect_kw in out
    print(f'  {"✓" if hit else "✗"} {name}  →  {"抓到" if hit else "**没抓到（守卫无效！）**"}')
    if not hit:
        for line in out.split('\n'):
            if '•' in line or '跨脚本' in line or '阈值' in line:
                print('       ', line.strip()[:110])
    return hit


def doc_inject_case(name, expect_kw):
    """文档侧的注入（数字漂移）。"""
    p = SKILL / 'references' / 'skill-mechanics.md'
    orig = p.read_text(encoding='utf-8')
    new = orig.replace('**14 个**已废弃说法', '**9 个**已废弃说法')
    if new == orig:
        print(f'  ✗ {name}：注入未生效（文档里找不到「**14 个**已废弃说法」）')
        return False
    p.write_text(new, encoding='utf-8')
    try:
        out = run_audit()
    finally:
        p.write_text(orig, encoding='utf-8')
    hit = expect_kw in out
    print(f'  {"✓" if hit else "✗"} {name}  →  {"抓到" if hit else "**没抓到！**"}')
    return hit


def _region_replace(text, start_rx, end_rx, old, new, count=0):
    """只把 `text` 里**指定区段内**的 `old` 换成 `new`，区段外一个字不动。

    用途：**阶段级守卫**（`STAGE_CORE`）的注入必须只动那一个区段 —— 否则注入的
    就不是"这一阶段少了读点"这个故障，用例也就不成立。

    ⚠️ `count=0` 表示区段内**全部**替换。这条默认值是踩出来的：
    本项目已 **4 次**因"只替换第一处"而**注入落空**（CHANGELOG v4.5 / v4.7 / v6.0 / v6.5）——
    守卫看起来"没抓到"，其实是**注入没到位**。
    **注入前先数一遍目标词在区段里出现了几次。**
    """
    m = re.search(start_rx, text)
    if not m:
        return text
    rest = text[m.end():]
    me = re.search(end_rx, rest)
    if not me:
        return text
    seg = rest[:me.start()]
    new_seg = seg.replace(old, new) if count == 0 else seg.replace(old, new, count)
    if new_seg == seg:
        return text
    return text[:m.end()] + new_seg + rest[me.start():]


def _plot_card_text(nums, status='已与作者确认（2026-10-09）', style='pad2',
                    hook=True, must=True):
    """剧情卡 fixture —— 字段名与 `guides/plot-co-creation.md` 3.3 **逐字一致**。

    `style` 用来验证闸门的**多写法兼容**（同一含义的多种写法都要认）：
        'plain' → `## 第 1 章` ／ 'pad2' → `## 第 02 章` ／ 'pad3' → `## 第001章`
    """
    _fmt = {'plain': lambda n: str(n),
            'pad2': lambda n: '%02d' % n,
            'pad3': lambda n: '%03d' % n}[style]
    head = (f'# 剧情卡 · 第 {_fmt(nums[0])}–{_fmt(nums[-1])} 章\n\n'
            f'> 共创状态：{status}｜档位：推荐档\n'
            f'> 作者改动：无｜未回应项：无\n\n---\n\n')
    body = ''
    for n in nums:
        body += f'## 第 {_fmt(n)} 章：测试章（约 3000 字 · 冲突章）\n\n'
        if hook:
            body += ('- **本章钩子**（已选定）：他当着满屋子人承认炉子是借的\n'
                     '  - 候选 A：<…> ｜ 代价：<…>\n')
        if must:
            body += '- **要说清的事**：① 借来的那台炉子来路不明\n'
        body += ('- **回不去的事件**（没有就写「无」）：无\n'
                 '- **章末读者该冒出的问题**：炉子是谁的？\n'
                 '- **作者原话 / 改动**（有就原样记，没有写「无」）：无\n\n')
    return head + body


def write_plot_card(base, nums, name=None, **kw):
    """把剧情卡落到 `细纲/剧情卡-第NN-NN章.md`（接口契约路径）并返回该路径。"""
    d = base / '细纲'
    d.mkdir(parents=True, exist_ok=True)
    name = name or ('剧情卡-第%02d-%02d章.md' % (nums[0], nums[-1]))
    p = d / name
    p.write_text(_plot_card_text(nums, **kw), encoding='utf-8')
    return p


def main():
    print('=' * 74)
    print('守卫故障注入测试（每个用例都会在跑完后自动还原）')
    print('=' * 74)
    # ★ 0. 启动自愈：回放上一跑留下的快照（若上一跑被 SIGPIPE 杀掉，还原没执行）
    #    必须排在 **backup_scripts() 与基线检查之前** —— 否则残留会被当成真缺陷。
    _healed, _skipped = self_heal()
    if _healed or _skipped:
        print(f'  ⚠ 上一跑被中断留下快照：自愈还原 {_healed} 个文件'
              f'（跳过 {_skipped} 个：文件缺失或恢复失败，请手工检查）')
        print('     成因通常是 stdout 被 head/tail 提前关闭管道 → SIGPIPE 杀掉进程，还原没执行\n')
    backup_scripts()

    base = run_audit()
    if '全部通过' not in base:
        print('  ✗ 基线本来就不绿，先修好再来测守卫：')
        print(base[-1500:])
        restore_scripts()
        return 1
    print('  基线（未注入）：✓ 全绿\n')

    results = []

    print('① 守卫⓪：注释掉共享 import（曾经用 `in src` 判断 → 被注释骗过）')
    print('   ⚠️ 这个用例 2026-09-21 抓到过一个**新形态**：check_aistyle.py 为了找章节')
    print('      文件加了个 try/except 兜底 `from _shared import find_chapter_files`，')
    print('      于是旧判据"存在任意 from _shared import"被那行**mask 掉**——')
    print('      真正的委托 import 注释掉后守卫照样通过。**合法代码让守卫失效，')
    print('      而守卫看起来还在。** 现在改为按**委托符号**（extract_body/read_text）判。')
    results.append(inject_case('注释 import', 'check_aistyle.py',
                               lambda s: s.replace('from _shared import (', '# from _shared import (', 1),
                               '没有走 _shared'))

    print('② 守卫⓪：删掉 continuity 的共享 import')
    print('   （原「守卫①」已并入⓪——① 与⓪查的是同一件事，且⓪现在按**委托符号**判，'
          '不再被兜底 import 屏蔽）')
    results.append(inject_case('删 import', 'check_continuity.py',
                               lambda s: s.replace(
                                   'from _shared import extract_body as _shared_extract_body',
                                   '# from _shared import extract_body as _shared_extract_body', 1),
                               '_shared'))

    print('③ 守卫③：本地 def 覆盖共享实现（docstring 里仍写着 _shared 名字）')
    results.append(inject_case(
        '本地 def 覆盖', 'check_continuity.py',
        lambda s: s.replace(
            'def extract_body(text):\n    """取正文。',
            'def _local_body(text):\n    return text\n\n\ndef extract_body(text):\n    """取正文。',
            1).replace('    return _shared_extract_body(text)',
                       '    return _local_body(text)', 1),
        '覆盖 _shared 实现'))

    print('④ 守卫②：wordcount 读文件改回硬编码 utf-8（GBK 会被静默少算）')
    results.append(inject_case(
        '硬编码 utf-8 读盘', 'check_chapter_wordcount.py',
        lambda s: s.replace('_shared_extract_body(_shared_read_text(file_path))',
                            '_shared_extract_body(Path(file_path).read_text(encoding="utf-8", errors="replace"))', 1),
        '硬编码'))

    print('⑤ 守卫②：只 import 却不调用 _shared_read_text')
    results.append(inject_case('import 却不调用', 'check_aistyle.py',
                               lambda s: s.replace('    return _shared_read_text(path)',
                                                   '    return path.read_bytes().decode("utf-8", "ignore")', 1),
                               '从未调用'))

    print('⑥ 守卫④：让 _all_docs 变回"定义了没调用"')
    def break_alldocs(s):
        # ⚠️ 必须替换掉**全部**调用点（用等价表达式内联），否则不构成"零调用"。
        #    本用例初版只替换第一处，另一处（数字漂移守卫里的）仍在 → 计数=2 →
        #    守卫不报警，看起来像"守卫无效"，其实是**注入没到位**。
        #    **故障注入脚本自己也要被验证。**
        s = s.replace("_all_docs(root, 'refs')",
                      "{f: read_text(f) for f in (root / 'references').rglob('*.md')}")
        s = s.replace("_all_docs(root, 'all')",
                      "{f: read_text(f) for f in root.rglob('*.md')}")
        return s
    results.append(inject_case('定义未调用', 'audit_release.py', break_alldocs, '从未被调用'))

    print('⑦ 守卫②b：让 aistyle 重新自己定义 SIMILE_WORDS')
    results.append(inject_case(
        '重复定义词表', 'check_aistyle.py',
        lambda s: s.replace('from _shared import (',
                            "SIMILE_WORDS = ['像', '如同']\nfrom _shared import (", 1),
        '自己定义了 SIMILE_WORDS'))

    print('⑧ 数字型漂移守卫：把文档里的计数改错（14 → 9）')
    results.append(doc_inject_case('文档计数漂移', '文档数字已漂'))

    print('⑨ 执行层守卫：改坏任务包里的配额卡副本（与规范卡分叉）')
    def de_fork(s):
        return s.replace('≥5 处 ≥60 字的复合句', '≥3 处 ≥60 字的复合句', 1)
    results.append(inject_case('配额卡分叉', 'references/guides/subagent-brief.md',
                               de_fork, '分叉'))

    print('⑩ 执行层守卫：把 SKILL.md 的"子代理必读"改回全套路径口径')
    def revert_reads(s):
        return s.replace('**但任务包不会给你 10 本指南**',
                         '你的任务包里已经附了每一步需要的文件绝对路径和脚本路径', 1)
    results.append(inject_case('子代理必读口径漂回', 'SKILL.md', revert_reads, '子代理必读口径'))

    print('⑫ 规则传导守卫：改掉速查卡里的一条关键规则（但源指南仍有）')
    print('   （子代理只读速查卡 → 源指南改了不同步，等于这条规则对子代理不存在）')
    def break_propagation(s):
        return s.replace('① **禁止同构**', '① **XXXXXXXX**', 1)
    results.append(inject_case('规则传导断裂', 'references/guides/quick-reference-card.md',
                               break_propagation, '规则传导断裂'))

    print('⑬ 核心指南读点守卫：把一本核心指南从「每章最小必做清单」里整个拿掉')
    print('   （清单是 Agent 逐章照做的唯一清单，不在清单里 = 读点靠运气）')
    def drop_core_guide(s):
        # ⚠️ 必须在**小节内**替换**全部**出现位置——只删一处不算故障
        #    （初版只删一处，清单里还有另一处 → 守卫不响，看起来像守卫无效）
        # ⚠️ expect_kw 用**短关键字**（'每章最小必做清单'），不要用完整报错句：
        #    2026-09-19 守卫扩成"两份清单都查"后，文案变成
        #    「不在SKILL.md「每章最小必做清单」里」，中间插了清单名 →
        #    原来那句 `不在「每章最小必做清单」里` **匹配不上**了。
        #    守卫其实在正常工作，是**断言写太死**造成的假阴性。
        m = re.search(r'每章最小必做清单(.*?)(?:\n你不是首席内容官|\Z)', s, re.S)
        if not m:
            return s
        return s.replace(m.group(1), m.group(1).replace('human-rhythm.md', 'QQQ.md'), 1)
    results.append(inject_case('核心指南无读点', 'SKILL.md', drop_core_guide, '每章最小必做清单'))

    print('⑪ 闸门 fail-closed：写作计划里的章号字段认不出时，必须报错而**不是**报通过')
    print('   （真实事故：项目用 `index`，旧脚本只认 `chapterNumber` → 扫到 0 章却报"✓ 通过"，')
    print('     还建议重写第 1 章。这是最危险的一种假绿。）')
    import tempfile, json as _json
    _td = Path(tempfile.mkdtemp(prefix='jnovel_gate_'))
    (_td / '02-写作计划.json').write_text(
        _json.dumps({'chapters': [{'编号未知': 1, 'status': 'completed'}]}, ensure_ascii=False),
        encoding='utf-8')
    _r = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_batch_gate.py'), str(_td)],
                        capture_output=True, text=True, encoding='utf-8', errors='replace')
    _hit = (_r.returncode == 2) and ('一章都没能解析出章号' in (_r.stdout or ''))
    print(f'  {"✓" if _hit else "✗"} 0 章 fail-closed  →  '
          f'{"抓到" if _hit else "**没抓到！（退出码 %d）**" % _r.returncode}')
    results.append(_hit)
    try:
        import shutil as _sh
        _sh.rmtree(_td)
    except Exception:
        pass   # 受限沙箱可能拦删除；目录在系统临时区，留着无害

    print('⑭ 融合规则传导：把速查卡里的「私人细节」删掉（humanize-toolkit 有、速查卡没有）')
    results.append(inject_case('融合规则漏传导', 'references/guides/quick-reference-card.md',
                               lambda s: s.replace('私人细节', 'QQQ细节', 1), '规则传导断裂'))

    print('⑮ 融合规则传导：把速查卡里的万能情绪模板词删掉')
    results.append(inject_case('情绪模板漏传导', 'references/guides/quick-reference-card.md',
                               lambda s: s.replace('空气凝固', 'QQQ', 1), '规则传导断裂'))

    print('⑯ **半角引号硬闸门：造一个含 ASCII 双引号的章节，看 check_aistyle 是否 exit 1**')
    _tq = Path(tempfile.gettempdir()) / '_guard_quote_test.md'
    _tq.write_text('# 第1章 测试\n\n他说"走"。\n' + '（正文）' * 400, encoding='utf-8')
    _rq = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_aistyle.py'), str(_tq)],
                         cwd=str(SKILL), capture_output=True, text=True, encoding='utf-8', errors='replace')
    _okq = (_rq.returncode == 1) and ('半角双引号' in (_rq.stdout or ''))
    print(f'  {"✓" if _okq else "✗"} 半角引号闸门  →  '
          f'{"抓到（exit 1）" if _okq else "**没抓到！（退出码 %d）**" % _rq.returncode}')
    results.append(_okq)
    try:
        _tq.unlink()
    except Exception:
        pass

    print('⑰ **批次闸门「下一章」：只完成第 1 章、本批计划 1–10 时，必须提示第 2 章而不是第 11 章**')
    _td2 = Path(tempfile.gettempdir()) / '_guard_gate_test'
    try:
        import shutil as _sh2
        _sh2.rmtree(_td2)
    except Exception:
        pass
    _td2.mkdir(parents=True, exist_ok=True)
    (_td2 / '02-写作计划.json').write_text(json.dumps({'costMode': 'standard', 'chapters': [
        {'chapterNumber': n, 'status': ('completed' if n == 1 else 'pending'),
         'wordCountPass': True} for n in range(1, 11)]}, ensure_ascii=False), encoding='utf-8')
    (_td2 / '04-质检档案.md').write_text('### 第1章\n', encoding='utf-8')
    (_td2 / '05-创作台账.md').write_text('最近重读章号：第1章\n本章返工轮次（retryCount）：0\n', encoding='utf-8')
    (_td2 / '03-状态台账.md').write_text('第1章\n', encoding='utf-8')
    (_td2 / '01-大纲.md').write_text(
        '| 章节 | 标题 | 开场类型 | 章末型 |\n|---|---|---|---|\n| 第1章 | A | 新起 | 甲·信息结算 |\n',
        encoding='utf-8')
    # ★ 2026-10-07 v7.0.0：本章已 completed，就必须有 `_meta/` 实物清单
    #   （否则 `concrete_list_missing` 会阻塞 → 这个用例会因为别的原因失败）。
    (_td2 / 'chapters' / '_meta').mkdir(parents=True, exist_ok=True)
    (_td2 / 'chapters' / '_meta' / '第01章-测试.meta.md').write_text(
        '# 第01章 元数据\n\n## 本章概要\n- 核心事件：fixture\n\n'
        '## 实物清单（本章 3–8 项：只属于这一章的东西）\n'
        '- 半包受潮的火柴\n- 302 路末班车\n- 补了两次的蓝布书包\n',
        encoding='utf-8')
    # ★ 2026-10-09 v7.1.0：本批已 completed 的章还必须有 `细纲/剧情卡-*.md`
    #   （否则 `plot_card_missing` 会阻塞 → 本用例会因为别的原因失败）。
    write_plot_card(_td2, [1])
    _rg = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_batch_gate.py'), str(_td2)],
                         cwd=str(SKILL), capture_output=True, text=True, encoding='utf-8', errors='replace')
    _og = _rg.stdout or ''
    _okg = ('第 2 章' in _og) and ('第 11 章' not in _og)
    print(f'  {"✓" if _okg else "✗"} 下一章不跳号  →  '
          f'{"抓到（提示第 2 章）" if _okg else "**没抓到！仍提示跳章**"}')
    results.append(_okg)
    try:
        import shutil as _sh3
        _sh3.rmtree(_td2)
    except Exception:
        pass

    # ══════════════════════════════════════════════════════════════════
    # 2026-09-19 新增：三个 P0 结构性事故的回归守卫
    # 这三条都不是 audit_release 的静态守卫，而是**脚本自身的行为**——
    # 只能"造一个真实项目 fixture，跑脚本，看它的退出码和输出"。
    # ══════════════════════════════════════════════════════════════════
    import shutil as _shx

    def _mk_project(base: Path, *, min_words=2000, n_chars=3000,
                    meta_dir=False, ledger=''):
        """造一个合规结构的临时项目。返回项目目录。

        ⚠️ `n_chars` 要**留够余量**：每段 filler 是 30 个中文字，
        初版按 2200 生成只有 ~1980 字 → 低于 fixture 自己的下限 2000 →
        用例报"exit 1"，看起来像"闸门坏了"，其实是**测试数据不够**。
        **fixture 自身也要满足它要验证的闸门**，否则测的是错的东西。
        """
        try:
            _shx.rmtree(base)
        except Exception:
            pass
        (base / 'chapters').mkdir(parents=True, exist_ok=True)
        filler = '风从巷口灌进来，晾在绳上的衣服翻了个面，水滴落在青石板上，声音很轻。' * (n_chars // 34 + 2)
        # 第 1 章：结尾带钩子人物 陆铮
        (base / 'chapters' / '第01章-测试.md').write_text(
            '# 第01章 测试\n\n' + filler + '\n\n陆铮站起身，把门推开。陆铮没有回头。\n',
            encoding='utf-8')
        # 第 2 章：开头 700 字内**不提** 陆铮（触发边界硬命中）
        (base / 'chapters' / '第02章-测试.md').write_text(
            '# 第02章 测试\n\n' + filler[:700] + '\n\n' + filler + '\n', encoding='utf-8')
        # ★ 2026-10-07 v7.0.0：`_meta/` 现在是**必填**（实物清单的落盘位置）——
        #   `check_batch_gate.py` 的 `concrete_list_missing` 会查本批次已写完的章。
        #   ⚠️ 这正是 v6.8.0 加 `retryCount` 时踩过的同一个坑：**加一个新的必填字段，
        #      会让所有旧 fixture 失效**（当时它直接把用例 ⑰ 打挂）。所以在这里统一补齐，
        #      而不是等用例炸了再逐个补。
        (base / 'chapters' / '_meta').mkdir(exist_ok=True)
        for _n in (1, 2):
            _meta = (f'# 第0{_n}章 元数据\n\n## 本章概要\n- 核心事件：fixture\n\n'
                     f'## 实物清单（本章 3–8 项：只属于这一章的东西）\n'
                     f'- 半包受潮的火柴\n- 302 路末班车\n- 补了两次的蓝布书包\n')
            if meta_dir and _n == 1:
                # 这一段是给用例 ㉒ 用的：`_meta/` 里的东西**不该被任何脚本当成正文**
                _meta += '\n## 章节备注\n' + filler + '\n'
            (base / 'chapters' / '_meta' / f'第0{_n}章-测试.meta.md').write_text(
                _meta, encoding='utf-8')
        # ★ 2026-10-09 v7.1.0：`细纲/剧情卡-*.md` 也是**必填**（剧情共创的产出物）——
        #   `check_batch_gate.py` 的 `plot_card_missing` 会查本批次已写完的章。
        #   ⚠️ 这是"加一个新必填字段 → 所有旧 fixture 失效"的**第三次**
        #      （v6.8.0 的 retryCount、v7.0.0 的实物清单）。**统一在这里补齐，
        #      不是等用例炸了逐个补，更不是放宽判据。**
        write_plot_card(base, [1, 2])
        (base / '00-人物档案.md').write_text(
            '# 人物档案\n\n## 陆铮（主角）· 28 岁 · 男\n性格：沉默。\n', encoding='utf-8')
        (base / '02-写作计划.json').write_text(json.dumps({
            'costMode': 'standard', 'minWordsPerChapter': min_words,
            'chapters': [{'chapterNumber': n, 'status': 'completed', 'wordCountPass': True}
                         for n in (1, 2)]}, ensure_ascii=False), encoding='utf-8')
        (base / '05-创作台账.md').write_text(
            '# 创作台账\n\n最近重读章号：第2章\n本章返工轮次（retryCount）：0\n' + ledger, encoding='utf-8')
        return base

    _tmp = Path(tempfile.gettempdir())

    print('⑱ **--all 必须看得见 chapters/ 下的章节**')
    print('   （真实事故：脚本只在项目根 `glob(第*.md)` → 对合规项目一个文件都找不到，')
    print('     还打印"没有找到章节文件"并 exit 0 —— Phase 4 唯一那句字数检查形同虚设）')
    _p18 = _mk_project(_tmp / '_guard_all_chapters')
    _r18 = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_chapter_wordcount.py'),
                           '--all', str(_p18)],
                          cwd=str(SKILL), capture_output=True, text=True, encoding='utf-8', errors='replace')
    _o18 = _r18.stdout or ''
    _ok18 = (_r18.returncode == 0) and ('第01章' in _o18) and ('第02章' in _o18) and ('没有找到' not in _o18)
    print(f'  {"✓" if _ok18 else "✗"} chapters/ 可见  →  '
          f'{"抓到 2 章" if _ok18 else "**没抓到！（exit %d）**" % _r18.returncode}')
    results.append(_ok18)

    print('⑲ **--all 扫不到章节时必须 fail-closed（exit 2）**')
    print('   （"没扫到"≠"通过"。同类事故已是第三次复发，最危险的一种假绿。）')
    _p19 = _tmp / '_guard_empty_project'
    try:
        _shx.rmtree(_p19)
    except Exception:
        pass
    _p19.mkdir(parents=True, exist_ok=True)
    _r19 = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_chapter_wordcount.py'),
                           '--all', str(_p19)],
                          cwd=str(SKILL), capture_output=True, text=True, encoding='utf-8', errors='replace')
    _ok19 = (_r19.returncode == 2)
    print(f'  {"✓" if _ok19 else "✗"} 0 章 fail-closed  →  '
          f'{"抓到（exit 2）" if _ok19 else "**没抓到！（exit %d，应为 2）**" % _r19.returncode}')
    results.append(_ok19)

    print('⑳ **字数下限必须读 02-写作计划.json 的 minWordsPerChapter**')
    print('   （此前硬编码 3000：项目配 2000 时，2200 字的**合规章节被判 FAIL**，Agent 白补 800 字）')
    _r20 = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_chapter_wordcount.py'),
                           '--all', str(_p18)],
                          cwd=str(SKILL), capture_output=True, text=True, encoding='utf-8', errors='replace')
    _o20 = _r20.stdout or ''
    _ok20 = ('下限 2000' in _o20) and (_r20.returncode == 0)
    print(f'  {"✓" if _ok20 else "✗"} 下限取项目配置  →  '
          f'{"读到 2000" if _ok20 else "**没读到！（exit %d）**" % _r20.returncode}')
    results.append(_ok20)

    print('㉑ **模板假放行必须被拦下**（台账里抄了文档里的 `第N→N+1章` 占位符）')
    print('   （真实事故：check_continuity 严格正则正确地不豁免，check_batch_gate 宽松正则却报')
    print('     "已放行" → 宽松层覆盖严格层，一条从提示文字抄来的假放行豁免了全书边界检查）')
    _p21 = _mk_project(_tmp / '_guard_fake_waiver', meta_dir=True,
                       ledger='boundary-waived: 第N→N+1章，理由：该钩子第M章回收，已在03-状态台账登记\n')
    _r21 = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_batch_gate.py'), str(_p21)],
                          cwd=str(SKILL), capture_output=True, text=True, encoding='utf-8', errors='replace')
    _o21 = (_r21.stdout or '') + (_r21.stderr or '')
    _ok21 = ('放行记录无效' in _o21) and ('[已放行]' not in _o21) and (_r21.returncode == 1)
    print(f'  {"✓" if _ok21 else "✗"} 模板假放行被拦  →  '
          f'{"抓到（exit 1）" if _ok21 else "**没抓到！（exit %d）**" % _r21.returncode}')
    results.append(_ok21)

    print('㉒ **_meta/ 里的元数据不得被当成章节正文**')
    print('   （否则字数虚高、边界检测多出"自己接自己"的假边界）')
    _r22 = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_chapter_wordcount.py'),
                           '--all', str(_p21)],
                          cwd=str(SKILL), capture_output=True, text=True, encoding='utf-8', errors='replace')
    _o22 = _r22.stdout or ''
    _ok22 = ('总计: 2 章' in _o22) and ('meta' not in _o22)
    print(f'  {"✓" if _ok22 else "✗"} _meta/ 被跳过  →  '
          f'{"只数到 2 章正文" if _ok22 else "**没跳过！元数据混进正文**"}')
    results.append(_ok22)

    print('㉓ **两套「每章清单」读点不同步必须报警**（守卫此前只查 SKILL.md）')
    print('   （铁律一强制执行的是 phase3 执行清单；只写在 SKILL.md 里的读点 = Agent 不会读）')
    def _desync_clists(s):
        m = re.search(r'本阶段执行清单(.*?)\n```', s, re.S)
        if not m:
            return s
        # ★ 2026-10-09 修（**本项目第 5 次「注入落空」**）：**必须在整个区段内替换全部出现**。
        #   前四次：⑥（`_all_docs` 两处调用）、⑬（清单里两处）、㉜（两条例外/两个例外）、
        #   v6.5.0（清单措辞被改 → 锚点整个匹配不上）。见 CHANGELOG v4.5/v4.7/v6.0/v6.5。
        #   本次成因：另一位同事给清单第 5 项（改写阶段）补读点时，**又在这个区段里加了
        #   一处 `humanize-toolkit.md`**（「动笔前」那处还在）→ "只删一处"不再构成
        #   "这一步没有读点" → 注入成了空操作 → 断言失败。
        #   **规律（第 5 次被验证）：注入前先数一遍目标词在目标区段里出现了几次。**
        return s.replace(m.group(1), m.group(1).replace('humanize-toolkit.md', 'QQQ.md'), 1)
    results.append(inject_case('两套清单读点不同步', 'references/flows/phase3-writing.md',
                               _desync_clists, '不同步'))

    print('㉔ **世界设定传导链：从任务包里拿掉「世界设定包」必须报警**')
    print('   （2026-09-20 真实事故：圣经在整个 SKILL 里只有一处提及、创作期零读点 →')
    print('     落笔时不是一个庞大的世界，子代理之间不是同一个世界）')
    results.append(inject_case('任务包无世界设定包', 'references/guides/subagent-brief.md',
                               lambda s: s.replace('世界设定包', 'QQQ包'), '世界设定传导链断裂'))

    print('㉕ **世界设定传导链：从速查卡拿掉「世界一致性」必须报警**')
    print('   （子代理只读速查卡 → 卡里没有 = 这条约束对它不存在）')
    # ⚠️ expect_kw 用「世界设定传导链断裂」而不是「规则传导断裂」：
    #    这条故障会被**两个守卫**同时抓到（⑧ 传导链守卫 + PROPAGATION_RULES 规则表），
    #    先报出的是前者。**断言写得比守卫窄，就会把"守卫正常工作"误判成"守卫无效"**
    #    ——这个坑在 ⑬ 上已经踩过一次（文案里插了清单名，旧关键字匹配不上）。
    results.append(inject_case('速查卡无世界一致性', 'references/guides/quick-reference-card.md',
                               lambda s: s.replace('世界一致性', 'QQQ'), '世界设定传导链断裂'))

    print('㉖ **剧情脚手架传导链：从 Phase 1 拿掉脚手架环节必须报警**')
    print('   （脚手架填的是流程真空区：作者的剧情设想既不是"内核"也不是"细纲"，没有采集入口）')
    results.append(inject_case('Phase1 无剧情脚手架', 'references/flows/phase1-interview.md',
                               lambda s: s.replace('剧情脚手架', 'QQQ环节'), '剧情脚手架传导链断裂'))

    print('㉗ **剧情脚手架传导链：从任务包拿掉「作者画面」必须报警**')
    print('   （作者画面是流水线上最保人味的素材——那是作者自己的眼睛，传不到就白挖了）')
    results.append(inject_case('任务包无作者画面', 'references/guides/subagent-brief.md',
                               lambda s: s.replace('作者画面', 'QQQ素材'), '剧情脚手架传导链断裂'))

    print('㉘ **开场不许同质：从速查卡拿掉这条禁令必须报警**')
    print('   （2026-09-21 真实事故：两条规则叠加被过度执行 →')
    print('     三到五章开头全变成"时间＋环境空镜"。子代理只读速查卡）')
    results.append(inject_case('速查卡缺开场同质禁令', 'references/guides/quick-reference-card.md',
                               lambda s: s.replace('开场不许同质', 'QQQ').replace('别和它同型', 'QQQ'),
                               '规则传导断裂'))

    print('㉙ **跨章开场同质必须被 check_repetition.py --all 抓到**')
    print('   （批量写作特有的结构级同构——单章看不出来，连写才暴露）')
    _tk = Path(tempfile.gettempdir()) / '_guard_openings'
    try:
        _shx.rmtree(_tk)
    except Exception:
        pass
    (_tk / 'chapters').mkdir(parents=True, exist_ok=True)
    for _i, _txt in enumerate([
        '早上五点，天色还暗，村子静悄悄的。他睁开眼。',
        '第二天清晨，雾气还没散，路上没有人。他走出去。',
        '傍晚的时候，风从巷口灌进来。他把门关上。',
    ], start=1):
        (_tk / 'chapters' / f'第0{_i}章-测试.md').write_text(
            f'# 第0{_i}章 测试\n\n' + _txt + '\n', encoding='utf-8')
    _rok = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_repetition.py'),
                           '--all', str(_tk)],
                          cwd=str(SKILL), capture_output=True, text=True, encoding='utf-8', errors='replace')
    _ook = (_rok.returncode == 1) and ('开场同质' in (_rok.stdout or ''))
    print(f'  {"✓" if _ook else "✗"} 连续 3 章空镜起手  →  '
          f'{"抓到（exit 1）" if _ook else "**没抓到！（exit %d）**" % _rok.returncode}')
    results.append(_ook)
    try:
        _shx.rmtree(_tk)
    except Exception:
        pass

    print('㉚ **章末不许公式化：从速查卡拿掉这条禁令必须报警**')
    print('   （2026-09-21 真实事故：五章结尾全是"叙述+余味"，两章连着用「反正…」。')
    print('     闸门只查大纲的「章末型」字段 → 标签轮换了、手感没换）')
    results.append(inject_case('速查卡缺章末禁令', 'references/guides/quick-reference-card.md',
                               lambda s: s.replace('章末不许公式化', 'QQQ').replace('换落笔形态', 'QQQ'),
                               '规则传导断裂'))

    print('㉛ **相邻两章同收束词必须被 check_repetition.py --all 抓到**')
    print('   （"反正…"接着"反正…"—— 章末公式化最硬的信号）')
    _te = Path(tempfile.gettempdir()) / '_guard_endings'
    try:
        _shx.rmtree(_te)
    except Exception:
        pass
    (_te / 'chapters').mkdir(parents=True, exist_ok=True)
    for _i, _txt in enumerate([
        '他睁开眼，天还没亮。',
        '反正明天还得去挨打。',
        '反正他自己知道就够了。',
    ], start=1):
        (_te / 'chapters' / f'第0{_i}章-测试.md').write_text(
            f'# 第0{_i}章 测试\n\n' + _txt + '\n', encoding='utf-8')
    _rek = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_repetition.py'),
                           '--all', str(_te)],
                          cwd=str(SKILL), capture_output=True, text=True, encoding='utf-8', errors='replace')
    _oek = (_rek.returncode == 1) and ('章末同质' in (_rek.stdout or ''))
    print(f'  {"✓" if _oek else "✗"} 相邻两章同收束词  →  '
          f'{"抓到（exit 1）" if _oek else "**没抓到！（exit %d）**" % _rek.returncode}')
    results.append(_oek)
    try:
        _shx.rmtree(_te)
    except Exception:
        pass

    print('㉜ **P3 续航：从 Phase 3 拿掉「两个例外」必须报警**')
    print('   （Phase 3 铁律是"全程禁止确认"——没有例外声明，')
    print('     脚手架的方向对齐会被那条铁律直接压死）')
    # ⚠️ 这里必须把**两种写法都替换掉**：「两个例外」和「两条例外」。
    #    守卫的正则是 `两个例外|两条例外`，只替换一种，另一种仍能匹配 →
    #    用例报"没抓到"，看起来像守卫无效，其实是**注入没到位**。
    #    **这个坑已经在 ⑥（_all_docs 两处调用）、⑬（清单里两处）踩过两次，这是第三次。**
    #    规律：注入前先 grep 一遍目标词的全部出现形式，别凭印象写替换。
    results.append(inject_case('Phase3 无例外声明', 'references/flows/phase3-writing.md',
                               lambda s: s.replace('两个例外', 'QQQ').replace('两条例外', 'QQQ'),
                               '剧情脚手架传导链断裂'))

    print('㉝ **P3 续航：从 plot-scaffold 拿掉「意图浓度」必须报警**')
    print('   （没有浓度判据，介入频次会退回"按章数"——对弱意图作者是负担、对强意图作者是缺位）')
    results.append(inject_case('脚手架缺意图浓度', 'references/guides/plot-scaffold.md',
                               lambda s: s.replace('意图浓度', 'QQQ'), '剧情脚手架传导链断裂'))

    print('㉞ **low 模式：从 low-cost-mode 拿掉「人味不降清单」必须报警**')
    print('   （low 会被读成"可以牺牲人味"——而用户要的是"高效质检+人味"，不是"省钱不保人味"）')
    results.append(inject_case('low 缺人味不降', 'references/guides/low-cost-mode.md',
                               lambda s: s.replace('人味不降', 'QQQ'), '低费用模式口径不一致'))

    print('㉟ **low 模式：其它文件里写回旧说法「只跑 3 个脚本」必须报警**')
    print('   （机械项是脚本、0 reasoning token——砍它等于白扔质量；散落各处必然互相矛盾）')
    results.append(inject_case('phase3 残留旧 low 口径', 'references/flows/phase3-writing.md',
                               lambda s: s.replace('机械项清单（两种模式都全跑、一个不砍）', '只跑 3 个脚本'),
                               '低费用模式口径不一致'))

    # ── ㊱–㊴ 低消耗 × 链式流水线的接缝（2026-09-21）──────────────────────
    # 四条各对应一个**真实机制**，不是为凑数——去掉它，对应的洞就没有任何东西拦着。
    print('㊱ **主编持正文回流必须报警**（成本洞）')
    print('   （主编是流水线里活得最久的角色；它读正文 = 子代理隔离省下的钱被它一个人花回去）')
    results.append(inject_case('主编持正文回流', 'references/guides/parallel-workflow.md',
                               lambda s: s.replace('| 主编 | 圣经 + **交接卡**（不是正文）',
                                                   '| 主编 | 圣经 + 全部真实章节'),
                               '低消耗流水线口径不一致'))

    print('㊲ **低模式去掉「主编零正文」的支点必须报警**')
    print('   （没有 make_handoff 裁卡，主编只能回头读正文——成本洞重新出现）')
    results.append(inject_case('low 缺交接卡支点', 'references/guides/low-cost-mode.md',
                               lambda s: s.replace('make_handoff', 'QQQ'),
                               '低消耗流水线口径不一致'))

    print('㊳ **文档里的命令参数在脚本里不存在，必须报警**')
    print('   （"文档写了命令"≠"命令有效"——上一轮结构审计 4 个 P0 里有 2 个属于这类）')
    results.append(inject_case('文档命令参数不存在', 'references/guides/low-cost-mode.md',
                               lambda s: s.replace('--drift', '--drfit', 1),
                               '低消耗流水线口径不一致'))

    print('㊴ **链式流水线去掉「边界冻结」必须报警**（质量洞）')
    print('   （磨手改结尾 → 下一章承接的结尾被掉包；两个都"对"，当场没有脚本会报）')
    results.append(inject_case('流水线缺边界冻结', 'references/guides/chained-pipeline.md',
                               lambda s: s.replace('边界冻结', 'QQQ'),
                               '低消耗流水线口径不一致'))

    print('㊵ **SKILL.md 里残留旧 low 口径必须报警**')
    print('   ⚠️ 2026-09-21 实测发现的**真漏洞**：守卫原名单只查 phase3 / phase4，')
    print('      漏了 SKILL.md——而它是**主入口**，写错的口径会被当权威读。')
    print('      当时 SKILL.md 的「模式开关」一节确实还在写"只跑 3 个脚本…默认串行"。')
    results.append(inject_case('SKILL.md 残留旧 low 口径', 'SKILL.md',
                               lambda s: s.replace('机械质检**全跑脚本**（0 token，一个不砍）',
                                                   '质检降级为只跑 3 个脚本'),
                               '低费用模式口径不一致'))

    print('㊶ **拿掉漂移自测入口必须报警**')
    print('   （判据的坑改完必须能随时复验——不必等真实项目在手边）')
    print('   ⚠️ 初版漏抓：`--selftest` 在文件里出现**两次**（add_argument + 报错提示文案），')
    print('      只替换一处 → 旧判据"字符串还在"照样通过。故判据改为查**声明**。')
    print('      （同一概念多处出现，这是本项目第 6 次踩。）')
    results.append(inject_case('去掉漂移自测', 'check_aistyle.py',
                               lambda s: s.replace("'--selftest'", "'--selftst'"),
                               '低消耗流水线口径不一致'))

    # ── ㊷–㊼ 「三站齐备」守卫（2026-09-23）────────────────────────────
    # 这一组守的是本轮审计的核心结论：**一条机制缺任何一站就等于不存在**
    #   （缺生成点=没东西可填；缺交付点=执行者看不到；缺校验点=不可证伪）。
    # 每条都对应一个**实测过的孤儿或矛盾**，不是为凑数。
    _STATIONS = '一致性机制的「三站」不齐'

    print('㊷ **删掉细纲里的契约栏位必须报警**（生成点）')
    print('   （"读点 12 处、产出 0 处"——主逻辑机制不能只靠读点齐全）')
    print('   ⚠️ 初版漏抓：守卫关键词写的是"接口契约"，而改个标题它照样在——')
    print('      判据改成查**键名**（`进入·位置时间`）才真正验到"栏位存在"。')
    results.append(inject_case('契约无生成点', 'references/flows/phase2-planning.md',
                               lambda s: s.replace('进入·位置时间', 'QQQ'),
                               _STATIONS))

    print('㊸ **删掉 Phase 3 的契约校验必须报警**（交付点）')
    results.append(inject_case('契约无交付点', 'references/flows/phase3-writing.md',
                               lambda s: s.replace('check_contract.py', 'QQQ.py'),
                               _STATIONS))

    print('㊹ **删掉三层锚的累计基线必须报警**（交付点）')
    print('   （只给批锚时**累积漂移在定义上不可见**——每批都合格，全书可以漂到任意远）')
    results.append(inject_case('三层锚缺累计基线', 'references/flows/phase3-writing.md',
                               lambda s: s.replace('--book-base', '--QQQ'),
                               _STATIONS))

    print('㊺ **删掉意象配额开关必须报警**（校验点）')
    print('   （它是唯一的真孤儿：规则写了、零读点——本轮才把它变成脚本）')
    results.append(inject_case('意象配额孤儿化', 'references/flows/phase3-writing.md',
                               lambda s: s.replace('--imagery', '--QQQ'),
                               _STATIONS))

    print('㊻ **从执行清单里删掉锚点滚动必须报警**（交付点）')
    print('   （主力文风机制此前正因"不在任何执行清单里"而等于不存在）')
    results.append(inject_case('锚点滚动孤儿化', 'SKILL.md',
                               lambda s: s.replace('锚点滚动', 'QQQ'),
                               _STATIONS))

    print('㊼ **删掉伏笔表的单载体声明必须报警**（生成点）')
    print('   （双载体同名必然漂移，而漂移表现恰好是"同一伏笔两处状态不同"）')
    results.append(inject_case('伏笔双载体', 'references/guides/outline-template.md',
                               lambda s: s.replace('唯一权威载体', 'QQQ'),
                               _STATIONS))

    print('㊽ **Phase 2 的模式选择提示写回旧 low 口径必须报警**')
    print('   ⚠️ 这是**最坏的位置**：它在作者做选择的那一刻说"低费用模式…质量略降"，')
    print('      作者会带着这个预期去选。（守卫名单此前漏了 phase2，本轮补上）')
    results.append(inject_case('phase2 选项写旧口径', 'references/flows/phase2-planning.md',
                               lambda s: s.replace('**批量推进档**', '低费用模式')
                                          .replace('组织形式走**链式流水线 + 批次**。',
                                                   '组织形式默认串行，质量略降。'),
                               '低费用模式口径不一致'))

    print('㊾ **成本审计失去真实数据源必须报警**')
    print('   ⚠️ 它原来找 `<项目>/AGENT工作日志/session.jsonl`——**该目录从未被任何环节产出**，')
    print('      于是这个"成本审计工具"自己就是个孤儿：有校验点，却指向不存在的生成点。')
    print('      真实 usage 在 `~/.workbuddy/traces/`。')
    results.append(inject_case('审计失去数据源', 'audit_tokens.py',
                               lambda s: s.replace("'--traces'", "'--Qtraces'"),
                               '低消耗流水线口径不一致'))

    print('㊿ **成本判据退回"只给比例"必须报警**')
    print('   （"cacheRead 占 98.7%"是比例，它不告诉你绝对量；真正该盯的是')
    print('     **每次调用平均重发多少上下文**——实测中位 149,675 tokens/次）')
    results.append(inject_case('判据只剩比例', 'references/guides/token-efficiency.md',
                               lambda s: s.replace('每次调用平均重发上下文', 'QQQ'),
                               _STATIONS))

    print('51. **拿掉任务包的"拒绝开工"校验必须报警**')
    print('   （任务包编译器是压"每次调用上下文"的唯一机械手段：')
    print('     槽位有预算，缺槽位/超预算就退出 1 —— 把"记得填"变成"编译不过"）')
    results.append(inject_case('任务包失去校验', 'make_task_package.py',
                               lambda s: s.replace("'--check'", "'--Qcheck'"),
                               '低消耗流水线口径不一致'))

    print('52. **脚本改回「非幂等 stdio 包装」必须报警**')
    print('   ⚠️ 这类写法会让**脚本互相 import 直接崩**（旧 wrapper 被 GC 时')
    print('      把共用的底层 buffer 一起关掉 → ValueError: I/O operation on closed file）。')
    print('      实测触发：make_task_package 要复用 check_contract / make_handoff 的解析逻辑。')
    print('   ⚠️ 注入用的"坏代码"必须**拼接构造**，不能字面写——')
    print('      守卫会扫 test_guards.py 自己，字面写进去等于让它命中本文件的 fixture。')
    _BAD_WRAP = ('sys.stdout = io.TextIOWrapper(sys.stdout'
                 + '.buffer, encoding=\'utf-8\')')
    results.append(inject_case(
        '非幂等 stdio 包装', 'check_contract.py',
        lambda s: s.replace('ensure_utf8_stdio()', _BAD_WRAP),
        '非幂等'))

    print('53. **工单失去 Phase 3 接入必须报警**')
    print('   （「一次合格率」此前没有数据源：retryCount 从未落盘、成本与质量不在一处。')
    print('     工单是唯一让"改规则前后能对比"的东西——它断链就等于又回到凭感觉改。）')
    results.append(inject_case('工单断链', 'references/flows/phase3-writing.md',
                               lambda s: s.replace('make_workorder.py', 'QQQ.py'),
                               _STATIONS))

    print('54. **retryCount 不再落盘必须报警**')
    print('   （它此前只存在于铁律七的规则描述里、从未被记录过任何一次实际值——')
    print('     于是"返工率"在**定义上**不可统计。）')
    results.append(inject_case('返工不落盘', 'references/guides/creation-ledger.md',
                               lambda s: s.replace('本章返工轮次', 'QQQ'),
                               _STATIONS))

    print('55. **指标分层被删必须报警**（CTQ = 给耦合指标一个可解的目标）')
    print('   （17 指标同时硬门 → 改 A 坏 B → 实测某章节律被查 189 次、脚本调用 486 次）')
    results.append(inject_case('指标不分层', 'references/execution-contract.md',
                               lambda s: s.replace('修法①：指标分层', 'QQQ'),
                               _STATIONS))

    print('53. **工单失去 Phase 3 接入必须报警**')
    print('   （「一次合格率」此前没有数据源：retryCount 从未落盘、成本与质量不在一处。')
    print('     工单是唯一让"改规则前后能对比"的东西——它断链就等于又回到凭感觉改。）')
    results.append(inject_case('工单断链', 'references/flows/phase3-writing.md',
                               lambda s: s.replace('make_workorder.py', 'QQQ.py'),
                               _STATIONS))

    print('54. **retryCount 不再落盘必须报警**')
    print('   （它此前只存在于铁律七的规则描述里、从未被记录过任何一次实际值——')
    print('     于是"返工率"在**定义上**不可统计。）')
    results.append(inject_case('返工不落盘', 'references/guides/creation-ledger.md',
                               lambda s: s.replace('本章返工轮次', 'QQQ'),
                               _STATIONS))

    print('55. **指标分层被删必须报警**（CTQ = 给耦合指标一个可解的目标）')
    print('   （17 指标同时硬门 → 改 A 坏 B → 实测某章节律被查 189 次、脚本调用 486 次）')
    results.append(inject_case('指标不分层', 'references/execution-contract.md',
                               lambda s: s.replace('修法①：指标分层', 'QQQ'),
                               _STATIONS))

    print('56. **槽位改回「空则静默跳过」必须报警**')
    print('   （实测：这个写法让 author_shot / lesions / diagnosis 三个槽位**永远不出现在包里**，')
    print('     而校验器只在 required 时报错 → 静默通过。生成物里它们出现 0 次。）')
    results.append(inject_case(
        '槽位静默跳过', 'scripts/make_task_package.py',
        lambda s: s.replace(
            '        parts.append(SLOT_OPEN.format(key=key, budget=budget, req=int(req)))',
            '        if not body and not req:\n            continue\n'
            '        parts.append(SLOT_OPEN.format(key=key, budget=budget, req=int(req)))', 1),
        '前移机制不完整'))

    print('57. **细纲模板丢「场景字数配额」栏位必须报警**')
    print('   （占返工 52% 的字数，其前移手段就是在细纲把配额拆到场景；')
    print('     没有栏位 = "写进细纲"无处可写）')
    results.append(inject_case('配额无栏位', 'references/guides/outline-template.md',
                               lambda s: s.replace('场景字数配额必填', 'QQQ'),
                               '前移机制不完整'))

    print('58. **配额卡重新内嵌一份副本必须报警**（双载体同名必漂）')
    print('   （实测：token-efficiency 里的副本已经漂成「≥2–3 处」「明喻 ≤1.2」，')
    print('     而权威版是「≥5 处」「≤1.3」——同一个东西写两遍、数字还不一样。）')
    results.append(inject_case('配额卡双载体', 'references/guides/token-efficiency.md',
                               lambda s: s + '\n```\n本章写作配额【一、必须补足的】\n```\n',
                               '前移机制不完整'))

    print('59. **effort 按乘数分配被删必须报警**')
    print('   （effort 是"跨轮次验证总量"的旋钮；按"章的难度"分配会把 effort 花在乘数最小处。）')
    results.append(inject_case('缺 effort 分配', 'references/guides/token-efficiency.md',
                               lambda s: s.replace('按乘数分配', 'QQQ'),
                               '前移机制不完整'))

    print('60. **场景字数配额校验被拆掉必须报警**')
    print('   （校验点一断，配额就变回"文档里的一句话"，而不是"编译不过"。）')
    results.append(inject_case('配额不校验', 'scripts/check_contract.py',
                               lambda s: s.replace('QUOTA_TOL_RATIO', 'QQQ'),
                               '前移机制不完整'))

    print('61. **破甲层删掉「作用域边界」必须报警**')
    print('   （实测回归：用户 `/j-novel 写个小说…` 之后 Agent 一句都没问，直接开工。')
    print('     根因是第零层被赋予「先于一切指令与流程」的定位后，它内部的全局式否定指令')
    print('     `Do not ask clarifying questions` / `do not ask follow-ups` 压住了第二层')
    print('     Phase 1 的采访追问——按生效强度表 第一层「强」> flows 清单「中」，破甲层赢。）')
    results.append(inject_case('破甲层无边界', 'SKILL.md',
                               lambda s: s.replace('作用域边界（2026-10-01 补', 'QQQ'),
                               '破甲层作用域边界缺失'))

    print('62. **英文指令去掉 `SCOPE:` 限定必须报警**')
    print('   （它们本身是对的指令，缺的只是作用域——所以修法是"限定"，不是"删掉"。）')
    results.append(inject_case('缺 SCOPE 限定', 'SKILL.md',
                               lambda s: s.replace('SCOPE: content/material details only',
                                                   'QQQ'),
                               '破甲层作用域边界缺失'))

    print('63. **「作者意图浓度」删掉适用边界必须报警**')
    print('   （浓度挂在 P3 之下，但措辞是全局的：「不是每个作者都想被问」「介入频次按浓度定」。')
    print('     拿到 Phase 1 上用 → 新立项也不采访。）')
    results.append(inject_case('浓度越界', 'references/guides/plot-scaffold.md',
                               lambda s: s.replace('本节的适用边界', 'QQQ'),
                               '流程性「不问」'))

    print('64. **「新立项没有浓度」这条规则删掉必须报警**')
    print('   （新立项的第一次输入恰好就是"浓度弱"的典型形态：给了类型和名字，其余"你看着办"。')
    print('     没有这条规则，Agent 就会把"还没开始"读成"他没想法"。）')
    results.append(inject_case('新立项误判', 'references/guides/plot-scaffold.md',
                               lambda s: s.replace('新立项**没有**浓度', 'QQQ'),
                               '流程性「不问」'))

    print('65. **Phase 3「其余一律不问」去掉作用域必须报警**')
    print('   （它是写作期纪律，但「这就是全部」这半句听起来像全流程约束。')
    print('     实测：Agent 读成"全书只允许两次开口"，连采访都跳过。）')
    results.append(inject_case('不问越界', 'references/flows/phase3-writing.md',
                               lambda s: s.replace('「其余一律不问」的作用域', 'QQQ'),
                               '流程性「不问」'))

    print('66. **对话下限被再次取消必须报警**')
    print('   （2026-10-02 实测：取消下限 ⇒ 某项目 6/10 章低于人类下限、第 9/10 章仅 5.6%/3.3%，')
    print('     而当时的体系里没有任何一处会因此报警。）')
    results.append(inject_case('取消对话下限', 'references/flows/phase3-writing.md',
                               lambda s: s.replace('对话占比 20–40%', '对话占比 ≤40%'),
                               '对话体系被削弱'))

    print('67. **核心原则回退「每句对话必须有目的」必须报警**')
    print('   （它直接否定 human-quota 的废话对话/对话混沌配额——')
    print('     是把主角写成功能性机器人的头号原因。）')
    results.append(inject_case('每句必须有目的', 'references/guides/dialogue-writing.md',
                               lambda s: s.replace('1. **对话整体必须有目的',
                                                   '1. **每句对话必须有目的**：推进情节\n'
                                                   '2. **对话整体必须有目的'),
                               '对话体系被削弱'))

    print('68. **声口卡关键栏被删必须报警**')
    print('   （「压力下的反应方式」是冷面角色不变成功能机器人的关键——')
    print('     只定义"短"、不定义"短之外他还有什么"，就必然写成机器人。）')
    results.append(inject_case('声口卡缺关键栏', 'references/guides/character-template.md',
                               lambda s: s.replace('压力下的反应方式', 'QQQ'),
                               '对话体系被削弱'))

    print('69. **质检档案的对话列被删必须报警**')
    print('   （规则在、留痕不在 = 等于不存在。对话是最容易整体塌掉、')
    print('     又最不容易被单个脚本抓到的一块。）')
    results.append(inject_case('对话无留痕', 'references/flows/phase3-writing.md',
                               lambda s: s.replace('对话专项（2026-10-02 新增', 'QQQ'),
                               '对话体系被削弱'))

    print('70. **对话判据退回「只有上限」必须报警**')
    print('   （kind=max 时"对话占比 0%"会被判合格——这正是塌掉的方式。）')
    results.append(inject_case('对话只留上限', 'scripts/check_human_rhythm.py',
                               lambda s: s.replace("'dialog':       dict(kind='range'",
                                                   "'dialog':       dict(kind='max'"),
                               '对话体系被削弱'))

    print('71. **来源的「角色嗓音差异化」被删必须报警**')
    print('   （融合时这条整块没进来过；它是"所有人一个调"的唯一解药。）')
    results.append(inject_case('声口指标缺失', 'references/guides/dialogue-writing.md',
                               lambda s: s.replace('角色嗓音差异化', 'QQQ'),
                               '对话体系被削弱'))

    print('72. **retryCount 的字段级校验被删必须报警**')
    print('   （实测铁证：05-创作台账 里同一文件、同一模板的两个字段命运相反 ——')
    print('     「最近重读章号」有校验→有值；「retryCount」无校验→整个字段缺失。')
    print('     而它是「一次合格率」唯一的数据源。）')
    results.append(inject_case('缺字段级校验', 'scripts/check_batch_gate.py',
                               lambda s: s.replace('ledger_field_missing', 'QQQ'),
                               '字段级校验被削弱'))

    print('73. **07-剧情脚手架的串行读点被删必须报警**')
    print('   （此前只有并行模式的 make_task_package 读它 →')
    print('     串行模式（单会话写作）下作者给的画面写了没人用。）')
    results.append(inject_case('脚手架无读点', 'references/flows/phase3-writing.md',
                               lambda s: s.replace('07-剧情脚手架.md', 'QQQ.md'),
                               '字段级校验被削弱'))

    print('74. **改了主流程忘同步支线（口径漂移）必须报警**')
    print('   （v6.7.0 把对话占比从「≤40%，无下限」改成「20–40%；<10% 硬失败」，')
    print('     改了 phase3 却漏了 4 个下游文件——其中 subagent-brief.md 是子代理任务包的')
    print('     唯一事实源，且与同一文件上方的配额卡自相矛盾。）')
    results.append(inject_case('口径漂移', 'references/guides/chapter-template.md',
                               lambda s: s.replace('对话占比 **20–40%**', '对话占比 ≤40%（无下限）')
                                          .replace('低于 10% 判失败', ''),
                               '口径一致性被破坏'))

    print('75. **生成点丢掉新口径锚点必须报警**')
    print('   （细纲的「句法目标表」会被复制进每一批细纲——旧口径漏到那里就是全批漏。）')
    results.append(inject_case('细纲口径缺失', 'references/flows/phase2-planning.md',
                               lambda s: s.replace('| 对话占比 | X% | **20–40%（场景章 ≥25%；<10% 判失败）** |',
                                                   '| 对话占比 | X% | ≤40% |'),
                               '口径一致性被破坏'))

    print('76. **「先补足、再压住」的顺序被削弱必须报警**')
    print('   （用户反馈"文笔还是很 AI"的机制根因：配额几乎全是"压住"（≤），')
    print('     再叠加 CTQ 把这些项降为"不阻塞" → 永久不被修 → 删掉的没补回来 = 干净但空洞。）')
    results.append(inject_case('补足清单被删', 'scripts/check_human_rhythm.py',
                               lambda s: s.replace('_FIX_HINT', 'QQQ'),
                               '「先补足、再压住」的整改顺序被削弱'))

    print('77. **速查卡丢掉「补足」必须报警**')
    print('   （★ 子代理**只读速查卡**——这一处缺了，写手永远只学到"把超标的降下来"。）')
    results.append(inject_case('速查卡无补足', 'references/guides/quick-reference-card.md',
                               lambda s: s.replace('先补足', 'QQQ'),
                               '「先补足、再压住」的整改顺序被削弱'))

    # ══════════════════════════════════════════════════════════════════
    # 78 / 79：v7.0.0「具体性闸门」的**校验点**（本体层）
    #   背景：17 项硬指标 + 成本配额卡全是**频率指标**（多长/多密/多少次），
    #   而"这一章里的东西是不是只属于这本书"这一层此前**只有说明文字**——
    #   没有生成点、没有交付点、没有校验点。v7.0.0 补成三站齐备，这两个用例守校验点：
    #     78 = 有牙齿的那一半（批次闸门硬拦：清单没定 / 不够 3 项）
    #     79 = 只提示的那一半（实物覆盖率：承诺 4 项、正文兑现 1 项）
    # ══════════════════════════════════════════════════════════════════

    def _mk_concrete_proj(base, with_list, plot='pad2'):
        """造一个「除实物清单/剧情卡外**其余全绿**」的项目。

        ⚠️ 其余必须全绿：否则闸门失败的原因可能是别的判据，用例就成了
        "我以为抓到了实物清单，其实抓到的是别的东西"。（所以 78 里有一组 control。）

        `plot` 控制剧情卡 fixture（v7.1.0 新增）：
            None     → 不产出卡（注入：卡缺失）
            'pad2'/'plain'/'pad3' → 按该章号写法产出合规卡
            'nohook' → 产出卡但删掉 `**本章钩子**` 字段（注入：字段缺失）
        """
        try:
            _shx.rmtree(base)
        except Exception:
            pass
        (base / 'chapters' / '_meta').mkdir(parents=True, exist_ok=True)
        (base / 'chapters' / '第01章-测试.md').write_text(
            '# 第01章 测试\n\n'
            + '风从巷口灌进来，晾在绳上的衣服翻了个面，水滴落在青石板上。' * 60 + '\n',
            encoding='utf-8')
        _meta = '# 第01章 元数据\n\n## 本章概要\n- 核心事件：fixture\n'
        if with_list:
            _meta += ('\n## 实物清单（本章 3–8 项：只属于这一章的东西）\n'
                      '- 半包受潮的火柴\n- 302 路末班车\n- 补了两次的蓝布书包\n')
        (base / 'chapters' / '_meta' / '第01章-测试.meta.md').write_text(
            _meta, encoding='utf-8')
        # ★ 2026-10-09 v7.1.0：剧情卡（与实物清单同层的 L2 必填项）
        if plot:
            write_plot_card(base, [1], style=('pad2' if plot in ('pad2', 'nohook') else plot),
                            hook=(plot != 'nohook'))
        (base / '02-写作计划.json').write_text(json.dumps({
            'costMode': 'standard', 'wordsPerChapter': 2000,
            'chapters': [{'chapterNumber': 1, 'status': 'completed', 'wordCountPass': True}],
        }, ensure_ascii=False), encoding='utf-8')
        (base / '04-质检档案.md').write_text('### 第1章\n', encoding='utf-8')
        (base / '05-创作台账.md').write_text(
            '最近重读章号：第1章\n本章返工轮次（retryCount）：0\n', encoding='utf-8')
        (base / '03-状态台账.md').write_text('第1章 ✓\n', encoding='utf-8')
        (base / '01-大纲.md').write_text(
            '| 章节 | 标题 | 开场类型 | 章末型 |\n|---|---|---|---|\n'
            '| 第1章 | A | 新起 | 甲·信息结算 |\n', encoding='utf-8')
        return base

    print('78. **实物清单缺失 → 批次闸门拦下**（清单没定 = 本章没有只属于它的东西）')
    print('   （v7.0.0 之前"没定清单"和"定了清单"过闸门的结果完全一样 ——')
    print('     17 项频率指标全绿也看不出这个洞。生成点/交付点都有了，校验点此前是空的。）')
    _p78 = _mk_concrete_proj(_tmp / '_guard_concrete_gate', True)
    _r78a = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_batch_gate.py'),
                            str(_p78)],
                           cwd=str(SKILL), capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
    _p78b = _mk_concrete_proj(_tmp / '_guard_concrete_gate_bad', False)
    _r78b = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_batch_gate.py'),
                            str(_p78b)],
                           cwd=str(SKILL), capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
    _o78b = (_r78b.stdout or '') + (_r78b.stderr or '')
    print(f'       control（有清单）exit={_r78a.returncode}（应 0）｜'
          f'injected（无清单）exit={_r78b.returncode}（应 1）')
    # ⚠️ 断言用**阻塞项标记** `【实物清单缺失】`，不用 "实物清单" 这种也会出现在
    #    "补法提示"里的词 —— 本项目已有两次"断言命中无关文本"造成的假通过。
    #    （规律：存在性判据要问"**有没有一处合规**"，而不是"某段文本出现过没有"。）
    _ok78 = ((_r78a.returncode == 0) and (_r78b.returncode == 1)
             and ('【实物清单缺失】' in _o78b))
    print(f'  {"✓" if _ok78 else "✗"} 实物清单闸门  →  '
          f'{"抓到（control 过 / injected 拦）" if _ok78 else "**没抓到！**"}')
    if not _ok78:
        print('       injected 输出尾部：', _o78b[-300:].replace('\n', ' / ')[:200])
    results.append(_ok78)

    print('79. **实物覆盖率提示生效**（清单 4 项、正文只兑现 1 项 → 覆盖率 + 未兑现条目）')
    print('   （这一层**只提示不阻塞**：没有人类基线，且脚本判断不了"清单本身好不好"。'
          '所以它既不能挡住合格稿，也必须真的把"承诺了没写"念出来。）')
    _p79 = _tmp / '_guard_concrete_coverage'
    try:
        _shx.rmtree(_p79)
    except Exception:
        pass
    (_p79 / '细纲').mkdir(parents=True, exist_ok=True)
    (_p79 / 'chapters' / '_meta').mkdir(parents=True, exist_ok=True)
    (_p79 / '细纲' / '第01章细纲.md').write_text(
        '# 第01章 测试\n\n'
        '- 进入·位置时间: 巷口\n- 进入·情绪: 平静\n- 进入·已知: 无\n- 进入·身体: 无\n'
        '- 退出·位置时间: 屋里\n- 退出·情绪: 平静\n- 退出·已知: 无\n- 退出·身体: 无\n',
        encoding='utf-8')
    _hit_item = '半包受潮的火柴'
    _miss_items = ['302 路末班车', '补了两次的蓝布书包', '灶台上那道灰痕']
    (_p79 / 'chapters' / '第01章-测试.md').write_text(
        '# 第01章 测试\n\n他摸出那半包受潮的火柴，划了三下，第四下才着。\n'
        + '风从巷口灌进来，晾在绳上的衣服翻了个面。' * 60 + '\n', encoding='utf-8')
    (_p79 / 'chapters' / '_meta' / '第01章-测试.meta.md').write_text(
        '# 第01章 元数据\n\n## 本章概要\n- 核心事件：fixture\n\n'
        '## 实物清单（本章 3–8 项：只属于这一章的东西）\n'
        '- ' + _hit_item + '\n' + ''.join('- ' + _x + '\n' for _x in _miss_items),
        encoding='utf-8')
    _r79 = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_contract.py'),
                           str(_p79)],
                          cwd=str(SKILL), capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    _o79 = _r79.stdout or ''
    _cl = [_l.strip() for _l in _o79.split('\n')]
    # ⚠️ 判据 = "**有没有一处**合规的行"，不是"某段文本出现过"：
    #    覆盖率行必须**整行**对上（章号 + 清单 4 项 + 兑现 1 项 + 25%），
    #    未兑现行必须同时含 3 个未兑现项、且**不含**已兑现的那一项。
    _row79 = [_l for _l in _cl if _l.startswith('第1章') and '清单 4 项' in _l
              and '兑现 1 项' in _l and '25%' in _l]
    _mis79 = [_l for _l in _cl if _l.startswith('未兑现')
              and all(_x in _l for _x in _miss_items) and _hit_item not in _l]
    _ok79 = (_r79.returncode == 0) and bool(_row79) and bool(_mis79)
    print(f'  {"✓" if _ok79 else "✗"} 覆盖率与未兑现清单  →  '
          f'{"抓到" if _ok79 else "**没抓到！**"}'
          f'（exit={_r79.returncode}，应 0 = 不阻塞）')
    if not _ok79:
        for _l in _cl[-14:]:
            print('       ', _l[:110])
    results.append(_ok79)

    # ══════════════════════════════════════════════════════════════════
    # 80 / 81：v7.1.0「剧情共创」的**校验点**（剧情层）
    #   背景：剧情（"第 N 章发生什么、这一章的钩子是哪件事"）此前**唯一的入口**
    #   是剧情脚手架，而它被标成【可选】→ 实测 29 本有 AI 大纲的项目里只有 2 本
    #   产出过（≈ 7%）→ 默认路径变成"AI 按通用节奏推导 + 让作者确认"。
    #   本轮把流程改成"每批细纲展开**之前**先出剧情卡 → 磨 → 再展开"，这两个用例守校验点：
    #     80 = 卡缺失 / 字段缺失 → 批次闸门硬拦（有牙齿的那一半）
    #     81 = **"作者未回应"必须仍然放行**（防未来有人把判据加严的护栏）
    #   ⚠️ 判据**只查两件"动作"**：卡有没有产出、问过没。
    #      **绝不查"作者改了几处"** —— 不可校验，且会把主 Agent 逼成"逼作者改东西过检"。
    # ══════════════════════════════════════════════════════════════════

    def _run_gate(base):
        _r = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'check_batch_gate.py'),
                             str(base)],
                            cwd=str(SKILL), capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
        return _r.returncode, (_r.stdout or '') + (_r.stderr or '')

    print('80. **剧情卡缺失 / 字段缺失 → 批次闸门拦下**（不出卡 = 细纲只剩 AI 推的通用节奏）')
    print('   （v7.1.0 之前"出了卡"和"没出卡"过闸门的结果完全一样 ——')
    print('     控制/注入对照三连：control 用不补零写法 `## 第 1 章` 证明多写法兼容；')
    print('     注入 B 用 `## 第001章` 且删掉钩子字段 —— 报"缺字段"而非"没有卡"，')
    print('     正好反证三种章号写法都被解析器认了。）')
    _p80 = _mk_concrete_proj(_tmp / '_guard_plotcard_gate', True, plot='plain')
    _rc80, _o80 = _run_gate(_p80)
    _p80b = _mk_concrete_proj(_tmp / '_guard_plotcard_none', True, plot=None)
    _rb80, _ob80 = _run_gate(_p80b)
    _p80c = _mk_concrete_proj(_tmp / '_guard_plotcard_nofield', True, plot=None)
    # 注入 B：卡存在（`第001章` 写法）但删掉 `**本章钩子**` 字段
    write_plot_card(_p80c, [1], style='pad3', hook=False)
    _rc80c, _oc80 = _run_gate(_p80c)
    print(f'       control（有卡·`第 1 章`）exit={_rc80}（应 0）｜'
          f'注入A（无卡）exit={_rb80}（应 1）｜注入B（有卡缺钩子·`第001章`）exit={_rc80c}（应 1）')
    # ⚠️ 断言用**阻塞项标记** `【剧情卡缺失】`，不用 "剧情卡" 这种也会出现在
    #    "补法提示"与"作者未回应"说明里的词 —— 本项目已有两次"断言命中无关文本"的假通过。
    #    规律：存在性判据要问"**有没有一处合规**"，而不是"某段文本出现过没有"。
    _ok80 = (_rc80 == 0 and _rb80 == 1 and _rc80c == 1
             and ('【剧情卡缺失】' in _ob80) and ('【剧情卡缺失】' in _oc80)
             # 注入 B 报的是"缺字段"而不是"没有卡" → 证明 `第001章` 被认出来了
             and ('缺 `**本章钩子**`' in _oc80))
    print(f'  {"✓" if _ok80 else "✗"} 剧情卡闸门  →  '
          f'{"抓到（control 过 / 两种注入都拦）" if _ok80 else "**没抓到！**"}')
    if not _ok80:
        print('       注入B 输出尾部：', _oc80[-300:].replace('\n', ' / ')[:220])
    results.append(_ok80)

    print('81. **「作者未回应」必须仍然放行**（本轮的护栏用例：防判据被后人加严）')
    print('   （理由：**"作者没回"是设计内的合法结果** —— 问了、给了选项、记了录就算过，')
    print('     节流档/推荐档的不问批次用"一次性告知"，同样只记一行。')
    print('     如果它被拦下，主 Agent 就会去逼作者改东西以过检 = **表演式修改**，')
    print('     那比没磨更糟：它污染了"哪些是作者的品味"这条唯一的对账基准。）')
    _p81 = _mk_concrete_proj(_tmp / '_guard_plotcard_noresp', True, plot='pad2')
    _r81a, _o81a = _run_gate(_p81)
    _card81 = _p81 / '细纲' / '剧情卡-第01-01章.md'
    _card81.write_text(
        _card81.read_text(encoding='utf-8').replace(
            '共创状态：已与作者确认（2026-10-09）', '共创状态：作者未回应（2026-10-09）'),
        encoding='utf-8')
    _r81b, _o81b = _run_gate(_p81)
    _ok81 = (_r81a == 0 and _r81b == 0
             and '【剧情卡缺失】' not in _o81b and '【剧情卡缺失】' not in _o81a)
    print(f'       control（已与作者确认）exit={_r81a}（应 0）｜'
          f'注入（作者未回应）exit={_r81b}（**应 0 —— 拦住就是护栏坏了**）')
    print(f'  {"✓" if _ok81 else "✗"} 未回应仍放行  →  '
          f'{"放行（不会被逼着表演式修改）" if _ok81 else "**被拦下了！这条判据太严**"}')
    if not _ok81:
        print('       输出尾部：', _o81b[-300:].replace('\n', ' / ')[:220])
    results.append(_ok81)

    print('82. **细纲目录里的"非细纲产物"不得抢走 `outlines[0]`**（v7.1.1 修的真实回归）')
    print('   （判据是**排序**，不是"名字好记"：`细纲/` 里现在会放剧情卡、文风锚点，')
    print("     而 '剧' U+5267 / '文' U+6587 **都小于** '第' U+7B2C →")
    print('     sorted() 之后它们必然排第一，而 make_task_package 取的是 `outlines[0]` →')
    print('     任务包会从**剧情卡**里抽 contract / voice_preset / batch_global，')
    print('     表现为"细纲里明明写好了，却报 3 个【待填】"。')
    print('     `文风锚点-*.md` 是同款旧坑 —— 所以修法是"排除已知产物 + 优先像细纲的"，')
    print('     而不是给剧情卡打一个特例补丁。）')
    _p82 = _tmp / '_guard_outline_order'
    try:
        _shx.rmtree(_p82)
    except Exception:
        pass
    (_p82 / '细纲').mkdir(parents=True, exist_ok=True)
    # 三个文件都排在真实细纲之前（按 Unicode 码位）
    (_p82 / '细纲' / '剧情卡-第01-05章.md').write_text('# 剧情卡\n', encoding='utf-8')
    (_p82 / '细纲' / '文风锚点-第1批.md').write_text('# 锚点\n', encoding='utf-8')
    (_p82 / '细纲' / '第01-05章细纲.md').write_text('# 细纲\n', encoding='utf-8')
    try:
        from check_contract import find_outline_files  # noqa: PLC0415
        _names = [p.name for p in find_outline_files(_p82)]
        _err82 = ''
    except Exception as _e:                              # pragma: no cover
        _names, _err82 = [], repr(_e)
    # 断言问的是"**第一份是不是细纲**"（而不是"列表里有没有细纲"）——
    # 因为故障恰恰是"细纲在列表里，但不在第一"。
    _ok82 = (bool(_names) and _names[0] == '第01-05章细纲.md')
    print(f'       find_outline_files → {_names or _err82}')
    print(f'  {"✓" if _ok82 else "✗"} 细纲不被抢位  →  '
          f'{"第一份就是细纲" if _ok82 else "**被抢走了！任务包会抽错文件**"}')
    results.append(_ok82)

    print('83. **空转的传导规则必须可见**（v7.1.1 修：此前是裸 `continue`，守卫永久空转且无人知道）')
    print('   （成因是**反向传导**：源指南改了措辞、速查卡还留着 → 守卫判"源已无此规则"→ 跳过。')
    print('     实测 32 行传导规则里 3 行（9%）如此，而且**三行全是反向传导**。）')
    print('     这类"看起来在工作的守卫"与本项目最忌讳的"假闸门"同源 ——')
    print('     所以判据是**要看得见**，不是"不许发生"：有意删掉一条规则是合法的。')
    print('     ⚠️ 本用例同时断言它**不许变成阻塞**（否则会在合法场景下卡住发布）。）')
    _ar83 = SKILL / 'scripts' / 'audit_release.py'
    _orig83 = _ar83.read_text(encoding='utf-8')
    _new83 = _orig83.replace(
        "r'作者自己的眼睛', r'作者自己的眼睛')",
        "r'QQQ降级用的不存在锚点', r'作者自己的眼睛')", 1)
    if _new83 == _orig83:
        print('  ✗ 传导规则休眠可见：注入未生效（锚点行没找到，守卫可能被改过）')
        results.append(False)
    else:
        _ar83.write_text(_new83, encoding='utf-8')
        try:
            _out83 = run_audit()
        finally:
            _ar83.write_text(_orig83, encoding='utf-8')
        # ① 提示要出现 ② 点名到具体那一行 ③ **仍然判通过**（提示不是失败）
        _ok83 = ('传导规则休眠' in _out83
                 and '作者画面·是作者的眼睛' in _out83
                 and '✓ 全部通过' in _out83)
        print(f'  {"✓" if _ok83 else "✗"} 休眠可见且不阻塞  →  '
              f'{"提示出现、且仍判通过（对）" if _ok83 else "**没抓到 / 或错误地阻塞了**"}')
        if not _ok83:
            print('       输出尾部：', _out83[-320:].replace('\n', ' / ')[:240])
        results.append(_ok83)

    # ── 阶段级读点守卫（STAGE_CORE）的两个用例 ──────────────────────────
    # 区段锚点与 audit_release.py 的 STAGE_CORE 里那一行**逐字一致**。
    _S5 = r'\[\s*\]\s*5\.\s*【必须】修改'
    _S55 = r'\[\s*\]\s*5\.5'
    _P3 = 'references/flows/phase3-writing.md'

    print('84. **阶段级守卫有牙齿**：把 `occupancy-rewrite` 从「改写阶段」区段删掉必须报警')
    print('   （真实缺口：这一步的 8 个读点是刚补上的，而此前**没有任何检查问过')
    print('     "这一步有没有读点"** —— `_CORE_GUIDES` 只查"这本指南有没有至少一个读点"，')
    print('     所以把它从这里删掉，审计照样全绿。⚠️ 该词在区段内有 4 处（全部属这一步的读点），')
    print('     必须**全部**删掉才构成"这一步没有读点"这个故障态。）')
    results.append(inject_case(
        '改写阶段缺 occupancy-rewrite', _P3,
        lambda s: _region_replace(s, _S5, _S55, 'occupancy-rewrite', 'QQQ'),
        '救不了这个阶段'))

    print('85. **阶段级守卫不会被"别处有读点"骗过**（★ 本轮最重要的一条）')
    print('   （注入：把 `humanize-toolkit` 从「改写阶段」区段**移走**，但它仍留在 phase3 的')
    print('     「动笔前」那一步 —— 即**整份文件里仍旧搜得到**。')
    print('     这条用例钉死的是判据本身：守卫必须**先切区段、再在区段内搜**。')
    print('     若哪天有人把它退回"按整文件查"，这条用例立刻变红 ——')
    print('     那种写法会是一个**永远为绿的守卫**，正是本项目最忌讳的"假闸门"。）')
    results.append(inject_case(
        '改写阶段缺 humanize-toolkit（别处仍有）', _P3,
        lambda s: _region_replace(s, _S5, _S55, 'humanize-toolkit', 'QQQ-toolkit'),
        '救不了这个阶段'))

    # ══════════════════════════════════════════════════════════════════
    # 86 / 87 / 88：v7.2.0「改写工程」的**校验点**
    #   背景：用户"改写修改阶段是去 AI 味最重要的环节，现在**问题探得出来、但改得一般**"。
    #   本轮新增两条工序 —— **不许出现"要求了但不产出"**（本项目已犯过 4 次同型）：
    #     ① 改前出「修改方案表」（治"14 项耦合 → 多轮打地鼠"）
    #     ② 改后做「改前改后三行对照」（治"Agent 不知道自己改得有没有效果"）
    #   三个用例守的正是这两条工序的**校验点**：
    #     86 = 方案表缺失/字段缺失（**软提示 + 硬拦**两层）
    #     87 = ★ **"改前 = 改后"必须拦下**（防空改 —— 本轮最重要的一条）
    #     88 = 单元编号不是摆设（U1–U6 之外 → 报错）
    #   ⚠️ 判据只停在三件**机器可验**的事：**文件/字段在不在 · 改前 ≠ 改后 ·
    #      单元编号在不在契约里**。"为什么更好是否具体""单元选得对不对"
    #      **机器判不了 → 一条判据都不写**（写正则去猜会逼出表演式改写）。
    # ══════════════════════════════════════════════════════════════════
    _PLAN_HEAD = '| # | 缺陷项 | 单元 | 定位 | 改成什么 | 会影响 |'

    def _plan_text(entries=None, *, unit='U1', why=True):
        """改写方案表 fixture —— 字段名与 `guides/rewrite-units.md` 第四节**逐字一致**。

        `entries = [(改前, 改后), …]`；默认一条**真实**改写（改前 ≠ 改后）。
        """
        entries = entries if entries is not None else [
            ('他把火柴塞进兜里。',
             '他把火柴塞进兜里，指尖在兜口停了一下。外面那盏灯还亮着，他没回头看。')]
        out = ['# 第 01 章 改写方案', '',
               '> 依据：`check_human_rhythm.py` 报告（2026-10-09）｜轮次：1', '',
               _PLAN_HEAD, '|---|---|---|---|---|---|']
        for i, (_b, _a) in enumerate(entries, 1):
            out.append(f'| {i} | 平均句长 21（<23） | {unit} | 第 3 段 '
                       f'| 动作后补一句环境／身体感受 | 句长↑ / p90↑ |')
        out += ['', '---', '']
        for i, (_b, _a) in enumerate(entries, 1):
            out += [f'### #{i}', f'- 改前：{_b}', f'- 改后：{_a}']
            if why:
                out.append('- 为什么更好：U1 插入——原句之后补进"身体感受 + 周围环境"，'
                           '句长 9 → 41 字，位置未变。')
            out.append('')
        return '\n'.join(out) + '\n'

    def _workorder_text(retry=0):
        """`06-章节工单.md` fixture —— 列名与 `make_workorder.COLS` 逐字一致。"""
        return ('# 章节工单（《测试》）—— 机器写的度量\n\n'
                '> 由 `scripts/make_workorder.py` 追加。\n\n'
                '| 章节 | 字数 | 机械结论 | 缺陷类型 | 返工轮次 | 备注 |\n'
                '|---|---|---|---|---|---|\n'
                f'| 第1章 | 3000 | 2 项 | 节律、词汇 | {retry} | |\n')

    def _mk_rewrite_proj(base, *, plan='ok', workorder=0, **plan_kw):
        """在 `_mk_concrete_proj` 之上加「改写方案表 + 章节工单」两个 fixture。

        ⚠️ **其余必须全绿**（control 得 exit 0）——否则"我以为抓到了方案表，
        其实抓到的是别的东西"。（与用例 78 同款对照设计。）
        `plan=None` → **不产出**方案表；`workorder=None` → **不产出**工单。
        """
        _mk_concrete_proj(base, True, plot='pad2')
        if plan is not None:
            _d = base / 'chapters' / '_meta'
            _d.mkdir(parents=True, exist_ok=True)
            (_d / '第01章-改写方案.md').write_text(_plan_text(**plan_kw), encoding='utf-8')
        if workorder is not None:
            (base / '06-章节工单.md').write_text(_workorder_text(workorder),
                                                 encoding='utf-8')
        return base

    print('86. **改写方案表缺失 / 字段缺失 → 软提示 + 硬拦**（两层，性质不同）')
    print('   （v7.2.0 之前"出了方案表"和"没出"过闸门的结果完全一样 ——')
    print('     而这是"生成点有了、交付点有了、校验点没有"的**第 4 次**。）')
    print('   ⚠️ **两层刻意分开**：')
    print('     · **硬层** = 方案表**存在**时校验格式（文件/字段在不在、改前≠改后）；')
    print('     · **软层** = **返工过却没有方案表** → 只**提示**、**不阻塞**。')
    print('       为什么不阻塞：触发源 `06-章节工单.md` 的「返工轮次」由 `--retry` 写入，')
    print('       **默认值是 0 —— "没填"与"真的是 0"长得一样** → 这个源**有漏报**。')
    print('       **有漏报的判据不许当硬闸门**（那就是造假闸门）；它只配做提示。')
    _p86a = _mk_rewrite_proj(_tmp / '_guard_plan_ok', plan='ok', workorder=0)
    _rc86a, _o86a = _run_gate(_p86a)
    # 注入 A：工单记着返工过（轮次 1），但**没有**方案表 → 软提示（不阻塞）
    _p86b = _mk_rewrite_proj(_tmp / '_guard_plan_none', plan=None, workorder=1)
    _rb86, _ob86 = _run_gate(_p86b)
    # 注入 B：方案表**存在**但删掉「为什么更好：」→ 硬拦，且报的是**缺字段**
    #         （**这正好反证解析器认得出这个文件** —— 不是"没找到文件"）
    _p86c = _mk_rewrite_proj(_tmp / '_guard_plan_nowhy', plan='nowhy', why=False)
    _rc86c, _oc86c = _run_gate(_p86c)
    print(f'       control（方案表完整·工单无返工）exit={_rc86a}（应 0）｜'
          f'注入A（无方案表·返工过）exit={_rb86}（**应 0 —— 软层不该阻塞**）｜'
          f'注入B（有表缺字段）exit={_rc86c}（应 1）')
    # ⚠️ 断言用**专用标记**，不用"改写方案表"这种也会出现在"补法提示"里的词 ——
    #    本项目已有两次"断言命中无关文本"的假通过。
    _ok86 = (_rc86a == 0 and _rb86 == 0 and _rc86c == 1
             and ('没有改写方案表' in _ob86)          # 软提示真的念出来了
             and ('【改写方案表不合规】' in _oc86c)   # 硬层真的拦下了
             and ('缺「为什么更好：」' in _oc86c))     # 报"缺字段"，不是"没有文件"
    print(f'  {"✓" if _ok86 else "✗"} 方案表两层  →  '
          f'{"抓到（软提示 + 硬拦 + 缺字段反证）" if _ok86 else "**没抓到！**"}')
    if not _ok86:
        print('       注入B 输出尾部：', _oc86c[-300:].replace('\n', ' / ')[:220])
        print('       注入A 输出尾部：', _ob86[-300:].replace('\n', ' / ')[:220])
    results.append(_ok86)

    print('87. ★ **「改前 = 改后」必须拦下**（防空改 —— 本轮最重要的一条）')
    print('   （三行对照最容易退化成**形式主义**：把 `改前：` 抄一遍写进 `改后：`。')
    print('     "改完了"其实是"抄了一遍"，这是本轮最可能发生的假工作。')
    print('   ⚠️ 归一化口径（这就是判据本身，别放宽）：**只保留汉字/字母/数字**，')
    print('     空白、标点、markdown 装饰**全部丢掉再比** —— 所以')
    print('     **"只改标点/只加空格"也算空改**（`。` → `，` 不是内容上的改动）。')
    print('     反过来，只要真换过词，归一化后必然不同 → 不会误拦。）')
    _p87a = _mk_rewrite_proj(_tmp / '_guard_plan_ok2', plan='ok', workorder=0)
    _rc87a, _o87a = _run_gate(_p87a)
    # 注入 A：改后**逐字抄**改前
    _p87b = _mk_rewrite_proj(_tmp / '_guard_plan_copy', plan='copy', workorder=0,
                            entries=[('他把火柴塞进兜里。', '他把火柴塞进兜里。')])
    _rb87, _ob87 = _run_gate(_p87b)
    # 注入 B：只改标点（`。` → `，`）—— 同样算空改
    _p87c = _mk_rewrite_proj(_tmp / '_guard_plan_punct', plan='punct2', workorder=0,
                            entries=[('他把火柴塞进兜里。', '他把火柴，塞进兜里。')])
    _rc87c, _oc87 = _run_gate(_p87c)
    print(f'       control（真实改写）exit={_rc87a}（应 0）｜'
          f'注入A（逐字抄）exit={_rb87}（应 1）｜'
          f'注入B（只改标点）exit={_rc87c}（应 1）')
    _ok87 = (_rc87a == 0 and _rb87 == 1 and _rc87c == 1
             and ('【改写方案表不合规】' in _ob87)
             and ('归一化后完全相同' in _ob87)
             and ('归一化后完全相同' in _oc87))
    print(f'  {"✓" if _ok87 else "✗"} 空改被拦下  →  '
          f'{"抓到（抄一遍 + 只改标点，都拦）" if _ok87 else "**没抓到！形式主义溜过去了**"}')
    if not _ok87:
        print('       注入A 输出尾部：', _ob87[-300:].replace('\n', ' / ')[:220])
        print('       注入B 输出尾部：', _oc87[-300:].replace('\n', ' / ')[:220])
    results.append(_ok87)

    print('88. **单元编号不是摆设**：`单元` 列里出现的编号必须落在 U1–U6 之内')
    print('   （★ **只查这一件事** —— "这个单元选得对不对"是理解级判断，机器判不了，')
    print('     交质检子代理。这一条只钉住"编号来自契约"，防止 U7/UX 这类**不存在的')
    print('     单元**流进流程（一旦流进去，下游按它去翻 rewrite-units.md 就翻不到）。')
    print('   ⚠️ control 用 `U5/U6` 复合写法：**同一含义的多种写法都要认** ——')
    print('     "只认单个 U1"的窄锚点会把合规表判死。')
    _p88a = _mk_rewrite_proj(_tmp / '_guard_unit_ok', plan='ok', workorder=0, unit='U5/U6')
    _rc88a, _o88a = _run_gate(_p88a)
    _p88b = _mk_rewrite_proj(_tmp / '_guard_unit_u7', plan='u7', workorder=0, unit='U7')
    _rb88, _ob88 = _run_gate(_p88b)
    _p88c = _mk_rewrite_proj(_tmp / '_guard_unit_ux', plan='ux', workorder=0, unit='UX')
    _rc88c, _oc88 = _run_gate(_p88c)
    print(f'       control（`U5/U6` 复合写法）exit={_rc88a}（应 0）｜'
          f'注入A（`U7`）exit={_rb88}（应 1）｜注入B（`UX`）exit={_rc88c}（应 1）')
    _ok88 = (_rc88a == 0 and _rb88 == 1 and _rc88c == 1
             and ('未知单元' in _ob88) and ('U7' in _ob88)
             and ('未知单元' in _oc88) and ('UX' in _oc88))
    print(f'  {"✓" if _ok88 else "✗"} 未知单元被拦  →  '
          f'{"抓到（U7 / UX 都拦，U5/U6 放行）" if _ok88 else "**没抓到！**"}')
    if not _ok88:
        print('       注入A 输出尾部：', _ob88[-300:].replace('\n', ' / ')[:220])
    results.append(_ok88)

    for _d in (_p18, _p19, _p21, _p78, _p78b, _p79, _p80, _p80b, _p80c, _p81, _p82,
               _p86a, _p86b, _p86c, _p87a, _p87b, _p87c, _p88a, _p88b, _p88c):
        try:
            _shx.rmtree(_d)
        except Exception:
            pass

    restore_scripts()
    tail = run_audit()
    ok_restore = '全部通过' in tail
    # ★ 确认全绿 = 树干净 → 清空注入日志。不清的话，个别未配对的登记会让日志常驻，
    #   下一次自愈就会去回滚一些**根本没在被注入状态**的文件（用新坑换旧坑）。
    if ok_restore:
        _journal_clear()
    print()
    print('=' * 74)
    print(f'还原后：{"✓ 重新全绿" if ok_restore else "✗ 还原不干净！（内存备份还原失败，请手工检查改动过的文件）"}')
    print(f'注入用例：{sum(results)}/{len(results)} 被抓到')
    if not ok_restore:
        print('提示：若你刚才用 `head`/`tail` 截断过输出，进程可能被 SIGPIPE 杀掉 →'
              ' 直接重跑一次本脚本即可自愈（它会回放注入日志）；'
              '也可 `grep -rn "QQQ" --include=*.md --include=*.py .` 手工查残留。')
    print('=' * 74)
    return 0 if (all(results) and ok_restore) else 1


if __name__ == '__main__':
    sys.exit(main())
