# trade-client-memory · 外贸客户记忆助手

> **Status: Beta (Email channel only).** Flow and output validated on fictional data end-to-end (84 deterministic checks, incl. a simulated IMAP server) and shaped by a real-mailbox experiment; a live IMAP re-validation (yunyou/exmail/QQ/163/Gmail) and the WorkBuddy macOS re-run are still pending — see docs/E2E-RUNLOG.md for the exact unverified list. Don't claim provider/host support beyond that.
>
> **Not part of this release:** multichannel capture (Alibaba seller IM / WhatsApp Web) is research only. The research note lives in this git repository (`docs/MULTICHANNEL-RESEARCH.md`) but is deliberately excluded from the Email Beta ZIP: no multichannel code ships, and nothing there is claimed as supported.

A Skill Pack that turns an authorized mailbox into **one folder per customer**: full chronological communication record (cleaned, with source IDs), all attachments (hash-deduped, signature logos identified), and a one-glance status overview — designed for foreign-trade salespeople who use an AI agent but shouldn't need to understand Python, IMAP or MCP.

让外贸业务员**只需授权邮箱**,就能得到按客户整理好的完整沟通资料、附件、以及一眼能看懂的客户进展和下一行动。Agent 能做的自己做;用户只负责输入授权码和确认结果。

## The flow / 使用流程

```
"帮我整理客户邮件"                  (说给任意 Agent 听)
      │
      ▼
Agent:init + doctor + 问一句「演示还是连真实邮箱」
      │
      ▼
双击 连接邮箱.command / .bat          ← Agent 自动生成;输授权码(不回显)
      │  收件+已发送一起拉,进度/ETA/中文错误提示,fetch-status.json 落盘
      ▼
回 Agent:「邮件读取完成」             (或宿主支持时 Agent 轮询状态自动继续)
      │
      ▼
Agent:ingest(去重/附件提取/覆盖记录)→ 识别客户 → render → 填写状态
      │
      ▼
聊天窗口:每个客户两三行现状+下一步(按紧急度排序)
本地磁盘:客户档案/<公司 - 国家>/客户沟通记录.md + 附件/  +  客户概览.md
```

演示与真实邮箱同一条流水线:`samples/demo-mails/` + `samples/example-output/客户档案/` 是完整虚构示例(含签名 Logo 识别、附件去重、客户概览),可先看效果。

## What the user sees / 用户可见输出

```
<base>/
├── 客户档案/
│   ├── 客户概览.md                    ← 一页纸:全部客户现状+下一步,按紧急度
│   └── Casa Rossi Mobili - 意大利/
│       ├── 客户沟通记录.md            ← 顶部:情况/关键事实/阶段/下一行动(Agent 区域,可手改)
│       │                                下方:全部往来按时间排序,每封带来源 [msg-<id>]
│       └── 附件/                      ← 原格式保存;签名 Logo 已识别;按内容去重
├── 内部数据/                           ← 程序状态(.eml/JSON/索引),与可见成果隔离,可整体删除
└── 连接邮箱.command / .bat             ← 下次拉新邮件:双击 → 输授权码 → 回「邮件读取完成」
```

Program state, raw .eml and indexes never mix into the customer folders — and real user data never enters the skill directory, the git repo or the release ZIP.

## Engineering / 工程要点

- **Deterministic core** (`scripts/tcm.py`, stdlib-only): dual-fingerprint dedupe immune to Message-ID being added/stripped between exports; attachment extraction with content-hash dedupe and signature-logo detection (same inline image in ≥3 mails); quote-chain cleaning that collapses historical copies but preserves point-by-point quoted answers; empty text/plain falls back to HTML (58/120 real sent mails had this); partial-coverage reporting (never claims full history); folder rename without data loss on customer name/country change.
- **Terminal fetch** (`scripts/imap_fetch.py`): read-only (EXAMINE + BODY.PEEK), inbox+sent together with sent-folder auto-detection (modified-UTF-7 decoded), per-mail progress with ETA, friendly Chinese error recovery, `fetch-status.json` for agent handoff. Password only via getpass in the user's own window.
- **Single source of status**: chat brief and 客户概览.md both render from `state.reports` — verify fails if they diverge.
- **Idempotent everything**: re-fetch, re-ingest, re-render never duplicate segments, attachments or todos.
- **Agent-owned sections** survive re-renders (`<!-- TCM:AGENT:BEGIN/END -->`), so user/agent edits are never clobbered.

