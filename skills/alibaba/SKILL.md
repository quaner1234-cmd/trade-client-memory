---
name: trade-client-memory-alibaba
description: 外贸业务员用的阿里巴巴国际站询盘整理 Skill——用户授权已登录的阿里卖家后台后，自动读取询盘与聊天记录，去重、分客户建档、判断进展与下一步，输出本地 Markdown 客户档案（一个客户一个文件夹）和客户总览。适用于“整理阿里询盘/更新客户档案/看哪个客户该跟进/定时增量同步”。只读不发送，历史只追加不删除。已在 Hermes 环境端到端验证；其他 Agent 未验证。
---

# 阿里巴巴国际站询盘整理 Skill

给不懂编程的外贸业务员用。**授权浏览器 → 等待 → 直接看到每个阿里客户现在怎么样、接下来做什么**；要看历史和原文时打开对应客户文件夹。

所有脚本在 `scripts/`（相对本文件）。用户数据写在**用户选择的基础目录** `<base>/` 下：`客户档案/`（可见成果）与 `内部数据/`（程序状态）。**绝不写进本 skill 安装目录、不进 Git、不进发布包。**

与 Email Skill 的关系：本 Skill **独立可用**，输出结构相同（一个客户一个文件夹 + 客户概览），两者可共存互不干扰。不读取邮箱，不修改 Email Skill 任何文件。

## 安全红线（每阶段适用，违反即停）

1. **只读平台**：不发送、不回复、不删除、不标记任何消息。脚本本身无写能力，也不得通过其它工具代做。
2. **不外传**：客户数据只落在 `<base>/`。未经用户明确同意，不上传 Notion / 云端 / 第三方。提醒一次即可，不要反复问。
3. **数据隔离**：`<base>/` 永不进 skill 目录、不进 Git、不进发布包。发布或打包前必须跑 `scripts/tcm_ali.py audit`。
4. **不编造**：判断不出的事实一律写「待确认」。平台的数量字段不等于确认订单；国家取自平台字段时标注「未核实」。
5. **不遗漏**：匹配不上的客户**必须**进 `客户档案/_待确认/`，不许静默跳过。
6. **append-only**：只追加。**禁止删除、覆盖或为了“修复”而替换历史内容。**
7. **隐私讲清一次**：档案在本地，但“理解聊天”会经过 Agent 所用模型的云端；公司禁止数据出网时改用本地模型或先脱敏。

## 目录约定（`<base>/` 下）

```
客户档案/
  <客户名 - 国家>/
    客户沟通记录.md        机械生成的时间线（幂等追加）
    附件/                  图片与文件（可以为空目录）
    客户进展.md            仅 Agent 区域可写，见 Phase 4
  _待确认/                 匹配不上的线索，一个会话一个 md
  客户概览.md              所有客户的总览表
内部数据/
  raw/list-<ts>.json       本次抓到的会话列表原文
  raw/conv-<id>-<ts>.json  本次抓到的会话消息原文
  state.json               检查点、去重已见 ID、客户映射
  fetch-status.json        抓取进度（中断恢复用）
  run.log                  本地运行日志（不进 Git）
```

## Where are we（每次先跑）

进度**只认 `<base>/内部数据/state.json` 与 `fetch-status.json`**，不从对话记忆推断：

```bash
python scripts/tcm_ali.py --base <base> status
```

- 报告未初始化 → 从 Phase 0 开始。
- `fetch-status.json` 存在且 `finished:false` → 上次抓取中断，**直接重跑 extract**（已抓到的不会重复）。

## Phase 0 — 初始化与环境检查

```bash
python scripts/tcm_ali.py --base <base> init
python scripts/tcm_ali.py --base <base> doctor
```

`<base>` 默认取用户当前目录，向用户确认一句即可。`doctor` 检查写权限与浏览器桥接是否可用。**桥接不可用必须停下来告诉用户**，不得伪造数据。

## Phase 1 — 读取阿里卖家后台（只读）

用户需先在**自己的 Chrome** 里登录阿里卖家后台（message.alibaba.com）。Agent 通过浏览器桥接接管该已登录会话，**只读**操作：

```bash
python scripts/tcm_ali.py --base <base> extract --max-conv 20 --since 2026-09-01
```

