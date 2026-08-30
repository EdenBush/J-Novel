# 指南读取索引（Guide Index）

**"动作前必读"的完整映射表。** 执行契约第三条铁律：关键动作前必须读对应的指南，不读不动手。

本 SKILL 的所有引用路径相对**本 SKILL 目录**（安装后通常在 `~/.workbuddy/skills/j-novel/`）。找不到文件时，先定位 SKILL 目录再读，**不要因为找不到就跳过**。

---

## 动作 → 必读指南

| 动作/阶段 | 【必须】先读 | 【标准】参考 |
|----------|------------|------------|
| 采访任何一轮 | `guides/interview-engine.md` | `flows/phase1-interview.md` |
| 采访收尾（生成标题） | `guides/title-guide.md` | — |
| 规划章节结构（超长篇 2000+ 章） | `guides/plot-structures.md` + **`guides/volume-arc-planning.md`**（卷/弧/市场验证/锁死留白） | `guides/outline-template.md` |
| 写人物档案 | `guides/character-template.md` | `guides/character-building.md` |
| 写故事圣经 | `guides/bible-template.md` | — |
| **创建/更新创作台账** | `guides/creation-ledger.md` | — |
| **每章动笔前（seam 重读）** | `05-创作台账.md`（五段全文） | `guides/creation-ledger.md` |
| **动笔写任何一章前** | `guides/writing-mindset.md` + **`guides/concrete-voice.md`** | `guides/chapter-template.md` |
| 规划/写作章首引子（仅新起型）与章尾钩子、判定开场类型 | `guides/hook-techniques.md` + `guides/chapter-craft.md`（「续接优先」） | — |
| 写对话 | `guides/dialogue-writing.md` | — |
| **每章质检** | `guides/ai-taste-selfcheck.md` + `guides/hard-style-check.md` + `guides/continuity-check.md` + `guides/human-quota.md` + **`guides/human-rhythm.md`** | 运行 `scripts/check_repetition.py`（重复）+ `scripts/check_aistyle.py`（词汇层指纹）+ **`scripts/check_human_rhythm.py`（句法层节律，退出码 1 = 不合格）** |
| **质检后给读数定档** | `guides/confidence-binding.md` | — |
| **读数「晃」/ 重试换路** | `guides/confidence-binding.md` | — |
| **节律/词汇检测超标后修改** | **`guides/rewrite-playbook.md`**（脚本报哪项超就翻哪节，按招改） | `guides/human-rhythm.md`（规则原理解释） |
| AI 味重需清洗 / 修改前的指令措辞 | `guides/deai-workflow.md` + **`guides/occupancy-rewrite.md`** | — |
| **生成细纲 / 章节任务卡 / 派发子代理任务包**（这些本质都是"对 LLM 的写作指令"） | **`guides/prompt-engineering.md`**（5 原则，防止指令本身诱导 AI 味——你写"补充细节"它就用破折号补刀，你写"写具体"它就写"一个人"） | — |
| **改 SKILL 规则 / 写任何新的写作指令前** | **`guides/prompt-engineering.md`** | — |
| 字数不足需扩充 | `guides/chapter-craft.md`（扩充技巧） | — |
| 故事平淡需爽点 | `guides/thrill-panel.md` | — |
| **章节边界连续性（批次验收）** | `guides/continuity-check.md`（钩子桥接/场景重述红线） | 运行 `scripts/check_continuity.py`（退出码 1 = 有悬空钩子需复查） |
| 规划/执行并发写作 | `guides/parallel-workflow.md` | `flows/phase3-writing.md` |
| **派发子 Agent 前** | `guides/subagent-brief.md`（任务包标准模板：工具权限/绝对路径/必调子技能/排班/**三态台账条目**/诊断句） | `guides/parallel-workflow.md` |
| 卷终维护圣经 | `guides/bible-template.md`（状态机） | `guides/creation-ledger.md`（台账对账） |

---

## 子技能触发表（专业版，已安装时主动使用）

**内建指南是"轻量兜底"，已安装的子技能是"专业版"。** 触发条件满足时，**必须**通过 Skill 工具加载对应子技能执行（以该技能的人格身份工作），不能只用内建指南应付——子技能经过深度打磨，有完整的专用方法论。内建指南仅在子技能未安装时作为兜底。

