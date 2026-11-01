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

★ 槽位总数 = **23**（2026-11-01 核对；v9.0 加到 22，v11.0 新增 `consequence_chain` → 23）。
  以 `len(SLOTS)` 为准，
  下面的报告都是动态算的；这一行只是为了让"18"这个数字在文件里可被 grep 到
  （此前只在运行时输出里出现）。
  最近一次新增是 `voice_charter`（**口吻宪法**，治"**我们对声音只有定义、没有样本**"——
  `voice_preset` 是抽象参数，口吻宪法是**可模仿的文本**；素材源 `00-故事圣经.md`
  的 `## 〇、口吻宪法`，见 guides/voice-charter.md）。
  上一次新增是 `best_of_book`（**本书自己的好段落**，治"**改写目标是指标、不是样例**"——
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
                     parse_exemplar_sections, read_text)

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
    # ★★ `promise_debt`（2026-10-26 v9.0）：**连贯的执行点**。
    #   此前 brief 只带 `prev_tail`（上一章真实结尾 800 字）——写手知道"刚才发生了什么"，
    #   但**不知道"读者在等什么"**。而读者追读的原因恰恰是后者。
    #   实测缺口：`03-状态台账` 管"世界有没有矛盾"，`伏笔表` 管"信息埋在哪收"，
    #   没有任何一处管"谁欠读者什么、欠了多久" → 「挖坑不填 / 越看越乱 / 赢了跟没赢一样」
    #   这三种读者抱怨，旧体系**一条都抓不到**（见 guides/promises.md 第零节）。
    #   位置：紧跟 `contract`（都是硬约束，必须在"怎么写"之前被看到）。
    ('promise_debt', '本章必须还的债（承诺账到期的条目）', 420, True, 'script'),
    # ★★ `story_ledger`（同上）：L1 故事层的执行点。与 `concrete_list` 同一份文件、
    #   同一个时点，但回答不同问题：那是"这一章有什么东西"，这是"这一章发生了什么事"。
    #   在它之前，写手只拿到前者 → 能写出"具体，但没发生任何事"的章节。
    ('story_ledger', '本章故事账（选择/代价/位移/可删性/欲望）', 500, True, 'script'),
    # ★★ `consequence_chain`（2026-11-01 v11.0）——L1 故事层的**第二根轴**。
    #   · **治什么**：模型"用代理条件代替后果推演"。实测（三次 POV，见 guides/consequence-chain.md）：
    #     盲盒 8/8 能复述规则、5/8 仍把封存砸在垃圾盒上（触发前只核对余额）；
    #     路口里事先点名"会闯红灯"的 6 辆车，一辆都没放行，全部卷进事故。
    #     写作里的同型物：**"符合人设／推进剧情／伏笔回收了／情绪词够了"全是代理条件。**
    #   · **为什么不能写成一句提醒**：`v3-prompt`（一句"请推演二阶后果"）实测分数 2226、
    #     砸盒率**不降**；`v2-harness`（把状态摆在眼前）1879，**更差**；
    #     `v4-belief`（让它脑内维护状态）1780，**全场最差** —— **脑内维护比不维护更糟**。
    #     唯一有效的是 `v1-clear`：**把隐含规则写成明文 + 给出一个它不知道自己有的选项**（2396，砸盒 5/8→1/8）。
    #     → 所以这里给的是**固定的五栏格式 + 三条可机械判的判据**，而不是形容词。
    #   · **为什么紧挨 `story_ledger`**：同文件、同时点、同一个 30 秒；
    #     但一个管"该不该存在"（点），一个管"会往哪儿长"（线）。**顺序按作用域从大到小**不适用这里，
    #     这里按"判断顺序"排：先立得住，再谈往哪儿长。
    ('consequence_chain', '本章后果链（动作/一阶/二阶/没看见的路/误判）',
     700, True, 'script'),
    # ★ `lesions` 从"可选且永不产出"改为**必填**（bug 修复，见 CHANGELOG v6.6.0）：
    #   它此前 required=False + 赋空串 → render() 静默跳过 → 校验器也不报。
    #   而"已知病灶不传递给写手"= 同一类缺陷逐章复发，正是前移该消掉的东西。
    #   没有病灶时写「无」——**强制做出一次判断**，比静默缺失好。
    ('lesions', '本批已知病灶（占位式指令：这里要放什么；脚本按缺陷帕累托自动生成）', 460, True, 'script'),
    # ── 中段：参考性事实 ─────────────────────────────────────
    ('ledger', '状态台账·本章出场人物条目（脚本自动按角色名匹配）', 900, True, 'script'),
    ('world_pack', '世界设定包（脚本按专名表/规则/场景卡自动裁剪）', 1000, True, 'script'),
    ('used_devices', '本批已用过的开场 / 章末型（避开它们）', 220, True, 'script'),
    # ⚠️ 预算必须给**段落粒度**留余量：`_tail` 是按段往回拼到 ≥800 字，
    #    多带一段就会溢出（实测 882 > 820）。这种"超预算"是**度量口径**问题，
    #    不是包写胖了 —— 放宽容量的正确做法，而不是放宽判据。
    ('prev_tail', '上一章真实结尾', 900, True, 'script'),
    ('diagnosis', '诊断句（仅重写任务）', 90, False, 'editor'),
    # ★ fix_recipe（2026-10-21）：按工单的缺陷类型，从 rewrite-playbook 抽对应节的核心配方。
    #   它补的是最后一层：报告只说"查哪一节"（索引），而那一节的内容还在文件里、要执行者
    #   自己去 Read —— 于是最常见的结局是它照 L1 的动作名即兴写，指标变绿但不是范本里那种写法。
    #   现在由编译器按缺陷项抽节，**内容直接进上下文**（给原文不给路径）。
    ('fix_recipe', '改写配方（按本章缺陷类型抽节，给原文不给路径）', 400, False, 'script'),
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
    # ★ `voice_charter`（2026-10-13 新增，v7.6.0「世界引擎」）：**我们对声音只有定义、
    #   没有样本** —— 实测《秘典大盗》规则书的成分表：声音层占 **29% 字数**，我们 **0**。
    #   · **治什么**：任务包里此前与"声音"有关的只有 `voice_preset`（具体坐标／叙述者立场／
    #     留白点）与 `batch_global`（文风基因／锚点示例／句法基线）——**全是抽象参数**
    #     （"叙述者立场：护短"）。而按模型机制：**抽象描述不激活语域，文本样本才激活**
    #     （本项目自己在《人类范本工艺逆向工程报告》里已证过：模型有 n-gram 记忆模块，
    #     **你放什么它就检索什么**）。→ 我们一直在告诉模型"要什么声音"，
    #     从来没给过它"那个声音"。本槽位给的是**一段 300–500 字的可模仿正文样本**
    #     （外加叙述者人格与语域禁令），是**文本**而不是参数。
    #   · **为什么必须常驻（每章都发）**：语域效应需要**整体在场**，切片会毁掉它 ——
    #     所以它**不能**混在厚圣经里被按章裁切（那是 `world_pack` 的机制）。
    #     实测代价：≈2000 字 ≈3000 tokens，占每次调用 ~149,675 tokens 的 **约 2%**。
    #   · **为什么必填而不是"有就发"**：判据 =「**后期修它是否显著贵于前期**」——
    #     声音缺失时每一章都在用**通用先验**补声音，而那正是"文笔很 AI"的直接来源；
    #     后期修要**重写句子**（贵）。确实没有样本时**不许留空**：写显式说明
    #     （「本书尚未定口吻宪法」），强制做出一次判断 > 静默缺失（同 `lesions` / `author_shot`）。
    #   · **为什么必须有硬预算 2000**：它是**常驻**的（每章都发），
    #     而它之后的槽位（`batch_global` / `voice_preset` / `world_pack` 之外的正向素材）
    #     是**按章变化**的 —— 不封顶就会把"这一章专属的素材"挤掉，
    #     即"一个全书不变的常量吃掉按章变化的变量"。2000 字同时也逼它**必须短**：
    #     `§ 0.3` 的判据是"短而常驻"，写长了本身就不合格。
    #   · **位置**：**正向素材区的最前面、`batch_global` 之前** —— 理由：**声音先于内容**。
    #     它管**每一句**（语域是全局的），而其他正向素材只管**这一章**
    #     （`batch_global` 是批次级、`world_pack`／`concrete_list` 是本章级）。
    #     显著性设计：负向提示在前、正向具体素材在后，而正向素材内部再按**作用域从大到小**排。
    #   · **防什么坑**：
    #     ① **防手抄**：由脚本从 `00-故事圣经.md` 的 `## 〇、口吻宪法` 段**抽取**，
    #        单一事实源 = 圣经（防双载体漂移——本项目已在配额卡上踩过"写在两处、数字还不一样"）。
    #     ② **防`--check` 静默放行**：抽不到 → 返回 `TODO` 标记（`【待填`）→
    #        `--check` 报"待填残留 → 拒绝开工"（本项目既定模式）。
    #     ③ **防贪心**：不设软性"建议 2000"，而是硬上限 —— 超了直接编译不过。
    #     ⚠️ 判据的边界：脚本只能判"**这段在不在、样本块够不够 300 字**"；
    #        "**这个声音好不好**"**不许脚本判**（那是质检子代理/人的活，见铁律一）。
    ('voice_charter', '口吻宪法（本书声音：人格 / 样本 / 禁令）',
     2000, True, 'script'),
    # ★★★ `craft_cards`（2026-10-26 v9.0）——**本轮最重要的一处架构改动**。
    #   它替掉的是"读指南"这个机制本身，而不是某一本指南。
    #
    #   · **为什么必须替掉**（这是算术，不是判断）：`references/guides/` 共 54 本 / 44.3 万字符
    #     ≈ 30 万 token；单章"该读的都读"去重 21 本 ≈ **15.2 万 token**。
    #     而 `execution-contract.md` 自己规定「每次调用平均重发 >100k = 纪律失守」、
    #     调用预算 **10–18 次/章** → **读点要求与成本纪律在数学上互相否定**。
    #     执行者只能二选一，而**成本有审计（audit_tokens.py 能查）、读点没有审计**，
    #     于是它选成本、跳过读点 —— **而且选得对**。
    #     这就是"改写阶段那些指南没被读取"反复复发、改了多遍仍无效的真因：
    #     **不是指令不够狠，是清单不可执行。**
    #
    #   · **换法**：不再要求读，改成**编译**。`references/cards/` 里每张卡是一页纯操作
    #     （≤1200 字、**不含阈值**），由 `_craft_cards()` 按本章性质挑 3–4 张内联进来。
    #     → 写手拿到 ≈3000 字就能动笔，**本章不需要读任何 guide**。
    #
    #   · **为什么卡里不许有阈值**：`quality-constitution.md` 第三节的"表述规范"——
    #     脚本用数字（判据），写手用形态（动作）。给写手看"平均句长 23–37"，
    #     它会去做"统计意义上的最小改动"（指标一进区间就收手），而不是"把这一句写长"。
    #     ⚠️ 但 `quota_card` **保留不动**：它是 A/B 实测有效的护栏（超长句 1.40→4.41），
    #     删掉它是拿已验证的东西去赌未验证的。**卡管"朝哪儿写"，配额卡管"别掉出去"。**
    ('craft_cards', '本章工艺卡（一页一主题，3–4 张；写手唯一要读的"怎么写"）',
     4200, True, 'script'),
    ('batch_global', '批次全局三件套（细纲没有则脚本从圣经+上一章正文拼）',
     1000, True, 'script'),
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
    ('concrete_list', '本章实物清单（脚本从 _meta 抽）', 320, True, 'script'),
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
    ('additive_menu', '本章加法菜单（脚本按章型选 3 招，含做法）', 600, True, 'script'),
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
    # ★ v9.1：`human_exemplar` 由 **editor 手填 → script 自动选段**。
    #   理由（这条是本次改动里"性价比"最高的一处）：它此前是 TODO，而它的官方逃生口径是
    #   "本章无适配的范本时刻"——实测后果可以预见：**最强的那根杠杆在实际项目里多半是空的**。
    #   "给写手一段真人原文"变成了"给写手一句解释为什么没给"。
    #   按模型机制（放什么检索什么），**真人原文是唯一能真正改变语域的东西**，比任何指令都强
    #   → **把最强的杠杆做成"可选"，等于把它做成"没有"。**
    ('human_exemplar', '人类范本原文（按章型自动选 2 段，给原文不给路径）',
     1200, True, 'script'),
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
    ('best_of_book', '本书最佳段落（脚本按缺陷类型自动取）',
     700, True, 'script'),
    # ★ `author_shot`（2026-09-24 修复）：此前 required=False + 赋空串 → **永不产出**。
    #   而 `low-cost-mode.md` 把它列为**必留项**，定位是
    #   「**流水线上最保人味的素材——作者自己的眼睛**」。
    #   判据：它消除的缺陷（人味不足）**后期修要付多轮润色**，属于"后期修显著贵于前期"
    #   → 按这个判据它**必须是必填**，不该是"（若有）"。
    #   放在最后一位：它是**正向、具体、要照着写**的素材，给最高显著性。
    ('author_shot', '作者画面（脚本从脚手架自动抽；无则显式说明）', 420, True, 'script'),
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
# ⚠️ 从 6000 提到 8000 的理由（2026-10-13，v7.6.0）：
#   新增的 `voice_charter` 是**常驻必填的 2000 字**（全书同一份，不随章变化）。
#   · **这是唯一被允许的两种增量之一**：① **一次性固定成本**（同 `quota_card`）、
#     ② **本书常驻的声音层**（超一次、之后只读不改）。**按章变化的素材不许靠上调总量解决。**
#   · 按量级算：+2000 字 ≈ 3000 tokens ≈ 每次调用 ~149,675 tokens 的 **约 2%**；
#     换掉的是"每一章都在用通用先验补声音"——而那正是"文笔很 AI"的直接来源
#     （诊断见 `故事圣经厚度研究报告.md` 第四节：声音层**必须常驻**，不能切片）。
#   · ⚠️ **同时收紧**：`voice_charter` 的 2000 是**硬上限**，超了编译不过 ——
#     "常驻的必须短"是这条设计的前提，不是它的例外。
#   · 不造假闸门：8000 是**总上限**，不是目标；实测填满约 6.3–6.6k 字，
#     余量仍是留给主编填槽位的。
# ⚠️ 从 8000 提到 11000 的理由（2026-10-26 v9.0「编译取代阅读」）：
#   · 新增三个槽位：`promise_debt`(420) + `story_ledger`(500) + `craft_cards`(4200)。
#   · **这不是"又一次上调总量"，是一次交换**：`craft_cards` 替掉的是
#     **单章 21 本指南 ≈ 15.2 万 token 的读点**。11000 字 ≈ 1.6 万 token，
#     且它是本章**唯一**要读的东西（写手不再读 guides）。
#   · 上一轮已经证明"靠限制总量来压成本"是错的方向：压的是**被乘数**，
#     而真正能被压掉的是**读点的存在**——把它换成 3000 字的卡，是净赚。
#   · ⚠️ 仍守上一条纪律：**按章变化的素材不许靠上调总量解决**。
#     卡是"一页一主题"的固定件（全书同一套，按章型换），不是每章新写的长文。
# ⚠️ 从 11500 提到 12000 的理由（2026-11-01 v11.0）——**这是最后一格**：
#   · 新增 `consequence_chain`(700)。它不是"更多素材"，是**判断的产出物**
#     （一阶/二阶/没看见的路/误判），而且实测证明**素材替代不了它**
#     （`v2-harness`：把状态摆在眼前 → 分数更低、砸盒率不降）。
#   · **12000 正好是 `architecture.md` 第八节自毁条款那条线**（"brief >12000 字就该砍槽位"）。
#     → 到这里封顶。**下一次要加任何槽位，必须先删掉一个**，不许再动这个数。
#   · 如果 `--check` 报超预算，砍的顺序写死：**先砍 `craft_cards` 的 4 张到 3 张**（按章换卡），
#     **不要砍** `consequence_chain` / `story_ledger` / `voice_charter`——
#     那三个是"判断"，砍掉就退回"写完再补意义"。
TOTAL_BUDGET = 12000
TODO = '【待填'
SLOT_OPEN = '<!-- slot:{key} budget={budget} required={req} -->'
SLOT_CLOSE = '<!-- /slot:{key} -->'
_PKG_CHAPTER = re.compile(r'第\s*(\d{1,4})\s*章')

