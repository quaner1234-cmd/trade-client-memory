# 邮件接入参考(references/email-access.md)

给 Agent 看的接入决策与操作细节。原则:**前一种路径可行就不动用后一种;涉及密码的步骤永远由用户在终端亲自完成。**

## 路径对比

| 路径 | 触发条件 | 密码 | 局限 |
| --- | --- | --- | --- |
| A. .eml 导入 | 用户能从邮箱客户端导出文件 | 无需 | 手动导出,适合起步和历史邮件 |
| B. 粘贴文本 | 用户只有几封邮件的正文 | 无需 | 头部信息可能缺失,身份判断弱 |
| C. 只读 IMAP | 用户要持续读取、邮件量大 | 需要(用户终端输入) | 需要授权码;企业邮箱可能有安全策略 |

## 路径 A/B:导出 .eml(向用户转述的操作指引)

- **Outlook(经典桌面版)**:选中邮件 → 拖到文件夹,或 文件 → 另存为 → 类型选 `.eml`。
- **新版 Outlook / 网页 Outlook**:打开邮件 → 更多操作 → 下载(保存为 .eml)。
- **Foxmail**:右键邮件 → 导出邮件(选 .eml 格式)。
- **QQ 企业邮 / 网易企业邮网页版**:打开单封邮件 → 更多 → 导出/下载原始邮件。
- 批量历史邮件:按客户搜索后逐封导出太慢时,才考虑路径 C。

注意:转发给用户的邮件、拖出来的附件本身不是 .eml;要的是**原始邮件文件**。

## 路径 C:只读 IMAP

使用 `scripts/imap_fetch.py`(纯 Python 标准库,无依赖)。只读保证:EXAMINE 只读会话 + `BODY.PEEK[]` 取信,不修改任何标志,代码中没有删除/移动/发送调用。

### 服务商配置发现表(先查表,不要盲猜)

| 邮箱域名 | IMAP 服务器 | 端口 | 密码说明 |
| --- | --- | --- | --- |
| `*@qq.com` / `*foxmail.com`(个人) | imap.qq.com | 993 | 需在设置里开 IMAP 并生成**授权码** |
| 腾讯企业邮(自定义域名) | imap.exmail.qq.com | 993 | 成员账号生成**客户端专用密码** |
| `*@163.com` / `*@126.com`(个人) | imap.163.com | 993 | 需开 IMAP + **授权码** |
| 网易企业邮(自定义域名) | imap.qiye.163.com | 993 | 管理员确认 IMAP 开启 |
| 阿里企业邮(自定义域名) | imap.mxhichina.com | 993 | 同上 |
| `*@gmail.com` | imap.gmail.com | 993 | 需 **App Password**(两步验证) |
| Outlook / Microsoft 365 | outlook.office365.com | 993 | 基础认证常被关闭,需 App Password/现代认证;失败属预期,引导走路径 A |

**域名不在表中**(公司自建/小众服务商):向用户要一次服务器地址(通常在邮箱网页设置 → 客户端/IMAP 设置里),**只尝试登录一次**,失败就回到路径 A,不要反复试密码——连续失败可能触发企业邮箱风控锁号。

### 标准路径:双击启动器(v3 默认)

不要让用户记命令。Agent 用一条命令生成双击文件,用户双击、输授权码、等"邮件读取完成":

```bash
python scripts/tcm.py --base <base> launcher --host imap.exmail.qq.com \
    --user 用户邮箱 --since 2026-09-01 --limit 120
```

生成 `连接邮箱.command`(macOS)与 `连接邮箱.bat`(Windows),内部调用 `imap_fetch.py`,参数固定写死,输出到 `<base>/内部数据/inbox/`,进度写 `<base>/内部数据/fetch-status.json`。

- 授权码在用户自己的窗口输入(不回显),**永不经过聊天**;建议用授权码/应用专用密码,不用登录密码。
- **收件与已发送一起拉**:`--folders auto`(默认)会 LIST 文件夹并自动挑第一个"已发送"(已发送/Sent Items/Sent Messages 等,modified-UTF-7 自动解码);也可 `--folders "INBOX,已发送"` 指定。只判断客户状态必须同时看两边。
- 进度体验:逐封打印 `[n/total] 文件夹 · 已用时 · 预计剩余`(≥10 封后给出可靠 ETA,之前明说"统计中");结尾打印「邮件读取完成」。
- 失败即人话:登录失败(授权码错/未开 IMAP)→ 生成新授权码后重新双击;连不上 → 查网络重试;**已下载邮件不丢、重跑不重复**。
- Agent 衔接:轮询 `fetch-status.json`(`finished:true` 即完成);宿主不支持等待 → 请用户完成后回一句「邮件读取完成」。**不要绕过沙箱去自动开终端。**
- 状态文件同时携带覆盖统计(各文件夹 total/fetched),ingest/status/render 会读取并写进客户档案的覆盖声明——partial 拉取必须如实展示。

```bash
python scripts/tcm.py --base <base> ingest --owner 用户邮箱   # 读取完成后运行
```

- SEARCH 不可信防护:`--strategy auto` 对已知不可靠服务商(刺猬云邮 yunyou.top)直接走**有窗口上限的逐封枚举**;SEARCH 返回 0 封但文件夹非空(漏结果的明确信号)也会自动回退枚举。`--enum-window`(默认 1000)限制扫描范围,超出部分记为「覆盖不完整」并写明未覆盖数量;合法空文件夹不会触发枚举。
- 文件名带账号与原始文件夹哈希(`imap-<user>-<hash>-<folder>-<hash>-<uid>.eml`),多账号/同名文件夹互不覆盖,同源重拉幂等。

### 已有只读连接器时的优先级(doctor 发现的连接器)

若 `tcm.py doctor` 报告 `connector_available=true`(PATH 上有 `mail-read`,或设置了 `TCM_MAIL_READ` 环境变量指向某个只读邮件 CLI),**优先复用该连接器读取**,流程:

1. `mail-read accounts` / `folders` 确认账号与文件夹;`latest` / `from "客户@x.com" --limit 20` 定位目标邮件。
2. `mail-read show <uid> --account <acc>` 读全文后,**由 Agent 把每封邮件重写为标准 .eml 存入 `内部数据/inbox/`**:头部保留 From / To / Cc / Date / Subject / Message-ID 原值,正文原样。然后正常 `tcm.py ingest`——去重指纹与档案出处和直接导入完全一致,不产生第二套事实存储。
3. 需要"已发送"时用 `--folder sent`(该工具自动解码中文文件夹名)。

注意:外部连接器的 SEARCH 是否可靠取决于其实现与你的服务商;刺猬云邮等已知不可靠服务端请走 imap_fetch.py 的枚举策略(见上)。若连接器无法产出含 Message-ID 的完整头部,宁可改用 imap_fetch.py,不要构造没有指纹依据的 .eml。

## 密码安全规则(路径 C)

1. 只在用户的交互终端输入(getpass 隐藏回显);Agent 不经手。
2. 不写入任何文件、state.json、环境变量、Git。
3. 汇报和日志里只出现邮箱地址,不出现任何凭据。
4. 用户问"要不要把授权码发你" → 明确拒绝,解释为什么终端输入更安全。
