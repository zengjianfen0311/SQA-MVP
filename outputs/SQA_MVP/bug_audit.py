"""Bug-audit run ledger, isolated from the general import and sync paths."""

import hashlib
import json
import re
from datetime import datetime
from store import LOCAL_TZ, Problem, encode, need

SHANGHAI = LOCAL_TZ
AUDIT_FIELDS = {
    "Bug Standard Review of Testing",
    "Bug Standard Review of Development",
    "Bug quality audit",
}
CHANGE_FIELDS = {"State", "Severity"}
ITEM_STATUSES = {"written", "unknown", "failed", "skipped"}
EVIDENCE_KEYS = {"history", "comments", "attachments", "relations", "description", "discussion"}
EVIDENCE_STATUSES = {"complete", "partial", "failed", "not_requested"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS bug_audit_runs (
    id INTEGER PRIMARY KEY,
    run_key TEXT NOT NULL UNIQUE,
    rule_version TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    collection_status TEXT NOT NULL DEFAULT 'pending',
    audit_status TEXT NOT NULL DEFAULT 'pending',
    writeback_status TEXT NOT NULL DEFAULT 'pending',
    notification_status TEXT NOT NULL DEFAULT 'pending',
    status TEXT NOT NULL DEFAULT 'running',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created TEXT NOT NULL,
    changed TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bug_audit_items (
    run_id INTEGER NOT NULL REFERENCES bug_audit_runs(id) ON DELETE CASCADE,
    bug_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    history_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    inclusion_reason TEXT NOT NULL,
    PRIMARY KEY(run_id, bug_id)
);
CREATE TABLE IF NOT EXISTS bug_audit_fields (
    run_id INTEGER NOT NULL,
    bug_id TEXT NOT NULL,
    name TEXT NOT NULL,
    original_json TEXT,
    proposed_json TEXT,
    ready INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    preflight_json TEXT,
    conflict INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    readback_json TEXT,
    PRIMARY KEY(run_id, bug_id, name),
    FOREIGN KEY(run_id, bug_id) REFERENCES bug_audit_items(run_id, bug_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS bug_audit_events (
    run_id INTEGER NOT NULL REFERENCES bug_audit_runs(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created TEXT NOT NULL,
    PRIMARY KEY(run_id, event_id)
);
"""


def _iso(value, label):
    need(isinstance(value, str) and value, label + "无效")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise Problem(label + "无效") from exc
    need(parsed.tzinfo is not None, label + "必须包含时区")
    return parsed.astimezone(SHANGHAI)


def _json_value(value, label, maximum=4000):
    need(value is None or isinstance(value, (str, int, float, bool)), label + "格式无效")
    if isinstance(value, str):
        need(len(value) <= maximum, label + "超出长度限制")
    return value


def _status(value, allowed, label):
    need(isinstance(value, str) and value in allowed, label + "无效")
    return value


def _same_field_value(name, left, right):
    if name in AUDIT_FIELDS - {"Bug quality audit"} and isinstance(left, str) and isinstance(right, str):
        return sorted(part.strip() for part in left.split(",") if part.strip()) == sorted(
            part.strip() for part in right.split(",") if part.strip())
    return left == right


class BugAudit:
    def __init__(self, store):
        self.store = store
        with store.connection(write=True) as db:
            db.executescript(SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(bug_audit_events)")}
            if "details_json" not in columns:
                db.execute("ALTER TABLE bug_audit_events ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}' ")

    def begin_run(self, run_key, window, scope, rule_version):
        need(isinstance(run_key, str) and re.fullmatch(r"[A-Za-z0-9:._-]{1,240}", run_key), "运行键无效")
        need(isinstance(scope, dict) and set(scope).issubset({"project", "teams", "states", "scope_hash"}), "范围摘要包含不允许字段")
        need(isinstance(scope.get("project", ""), str) and len(scope.get("project", "")) <= 160
             and not re.search(r"[\\/]|^[A-Za-z]:", scope.get("project", "")), "项目范围无效")
        for key in ("teams", "states"):
            if key in scope:
                need(isinstance(scope[key], list) and len(scope[key]) <= 50
                     and all(isinstance(value, str) and len(value) <= 120 and
                             not re.search(r"[\\/]|^[A-Za-z]:", value) for value in scope[key]),
                     key + " 范围无效")
        if "scope_hash" in scope:
            need(isinstance(scope["scope_hash"], str) and re.fullmatch(r"[A-Fa-f0-9]{16,64}", scope["scope_hash"]),
                 "范围摘要哈希无效")
        need(isinstance(rule_version, str) and 1 <= len(rule_version) <= 80, "规则版本无效")
        need(isinstance(window, dict), "时间窗口格式无效")
        start, end = _iso(window.get("start"), "窗口开始时间"), _iso(window.get("end"), "窗口结束时间")
        need(start.date() == end.date() and start < end, "时间窗口须为同一北京时间日期的左闭右开区间")
        need(start.hour == start.minute == start.second == start.microsecond == 0,
             "每日窗口必须从北京时间 00:00 开始")
        start_text = start.isoformat()
        end_text = end.isoformat()
        with self.store.connection(write=True) as db:
            db.execute("""INSERT OR IGNORE INTO bug_audit_runs
                (run_key,rule_version,window_start,window_end,scope_json,created,changed)
                VALUES(?,?,?,?,?,?,?)""", (run_key, rule_version, start_text, end_text,
                                               encode(scope), _now(), _now()))
            row = db.execute("SELECT * FROM bug_audit_runs WHERE run_key=?", (run_key,)).fetchone()
            need(row["rule_version"] == rule_version and row["window_start"] == start_text
                 and row["scope_json"] == encode(scope),
                 "运行键已对应不同的范围、窗口或规则版本", 409)
            return self._run(row)

    def record_evidence(self, run_id, event_id, payload):
        self._payload(payload)
        need(isinstance(payload.get("items"), list), "items 必须为数组")
        scope_status = _status(payload.get("scope_status"), {"complete", "partial", "failed"}, "范围完整性")
        with self.store.connection(write=True) as db:
            run = self._run_row(db, run_id)
            replayed = self._event(db, run_id, event_id, "evidence", payload)
            if replayed:
                return self._evidence_result(db, run_id)
            start, end = _iso(run["window_start"], "窗口开始时间"), _iso(run["window_end"], "窗口结束时间")
            candidates = 0
            partial = scope_status != "complete"
            for item in payload["items"]:
                need(isinstance(item, dict), "Bug 证据项格式无效")
                bug_id = self._bug_id(item.get("bug_id"))
                history = item.get("history", [])
                need(isinstance(history, list), "History 必须为数组")
                evidence = item.get("evidence", {})
                need(isinstance(evidence, dict), "证据完整性格式无效")
                safe_evidence = {str(k): _status(v, EVIDENCE_STATUSES, "证据完整性状态")
                                 for k, v in evidence.items() if k in EVIDENCE_KEYS}
                partial = partial or safe_evidence.get("history") != "complete"
                partial = partial or any(v in {"partial", "failed"} for v in safe_evidence.values())
                valid = []
                for entry in history:
                    need(isinstance(entry, dict), "History 格式无效")
                    field = entry.get("field")
                    kind = entry.get("kind", "Change")
                    need(isinstance(kind, str) and (field is None or isinstance(field, str)), "History 字段无效")
                    if kind.lower() == "add" or field not in CHANGE_FIELDS:
                        continue
                    old, new = entry.get("old_value"), entry.get("new_value")
                    stamp = _iso(entry.get("changed_at"), "History 时间")
                    if start <= stamp < end and (old is None or new is None):
                        partial = True
                        continue
                    if old is None or new is None or str(old) == str(new):
                        continue
                    if start <= stamp < end:
                        need(isinstance(old, str) and isinstance(new, str) and
                             1 <= len(old) <= 120 and 1 <= len(new) <= 120 and
                             "\n" not in old and "\r" not in old and "\n" not in new and "\r" not in new,
                             "State/Severity History 值无效")
                        hid = entry.get("id")
                        need(isinstance(hid, (str, int)) and str(hid), "History ID 无效")
                        valid.append({"id": str(hid), "changed_at": stamp.isoformat(),
                                      "field": field, "old_value": _json_value(old, "History 旧值"),
                                      "new_value": _json_value(new, "History 新值")})
                if not valid:
                    continue
                candidates += 1
                snapshot = item.get("snapshot", {})
                need(isinstance(snapshot, dict), "Bug 快照格式无效")
                safe_snapshot = {key: _json_value(snapshot[key], key, 1000)
                                 for key in ("title", "state", "severity", "team", "project") if key in snapshot}
                prior = db.execute("SELECT history_json,evidence_json FROM bug_audit_items WHERE run_id=? AND bug_id=?",
                                   (run_id, bug_id)).fetchone()
                merged = {(entry["id"], entry["field"]): entry for entry in json.loads(prior["history_json"])} if prior else {}
                merged.update({(entry["id"], entry["field"]): entry for entry in valid})
                merged_evidence = json.loads(prior["evidence_json"]) if prior else {}
                merged_evidence.update(safe_evidence)
                db.execute("""INSERT INTO bug_audit_items
                    (run_id,bug_id,snapshot_json,history_json,evidence_json,inclusion_reason)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(run_id,bug_id) DO UPDATE SET
                    snapshot_json=excluded.snapshot_json,history_json=excluded.history_json,
                    evidence_json=excluded.evidence_json,inclusion_reason=excluded.inclusion_reason""",
                           (run_id, bug_id, encode(safe_snapshot), encode(list(merged.values())),
                            encode(merged_evidence), "State/Severity 在窗口内发生真实变化"))
            collection_status = "failed" if scope_status == "failed" else ("partial" if partial else "complete")
            db.execute("UPDATE bug_audit_runs SET collection_status=?,audit_status=?,changed=? WHERE id=?",
                       (collection_status, self._audit_status(db, run_id), _now(), run_id))
            return self._evidence_result(db, run_id)

    def record_findings(self, run_id, event_id, payload):
        self._payload(payload)
        need(isinstance(payload.get("items"), list), "items 必须为数组")
        with self.store.connection(write=True) as db:
            self._run_row(db, run_id)
            if self._event(db, run_id, event_id, "findings", payload):
                return self._finding_result(db, run_id)
            for item in payload["items"]:
                need(isinstance(item, dict) and isinstance(item.get("fields"), list), "审计结论格式无效")
                bug_id = self._bug_id(item.get("bug_id"))
                need(db.execute("SELECT 1 FROM bug_audit_items WHERE run_id=? AND bug_id=?", (run_id, bug_id)).fetchone(),
                     "审计结论中的 Bug 不属于本次候选")
                for field in item["fields"]:
                    need(isinstance(field, dict), "字段结论格式无效")
                    name = field.get("name")
                    need(isinstance(name, str) and name in AUDIT_FIELDS, "审计字段不在允许范围")
                    original = _json_value(field.get("original_value"), "字段原值")
                    proposed = _json_value(field.get("proposed_value"), "字段拟值")
                    ready = field.get("ready")
                    need(type(ready) is bool, "ready 必须为布尔值")
                    if proposed is None:
                        ready = False
                    reason = field.get("reason", "")
                    need(isinstance(reason, str) and len(reason) <= 1000, "阻塞原因无效")
                    need(ready or bool(reason), "pending 字段必须填写阻塞原因")
                    prior = db.execute("SELECT * FROM bug_audit_fields WHERE run_id=? AND bug_id=? AND name=?",
                                       (run_id, bug_id, name)).fetchone()
                    if prior and prior["status"] != "pending":
                        need(prior["original_json"] == encode(original) and prior["proposed_json"] == encode(proposed)
                             and prior["ready"] == int(ready), "已有回填回执的字段不得覆盖审计结论", 409)
                        continue
                    db.execute("""INSERT INTO bug_audit_fields
                        (run_id,bug_id,name,original_json,proposed_json,ready,reason,status)
                        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(run_id,bug_id,name) DO UPDATE SET
                        original_json=excluded.original_json,proposed_json=excluded.proposed_json,
                        ready=excluded.ready,reason=excluded.reason,
                        status=CASE WHEN bug_audit_fields.conflict=1 THEN 'conflict' ELSE excluded.status END""",
                               (run_id, bug_id, name, encode(original), encode(proposed), int(ready), reason,
                                "pending" if ready else "pending"))
            db.execute("UPDATE bug_audit_runs SET audit_status=?,writeback_status=?,changed=? WHERE id=?",
                       (self._audit_status(db, run_id),
                        self._stage_status(db, run_id), _now(), run_id))
            return self._finding_result(db, run_id)

    def record_writeback(self, run_id, event_id, payload):
        self._payload(payload)
        need(isinstance(payload.get("items"), list), "items 必须为数组")
        with self.store.connection(write=True) as db:
            self._run_row(db, run_id)
            if self._event(db, run_id, event_id, "writeback", payload):
                return self._writeback_result(db, run_id)
            for item in payload["items"]:
                need(isinstance(item, dict) and isinstance(item.get("fields"), list), "回填结果格式无效")
                bug_id = self._bug_id(item.get("bug_id"))
                for result in item["fields"]:
                    name = result.get("name") if isinstance(result, dict) else None
                    need(name in AUDIT_FIELDS, "审计字段不在允许范围")
                    row = db.execute("SELECT * FROM bug_audit_fields WHERE run_id=? AND bug_id=? AND name=?",
                                     (run_id, bug_id, name)).fetchone()
                    need(row is not None, "字段尚未登记审计结论")
                    need(row["ready"] and json.loads(row["proposed_json"]) is not None,
                         "pending 或 null 字段不得登记写入")
                    preflight = _json_value(result.get("preflight_value"), "预检原值")
                    if not _same_field_value(name, preflight, json.loads(row["original_json"])):
                        db.execute("UPDATE bug_audit_fields SET preflight_json=?,conflict=1,status='conflict' WHERE run_id=? AND bug_id=? AND name=?",
                                   (encode(preflight), run_id, bug_id, name))
                        continue
                    status = _status(result.get("status"), ITEM_STATUSES, "写入状态")
                    readback = _json_value(result.get("readback_value"), "回读值")
                    if status in {"written", "skipped"}:
                        status = "verified" if _same_field_value(name, readback, json.loads(row["proposed_json"])) else ("unknown" if readback is None else "failed")
                    db.execute("UPDATE bug_audit_fields SET preflight_json=?,status=?,readback_json=? WHERE run_id=? AND bug_id=? AND name=?",
                               (encode(preflight), status, encode(readback), run_id, bug_id, name))
            db.execute("UPDATE bug_audit_runs SET writeback_status=?,changed=? WHERE id=?",
                       (self._stage_status(db, run_id), _now(), run_id))
            return self._writeback_result(db, run_id)

    def record_notification(self, run_id, event_id, payload):
        self._payload(payload)
        result = {key: payload[key] for key in ("status", "target", "message_id", "idempotency_key", "attachment_status", "summary_hash") if key in payload}
        for key, value in result.items():
            need(isinstance(value, str) and len(value) <= 300, key + " 无效")
        if "target" in result:
            need(bool(result["target"]) and not re.search(r"[\\/]|^[A-Za-z]:", result["target"]), "通知目标不能包含本地路径")
        for key in ("message_id", "idempotency_key"):
            if key in result:
                need(bool(re.fullmatch(r"[A-Za-z0-9:._-]{1,300}", result[key])), key + " 格式无效")
        if "summary_hash" in result:
            need(bool(re.fullmatch(r"[A-Fa-f0-9]{32,64}", result["summary_hash"])), "summary_hash 须为摘要哈希")
        result["status"] = _status(result.get("status"), {"pending", "sent", "unknown", "partial", "failed", "verified", "not_required"}, "通知状态")
        if "attachment_status" in result:
            result["attachment_status"] = _status(result["attachment_status"],
                {"complete", "verified", "not_requested", "mismatch", "unknown", "failed"}, "附件状态")
            if result["attachment_status"] == "mismatch" and result["status"] in {"sent", "verified"}:
                result["status"] = "partial"
            elif result["attachment_status"] in {"unknown", "failed"} and result["status"] == "verified":
                result["status"] = "partial"
        if result["status"] == "verified":
            need(all(key in result for key in ("target", "message_id", "idempotency_key", "attachment_status", "summary_hash")),
                 "verified 通知必须包含目标、消息回执、幂等键、附件状态和摘要哈希")
            need(result["attachment_status"] in {"complete", "verified"}, "附件未核验，通知不能标 verified")
        with self.store.connection(write=True) as db:
            self._run_row(db, run_id)
            if self._event(db, run_id, event_id, "notification", payload, result):
                return self._notification_result(db, run_id)
            db.execute("UPDATE bug_audit_runs SET notification_status=?,summary_json=?,changed=? WHERE id=?",
                       (result["status"], encode(result), _now(), run_id))
            return self._notification_result(db, run_id)

    def complete_run(self, run_id, event_id, payload):
        self._payload(payload)
        with self.store.connection(write=True) as db:
            self._run_row(db, run_id)
            self._event(db, run_id, event_id, "complete", payload)
            run = db.execute("SELECT * FROM bug_audit_runs WHERE id=?", (run_id,)).fetchone()
            statuses = [run[key] for key in ("collection_status", "audit_status", "writeback_status", "notification_status")]
            pending = db.execute("SELECT COUNT(*) FROM bug_audit_fields WHERE run_id=? AND ready=0", (run_id,)).fetchone()[0]
            if "failed" in statuses:
                final = "failed"
            else:
                final = "complete" if not pending and all(value in {"complete", "verified", "not_required"} for value in statuses) else "partial"
            if run["status"] != final:
                db.execute("UPDATE bug_audit_runs SET status=?,changed=? WHERE id=?", (final, _now(), run_id))
            return self._run(db.execute("SELECT * FROM bug_audit_runs WHERE id=?", (run_id,)).fetchone())

    def list_runs(self, limit=50):
        need(type(limit) is int and 1 <= limit <= 200, "limit 须为 1–200")
        with self.store.connection() as db:
            rows = db.execute("SELECT * FROM bug_audit_runs ORDER BY window_start DESC,id DESC LIMIT ?", (limit,)).fetchall()
            metadata = self._executions(db)
            return [{**self._run(row), "execution": metadata.get(row["id"], self._legacy_execution())} for row in rows]

    def get_run(self, run_id):
        with self.store.connection() as db:
            row = self._run_row(db, run_id)
            items = db.execute("SELECT * FROM bug_audit_items WHERE run_id=? ORDER BY bug_id", (run_id,)).fetchall()
            result = self._run(row)
            result["execution"] = self._executions(db).get(run_id, self._legacy_execution())
            result["items"] = []
            result["events"] = [{"event_id": event["event_id"], "stage": event["stage"],
                                 "created": event["created"], "details": json.loads(event["details_json"])}
                                for event in db.execute("SELECT * FROM bug_audit_events WHERE run_id=? ORDER BY created,event_id", (run_id,))]
            for item in items:
                fields = db.execute("SELECT * FROM bug_audit_fields WHERE run_id=? AND bug_id=? ORDER BY name", (run_id, item["bug_id"])).fetchall()
                result["items"].append({
                    "bug_id": item["bug_id"], "snapshot": json.loads(item["snapshot_json"]),
                    "history": json.loads(item["history_json"]), "evidence": json.loads(item["evidence_json"]),
                    "inclusion_reason": item["inclusion_reason"],
                    "fields": [{"name": f["name"], "original_value": json.loads(f["original_json"]),
                                "proposed_value": json.loads(f["proposed_json"]), "ready": bool(f["ready"]),
                                "reason": f["reason"], "preflight_value": json.loads(f["preflight_json"]) if f["preflight_json"] else None,
                                "conflict": bool(f["conflict"]), "status": f["status"],
                                "readback_value": json.loads(f["readback_json"]) if f["readback_json"] else None} for f in fields],
                })
            return result

    @staticmethod
    def _legacy_execution():
        return {"producer": "legacy", "mode": "legacy", "profile": "", "stage": ""}

    @staticmethod
    def _executions(db):
        # 旧库只读兼容；不从 Web 导入 Worker 或接触其凭据/检查点。
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bug_audit_worker_jobs'").fetchone():
            return {}
        return {row["run_id"]: {"producer": "worker", "mode": row["mode"], "profile": row["profile"], "stage": row["stage"]}
                for row in db.execute("SELECT run_id,mode,profile,stage FROM bug_audit_worker_jobs")}

    @staticmethod
    def _run(row):
        return {"id": row["id"], "run_key": row["run_key"], "rule_version": row["rule_version"],
                "window": {"start": row["window_start"], "end": row["window_end"]},
                "scope": json.loads(row["scope_json"]), "collection_status": row["collection_status"],
                "audit_status": row["audit_status"], "writeback_status": row["writeback_status"],
                "notification_status": row["notification_status"], "status": row["status"],
                "summary": json.loads(row["summary_json"]), "changed": row["changed"]}

    def _event(self, db, run_id, event_id, stage, payload, details=None):
        need(isinstance(event_id, str) and re.fullmatch(r"[A-Za-z0-9:._-]{1,200}", event_id), "event_id 无效")
        digest = hashlib.sha256(encode(payload).encode("utf-8")).hexdigest()
        existing = db.execute("SELECT stage,payload_hash FROM bug_audit_events WHERE run_id=? AND event_id=?",
                              (run_id, event_id)).fetchone()
        if existing:
            need(existing["stage"] == stage and existing["payload_hash"] == digest, "event_id 已用于不同内容", 409)
            return True
        db.execute("INSERT INTO bug_audit_events(run_id,event_id,stage,payload_hash,details_json,created) VALUES(?,?,?,?,?,?)",
                   (run_id, event_id, stage, digest, encode(details or {}), _now()))
        if stage != "complete":
            db.execute("UPDATE bug_audit_runs SET status='running',changed=? WHERE id=?", (_now(), run_id))
        return False

    def _evidence_result(self, db, run_id):
        count = db.execute("SELECT COUNT(*) FROM bug_audit_items WHERE run_id=?", (run_id,)).fetchone()[0]
        row = db.execute("SELECT collection_status FROM bug_audit_runs WHERE id=?", (run_id,)).fetchone()
        return {"run_id": run_id, "candidate_count": count, "status": row["collection_status"]}

    def _finding_result(self, db, run_id):
        count = db.execute("SELECT COUNT(*) FROM bug_audit_fields WHERE run_id=? AND ready=1", (run_id,)).fetchone()[0]
        return {"run_id": run_id, "ready_field_count": count}

    def _writeback_result(self, db, run_id):
        rows = db.execute("SELECT status,conflict FROM bug_audit_fields WHERE run_id=?", (run_id,)).fetchall()
        return {"run_id": run_id, "conflict_count": sum(row["conflict"] for row in rows),
                "verified_count": sum(row["status"] == "verified" for row in rows),
                "unknown_count": sum(row["status"] == "unknown" for row in rows)}

    def _notification_result(self, db, run_id):
        row = db.execute("SELECT notification_status,summary_json FROM bug_audit_runs WHERE id=?", (run_id,)).fetchone()
        receipts = [json.loads(event["details_json"]) for event in db.execute(
            "SELECT details_json FROM bug_audit_events WHERE run_id=? AND stage='notification' ORDER BY created,event_id", (run_id,))]
        return {"run_id": run_id, "status": row["notification_status"],
                "receipt": json.loads(row["summary_json"]), "receipts": receipts}

    def _audit_status(self, db, run_id):
        candidates = db.execute("SELECT COUNT(*) FROM bug_audit_items WHERE run_id=?", (run_id,)).fetchone()[0]
        complete = db.execute("""SELECT COUNT(*) FROM (
            SELECT bug_id FROM bug_audit_fields WHERE run_id=?
            GROUP BY bug_id HAVING COUNT(*)=3)""", (run_id,)).fetchone()[0]
        return "complete" if candidates == complete else "partial"

    def _stage_status(self, db, run_id):
        rows = db.execute("SELECT status,conflict FROM bug_audit_fields WHERE run_id=? AND ready=1", (run_id,)).fetchall()
        if not rows:
            return "not_required"
        if any(row["conflict"] or row["status"] in {"unknown", "failed"} for row in rows):
            return "partial"
        return "verified" if all(row["status"] == "verified" for row in rows) else "partial"

    @staticmethod
    def _run_row(db, run_id):
        need(type(run_id) is int and run_id > 0, "运行 ID 无效")
        row = db.execute("SELECT * FROM bug_audit_runs WHERE id=?", (run_id,)).fetchone()
        need(row is not None, "运行不存在", 404)
        return row

    @staticmethod
    def _bug_id(value):
        need(isinstance(value, (str, int)) and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", str(value)), "Bug ID 无效")
        return str(value)

    @staticmethod
    def _payload(payload):
        need(isinstance(payload, dict) and payload.get("schema_version") == 1, "schema_version 须为 1")


def _now():
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")
