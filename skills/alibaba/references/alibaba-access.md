# 阿里卖家后台读取说明（只读）

## 前置条件

用户在**自己的 Chrome** 里登录阿里卖家后台（message.alibaba.com）。Agent 不保存、不询问、不接触密码。

## 桥接方式

已验证：**Hermes + bsk CLI**（`bsk session start --no-focus` → `bsk js <expr> --session <sid>`）。

未验证：Playwright MCP、Chrome DevTools Protocol 直连、其他 Agent 的自带浏览器。
要用它们请自行封装一层，接口只需 3 个能力：

1. `navigate(url)` — 打开页面
2. `js(expression)` — 在页面执行 JS 并返回 JSON
3. `session start/stop` — 建立、销毁会话

把 `tcm_ali.Bridge` 替换掉即可，其余逻辑不变。切换后**先跑 `tests/test_offline.py` 和 `doctor`**。

## 页面结构（2026-10 实测）

| 目标 | 选择器 / 属性 |
| --- | --- |
| 会话列表项 | `a[href*="maDetail"]`，会话号在 `href` 的 `imInquiryId=(\d+)` |
| 买家显示名 | `.aui2-grid-name` |
| 最后一条消息摘要 | `.buyer-chat-msg` |
| 更新时间 | `.m-header-padding` 中含 "Updated on" 的那一项 |
| 消息项 | `.message-item-wrapper` |
| 消息元数据 | `data-expinfo`（JSON：messageId / messageType / sendTime） |
| 消息正文 | `data-original`（HTML 片段） |
| 消息方向 | class 含 `item-left`=买家，`item-right`=我方，其余=系统 |
| 加载更早 | `a.show-more` |

**页面改版会导致列表返回 0 条。** 症状要先当成「登录态过期或页面改版」告诉用户，不要改代码硬猜选择器。

## 消息类型

| messageType | 含义 |
| --- | --- |
| 1 | 文本 |
| 50 | 名片 / 询盘卡片 |
| 60 | 图片 |
| 9545 | 平台自动跟进（**不是我方人工回复**） |
| 9999 | 系统消息 |

## ID 归一化

同一条逻辑消息，平台有时在 ID 后加 `.PNM` 传输后缀（实例如：同一条消息某次抓到是 `1234567890123`，另一次是 `1234567890123.PNM`）。
去重前必须归一化，否则同一条消息会重复入档。见 `scripts/tcm_ali_lib.py::norm_msg_id`。

## 未登录处理

列表为空时脚本**只记录「未登录 / 列表未加载」并停止**，不写任何结论。
严禁把「未登录」推断成「没有新询盘」——这是最容易误导业务的错误。
