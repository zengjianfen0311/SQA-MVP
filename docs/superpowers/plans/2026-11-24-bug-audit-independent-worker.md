# Bug 审计独立 Worker 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans。用户已批准设计、选择 Native 并要求继续，按本计划逐项测试和实施。

**Goal:** 无 Codex、Lark CLI 和个人技能的干净克隆可以运行演示；配置公司模型、TP、飞书后由独立 Worker 完成真实审计与恢复对账。

**Architecture:** Bug 专属 Python 包负责配置、HTTP 适配器、规则、公司模型、协调器与持久化 outbox，复用 BugAudit 台账；Web 仅增加执行来源/模式展示。默认离线/只读，外部副作用由显式 apply 运行执行。

**Tech Stack:** Python 3.11+ 标准库、SQLite、TOML、tzdata 2026.6（本机 Python 3.13 基线已安装并验证）、unittest、原生 JavaScript。

**Spec:** [已批准设计](../specs/2026-11-24-bug-audit-independent-worker-design.md)

**执行状态（2026-11-25）:** Tasks 1–10 的实现、离线、独立审查修复及真实只读验证完成，最新干净 venv 119/119 通过。用户随后授权真实执行：首笔 TP 回填回读通过，新“肖强的飞书助手”摘要与 CSV 交付核验通过；旧 bug-2 已暂停，新 Windows 任务已启用，首次自动触发定于 11 月 26 日 19:30，手工触发复用原记录且未重复副作用。生产 run_id=2 的 30 字段中 1 verified、18 未尝试、11 pending，整单保持 partial；不是全量回填完成。人工语义金标、未完成字段恢复、自然定时触发及跨平台 CI 仍待验收。见[只读验证](../validation/2026-11-24-bug-audit-independent-worker.md)和[生产验收](../validation/2026-11-25-bug-audit-production-acceptance.md)。

## Global Constraints

- 只改 Bug 审计专属代码、测试、配置、依赖、CI 和文档；不改通用 store.py/imports.py/sync.py 或其他页面。
- Web 不持有任何外部凭据，不新增 TP/飞书写路由或按钮。
- 不读取 `.codex`、`.agents`、历史审计目录或自动发现个人 `.env` 作为运行依赖；凭据从指定环境变量或用户显式传入的环境文件载入本次进程。
- 默认 doctor/demo 不联网，run 需显式 --live，真实回填及通知需 --apply 和配置 allow_apply 同时成立。
- Asia/Shanghai 左闭右开冻结窗口保留完整精度；只纳入 State/Severity 真实变化，保留全部命中证据。
- 只有三个审计字段可写，pending/null 不写；原值冲突不覆盖；未知结果先回读；多选字段忽略选项顺序，文字字段精确比较。
- 每次外部副作用前持久化 prepared/in_flight；未知发送不能盲重试；不要在长 SQLite 事务内调用网络。
- 所有默认测试隔离且不读取真实凭据；现有基线 32 项通过（提交 68a0479）。
- 不自动暂停旧任务、创建生产调度或执行今日重复回填/通知；生产切换默认跨北京时间自然日。

## Review Focus

- 伪造模型引用、部分评论覆盖与上下文超限不能变成 ready；Task 5 的语义负例覆盖。
- 分页返回重复页、未知 History 旧值、截断/乱序记录必须明确 partial；Task 3 的获取契约测试覆盖。
- 写入/发送成功后、本地登记前进程中断不能重发；Tasks 2/6/7/9 通过真实模拟 HTTP 请求计数覆盖。
- 配置相对路径、中文空格路径、空 HOME、跨协议重定向与错误消息不能泄露凭据；Tasks 1/10 覆盖。
- dry-run/apply 身份隔离及旧执行器同日副作用不能被新 profile 绕过；Tasks 8/10 覆盖。

## 文件与接口约定

新增专属包 `outputs/SQA_MVP/bug_audit_worker/`，模块为 config、transport、journal、tp、history、rules、model、writeback、feishu、reports、engine、cli、demo。外部入口 `outputs/SQA_MVP/bug_audit_worker_cli.py`。允许修改 `bug_audit.py` 的专属 schema/查询投影和 `web/app.js` 的 Bug 页面；保留原 CLI。

统一领域数据用 JSON 字典：Bundle 包含 window/scope/candidates/items/completeness/options/features；item 含 bug_id/snapshot/history/comments/relations/original/evidence。模型返回 checks（rule_id/status/reason/evidence_refs）与 coverage；规则汇总产生现有三字段 findings。输出对象显式区分 status、counts、mode、run_id。

