"""Local-only importer for structured Bug-audit run results."""

import argparse
import json
import sys
from pathlib import Path

from bug_audit import BugAudit
from store import Problem, Store


STAGES = {
    "record-evidence": "record_evidence",
    "record-findings": "record_findings",
    "record-writeback": "record_writeback",
    "record-notification": "record_notification",
    "complete": "complete_run",
}


def parser():
    root = argparse.ArgumentParser(description="Bug 审计本机台账导入工具")
    root.add_argument("--data-dir", required=True, help="SQA-MVP 本机数据目录")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("begin", *STAGES):
        command = commands.add_parser(name)
        command.add_argument("--input", required=True, help="UTF-8 JSON 文件")
        if name in STAGES:
            command.add_argument("--run-id", required=True, type=int)
            command.add_argument("--event-id", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise Problem("JSON schema_version 须为 1")
        audit = BugAudit(Store(args.data_dir))
        if args.command == "begin":
            result = audit.begin_run(payload.get("run_key"), payload.get("window"),
                                     payload.get("scope"), payload.get("rule_version"))
        else:
            method = getattr(audit, STAGES[args.command])
            result = method(args.run_id, args.event_id, payload)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Problem as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError):
        print("JSON 文件无法读取或格式无效", file=sys.stderr)
        return 2
    except Exception:
        print("Bug 审计台账写入失败", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