# ══════════════════════════════════════════════════════════════════════
# 槽位类型标签（v11.1，2026-11-01）
# ══════════════════════════════════════════════════════════════════════
# **为什么需要它**（实测：一份填满的包里有 **51 个 `##` 标题**，而只有 2 段要写手动手的）：
#   23 个槽位此前**全部用同一个 `##` 标题、同一种祈使语气**呈现 ——
#   于是写手拿到的是一份"51 条要求"的文档，而其中 21 段是**已经定好的事实**或**照着写的素材**。
#   按模型的失效机制（长上下文里权重按位置衰减 + "能勾选的检查都会被勾选着完成"），
#   它会把预算花在**打勾**上，不是花在**做事**上。
#   → 这是 J-Novel 版的"把事后才能算的、和每轮必须做的混在一起"。
#
# 三类：〔已定〕不许再议的事实／〔素材〕照着写、不用响应／〔红线〕不许越的边界。
# ★ **缺省 = 素材**：新加槽位忘了登记 → 只会被降级成"素材"，**不会谎称"已定"**
#   （错了的方向必须是不利的那个）。
SLOT_KIND = {
    'meta': '已定', 'contract': '已定', 'promise_debt': '已定', 'story_ledger': '已定',
    'consequence_chain': '已定', 'ledger': '已定', 'world_pack': '已定',
    'used_devices': '已定', 'prev_tail': '已定', 'rhythm_base': '已定',
    'voice_preset': '已定', 'diagnosis': '已定',
    'lesions': '红线', 'quota_card': '红线',
}
KIND_DEFAULT = '素材'


def slot_kind(key: str) -> str:
    return SLOT_KIND.get(key, KIND_DEFAULT)


def _demote_headings(t: str) -> str:
    """把 `## ` 降成 `### `，**跳过代码围栏内的行**。

    ★ 为什么要跳过：卡片里有 ```markdown 示例块（例如"照这个格式写进 `_meta`"），
      那里面的大标题是**写手要逐字照抄的内容** —— 降级它 = 让写手往 `_meta` 里写错格式。
      （本项目已记录过同类事故：对"文本"做的批量变换，必须能区分"正文"与"示例"。）
    """
    out, infence = [], False
    for line in t.split('\n'):
        if line.lstrip().startswith('```'):
            infence = not infence
            out.append(line)
            continue
        if not infence and line.startswith('## '):
            out.append('### ' + line[3:])
        else:
            out.append(line)
    return '\n'.join(out)


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


