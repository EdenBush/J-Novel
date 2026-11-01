#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""故事账校验：这一章作为一个故事单元，立不立得住。

用法:
    python scripts/check_story.py <项目目录>              # 全部章
    python scripts/check_story.py <项目目录> --project    # 附跨章曲线（原地转/白给/流水账）
    python scripts/check_story.py <项目目录> --brief      # 一章一行
    python scripts/check_story.py <章节md或.meta.md>      # 单章

退出码:
    0 = 通过（或"本项目尚未启用故事账"——历史项目不判，只提示）
    1 = 有阻塞项（缺段/缺键/待填/位移为无/可删性无数字/可删性 0）
    2 = 取不到项目（找不到章节文件，或读不出 02-写作计划.json）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 为什么会有这个脚本

本体系此前有 17 项句法指标、五项独立测试、可替换性四问、口吻宪法——
**全部落在句子层。没有任何一条问过"这一章为什么必须存在"。**

后果是一个此前**没有任何闸门**的失败模式：
句子全部达标、伏笔登记工整、章末型轮换正确、盲评能通过"像人写的"，
而读者第 12 章弃书——因为**每章都在原地转**：主角一直在做事，
但没有一次真的失去什么，也没有一次真的改变了什么。**每一章都可以删掉。**

判"好不好"需要理解，正则做不到（本体系已诚实承认过一次：
v6.9.0 的"闲笔密度"判据就是这么被弃用的）。
所以本脚本**只做两件事**：

  ① **验形状**：五格齐不齐、精度够不够、有没有互相矛盾（无选择 + 可删性低 = 水章）
  ② **验曲线**：跨章看"位移/代价/可删性"有没有连续同值（单章看不出的停滞）

**它不判好坏。** 好坏由 `review-agent.md` 的独立评委跑三条判据
（选择是否两难 / 代价是否已发生 / 位移是否不可逆）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 生效点（为什么历史项目不会被判死）

**本项目规矩：新必填字段不追溯旧稿。**
所以判据是自适应的：
  · 全书**没有任何一章**写过 `## 故事账` → **只提示，不阻塞**（exit 0）；
  · 一旦有某一章写了 → **从它之后的章**（含它自己）缺段即阻塞。
这条与 `check_batch_gate.py` 的「任务包裸写」判定同一形态——**生效点自适应**。
理由：一个"上线当天全库报错"的闸门，唯一的结果是被整体关掉。

⚠️ 但**不许把"历史项目不判"读成"这本不用写"**：
判据是"有没有开始"；开始了就必须走完。这是本项目对"留待以后"的一贯态度——
豁免要有名字（`indicator-waived`），拖延没有名字。
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import (  # noqa: E402
    find_chapter_files, find_meta_file, read_text, ensure_utf8_stdio,
)

STORY_HEAD = '故事账'
KEYS = ('选择', '代价', '位移', '可删性', '欲望')
PLACEHOLDER = ('待填', 'TODO', '？？', '??', 'xxx', 'XXX', '……（')
# 位移只许落在这五类上（口径见 guides/story-ledger.md 第二节）
MOVE_KINDS = ('信息', '关系', '位置', '资源', '自我认知')
ABSTRACT_DESIRE = re.compile(
    r'^(想?(变强|复仇|活下去|活着|成功|自由|成长|赢|出人头地)|野心|尊严|证明自己)$')
WATER = 0  # 可删性 == 0 处 = 水章

