# Bug 审计独立 Worker

仓库已包含审计执行流程，部署机不需要安装 Codex、Lark CLI 或个人技能。Worker 使用 TP 原生接口、公司模型和飞书机器人应用；Web 读取同一 SQLite 台账。其他独立功能模块不参与执行。

## 从干净克隆启动

需要 Python 3.11+。以下命令在仓库根目录运行；Windows 可将 `python` 替换为 `py -3.13`，或使用虚拟环境解释器的完整路径。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r outputs/SQA_MVP/requirements-bug-audit-worker.txt
.venv/Scripts/python.exe outputs/SQA_MVP/bug_audit_worker_cli.py doctor
.venv/Scripts/python.exe outputs/SQA_MVP/bug_audit_worker_cli.py demo --data-dir work/bug-worker-demo
.venv/Scripts/python.exe outputs/SQA_MVP/app.py --data-dir work/bug-worker-demo serve --port 8778
```

Linux/macOS 使用 `.venv/bin/python`。打开 `http://127.0.0.1:8778/`，在全新数据目录设置本机负责人账号，进入“Bug 审计 → 每日审计运行”。演示不生成用户、不替换已有账号。演示目录必须为空，或已有本工具的演示标记；禁止指定真实业务目录。

演示使用合成日期和资料，不联网、不读密钥，不产生真实 TP/飞书回执。页面和 CLI 显示 `demo`；字段保持未执行。演示用例通过不代表公司模型语义质量或真实系统接入已验收。

## 部署配置

复制 `bug_audit_worker/config.example.toml` 到 Git 忽略的 `work/bug-audit-worker/worker.toml`。所有相对路径以 TOML 所在目录为基准，与启动 cwd 无关。`data_dir` 必须指向 Web 使用的数据目录；`runs_dir` 保存检查点、报告和 CSV，应使用部署账号专用目录并限制访问。检查点会保存脱敏后的必要业务证据；不得公开或提交 Git。

| 配置 | 填写内容 |
| --- | --- |
| worker.profile | 部署配置的稳定名称；不要每天改变 |
| scope | 实际 project ID/名称、AssignedTeams ID/名称，逐项对应；查询所有状态和创建年份 |
| tp | HTTPS 域名、当前项目 Context ID、token 环境变量名称 |
| model | 公司 base URL、Responses 或 Chat Completions、模型名、结构化输出能力与预算 |
| feishu | 机器人应用 ID、secret 环境变量名称、机器人 open_id、目标群 ID/名称及真实提及人员 open_id/姓名 |
| transport/limits | 超时、响应上限、GET 有限重试、分页、批次和模型调用上限 |

本次用户确认的模型参数如下；密钥仅放入 Worker 进程环境：

```toml
[model]
base_url = "https://aiapi.suunto.cn/v1"
api_style = "responses"
name = "gpt-6.1-sol"
key_env = "BUG_AUDIT_MODEL_KEY"
structured_mode = "schema"
```

环境变量默认为 `BUG_AUDIT_TP_TOKEN`、`BUG_AUDIT_MODEL_KEY`、`BUG_AUDIT_FEISHU_SECRET`。使用部署机的环境或密钥管理机制注入；不要将值放到命令行、TOML、聊天或 Git 中。桌面终端中设置的环境不会自动传给操作系统调度服务，需要在调度运行账号的启动环境中配置。

也可显式提供 UTF-8 环境文件，支持 `NAME=value`、单/双引号及 `export NAME=value`。CLI 只加载 TOML 引用的凭据变量，不执行 shell、不展开变量、不改变代理或系统路径；默认不搜索任何 `.env`。可多次指定，后一个文件覆盖前一个文件的同名值。文件解析全部成功后才更新本次进程环境，错误不会回显密钥值。

```powershell
python outputs/SQA_MVP/bug_audit_worker_cli.py doctor --config /部署配置/worker.toml --env-file /部署配置/worker.env --live
```

若已有 TP 文件使用 `TP_ACCESS_TOKEN`，将 `tp.token_env` 填为这个变量名即可；模型/飞书可另放一份专用文件，再追加一个 `--env-file`。`*_env` 字段填写变量名，密钥值写在启动环境或环境文件中。`--env-file` 仅配合 `--live` 使用，离线命令不读取凭据；参数相对路径按启动 cwd 解析，调度建议使用绝对路径。

`structured_mode="json"` 仅用于已明确不支持服务端 schema 的网关，仍保留本地严格验证；程序不会自动换协议或公开模型服务。

## 自检与只读分析

```powershell
python outputs/SQA_MVP/bug_audit_worker_cli.py doctor --config work/bug-audit-worker/worker.toml
python outputs/SQA_MVP/bug_audit_worker_cli.py doctor --config work/bug-audit-worker/worker.toml --live
python outputs/SQA_MVP/bug_audit_worker_cli.py run --config work/bug-audit-worker/worker.toml --live
```

