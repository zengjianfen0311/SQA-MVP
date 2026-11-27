"""一次性执行协调器；阶段检查点与副作用日志分别持久化。"""
import base64
import copy
import hashlib
import json
import os
import uuid
from datetime import date, datetime
from pathlib import Path

from bug_audit import BugAudit
from store import Store
from .config import ConfigError, require, canonical_url
from .history import SHANGHAI, timestamp
from .journal import Journal, JournalError, digest, encoded
from .model import ModelClient, redact
from .rules import RULE_VERSION, assemble_findings, build_facts, deterministic_checks, rules_digest
from .tp import TPClient
from .feishu import FeishuClient
from .reports import report_run
from .writeback import Writeback


class Worker:
    def __init__(self, config, adapters=None):
        self.config = config
        self.store = Store(config.data_dir)
        self.audit = BugAudit(self.store)
        self.journal = Journal(self.store)
        self.tp = (adapters or {}).get("tp") or TPClient(config)
        self.model = (adapters or {}).get("model") or ModelClient(config)
        self.feishu = (adapters or {}).get("feishu") or FeishuClient(config)
        self.demo = bool((adapters or {}).get("demo"))
        scope = config.section("scope")
        normalized = copy.deepcopy(config.data)
        for section in ("tp", "model", "feishu"): normalized[section]["base_url"] = canonical_url(normalized[section]["base_url"])
        self.scope_hash = digest({"origin": normalized["tp"]["base_url"], "project": scope["project_id"], "teams": sorted(scope["team_ids"])})
        self.config_hash = digest({"config": normalized, "rules": rules_digest(),
                                  "prompt": hashlib.sha256((Path(__file__).parent / "prompts/semantic-review.md").read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest()})
        # 只兼容内容完全相同的旧 LF/CRLF 哈希；实际文字或配置改变仍拒绝。
        rule_text = (Path(__file__).parent / "rules" / (RULE_VERSION + ".md")).read_text(encoding="utf-8-sig")
        prompt_text = (Path(__file__).parent / "prompts/semantic-review.md").read_text(encoding="utf-8-sig")
        rule_hashes = {hashlib.sha256(t.encode("utf-8")).hexdigest() for t in (rule_text, rule_text.replace("\n", "\r\n"))}
        prompt_hashes = {hashlib.sha256(t.encode("utf-8")).hexdigest() for t in (prompt_text, prompt_text.replace("\n", "\r\n"))}
        self.equivalent_config_hashes = {self.config_hash} | {digest({"config": normalized, "rules": r, "prompt": p}) for r in rule_hashes for p in prompt_hashes}

    def _apply_gate(self, window, run_id=None):
        cfg = self.config.data["worker"]
        require(cfg["allow_apply"] is True, "apply 需要配置 allow_apply=true")
        require(cfg["legacy_quiesced"] is True, "apply 前必须确认旧执行器已停止且无在途任务")
        try:
            first = date.fromisoformat(cfg["first_apply_date"])
            last = date.fromisoformat(cfg["legacy_last_run_date"]) if cfg["legacy_last_run_date"] else None
        except (ValueError, TypeError):
            raise ConfigError("首次 apply 日期或旧执行器最后运行日期无效") from None
        day = timestamp(window["start"]).date()
        require(day >= first and (last is None or day > last), "首次生产日期以前或旧执行器已运行当天禁止 apply")
        with self.store.connection() as db:
            rows = db.execute("SELECT r.*,j.mode,j.profile FROM bug_audit_runs r LEFT JOIN bug_audit_worker_jobs j ON j.run_id=r.id WHERE r.window_start=?", (window["start"],)).fetchall()
            for row in rows:
                if row["id"] == run_id: continue
                scope = json.loads(row["scope_json"])
                if row["mode"] is None:
                    project = scope.get("project", "")
                    same_project = (not project or project == self.config.data["scope"]["project_name"]
                                    or project.startswith(str(self.config.data["scope"]["project_id"]) + " "))
                    if same_project:
                        effects = db.execute("SELECT 1 FROM bug_audit_events WHERE run_id=? AND stage IN ('writeback','notification') LIMIT 1", (row["id"],)).fetchone()
                        require(effects is None, "旧执行器当天已有副作用记录，禁止同日切换 apply")
                    continue
                if scope.get("scope_hash") and scope["scope_hash"] != self.scope_hash:
                    # 旧 Worker 身份没有保存可逆 origin；同名同团队的旧 apply 保守阻塞。
                    if row["mode"] == "apply" and scope.get("project") == self.config.data["scope"]["project_name"] and set(scope.get("teams", [])) == set(self.config.data["scope"]["team_names"]):
                        raise ConfigError("同日存在旧身份的相同项目团队 apply，需先人工对账，禁止新建重复运行")
                    continue
                if not scope.get("scope_hash") and scope.get("project") not in (None, "", self.config.data["scope"]["project_name"]): continue
                if row["mode"] == "apply":
                    raise ConfigError("相同范围当天已有 apply，仅可恢复原运行；禁止以新 profile 或规则再次执行")

    def _save(self, run_id, mode, window, state, stage):
        # 内容寻址：文件已落地但 DB 更新前中断时，旧检查点仍完整可恢复。
        directory = self.config.runs_dir / str(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        def scrub(value):
            if isinstance(value, dict):
                for key in value: value[key] = scrub(value[key])
            elif isinstance(value, list):
                for index in range(len(value)): value[index] = scrub(value[index])
            elif isinstance(value, str): return redact(value)
            return value
        scrub(state)
        content = encoded(state).encode("utf-8")
        sha = hashlib.sha256(content).hexdigest()
        path = directory / (sha + ".json")
        if not path.exists():
            temporary = directory / (uuid.uuid4().hex + ".tmp")
            with temporary.open("xb") as handle:
                if os.name != "nt": os.chmod(temporary, 0o600)
                handle.write(content); handle.flush(); os.fsync(handle.fileno())
            temporary.replace(path)
        checkpoint = {"path": path.relative_to(self.config.runs_dir).as_posix(), "sha256": sha,
                      "model_calls": state.get("model_calls", 0)}
        stored_hash = self.config_hash
        try: previous = self.journal.job(run_id)
        except JournalError: previous = None
        if previous and previous["config_hash"] in self.equivalent_config_hashes and previous["profile"] == self.config.profile:
            stored_hash = previous["config_hash"]
        self.journal.save_job(run_id, self.config.profile, mode, window, stored_hash, stage, checkpoint)

    def _load(self, run_id):
        job = self.journal.job(run_id)
        if job["config_hash"] not in self.equivalent_config_hashes or job["profile"] != self.config.profile:
            raise JournalError("恢复配置、规则或模型已变化，不能覆盖原执行记录")
        path = (self.config.runs_dir / job["checkpoint"]["path"]).resolve()
        if not path.is_relative_to(self.config.runs_dir.resolve()): raise JournalError("检查点路径超出运行目录")
        try: content = path.read_bytes()
        except OSError: raise JournalError("检查点文件不可读") from None
        if hashlib.sha256(content).hexdigest() != job["checkpoint"]["sha256"]:
            raise JournalError("检查点哈希不匹配")
        try: state = json.loads(content)
        except ValueError: raise JournalError("检查点格式无效") from None
        return job, state

    def run(self, mode, window=None, source_run_id=None):
        require(mode in {"demo", "dry_run", "apply"}, "运行模式无效")
        require((mode == "demo") == self.demo, "demo 只能使用离线演示适配器")
        if source_run_id is not None:
            require(mode == "apply" and window is None, "只读计划只能作为独立 apply 的来源，不能另设窗口")
            source = self.audit.get_run(source_run_id)
            source_job = self.journal.job(source_run_id)
            require(source_job["mode"] == "dry_run" and source["scope"].get("scope_hash") == self.scope_hash, "来源须为同一业务范围的只读计划")
            window = source["window"]
        if window is None:
            end = datetime.now(SHANGHAI)
            window = {"start": end.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(), "end": end.isoformat()}
        start, end = timestamp(window["start"]), timestamp(window["end"])
        require(start.date() == end.date() and start < end and start.time().isoformat() == "00:00:00", "窗口须从同一北京时间日期零点开始")
        window = {"start": start.isoformat(), "end": end.isoformat()}
        with self.journal.lock(self.scope_hash):
            key = "worker:" + self.config.profile + ":" + mode + ":" + start.date().isoformat() + ":" + self.scope_hash[:24] + ":" + rules_digest()[:24]
            with self.store.connection() as db:
                matched = db.execute("SELECT id FROM bug_audit_runs WHERE run_key=?", (key,)).fetchone()
                if matched is None:
                    aliases = db.execute("""SELECT r.id,r.scope_json,j.config_hash FROM bug_audit_runs r
                        JOIN bug_audit_worker_jobs j ON j.run_id=r.id WHERE j.profile=? AND j.mode=?
                        AND r.window_start=? AND r.rule_version=?""", (self.config.profile,mode,window["start"],RULE_VERSION)).fetchall()
                    aliases = [row for row in aliases if row["config_hash"] in self.equivalent_config_hashes and json.loads(row["scope_json"]).get("scope_hash") == self.scope_hash]
                    require(len(aliases) <= 1, "同日存在多条等价运行，需先人工对账")
                    matched = aliases[0] if aliases else None
            if mode == "apply": self._apply_gate(window, matched["id"] if matched else None)
            if matched:
                try: self.journal.job(matched["id"])
                except JournalError: pass
                else: return self._continue(matched["id"])
            scope = self.config.data["scope"]
            run = self.audit.begin_run(key, window, {"project": scope["project_name"], "teams": scope["team_names"], "scope_hash": self.scope_hash}, RULE_VERSION)
            with self.store.connection() as db:
                exists = db.execute("SELECT 1 FROM bug_audit_worker_jobs WHERE run_id=?", (run["id"],)).fetchone()
            if not exists: self._save(run["id"], mode, run["window"], {"model_calls": 0, "findings": [], "source_run_id": source_run_id}, "collect")
            return self._continue(run["id"])

    def resume(self, run_id):
        with self.journal.lock(self.scope_hash):
            job, _ = self._load(run_id)
            if job["mode"] == "apply": self._apply_gate(job["window"], run_id)
            require((job["mode"] == "demo") == self.demo, "演示记录必须通过 demo 入口恢复")
            return self._continue(run_id, retry_failed=True)

    def _continue(self, run_id, retry_failed=False):
        job, state = self._load(run_id)
        mode, window = job["mode"], job["window"]
        if job["stage"] == "complete": return self._result(run_id, state)
        save = lambda stage: self._save(run_id, mode, window, state, stage)
        if "bundle" not in state:
            state["bundle"] = self.tp.collect(window)
            save("audit")
        bundle = state["bundle"]
        evidence = json.loads(encoded({"schema_version": 1, "scope_status": bundle["completeness"], "items": bundle["items"]}))
        self.audit.record_evidence(run_id, "worker-evidence:" + digest(evidence)[:32], evidence)
        done = {f["bug_id"] for f in state["findings"]}
        for item in bundle["items"]:
            if item["bug_id"] in done: continue
            facts = build_facts(item, window)
            features = {"items": bundle["features"], "details": bundle["feature_details"], "status": bundle["feature_status"]}
            self.model.calls = state["model_calls"]
            # 先预留预算，进程被终止也不能经 resume 绕开调用上限。
            state["model_calls"] += 1; save("audit")
            checks = self.model.review(item, facts, features)
            requested = sorted({fid for c in checks for fid in c.get("requested_feature_ids", [])})
            if requested:
                details = self.tp.feature_details(requested, bundle["features"])
                features["details"] = [d for d in features["details"] if d["id"] not in requested] + details
                self.model.calls = state["model_calls"]
                state["model_calls"] += 1; save("audit")
                checks = self.model.review(item, facts, features)
                item["review_features"] = details
            all_checks = deterministic_checks(item, facts) + checks
            fields = assemble_findings(item, all_checks, bundle["options"])
            state["findings"].append({"bug_id": item["bug_id"], "fields": fields, "checks": all_checks})
            save("audit")
        findings = json.loads(encoded({"schema_version": 1, "items": state["findings"]}))
        self.audit.record_findings(run_id, "worker-findings:" + digest(findings)[:32], findings)
        if mode == "apply":
            # report 已冻结意味着回填阶段结束；之后恢复仅处理通知，不重写已发摘要。
            if "report" not in state:
                save("writeback")
                Writeback(self.tp, self.journal, self.audit).execute(run_id, {"bundle": bundle, "findings": state["findings"]})
        if "report" not in state:
            report = report_run(bundle, state["findings"], self.journal.intents(run_id))
            report["markdown"] = "执行模式：" + mode + "\n" + report["markdown"]
            report["csv_base64"] = base64.b64encode(report.pop("csv_bytes")).decode("ascii")
            state["report"] = report
            save("notify" if mode == "apply" else "planned")
            for name, content in (("报告.md", report["markdown"].encode("utf-8")), (report["filename"], base64.b64decode(report["csv_base64"]))):
                target = self.config.runs_dir / str(run_id) / name
                temporary = target.with_name(uuid.uuid4().hex + ".tmp")
                with temporary.open("xb") as handle:
                    if os.name != "nt": os.chmod(temporary, 0o600)
                    handle.write(content); handle.flush(); os.fsync(handle.fileno())
                temporary.replace(target)
        if mode == "apply":
            self.feishu.recover(run_id, self.journal)
            report = dict(state["report"])
            report["csv_bytes"] = base64.b64decode(report.pop("csv_base64"))
            notification = self.feishu.notify(run_id, report, self.journal, retry_failed=retry_failed)
            self._notification(run_id, notification)
        else:
            self.audit.record_notification(run_id, "worker-no-notification", {"schema_version": 1, "status": "not_required"})
        self.audit.complete_run(run_id, "worker-complete:" + digest(self.journal.intents(run_id))[:24], {"schema_version": 1})
        result = self._result(run_id, state)
        save("complete" if result["exit_code"] == 0 else "partial")
        return self._result(run_id, state)

    def _notification(self, run_id, result):
        status = result["status"]
        if status == "blocked": status = "partial"
        payload = {"schema_version": 1, "status": status}
        receipts = result.get("receipts", [])
        if receipts:
            payload.update(receipts[0])
            payload["attachment_status"] = "verified" if status == "verified" and len(receipts) == 2 else "unknown"
        self.audit.record_notification(run_id, "worker-notification:" + digest(payload)[:32], payload)

    def _result(self, run_id, state, extra_issues=None):
        run = self.audit.get_run(run_id)
        job = self.journal.job(run_id)
        fields = [f for item in run["items"] for f in item["fields"]]
        planned = (run["collection_status"] == "complete" and run["audit_status"] == "complete"
                   and len(fields) == 3 * len(run["items"]) and all(f["ready"] for f in fields) and bool(state.get("report")))
        success = run["status"] == "complete" if job["mode"] == "apply" else planned
        if extra_issues: success = False
        return {"run_id": run_id, "profile": job["profile"], "mode": job["mode"], "window": job["window"],
                "status": "complete" if success and job["mode"] == "apply" else "planned" if success else "partial",
                "exit_code": 0 if success else 3, "stage": job["stage"], "issues": extra_issues or [],
                "counts": {"bugs": len(run["items"]), "fields": len(fields), "pending": sum(not f["ready"] for f in fields),
                           "verified": sum(f["status"] == "verified" for f in fields)}, "model_calls": state.get("model_calls", 0),
                "report_directory": str(self.config.runs_dir / str(run_id))}

    def reconcile(self, run_id, online=False, receipts=None):
        require(not receipts or online, "人工回执必须在线核验")
        with self.journal.lock(self.scope_hash):
            job, state = self._load(run_id)
            issues = []
            if online and job["mode"] == "apply":
                Writeback(self.tp, self.journal, self.audit).recover(run_id)
                delivery = self.feishu.recover(run_id, self.journal, receipts)
                self._notification(run_id, delivery)
                if delivery["status"] != "verified": issues.append("通知消息或附件未全部通过在线回读")
                self.audit.complete_run(run_id, "worker-reconcile:" + digest(self.journal.intents(run_id))[:24], {"schema_version": 1})
            return self._result(run_id, state, issues)