# ══════════════════════════════════════════════════════════════════════
# 后果链（v11.0，2026-11-01）—— L1 故事层的**第二根轴**
# ══════════════════════════════════════════════════════════════════════
# **为什么写在 check_story 里而不是新开一个脚本**：
#   两者读同一个文件（`_meta`）、同一个时点（动笔前）、同一条"生效点自适应"策略、
#   同一个出口（`make_workorder` 的「故事」列）。**开第二个脚本 = 造平行系统**，
#   而本体系对平行系统的判据是"它会不会让执行者多记一个名字"。
#
# **它查什么**：只查**形状**。查不了"这条推演对不对"（那需要理解），
#   但查得了三件机器能查、而人一定会糊弄的事：
#     ① `二阶` 有没有**指向**（第 N 章 / 下一章 / 后面）。
#        没有指向 = 只推了一跳，写的是形容词。**这是本段最硬的判据。**
#     ② 四栏里有没有**总结词**（`引发冲突`／`关系变差`／`埋下伏笔`…）。
#        判据一句话：**换一本书还成立 → 这是总结，不是后果。**
#     ③ `误判` 有没有被写成「无」。它是"全员清醒"唯一的上游入口，
#        空着 = 这一章没有任何人判断失误 = AI 最典型的那种"全员通透"。
CONSEQ_HEAD = '后果链'
CKEYS = ('动作', '一阶', '二阶', '没看见的路', '误判')
# `二阶` 必须指到具体章号或场景。**只认"有没有指向"，不认"内容好不好"**——
# 内容好不好正则判不了（本体系已有先例：v6.9.0 的"闲笔密度"判据就是这么弃用的）。
SECOND_RX = re.compile(r'第\s*\d{1,4}\s*章|下一?章|后面|之后|后续第|第\s*\d{1,4}\s*节')
# 总结词：共同点是**换一本同类型小说照样成立**。
CONSEQ_BANNED = (
    '引发冲突', '引发矛盾', '关系变差', '关系变微妙', '关系变得微妙', '产生隔阂',
    '局势更复杂', '局势复杂', '更加复杂', '埋下伏笔', '为后续', '为后文',
    '他很痛苦', '很难过', '内心崩溃', '内心震动', '情绪复杂', '矛盾升级',
    '有所成长', '变强了', '更成熟',
)

CH_NO_RX = re.compile(r'第\s*0*(\d{1,4})\s*章')


def chapter_no(p: Path):
    m = CH_NO_RX.search(p.name)
    return int(m.group(1)) if m else None


def _parse_block(text: str, head: str, keys):
    """抽一个 `## <head>` 段的 `- 键：值`。返回 dict / None（无段）。

    解析规则是**跨 agent 的接口契约，不许改**：
    定位 `## <head>` 标题行（允许标题后带括号说明）→ 向下取 `- 键：值` →
    遇到下一个 `## ` 或文件结束为止。键名逐字。
    """
    m = re.search(r'(?m)^##\s*' + head + r'[^\n]*$', text)
    if not m:
        return None
    rest = text[m.end():]
    nxt = re.search(r'(?m)^##\s', rest)
    body = rest[:nxt.start()] if nxt else rest
    out = {}
    for line in body.split('\n'):
        mm = re.match(r'^\s*[-*]\s*([^：:]{1,8})[：:]\s*(.*)$', line)
        if not mm:
            continue
        k, v = mm.group(1).strip(), mm.group(2).strip()
        if k in keys:
            out[k] = v
    return out


def parse_story(text: str):
    """抽「故事账」五格。返回 dict / None（无段）。"""
    return _parse_block(text, STORY_HEAD, KEYS)


def parse_consequence(text: str):
    """抽「后果链」五栏。返回 dict / None（无段）。"""
    return _parse_block(text, CONSEQ_HEAD, CKEYS)


def _conseq_of(row):
    """从一行 `(章号, 章名, meta路径, 故事账)` 里读出后果链。

    ⚠️ **不把后果链塞进 `collect()` 的返回值**：那个 5 元组已被 `enabled_at`、
    `--brief`、`project_curve` 多处按位置索引（`r[3]`）。改元组形状 = 一次静默的连锁破坏，
    而本项目的既定做法是**加一个读法，不动既有形状**。
    """
    meta = row[2] if len(row) > 2 else None
    if not meta:
        return None
    try:
        p = Path(meta)
        if not p.is_file():
            return None
        return parse_consequence(read_text(p))
    except Exception:
        return None


# ⚠️ **项目级"有没有后果链"必须缓存**（2026-11-01 实测出来的性能坑）：
#   `make_workorder` 是**一次一章**地调本脚本的，而单章模式判不出"本项目是否已启用"，
#   于是每章都要回项目里扫一遍 `_meta`。加了后果链之后这个分支变成**几乎每次都成立**
#   （没写后果链的项目永远 `c_enabled_at is None`）→ 一次 QA 里重复扫 N 遍全库。
#   实测后果：`test_guards` 整套从约 7 分钟涨到 10 分钟以上，**被工具超时掐死**，
#   而掐死会留下注入残留（本项目记录过的老坑）。
#   → 读的是**只读事实**（同一进程内文件不会变），缓存安全。
_PROJ_HAS_C = {}


