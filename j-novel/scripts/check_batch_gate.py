#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批次验收闸门（Batch Gate）
==========================

为什么需要这个脚本：
    parallel-workflow.md 里写了"下一批派发前必须跑完 5 项验收"，
    但那是「自查清单」——主编一句"差不多了"就能跳过。
    实测事故：主编把第 7-8 章和第 9-10 章两批同时派出去，第 9 章在第 8 章
    还没收尾时就写完了，章与章之间就断了。

    本脚本把这 5 项验收变成「可执行 + 退出码」：
        exit 0 = 闸门通过，可派发下一批
        exit 1 = 有阻塞项，不许派发

它查什么：
    1. 章节完成连续性 —— 有没有「跳号完成」（第3章完成但第2章没完成）← 治乱序
    2. 字数达标 —— wordCountPass
    3. 质检痕迹 —— 04-质检档案.md 每章是否有记录
    4. 台账推进 —— 03/05 台账是否推进到已完成末章
    5. 章节边界 —— 上章结尾钩子人物，本章开头是否接住
    6. 字段级（L2）—— 台账的 retryCount 有值；本批已写完的章都定了「实物清单」（≥3 项）、
       都出了「剧情卡」（含 `本章钩子` / `要说清的事` 字段 + 头部 `共创状态：`）、
       ★ **返工过（`06-章节工单.md` 的返工轮次 ≥1）的章都出了「改写方案表」**（v7.3.0）

⚠️ 第 6 项里的「实物清单」只查**有没有定、够不够 3 项**（事实，可判定）；
   它**不查兑现率**——"清单里的物在正文用了几项"由 `check_contract.py` 的
   `check_concrete_coverage` **只提示、不阻塞**（那一层没有人类基线）。
⚠️ 「剧情卡」同样只查**产出与字段**，**绝不查"作者改了几处"**（不可校验，且一旦
   做成闸门就会逼出"表演式修改"）——详见 `plot_card_missing` 处的注释。
⚠️ 「改写方案表」分**硬层 A（格式）/ 硬层 B（返工过就必须有表）/ 降级留痕**三层，
   判据与"为什么敢硬"见 `parse_reworked_chapters` 的 docstring 与 main 里的分层注释。

用法:
    python check_batch_gate.py <项目目录>
    python check_batch_gate.py <项目目录> --json
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import ensure_utf8_stdio      # noqa: E402

ensure_utf8_stdio()

_CJK = re.compile(r'[\u4e00-\u9fff]')
_ENCODINGS = ('utf-8', 'gb18030', 'gbk', 'utf-16', 'big5')

# ── 实物清单（本体层 / 具体性闸门，v7.0.0 新增）──────────────────────
# 落盘位置是**接口契约**（跨 agent 共享，不许改）：
#   `chapters/_meta/第XX章-<标题>.meta.md` 的「## 实物清单」段。
# ⚠️ **绝不能放进正文文件** `chapters/第XX章-<标题>.md` —— 2026-09-19 已确立
#   "正文文件严禁任何工程字段"（真人盲评里两位评委各自把外挂的元数据头部列为最强 AI
#   指纹）。`_meta/` 文件才是工程字段的合法容器。
# ★ 2026-10-09 v7.1.1：`CONCRETE_HEAD` / `find_meta_file` / `parse_concrete_list` /
#   `CHAPTER_DIRS` / `META_NO_RX` 已**收归 `_shared`**（此前 `parse_concrete_list` 3 份、
#   `find_meta_file` 2 份、`CHAPTER_DIRS` 4 份）。这里只留本闸门专有的下限。
CONCRETE_MIN_ITEMS = 3      # 清单 3–8 项，3 是下限（见 guides/specificity-gate.md 第二节）

# ── 剧情卡（剧情共创层，v7.1.0 新增）────────────────────────────────
# 落盘位置是**接口契约**（跨 agent 共享，不许改）：
#   `细纲/剧情卡-第NN-NN章.md` —— 每批一份，章号两位补零（如 `剧情卡-第06-10章.md`）。
# 它是**细纲展开之前**与作者一起磨剧情的产物，说明书见 `guides/plot-co-creation.md`。
# 为什么需要校验点：剧情（"第 N 章发生什么、这一章的钩子是哪件事"）此前**唯一的入口**
#   是剧情脚手架，而它被标成【可选】→ 实测 29 本有 AI 大纲的真实项目里**只有 2 本**
#   产出过（≈ 7%）→ 默认路径变成"AI 按通用节奏推导 + 让作者确认"。
PLOT_CARD_DIR = '细纲'
PLOT_CARD_GLOB = '剧情卡-*.md'
PLOT_STATUS_HEAD = '共创状态：'          # 钉死的字段名（人看的形态）
# ⚠️ 判据要覆盖同一含义的多种写法：半角冒号也算（写手/作者都可能打出来）。
PLOT_STATUS_RX = re.compile(r'共创状态\s*[:：]')
# ⚠️ 字段名逐字钉死（`guides/plot-co-creation.md` 3.3：「字段名一个字都不许改」），
#    但**星号只锁开头**：
#    `**本章钩子**（已选定）：` 与 `**本章钩子**：` 都要认 —— 锁太死会把合规卡判死。
PLOT_FIELD_HOOK = re.compile(r'\*\*\s*本章钩子')
PLOT_FIELD_MUST = re.compile(r'\*\*\s*要说清的事')
# 章号兼容 `第 6 章` / `第 06 章` / `第001章` 三种写法（本项目的既有口径：见 `_shared.META_NO_RX`）。
# ⚠️ 必须落在**标题行**（`## 第 NN 章`）上——否则会命中卡片标题「# 剧情卡 · 第 06–10 章」
#    这类正文里的引用（本项目已有两次"断言命中无关文本"的事故）。
PLOT_NO_RX = re.compile(r'(?m)^#{1,6}\s*第\s*0*(\d{1,4})\s*章')

# ── 改写方案表（改写工程，v7.2.0 新增）──────────────────────────────
# 落盘位置是**接口契约**（跨 agent 共享，不许改）：
#   `chapters/_meta/第NN章-改写方案.md` —— 格式与字段名见 `guides/rewrite-units.md` 第四节。
# **为什么需要这个校验点**：这是"生成点有了、交付点有了、校验点没有"的**第 4 次**
#   （v6.8.0 `retryCount` → v7.0.0 实物清单 → v7.1.0 剧情卡 → 本次）：
#     生成点：改写工序要求"**改前先出方案表** + 改后做三行对照"（phase3 清单 5 / SKILL.md 动作 7）
#     交付点：改写任务包（`--rewrite`）会把它当输入
#     校验点：**此前没有** —— 于是"出了表"和"没出表"过闸门的结果完全一样。
REWRITE_PLAN_TAG = '改写方案'
PLAN_TITLE_RX = re.compile(r'(?m)^#{1,6}[^\n]*' + REWRITE_PLAN_TAG)
# 表头列名**逐字钉死**，但**只允许列间距不同**（`rewrite-units.md` 第四节的原文要求）：
#   所以判据是"按 `|` 切、逐格 strip 掉空白与星号之后，与这 6 个名字逐字相同"，
#   **不是**"这几个词在文件里出现过"（前者是格式校验，后者命中一句注释就为绿）。
PLAN_HEAD_CELLS = ('#', '缺陷项', '单元', '定位', '改成什么', '会影响')
# ★ v7.5.0：最后一列改名为「预期改善」——旧表头「会影响」**照样认**（兼容性优先）。
#   为什么改名（有真实依据，不是口味问题）：真实项目 10 章都在「会影响」这一格填
#   `事件／信息／接口不变` —— **那是"边界"，不是"预期改善"**。歧义已被逐字实证。
#   但**不许因为改名让既有项目突然全红**，故两套表头都接受。
PLAN_HEAD_CELLS_V2 = ('#', '缺陷项', '单元', '定位', '改成什么', '预期改善')
PLAN_HEAD_ACCEPTED = (PLAN_HEAD_CELLS, PLAN_HEAD_CELLS_V2)
# 三行对照的字段名。⚠️ 字段名一字不许改，但**半角/全角冒号都要认**
#   （写手可能打 `改前:`）—— 与剧情卡 `共创状态：` 的既有口径一致。
PLAN_FIELD_BEFORE = re.compile(r'改前\s*[:：](.*)')
PLAN_FIELD_AFTER = re.compile(r'改后\s*[:：](.*)')
PLAN_FIELD_WHY = re.compile(r'为什么更好\s*[:：](.*)')
# 六个改写单元是**接口契约**（跨 agent 共享，一个字都不许改）。
REWRITE_UNITS = ('U1', 'U2', 'U3', 'U4', 'U5', 'U6')
_UNIT_TOKEN_RX = re.compile(r'U[A-Za-z0-9]+')

# ── 方案表的「内容精度」判据（v7.5.0 新增）──────────────────────────────
# ★ 为什么需要它（真实事故，证据见 `rewrite-units.md` 4.0.3）：
#   真实项目 10 章的方案表，四列字段**逐字相同**：
#     | 1 | 落笔/句式缺陷 | U2 | 章末或指定句 | 见三行对照 | 事件／信息／接口不变 |
#   表填满了、格式全对、**旧闸门全过** —— 而"先把耦合算清楚"这个机制的作用 = **0**。
#   根因：旧校验只查"字段在不在、格式对不对"，**没规定内容精度**
#   → "填满字段"成了唯一要求 → 表格退化成模板 → 改的时候没有具体目标 → 还是打地鼠。
#
# ★ 判据分级（照 `rewrite-units.md` 4.0.6：**形状给脚本，精度给质检子代理/人**）：
#   · **硬层**：判据是"**这一格是不是某句固定的空话**"（整格归一化后等于黑名单项）
#     与"定位有没有数到段/句" —— 都是**文件级事实**，**不会误伤合法值**（故敢硬拦）。
#     ⚠️ 刻意**不用子串匹配**：`章末落笔公式化` 含"落笔"但**是合法值**，子串匹配会误伤。
#     （v7.3.0 立的判据：**只有"有假阳性"才不许硬化**；"假阴性"只降低覆盖率。）
#   · **软层**：理由的引号短语是否锚定在文件里、顺序说明是否像抄的 ——
#     引号也可用于强调、顺序说明也可能合法地宽泛 ⇒ **只提示，不阻塞**。
PLAN_BLACKLIST = {
    '缺陷项': ('落笔/句式缺陷', '落笔／句式缺陷', '句式缺陷', '落笔缺陷', '综合问题',
               'AI味', 'AI味重', '文字问题', '综合缺陷', '文笔问题'),
    '定位': ('章末或指定句', '多处', '全章', '相关段落', '若干处', '指定句'),
    '改成什么': ('见三行对照', '见下文', '见下', '同上', '待定', '酌情处理', '视情况而定'),
}
# `定位` 的合格形态：能数到段或句（**范围写法也要认**，如"第 3–5 段"）
PLAN_LOC_RX = re.compile(
    r'第\s*\d+\s*(?:[–—\-~～至到]\s*\d+\s*)?[段句]'
    r'|末段|末句|最后一段|最后一句|倒数第\s*\d+\s*[段句]'
    r'|章末段|结尾段')