# ── 口吻宪法（本书声音层，v7.6.0「世界引擎」）──────────────────────────
# 落盘位置是**接口契约**（跨 agent 共享，不许改）：
#   `00-故事圣经.md` 的 `## 〇、口吻宪法` 段，**位置在 `## 一、世界观手册` 之前**，
#   硬上限 2000 字，必含三个子段（**标题逐字**）：
#     `### 叙述者人格` / `### 声音样本（300–500 字）` / `### 语域禁令`
#
# ⚠️ 边界（铁律一，不造假闸门）：本函数**只做"抽取进包"**。
#   "**三个子段齐不齐、样本块够不够 300 字**"是 `check_worldbuilding.py`【4】的活；
#   "**这个声音好不好**"**谁都不许用正则判**（那是质检子代理/人的活）。
#   这里最多给一句**非阻塞**的提示（与 `_voice_preset` 的 lack 提示同型）——
#   让"缺了什么"在**编译时**就可见，而不是等 `--check` 或等写完才发现。
# ⚠️ 抓不到 → 返回 `TODO` 标记 → `--check` 报"待填残留 → 拒绝开工"（本项目既定模式）。
#   这是刻意的：口吻宪法没有脚本判据能"补"出来，只能靠**编译期强制**。
VOICE_CHARTER_HEADS = ('叙述者人格', '声音样本', '语域禁令')
# 圣经文件名：与 `check_worldbuilding.py` 的 `SETTING_FILES` 同口径（同一份文件，
# 两个脚本各找一遍 → 口径必须一致，否则"体检过了、编译却说没有"）。
BIBLE_FILES = ('00-故事圣经.md', '00-世界设定.md', '00-世界书.md')


def _find_bible(proj: Path):
    """找故事圣经。返回 (path, text) 或 (None, '')。

    ⚠️ 兜底只认**项目根**下的 `*圣经*.md`（不做递归）—— "随便哪个目录里的圣经"
       不是单一事实源，抓进来会让"抽到了哪一份"不可复现。
    """
    for name in BIBLE_FILES:
        p = proj / name
        if p.is_file():
            return p, read_text(p)
    for p in sorted(proj.glob('*.md')):
        if '圣经' in p.name:
            return p, read_text(p)
    return None, ''


def _voice_charter(proj: Path) -> str:
    """从 `00-故事圣经.md` 抽 `## 〇、口吻宪法` **整段**。

    **单一事实源 = 圣经**：这里只抽取进包，**不另存一份**（防双载体漂移——
    本项目已在配额卡上实测过"写在两处、数字还不一样"）。
    为什么抽整段而不是只抽样本：语域效应需要**整体在场**（人格 + 样本 + 禁令是
    一个整体，见 `guides/voice-charter.md` § 0.3），切片会毁掉它。
    """
    p, text = _find_bible(proj)
    if p is None:
        return (f'{TODO}：找不到故事圣经（找过 '
                f'{" / ".join(BIBLE_FILES)}）—— 旧项目**补一段口吻宪法即可**】')
    body = _section(text, '口吻宪法')
    if not body.strip():
        return (f'{TODO}：{p.name} 里没有「## 〇、口吻宪法」段'
                f'（本书声音：叙述者人格 / 声音样本 / 语域禁令）。'
                f'**旧项目补一段口吻宪法即可** —— 位置在 `## 一、世界观手册` **之前**，'
                f'硬上限 2000 字；模板见 references/guides/bible-template.md 〇节，'
                f'写法见 references/guides/voice-charter.md】')
    lack = [k for k in VOICE_CHARTER_HEADS if k not in body]
    if lack:
        body += (f'\n\n> ⚠️ 口吻宪法缺子段：{"、".join(lack)}'
                 f'—— 三个子段是**接口契约**（标题逐字），'
                 f'缺一个就等于没有声音（`check_worldbuilding.py`【4】会报硬问题）。'
                 f'**回到 `00-故事圣经.md` 补**（单一事实源在圣经，不在这里补）')
    return body


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


def _story_ledger(proj: Path, chapter_no: int) -> str:
    """抽本章的「故事账」五格（L1 故事层）。

    ★ 2026-10-26 v9.0：它与 `concrete_list` 是**同一个时点、同一份文件、两种内容**——
      实物清单管"这一章有什么东西"，故事账管"这一章发生了什么事、值不值得写"。
      在 v9 之前，写手只拿到前者：于是它能写出"具体但没发生任何事"的章节。
      这条正是 `quality-constitution.md` 说的"L1 是前两层的前提"。

    ★ 抽不到 → `TODO`（`--check` 拦住）。生效点自适在 `--check` 里不做特殊处理：
      历史项目本来就没有故事账，`check_story.py` 会判"未启用、不阻塞"；
      但**新编译的任务包**要求它有——因为包是给"下一章"用的，不是给历史章用的。
    """
    p = find_meta_file(proj, chapter_no)
    if p is None:
        return (f'{TODO}：找不到 chapters/_meta/第{chapter_no:02d}章-<标题>.meta.md'
                f'（故事账与实物清单同落此处；五格见 guides/story-ledger.md）】')
    m = re.search(r'(?m)^##\s*故事账[^\n]*$', read_text(p))
    if not m:
        return (f'{TODO}：{p.name} 里没有「## 故事账」段（五格：选择/代价/位移/可删性/欲望；'
                f'格式见 guides/story-ledger.md 第一节）】')
    body = read_text(p)[m.end():]
    nxt = re.search(r'(?m)^##\s', body)
    body = (body[:nxt.start()] if nxt else body).strip()
    if not body:
        return f'{TODO}：{p.name} 的「## 故事账」段是空的】'
    return ('本章故事账（**动笔前已定；写作时朝它写，不要写完再补意义**）：\n'
            + body
            + '\n\n判据：`位移` 必须从什么变成什么；`可删性` 有数字；'
              '**`可删性 0 处` = 这章是水章，回去改事件，不是改句子**。')


def _consequence_chain(proj: Path, chapter_no: int) -> str:
    """抽本章的「后果链」五栏（v11.0 · L1 故事层的第二根轴）。

    ★ **它和 `story_ledger` 是同一份文件、同一个时点，但回答两个问题**：
      故事账问"这一章该不该存在"（**点**：选择／代价／位移／可删性／欲望）；
      后果链问"这一章会往哪儿长"（**线**：一阶／二阶／没看见的路／误判）。

    ★ **为什么必须有它**（实测，见 `guides/consequence-chain.md` 第一节）：
      V4.1-Flash 的稳定短板不是"不会检查"，是**用代理条件代替后果推演**——
      盲盒里 8/8 能复述规则，5/8 仍把封存砸在垃圾盒上，因为触发前它只核对余额。
      写作里的同型物是"符合人设／推进剧情／伏笔回收了"——**全是代理条件**。
      而**光提醒没用**（`v3-prompt` 实测 2226 分、砸盒率不降），
      **把状态摆在眼前也没用**（`v2-harness` 1879 分）——
      **唯一有效的是把隐含后果写成明文、并给出一个它不知道自己有的选项**（`v1-clear` 2396 分）。
      → 所以这里给的是**填空格式 + 明文 + 三条可机械判的判据**，不是一句"请推演二阶后果"。

    ★ 抽不到 → `TODO`（`--check` 拒绝开工）。和 `story_ledger` 同一条豁免逻辑：
      历史项目由 `check_story.py` 判"未启用、不阻塞"；但**新编译的包**必须有它。
    """
    p = find_meta_file(proj, chapter_no)
    if p is None:
        return (f'{TODO}：找不到 chapters/_meta/第{chapter_no:02d}章-<标题>.meta.md'
                f'（后果链与故事账同落此处；五栏见 guides/consequence-chain.md 第四节）】')
    text = read_text(p)
    m = re.search(r'(?m)^##\s*后果链[^\n]*$', text)
    if not m:
        return (f'{TODO}：{p.name} 里没有「## 后果链」段'
                f'（五栏：动作／一阶／二阶／没看见的路／误判；'
                f'格式见 guides/consequence-chain.md 第四节）】')
    body = text[m.end():]
    nxt = re.search(r'(?m)^##\s', body)
    body = (body[:nxt.start()] if nxt else body).strip()
    if not body:
        return f'{TODO}：{p.name} 的「## 后果链」段是空的】'
    return ('本章后果链（**动笔前已定。写正文时这五栏要各自有落点——'
            '落不下去 = 当时那条推演是假的**）：\n'
            + body
            + '\n\n判据（写之前对着它写，不是写完再对）：\n'
              '· **`二阶` 必须指到具体章号或场景**。指不到 = 只推了一跳，写的是形容词。\n'
              '· **四栏里出现"引发冲突／关系变差／埋下伏笔"这类总结词 = 表演式填空**'
              '（换一本书还成立 = 不是后果）。\n'
              '· **`误判` 不许写"无"**：这一栏是"全员清醒"唯一的解药——'
              '人物心里得有一个**错的判断**，那个错会漏到动作里。\n'
              '· 自问一句：**这一章拿掉之后，下一章第一段要改几个字？0 个 = 这章是空转。**')


def _promise_debt(proj: Path, chapter_no: int) -> str:
    """抽「本章必须还的债」：`08-承诺账.md` 里 `应兑现 == 本章` 且状态未兑现的条目。

    ★ 这是**连贯**的执行点。此前的 brief 只带"上一章真实结尾"（800 字），
      所以写手知道"刚才发生了什么"，但**不知道"读者在等什么"**——
      而后者才是读者追读的原因。`promises.md` 是这条机制的说明书。

    ★ 没有承诺账时**不报 TODO**，而是给出"如何建"的一句提示并放行：
      理由 = 生效点自适应（历史项目不追溯；`check_promises.py` 同样 exit 0）。
      这里与 `concrete_list` / `story_ledger` 的区别是刻意的：
      **故事账与实物清单是"本章必需的判断"，承诺账是"全书级的账"**——
      新项目第一、二章本来就没有可还的债。
    """
    f = proj / '08-承诺账.md'
    if not f.is_file():
        return ('（本书尚无 `08-承诺账.md`——前两章属正常。'
                '第 3 章起建立：七列表格，见 guides/promises.md 第一节。'
                '**本章只需"欠债"**：写完把读者会记着的那件事登记进去。）')
    txt = read_text(f)
    rows = []
    for line in txt.split('\n'):
        s = line.strip()
        if not s.startswith('|'):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if len(cells) < 7 or set(''.join(cells)) <= set('-: '):
            continue
        if cells[0].strip() in ('#', ''):
            continue
        tag, promise, kind, born, due, status = cells[0], cells[1], cells[2], cells[3], cells[4], cells[5]
        m = re.search(r'(\d{1,4})', due)
        if not m:
            continue
        due_n = int(m.group(1))
        if status and any(k in status for k in ('✓', '√', '已兑现', '✗', '×', '作废')):
            continue
        if due_n == chapter_no:
            rows.append(f'· {tag}〔{kind}〕{promise}（第 {born} 章许下，本章到期）')
        elif due_n < chapter_no:
            rows.append(f'· {tag}〔{kind}〕{promise}（**已逾期**：应第 {due_n} 章兑现，'
                        f'本章是第 {chapter_no} 章 → 还掉或显式改期）')
    if not rows:
        return ('（本章无到期的债。**那本章只需"欠债"**：写完把读者会记着的那件事'
                '登记进 `08-承诺账.md`——不登记 = 下一章没人记得它。）')
    return ('本章必须还的债（**一章最多还 2 笔**；还 5 笔 = 交作业，读起来像结算清单）：\n'
            + '\n'.join(rows[:4])
            + '\n\n还债**必须有代价**——否则读者会觉得"这也太容易了"。'
              '真还不了一定要**显式改期**（在「承诺」列写一句为什么延），不允许无声拖过去。')


