#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节 MD → TXT 转换脚本（单章 + 合订本）
转换时删除所有概要/元数据内容，只保留纯正文：
- 删除「## 本章概要」块（核心事件/承接上章/悬念钩子）
- 删除「## 章节备注」块（本章悬念/下章预告/伏笔标记）
- 删除【本章质检摘要】块（兼容旧版章节文件）
- 删除 Markdown 标记（#、**、`、链接语法等）
保留：章节标题、章首引子文字、正文文字。

用法:
  python convert_to_txt.py <章节文件.md>                     # 单章 → 同名 .txt
  python convert_to_txt.py --all <项目目录>                  # 全部章节 → 单章 txt + 合订本.txt
  python convert_to_txt.py --all <项目目录> --out <输出目录>   # 指定输出目录
  python convert_to_txt.py --all <项目目录> --book <合订本名>  # 自定义合订本文件名
"""

import argparse
import io
import re
import sys
from pathlib import Path

# 修复 Windows 控制台编码问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 删除区块的起始标记（标题行本身连同内容一起删除）
DEL_BLOCK_STARTS = ('## 本章概要', '## 章节备注', '## 章节概要')
# 质检摘要起始标记
QC_START = '【本章质检摘要】'
# 保留区块标题（标题行删除，但内容保留）
KEEP_BLOCK_TITLES = ('## 章首引子', '## 正文', '## 引子')


def strip_markdown(line: str) -> str:
    """去除行内 Markdown 标记，返回纯文本"""
    line = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', line)  # 链接 [t](url) -> t
    line = re.sub(r'\*\*(.*?)\*\*', r'\1', line)  # 粗体
    line = re.sub(r'\*(.*?)\*', r'\1', line)  # 斜体
    line = re.sub(r'`(.*?)`', r'\1', line)  # 行内代码
    line = re.sub(r'~~(.*?)~~', r'\1', line)  # 删除线
    line = re.sub(r'^\s*[#>]+\s*', '', line)  # 行首 # 或 > 标记
    line = re.sub(r'^\s*[-*+]\s+', '', line)  # 行首列表标记
    return line.strip()


def convert_chapter(file_path: Path) -> str:
    """把单章 MD 转为纯正文文本。返回空字符串表示无可转换内容。"""
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.read().split('\n')

    out_lines = []
    skip_block = False      # 正在跳过删除区块（概要/备注）
    skip_qc = False         # 正在跳过质检摘要
    seen_content = False    # 是否已产生任何内容（用于合订本过滤无正文文件）

    for raw in lines:
        line = raw.strip()

        # 质检摘要：从标记开始跳到下一个 --- 或文件结束
        if line.startswith(QC_START):
            skip_qc = True
            continue
        if skip_qc:
            if line == '---' or line == '':
                skip_qc = False
            continue

        # 删除区块开始（概要/备注）
        if any(line.startswith(b) for b in DEL_BLOCK_STARTS):
            skip_block = True
            continue
        if skip_block:
            # 遇到新标题、分隔线或质检标记 → 结束跳过
            if line.startswith('#') or line == '---' or line.startswith(QC_START) or line == '':
                # 空行不算结束，继续跳过（避免概要内空行提前终止）
                if line != '':
                    skip_block = False
            if skip_block:
                continue

        # 分隔线 → 跳过（不留）
        if line == '---':
            continue

        # 章节标题：去掉 # 标记，作为 txt 标题行
        if line.startswith('# ') and '章' in line:
            title = strip_markdown(line)
            if title:
                out_lines.append(title)
                out_lines.append('')
            continue

        # 保留区块的标题行（章首引子/正文）→ 标题本身删除，内容保留
        if any(line.startswith(b) for b in KEEP_BLOCK_TITLES):
            continue

        # 其他内容行：去掉 Markdown 标记
        cleaned = strip_markdown(line)
        if cleaned:
            out_lines.append(cleaned)
            seen_content = True
        else:
            # 空行保留用于段落分隔（但避免连续空行）
            if out_lines and out_lines[-1] != '':
                out_lines.append('')

    # 清理尾部多余空行
    while out_lines and out_lines[-1] == '':
        out_lines.pop()

    if not seen_content:
        return ''

    return '\n'.join(out_lines) + '\n'


def chapter_number(name: str) -> int:
    """从文件名提取章节号（用于排序）"""
    m = re.search(r'第\s*(\d+)\s*章', name)
    return int(m.group(1)) if m else 10 ** 9


def convert_single(file_path: Path, out_dir: Path = None) -> Path:
    """转换单章，输出 .txt"""
    text = convert_chapter(file_path)
    if not text:
        print(f'[跳过] {file_path.name}：无可转换的正文内容')
        return None
    out_dir = out_dir or file_path.parent
    out_path = out_dir / (file_path.stem + '.txt')
    out_path.write_text(text, encoding='utf-8')
    print(f'[完成] {file_path.name} → {out_path.name}（{len(text.splitlines())} 行）')
    return out_path


def convert_all(project_dir: Path, out_dir: Path, book_name: str):
    """转换全部章节 + 生成合订本"""
    chapter_files = [p for p in project_dir.glob('第*.md')]
    chapter_files.sort(key=lambda p: chapter_number(p.name))

    if not chapter_files:
        print(f'[错误] 目录中未找到章节文件（第*.md）：{project_dir}')
        return

    print(f'找到 {len(chapter_files)} 章，开始转换...\n')
    book_parts = []
    for f in chapter_files:
        out_path = convert_single(f, out_dir)
        if out_path:
            text = out_path.read_text(encoding='utf-8')
            book_parts.append((f.name, text))

    if book_parts:
        # 合订本：章间用分页符分隔
        book = ''
        for name, text in book_parts:
            if book:
                book += '\n\f\n'  # 分页符
            book += text
        book_path = out_dir / (book_name + '.txt')
        book_path.write_text(book, encoding='utf-8')
        print(f'\n[合订本] {book_path.name}（{len(book_parts)} 章，共 {len(book.splitlines())} 行）')


def main():
    parser = argparse.ArgumentParser(description='章节 MD → TXT 转换（剥离概要/备注/质检摘要）')
    parser.add_argument('path', help='章节 .md 文件，或 --all 时的项目目录')
    parser.add_argument('--all', action='store_true', help='转换目录下所有章节并生成合订本')
    parser.add_argument('--out', default=None, help='输出目录（默认与源文件同目录）')
    parser.add_argument('--book', default='合订本', help='合订本文件名（不含 .txt）')
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    if args.all:
        project = Path(args.path)
        if not project.is_dir():
            print(f'[错误] 目录不存在：{args.path}')
            return
        out_dir = out_dir or project
        convert_all(project, out_dir, args.book)
    else:
        f = Path(args.path)
        if not f.exists():
            print(f'[错误] 文件不存在：{args.path}')
            return
        out_dir = out_dir or f.parent
        convert_single(f, out_dir)


if __name__ == '__main__':
    main()