# `预期改善` 的"答错问题"形态：只说了"不变/不受影响"（那是"边界"，不是"改好"）
PLAN_NOCHANGE_RX = re.compile(r'不变|不受影响|不改变|没有影响')
# …但**含改善信号**时不算（如"句长↑，其余不变"）
PLAN_IMPROVE_RX = re.compile(r'↑|↓|升高|降低|提高|提升|增加|增多|减少|变好|改善')


def _norm_cell(s: str) -> str:
    """单元格归一化：去 markdown 强调符与所有空白。用于与黑名单**整格精确**比较。

    ⚠️ 刻意**保留**标点与斜杠 —— 黑名单项本身含 `/`（如"落笔/句式缺陷"）。
    """
    return re.sub(r'[\s\u3000*]', '', s or '')
# 「改前 ≠ 改后」的归一化口径：**只保留汉字/字母/数字**，其余（空白、标点、
# markdown 装饰符）**全部丢掉再比**。
# 为什么定这么严（这一层是本闸门最容易做成形式主义的地方）：
#   ① 直接比原文 → "改后"把"改前"抄一遍照样过 —— 那正是要拦的**空改**；
#   ② 只去空格 → **只改标点也算改动**（`。` → `，`、句末补个引号），而标点不是内容；
#   ③ 所以判据是"**换成内容层面的字符之后，还一样不一样**"。
# ⚠️ 这一层**永远判不了"改得好不好"** —— 它只回答"**到底改了没有**"。
_NORM_KEEP_RX = re.compile(r'[^\u4e00-\u9fff\u3400-\u4dbfA-Za-z0-9]+')

# ── 章节工单（返工轮次的**可靠触发源**，v7.2.0 新增；v7.3.0 起可做硬闸门）────
# `06-章节工单.md` 由 `make_workorder.py --archive` **机器写入**（每章一行，
#   列：章节 | 字数 | 机械结论 | 缺陷类型 | 返工轮次 | 备注）。
# `05-创作台账.md` 的 `retryCount` **只有当前章**，答不出"历史上哪几章返工过"
#   —— 所以它做不了这个触发源。
# ★ 2026-10-10 v7.3.0：`retryCount >= 1` 从"软提示"升为**硬闸门**（见
#   `parse_reworked_chapters` 的 docstring：**只有"有假阳性"才不许硬**）。
# `--retry` **未指定**时写的是未知标记 `?`（不是 0）—— 那是"判不了"，既不当 0、
#   也不当"返工过"（判据必须保守），只**留痕**（v7.1.1：跳过必须留痕）。
WORKORDER_FILE = '06-章节工单.md'
WORKORDER_RETRY_HEAD = '返工轮次'
# 返工轮次的**未知标记**。⚠️ 必须与 `make_workorder.UNKNOWN_RETRY` 逐字一致
#   （同一含义的写法只能有一份口径；它不是一个"合法的轮次数"）。
UNKNOWN_RETRY = '?'

# 共享层（唯一真相源）：read_text / parse_waivers / find_chapter_files
#   + 实物清单（2026-10-09 v7.1.1 收归）：find_meta_file / parse_concrete_list / CONCRETE_HEAD
#   + 章节目录名与章号正则（CHAPTER_DIRS / META_DIR / META_NO_RX）
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _shared import (CHAPTER_DIRS, CONCRETE_HEAD, META_DIR, META_NO_RX,  # noqa: E402
                     find_meta_file, parse_concrete_list,
                     parse_waivers, read_text as _shared_read_text)


def read_text(path: Path) -> str:
    """稳健读文本 —— **委托 `_shared.read_text`**（2026-09-19 收归）。

    ⚠ 此前这里是**第四份**独立实现，而且和后三份口径相反：
    它的遗留编码候选里含 `'utf-16'`，且用"中文占比最高"来选编码 ——
    正是 `_shared.py` 用 400 个真实文件实测证明**会误判 4% 的文件**的那个启发式
    （utf-16 能把任意偶数长度字节流解成"高中文占比"的乱码）。
    `_shared` 的规则是**绝不无 BOM 猜 utf-16**，这里却把它当候选。

    后果：同一个文件在两个脚本里可能被解成不同内容 → 闸门之间互相矛盾。
    全 SKILL 只允许有一份 read_text。
    """
    return _shared_read_text(path)


def read_json(path: Path):
    """读 JSON（JSON 几乎总是 UTF-8，直接按 utf-8 读，不做编码探测）。"""
    raw = path.read_bytes()
    for enc in ('utf-8-sig', 'utf-8'):
        try:
            return json.loads(raw.decode(enc))
        except Exception:
            continue
    raise ValueError(f'JSON 解析失败（已试 utf-8-sig / utf-8）：{path}')


def find_plot_cards(root: Path):
    """收集全部剧情卡 —— `细纲/剧情卡-*.md`（每批一份）。

    目录名与 `guides/plot-co-creation.md` 7.1 的接口契约一致；
    结构不规范的老项目兜底到全库 rglob（与 `find_meta_file` 同款兜底）。
    """
    cands = list((root / PLOT_CARD_DIR).glob(PLOT_CARD_GLOB))
    if not cands:
        cands = list(root.rglob(PLOT_CARD_GLOB))
    return sorted(set(cands))


def parse_plot_card(text: str):
    """解析一张剧情卡 → `(头部文本, {章号: 该章段文本})`。

    定位 `## 第 NN 章` 标题行 → 该章段取到**下一个章标题或文件结束**为止。
    章号兼容 `第 6 章` / `第 06 章` / `第001章`（同一含义的多种写法都要认）。

    ⚠️ 同一章号出现多次时，段落**累加**而不是覆盖 —— 判据是
    **"有没有一处合规"**，不是"某个位置恰好合规"（本项目踩过两次"命中注释"的坑）。
    """
    hits = list(PLOT_NO_RX.finditer(text))
    head = text[:hits[0].start()] if hits else text
    segs = {}
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        n = int(m.group(1))
        segs[n] = segs.get(n, '') + text[m.start():end]
    return head, segs


# ══════════════════════════════════════════════════════════════════
# 改写方案表（v7.2.0）—— 解析与校验
# ══════════════════════════════════════════════════════════════════

def _split_md_row(line: str) -> list:
    """把一行 markdown 表格切成单元格：去首尾 `|`、逐格 strip 空白与 `*`。

    支持 Markdown 的 `\\|` 转义：**只按未被转义的 `|` 切分**，并把 `\\|` 还原成 `|`
    —— 不还原的话，单元格内容里会留着多余的反斜杠（「伏笔 \\| 回收」会读成
    带反斜杠的「伏笔 \\ 回收」），下游拿去做字符串比较就会**对不上**。

    ⚠️ **裸 `|` 仍会切错**（如单元格里直接写「伏笔 | 回收」）—— 那是**稿件数据问题**，
    不是解析问题：Markdown 规范里这种写法本就要转义，任何解析器都读不对
    （表格里要写竖线就得写 `\\|`，或用全角「｜」）。
    下游**不会静默吞掉**这类行：列数对不上表头时会被计入「跳过」并**点名章号与行号**
    （见 5.6 节红线 1 的 `redline_skips`）——**"看不见"才是事故，"看得见"是可接受的**。

    ⚠️ 本函数被**多处**调用（改写方案表解析、工单解析、大纲红线），
    **签名与返回类型不许变**（仍 `list[str]`），`strip('*')` 的行为也保留。
    """
    body = line.strip().strip('|')
    # `(?<!\\)` = 该竖线**前面不是反斜杠**（即未被转义）才算分隔符。
    return [c.strip().strip('*').strip().replace('\\|', '|')
            for c in re.split(r'(?<!\\)\|', body)]


def _is_md_sep_row(cells: list) -> bool:
    """`|---|---|` 这行分隔行（含 `:---:` 对齐写法）→ 不属于任何列位。

    ⚠️ 空列表必须返回 False（`all([])` 是 True，会把空行误判成分隔行）。
    """
    return bool(cells) and all(set(c) <= set('-: ') for c in cells)


def _find_col(head: list, name: str) -> int:
    """在表头里找列位（按**列名**找，列序可以任意）→ 找不到返回 -1。

    ⚠️ 调用方必须自己判 -1 —— **绝不能**拿它直接下标（`c[-1]` 会取到**最后一列**，
    把别的东西当成目标列，即"静默读错列"）。
    """
    for j, x in enumerate(head):
        if name in x:
            return j
    return -1


def _md_hdr_kind(cells: list) -> str:
    """判断一行是不是「章末型 / 开场类型」表的表头 → `'full'` / `'partial'` / `''`。

    · `'full'`    —— 两列都在 → **可用表头**，其下数据行按**它自己的**列位映射。
    · `'partial'` —— 只含其中一列、且首格不是「第 N 章」→ **缺列名**：
                     该表整体读不到列，必须**留痕**（"其下所有行未检查"）。
    · `''`        —— 普通数据行（含章数据行）。

    首格不是「第 N 章」这一条是**防误判**用的：章数据行的章末型取值不会长成
    "章末型"这三个字，而真表头的首格总是"章号 / 章节 / 序号"这类列名。
    """
    has_end = any('章末型' in x for x in cells)
    has_open = any('开场类型' in x for x in cells)
    if has_end and has_open:
        return 'full'
    if (has_end or has_open) and cells and not re.match(r'^第\s*\d+\s*章', cells[0]):
        return 'partial'
    return ''


