#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
任务包编译器（J-Novel 新增，2026-09-23）
=========================================

**为什么需要它**：任务包此前由**主编手工装配**（十项）。手工装配有三个必然缺陷，
而且每一个都在实测中发生过：

| 缺陷 | 实测 |
|---|---|
| **槽位没有 schema** → 少填一个没人知道 | 「锚点滚动」「句法目标表」写了却从未进包（漏了几个月） |
| **槽位没有预算** → 有的一塞 3000 字 | 实测**每次调用平均重发 149,675 tokens 上下文**（中位） |
| **脚本能拿到的东西却靠人记** → 必然漏 | 上章结尾、契约、锚，本来都能从文件里自动抠 |

**所以本脚本做三件事**：
    ① **装配**：脚本能拿到的（上章真实结尾 / 契约 / 批次全局三件套 / 章号奇偶字数）
       全部**自动填好**；只有需要理解才能产生的（世界设定包 / 作者画面 / 已知病灶 / 诊断句）
       留成**显式待填标记**。**脚本不假装自己能写世界设定包。**
    ② **预算**：每个槽位有字数上限，总包有总上限 → 这是把"每次调用的上下文"
       从十几万压下来的**唯一机械手段**（成本 ≈ 调用次数 × 上下文）。
    ③ **校验**：`--check` 检查必填齐备 + 未超预算 + 没有"待主编填"残留。
       **缺槽位 = 拒绝开工** —— 把"记得填"变成"编译不过"。

★ 槽位总数 = **17**（2026-10-09 起）。以 `len(SLOTS)` 为准，下面的报告都是动态算的；
  这一行只是为了让"17"这个数字在文件里可被 grep 到（此前只在运行时输出里出现）。
  最近一次新增是 `best_of_book`（**本书自己的好段落**，治"**改写目标是指标、不是样例**"——
  Agent 只会做"统计意义上的最小改动"，指标达标而文本没变好；素材源 `细纲/_最佳段落库.md`，
  **零采集成本**，见 guides/rewrite-units.md）。
  上一次新增是 `additive_menu` / `human_exemplar`（**加法层**：前者治"子代理拿不到
  '往里加什么'"，后者治"**文档要求了、编译器却从未产出**"（v6.6.0 同型）；
  素材源分别是 guides/quick-reference-card.md【六】与 guides/human-exemplars.md）。
  再上一轮新增是 `concrete_list`（本体层/具体性闸门，见 guides/specificity-gate.md）。

用法:
  python make_task_package.py <项目> --chapter 12            # 打印骨架
  python make_task_package.py <项目> --chapter 12 --write    # 写到 任务包/第12章-任务包.md
  python make_task_package.py <项目> --check <任务包.md>      # 校验单份
  python make_task_package.py <项目> --check --all           # 批闸：校验全部
