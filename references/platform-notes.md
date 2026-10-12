# 平台适配参考(references/platform-notes.md)

本 Skill Pack 是平台无关的:一个 `SKILL.md` 工作流 + 两个标准库 Python 脚本,任何能**读写文件并运行终端命令**的 Agent 都能执行。不要求、也不假设所有平台有相同的邮件连接器。

## 已测试平台

| 平台 | 状态 | 说明 |
| --- | --- | --- |
| ZCode(Windows) | ✅ 全流程实测 | 本包的开发与验证平台;场景 1–6 均在此完成 |
| 其他 Agent(Codex / WorkBuddy / Hermes / Cline…) | ⚠️ 未实测 | 机制上仅依赖:文件读写 + 终端跑 python + 按本文档引导用户。**未测试的平台不要声称已支持**,跑通一次后再记录 |

## 通用依赖

- Python ≥ 3.10,仅标准库(email/imaplib/hashlib/json),**无 pip 安装**。
- Windows 注意:直接调 python 时先 `set PYTHONIOENCODING=utf-8`(cmd)或 `$env:PYTHONIOENCODING='utf-8'`(pwsh),避免中文正文编码崩溃。`tcm.py` 与 `imap_fetch.py` 内部已各自 `reconfigure(encoding="utf-8")`,一般无需干预;但从老 shell 调用出现 UnicodeEncodeError 时先设此变量。
- 路径:所有命令以本包根目录为工作目录,脚本用 `scripts/` 相对路径。

## 邮箱连接器差异(按平台)

| 平台 | 现有邮件连接器 | 建议路径 |
| --- | --- | --- |
| 无任何连接器(最常见) | — | 路径 A(.eml)/ B(粘贴);持续读取才用 imap_fetch.py |
| 本机已有只读邮件 CLI/MCP(如 `mail-read` 在 PATH,或 `TCM_MAIL_READ` 指定) | 只读多账号 IMAP | **优先复用**;imap_fetch.py 作后备。见 email-access.md |
| 已配置其他只读邮件 MCP | 各自不同 | 允许复用,但必须确认其权限是**只读**(无 send/delete/mark 能力)后才可用;写能力未屏蔽的一律不用 |

评估过未引入的适配器:**QwenPaw Mail**(github.com/agentscope-ai/QwenPaw)——能力全(收/搜/发/转发/整理),但含发送与整理(移动/删除)能力,首版只读约束下需要逐工具权限压制,未经真实邮箱验证,故不作为依赖;待后续版本按需评估。

## Agent 差异速记

- **能交互终端**(用户可见的 shell):路径 C 可让用户直接跑 `imap_fetch.py`。
- **只有内部终端**(用户看不到输入回显):路径 C 改为——Agent 生成一条命令,让用户复制到自己开的终端运行;Agent 不得索要密码。
- **有 MCP/插件体系**(如 Codex config.toml、DSH cordis.patch.yml):不需要为本包新增 MCP 注册;它不是 MCP,是一个工作目录里的 skill。让 Agent 读 `SKILL.md` 即可。

## 给分发者的提示

- 公开 ZIP 不包含 `data/`(运行时生成)、不包含任何真实客户数据或服务器配置。
- `samples/demo-mails/` 全部为虚构人物/公司/域名(sunrisetrade.example、casarossi.it 等),用于开箱演示,可放心分发。
- 更新版本时保持 `templates/*.md` 字段标签逐字不变(校验依赖),新增字段只能追加。

## 真实邮箱实验补充(WorkBuddy × macOS × 刺猬云邮,2026-10)

- macOS 沙箱阻止 Agent 自动打开交互式终端 → **必须用双击启动文件**(`连接邮箱.command`),用户输入授权码;不要尝试自动注入或绕过。Windows 同理提供 `.bat`。
- 刺猬云邮实测收件+已发送 120 封中,**58 封已发送邮件 text/plain 为空、仅 HTML 有正文**——本包 `pick_body` 已按此修复(空 plain 回退 HTML),回归测试 T15。
- 交付结构按实验结论改为「一个客户一个文件夹」(客户沟通记录.md + 附件/ + 客户概览.md);独立摘要文件、JSON、裸 .eml 不是业务员要的输出,已全部移入 内部数据/。
- 实验中的交付结构生成器与邮箱遍历脚本只在实验机(macOS)上,本仓库**未取得其代码**,以上实现按实验描述与验收标准重写;已用脱敏回归测试覆盖其发现的问题形态。