def find_rewrite_plans(root: Path) -> list:
    """收集全部改写方案表 —— `<章节目录>/_meta/*改写方案*.md`。

    目录名与 `find_chapter_files` 同口径（`chapters` / `正文` / `章节目录`），
    结构不规范的老项目兜底到全库 rglob。
    ⚠️ 过滤条件是"**文件名里含「改写方案」**"，不是精确等于 `第NN章-改写方案.md` ——
    名字多一个后缀（`-第01章-改写方案.md`）就**看不见**，正是本项目复发过三次的
    "该有的东西静默消失"。
    """
    cands = []
    for d in CHAPTER_DIRS:
        sub = root / d / META_DIR
        if sub.is_dir():
            cands += [p for p in sub.glob('*.md') if REWRITE_PLAN_TAG in p.name]
    if not cands:                                   # 老项目结构不规范时的兜底
        cands = [p for p in root.rglob('*.md') if REWRITE_PLAN_TAG in p.name]
    return sorted(set(cands))


def find_rewrite_plan(root: Path, chapter_no: int):
    """找本章的改写方案表。返回 Path；找不到返回 None。

    章号认 `第 6 章` / `第 06 章` / `第001章` 三种写法（复用 `_shared.META_NO_RX`，
    与`find_meta_file` / `PLOT_NO_RX` 同口径）。
    """
    for p in find_rewrite_plans(root):
        m = META_NO_RX.search(p.name)
        if m and int(m.group(1)) == chapter_no:
            return p
    return None


def find_plan_header(text: str):
    """找方案表的**表头行** → `(行号, 单元格列表)`；找不到返回 `(-1, [])`。

    判据：按 `|` 切开、逐格 strip 之后与 `PLAN_HEAD_CELLS` **逐字且逐列相同**
    （列数也必须一致）——**只允许列间距不同**。
    ⚠️ 不用 `in` / 不用子串：那会命中"补法提示里的那一行格式示例"（本项目踩过两次）。
    """
    for i, line in enumerate(text.split('\n')):
        if not line.strip().startswith('|'):
            continue
        cells = _split_md_row(line)
        # ★ v7.5.0：两套表头都认（旧 `会影响` / 新 `预期改善`）——见 PLAN_HEAD_ACCEPTED 的注释
        if any(cells == list(_acc) for _acc in PLAN_HEAD_ACCEPTED):
            return i, cells
    return -1, []


def find_unknown_units(text: str, header_cells: list) -> list:
    """在「单元」列里找出**不在 U1–U6 之内**的编号。

    **只查这一件事**：编号是不是契约里的那六个。
    不查"这个单元选得对不对"（U5 被当成 U1 用？）——那是**理解级判断**，
    机器判不了（`rewrite-units.md` 4.3 的同一原则），交独立质检子代理。
    """
    if not header_cells:
        return []
    try:
        idx = header_cells.index('单元')
    except ValueError:
        return []
    bad = []
    for i, line in enumerate(text.split('\n')):
        if not line.strip().startswith('|'):
            continue
        cells = _split_md_row(line)
        if cells == list(header_cells):                     # 表头行本身
            continue
        if all(set(c) <= set('-: ') for c in cells):        # `|---|---|` 分隔行
            continue
        if idx >= len(cells):
            continue
        for tok in _UNIT_TOKEN_RX.findall(cells[idx]):
            if tok.upper() not in REWRITE_UNITS:
                bad.append(tok)
    return bad


def parse_rewrite_pairs(text: str) -> list:
    """抽出所有「改前 → 改后 → 为什么更好」**三行齐全**的对照块 → `[(改前, 改后)]`。

    三行的**顺序必须是 改前 → 改后 → 为什么更好**（`rewrite-units.md` 4.1 的格式）。
    三行不齐的**不算一个块** —— 判据是"**有没有一处合规**"，不是"三个词出现过没有"。
    """
    out = []
    for mb in PLAN_FIELD_BEFORE.finditer(text):
        ma = PLAN_FIELD_AFTER.search(text, mb.end())
        if not ma:
            continue
        mw = PLAN_FIELD_WHY.search(text, ma.end())
        if not mw:
            continue
        out.append((mb.group(1).strip(), ma.group(1).strip()))
    return out


def check_plan_precision(text: str, header_cells: list) -> tuple:
    """校验方案表**每一格的内容精度** → `(硬问题列表, 软提示列表)`。

    ★ **硬层**只查两件**文件级事实**（不误伤合法值，故敢硬拦）：
      · 这一格是不是**某句固定的空话**（整格归一化后等于黑名单项）；
      · 「定位」有没有**数到段或句**。
      ⚠️ 刻意**不用子串匹配**：`章末落笔公式化` 含"落笔"但是**合法值**。
    ★ **软层**查两件有假阳性可能的事（引号也可用于强调）→ 只提示，不进退出码。
    """
    hard, soft = [], []
    if not header_cells:
        return hard, soft

    def _col(cells, name):
        try:
            i = header_cells.index(name)
        except ValueError:
            return ''
        return cells[i] if i < len(cells) else ''

    _last = '预期改善' if '预期改善' in header_cells else '会影响'
    _bl = {_norm_cell(b) for _col_ in PLAN_BLACKLIST for b in PLAN_BLACKLIST[_col_]}

    _rows = []
    for _lineno, _line in enumerate(text.split('\n'), 1):
        _s = _line.strip()
        if not _s.startswith('|'):
            continue
        _cells = _split_md_row(_line)
        if any(_cells == list(_acc) for _acc in PLAN_HEAD_ACCEPTED):
            continue                                    # 表头行
        if all(set(c) <= set('-: ') for c in _cells):
            continue                                    # `|---|---|` 分隔行
        if len(_cells) != len(header_cells):
            continue                                    # 不是本表的行（列数不同）
        _rows.append((_lineno, _cells))

    # 按「行」聚合：同一行有多个格子不合格时**合成一条** ——
    #   否则一份 3 行的表会刷出 15 条（真实项目 10 章 = 150 条），报告会长到没人看。
    _bad_rows = {}
    for _lineno, _cells in _rows:
        _items = []
        _hollow = [c for c in ('缺陷项', '定位', '改成什么')
                   if _col(_cells, c) and _norm_cell(_col(_cells, c)) in _bl]
        if _hollow:
            _items.append('、'.join(_hollow) + ' 是空话（没有信息量）')
        _loc = _col(_cells, '定位')
        # ⚠️ 去重：定位已在上一行报了"空话"就不再报形态问题（同一格不报两次）
        if _norm_cell(_loc) and not PLAN_LOC_RX.search(_loc) and '定位' not in _hollow:
            _items.append('定位 没数到段或句')
        _imp = _col(_cells, _last)
        if (_norm_cell(_imp) and PLAN_NOCHANGE_RX.search(_imp)
                and not PLAN_IMPROVE_RX.search(_imp)):
            _items.append(f'{_last} 只写了"不变"（那一列问的是预期改善哪几项）')
        if _items:
            _bad_rows[_lineno] = '；'.join(_items)
    for _lineno in sorted(_bad_rows):
        hard.append('第 %d 行：%s。（合格写法见 `rewrite-units.md` 4.0.2）'
                    % (_lineno, _bad_rows[_lineno]))

    # ── 软层①：理由里的引号短语能不能在文件里找到（防"理由从别处抄的"）──
    #   真实案例：某章理由提到"三叶草带湿土、抖了抖塞进袖子"，而改前/改后句里根本没有。
    for _ma in PLAN_FIELD_WHY.finditer(text):
        _whole = _ma.group(0)
        for _q in re.findall(r'[“"「]([^”"」]{4,})[”"」]', _ma.group(1)):
            if _q not in text.replace(_whole, ''):
                soft.append(
                    f'「为什么更好」里引用的“{_q}”**在文件里（改前/改后句）找不到** —— '
                    f'这条理由可能是从别处抄的或记混了，请人工确认。')

    # ── 软层②：顺序说明点到的单元，若表里一个都没有 → 像模板抄的 ──
    #   真实案例：表内单元全是 U2，说明却写"先做段落级重写（U5/U6）"。
    _head_units = set()
    for _lineno, _cells in _rows:
        for _tok in _UNIT_TOKEN_RX.findall(_col(_cells, '单元')):
            _head_units.add(_tok.upper())
    _mseq = re.search(r'顺序说明[^\n]*\n?([\s\S]{0,300})', text)
    if _mseq and _head_units:
        _said = {t.upper() for t in _UNIT_TOKEN_RX.findall(_mseq.group(1))}
        if _said and not (_said & _head_units):
            soft.append(
                f'「顺序说明」点到的单元（{"、".join(sorted(_said))}）在表里一个都没有 —— '
                f'像是**模板抄来的**，请按本次实际单元改写。')

    return hard, soft


def check_rewrite_plan(text: str) -> tuple:
    """校验一份改写方案表 → `(硬问题列表, 软提示列表)`（硬列表空 = 合规）。

    **判据全是"文件级事实"**，没有一条依赖判断 —— 所以敢做成硬失败：
      ① 标题行含「改写方案」
      ② 表头行 6 列逐字对上（允许列间距不同；旧 `会影响` / 新 `预期改善` 两套都认）
      ③ 至少一处三行对照齐全（`改前：` + `改后：` + `为什么更好：`）
      ④ 至少一处「改前 ≠ 改后」（归一化后比 = **防空改**）
      ⑤「单元」列里出现的编号落在 U1–U6 之内
      ⑥ ★ v7.5.0：**内容精度**（见 `check_plan_precision`）——黑名单空话 / 定位没数到段句 /
         「预期改善」只说了"不变"
    """
    issues = []
    if not PLAN_TITLE_RX.search(text):
        issues.append('标题行不含「改写方案」')

    _hi, _hc = find_plan_header(text)
    if _hi < 0:
        issues.append('缺表头行 `| # | 缺陷项 | 单元 | 定位 | 改成什么 | 预期改善 |`'
                      '（旧表的 `会影响` 也认）')

    # 哪个字段整份文件里都没有 —— 报的是"**缺字段**"，与"没有文件"是两件事
    # （用例 86 的注入 B 专门断言这一点：它反证解析器**认得出**这个文件）。
    _miss = [name for name, rx in
             (('改前', PLAN_FIELD_BEFORE), ('改后', PLAN_FIELD_AFTER),
              ('为什么更好', PLAN_FIELD_WHY))
             if not rx.search(text)]
    for _name in _miss:
        issues.append(f'缺「{_name}：」字段')

    _pairs = parse_rewrite_pairs(text)
    if not _pairs:
        issues.append('没有一处「改前：/改后：/为什么更好：」三行齐全的对照块')
    elif not any(_NORM_KEEP_RX.sub('', a) != _NORM_KEEP_RX.sub('', b)
                 for a, b in _pairs):
        # 归一化后全部相同 = **改了个空**（抄了一遍）。这是最容易发生的假工作。
        issues.append('所有对照块的「改前」与「改后」归一化后完全相同'
                      '（防空改：改前必须 ≠ 改后）')

    _bad_units = find_unknown_units(text, _hc)
    if _bad_units:
        issues.append('「单元」列出现未知单元 %s（只认 U1–U6）'
                      % '、'.join(sorted(set(_bad_units))))

    # ★ v7.5.0：内容精度（硬问题并入 issues；软提示另返回）
    _hard, _soft = check_plan_precision(text, _hc)
    issues.extend(_hard)
    return issues, _soft


