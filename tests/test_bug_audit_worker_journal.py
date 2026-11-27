import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULES = Path(__file__).resolve().parents[1] / "outputs" / "SQA_MVP"
sys.path.insert(0, str(MODULES))
from store import Store
from bug_audit import BugAudit
from bug_audit_worker.journal import Journal, JournalError


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.audit = BugAudit(self.store)
        self.run = self.audit.begin_run("journal", {"start":"2026-11-24T00:00:00+08:00", "end":"2026-11-24T19:30:00+08:00"}, {}, "v1")
        self.j = Journal(self.store)

    def prepare(self):
        return self.j.prepare(self.run["id"], "tp:1", "tp", "a" * 64, {"bug_id":"1"})

    def test_intent_survives_reopen_and_verified_cannot_be_downgraded(self):
        self.prepare()
        self.j.inflight("tp:1")
        reopened = Journal(self.store)
        self.assertEqual(reopened.intents(self.run["id"])[0]["status"], "in_flight")
        reopened.settle("tp:1", "unknown", {"readback": None})
        reopened.settle("tp:1", "verified", {"readback": "Pass"})
        reopened.settle("tp:1", "failed", {"readback": "Fail"})
        self.assertEqual(reopened.intents(self.run["id"])[0]["receipt"], {"readback":"Pass"})
        with self.assertRaises(JournalError): reopened.inflight("tp:1")

    def test_operation_key_cannot_be_reused_for_different_request(self):
        self.prepare()
        self.assertEqual(self.prepare()["status"], "prepared")
        with self.assertRaises(JournalError):
            self.j.prepare(self.run["id"], "tp:1", "tp", "b"*64, {"bug_id":"1"})
        self.assertEqual(len(self.j.intents(self.run["id"])), 1)

    def test_checkpoint_identity_is_immutable_and_payload_rejects_credentials(self):
        window = self.run["window"]
        self.j.save_job(self.run["id"], "dev", "dry_run", window, "c"*64, "evidence", {"path":"bundle.json", "sha256":"d"*64})
        with self.assertRaises(JournalError):
            self.j.save_job(self.run["id"], "dev", "apply", window, "c"*64, "evidence", {})
        with self.assertRaises(JournalError):
            self.j.prepare(self.run["id"], "unsafe", "tp", "a"*64, {"access_token":"do-not-persist"})
        with self.store.connection() as db:
            values = list(db.execute("SELECT details_json FROM bug_audit_worker_intents"))
        self.assertNotIn("do-not-persist", json.dumps([tuple(v) for v in values]))

    def test_separate_process_cannot_enter_active_lock_and_exit_releases_it(self):
        code = "import sys;sys.path.insert(0,sys.argv[1]);from store import Store;from bug_audit_worker.journal import Journal;j=Journal(Store(sys.argv[2]));ctx=j.lock('same');ctx.__enter__()"
        with self.j.lock("same"):
            child = subprocess.run([sys.executable, "-c", code, str(MODULES), self.temp.name], capture_output=True, timeout=10)
            self.assertNotEqual(child.returncode, 0)
        child = subprocess.run([sys.executable, "-c", code + ";import os;os._exit(0)", str(MODULES), self.temp.name], capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        with self.j.lock("same"):
            self.assertEqual(len(self.j.intents(self.run["id"])), 0)

if __name__ == "__main__": unittest.main()