def _project_has_consequence(proj) -> bool:
    key = str(Path(proj))
    if key not in _PROJ_HAS_C:
        ok = False
        try:
            from _shared import find_meta_files
            ok = any(parse_consequence(read_text(m)) for m in find_meta_files(proj))
        except Exception:
            ok = False
        _PROJ_HAS_C[key] = ok
    return _PROJ_HAS_C[key]


def _num(s: str):
    m = re.search(r'(\d{1,3})', s or '')
    return int(m.group(1)) if m else None


def _is_none(v: str) -> bool:
    return bool(re.match(r'^(无|没有|无变化|略|N/?A|不适用)[。，,；;]?', (v or '').strip()))


def validate(d):
    """返回 [(level, msg)]；level ∈ {'err','warn'}。"""
    out = []
    if d is None:
        return [('err', '缺 `## 故事账` 段（五格见 guides/story-ledger.md 第一节）')]
    for k in KEYS:
        v = d.get(k, '').strip()
        if not v:
            out.append(('err', f'缺键或空值：`{k}：`'))
            continue
        if any(p in v for p in PLACEHOLDER):
            out.append(('err', f'`{k}：` 还是待填状态：{v[:40]}'))
            continue
        # 长度只是"没写精度"的兜底信号。三条**不该被它抢先报**的：
        #   · `无`（有自己的专项判据，报"过短"会把真正的问题埋掉）
        #   · `可删性`（有自己的数字判据）
        #   · 抽象欲望（有自己的提示，且往往就是三四个字）
        # ⚠️ 这是"闸门报了错的项"的一种：多个判据命中同一格时，**先报最具体的那个**。
        _skip_len = (_is_none(v) or k == '可删性'
                     or (k == '欲望' and ABSTRACT_DESIRE.match(v.rstrip('。.!！'))))
        if not _skip_len and len(v) < 4:
            out.append(('err', f'`{k}：` 过短（<4 字），没写到能用的精度：{v}'))

    move = d.get('位移', '').strip()
    if move and not any(p in move for p in PLACEHOLDER):
        if _is_none(move):
            out.append(('err', '`位移：无` **不许**——静场章也要有变化（关系/认知/位置至少变一样）。'
                               '真正无事发生的章，问题在"这一章该不该存在"'))
        elif not any(k in move[:12] for k in MOVE_KINDS):
            out.append(('warn', f'`位移：` 没落在五类上（{"／".join(MOVE_KINDS)}）：{move[:30]}…'
                                '—— 自造类别会让跨章曲线对不上'))

    del_n = _num(d.get('可删性', ''))
    if del_n is None and d.get('可删性'):
        out.append(('err', '`可删性：` 没给数字（写法：`3 处（第14章…／第16章…／第19章…）`）。'
                           '**没给数字 = 没算过**'))
    elif del_n == WATER:
        out.append(('err', '`可删性：0 处` = **水章**（删掉它后文不用改）。'
                           '要么删，要么给它一件事（见 story-ledger 第三节「三种病」）'))

    if _is_none(d.get('选择', '')) and del_n is not None and del_n < 3:
        out.append(('err', '本章`选择：无`，而`可删性`又 <3 处 —— 流水账的典型形态：'
                           '没有决定，也不承重'))

    cost = d.get('代价', '').strip()
    if _is_none(cost):
        if not re.search(r'第\s*\d+\s*章|蓄能|下一?章兑现', cost):
            out.append(('err', '`代价：无` 必须带指向（`无——代价在第14章兑现` / `无——本章是蓄能章`），'
                               '否则无法与"忘了写"区分'))

    desire = d.get('欲望', '').strip()
    if desire and ABSTRACT_DESIRE.match(desire.rstrip('。.!！')):
        out.append(('warn', f'`欲望：{desire}` 是抽象词 —— 读者没有线可抓。'
                            '换成"具体对象 + 期限或数量"（`下个月前凑够三十七两，把妹妹从契上赎回来`）'))
    return out


def _norm_c(s: str) -> str:
    return re.sub(r'[\s，,、。；;：:·*`「」“”"\'（）()—-]', '', s or '')


