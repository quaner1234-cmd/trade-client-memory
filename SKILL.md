---
name: trade-client-memory
description: 外贸客户记忆助手——授权邮箱后,把客户往来邮件(含附件)整理为"一个客户一个文件夹"的本地客户档案:客户沟通记录.md + 附件/ + 客户概览.md + 聊天简报。适用于"整理客户邮件/连接邮箱/更新客户档案/看客户进展";也适用于上次整理中断后的继续。不需要 Notion;密码只经用户在本地终端输入。
---

# 外贸客户记忆助手(Skill)v3

用户是外贸业务员,不懂编程。目标:**授权邮箱 → 等待 → 直接看到每个客户现在怎么样、接下来做什么;要历史和附件时打开对应客户文件夹。** 原则:Agent 能做的自己做;用户只做必要授权(输授权码)和结果确认。

所有脚本在 `scripts/`(相对本文件)。用户数据在**用户选择的基础目录** `<base>/` 下:`客户档案/`(可见成果)与 `内部数据/`(程序状态)。**绝不写进本 skill 安装目录。**

## Where are we(每次执行先做)

**进度只认 `内部数据/state.json` 与 `fetch-status.json`,不从对话记忆推断。**

```bash
python scripts/tcm.py --base <base> status
```

- 报 "state.json not found" → 从 Phase 0 开始。
- `fetch-status.json` 存在且 `finished:false` → 上次拉取中断:按 Phase 1 的恢复话术处理(重新双击即可,已下载的不会重复)。
- `unprocessed` 非空 → 上次整理中断:跳到 Phase 4 继续。

## 安全红线(每阶段适用)

1. **只读邮箱**:不发送/删除/移动/标记邮件。本 skill 脚本无此能力;其他工具有写能力也不得用。
2. **范围最小**:拉取前必须和用户确认范围(日期/客户/数量上限)。默认 `--since` 近期 + `--limit`;**未经确认不扫全箱**。partial 覆盖要如实写进记录,禁止声称"整理了全部历史"。
3. **密码不经过聊天**:只在用户自己的终端/双击窗口输入(getpass 不回显)。不落盘、不进 state、不进 Git。
4. **数据隔离**:真实客户数据只出现在 `<base>/` 下;不进 skill 目录、不进 Git、不进发布包;未经确认不上传任何第三方(包括 Notion)。
5. **不编造**:无法确认的事实标「待确认」;没取到的附件写"附件未取得";不凭图片/文件名认定客户品牌、公司、国家。
6. **隐私讲清一次**:档案在本地,但"理解邮件"会经过 Agent 所用模型的云端;公司禁数据出网 → 本地模型或脱敏导入。公司邮箱需符合公司授权。各提醒一次即可。
7. **不绕过沙箱**:宿主无法自动开终端时,用双击启动文件 + 一次用户确认,不要尝试自动注入或绕过安全机制。

## Phase 0 — 安装与环境检查(全自动)

```bash
python scripts/tcm.py --base <base> init
python scripts/tcm.py --base <base> doctor
```

`<base>` 默认用用户当前工作目录(和用户确认一句即可)。`doctor` 结果:`data_writable=false` → 停下解决权限;`connector_available=true` → Phase 1 优先复用该连接器;否则自动准备通用 IMAP。

**首次对话先问一句**:「先看演示样例,还是直接连接你的真实邮箱?」演示路径 = ingest `samples/demo-mails/`(虚构数据);真实路径走 Phase 1。不要默认让用户跑演示。

## Phase 1 — 连接邮箱(自动准备,用户只输授权码)

按顺序:

1. **doctor 发现了只读连接器**(如本机 mail-read CLI/MCP)→ 复用读取,按 `references/email-access.md` 的「连接器 → .eml」配方把完整头部+正文写成 .eml 存入 `内部数据/inbox/`,然后 Phase 2。
2. **无连接器 → 通用 IMAP(默认路径)**:
   - 按 `references/email-access.md` 服务商表确定 host;不确定就问一次用户,只试登录一次。
   - **与用户确认范围**:日期(`--since`)与数量上限(`--limit`)。
   - 生成双击启动文件并告诉用户怎么用:

     ```bash
     python scripts/tcm.py --base <base> launcher --host <host> --user <邮箱> --since 2026-09-01 --limit 120
     ```

     生成 `连接邮箱.command`(macOS)与 `连接邮箱.bat`(Windows)。话术:「双击它 → 输入授权码(屏幕不显示字符,输完按 Enter)→ 等它显示『邮件读取完成』→ 回来说一声。」(macOS 沙箱可能阻止 Agent 自动开终端,双击文件就是为此设计的正路,不要绕过。)
   - 启动器内部运行 `imap_fetch.py`:收件+已发送**一起拉**(自动探测"已发送"文件夹),逐封打印进度/已用时/预计剩余,写 `内部数据/fetch-status.json`。
   - **衔接**:宿主支持持续等待 → 你定期读 `fetch-status.json`,`finished:true` 即进入 Phase 2;宿主不支持 → 用上面的一次性确认话术。两种都合法,不要第三种(不绕沙箱)。
