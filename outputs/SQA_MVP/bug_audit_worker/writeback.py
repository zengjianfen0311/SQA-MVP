"""三字段写回；意图和回执持久化先于台账投影，恢复绝不盲发。"""
from bug_audit import _same_field_value
from .config import ConfigError
from .journal import digest
from .tp import FIELDS, business_fingerprint, raw_business_hash, feature_index_hash, feature_source_hash
from .transport import TransportError


class Writeback:
    def __init__(self,tp,journal,audit):
        self.tp,self.journal,self.audit=tp,journal,audit

    def _project(self,run_id,intent):
        fields=intent["receipt"].get("fields",[])
        payload={"schema_version":1,"items":[{"bug_id":intent["details"]["bug_id"],"fields":fields}]}
        event="worker-writeback:"+digest(intent["operation_key"])[:24]+":"+digest(payload)[:24]
        self.audit.record_writeback(run_id,event,payload)

    def _read_receipt(self,intent,uncertain=False):
        details=intent["details"];bid=details["bug_id"]
        try:
            readback=self.tp.read_fields([bid])[bid]
        except (TransportError,ConfigError,KeyError):
            return "unknown",{"fields":[{**field,"status":"unknown","readback_value":None} for field in details["fields"]],"reason":"回读未完成"}
        business_ok=raw_business_hash(readback["bug"])==details["business_hash"]
        fields=[];statuses=[]
        for field in details["fields"]:
            name=field["name"];actual=readback["original"].get(name)
            result={"name":name,"preflight_value":field["preflight_value"],"readback_value":actual}
            if field.get("conflict"):
                result["status"]="unknown";statuses.append("conflict")
            elif not business_ok:
                result["status"]="unknown";statuses.append("unknown")
            elif _same_field_value(name,actual,details["desired"][name]):
                result["status"]="written" if name in details["requested"] else "skipped";statuses.append("verified")
            else:
                result["status"]="unknown" if uncertain else "failed";statuses.append(result["status"])
            fields.append(result)
        status=next((s for s in ("unknown","failed","conflict") if s in statuses),"verified")
        return status,{"fields":fields,"other_business_preserved":business_ok}

    def execute(self,run_id,plan):
        bundle=plan["bundle"];originals={i["bug_id"]:i for i in bundle["items"]}
        existing={i["operation_key"]:i for i in self.journal.intents(run_id)}
        for finding in plan["findings"]:
            bid=finding["bug_id"];key=str(run_id)+":tp:"+bid
            if key in existing:
                intent=existing[key]
                if intent["status"] in {"in_flight","unknown","failed"}:
                    status,receipt=self._read_receipt(intent,uncertain=True)
                    intent=self.journal.settle(key,status,receipt)
                if intent["status"]!="prepared":
                    self._project(run_id,intent);continue
                # prepared 明确尚未发请求，但仍需重新预检；请求内容不得变化。
            fields=finding["fields"]
            if any(f.get("name") not in FIELDS for f in fields):raise ConfigError("写回包含未授权字段")
            ready=[f for f in fields if f.get("ready") and f.get("proposed_value") is not None]
            if not ready:continue
            if any(not isinstance(f["proposed_value"],str) or not f["proposed_value"] for f in ready):raise ConfigError("可写拟值无效")
            for f in ready:
                if f["name"] in FIELDS[:2] and not set(f["proposed_value"].split(","))<=set(bundle["options"].get(f["name"],[])):
                    raise ConfigError("拟值不在已核验在线选项中")
            before=originals[bid]
            try:
                refreshed=self.tp.refresh([bid],bundle["window"])
                current=next(i for i in refreshed["items"] if i["bug_id"]==bid)
                changed=(business_fingerprint(current)!=before.get("source_hash",business_fingerprint(before))
                         or refreshed["completeness"]!="complete" or digest(refreshed["options"])!=digest(bundle["options"]))
                changed = changed or refreshed.get("feature_index_hash", feature_index_hash(refreshed["features"])) != bundle.get("feature_index_hash", feature_index_hash(bundle["features"]))
                used = {str(row["id"]): row for row in bundle.get("feature_details", []) + before.get("review_features", [])}
                if not changed and used:
                    actual = self.tp.feature_details([int(value) for value in used], refreshed["features"])
                    changed = any(row["source_hash"] != used[str(row["id"])].get("source_hash", feature_source_hash(used[str(row["id"])])) for row in actual)
            except (TransportError,ConfigError,StopIteration):
                current=None;changed=True
            if changed:
                results=[{"name":f["name"],"preflight_value":f["original_value"],"status":"unknown","readback_value":None} for f in ready]
                self.audit.record_writeback(run_id,"worker-source-changed:"+digest({"bid":bid,"results":results})[:32],{"schema_version":1,"items":[{"bug_id":bid,"fields":results}]})
                continue
            desired={f["name"]:f["proposed_value"] for f in ready}
            prefields=[];requested={}
            for f in ready:
                name=f["name"];value=current["original"][name]
                conflict=not _same_field_value(name,value,f["original_value"])
                prefields.append({"name":name,"preflight_value":value,"conflict":conflict})
                if not conflict and not _same_field_value(name,value,desired[name]):requested[name]=desired[name]
            details={"bug_id":bid,"fields":prefields,"desired":desired,"requested":list(requested),"business_hash":current["business_hash"]}
            intent=self.journal.prepare(run_id,key,"tp",digest({"id":bid,"fields":requested}),details)
            uncertain=False
            if requested:
                self.journal.inflight(key)
                try:self.tp.write_fields(bid,requested)
                except TransportError as exc:uncertain=exc.uncertain
            status,receipt=self._read_receipt(intent,uncertain)
            intent=self.journal.settle(key,status,receipt)
            self._project(run_id,intent)
        self.audit.record_writeback(run_id,"worker-writeback-summary:"+digest(self.journal.intents(run_id))[:24],{"schema_version":1,"items":[]})
        return self.audit.get_run(run_id)["writeback_status"]

    def recover(self,run_id):
        for intent in self.journal.intents(run_id):
            if intent["kind"]!="tp":continue
            if intent["status"] in {"in_flight","unknown","failed"}:
                status,receipt=self._read_receipt(intent,uncertain=True)
                intent=self.journal.settle(intent["operation_key"],status,receipt)
            if intent["status"]!="prepared":self._project(run_id,intent)
        return self.audit.get_run(run_id)["writeback_status"]
