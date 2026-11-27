"""本审计流程所需 TP 原生接口；只读获取与写入调用分开。"""
from collections import defaultdict
from urllib.parse import parse_qs, quote, urlsplit

from .config import ConfigError
from .history import select_history, timestamp
from .journal import digest
from .transport import HTTPClient, TransportError

FIELDS = ("Bug Standard Review of Testing", "Bug Standard Review of Development", "Bug quality audit")
BUG_SELECT = "{id,name,description,createDate,modifyDate,lastStateChangeDate,stateName:entityState.name,stateFinal:entityState.isFinal,severityName:severity.name,projectId:project.id,assignedTeamIds:assignedTeams.Select(team.id),assignedTeams:assignedTeams.Select(team.name),featureId:feature.id,featureName:feature.name,tags,customValues}"
HISTORY_SELECT = "{id,sourceEntityId,date,changes,modification,createDate,stateName:entityState.name,severityName:severity.name}"
RELATION_SELECT = "{id,inbound:inboundRelations.Select({id,targetId:inbound.id,targetEntity:inbound.entityType.name}),outbound:outboundRelations.Select({id,targetId:outbound.id,targetEntity:outbound.entityType.name}),inboundCount:inboundRelations.Count,outboundCount:outboundRelations.Count}"


def custom_fields(bug):
    values = bug.get("customValues", {}).get("customFields", [])
    if not isinstance(values, list): raise ConfigError("TP 自定义字段结构无效")
    return {field["name"]: field.get("value") for field in values}


def chunks(values, size):
    for offset in range(0, len(values), size): yield values[offset:offset+size]


def id_where(key, ids):
    if not ids or any(not str(value).isdigit() or int(value) <= 0 for value in ids):
        raise ConfigError("TP 查询需要明确正整数 ID 集")
    return key + " in [" + ",".join(map(str, ids)) + "]"


def business_fingerprint(item):
    return digest({k: item.get(k) for k in ("snapshot", "body", "created_at", "feature_id", "fields", "comments", "relations", "evidence", "lifecycle")})


def feature_index_hash(rows):
    return digest(sorted(({key: row.get(key) for key in ("id", "name", "projectId")} for row in rows), key=lambda row: str(row["id"])))


def feature_source_hash(row):
    return digest({key: row.get(key) for key in ("id", "name", "description", "projectId")})


def raw_business_hash(bug):
    value={k:v for k,v in bug.items() if k not in {"modifyDate","customValues"}}
    for key in ("assignedTeamIds","assignedTeams"):
        if isinstance(value.get(key),list):value[key]=sorted(value[key])
    value["customFields"]={k:v for k,v in custom_fields(bug).items() if k not in FIELDS}
    return digest(value)