默认 doctor 只检查配置、Python 和 Asia/Shanghai 时区，不读取凭据、不联网。在线 doctor 检查凭据存在性、项目/团队/三个字段选项、机器人身份/群/成员读取权限，并向公司模型发送无真实业务资料的合成探针。认证取令牌和模型请求使用 POST，但不修改 TP、发送消息或上传文件。

在线 run 默认为 `dry_run`：按北京时间当天 00:00（含）至首次运行冻结时间（不含）采集与审计，生成台账和计划，不回填 TP、不发飞书。模型超时、拒绝、引用伪造、评论未全覆盖、输入或调用预算耗尽等情况保留 pending。原值/拟值/逐字段 ready 和阻塞原因可在 Web 检查。

Feature 索引过大或不完整时，该项保留 pending，其他语义检查继续覆盖全部正文和评论。旧单不提交无关 Feature 索引；已关联 Feature 在项目归属和非空描述均核实后，仅审查该实体。未关联 Feature 的完整目录必须适配部署模型的输入预算；不能用截断后的候选目录声称该项通过。逐项规则结论和证据引用保存在私有检查点供复核。

模型引用已知 Feature 候选但因缺少描述判为 pending 时，Worker 最多补取一次候选描述并复审；没有明确引用、描述为空或仍无法确认时继续 pending。仅影响文字审计的分类细化、已关联 Feature 的适用性、状态一致性，不连带阻塞没有对应下拉选项的字段；文字审计仍保留全部适用检查，不能因此写成“无异常”。

同一 profile/模式/日期/业务范围/规则复用同一 run_id 和首次截止时间，不因重跑扩大窗口。修改配置、规则或模型后不能强行恢复原记录。`report_directory` 指向该次运行的“报告.md”和 UTF-8 BOM CSV；checkpoint 及其哈希是恢复依据，不能手工编辑。

LF/CRLF 不改变规则和提示词身份，旧换行哈希只在内容及配置完全相同时兼容，保留原 run_id/outbox。实际规则变化时，已有当天 apply 只能恢复原记录，新版本执行从下一个受控生产日期开始；不会因同 profile 新规则再次发群。

## 授权执行与旧任务切换

首次真实执行前，先完成只读验收，并确认机器人具备群、成员、发送、消息读取、文件上传及附件读取权限。用户 OAuth 和浏览器登录态不替代机器人权限，Worker 不读取原 Lark CLI 账号。

生产默认跨北京时间自然日切换。先核对旧 Codex 任务当天回执，暂停旧任务并确认没有在途进程，再设置：

```toml
[worker]
allow_apply = true
legacy_quiesced = true
first_apply_date = "2026-11-25"
legacy_last_run_date = "2026-11-24"
```

上面仅为日期示例，部署者必须按实际切换情况填写。旧执行器当天已有副作用时，首版禁止同日接管；同一数据库内相同范围的其他 profile 已有 apply 时也禁止绕过去重。程序无法确认另一台不共享数据库的机器是否在写，生产仅支持一个活跃执行节点。

```powershell
python outputs/SQA_MVP/bug_audit_worker_cli.py run --config work/bug-audit-worker/worker.toml --live --apply
# 可保留已审阅只读计划的冻结窗口，但会重新取证和审计，使用独立 apply 记录：
python outputs/SQA_MVP/bug_audit_worker_cli.py run --config work/bug-audit-worker/worker.toml --live --apply --from-run 1
```

只写 Testing、Development 和 Bug quality audit 三字段。null/pending 不写，原值冲突不覆盖；写前复核业务资料与在线选项，变化会阻塞旧结论。TP 没有原子条件写入，紧邻预检与写后回读仍不能消除所有竞态；结果不符保持部分完成。待确认结论、冲突或未知回执不能显示整个真实流程完成。

TP 回填与飞书通知分别登记；通知失败不抹去已完成回填。摘要、上传和附件消息有独立持久意图，消息回读核对群、应用、正文、真实提及，附件下载后比对 SHA-256。

消息回读中的附件 Key 可能与上传 Key 不同；程序使用已核验消息的实际资源 Key 下载，仍要求原文件名与字节哈希匹配。Key 变化不能通过重复发送解决。

## 中断恢复与每日对账

```powershell
# dry_run 恢复：
python outputs/SQA_MVP/bug_audit_worker_cli.py resume --config work/bug-audit-worker/worker.toml --run-id 1 --live
# apply 恢复仍需显式写入开关：
python outputs/SQA_MVP/bug_audit_worker_cli.py resume --config work/bug-audit-worker/worker.toml --run-id 2 --live --apply
# 只读本地 / 在线对账：
python outputs/SQA_MVP/bug_audit_worker_cli.py reconcile --config work/bug-audit-worker/worker.toml --run-id 2
python outputs/SQA_MVP/bug_audit_worker_cli.py reconcile --config work/bug-audit-worker/worker.toml --run-id 2 --live
```

