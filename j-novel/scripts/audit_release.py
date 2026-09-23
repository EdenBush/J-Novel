#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前 QA：一条命令跑完整个 SKILL 的体检。

用法:
    python scripts/audit_release.py                    # 在 SKILL 目录下运行
    python scripts/audit_release.py --root /path/to/j-novel
    python scripts/audit_release.py --regress          # 附分离回归（需人类/AI 样本，较慢）
    python scripts/audit_release.py --json

检查七项（标号 1–6 + 6.5；**加 `--regress` 则为 8 项**，与 guide-index.md 的说法一致）:
    1. 引用完整性      —— 所有 references/guides|flows|prompts/xxx.md 是否真实存在
    2. 脚本语法        —— 全部 .py 能否通过 py_compile
    3. 阈值一致性      —— 关键阈值在"脚本(唯一事实源) vs 文档"之间是否漂移
    4. 调用链闭合      —— 关键工具/规则是否 ≥2 处调用点（1 处 = 死工具）
    5. 结构完整性      —— SKILL.md frontmatter / 代码围栏配平 / 标题层级
    6. 残留检查        —— 已废弃的旧数字、旧说法是否清干净
    6.5 跨脚本一致性    —— 多脚本共用的词表/阈值/实现是否真的一致，
                           **含「声明-实现一致性」三层守卫**：
                             ⓪ 自检名单是否漏文件（wordcount 曾被漏掉）
                             ① 该 import 的共享实现是否 import 了
                             ② import 了是否**真被调用**（aistyle 曾 import 却不调用）
                             ②b 共用的词表是否只此一份（SIMILE_WORDS 曾两处）
                             ③ **import 后是否被本地 def 覆盖**（continuity 曾如此）
                             ④ **定义了是否真被调用**（`_all_docs()` 曾从未被调用）
                           ⚠️ 判据一律基于"剥掉注释与 docstring 后的代码"——
                              否则注释里提一句函数名就能骗过守卫（故障注入测出来的）。