class TPClient:
    def __init__(self, config, http=None):
        self.config = config
        self.http = http or HTTPClient(config.data["tp"]["base_url"], **config.section("transport"))

    def _query(self, query=None):
        return {**(query or {}), "access_token": self.config.secret("tp")}

    def rows(self, resource, where, select):
        if resource not in {"Bugs", "Features", "Projects", "Teams", "Comments", "BugHistories"} or not where.strip():
            raise ConfigError("TP 查询资源或范围无效")
        size = self.config.data["limits"]["page_size"]
        rows, seen, pages, skip, total, end, issue = [], set(), 0, 0, None, None, None
        for _ in range(self.config.data["limits"]["max_pages"]):
            try:
                payload = self.http.json("GET", "api/v2/" + resource, query=self._query({"where": where, "select": select, "take": size, "skip": skip, "orderBy": "id"}))
                if isinstance(payload, list):
                    items, has_next, next_value, declared = payload, False, None, None
                elif isinstance(payload, dict):
                    items = payload.get("items", payload.get("Items"))
                    has_next = "next" in payload or "Next" in payload
                    next_value = payload.get("next", payload.get("Next"))
                    declared = payload.get("total", payload.get("totalCount", payload.get("Total")))
                else: raise ConfigError("TP 分页结构无效")
                if not isinstance(items, list) or len(items) > size: raise ConfigError("TP 分页大小无效")
                if declared is not None:
                    if type(declared) is not int or declared < 0 or total is not None and declared != total: raise ConfigError("TP 分页总量变化")
                    total = declared
                ids = [str(row["id"]) for row in items if isinstance(row, dict) and type(row.get("id")) in {int, str}]
                if len(ids) != len(items) or len(set(ids)) != len(ids) or seen.intersection(ids): raise ConfigError("TP 分页 ID 缺失或重复")
                rows.extend(items); seen.update(ids); pages += 1
                if has_next and next_value in (None, ""):
                    end = "explicit_next_empty"; break
                if has_next:
                    if not isinstance(next_value, str): raise ConfigError("TP next 结构无效")
                    values = parse_qs(urlsplit(next_value).query).get("skip", [])
                    if len(values) != 1 or not values[0].isdigit() or not items or int(values[0]) != skip+len(items): raise ConfigError("TP next 跳页或未前进")
                    skip = int(values[0])
                elif total is not None and len(rows) >= total:
                    end = "declared_total"; break
                elif len(items) < size:
                    end = "short_page"; break
                else: skip += len(items)
            except (TransportError, ConfigError, KeyError, TypeError) as exc:
                issue = exc.category if isinstance(exc, TransportError) else "pagination_contract"
                break
        if total is not None and total != len(rows): issue = "total_mismatch"
        if not end and not issue: issue = "page_limit"
        return rows, {"status": "complete" if end and not issue else "partial" if rows else "failed", "count": len(rows), "pages": pages, "end_reason": end, "issue": issue}

    def _required(self, resource, where, select):
        rows, audit = self.rows(resource, where, select)
        if audit["status"] != "complete": raise ConfigError("TP " + resource + " 未完整获取")
        return rows

    def metadata(self):
        scope = self.config.data["scope"]
        projects = self._required("Projects", "id == " + str(scope["project_id"]), "{id,name}")
        teams = self._required("Teams", id_where("id", scope["team_ids"]), "{id,name}")
        if projects != [{"id": scope["project_id"], "name": scope["project_name"]}] or {t["id"]: t["name"] for t in teams} != dict(zip(scope["team_ids"], scope["team_names"])):
            raise ConfigError("TP 项目或团队与显式配置不一致")
        context = self.http.json("GET", "api/v2/Context/" + quote(self.config.data["tp"]["context_id"], safe=""), query=self._query())
        fields = {f["name"]: f for f in context.get("customFields", {}).get("items", []) if f.get("name") in FIELDS}
        if set(fields) != set(FIELDS): raise ConfigError("三个审计字段的在线元数据不完整")
        options = {}
        for name in FIELDS[:2]:
            value = fields[name].get("value")
            if not isinstance(value, str) or not value.strip(): raise ConfigError("审计选项元数据无效")
            options[name] = value.splitlines()
        return {"options": options, "status": "complete"}

    def _batch(self, resource, key, select, ids):
        output, status = [], "complete"
        for part in chunks(ids, self.config.data["limits"]["batch_size"]):
            rows, audit = self.rows(resource, id_where(key, part), select)
            membership_key = {"general.id": "generalId"}.get(key, key)
            if any(str(row.get(membership_key)) not in set(map(str, part)) for row in rows):
                raise ConfigError("TP 批量响应含范围外实体")
            output.extend(rows)
            if audit["status"] != "complete": status = "partial"
        return output, status

    def _hydrate(self, bugs, histories, history_status, window, metadata, candidate_count=None):
        ids = [bug["id"] for bug in bugs]
        selected, unresolved = select_history(bugs, histories, window)
        selected_ids = [int(item["bug_id"]) for item in selected]
        comments, comment_status = self._batch("Comments", "general.id", "{id,generalId:general.id,createDate,description}", selected_ids)
        relations, relation_status = self._batch("Bugs", "id", RELATION_SELECT, selected_ids)
        features, feature_audit = self.rows("Features", "project.id == " + str(self.config.data["scope"]["project_id"]), "{id,name,projectId:project.id}") if selected_ids else ([], {"status": "complete"})
        feature_status = feature_audit["status"]
        if len(features) > self.config.data["limits"]["max_features"]:
            features = []; feature_status = "partial"
        linked = sorted({bug.get("featureId") for bug in bugs if bug.get("featureId")})
        details, detail_status = self._batch("Features", "id", "{id,name,description,projectId:project.id}", linked)
        details = [{**row, "source_hash": feature_source_hash(row)} for row in details]
        if detail_status != "complete": feature_status = "partial"
        by_bug = {str(b["id"]): b for b in bugs}
        rels = {str(r["id"]): r for r in relations}
        cs, hs = defaultdict(list), defaultdict(list)
        for comment in comments: cs[str(comment["generalId"])].append(comment)
        for history in histories: hs[str(history["sourceEntityId"])].append(history)
        items = []
        for selection in selected:
            bid = selection["bug_id"]; bug = by_bug[bid]; fields = custom_fields(bug); rel = rels.get(bid, {})
            rel_complete = relation_status == "complete" and all(isinstance(rel.get(k), list) and rel.get(k + "Count") == len(rel[k]) for k in ("inbound", "outbound"))
            item = {**selection, "snapshot": {"title": bug.get("name", ""), "state": bug.get("stateName", ""), "severity": bug.get("severityName", ""),
                                             "team": ",".join(bug.get("assignedTeams", [])), "project": self.config.data["scope"]["project_name"]},
                    "body": bug.get("description", ""), "created_at": bug.get("createDate"), "feature_id": bug.get("featureId"),
                    "fields": {k:v for k,v in fields.items() if k not in FIELDS}, "original": {name: fields.get(name) for name in FIELDS},
                    "comments": [{"id": str(c["id"]), "at": c["createDate"], "text": c.get("description", "")} for c in cs[bid]],
                    "lifecycle": hs[bid], "relations": {k:rel.get(k) for k in ("inbound", "outbound")},
                    "evidence": {"history": history_status, "comments": comment_status, "relations": "complete" if rel_complete else "partial", "description": "complete", "attachments": "not_requested"}}
            item["source_hash"] = business_fingerprint(item)
            item["business_hash"] = raw_business_hash(bug)
            items.append(item)
        complete = not unresolved and history_status == comment_status == relation_status == feature_status == "complete" and all(i["evidence"]["relations"] == "complete" for i in items)
        return {"window": window, "scope": self.config.section("scope"), "candidate_count": len(bugs) if candidate_count is None else candidate_count,
                "items": items, "unresolved": unresolved, "completeness": "complete" if complete else "partial", "options": metadata["options"],
                "features": features, "feature_index_hash": feature_index_hash(features), "feature_details": details, "feature_status": feature_status}

    def feature_details(self, ids, index):
        known = {str(f["id"]) for f in index if f.get("projectId") == self.config.data["scope"]["project_id"]}
        if not ids or len(ids) > self.config.data["limits"]["max_features"] or not set(map(str, ids)) <= known:
            raise ConfigError("Feature 补取必须属于已读取项目索引且不超过预算")
        rows, status = self._batch("Features", "id", "{id,name,description,projectId:project.id}", ids)
        if status != "complete" or {str(r["id"]) for r in rows} != set(map(str, ids)) or any(r.get("projectId") != self.config.data["scope"]["project_id"] for r in rows):
            raise ConfigError("Feature 描述未完整获取或项目范围不符")
        return [{**row, "source_hash": feature_source_hash(row)} for row in rows]

    def collect(self, window):
        metadata = self.metadata(); scope = self.config.data["scope"]
        where = "project.id == " + str(scope["project_id"]) + " and assignedTeams.Where(" + id_where("team.id", scope["team_ids"]) + ").Count > 0 and modifyDate >= Convert.ToDateTime(\"" + timestamp(window["start"]).isoformat() + "\")"
        bugs, audit = self.rows("Bugs", where, BUG_SELECT)
        if any(b.get("projectId") != scope["project_id"] or not set(b.get("assignedTeamIds", [])) & set(scope["team_ids"]) for b in bugs): raise ConfigError("TP 候选包含配置范围外 Bug")
        histories, status = self._batch("BugHistories", "sourceEntityId", HISTORY_SELECT, [b["id"] for b in bugs])
        result = self._hydrate(bugs, histories, status, window, metadata)
        if audit["status"] != "complete": result["completeness"] = audit["status"]
        return result

    def refresh(self, ids, window):
        metadata = self.metadata()
        bugs, status = self._batch("Bugs", "id", BUG_SELECT, ids)
        scope = self.config.data["scope"]
        if status != "complete" or set(map(str, ids)) != {str(b["id"]) for b in bugs}: raise ConfigError("TP 预检主体未完整读取")
        if any(b.get("projectId") != scope["project_id"] or not set(b.get("assignedTeamIds", [])) & set(scope["team_ids"]) for b in bugs): raise ConfigError("TP 预检发现团队或项目已变化")
        histories, history_status = self._batch("BugHistories", "sourceEntityId", HISTORY_SELECT, ids)
        return self._hydrate(bugs, histories, history_status, window, metadata)

    def read_fields(self, ids):
        bugs, status = self._batch("Bugs", "id", BUG_SELECT, ids)
        if status != "complete" or {str(b["id"]) for b in bugs} != set(map(str, ids)): raise ConfigError("TP 写后回读不完整")
        return {str(b["id"]): {"original": {n:custom_fields(b).get(n) for n in FIELDS}, "bug": b} for b in bugs}

    def write_fields(self, bug_id, values):
        if not values or set(values) - set(FIELDS) or any(not isinstance(v, str) or not v for v in values.values()): raise ConfigError("TP 只允许三个已确认且非空的审计字段")
        id_where("id", [bug_id])
        payload = [{"Id": int(bug_id), "CustomFields": [{"Name": n, "Value": v} for n,v in values.items()]}]
        return self.http.json("POST", "api/v1/Bugs/bulk", query=self._query({"format": "json"}), payload=payload)