恢复核验配置、模式、窗口、文件哈希和意图，先回读未知结果，已核验副作用不重发。飞书明确拒绝的失败项可在显式 resume 中补发；摘要成功、附件失败时仅补失败部分。对账仅回读已有实体/消息和下载文件，不创建新副作用，也不重新调用业务审计模型。

回填阶段结束并冻结报告后，resume 仅处理通知，不再重做 TP 写回。若字段因网络预检失败而尚未尝试，需单列待处理清单、重新取证后制定恢复计划；网络恢复或次日正常运行不能被当作这些历史项已自动补齐。保留旧计划和回执，禁止删除台账绕过同日防重。

发送成功但响应丢失时，如果没有消息 ID，程序保留 unknown，即使已超过服务端 uuid 一小时去重窗口也不会盲发。人工找到消息后，可提供 JSON：

```json
{"2:feishu:summary":"om_真实消息ID"}
```

```powershell
python outputs/SQA_MVP/bug_audit_worker_cli.py reconcile --config work/bug-audit-worker/worker.toml --run-id 2 --live --receipts work/verified-message-ids.json
```

程序会在线核验这条消息的归属、正文与身份，不能靠手写回执直接标成功；之后 resume 只继续未完成部分。上传结果未知且缺 file_key 时，不能自动安全重传；保留未知供人工核对。TP 已明确拒绝或资料变化的阻塞项也不会因普通重跑被强行覆盖，应核对原值、权限和业务资料再处置。

退出码：0 为所选模式成功（只读只表示计划成功）；2 为配置/输入阻塞；3 为 partial/unknown；4 为执行异常。CLI 输出 JSON；Web 仍仅允许既有读取权限，没有新增网页写入操作。

## 备份与迁移

现有 Web 数据库备份会保留 Worker jobs、outbox 和每次尝试日志，已用恢复测试验证。但它不会自动收集 `runs_dir` 的外部文件。迁移前停止 Worker，同时备份数据库、完整 runs_dir 和不含密钥的部署配置；恢复到新目录后保持配置路径解析一致并重新注入密钥，再先做离线 reconcile。只恢复数据库会缺检查点，不能由此重新发送。

## 操作系统调度

`bug_audit_worker/schedule/run-worker.ps1` 和 `run-worker.sh` 是一次性启动示例，不安装任务，默认 `dry_run`；只有显式模式 `apply` 才附加写开关。配置用绝对路径，解释器固定为部署虚拟环境。操作系统调度设为北京时间每日 19:30，并设置不并发启动；Worker 还有同数据目录/业务范围的进程锁。

Windows 任务计划程序中，将触发器时区与北京时间对齐；程序使用 PowerShell，参数示例：

```text
-NoProfile -File "D:\部署目录\SQA-MVP\outputs\SQA_MVP\bug_audit_worker\schedule\run-worker.ps1" -ConfigPath "D:\部署配置\worker.toml" -Python "D:\部署目录\SQA-MVP\.venv\Scripts\python.exe" -Mode apply
```

使用环境文件时，也可将任务程序直接设为部署虚拟环境的 Python，参数用官方 CLI 的 `run --config ... --env-file ... --live --apply`；无需将密钥值写入任务参数。

POSIX 支持 `CRON_TZ` 的 cron 可配置如下；不支持该变量的实现需在调度器显式配置 Asia/Shanghai，不能直接使用主机默认时区：

```cron
CRON_TZ=Asia/Shanghai
30 19 * * * PYTHON=/opt/sqa/.venv/bin/python /bin/sh /opt/sqa/outputs/SQA_MVP/bug_audit_worker/schedule/run-worker.sh /etc/sqa/worker.toml apply
```

仓库调度示例不自动注册任务。本机已在用户授权下完成首笔真实回填、新机器人通知和 Windows 任务切换，详细结果与部分完成项见[生产验收记录](../../docs/superpowers/validation/2026-11-25-bug-audit-production-acceptance.md)。Windows 本机和模拟 HTTP 链路已验证；Linux/macOS × Python 3.11/3.13 已提供专属 CI 矩阵，需等待实际 CI 结果，不能视为已通过。

## 验证边界

离线验证覆盖干净副本、空 HOME、中文路径、换 cwd、无外部 CLI/个人凭据、完整模拟链、字段冲突、中断恢复、消息丢响应、附件下载哈希及旧台账兼容。运行：

```powershell
python -X utf8 -m unittest discover -s tests -p "test_bug_audit*.py" -v
node --check outputs/SQA_MVP/web/app.js
```

真实上线还需要：有效 TP token/Context/范围，飞书机器人应用 secret/成员读取等权限，公司模型 key，以及人工核对的代表性语义案例。在线 doctor、真实只读审计、语义质量对照和受控 apply 应分别记录证据；模拟请求和 schema 校验不代替这些验收。
