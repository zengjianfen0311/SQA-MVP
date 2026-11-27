"""副作用意图先持久化；同机跨进程锁不抢占仍活跃的执行者。"""
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone


class JournalError(RuntimeError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def safe(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or any(s in key.lower() for s in ("token", "secret", "authorization", "cookie", "raw_comment")):
                raise JournalError("执行日志禁止保存凭据或原始评论")
            safe(item)
    elif isinstance(value, list):
        for item in value: safe(item)
    elif isinstance(value, str) and (re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("/")):
        raise JournalError("执行日志禁止保存绝对路径")
    try:
        text = encoded(value)
    except (ValueError, TypeError):
        raise JournalError("执行日志数据格式无效") from None
    if len(text) > 1000000:
        raise JournalError("执行日志过大")


SCHEMA = """
CREATE TABLE IF NOT EXISTS bug_audit_worker_jobs (
 run_id INTEGER PRIMARY KEY REFERENCES bug_audit_runs(id),
 profile TEXT NOT NULL, mode TEXT NOT NULL, window_json TEXT NOT NULL,
 config_hash TEXT NOT NULL, stage TEXT NOT NULL, checkpoint_json TEXT NOT NULL, changed TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bug_audit_worker_intents (
 operation_key TEXT PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES bug_audit_runs(id),
 kind TEXT NOT NULL, payload_hash TEXT NOT NULL, details_json TEXT NOT NULL,
 status TEXT NOT NULL, receipt_json TEXT NOT NULL DEFAULT '{}', changed TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bug_audit_worker_intent_events (
 id INTEGER PRIMARY KEY, operation_key TEXT NOT NULL REFERENCES bug_audit_worker_intents(operation_key),
 status TEXT NOT NULL, receipt_json TEXT NOT NULL, changed TEXT NOT NULL
);
"""


class Journal:
    def __init__(self, store):
        self.store = store
        with store.connection(True) as db:
            for statement in SCHEMA.split(";"):
                if statement.strip(): db.execute(statement)

    @contextmanager
    def lock(self, scope_key):
        directory = self.store.root / "bug-audit-worker-locks"
        directory.mkdir(exist_ok=True)
        path = directory / (hashlib.sha256(str(scope_key).encode()).hexdigest() + ".lock")
        handle = path.open("a+b")
        locked = False
        try:
            handle.seek(0, 2)
            if handle.tell() == 0: handle.write(b"0"); handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                raise JournalError("已有 Worker 正在执行相同范围") from None
            yield
        finally:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def save_job(self, run_id, profile, mode, window, config_hash, stage, checkpoint):
        if mode not in {"demo", "dry_run", "apply"}: raise JournalError("执行模式无效")
        safe(checkpoint)
        with self.store.connection(True) as db:
            previous = db.execute("SELECT * FROM bug_audit_worker_jobs WHERE run_id=?", (run_id,)).fetchone()
            if previous and (previous["profile"], previous["mode"], previous["window_json"], previous["config_hash"]) != (profile, mode, encoded(window), config_hash):
                raise JournalError("检查点身份、模式、配置或冻结窗口不一致")
            db.execute("""INSERT INTO bug_audit_worker_jobs VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET stage=excluded.stage,
                checkpoint_json=excluded.checkpoint_json,changed=excluded.changed""",
                (run_id, profile, mode, encoded(window), config_hash, stage, encoded(checkpoint), now()))
        return self.job(run_id)

    def job(self, run_id):
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM bug_audit_worker_jobs WHERE run_id=?", (run_id,)).fetchone()
        if row is None: raise JournalError("Worker 检查点不存在")
        result = dict(row)
        result["window"] = json.loads(result.pop("window_json"))
        result["checkpoint"] = json.loads(result.pop("checkpoint_json"))
        return result

    @staticmethod
    def _intent(row):
        result = dict(row)
        result["details"] = json.loads(result.pop("details_json"))
        result["receipt"] = json.loads(result.pop("receipt_json"))
        return result

    def prepare(self, run_id, key, kind, payload_hash, details):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9:._-]{1,240}", key): raise JournalError("操作键无效")
        if kind not in {"tp", "feishu_summary", "feishu_file"}: raise JournalError("操作类型无效")
        if not re.fullmatch(r"[a-f0-9]{64}", payload_hash): raise JournalError("操作摘要无效")
        safe(details)
        with self.store.connection(True) as db:
            row = db.execute("SELECT * FROM bug_audit_worker_intents WHERE operation_key=?", (key,)).fetchone()
            if row:
                if (row["run_id"], row["kind"], row["payload_hash"], row["details_json"]) != (run_id, kind, payload_hash, encoded(details)):
                    raise JournalError("操作键已对应不同请求")
                return self._intent(row)
            db.execute("INSERT INTO bug_audit_worker_intents VALUES(?,?,?,?,?,'prepared','{}',?)",
                       (key, run_id, kind, payload_hash, encoded(details), now()))
            db.execute("INSERT INTO bug_audit_worker_intent_events(operation_key,status,receipt_json,changed) VALUES(?,'prepared','{}',?)",(key,now()))
            return self._intent(db.execute("SELECT * FROM bug_audit_worker_intents WHERE operation_key=?", (key,)).fetchone())

    def inflight(self, key):
        with self.store.connection(True) as db:
            changed = db.execute("UPDATE bug_audit_worker_intents SET status='in_flight',changed=? WHERE operation_key=? AND status='prepared'", (now(), key)).rowcount
            if changed != 1: raise JournalError("操作已执行或结果尚未核实，禁止重复请求")
            db.execute("INSERT INTO bug_audit_worker_intent_events(operation_key,status,receipt_json,changed) VALUES(?,'in_flight','{}',?)",(key,now()))

    def settle(self, key, status, receipt):
        if status not in {"verified", "failed", "unknown", "conflict"}: raise JournalError("回执状态无效")
        safe(receipt)
        with self.store.connection(True) as db:
            row = db.execute("SELECT * FROM bug_audit_worker_intents WHERE operation_key=?", (key,)).fetchone()
            if row is None: raise JournalError("操作意图不存在")
            if row["status"] != "verified":
                db.execute("UPDATE bug_audit_worker_intents SET status=?,receipt_json=?,changed=? WHERE operation_key=?",
                           (status, encoded(receipt), now(), key))
                db.execute("INSERT INTO bug_audit_worker_intent_events(operation_key,status,receipt_json,changed) VALUES(?,?,?,?)",(key,status,encoded(receipt),now()))
            return self._intent(db.execute("SELECT * FROM bug_audit_worker_intents WHERE operation_key=?", (key,)).fetchone())

    def retry_failed(self,key):
        with self.store.connection(True) as db:
            row=db.execute("SELECT * FROM bug_audit_worker_intents WHERE operation_key=?",(key,)).fetchone()
            if row is None or row["status"]!="failed" or json.loads(row["receipt_json"]).get("safe_retry") is not True:
                raise JournalError("不能重试未经证明拒绝的请求")
            db.execute("UPDATE bug_audit_worker_intents SET status='prepared',changed=? WHERE operation_key=?",(now(),key))
            db.execute("INSERT INTO bug_audit_worker_intent_events(operation_key,status,receipt_json,changed) VALUES(?,'prepared',?,?)",(key,row["receipt_json"],now()))

    def intents(self, run_id):
        with self.store.connection() as db:
            return [self._intent(row) for row in db.execute("SELECT * FROM bug_audit_worker_intents WHERE run_id=? ORDER BY operation_key", (run_id,))]
