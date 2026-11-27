# Bug 审计每日工作流实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Bug 审计模块中增加可恢复、可核对的每日审计台账 CLI、只读 API 和页面，并在阶段 A 验收后让现有 Codex 自动化登记阶段结果。

**Architecture:** 新建 Bug 审计专属 SQLite 服务与 CLI，复用 `Store.connection()` 的事务；通过阶段化 JSON 导入候选、审计字段结果及 TP/飞书操作回执。`app.py` 增加受现有会话和角色保护的只读查询路由，`web/app.js` 仅扩展 Bug 审计页。外部 TP/飞书副作用仍由既有 Codex 自动化及通道执行，项目不发起、控制或重试。

**Tech Stack:** Python 3.11+ 标准库、SQLite、原生 HTTP 服务、原生 JavaScript/CSS/HTML、Python `unittest`。

**Spec:** [../specs/2026-11-24-bug-audit-daily-workflow-design.md](../specs/2026-11-24-bug-audit-daily-workflow-design.md)

## Global Constraints

- 仅开发“Bug 审计”；禁止修改其他独立功能模块。
- `sync.py`、`imports.py`、通用 `store.py` schema 与通用导入/自动更新逻辑保持不变。
- 允许在 `app.py` 增加 Bug Audit 专属 API 路由，在 `web/app.js` 增加 Bug 审计页专属视图。
- 自动化继续按北京时间每天 19:30 执行，保留范围、日期窗口、审计规则、TP/飞书身份、目标群及外部操作顺序；不创建第二个定时任务。
- 服务不持有 TP token、飞书凭据或用户 Cookie；CLI 不接受凭据参数；台账和通知不保存凭据、评论全文、敏感标识或本地绝对路径。
- 数据窗口为北京时间当天 00:00（含）至运行开始时刻（不含）；仅纳入 State 或 Severity 的真实旧值到新值变化；Add 初始化、其他字段变化、同值更新不算候选。
- 可写字段仅限 `Bug Standard Review of Testing`、`Bug Standard Review of Development`、`Bug quality audit`；拟值 `null` 表示不写入。
- 默认测试只使用隔离数据库和模拟 JSON，不访问 TP 或飞书；live 测试必须显式 opt-in。
- 同日同范围同规则版本恢复同一运行；重放已确认副作用只登记/核对回执，不重复执行外部副作用。

## Review Focus

- 夏令时/时区偏移或不带偏移的时间输入不能改变北京时间左闭右开窗口；测试 `test_window_normalizes_to_shanghai_and_excludes_cutoff`。
- 重复 Bug、History 与事件重放不得重复计数；测试 `test_replay_is_idempotent_for_bug_history_and_event`。
- `Add`、仅非目标字段变化、旧值等于新值应排除，State/Severity 任一真实变化应纳入一次；测试 `test_candidate_requires_real_state_or_severity_change`。
- 三字段各自 ready，`null` 或 pending 不可写；预检值改变标记冲突且不产生写入状态；测试 `test_field_readiness_and_preflight_conflict_are_independent`。
- 权限、未知路由、超大/错误 JSON 以及内容回执不得泄露凭据/评论全文；测试 `test_bug_audit_routes_require_review_role_and_return_redacted_projection` 及 CLI 输入校验测试。

---

### Task 1: Bug 审计台账与状态模型

**Files:**
- Create: `outputs/SQA_MVP/bug_audit.py`
- Create: `tests/test_bug_audit.py`

**Interfaces:**
- Consumes: `Store.connection(write=False|True)`、`store.encode`、`store.need`、`store.Problem`。
- Produces: `BugAudit(store)`、`begin_run(run_key, window, scope, rule_version) -> dict`、`record_evidence(run_id, event_id, payload) -> dict`、`record_findings(run_id, event_id, payload) -> dict`、`record_writeback(run_id, event_id, payload) -> dict`、`record_notification(run_id, event_id, payload) -> dict`、`complete_run(run_id, event_id, payload) -> dict`、`list_runs(limit=50) -> list[dict]`、`get_run(run_id) -> dict`。
- 数据表使用 `bug_audit_runs`、`bug_audit_items`、`bug_audit_fields`、`bug_audit_events` 专属前缀；在 `bug_audit.py` 初始化，不改 `store.py`。

