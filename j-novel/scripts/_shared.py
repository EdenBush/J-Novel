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
import io
import re
import sys

_CJK = re.compile(r'[\u4e00-\u9fff]')
_LEGACY_ENCODINGS = ('gb18030', 'gbk', 'big5')


def ensure_utf8_stdio():
    """把 stdout/stderr 包成 UTF-8（Windows 控制台默认 GBK，中文会乱码）。

    ⚠️ **必须幂等**，这是踩出来的：此前 12 个脚本各自在**模块级**写
        `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`
    于是**任何一个脚本 import 另一个脚本，就会崩**：

        ValueError: I/O operation on closed file   (lost sys.stderr)

    原因：`sys.stdout` 已经是一个 TextIOWrapper 时，`sys.stdout.buffer` 是它**共用的**
    底层 buffer；再包一层，旧 wrapper 被 GC 回收时**把底层 buffer 一起关掉**。

    实测触发：`make_task_package.py` 需要复用 `check_contract` 与 `make_handoff`
    的解析逻辑，一 import 就崩 —— 也就是说**这 12 个脚本此前是"不可被复用"的**。

    **规律：模块级的全局副作用必须幂等**（否则"被 import"这个动作就会改变程序状态）。
    """
    for name in ('stdout', 'stderr'):
        s = getattr(sys, name, None)
        if s is None or getattr(s, '_jnovel_utf8', False):
            continue
        try:
            w = io.TextIOWrapper(s.buffer, encoding='utf-8', errors='replace')
        except Exception:
            continue
        w._jnovel_utf8 = True
        setattr(sys, name, w)


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


def extract_body(text: str, keep_blank: bool = False) -> str:
    """从章节文件里取出「正文」——三个脚本必须都走这里。

    `keep_blank=True` 保留段落之间的空行（**只有需要切段的调用方才用**）。
    ⚠️ 默认 False 会压掉全部空行 → 正文变成**一行**。
       于是 `re.split(r'\\n\\s*\\n', body)` 永远得到 1 段，
       `check_aistyle` 的指标 1「段落长度变异系数 CV」**恒为 0**——
       而 0 在它自己的判据里是"段长均匀(AI)"，等于**每一章都被报成最差值**。
       实测：某章真实 80 段，压缩后 1 段。这个死指标挂了很久没人发现，
       因为它的输出"看起来像正常的数字"。
    """
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
            if keep_blank:
                out.append('')
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


# ──────────────────────────────────────────────────────────────
# 章节目录解析（2026-09-19 收归共享）
# ──────────────────────────────────────────────────────────────
# 事故：规范要求正文放 `chapters/`，但 check_chapter_wordcount.py 的 --all
# 只在**项目根** `glob('第*.md')` → 对合规项目一个文件都找不到 →
# 打印"没有找到章节文件" → **退出码 0（假绿）**。
# Phase 4 逐章查字数的唯一命令因此形同虚设，且不报错。
# 这是"闸门看不见稿子却报通过"的**第三次**复发（前两次：`正文/` 目录、`index` 字段）。
#
# 修法：目录解析收归一处，所有脚本共用；**找不到任何章节 = 失败**，不是"没事发生"。
_CHAPTER_DIRS = ('chapters', '正文', '章节目录')
_CHAPTER_GLOB = '第*.md'
_BACKUP_SUFFIX = ('.原稿备份.md', '.bak.md', '.旧.md', '.orig.md')


def find_chapter_files(root) -> list:
    """在项目里找章节正文文件。返回**排序后**的 Path 列表（排除备份/元数据）。

    查找顺序：`chapters/` → `正文/` → `章节目录/` → 项目根 → **递归兜底**。
    找到第一个非空结果即返回（与 check_batch_gate 的兼容范围一致）。
    """
    from pathlib import Path
    root = Path(root)
    if root.is_file():
        return [root]

    def _clean(files):
        out = []
        for f in files:
            n = f.name
            if any(n.endswith(s) for s in _BACKUP_SUFFIX):
                continue
            # `第01章-x.meta.md` 是元数据，不是正文
            if n.endswith('.meta.md'):
                continue
            # 排除 chapters/_meta/ 等子目录里的东西（只认规范位置）
            try:
                rel = f.relative_to(root)
            except ValueError:
                rel = f
            if any(part.startswith('_') for part in rel.parts[:-1]):
                continue
            out.append(f)
        return sorted(out, key=lambda p: str(p))

    for d in _CHAPTER_DIRS:
        sub = root / d
        if sub.is_dir():
            hit = _clean(list(sub.glob(_CHAPTER_GLOB)))
            if hit:
                return hit
    hit = _clean(list(root.glob(_CHAPTER_GLOB)))
    if hit:
        return hit
    # 兜底：递归（老项目结构不规范时，宁可找到也不要静默空手）
    return _clean(list(root.rglob(_CHAPTER_GLOB)))