HTTPClient.request(method,path,query=None,headers=None,body=None) 返回 Response(status,headers,body)，json 方法解析有界 UTF-8 JSON；每客户端固定 base URL，默认 HTTPS、禁重定向，GET 有限重试，写请求永不由 transport 自动重发。失败返回无凭据 TransportError(category,status,uncertain)。模拟服务使用显式 loopback HTTP 测试配置，不放宽远端 TLS。

### Task 1：可移植配置、环境自检与 HTTP 边界

**Files:** 新增 config.py、transport.py、__init__.py、config.example.toml、requirements-bug-audit-worker.txt；测试 tests/test_bug_audit_worker_config.py、tests/test_bug_audit_worker_transport.py。

**Interfaces:** load_config(path)->Config（配置目录相对路径、scope/profile/预算、环境变量名）；Config.secret(section)->str 仅在线适配器调用；doctor(config,online=False)->dict；HTTPClient 与 Response 如上。

- [x] 先写失败测试：换 cwd/中文路径、未知配置、空凭据、隐式个人环境依赖、禁止 URL 凭据、分页/输入上限、自检缺时区数据；本地 HTTP 服务验证重定向拒绝、GET 重试有界、POST 超时只发一次、超大响应和错误脱敏。
- [x] 运行 `py -3.13 -X utf8 -m unittest tests.test_bug_audit_worker_config tests.test_bug_audit_worker_transport -v`，预期先因模块不存在失败。
- [x] 实现配置和 HTTP；配置仅包含环境变量名，示例域名使用 example.invalid，依赖锁定 tzdata==2026.6。
- [x] 同命令预期全部通过；记录结果，仅提交本任务文件。

### Task 2：运行检查点、互斥与 outbox

**Files:** journal.py、tests/test_bug_audit_worker_journal.py。

**Interfaces:** Journal(store)；lock(scope_key) 上下文；save_job(run_id,profile,mode,window,config_hash,stage,checkpoint)；job(run_id)；prepare(run_id,key,kind,payload_hash,details)；inflight(key)；settle(key,status,receipt)；intents(run_id)。表以 bug_audit_worker_ 开头。

- [x] 先写失败测试：同 key 不同内容拒绝、跨进程互斥、进程退出锁释放、持久 in_flight 恢复、verified 不降级、写回执后登记失败可恢复，事务回滚和不保存凭据。
- [x] 运行 `py -3.13 -X utf8 -m unittest tests.test_bug_audit_worker_journal -v`，预期先失败。
- [x] 使用同一 SQLite 的短事务和操作系统文件锁（Windows msvcrt/POSIX flock）；不以过期时间强行抢占仍活跃进程的锁。意图只保存必要字段/哈希/回执，模型原始评论不入 outbox。
- [x] 重跑预期通过；记录并提交本任务文件。

### Task 3：TP 有界取证与 History 筛选

**Files:** tp.py、history.py、tests/test_bug_audit_worker_tp.py；合成数据 tests/fixtures/bug_audit_worker/tp/。

**Interfaces:** TPClient(config,http) 的 metadata()/collect(window)->Bundle、refresh(ids)->Bundle、read_fields(ids)；真实写入接口由 Task 6 使用独立方法，不由 collect 执行。select_history(candidates,histories,window)->(items,unresolved)。

- [x] 先测 v2 items/next 分页计数、重复页/缺页、三组 AssignedTeams、窗口前基线、Add/同值/其他字段排除、同 History 双字段、微秒截止、评论批量/Relations 类型与方向/选项元数据及必要 Feature 获取。
- [x] 运行 `py -3.13 -X utf8 -m unittest tests.test_bug_audit_worker_tp -v`，预期先失败。
- [x] 实现 native HTTP GET，仅本流程所需 Bugs/Projects/Teams/Features/Comments/BugHistories/Context。Context ID 是配置项，不能读取个人 metadata-baseline.json。全部批次校验所属 ID，失败保留 partial，不擅自查询全库。
- [x] 模拟服务断言 URL 过滤、投影和请求数量；预期通过后记录并提交。

### Task 4：版本化规则与确定性事实

**Files:** rules.py、rules/2026-11-10.md、tests/test_bug_audit_worker_rules.py、脱敏合成语义案例。

**Interfaces:** build_facts(item,window)->dict；deterministic_checks(item,facts)->list；assemble_findings(item,checks,options)->list（三字段原值/拟值/ready/reason）；rules_digest()->str。

