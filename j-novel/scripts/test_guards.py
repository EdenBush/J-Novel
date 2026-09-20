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

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

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
    """把待注入文件的原文读进内存。"""
    _ORIG.clear()
    for f in sorted((SKILL / 'scripts').glob('*.py')):
        if f.name == Path(__file__).name:      # 不备份自己（防递归）
            continue
        _ORIG[f] = f.read_text(encoding='utf-8')
    for rel in DOC_TARGETS:
        f = SKILL / rel
        if f.exists():
            _ORIG[f] = f.read_text(encoding='utf-8')


def restore_scripts():
    """从内存还原（幂等：可重复调用）。"""
    for f, txt in _ORIG.items():
        f.write_text(txt, encoding='utf-8')


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


def main():
    print('=' * 74)
    print('守卫故障注入测试（每个用例都会在跑完后自动还原）')
    print('=' * 74)
    backup_scripts()

    base = run_audit()
    if '全部通过' not in base:
        print('  ✗ 基线本来就不绿，先修好再来测守卫：')
        print(base[-1500:])
        restore_scripts()
        return 1
    print('  基线（未注入）：✓ 全绿\n')

    results = []

    print('① 守卫⓪：注释掉共享 import（曾用 `in src` 判断 → 被注释骗过）')
    results.append(inject_case('注释 import', 'check_aistyle.py',
                               lambda s: s.replace('from _shared import (', '# from _shared import (', 1),
                               '没有走 _shared'))

    print('② 守卫①：删掉 continuity 的共享 import')
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
    (_td2 / '05-创作台账.md').write_text('最近重读章号：第1章\n', encoding='utf-8')
    (_td2 / '03-状态台账.md').write_text('第1章\n', encoding='utf-8')
    (_td2 / '01-大纲.md').write_text(
        '| 章节 | 标题 | 开场类型 | 章末型 |\n|---|---|---|---|\n| 第1章 | A | 新起 | 甲·信息结算 |\n',
        encoding='utf-8')
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
        if meta_dir:
            (base / 'chapters' / '_meta').mkdir(exist_ok=True)
            (base / 'chapters' / '_meta' / '第01章-测试.meta.md').write_text(
                '# 元数据\n\n## 本章概要\n这一行不该被任何脚本当成正文。' + filler + '\n',
                encoding='utf-8')
        (base / '00-人物档案.md').write_text(
            '# 人物档案\n\n## 陆铮（主角）· 28 岁 · 男\n性格：沉默。\n', encoding='utf-8')
        (base / '02-写作计划.json').write_text(json.dumps({
            'costMode': 'standard', 'minWordsPerChapter': min_words,
            'chapters': [{'chapterNumber': n, 'status': 'completed', 'wordCountPass': True}
                         for n in (1, 2)]}, ensure_ascii=False), encoding='utf-8')
        (base / '05-创作台账.md').write_text(
            '# 创作台账\n\n最近重读章号：第2章\n' + ledger, encoding='utf-8')
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
        return s.replace(m.group(1), m.group(1).replace('humanize-toolkit.md', 'QQQ.md', 1), 1)
    results.append(inject_case('两套清单读点不同步', 'references/flows/phase3-writing.md',
                               _desync_clists, '不同步'))

    for _d in (_p18, _p19, _p21):
        try:
            _shx.rmtree(_d)
        except Exception:
            pass

    restore_scripts()
    tail = run_audit()
    ok_restore = '全部通过' in tail
    print()
    print('=' * 74)
    print(f'还原后：{"✓ 重新全绿" if ok_restore else "✗ 还原不干净！（内存备份还原失败，请手工检查改动过的文件）"}')
    print(f'注入用例：{sum(results)}/{len(results)} 被抓到')
    print('=' * 74)
    return 0 if (all(results) and ok_restore) else 1


if __name__ == '__main__':
    sys.exit(main())
