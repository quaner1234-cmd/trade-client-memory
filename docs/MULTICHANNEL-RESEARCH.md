# 多渠道客户记忆扩展 · 可行性研究（阿里巴巴国际站卖家端 + WhatsApp）

状态：最小实验已完成（2026-10-11/12，Asia/Shanghai）。**未修改任何现有邮件模块**；未发送任何消息；未对任何平台做写操作。
本文只记录实际验证过的能力；未实测的能力一律标注 UNKNOWN，不声称支持。
证据分层：FACT（实测/官方原文引证） / UNKNOWN（未验证）。

**脱敏说明**：本公开版本不含客户姓名、联系方式、平台会话/消息 ID 或消息正文；实验会话以代号 A（阿里会话 1）/ B（阿里会话 2）/ C（WhatsApp 会话）表示。真实标识与原文仅存于用户本地私有目录，不进本仓库。

## 0. 结论摘要

| 路径 | 渠道 | 结论 | 依据 |
| --- | --- | --- | --- |
| B. 已授权浏览器只读提取 | 阿里国际站卖家端 IM | **PASS（2 个会话，39+5 条消息）** | 本机 bsk 0.3.2 + 已登录 Chrome 实测，见 §1.2 |
| B. 已授权浏览器只读提取 | WhatsApp Web | **PASS（1 个会话，6 条消息 + 2 张图已落盘）** | 同上，见 §1.1 |
| A. 官方接口 | 阿里卖家端 IM/询盘 | **不可用（普通卖家）**：open.alibaba.com 82 个 API 无消息类；IM 读取 API 属 TOP「OKKI&TM」类目（服务商），2021 年起聊天记录类不再对新申请者开放 | 见 §2.2 |
| C. 官方导出 | WhatsApp | 可用（零封号风险）；移动端支持，Web/Windows 桌面支持情况官方各语言版本说明不一致 | 见 §2.3 |
| 第三方库（whatsapp-web.js / WPPConnect） | WhatsApp | **不建议**：官方明示非官方客户端违反 ToS、可致封禁；社区封号案例多；2026 起 passkey 变更加剧不可用 | 见 §2.3 |
| Playwright MCP / browser-use / Skyvern | 通用备选 | 均支持已登录会话复用（机制各异）；当前 bsk 路径已满足需求，无需引入 | 见 §2.1 |
| 桌面客户端本地缓存 | 阿里卖家端 | UNKNOWN（未探索，不推荐作为路径） | §1.2 附注 |

## 1. 已实测（本机证据）

### 1.1 WhatsApp Web 只读提取 —— PASS（范围：1 个会话）

环境：bsk 0.3.2（browser-skill）+ Chrome（扩展已连接，instance `<browser-instance-id>`）+ 用户已登录的 web.whatsapp.com。

实测方法（只读，未发送、未修改任何内容）：
1. Agent 标签打开 `https://web.whatsapp.com/`；
2. 从 `#pane-side` 读取会话列表（本次 21 项，**未读取任何其他会话内容**）；
3. 仅点击用户授权的 1 个会话（代号 C），从 `#main` 读取消息；
4. 两张客户参考图从页面内 blob URL 通过 `fetch` → base64 → 落盘（纯本地读取，未触发任何消息操作）。

提取结果（规范化输出，6 条消息）：

| 字段 | 来源 | 实测形态 |
| --- | --- | --- |
| 平台消息 ID | 消息节点 `data-id` | 16 位十六进制平台 ID，会话内稳定（真实值仅存本地） |
| 时间 + 发件人 | `[data-pre-plain-text]` | `[HH:MM, YYYY年M月D日] <发件人>: ` → ISO 带时区 + 原文保留 |
| 正文 | 消息节点文本 | 完整原文（含多段） |
| 附件 | 页面内 blob → 落盘 | 2 张 JPEG（1024×1536、1312×1199），sha256 已记录 |

限制与副作用（如实记录）：
- 只覆盖**当前已加载**的最近消息；更早历史未翻页；
- 打开会话会把我方视角**标记为已读**——浏览器读取的固有副作用；
- 会话列表含全部联系人，本轮严格只读了 1 个会话。