def _craft_cards(proj: Path, chapter_no: int, ol_seg: str = '') -> str:
    """按本章性质挑 3–4 张一页卡注入（**写手唯一读的"怎么写"**）。

    ★ 为什么由脚本挑（不靠记忆）：`cards/README.md` 第三节写了规则，
      但"按章型换卡"这件事一旦靠人记，就会退化成"永远注入同一套"——
      这正是本体系反复复发的形态（**规则写了、没人执行**）。
      **能做成脚本的，不要只写成文字。**

    选卡规则（改这里 = 改规则，不在文档里另存一份）：
      基线：voice + prose + dialogue + ending
      · 静场章 → dialogue 换 scene（静场章没有对抗，对白不是主战场）
      · 打斗/追击/冲突章 → dialogue 换 scene（同上：场在打，话少）
      · 首章 / 卷首 / 新场景断开 → 追加 opening
      · 本章要新造地点/势力/机制（细纲里有"新"且世界包没有）→ 追加 world
      · rewrite 模式 → ending 换 revise
    """
    cdir = Path(__file__).resolve().parent.parent / 'references' / 'cards'
    if not cdir.is_dir():
        return f'{TODO}：找不到 references/cards/（一页卡目录）】'

    seg = ol_seg or ''
    quiet = '静场' in seg
    fight = bool(re.search(r'打斗|追击|战斗|厮杀|搏斗|冲突章', seg))
    fresh = '卷首' in seg or '新场景' in seg
    new_world = bool(re.search(r'新造|新地点|新势力|新机制|首次出现', seg))

    picks = ['card-voice.md', 'card-prose.md', 'card-dialogue.md', 'card-ending.md']
    if quiet or fight:
        picks = [p for p in picks if p != 'card-dialogue.md'] + ['card-scene.md']
    if chapter_no == 1 or fresh:
        picks.insert(1, 'card-opening.md')
    if new_world:
        picks.append('card-world.md')
    if not _is_rewrite_hint(seg) and chapter_no == 1:
        picks.append('card-story.md')

    out, total, skipped = [], 0, []
    # ★ 卡总量封顶：写手要读得完才有意义。但**超了不许静默丢弃**——
    #   实测过一次：4 张卡按顺序累加，第 4 张（章末）被 4200 的上限挡掉，
    #   而"章末最后三行决定读者读不读下一章"恰恰是最不该缺的一张。
    #   静默丢卡 = "看起来配了、其实没有"，本项目最忌讳的那一类。
    #   现在：① 上限提到 5200（刚好够 4 张）② 被跳过的卡**写进槽位里**，主编看得见。
    for name in picks:
        p = cdir / name
        if not p.is_file():
            skipped.append(f'{name}（文件不存在）')
            continue
        t = read_text(p).strip()
        if total + len(t) > 5200:
            skipped.append(name)
            continue
        # ★ v11.1：卡内标题降一级（`##` → `###`）。它们是"本章工艺卡"这一段的**子节**，
        #   不是 23 个同位槽位之一。降级前实测：一份包里 **51 个 `##`**，
        #   写手无法从层级上看出一段到底是"要求"还是"素材"。
        out.append(_demote_headings(t))
        total += len(t)
    if not out:
        return f'{TODO}：references/cards/ 里没有可用的卡】'
    note = (f'\n\n> ⚠️ 以下卡因总量上限未注入：{"、".join(skipped)}。'
            f'需要它们时按 `cards/README.md` 单独打开。' if skipped else '')
    return ('\n\n---\n\n'.join(out)
            + '\n\n> 以上是本章的**工艺卡**（一页一主题）。'
              '它们取代了"读若干本指南"——**本章不需要再读 guides**；'
              '只有改写深查时按 `guide-index.md` 取一节。'
            + note)


def _is_rewrite_hint(seg: str) -> bool:
    """粗判本章是不是"改写任务"（细纲里常见的标记）。"""
    return bool(re.search(r'改写|重写|返工', seg or ''))


# ──────────────────────────────────────────────────────────────────────
# 思考协议（2026-11-01 v10.0）
# ──────────────────────────────────────────────────────────────────────
# **它修的是什么**：本体系此前 54 本 guides / 10 张卡 / 22 个脚本，
# **没有任何一行管思维链**——全部工程都压在**输出面**（句长、密度、次数、改写）。
#
# 而 AI 味是在正文**之前**定下的：模型的自我审查发生在思维链里，
# 而 `architecture.md` §7.2 ③ 那条"禁止自评"只挡在正文之后（"写完直接交"）——
# **闸门设在了一道已经流过去的下游**。这是本体系最贵的一处结构性错位。
#
# **为什么必须放在 brief 的最后**（位置就是机制）：
#   模型读完 brief 立刻开始思考 → **最后一行 = 第一个思考动作**。
#   所以它不是"再强调一遍要求"，而是**给思维链设起手式**：
#   第一件事必须是"这一章我不写 ___"（一次减法），而不是"嗯，我需要写……"（一次复述）。
#
# **为什么不在 SLOTS 里**：SLOTS 的顺序是"显著性设计"（硬约束→参考→正向素材），
#   而这一段的作用域是**整条思维链**，它必须晚于渲染尾部的三条开写指令才生效。
#   做成槽位会被后面的尾巴盖住 → **放在 render() 的最末尾**。
#
# **为什么读卡而不是内联字符串**：`cards/card-thinking.md` 是**唯一事实源**
#   （写手读的那一份）；内联一份字符串 = 双载体，必漂（本项目已在配额卡上踩过）。
def _thinking_card() -> str:
    """把 `cards/card-thinking.md` 原文接到 brief 最末尾（给思维链设起手式）。"""
    p = Path(__file__).resolve().parent.parent / 'references' / 'cards' / 'card-thinking.md'
    if not p.is_file():
        # ★ 不 fail-closed：卡缺失时 brief 仍然可写（否则整个编译器被一张卡拖死）。
        #   但**必须留痕**——静默少一段 = "看起来配了、其实没有"，本项目最忌讳的那一类。
        return ('# 现在开始想（动笔之前）\n\n'
                '> ⚠️ **思考协议卡缺失**（`references/cards/card-thinking.md` 找不到）——'
                '本次按老办法写：不动笔前先定"不写什么"，思考里不做自评。\n')
    lines = read_text(p).strip().split('\n')
    # 卡自己的 H1 是"一页卡"的标题（"这一页…"），内联进 brief 后语义不通 →
    # 换成 brief 语境下的标题，其余原样（**不做任何改写**，单一事实源仍在那张卡里）。
    if lines and lines[0].startswith('# '):
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines.pop(0)
    return ('# 现在开始想（动笔之前：这一节定的是你思考里做什么，不是正文写什么）\n\n'
            + '\n'.join(lines))


# ──────────────────────────────────────────────────────────────────────
# 人类范本自动选段（2026-10-26 v9.1）
#
# **它修的是什么**：`human_exemplar` 此前是**主编手填的 TODO**，而它有官方逃生口径
# （"本章无适配的范本时刻"）。实测后果可以预见：**最强的那根杠杆在实际项目里多半是空的**
# —— "给写手一段真人原文"变成了"给写手一句解释为什么没给"。
# 而按模型机制（放什么检索什么），**真人原文是唯一能真正改变语域的东西**，
# 它比任何一条指令都强。**把最强的杠杆做成"可选"，就是把它做成"没有"。**
#
# 选段规则（按章型；改这里 = 改规则，不在文档里另存一份）：
#   · 静场/日常 → 纯闲笔 / 章末平静 / 小动作代替情绪 / 现实锚定
#   · 打斗/冲突 → 群体噪音 / 生理写实 / 降格幽默
#   · 对话为主   → 对话的不完美 / 称呼即关系刻度
#   · 情绪/失去  → 故意不解释 / 自由间接引语 / 生理写实
#   · 默认       → 叙述者跳出来 / 故意不解释
# 每类取 **2 节**，每节取**最长的一段**（材料越多越能带起语域），裁到 ≈480 字（句末）。
# ──────────────────────────────────────────────────────────────────────
_EXEMPLAR_RULES = [
    (r'静场|日常|过场|生活', ['纯闲笔', '章末的', '小动作代替情绪', '现实锚定']),
    (r'打斗|追击|战斗|厮杀|冲突|对峙|围杀', ['群体噪音', '生理写实', '降格幽默']),
    (r'对话|谈判|争吵|说服|交涉', ['对话的不完美', '称呼即关系刻度']),
    (r'情绪|告别|死亡|失去|葬礼|离别|崩溃', ['故意不解释', '自由间接引语', '生理写实']),
]
_EXEMPLAR_DEFAULT = ['叙述者跳出来', '故意不解释']


def _trim_at_sentence(t: str, n: int) -> str:
    if len(t) <= n:
        return t
    seg = t[:int(n * 1.15)]
    idx = max(seg.rfind(m) for m in '。！？…')
    return seg[:idx + 1] if idx >= n * 0.5 else seg


