import json
import sqlite3
import sys
import tempfile
import unittest
import subprocess
import shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outputs/SQA_MVP"))
from bug_audit import BugAudit
from store import Store
from backup import backup, restore
from bug_audit_worker.journal import Journal
from tests.test_bug_audit_worker_tp import WINDOW


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "data")
        self.audit = BugAudit(self.store)

    def test_legacy_without_worker_tables_remains_readable(self):
        run = self.audit.begin_run("old", WINDOW, {}, "v1")
        self.assertEqual(self.audit.get_run(run["id"])["execution"], {"producer": "legacy", "mode": "legacy", "profile": "", "stage": ""})
        self.assertEqual(self.audit.list_runs()[0]["execution"]["producer"], "legacy")

    def test_only_public_execution_metadata_is_exposed(self):
        run = self.audit.begin_run("new", WINDOW, {}, "v1")
        j = Journal(self.store)
        for mode in ("demo", "dry_run", "apply"):
            entry = self.audit.begin_run(mode, WINDOW, {}, "v1")
            j.save_job(entry["id"], "test", mode, WINDOW, "private-config-hash", "notify", {"path": "private/file.json", "sha256": "a" * 64})
            public = self.audit.get_run(entry["id"])
            self.assertEqual(public["execution"], {"producer": "worker", "mode": mode, "profile": "test", "stage": "notify"})
            self.assertNotIn("private", json.dumps(public))
            self.assertEqual(self.audit.list_runs()[0]["execution"]["mode"], mode)
        self.assertEqual(self.audit.get_run(run["id"])["execution"]["producer"], "legacy")

    def test_database_backup_retains_outbox_attempt_history(self):
        run = self.audit.begin_run("backup", WINDOW, {}, "v1")
        j = Journal(self.store)
        j.save_job(run["id"], "test", "apply", WINDOW, "hash", "partial", {"path": "1/checkpoint.json"})
        key = str(run["id"]) + ":tp:1"
        j.prepare(run["id"], key, "tp", "a" * 64, {"bug_id": "1"})
        j.inflight(key); j.settle(key, "unknown", {"reason": "lost"})
        archive = backup(self.store, "worker-validation")
        restored = Store(restore(archive, Path(self.temp.name) / "restored"))
        rj = Journal(restored)
        self.assertEqual(rj.job(run["id"])["stage"], "partial")
        self.assertEqual(rj.intents(run["id"])[0]["status"], "unknown")
        with restored.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bug_audit_worker_intent_events").fetchone()[0], 3)

    @unittest.skipUnless(shutil.which("node"), "页面渲染验证需要 Node.js")
    def test_page_renders_execution_mode_and_escapes_profile(self):
        script = r'''
const fs=require('fs'),vm=require('vm');
const source=fs.readFileSync(process.argv[1],'utf8');
let handler, content={innerHTML:'',parentElement:{querySelectorAll:()=>[],addEventListener:(type,fn)=>handler=fn}};
let ctx={api:async name=>name==='bugs'?{payload:null}: [{id:1,window:{start:'s',end:'e'},scope:{},collection_status:'complete',audit_status:'complete',writeback_status:'partial',notification_status:'not_required',status:'partial',execution:{producer:'worker',mode:'demo',profile:'<img src=x>',stage:'planned'}}],
 heading:()=>'',empty:()=>'',tag:(s)=>String(s),button:()=>'',esc:s=>String(s??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),
 $:()=>content,document:{querySelector:()=>null}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('async function showBugs()'),source.indexOf('async function createTask(')),ctx);
(async()=>{let page=await ctx.showBugs();page.mount();await handler({target:{closest:s=>s==='[data-bug-audit-view]'?{dataset:{bugAuditView:'runs'}}:null}});console.log(content.innerHTML)})().catch(e=>{console.error(e);process.exitCode=1});
'''
        path = Path(__file__).resolve().parents[1] / "outputs/SQA_MVP/web/app.js"
        result = subprocess.run(["node", "-e", script, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("合成演示", result.stdout)
        self.assertIn("未执行真实回填", result.stdout)
        self.assertIn("&lt;img src=x&gt;", result.stdout)
        self.assertNotIn("<img src=x>", result.stdout)