def read_min_words(project) -> int:
    """从项目的 `02-写作计划.json` 读 `minWordsPerChapter`。

    事故：脚本默认 `min_words=3000` 硬编码，而项目配置是 2000 ——
    2200 字的**合规章节会被判 FAIL**，Agent 于是去补写到 3000+（虚耗且改变文风）。
    读不到配置时回落到 3000（旧默认，保持向后兼容）。
    """
    import json
    from pathlib import Path
    root = Path(project)
    for probe in (root / '02-写作计划.json',
                  root.parent / '02-写作计划.json',
                  root / 'chapters' / '02-写作计划.json'):
        if not probe.is_file():
            continue
        try:
            data = json.loads(read_text(probe))
        except Exception:
            continue
        for key in ('minWordsPerChapter', 'minWords', 'wordsPerChapter'):
            v = data.get(key)
            if isinstance(v, int) and v > 0:
                return v
        # 兼容：章节数组里第一个有 minWords 的
        for ch in (data.get('chapters') or []):
            if isinstance(ch, dict):
                v = ch.get('minWords') or ch.get('minWordsPerChapter')
                if isinstance(v, int) and v > 0:
                    return v
    return 3000


# ──────────────────────────────────────────────────────────────
# 章节边界「人工放行」解析（2026-09-19 收归共享）
# ──────────────────────────────────────────────────────────────
# 事故：check_continuity.py 用**严格**正则（`第\d+→\d+章`）→ 模板行
# `boundary-waived: 第N→N+1章，理由：该钩子第M章回收` 的 `N`/`M` 不是数字 →
# 正确地**不豁免**（exit 1）。而 check_batch_gate.py 用 `(.{8,})` **宽松**正则 →
# 同一行匹配上了 → `waived = True` → 报"✓ 闸门通过"。
# **宽松层覆盖了严格层**，一条从文档里抄来的**模板示例**豁免了全书的边界检查。
#
# 修法：两个脚本走同一个解析器；占位符一律拒绝；豁免**按章号对**逐条扣。
_WAIVER_RX = re.compile(r'boundary-waived\s*[:：]\s*第\s*(\d{1,4})\s*[→\->—~至]{1,2}\s*(\d{1,4})\s*章')
_ANY_WAIVER = re.compile(r'boundary-waived\s*[:：]\s*(.{0,60})')


def parse_waivers(text: str):
    """解析台账里的 boundary-waived 记录。

    返回 `(pairs, problems)`：
      pairs    —— `{(8, 9)}` 形式的**合法**放行对
      problems —— 形似放行但**无效**的记录（占位符章号 / 理由 <8 字），
                  必须报出来——否则"写了一条假放行"和"没写"都无法区分。
    """
    pairs, problems = set(), []
    for m in _ANY_WAIVER.finditer(text):
        raw = m.group(1).strip()
        mm = _WAIVER_RX.search(m.group(0))
        if not mm:
            first = raw[:40] or '(空)'
            problems.append(
                f'放行记录无效（章号必须是具体数字）："{first}" —— '
                f'`第N→N+1章` 这种**文档模板**不能当放行用。')
            continue
        a, b = int(mm.group(1)), int(mm.group(2))
        if b != a + 1:
            problems.append(f'放行记录无效（第{a}→{b}章不构成相邻边界）')
            continue
        # 理由长度：从「理由」之后算起
        reason = ''
        rm = re.search(r'理由\s*[:：]\s*(.*)', m.group(0))
        if rm:
            reason = rm.group(1).strip()
        if len(reason) < 8:
            problems.append(f'放行记录理由过短（第{a}→{b}章，<8 字视为无效放行）')
            continue
        pairs.add((a, b))
    return pairs, problems