def _human_exemplar_auto(proj: Path, chapter_no: int, ol_seg: str = '') -> str:
    """按章型从 `human-exemplars.md` 自动挑 2 段真人原文（带来源标注）。"""
    he = Path(__file__).resolve().parent.parent / 'references' / 'guides' / 'human-exemplars.md'
    if not he.is_file():
        return f'{TODO}：找不到 references/guides/human-exemplars.md】'
    sections = parse_exemplar_sections(read_text(he))
    if not sections:
        return f'{TODO}：human-exemplars.md 解析不出任何片段（解析契约见 _shared）】'

    want = _EXEMPLAR_DEFAULT
    seg = ol_seg or ''
    for rx, keys in _EXEMPLAR_RULES:
        if re.search(rx, seg):
            want = keys
            break

    picked, used = [], set()

    def _take(title, pairs):
        """从一节里取材料：够长就取一段，太短就把同节最长的两段并起来。

        ⚠️ 为什么要并：本库多数片段只有 50–80 字（一句话的例），而**语感需要足够的字数才带得起来**
        （300 字以下基本只能示范"一个动作"，带不动整段语域）。
        同节的 2 例是**同一个技法的两个例子**，并起来不违和，反而更能说明"这一招长什么样"。
        """
        ranked = sorted(pairs, key=lambda x: -len(x[1]))
        book, text = ranked[0]
        if len(text) >= 200 or len(ranked) < 2:
            return book, _trim_at_sentence(text, 480)
        book2, text2 = ranked[1]
        joined = text + '\n\n' + text2
        src = book if book == book2 else f'{book}／{book2}'
        return src, _trim_at_sentence(joined, 480)

    for key in want:                       # 先按章型命中
        for title, pairs in sections:
            if key in title and title not in used and pairs:
                used.add(title)
                picked.append((title, *_take(title, pairs)))
                break
        if len(picked) == 2:
            break
    if len(picked) < 2:                    # 不够就按"有片段的节"顺序补齐
        for title, pairs in sections:
            if len(picked) == 2:
                break
            if title in used:
                continue
            used.add(title)
            picked.append((title, *_take(title, pairs)))

    if not picked:
        return f'{TODO}：human-exemplars.md 里没有可用片段】'
    out = ['本章真人范本（**给原文，不给规则**——照着它的语感写，不是照着它的事件写）：']
    for title, book, text in picked:
        out.append(f'\n【来源：{book} · {title}】')
        out.append(text)
    out.append('\n> 用法：**读一遍，然后写第一句**。不要模仿它的事件，模仿它「哪里不干净」——'
               '多出来的那半句、说漏的那个称呼、没解释的那件事。')
    return '\n'.join(out)


# ──────────────────────────────────────────────────────────────────────
# 四个"人工格"的自动填充（2026-10-26 v9.2）——**批量自动化的前提**
#
# **为什么必须自动化**：`ledger` / `world_pack` / `lesions` / `author_shot` 此前
# 每章要人填 4 次。这意味着"批量写 10 章"要么花 40 次人工介入，要么这四格一直是空的。
# **一个需要人工介入 40 次/10 章的流程，不可能被称为自动化。**
#
# **为什么以前坚持"人填"**：定位是"需要理解才能产生"。这句话是对的，
# 但它推出的是"**人判断得更好**"，不是"**没有人就必须空着**"。
# 而当"空着"是唯一替代品时，实际发生的往往是第三种结果：**填空话**
# （"本章无适配的范本时刻"）。本项目已在这个模式上栽过一次（`human_exemplar`）。
# → 修法：**脚本给出一个可用的近似值，并标注「可覆盖」**。
#   主编不满意就改一行；没时间改，也不至于空着。
#
# ⚠️ 抽不到就返回 TODO（fail-closed），**不许编**：
#   判据 = 这一段里每一个事实，都必须能在项目文件里指出来源行。
# ──────────────────────────────────────────────────────────────────────

# 缺陷类型 → 占位式指令（"这里要放什么"，不是"不要写 X"）
# 这张表是 `lesions` 自动生成的引擎，也是**体系从自己的缺陷里学**的那一环：
# 上一批栽在哪一类，下一批就把"该放什么"提前写进 brief。
_LESION_RECIPES = {
    '字数': '这里要放一段**具体的过程**——一个动作从开始到结束（谁动手、怎么动、手感到什么、结果如何），不要概括成一句',
    '节律': '这里要放**一句把整个处境装进去的长句**（谁+状态→动作→手段→结果→推论），它前面留短句呼吸',
    '词汇': '这里要放**一个只有这个人物会说的词**（口头禅、方言词、他的行话），不是"他很愤怒"',
    '重复': '这一段**换一个入口**：上一章是从时间/环境起手的，这一章从人物、物或一句对话起手',
    '连续性': '这里要放**一句把上一章末那个动作接住的话**（他手上还留着什么、身上还疼在哪）',
    '世界': '这里要放**一条只有这个世界才有的规矩或物**（专名表里的词，不许另造同义新词）',
    '契约': '这里要放**一句交代他此刻在哪、知道什么、身上什么状况**（读者要能自己接上）',
    '故事': '这里要放**一个他真的失去的东西**（已经发生，不是"他很难过"）',
    '承诺': '这里要放**一件读者会记着、但这一章不解答的事**（写完登记进承诺账）',
}


def _ledger_auto(proj: Path, chapter_no: int, ol_seg: str = '') -> str:
    """从 `03-状态台账.md` 的「人物状态」表里抽本章出场人物条目。

    匹配方式：台账「角色」列的名字**出现在本章细纲文本里** → 认为他出场。
    宁窄不宽：抽不到就走兜底并**显式声明是兜底**（"抽错了人"比"没抽"更坏——
    写手会照着错的状态写）。
    """
    f = proj / '03-状态台账.md'
    if not f.is_file():
        return f'{TODO}：找不到 03-状态台账.md】'
    txt = read_text(f)
    m = re.search(r'(?m)^##\s*人物状态[^\n]*$', txt)
    if not m:
        return f'{TODO}：03-状态台账.md 里没有「## 人物状态」段】'
    rest = txt[m.end():]
    nxt = re.search(r'(?m)^##\s', rest)
    body = rest[:nxt.start()] if nxt else rest

    rows, header = [], []
    for line in body.split('\n'):
        s = line.strip()
        if not s.startswith('|') or set(s) <= set('|-: '):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if not header:
            header = cells
            continue
        if cells and cells[0]:
            rows.append(cells)
    if not rows:
        return f'{TODO}：03-状态台账.md 的「人物状态」表还没有数据行】'

    seg = ol_seg or ''
    hit = [r for r in rows if r[0] and r[0] in seg]
    note = ''
    if not hit:
        hit = rows[:6]
        note = ('\n\n> ⚠️ 本章细纲里没有出现台账中的角色名 —— 以上是**全表前 6 行**（兜底）。'
                '若本章真正出场的人不在这里，请补细纲的「人物状态追踪」，不要手动改这一段。')
    cols = header[:6] if header else ['角色', '位置', '情绪', '关系', '已知信息', '身体状态']
    out = ['本章出场人物（**起点状态**；写作时从这里开始，写完结账回 `03-状态台账`）：']
    for r in hit:
        kv = [f'{cols[i]}={r[i]}' for i in range(min(len(cols), len(r))) if r[i] and r[i] != '—']
        out.append('· ' + '｜'.join(kv))
    return '\n'.join(out) + note


def _world_pack_auto(proj: Path, chapter_no: int, ol_seg: str = '') -> str:
    """从圣经「一、世界观手册」裁出本章要用的 500–800 字切片。

    三样，按信息密度取（不按章节顺序）：**专有名词表**（最硬）→ **底层规则** → **场景卡**。
    场景卡优先取"名字出现在本章细纲里"的行（本章真的要用到）。
    """
    bible, txt = _find_bible(proj)
    if bible is None:
        return f'{TODO}：找不到故事圣经（00-故事圣经.md）】'
    m = re.search(r'(?m)^##\s*一、世界观手册[^\n]*$', txt)
    body = txt[m.end():] if m else txt
    nxt = re.search(r'(?m)^##\s*二、', body)
    body = body[:nxt.start()] if nxt else body

    parts, total = [], 0

    def _sec(head_rx, cap):
        nonlocal total
        mm = re.search(head_rx, body, re.M)
        if not mm:
            return
        rest = body[mm.end():]
        nn = re.search(r'(?m)^###\s', rest)
        seg = (rest[:nn.start()] if nn else rest).strip()
        lines = [l for l in seg.split('\n') if l.strip() and not l.strip().startswith('>')]
        seg = '\n'.join(lines)[:cap]
        if seg:
            parts.append(seg)
            total += len(seg)

    _sec(r'(?m)^###\s*7\.\s*专有名词表[^\n]*$', 420)      # 最硬：只用表内的词
    if total < 700:
        _sec(r'(?m)^###\s*2\.\s*底层规则[^\n]*$', 320)
    if total < 700:
        mm = re.search(r'(?m)^###\s*4\.\s*地图与场景卡[^\n]*$', body)
        if mm:
            rest = body[mm.end():]
            nn = re.search(r'(?m)^###\s', rest)
            seg = rest[:nn.start()] if nn else rest
            rows = [l.strip() for l in seg.split('\n') if l.strip().startswith('|')]
            keyed = [l for l in rows if any(k in ol_seg for k in re.findall(r'[\u4e00-\u9fff]{2,6}', l))]
            pick = keyed or rows
            if pick:
                t = '\n'.join(pick)[:260]
                parts.append(t)
                total += len(t)

    if not parts:
        return (f'{TODO}：圣经「一、世界观手册」里抽不出可用切片'
                f'（专有名词表/底层规则/场景卡都空着）】')
    return ('本章世界切片（**是约束不是参考**：表里已有名字的东西禁止另造同义新词；'
            '要造新词必须在回报里单列 `新增专名：`）：\n\n' + '\n\n'.join(parts))


