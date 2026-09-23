# 指南读取索引（Guide Index）

**"动作前必读"的完整映射表。** 执行契约第三条铁律：关键动作前必须读对应的指南，不读不动手。

本 SKILL 的所有引用路径相对**本 SKILL 目录**（安装后通常在 `~/.workbuddy/skills/j-novel/`）。找不到文件时，先定位 SKILL 目录再读，**不要因为找不到就跳过**。

> **改 SKILL 本身、或排查"规则写了却没生效"时** → 先读 `references/skill-mechanics.md`（三层加载模型：SKILL.md 全量注入 / flows 按阶段读 / guides 按动作读；子代理隔离性；常见反模式）。**放错层级 = 规则不存在。**

---

## 动作 → 必读指南

| 动作/阶段 | 【必须】先读 | 【标准】参考 |
|----------|------------|------------|
| 采访任何一轮 | `guides/interview-engine.md` | `flows/phase1-interview.md` |
| **作者说出具体剧情设想**（"第X章他……""我想让他……""最后他……"）—— **P1 采集** | **`guides/plot-scaffold.md`**（脚手架五问：定位／内容／因果／情绪／**画面**；自动触发话术见第三节 3.1 B） | `flows/phase1-interview.md`（触发时机与产物） |
| **Phase 2 生成大纲/细纲时**（若存在 `07-剧情脚手架.md`）—— **P2 落点** | `07-剧情脚手架.md`——**逐条找落点或标「未采用（原因）」；脚手架优先于自由发挥** | `guides/plot-scaffold.md` 第四节 |
| **Phase 3 写作期：缺剧情方向／作者想调整走向／正文偏离了条目**—— **P3 续航** ★ | **`guides/plot-scaffold.md` 第五节**（意图浓度判据／四条触发点／**方向对齐四步**／建议质量要求／Phase 3 例外边界） | `07-剧情脚手架.md`（浓度 + 对齐记录 + 条目来源） |
| **Phase 4/5 验收：脚手架对账** | `guides/plot-scaffold.md` 第六节（状态推进／已偏离开原因／作者原创没被静默改掉） | `07-剧情脚手架.md` 对账表 |
| 采访收尾（生成标题） | `guides/title-guide.md` | — |
| 规划章节结构（超长篇 2000+ 章） | `guides/plot-structures.md` + **`guides/volume-arc-planning.md`**（卷/弧/市场验证/锁死留白） | `guides/outline-template.md` |
| 写人物档案 | `guides/character-template.md` | `guides/character-building.md` |
| 写故事圣经 | `guides/bible-template.md` | — |
| **创建/更新创作台账** | `guides/creation-ledger.md` | — |
| **每章动笔前（seam 重读）** | `05-创作台账.md`（五段全文） | `guides/creation-ledger.md` |
| **动笔写任何一章前** | `guides/writing-mindset.md` + **`guides/concrete-voice.md`** | `guides/chapter-template.md` |
| **定章末型 / 定开场方式 / 定情绪载体（写细纲时就要做）** | **`guides/narrative-craft.md`**（人类作者的**决策逻辑**：章末四型+实测配比／日常进入型开场／结构级废笔／称呼即关系刻度／异质材料插入／情绪的外部介质／排版即节奏，**每项附真实人类原文**） | `guides/hook-techniques.md`（四型怎么落笔）· `guides/chapter-craft.md`（开场类型判定） |
| 规划/写作章首引子（仅新起型）与章尾落点、判定开场类型 | `guides/hook-techniques.md`（**章尾四型**：信息结算 30% / 关系余韵 27% / 喜剧反转 23% / 悬念 13%）+ `guides/chapter-craft.md`（「续接优先」+ 日常进入型） | — |
| 写对话 | `guides/dialogue-writing.md`（含**三之二·称呼即关系刻度**） | — |
| **动笔前想"往里加什么"（加法层）** | **`guides/humanize-toolkit.md`**（30 招内容注入：私人细节／情绪体感化／人性灰度／主角必须犯错／句式情绪同步／感官偏见化／信息碎玻璃化／减空洞加颗粒…**每招带改前改后对照**，并附 3 部人类的实测裁决） | 按类型选重点（悬疑/言情/玄幻/都市/通用 五档） |
| **质检后扫禁用词与模板句式** | **`guides/ai-cliche-blacklist.md`**（分级禁用词／AI 模板句式清洗表／小说专属万能情绪模板／模板道具清单／误伤警告表／**实测驳回表**） | 跑 `scripts/check_aistyle.py`（含新增的**半角引号硬闸门**与万能情绪模板提示） |
| **从零开始完整流程**（七轮采访 + 圣经 + 正文） | **`reasonix-novel-weaver`** | — |
| **高效出稿**（质量可接受、追求速度） | **`reasonix-novel-weaver-lite`** | — |
| **章节质检后（脚本已全绿）** | **`guides/review-agent.md`**（独立质检子代理：同构／删除／作者在场／用力过猛 四项测试） | — |
| **每章质检** | `guides/ai-taste-selfcheck.md` + `guides/hard-style-check.md` + `guides/continuity-check.md` + `guides/human-quota.md` + **`guides/human-rhythm.md`** | 运行 `scripts/check_repetition.py`（重复）+ `scripts/check_aistyle.py`（词汇层指纹）+ **`scripts/check_human_rhythm.py`（句法层节律，退出码 1 = 不合格）** |
| **质检后给读数定档** | `guides/confidence-binding.md` | — |
| **读数「晃」/ 重试换路** | `guides/confidence-binding.md` | — |
| **节律/词汇检测超标后修改** | **`guides/rewrite-playbook.md`**（脚本报哪项超就翻哪节，按招改） | `guides/human-rhythm.md`（规则原理解释） |
| **"读着还是 AI 味"（表层指标已达标）** | **`guides/post-polish.md`**（后精修 4 工序：长句缝合/定指替换/长对话补足/节奏起伏）——治**深度分布指标**（超长句占比/p90/节奏CV/中文量词/长对话/数字） | `guides/concrete-voice.md`（具体性原理） |
| AI 味重需清洗 / 修改前的指令措辞 | `guides/deai-workflow.md` + **`guides/occupancy-rewrite.md`** | — |
| **生成细纲 / 章节任务卡 / 派发子代理任务包**（这些本质都是"对 LLM 的写作指令"） | **`guides/prompt-engineering.md`**（5 原则，防止指令本身诱导 AI 味——你写"补充细节"它就用破折号补刀，你写"写具体"它就写"一个人"） | — |
| **改 SKILL 规则 / 写任何新的写作指令前** | **`guides/prompt-engineering.md`** | — |
| 字数不足需扩充 | `guides/chapter-craft.md`（扩充技巧） | — |
| 故事平淡需爽点 | `guides/thrill-panel.md` | — |
| **章节边界连续性（批次验收）** | `guides/continuity-check.md`（钩子桥接/场景重述红线） | 运行 `scripts/check_continuity.py`（退出码 1 = 有悬空钩子需复查） |
| **并发写作（默认走链式流水线）** | **`guides/chained-pipeline.md`**（并行单位 = **工序**非章节；接口契约冻结 ＋ **边界冻结**；笔手/磨手/主编三角色；**主编零正文**；缺陷路由；领先上限 3） | `guides/parallel-workflow.md`（批次闸门/台账/锚点滚动/交叉互检） |
| **批次边界 / 每章写后（流水线闸门）** | **`guides/low-cost-mode.md` 第三节**（三道闸门：章闸每章·窗闸每章滚动 3 章·批闸每批；判据「**发现缺陷的时点 ≤ 缺陷被定稿的时点**」） | **窗闸**：`check_repetition.py --all <项目> --window 3 --brief --imagery`<br>**声音漂移（三层锚）**：`check_aistyle.py --all <项目> --drift --brief --window 3 --base <批锚> --vol-base <卷锚> --book-base <全书锚>`（★ 只给批锚时**累积漂移在定义上不可见**）<br>**边界冻结**：`make_handoff.py <项目> --check --window 3` |
| **写前（并行/链式必做）· 契约自检** | **`flows/phase2-planning.md` 细纲规格的「接口契约（冻结 · 机器可读）」** —— 契约是并行写作的**接口定义**：第 N+1 章写手看不到第 N 章正文，只能靠它。「进入·X」必须等于上一章「退出·X」 | 跑 `scripts/check_contract.py <项目目录>`（**退出码 1 = 先修契约再派发**；此刻还没有正文要改，是最便宜的修复点） |
| **批末（每批一次）· 锚点滚动** | `guides/parallel-workflow.md` 第六节 —— 从**本批已定稿章**抽 1–2 段最代表文风的原文，更新**下一批细纲**的「文风锚点示例」。**三层锚**：全书锚（第 1 章，**永不滚动**）／卷锚（每卷第 1 章）／批锚（本批首章，滚动） | 跑 `check_repetition.py --all --imagery`（意象配额）+ `check_contract.py <项目>`（契约齐备兜底） |
| **意象配额（防长篇自我复制）** | `guides/bible-template.md` 四·五「意象使用记录」——**同一意象每卷 ≤4 次，且至少有一次"该出现时缺席"** | 跑 `check_repetition.py --all <项目> --imagery`（0 token；单字意象会明确标注"无法计数、未检查"，不静默通过） |
| **主编取「上一章真实结尾」/ 缝合边界** | **`scripts/make_handoff.py`**（从正文裁出**交接卡**：上章真实结尾 ＋ 本章首段/末段 ＋ 指纹）——**主编读卡不读正文**，这是 low 模式最大的一笔省 | `guides/chained-pipeline.md` 第五·二节（主编零正文）· 第六节（缝合 SOP） |
| **批次放行前（硬性）** | 跑 `scripts/check_batch_gate.py <项目目录>`（**退出码 0 才许继续**；查乱序完成/字数/质检痕迹/台账推进/章节边界） | — |
| **派发子 Agent 前** | `guides/subagent-brief.md`（任务包标准模板：工具权限/绝对路径/必调子技能/排班/**三态台账条目**/**接口契约**/**世界设定包**/诊断句） | ★ **任务包由编译器产出，不要手工拼**：`scripts/make_task_package.py <项目> --chapter N --write`（脚本能拿到的全自动填）→ `--check --all` 校验（**缺槽位 / 超预算 / 待填残留 → 拒绝开工**）。`guides/chained-pipeline.md` |
| **控制"每次调用带多重上下文"**（成本第一优先） | `guides/token-efficiency.md` —— 实测**每次调用平均重发 149,675 tokens**（中位），这才是成本大头（不是调用次数） | `make_task_package.py` 的**槽位预算**（总上限 5000 字）· 主编零正文（`make_handoff.py`）· 分层锚（只给该给的那一层） |
| **每章写前（世界切片）** | 圣经 `一、世界观手册`——裁出本章的**场景卡／规则与边界／专有名词表**；并行模式裁 500–800 字**内联进任务包** | `guides/bible-template.md`（密度判据＋反模式）· `guides/subagent-brief.md`（「世界设定包怎么裁」） |
| **每章收尾（设定回流）** | 把本章新造的专有名词登记回圣经「专有名词表」（含首次出现章号） | 跑 `scripts/check_worldbuilding.py <项目目录>`（设定激活率／未登记新词） |
| **低费用模式（costMode: low）** | **`guides/low-cost-mode.md`（唯一事实源）**——**批量推进档**：① 组织形式走**链式流水线 + 批次**（不是串行）② 机械质检**全跑脚本** + 判断项**合并成 1 次** ③ **人味项目一个不砍** ④ **第三节**专治流水线接缝（主编零正文／三道闸门／边界冻结／只向前修／领先上限 3） | `guides/quick-reference-card.md`（必读文件降为一页卡）· `guides/chained-pipeline.md`（批量组织） |
| **派发子代理 / 子代理写作前** | **`guides/quick-reference-card.md`**（**子代理默认只读这一页**，不读全套 guide——含成本配额卡/硬指标九项+深度七项+硬性句式/情绪配比/人味配额/改写顺序/成本纪律） | `guides/subagent-brief.md`（任务包内联配额卡） |
| **动笔前（治"意义层"AI 味——指标测不到的那一层）** | **`guides/human-exemplars.md`**（人类范本片段库，**按"时刻"组织**：叙述者跳出来／自由间接引语／**故意不解释**／纯闲笔／群体噪音／动作与情绪脱钩／现实锚定／对话不完美／降格幽默／生理写实。**给原文，不给规则**） | 配额卡【四、加法配额】 |
| **写完后的"不可 hack 判据"** | **删除测试**（随机删 3 处细节，故事是否受损）· **找错测试**（主角哪一步判断错了）——**这两项取代不了，也 hack 不了** | `guides/human-exemplars.md` 使用清单 |
| **NSFW 模式（contentMode: nsfw）** | **`guides/nsfw-mode.md`** + 注入 `references/prompts/infinite-gen-3.md` | — |
| **每一次 LLM 调用前（成本纪律，铁律九，两种模式都适用）** | **`guides/token-efficiency.md`**（成本 ≈ 调用次数 × 上下文；一次跑完脚本/一次批量改写/不重读/子代理只读速查卡） | 跑 `scripts/audit_tokens.py --traces`（读平台轨迹真实 usage：★ **每次调用平均重发上下文** >100k/次 = 失守；再看调用数是否超标） |
| **想量化"这套流程到底花了多少 token"** | 跑 `scripts/audit_tokens.py --traces`（读平台轨迹 `~/.workbuddy/traces` 的真实 usage：prompt/cacheRead/output + 每次调用平均上下文 + 会话长度分布） | 判据见 `guides/token-efficiency.md` 第四节 |
| **改动本 SKILL 之后 / 发布或冻结版本之前** | 读 `references/skill-mechanics.md` 第五节（维护检查清单 + 判据） | 跑 `python scripts/audit_release.py --regress`（引用完整性/脚本语法/阈值一致性/调用链闭合/结构完整性/残留检查/**跨脚本一致性**/分离回归，**8 项**一次跑完；退出码 0 才算通过）<br>**动了任何检查项后，再跑 `python scripts/test_guards.py`**（故障注入；"加了守卫"≠"守卫有效"，用例数以运行时输出的 n/n 为准） |
| **作者要改设定／改剧情／改结局（写到一半想动大纲）** | **`guides/change-management.md`**（五步变更流程：登记 → 影响评估 → 分层执行 → 留痕 → 验证；含"变更三深度"帮作者选代价） | `guides/volume-review.md`（卷末复盘产出的 P0 修订计划走同一套流程） |
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
3. **子代理按精简口径，不是本索引全量**：子代理任务包给 **① 内联的关键内容（给原文不给路径）② 速查卡 ③ 本批 1–2 本 guide**——**不要附"对应动作的必读路径"清单**，那会把 10 本 guide ≈ 40k 塞进子代理上下文，并跟着它每一次调用重发。完整判据见 `guides/subagent-brief.md`（唯一事实源）。任务包缺上述三样 → 拒绝开工并向主编要
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
| **八 · 注册分离** | 正文不得含细纲记号/质检字段/未展开缩写 | 每章步骤 6 注册审计 | 正文里出现【场景1】或评分 |

**外加两把刀：**
- **崩坏红线**（execution-contract 第六节）：连续 3 章同钩子 / 同段改 ≥3 次 / deai↔pad 交替 ≥2 轮 / 子代理无数字回报 → 五拍恢复
- **占位式改写**（`guides/occupancy-rewrite.md`）：指令说"这里放 Y"，不说"不要写 X"