def validate_consequence(d):
    """后果链形状校验。返回 [(level, msg)]；level ∈ {'err','warn'}。

    **判据全部落在"能不能指到具体物/具体章"上**，不落在"写得好不好"上——
    因为"写得好不好"判不了（见本文件顶部那条自陈），而"有没有指向"判得了。
    """
    out = []
    if d is None:
        return [('err', '缺 `## 后果链` 段（五栏见 guides/consequence-chain.md 第四节）')]

    for k in CKEYS:
        v = (d.get(k) or '').strip()
        if not v:
            out.append(('err', f'缺键或空值：`{k}：`'))
        elif any(p in v for p in PLACEHOLDER):
            out.append(('err', f'`{k}：` 还是待填状态：{v[:40]}'))

    def _v(k):
        v = (d.get(k) or '').strip()
        return '' if any(p in v for p in PLACEHOLDER) else v

    # ① 二阶必须指到具体章号/场景 —— 本段最硬的判据
    second = _v('二阶')
    if second:
        if not SECOND_RX.search(second):
            out.append(('err', '`二阶：` 没有指向 —— 要写清"逼出了哪一章的哪件事"'
                               '（`第9章`／`下一章`／`后面那场退货`）。'
                               '**没有指向 = 只推了一跳，你写的是形容词**'))
        elif len(second) < 8:
            out.append(('err', f'`二阶：` 过短（<8 字），没写到能用的精度：{second}'))

    # ② 一阶 ≈ 二阶 → 同一跳拆成两句
    _n1, _n2 = _norm_c(_v('一阶')), _norm_c(_v('二阶'))
    if _n1 and _n2 and (_n1 == _n2
                        or (_n1 in _n2 and len(_n1) >= len(_n2) * 0.8)
                        or (_n2 in _n1 and len(_n2) >= len(_n1) * 0.8)):
        out.append(('err', '`一阶` 和 `二阶` 几乎一样 —— 那是**同一跳**拆成两句话说，不是推演'))

    # ③ 总结词
    for k in ('一阶', '二阶', '没看见的路', '误判'):
        v = _v(k)
        hit = next((b for b in CONSEQ_BANNED if b in v), None)
        if hit:
            out.append(('err', f'`{k}：` 出现总结词「{hit}」—— **换一本书还成立 = 总结，不是后果**。'
                               '换成可观察的：谁做了什么／什么东西换了位置／哪句话改了'))

    # ④ 误判 不许"无"（它是"全员清醒"唯一的上游入口）
    wrong = _v('误判')
    if wrong:
        if _is_none(wrong):
            out.append(('err', '`误判：无` **不许** —— 这一栏是"全员清醒"唯一的解药。'
                               '蓄能章也要写"谁错判了形势"（谁、依据什么信息、错在哪）'))
        elif len(wrong) < 8:
            out.append(('err', f'`误判：` 过短（<8 字），没写到能用的精度：{wrong}'))

    # ⑤ 没看见的路不许写成"别无选择"（那是作者替人物关的门）
    nv = _v('没看见的路')
    if nv and ('别无选择' in nv or '没有别的路' in nv or '只能这样' in nv):
        out.append(('err', '`没看见的路：他别无选择` —— 那是**作者替人物关的门**。'
                           '要写"读者会想到、他没选"的那条，**以及他为什么没选**'
                           '（不是蠢，是他手里只有当时那些信息）'))

    act = _v('动作')
    if act and len(act) < 6:
        out.append(('err', f'`动作：` 过短（<6 字），要写到"谁做了什么"：{act}'))
    return out


def collect(target: Path):
    """返回 (rows, proj)。rows = [(章号, 章节名, meta路径, 故事账dict|None)]"""
    if target.is_file():
        n = chapter_no(target)
        if target.name.endswith('.meta.md'):
            _proj = target.parent.parent if target.parent.name == '_meta' else target.parent
            return [(n, target.name, target, parse_story(read_text(target)))], _proj
        _proj = target.parent.parent if target.parent.name in ('chapters', '正文', '章节目录') else target.parent
        meta = find_meta_file(_proj, n) if n is not None else None
        d = parse_story(read_text(meta)) if meta else None
        return [(n, target.name, meta, d)], _proj

    files = find_chapter_files(target)
    if not files:
        return [], target
    rows = []
    for f in files:
        n = chapter_no(f)
        meta = find_meta_file(target, n) if n is not None else None
        d = parse_story(read_text(meta)) if meta else None
        rows.append((n, f.name, meta, d))
    return rows, target