def _lesions_auto(proj: Path, chapter_no: int) -> str:
    """从 `06-章节工单.md` 的**缺陷帕累托**自动生成 1–2 条占位式指令。

    这是"**体系从自己的缺陷里学**"的那一环：上一批栽在哪一类，这一批就把"该放什么"
    提前写进 brief（**前馈 > 反馈**——反馈要多写一轮、要多花 15 万 token；前馈只要几行字）。
    """
    f = proj / '06-章节工单.md'
    if not f.is_file():
        return '无（本批尚无工单记录——第一批没有历史病灶可前馈）'
    txt = read_text(f)
    idx_defect, count = None, {}
    for line in txt.split('\n'):
        s = line.strip()
        if not s.startswith('|') or set(s) <= set('|-: '):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if '缺陷类型' in cells:
            idx_defect = cells.index('缺陷类型')
            continue
        if idx_defect is None or idx_defect >= len(cells):
            continue
        for t in re.split(r'[、,，/｜| ]+', cells[idx_defect]):
            t = t.strip()
            if t and t not in ('—', '无', '-'):
                count[t] = count.get(t, 0) + 1
    if not count:
        return '无（工单里还没有缺陷记录）'
    top = sorted(count.items(), key=lambda x: -x[1])[:2]
    lines = [f'本批已出现过的病灶（按频次取前 {len(top)} 类；'
             f'下面是"这里该放什么"，不是"不要写什么"）：']
    for name, n in top:
        recipe = _LESION_RECIPES.get(name)
        if recipe:
            lines.append(f'· **{name}**（{n} 章）→ {recipe}')
    if len(lines) == 1:
        return '无（工单里的缺陷类型都不在已知映射表里）'
    lines.append('\n> 判据：这几条是**前馈**（上一批的账，这一批提前还），不是本章的验收项。')
    return '\n'.join(lines)


def _author_shot_auto(proj: Path, chapter_no: int) -> str:
    """从 `07-剧情脚手架.md` 抽本章的「作者脑中的画面」+「读者情绪目标」。

    抽不到时给**显式的一句**而不是 TODO：因为"作者没投喂画面"是**合法状态**
    （`plot-scaffold.md`：问过就算过，作者说没有也不阻塞）。
    把它变成编译阻塞 = 把合规流程卡死。
    """
    f = proj / '07-剧情脚手架.md'
    if not f.is_file():
        return ('本章无脚手架条目（作者尚未投喂画面）——**这不是缺陷**。'
                '但如果你手边有一个具体画面，现在给：它是整条流水线上最保人味的素材。')
    txt = read_text(f)
    blocks = re.split(r'(?m)^###\s+', txt)
    for b in blocks[1:]:
        head = b.split('\n', 1)[0]
        if '第' not in head:
            continue
        hit = False
        for a, bb in re.findall(r'第\s*(\d{1,4})\s*(?:[–\-~至]\s*(\d{1,4}))?\s*章', head):
            lo = int(a)
            hi = int(bb) if bb else lo
            if lo <= chapter_no <= hi:
                hit = True
                break
        if not hit:
            continue
        shot = re.search(r'\*\*作者脑中的画面\*\*\s*[:：]\s*(.+)', b)
        mood = re.search(r'\*\*读者情绪目标\*\*\s*[:：]\s*(.+)', b)
        if not shot:
            continue
        out = ['作者画面（**原样保留，不要改写、不要解释**——那是作者自己的眼睛）：',
               shot.group(1).strip()]
        if mood:
            out.append(f'\n读者情绪目标：{mood.group(1).strip()}　'
                       f'（它决定这段怎么收束——目标若是"赢了但高兴不起来"，'
                       f'就**不能用爽文的收束方式**）')
        return '\n'.join(out)
    return '本章无脚手架条目（本书脚手架里没有覆盖本章的条目）——**这不是缺陷**，照写即可。'


# 加法菜单的自动选招（按章型）——治"这一格是主编手填，批量时必然空着"
_ADDITIVE_RECIPES = [
    (r'静场|日常|过场', [
        ('一件没人要的物件', '给它一个来历（谁留下的、为什么还在），但**别让人物评论它**'),
        ('一段无人的环境', '只写 2–3 句，且要有人刚刚离开的痕迹（椅子还热、灯还亮着）'),
        ('一次没有后果的动作', '他做了，没人看见，故事不受影响——**这就是它存在的理由**'),
    ]),
    (r'打斗|追击|战斗|冲突|对峙', [
        ('主角判断错一次', '让他根据经验推断，而推断是错的——**代价当场发生**'),
        ('对手做对了一件事', '对手赢了一小步，主角因此改了计划'),
        ('赢的代价', '赢了之后立刻付一样东西（钱、信誉、一个地方）'),
    ]),
    (r'对话|谈判|争吵|说服', [
        ('一处打断', '话说到一半被别人截走，被打断的那半句不再补'),
        ('一处答非所问', '问"你去哪了"，答"你吃饭了吗"'),
        ('一次称呼改口', '关系变了就改称呼，别改解释（"陈总"→"汉升"）'),
    ]),
]
_ADDITIVE_DEFAULT = [
    ('一处不解释', '他知道了但没说 / 角色不知道 / 作者故意跳过'),
    ('一件没用的东西', '对情节零贡献的物（冰箱里过期的酸奶、扣着的半杯凉水）'),
    ('一次不完美的对话', '结巴／改口／说到一半被打断——**至少一处必须是主角说的**'),
]


def _additive_menu_auto(ol_seg: str = '') -> str:
    """按章型选 3 招加法（**这是"往里加什么"，不是"改成什么样"**）。

    ⚠️ 它和 `craft_cards` 有重叠（`card-prose` 第三节也讲具体性），这是**有意的冗余**：
    卡讲"怎么抓具体"，这里讲"这一章具体该加哪三样"。**一个给方法，一个给清单。**
    """
    picks = _ADDITIVE_DEFAULT
    for rx, items in _ADDITIVE_RECIPES:
        if re.search(rx, ol_seg or ''):
            picks = items
            break
    out = ['本章挑这 3 招加法（**挑 3 招，不要逐条打卡**——逐条打卡＝同构模板）：']
    for name, how in picks:
        out.append(f'· **{name}**：{how}')
    out.append('\n> 判据：这三招都是**加法**（往里放东西），删不出来。'
               '它们的上限是"到处都在用力"——**三项做到就是够，做满反而像 AI**。')
    return '\n'.join(out)


_BOB_KEYS = ('缺闲笔', '章末落笔', '对白声口', '本体层具体性', '叙述者立场')


def _best_of_book_auto(proj: Path, chapter_no: int, ol_seg: str = '') -> str:
    """从 `细纲/_最佳段落库.md` 按本章病灶类型挑 1–2 段（**本书自己的好段落**）。

    与 `human_exemplar` 的分工不变：那是**外部**人类范本（拉开距离），
    这是**本书**的已定稿好段落（更贴近"这一本的味道"，零采集成本）。
    抽不到时给**显式说明**而不是 TODO：第一批本来就没有已定稿章可挑，那是时序，不是缺失。
    """
    f = proj / '细纲' / '_最佳段落库.md'
    if not f.is_file():
        return '（本书尚无 `细纲/_最佳段落库.md`——第一批属正常，批末 7.7 会建。）'
    txt = read_text(f)
    seg = ol_seg or ''
    if re.search(r'静场|日常', seg):
        want = ['缺闲笔', '叙述者立场']
    elif re.search(r'打斗|冲突|对峙', seg):
        want = ['章末落笔', '本体层具体性']
    else:
        want = ['章末落笔', '缺闲笔']

    blocks = {}
    for m in re.finditer(r'(?m)^#{2,4}\s*([^\n]+)$', txt):
        key = next((k for k in _BOB_KEYS if k in m.group(1)), None)
        if not key:
            continue
        rest = txt[m.end():]
        nxt = re.search(r'(?m)^#{2,4}\s', rest)
        blocks[key] = (rest[:nxt.start()] if nxt else rest).strip()

    out = []
    for k in want:
        if blocks.get(k):
            body = re.sub(r'(?m)^>\s*', '', blocks[k]).strip()
            out.append(f'【本书取样 · {k}】\n{body[:600]}')
        if len(out) == 2:
            break
    if not out:
        keys = '／'.join(blocks) if blocks else '（空）'
        return f'（`_最佳段落库.md` 里没有本章需要的分类。现有分类键：{keys}。）'
    return ('本书已定稿的好段落（**照着它的样子写**——不是照着指标写）：\n\n'
            + '\n\n'.join(out))


def _batch_global_auto(proj: Path, chapter_no: int, ol_chaps: dict) -> str:
    """批次全局三件套：细纲有就用细纲，没有就**从圣经与上一章正文自动拼**。

    ★ 为什么必须自动：这一格是 `editor`，而**批量写 10 章时没人会填 10 次**。
      而且它更好的部分是：**锚点示例本来就该是一段真实原文**——手抄会漂，脚本抽不会。
    """
    seg = ol_chaps.get(chapter_no, '')
    g = _section(seg, '批次全局', ('文风基因', '锚点示例', '句法'))
    if g:
        return g

    parts = []
    bible = _find_bible(proj)[1]
    if bible:
        m = re.search(r'(?m)^###\s*叙述者人格[^\n]*$', bible)
        if m:
            rest = bible[m.end():]
            nxt = re.search(r'(?m)^###\s', rest)
            seg2 = (rest[:nxt.start()] if nxt else rest).strip()
            if seg2:
                parts.append('**文风基因（本书口吻宪法的人格四格）**\n' + seg2[:400])
    prev = None
    for f in find_chapter_files(proj):
        mm = _PKG_CHAPTER.search(Path(f).name)
        if mm and int(mm.group(1)) == chapter_no - 1:
            prev = Path(f)
            break
    if prev is not None:
        try:
            body = _paras(read_text(prev))
            anchor = next((p for p in body if 150 <= len(p) <= 400), None) or _tail(body, 300)
            if anchor:
                parts.append(f'**锚点示例（第 {chapter_no - 1} 章正文原样摘录，一个字没改）**\n{anchor}')
        except Exception:
            pass
    if not parts:
        return f'{TODO}：细纲没有「批次全局信息」段，且圣经/上一章正文也抽不出锚点】'
    parts.append('**句法基线**：不列数字。看本 brief 末尾那三条开写指令——'
                 '它们在讲形态，不是在讲阈值。')
    return '\n\n'.join(parts)


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