## Layout / 目录

```
START-HERE.md              面向业务员(中文)
SKILL.md                   Agent 工作流:判断演示/真实 → launcher → 轮询 → ingest → render → report → brief → verify
references/                email-access(服务商表+launcher) / trade-judgement(严格事实规则) / platform-notes
templates/state.example.json   内部数据状态契约
scripts/tcm.py             确定性核心:init/ingest/map-customer/report/render/brief/done/status/verify/doctor/launcher
scripts/imap_fetch.py      只读拉取(收+发、进度/ETA/状态文件)
tests/run_tests.py         60 项确定性测试(含真实实验问题的脱敏回归)
tests/demo_corpus.py       复现实验邮件形态的虚构语料生成器
samples/demo-mails/        基础虚构邮件
samples/example-output/客户档案/   完整虚构示例输出(客户文件夹+附件+概览)
docs/E2E-RUNLOG.md         真实运行记录;docs/CHAT-REPORT-EXAMPLE.md 聊天汇报实例
```

## Tested / 已验证

- **Platform**: Windows 11 + Python 3.12 + ZCode (GLM) — full E2E simulation from fresh install to chat brief, see `docs/E2E-RUNLOG.md`.
- **Deterministic suite**: 84/84 checks — including a fake IMAP server covering the Chinese sent folder (raw modified-UTF-7 SELECT), partial SEARCH misses recovered by bounded enumeration, legitimate zero results (no wasteful walk), mid-fetch connection drops (partial, never full success), and enum-window truncation recorded as incomplete coverage; including desensitized regressions for every finding of the real-mailbox experiment (empty plain + HTML, reply chains, inline images vs real attachments, logo filtering, dedupe, mixed inbox/sent, incremental folder updates, coverage honesty, brief/overview consistency, data isolation).
- **Not yet verified on real mailboxes/hosts**: the macOS/WorkBuddy experiment validated the flow shape; this repo's fetch path still awaits one live IMAP run per provider, and other agent hosts are unexercised — see platform-notes for the honest list.

## Security / 安全边界

Read-only toward mail; passwords only via hidden input in the user's own terminal, never persisted; no full-mailbox scans without consent; partial coverage always disclosed; customer data confined to the user-chosen base dir. **Local storage ≠ nothing leaves the machine**: comprehension sends mail text to the agent's cloud model unless it runs a local model — stated plainly in START-HERE. Company authorization and data policies apply and are mentioned once per run.

## Roadmap

1. Live-IMAP validation pass per major provider (exmail/QQ/163/Gmail) + launcher polish from real feedback.
2. Notion sync as optional module (structures already fixed).
3. Scheduled incremental runs via host cron (same loop).

## 阿里巴巴国际站询盘 Skill（2026-10-14 新增）

同一仓库现在**另有**一套独立 Skill：**`skills/alibaba/`** —— 把已登录的阿里卖家后台（message.alibaba.com）里的询盘与聊天，只读整理成同构的本地客户档案（一个客户一个文件夹 + 客户概览）。

- 输出结构与本 Email Skill 一致，可并存；**不读取邮箱，不修改 Email Skill 的任何文件**。
- 自带 `SKILL.md` / `README.md` / `references/` / `scripts/` / `samples/` / `tests/`（67 项离线测试）。
- 打包发布：`python skills/alibaba/scripts/build.py`（输出独立包，自带隐私扫描）。
- 通过 `.gitattributes export-ignore` 排除在本 Email Beta ZIP 之外，两套包互不混淆。
- **仅在 Hermes + bsk 浏览器桥接环境端到端验证**；其他 Agent / 桥接未验证，见其 README 的「已知限制」。

## License

MIT — see [LICENSE](LICENSE). All sample data is fictional; any resemblance to real companies is coincidental.