def project_curve(rows):
    """跨章曲线：单章看不出的停滞，只有铺开才看得见。"""
    alerts = []
    seq = [(n, d) for n, _f, _m, d in rows if d]

    # ① 原地转：连续 3 章位移同一类
    run, kind = 1, None
    for i, (n, d) in enumerate(seq):
        cur = next((k for k in MOVE_KINDS if k in (d.get('位移') or '')[:12]), None)
        if i and cur and cur == kind:
            run += 1
            if run == 3:
                alerts.append(f'第{n - 2}–{n}章 连续 3 章`位移`都是「{kind}」——'
                              f'**原地转风险**：读者会觉得在原地踏步')
        else:
            run, kind = 1, cur

    # ② 白给：连续 5 章代价为无
    run = 0
    for n, d in seq:
        if _is_none(d.get('代价', '')):
            run += 1
            if run == 5:
                alerts.append(f'第{n - 4}–{n}章 连续 5 章`代价：无`——**白给风险**：'
                              f'爽点没有账单，读者不为他高兴')
        else:
            run = 0

    # ③ 流水账：连续 3 章可删性 <3
    run = 0
    for n, d in seq:
        v = _num(d.get('可删性', ''))
        if v is not None and v < 3:
            run += 1
            if run == 3:
                alerts.append(f'第{n - 2}–{n}章 连续 3 章`可删性`<3 处——**流水账风险**：'
                              f'这几章可以并成半章')
        else:
            run = 0

    # ④ 位移方向断了：五类里某一类全书没出现过（长篇常见的"只会变强"）
    seen = {k for _n, d in seq for k in MOVE_KINDS if k in (d.get('位移') or '')[:12]}
    if len(seq) >= 6 and len(seen) <= 2:
        alerts.append(f'全书 {len(seq)} 章只用到位移类别 {sorted(seen) or "无"} —— '
                      f'长篇的单一维度：读者对"又来了"的耐受度下降很快')

    # ⑤ 后果链：二阶指向了"过去"或"自己"——那不是后果，那是回忆
    back = []
    for n, _f, _m, _d in rows:
        c = _conseq_of((n, _f, _m, _d)) or {}
        sec = (c.get('二阶') or '').strip()
        mm = re.search(r'第\s*(\d{1,4})\s*章', sec)
        if mm and n is not None and int(mm.group(1)) <= n:
            back.append(f'第{n}章 `二阶` 指向第{mm.group(1)}章（不晚于本章）')
    if back:
        alerts.append('后果链指向了**过去或自己**（3 例以上列出）：' + '；'.join(back[:3])
                      + ' —— `二阶` 必须是"从这一章往后长出来的东西"')

    # ⑥ 误判在复读：连续 3 章开头 6 字相同 = 模板化填写（"他以为对方会…"用到第三遍）
    run, head = 0, None
    for n, _f, _m, _d in rows:
        c = _conseq_of((n, _f, _m, _d)) or {}
        cur = _norm_c(c.get('误判', ''))[:6]
        if cur and cur == head:
            run += 1
            if run == 3:
                alerts.append(f'第{n - 2}–{n}章 `误判` 开头都是「{cur}」——**模板化填写**：'
                              f'三章是同一种错法，读者会看出公式')
        else:
            run, head = 1, cur
    return alerts