# =========================================================================
# fix_recipe（2026-10-21）：把工单里该章的缺陷类型编译成"配方 + 指向"
#
#   治最后一层：报告告诉执行者"查哪一节"（索引），但那一节的**内容还在文件里，
#   要它自己去 Read** —— 于是最常见的结局是它照 L1 的动作名（"插入铺陈句"）
#   即兴写，指标变绿但不是范本里那种写法。现在由编译器按缺陷项抽节，
#   **内容直接进上下文**（给原文不给路径）。
#
#   ⚠️ 为什么按**工单的缺陷类型**映射、而不是按 17 项指标：工单那列是
#      `make_workorder.py` **机器写入**的（客观事实），而 17 项指标在任务包
#      装配时并不在手边（要另外跑一次脚本）。**用已经在文件里的事实，不用再判断一次。**
#   ⚠️ 为什么**只抽 rewrite-playbook**：它的节标题格式统一（`## N. 标题`），
#      `_section()` 能可靠切分；其余几家标题格式不同，宁给指向不给错内容。
# =========================================================================
FIX_RECIPE_BY_DEFECT = {
    '节律': ('平均句长过短', '句首代词超标', '句首引号不足'),
    '词汇': ('情绪词过低', '明喻超标', '数字密度过低'),
}
FIX_RECIPE_POINTER = {
    '节律': 'human-rhythm.md 规则 1–7（句子怎么组织）',
    '词汇': 'human-quota.md（人味配额）',
    '字数': 'chapter-craft.md（场景质感怎么补）',
    '重复': 'check_repetition.py 的报告（跨章开场/章末同质）',
    '连续性': 'continuity-check.md（衔接三查 + 逻辑 BUG 四查）',
    '世界': 'world-engine.md（机制卡六格）',
    '契约': 'flows/phase3-writing.md 的接口契约',
}


def _read_wo_defects(proj: Path, chapter_no: int):
    """从 06-章节工单.md 读该章的「缺陷类型」列 → `[标签, ...]`。"""
    p = proj / '06-章节工单.md'
    if not p.is_file():
        return []
    for ln in read_text(p).split('\n'):
        st = ln.strip()
        if not (st.startswith(f'| 第{chapter_no}章 ') or st.startswith(f'| 第{chapter_no}章|')):
            continue
        cells = [c.strip() for c in st.strip('|').split('|')]
        if len(cells) > 3 and cells[3] not in ('—', ''):
            return [x.strip() for x in cells[3].replace('、', ' ').split() if x.strip()]
    return []


def _fix_recipe(proj: Path, chapter_no: int) -> str:
    """按缺陷类型抽配方；抽不到 → 给指向或 TODO，**不许编内容**。"""
    defects = _read_wo_defects(proj, chapter_no)
    if not defects:
        return (f'{TODO}：本章工单里没有缺陷类型（先跑 '
                f'`make_workorder.py --archive {chapter_no}`）】')
    guide = Path(__file__).resolve().parent.parent / 'references' / 'guides' / 'rewrite-playbook.md'
    if not guide.is_file():
        return f'{TODO}：找不到 references/guides/rewrite-playbook.md】'
    text = read_text(guide)
    parts = []
    for d in defects:
        for kw in FIX_RECIPE_BY_DEFECT.get(d, ())[:1]:
            sec = _section(text, kw)
            if sec:
                parts.append(f'【{d} → {kw}】\n{sec[:250].strip()}')
                break
    if parts:
        return '\n\n'.join(parts)[:400]
    ptr = [f'· {d} → {FIX_RECIPE_POINTER[d]}' for d in defects if d in FIX_RECIPE_POINTER]
    if ptr:
        return '【本章缺陷类型不在自动映射内 —— 只给指向，去查那一节】\n' + '\n'.join(ptr)
    return f'{TODO}：缺陷类型 {"、".join(defects)} 无对应配方，按 deai-entry 第 3 步索引表查】'




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
    fills['batch_global'] = _batch_global_auto(proj, chapter_no, ol_chaps)

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

    # ②c v9.0 的两个新硬约束：**读者在等什么** + **这一章该发生什么**
    #    这两个都在"怎么写"之前 —— 位置邻近 `contract` 是按显著性设计的：
    #    负向/硬约束在前，正向素材在后。
    fills['promise_debt'] = _promise_debt(proj, chapter_no)
    fills['story_ledger'] = _story_ledger(proj, chapter_no)
    # ★ v11.0：后果链（L1 第二根轴）。抽不到 → TODO → `--check` 拒绝开工，
    #   与 story_ledger / concrete_list 同一个既定模式（"记得填"变成"编译不过"）。
    fills['consequence_chain'] = _consequence_chain(proj, chapter_no)

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

    # ⑤ 需要理解才能产生的 —— **v9.2 起改为"脚本给近似值 + 标注可覆盖"**
    #    这四格是批量自动化的最大障碍（每章 4 次人工介入）× 10 章 = 40 次，
    #    那就不是自动化。脚本抽得到就抽，抽不到才 TODO（fail-closed）。
    fills['ledger'] = _ledger_auto(proj, chapter_no, ol_chaps.get(chapter_no, ''))
    fills['world_pack'] = _world_pack_auto(proj, chapter_no, ol_chaps.get(chapter_no, ''))
    fills['author_shot'] = _author_shot_auto(proj, chapter_no)
    # ★ 实物清单（v7.0.0）：脚本可自填——从本章的 `_meta` 文件抽，**不用主编手抄**。
    #   抽不到 → TODO → `--check` 拦住（本体层无脚本判据，只能靠编译期强制）。
    fills['concrete_list'] = _concrete_list(proj, chapter_no)
    # ★ 加法层两槽（2026-10-09）：**脚本拿不到**（"按本章偏低的项挑哪几招""按'时刻'挑哪
    #   1–2 段范本"都是判断，不是抽取）→ 给 TODO 标记，由 `--check` 拦住未填的包。
    #   与 `lesions` / `author_shot` 同一个既定模式：**把"记得填"变成"编译不过"**。
    fills['additive_menu'] = _additive_menu_auto(ol_chaps.get(chapter_no, ''))
    fills['human_exemplar'] = _human_exemplar_auto(proj, chapter_no,
                                                   ol_chaps.get(chapter_no, ''))
    # ★ 本书最佳段落（v7.2.0）：**"挑哪几段"是判断不是抽取** → 与加法层两槽同一模式。
    #   治的是"**改写目标是指标，不是样例**"：给 Agent 一个**文本级的参照**（"长成这一段
    #   这个样子"），而不是一串可凑的指标区间（"把句长提到 23"）。
    #   与 `human_exemplar` 是**两个来源**：那是**外部**人类范本（拉开距离），
    #   这是**本书自己的**已定稿好段落（更贴近"这一本的味道"，且**零采集成本**）。
    #   ⚠️ 分类键 `缺闲笔`/`章末落笔`/`对白声口`/`本体层具体性`/`叙述者立场` 是**逐字
    #     接口契约**（跨 agent 共享），写进提示里是为了让主编**按缺陷类型**挑，不是随手抄一段。
    fills['best_of_book'] = _best_of_book_auto(proj, chapter_no,
                                               ol_chaps.get(chapter_no, ''))
    fills['lesions'] = _lesions_auto(proj, chapter_no)
    fills['diagnosis'] = (f'{TODO}：诊断：{{一句话}} / 本稿必须不再犯：{{一句话}}】'
                          if rewrite else '')
    # ★ fix_recipe（2026-10-21）：按工单的缺陷类型抽 rewrite-playbook 对应节的核心配方。
    #   非 rewrite 任务也给 —— 初稿质检后马上要改，配方那时就该在手边。
    fills['fix_recipe'] = _fix_recipe(proj, chapter_no)
    # ★ 新增：脚本可自填的四项（0 reasoning token）
    fills['rhythm_base'] = _rhythm_base(proj, chapter_no)
    fills['quota_card'] = _quota_card()
    # ★ 口吻宪法（v7.6.0）：**常驻**——每章都发同一份，从圣经抽，**不手抄**。
    #   抽不到 → TODO → `--check` 拦住（口吻宪法没有脚本能"补"出来的部分，
    #   只能靠编译期强制；见 `_voice_charter` 的说明）。
    fills['voice_charter'] = _voice_charter(proj)
    # ★★★ v9.0：工艺卡（替掉"读指南"这个机制本身）。
    #   挑卡规则全在 `_craft_cards()` 里（按章型换卡）——**不在文档里另存一份**。
    fills['craft_cards'] = _craft_cards(proj, chapter_no, ol_chaps.get(chapter_no, ''))
    vp, lack = _voice_preset(ol_chaps, chapter_no)
    if lack and TODO not in vp:
        # 让问题在**编译时**就可见，而不是等 --check 才报
        vp += (f'\n\n> ⚠️ 缺三行核心：{"、".join(lack)}'
               f'——文档要求"没写这三行 = 写前分析未完成"。'
               f'**回到细纲补**（单一事实源在细纲，不在这里补）')
    fills['voice_preset'] = vp
    return fills


