# 端到端运行记录(E2E Run Log)

- 日期:2026-10-10(PoC 首轮;同日 Beta 修复轮)
- 执行者:ZCode Agent(GLM),按本包 `SKILL.md` 工作流执行(即"Agent 真实使用本 skill"的验收运行)
- 环境:Windows 11(10.0.26300)· Git Bash · Python 3.12.10 · 无邮箱连接器参与 · 无网络依赖
- 测试数据:`tests/fixtures/`(10 封虚构邮件,3 客户收/发多轮 + 1 身份不明发件人)与 `samples/demo-mails/` 同源

## Beta 修复轮(2026-10-10,审查后 7 项缺陷)

流程:先写回归测试(红)→ 修复 → 全量跑绿 → 文档同步。确定性套件从 19 项扩到 **42/42 PASS**。

| # | 缺陷 | 回归测试 | 修复 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | `ingest --owner` 只对当批生效,不写入 state.json,第二次 ingest 就忘 | T13(3 项检查) | owner 校验后写入 `state["owner"]`;方向修正提取为 `apply_owner_directions()`,`ingest --owner` 与 `owner` 命令共用,所有权认知全局生效 | PASS |
| 2 | 去重漏洞:同一邮件先带 Message-ID 导入、再被客户端导出剥掉 Message-ID 后重复入库(单向) | T9 两个方向 | ingest 去重增加第三分支:来信规范化哈希命中 `identity_index`(它记录了所有备用指纹→主指纹)即跳过 | PASS(双向) |
| 3 | verify 误报成功:10 封全部 done、零客户零档案仍 `ok:true` | T10(2)+ T10b(4) | 新增失败守卫:done 但发件人未映射 / 客户邮件已 done 但无档案或档案未引用该 id / 档案缺模板章节或 frontmatter 字段 / 有档案无 tasks.md / 有已处理收件但零档案;unprocessed>0 降为 warnings(须向用户交代) | PASS |
| 4a | `imap_fetch --limit 0`/负数静默变成"全部拉取" | T11(3) | argparse `type=validate_limit`,<1 直接报错 | PASS |
| 4b | 拉取文件名 `imap-<folder>-<uid>.eml`:多账号/净化后同名的文件夹互相覆盖 | T12(3) | 文件名编码账号与原始文件夹哈希:`imap-<user>-<acchash>-<folder>-<folderhash>-<uid>.eml`,跨账号/文件夹唯一,同源重拉幂等覆盖 | PASS |
| 5 | tasks.md 路径表述不一(START-HERE 树画在 data/ 根,实际在 data/clients/);status 把它混进 profile_files | T14(2) | 全部文档统一为 `data/clients/tasks.md`;status 单独报 `tasks_file` 且 profile_files 排除它 | PASS |
| 6 | 首次使用不会自动发现已有邮箱连接器 | T15(4) | `doctor` 新增:PATH 上的 `mail-read`(`shutil.which`)、本机 `_shared` 路径、`data_writable` 探测、`connector_available` 汇总;SKILL.md Phase 0/1 与 email-access.md 写明"连接器 → .eml"配方(重写完整头部入 data/inbox,指纹与出处和直接导入一致) | PASS(发现逻辑实测;真实邮箱拉取仍未验证) |
| 7 | 未区分"本地存储"与"理解过程经云端模型" | 文档项 | START-HERE 新增「数据存哪里、谁看得到」表;README Security 与 SKILL.md 红线各加一条;明确公司禁数据出网时的三条出路 | 已落实 |

修复过程还暴露并修正一处回归:提取 `apply_owner_directions` 时误把 `msgs_dir` 赋值粘进注释行(套件立即报 NameError 拦截),已修复——回归套件本身抓到了修复中的错误。

## Beta 轮对真实 data/ 的复验

- 强化版 `verify` 对 4 客户档案(章节、frontmatter、引用覆盖)、tasks.md、10 封消息全部通过,exit 0,warnings 空。
- 全量重导 `samples/demo-mails` → `new:0 skipped:10`,去重完好;`status` 的 `tasks_file` 正确分离。

## PoC 首轮场景结果(2026-10-10,全部真实执行,非推断)

