# ⚠️ 这里不是运行时读物 —— Agent 不要读这些文件

本目录的 `reasonix-novel-*.md` 是 12 个子技能**导入时的源快照**（随 `_user_meta.json`
的 `source: userImport` 一起带进来的），**不是本 SKILL 的组成部分**。

## 为什么 Agent 不该读它们

| 事实 | 后果 |
|---|---|
| 它们是**快照**，不随子技能更新 | 读到的可能是**过期方法论** |
| 真正的技能在 `~/.workbuddy/skills/reasonix-novel-*/SKILL.md` | 这里读到的和**实际生效的**可能不是一份东西 |
| 本 SKILL 的任何文件都不引用它们 | 读了就是读了**没人维护的副本** |

**实测漂移（2026-09-19）**：`tools/reasonix-novel-deai.md` 与已安装的
`reasonix-novel-deai/SKILL.md` 已经不一致——前者还是「分六步重写 + 第六步独立点数」，
后者已改成「分六步洗稿 + 第六步并入收尾」。**两份真相源必然分叉，这只是时间问题。**

## 子技能的正确用法

需要专业版方法时，通过 **Skill 工具**加载对应技能（以该技能的人格身份工作）：

```
reasonix-novel-deai / pad / hard-check / write-master / dialogue-master /
mood-composer / thrill-booster / rhythm-check / reader-sim / bible-updater /
weaver / weaver-lite / weaver-pro / weaver-lite-pro
```

调度规则见 `references/guide-index.md` 的「子技能触发表」。

## 那这个目录留着干什么

作为**导入溯源**保留（能看出这批技能当初是从哪一份源装进来的）。
它不参与创作流程，删除它不会影响本 SKILL 的任何功能，但**保留它也没有任何运行价值**。
如果你（人类用户）确认不需要溯源，整个 `tools/` 目录可以直接删。

> 本文件的存在由 `scripts/audit_release.py` 的守卫盯着——如果这份声明被删掉，
> 而 `tools/` 还在，自检会报警（防止"看起来像技能文件的东西"重新变成无标注的陷阱）。