### 1.2 阿里国际站卖家端 —— PASS（2 个会话）

环境：同一已登录 Chrome 会话，`seller.alibaba.com` 卖家账号已登录。

实测方法（只读，未发送、未修改任何内容）：
1. `message.alibaba.com/message/default.htm#feedback/all` 读取询盘/会话列表（20+ 项，仅列表）；
2. 只点开用户授权的 2 个会话（代号 A、B；真实姓名与询盘 ID 仅存本地）；
3. 从 `.message-item-wrapper` 节点提取每条消息；会话 A 用「Show more messages」加载至按钮消失（39 条，最早 2026-09-09）。

提取结果（会话 A 39 条 / 会话 B 5 条）：

| 字段 | 来源 | 实测形态 |
| --- | --- | --- |
| 平台消息 ID | 节点 `data-expinfo.messageId` | 13 位数字平台 ID（真实值仅存本地） |
| 消息类型 | `data-expinfo.messageType` | 1=文本，60=图片，9999=系统，9545=自动跟进系统，50=名片 |
| 时间戳 | `data-expinfo.sendTime`（epoch ms） | epoch 毫秒 → 可转换为带时区 ISO 时间 |
| 正文原文 | `data-original` | 完整原文（含 `<br/>` 与 `<img>` 的图片消息） |
| 方向 | 节点 class（item-left=买家 / item-right=卖家） | received/sent 正确区分 |
| 图片附件 | `data-original` 内 `<img src="clouddisk.alibaba.com/...">` | 记录来源链接；**载荷未下载** |

覆盖声明：会话 A 视图内加载至最早 2026-09-09（更早历史未在本视图提供）；会话 B 共 5 条未翻页；两者媒体消息仅记录来源链接。

其他观察（FACT）：
- 询盘列表页有官方「Export Inquiry」按钮，本次点击返回 **"You are not authorized to access this action!"**（当前账号无此权限，非本站功能缺失的结论）；
- 本机装有 AlibabaSupplier（阿里卖家 CEF 客户端）与 AliWorkbench；其本地缓存是否含可读会话数据 **UNKNOWN（未探索）**，不作为路径。

### 1.3 对既有资产的对齐

- 现有客户文件夹/去重/覆盖声明机制（`scripts/tcm.py`、`imap_fetch.py`）在本次研究中**未做任何修改**。
- 统一消息结构（§3）与现有机制同构：来源 ID 主指纹 + 内容备用指纹、附件内容哈希、覆盖如实声明、方向标记。落地方案见 §3.4。

## 2. 委托核查结论（已完成）

### 2.1 浏览器自动化栈对比（Playwright MCP / browser-use / Skyvern）

结论先行：三者都能满足「复用已登录会话只读提取」这一用例；当前 bsk 路径已实测可用，**无需为此引入新工具**。若未来需要无人值守/云端调度，再按下列事实评估。

**① Microsoft Playwright MCP**（v0.0.83，2026-09-28 发布）
- 会话复用（FACT）：`--extension` 模式配合 Chrome 商店的 Playwright Extension，**直连现有标签页并复用默认用户 profile 的 cookies/登录态**；另支持 `--user-data-dir` 持久 profile、`--cdp-endpoint` 附加已运行 Chrome、`--profile-dir-name` 指定 profile。（github.com/microsoft/playwright-mcp；github.com/microsoft/playwright/tree/main/packages/extension）
- 读取能力（FACT）：无障碍树快照 `browser_snapshot`（非像素，官方称"无需视觉模型"）、`browser_find` 文本/正则检索、`browser_evaluate` 执行 JS、`browser_cookie_list`/`localstorage_list`；首次连接可在页面中选择接管哪个标签页。
- 本地要求（FACT）：Node.js ≥18，`npx @playwright/mcp@latest`；免费开源、无需 API key。
- 限制（FACT，官方 README）：官方声明它**"不是安全边界"**，`--allowed-origins` 也不构成安全边界；默认每次连接需弹窗批准（可用 `PLAYWRIGHT_MCP_EXTENSION_TOKEN` 免批准）；持久 profile 同时只能被一个浏览器实例使用。
- 对本用例结论：可行；机制与 bsk 类似（扩展直连），但需另装扩展+配置 MCP。UNKNOWN：官方文档未提及 CAPTCHA/反检测立场。