def parse_reworked_chapters(root: Path):
    """从 `06-章节工单.md` 读「**哪几章返工过**」
    → `(reworked_set, 跳过原因, info_dict)`。

    为什么用**工单**而不是 `05-创作台账.md`：
      · 工单是 `make_workorder.py --archive` **机器写入**的，**每章一行**、字段固定；
      · 台账的 `retryCount` **只有当前章**，答不出"历史上哪几章返工过"。

    ⚠️⚠️ **这个源能不能做硬闸门，取决于它有没有"假阳性"，而不是有没有"漏报"**
    （v7.3.0 修正了 v7.2.0 那句**过宽的表述**。上一轮写的是"有漏报的判据不许当
    硬闸门"——**这句话太宽了**，它把两种性质完全不同的偏差混成了一种）：

      · **假阳性（误拦）**：把"**没改写过**的章"判成"改写过的" → 会**卡住合法流程**
        （逼着主编给没返工的章补一张方案表）→ **绝对不能有**。
        判据一旦有假阳性，它就是**造假闸门**（本项目最忌讳的一类失败）。
      · **假阴性（漏拦）**：把"**改写过了**的章"漏掉 → 只是**拦得不全**
        → **可以接受**（比完全不拦好）。

    **判据（这条是本层设计的全部依据，不许简化成"有漏报就不许硬"）**：
      **只有"有假阳性"才不许硬化；"有假阴性"只降低覆盖率，不构成禁止硬化的理由。**

    按这条重新看这个源，两种信号的性质完全不同：
      · **`retryCount >= 1` —— 无假阳性，可以硬化**（v7.3.0 已硬化为闸门 B）：
        它是**显式填写**的（`make_workorder.py --archive N --retry K` 写进去的），
        **填了 1 就是真的返工过 1 次** —— 没有任何"把没返工的章写成 ≥1"的路径。
        它会有**假阴性**（忘填 → 漏拦），但"忘填"只会让闸门**少拦一章**，
        **不会多拦一章** → 按上面的判据，**这不构成禁止硬化的理由**。
      · **`retryCount` 读不到（`?`／空／非数字）—— 判不了，绝不当 0**：
        v7.2.0 之前 `--retry` 的默认值是 0，于是"**没填**"与"**真的是 0**"在工单里
        **长得一样** → 这类行既不能算"返工过"（会假阳性），也不能算"没返工"。
        v7.3.0 起 `make_workorder.py` 在未指定 `--retry` 时写**未知标记** `?`，
        本函数把它们收进第三个返回值 `info['unknown']`，由调用方**打进报告留痕**
        （v7.1.1 立的规矩：**跳过/判不了必须留痕**，不许静默）。

    ⚠️ **取不到工单就降级 + 留痕**（v7.1.1 立的规矩：跳过必须留痕）：返回的第二个值是
      降级理由，调用方必须把它打进报告 —— 静默跳过正是本 SKILL 最忌讳的那种失败。
      代价如实写清：**本批次失去"返工过 → 必须交出方案表"这条硬校验（闸门 B 不执行）**，
      只剩"方案表存在时做格式校验"（闸门 A）。
      ⚠️ **降级不等于放行**：它是"**判不了**"，所以**必须出现在报告里**——
      报告里看不到它，就等于这一批偷偷少了一道闸门。

    返回：`(reworked_set, 跳过原因, {'counts': {章号: 轮次}, 'unknown': [章号]})`
    """
    p = root / WORKORDER_FILE
    if not p.exists():
        return set(), (
            f'找不到 `{WORKORDER_FILE}`（`make_workorder.py --archive` 每章质检时写入）'
            f'→ **无法判定哪几章返工过**，因此「返工过 → 必须交出改写方案表」这条硬校验'
            f'**降级为不执行**（注意：是**判不了**，不是"通过"）；本批只保留'
            f'「方案表存在时做格式校验」。代价：**本批次拦不住"边改边测（打地鼠）"的章**。'
            f'（闸门 A 不受影响 —— 它只依赖方案表本身在不在、字段全不全。）'), {}

    rows, idx_retry = [], None
    for line in read_text(p).split('\n'):
        s = line.strip()
        if not s.startswith('|'):
            continue
        cells = _split_md_row(line)
        if idx_retry is None and any(WORKORDER_RETRY_HEAD in c for c in cells):
            idx_retry = next(i for i, c in enumerate(cells) if WORKORDER_RETRY_HEAD in c)
            continue                                    # 表头行，不是数据
        rows.append(cells)

    if idx_retry is None:
        return set(), (
            f'`{WORKORDER_FILE}` 里找不到「{WORKORDER_RETRY_HEAD}」列 → 同上，'
            f'「返工过 → 必须交出方案表」这条硬校验**降级为不执行**（代价相同）。'), {}

    if not rows:
        return set(), (f'`{WORKORDER_FILE}` 里没有可解析的工单行 → 同上，'
                       f'「返工过 → 必须交出方案表」这条硬校验**降级为不执行**'
                       f'（代价相同）。'), {}

    out, counts, unknown = set(), {}, []
    for cells in rows:
        if idx_retry >= len(cells):
            continue
        m = META_NO_RX.search(cells[0])
        if not m:
            continue
        _n = int(m.group(1))
        _raw = cells[idx_retry].strip()
        if _raw == UNKNOWN_RETRY:
            # `--retry` 未指定 → `make_workorder.py` 明确写的"未知"，**不是 0**。
            unknown.append(_n)
            continue
        try:
            _r = int(_raw)
        except (TypeError, ValueError):
            # 空值/非数字 → 同样"判不了"。⚠️ 既不能当 0（漏拦），
            # 也不能当"返工过"（**假阳性：会卡住合法流程**）→ 只记入未知。
            unknown.append(_n)
            continue
        counts[_n] = _r
        if _r >= 1:
            out.add(_n)
    return out, '', {'counts': counts, 'unknown': sorted(set(unknown))}