3. **用户坚持手工**:导出 .eml 放入 `内部数据/inbox/`(备用,能连邮箱时不主动推荐)。

失败恢复(话术已内置在脚本输出里):登录失败 → 生成/重置授权码后重新双击;连接超时 → 查网络重试;已下载邮件不丢、重跑不重复。

## Phase 2 — 导入与去重(全自动)

```bash
python scripts/tcm.py --base <base> ingest --owner <用户邮箱>
```

- `--owner` 会持久化,之后不必重复。
- 自动完成:双指纹去重(Message-ID 增/剥双向免疫)、**附件载荷提取**(内容哈希、内嵌图片 cid 映射)、收发方向标记、覆盖范围并入 state(来自 fetch-status)。
- `unrecognized` 有内容 → 列文件名,建议用户改用"另存为 .eml",不要硬解析。

## Phase 3 — 识别客户(你的核心判断)

读 `内部数据/messages/msg-<id>.json`,按 `references/trade-judgement.md` 判定(尤其:价格是报价还是指示价、预计日期≠确定交期、已发=已行动、品牌/公司/国家不凭图片认定),然后:

```bash
python scripts/tcm.py --base <base> map-customer <slug> <email>[,<email2>] \
    --name "姓名" --company "公司" --country "国家" [--unconfirmed]
```

- slug 永远不变(内部身份);公司/国家变化直接更新映射,render 会**重命名文件夹而不丢内容**。
- 免费邮箱+无佐证 → `--unconfirmed`,不得并入已有客户。

## Phase 4 — 生成客户档案(先 render,再由你填头部)

```bash
python scripts/tcm.py --base <base> render
```

render 机械生成(幂等,重复运行无重复段落/附件):

- `客户档案/<公司 - 国家>/客户沟通记录.md`:顶部覆盖范围声明 + 你专用的 Agent 区域 + 按时间排序的完整沟通时间线(每封含日期/发收方/主题/来源 id/清理后正文/附件链接;历史引用折叠、签名折叠、**逐项回复保留**、签名 Logo 自动识别不预览、附件按内容哈希去重、未取得如实标注)。
- `客户档案/<公司 - 国家>/附件/`(无附件也保留空目录)。
- `客户档案/客户概览.md`(来自 reports,见 Phase 5)。

然后你**只编辑 Agent 区域**(标记 `<!-- TCM:AGENT:BEGIN/END -->` 之间,更新不覆盖,用户手改也保留),填四节:`## 客户情况`、`## 关键已确认事实`、`## 当前阶段`、`## 下一行动`。已发邮件意味着我方已行动;待办完成即关闭或替换,不堆积。

## Phase 5 — 状态单一来源(聊天简报 = 客户概览)

对每个有归档邮件的客户写状态(**这是简报与概览的唯一数据源,禁止另写一份**):

```bash
python scripts/tcm.py --base <base> report <slug> --stage "PI 待发" \
    --situation "<一两句现状>" --next "<谁需要做什么>" \
    --priority urgent|action|waiting|unconfirmed
```

优先级定义:`urgent` 业务员紧急行动(投诉/临期)> `action` 业务员行动 > `waiting` 等待客户 > `unconfirmed` 信息待核实。再 `render` 一次,`客户概览.md` 即更新。

完成后在聊天窗口直接展示(不要长报告、不要测试数据):

```bash
python scripts/tcm.py --base <base> brief
```

输出格式(每客户最多两三行,按紧急度排序,首次展示全部,增量只展示有变化的客户,其余指向概览):

> **客户名 · 国家|阶段**
> 当前情况:……
> 下一步:谁需要做什么。

## Phase 6 — 收尾(不得跳过)

```bash
python scripts/tcm.py --base <base> done <id>...    # 已并入档案的邮件立即标记
python scripts/tcm.py --base <base> verify          # 结构+出处+防误报守卫
```

- done 纪律:只对已并入的客户邮件 done;无关邮件保持 unprocessed(verify 会以 warning 提醒你交代)。
- verify 失败项包括:done 未映射、已归档但无 report、概览被手改不一致、档案缺章节、附件链接失效、状态泄漏绝对路径。逐条修复后重跑,不带着失败收工。

## 完成标准

- [ ] fetch-status finished(或用户明确停止)且 unprocessed 已交代
- [ ] verify exit 0
- [ ] 每个客户:文件夹 + 沟通记录(四节齐全、每封已处理邮件被引用)+ 附件目录
- [ ] 每个有归档邮件的客户有 report;概览与简报同源
- [ ] 聊天里展示了按紧急度排序的客户状态简报

## 中断与恢复

`state.json`/`messages/`/`fetch-status.json` 全部落盘;任何一步断了,重新调用本 skill,`status` 即知进度;render/ingest 幂等,重复执行不产生重复。

## 预留扩展点(未做,不阻塞)

Notion 同步(概览/记录结构固定,可机械转换)、宿主定时增量(同一闭环)、Agent Email 主动汇报。
