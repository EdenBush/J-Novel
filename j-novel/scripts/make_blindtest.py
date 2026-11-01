#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「盲测题」：把本章 3 段与真实人类 3 段打乱，交给独立评委盲判。

用法:
    python scripts/make_blindtest.py <项目目录> --chapter 7
    python scripts/make_blindtest.py <项目目录> --chapter 7 --human 我的素材.txt
    python scripts/make_blindtest.py <项目目录> --chapter 7 --len 150 --blocks 3

产出（两份，**分开放，评委只许看前一份**）:
    chapters/_meta/第NN章-盲测题.md     ← 转交给评委的全文
    chapters/_meta/第NN章-盲测答案.md   ← 答案与计分规则，**不许给评委**

退出码: 0 = 已生成；1 = 取不到足够的材料；2 = 项目/章节读不出

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 为什么要有盲测（它治的是"判据可以被凑"）

本体系现有的"像不像人"判据，全部是**对照清单自查**：
17 项指标、11 项量表、五项测试、可替换性四问。
它们的问题是同一个：**清单一旦公开，就可以被满足**。
实测已经出现过——`招式依据` 那一行写了 4 本**从没打开过**的指南，闸门全过
（旧校验只认格式，而格式是最容易模仿的东西）。

盲测换了一个**不能靠"满足"通过的任务**：
> 这里有 6 段文字，其中 3 段真实人类写的、3 段是本章。**哪 3 段是人写的？**

它有三个性质，是清单做不到的：
  ① **没有可模仿的格式** —— 唯一的答案是一个划分，不是一个可填的表格；
  ② **有对照** —— 评判必须把本章和**真人**放在一起比，"整体感觉不错"在这种任务上无效；
  ③ **会暴露具体句子** —— 评委被迫说出"哪一句露了"，那就是要改的句子
     （而"自己挑句子"是被明令禁止的做法，见 deai-entry 禁令一）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 三个设计约束（每条都对应一个会让测试失效的坑）

1. **对照必须是真人类，不许是本书。**
   `细纲/_最佳段落库.md` 里的段落是**本书（AI 写的）**——拿它当对照 = 自证。
   所以人类池只取 `guides/human-exemplars.md`（原文摘录）或 `--human` 指定的真实文本。

2. **六段必须等长。** 长度是评委最容易抓的廉价线索：
   如果人类段 50 字、本章段 300 字，评委不需要读内容就能分开 → 测试变成测长度。
   故：先算人类段的可用长度，再把本章段裁到同一档（±15%）。

3. **答案不许泄。** 题面里不出现书名、不出现"本章"二字、不按来源排序（用 seed 打乱）。
   ⚠️ 实测过的同类事故：手写提示词时"等距抽 8 句"被转述成"每章抽一句"——
   **模板一经手抄就向"更省事"漂**。所以题面由脚本生成，**不要手写**。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 诚实边界（别把它当闸门）

· 人类池目前很薄：`human-exemplars.md` 20 段 / 3 本书，够长的（≥70 字）只有 7 段。
  → 所以本测试是**诊断**，不是闸门；它给出的是一个**方向明确的读数**，不是合格证。
· 评委是模型，模型有偏好（偏爱"有毛边"的文本）。所以答案页规定**评委可靠性检查**：
  若评委把 ≥2 段真人判成 AI，**本次结果作废**（不是"本章不合格"）。