# ──────────────────────────────────────────────────────────────
# 实物清单（_meta）解析（2026-10-09 v7.1.1 收归共享）
# ──────────────────────────────────────────────────────────────
# **为什么收归**：v7.0.0 加「具体性三站」（生成点 / 交付点 / 校验点）时，解析器被
# 复制成了多份 —— `parse_concrete_list` **3 份**（make_task_package / check_contract /
# check_batch_gate）、`find_meta_file` **2 份**、`CHAPTER_DIRS` **4 份**（3 个调用方
# 各一份 + 本文件的 `_CHAPTER_DIRS`，而且三处注释都写着"与 _shared.find_chapter_files
# 同口径"—— 那句注释本身，就是"靠人肉同步"的证据）。
# CHANGELOG v7.0.0 的「遗留」点名了这件事：功能一致且都自测过，但这是本项目最忌讳的
# 「**双载体同名必漂**」—— 三份今天等价，改一份漏两份只是时间问题。
#
# **收归时取的是哪一份**：
#   · `find_meta_file` 取 `check_batch_gate.py` 的那份：它的章号正则是
#     `第\s*0*(\d{1,4})\s*章` —— **显式吃掉前导零**，与同文件 `PLOT_NO_RX` 同口径。
#     `make_task_package.py` 那份写 `第\s*(\d{1,4})\s*章`，靠后面的 `int()` 兜住前导零：
#     三种写法结果相同，但**意图没有写进正则** —— 下一个人可能顺手改成别的正则，
#     而不知道"前导零"是被 `int()` 兜着的。两份的目录列举与 rglob 兜底完全一致。
#   · `parse_concrete_list` 三份**逐字等价**（只差返回注解：一份 `-> list`，两份没有），
#     函数体一模一样，取任一即可；这里补上 `list[str]`，让契约在签名上就看得见。
#   · `find_meta_files`（复数版，按目录列举）只有 check_contract 一份，直接搬过来。
#
# ⚠️ **解析规则是跨 agent 的接口契约，改它要同步所有调用方**：
#   落盘位置 = `chapters/_meta/<章名>.meta.md` 的「## 实物清单」段；
#   解析 = 定位 `## 实物清单` 标题行 → 向下取 `- ` 开头的列表项 →
#   遇到下一个 `## ` 或文件结束为止；条目文本去首尾空白。
#   生成点（`make_task_package` 抽进任务包 `concrete_list` 槽位）与校验点
#   （`check_batch_gate` 硬拦、`check_contract` 覆盖率）**必须看同一份清单**，
#   否则"编译过的包"和"验收的闸门"看的不是同一套东西 —— 这正是重复实现最危险的后果。
#
# **章号为什么必须认三种写法**（`第 6 章` / `第 06 章` / `第001章`）：
#   真实项目里三种**都出现过**（v7.1.0 的端到端实测用的就是这三种）。只认一种 =
#   对另外两种项目**静默看不见** —— 而"该有的东西静默消失"在本项目已复发三次
#   （`正文/` 目录、`index` 字段、`_meta/` 本身），所以这里宁可宽。
META_DIR = '_meta'
CONCRETE_HEAD = '实物清单'
META_GLOB = '*.meta.md'
# 章节目录名的**公开名**：三个调用方此前各自复制过一份（`CHAPTER_DIRS = (...)`
# 还都带着"与 _shared.find_chapter_files 同口径"的注释 —— 靠人肉同步的典型）。
# 值只有**一处字面量**（上面 `_CHAPTER_DIRS`，`find_chapter_files` 沿用它）；
# 这里是同一个元组的另一个名字，不是副本 —— 所以不存在漂移的可能。
CHAPTER_DIRS = _CHAPTER_DIRS
# 章号兼容三种写法：前导零由 `0*` **显式**吃掉，不靠下游 `int()` 兜。
META_NO_RX = re.compile(r'第\s*0*(\d{1,4})\s*章')


