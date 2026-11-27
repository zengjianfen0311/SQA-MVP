"""由真实历史快照计算旧新值，不将 Add 或同值更新作为候选。"""
import re
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import ConfigError

SHANGHAI = ZoneInfo("Asia/Shanghai")


def timestamp(value):
    try:
        if isinstance(value, str) and value.startswith("/Date("):
            match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d+)?\)/", value)
            if not match: raise ValueError()
            parsed = datetime.fromtimestamp(int(match[1])/1000, timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None: raise ValueError()
        return parsed.astimezone(SHANGHAI)
    except (ValueError, TypeError, OverflowError):
        raise ConfigError("时间戳无效或缺少时区") from None


def select_history(candidates, histories, window):
    start, end = timestamp(window["start"]), timestamp(window["end"])
    grouped = defaultdict(list)
    unresolved, items = [], []
    for entry in histories:
        grouped[str(entry.get("sourceEntityId"))].append(entry)
    for bug in candidates:
        bug_id = str(bug["id"])
        rows = grouped[bug_id]
        try:
            rows = sorted(rows, key=lambda h: (timestamp(h["date"]), int(h["id"])))
        except (ConfigError, KeyError, ValueError):
            unresolved.append({"bug_id": bug_id, "reason": "历史排序依据无效"}); continue
        if not rows:
            unresolved.append({"bug_id": bug_id, "reason": "缺少历史"}); continue
        previous, hits = None, {}
        for row in rows:
            kind = str(row.get("modification", "")).lower()
            if start <= timestamp(row["date"]) < end:
                if kind not in {"add", "update", "delete"}:
                    unresolved.append({"bug_id": bug_id, "reason": "历史操作类型未知"})
                if kind == "update":
                    changes = row.get("changes")
                    if not isinstance(changes, str):
                        unresolved.append({"bug_id": bug_id, "reason": "缺少变更字段依据"})
                    else:
                        names = set(changes.split(","))
                        for source, key, field in (("EntityState", "stateName", "State"), ("State", "stateName", "State"), ("Severity", "severityName", "Severity")):
                            if source not in names: continue
                            old = previous.get(key) if previous else None
                            new = row.get(key)
                            if not isinstance(old, str) or not isinstance(new, str) or not old or not new:
                                unresolved.append({"bug_id": bug_id, "history_id": str(row["id"]), "field": field, "reason": "旧新值未核实"})
                            elif old != new:
                                hits[(str(row["id"]), field)] = {"id": str(row["id"]), "kind": "Change", "changed_at": timestamp(row["date"]).isoformat(),
                                    "field": field, "old_value": old, "new_value": new}
            previous = row
        if hits:
            items.append({"bug_id": bug_id, "history": list(hits.values())})
    return items, unresolved
