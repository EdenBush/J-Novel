#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节字数检查脚本（融合版移植自 Chinese Novelist）
检查指定章节文件的字数，低于目标字数时提示需要扩充。
用法:
  python check_chapter_wordcount.py <章节文件路径> [最小字数]
  python check_chapter_wordcount.py --all <项目目录> [最小字数]
"""

import io
import re
import sys
from pathlib import Path

# 修复 Windows 控制台编码问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def count_chinese_words(text: str) -> int:
    """统计中文字数（排除标点符号和Markdown标记）"""
    # 移除Markdown标记
    text = re.sub(r'#{1,6}\s*', '', text)  # 标题
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # 粗体
    text = re.sub(r'\*(.*?)\*', r'\1', text)  # 斜体
    text = re.sub(r'~~(.*?)~~', r'\1', text)  # 删除线
    text = re.sub(r'`(.*?)`', r'\1', text)  # 行内代码
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)  # 链接

    # 统计中文字符（汉字）
    chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
    return len(chinese_chars)


def extract_content_from_chapter(file_path):
    """取章节正文。**读文件与取正文都必须走 _shared**（2026-09-14 重构）。

    此前本脚本自己剥 本章概要/章首引子/章节备注 三块，而另外两个脚本各有各的剥法；
    实测同一章分母 2976 vs 3074 vs 3351 字 → 密度指标互相矛盾。

    ⚠️ 2026-09-14 二次修：上一版只把 `extract_body` 统一了，**读文件仍是硬编码
    `read_text(encoding="utf-8", errors="replace")`** —— 文件若是 GBK/GB18030，
    会解出一堆 U+FFFD 再被 `errors="replace"` 静默吞掉，中文字数趋近 0，
    于是**"编码读错"被报成"字数不足"**（比崩溃更危险：用户会去补写正文）。
    实测同一段内容：UTF-8 报 1000 字，GBK 报 40 字（少算 96%）。
    现改为 `_shared.read_text`（BOM → 严格 utf-8 → 遗留编码），并纳入自检名单。
    """
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from _shared import extract_body as _shared_extract_body
    from _shared import read_text as _shared_read_text
    return _shared_extract_body(_shared_read_text(file_path))


def check_chapter(file_path: str, min_words: int = 3000) -> dict:
    """检查单个章节的字数"""
    path = Path(file_path)

    if not path.exists():
        return {
            'file': str(path),
            'exists': False,
            'word_count': 0,
            'status': 'error',
            'message': f'文件不存在: {file_path}'
        }

    main_content = extract_content_from_chapter(path)
    word_count = count_chinese_words(main_content)

    status = 'pass' if word_count >= min_words else 'fail'
    message = f'字数: {word_count}' + (
        f' (PASS, 达标)' if word_count >= min_words else f' (FAIL, 不足, 需要至少 {min_words} 字)'
    )

    return {
        'file': str(path),
        'exists': True,
        'word_count': word_count,
        'status': status,
        'message': message
    }


def check_all_chapters(directory: str, pattern: str = '第*.md', min_words: int = None) -> list:
    """检查目录下所有章节文件。

    ⚠️ 2026-09-19 修：此前是 `dir_path.glob('第*.md')` —— **只在项目根扫**，
    而规范要求正文放 `chapters/` → 对**完全合规**的项目一个文件都找不到，
    打印"没有找到章节文件"后 **`return 0`（假绿）**。
    Phase 4 唯一那句字数检查命令因此静默失效（且这是同类事故的第三次复发）。

    现在走 `_shared.find_chapter_files`（chapters/ → 正文/ → 根 → 递归兜底）。
    """
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from _shared import find_chapter_files

    dir_path = Path(directory)
    if not dir_path.exists():
        print(f'错误: 目录不存在 - {directory}')
        return None                      # None = 基础设施错误，不是"0 章通过"

    if min_words is None:
        from _shared import read_min_words
        min_words = read_min_words(dir_path)

    chapter_files = find_chapter_files(dir_path)
    if not chapter_files and pattern != '第*.md':
        chapter_files = sorted(dir_path.glob(pattern))

    return [check_chapter(str(f), min_words) for f in chapter_files]


def print_results(results: list, min_words: int = 3000) -> int:
    """打印检查结果。返回**未达标章数**；`-1` = 没扫到任何章节（fail-closed）。"""
    if not results:
        print('没有找到章节文件')
        print('  ⚠ 这不等于"通过"——说明脚本没看见你的稿子。')
        print('  → 章节正文应放在 `chapters/第NN章-标题.md`（见 phase2-planning.md「项目结构」）。')
        print('  → 先修结构再重跑，不要当成"没有待办"。')
        return -1

    total_words = 0
    passed = 0
    failed = 0

    print('\n' + '=' * 60)
    print('章节字数检查报告')
    print('=' * 60)

    for result in results:
        if not result['exists']:
            print(f'\n[ERROR] {result["file"]}')
            print(f'   {result["message"]}')
            failed += 1
            continue

        total_words += result['word_count']
        if result['status'] == 'pass':
            passed += 1
            icon = '[PASS]'
        else:
            failed += 1
            icon = '[FAIL]'

        print(f'\n{icon} {Path(result["file"]).name}')
        print(f'   {result["message"]}')

    print('\n' + '-' * 60)
    print(f'总计: {len(results)} 章 | {passed} 章达标 | {failed} 章不足 | 总字数: {total_words:,}')
    print(f'（下限 {min_words} 字/章，取自 02-写作计划.json 的 minWordsPerChapter）')
    print('-' * 60)

    if failed > 0:
        print(f'\n[警告] 有 {failed} 章内容不足 {min_words} 字，建议使用扩充技巧:')
        print('   - 场景肌理充实（感官/空间/情绪三层）')
        print('   - 关键时刻放慢（动作分解、思维展开）')
        print('   - 内心世界展开（思维链/感官触发回忆/内心对话/身体反应）')
        print('   - 对话层次丰富（动作中断/潜台词/话题绕弯/权力博弈）')
        print('   - 次要情节穿插（配角片段/暗线推进/伏笔埋设）')
        print('   - 感官维度叠加')
        print('\n   参考: references/guides/chapter-craft.md')
    return failed


def main():
    """主函数"""
    min_words = None            # None = 自动（命令行 > 项目配置 > 3000）

    if len(sys.argv) < 2:
        print('用法:')
        print('  检查单个章节: python check_chapter_wordcount.py <章节文件路径> [最小字数]')
        print('  检查所有章节: python check_chapter_wordcount.py --all <项目目录> [最小字数]')
        print('')
        print('示例:')
        print('  python check_chapter_wordcount.py novel-output/故事/第01章.md')
        print('  python check_chapter_wordcount.py novel-output/故事/第01章.md 3500')
        print('  python check_chapter_wordcount.py --all novel-output/故事')
        print('  python check_chapter_wordcount.py --all novel-output/故事 3500')
        print('')
        print('退出码: 0=全部达标 / 1=有章节字数不足 / 2=没扫到章节或目录不存在（fail-closed）')
        return 2

    if sys.argv[1] == '--all':
        if len(sys.argv) < 3:
            print('错误: 使用 --all 时需要指定目录路径')
            return 2
        directory = sys.argv[2]
        if len(sys.argv) > 3:
            min_words = int(sys.argv[3])
        results = check_all_chapters(directory, min_words=min_words)
        if results is None:
            return 2
        actual_min = min_words
        if actual_min is None:
            import os as _os, sys as _sys
            _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            from _shared import read_min_words
            actual_min = read_min_words(Path(directory))
        failed = print_results(results, actual_min)
        if failed < 0:
            return 2
        return 1 if failed > 0 else 0

    file_path = sys.argv[1]
    if len(sys.argv) > 2:
        min_words = int(sys.argv[2])
    if min_words is None:
        import os as _os, sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from _shared import read_min_words
        min_words = read_min_words(Path(file_path).parent)
    result = check_chapter(file_path, min_words)
    failed = print_results([result], min_words)
    if failed < 0:
        return 2
    return 1 if failed > 0 else 0


if __name__ == '__main__':
    sys.exit(main())