| # | 场景 | 观察结果 | 结论 |
| --- | --- | --- | --- |
| 1 | 不配置邮箱,仅导入邮件 → 生成档案与待办 | `ingest` 9 封全 new;`map-customer` ×4(1 个 `--unconfirmed`);Agent 写 4 档案 + tasks.md;`done` ×9;`verify` `{ok:true, problems:[]}`,计数 messages 9 / customers 4 / profiles 4 | **PASS** |
| 2 | 第二次导入同一批 → 不重复 | `ingest` → `new:0 skipped:9`;`status` total 9、unprocessed 0;4 份档案 md5 **字节级不变**;verify PASS | **PASS** |
| 3 | 新增一封邮件 → 更新正确档案 | 导入 `10-marco-confirm.eml` → `new:1 skipped:0`,id `6fce646dab55`;Marco 档案:阶段 还价→PI、新增事实与进展(均带 [msg-6fce646dab55])、待确认项消解(L/C 项因客户接受 T/T 关闭);tasks.md:还价待办替换为"发 PI",日历事件更新为 11-05~07;sarah/hans/david 档案未动;`done` 后 verify `{ok:true}`,messages 10 / customers 4 / profiles 4;再全量导入 → `new:0 skipped:10` | **PASS** |
| 4 | 无法确定身份/事实 → 标待确认不猜测 | david.m1987@gmail.com(free_domain=true,无公司/产品指向)独立建档 `unconfirmed: true`,档案「待确认」列 3 项核实点,不并入任何已有客户;"same model as last time" 未对应到任何已知产品,只列可能项不做认定 | **PASS** |
| 5 | 缺邮箱连接器/Notion 等可选能力 → 基本流程不受影响 | 全程仅用 .eml 导入路径;`doctor` 报告可选工具存在与否仅作提示;未调用任何邮箱服务、Notion、第三方 API | **PASS** |
| 6 | Agent 自主完成本地准备(而非只输出命令) | 本记录整体即证据:init/ingest/map-customer/档案写入/done/verify 全部由 Agent 直接执行完成;需要用户的步骤(仅 IMAP 密码输入)在无邮箱演示路径中未出现 | **PASS** |

PoC 轮发现并已修复的缺陷(1–2 为状态机,3 为隐私,4 为 fixture):

1. **别名键副本**:state 最初为每封邮件同时存内容哈希键与 Message-ID 别名键,JSON 往返后成为两个独立对象,`done`/`owner` 只改到其一(T3/T7 暴露)。修复:一邮件一条记录 + `identity_index` 别名索引。
2. **id 长度不一致**:证据引用约定 12 位短 id,实现却把 64 位哈希写进文件名。修复:`id = hash[:12]`,state 仍用全哈希去重,verify 增加短 id 碰撞检测。
3. **state 泄漏本机路径**:消息记录的 `file` 字段存绝对路径,会在 state.json/messages 里带入机器用户名(ZIP 自检时发现)。修复:skill 根目录内的文件一律存相对路径,外部文件才存绝对路径。
4. fixture 09 的 Message-ID 含空格(非 RFC5322 合法格式),修正后再入库。

## 未测试/已知边界(FACT 边界,不得外推)

- `imap_fetch.py` 的 IMAP 路径**未连接真实邮箱验证**(本演示无凭据);其只读语义(EXAMINE + BODY.PEEK、无 store/delete 调用)以代码为准,待真实账号一次验证。
- 其他 Agent 宿主(Codex/WorkBuddy/Hermes/Cline 等)未实测;机制上只依赖文件读写 + 终端 + Python 标准库,但未跑通前不声称支持。
- 已有的外部只读邮件连接器(如 `mail-read` CLI/MCP)作为可选优先路径写在 `references/email-access.md`;没有连接器的用户走 imap_fetch.py 或 .eml 导入。
- QwenPaw Mail 适配器:评估后未引入(含发送/整理写能力,未做真实邮箱安全验证),见 platform-notes.md。
- 重复导入场景覆盖"同批文件重复导入"与"Message-ID/内容哈希两种指纹";邮件客户端导出时**剥离 Message-ID 头**的情形由 alt-hash 索引覆盖,未构造真实客户端差异样本。

## 复现方式

```bash
python tests/run_tests.py                 # 确定性 19 项
python scripts/tcm.py doctor              # 环境自检
# 端到端:删 data/ 后按 SKILL.md Phase 0→5 对 samples/demo-mails 执行一遍
```

---

# v4 重构轮(2026-10-11,基于 WorkBuddy 真实企业邮箱实验)

## 前提核查(诚实声明)

WorkBuddy 实验的「交付结构生成器」与「邮箱遍历脚本」只存在于实验机(macOS),本机 `~/.workbuddy`、`~/.workbuddy-ai`(含 artifact-index 4 个索引)对「沟通记录/客户概览/客户档案」零命中——**代码不可取得,本仓库按实验描述与验收标准重新实现,未假装复用**。实验发现的问题形态全部转化为脱敏回归测试。

## 架构变更(响应实验发现)