退出码: 0 = 齐备且不超预算；1 = 缺槽位 / 超预算 / 待填未填；2 = 取不到源（fail-closed）
"""

import argparse
import io
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _shared import (CONCRETE_HEAD, ensure_utf8_stdio,  # noqa: E402
                     find_chapter_files, find_meta_file, parse_concrete_list,
                     read_text)

# ★ 必须在 import 同族脚本之前调用（它们也会调，但幂等）
ensure_utf8_stdio()

from check_contract import (find_outline_files, parse_contract,  # noqa: E402
                            split_chapters)
from make_handoff import _paras, _tail                     # noqa: E402

PKG_DIR = '任务包'

# ── 槽位表 ────────────────────────────────────────────────────────────
# (键, 标题, 预算字数, 必填, 谁来填)
# 预算依据：成本 ≈ 调用次数 × **每次调用带多重的上下文**。
# 实测中位 149,675 tokens/次 —— 任务包是"被乘数"里可以机械约束的那部分。
# CJK 约 1 token/字，故总量是可控的量级。
#
# ★ 槽位顺序 = **显著性设计**（2026-09-24 重排，见下方 rationale）。
#   开头放**硬约束**、结尾放**「照着写」的正向具体素材**；参考性内容放中段。
#
#   为什么重排：论文自陈的限制是「**长上下文上的稀疏检索**可能退化」，而我们的调用
#   在 149,675 tokens/次 —— 正落在这个区间。所以**不能靠"多给"取胜，只能靠"给对位置"**。
#   重排前的顺序让 `lesions`（负向"别犯X"）落在最后一位，即**最高显著性位置**：
#   模型会被"别犯X"主导 → 写出**保守、安全、平**的文字，而"平"正是 AI 味的另一种形态
#   （推向统计安全区 = 合格的均匀）。现在把**正向的具体素材**放到最后。
SLOTS = [
    # ── 前段：硬约束与负向提示（必须在动笔前就被"看到"）──────────
    ('meta', '本章元信息', 60, True, 'script'),
    ('contract', '接口契约（冻结）', 420, True, 'script'),
    # ★ `lesions` 从"可选且永不产出"改为**必填**（bug 修复，见 CHANGELOG v6.6.0）：
    #   它此前 required=False + 赋空串 → render() 静默跳过 → 校验器也不报。
    #   而"已知病灶不传递给写手"= 同一类缺陷逐章复发，正是前移该消掉的东西。
    #   没有病灶时写「无」——**强制做出一次判断**，比静默缺失好。
    ('lesions', '本批已知病灶（占位式指令：这里要放什么）', 220, True, 'editor'),
    # ── 中段：参考性事实 ─────────────────────────────────────
    ('ledger', '状态台账·本章出场人物条目', 620, True, 'editor'),
    ('world_pack', '世界设定包（按本章裁剪）', 820, True, 'editor'),
    ('used_devices', '本批已用过的开场 / 章末型（避开它们）', 220, True, 'script'),
    # ⚠️ 预算必须给**段落粒度**留余量：`_tail` 是按段往回拼到 ≥800 字，
    #    多带一段就会溢出（实测 882 > 820）。这种"超预算"是**度量口径**问题，
    #    不是包写胖了 —— 放宽容量的正确做法，而不是放宽判据。
    ('prev_tail', '上一章真实结尾', 900, True, 'script'),
    ('diagnosis', '诊断句（仅重写任务）', 90, False, 'editor'),
    # ── 后段：给"该怎么写"的当前书事实（最高显著性）──────────────
    # ★ `rhythm_base`（2026-09-24 新增）：节律占质检调用约 26%，是第二大返工源，
    #   而任务包里此前**没有任何节律槽位**。按"**给事实不给目标**"给已写章节的实测值：
    #   ❌「本章段落 CV 请达到 0.85」（可凑，项目实测过配额当目标被 hack）
    #   ✅「本书前 N 章实测：CV 0.81–0.94，均值 0.87」（无目标可凑）
    ('rhythm_base', '本书已写章节的节律实测（事实，非目标）', 200, True, 'script'),
    # ★ `quota_card`（2026-09-24 新增）：`subagent-brief.md` L146 要求
    #   「**逐字复制** `quick-reference-card.md` 的【零】整块，不要手抄、不要改数字」，
    #   L428 还有勾选项「成本配额卡已内联进任务包」—— **但此前编译器根本不产出它，
    #   实测任务包里"配额卡"出现 0 次**。它治的恰恰是字数+节律+词汇这三类（≈91% 返工）。
    #   逐字复制交给脚本做，比"要求人别手抄"可靠。
    # ★ 预算 1400 → 1900（2026-10-07，v7.0.0 修既有 BUG）：
    #   实测【零】节的配额卡代码块已有 **1835 字 > 1400** —— 也就是说**任何真实项目**
    #   跑 `--check` 都会报「`quota_card` 超预算」→ **拒绝开工**（真实项目实测复现）。
    #   成因：v6.9.0 把「平均句长」从"压住"栏移到"补足"栏（附分布型目标与手段）、
    #   把「明喻」移到区间项 —— 都是**必要的**修改，但卡长了约 430 字，
    #   而预算没跟着动（**改卡的人与改预算的人是两处**）。
    #   **为什么上调预算而不是删卡**：这个代码块是子代理唯一的"写的时候就满足"的配额权威，
    #   删它 = 退回"写完再修"，那正是 91% 返工的来源；而它是**一次性固定成本**
    #   （全书同一张卡）。+500 字 ≈ 上下文的 **0.33%**（相对 149,675 tokens/次），
    #   换掉的是"每一个项目都编译不过"。
    #   ⚠️ 正确顺序：**先看它该不该这么长，再看预算合不合理** —— 反过来做就会砍掉内容。
    ('quota_card', '成本配额卡（逐字复制自 quick-reference-card 零节）',
     1900, True, 'script'),
    ('batch_global', '批次全局三件套（文风基因 / 锚点示例 / 句法基线）',
     900, True, 'script'),
    # ★ `voice_preset`（2026-09-24 新增）：文档要求"**动笔前**定的三行"
    #   （具体坐标／叙述者立场／留白点），且"没写这三行 = 写前分析未完成"——
    #   但它此前散落在细纲里、无槽位、无校验。实测真实项目 10 章里只有 3 章有这个段，
    #   且**被填成了别的东西**（只有破折号预算+专名锚点，三行核心从未被写过）。
    #   单一事实源 = 细纲（写手本来就读它），这里由脚本抽取进包，**不另存一份**（防双载体漂移）。
    ('voice_preset', '写前声音预置（具体坐标 / 叙述者立场 / 留白点）',
     300, True, 'script'),
    # ★ `concrete_list`（v7.0.0 新增）：17 项硬指标**全是频率指标**（多长/多密/多少次），
    #   测的是"影子"；而"具体性"（这个细节是不是**只属于这本书**）这一层此前
    #   **只有判据、没有产出物、没有校验点**（速查卡【六】与 subagent-brief §15 都写了判据，
    #   但判据只是说明文字 = L4 弱层，读点靠运气）。
    #   · 生成点：动笔前在 `chapters/_meta/<章名>.meta.md` 的「实物清单」段定 3–8 项。
    #   · 这里**从 `_meta` 抽取**（不是让主编再手抄一份）= 单一事实源，防双载体漂移
    #     （配额卡那条"写在两处、数字还不一样"的坑，本项目踩过）。
    #   · 位置：与 `additive_menu` / `human_exemplar` / `author_shot` 同处**后段正向素材区**
    #     —— 它们都是"正向、具体、要照着写"的素材
    #     （不靠"多给"取胜，靠"给对位置"）。
    #     ⚠️ 2026-10-09：此前注释写的是"紧贴 author_shot"，后来两者之间插进了
    #     `additive_menu` 与 `human_exemplar` —— 所以措辞改成"同区"而不是"紧贴"。
    #     别按字面去数相邻，**顺序的意图是"落在后段"，不是"必须相邻"**。
    ('concrete_list', '本章实物清单（只属于这一章的东西）', 200, True, 'editor'),
    # ★ `additive_menu`（2026-10-09 新增）：**子代理拿不到"加法层"** —— 已核实的交付点缺口。
    #   速查卡【零】节的配额卡是**四栏**（补足／压住／区间／**加法**），但四栏回答的
    #   全是"**改成什么样**"（多长/多密/多少次），本质是**减法与频率**；
    #   而"**往里加什么**"（内容注入：私人细节 / 情绪体感化 / 主角犯错 / 句式随情绪变 /
    #   道具非功能化）此前**没有任何槽位承载**：
    #   · `low-cost-mode.md` 的「人味不降清单」把"**加法层 3–5 招**"列为**必留项**
    #     （理由写得很直白：「**只删 AI 味会写成白开水，加法决定上限**」）；
    #   · `subagent-brief.md` §15 也要求"一章挑 3–5 招"。
    #   但真正写字的那个执行者（子代理）在"必读文件"里拿到的是**速查卡的路径**，
    #   **路径不是内联** —— 而同一份文件自己写着「**不是给路径让子代理自己去读——
    #   子代理不会主动翻**」。于是加法层对执行者**结构上不可靠**。
    #   · 形态：**3–5 招，按本章偏低的项挑**（**不是逐条打卡**——逐条打卡＝同构模板）。
    #     与 `diagnosis` 正好相反：那是"别犯什么"（负向），这是"该加什么"（正向）。
    #   · 位置：**后段高显著区**，紧邻 `human_exemplar` / `author_shot` / `concrete_list`
    #     —— 它们同为"正向、具体、要照着写"的素材（显著性设计：负向提示在前、正向素材在后）。
    #   · 为什么**必填**：判据 =「**后期修它是否显著贵于前期**」—— 加法层缺了，稿子会退化成
    #     白开水，后期修要**重写句子**（贵）；且"本章偏哪几项"**本身就需要一次判断**。
    #     确实不适用时写一句显式说明，**不许留空**（强制做出一次判断 > 静默缺失）。
    ('additive_menu', '本章加法菜单（3–5 招，按偏低项挑）', 250, True, 'editor'),
    # ★ `human_exemplar`（2026-10-09 新增）：**文档要求了，但编译器从未产出** ——
    #   与 v6.6.0 修的 `lesions` / `author_shot`（「required=False + 赋空串 → 永不产出」）
    #   是**完全同型**的缺陷，只是成因更直白：`SLOTS` 里**根本没有这个槽位**
    #   （`grep "exemplar\|范本"` 在编译器里结果为空）。
    #   · 要求在白纸黑字处有两处：`SKILL.md` 动作 4 与 `phase3-writing.md` 清单 2，都是
    #     「**人类范本片段**（按"时刻"选 1–2 段，**给原文不给路径**）」。
    #   · 为什么"给原文不给路径"这条**必须由编译器落地**：模型有一个 n-gram 记忆模块，
    #     它检索的是**上下文里实际存在的字**。给路径 = 靠主编自觉手抄，而**手抄必漂**
    #     （本项目已在配额卡上实测过"写在两处、数字还不一样"）—— 单一事实源要由脚本抽，
    #     但**本槽位没有可抽的文件**（"按'时刻'挑哪 1–2 段"是一个判断，不是抽取），
    #     所以这里退而求其次：**必填 + 强制给原文 + 由 `--check` 拦住未填**。
    #   · 为什么**必填**：它是"喂什么就检索什么"的一层，缺了**没有事后脚本能补**
    #     （与 `author_shot` 同判据：它消除的缺陷——没人味——后期修要付多轮润色）。
    #     确实无适配时刻时写「本章无适配的范本时刻（已查 human-exemplars 目录）」，
    #     **不许留空**：强制做出一次判断，比静默缺失好。
    ('human_exemplar', '人类范本原文（按"时刻"挑 1–2 段，给原文不给路径）',
     300, True, 'editor'),
    # ★ `best_of_book`（2026-10-09 新增，v7.2.0 改写工程）：治"**改写目标是指标，不是样例**"。
    #   已核实的根因：改写阶段的问题**探得出来、但改得一般**——因为 Agent 拿到的"目标"
    #   是一串指标区间（"把平均句长提到 23–37"），于是它做的是**统计意义上的最小改动**：
    #   指标一进区间就收手，文本本身没变好（句级工具改变不了一段话的组织方式，
    #   见 guides/rewrite-units.md 0.5）。**缺的那个东西是"文本级的参照"**——
    #   不是"把句长提到 23"，是"长成这一段这个样子"。
    #   · 与 `human_exemplar` 的分工（**两个来源，都要有**）：
    #     `human_exemplar` = **外部**人类作者范本（拉开距离）；
    #     `best_of_book`   = **本书自己的**已定稿高分段落（更贴近"这一本的味道"）。
    #     后者还多一条：**零采集成本**——它已经躺在项目里了，此前却**从没被系统用过**。
    #   · 与 `batch_global`（文风基因／锚点示例）的区别：那是**文风代表段（全书统一 1 段）**，
    #     回答"这一本整体什么腔调"；本槽位是**按缺陷类型分类的手术样例**
    #     （`缺闲笔` / `章末落笔` / `对白声口` / `本体层具体性` / `叙述者立场`）——
    #     回答"**我手上这个病灶，本书里改好的那处长什么样**"。
    #     ⚠️ 分类键是**逐字接口契约**（跨 agent 共享，不许改）。
    #   · 素材源是 `细纲/_最佳段落库.md`（`_` 开头，已被 `find_outline_files` 排除，安全）。
    #     但"挑哪几段"是**判断不是抽取**（要按本章病灶选），所以这里不给 TODO 之外的兜底：
    #     与 `additive_menu` / `human_exemplar` 同一个既定模式。
    #   · 为什么**必填**：判据同 `human_exemplar` —— 它消除的缺陷（"改得一般"）**后期修要付
    #     多轮重写**（贵），且"拿指标当目标"是**结构性**的（每次调用都会复发）。
    #     确实不适用时写显式说明（例：「本章是全书写完前的第 1 章，尚无已定稿章可挑样例」），
    #     **不许留空**——强制做出一次判断 > 静默缺失。
    #   · 位置：**紧接 `human_exemplar`、在 `author_shot` 之前**——同属**后段正向素材区**
    #     （显著性设计：负向提示在前、正向具体素材在后）。
    ('best_of_book', '本书最佳段落（按缺陷类型挑的手术样例）',
     350, True, 'editor'),
    # ★ `author_shot`（2026-09-24 修复）：此前 required=False + 赋空串 → **永不产出**。
    #   而 `low-cost-mode.md` 把它列为**必留项**，定位是
    #   「**流水线上最保人味的素材——作者自己的眼睛**」。
    #   判据：它消除的缺陷（人味不足）**后期修要付多轮润色**，属于"后期修显著贵于前期"
    #   → 按这个判据它**必须是必填**，不该是"（若有）"。
    #   放在最后一位：它是**正向、具体、要照着写**的素材，给最高显著性。
    ('author_shot', '作者画面（脚手架原话）', 200, True, 'editor'),
]
# ⚠️ 从 5000 提到 6000 的理由（2026-09-24）：
#   新增的 `quota_card` 是**一次性固定成本**（全书同一张卡，不随章数增长），
#   而它压制的是占返工 ≈91% 的三类（字数 52% / 节律 26% / 词汇 13%）。
#   按量级算：多带 ≈1900 字 ≈ **1.3%** 的上下文体积（相对 149,675 tokens/次），
#   换掉的是**每一轮返工 149,675 tokens**。论文侧的顾虑（长上下文稀疏检索）在这个
#   增量下可忽略；**但这也意味着其余槽位必须继续收紧**，总量不再上调。
#   （2026-10-07：`quota_card` 自身预算由 1400 上调到 1900 —— 它实际已 1835 字。
#     总量仍锁 6000：实测一份填满的任务包约 4.3–4.6k 字，**余量是留给主编填槽位的**，
#     不许再靠上调总量来解决"某个槽位超预算"。）
TOTAL_BUDGET = 6000
TODO = '【待填'
SLOT_OPEN = '<!-- slot:{key} budget={budget} required={req} -->'
SLOT_CLOSE = '<!-- /slot:{key} -->'
_PKG_CHAPTER = re.compile(r'第\s*(\d{1,4})\s*章')


def _zishu(s: str) -> int:
    """字数（CJK 逐字计，非 CJK 按 3.5 字符折 1 —— 与 token 估算口径一致）。"""
    cjk = len(re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', s))
    return int(cjk + (len(s) - cjk) / 3.5)


def _section(text: str, head_kw: str, sub_kws=(), max_level=3):
    """抠出一节内容：先找含 head_kw 的 2 级标题，再在其中挑含 sub_kws 的子节。

    ⚠️ 为什么不整段搬："批次全局信息"整节可能上千字，而一个子代理只需要
       **文风基因 + 锚点示例 + 句法基线**这三样 —— **只给该给的**。
    """
    m = re.search(r'(?m)^#{1,2}[^\n]*' + re.escape(head_kw) + r'[^\n]*$', text)
    if not m:
        return ''
    rest = text[m.end():]
    nxt = re.search(r'(?m)^#{1,2}\s', rest)
    sec = rest[:nxt.start()] if nxt else rest
    if not sub_kws:
        return sec.strip()
    out = []
    for sub in re.finditer(r'(?m)^#{3,4}\s*([^\n]+)$', sec):
        if not any(k in sub.group(1) for k in sub_kws):
            continue
        nxt2 = re.search(r'(?m)^#{3,4}\s', sec[sub.end():])
        body = sec[sub.end():sub.end() + (nxt2.start() if nxt2 else len(sec))]
        out.append(f'**{sub.group(1).strip()}**\n{body.strip()}')
    return '\n\n'.join(out)


def _rhythm_base(proj: Path, chapter_no: int) -> str:
    """本书已写章节的节律实测（**给事实不给目标**）。

    为什么形态必须是"事实"而不是"目标"：项目实测过"底线当目标、配额当目标"被 hack ——
    给一个数字，模型就会去凑那个数字。给"已经发生过什么"，**没有可凑的对象**。
    所以下面明确写出"偏离是允许的"。
    """
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from check_aistyle import analyze_chapter
    except Exception as e:                                  # noqa: BLE001
        return f'{TODO}：载入节律指标失败（{e}）】'
    rows = []
    for f in find_chapter_files(proj):
        m = re.search(r'第\s*(\d{1,4})\s*章', f.name)
        if not m:
            continue
        no = int(m.group(1))
        if no >= chapter_no:
            continue
        try:
            r = analyze_chapter(f)
        except Exception:                                   # noqa: BLE001
            r = None
        if r:
            rows.append((no, r))
    if not rows:
        return '（本书首章，尚无历史基线——本章是定调章；写完后它就成为后续章的基线）'

    def _span(key):
        vs = [float(r.get(key, 0) or 0) for _, r in rows]
        return min(vs), max(vs), statistics.median(vs)

    metrics = (('para_cv', '段落节律 CV', '{}'),
               ('dialog_ratio', '对话占比', '{}'),
               ('simile_density', '明喻/千字', '{}'),
               ('emotion_density', '情绪词/千字', '{}'))
    lines = [f'本书已写 {len(rows)} 章实测（**事实，不是目标；偏离允许——只要你清楚自己在偏**）：']
    for key, label, fmt in metrics:
        lo, hi, med = _span(key)
        lines.append(f'- {label} {fmt.format(round(lo, 2))}–{fmt.format(round(hi, 2))}'
                     f'｜均值 {fmt.format(round(med, 2))}'
                     f'｜第{rows[-1][0]}章 {fmt.format(float(rows[-1][1].get(key, 0) or 0))}')
    return '\n'.join(lines)


def _quota_card() -> str:
    """逐字抽取「成本配额卡」（`quick-reference-card.md` 的【零】节）。

    ⚠️ 为什么由脚本抽而不是让主编贴：`subagent-brief.md` 要求"**逐字复制……不要手抄、
       不要改数字**"—— 而"要求人不许手抄"是个靠自觉的约束；**交给脚本就不可能抄错**。
    """
    p = Path(__file__).parent.parent / 'references' / 'guides' / 'quick-reference-card.md'
    if not p.is_file():
        return f'{TODO}：找不到 references/guides/quick-reference-card.md】'
    text = read_text(p)
    sec = re.search(r'(?ms)^##\s*零、.*?(?=^##\s)', text)
    body = sec.group(0) if sec else text
    m = re.search(r'(?ms)^```.*?^```', body)
    if not m or '【一、' not in m.group(0):
        return f'{TODO}：速查卡【零】节里找不到配额卡代码块（四栏：补足/压住/区间/加法）】'
    return ('> 逐字复制自 `references/guides/quick-reference-card.md`【零】节（脚本抽取）。\n'
            '> **写的时候就满足，不要等事后来修。**\n\n' + m.group(0).strip())


VOICE_KEYS = ('具体坐标', '叙述者立场', '留白点')


def _voice_preset(ol_chaps: dict, chapter_no: int):
    """从细纲抽「写前声音预置」段。返回 (文本, 缺的键)。

    **单一事实源 = 细纲**（写手本来就读它）—— 这里只做抽取进包，不另存一份，
    避免"双载体同名必漂"（伏笔表踩过这个坑）。
    """
    seg = ol_chaps.get(chapter_no, '') or ''
    m = re.search(r'(?m)^#{2,4}\s*(?:写前)?声音预置[^\n]*$', seg)
    if not m:
        return (f'{TODO}：细纲第 {chapter_no} 章没有「写前声音预置」段'
                f'（三行：具体坐标 / 叙述者立场 / 留白点）】'), list(VOICE_KEYS)
    rest = seg[m.end():]
    nxt = re.search(r'(?m)^#{2,4}\s', rest)
    body = (rest[:nxt.start()] if nxt else rest).strip()
    if not body:
        return f'{TODO}：细纲的「写前声音预置」段是空的】', list(VOICE_KEYS)
    lack = [k for k in VOICE_KEYS if k not in body]
    return body, lack


# ── 实物清单（本体层 / 具体性闸门，v7.0.0）────────────────────────────
# 落盘位置是**接口契约**（跨 agent 共享，不许改）：
#   `chapters/_meta/第XX章-<标题>.meta.md` 的「## 实物清单」段。
# ⚠️ **绝不能放进正文文件** `chapters/第XX章-<标题>.md` —— 2026-09-19 已确立
#   "正文文件严禁任何工程字段"并有守卫盯着（真人盲评里两位评委各自把外挂元数据头部
#   列为最强 AI 指纹）。`_meta/` 文件才是工程字段的合法容器。
# ★ 2026-10-09 v7.1.1：`CONCRETE_HEAD` / `find_meta_file` / `parse_concrete_list` 已
#   **收归 `_shared`**（此前 `parse_concrete_list` 3 份、`find_meta_file` 2 份）。
#   这里只留 `_concrete_list` —— 本脚本专有的"清单 → 任务包槽位文本"这一步。

def _concrete_list(proj: Path, chapter_no: int) -> str:
    """抽「实物清单」并拼成给写手看的形式。

    ★ 抽不到时返回 `TODO` 标记 —— 这样 `--check` 会报"待填残留 → 拒绝开工"，
      把"记得填"变成"编译不过"（与 `lesions` / `author_shot` 同一个既定模式）。
      **这是刻意的**：本体层没有脚本判据（见 guides/specificity-gate.md 第三节），
      所以"清单有没有定"这件事只能靠**编译期强制**，不能靠运行时检测。
    """
    p = find_meta_file(proj, chapter_no)
    if p is None:
        return (f'{TODO}：找不到 chapters/_meta/第{chapter_no:02d}章-<标题>.meta.md'
                f'（实物清单的落盘位置；格式见 guides/specificity-gate.md）】')
    items = parse_concrete_list(read_text(p))
    if not items:
        return (f'{TODO}：{p.name} 里没有「## {CONCRETE_HEAD}」段，或一个条目都没有'
                f'（本章 3–8 项，两项判据自检：只属于这一章 / 五感可验证；'
                f'格式见 guides/specificity-gate.md）】')
    return '\n'.join(
        ['本章实物清单（动笔前定，写的时候至少用上 3 项）：']
        + [f'· {it}' for it in items]
        + ['', '判据：每一项搬到别章都不成立。三条硬约束见 guides/specificity-gate.md'])


def _word_target(proj: Path) -> str:
    for n in ('02-写作计划.json', '写作计划.json'):
        p = proj / n
        if p.is_file():
            try:
                d = json.loads(read_text(p))
                v = d.get('minWordsPerChapter') or d.get('wordsPerChapter')
                if v:
                    return f'目标 {v} 字'
            except Exception:
                pass
    return '目标字数：见 02-写作计划.json'


def build(proj: Path, chapter_no: int, rewrite: bool = False):
    """返回 {slot_key: 内容}；脚本填不了的槽位返回 TODO 标记。

    `rewrite=True` 时产出 `diagnosis` 槽位（改写任务必须带诊断句——没有它，
    改写等于**重抽一次彩票**）。
    """
    fills = {}

    files = [str(p) for p in find_chapter_files(proj)]
    outlines = find_outline_files(proj)
    ol_text = read_text(outlines[0]) if outlines else ''
    ol_chaps = split_chapters(ol_text) if ol_text else {}

    # ① 批次全局三件套（只抠该给的三样）
    g = _section(ol_text, '批次全局', ('文风基因', '锚点示例', '句法'))
    fills['batch_global'] = g or f'{TODO}：细纲里没有「批次全局信息」段（文风基因/锚点示例/句法基线）】'

    # ② 接口契约（本章）
    c = parse_contract(ol_chaps.get(chapter_no, ''))
    if c:
        keys = ['进入·位置时间', '进入·情绪', '进入·已知', '进入·身体',
                '退出·位置时间', '退出·情绪', '退出·已知', '退出·身体',
                '承接要素', '交付钩子']
        fills['contract'] = '\n'.join(
            f'- {k}: {c.get(k, "").strip() or "（空）"}' for k in keys)
    else:
        fills['contract'] = (f'{TODO}：细纲第 {chapter_no} 章没有「接口契约」块】\n'
                             f'格式见 phase2-planning.md 细纲规格；跑 '
                             f'`check_contract.py <项目>` 可查')

    # ②b 本批已用过的开场 / 章末型 —— **前馈：给事实，不给目标**
    #     「跨章同质」现在是"写完由 check_repetition 报警"，属于反馈（= 多一轮 × 15万 token）。
    #     把"已经用过什么"提前写进任务包，就从反馈变成了前馈，而成本只有几行字。
    #     ⚠️ 只陈述事实（"已用过甲、丙"），**不写"本章请用乙"** —— 后者会变成可凑的目标。
    def _clean(s: str) -> str:
        s = re.sub(r'\*+', '', s).strip()
        return re.split(r'[（(，,。；;]', s)[0].strip()[:16]

    used = []
    for k in sorted(n for n in ol_chaps if n < chapter_no):
        seg = ol_chaps[k]
        o = re.search(r'(?:开场(?:方式|类型)|本章开头方式|开头方式)\**\s*[:：]\s*([^\n|]{1,40})', seg)
        e = re.search(r'章末型[^\n:：]*[:：]\s*([^\n|]{1,40})', seg)
        bits = ([f'开场 {_clean(o.group(1))}'] if o else []) + \
               ([f'章末 {_clean(e.group(1))}'] if e else [])
        if bits:
            used.append(f'第{k}章：' + ' / '.join(bits))
    fills['used_devices'] = (
        ('本批（截至上一章）**已经用过**的手法——本章请避开同型：\n'
         + '\n'.join('- ' + u for u in used[-8:])
         + '\n\n（这是**已发生的事实**，不是指标；照抄或替换都不影响，只用于避开重复。）')
        if used else '（本批尚无已定稿章，无历史手法可避开）')

    # ③ 上一章真实结尾（从正文裁，与交接卡同源）
    # ⚠️ 修复（v7.0.0）：这一行原本是
    #     `# ③ 上一章真实结尾（从正文裁，与交接卡同源）    prev = ''`
    #   —— `prev = ''` **被吞进了注释**。于是当 `idx is None`（= 编译第 1 章，
    #   没有任何"上一章"）时，`prev` 从没被赋值 → 下面直接
    #   `UnboundLocalError: cannot access local variable 'prev'` → **每本书的第 1 章
    #   都编译不出来**。这不是"少了个默认值"，是"注释吃掉了代码"这一类问题的第 N 次。
    prev = ''
    idx = next((i for i, f in enumerate(files)
                if (m := _PKG_CHAPTER.search(Path(f).name))
                and int(m.group(1)) == chapter_no - 1), None)
    if idx is not None:
        try:
            prev = _tail(_paras(read_text(files[idx])), 800)
        except Exception:
            prev = ''
    fills['prev_tail'] = prev or ('（本书首章，无上章）' if chapter_no <= 1 else
                                  f'{TODO}：找不到第 {chapter_no - 1} 章正文】')

    # ④ 元信息
    fills['meta'] = (f'章号 {chapter_no}（{"奇数" if chapter_no % 2 else "偶数"}）｜'
                     f'{_word_target(proj)}')

    # ⑤ 需要理解才能产生的 —— 显式留空，**不假装脚本能写**
    fills['ledger'] = f'{TODO}：从 03-状态台账.md 抄本章出场人物条目（每条带三态 ✓/?/✗）】'
    fills['world_pack'] = f'{TODO}：按本章裁剪 500–800 字（读圣经「世界观手册」）】'
    # ★ 修复：这三个此前**被赋空串 + required=False**，于是 render() 静默跳过、
    #   check_one() 也静默通过 —— 实测生成物里它们出现 **0 次**。
    #   现在：`author_shot` 与 `lesions` 改为必填（给 TODO 标记，主编看得见、校验拦得住）；
    #   `diagnosis` 只在 `--rewrite` 时产出（新写任务里它本来就不适用）。
    fills['author_shot'] = (f'{TODO}：从 07-剧情脚手架.md 原样抄作者本人的原话画面】'
                            '（若本章脚手架确无条目，写「本章无脚手架条目」——'
                            '但**不要留空**：它是"最保人味的素材"）')
    # ★ 实物清单（v7.0.0）：脚本可自填——从本章的 `_meta` 文件抽，**不用主编手抄**。
    #   抽不到 → TODO → `--check` 拦住（本体层无脚本判据，只能靠编译期强制）。
    fills['concrete_list'] = _concrete_list(proj, chapter_no)
    # ★ 加法层两槽（2026-10-09）：**脚本拿不到**（"按本章偏低的项挑哪几招""按'时刻'挑哪
    #   1–2 段范本"都是判断，不是抽取）→ 给 TODO 标记，由 `--check` 拦住未填的包。
    #   与 `lesions` / `author_shot` 同一个既定模式：**把"记得填"变成"编译不过"**。
    fills['additive_menu'] = (
        f'{TODO}：按本章**偏低的项**挑 3–5 招加法（不是逐条打卡——逐条打卡＝同构模板）。'
        '候选见速查卡【六】内容注入 / subagent-brief §15：私人细节 / 情绪体感化 / '
        '主角犯错 / 句式随情绪变 / 道具非功能化】')
    fills['human_exemplar'] = (
        f'{TODO}：按"时刻"从 references/guides/human-exemplars.md 选 1–2 段，**抄原文**'
        '（**给原文不给路径**——模型放什么就检索什么）。'
        '本章确无适配时刻时写「本章无适配的范本时刻（已查 human-exemplars 目录）」——'
        '但**不要留空**】')
    # ★ 本书最佳段落（v7.2.0）：**"挑哪几段"是判断不是抽取** → 与加法层两槽同一模式。
    #   治的是"**改写目标是指标，不是样例**"：给 Agent 一个**文本级的参照**（"长成这一段
    #   这个样子"），而不是一串可凑的指标区间（"把句长提到 23"）。
    #   与 `human_exemplar` 是**两个来源**：那是**外部**人类范本（拉开距离），
    #   这是**本书自己的**已定稿好段落（更贴近"这一本的味道"，且**零采集成本**）。
    #   ⚠️ 分类键 `缺闲笔`/`章末落笔`/`对白声口`/`本体层具体性`/`叙述者立场` 是**逐字
    #     接口契约**（跨 agent 共享），写进提示里是为了让主编**按缺陷类型**挑，不是随手抄一段。
    fills['best_of_book'] = (
        f'{TODO}：从 `细纲/_最佳段落库.md` 按**本章病灶的类型**挑 1–2 段，**抄原文**'
        '（分类键：缺闲笔 / 章末落笔 / 对白声口 / 本体层具体性 / 叙述者立场）。'
        '**给原文不给路径**——它是"本书自己的好段落"，比"把句长提到 23"这种指标更能'
        '定住"这一本的味道"。**改之前先读它，照着它的样子改**（单元与边界见 '
        'references/guides/rewrite-units.md）。'
        '尚无已定稿章可挑时，写显式说明（例：「本章是全书写完前的第 1 章，'
        '尚无已定稿章可挑样例」）——但**不要留空**】')
    fills['lesions'] = (f'{TODO}：本批真正出现过的 1–2 条病灶，**配占位式指令**'
                        '（"这里要放一个具体的东西"而不是"不要写泛泛的感慨"）；'
                        '没有则写「无」】')
    fills['diagnosis'] = (f'{TODO}：诊断：{{一句话}} / 本稿必须不再犯：{{一句话}}】'
                          if rewrite else '')
    # ★ 新增：脚本可自填的三项（0 reasoning token）
    fills['rhythm_base'] = _rhythm_base(proj, chapter_no)
    fills['quota_card'] = _quota_card()
    vp, lack = _voice_preset(ol_chaps, chapter_no)
    if lack and TODO not in vp:
        # 让问题在**编译时**就可见，而不是等 --check 才报
        vp += (f'\n\n> ⚠️ 缺三行核心：{"、".join(lack)}'
               f'——文档要求"没写这三行 = 写前分析未完成"。'
               f'**回到细纲补**（单一事实源在细纲，不在这里补）')
    fills['voice_preset'] = vp
    return fills


def render(proj: Path, chapter_no: int, fills: dict, rewrite: bool = False) -> str:
    parts = [f'# 第 {chapter_no} 章 · 任务包（由 make_task_package.py 编译）',
             '',
             f'> 本包由脚本编译：**脚本能拿到的已填好**，其余是**显式待填标记**。',
             f'> 任务类型：**{"改写（诊断句必填）" if rewrite else "新写"}**'
             f'｜预算上限：单槽位见标记，**总上限 {TOTAL_BUDGET} 字**。',
             f'> 校验：`python scripts/make_task_package.py "{proj}" '
             f'--check <本文件>`（缺槽位 / 超预算 / 待填残留 → 退出码 1，**拒绝开工**）',
             '',
             '> **槽位顺序是有意的**：开头放硬约束与"别犯什么"，'
             '结尾放**"该怎么写"的正向具体素材**（配额卡 / 文风基因 / 声音预置 / '
             '实物清单 / **加法菜单** / **范本原文** / **本书最佳段落** / 作者画面）'
             '—— 长上下文上的检索是近似的，**不能靠"多给"取胜，只能靠"给对位置"**。',
             '']
    for key, title, budget, req, who in SLOTS:
        # ★ 不再"空则跳过"：**所有槽位一律产出**。
        #   旧写法 `if not body and not req: continue` 让 3 个槽位**永远不出现在包里**，
        #   而校验器只在 required 时报错 → 静默通过（实测生成物里出现 0 次）。
        #   现在缺内容就写一句显式的"不适用"，使「槽位数 == SLOTS 数」恒成立。
        body = (fills.get(key, '') or '').strip() or '（不适用——本任务无此项）'
        parts.append(SLOT_OPEN.format(key=key, budget=budget, req=int(req)))
        parts.append(f'## {title}')
        parts.append('')
        parts.append(body)
        parts.append('')
        parts.append(SLOT_CLOSE.format(key=key))
        parts.append('')
    return '\n'.join(parts)


def parse_package(text: str):
    """把包解析成 {key: (内容, 预算, 必填)}。返回 (slots, total_chars)。"""
    slots = {}
    for m in re.finditer(r'<!-- slot:([a-z_]+) budget=(\d+) required=(\d) -->', text):
        key, budget, req = m.group(1), int(m.group(2)), m.group(3) == '1'
        end = text.find(SLOT_CLOSE.format(key=key), m.end())
        body = text[m.end():end if end > 0 else len(text)]
        body = re.sub(r'(?m)^##[^\n]*$', '', body, count=1).strip()
        slots[key] = (body, budget, req)
    return slots


def check_one(path: Path, brief=False) -> int:
    if not path.is_file():
        print(f'  ✗ 任务包不存在：{path}')
        return 1
    text = read_text(path)
    slots = parse_package(text)
    rew = '任务类型：**改写' in text or '任务类型：改写' in text
    probs = []
    absent_opt = []
    total = 0
    for key, title, budget, req, who in SLOTS:
        # `diagnosis` 只在改写任务里必填（新写任务里它本来就不适用）
        need = req or (key == 'diagnosis' and rew)
        if key not in slots:
            if need:
                probs.append(f'缺必填槽位 `{key}`（{title}）—— **拒绝开工**：'
                             f'子代理拿不到它就只能猜')
            else:
                absent_opt.append(key)
            continue
        body, _b, r = slots[key]
        n = _zishu(body)
        total += n
        if need and (not body or TODO in body):
            probs.append(f'槽位 `{key}` 还是待填状态：{body[:60]}')
        if n > budget:
            probs.append(f'槽位 `{key}` 超预算：{n} > {budget} 字 —— '
                         f'"被乘数"就是这么涨起来的（实测每次调用平均重发 14.9 万 tokens）')
        # 声音预置：三行核心必须齐（文档要求"没写这三行 = 写前分析未完成"）
        if key == 'voice_preset' and TODO not in body:
            lack = [k for k in VOICE_KEYS if k not in body]
            if lack:
                probs.append(f'槽位 `voice_preset` 缺三行核心：{"、".join(lack)}'
                             f'（实测真实项目 10 章里只有 3 章有这个段，且全都缺这三行）')
    if total > TOTAL_BUDGET:
        probs.append(f'**总包超预算**：{total} > {TOTAL_BUDGET} 字')
    if not brief:
        print(f'\n  {path.name}')
        print(f'    槽位 {len(slots)}/{len(SLOTS)}｜合计 {total} 字（上限 {TOTAL_BUDGET}）')
        if absent_opt:
            print(f'    ⚠ 缺可选槽位（不阻塞，但请确认是不是漏了）：'
                  f'{"、".join(absent_opt)}')
    for p in probs:
        print('    ✗ ' + p)
    return len(probs)


def main():
    ap = argparse.ArgumentParser(description='任务包编译器（装配 + 预算 + 校验）')
    ap.add_argument('project', help='项目目录')
    ap.add_argument('--chapter', type=int, help='章号')
    ap.add_argument('--write', action='store_true', help='写到 任务包/第NN章-任务包.md')
    ap.add_argument('--check', nargs='?', const='__ALL__', default=None,
                    metavar='任务包.md',
                    help='校验一份任务包；或 --check --all 校验全部')
    ap.add_argument('--all', action='store_true', help='与 --check 连用：校验全部')
    ap.add_argument('--brief', action='store_true', help='只输出问题（窗闸/批闸用）')
    ap.add_argument('--rewrite', action='store_true',
                    help='本任务是**改写**（产出 diagnosis 槽位；改写不带诊断句 = 重抽彩票）')
    args = ap.parse_args()

    proj = Path(args.project)
    if not proj.is_dir():
        print(f'[错误] 目录不存在：{args.project}')
        sys.exit(2)

    if args.check is not None:
        targets = []
        if args.check == '__ALL__' or args.all:
            d = proj / PKG_DIR
            targets = sorted(d.glob('*.md')) if d.is_dir() else []
            if not targets:
                print(f'[错误] {proj / PKG_DIR} 下没有任务包。')
                print('       这不等于"通过"——先跑 --write 生成，再校验。')
                sys.exit(2)
        else:
            targets = [Path(args.check)]
        print('=' * 60)
        print('任务包校验（缺槽位 = 拒绝开工；超预算 = "被乘数"失控）')
        print('=' * 60)
        bad = sum(check_one(t, args.brief) for t in targets)
        if args.brief:
            print(f'\n[低费用·批闸] {len(targets)} 个任务包｜问题 {bad}'
                  + (' → 合格' if not bad else ' → 不合格'))
        elif not bad:
            print(f'\n  ✓ {len(targets)} 个任务包齐备且未超预算')
        sys.exit(1 if bad else 0)

    if not args.chapter:
        ap.error('需要 --chapter N，或 --check')
    fills = build(proj, args.chapter, rewrite=args.rewrite)
    text = render(proj, args.chapter, fills, rewrite=args.rewrite)
    if args.write:
        out = proj / PKG_DIR
        out.mkdir(parents=True, exist_ok=True)
        p = out / f'第{args.chapter:02d}章-任务包.md'
        p.write_text(text, encoding='utf-8')
        n = _zishu(text)
        print(f'  ✓ {p.relative_to(proj)}  （{n} 字 / 上限 {TOTAL_BUDGET}）')
        todo = sum(1 for k, _t, _b, r, w in SLOTS if r and TODO in fills.get(k, ''))
        print(f'    待主编填：{todo} 个必填槽位 '
              f'（`--check` 会拦住未填的包，不会让它混到子代理手里）')
    else:
        print(text)
    sys.exit(0)


if __name__ == '__main__':
    main()