- [ ] **Step 1: 编写失败测试**，覆盖上海时区窗口、候选过滤/去重、同 run 恢复、事件重放幂等、证据 partial、字段级 ready/null、预检冲突、未知写入结果及回读状态。
- [ ] **Step 2: 运行 `python -m unittest tests.test_bug_audit -v`**，确认缺少 `BugAudit` 实现导致失败。
- [ ] **Step 3: 实现 `BugAudit` 与专属表初始化**；所有状态更新和事件登记使用同一 `Store.connection(write=True)` 事务，按唯一键及事件内容哈希去重。
- [ ] **Step 4: 扩充边界测试**，确认不完整 History 将运行标记 partial，成功字段不会被 pending 字段覆盖，冲突不进入写入完成态。
- [ ] **Step 5: 运行 `python -m unittest tests.test_bug_audit -v`**，确认通过且仅创建隔离临时库。

### Task 2: 本机 Bug Audit CLI 与 JSON schema

**Files:**
- Create: `outputs/SQA_MVP/bug_audit_cli.py`
- Modify: `tests/test_bug_audit.py`

**Interfaces:**
- Consumes: Task 1 的 `BugAudit` 接口。
- Produces: `python outputs/SQA_MVP/bug_audit_cli.py --data-dir DIR begin --input FILE` 以及 `record-evidence`、`record-findings`、`record-writeback`、`record-notification`、`complete` 子命令；统一要求 UTF-8 JSON `schema_version: 1`，写阶段命令要求 `run_id`、`event_id`。

- [ ] **Step 1: 编写 CLI 测试**，验证所有子命令调用目标服务方法、JSON 输出稳定、无效 schema/缺失 ID/未知字段/非法拟值返回非零码且错误输出不含输入敏感内容。
- [ ] **Step 2: 运行 `python -m unittest tests.test_bug_audit.TestBugAuditCli -v`**，确认命令入口尚不存在而失败。
- [ ] **Step 3: 实现 CLI 参数解析和输入校验**；`--data-dir` 必填，`--input` 读取 UTF-8 JSON，命令不提供凭据选项，TP 字段名用固定白名单。
- [ ] **Step 4: 增加恢复回放测试**，依次重复执行同一输入文件，确认 run/event/item/field 数量不变且 stdout 返回既有状态。
- [ ] **Step 5: 运行 `python -m unittest tests.test_bug_audit -v`**，确认 CLI 与模型测试通过。

### Task 3: Bug 审计只读 API 与权限

**Files:**
- Modify: `outputs/SQA_MVP/app.py`（仅 `PilotServer` BugAudit 初始化及 `Handler` GET 专属路由分支）
- Modify: `tests/test_bug_audit.py`

**Interfaces:**
- Consumes: `BugAudit.list_runs(limit)` 与 `BugAudit.get_run(run_id)`。
- Produces: 登录态 `GET /api/bug-audit/runs?limit=N` 和 `GET /api/bug-audit/runs/{id}`；仅 `owner`、`reviewer` 可访问，响应为去敏投影；不增加 HTTP 写入接口。

- [ ] **Step 1: 编写 API 测试**，覆盖未登录 401、member 403、owner/reviewer 200、非法 ID 404、limit 上限及不含原始评论/凭据/本地路径的投影。
- [ ] **Step 2: 运行 `python -m unittest tests.test_bug_audit.TestBugAuditApi -v`**，确认路由未注册而失败。
- [ ] **Step 3: 在 `PilotServer` 初始化 `BugAudit(store)`，并在登录后 GET 路由中加入 reviewer 权限检查和两个读取路径；POST mutate 路径保持原样。
- [ ] **Step 4: 运行 API 权限和脱敏测试**，确认无写路由可调用且既有会话、Host 检查生效。
- [ ] **Step 5: 运行 `python -m unittest tests.test_bug_audit -v`**，确认本任务测试通过。