退出码: 0 = 通过（可发布）；1 = 有阻塞项；2 = 无法运行（目录不对）
"""
import argparse
import compileall
import json
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- 关键工具清单
# 每项必须 ≥2 处调用点（注册处 + 实际触发处）。1 处 = 定义了但没接上 = 死工具。
# ══════════════════════════════════════════════════════════════════
# 规则传导表（2026-09-16 新增）
#
# **查什么**：子代理只读 `quick-reference-card.md`，所以凡在源指南里改的规则，
# 速查卡里必须有一份等价表述 —— 否则"改了指南"对子代理等于"什么都没改"。
#
# 每项 = (规则名, 源指南, 源标志正则, 速查卡标志正则)
# 速查卡允许换措辞，所以两侧用不同的等价正则。
# ⚠️ 新增/修改源指南里的关键规则后，**同步补一行到这里**，否则守卫管不到它。
# ══════════════════════════════════════════════════════════════════
PROPAGATION_RULES = [
    ('作者在场·自检法',      'narrative-craft.md', r'这里有一个人吗', r'这里有一个人吗'),
    ('作者在场·禁止同构',    'narrative-craft.md', r'禁止同构', r'禁止同构'),
    ('作者在场·必须挂钩',    'narrative-craft.md', r'付什么租', r'付什么租'),
    ('对话标签用「道」',      'dialogue-writing.md', r'道', r'用「\*\*道'),
    ('对话标签·上限',        'dialogue-writing.md', r'1/3|2/3|不要每句都挂', r'1/3|2/3|不要每句都挂'),
    ('单句成段·适用对象',    'narrative-craft.md', r'对话.*情绪重音|主要用在', r'一句一段'),
    ('单句成段·禁纯景物连排', 'narrative-craft.md', r'纯景物|空镜蒙太奇', r'纯景物|空镜蒙太奇'),
    ('毛边：判据制',         'human-quota.md', r'付房租|毛边', r'毛边'),
    ('删掉它故事变差了吗',    'human-quota.md', r'删掉它', r'删掉它|付什么租'),
    ('≥1 句 ≥100 字超长句',  'human-rhythm.md', r'100 字', r'100 字'),
    ('人类区间命中率',       'human-rhythm.md', r'命中率', r'命中率'),
    ('工艺四定',            'narrative-craft.md', r'章末型', r'章末型'),
    ('情绪三种写法配比',      'human-rhythm.md', r'三种写法|直说', r'三种写法'),
    ('称呼即关系刻度',       'dialogue-writing.md', r'称呼', r'称呼'),
    ('独立质检子代理',       'review-agent.md', r'四项测试|同构测试', r'同构测试|独立质检子代理'),
    # ── novel-humanize 融合（2026-09-16）────────────────────────────────
    ('私人细节·专属判据',     'humanize-toolkit.md', r'只有这个人|专属感', r'私人细节'),
    ('情绪体感化',           'humanize-toolkit.md', r'情绪体感化|他很X|他很 X', r'情绪体感化'),
    ('主角必须犯错',         'humanize-toolkit.md', r'主角必须犯错|判断错', r'主角必须犯错|判断错'),
    ('内容注入·挑 3-5 招',    'humanize-toolkit.md', r'3[–-]5 招', r'3[–-]5 招'),
    ('半角引号零容忍',        'ai-cliche-blacklist.md', r'半角双引号', r'ASCII'),
    ('破折号趋向清零',        'ai-cliche-blacklist.md', r'破折号', r'破折号'),
    ('万能情绪模板黑名单',     'ai-cliche-blacklist.md', r'空气凝固', r'空气凝固'),
    # ── 世界设定传导（2026-09-20）──────────────────────────────────────
    #    子代理只读速查卡 → 这四条世界纪律必须在卡里，否则对它不存在。
    ('世界设定包·必须内联',    'subagent-brief.md', r'世界设定包', r'世界设定包|世界一致性'),
    ('专名·禁止同义新词',      'subagent-brief.md', r'禁止另造同义新词', r'禁止另造同义新词|只用表内的词'),
    ('新词必须回流登记',       'subagent-brief.md', r'新增专名', r'新增专名'),
    ('硬设定是蓄能装置',       'subagent-brief.md', r'蓄能装置', r'蓄能装置|引信'),
    # ── 剧情脚手架（2026-09-21）────────────────────────────────────────
    #    作者画面是流水线上最保人味的素材：主编挖得到（plot-scaffold）、写手要保住（速查卡）。
    ('作者画面·给他不给解释',   'plot-scaffold.md', r'给画面，不给解释|不给解释', r'不要解释|不给解释'),
    ('作者画面·是作者的眼睛',   'plot-scaffold.md', r'作者自己的眼睛', r'作者自己的眼睛'),
    # ── 开场不许同质（2026-09-21）──────────────────────────────────────
    #    事故：两条规则（rewrite-playbook ②招 × chapter-craft 日常进入型）
    #    叠加被过度执行 → 三到五章开头都变成"时间＋环境空镜"。
    #    规则本身没错，错在**示范都是时间/环境起手且没有禁止同构**。
    ('日常进入≠环境空镜',      'chapter-craft.md',
     r'时间＋环境空镜|日常进入 ≠', r'开场不许同质|别和它同型'),
    # ── 章末不许公式化（2026-09-21）───────────────────────────────────
    #    事故：五章结尾全是"叙述 + 余味"，其中两章连着用「反正…」。
    #    闸门只查大纲的「章末型」字段（标签）→ 标签轮换了、手感没换，查不出来。
    ('章末·选型≠落笔',         'hook-techniques.md',
     r'选对「型」≠|选对.型.≠', r'章末不许公式化|换落笔形态'),
]


KEY_TOOLS = [
    'token-efficiency', 'skill-mechanics', 'audit_tokens', 'human-exemplars',
    'volume-arc-planning', 'chained-pipeline', 'check_batch_gate',
    'prompt-engineering', 'post-polish', 'low-cost-mode', 'nsfw-mode',
    '配额卡', '加法配额', '删除测试', '铁律九', '硬性句式',
    '现实锚定', 'costMode', 'contentMode',
]

# ---------------------------------------------------------------- 阈值一致性
# (标签, 脚本正则名, 文档中应出现的数字片段, 脚本内 hard 值)
# 脚本是唯一事实源；文档写的数字必须与脚本"同口径"（配额卡可更严，但不能更松）。
THRESHOLDS = [
    ('句首代词',  'pron_head',     ['15'],       15.0),
    ('句首引号',  'quote_head',    ['15'],       15.0),
    ('破折号',    'dash',          ['1.5'],      1.5),
    ('身体部位',  'body',          ['1.0'],      1.0),
    ('动作短语',  'act_density',   ['0.4'],      0.40),
    ('对话占比',  'dialog',        ['40'],       40.0),
    ('超长句占比', 'long_sent_pct', ['3'],        3.0),
    ('句长p90',   'sent_p90',      ['42'],       42.0),
    ('节奏CV',    'rhythm_cv',     ['0.19'],     0.19),
    ('量词密度',  'cn_measure',    ['10.5'],     10.5),
    ('长对话占比', 'long_dialog',   ['8'],        8.0),
    ('数字密度',  'digit_density', ['0.8'],      0.8),
    ('现实锚定',  'real_measure',  ['0.05'],     0.05),
    # 区间型指标用 lo 比对（2026-09-13 新增作者在场；see check_thresholds）
    ('作者在场',  'author_presence', ['2.5'],    2.5),
    # 2026-09-14 补：这三项此前完全没被审计（正是历史上被「底线当目标」害过的三项）
    ('平均句长',  'sent_mean',     ['23–37'],   18.0),
    ('情绪词',    'emotion',       ['0.25'],    0.25),
    ('明喻',      'simile',        ['0.8–1.5'], 0.80),
]

# 已废弃说法（出现即视为残留）
DEPRECATED = [
    '深度六项', '八条铁律', '每章动笔前先注入', '0.3–0.45',
    '对话至少 30%', '对话占比 ≥30%',
    # 第二批（规则真实性实测，三部范本 210 章证伪的"每章都必须 X"铁律）
    '每章必须有冲突', '前 20% 决定生死', '平均句长 ≥18 字',
    # 第三批（整层缺失排查；同属"底线当目标"型错误 + 名实不符）
    '每章至少 3 处单句成段', '每章 ≥3 处单句成段',
    # 第四批（2026-09-14 共享实现重构：旧模块名与旧口径说法）
    '_body.py', '统一走 _body', '检查六项',
]

# 解释语境标记：若废弃说法出现在这些词的邻近上下文里，说明是"历史说明/废止记录"，不是活规则。
# 只有"干巴巴地出现"才算残留（那意味着 Agent 会把它当现行规则执行）。
EXPLAIN_MARKERS = [
    '废止', '取消', '原规则', '原意', '原来的', '之前', '历史', '曾', '不再',
    '反转', '误判', '注水', '改为', '替代', '已改为', '旧', '实际结果',
    '原表述', '旧表述', '原来', '错的', '是编出来的', '不是作者', '实测',
]

CJK = re.compile(r'[\u4e00-\u9fff]')


def read_text(p: Path) -> str:
    """读文件并**归一化换行符为 \\n**。

    ⚠️ 2026-09-15 修（这个坑让一整层守卫静默失效）：
    本函数用 `read_bytes().decode()` 读，**不做换行转换**；而 Windows 上写的文件是 CRLF。
    于是所有形如 `` ```\\nxxx `` 的正则都匹配不到 `` ```\\r\\nxxx `` ——
    新加的"配额卡分叉"守卫因此**从头就是死的**（跑起来报 0 冲突，看起来在工作）。
    `Path.read_text()` 之所以没这个问题，是因为文本模式会自动做 universal newlines；
    用 bytes 读就必须自己转。**凡是要对文件内容做正则匹配的，都必须走归一化后的文本。**
    """
    raw = p.read_bytes()
    s = None
    try:
        _s = raw.decode('utf-8')
        if '\ufffd' not in _s:
            s = _s
    except Exception:
        pass
    if s is None:
        for enc in ('gb18030', 'gbk', 'big5'):
            try:
                s = raw.decode(enc)
                break
            except Exception:
                continue
    if s is None:
        s = raw.decode('utf-8', errors='ignore')
    # CRLF / CR → LF（否则以 \n 结尾的正则会全部失配）
    return s.replace('\r\n', '\n').replace('\r', '\n')


# ---------------------------------------------------------------- 文档缓存（性能）
# 阈值一致性(17项) 与 调用链闭合(19项) 都要反复遍历文档；
# 不缓存会重复读 36 遍（实测 51 个文件 × 36 ≈ 1800 次读，耗时 0.3s）。
#
# ⚠️ 2026-09-14 二次修：**这个函数原本定义了但从未被调用**（`grep -c '_all_docs('`
# 只有 1 次 = 定义处），"性能修复"根本没接上——与 SKILL 自己诊断的"假闸门"
# （声明了却没生效）完全同型。现在两处遍历真接上来了，并有"定义未调用"守卫盯着。
#
# ⚠️ 两个 scope 语义不同，不能混用：
#   'all'  = root 下全部 md（**含 SKILL.md**）——调用链闭合要算上 SKILL.md 的引用
#   'refs' = 只有 references/ ——阈值一致性只需在文档库里找数字
# 早期若把 refs 缓存套给调用链，会漏掉 SKILL.md 里的工具名，反而误报"仅 0 处"。
_DOC_CACHE = {}


def _all_docs(root, scope: str = 'all'):
    """按 scope 缓存并返回 {路径: 文本}。scope ∈ {'all', 'refs'}。"""
    if scope not in _DOC_CACHE:
        base = root if scope == 'all' else (root / 'references')
        _DOC_CACHE[scope] = {
            f: read_text(f) for f in base.rglob('*.md')
            if '.bak' not in f.name and '_archive' not in str(f)
        }
    return _DOC_CACHE[scope]


# ---------------------------------------------------------------- 1. 引用完整性
def check_references(root: Path):
    pat = re.compile(r'((?:references/)?(?:guides|flows|prompts)/[A-Za-z0-9_\-\.]+\.md)')
    bad = []
    for f in root.rglob('*.md'):
        if '.bak' in f.name or '_archive' in str(f):
            continue
        t = read_text(f)
        for m in set(pat.findall(t)):
            p = m if m.startswith('references/') else 'references/' + m
            if not (root / p).exists():
                bad.append((str(f.relative_to(root)), m))
    return bad


# ---------------------------------------------------------------- 2. 脚本语法
def check_syntax(root: Path):
    fails = []
    for f in sorted((root / 'scripts').glob('*.py')):
        ok = compileall.compile_file(str(f), quiet=2, force=True)
        if not ok:
            fails.append(f.name)
    # 清掉编译产物
    for d in (root / 'scripts').rglob('__pycache__'):
        for c in d.iterdir():
            try:
                c.unlink()
            except Exception:
                pass
        try:
            d.rmdir()
        except Exception:
            pass
    return fails


# ---------------------------------------------------------------- 3. 阈值一致性
def check_thresholds(root: Path):
    script = root / 'scripts' / 'check_human_rhythm.py'
    t = read_text(script)
    drift = []
    for label, name, doc_tokens, hard in THRESHOLDS:
        m = re.search(name + r"':\s*dict\(([^)]*)\)", t)
        if not m:
            drift.append(f'{label}：脚本里找不到指标 {name}')
            continue
        hm = re.search(r'hard=([0-9.]+)', m.group(1))
        if not hm:
            # 区间型指标（kind='range'）没有 hard，用 lo 作为"下限"比对
            hm = re.search(r'lo=([0-9.]+)', m.group(1))
        if not hm:
            drift.append(f'{label}：脚本 {name} 没有 hard/lo 阈值')
            continue
        if abs(float(hm.group(1)) - hard) > 1e-9:
            drift.append(f'{label}：脚本 hard={hm.group(1)}，体检表期望 {hard}（体检表需更新）')
        # 文档侧：至少一处出现该数字（走缓存，不再每次 rglob+read）
        found = False
        for _f, _t in _all_docs(root, 'refs').items():
            if any(tok in _t for tok in doc_tokens):
                found = True
                break
        if not found:
            drift.append(f'{label}：文档里找不到 {doc_tokens} —— 文档未同步脚本阈值')
    # check_aistyle 的硬性句式
    ta = read_text(root / 'scripts' / 'check_aistyle.py')
    m = re.search(r"HARD_STYLE_LIMITS\s*=\s*\{([^}]*)\}", ta)
    if not m or '0.12' not in m.group(1):
        drift.append('硬性句式：check_aistyle.py 里的 HARD_STYLE_LIMITS 缺少 0.12 同句线')

    # ── 数字型漂移守卫（2026-09-14 新增）────────────────────────────
    # **文档里写死的"计数"是最容易漂的一类**：本轮修文档时就现场踩到
    # 「我写 11 个已废弃说法、实际脚本里已经 14 个」。
    # 既然"数字"是可数的，就不该靠人记——改成机器核对：
    # 文档声称的数量必须等于脚本里的实际数量，否则直接报漂移。
    # ⚠️ 正则必须容忍 markdown 强调标记：文档里写的是 `**14 个**已废弃说法`，
    #    用 `(\d+)\s*个已废弃说法` 会**完全匹配不上** → 守卫静默失效。
    #    （又是"守卫自己也要被注入测试"的一例：第一次写就被这个坑到。）
    _count_claims = (
        (r'(\d+)\s*项\s*\*{0,2}关键阈值', len(THRESHOLDS), '关键阈值'),
        (r'(\d+)\s*个\s*\*{0,2}关键工具', len(KEY_TOOLS), '关键工具'),
        (r'(\d+)\s*个\s*\*{0,2}已废弃说法', len(DEPRECATED), '已废弃说法'),
    )
    for f, t in _all_docs(root, 'refs').items():
        for rx, actual, label in _count_claims:
            for m2 in re.finditer(rx, t):
                if int(m2.group(1)) != actual:
                    drift.append(f'{f.name}：写「{m2.group(1)} 个{label}」，'
                                 f'但脚本实际是 {actual} 个 —— 文档数字已漂')
    return drift


# ---------------------------------------------------------------- 4. 调用链闭合
def check_call_chain(root: Path):
    missing = []
    # 走缓存（scope='all' 保留原语义：**含 SKILL.md**，不是只扫 references/）
    docs = _all_docs(root, 'all')
    for kw in KEY_TOOLS:
        n = sum(1 for _f, _t in docs.items() if kw in _t)
        if n < 2:
            missing.append(f'{kw}（仅 {n} 处）')
    return missing


# ---------------------------------------------------------------- 5. 结构完整性
def check_structure(root: Path):
    issues = []
    skill = root / 'SKILL.md'
    if not skill.exists():
        return ['SKILL.md 不存在']
    t = read_text(skill)
    lines = t.split('\n')
    if not t.startswith('---'):
        issues.append('SKILL.md 缺 frontmatter')
    else:
        head = '\n'.join(lines[:40])
        for key in ('name:', 'description:', 'compatibility:'):
            if key not in head:
                issues.append(f'SKILL.md frontmatter 缺 {key}')
    infence, heads, top = False, 0, 0
    for l in lines:
        if l.strip().startswith('```'):
            infence = not infence
            continue
        if not infence:
            if l.startswith('## '):
                heads += 1
            elif l.startswith('# '):
                top += 1
    if infence:
        issues.append('SKILL.md 代码围栏未配平（有未闭合的 ```）')
    if heads < 8:
        issues.append(f'SKILL.md 顶层 ## 章节只有 {heads} 个（疑似结构被破坏）')
    if '每章最小必做清单' not in t:
        issues.append('SKILL.md 缺「每章最小必做清单」（全体系唯一权威清单）')
    return issues


# ---------------------------------------------------------------- 6. 残留检查
def check_residue(root: Path):
    """扫描已废弃说法。

    带"解释语境"的不算残留——文档里合法地记录"某规则已废止"是有价值的，
    真正危险的是废弃说法**干巴巴地出现**（Agent 会读成现行规则并执行）。
    """
    hits = []
    for f in root.rglob('*.md'):
        if '.bak' in f.name or '_archive' in str(f):
            continue
        lines = read_text(f).split('\n')
        for i, line in enumerate(lines):
            for kw in DEPRECATED:
                if kw not in line:
                    continue
                # 上下文窗口：本行 + 上 3 行 + 下 1 行
                # （上多下少：表格的说明性表头总在废弃说法之上，如「规则 | 原意 | 实际结果」）
                ctx = '\n'.join(lines[max(0, i - 3): i + 2])
                if any(mk in ctx for mk in EXPLAIN_MARKERS):
                    continue  # 解释性提及，放行
                hits.append(f'{f.relative_to(root)}:{i + 1}：残留「{kw}」（无解释语境）')
    return hits


# ---------------------------------------------------------------- 7. 分离回归
HUMAN_SAMPLES = [
    r'C:/Users/Administrator/Downloads/《超神机械师》+-+齐佩甲.txt',
    r'C:/Users/Administrator/Downloads/《惊悚乐园》（校对版全本+番外）作者：三天两觉.txt',
    r'C:/Users/Administrator/Downloads/我真没想重生啊-作者：柳岸花又明.txt',
]
AI_SAMPLES = [
    r'C:/Users/Administrator/WorkBuddy/2026-08-15-19-56-07/novel-output/反派少爷的生存法则/txt合订本/反派少爷的生存法则（合订本）.txt',
    r'C:/Users/Administrator/WorkBuddy/2026-08-01-20-52-39/chinese-novelist/20260801-205239-我，不会魔法，全法爷都破不了防/TXT/全书合订版.txt',
    r'C:/Users/Administrator/WorkBuddy/2026-08-30-01-14-18/novel-output/被三个校园顶级偶像包围了/被三个校园顶级偶像包围了-合订本.txt',
]


def _run(script: Path, target: str, python: str) -> int:
    try:
        r = subprocess.run([python, '-X', 'utf8', str(script), target],
                           capture_output=True, timeout=900)
        return r.returncode
    except Exception:
        return -1


def check_checks_separation(root: Path):
    """人类样本必须全过（exit 0）；AI 样本必须至少被一个闸门拦下（exit != 0）。"""
    python = sys.executable
    sc = root / 'scripts'
    problems, rows = [], []

    def first_existing(paths):
        return [p for p in paths if Path(p).exists()]

    _h, _a = first_existing(HUMAN_SAMPLES), first_existing(AI_SAMPLES)
    if not _h:
        problems.append(f'人类样本全部缺失（{len(HUMAN_SAMPLES)} 个路径都不存在）——回归未执行，不能算通过')
    if not _a:
        problems.append(f'AI 样本全部缺失（{len(AI_SAMPLES)} 个路径都不存在）——回归未执行，不能算通过')

    for p in _h:
        h = _run(sc / 'check_human_rhythm.py', p, python)
        a = _run(sc / 'check_aistyle.py', p, python)
        rows.append((Path(p).name[:14], h, a))
        if h != 0 or a != 0:
            problems.append(f'人类样本误杀：{Path(p).name[:20]} (rhythm={h}, aistyle={a})')

    for p in _a:
        h = _run(sc / 'check_human_rhythm.py', p, python)
        a = _run(sc / 'check_aistyle.py', p, python)
        rows.append((Path(p).name[:14], h, a))
        if h == 0 and a == 0:
            problems.append(f'AI 样本漏检（两个闸门都没拦住）：{Path(p).name[:20]}')

    for d in sc.rglob('__pycache__'):
        for c in d.iterdir():
            try:
                c.unlink()
            except Exception:
                pass
        try:
            d.rmdir()
        except Exception:
            pass
    return problems, rows



# ---------------------------------------------------------------- 9. 跨脚本一致性
def check_cross_script(root: Path):
    """两个脚本对同一份稿子必须给出同一结论——词表与区间不得各自为政。

    2026-09-14 新增：曾发现 check_aistyle 的 EMOTION_RANGE=0.55 与主脚本的 hi=0.50 冲突，
    且两边的 EMOTION_WORDS 差 8 个词，导致同一章一个判 FAIL 一个判通过。
    """
    issues = []
    h = read_text(root / 'scripts' / 'check_human_rhythm.py')
    a = read_text(root / 'scripts' / 'check_aistyle.py')

    def grab_words(txt):
        m = re.search(r'EMOTION_WORDS\s*=\s*\[(.*?)\]', txt, re.S)
        if not m:
            return set()
        return set(re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)))

    wh, wa = grab_words(h), grab_words(a)
    if wh != wa:
        if not wh or not wa:
            issues.append('情绪词表：至少一个脚本里找不到 EMOTION_WORDS')
        else:
            issues.append('情绪词表不一致：只在 check_aistyle = %s ｜ 只在 check_human_rhythm = %s'
                          % ('/'.join(sorted(wa - wh)) or '无', '/'.join(sorted(wh - wa)) or '无'))

    mh = re.search(r"'emotion':\s*dict\([^)]*hi=([0-9.]+)", h)
    ma = re.search(r'EMOTION_RANGE\s*=\s*\(\s*[0-9.]+\s*,\s*([0-9.]+)', a)
    if mh and ma and abs(float(mh.group(1)) - float(ma.group(1))) > 1e-9:
        issues.append('情绪词上限不一致：check_human_rhythm hi=%s vs check_aistyle %s'
                      % (mh.group(1), ma.group(1)))

    # ══════════════════════════════════════════════════════════════════
    # 声明-实现一致性（三层）—— 2026-09-14 新增，本块是这轮最重要的产出。
    #
    # **为什么需要它**：v4.6 修了 v4.5 的 6 个"假闸门"，但同一批修复里又引入了
    # 3 个同型问题，全是"**声明比实现走得快**"：
    #   · docstring 写"统一走 _shared.extract_body"—— extract_body 走了，**读文件仍硬编码 UTF-8**
    #   · 注释写"编码探测统一走 _shared.read_text"—— read_text 走了，**extract_body 被本地 def 覆盖**
    #   · 定义了 `_all_docs()` 缓存函数 —— **从未被调用**（"性能修复"没接上）
    # 这不是能力问题，是"改到一半以为改完了"。所以守卫不能只查"有没有那一行"，
    # 必须查 **① 在不在名单里 ② import 后有没有被覆盖 ③ 定义了有没有被调用**。
    # ══════════════════════════════════════════════════════════════════

    # ⓪ 所有脚本（含 wordcount/continuity）都必须在共享层名单里（检查在下方）
    _SHARED_FILES = ('check_human_rhythm.py', 'check_aistyle.py',
                     'check_chapter_wordcount.py', 'check_continuity.py')

    def _code_only(chunk: str) -> str:
        """剥掉三引号字符串与 # 注释，只留**真代码**。

        ⚠️ 这是故障注入测出来的教训：本文件的三处守卫最初直接扫原文，
        结果被**注释和 docstring 骗过**——
          · continuity 的 extract_body 被改成不委托了，但它 docstring 里写着
            `_shared_extract_body` → "委托检测"照样通过；
          · `_all_docs` 真被改回"定义了没调用"，但定义上方的注释里有
            "`grep -c '_all_docs('`" → "调用计数"把注释也算进去了。
        所以任何"按名字出现次数/存在性"判断，都必须先剥注释与文档字符串。
        """
        chunk = re.sub(r'"""(?:.|\n)*?"""', '', chunk)
        chunk = re.sub(r"'''(?:.|\n)*?'''", '', chunk)
        chunk = re.sub(r'(?m)#[^\n]*$', '', chunk)
        return chunk

    def _shared_imports(src: str) -> str:
        """把全部 `from _shared import ...` 语句拼成一段文本（**支持括号跨行**）。

        ⚠️ 必须合并续行：`from _shared import (a,\\n  b, c)` 这种写法下，
        用单行正则 `^from _shared import[^\\n]*b` 会匹配失败 → 守卫误报"没 import"。
        （自测时真踩到：改完 aistyle 后守卫报了两个假阳性。）
        """
        chunks = []
        for m in re.finditer(r'(?m)^[ \t]*from _shared import[ \t]*(.*)$', src):
            chunk, rest = m.group(1), src[m.end():]
            while chunk.count('(') > chunk.count(')') and rest:
                nl = rest.find('\n')
                if nl < 0:
                    chunk += rest
                    break
                chunk += ' ' + rest[:nl]
                rest = rest[nl + 1:]
            chunks.append(chunk)
        return '\n'.join(chunks)

    # ⓪ 每个脚本都必须**真的委托** _shared（取正文 + 读文件两个符号都要在）
    #
    # ⚠️ 判据必须是「**委托符号**在不在 import 里」，**不能**是「有没有那一行」。
    #    2026-09-21 故障注入实测：check_aistyle.py 为了找章节文件加了一个
    #    try/except 兜底 `from _shared import find_chapter_files` —— 于是把
    #    **真正的委托 import 整行注释掉**之后，只查"存在任意 `from _shared import`"
    #    的旧写法**照样通过**（被那行兜底 mask 掉了）。
    #    一个合法的兜底 import 悄悄让守卫失效，而守卫看起来还在。
    #    **教训：守卫要盯"语义有没有满足"，不是"字符串在不在"。**
    _NEED_SHARED = ('extract_body', 'read_text')
    for f in _SHARED_FILES:
        src = read_text(root / 'scripts' / f)
        imp = _shared_imports(_code_only(src))
        for _sym in _NEED_SHARED:
            if _sym not in imp:
                issues.append(
                    f'{f} 没有走 _shared —— 没有委托 `{_sym}`（自己写了一份，'
                    f'或委托 import 被注释掉了）。各写一份会让同一章在不同脚本里'
                    f'分母不同（实测曾差 12.6%）')

    # ② read_text 也不许各写一份（旧的"中文占比最高"启发式会把 UTF-8 误判成 utf-16）
    #    ⚠️ read_text 的名单**必须含 check_chapter_wordcount.py** ——
    #    它曾"看起来不需要 read_text"，实际用了硬编码 utf-8 + errors="replace"，
    #    把 GBK 文件读成 U+FFFD，于是**"编码读错"被报成"字数不足"**
    #    （实测同一段内容 UTF-8 报 1000 字、GBK 报 40 字）。
    for f in _SHARED_FILES:
        src = read_text(root / 'scripts' / f)
        imp = _shared_imports(src)
        # 「read_text 有没有 import」由 ⓪ 统一管；这里只管**调用点**——
        if not re.search(r'_shared_read_text\s*\(', src):
            # ⚠️ "import 了但一次都没调"——check_aistyle 就这么漏过：
            #    它 import 了 _shared_read_text，实际读盘走本地的 read_text_any()。
            #    **只查"有没有 import"的守卫会完全放过它。**
            issues.append(f'{f} import 了 _shared_read_text 却从未调用 —— '
                          f'说明它另有本地读盘实现（声明与实现不一致）')
        if re.search(r'_ENCODINGS\s*=\s*\(.*utf-16', src) and 'read_text' not in imp:
            issues.append(f'{f} 仍在用旧的"_ENCODINGS 中文占比最高"启发式（会选错 utf-16）')
        # 硬编码 utf-8 读文件（绕过共享实现）——只查真正的读盘调用
        for mm in re.finditer(r'\.read_text\(\s*encoding\s*=\s*[\'"]([^\'"]+)[\'"]', src):
            if mm.group(1).replace('-', '').lower().startswith('utf8'):
                issues.append(f'{f} 里仍有硬编码 `read_text(encoding="utf-8")` 读文件 '
                              f'—— 应走 _shared.read_text（GBK 文件会被静默少算）')

    # ②b 明喻词表只许在 _shared 一份（此前两脚本各写一份，且 aistyle 那份是死代码：
    #     定义了带负向断言的 count_similes，算密度却用裸 str.count → 「像」仍误匹配）
    for f in ('check_human_rhythm.py', 'check_aistyle.py'):
        src = read_text(root / 'scripts' / f)
        imp = _shared_imports(src)
        if re.search(r'(?m)^SIMILE_WORDS\s*=', src):
            issues.append(f'{f} 自己定义了 SIMILE_WORDS —— '
                          f'必须从 _shared 导入（两处定义 = 改一处漏一处）')
        if re.search(r'(?m)^def count_similes\s*\(', src):
            issues.append(f'{f} 自己定义了 count_similes —— 必须从 _shared 导入')
        if 'count_similes' in src and 'count_similes' not in imp:
            issues.append(f'{f} 用了 count_similes 但不是从 _shared 导入的')

    # ③ **import 后被本地 def 覆盖** —— 这是最隐蔽的一种：
    #    "我 import 了共享实现"这一行是真的，但下面又 `def extract_body(...)` 把它盖掉了。
    #    只查"有没有 import"的守卫会放过它（check_continuity 就是这么漏的）。
    _DELEGATE_HINT = re.compile(r'_shared_[A-Za-z_]*\s*\(|return\s+_shared')
    for f in _SHARED_FILES:
        src = read_text(root / 'scripts' / f)
        for mm in re.finditer(r'(?m)^def (extract_body|read_text|count_similes)\s*\(', src):
            name = mm.group(1)
            body = src[mm.end():mm.end() + 700]
            # 函数体（到下一个顶层 def 为止）里必须出现 **真的调用** 共享实现
            nxt = re.search(r'(?m)^def ', body)
            body = body[:nxt.start()] if nxt else body
            # ⚠️ 必须先剥注释与 docstring：否则 docstring 里写一句
            #    "本函数委托给 _shared_extract_body" 就能骗过这个守卫（实测踩过）
            if not _DELEGATE_HINT.search(_code_only(body)):
                issues.append(f'{f} 定义了本地 {name}() 覆盖 _shared 实现 —— '
                              f'"统一走共享实现"的声明会因此失效（函数体里没有对 _shared 的真调用）')

    # ④ **定义了却从未调用** —— 治 `_all_docs()` 那类"性能修复没接上"。
    #    判据：私有函数（`_xxx`）在**去掉注释后的代码里**出现的次数 ≤ 1（只有定义处）。
    for f in ('audit_release.py', 'check_human_rhythm.py', 'check_aistyle.py',
              'check_chapter_wordcount.py', 'check_continuity.py', 'check_batch_gate.py'):
        src = _code_only(read_text(root / 'scripts' / f))
        for mm in re.finditer(r'(?m)^def (_[a-z][a-z0-9_]*)\s*\(', src):
            fname = mm.group(1)
            if fname.startswith('__'):
                continue
            uses = len(re.findall(r'\b' + re.escape(fname) + r'\s*\(', src))
            if uses <= 1:
                issues.append(f'{f} 定义了 {fname}() 但从未被调用 —— '
                              f'"我加了个修复"与实际生效是两件事')

    # ══════════ 执行层一致性（2026-09-15 新增）══════════
    # 上面几层查的是"脚本与文档是否一致"。这一层查**"Agent 运行时拿到的东西是否一致"**——
    # 因为子代理只读任务包，任务包错了，前面所有闸门都白设。

    # ① 任务包的配额卡必须是规范卡的**逐行副本**（副本一定会漂，已实测：
    #    内联卡写 ≥2–3 处、同一文件第 10 条写 ≥5 处、规范卡写"2–3 是底线不是目标"）
    def _quota_lines(txt):
        m = re.search(r'```\n(本章写作配额.*?)\n```', txt, re.S)
        if not m:
            return None
        return [l.strip() for l in m.group(1).split('\n') if l.strip()]

    _card = read_text(root / 'references' / 'guides' / 'quick-reference-card.md')
    _brief = read_text(root / 'references' / 'guides' / 'subagent-brief.md')
    _cq, _bq = _quota_lines(_card), _quota_lines(_brief)
    if _cq and _bq:
        if _cq != _bq:
            only_c = [l for l in _cq if l not in _bq]
            only_b = [l for l in _bq if l not in _cq]
            issues.append(
                '任务包的配额卡与规范卡不一致（分叉）：'
                f'规范卡独有 {len(only_c)} 行 / 任务包独有 {len(only_b)} 行'
                + (f' ｜ 例：任务包独有「{only_b[0][:36]}」' if only_b else ''))
    elif _cq and not _bq:
        issues.append('任务包里找不到逐字复制的配额卡（应复制 quick-reference-card.md 的【零】整块）')

    # ⑥ 孤儿指南：每本 guide 都必须至少有一个读点（guide-index / SKILL.md / flows）
    #    "写了没人读"的指南 = 不存在。实测 41 本全部有读点，这条是防将来退化。
    _entry = read_text(root / 'references' / 'guide-index.md') + read_text(root / 'SKILL.md')
    _flows_all = ''.join(read_text(f) for f in (root / 'references' / 'flows').glob('*.md'))
    _orphans = []
    for _g in sorted((root / 'references' / 'guides').glob('*.md')):
        if _g.name in _entry or _g.name in _flows_all:
            continue
        _orphans.append(_g.name)
    if _orphans:
        issues.append('孤儿指南（从任何读点都到不了，等于不存在）：' + '、'.join(_orphans))

    # ⑦ 导入快照目录必须有「不要读这里」的声明（2026-09-19 新增）
    #    `tools/reasonix-novel-*.md` 是子技能导入时的**源快照**，零引用、且已实测漂移
    #    （deai 快照还停在旧版）。它们**不是运行时读物**，但长得像技能文件——
    #    Agent 用 ls 看到会误以为该读它，于是读到一份没人维护的过期方法论。
    #    声明文件（tools/README.md）是唯一防止这件事发生的东西，所以要盯住它。
    _tools = root / 'tools'
    if _tools.is_dir():
        _snaps = [f for f in _tools.glob('*.md') if f.name != 'README.md']
        if _snaps and not (_tools / 'README.md').exists():
            issues.append(
                f'tools/ 下有 {len(_snaps)} 个无标注的导入快照（如 {_snaps[0].name}）'
                f'却缺少 README.md —— Agent 会用 ls 看到它们，误以为是必读的技能文件，'
                f'读到过期副本。要么补 tools/README.md 声明"不要读"，要么删掉整个 tools/。')

    # ⑤ 核心指南必须在**每一个**「每章清单」里有读点
    #    清单是"唯一权威的写一章清单"，Agent 按它办事。
    #    实测 `dialogue-writing.md`（改过「道」的上限）与 `human-rhythm.md`（重写过规则 7）
    #    曾**不在清单里**——改动有落空风险。这八本承载 AI 味治理规则，必须有读点。
    #
    #    ⚠️ 2026-09-19 修（**这里是假闸门**）：
    #    此前只检查 SKILL.md 的「每章最小必做清单」。但**铁律一强制的是
    #    flow 文件的「执行清单」**（"进入任何 Phase，先读对应的流程文件，
    #    并按该文件的『执行清单』逐项执行"）。
    #    实测：`narrative-craft / human-quota / human-rhythm / humanize-toolkit /
    #    ai-cliche-blacklist` 五本**只写在 SKILL.md 清单里，phase3 执行清单里没有**
    #    → 严格按铁律一执行的 Agent 一本都不会读，而**本守卫当时全绿**。
    #    现改为两份清单都查：任何一份缺读点 = 传导断裂。
    _CORE_GUIDES = ('narrative-craft.md', 'human-quota.md', 'dialogue-writing.md',
                    'human-rhythm.md', 'review-agent.md', 'quick-reference-card.md',
                    # novel-humanize 融合（2026-09-16）：加法层 + 禁用词清单
                    'humanize-toolkit.md', 'ai-cliche-blacklist.md')
    _sk = read_text(root / 'SKILL.md')

    # 清单 A：SKILL.md「每章最小必做清单」
    _m = re.search(r'每章最小必做清单(.*?)(?:\n你不是首席内容官|\Z)', _sk, re.S)
    _sec = _m.group(1) if _m else ''
    if not _sec:
        issues.append('SKILL.md 里找不到「每章最小必做清单」小节（结构被改动？）')

    # 清单 B：phase3-writing.md 的「本阶段执行清单」（铁律一强制的那一份）
    _p3 = read_text(root / 'references' / 'flows' / 'phase3-writing.md')
    _m2 = re.search(r'本阶段执行清单(.*?)\n```', _p3, re.S)
    _sec2 = _m2.group(1) if _m2 else ''
    if not _sec2:
        issues.append('phase3-writing.md 里找不到「本阶段执行清单」小节（结构被改动？'
                      '——它是铁律一强制逐项执行的清单）')

    for _label, _body in (('SKILL.md「每章最小必做清单」', _sec),
                          ('phase3-writing.md「本阶段执行清单」', _sec2)):
        if not _body:
            continue
        for _g in _CORE_GUIDES:
            if _g not in _body:
                issues.append(f'核心指南 `{_g}` 不在{_label}里 —— '
                              f'清单是 Agent 逐章照做的唯一清单，不在清单里 = 读点靠运气')

    # ⑧ 世界设定的传导链必须完整（2026-09-20 新增）
    #    背景：`00-故事圣经.md` 曾在整个 SKILL 里**只有一处提及**（项目结构树），
    #    **创作期零读点** → 世界观在计划期写完就断了链。
    #    症状：落笔时不是一个庞大的世界；子代理之间不是同一个世界；设定被吃掉。
    #    这条守卫盯住链上的**每一环**——任何一环被顺手删掉，世界观就又断了。
    _chain = []
    _brief = read_text(root / 'references' / 'guides' / 'subagent-brief.md')
    _card2 = read_text(root / 'references' / 'guides' / 'quick-reference-card.md')
    _p3b = read_text(root / 'references' / 'flows' / 'phase3-writing.md')
    _idx = read_text(root / 'references' / 'guide-index.md')

    if not re.search(r'世界设定包', _brief):
        _chain.append('`subagent-brief.md` 的任务包里没有「世界设定包」槽位'
                      '（子代理会各自发明一套世界）')
    if not re.search(r'世界设定包已内联', _brief):
        _chain.append('`subagent-brief.md` 的「主编派发检查清单」没有核对「世界设定包已内联」')
    if not re.search(r'专有名词表', _brief):
        _chain.append('`subagent-brief.md` 里没有「专有名词表」（禁止另造同义新词的关键约束）')
    if not re.search(r'世界一致性', _card2):
        _chain.append('`quick-reference-card.md` 里没有「世界一致性」——'
                      '子代理只读这一页，等于这条约束对它不存在')
    if not re.search(r'世界切片', _p3b):
        _chain.append('`phase3-writing.md` 的执行清单没有「世界切片」动作 —— '
                      '主 Agent 每章读到的世界只有大纲/人物/细纲')
    if not re.search(r'世界切片|一、世界观手册', read_text(root / 'SKILL.md')):
        _chain.append('`SKILL.md` 的每章最小必做清单没有世界设定读点')
    if not re.search(r'世界切片', _idx):
        _chain.append('`guide-index.md` 里没有「世界切片／设定回流」的动作映射')
    if _chain:
        issues.append('世界设定传导链断裂：\n      · ' + '\n      · '.join(_chain))

    # ⑨ 剧情脚手架的传导链必须完整（2026-09-21 新增）
    #    脚手架填的是流程里的一个**真空区**：作者已有的具体剧情设想
    #    （"第 3 章遇到铁匠""第 5 章当众打脸"）既不是"内核"（Phase 1 验收的 12 项），
    #    也不是细纲（Phase 2 的下游产物）→ 没有采集入口 → 被 Agent 推导的大纲盖掉。
    #    链上任何一环被删，这个真空区就又回来了。
    _ps_chain = []
    if not (root / 'references' / 'guides' / 'plot-scaffold.md').is_file():
        _ps_chain.append('`references/guides/plot-scaffold.md` 不存在（脚手架主线指南缺失）')
    _p1 = read_text(root / 'references' / 'flows' / 'phase1-interview.md')
    if not re.search(r'剧情脚手架', _p1):
        _ps_chain.append('`phase1-interview.md` 没有「剧情脚手架」环节 —— 作者的剧情设想没有采集入口')
    _p2 = read_text(root / 'references' / 'flows' / 'phase2-planning.md')
    if not re.search(r'脚手架对账', _p2):
        _ps_chain.append('`phase2-planning.md` 没有「脚手架对账」—— 大纲可能盖掉作者明确要的东西')
    if not re.search(r'作者画面', _brief):
        _ps_chain.append('`subagent-brief.md` 任务包里没有「作者画面」—— '
                         '流水线上最保人味的素材传不到写手手上')
    if not re.search(r'作者的画面', _card2):
        _ps_chain.append('`quick-reference-card.md` 里没有「作者的画面」——'
                         '子代理只读这一页，等于它不知道该保住这段')
    _idx2 = read_text(root / 'references' / 'guide-index.md')
    if not re.search(r'plot-scaffold', _idx2):
        _ps_chain.append('`guide-index.md` 里没有 `plot-scaffold.md` 的动作映射')
    if not re.search(r'剧情脚手架', read_text(root / 'SKILL.md')):
        _ps_chain.append('`SKILL.md` 的 Phase 1 描述里没有提及剧情脚手架')

    # ── P3 续航链（2026-09-21 下午新增）：写作期的方向对齐 ──
    #    这是 Phase 3「禁止确认」的一个**有边界的例外**，和锚点章同级。
    #    任何一环被删，要么新机制失效，要么"疯狂创作"被无边界打断。
    _p3b2 = read_text(root / 'references' / 'flows' / 'phase3-writing.md')
    _psg = read_text(root / 'references' / 'guides' / 'plot-scaffold.md')
    if not re.search(r'两个例外|两条例外', _p3b2):
        _ps_chain.append('`phase3-writing.md` 没有声明「两个例外」—— '
                         'Phase 3 的"禁止确认"铁律会把脚手架对齐直接压死')
    if not re.search(r'意图浓度', _psg):
        _ps_chain.append('`plot-scaffold.md` 里没有「意图浓度」—— '
                         '介入频次会退回"按章数"，对弱意图作者是负担、对强意图作者是缺位')
    if not re.search(r'方向对齐四步|摊素材', _psg):
        _ps_chain.append('`plot-scaffold.md` 里没有「方向对齐四步」—— '
                         '先挖意图→再摊素材→才给建议的顺序会丢，退化成"直接给建议"（会带偏作者）')
    if not re.search(r'两条边界|内容边界', _psg) or not re.search(r'\(?\[\s*\]\s*7\.5', _p3b2):
        _ps_chain.append('`phase3-writing.md` 每章清单里没有 7.5「剧情脚手架对齐检查」—— '
                         '四条触发点没有落进 Agent 逐章照做的清单，等于不存在')
    if not re.search(r'剧情脚手架对齐', read_text(root / 'SKILL.md')):
        _ps_chain.append('`SKILL.md` 的 Phase 3 描述里没有提「剧情脚手架对齐」这个例外')

    if _ps_chain:
        issues.append('剧情脚手架传导链断裂：\n      · ' + '\n      · '.join(_ps_chain))

    # ⑩ 低费用模式的口径必须一致（2026-09-21 新增）
    #    事故：costMode: low 的设计散落在 8 个文件里、**各写一份**（phase3 三处 / phase4 两处 /
    #    guide-index / 速查卡 / review-agent / review-agent…）。这轮重新设计后，
    #    哪里还留着旧说法，就会出现"一处说 A、另一处说 B"的自相矛盾——
    #    而 Agent 会挑它读到的那个版本执行。**所以本文件是 costMode 的唯一事实源，其余只准引用。**
    _low = read_text(root / 'references' / 'guides' / 'low-cost-mode.md')
    _lowbad = []
    # 「文档在引用旧说法」的行不算违规——这些指南是教学性的，会主动引用错版本
    # 来解释"为什么原来那样是错的"。**（本文件多处复用这个排除表）**
    _QUOTING_OLD = ('修正', '原来', '原先', '旧设计', '老设计', '不成立', '曾经', '❌')
    # ① 不能再写"默认串行"（旧设计的错误判断）
    #    ⚠️ 排除词必须**多写几个同义写法**（旧设计／老设计／是错的／方向是反／❌）：
    #    这条守卫初版只排除了「旧设计」，而正文里另一处写的是「**老**设计"默认串行"，
    #    方向是反的」→ 立刻误报自己。
    #    **这是本项目第四次踩"同一概念有多种写法"的坑**（前三次：两个例外/两条例外、
    #    _all_docs 两处调用、清单里两处）。**写校验规则前先 grep 一遍所有写法。**
    _OLD_DESIGN_MARKS = ('旧设计', '老设计', '是错', '方向是反', '❌', '推翻', '旧的是')
    for _ln in _low.split('\n'):
        if re.search(r'默认\s*(serial|串行)', _ln) and not any(k in _ln for k in _OLD_DESIGN_MARKS):
            _lowbad.append('`low-cost-mode.md` 又写回「默认串行」——串行会让主 Agent 上下文'
                           '线性累积、cacheRead 反而更大；批量场景必须走链式流水线')
            break
    if not re.search(r'链式流水线', _low):
        _lowbad.append('`low-cost-mode.md` 里没有「链式流水线」——'
                       '批量组织是 low 模式**最大的一笔省**（子代理上下文独立、不累积）')
    if not re.search(r'判断项.{0,8}合并|合并成\s*1\s*次|合并成一次', _low):
        _lowbad.append('`low-cost-mode.md` 没有「判断项合并」——'
                       '质检会退回"砍检查项目"而不是"合并调用次数"')
    if not re.search(r'人味不降', _low):
        _lowbad.append('`low-cost-mode.md` 没有「人味不降清单」——low 会被读成"可以牺牲人味"')
    # ② 其它文件里不能残留旧说法
    #    ⚠️ 名单必须**含 SKILL.md / guide-index.md / phase2-planning.md**：
    #    · 2026-09-21 发现 SKILL.md 的「模式开关」一节还在写旧设计（旧名单只查 flow 文件）；
    #    · 2026-09-23 发现 **phase2-planning.md 的「模式选择」提示**也还在写——
    #      而那是**最坏的位置**：它在**作者做选择的这一刻**说"低费用模式…质量略降"，
    #      作者会带着这个预期去选。**选项描述里的口径污染，比说明里的严重得多。**
    #    · 判据必须**按行排查引用行**（本文件就有一段「修正：原来写的是……」在引用旧文案）。
    _OLD_CLAIM = re.compile(r'只跑\s*3\s*个脚本|只跑三脚本|只查\s*3\s*脚本')
    #    ⚠️ 「质量下降」的判据必须**要求同现**（本行同时出现 low 与降质说法），
    #    并且排除**否定句**。初版只查 `质量略降|质量下降|牺牲质量`，立刻炸出 4 处假阳性：
    #      · `EC:76`「质量下降，用户可追问」（说的是跳过【标准】级指令，与 low 无关）
    #      · `chained:177` / `parallel:268` / `low-cost:169`
    #        「这**不是**牺牲质量换省钱」——**说的是反话**
    #    **这是本项目第 7 次踩"存在性判据被无关文本/否定句命中"的坑。**
    #    规律：判据要么**要求同现**（两个词在同一行），要么**排除否定**。
    _LOW_QUALITY = re.compile(
        r'(?:低费用|低成本|低消耗|low)[^\n]{0,40}(?:质量略降|质量会降|质量下降)'
        r'|(?:质量略降|质量会降|质量下降)[^\n]{0,40}(?:低费用|低成本|低消耗|low)')
    _EXCLUDE = _QUOTING_OLD + ('并不', '误读', '过时', '反话')
    # ⚠️ 「否定排除」只给**降质说法**用（"这**不是**牺牲质量换省钱"是反话）。
    #    初版把 '不是' 也套在 `_OLD_CLAIM` 上 → 立刻造成**假阴性**：
    #    SKILL.md 的 low 描述里有一句"组织形式走链式流水线 + 批次（**不是**串行……）"，
    #    于是同一行里的「只跑 3 个脚本」被整行豁免，守卫不响。
    #    （故障注入用例 ㊵ 在改动后立刻变红，抓到的是回归。）
    #    **规律：排除词要按"它在否定什么"限定作用域，不能全表共用。**
    _EXCLUDE_NEG = _EXCLUDE + ('不是',)
    for _f in ('SKILL.md', 'references/guide-index.md',
               'references/flows/phase2-planning.md',
               'references/flows/phase3-writing.md', 'references/flows/phase4-validation.md',
               'references/guides/chained-pipeline.md', 'references/guides/parallel-workflow.md'):
        _t = read_text(root / _f)
        for _ln in _t.split('\n'):
            if any(k in _ln for k in _EXCLUDE):
                continue
            if _OLD_CLAIM.search(_ln):
                _lowbad.append(f'`{_f}` 里还留着旧说法「只跑 3 个脚本」——'
                               f'机械项是**脚本**（0 reasoning token），砍它等于白扔质量')
                break
        for _ln in _t.split('\n'):
            if any(k in _ln for k in _EXCLUDE_NEG):    # ← 只有这里需要否定排除
                continue
            if _LOW_QUALITY.search(_ln):
                _lowbad.append(f'`{_f}` 把 low 模式描述成「质量略降」——'
                               f'low 降的是"同样的检查做几次"，不是"检查多严"；'
                               f'它的人味项目一个不砍（最坏的位置是"选项描述"：'
                               f'作者会带着这个预期去选）')
                break
        # 「默认串行」同理（引用旧设计来说明"为什么错"的行不算）
        for _ln in _t.split('\n'):
            if re.search(r'默认\s*(serial|串行)', _ln) and \
                    not any(k in _ln for k in _EXCLUDE):
                _lowbad.append(f'`{_f}` 里还写着 low 模式「默认串行」——'
                               f'串行让主 Agent 上下文线性累积，cacheRead 反而更大')
                break
    # ③ 调用预算数字必须全网一致
    _budget_bad = [f for f in ('SKILL.md', 'references/execution-contract.md',
                               'references/flows/phase3-writing.md',
                               'references/flows/phase4-validation.md',
                               'references/guides/quick-reference-card.md',
                               'references/guides/low-cost-mode.md')
                   if re.search(r'6[–-]10\s*次', read_text(root / f))]
    if _budget_bad:
        _lowbad.append('调用预算数字不一致（残留旧值 6–10 次）：' + '、'.join(_budget_bad))
    if _lowbad:
        issues.append('低费用模式口径不一致：\n      · ' + '\n      · '.join(_lowbad))

    # ── 低消耗 × 链式流水线：质量/效率接缝（2026-09-21）────────────────
    # 这一组盯的是"又快又低 = 质量漏水"的三个具体洞：
    #   ① 主编读正文 → 子代理隔离省下的钱被它一个人花回去（**成本洞**）
    #      旧文档甚至写着主编"必持全部真实章节"，理由是"窗口够大"——
    #      那是窗口够不够的论证，不是成本的论证（实测 97.8% 是 cacheRead）。
    #   ② 跨章缺陷批末才发现 → 回头修，成本超线性（**质量洞**）
    #   ③ 磨手改结尾 → 下一章承接的结尾被掉包（**质量洞，且两边都通顺、无脚本会报**）
    _pipebad = []
    _low2 = read_text(root / 'references' / 'guides' / 'low-cost-mode.md')
    _chain = read_text(root / 'references' / 'guides' / 'chained-pipeline.md')
    _pw = read_text(root / 'references' / 'guides' / 'parallel-workflow.md')

    # ① 主编不许再被要求持有正文（成本洞的源头）
    #    ⚠️ 必须**按行排除"文档在引用旧说法"的行**：
    #    这些指南是教学性的，会**主动引用错版本**来解释"为什么原来那样是错的"
    #    （本文档就有一段「⚠️ 修正：这里原来写的是……」）。
    #    不做排除 → 守卫会抓到自己的"错误示范"，报一个假问题。
    #    **这是本项目第 5 次踩"同一概念有多种写法 / 旧文本被引用"的坑**
    #    （前四次：两个例外／两条例外、_all_docs 两处调用、清单两处、旧设计／老设计）。
    #    **写校验正则前先 grep 一遍：这个概念在文档里出现过几次、以哪些面貌出现。**
    #    （`_QUOTING_OLD` 排除表在「⑩ 低费用模式口径」块开头统一定义，本处只复用。）
    for _ln in _pw.split('\n'):
        if '全部真实章节' in _ln and not any(k in _ln for k in _QUOTING_OLD):
            _pipebad.append('`parallel-workflow.md` 又写回主编"持有全部真实章节"——'
                            '主编是流水线里活得最久的角色，它读过的每一章都会跟着之后'
                            '每一次调用被重发；子代理隔离省下的钱会被它一个人花回去')
            break

    # ② 三条机制必须都在（缺一条就是一个洞）
    for _kw, _what, _where in (
        ('make_handoff', '主编零正文的支点（交接卡）', _low2),
        ('发现缺陷的时点', '闸门定位判据（发现 ≤ 定稿）', _low2),
        ('窗闸', '跨章项每章跑的滚动窗口闸门', _low2),
        ('只向前修', '缺陷路由（系统缺陷不回改已定稿章）', _low2),
        ('主编零正文', '主编不读正文的机制', _chain),
        ('边界冻结', '文本层的接口冻结（防接力棒掉包）', _chain),
        ('窗闸窗口', '窗口与领先上限必须同值的对齐说明', _chain),
    ):
        if _kw not in _where:
            _pipebad.append(f'缺少「{_what}」（关键词 {_kw}）—— '
                            f'这是一条流水线与低消耗模式的接缝，缺了就是"跑得飞快但错误同样飞快"')

    # ③ 脚本必须真的支持文档里写的命令
    #    ⚠ 这条防的是上一轮结构审计发现的那类 P0："文档里写着一条命令"和
    #      "那条命令有效"**完全不相关**（4 个 P0 里 2 个属于这类）。
    #      闸门不能只靠人工端到端跑过才保证有效——这里做静态交叉比对。
    #    ⚠ 去重键必须是 (脚本, 参数) 而不是参数本身：
    #      `--window` 在 check_repetition 与 check_aistyle 里都有，
    #      用参数去重会让第二个脚本**根本不检查**（旧写法就是这么漏的）。
    _SCRIPT_RX = re.compile(r'scripts/([a-z_]+\.py)((?:\s[^\n]*)?(?:\\\n[^\n]*)*)')
    _cmd_flags = {}
    for _m in _SCRIPT_RX.finditer(_low2):
        _script, _args = _m.group(1), _m.group(2)
        _cmd_flags.setdefault(_script, set()).update(
            re.findall(r'(?<![\w-])(--[a-z][a-z0-9\-]*)', _args))
    for _script, _flags in sorted(_cmd_flags.items()):
        _sp = root / 'scripts' / _script
        if not _sp.is_file():
            _pipebad.append(f'`low-cost-mode.md` 引用了不存在的脚本 `scripts/{_script}`')
            continue
        _src = read_text(_sp)
        for _fl in sorted(_flags):
            if _fl not in _src:
                _pipebad.append(
                    f'`low-cost-mode.md` 里的 `{_script} {_fl}` 在该脚本里**不存在** —— '
                    f'"文档写了命令"≠"命令有效"（脚本会直接报错退出，闸门形同虚设）')

    def _declares(src: str, flag: str) -> bool:
        """脚本是否**声明**了这个参数（`add_argument('--flag'`）。

        ⚠️ 不能只查"字符串在不在"：`--selftest` 在 check_aistyle.py 里出现**两次**
        （`add_argument` + `parser.error(... 或使用 --selftest)`）。
        按"存在性"判 → 把声明改坏、只要那句提示文案还在，守卫就**不响**。
        **这是本项目第 6 次踩"同一概念在文件里出现多处"的坑**
        （前五次：两个例外／两条例例、_all_docs 两处调用、清单两处、旧设计／老设计、
        全部真实章节在 parallel-workflow 里两处）。
        **规律：任何"存在性"判据都要先 grep 一遍 —— 有几处？哪一处才是真正的声明？**
        """
        return bool(re.search(r"add_argument\(\s*['\"]" + re.escape(flag) + r"['\"]", src))

    for _script, _req, _why in (
        ('check_repetition.py', '--window', '窗闸需要滚动窗口'),
        ('check_repetition.py', '--brief', '窗闸报告必须定长'),
        ('check_aistyle.py', '--drift', '声音漂移检测'),
        ('check_aistyle.py', '--brief', '窗闸报告必须定长'),
        ('check_aistyle.py', '--selftest',
         '漂移判定的自测——判据的坑（基线前误判、0→爆发漏抓）改完必须能随时复验，'
         '不必等真实项目在手边'),
        ('make_handoff.py', '--check', '交接卡过期检测（边界冻结的唯一抓手）'),
        ('audit_tokens.py', '--traces',
         '成本审计的**真实数据源** —— 它原来找 `<项目>/AGENT工作日志/session.jsonl`，'
         '而该目录**从未被任何环节产出**（项目里没有、脚本里也没有写它的代码）：'
         '工具本身就是"有校验点、却指向一个不存在的生成点"的孤儿'),
        ('check_contract.py', '--window', '契约校验要能按窗闸形态跑（每章一次）'),
        ('check_contract.py', '--brief', '同上：窗闸报告必须定长'),
        ('check_repetition.py', '--imagery', '意象配额（唯一的真孤儿，已脚本化）'),
        ('make_task_package.py', '--check',
         '任务包校验（缺槽位 = 拒绝开工；超预算 = 上下文"被乘数"失控）'),
        ('make_task_package.py', '--write', '任务包装配（脚本能拿到的自动填）'),
        ('make_workorder.py', '--archive',
         '★ 每章质检那一步的入口（内部跑完全部机械脚本 + 按退出码登记缺陷类型 + 写工单）'),
        ('make_workorder.py', '--retry', '把 retryCount **当场落盘**——不落盘则良率不可统计'),
        ('make_workorder.py', '--report', '★ 批末出良率与缺陷帕累托（决定改哪条规则）'),
    ):
        if not _declares(read_text(root / 'scripts' / _script), _req):
            _pipebad.append(f'`{_script}` 没有声明参数 {_req} —— {_why}'
                            f'（否则"每章重读报告"会把省下的上下文又花回去）')

    # ④ 关键脚本必须存在
    for _s, _why in (('make_handoff.py', '主编零正文的支点'),
                     ('check_contract.py', '并行写作的接口定义校验（契约 diff）'),
                     ('make_task_package.py', '任务包装配 + 预算 + 校验')):
        if not (root / 'scripts' / _s).is_file():
            _pipebad.append(f'缺少 `scripts/{_s}` —— {_why}，机制落不了地')

    if _pipebad:
        issues.append('低消耗流水线口径不一致：\n      · ' + '\n      · '.join(_pipebad))

    # ⑤ **模块级的全局副作用必须幂等**（2026-09-23 新增）
    #    实测：12 个脚本各自在模块级写 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`，
    #    于是**任何一个脚本 import 另一个脚本就崩**：
    #        ValueError: I/O operation on closed file   (lost sys.stderr)
    #    原因：`sys.stdout` 已经是 TextIOWrapper 时，`.buffer` 是**共用的**底层 buffer；
    #    再包一层后，旧 wrapper 被 GC 回收会**连底层 buffer 一起关掉**。
    #    触发场景：`make_task_package.py` 需要复用 `check_contract` / `make_handoff` 的解析逻辑。
    #    **换言之：这 12 个脚本此前是"不可被复用"的**——而"能复用"是任何工程化的前提。
    #    修法：统一走 `_shared.ensure_utf8_stdio()`（幂等）。
    #    ⚠️ 扫描前必须 `_code_only()` 剥注释 —— 否则这条守卫会**匹配到它自己的注释**
    #    （上面那段说明里就引用了这个模式）。这是本项目第 8 次踩
    #    "存在性判据命中无关文本"，而本文件里**早就有 `_code_only()` 专治这个**：
    #    **规律：写任何"按模式扫描源码"的守卫，第一件事是套 `_code_only()`。**
    _raw_wrap = [p.name for p in sorted((root / 'scripts').glob('*.py'))
                 if p.name != '_shared.py'
                 and re.search(r'TextIOWrapper\(\s*sys\.(stdout|stderr)\.buffer',
                               _code_only(read_text(p)))]
    if _raw_wrap:
        issues.append('脚本用了**非幂等**的 stdio 包装（会让"脚本互相 import"直接崩）：'
                      + '、'.join(_raw_wrap)
                      + ' —— 必须改用 `_shared.ensure_utf8_stdio()`。'
                        '模块级的全局副作用不幂等时，"被 import"这个动作本身就会改变程序状态。')

    # ── 「一致性机制」的三站齐备（2026-09-23 新增）──────────────────────
    # 判据来自这一轮的审计结论：**一条机制要生效，必须走完三站**
    #   ① 生成点（信息在哪一步被产出）
    #   ② 交付点（怎么进入执行者的上下文）
    #   ③ 校验点（怎么知道它被遵守了）
    # **缺任何一站机制就死了**，而且死法不同：缺生成点=不存在；缺交付点=看不到；
    # 缺校验点=不可证伪。实测：文风线六项机制里只有一项三站齐全。
    #
    # 这组守卫的价值不只是"盯住这六条"——它是**第一个能自动发现孤儿的守卫**：
    # 任何"文档里定义了、却没有任何执行链引用"的东西都会被它抓出来。
    _S = read_text(root / 'SKILL.md')
    _P3 = read_text(root / 'references' / 'flows' / 'phase3-writing.md')
    _P2 = read_text(root / 'references' / 'flows' / 'phase2-planning.md')
    _SB = read_text(root / 'references' / 'guides' / 'subagent-brief.md')
    _RA = read_text(root / 'references' / 'guides' / 'review-agent.md')
    _BT = read_text(root / 'references' / 'guides' / 'bible-template.md')
    _OT = read_text(root / 'references' / 'guides' / 'outline-template.md')
    _mechbad = []
    for _kw, _what, _where, _station in (
        # ── 契约：主逻辑机制。此前"读点 12 处、产出 0 处、校验 0 处"
        #    ⚠️ 关键词必须查到**键名**（`进入·位置时间`），不能只查"接口契约"——
        #       后者改个标题也还在，验不出"栏位是否存在"。（用例 ㊷ 一开始就是这么漏的。）
        ('进入·位置时间', '细纲规格里有契约**键值栏位**（生成点）', _P2, '生成点'),
        ('check_contract.py', '契约校验接进 Phase 3 写前（交付点）', _P3, '交付点'),
        ('check_contract.py', '契约校验接进 SKILL 每章动作', _S, '交付点'),
        ('接口契约（本章）', '任务包里契约是一等槽位', _SB, '交付点'),
        # ── 三层锚：治"累积漂移在定义上不可见"
        ('--book-base', '三层锚的累计基线接进 Phase 3 质检', _P3, '交付点'),
        ('--book-base', '三层锚的累计基线在主入口有说明', _S, '交付点'),
        ('锚点滚动', '锚点滚动进了执行清单（此前是孤儿：两套清单都没有它）', _S, '交付点'),
        # ── 意象配额：从孤儿变成脚本
        ('--imagery', '意象配额脚本化并接进 Phase 3', _P3, '校验点'),
        # ── 独立质检的独立性：RA 自己和自己矛盾过
        ('独立但极轻', 'low 模式下"独立质检不并入"已声明', _RA, '交付点'),
        ('独立质检另起子代理', 'Phase 4 查痕口径已同步', 
         read_text(root / 'references' / 'flows' / 'phase4-validation.md'), '校验点'),
        # ── 伏笔单载体
        ('唯一权威载体', '伏笔表已声明单一载体（防双载体同名漂移）', _OT, '生成点'),
        ('卷终汇总视图', '圣经的伏笔表已降级为派生视图', _BT, '生成点'),
        # ── 台账例外
        ('对铁律六的显式例外', 'low 的台账策略已显式声明为例外', 
         read_text(root / 'references' / 'guides' / 'low-cost-mode.md'), '生成点'),
        # ── 成本基线的可复现性（2026-09-23）
        ('--traces', '成本审计指向**真实存在**的数据源（平台轨迹）',
         read_text(root / 'references' / 'guides' / 'token-efficiency.md'), '生成点'),
        ('每次调用平均重发上下文', '成本判据是**绝对量**（每次调用重发多少），不只是比例',
         read_text(root / 'references' / 'guides' / 'token-efficiency.md'), '校验点'),
        # ── 工单 / 良率 / 返工落盘（2026-09-23）—— 这一组盯的是「有没有人真的在记数」
        ('make_workorder.py', '★ 工单接入 Phase 3 每章质检（否则返工永远不可统计）',
         _P3, '交付点'),
        ('make_workorder.py', '★ 工单接入主入口十动作', _S, '交付点'),
        ('本章返工轮次', '`retryCount` 已落盘（此前只在铁律七的规则描述里出现过）',
         read_text(root / 'references' / 'guides' / 'creation-ledger.md'), '生成点'),
        ('indicator-waived', '指标让步接收（给耦合指标一个合法的终止条件）',
         read_text(root / 'references' / 'execution-contract.md'), '校验点'),
        ('修法①：指标分层', 'CTQ 分层（3 个硬门，其余降为参考指标）',
         read_text(root / 'references' / 'execution-contract.md'), '校验点'),
        ('自适应抽检', '判断项按连续合格章数动态调强度', _P3, '交付点'),
        ('本批已用过的开场', '前馈槽位（给事实不给目标，直接消掉跨章同质类返工）',
         read_text(root / 'scripts' / 'make_task_package.py'), '交付点'),
    ):
        if _kw not in _where:
            _mechbad.append(f'缺少「{_what}」——关键词 `{_kw}`（**{_station}**）。'
                            f'一条机制缺这一站就等于不存在：'
                            f'缺生成点=没东西可填；缺交付点=执行者看不到；缺校验点=不可证伪')
    if _mechbad:
        issues.append('一致性机制的「三站」不齐：\n      · ' + '\n      · '.join(_mechbad))

    # 清单 C：两份清单的**指南读点必须一致**（只写在一边的读点，另一边执行时会漏）
    #    两套编号同时存在（SKILL.md 10 个动作 vs phase3 的 0–7），
    #    所以"某个读点只更新了一边"是必然会发生的漂移。
    _g_rx = re.compile(r'`?([a-z][a-z0-9\-]+\.md)`?')
    _set_a = set(_g_rx.findall(_sec))
    _set_b = set(_g_rx.findall(_sec2))
    # 只比对 guides/ 下的写作指南（phase3 会额外引用 flows/ 与自身结构说明，不算缺口）
    _known = {p.name for p in (root / 'references' / 'guides').glob('*.md')}
    _only_a = sorted((_set_a - _set_b) & _known)
    if _only_a:
        issues.append(
            '两套「每章清单」读点不同步：这些指南只在 SKILL.md 清单里、phase3 执行清单里没有 → '
            + '、'.join(_only_a) +
            '（铁律一强制执行的是 phase3 的那份，只写在 SKILL.md 里等于 Agent 不会读）')

    # ④ 规则传导：源指南里有的关键规则，**速查卡里必须有**（子代理只读速查卡）
    _card = read_text(root / 'references' / 'guides' / 'quick-reference-card.md')
    for _label, _srcfile, _src_rx, _card_rx in PROPAGATION_RULES:
        _src = read_text(root / 'references' / 'guides' / _srcfile)
        if not re.search(_src_rx, _src):
            continue          # 源指南里已没有这条规则（可能被删/改名）——不算传导失败
        if not re.search(_card_rx, _card):
            issues.append(
                f'规则传导断裂：「{_label}」在 {_srcfile} 里有，但**速查卡里没有** —— '
                f'子代理只读速查卡，等于这条规则对子代理不存在。'
                f'（改源指南后必须同步 quick-reference-card.md）')

    # ③ 项目结构规范必须存在（**闸门能不能看见稿子，全靠它**）
    # 2026-09-15 实测：SKILL 规定了文件名却从未规定**目录与字段名**，
    # 于是真实项目自建了 `正文/` + `index` 字段 → 闸门一章都没扫到，却报"✓ 通过"。
    # 这条守卫保证那张"接口契约"不会被谁顺手删掉。
    _SPEC = read_text(root / 'references' / 'flows' / 'phase2-planning.md')
    for _kw, _why in (
        ('项目结构', '项目结构规范小节（脚本按它找文件）'),
        ('chapters/', '章节正文目录约定'),
        ('chapterNumber', 'JSON 章号字段名（脚本依赖）'),
    ):
        if _kw not in _SPEC:
            issues.append(f'phase2-planning.md 缺少「{_why}」（关键词 {_kw}）—— '
                          f'规范没写清，Agent 会自建结构，闸门就会看不见稿子')

    # ② "子代理必读"的口径不许再漂回"全套路径"
    for _f, _pat in (
        ('SKILL.md', r'任务包里(已经)?附了每一步需要的文件'),
        ('references/guide-index.md', r'子代理任务包内必须附本索引中对应动作的必读路径'),
    ):
        if re.search(_pat, read_text(root / _f)):
            issues.append(f'{_f}：子代理必读口径与 subagent-brief 冲突'
                          f'（应给"内联内容 + 速查卡 + 本批 1–2 本"，不是全套路径）')

    # soft 方向：max 的 soft 必须更严（更小），min 的必须更大，否则永不触发
    blk = re.search(r'THRESHOLDS = \{(.*?)\n\}', h, re.S)
    if blk:
        for mm in re.finditer(r"'(\w+)':\s*dict\(kind='(\w+)',([^\n]*(?:\n\s+'[^\n]*)*)", blk.group(1)):
            name, kind, body = mm.group(1), mm.group(2), mm.group(3)
            gh = re.search(r'hard=([0-9.]+)', body)
            gs = re.search(r'soft=([0-9.]+)', body)
            if not (gh and gs):
                continue
            hard, soft = float(gh.group(1)), float(gs.group(1))
            dead = (kind == 'max' and soft > hard) or (kind == 'min' and soft < hard)
            if dead:
                issues.append('软阈值永不触发：%s（kind=%s, hard=%s, soft=%s）'
                              % (name, kind, hard, soft))
    return issues


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description='j-novel 发布前 QA')
    ap.add_argument('--root', default=None, help='SKILL 根目录（默认脚本的上级目录）')
    ap.add_argument('--regress', action='store_true', help='附分离回归（较慢）')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    if not (root / 'SKILL.md').exists():
        print(f'[错误] 这不是 SKILL 目录：{root}')
        return 2

    refs = check_references(root)
    syntax = check_syntax(root)
    thr = check_thresholds(root)
    chain = check_call_chain(root)
    struct = check_structure(root)
    residue = check_residue(root)
    cross = check_cross_script(root)

    regress_problems, rows = ([], [])
    if args.regress:
        regress_problems, rows = check_checks_separation(root)

    blockers = refs + syntax + thr + chain + struct + residue + cross + regress_problems

    if args.json:
        print(json.dumps({
            'root': str(root),
            'reference_failures': refs,
            'syntax_failures': syntax,
            'threshold_drift': thr,
            'call_chain_missing': chain,
            'structure_issues': struct,
            'residue': residue,
            'cross_script': cross,
            'separation': rows,
            'pass': not blockers,
        }, ensure_ascii=False, indent=2))
        return 0 if not blockers else 1

    P = '=' * 62
    print(P)
    print(f'j-novel 发布前 QA：{root}')
    print(P)
    print(f'  1. 引用完整性      {len(refs)} 处失效')
    print(f'  2. 脚本语法        {len(syntax)} 个失败（共 {len(list((root/"scripts").glob("*.py")))} 个脚本）')
    print(f'  3. 阈值一致性      {len(thr)} 处漂移（{len(THRESHOLDS)} 项已核对）')
    print(f'  4. 调用链闭合      {len(chain)} 项不足 2 处（{len(KEY_TOOLS)} 项已核对）')
    print(f'  5. 结构完整性      {len(struct)} 处问题')
    print(f'  6. 残留检查        {len(residue)} 处残留（{len(DEPRECATED)} 个已废弃说法）')
    print(f'  6.5 跨脚本一致性    {len(cross)} 处冲突')
    if args.regress:
        print(f'  7. 分离回归        {len(regress_problems)} 处异常（人类 {len(HUMAN_SAMPLES)} / AI {len(AI_SAMPLES)} 样本）')
        for name, h, a in rows:
            flag = '✓' if (h == 0 and a == 0) else ('拦下' if (h != 0 or a != 0) else '?')
            print(f'       {name:16s} rhythm={h:>2} aistyle={a:>2}  {flag}')
    print()

    if blockers:
        print(f'✗ 发布前 QA 未通过（{len(blockers)} 项阻塞）：')
        for label, items in [('引用失效', refs), ('语法失败', syntax), ('阈值漂移', thr),
                             ('调用链缺口', chain), ('结构问题', struct),
                             ('残留', residue), ('跨脚本', cross), ('回归异常', regress_problems)]:
            for it in items:
                print(f'  • [{label}] {it}')
        print()
        print('  提示：修完再跑一次。这份检查对应 CHANGELOG 的「发布前 QA」记录。')
        return 1

    print('✓ 全部通过 —— 可以冻结为正式版。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