def find_meta_files(chapters_root) -> list:
    """列举项目里的 `_meta/*.meta.md`（**复数版**，给 check_contract 算实物覆盖率）。

    目录名与 `find_chapter_files` 同口径（chapters / 正文 / 章节目录），
    并保留项目根 `_meta/` 的兜底 —— 判据宁可宽一点，不要因为目录名换个写法就
    **看不见**。**不做 rglob 兜底**：复数版是"列举"，结构不规范时交给单数版
    `find_meta_file` 的全库兜底，别把两份的语义搅在一起。
    """
    from pathlib import Path
    root = Path(chapters_root)
    out = []
    for d in _CHAPTER_DIRS:
        sub = root / d / META_DIR
        if sub.is_dir():
            out += list(sub.glob(META_GLOB))
    sub = root / META_DIR
    if sub.is_dir():
        out += list(sub.glob(META_GLOB))
    return sorted(set(out))


def find_meta_file(root, chapter_no):
    """找本章的 `_meta` 元数据文件（**单数版，按章号找**）。返回 Path；找不到返回 None。

    命名兼容 `第01章-标题.meta.md` 与 `第1章.meta.md`，章号认
    `第 6 章` / `第 06 章` / `第001章`（见本节的说明）。结构不规范的老项目兜底到全库 rglob。

    ⚠️ 找不到**不是异常** —— 后果由调用方各自决定：`check_batch_gate` 把它转成
    **阻塞项**（"该有的东西静默消失"的入口），`make_task_package` 把它转成 TODO
    （编译期拒绝开工）。共享层不替它们做这个决定。
    """
    from pathlib import Path
    root = Path(root)
    cands = []
    for d in _CHAPTER_DIRS:
        sub = root / d / META_DIR
        if sub.is_dir():
            cands += list(sub.glob(META_GLOB))
    sub = root / META_DIR
    if sub.is_dir():
        cands += list(sub.glob(META_GLOB))
    if not cands:                                  # 老项目结构不规范时的兜底
        cands = list(root.rglob(META_GLOB))
    for p in sorted(set(cands)):
        m = META_NO_RX.search(p.name)
        if m and int(m.group(1)) == chapter_no:
            return p
    return None


def parse_concrete_list(text: str) -> list[str]:
    """抽「实物清单」条目 —— **解析规则是跨 agent 的接口契约，不许改**。

    定位 `## 实物清单` 标题行 → 向下取 `- ` 开头的列表项 →
    遇到下一个 `## ` 或文件结束为止。条目文本去首尾空白。

    ⚠️ 标题行用 `[^\\n]*$` 吃掉标题后面的括号说明（真实写法是
    `## 实物清单（本章 3–8 项：只属于这一章的东西）`），**不要收紧成标题精确相等**
    —— 收紧会让所有合规项目都解析出 0 项。
    """
    m = re.search(r'(?m)^##\s*' + CONCRETE_HEAD + r'[^\n]*$', text)
    if not m:
        return []
    rest = text[m.end():]
    nxt = re.search(r'(?m)^##\s', rest)
    body = rest[:nxt.start()] if nxt else rest
    items = []
    for line in body.split('\n'):
        mm = re.match(r'^-\s+(.*)$', line.strip())
        if not mm:
            continue
        item = mm.group(1).strip()
        if item:
            items.append(item)
    return items


def meta_body_path(meta_path):
    """由 `_meta/第01章-测试.meta.md` 推出正文文件 `第01章-测试.md`。

    规则（接口契约）：正文 = **`_meta/` 的上一级目录**下，去掉 `.meta` 的那个文件；
    `_meta/` 不在标准位置时退到同目录。
    """
    from pathlib import Path
    p = Path(meta_path)
    name = p.name
    if name.endswith('.meta.md'):
        name = name[: -len('.meta.md')]
    if p.parent.name == META_DIR:
        return p.parent.parent / (name + '.md')
    return p.parent / (name + '.md')


if __name__ == '__main__':
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    from pathlib import Path
    for p in sys.argv[1:]:
        t = read_text(p)
        b = extract_body(t)
        print('%-40s 全文 %5d 字 → 正文 %5d 字' % (Path(p).name[:38], len(_CJK.findall(t)), len(_CJK.findall(b))))