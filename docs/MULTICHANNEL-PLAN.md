# 多渠道集成计划(下一版本,不含在本包内)

状态:**计划文档,未实现**。Email Beta(v4.2)不含任何多渠道代码;本文件与 `MULTICHANNEL-RESEARCH.md` 都通过 `.gitattributes export-ignore` 排除在发布 ZIP 之外,只在 git 仓库内可见。

## 0. 分工与边界

| 角色 | 职责 | 产出 |
| --- | --- | --- |
| Hermes | 已实测的**脱敏适配器源码**(阿里卖家端 IM、WhatsApp Web 只读提取) | `normalize_alibaba.py` / `normalize_whatsapp.py` 或等价模块 + 其输出 JSON 样例(脱敏) |
| ZCode | 统一导入、增量去重、客户身份确认、客户文件夹归档 | 邮件渠道同款闭环扩展到 `channel` 字段;复用双指纹、身份映射、render/verify |
| Email Beta | **不受影响**:多渠道不得改动邮件模块的现有行为与测试 | 84 项测试继续全绿是多渠道合并的硬门槛 |

**硬性约束**

1. 阿里 / WhatsApp 实验代码**不得**进入 Email Beta 包,也不得改变邮件渠道的默认路径、CLI 行为或 state 结构语义(只能**新增可选字段**,不能改现有字段含义)。
2. WhatsApp 必须明示:平台条款禁止自动化收集(官方有大规模封禁执法记录),**只提供人工只读浏览路径**,程序化驱动浏览器有封号风险;官方导出(手机端)是唯一零风险历史获取方式。
3. 任何平台消息并入客户档案前,**必须经人工确认身份**(免费邮箱/无公司信息同规则)。
4. 反爬绕过、第三方非官方客户端(whatsapp-web.js/WPPConnect 等)**一律不引入**(研究文档已否决)。

## 1. 统一消息结构(复用现有 state 契约)

现有邮件 `state["messages"][hash]` 已是:{id, hash, file, from, to, date, subject, message_id, direction, body_format, attachments[], free_domain, processed}。多渠道只需**新增可选字段**,不改既有语义:

```json
{
  "channel": "email | alibaba | whatsapp",      // 新增,缺省视为 "email"
  "source": {                                    // 新增,非邮件渠道必填
    "platform": "alibaba_seller | whatsapp_web",
    "conversation_id": "<平台会话 ID>",
    "contact": {"display_name": "", "platform_id": "", "phone": null, "email": null}
  },
  "source_id": "<平台消息 ID>",                   // 新增;邮件渠道为空
  "sent_at_raw": "<平台原文时间>"                  // 新增;邮件渠道为空
}
```

- **双指纹**沿用:`identity_index` 里主指纹 = `sha256("alibaba:<source_id>")` 或 `sha256("whatsapp:<data-id>")`,备用 = 内容基础哈希。**邮件指纹算法不变**(现有测试锁死)。
- 附件沿用 `attachments[]` 契约(sha256 去重、`stored_path`、`error` 如实标注)。

## 2. 导入与归档流程(扩展 Email 闭环,不分叉)

```
Hermes 适配器输出会话 JSON(每会话一个文件,含 capture.coverage)
        │
        ▼  [新增] tcm.py ingest-channel --channel alibaba|whatsapp <json>
        │     - 校验 schema_version;逐条规范化 → 与邮件同构的记录
        │     - 双指纹去重(与邮件共用 identity_index,永不重复)
        │     - 附件落盘 内部数据/attachments/<id>/;缺失如实标 error
        │     - 覆盖范围写入 state["coverage"]["channels"]
        ▼
[人工确认] map-customer <slug> --channel-identity "alibaba:<contact_id>" ...
        │     未确认 → --unconfirmed,独立建档,不得并入
        ▼
render(同一函数):客户沟通记录.md 时间线按时间合并邮件与平台消息,
        │     每条标注渠道徽标(如 `msg-<id>` · 收件 · 阿里 / WhatsApp)
        ▼
report → brief → verify(同一套;verify 新增:多渠道记录必须引用存在 id)
```

**验收门槛(合并前必须全过)**
- [ ] Email 渠道 84 项测试继续全绿(零改动语义)。
- [ ] 新增多渠道测试:同一会话重复导入零新增;跨渠道同一客户时间线正确合并;未确认身份不并入;附件去重跨渠道生效;verify 拦截悬空引用。
- [ ] 用**脱敏虚构**样本(不得含真实客户/会话 ID/消息正文)做端到端。

## 3. 分阶段(建议)

| 阶段 | 内容 | 退出条件 |
| --- | --- | --- |
| M1 | 统一 `channel` 字段 + `ingest-channel` 命令 + 双指纹共用;仅接受**已落盘的规范化 JSON**(不碰浏览器) | 虚构样本端到端 + 邮件回归全绿 |
| M2 | 接入 Hermes 脱敏适配器输出;人工身份确认流程;render 渠道徽标 | 真实(脱敏)会话样例通过;verify 增强 |
| M3 | WhatsApp:仅**人工导出**路径(手机端导出 → 解析 .txt)+ 明示封号风险与人工步骤 | 导出样例解析正确;文档写明平台限制 |
| M4 | 可选:定时增量、Notion 合并(需另行授权) | 用户明确要求后另议 |

## 4. WhatsApp 平台限制(必须写进用户可见文档)

- 官方条款禁止非官方客户端与自动化收集;**程序化驱动浏览器批量读取有封号风险**(官方 2019 白皮书称月封约 200 万账号;2026-08 印度单月封 620 万)。
- **唯一零风险路径**:手机端官方导出(聊天 → 导出聊天),产物 .txt/.zip,不可回导;Web/桌面端是否支持导出官方各语言说明不一致,以实机为准。
- 若做只读提取:必须**人工在场、单会话、低频**;打开会话会把我方视角标记为已读(副作用);不得升级为批量/高频自动化。
- 第三方库(whatsapp-web.js / WPPConnect / OpenWA)已否决:违反 ToS、封号案例多、2026 起 passkey 变更进一步恶化。

## 5. 风险与依赖

- 依赖 Hermes 交付**脱敏**适配器源码与样例;未交付前 M2 无法启动。
- 阿里官方 IM 接口对普通卖家不可用(研究结论),因此只读浏览器提取是唯一现实路径,须遵守范围纪律(用户点名会话)。
- 任何平台页面结构变化都会使适配器失效——适配器需版本化并带失败时的明确报错(不得静默返回空)。