**② browser-use**（开源库 0.13.11，2026-10-07；Cloud 按量）
- 会话复用（FACT）：`Browser.from_system_chrome(profile_directory=...)` **复用本机 Chrome profile**（含登录态/cookies）；`Browser(cdp_url="http://localhost:9222")` 附加已运行实例；或导出/导入 `storage_state`。（docs.browser-use.com/open-source/customize/browser/real-browser、/remote、/authentication）
- 读取能力（FACT）：LLM 代理自主执行；Pydantic `output_model_schema` 结构化输出、`history.extracted_content()`；默认高亮交互元素供视觉、可截图。
- 本地要求（FACT）：Python ≥3.11，`uv pip install browser-use`，MIT 开源；**必须自备 LLM API key**；Cloud 可选。
- 限制（官方文档）：real-browser 文档警告**可能需先完全关闭 Chrome**（调试模式与现有进程冲突）；Google 搜索会拦截自动化浏览器（建议改用 DuckDuckGo）；profile sync 仅传 cookies（不含 localStorage/IndexedDB/扩展），部分站点需重新登录；Cloud 托管浏览器含 CAPTCHA 处理但部分站点仍可能拦截。
- 价格（FACT）：Cloud $0.02/浏览器小时、住宅代理 $5/GB、Agent 按模型费 +20%。（browser-use.com/pricing）
- 对本用例结论：可行但属"代理自主操作"路线；本机 profile 需关 Chrome、要写 prompt/schema，纯只读提取偏重。UNKNOWN：本机 profile 模式"是否必须关闭 Chrome"官方仅称"可能"，未逐平台验证。

**③ Skyvern**（开源 AGPL-3.0 v1.0.55，2026-10-01；Cloud $29/月起）
- 会话复用（FACT）："Control your own browser"——Chrome 打开 `chrome://inspect/#remote-debugging` 启用后设 `BROWSER_TYPE=cdp-connect` + `BROWSER_REMOTE_DEBUGGING_URL=http://127.0.0.1:9222` **连接已运行的 Chrome**（保留登录态）；自托管设 `CHROME_EXECUTABLE_PATH` 会复制现有 Chrome profile；Cloud 可用 `skyvern browser serve --tunnel` 连接本机浏览器。（README；www.skyvern.com/docs/developers/self-hosted/browser）
- 登录/2FA（FACT）：支持存储凭据（Skyvern/Bitwarden/1Password）、TOTP/邮箱/SMS 2FA——面向「让 Skyvern 替你做登录」的场景（**TOTP 仅 Pro 及以上**）。
- 读取能力（FACT）：视觉 LLM 驱动；`page.extract()`、`data_extraction_schema` 按 JSON schema 结构化提取。
- 本地要求（FACT）：Python 3.11–3.13 `pip install skyvern`（**Windows 另需 Rust + VS C++ 工具链**）或 Docker Compose；须自配 LLM key。Cloud：Free 5,000 credits / Hobby $29/月 / Pro $149/月。
- 限制（FACT）：自动 CAPTCHA 求解仅 Cloud（自托管检测到后暂停 30 秒等人工处理）；tunnel 暴露浏览器**必须配 `--api-key`**，否则 URL 持有者可完全控制浏览器。
- 对本用例结论：能力最全但最重；本用例「已登录、只读」不需要它的登录/2FA/反爬能力。UNKNOWN：自托管"复制现有 profile"在 Windows 的实际行为未实测。

### 2.2 阿里官方 IM 接口的申请条件 —— 普通卖家不可用