### A. 固定排班制（检查型工具——不由自检结论决定，到点必调）

**Agent 的自检结论不可信**（"我认为没问题"恰恰可能是 AI 味的来源）。以下检查型工具固定排班，自检"没问题"不是跳过理由；检查通过也是结果，须记入 `04-质检档案.md`。

| 排班 | 必调子技能 |
|------|-----------|
| 每章 | `reasonix-novel-hard-check`（机械质检）+ `reasonix-novel-dialogue-master`（**对话打磨，AI 味重灾区，每章必调**） |
| 每章（轮换） | `reasonix-novel-mood-composer`（偶数章加查情绪） |
| 锚点章/高潮章 | `dialogue-master` + `mood-composer` 全调 |
| 每 3-5 章 | `reasonix-novel-rhythm-check`（节奏诊断报告） |
| 每章（自检后） | 读者视角自检（内建版 Reader Simulator：走神点/太假对话/钩子强度三问）——卷终由主编组织完整 reader-sim |

### B. 症状触发制（修复型工具——自检发现问题才调，没病不治）

| 症状 / 时机 | 必调子技能 | 内建兜底（未装时） |
|------------|-----------|------------------|
| AI 味重（自评 ≥4 项 AI侧） | `reasonix-novel-deai`（六步深度清洗） | `deai-workflow.md` |
| 字数不足 | `reasonix-novel-pad`（专业补字） | 补字 SOP（chapter-craft） |
| 没毛病但差口气 | `reasonix-novel-write-master`（单点手术） | 六维诊断 |
| 平淡无聊 | `reasonix-novel-thrill-booster`（爽点注入） | `thrill-panel.md` |
| 卷终 / 完稿 | `reasonix-novel-reader-sim`（读者三层验证） | 读者体验抽检 |
| 卷终 | `reasonix-novel-bible-updater`（圣经活态维护） | 伏笔状态机 |

**执行规则**：
1. 排班制：到点必调，检查通过也是结果（记入质检档案）
2. 触发制：症状满足 → 必调；未安装 → 内建兜底并提示补装
3. 子技能执行完毕后，结果写回 `04-质检档案.md` / 状态台账（留痕）
4. 子技能的输出质量仍受执行契约约束（产物留痕、缺痕不合格）

---

## 执行规则

1. **【必须】指南 = 前置条件**：动作开始前完成阅读，不在动作中途补读
2. **一个动作对应多个必读**（如质检 = 三本）：全部读完再动手，不可只读其中一本
3. **子代理同样适用**：子代理任务包内必须附本索引中对应动作的必读路径；任务包不含 → 拒绝开工并向主编要
4. **记忆不可替代阅读**：哪怕你自认熟悉某本指南的内容，动作前仍要快速扫一遍——指南可能有更新，你的记忆可能滞后
5. **找不到文件**：先定位 SKILL 目录（`~/.workbuddy/skills/j-novel/` 或项目 `.workbuddy/skills/j-novel/`），仍找不到 → 停下询问，不跳过

---

## J-Novel 过程层四件套（速查）

Reasonix 的四条铁律管"产物合不合格"，这四件套管"长跑中还醒不醒"。

| 铁律 | 一句话 | 载体 | 违反的信号 |
|---|---|---|---|
| **五 · 三态标记** | 标 `?` 的不得当既成事实写 | `03-状态台账.md` 的「落地」列 + 伏笔表 | 台账有条目没标状态位 |
| **六 · seam 重读** | 每章边界重读创作台账五段 | `05-创作台账.md` | "最近重读章号"停在几章前 |
| **七 · 置信度绑定** | 读到「晃」禁止原路重走 | `guides/confidence-binding.md` | 同一章原样重写第三遍 |
| **八 · 注册分离** | 正文不得含细纲记号/质检字段/未展开缩写 | 每章步骤 6.5 注册审计 | 正文里出现【场景1】或评分 |

**外加两把刀：**
- **崩坏红线**（execution-contract 第六节）：连续 3 章同钩子 / 同段改 ≥3 次 / deai↔pad 交替 ≥2 轮 / 子代理无数字回报 → 五拍恢复
- **占位式改写**（`guides/occupancy-rewrite.md`）：指令说"这里放 Y"，不说"不要写 X"
