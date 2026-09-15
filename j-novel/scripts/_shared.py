# -*- coding: utf-8 -*-
"""三个质检脚本共用的两件事：**读文件** 与 **取正文**。

## 一、read_text —— 编码探测（2026-09-14 修）

**旧实现的 bug**：按「各编码解出来的中文占比最高」选编码。
但 **utf-16 能把任意偶数长度字节流解成中文乱码**，而 UTF-8 的 3 字节/字比 GBK 的
2 字节/字的"每字节中文数"更低 —— 于是**ASCII 偏多的 UTF-8 文件会被误判成 utf-16**。

实测（400 个真实 UTF-8 文件）：**16 个（4%）会被选错编码**，几乎全是误判成 utf-16；
限制到 novel-output 目录内是 **3/350（0.9%）**，都是短小、ASCII 偏多的 `04-质检档案.md`。

后果是**静默失败**：文件被解成乱码 → 角色名匹配不上、指标全是垃圾 → 检查"全部通过"。

**新规则（顺序不可换）**：
1. 有 BOM → 按 BOM 定（utf-8-sig / utf-16 / utf-32）
2. **严格 utf-8 能干净解码 → 就用它**（现代项目 99% 是这种情况）
3. 否则在遗留编码里按中文占比选（gb18030 / gbk / big5）
4. **绝不无 BOM 猜 utf-16**——它没有可判别特征，只能靠 BOM

## 二、extract_body —— 正文提取

**旧的 bug**：三个脚本各写一份，口径互不相同。实测同一章
（`玫瑰下的狩猎/第01章`，全文 3372 字）三个分母分别是 **3351 / 3074 / 2976**，
最大差 **12.6%** —— 所有"每千字"密度指标互相矛盾。

**统一口径**：剥掉标题行 / `## 本章概要`等整块 / `【本章质检摘要】`整块 / 分隔线 /
章节号行 / 面板行；**保留 `## 章首引子` 的内容**（它是读者看得到的散文）。

> ⚠️ 改动本文件会改变所有密度指标的分母。改完必须重跑
> `python scripts/audit_release.py --regress`，确认「人类 3/3 exit=0、AI 3/3 exit=1」不退化。
"""
import re

_CJK = re.compile(r'[\u4e00-\u9fff]')
_LEGACY_ENCODINGS = ('gb18030', 'gbk', 'big5')


def read_text(path) -> str:
    """按「BOM → 严格 utf-8 → 遗留编码(按中文占比)」读文件。"""
    from pathlib import Path
    raw = Path(path).read_bytes()
    if not raw:
        return ''

    # 1) BOM 优先（这是唯一能可靠识别 utf-16/32 的方式）
    for bom, enc in ((b'\xef\xbb\xbf', 'utf-8-sig'),
                     (b'\xff\xfe\x00\x00', 'utf-32'), (b'\x00\x00\xfe\xff', 'utf-32'),
                     (b'\xff\xfe', 'utf-16'), (b'\xfe\xff', 'utf-16')):
        if raw.startswith(bom):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                break

    # 2) 严格 utf-8 干净解码 → 直接用（不做占比比较！）
    try:
        return raw.decode('utf-8')
    except (UnicodeDecodeError, LookupError):
        pass

    # 3) 遗留编码：这时才按中文占比选
    best, best_ratio = None, -1.0
    for enc in _LEGACY_ENCODINGS:
        try:
            s = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        ratio = len(_CJK.findall(s)) / max(1, len(s))
        if ratio > best_ratio:
            best, best_ratio = s, ratio
    if best is not None:
        return best

    # 4) 全失败：宽容解一次，至少别崩
    return raw.decode('utf-8', errors='replace')


# ──────────────────────────────────────────────────────────────
# 正文提取
# ──────────────────────────────────────────────────────────────

_META_BLOCK = re.compile(
    r'^#{1,3}\s*(本章概要|章节概要|本章梗概|章节备注|本章备注|伏笔标记|本章伏笔|本章伏笔操作|'
    r'AI\s*味自评|人味配额|叙述者插话|叙述者表态|质量评分|衔接检查|逻辑检查|专项检查|'
    r'注册审计|本章质检摘要|成本记录)\s*[:：]?\s*$')
_ANY_HEAD = re.compile(r'^#{1,6}\s')
_SEP = re.compile(r'^[=\-—_*·]{3,}$')
_CHAPTER_LINE = re.compile(r'^第.{0,8}章.{0,24}$')
_BRACKET_META = re.compile(r'^[\[【](本章质检摘要|本章概要|章节概要|章节备注)[\]】]\s*$')
_PANEL = [
    re.compile(r'^\s*\d{2,4}\s+\S.{0,24}$'),        # "094 求援"
    re.compile(r'^\s*p[sS]?[:：]'),                  # 作者求票/感谢
    re.compile(r'^\s*[\[【].{0,80}[\]】]\s*$'),      # 系统面板整行
    re.compile(r'^\s*[\d\W_]{1,10}$'),               # 纯数字/符号行
    re.compile(r'^\s*(作品相关|内容简介|后记|番外)\s*$'),
]


def extract_body(text: str) -> str:
    """从章节文件里取出「正文」——三个脚本必须都走这里。"""
    out = []
    skip_meta = False
    for raw in text.split('\n'):
        line = raw.strip()

        if _META_BLOCK.match(line) or _BRACKET_META.match(line) or line.startswith('【本章质检摘要】'):
            skip_meta = True
            continue
        if skip_meta:
            if _ANY_HEAD.match(line) or line == '---':
                skip_meta = False
            else:
                continue

        # 标题行只跳这一行，内容保留（`## 章首引子` / `## 正文` 的内容由此保住）
        if _ANY_HEAD.match(line):
            continue
        if line == '---' or _SEP.match(line):
            continue
        if _CHAPTER_LINE.match(line):
            continue
        if any(p.match(line) for p in _PANEL):
            continue
        if not line:
            continue
        out.append(line)
    return '\n'.join(out)


# ──────────────────────────────────────────────────────────────
# 明喻计数
# ──────────────────────────────────────────────────────────────
# ⚠️ 2026-09-14 收归共享。此前 check_human_rhythm 与 check_aistyle **各写一份**，
# 而且 aistyle 那份是**死代码**：它定义了带负向断言的 count_similes，
# 实际算密度时却用 `sum(count_word(body, w) for w in SIMILE_WORDS)` ——
# 于是「像」仍是裸匹配，负向断言的修复**根本没生效**（图像/偶像/雕像/对象 全被算成明喻）。
# 这是同一类"声明比实现走得快"，现在两份合一份，并由自检盯着。
SIMILE_WORDS = ['像', '如同', '宛如', '犹如', '好似', '仿佛是']

# 「像」不能裸匹配：会误命中 图像/影像/偶像/摄像/雕像/对象（都不是明喻）。
#   但「好像 / 不像 / 像极了」按 SKILL 定义**要**计数，所以只排除上面那几个字。
_SIMILE_LIKE_RX = re.compile(r'(?<![图影偶摄雕对])像')


def count_similes(flat: str) -> int:
    """明喻计数：其它词用 str.count，「像」用带负向断言的正则。"""
    return sum(flat.count(w) for w in SIMILE_WORDS if w != '像') + len(_SIMILE_LIKE_RX.findall(flat))


if __name__ == '__main__':
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    from pathlib import Path
    for p in sys.argv[1:]:
        t = read_text(p)
        b = extract_body(t)
        print('%-40s 全文 %5d 字 → 正文 %5d 字' % (Path(p).name[:38], len(_CJK.findall(t)), len(_CJK.findall(b))))