**结论：普通国际站卖家无法自助调用官方 IM 接口读取自己的会话列表/历史消息。**
- FACT 1：open.alibaba.com 开放平台**全站 82 个 API、8 大类**（Product、Product V2、Video、Buyer/Seller 交易物流），**无任何 message/im/chat/inquiry 接口**（逐条核对 `/handler/share/apidoc/getApiCategoryMixed.json`）。
- FACT 2：可验证的 IM 读取 API 位于 TOP 体系 **「OKKI&TM」类目**（服务小满 OKKI CRM/服务商集成）：
  - `alibaba.interaction.im.conversation.list.query`（会话列表查询，apiId=69003；入参 seller_account_id/limit_time_stamp/count，返回 conversation_id）
  - `alibaba.interaction.im.message.list.query`（历史聊天消息查询，apiId=69005；入参 conversation_id/count/forward/limit_time_stamp/self_account_id）
  - 同组另有 assignsetting.query（说明为"提供数据给小满"）、assistant.message.send、message.card.parse 等。各接口页**「如何获得此API」栏为空**（申请路径未公开）。
- FACT 3（决定性）：官方公告《旺旺聊天记录接口下线与升级公告》（2021-08-16）：旧接口（`taobao.wangwang.abstract.logquery`、`eservice.chatrelation.get` 等）**2021-09-15 下线**，替代为「旺旺聊天消息服务」，**「一期只开放给具备『旺旺聊天记录』权限的应用，暂不支持新用户申请」**。
- FACT 4：应用条件——需注册开发者账号并签协议；类目分 Commercial Developer（ISV）与 Individual Developer（自用店铺），均需平台审核；权限包部分默认开通、其余单独申请；访问卖家数据需 OAuth2.0 授权；服务商需入驻外贸服务市场、应用部署聚石塔并审核。收费：旺旺聊天消息服务 0.01 元/百条；基础 API 聚石塔内 0.02 元/百次、塔外 0.2 元/百次。
- FACT 5：ICBU 消息体系为 **TMC 推送订阅**（ISV 模式 + `taobao.tmc.user.permit` + TmcClient 长连接）；示例 topic 仅 `icbu_trade_OrderNotify`（订单变更），**无 IM 会话 topic**。
- 卖家侧官方能力：客户端界面查看/手动导出（「阿里卖家 PC 端 → 接待中心 → 查看消息记录 → 消息管理器 → 工具 → 导入/导出」；主账号可在「商机沟通 → 询盘 → TM 消息管理器」查看子账号消息）；询盘列表页 Export Inquiry 按钮实测当前账号无权限。TradeManager 网页版保留 365 天记录，桌面版 2018-12-31 停止支持。
- UNKNOWN：OKKI&TM IM 接口的具体申请条件/权限包名/是否对自研商家开放（"如何获得此API"栏空白）；ICBU IM 接口当前是否仍有效及有无下线公告；service.alibaba.com 官方导出帮助条目的一手确认（该站为 JS 渲染，未取到原文）；自用型应用可申请的 API 范围明细。

### 2.3 WhatsApp 官方导出 + ToS/封号风险

**Web 读取（FACT）**：
- WhatsApp Web 是官方客户端（手机扫码，关联设备），界面为普通聊天窗口；
- 条款：Messaging Guidelines（2026-09-23 版）禁止「使用非官方客户端、批量/自动消息、自动化」与抓取数据；ToS《可接受使用》（2021-01-04）禁止以自动化方式收集用户信息；FAQ「关于非官方应用」明确关联非官方版本违反 ToS，可致临时/永久封禁；
- 执法（FACT）：官方白皮书 Stopping Abuse（2019-02-06）称用机器学习识别自动化、每月封约 200 万账号；官方印度月报（2026-10-01 发布）显示 2026-08 封禁 620.2 万印度账号（169 万为主动封禁）；
- UNKNOWN：无官方「2025 年非官方客户端封号潮」专门公告（相关报道均为二级来源）；
- 风险判定：**人工只读浏览自己已登录的会话＝正常使用；程序化驱动浏览器批量读取落入官方「自动化」定义，有封号风险**。本次实验为单会话、低频、人工在场的只读操作。