### Task 4: Bug 审计页面的每日运行只读视图

**Files:**
- Modify: `outputs/SQA_MVP/web/app.js`（仅 `showBugs()` 与 Bug 页面专属事件/视图）
- Modify: `outputs/SQA_MVP/web/style.css`（仅必要的 Bug 审计样式）
- Modify: `tests/test_bug_audit.py`（静态路由/页面呈现验收辅助测试，如项目现有验收方式适用）

**Interfaces:**
- Consumes: Task 3 的两个 API 响应；保留现有 `api()`、`tag()`、`table()`、`esc()` 等页面辅助方法。
- Produces: Bug 审计页内“当前快照 / 每日审计运行”切换；每日运行列表显示窗口、范围完整性、阶段状态和计数；详情展示 Bug、History 证据定位及三个字段各自 ready/冲突/写入/回读状态。

- [ ] **Step 1: 明确页面状态验收**，用 fixture 确认空列表、partial/failed、正常完成、字段级部分成功均有对应断言。
- [ ] **Step 2: 实现专属视图**，所有动态数据经 `esc()` 编码；运行详情只读，不出现 TP/飞书写入触发控件，不修改导航或其他页面。
- [ ] **Step 3: 运行 `python -X utf8 -m unittest tests.test_bug_audit -v`、`node --check outputs/SQA_MVP/web/app.js` 和 Bug Audit HTTP 集成测试**，验证页面调用与权限；尝试既有隔离验收命令，若本机样本配置不存在则记录环境限制，不读取正式数据库或替代样本。

### Task 5: 阶段 A 端到端离线验收与使用说明

**Files:**
- Create: `tests/fixtures/bug_audit/`（脱敏模拟阶段 JSON）
- Modify: `outputs/SQA_MVP/本机使用与验收.md`（只增加 Bug 审计专属操作说明）
- Modify: `docs/superpowers/specs/2026-11-24-bug-audit-daily-workflow-design.md`（阶段状态及实施偏差，如有）

**Interfaces:**
- Consumes: Tasks 1–4。
- Produces: 可重复的纯离线验收命令、CLI 示例 schema 和阶段 A 验收记录；不包含真实 Bug 原文、凭据或绝对路径。

- [ ] **Step 1: 准备模拟输入集**，覆盖有效候选、Add、其他字段变动、同值更新、缺失 History、三字段 mixed-ready、冲突、未知结果、通知部分回执。
- [ ] **Step 2: 在临时数据目录执行完整 CLI 链路两次**，断言第二次恢复原 run，计数不变，已成功外部回执不触发任何外部操作。
- [ ] **Step 3: 执行 `python -m unittest tests.test_bug_audit -v` 与既有隔离验收命令**，记录退出码和通过结果；默认测试不得访问网络。
- [ ] **Step 4: 更新中文操作说明**，列明命令顺序、错误恢复方法、台账查看入口、数据目录及阶段 A 不执行外部动作的边界。

### Task 6: 更新既有 Codex 自动化以登记阶段结果（阶段 B）

**Files:**
- Modify: 现有 Codex automation `bug-2` 的提示词（使用 Codex Automations 工具，不新建 automation）
- Modify: `outputs/SQA_MVP/本机使用与验收.md`（仅增补阶段 B 实际验收记录）

**Interfaces:**
- Consumes: 阶段 A 已通过的 CLI、JSON schema 和用户现有 automation `bug-2`。
- Produces: 原自动化在现有 TP/飞书步骤前后按阶段调用本机 CLI，登记候选、审计、逐字段写入/回读、通知及发送后回读回执；原调度与外部流程不变。

- [ ] **Step 1: 在阶段 A 全部离线验收通过后读取并备份现有 automation 配置**，核实 ID `bug-2`、每日北京时间 19:30、范围、工具身份和目标未变。
- [ ] **Step 2: 仅向现有提示词追加 CLI 登记与恢复步骤**，加入运行目录/临时 JSON 清理要求及凭据不落地规则，不改写 TP/飞书动作语义。
- [ ] **Step 3: 用模拟回放验证 automation 顺序**，确保登记失败不会导致重复 TP/飞书副作用，未知写/发结果仅回读后登记。
- [ ] **Step 4: 等待后续真实日常运行并核对各阶段台账与外部回执**；不手动触发 TP/飞书 live 流程来代替每日运行验收。更新验收记录，分别标明已验证和待后续运行验证项。