| 实验发现 | v4 响应 | 回归测试 |
| --- | --- | --- |
| 58/120 封已发送邮件 text/plain 为空、HTML 完整 | `pick_body`:空 plain(纯空白)不再遮蔽 HTML,回退提取 | T15 |
| 引用链、签名 Logo、重复附件污染正文 | `clean_body`(历史引用折叠/**逐项回复保留**/签名与免责声明折叠)+ 附件内容哈希去重 + 内联同图 ≥3 封判定疑似 Logo(存档不预览) | T17 T18 |
| 独立摘要/JSON/.eml 不是业务员要的 | 输出改为 `客户档案/<公司 - 国家>/客户沟通记录.md + 附件/` + `客户概览.md`;`.eml`/JSON/索引全部移入 `内部数据/` | T18 T19 T22 |
| macOS 沙箱无法自动开交互终端 | `tcm.py launcher` 生成可双击的 `连接邮箱.command/.bat`,用户双击输授权码;Agent 轮询 `fetch-status.json` 或等一次用户确认,**不绕沙箱** | T23 T24 |
| 收发必须一起看 | `imap_fetch` 默认 `--folders auto`:LIST 自动探测"已发送"(modified-UTF-7 解码),双文件夹进度/ETA/状态文件 | T24 |
| 部分拉取被当作全史 | 覆盖统计(total/fetched/遗漏)从 fetch-status 进 state,render 写进每份档案顶部 | T21 |
| 业务员要的是"现在怎么样/下一步" | 状态单一来源 `state.reports`(report 命令)→ `brief` 聊天简报与 `客户概览.md` 同源;verify 强制一致 | T20 |
| 客户改名/换国不能丢档 | slug 为稳定内部身份,folder 名随映射重命名,`shutil.move` 保留全部内容 | T19 |
| 真实数据与安装目录/发布包隔离 | 用户数据只在 `<base>/客户档案` 与 `<base>/内部数据`,.gitignore 根锚定;state 不存机器绝对路径 | T1 T22 |

## v4 测试与端到端

- 套件重建:**60/60 PASS**(T1–T24,含实验全部问题形态的脱敏回归;先红后绿,红状态确认了旧代码的缺陷)。
- 端到端模拟(全新 `<base>`,17 封虚构邮件,收 14/发 9 的覆盖声明):
  1. `init` → `doctor` → `launcher`(生成 .command/.bat)→ 模拟双击拉取(fetch-status:收 14/725、发 9/460)。
  2. `ingest --owner` → 17 new/0 skipped;`map-customer` ×4(1 个 unconfirmed);`done`;`render` → 4 客户文件夹 + 概览。
  3. Agent 填写沟通记录 Agent 区域(引用真实 msg id;过程中写错两个 id 被立即更正)→ `report` ×4 → `render` → `brief`。
  4. **聊天简报真实输出**见 `docs/CHAT-REPORT-EXAMPLE.md`(每客户两三行、按紧急度、区分业务员行动/等待客户/待核实)。
  5. `verify` PASS(warning:1 封 David 邮件按设计保持未处理)。
  6. 增量:投入全新 PO 邮件 → ingest 1 new → re-render → 归入原客户文件夹、时间线段落唯一、Agent 区域保留。
- 示例输出提交:`samples/example-output/客户档案/`(2 客户、签名 Logo 识别、附件去重、概览;verify PASS)。
- 渲染质量抽查(Marco 档案):内联 Logo 三封重复 → 「签名图片(疑似,已存档不预览)」;price-list/catalog 正常链接;逐项回复(`> 1.…` + 答复)完整保留;尾随历史引用折叠为标记;签名与免责声明折叠;Sarah 空 text/plain 邮件正文从 HTML 完整提取。

## 仍未验证(FACT 边界)

- `imap_fetch.py` 未连接真实 IMAP(含刺猬云邮);服务商表、已发送自动探测、进度/ETA 在真实服务器上的表现待一次实连。
- macOS 双击 `.command` 流程按实验描述实现,本仓库未在 macOS 上验证;Windows `.bat` 同理未实机双击验证。
- 其他 Agent 宿主(Codex/WorkBuddy/Hermes)未实测本 v3 工作流。
- 附件格式覆盖:已测 PNG/PDF;Word/Excel/PPT 走同一字节流路径,未逐一构造样本。
- 大邮箱(>1000 封)性能与分页未测。

---

# v4.1 定向可靠性修复轮(2026-10-11,先红后绿,套件 60→76)

