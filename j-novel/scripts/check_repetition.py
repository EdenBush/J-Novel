#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节重复检测脚本
检测章节内的重复段落（整段复制粘贴是 AI 生成的典型硬伤）。
- 精确重复：归一化后完全相同的段落（长度 > 20 字才报告，避免短句误报）
- 近似重复：相似度 ≥ 0.9 的段落对

用法:
  python check_repetition.py <章节文件.md>        # 检测单章
  python check_repetition.py --all <项目目录>     # 检测全部章节
退出码: 0 = 无重复, 1 = 发现重复
"""

import argparse
import io
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

MIN_DUP_LEN = 15       # 段落归一化后至少 15 字才参与重复判定（避免"他说。"类短句误报）
SIM_THRESHOLD = 0.9    # 近似重复相似度阈值


def extract_body(text: str) -> str:
    """提取正文（去掉标题/概要/备注/质检等元数据块）"""
    lines = text.split('\n')
    out, skip_block, skip_qc = [], False, False
    for raw in lines:
        line = raw.strip()
        if line.startswith('【本章质检摘要】'):
            skip_qc = True
            continue
        if skip_qc:
            if line == '---':
                skip_qc = False
            continue
        if any(line.startswith(b) for b in ('## 本章概要', '## 章节备注', '## 章节概要')):
            skip_block = True
            continue
        if skip_block:
            if line.startswith('#') or line == '---':
                skip_block = False
            if skip_block:
                continue
        if line.startswith('## '):      # 章首引子/正文等结构标题
            continue
        if line == '---':
            continue
        out.append(line)
    return '\n'.join(out)


def normalize(s: str) -> str:
    """归一化：去空白和标点，只留汉字/字母/数字，便于比较"""
    return re.sub(r'[\s\u3000\W_]+', '', s)


def split_paragraphs(body: str) -> list:
    """按空行切分段落，返回 [(原段落, 归一化文本)]"""
    paras = []
    for block in re.split(r'\n\s*\n', body):
        block = block.strip()
        norm = normalize(block)
        if len(norm) >= MIN_DUP_LEN:
            paras.append((block, norm))
    return paras


def find_duplicates(file_path: Path) -> list:
    """检测单章重复，返回 [(段A原文, 段B原文, 类型)]"""
    try:
        text = file_path.read_text(encoding='utf-8')
    except Exception as e:
        print(f'[错误] 读取失败 {file_path.name}: {e}')
        return []

    body = extract_body(text)
    paras = split_paragraphs(body)
    dupes = []
    n = len(paras)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = paras[i][1], paras[j][1]
            if a == b:
                dupes.append((paras[i][0], paras[j][0], '精确重复'))
            elif len(a) > 30 and len(b) > 30:
                r = SequenceMatcher(None, a, b).ratio()
                if r >= SIM_THRESHOLD:
                    dupes.append((paras[i][0], paras[j][0], f'近似重复 {r:.0%}'))
    return dupes


def check_file(file_path: Path) -> int:
    dupes = find_duplicates(file_path)
    if not dupes:
        print(f'[通过] {file_path.name}：无重复段落')
        return 0
    print(f'[失败] {file_path.name}：发现 {len(dupes)} 处重复')
    for k, (a, b, kind) in enumerate(dupes, 1):
        print(f'  ── 第 {k} 处（{kind}）──')
        print(f'  A：{a[:60]}{"…" if len(a) > 60 else ""}')
        print(f'  B：{b[:60]}{"…" if len(b) > 60 else ""}')
    return 1


def main():
    parser = argparse.ArgumentParser(description='章节重复检测（AI 硬伤扫描）')
    parser.add_argument('path', help='章节 .md 文件，或 --all 时的项目目录')
    parser.add_argument('--all', action='store_true', help='检测目录下所有章节')
    args = parser.parse_args()

    if args.all:
        project = Path(args.path)
        if not project.is_dir():
            print(f'[错误] 目录不存在：{args.path}')
            sys.exit(1)
        files = sorted(project.glob('第*.md'), key=lambda p: p.name)
        if not files:
            print('[错误] 未找到章节文件（第*.md）')
            sys.exit(1)
        total_dupes = 0
        for f in files:
            total_dupes += check_file(f)
        print(f'\n===== 汇总：{len(files)} 章，{total_dupes} 章含重复 =====')
        sys.exit(1 if total_dupes > 0 else 0)
    else:
        f = Path(args.path)
        if not f.exists():
            print(f'[错误] 文件不存在：{args.path}')
            sys.exit(1)
        sys.exit(check_file(f))


if __name__ == '__main__':
    main()