## 自审记录

- **规格覆盖：** 运行台账/幂等、候选窗口与 History 规则、字段级结论及冲突、写回/通知回执、只读 API/UI、权限脱敏、分阶段自动化及离线/live 边界均已映射到任务。
- **步骤粒度：** 每个实现任务先定义测试和接口，再实现并执行对应验证；自动化修改与阶段 A 离线验收分开。
- **接口一致性：** API/UI 共用 `BugAudit.list_runs()` / `get_run()`；CLI 写入入口与阶段化服务方法一一对应。
- **范围检查：** 未安排改动 `sync.py`、`imports.py`、通用 Store schema、其他页面、导航或第二个定时任务。
- **剩余不确定项：** 运行与字段内部列由实现时按设计落定；HTTP 详情投影不得暴露原始敏感证据。自动化能力调用方式需在阶段 B 实施时通过 Codex Automation 工具核实。

## 执行调整

截至 2026-11-24：Task 1–5 的台账、CLI、只读 API/UI 代码及离线验收已实施；Task 4 页面仅完成 HTTP、源码契约和语法检查，没有浏览器视觉/交互验收。Task 6 已完成既有自动化提示词追加和配置回读核对，真实每日运行的台账与外部回执对账仍待后续执行。各任务上方的步骤保留原计划，实际结果以本节和执行台账为准。

- 最终代码审查代理因配额不足失败，实施者按状态流和调用链进行了自审；没有独立代码审查通过的结论。
- 自审复现并修正：ready 字段缺回执时整批误报 verified、重复结论重置既有回执、已有副作用的结论可被覆盖、恢复后汇总状态滞后、同日重跑截止变化导致无法恢复。每项均新增失败回归测试后修正。
- 同时通过失败回归测试修正每日窗口非零点也被接受、候选增补后旧审计完成状态未失效两项边界；最终发现式测试 26/26 通过，JavaScript 语法与差异空白检查通过。
- 隔离预览首页及静态资源返回 HTTP 200；重启操作被执行策略拦截，所以原 8777 进程尚未加载最终 Python 修正。没有尝试绕过策略；手工重启入口已记入 Bug 审计使用说明。
- 后续用户人工登录后，已补做合成样例的桌面/窄屏目视、快照空状态、每日运行与详情交互检查；修正每日运行长证据文本造成的整页溢出，并完成同一 DOM 尺寸断言的失败→通过验证。当前完整测试为 Python 3.13 上 27/27 通过，浏览器 warning/error 日志为空。此前的浏览器验收缺口已部分消除；有数据的历史快照、额外失败/冲突样例目视、旧预览后端重启及真实每日对账仍未验证。
- 用户进一步授权重启后，8767/8777 均已备份并重启为 Python 3.13，健康与完整性检查通过，旧进程限制已解除。11 月 20 日真实检查点在临时库完成 125 单/375 字段/153 条字段变更证据的完整幂等回放，原消息和附件在线核验一致；没有向真实 TP/飞书发起新副作用，也未把历史回放混入业务台账。四项真实边界先用失败回归测试复现再修正，完整测试现为 31/31。原自动化已加入本机登记后的逐项对账摘要；今日 19:30 首次新运行仍须实际触发后核对。

- 由于本地 `work/pilot-sources.json` 缺失，既有跨模块隔离验收无法启动；Bug Audit 自身使用独立合成 fixtures、临时数据库、真实本机 HTTP 请求和 JavaScript 语法检查完成验证。正式数据库未参与测试。
- 页面验收采用 API 权限集成测试、静态 DOM 契约断言与 `node --check`；未通过 UI 自动化登录本机 Web 应用，以遵守 `computer-use/SKILL.md` 的“Do not automate user authentication dialogs”限制。成本是本轮没有真实浏览器视觉截图和交互录屏。
