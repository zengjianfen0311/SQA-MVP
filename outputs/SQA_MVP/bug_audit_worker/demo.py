"""纯合成演示，不联网、不读取凭据、不生成真实副作用回执。"""
import json
from pathlib import Path
from .config import ConfigError, load_config
from .engine import Worker
from .journal import digest
from .rules import MAPPING, check, semantic_rules
from .tp import FIELDS, business_fingerprint

WINDOW = {"start": "2026-11-24T00:00:00+08:00", "end": "2026-11-24T19:30:00+08:00"}


class DemoTP:
    def __init__(self, config): self.config = config

    def collect(self, window):
        item = {"bug_id": "1", "snapshot": {"title": "合成演示：导航重启", "state": "Open", "severity": "Major", "team": "演示团队", "project": "演示项目"},
                "created_at": "2026-11-23T12:00:00+08:00", "feature_id": None,
                "body": "固件 1.0：开始导航后重启，可重复，预期保持运行。合成数据。",
                "fields": {"Bug Category": "Functional", "REG issue": "No"}, "original": {name: None for name in FIELDS},
                "comments": [{"id": "20", "at": "2026-11-24T12:00:00+08:00", "text": "合成说明：重启涉及多个功能，继续定位。"}],
                "history": [{"id": "11", "kind": "Change", "changed_at": "2026-11-24T12:00:00+08:00", "field": "State", "old_value": "New", "new_value": "Open"}],
                "lifecycle": [{"id": 10, "sourceEntityId": 1, "date": "2026-11-23T12:00:00+08:00", "modification": "Add", "stateName": "New", "severityName": "Major"},
                              {"id": 11, "sourceEntityId": 1, "date": "2026-11-24T12:00:00+08:00", "modification": "Update", "changes": "EntityState", "stateName": "Open", "severityName": "Major"}],
                "relations": {"inbound": [], "outbound": []}, "evidence": {"history": "complete", "comments": "complete", "relations": "complete", "description": "complete", "attachments": "not_requested"}}
        item["source_hash"] = business_fingerprint(item)
        return {"window": window, "scope": self.config.section("scope"), "items": [item], "candidate_count": 1,
                "completeness": "complete", "unresolved": [], "options": {name: sorted(set(MAPPING.values()) | {"No need", "OK"}) for name in FIELDS[:2]},
                "features": [], "feature_details": [], "feature_status": "complete"}


class DemoModel:
    calls = 0

    def review(self, item, facts, features):
        self.calls += 1
        return [check(rid, "not_applicable" if rid in {"submission.feature", "submission.app_platform", "submission.app_version", "flow.explanation", "flow.keywords"} else "pass",
                      "合成演示结论，未经真实模型审计", ["body"]) for rid in semantic_rules(item)]


def run_demo(directory):
    directory = Path(directory).resolve()
    marker = directory / ".bug-audit-demo"
    if directory.exists() and any(directory.iterdir()) and (not marker.is_file() or marker.read_text(encoding="utf-8") != "synthetic-only-v1"):
        raise ConfigError("demo 只能使用空目录或本工具已标记的专用演示目录")
    directory.mkdir(parents=True, exist_ok=True)
    marker.write_text("synthetic-only-v1", encoding="utf-8")
    text = (Path(__file__).parent / "config.example.toml").read_text(encoding="utf-8")
    text = text.replace('profile = "firmware"', 'profile = "demo"').replace('data_dir = "./data"', 'data_dir = "."')
    text = text.replace("填写实际项目名称", "演示项目").replace("填写实际团队名称", "演示团队")
    path = directory / "worker-demo.toml"
    if path.exists() and path.read_text(encoding="utf-8") != text:
        raise ConfigError("演示配置已变化，不能覆盖，请使用新的空目录")
    path.write_text(text, encoding="utf-8")
    config = load_config(path)
    worker = Worker(config, {"tp": DemoTP(config), "model": DemoModel(), "demo": True})
    result = worker.run("demo", WINDOW)
    result["notice"] = "合成演示；未连接 TP、模型或飞书，未执行真实回填和通知"
    return result