- `--max-conv` 单次最多打开几个会话（默认 20，保守以免超时）。
- `--since` 只处理该日期之后有更新的会话。
- 原文落 `内部数据/raw/`，同时推进 `state.json` 检查点。

**未登录时**：脚本只记录“未登录 / 列表为空”并停止，**不写入失败结论**，让用户登录后重试。严禁把“未登录”推断成“没有新询盘”。

## Phase 2 — 导入、去重、建档（全自动，确定性代码）

```bash
python scripts/tcm_ali.py --base <base> ingest --owner "<业务员显示名>"
```

`--owner` 是**业务员自己的**显示名（填真实姓名即可，仅存本地 state），用于区分“我方”与“客户”，会持久化到 state。

机械完成的动作：
- 消息 ID 去掉 `.PNM` 传输后缀后按 ID 去重 → 同一条消息永不重复入档。
- 已有档案的客户：**追加**新消息，不改动旧内容。
- 无档案但有买家实质消息 → 新建 `<客户名 - 未核实国家>/` 客户目录。
- 无档案且无实质内容（仅系统分配 / 移交 / 我方追问）→ 标为「待核验」线索，不遗漏。
- 客户名存在歧义（库中同名）→ 一律进 `_待确认/`，**不猜测归属**。

## Phase 3 — 判断进展（这是你的核心判断，不是脚本）

读 `客户档案/<客户>/客户沟通记录.md`，按 `references/trade-judgement.md` 判断，尤其注意：
- 平台数量字段 ≠ 确认订单，不要把“客户要 500 件”写成事实。
- “我方已发”只说明我方已行动，不代表客户已回复。
- 公司、国家不靠图片和名字猜；没有佐证就写待确认。

## Phase 4 — 写进展与下一步（只编辑 Agent 区域）

```bash
python scripts/tcm_ali.py --base <base> report <slug> \
    --stage "初始询盘" --priority action \
    --situation "<一两句现状>" --next "<谁要做什么>"
```

优先级：`urgent`（客户在催 / 临期）> `action`（业务员要动）> `waiting`（等客户）> `unconfirmed`（待核实）。

内容写进 `客户进展.md` 的 Agent 区域（`<!-- TCM:ALI:AGENT:BEGIN/END -->` 之间）。**更新时该区域之外的内容（包括用户手写部分）保持不变。** 之后再 `render` 更新总览。

## Phase 5 — 收尾

```bash
python scripts/tcm_ali.py --base <base> render   # 生成 / 更新 客户概览.md
python scripts/tcm_ali.py --base <base> brief    # 在聊天里展示简报
python scripts/tcm_ali.py --base <base> verify   # 结构与遗漏守卫，exit 0 才算完成
python scripts/tcm_ali.py --base <base> audit    # 隐私扫描，发布 / 打包前必跑
```

`verify` 检查项：已建档但缺进展文件、档案缺章节、`_待确认` 有未交代线索、消息缺来源 id、文件内出现绝对路径泄漏。

## 增量与定时同步

所有命令幂等，重复运行不产生重复内容。定时场景只需在宿主里加一条 cron/launchd 调用 `extract → ingest → render`，**不修改本 Skill 逻辑**。宿主不支持调度就手动跑同一串命令。

## 完成标准

- [ ] `doctor` 通过（写权限 OK、桥接可用）
- [ ] `extract` 完成且 `finished:true`（或用户明确停止）
- [ ] `ingest` 后无客户被遗漏（有歧义的都落到 `_待确认/` 并记录）
- [ ] 每个有实质内容的客户都有 `客户进展.md`
- [ ] `verify` exit 0
- [ ] 打包 / 发布场景 `audit` 无隐私告警

## 已知未验证事项

- 本 Skill 仅在 **Hermes + bsk 浏览器桥接**环境端到端验证过（见 `tests/`）。**其他 Agent 或其他浏览器桥接均未验证**，使用前先跑 `doctor` 与 `tests/test_offline.py`。
- 阿里页面结构变化会导致选择器失效，症状是列表返回 0 条。`doctor` 会给出提示；**不要改代码硬猜**，先让用户确认登录态是否过期或页面是否改版。