| # | 问题 | 回归测试 | 修复 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | imap_fetch 仍依赖服务端 SEARCH(刺猬云邮实测会静默漏结果) | —(需真实服务端;逻辑以纯函数与策略分支测试) | 新增 `--strategy auto/search/enum`:auto 下 SEARCH 返回 0 封但文件夹 EXISTS>0(漏结果的确切信号)自动切换**逐封枚举**(按序号走 1..N,逐封读 UID+INTERNALDATE 做日期过滤,只读元数据);enum 可强制 | 实现完成,真实服务端待验 |
| 2a | --limit 截断后进度/ETA 与计划数不一致 | T25(plan_folder_uids/eta_seconds) | 进度与 ETA 一律对 planned(截断后计划数)计量,不再混用服务器总数 | PASS |
| 2b | 文件夹缺失/跳过/单封读取失败仍报"完整成功" | T25(finalize_status) | 状态文件新增 `complete` 与 `errors[]`:任何遗漏 → `complete:false`、stage=「读取完成(部分,有遗漏)」、终端打印计划/成功/遗漏数量与补齐指引;只有零遗漏才打「邮件读取完成」 | PASS |
| 3 | 已发送邮件(我方报价/回复)未归档不拦截验收 | T26 | verify 新增 sent 规则:已 done 的发送邮件,收件人映射到客户 → 该客户沟通记录必须包含该 id;收件人全部未映射 → 阻止验收 | PASS |
| 4a | 真实数据可能误写进 Skill 安装目录/Git 工作树 | T27 | `guard_base`:数据目录位于 skill 目录内(除 samples/ 虚构白名单)直接拒绝并给出迁移话术 | PASS |
| 4b | launcher 参数未校验,host/user 可注入 shell | T27 | host/user/since 白名单校验(不合法即拒绝);.command 用 shlex.quote,.bat 双引号+拒绝含引号/百分号的路径 | PASS |
| 5 | 示例附件是文本假 PDF,打不开 | T28 | `minimal_pdf()`:手写合法单页 PDF(对象/xref/%%EOF 齐全);示例语料全部换用;示例由 `samples/build_example.py` 一键重建 | PASS |

修复过程中套件还抓到两个实施错误(追加测试块落在汇总之后不可达;示例脚本 to 参数缺列表包装),均已当场修正——先红后绿流程再次拦截回归。

## Beta 边界(截至本轮,保持不变)

- 真实邮箱(刺猬云邮/exmail/QQ/163/Gmail)尚未用 v4.1 实连;enum 回退与 SEARCH 可疑信号需一次实连确认。
- macOS `.command` 双击与 WorkBuddy 端到端尚未复验;Windows `.bat` 未实机双击。
- 其他 Agent 宿主未实测;Word/Excel/PPT 附件未逐一构造样本(PDF/PNG 已真实)。

---

# v4.2 邮箱读取环节定向修复(2026-10-11,先红后绿,套件 76→84,新增模拟 IMAP 服务端)

| # | 问题 | 回归测试 | 修复 | 结果 |
| --- | --- | --- | --- | --- |
| 1a | resolve_folders 未正确解析 imaplib LIST 的 bytes 响应(正则残缺被 except 静默吞掉),中文已发送(&XfJT0ZAB-)漏检;SELECT 用解码名而服务器只认原始名 | T29a(模拟服务端) | 返回 (raw, display) 二元组:raw 名用于 SELECT,display 名(修改 UTF-7 解码)用于覆盖统计与展示;LIST 解析兼容 tuple/bytes 两种响应形态与引号包裹 | PASS |
| 1b | 找不到已发送文件夹时静默只拉收件箱 | T29c | auto 模式未发现已发送 → 记入 errors(「收发不完整」),complete=false,如实报部分完成 | PASS |
| 2 | auto 只在 SEARCH 返回 0 时回退,识别不了部分漏信;合法零结果会误触发枚举 | T29b T29c | `pick_strategy`:已知不可信服务商(刺猬云邮 yunyou.top)直接优先枚举;SEARCH 返 0 且 EXISTS>0(漏结果确切信号)才回退;空文件夹留在 search(不遍历) | PASS |
| 3 | 枚举无上限 | T29e | `--enum-window`(默认 1000):只检查最近 N 个序号;超出 → coverage{total:服务器实际数, scanned, truncated:true} + errors 写明「未覆盖更早的 X 封」→ complete=false;渲染覆盖声明据此如实显示遗漏 | PASS |
| 4 | 此前只测纯函数,未测协议交互 | T29a–e | 新增 `tests/fake_imap.py`:线程内模拟 IMAP4rev1 服务端(LIST/SELECT/EXAMINE/UID SEARCH/FETCH 字面量/LOGOUT),覆盖中文已发送、SEARCH 漏信、合法零结果、**传输中途断连**(partial 而非全成)、窗口截断五类场景 | PASS |
| 5 | T27 断言含 `or True` 恒真;README 测试数量过期 | T27 重写 | 改为精确断言(--user 参数完整、无注入片段);README 同步 84/84 | PASS |

实施中红绿流程再次拦截两类错误(模拟服务端 literal 尺寸行缺闭合 `}` 导致 imaplib 失步;枚举响应非 tuple 形态的 UID 解析遗漏)。

**Beta 边界保持**:模拟服务端≠真实服务端;刺猬云邮/exmail/QQ/163/Gmail 实连复验与 WorkBuddy macOS 端到端仍未完成,指令见 docs/WORKBUDDY-REVALIDATE.md。