**官方导出（FACT，faq.whatsapp.com/1180414079177245）**：
- Android：聊天 → 更多选项 → 更多 → 导出聊天 → 不含/包含影音内容；iOS：点联系人名 → 导出聊天 → 附全部/部分/不含影音内容；产物为 ZIP/文本，不可回导，阅后即焚消息不导出；
- .txt 行格式（据开源解析器 whatsapp-chat-parser 正则与测试样例）：`06/03/2017, 00:45 - 发送者: 消息`；日/月顺序随地区设置变化；
- **注意**：英文版 FAQ 称 Web 与 Windows 桌面支持导出（全部消息或自定义日期范围、ZIP 不含媒体），但中文/西/法/葡版同一页写「这项功能在网页应用/Windows 应用不受支持」——以实机为准（本次未实测桌面端导出）；
- 风险判定：官方导出零封号风险，是唯一完全合规的历史记录获取路径。

**第三方库参考（不建议使用）**：
- whatsapp-web.js：Node+Puppeteer 驱动 WhatsApp Web（Apache-2.0，2.27 万星，最新提交 2026-10-11，活跃）；README 免责声明：「WhatsApp 不允许机器人或非官方客户端，不应视为完全安全」；封号案例：#532（2021）、#3474（2025-02）、#4008（2025-12 发 3 条即封）、#3948/#3963（2025-11 起 24 小时限制）；
- WPPConnect：同类库（LGPL-3.0，3,469 星，活跃；README 无封号免责声明、正在招维护者）；#715（2021 封号）、#2013（用户问是否安全）；
- 2026-06 起两库与 OpenWA 均报告扫码/配对返回 428 或要求 passkey（wwebjs #201806、WPPConnect #2815、OpenWA #560），**可用性进一步恶化**；
- 风险判定：官方明确视为违规、社区封号案例多、2026 年 passkey 变更加剧不可用——**勿对真实账号使用**。

## 3. 统一消息结构（已实现）

### 3.1 设计目标

保留 **来源 ID、时间、发件人、正文、附件** 五要素；与邮件渠道并存；可增量去重；覆盖范围如实声明；不引入 CRM 或新界面。

### 3.2 结构定义（每个会话一个 JSON 文件；实测输出见 `D:/Projects/_work_multichannel/内部数据/multichannel/`，仅存本地）

```json
{
  "schema_version": 1,
  "channel": "alibaba | whatsapp",
  "source": {
    "platform": "alibaba_seller | whatsapp_web",
    "conversation_id": "inquiry:<平台询盘 ID>",
    "conversation_label": "<客户显示名（示例）>",
    "contact": {"display_name": "", "platform_id": "", "phone": null, "email": null}
  },
  "messages": [
    {
      "id": "mc-<12hex>",
      "fingerprint": {"primary": "sha256('<platform>:<source_id>')", "alternate": "sha256(内容基础哈希)"},
      "source_id": "<平台消息 ID；缺失时为 null>",
      "sent_at": "<ISO 8601 带时区，如 2026-10-09T19:40:27+08:00>",
      "sent_at_raw": "<平台原文时间>",
      "direction": "received | sent | system",
      "sender": {"display_name": "", "platform_id": "", "is_owner": false},
      "body": "<原始文本；图片消息为 [图片消息]>",
      "body_format": "plain",
      "attachments": [
        {"filename": null, "content_type": "image/jpeg", "size": "<字节数>",
         "sha256": "<内容哈希>", "stored_path": "内部数据/.../attachments/<file>",
         "source_url": null, "error": null}
      ]
    }
  ],
  "capture": {
    "method": "authorized_browser",
    "captured_at": "<ISO 8601>",
    "account_ref": "<登录账号的脱敏标识>",
    "coverage": {"messages_seen": 39, "history_complete": false, "note": "…"}
  }
}
```

字段要点：
- **来源 ID**：`source_id` 原样保留（阿里 `messageId`，WhatsApp `data-id`）；公开文档中一律用占位符；
- **时间**：`sent_at`（ISO 带时区）+ `sent_at_raw`（平台原文）；
- **发件人**：显示名 + `is_owner`（对应 sent/received）；
- **正文**：原文完整保留；图片消息以 `[图片消息]` 占位，来源链接在 attachments；
- **附件**：内容哈希去重、`stored_path` 落盘、取不到时 `error` 如实标注；
- **去重**：主指纹 = 平台来源 ID；备用指纹 = 内容基础哈希（与现有邮件双指纹同思路）；
- **覆盖诚实**：`capture.coverage` 记录已见条数、是否完整、未覆盖范围。