· 它测的是"**短段落的语感**"，不是"整章好不好看"。故事层归 `guides/story-ledger.md`。
"""
import argparse
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _shared import (  # noqa: E402
    find_chapter_files, extract_body, read_text, ensure_utf8_stdio,
    parse_exemplar_sections, panel_ratio,
)

CH_NO_RX = re.compile(r'第\s*0*(\d{1,4})\s*章')
# ★ v9.1：片段解析**已收归 `_shared.parse_exemplar_sections`**（两个消费者：本脚本 + 任务包
#   的 `human_exemplar` 槽位）。各写一份的后果不是"重复"，是**两边抽到不同片段**：
#   盲测用的对照组和写手看到的范本不是同一批文本，而且两处都不会报错。
#   （本文件原有一份逐行扫描实现，正因为"另一处也写了一份"才收归。）


def sentences(text: str):
    """粗分句：以 。！？… 断句，换行也当断点。够用即可（不需要完美分词）。"""
    out, buf = [], ''
    for ch in text:
        buf += ch
        if ch in '。！？…':
            s = buf.strip()
            if len(s) > 2:
                out.append(s)
            buf = ''
        elif ch == '\n':
            s = buf.strip()
            if len(s) > 2:
                out.append(s)
            buf = ''
    if buf.strip() and len(buf.strip()) > 2:
        out.append(buf.strip())
    return out


def windows(text: str, target: int):
    """按句边界拼出 ≥target 字的窗口。"""
    sents = sentences(text)
    wins, buf = [], ''
    for s in sents:
        buf += s
        if len(buf) >= target:
            wins.append(buf)
            buf = ''
    if buf and len(buf) >= target * 0.6:
        wins.append(buf)
    return wins


def load_human_pool(extra: Path = None):
    """返回 [(出处, 正文)]。正文已剥掉 markdown 粗体标记。

    内建来源走共享解析器（`_shared.parse_exemplar_sections`），与任务包的
    `human_exemplar` 槽位**同一份解析代码**——否则盲测的对照组和写手看到的范本
    会悄悄变成两批不同的文本。
    """
    pool = []
    if extra and extra.is_file():
        raw = read_text(extra)
        for para in re.split(r'\n\s*\n', raw):
            t = re.sub(r'^#.*$', '', para, flags=re.M).strip()
            t = re.sub(r'^\s*[-*]\s+', '', t, flags=re.M)
            if len(t) >= 70 and panel_ratio(t) < 0.3:
                pool.append((f'外部素材：{extra.name}', t))
    he = Path(__file__).resolve().parent.parent / 'references' / 'guides' / 'human-exemplars.md'
    if he.is_file():
        for _title, pairs in parse_exemplar_sections(read_text(he)):
            pool.extend(pairs)
    return pool


def trim_to(t: str, n: int) -> str:
    """裁到 ≈n 字，**必须落在句末**。

    ⚠️ 初版直接 `t[:n]` 硬切，于是人类段被切成
    「…9月1号正是流火的末端从学校出发搭乘737…迎接自己的居然只有602宿舍的一帮光棍」
    和「…然后就用了银行里所有的积蓄（本来」——**半句截断本身就是最强的 AI 指纹**，
    等于给真人段人为加了破绽。这是"裁判工具污染被试"的典型。

    正确形态：优先取 [0.55n, 1.2n] 内**最后一个句末**；找不到就向后找**下一个**句末；
    再找不到才硬切（并保证长度≤1.2n，不会长出太多）。
    """
    t = t.strip()
    if len(t) <= n * 1.2:
        return t
    seg = t[:int(n * 1.2)]
    idx = max(seg.rfind(m) for m in '。！？…')
    if idx >= n * 0.55:
        return seg[:idx + 1]
    tail = t[int(n * 0.55):]
    m = re.search(r'[。！？…]', tail)
    if m:
        return t[:int(n * 0.55) + m.end()]
    return seg


def pick_even(wins, k, L):
    """等距抽 k 段（20%/50%/80%），不挑——「不许挑」是禁令一的要求。

    ⚠️ 初版这里写了一个"边取边查重"的循环，**索引找不到时会无限漂**。
    等距抽样的正确形态是：**先算出目标下标集合，再去重、再补齐**，不要边抽边判。
    """
    n = len(wins)
    if n <= k:
        return list(wins)
    idxs = {min(n - 1, max(0, round(n * r))) for r in (0.2, 0.5, 0.8)}
    i = 0
    while len(idxs) < k and i < n:          # 等距点撞车时，从前往后补齐
        idxs.add(i)
        i += 1
    return [wins[i] for i in sorted(idxs)[:k]]


def main():
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('project')
    ap.add_argument('--chapter', type=int, required=True)
    ap.add_argument('--human', default=None, help='外部真实文本（优先于内建人类范本库）')
    ap.add_argument('--blocks', type=int, default=3)
    ap.add_argument('--len', type=int, default=120, help='每段目标字数（会被人类池长度自动下调）')
    a = ap.parse_args()

    proj = Path(a.project).expanduser()
    if not proj.exists():
        print(f'✗ 项目路径不存在：{proj}')
        return 2
    files = [f for f in find_chapter_files(proj)
             if (m := CH_NO_RX.search(f.name)) and int(m.group(1)) == a.chapter]
    if not files:
        print(f'✗ 找不到第 {a.chapter} 章正文（在 chapters/ 下找 `第NN章-*.md`）')
        return 2

    body = extract_body(read_text(files[0]), keep_blank=True)
    pool = load_human_pool(Path(a.human) if a.human else None)
    if len(pool) < a.blocks:
        print(f'✗ 人类对照池不足：只有 {len(pool)} 段（需要 {a.blocks} 段）。'
              f'用 `--human <真实文本>` 指定作者投喂的素材。')
        return 1

    rng = random.Random(a.chapter * 7919)
    # ① 长度优先：短段太容易被"读起来像不像一句话"左右，语感信息量不足。
    #    ⚠️ 阈值定 90 而不是 100：本库剥掉粗体后 ≥100 字的只剩 **2 段**，
    #    而盲测要 3 段人类对照——阈值太严会让脚本"抽不出题"而不是"题不好"。
    #    这是"判据太窄"的又一种形态：**看似在挑好料，实际是把自己饿死**。
    cand = [p for p in pool if len(p[1]) >= 90] or sorted(pool, key=lambda x: -len(x[1]))[:a.blocks]
    cand.sort(key=lambda x: -len(x[1]))
    cand = cand[:max(a.blocks * 2, 5)]
    rng.shuffle(cand)
    # ② 出处尽量分散（同一本书的腔调一致，会形成模式让评委抓住"这批特点是 AI"）
    chosen, seen_books = [], set()
    for book, t in cand:
        if book in seen_books and len(chosen) < a.blocks:
            continue
        chosen.append((book, t))
        seen_books.add(book)
        if len(chosen) == a.blocks:
            break
    for book, t in cand:                       # 出处不够时再放宽
        if len(chosen) == a.blocks:
            break
        if (book, t) not in chosen:
            chosen.append((book, t))

    L = min([a.len] + [len(t) for _b, t in chosen])

    wins = windows(body, int(L * 0.9))
    # ★ 抽不够就**降阶梯**重抽，不许"少抽两段继续跑"。
    #   实测事故：某次只抽出 2 段，脚本照样生成了 5 段（人类 3 / 本书 2）的题，
    #   而答案页的计分写的是「÷3」——**分母和题目不一致，读数直接作废**。
    #   与本项目最忌讳的"看起来在工作"同型：输出格式正常，数字是错的。
    for ratio in (0.9, 0.7, 0.55, 0.4):
        wins = windows(body, max(50, int(L * ratio)))
        if len(wins) >= a.blocks:
            break
    if len(wins) < a.blocks:
        print(f'✗ 本章抽不出 {a.blocks} 段不重复的窗口（正文 {len(body)} 字 → 窗口 {len(wins)} 个）。'
              f'把 --len 调到 {max(50, len(body) // (a.blocks + 1))} 以下再试；'
              f'或者本章太短，盲测对它没有意义。')
        return 1
    # 六段对齐到同一长度档（取六段里最短的那段），否则"长度"会成为评委的廉价线索
    L_eff = min([L] + [len(w) for w in wins])
    own_blocks = [trim_to(w, L_eff) for w in pick_even(wins, a.blocks, L_eff)]
    # 人类段最后才裁 —— 必须与本书段用**同一个长度档**（L_eff），否则长度泄题
    human_blocks = [trim_to(t, L_eff) for _b, t in chosen]

    items = [('人', b, t) for b, t in zip([c[0] for c in chosen], human_blocks)]
    items += [('本书', f'第{a.chapter}章', t) for t in own_blocks]
    rng.shuffle(items)

    letters = 'ABCDEFGH'[:len(items)]
    out_dir = proj / 'chapters' / '_meta'
    out_dir.mkdir(parents=True, exist_ok=True)
    q = out_dir / f'第{a.chapter:02d}章-盲测题.md'
    k = out_dir / f'第{a.chapter:02d}章-盲测答案.md'

    lines = [
        f'# 盲测题（第 {a.chapter} 章）',
        '',
        '> 下面 %d 段文字，**其中 %d 段是真人写的、%d 段是同一个作者写的另一份稿子**。' % (len(items), a.blocks, len(items) - a.blocks),
        '> 你的任务是**只凭文字本身**把它们分开。',
        '',
        '**规则（请严格遵守）**',
        '1. 不要猜"哪段更像网文""哪段更文雅"——**只看手感**：写法上有没有一个人在场、有没有多余的东西、',
        '   句子是不是每句都在干活、有没有一处写得其实不太好。',
        '2. 每一段都必须给出：`人 / AI` 的判定 + **一句露出破绽的原句**（逐字引用）+ 一句话理由。',
        '3. **不许空泛**：「整体感觉不错」「语言流畅」不算理由，会被退回。',
        '4. 最后给一句：如果只能保一段，你保哪段，为什么。',
        '',
        '**输出格式**',
        '```',
        'A：人 / AI',
        '  破绽句：「……」',
        '  理由：……',
        '（B–%s 同上）' % letters[-1],
        '最终：判为「人」的是 ____；最该保的是 ____。',
        '```',
        '',
        '---',
        '',
    ]
    for letter, (_src, _ref, text) in zip(letters, items):
        lines += [f'## {letter}', '', text.strip(), '']
    q.write_text('\n'.join(lines), encoding='utf-8')

    key = [
        f'# 盲测答案（第 {a.chapter} 章）—— **不许交给评委**',
        '',
        '| 段 | 来源 | 出处 |',
        '|---|---|---|',
    ]
    for letter, (src, ref, _t) in zip(letters, items):
        key.append(f'| {letter} | {src} | {ref} |')
    key += [
        '',
        '## 计分（照抄，不许自创）',
        '',
        '- **人类置信度** = 被判为「人」的**本书段**数 ÷ %d' % a.blocks,
        '- **评委可靠性** = 被判为「人」的**人类段**数 ÷ %d' % a.blocks,
        '  - ≥ %d：可靠' % max(1, a.blocks - 1),
        '  - ≤ %d：**本次作废**（评委没在读手感，在乱猜——不是本章不合格）' % (a.blocks - 2),
        '',
        '## 判读',
        '',
        '| 人类置信度 | 含义 | 下一步 |',
        '|---|---|---|',
        '| 3/3 | 已到"分不出来" | 只处理评委点出的破绽句 |',
        '| 2/3 | 过 | 同上，顺手改 |',
        '| ≤1/3 | 没过 | **回 `deai-entry.md`**，档位至少 R2；把评委的破绽句逐条进改写方案表 |',
        '',
        '⚠️ **连续 3 章 ≤1/3 → 升级为 R3（全书视图）**：那不是某几句的问题，是这本书的语域整体偏了。',
        '',
        '## 诚实边界',
        '',
        '- 这是**诊断**，不是闸门。人类池只有 3 本书的摘录，评委也只读短段。',
        '- 它测"短段落的语感"，不测"整章好不好看"——后者归 `guides/story-ledger.md`。',
        '- **不许拿本书的段落当对照**（`_最佳段落库.md` 是 AI 写的，那是自证）。',
    ]
    k.write_text('\n'.join(key), encoding='utf-8')

    print(f'✓ 题面：{q}')
    print(f'✓ 答案：{k}  ← **不许给评委**')
    n_h = sum(1 for s, _r, _t in items if s == '人')
    print(f'  共 {len(items)} 段（人类 {n_h} / 本书 {len(items) - n_h}）｜每段目标 {L_eff} 字')
    print('  下一步：把「题面」全文转交给一个**没读过本章、没见过规则包**的子代理。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
