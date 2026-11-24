"""Race 3S pilot with loopback and an explicitly configured office LAN listener."""

import argparse
import base64
import binascii
import json
import logging
import ipaddress
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from backup import backup, restore
from imports import IMPORT_FILES, PROJECT, STAGE, VERSION
from store import LOCAL_TZ, MAX_FILE, Problem, Store, clean, need, owner
from store import reviewer
from bug_audit import BugAudit
from sync import PilotSync
from library import LocalLibrary
from governance import Governance
from crash_site import CrashSite, SITE as CRASH_SITE

WEB = Path(__file__).resolve().parent / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
SESSION_AGE = 8 * 60 * 60
MAX_BODY = ((MAX_FILE + 2) // 3) * 4 * 4 + 64 * 1024
LOG = logging.getLogger("sqa")


class PilotServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, store, samples=None, sync_config=None, library_config=None):
        super().__init__(("127.0.0.1", port), Handler)
        self.store = store
        self.bug_audit = BugAudit(store)
        self.samples = samples
        self.sync = PilotSync(self, sync_config) if sync_config else None
        self.library = LocalLibrary(library_config) if library_config else None
        self.crash_site = CrashSite(library_config, store.root)
        self.governance = Governance(store, self.library)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.host = f"127.0.0.1:{self.server_port}"
        self.allowed_hosts = {self.host}
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.publish_lock = threading.Lock()
        self.backup_stop = threading.Event()
        self.next_auth = 0.0
        self.auth_lock = threading.Lock()

    def lan_listener(self, address):
        ip = ipaddress.IPv4Address(address)
        need(any(ip in ipaddress.ip_network(block) for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")),
             "局域网监听必须使用明确的办公内网 IPv4 地址")
        need(not self.store.setup_needed(), "请先在本机创建负责人账号")
        listener = ThreadingHTTPServer((str(ip), self.server_port),
            lambda request, client_address, transport: Handler(request, client_address, self))
        self.allowed_hosts.add(f"{ip}:{self.server_port}")
        return listener

    def mint(self, user):
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        with self.session_lock:
            self.sessions = {k: v for k, v in self.sessions.items()
                             if v[2] > time.monotonic() and v[0] != user["id"]}
            self.sessions[token] = (user["id"], csrf, time.monotonic() + SESSION_AGE)
        return token, csrf

    def daily_backups(self):
        completed = None
        while not self.backup_stop.is_set():
            current = datetime.now(LOCAL_TZ)
            today = current.date().isoformat()
            try:
                calendar = self.store.root / "workdays.json"
                overrides = json.loads(calendar.read_text(encoding="utf-8")) if calendar.is_file() else {}
                is_workday = overrides.get(today, current.weekday() < 5)
                if is_workday and current.hour >= 9 and today != completed and not self.store.setup_needed():
                    backup(self.store, "daily")
                    completed = today
            except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
                LOG.exception("自动备份失败")
            self.backup_stop.wait(60)

    def ensure_first_backup(self):
        with self.publish_lock:
            with self.store.connection() as db:
                exists = db.execute("SELECT 1 FROM events WHERE action='import.publish' LIMIT 1").fetchone()
            if not exists:
                backup_dir = self.store.root / "backups"
                if not list(backup_dir.glob("sqa-pre-import-*.zip")):
                    backup(self.store, "pre-import")

    def stage(self, user, kind, cutoff, files, prepared_pr=None):
        owner(user)
        self.ensure_first_backup()
        return self.store.stage_import(user, kind, cutoff, files, prepared_pr)

    def first_publish(self, user, data, **conditions):
        owner(user)
        self.ensure_first_backup()
        return self.store.publish(user, data, **conditions)

    def stage_samples(self, user):
        owner(user)
        need(self.samples, "本机尚未配置历史样本", 404)
        results = {}
        for kind, slots in IMPORT_FILES.items():
            block = self.samples[kind]
            paths = {slot: Path(block[slot]) for slot in slots}
            need(all(path.is_file() and path.stat().st_size <= MAX_FILE for path in paths.values()),
                 f"{kind} 的历史样本文件缺失或过大")
            files = {slot: (path.name, path.read_bytes()) for slot, path in paths.items()}
            results[kind] = self.stage(user, kind, block.get("cutoff", ""), files)
        return results

    def set_account(self, user, data):
        account = self.store.set_account(user, data)
        if not account["enabled"]:
            with self.session_lock:
                self.sessions = {key: value for key, value in self.sessions.items() if value[0] != account["id"]}
        return account

    def sync_status(self, user):
        owner(user)
        return {"enabled": False, "jobs": []} if self.sync is None else {
            "enabled": True, **self.sync.read()
        }

    def sync_run(self, user):
        owner(user)
        need(self.sync, "自动更新尚未配置", 409)
        self.sync.trigger.set()
        return {"queued": True}

    def library_worker(self):
        while not self.backup_stop.is_set():
            self.library.refresh()
            self.crash_site.refresh()
            self.backup_stop.wait(300)

    def library_access(self, user):
        owner(user)
        need(self.library, "本机资料来源尚未配置", 404)
        return self.library

    def library_refresh(self, user):
        self.library_access(user).refresh(force=True)
        return {"ok": True}

    def crash_site_status(self, user, force=False):
        owner(user)
        return self.crash_site.refresh(force)


class Handler(BaseHTTPRequestHandler):
    server_version = "SQA-Local"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, template, *args):
        pass

    def do_GET(self):
        self.handle_request(False)

    def do_POST(self):
        self.handle_request(True)

    def send(self, code, body, mime, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        ancestors = "'self'" if self.path.startswith("/api/crash-view/") else "'none'"
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-src 'self'; frame-ancestors " + ancestors + "; base-uri 'none'; form-action 'self'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def write_json(self, value, code=200, headers=None):
        self.send(code, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                  "application/json; charset=utf-8", headers)

    def body(self):
        length = self.headers.get("Content-Length", "")
        need(length.isdecimal() and 0 < int(length) <= MAX_BODY, "请求内容过大或缺失", 413)
        need(self.headers.get_content_type() == "application/json", "仅接受 JSON 请求", 415)
        try:
            data = json.loads(self.rfile.read(int(length)))
        except (ValueError, UnicodeError) as exc:
            raise Problem("请求 JSON 无效") from exc
        need(isinstance(data, dict), "请求格式无效")
        return data

    def session(self):
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookies["sqa_session"].value if "sqa_session" in cookies else ""
        with self.server.session_lock:
            session = self.server.sessions.get(token)
        if session and session[2] > time.monotonic():
            user = self.server.store.get_user(session[0])
            if user:
                return user, session[1], token
        return None, None, token

    def handle_request(self, post):
        try:
            request_host = self.headers.get("Host")
            need(request_host in self.server.allowed_hosts, "请从已配置的工作台地址访问", 403)
            parsed = urlsplit(self.path)
            path = parsed.path
            if not post and path in STATIC:
                file, mime = STATIC[path]
                self.send(200, (WEB / file).read_bytes(), mime)
                return
            need(path.startswith("/api/"), "页面不存在", 404)
            if post:
                need(self.headers.get("Origin") in {None, "http://" + request_host}
                     and self.headers.get("X-SQA-Request") == "pilot", "请求来源不可用", 403)
            user, csrf, token = self.session()
            store = self.server.store
            if not post and path == "/api/session":
                self.write_json({"user": user, "csrf": csrf, "setup": store.setup_needed(),
                                 "project": PROJECT, "version": VERSION, "stage": STAGE, "samples": bool(self.server.samples),
                                 "governance": True, "projects": self.server.governance.grants(user) if user else []})
                return
            if post and path in {"/api/login", "/api/setup"}:
                if path == "/api/setup":
                    need(self.client_address[0] == "127.0.0.1", "首次负责人账号只能在本机创建", 403)
                with self.server.auth_lock:
                    need(time.monotonic() >= self.server.next_auth, "请求过快，请稍后再试", 429)
                    self.server.next_auth = time.monotonic() + 0.7
                data = self.body()
                account = store.add_user(None, {**data, "role": "owner"}) if path == "/api/setup" else store.login(data)
                new_token, csrf = self.server.mint(account)
                self.write_json({"user": account, "csrf": csrf}, headers={
                    "Set-Cookie": f"sqa_session={new_token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_AGE}"})
                return
            need(user, "请先登录", 401)
            if not post and path.startswith("/api/bug-audit/"):
                reviewer(user)
                if path == "/api/bug-audit/runs":
                    values = parse_qs(parsed.query).get("limit", ["50"])
                    need(len(values) == 1 and values[0].isascii() and values[0].isdecimal(), "limit 格式无效")
                    self.write_json(self.server.bug_audit.list_runs(int(values[0])))
                    return
                match = re.fullmatch(r"/api/bug-audit/runs/([1-9][0-9]*)", path)
                need(match is not None, "接口不存在", 404)
                self.write_json(self.server.bug_audit.get_run(int(match.group(1))))
                return
            if not post and path.startswith("/api/crash-view/"):
                owner(user)
                name = path.rsplit("/", 1)[-1]
                need(path == "/api/crash-view/" + name and name in {"index.html", "styles.css", "app.mjs", "data.mjs", "snapshot.mjs", "signatures.mjs"}, "页面不存在", 404)
                mime = "text/html" if name.endswith(".html") else "text/css" if name.endswith(".css") else "text/javascript"
                self.send(200, (WEB / "crash-view" / name).read_bytes(), mime + "; charset=utf-8")
                return
            if not post and path.startswith("/api/crash-data/"):
                owner(user)
                name = path.rsplit("/", 1)[-1]
                need(path == "/api/crash-data/" + name, "文件不存在", 404)
                self.send(200, self.server.crash_site.download(name), "application/json" if name.endswith(".json") else "text/csv; charset=utf-8")
                return
            if post:
                need(secrets.compare_digest(self.headers.get("X-SQA-CSRF", ""), csrf), "会话已变更，请重新登录", 403)
                if path == "/api/logout":
                    with self.server.session_lock:
                        self.server.sessions.pop(token, None)
                    self.write_json({"ok": True}, headers={
                        "Set-Cookie": "sqa_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
                    return
                self.write_json(self.mutate(path, user, self.body()))
                return
            if path.startswith("/api/governance-snapshot/"):
                sha = path.rsplit("/", 1)[-1]
                raw = self.server.governance.snapshot(user, sha)
                self.send(200, raw, "application/octet-stream", {"Content-Disposition": "attachment; filename=" + sha})
                return
            if path.startswith("/api/source/"):
                parts = path.split("/")
                need(len(parts) == 5 and parts[3].isascii() and parts[3].isdecimal(), "来源地址无效")
                name, raw = store.source_download(user, int(parts[3]), parts[4])
                self.send(200, raw, "application/octet-stream",
                          {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)})
                return
            if path.startswith("/api/library-file/"):
                parts = path.split("/")
                need(len(parts) == 5, "资料文件不存在", 404)
                preview = parse_qs(parsed.query).get("view") == ["1"]
                name, raw, mime = self.server.library_access(user).download(parts[3], parts[4], preview)
                disposition = "inline" if mime else "attachment"
                self.send(200, raw, mime or "application/octet-stream",
                          {"Content-Disposition": disposition + "; filename*=UTF-8''" + quote(name)})
                return
            if path.startswith("/api/library/"):
                self.write_json(self.server.library_access(user).catalog(path.rsplit("/", 1)[-1]))
                return
            read = {"/api/dashboard": lambda: self.server.governance.dashboard(user),
                    "/api/crash-site": lambda: self.server.crash_site_status(user),
                    "/api/release-board": lambda: self.server.governance.board(user, parse_qs(parsed.query).get("detail") == ["1"]),
                    "/api/governance/catalog": lambda: self.server.governance.catalog(user),
                    "/api/governance/records": lambda: self.server.governance.records(user),
                    "/api/governance/documents": lambda: self.server.governance.documents(user),
                    "/api/governance/team": lambda: self.server.governance.team(user),
                    "/api/metrics": lambda: self.server.governance.metric_detail(user),
                    "/api/pr": lambda: store.detail(user, "pr", parse_qs(parsed.query).get("version", [VERSION])[0]),
                    "/api/pr-overview": lambda: store.pr_overview(user),
                    "/api/bugs": lambda: store.detail(user, "bugs"),
                    "/api/batches": lambda: store.list_batches(user),
                    "/api/accounts": lambda: store.accounts(user),
                    "/api/people": store.task_people,
                    "/api/tasks": lambda: store.tasks(user),
                    "/api/audit": lambda: store.audit(user),
                    "/api/sync": lambda: self.server.sync_status(user)}
            need(path in read, "接口不存在", 404)
            self.write_json(read[path]())
        except Problem as exc:
            self.write_json({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            LOG.exception("请求处理失败")
            self.write_json({"error": "本机服务处理失败，请查看服务日志"}, 500)

    def mutate(self, path, user, data):
        store = self.server.store
        if path == "/api/import":
            owner(user)
            kind = clean(data, "kind", 12)
            uploads = data.get("files")
            need(kind in IMPORT_FILES and isinstance(uploads, dict) and set(uploads) == set(IMPORT_FILES[kind]),
                 "请选择完整来源文件")
            files = {}
            for slot, entry in uploads.items():
                need(isinstance(entry, dict), "文件上传格式无效")
                name = clean(entry, "name", 255)
                content = clean(entry, "base64", (MAX_FILE + 2) // 3 * 4)
                try:
                    raw = base64.b64decode(content, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise Problem("文件传输编码无效") from exc
                files[slot] = (name, raw)
            return self.server.stage(user, kind, clean(data, "cutoff", 35, False), files)
        mutations = {
            "/api/governance/evidence.save": lambda: self.server.governance.evidence_save(user, data),
            "/api/governance/evidence.action": lambda: self.server.governance.evidence_action(user, data),
            "/api/governance/document.save": lambda: self.server.governance.document_save(user, data),
            "/api/governance/document.action": lambda: self.server.governance.document_action(user, data),
            "/api/governance/team.save": lambda: self.server.governance.team_save(user, data),
            "/api/crash-site.refresh": lambda: self.server.crash_site_status(user, True),
            "/api/library.refresh": lambda: self.server.library_refresh(user),
            "/api/samples.stage": lambda: self.server.stage_samples(user),
            "/api/sync.run": lambda: self.server.sync_run(user),
            "/api/publish": lambda: self.server.first_publish(user, data),
            "/api/withdraw": lambda: store.withdraw(user, data),
            "/api/metric.save": lambda: store.save_metric(user, data),
            "/api/pr.review": lambda: store.review(user, data, "pr"),
            "/api/bugs.review": lambda: store.review(user, data, "bugs"),
            "/api/task.create": lambda: store.create_task(user, data),
            "/api/task.action": lambda: store.task_action(user, data),
            "/api/account.create": lambda: store.add_user(user, data),
            "/api/account.status": lambda: self.server.set_account(user, data),
        }
        need(path in mutations, "接口不存在", 404)
        return mutations[path]()


def main():
    parser = argparse.ArgumentParser(description="Race 3S 质量体系本机 MVP")
    parser.add_argument("--data-dir", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", default=8765, type=int)
    serve.add_argument("--sample-config")
    serve.add_argument("--sync-config")
    serve.add_argument("--library-config")
    serve.add_argument("--lan-ip", help="Explicit office LAN IPv4; loopback stays available")
    save = commands.add_parser("backup")
    save.add_argument("--reason", default="manual", choices=("manual", "pre-upgrade", "daily"))
    initialize = commands.add_parser("governance-init")
    initialize.add_argument("--library-config", required=True)
    recover = commands.add_parser("restore")
    recover.add_argument("--archive", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.command == "restore":
        print(restore(args.archive, args.data_dir), flush=True)
    elif args.command == "governance-init":
        store = Store(args.data_dir)
        with store.connection() as db:
            account = db.execute("SELECT id,name,role,enabled FROM users WHERE role='owner' AND enabled=1").fetchone()
        need(account, "须先设置负责人账号")
        archive = backup(store, "pre-import")
        governance = Governance(store, LocalLibrary(args.library_config))
        result = governance.initialize_drafts(dict(account))
        print(json.dumps({"backup": str(archive), **result}, ensure_ascii=False), flush=True)
    elif args.command == "backup":
        print(backup(Store(args.data_dir), args.reason), flush=True)
    else:
        samples = json.loads(Path(args.sample_config).read_text(encoding="utf-8-sig")) if args.sample_config else None
        server = PilotServer(args.port, Store(args.data_dir), samples, args.sync_config, args.library_config)
        lan = server.lan_listener(args.lan_ip) if args.lan_ip else None
        if lan:
            threading.Thread(target=lan.serve_forever, name="sqa-lan", daemon=True).start()
            print(f"Office LAN · http://{args.lan_ip}:{server.server_port}/", flush=True)
        worker = threading.Thread(target=server.daily_backups, daemon=True)
        worker.start()
        if server.sync:
            threading.Thread(target=server.sync.worker, name="sqa-sync", daemon=True).start()
        if server.library:
            threading.Thread(target=server.library_worker, name="sqa-library", daemon=True).start()
        print(f"Race 3S 质量体系 MVP · {server.origin}/", flush=True)
        try:
            server.serve_forever()
        finally:
            if lan:
                lan.shutdown()
                lan.server_close()
            server.backup_stop.set()
            if server.sync:
                server.sync.stopping.set()
                server.sync.trigger.set()
            server.server_close()


if __name__ == "__main__":
    main()