- [x] 先测旧单临界日期、周末跨越与恰好 48/72 小时、首次 Severity 真修改、Relations General 未知、REG Yes 字段要求、旧 Developer 规则不再适用、多选 OK 排他及字段各自 pending。
- [x] 运行 `py -3.13 -X utf8 -m unittest tests.test_bug_audit_worker_rules -v`，预期先失败。
- [x] 将已批准纯规则迁入仓库并记录规则版本/哈希，不导入个人路径；文字规则保持短分类，过去已纠正问题与固定首次响应结果分别处理。
- [x] 预期用例全部通过；记录并提交。

### Task 5：公司模型适配、证据引用与覆盖校验

**Files:** model.py、prompts/semantic-review.md、tests/test_bug_audit_worker_model.py。

**Interfaces:** ModelClient(config,http).review(item,facts,features)->checks；probe()->dict（只合成输入）；validate_review(response,evidence)->checks。支持显式 chat_completions/responses 与严格 schema/显式 JSON 兼容模式。

- [x] 先测两种协议的真实模拟请求、拒绝/截断/无效 JSON、伪造 ID、缺覆盖、无引用通过、无效选项、不确定/恶意指令、预算超限及业务信息脱敏；证明字段 pending 而非默认 OK。
- [x] 运行 `py -3.13 -X utf8 -m unittest tests.test_bug_audit_worker_model -v`，预期先失败。
- [x] 实现无工具调用的独立模型请求，按 rule_id 和已知证据引用验证。每份有效评论须覆盖；首版上下文超限明确 pending，不静默截断；分段能力只在测试能证明覆盖后启用。
- [x] 预期通过；模型调用日志只存用量/摘要，不存密钥、原始大段资料；记录并提交。

### Task 6：TP 三字段写回及未知结果恢复

**Files:** writeback.py、tests/test_bug_audit_worker_writeback.py。

**Interfaces:** Writeback(tp,journal,audit).execute(run_id,plan)->dict；recover(run_id)->dict；读取 Task 3 Bundle 和 Task 4 findings，仅提交白名单 CustomFields。

- [x] 先测 ready/null 独立、原值并发变化、资料指纹变化、允许字段外拒绝、原值相同无 POST、POST 成功与丢响应、回读不匹配、其他业务字段保留、verified 重放不增加请求。
- [x] 运行 `py -3.13 -X utf8 -m unittest tests.test_bug_audit_worker_writeback -v`，预期先失败。
- [x] 写前 refresh 同步校验主体/评论/Relations/选项指纹；变化则阻塞受影响结论，不能以旧计划覆盖。请求前 prepare/inflight，成功/未知后都先回读，再登记 journal 和 BugAudit；故障后可从已保存回执补台账。
- [x] 模拟 HTTP 计数及实际 payload 断言通过；记录并提交。

### Task 7：飞书机器人通知与附件完整性

**Files:** feishu.py、reports.py、tests/test_bug_audit_worker_feishu.py。

**Interfaces:** FeishuClient(config,http).probe()/notify(run_id,report,journal)/recover(run_id,journal)；report_run(bundle,findings,receipts)->(markdown,csv_bytes,summary)。

- [x] 先测机器人认证、群/成员缺权限 blocked、身份/目标不符、真实 at 标签、UTF-8 与 CSV 公式防护、multipart 字节、uuid、发送后读取与下载哈希、摘要成功附件失败、响应丢失与过一小时恢复不能重发。
- [x] 运行 `py -3.13 -X utf8 -m unittest tests.test_bug_audit_worker_feishu -v`，预期先失败。
- [x] native 飞书 HTTP，无 CLI/OAuth 隐式依赖；摘要、附件分别持久化，未知结果保留 unknown，已核验不重发。缺通知权限不伪装 TP 步骤失败或整单闭环。
- [x] 模拟服务验证实际消息、身份和字节哈希；通过后记录并提交。

### Task 8：完整协调器、CLI、演示与只读模式

**Files:** engine.py、cli.py、demo.py、bug_audit_worker_cli.py；tests/test_bug_audit_worker_engine.py、tests/test_bug_audit_worker_cli.py；demo fixtures。

**Interfaces:** Worker(config,adapters=None).run(mode,window=None)/resume(run_id)/reconcile(run_id,online=False)；入口 doctor/demo/run/resume/reconcile。run 需 --live；apply 还需 --apply 与 allow_apply，且日期达到配置 first_apply_date。