def render(proj: Path, chapter_no: int, fills: dict, rewrite: bool = False) -> str:
    # ══ 头部（v11.1 重写）════════════════════════════════════════════
    # **砍掉了什么、为什么**（判据：删掉它，写手会不会变差？）
    #   · 「总上限 12000 字」——写手管不了预算，那是编译期的事；
    #   · 「校验：`--check ...`」——**这是给主编的命令**，写在包里会让写手以为要自己跑检查；
    #   · 「槽位顺序是有意的 + 作用域从大到小」——那是**维护者**看的排序理由，
    #     已完整写在 `SLOTS` 的注释里，不必每章重发一遍（每章重发 = 每章稀释一次）。
    # 换成三行**类型导读**：它回答写手开工前唯一需要知道的元问题——
    #   「这 23 段里，哪几段是要我动手的？」
    _n_ok = sum(1 for k, *_ in SLOTS if slot_kind(k) == '已定')
    _n_rest = len(SLOTS) - _n_ok
    parts = [f'# 第 {chapter_no} 章 · 任务包（{"改写" if rewrite else "新写"}）',
             '',
             f'> **本包 {len(SLOTS)} 段，只有最后一段要你动手。**',
             f'> 〔已定〕{_n_ok} 段 = 已经定好的事实，**不许再议，照它写**；'
             f'〔素材〕／〔红线〕{_n_rest} 段 = 给你用的，**不用逐条响应**。',
             '> **没有〔产出〕标记的段落，都不是要你"检查"的项目。**'
             '（清单越长执行率越低——本项目已实测过这一条）',
             '']
    for key, title, budget, req, who in SLOTS:
        # ★ 不再"空则跳过"：**所有槽位一律产出**。
        #   旧写法 `if not body and not req: continue` 让 3 个槽位**永远不出现在包里**，
        #   而校验器只在 required 时报错 → 静默通过（实测生成物里出现 0 次）。
        #   现在缺内容就写一句显式的"不适用"，使「槽位数 == SLOTS 数」恒成立。
        body = (fills.get(key, '') or '').strip() or '（不适用——本任务无此项）'
        parts.append(SLOT_OPEN.format(key=key, budget=budget, req=int(req)))
        # ★ v11.1：标题带类型前缀（见 `SLOT_KIND` 的说明）——
        #   这是**权重分层**：让"已定"和"素材"在视觉与语义上都退到背景，
        #   把显著性留给最后那一段"要你动手的"。
        parts.append(f'## 〔{slot_kind(key)}〕{title}')
        parts.append('')
        parts.append(body)
        parts.append('')
        parts.append(SLOT_CLOSE.format(key=key))
        parts.append('')
    # ★★★ 尾部（v11.1 重构）——**位置就是它的全部机制**。
    #   为什么在最后：生成紧跟在最近的上下文之后；brief 有 1 万字，中段会被稀释
    #   （lost-in-the-middle），**最后 500 字才决定它从哪里起步**。
    #
    #   三条旧的"开写指令"（回读声音样本 / 一句长句 / 不自评）**内容全部保留**，
    #   只是从"三条并列"改成"三件事"（想／写／交）——因为写手真正要产出的就是这三样，
    #   而此前它面对的是 51 个 `##` 标题，无法分辨哪一段是"动作"。
    #
    #   ★ 新增「一次豁免」（张力出口）：纯执行会让输出落到"安全且均匀"那一侧
    #     （本项目的实测：配额只列"要补什么"，会让明喻 1.1→2.38、身体部位 0.5→1.19 —— 
    #      **注意力是零和的，全都要 = 全都糊**）。给它**一次带理由的违反权**，
    #     它就从"合规模式"切到"判断模式"；而理由本身是数据：
    #     **连续三章豁免同一项 = 那条配额错了，该改卡。**
    parts += [
        '---',
        '',
        '## 现在开始写（只做三件事；上面每一段都不是清单）',
        '',
        '| # | 你要产出什么 | 靠什么 |',
        '|---|---|---|',
        '| **1 想** | 六格：删／眼／弃／留／开，＋ 一行「我放弃了」 | 下面那张卡 |',
        '| **2 写** | 正文。**第一句照「口吻宪法 · 声音样本」起手**（不要先写天色、时间、天气）；'
        '中后段要有一句**「读者会被按在水里」的长句**（一整个处境塞进去，中间不断） | 声音样本 · 实物清单 · 后果链 |',
        '| **3 交** | 直接交。不自评、不回看指标、不盘它像不像 | —— |',
        '',
        '### 一次豁免（可选，本章最多一次）',
        '',
        '「配额卡」里的任何**一条**，这一章你都可以不遵守 —— 但交稿时要写清两件事：',
        '**违反了哪一条**／**为什么这一章非这样不可**。不写 = 没有豁免，配额照常。',
        '',
        '> 豁免**不会被罚**。被罚的是「什么都不写，却什么都没做到」——那才是表演式合规。',
        '> ⚠️ 豁免**只在配额卡范围内**：字数／硬性句式／后果链／三态标记**不在豁免范围**。',
        '> 📌 连续三章豁免同一项 → 那不是你的问题，是**那条配额本身错了**，批末体检时改卡。',
        '',
        '> 三条都做到，比"每一条都做到"更像人写的——**人不会在所有地方同时用力**。',
        '',
    ]
    # ★★★ v10.0「思考协议」——**必须是 brief 的最后一个字节**（见 `_thinking_card()` 的说明）。
    #   brief 尾部三条管"怎么落笔"，这一段管"**思考里先做什么**"；
    #   而思考发生在落笔之前 → **它的位置必须在三条之后**，否则起手式会被盖住。
    parts += [
        '---',
        '',
        _thinking_card(),
        '',
    ]
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

    # ══ 分层自检（v11.1）════════════════════════════════════════════
    # **为什么在 `--check` 里查，而不只写在 `audit_release` 里**：
    #   audit 是**发布前**跑（改 SKILL 的人），`--check` 是**每章编译后**跑（写作的人）。
    #   分层坏了，受害的是每一章的写手 —— 所以必须在**每章都过**的那道闸上查。
    # 判据与 `_demote_headings` / `render()` 里的两处改动一一对应：
    #   ① 每个槽位标题必须带类型标签（否则它就和"已定事实"混在一起）；
    #   ② 标题总数不许显著多于槽位数（否则是**卡的子标题没降级** → 又变回 51 段）。
    _n_tag = len(re.findall(r'(?m)^## 〔', text))
    _n_head = len(re.findall(r'(?m)^## ', text))
    if _n_tag < len(slots):
        probs.append(f'**有 {len(slots) - _n_tag} 个槽位标题没带类型标签**'
                     f'（〔已定〕／〔素材〕／〔红线〕）—— 标签是权重分层的唯一载体；'
                     f'缺了它，写手会把这一段也当"要检查的项目"')
    if _n_head > len(SLOTS) + 10:
        probs.append(f'**标题数异常**：{_n_head} 个 `##`（槽位只有 {len(SLOTS)} 个）—— '
                     f'多半是工艺卡的子标题**没降级**（`_demote_headings`），'
                     f'写手又会看到"几十条要求"')

    if not brief:
        print(f'\n  {path.name}')
        print(f'    槽位 {len(slots)}/{len(SLOTS)}｜合计 {total} 字（上限 {TOTAL_BUDGET}）')
        _kinds = {}
        for k, *_ in SLOTS:
            _kinds[slot_kind(k)] = _kinds.get(slot_kind(k), 0) + 1
        print('    分层：' + '／'.join(f'〔{k}〕{v}' for k, v in _kinds.items())
              + f'｜标题 {_n_head} 个｜**要写手动手的 1 段**（「现在开始写」）')
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
    # ★ v9.2：批量预编译（**自动化的入口**）。
    #   为什么单独一个开关：批量模式下 agent 不该"写一章、编一章"——
    #   那会把编译等待和写作混在同一段上下文里，中途失败还要从头来。
    #   正确形态：**先把 N 章的 brief 全编译好并逐个自检**，然后 agent 只需
    #   "读 brief → 写"，一章接一章。详见 `guides/batch-mode.md`。
    ap.add_argument('--batch', metavar='A-B',
                    help='批量编译第 A–B 章（例：6-15）')
    args = ap.parse_args()

    proj = Path(args.project)
    if not proj.is_dir():
        print(f'[错误] 目录不存在：{args.project}')
        sys.exit(2)

    if args.batch:
        m = re.match(r'^\s*(\d{1,4})\s*[-–~至]\s*(\d{1,4})\s*$', args.batch)
        if not m:
            ap.error('--batch 的格式是 A-B（例：6-15）')
        a, b = int(m.group(1)), int(m.group(2))
        if b < a:
            a, b = b, a
        out = proj / PKG_DIR
        out.mkdir(parents=True, exist_ok=True)
        print('=' * 60)
        print(f'批量预编译：第 {a}–{b} 章（共 {b - a + 1} 章）')
        print('=' * 60)
        bad = 0
        for n in range(a, b + 1):
            fills = build(proj, n, rewrite=args.rewrite)
            text = render(proj, n, fills, rewrite=args.rewrite)
            (out / f'第{n:02d}章-任务包.md').write_text(text, encoding='utf-8')
            todo = [k for k, _t, _bb, r, w in SLOTS if r and TODO in fills.get(k, '')]
            zs = _zishu(text)
            over = zs > TOTAL_BUDGET
            if todo or over:
                bad += 1
            flag = '✓' if not (todo or over) else '✗'
            tail = (f'待填 {len(todo)}：{"、".join(todo[:4])}' if todo else '齐备')
            if over:
                tail += f'｜⚠ {zs} 字 > 上限 {TOTAL_BUDGET}'
            print(f'  {flag} 第{n:02d}章  {zs:>5} 字  {tail}')
        print('-' * 60)
        if bad:
            print(f'✗ {bad} 章未齐备 —— **先补齐再开写**（批量模式不跳过缺料的章）')
            sys.exit(1)
        print(f'✓ {b - a + 1} 章全部齐备 —— 可以按序开写（协议见 guides/batch-mode.md）')
        sys.exit(0)

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