def main():
    ap = argparse.ArgumentParser(description='批次验收闸门')
    ap.add_argument('project', help='项目目录（含 02-写作计划.json）')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    args = ap.parse_args()

    root = Path(args.project)
    if not root.exists():
        print(f'[错误] 目录不存在：{root}')
        sys.exit(2)

    plan_path = root / '02-写作计划.json'
    if not plan_path.exists():
        print(f'[错误] 找不到 02-写作计划.json —— {plan_path}')
        sys.exit(2)

    plan = read_json(plan_path)
    chapters = plan.get('chapters', [])
    if not chapters:
        print('[错误] 02-写作计划.json 里没有 chapters')
        sys.exit(2)

    cost_mode = plan.get('costMode', 'standard')

    # ⚠️ 2026-09-15 修：字段名兼容。
    # 实测真实项目用 `"index": 1`，而本脚本读 `chapterNumber` → 恒为 None
    # → `by_no` 为空 → 扫到 0 章 → 却报"✓ 闸门通过，下一章：第 1 章"（对 10 章完成稿！）
    # **两种写法都合规，脚本必须都认**；认不出时下面的 fail-closed 会拦住。
    def _no_of(c):
        for k in ('chapterNumber', 'index', 'no', 'chapter', 'chapterNo'):
            v = c.get(k)
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.strip().isdigit():
                return int(v.strip())
        return None

    by_no = {}
    _unnumbered = 0
    for c in chapters:
        n = _no_of(c)
        if n is None:
            _unnumbered += 1
            continue
        by_no[n] = c
    nums = sorted(by_no)

    # ⚠️ fail-closed：**一章都没认出来 = 不能报通过**。
    # 假闸门里最危险的一种是"没看到东西却给绿"——它比报错更糟，
    # 因为它会让主编以为验收过了，甚至照着"下一章"的提示去重写已有章节。
    if not nums:
        print('[错误] 从 02-写作计划.json 里**一章都没能解析出章号** —— 拒绝放行。')
        print(f'        chapters 共 {len(chapters)} 项，无法识别章号的 {_unnumbered} 项。')
        if chapters:
            print(f'        实际字段：{sorted(chapters[0].keys())}')
            print('        需要 `chapterNumber`（规范写法）或 `index`（等价写法）且为整数。')
        print('        → 修好字段名再跑。**不要因为"脚本没报错"就当验收通过。**')
        sys.exit(2)

    if _unnumbered:
        print(f'  [警告] 有 {_unnumbered} 项章节没有可识别的章号，已跳过（未计入验收）')

    # ---------------- 1. 完成连续性（治乱序） ----------------
    completed = [n for n in nums if by_no[n].get('status') == 'completed']
    watermark = 0
    for n in nums:
        if by_no[n].get('status') == 'completed':
            watermark = n
        else:
            break

    out_of_order = [n for n in completed if n > watermark]

    # ---------------- 2. 字数 ----------------
    # ⚠️ 2026-09-15 修：`wordCountPass` 缺失时**用 `words` 兜底**。
    # 实测项目只记 `words`，没有 wordCountPass → 原逻辑 `not None` = True
    # → 把 10 章全部误报成"字数未过"。不能把"字段没写"判成"不合格"。
    _minw = plan.get('wordsPerChapter') or 3000
    nopass = []
    _nopass_unknown = []
    for n in completed:
        c = by_no[n]
        if 'wordCountPass' in c:
            if not c.get('wordCountPass'):
                nopass.append(n)
        elif isinstance(c.get('words'), int):
            if c['words'] < _minw:
                nopass.append(n)
        else:
            _nopass_unknown.append(n)

    # ---------------- 3. 质检痕迹 ----------------
    qc_path = root / '04-质检档案.md'
    qc_text = read_text(qc_path) if qc_path.exists() else ''
    # ── 正文文件含工程脚手架？（2026-09-19 新增，来自一次真实盲评）────────
    # **为什么加**：三位独立盲评评委里有两位**各自**把「本章概要 / 章末型：丁·悬念 /
    #   伏笔标记（预计第X卷回收）」列为**最强的 AI 指纹**——原话："人类作者投正文
    #   不会写'章末型：丁'""外挂的元数据头部暴露了流水线出身"。
    #   `extract_body()` 会剥掉它们，所以**脚本从来没报过**；但任何读到这个文件的人都会看到。
    # 修法：正文文件只留 `# 标题 + 章首引子 + 正文`；元数据移入 `chapters/_meta/<章名>.meta.md`。
    _SCAFFOLD = ('本章概要', '章末型', '伏笔标记', '章节备注', '章末落点', '开场类型')
    scaffold_hits = []
    for _cf in sorted((root / 'chapters').glob('第*.md')):
        # 排除备份/草稿（ 这类不属于正式稿，
        # 但**备份也不该留在 chapters/ 里**——会污染任何扫这一层的工具）
        if any(k in _cf.name for k in ('.bak', '备份', '.orig', '_backup', '.tmp')):
            continue
        _cs = read_text(_cf)
        _found = [_k for _k in _SCAFFOLD if _k in _cs]
        if _found:
            scaffold_hits.append('%s（%s）' % (_cf.name, '、'.join(_found)))

    missing_qc = []
    for n in completed:
        # ⚠️ 2026-09-15 修：**格式宽容**。
        # 原正则只认「第N章」；实测真实项目用表格 `| 01 | 本局无攻略 | 3327 |`，
        # 于是 10 章全被判"质检无记录" —— **假阳性阻塞**（会逼主编去改本来没问题的档案）。
        # 现在同时接受：`第3章`／`第03章`／表格行 `| 3 |`／`### 3.`／`## 03、`
        pats = (
            rf'第\s*0*{n}\s*章',
            rf'(?m)^\|\s*0*{n}\s*\|',
            rf'(?m)^#{{1,4}}\s*0*{n}\s*[.、·\s]',
            rf'(?m)^\s*0*{n}\s*[.、·]\s*\S',
        )
        if not any(re.search(p, qc_text) for p in pats):
            missing_qc.append(n)

    # ---------------- 4. 台账推进 ----------------
    ledger_path = root / '05-创作台账.md'
    state_path = root / '03-状态台账.md'
    ledger_lag = []
    ledger_field_missing = []   # ★ L2 字段级校验（2026-10-02 新增）
    concrete_list_missing = []  # ★ L2 字段级校验：实物清单（2026-10-07 v7.0.0 新增）
    plot_card_missing = []      # ★ L2 字段级校验：剧情卡（2026-10-09 v7.1.0 新增）
    rewrite_plan_bad = []       # ★ L2 硬层 A：方案表**存在**但格式缺项（v7.2.0）
    _no_plan_rework = []        # ★ L2 硬层 B：**返工过却没有方案表**的章号（v7.3.0 起硬拦）
    _rework_rows = []           # ★ 硬层 B 的明细行（`第 N 章：返工轮次 = K`，给主编看的）
    _wo_skip_note = ''          # ★ 降级留痕（找不到工单时非空；见 parse_reworked_chapters）
    _retry_unknown_note = ''    # ★ 返工轮次读不到（`?`／空／非数字）的留痕（v7.3.0）
    soft_hints = []             # 软提示（不阻塞）
    if completed:
        last = max(completed)
        lt = read_text(ledger_path) if ledger_path.exists() else ''
        st = read_text(state_path) if state_path.exists() else ''
        m1 = re.search(r'最近重读章号[：:]\s*第\s*(\d+)\s*章', lt)
        if not m1:
            ledger_lag.append(f'05-创作台账 未找到"最近重读章号"（应为第 {last} 章）')
        elif int(m1.group(1)) < last:
            ledger_lag.append(f'05-创作台账"最近重读章号"={m1.group(1)}，落后于已完成第 {last} 章')
        m2 = re.findall(r'第\s*(\d+)\s*章', st)
        if st and not m2:
            ledger_lag.append('03-状态台账 未记录章号')
        elif m2 and max(int(x) for x in m2) < last:
            ledger_lag.append(f'03-状态台账最新章号={max(int(x) for x in m2)}，落后于第 {last} 章')

        # ══ L2 字段级校验（2026-10-02 新增）════════════════════════
        # **为什么要加这一层**：此前所有校验都停在 L1（"文件在不在"），
        # 于是出现了一个实证过的漏洞 —— `05-创作台账.md` 里**同一个文件、同一份模板**
        # 的两个字段命运相反：
        #   · 「最近重读章号」被本脚本校验 → 真实项目里**有值** ✓
        #   · 「retryCount」没有任何脚本校验 → 真实项目里**整个字段缺失** ✗
        # 而 `retryCount` 恰恰是「一次合格率」唯一的数据源（v6.5.0 加它的理由
        # 就是"此前从未落盘"）—— **生成点+交付点齐备、缺校验点 = 换个地方继续不落盘**。
        # 判据形态：必须是**字段名 + 值**，不接受只写字段名（否则一个标题也算通过）。
        _rc = re.search('返工轮次.{0,20}?[:：][ ]*([0-9]+)', lt)
        if not _rc:
            ledger_field_missing.append(
                '05-创作台账 缺 `retryCount`（返工轮次）字段或值 —— 它是「一次合格率」的唯一数据源。'
                '模板见 guides/creation-ledger.md，格式：`本章返工轮次（retryCount）：0`')

        # ══ 实物清单（v7.0.0 新增）════════════════════════════════
        # **为什么加这一条**：与 `retryCount` 是同一个病 —— "生成点有了、交付点有了、
        #   校验点没有"。实物清单是本体层（"这一章里的东西是不是只属于这本书"）唯一的产出物：
        #     生成点：`chapters/_meta/<章名>.meta.md` 的「## 实物清单」段（动笔前定，3–8 项）
        #     交付点：任务包 `concrete_list` 槽位（make_task_package 抽不到就 TODO → 拒绝开工）
        #     校验点：**此前没有** —— 于是"没定清单"和"定了清单"过闸门的结果完全一样。
        #   17 项频率指标全绿也看不出这个洞（见 guides/specificity-gate.md 第一节）。
        #
        # 判据（**事实级**，可判定，所以敢做成硬失败）：
        #   ① 有本章的 `*.meta.md`；② 里面有 `## 实物清单` 段；③ 段里 ≥3 个列表项。
        # ⚠️ **兑现率（清单写了 5 项、正文用了几项）不在这里查**：那需要人类基线，
        #    而且脚本判断不了清单本身好不好 → 由 `check_contract.check_concrete_coverage`
        #    只提示、不阻塞。**硬闸门只查"有没有定"，不查"定的好不好"。**
        # ⚠️ 只扫**本批次窗口内已经写完的章**（`completed`），窗口外 / 还没写的章一律不牵连：
        #    旧项目（v7.0.0 之前）根本没有 `_meta/` 目录，但它本批次没有新完成的章就一条都不报。
        #    **新必填字段不追溯旧稿**——这是兼容性设计，不是漏网。
        for _n in completed:
            _mp = find_meta_file(root, _n)
            if _mp is None:
                concrete_list_missing.append(
                    f'第 {_n} 章：`chapters/_meta/` 下找不到本章的 `*.meta.md`')
                continue
            _items = parse_concrete_list(read_text(_mp))
            if not _items:
                concrete_list_missing.append(
                    f'第 {_n} 章：{_mp.name} 里没有「## {CONCRETE_HEAD}」段，或一个条目都没有')
            elif len(_items) < CONCRETE_MIN_ITEMS:
                concrete_list_missing.append(
                    f'第 {_n} 章：{_mp.name} 的实物清单只有 {len(_items)} 项'
                    f'（下限 {CONCRETE_MIN_ITEMS} 项）')

        # ══ 剧情卡（v7.1.0 新增）════════════════════════════════════
        # **为什么加这一条**：与 `retryCount`／实物清单是**同一个病的第三次** ——
        #   "生成点有了、交付点有了、校验点没有"。
        #   剧情（"第 N 章发生什么、这一章的钩子是哪件事"）此前**唯一的入口**是
        #   `plot-scaffold.md` 的剧情脚手架，而它被标成【可选】→ 实测 29 本有 AI 大纲的
        #   真实项目里**只有 2 本**产出过（≈ 7%）→ **默认路径**变成
        #   "AI 按通用节奏推导大纲 + 让作者【确认】"。
        #   而"确认"是二元的（点头/不点头），中间没有颗粒度 —— 用户的原话是
        #   "让 AI 自己发挥构造剧情框架，写出来的东西不太好"。
        # 修法（流程侧）：每批细纲展开**之前**先出剧情卡（每章 3–5 行：钩子 + 要点）
        #   → 跟作者磨 → 磨完才展开成完整细纲。本函数是那条链的**校验点**。
        #
        # 判据（**只查存在性与字段**，可判定，所以敢做成硬失败）：
        #   ① `细纲/剧情卡-*.md` 里有一张卡，其章段集合含本章号
        #      （兼容 `第 6 章`／`第 06 章`／`第001章`）
        #   ② 该卡**文件头**含 `共创状态：` 行
        #   ③ 该章段含 `**本章钩子**` 与 `**要说清的事**` 两个字段
        #
        # ⚠️⚠️ **绝不能加的一条判据：作者改了几处。**
        #   ① 它**不可校验**——"他改了几处"没有任何可机械判定的形态；
        #   ② 更严重的是，**一旦把它做成闸门，主 Agent 为了过检就会去逼作者改东西**：
        #      那是**表演式修改**（作者随手改两个字好让闸门变绿），**比没磨更糟**——
        #      它污染了"哪些是作者的品味"这条唯一的对账基准。
        #   所以判据**只停在两件"动作"上：卡有没有产出 / 问过没**。
        #   （`guides/plot-co-creation.md` 7.3 把这一条写成"明确不设判据"，这里是它的代码侧。）
        #
        # ⚠️ 由此直接推出：**`共创状态：作者未回应（YYYY-MM-DD）` 必须照常放行**，
        #   它与"已与作者确认"在留痕上**等价**。理由：**"问了、作者没回、不阻塞"
        #   是设计内的合法结果**——节流档 / 推荐档的"不问"批次用"一次性告知"
        #   （`共创状态：未确认·一次性告知`），推荐档其余批次问完就往下走、
        #   把没回的记进 `未回应项：`（`plot-co-creation.md` 4.4 / 5.3 / 第六节）。
        #   把它拦下的后果**不是"质量下降"，而是主 Agent 转身去骚扰作者**——
        #   正是本轮要防的那件事。（用例 81 是这条的护栏：谁把判据加严它会立刻报警。）
        # ⚠️ 只扫**本批次窗口内已经写完的章**（`completed`）——窗口外 / 旧项目一律不牵连，
        #   与上面实物清单同口径（新必填字段**不追溯旧稿**，是兼容性设计不是漏网）。
        _card_idx = {}
        for _cp in find_plot_cards(root):
            _chead, _csegs = parse_plot_card(read_text(_cp))
            for _cn, _cseg in _csegs.items():
                _card_idx.setdefault(_cn, []).append((_cp, _chead, _cseg))
        for _n in completed:
            _ents = _card_idx.get(_n)
            if not _ents:
                plot_card_missing.append(
                    f'第 {_n} 章：`{PLOT_CARD_DIR}/` 下没有剧情卡含「## 第 {_n} 章」段'
                    f'（期望 {PLOT_CARD_DIR}/剧情卡-第XX-XX章.md）')
                continue
            # 存在性判据问"**有没有一处合规**"：任一卡的头部与字段都齐 → 通过。
            # （同章号被多张卡重复覆盖是允许的，取合规的那一张。）
            _fails = []
            for _cp, _chead, _cseg in _ents:
                _miss = []
                if not PLOT_STATUS_RX.search(_chead):
                    _miss.append(f'文件头没有 `{PLOT_STATUS_HEAD}` 行')
                if not PLOT_FIELD_HOOK.search(_cseg):
                    _miss.append('本章段缺 `**本章钩子**` 字段')
                if not PLOT_FIELD_MUST.search(_cseg):
                    _miss.append('本章段缺 `**要说清的事**` 字段')
                if not _miss:
                    _fails = []
                    break
                _fails.append(f'{_cp.name}：' + '、'.join(_miss))
            if _fails:
                plot_card_missing.append(f'第 {_n} 章：' + '；'.join(_fails))

        # ══ 改写方案表（改写工程，v7.2.0 新增；v7.3.0 把软层升级为硬层）══════
        # **最终形态是三层（硬 A / 硬 B / 降级留痕），性质各不相同** —— 分层的依据是
        # 本 SKILL 的铁律"**能机器验的只有两件：① 文件/字段在不在 ② 改前 ≠ 改后**"，
        # 加上 v7.3.0 立的一条判据：**只有"有假阳性"才不许硬化**。
        #
        # ▸ **硬层 A（可判定 → 敢做成硬失败）**：方案表**存在**时，必须
        #     ① 标题行含「改写方案」② 表头 6 列逐字对上（允许列间距不同）
        #     ③ 至少一处三行对照齐全 ④ 至少一处「改前 ≠ 改后」（防空改）
        #     ⑤「单元」列里出现的编号落在 U1–U6
        #   五条全是**文件级事实**（东西在不在 / 格子填没填 / 字符串一样不一样 /
        #   编号在不在契约里），没有一条依赖"判断"，所以硬失败是站得住的。
        #
        # ▸ **硬层 B（v7.3.0 新增：从"软提示"升级而来）**：窗口内**返工过**
        #   （`06-章节工单.md` 的「返工轮次」≥1）的章，**必须存在**改写方案表。
        #   为什么现在敢硬 —— **v7.2.0 那句"有漏报的判据不许当硬闸门"过宽了**，
        #   必须把两种偏差分开（详见 `parse_reworked_chapters` 的 docstring）：
        #     · **假阳性（误拦）**：把"没改写的章"判成"改写过的" → **卡住合法流程**
        #       → **绝对不能有**（那就是造假闸门）；
        #     · **假阴性（漏拦）**：把"改写过的章"漏掉 → 只是**拦得不全**
        #       → **可以接受**（比完全不拦好）。
        #   **判据：只有"有假阳性"才不许硬化。** `retryCount >= 1` 是**显式填写**的
        #   （填了 1 就是真返工过 1 次），**没有"把没返工的章写成 ≥1"的路径**
        #   → 无假阳性 → 可以硬。它会漏拦（忘填 `--retry`），但漏拦只是少拦一章。
        #   ⚠️ 用户实测到的"跳过"正是这一层软着导致的：**Agent 打完字改完、
        #   只要不写方案表，闸门完全静默**。
        #   ⚠️ **只查存在性，不查方案表内容质量** —— "为什么更好是否具体""单元
        #   选得对不对"是理解级判断，机器判不了，交**独立质检子代理四问**。
        #
        # ▸ **软层（不许写判据的那三件事）**：以下三件事**机器判不了，所以一条
        #   判据都不写**，交独立质检子代理 / 人：
        #     ·「为什么更好」是否**具体** —— `rewrite-units.md` 4.3 已论证它是
        #       理解级判断（与 v6.9.0「闲笔密度」是同一次失败教训）→ **宁可承认测不了**
        #     · 单元**选得对不对**（把 U5 当 U1 用？）
        #     · 方案表的**顺序**对不对（先粗后细）
        #   ⚠️ **不许写正则去猜这三件事**：猜出来的判据会逼出"**表演式改写**"
        #      （把理由写得像样、单元编号填对，而文本一点没变好）——那**比没有闸门更糟**。
        #
        # ▸ **降级 + 留痕（取不到工单时）**：**降级不等于放行**——它是"**判不了**"。
        #   降级理由与代价由 `parse_reworked_chapters` 给出（v7.1.1 立的规矩：
        #   **跳过必须留痕**），调用方必须打进报告；静默少一道闸门是本 SKILL
        #   最忌讳的失败。⚠️ 降级时**绝不能**改判为"通过"（那是造假闸门）。
        # ⚠️ 只扫**本批次窗口内已经写完的章**（`completed`），与上面两条 L2 校验同口径：
        #   窗口外 / 旧项目一律不牵连（新必填字段**不追溯旧稿**）。
        for _n in completed:
            _pp = find_rewrite_plan(root, _n)
            if _pp is None:
                continue                    # 不存在 → 硬层 A 不判（由硬层 B 判）
            _plan_issues, _plan_soft = check_rewrite_plan(read_text(_pp))
            if _plan_issues:
                rewrite_plan_bad.append(
                    f'第 {_n} 章：{_pp.name} —— ' + '；'.join(_plan_issues))
            # ★ v7.5.0：**软层提示**（理由是否锚定 / 顺序说明是否像抄的）—— 只提示，不阻塞
            for _hint in _plan_soft:
                soft_hints.append(f'第 {_n} 章：{_pp.name} —— {_hint}')

        _reworked, _wo_skip, _wo_info = parse_reworked_chapters(root)
        _wo_skip_note = _wo_skip
        if _wo_skip:
            soft_hints.append(_wo_skip)     # ★ 跳过必须留痕（v7.1.1）
        else:
            _counts = _wo_info.get('counts', {})
            # ★ 硬层 B：返工轮次 ≥1 的章，必须有对应的改写方案表（v7.3.0）
            _no_plan_rework[:] = [x for x in sorted(_reworked & set(completed))
                                  if find_rewrite_plan(root, x) is None]
            _rework_rows[:] = ['第 %d 章：返工轮次 = %d'
                               % (x, _counts.get(x, 1)) for x in _no_plan_rework]
            # ★ 留痕：返工轮次读不到（`?`／空／非数字）的章 —— 既不当 0、也不当返工过。
            _unk = [x for x in _wo_info.get('unknown', []) if x in set(completed)]
            if _unk:
                _retry_unknown_note = (
                    '第 %s 章：`%s` 的「返工轮次」记的是**未知**（`?`／空／非数字 —— '
                    '`make_workorder.py` 在**未指定 `--retry`** 时写入 `%s`）—— '
                    '这几章**既不算"返工过"、也绝不算 0**：`--retry` 的默认值曾是 0，'
                    '"没填"与"真的是 0"在工单里长得一样，所以判据必须保守'
                    '（**有假阳性才不许硬，假阴性可以接受**）→ 本闸门对这 %d 章'
                    '既不拦也不是"放行"，只是**判不了**；一次合格率里它们也不能算'
                    '"首次合格"。要消掉这条提示：重跑 '
                    '`--archive <章号> --retry N` 把轮次补上。'
                    % ('、'.join(str(x) for x in _unk), WORKORDER_FILE,
                       UNKNOWN_RETRY, len(_unk)))
                soft_hints.append(_retry_unknown_note)

        # 软提示：对话专项列（2026-10-02 随对话体系新增；给存量项目留过渡，不阻塞）
        _arc = root / '04-质检档案.md'
        _at = read_text(_arc) if _arc.exists() else ''
        if _at and '对话' not in _at:
            soft_hints.append(
                '04-质检档案 里没有任何「对话」列 —— 2026-10-02 起质检摘要应含「对话专项」'
                '（占比／废话／混沌／称呼／去标签可辨／间接叙述偷对话）')

        # 软提示：状态台账的三态标记（铁律五）
        if st and not re.search('[✓?✗×]', st):
            soft_hints.append(
                '03-状态台账 里找不到三态标记（✓ 已落正文 / ? 仅规划 / ✗ 已被否定）—— '
                '铁律五要求全条目带标记，否则下游会把"仅规划"当成已发生的事实来写')

        # 软提示：人物档案的声口卡（2026-10-02 随对话体系新增）
        _chp = root / '00-人物档案.md'
        if _chp.exists() and '压力下的反应' not in read_text(_chp):
            soft_hints.append(
                '00-人物档案 里没有「压力下的反应方式」（声口卡四栏之一）—— '
                '它决定了冷面角色会不会被写成"功能性的短句机器人"')    # ---------------- 5. 章节边界 ----------------
    boundary_fail = False
    boundary_note = ''
    continuity_script = Path(__file__).parent / 'check_continuity.py'
    if continuity_script.exists():
        try:
            r = subprocess.run(
                [sys.executable, str(continuity_script), str(root)],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=180,
            )
            boundary_fail = (r.returncode == 1)
            boundary_note = (r.stdout or '')[-400:]
        except Exception as e:
            boundary_note = f'（连续性检查执行失败：{e}）'
    else:
        boundary_note = '（未找到 check_continuity.py，跳过）'

    # ---------------- 5.5 章节边界的人工放行 ----------------
    # 设计意图：check_continuity 是【提示工具】不是硬闸门——"刻意留后章"是正常写法。
    # 主编复查后若判定正常，在 05-创作台账.md 里写一行显式放行记录即可，不必绕过闸门。
    #   格式：boundary-waived: 第8→9章，理由：该钩子第11章回收（至少 8 字）
    #
    # ⚠️ 2026-09-19 修（**重要，假放行事故**）：
    #   此前这里用宽松正则 `boundary-waived\s*[:：]\s*(.{8,})` + `waived = True`（**全局**）。
    #   而 check_continuity.py 用严格正则 `第(\d+)→(\d+)章` **按章号对**扣。
    #   于是**从文档里抄来的模板行**：
    #       boundary-waived: 第N→N+1章，理由：该钩子第M章回收，已在03-状态台账登记
    #   在 check_continuity 里**正确地不豁免**（N/M 不是数字）→ exit 1，
    #   却被这里匹配上 → 报"✓ 闸门通过 —— [已放行]"。**宽松层覆盖了严格层。**
    #   实测：真实项目台账里就躺着这一行模板，全书边界检查被它一条豁免。
    #
    #   现改为：走 `_shared.parse_waivers`（与 check_continuity 同一口径）
    #   —— ① 章号必须是具体数字，② 必须相邻，③ 理由 ≥8 字，④ 无效记录要报出来。
    waived, waiver_note, waiver_problems = False, '', []
    if boundary_fail:
        lt_all = read_text(root / '05-创作台账.md') if (root / '05-创作台账.md').exists() else ''
        _pairs, waiver_problems = parse_waivers(lt_all)
        if _pairs:
            waived = True
            _lst = sorted(_pairs)
            waiver_note = ' ｜ '.join(f'第{a}→{b}章' for a, b in _lst[:4])
            if len(_lst) > 4:
                waiver_note += f' ｜ …另有 {len(_lst) - 4} 条'

    # ---------------- 5.6 崩坏红线 1：连续 3 章同章末型 / 同开场类型 ----------------
    # 原设计里这条红线只写在文档里，靠 Agent 自己数——会话一切断就数不了（03/05 台账都不记这个）。
    # 但数据其实在 01-大纲.md 的章节表里（章末型 / 开场类型两列），所以可以机械检查。
    #
    # ★★ 2026-10-10 v7.4.0 修：**一个 01-大纲.md 里常有多张表**（分卷大纲就是"每卷一张表"），
    #   而旧实现只认**扫描到的第一个表头**，把后面几张表的行统统按"列数与表头不一致"丢掉。
    #   实测某真实项目因此丢掉 **42 行** —— 而"连续两章同型"是本书的红线之一：
    #   **漏检 42 行 = 这条红线在这 42 行上形同不存在**，输出里却只有一行不起眼的 [警告]。
    #   改法（★注意修的是**哪一半**）：**列的个数校验一个字没动**（它本意是对的 ——
    #   列错位会把「章末型」读成「开场类型」→ **误报**红线）。修的是**解析器读不准**：
    #   ① 一行一个表头 → **扫描全部表头**；② 表头归属按**行号区间**（行归"最近的上方表头"）；
    #   ③ 逐表用**该表自己的表头**做列数校验与列位映射。让它把**本来能读对的行读对**，
    #   读不对的**才**跳过 —— 且跳过必须**点名章号 + 行号 + 原因**（不再是一行笼统警告）。
    redline_note, redline_hit = '', False
    redline_skips = []          # 仍被跳过的行/表：逐条点名 —— "跳过必须留痕"（skill-mechanics.md 反模式表）
    outline = root / '01-大纲.md'
    if outline.exists():
        data = []
        cur = None              # 当前所在表的表头：(行号, 单元格, 是否可用)
        for ln, line in enumerate(read_text(outline).split('\n'), 1):
            if not line.strip().startswith('|'):
                # 非表格行（`## 第一卷` / 空行 / 散文…）→ **当前表到此结束**。
                # 少了这一步，第二张表**之前**的散文行会被算进第一张表的地盘。
                cur = None
                continue
            cells = _split_md_row(line)
            if _is_md_sep_row(cells):
                continue        # `|---|---|` 分隔行不属于任何列位
            kind = _md_hdr_kind(cells)
            if kind:
                cur = (ln, cells, kind == 'full')
                if kind == 'partial':
                    # 缺列名的表头：**整张表读不到列** —— 留痕（说清"其下所有行未检查"）。
                    # 旧实现在这里会静默少查一张表：`col()` 返回 -1 后 `c[-1]` 取到**最后一列**，
                    # 把别的东西当成「开场类型」。
                    redline_skips.append(
                        f'第 {ln} 行的表头 / 原因：只有「章末型」与「开场类型」中的一列'
                        f'（缺列名）→ 该表无法定位列，**其下所有章数据行未检查**')
                continue
            mnum = re.match(r'^第\s*(\d+)\s*章', cells[0]) if cells else None
            if not mnum:
                continue        # 不是章数据行（如「合计」行）→ 与红线 1 无关，不算"跳过"
            n = int(mnum.group(1))
            if cur is None:
                redline_skips.append(
                    f'第 {n} 章 / 第 {ln} 行 / 原因：其上方没有任何含「章末型 / 开场类型」的表头'
                    f'→ 未参与红线 1 检查')
                continue
            hln, hcells, hdr_ok = cur
            if not hdr_ok:
                redline_skips.append(
                    f'第 {n} 章 / 第 {ln} 行 / 原因：所在表的表头（第 {hln} 行）缺列名'
                    f'→ 未参与红线 1 检查')
                continue
            # ⚠ 列的个数校验**保留原样**（放宽它就是"为了少警告而放行列错位"→ 会误报红线）。
            #   变的只是"跳过要说清楚是谁、为什么"。
            if len(cells) != len(hcells):
                redline_skips.append(
                    f'第 {n} 章 / 第 {ln} 行 / 原因：列数 {len(cells)} 与所在表头'
                    f'（第 {hln} 行）的 {len(hcells)} 列不一致（列错位会误报红线，故不参与检查）')
                continue
            i_end, i_open = _find_col(hcells, '章末型'), _find_col(hcells, '开场类型')
            data.append((n, cells[i_end][:6], cells[i_open][:8]))

        # ★ 多表合并后**章号可能不连续**（第 1–20 章一张表、第 21–40 章另一张），
        #   而红线 1 的语义是"看**最近 3 行**"（问的是"当下是不是崩坏了"）。
        #   所以必须**先按章号排序**再取最后 3 行 —— **不能**按文件里出现的顺序取，
        #   否则分卷表一换，取到的是"上一卷末尾 + 本卷开头"这种**不相邻**的 3 章
        #   （正是 2026-09-14 那次假红线的同型错误）。排序是稳定的：同章号保持原序。
        data.sort(key=lambda t: t[0])

        # 「续接 / 跳接」是 SKILL 规定的**默认与过渡**开场类型（"默认续接"），
        # 连续出现是正常写法，不算崩坏——否则每本书开局都会误报。
        # 「章末型」不豁免：SKILL 明确规定"连续两章不得同型，尤其不得连续两章悬念型"。
        #
        # 语义：红线 1 问的是"**当下**是不是崩坏了"（不是"历史上有没有出现过"），
        # 所以只看**最近的 3 行**（`data` 已按章号排序，见上）。
        #
        # ⚠️ 2026-09-14 修：此前是 `[x for x in data if x[idx]][-3:]` —— 先按
        # "该字段非空"过滤，再取最后 3 条。若中间某章该字段为空（如第 3 章没填
        # 章末型），取到的会是**第 1/2/4 章**，把"不相邻的 3 章"当成连续同型报出来。
        # 现改为：取最后 3 行原始记录 + **校验章号严格连续**（空值即不满足）。
        # ⚠️ 这一段判据 v7.4.0 **一字未动**（多表改动只影响 `data` 怎么攒出来）。
        _EXEMPT_OPEN = {'续接', '跳接'}
        for field, idx in (('章末型', 1), ('开场类型', 2)):
            last3 = data[-3:]
            if len(last3) != 3:
                continue
            if not all(last3[i][0] + 1 == last3[i + 1][0] for i in range(2)):
                continue          # 章号不连续（有缺章/跳号）→ 不判
            if any(not x[idx] for x in last3):
                continue          # 有章没填这个字段 → 不判（避免拿空值当"同型"）
            v = last3[0][idx]
            if len({x[idx] for x in last3}) != 1:
                continue
            if field == '开场类型' and any(e in v for e in _EXEMPT_OPEN):
                continue
            redline_hit = True
            redline_note = (f'连续 3 章同一{field}（{v}）：'
                            f'第 {last3[0][0]}/{last3[1][0]}/{last3[2][0]} 章')

    # ---------------- 报告 ----------------
    blockers = []
    if out_of_order:
        blockers.append(
            f'【乱序完成】这些章已 completed，但它们之前的章还没完成：第 {out_of_order} 章\n'
            f'      → 说明它们是在"上游章未收尾"时写的，极可能基于错误的上一章结尾。\n'
            f'      → 处理：让上游章先完成，再把乱序章按『链式流水线』重做（或至少逐对复查边界）。'
        )
    if nopass:
        blockers.append(f'【字数未达标】第 {nopass} 章')
    if missing_qc:
        blockers.append(f'【质检无记录】04-质检档案.md 里找不到这些章的记录：第 {missing_qc} 章')
    if ledger_lag:
        blockers.append('【台账未推进】' + '；'.join(ledger_lag))
    if ledger_field_missing:
        blockers.append('【台账缺字段】' + '；'.join(ledger_field_missing))
    if concrete_list_missing:
        blockers.append(
            '【实物清单缺失】实物清单是**动笔前**必须定的（`guides/specificity-gate.md` 第二节），'
            '定义见任务包 `concrete_list` 槽位：\n'
            + '\n'.join('      · ' + _c for _c in concrete_list_missing) +
            '\n      → 它是本体层（"这一章的东西是不是只属于这本书"）唯一的产出物；'
            '没定清单 = 这一章没有只属于它的东西，而 17 项频率指标全绿也看不出来。\n'
            '      → 补法：在 `chapters/_meta/第XX章-<标题>.meta.md` 里加一段\n'
            '           `## 实物清单（本章 3–8 项：只属于这一章的东西）`\n'
            '         下面每行一项，形如 `- 半包受潮的火柴`（只写物，不写解释）。\n'
            '      → 落盘位置是接口契约：**绝不能写进正文文件**（正文严禁任何工程字段）。'
        )
    if plot_card_missing:
        blockers.append(
            '【剧情卡缺失】剧情卡是**动笔前与作者一起磨的产物**'
            '（`guides/plot-co-creation.md`），必须站在**细纲展开之前**：\n'
            + '\n'.join('      · ' + _p for _p in plot_card_missing) +
            '\n      → 没有它，细纲只能按 AI 推的通用节奏展开 —— 那正是要消灭的默认路径'
            '（实测 29 本有 AI 大纲的项目里只有 2 本产出过剧情脚手架 ≈ 7%）。\n'
            '      → 补法：落盘 `细纲/剧情卡-第NN-NN章.md`（每批一份，章号两位补零，'
            '如 `细纲/剧情卡-第06-10章.md`），最小格式：\n'
            '           `# 剧情卡 · 第 06–10 章`\n'
            '           `> 共创状态：已与作者确认（YYYY-MM-DD）｜档位：推荐档`\n'
            '           `> 作者改动：无｜未回应项：无`\n'
            '           `## 第 06 章：<章名>`\n'
            '           `- **本章钩子**（已选定）：<这一章的钩子是"哪件事">`\n'
            '           `- **要说清的事**：① … ② … ③ …`\n'
            '         逐字模板见 `guides/plot-co-creation.md` 3.3，**字段名一个字都不许改**。\n'
            '      → ⚠️ 闸门只查"**卡有没有产出 / 字段在不在 / 问过没**"：'
            '`共创状态：作者未回应（YYYY-MM-DD）` 照常放行（"问了、作者没回、不阻塞"'
            '是设计内的合法结果）。**不查作者改了几处**——不可校验，且会逼出表演式修改。'
        )
    if rewrite_plan_bad:
        blockers.append(
            '【改写方案表不合规】方案表**存在**但格式缺项（`guides/rewrite-units.md` '
            '第四节；落盘 `chapters/_meta/第NN章-改写方案.md`）：\n'
            + '\n'.join('      · ' + _q for _q in rewrite_plan_bad) +
            '\n      → 硬层只查三件**机器可验**的事：**文件在不在 / 字段全不全 / '
            '改前 ≠ 改后**（防空改）。\n'
            '        「为什么更好**是否具体**」「单元**选得对不对**」机器判不了，'
            '**不许写正则去猜** —— 猜出来会逼出"表演式改写"（理由写得像样、文本没变好）。\n'
            '      → 补法：最小格式（字段名一字不许改，单元只认 U1–U6）：\n'
            '           `# 第 01 章 改写方案`\n'
            '           `| # | 缺陷项 | 单元 | 定位 | 改成什么 | 会影响 |`\n'
            '           `### #1`\n'
            '           `- 改前：…` / `- 改后：…` / `- 为什么更好：…`\n'
            '      → 三行对照是"三行一组、一条不能少"：**改了标点/空格不算改动**'
            '（归一化只保留汉字/字母/数字）。'
        )
    if _no_plan_rework:
        # ★ v7.3.0 硬层 B。为什么敢硬：`retryCount >= 1` 是**显式填写**的、
        #   **无假阳性**（见 `parse_reworked_chapters` 的 docstring）。
        #   ⚠️ 只查**存在性** —— 表写得好不好由**独立质检子代理四问**判，脚本判不了。
        blockers.append(
            '【返工过却没有改写方案表】这一批里有章**返工过**（`%s` 里机器写入的'
            '「返工轮次」≥1），但 `chapters/_meta/` 下**没有改写方案表**：\n'
            % WORKORDER_FILE
            + '\n'.join('      · ' + _x for _x in _rework_rows) +
            '\n      → 返工过 = 这一章被改过；**没有方案表 = 是"边改边测"（打地鼠），'
            '而不是"改前先出方案、一次改完"**。\n'
            '       14 项指标是耦合的（补长句会把明喻挤上去、补数字那一轮情绪词归零），'
            '打地鼠会把上一轮刚改好的地方再挤坏一次 —— 所以"改前先把耦合算进表里"'
            '是工序，不是形式。\n'
            '      → 落盘：`chapters/_meta/第NN章-' + REWRITE_PLAN_TAG + '.md`'
            '（格式见 `guides/rewrite-units.md` 第四节），'
            '最小格式（字段名一字不许改，单元只认 U1–U6）：\n'
            '           `# 第 NN 章 改写方案`\n'
            '           `| # | 缺陷项 | 单元 | 定位 | 改成什么 | 会影响 |`\n'
            '           `### #1`\n'
            '           `- 改前：…` / `- 改后：…` / `- 为什么更好：…`\n'
            '      → ⚠️ 本闸门**只查"有没有交出这张表"**；"表写得好不好"'
            '（为什么更好是否具体、单元选得对不对）**机器判不了**，'
            '由**独立质检子代理四问**判 —— 见 `guides/deai-entry.md` 第 4 步。'
        )
    if boundary_fail and not waived:
        blockers.append(
            '【章节边界有悬空钩子】check_continuity.py 退出码 1 —— 逐条复查：'
            '"该接没接"（补一句桥接）还是"刻意留后章"（在细纲标注回收章）。\n'
            '      → 复查后若判定是刻意留白，在 05-创作台账.md 写一行放行记录，'
            '**必须写具体章号**（照抄文档里的 N/M 占位符不算）：\n'
            '         boundary-waived: 第8→9章，理由：该钩子第11章回收'
        )
    if waiver_problems:
        blockers.append(
            '【放行记录无效】05-创作台账.md 里有形似 boundary-waived 但**不成立**的记录：\n'
            + '\n'.join('      · ' + p for p in waiver_problems) +
            '\n      → 放行必须指名"第X→Y章"（X、Y 是真实数字、且相邻）+ 理由 ≥8 字。\n'
            '      → 文档里的 `第N→N+1章` 是**示例模板**，抄进台账等于写了一条假放行。'
        )
    if redline_hit:
        blockers.append(
            '【崩坏红线 1】' + redline_note + '\n'
            '      → 触发即停（execution-contract.md 第六节五拍处置）：停 / 名 / 锚 / 新计划 / 记。\n'
            '        不要把第 4 章也写成同一型——换型比改文便宜。'
        )

    if args.json:
        print(json.dumps({
            'completed': completed, 'watermark': watermark,
            'out_of_order': out_of_order, 'wordcount_fail': nopass,
            'missing_qc': missing_qc, 'ledger_lag': ledger_lag,
            'ledger_field_missing': ledger_field_missing, 'soft_hints': soft_hints,
            'concrete_list_missing': concrete_list_missing,
            'plot_card_missing': plot_card_missing,
            # ★ v7.2.0 = 硬层 A（格式）+ 软提示；★ v7.3.0 = 再加**硬层 B**
            #   （返工过却没出表 → 不再只是提示）+ 降级留痕 + 未知轮次留痕
            'rewrite_plan_bad': rewrite_plan_bad,
            'rewrite_plan_rework_without_plan': _no_plan_rework,
            'rewrite_plan_check_skipped': _wo_skip_note,
            'retry_count_unknown': _retry_unknown_note,
            'boundary_fail': boundary_fail,
            'boundary_waived': waived, 'boundary_waive_reason': waiver_note,
            'redline1_hit': redline_hit, 'redline1_note': redline_note,
            # ★ v7.4.0「跳过必须留痕」：01-大纲 红线 1 未参与检查的行/表，
            #   逐条点名（章号 / 行号 / 原因）—— "该查的行被静默跳过、而报告全绿"不许再发生。
            'redline1_skipped': redline_skips,
            'costMode': cost_mode,
            'pass': not blockers,
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if not blockers else 1)

    print('=' * 62)
    print('批次验收闸门')
    print('=' * 62)
    print(f'项目：{root.name}')
    print(f'费用模式：{cost_mode} ｜ 已完成：第 {completed} 章 ｜ 连续水位线：第 {watermark} 章')
    # ⚠️ 提示类信息放在标题**之后**（此前在标题之前，输出像"顶部有两行孤立文字"）
    if scaffold_hits:
        print()
        print('  [脚手架] ⚠ 正文文件里含工程字段 —— 盲评实测这是**最强的 AI 指纹**：')
        for _h in scaffold_hits[:5]:
            print('     · %s' % _h)
        print('     → 移入 chapters/_meta/<章名>.meta.md，正文文件只留 `# 标题 + 章首引子 + 正文`')
    if waived:
        print(f'  [已放行] 章节边界：{waiver_note}')
    if soft_hints:
        print('  [字段提示] 以下**不阻塞**，但会慢慢漏（2026-10-02 的字段级体检）：')
        for _h in soft_hints[:4]:
            print('     · %s' % _h)
    if redline_note:
        print(f'  [红线1] {redline_note}')
    # ★ 跳过必须留痕：逐条点名，"看不见"才是事故（不阻塞 —— 行在表外可能是合法的）。
    if redline_skips:
        print(f'  [红线1·未检查] 01-大纲 有 {len(redline_skips)} 行/表**未参与**「章末型 / 开场类型」'
              f'连续性检查（不是"检查通过"，是"没查"）：')
        for _s in redline_skips[:12]:
            print(f'     · {_s}')
        if len(redline_skips) > 12:
            print(f'     · …另有 {len(redline_skips) - 12} 条（同上，常见成因是裸 `|` 未转义）')
        print('     → 修法：① 表格里的竖线写成 `\\|`（或改掉用「｜」全角）；'
              '② 每张表都要有含「章末型」「开场类型」两列的表头。')
    print()

    if not blockers:
        # ⚠️ 2026-09-16 修：原实现用 `max(nums)+1`（nums = 本批**计划**的全部章号），
        # 于是「只完成第 1 章、本批计划 1–10」时会提示"下一章：第 11 章"——照做会**跳过 2–10 章**。
        # 真实事故：《我不是大师》第 01 章完成、02–10 pending，闸门却建议从第 11 章开始。
        # 正确语义：下一章 = 连续水位线 + 1；并区分"本批进行中"与"本批已收尾可派下一批"。
        _last = max(nums) if nums else 0
        _first = min(nums) if nums else 1
        if nums and watermark >= _last:
            print('✓ 闸门通过 —— 本批（第 %d–%d 章）已收尾，可以派发下一批（下一章：第 %d 章）'
                  % (_first, _last, _last + 1))
        else:
            _next = (watermark + 1) if watermark else _first
            _rest = [n for n in nums if n > watermark] if nums else []
            print('✓ 闸门通过 —— 本批**进行中**：下一章应写「第 %d 章」（本批第 %d–%d 章，还剩 %d 章未写）'
                  % (_next, _first, _last, len(_rest)))
        sys.exit(0)

    print(f'✗ 闸门未通过（{len(blockers)} 项阻塞）—— 不许派发下一批：\n')
    for b in blockers:
        print('  •', b)
    print()
    print('  提示：闸门存在的意义是"把前批收尾从主编自觉变成可验证清单"。')
    print('        台账/质检档案停更、章节乱序完成 —— 都是主编在长跑中漂移的信号。')
    sys.exit(1)


if __name__ == '__main__':
    main()