- [x] 先测合成完整链、冻结窗口重跑、profile/模式 run_key 隔离、dry-run 零 TP/飞书写请求、apply 关闭时拒绝、阶段失败、逐项对账和重放、陈旧计划必须新执行记录与实时预检。
- [x] 运行 `py -3.13 -X utf8 -m unittest tests.test_bug_audit_worker_engine tests.test_bug_audit_worker_cli -v`，预期先失败。
- [x] 协调器在锁内按阶段调用适配器与现有 BugAudit；检查点采用写临时文件后替换、内容哈希和专属表关联，恢复核验哈希。退出码固定为 0（所选模式成功）、2（配置/权限/输入阻塞）、3（partial/unknown）、4（执行失败）。demo 不读任何真实凭据，不覆盖非演示目录。
- [x] 演示与所有失败恢复用例通过，产出可供 Web 读取的台账；记录并提交。

### Task 9：台账兼容升级与 Bug 页面执行模式

**Files:** 仅修改 bug_audit.py 的 Bug 专属 schema/投影、web/app.js 的 showBugs；测试 tests/test_bug_audit_worker_projection.py，保留 tests/test_bug_audit.py。

**Interfaces:** list_runs/get_run 增加 worker 元信息（producer/mode/profile/stage），旧运行默认 legacy，不暴露 endpoint 凭据、原评论或绝对路径；现有 API 权限和只读路由不变。

- [x] 先测旧库升级、旧 CLI 不变、空 worker 表、各执行模式 badge、未知/恢复阶段、权限投影以及备份恢复后的 Worker 日志完整。
- [x] 运行 `py -3.13 -X utf8 -m unittest tests.test_bug_audit tests.test_bug_audit_worker_projection -v`，预期新增投影测试先失败。
- [x] worker 专属结构可选关联，页面只显示模式和阶段；demo/只读不能显示真实写回已验证。仅 showBugs 内修改展示。
- [x] 上述测试与 `node --check outputs/SQA_MVP/web/app.js` 通过；记录并提交。

### Task 10：干净环境、跨平台 CI、部署与受控切换

**Files:** tests/test_bug_audit_worker_portability.py、.github/workflows/bug-audit-worker.yml、Bug审计Worker使用说明.md、Windows/POSIX 调度示例和本机配置模板（均 Bug 专属）。

**Interfaces:** 空 HOME/USERPROFILE、不同 cwd、无 Codex/Lark CLI 的复制/克隆测试环境可完成安装、doctor、demo、查询台账、恢复与 reconcile；调度示例仅调用一次性命令，不自动注册。

- [x] 先编写干净环境测试：扫描运行导入/子进程调用无个人依赖，真实子进程在中文空格路径运行；空数据库不覆盖账号，旧配置不被读取；配置首个 apply 日期以前不得发外部写请求；旧执行器已有当日回执时禁止同日生产切换。
- [x] 运行 `py -3.13 -X utf8 -m unittest tests.test_bug_audit_worker_portability -v`，预期缺入口/资料时失败。
- [x] 补全依赖/示例/运行说明、作用域和权限自检、未知状态恢复、两实例同数据目录及单活要求。CI 矩阵 Windows/Linux/macOS × Python 3.11/3.13，只运行 Bug 审计离线测试，不使用真实凭据；未执行的 CI 平台记为待验。
- [x] 运行 `py -3.13 -X utf8 -m unittest discover -s tests -p "test_bug_audit*.py" -v`、Node 语法及差异检查；干净环境 install/doctor/demo/reconcile 全部通过后独立整分支审查，修复重要项。
- [x] 部署参数已补齐，真实只读、首笔受控 TP 写入、新机器人摘要/CSV 和在线回执对账已验证；30 字段中的未完成项单独保留，不凭演示或首笔成功声称整单闭环。
- [x] 用户明确授权继续真实执行和调度切换；核实今日无旧副作用并停止旧任务后，今日受控验收，新日 26 日 19:30 开始自动调度。已注册、启用并手工触发验证防重，自然定时触发待验。

## 自审及执行约定

- Task 1 的 Config/HTTP 被所有适配器共享；Bundle/字段结构由 Task 3 定义，规则/模型/协调器采用同一结构。
- Task 2 outbox 为 Tasks 6/7 的防重依据；协调器恢复必须先回读、后补登记，不能以新 event_id 重新执行。
- 既有完整基线实际为 32 项，已在分支创建后重新运行通过；保持原功能与所有新测试同时通过。
- 所有实现任务先看到对应失败测试再写代码；每个任务完成时记录提交/测试/偏差到专属 progress.md。未拿到真实配置不阻断离线实现，但不把在线验收标完成。
- 用户批准设计、选择 Native 并要求继续；实施计划细化已批准范围，按 Native 连续执行。真实接入、调度切换均保持显式配置和既有授权边界。