def main():
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('target')
    ap.add_argument('--project', action='store_true', help='附跨章曲线')
    ap.add_argument('--brief', action='store_true')
    a = ap.parse_args()

    target = Path(a.target).expanduser()
    if not target.exists():
        print(f'✗ 路径不存在：{target}')
        return 2
    rows, proj = collect(target)
    if not rows:
        print(f'✗ 在 {target} 下找不到章节文件（chapters/ 第*.md）—— 不是"没事发生"，是**看不见稿子**')
        return 2

    enabled_at = next((i for i, r in enumerate(rows) if r[3] is not None), None)
    c_enabled_at = next((i for i, r in enumerate(rows) if _conseq_of(r) is not None), None)
    # ★ 单章模式（`make_workorder.py --archive` 就是这么调的：一次只给一个章节文件）
    #   下，`rows` 里只有这一章 → 单看 rows 永远判不出"本项目是否已启用"，
    #   于是**已经启用的项目里，漏写故事账的章会被放行**（"生效点"的洞）。
    #   所以单章模式要回项目里扫一遍 `_meta`，用项目级事实定生效点。
    #   ★ 后果链同理，且**与故事账各自独立算生效点**：先写故事账、后加后果链的项目，
    #     不该因为"故事账早就有"而被要求"每一章都得有后果链"（那会把老书全线判死）。
    if (enabled_at is None or c_enabled_at is None) and len(rows) == 1:
        if enabled_at is None:
            try:
                from _shared import find_meta_files
                if any(parse_story(read_text(m)) for m in find_meta_files(proj)):
                    enabled_at = 0
            except Exception:
                pass
        # ★ 后果链用**缓存版**（见 `_project_has_consequence` 的说明）：
        #   这个分支在"没写后果链的项目"里几乎每次调用都成立，
        #   不缓存就会把整个 `_meta` 目录反复扫一遍（实测把 QA 从 7 分钟拖到 10 分钟以上）。
        if c_enabled_at is None and _project_has_consequence(proj):
            c_enabled_at = 0
    if enabled_at is None and c_enabled_at is None:
        print(f'ℹ 本项目尚未启用故事账／后果链（{len(rows)} 章都没有对应段）。')
        print('  历史项目不追溯 —— **但从这一章起要写**：')
        print('  · guides/story-ledger.md（五格：这一章该不该存在）')
        print('  · guides/consequence-chain.md（五栏：这一章会往哪儿长）')
        print('  载体：chapters/_meta/<章名>.meta.md 的 `## 故事账` / `## 后果链`（动笔前定）')
        return 0

    errs, warns = 0, 0
    for i, row in enumerate(rows):
        n, name, meta, d = row
        c = _conseq_of(row)
        issues = []
        # ① 故事账
        if d is None:
            if enabled_at is not None and i >= enabled_at:   # 生效点之前的历史章不判
                issues.append(('err', '缺 `## 故事账` 段（本项目自第 %d 章起启用，本章必须写）'
                               % rows[enabled_at][0]))
        else:
            issues += validate(d)
        # ② 后果链
        if c is None:
            if c_enabled_at is not None and i >= c_enabled_at:
                issues.append(('err', '缺 `## 后果链` 段（本项目自第 %d 章起启用，本章必须写）'
                               % rows[c_enabled_at][0]))
        else:
            issues += validate_consequence(c)
        if not issues and d is None and c is None:
            continue

        e = [m for lv, m in issues if lv == 'err']
        w = [m for lv, m in issues if lv == 'warn']
        errs += len(e)
        warns += len(w)
        if a.brief:
            tag = '✗' if e else ('!' if w else '✓')
            move = (d or {}).get('位移', '—')[:14]
            dele = (d or {}).get('可删性', '—')[:8]
            print(f'{tag} 第{n}章  位移:{move:<16} 可删性:{dele}')
        else:
            head = f'第 {n} 章 · {name}'
            if not e and not w:
                print(f'✓ {head}')
            else:
                print(('✗ ' if e else '! ') + head)
            for m in e:
                print(f'    [阻塞] {m}')
            for m in w:
                print(f'    [提示] {m}')
            if not e and not w:
                continue
            if meta is None:
                print(f'    落盘位置：chapters/_meta/第{n:02d}章-*.meta.md 的 '
                      f'`## 故事账` ／ `## 后果链`')

    if a.project:
        alerts = project_curve(rows)
        print('\n' + '=' * 62)
        print(f'跨章故事曲线（{len([1 for r in rows if r[3]])} 章有账 ／ '
              f'{len([1 for r in rows if _conseq_of(r)])} 章有后果链）')
        if alerts:
            for m in alerts:
                print(f'  [提示] {m}')
        else:
            print('  ✓ 没有连续同值的停滞迹象')

    print('\n' + '-' * 62)
    print(f'合计：阻塞 {errs} 项 ｜ 提示 {warns} 项 ｜ 章数 {len(rows)}')
    if errs:
        print('→ 先修阻塞项。**动笔前**定的东西，写完再补只会补出"我写的东西的意义"。')
        return 1
    print('→ 形状合格。**注意：本脚本不判好坏** —— '
          '好坏由独立评委跑三条判据（选择是否两难／代价是否已发生／位移是否不可逆）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
