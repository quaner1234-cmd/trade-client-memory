# 阿里巴巴国际站询盘整理 Skill

把已登录的阿里卖家后台（ message.alibaba.com ）里的询盘和聊天，**只读**整理成本地 Markdown 客户档案：一个客户一个文件夹 + 客户总览，自动去重、建档、标出该跟进的客户。

外贸业务员专用，不需要懂编程。

## 它能做什么

- 读取阿里卖家后台的询盘列表与聊天原文（**只读，绝不发消息**）
- 自动去重：同一条消息永不重复入档（含平台 `.PNM` 后缀坑）
- 自动分客户建档：`客户档案/<客户名 - 国家>/客户沟通记录.md`
- 自动识别"有没有实质询价"，只有打招呼 / 只有系统通知的不会误判成有效询盘
- **匹配不上的客户不会丢**，一律落 `客户档案/_待确认/`
- 输出客户概览表，按紧急度排序告诉你下一步做什么
- 增量同步：重复跑不产生重复内容，可挂定时任务

## 不能做什么（安全红线）

- ❌ 不发送、不回复、不删除、不标记任何平台消息
- ❌ 不自动把数据上传到 Notion / 云端
- ❌ 不拿不准的事情冒充事实：写不清的一律标「待确认」
- ❌ 从不删除或覆盖历史记录（append-only）

## 30 秒上手

```bash
# 1. 初始化（<base> 是你要放客户档案的目录）
python skills/alibaba/scripts/tcm_ali.py --base ./我的客户 init
python skills/alibaba/scripts/tcm_ali.py --base ./我的客户 doctor

# 2. 在 Chrome 里登录 message.alibaba.com，然后抓取（只读）
python skills/alibaba/scripts/tcm_ali.py --base ./我的客户 extract

# 3. 整理建档
python skills/alibaba/scripts/tcm_ali.py --base ./我的客户 ingest --owner "张三"

# 4. 看结果
python skills/alibaba/scripts/tcm_ali.py --base ./我的客户 brief
```

完整流程（含 Agent 怎么判断进展、怎么写下一步）看 [`SKILL.md`](SKILL.md)。

## 与 Email Skill 的关系

同一个仓库里的两套 Skill，**互相独立、互不影响**：

| | Email Skill（仓库根） | Alibaba Skill（`skills/alibaba/`） |
| --- | --- | --- |
| 数据来源 | 邮箱 IMAP / 邮件连接器 | 阿里卖家后台网页 |
| 输出结构 | 一个客户一个文件夹 + 客户概览 | 相同 |
| 同时使用 | ✅ 客户档案目录建议分开，或按渠道命名区分 | ✅ |

Alibaba Skill **不读取邮箱**，也**不修改 Email Skill 的任何文件**。

## 环境要求

- Python 3.10+
- **Hermes + bsk 浏览器桥接**（已验证）
- 用户自己 Chrome 里已登录的阿里卖家后台

其他 Agent / 其他浏览器桥接**未验证**。要用请实现 3 个能力（navigate / js / session）替换 `scripts/tcm_ali.py` 里的 `Bridge` 类，见 `skills/alibaba/references/alibaba-access.md`。

## 自测

```bash
python skills/alibaba/tests/test_offline.py
```

67 项离线测试，不联网、不碰真实数据。

## 隐私

- 客户数据只写在你指定的 `<base>/`，永不进 skill 目录、不进 Git
- 发布 / 打包前跑：`python skills/alibaba/scripts/tcm_ali.py --base <任意> audit`
- `audit` 会扫出邮箱、手机号、token、委托地址等，任何一处命中就 exit 1

## 目录结构

```
skills/alibaba/
├── SKILL.md                    Agent 执行流程（主入口）
├── README.md                   本文件
├── references/
│   ├── trade-judgement.md      外贸判断规则（平台数据 ≠ 事实）
│   └── alibaba-access.md       页面结构、桥接方式、ID 坑
├── scripts/
│   ├── tcm_ali.py              CLI 主程序
│   └── tcm_ali_lib.py          确定性逻辑（去重 / 归一化 / 实质判定）
├── samples/
│   └── build_example.py        虚构示例数据生成
└── tests/
    └── test_offline.py         离线测试（67 项）
```

## 已知限制

- 单次抓取会话数有上限（默认 20），超出的下次继续（不丢，见 `--max-conv`）
- 聊天里的图片只保存源 URL，平台链接可能过期
- 自动建档的国家取自平台字段，标注「未核实」，需人工确认
- 页面改版会导致选择器失效，症状是列表 0 条 → 先让用户确认登录态