### 3.3 合并规则

- 路由：`source.contact` 经人工确认后映射到现有客户 slug，未确认不并入；
- 不新增事实层：平台消息是**原始沟通记录**，与邮件同级；AI 判断仍只进 Agent 区域。

### 3.4 落盘与 Notion 增量合并（分阶段）

- **阶段 1（本地，已完成）**：平台消息写入 `_work_multichannel/内部数据/multichannel/<channel>/<conversation>.json`；规范化脚本 `normalize_alibaba.py` / `normalize_whatsapp.py`（纯标准库，读已落盘 JSON）。
- **阶段 2（Notion，需明确授权后执行）**：按现有客户原始资料页增量合并——原文保留原位、按 message id/时间/版本去重、只追加受支持内容、写后回读。例：某客户页已有「原始 WhatsApp 聊天摘录（用户提供）」章节，新消息追加进该既有章节，而不是新建平行页面。

## 4. 最小实验执行记录（2 阿里 + 1 WhatsApp）

前置：用户在带 bsk 扩展的 Chrome 登录 `seller.alibaba.com`（已满足）；会话由用户授权自行选定，实际选定 2 个阿里会话（代号 A、B）+ 1 个 WhatsApp 会话（代号 C）。

| 步骤 | 结果 |
| --- | --- |
| 阿里会话列表读取（仅列表） | PASS（20+ 项，未点开范围外会话） |
| 阿里会话 A（含 Show more 至最早） | PASS：39 条（12 收 / 20 发 / 7 系统），平台消息 ID/时间戳/原文完整 |
| 阿里会话 B | PASS：5 条（1 收 / 2 发 / 2 系统） |
| WhatsApp 会话 C | PASS：6 条（4 收 / 2 发），2 张参考图落盘（sha256 已记录） |
| 站点侧写操作 | 零（未发送/删除/标记任何消息） |
| 范围外联系人 | 零读取 |

## 5. 风险与边界

| 项 | 说明 |
| --- | --- |
| 账号风险 | 阿里 ToS 限制自动化访问（ali-cli 自述）；WhatsApp 条款禁止自动化收集，官方有大规模封禁执法记录（§2.3）。本次实验为单会话、低频、只读、人工在场；**不得升级为高频/批量自动化**。 |
| 只读副作用 | 打开会话会把我方视角标记为已读（WhatsApp 实测；阿里同类行为未单独验证）。 |
| 范围纪律 | 不自动发消息；不读全部联系人；会话由用户点名或授权选定；实验规模限定 2+1。 |
| 数据边界 | 本仓库文档不含客户姓名、联系方式、平台会话/消息 ID 或消息正文；真实标识与原文仅存用户本地私有目录，不进 Git、不进发布包。 |
| 声明纪律 | 未实测的平台能力一律标 UNKNOWN；不使用反爬绕过手段；不引入第三方 WhatsApp 客户端库。 |

## 6. UNKNOWN 清单（未实测，不得声称支持）

1. 阿里卖家端更早历史（早于 2026-09-09）在视图内的可达性（未继续翻页验证）；
2. 阿里媒体消息载荷下载（本次仅记录来源链接）；
3. WhatsApp 桌面端/Web 官方导出（官方各语言说明不一致，未实机验证）；
4. AlibabaSupplier / AliWorkbench 本地缓存是否含可读会话数据（未探索）；
5. 其他 Agent 宿主（Codex/WorkBuddy 等）对同一流程的复用（未实测）；
6. 阿里询盘 Export Inquiry 功能的完整权限条件（实测当前账号无权限）；
7. OKKI&TM IM 接口的具体申请条件/权限包名/是否对自研商家开放；ICBU IM 接口当前有效性（§2.2）；
8. Playwright MCP 的 CAPTCHA/反检测立场（官方文档未提及）；browser-use 本机 profile 模式"是否必须关闭 Chrome"（官方仅称"可能"）；Skyvern 自托管"复制现有 profile"在 Windows 的实际行为（均未实测，§2.1）。
