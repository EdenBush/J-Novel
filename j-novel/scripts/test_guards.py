# -*- coding: utf-8 -*-
"""守卫故障注入测试：验证 `audit_release.py` 的检查项**真的会响**。

用法:
    python scripts/test_guards.py

## 为什么要这个文件

> **"加了守卫"和"守卫有效"是两件事。**

2026-09-14 的教训：一个团队在 v4.5 明确写下"假闸门（看似做了检查、实际从未触发）
是最危险的一类失败"，v4.6 还是在新位置又犯了三次（声明比实现走得快）。
光靠人读代码发现不了——**唯一的办法是故意把代码改坏，看它是否报警**。

这个脚本把那次人工做的 8 个注入用例固化下来。**改完任何检查项后跑一次**，
把"我以为守卫有效"变成"我验证过守卫有效"。

## 它怎么工作

对每个用例：备份 → 注入故障 → 跑 `audit_release.py` → 检查是否报出预期的关键字 →
**无论成败都还原** → 最后确认还原后重新全绿（防止把技能改坏）。

## 实测记录（2026-09-14，8/8）

本脚本的**前身**在人工跑时抓到过 4 个"守卫自身失效"：
  1. 守卫用 `in src` 判断 → 被注释掉的 `# from _shared import` 骗过
  2. 守卫扫原文 → continuity 的 **docstring** 里写着 `_shared_extract_body`，委托检测照样通过
  3. 调用计数把**注释**里 `` `grep -c '_all_docs('` `` 也算进去 → 定义未调用检测失效
  4. 数字漂移正则不认 markdown 强调 → 文档写 `**14 个**已废弃说法` 时**完全匹配不上**
"""
import io
import shutil
import subprocess
import sys
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SKILL = Path(__file__).resolve().parent.parent
PY = sys.executable
BAK = SKILL / '_guard_bak'


def run_audit():
    """跑一次 QA，返回 stdout。"""
    r = subprocess.run([PY, '-X', 'utf8', str(SKILL / 'scripts' / 'audit_release.py')],
                       cwd=str(SKILL), capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return r.stdout or ''


def backup_scripts():
    if BAK.exists():
        shutil.rmtree(BAK)
    BAK.mkdir(parents=True)
    for f in (SKILL / 'scripts').glob('*.py'):
        if f.name == Path(__file__).name:      # 不备份自己（防递归）
            continue
        shutil.copy2(f, BAK / f.name)


def restore_scripts():
    for f in BAK.glob('*.py'):
        shutil.copy2(f, SKILL / 'scripts' / f.name)


def inject_case(name, fname, mutate, expect_kw):
    """注入 → 跑 → 判定 → 还原。返回是否被抓到。"""
    p = SKILL / 'scripts' / fname
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
        if BAK.exists():
            shutil.rmtree(BAK)
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

    restore_scripts()
    tail = run_audit()
    ok_restore = '全部通过' in tail
    print()
    print('=' * 74)
    print(f'还原后：{"✓ 重新全绿" if ok_restore else "✗ 还原不干净！（请检查 _guard_bak/）"}')
    print(f'注入用例：{sum(results)}/{len(results)} 被抓到')
    print('=' * 74)
    if BAK.exists():
        shutil.rmtree(BAK)
    return 0 if (all(results) and ok_restore) else 1


if __name__ == '__main__':
    sys.exit(main())
