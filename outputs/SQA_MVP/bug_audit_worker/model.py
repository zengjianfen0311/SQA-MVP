"""公司模型适配；输出仅为不可信检查结果，不执行模型工具或命令。"""
import html
import json
import re
from pathlib import Path

from .rules import RULE_VERSION, check, semantic_rules
from .transport import HTTPClient, TransportError


def redact(value):
    text=html.unescape(re.sub(r"<[^>]*>"," ",str(value or "")))
    text=re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}","[邮箱]",text)
    text=re.sub(r"(?i)\b(?:access[_-]?token|api[_-]?key|app[_-]?secret|cookie|authorization|device[_-]?id|serial|imei)\s*[:=]\s*[^\s;,<>]+","[敏感信息]",text)
    text=re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._-]+","[凭据]",text)
    return text


def submitted_fields(item):
    return {k:redact(v) for k,v in item.get("fields",{}).items()
            if any(part in k.casefold() for part in ("firmware","suunto app","reproduc","category","reg issue","previously"))}


def pending(item, reason):
    return [check(rid,"pending",reason) for rid in semantic_rules(item)]


def validate_review(response, item, features, diagnostics=None):
    required=semantic_rules(item)
    if not isinstance(response,dict) or set(response)!={"schema_version","checks","coverage"} or response.get("schema_version")!=1:
        return pending(item,"模型输出结构无效")
    coverage=response.get("coverage")
    expected_comments=[str(c["id"]) for c in item.get("comments",[])]
    expected_fields=list(submitted_fields(item))
    if (not isinstance(coverage,dict) or set(coverage)!={"body","comment_ids","field_names"}
            or coverage.get("body") is not True or not isinstance(coverage.get("comment_ids"),list)
            or not isinstance(coverage.get("field_names"),list)
            or sorted(coverage["comment_ids"])!=sorted(expected_comments)
            or sorted(coverage["field_names"])!=sorted(expected_fields)):
        return pending(item,"模型未覆盖全部正文、字段和有效评论")
    results=response.get("checks")
    if not isinstance(results,list) or len(results)!=len(required) or any(not isinstance(r,dict) for r in results):
        return pending(item,"模型检查项缺失或重复")
    if {r.get("rule_id") for r in results}!=set(required):return pending(item,"模型检查项与规则版本不一致")
    known_features={str(f["id"]) for f in features.get("items",[])}|{str(f["id"]) for f in features.get("details",[])}
    detailed={str(f["id"]) for f in features.get("details",[]) if isinstance(f.get("description"),str) and redact(f["description"]).strip()}
    known={"body","snapshot","comments"}|{"comment:"+c for c in expected_comments}|{"field:"+f for f in expected_fields}|{"feature:"+f for f in known_features}
    by_id={r["rule_id"]:r for r in results};output=[]
    if diagnostics is not None: diagnostics["valid"] = True
    for rid in required:
        result=by_id[rid]
        status=result.get("status");reason=result.get("reason");refs=result.get("evidence_refs")
        valid=(set(result)=={"rule_id","status","reason","evidence_refs"}
               and status in {"pass","fail","pending","not_applicable"} and isinstance(reason,str) and 1<=len(reason)<=240
               and redact(reason)==reason and isinstance(refs,list) and all(isinstance(ref,str) and ref in known for ref in refs)
               and (status not in {"pass","fail"} or bool(refs)))
        if not valid:
            if diagnostics is not None: diagnostics["valid"] = False
            output.append(check(rid,"pending","模型引用或检查结论无效"));continue
        state = str(item.get("snapshot", {}).get("state", "")).casefold()
        mandatory = {"submission.firmware", "submission.reproducibility", "flow.state_consistency"}
        if state in {"invalid", "duplicate", "wont fix", "later", "reproduce", "needs info"}:
            mandatory.add("flow.explanation")
        if state in {"invalid", "reproduce", "needs info"}:
            mandatory.add("flow.keywords")
        if item.get("feature_id"):
            mandatory.add("submission.feature")
        if status=="not_applicable" and rid in mandatory:
            output.append(check(rid,"pending","模型跳过了必须审查的检查项"));continue
        if item.get("evidence",{}).get("comments")!="complete" or item.get("evidence",{}).get("description")!="complete":
            output.append(check(rid,"pending","正文或评论证据不完整"));continue
        validated=check(rid,status,reason,refs)
        if rid=="submission.feature" and status in {"pass","fail","pending"}:
            candidates=[ref.split(":",1)[1] for ref in refs if ref.startswith("feature:")]
            if features.get("status")!="complete":
                validated=check(rid,"pending","Feature 候选范围未完整核实")
            elif not candidates and status!="pending":
                validated=check(rid,"pending","Feature 适用性缺少实际候选证据")
            elif any(value not in detailed for value in candidates):
                validated=check(rid,"pending","Feature 候选细节未核实",refs)
                validated["requested_feature_ids"]=sorted({int(value) for value in candidates if value not in detailed})
        output.append(validated)
    return output


def response_schema():
    strings={"type":"array","items":{"type":"string"}}
    check_schema={"type":"object","additionalProperties":False,
                  "required":["rule_id","status","reason","evidence_refs"],"properties":{
                      "rule_id":{"type":"string"},"status":{"type":"string","enum":["pass","fail","pending","not_applicable"]},
                      "reason":{"type":"string"},"evidence_refs":strings}}
    coverage={"type":"object","additionalProperties":False,"required":["body","comment_ids","field_names"],
              "properties":{"body":{"type":"boolean"},"comment_ids":strings,"field_names":strings}}
    return {"type":"object","additionalProperties":False,"required":["schema_version","checks","coverage"],
            "properties":{"schema_version":{"type":"integer","enum":[1]},
                          "checks":{"type":"array","items":check_schema},"coverage":coverage}}


class ModelClient:
    def __init__(self,config,http=None):
        self.config=config;self.calls=0
        self.http=http or HTTPClient(config.data["model"]["base_url"],**config.section("transport"))

    def review(self,item,facts,features):
        self.last_validation = {"valid": False}
        # 已关联 Feature 只核对该实体的明确描述，不要求重新搜索整个项目。
        if item.get("feature_id"):
            linked = [f for f in features.get("details", []) if str(f.get("id")) == str(item["feature_id"])
                      and f.get("projectId") == self.config.data["scope"]["project_id"]
                      and isinstance(f.get("description"), str) and redact(f["description"]).strip()]
            if len(linked) == 1:
                features = {"items": [{"id": linked[0]["id"], "name": linked[0].get("name", "")}], "details": linked, "status": "complete"}
        cfg=self.config.data["model"]
        payload={"rule_version":RULE_VERSION,"required_checks":semantic_rules(item),"body":redact(item.get("body")),
                 "snapshot":{k:redact(v) for k,v in item.get("snapshot",{}).items()},"fields":submitted_fields(item),
                 "comments":[{"id":str(c["id"]),"at":c.get("at"),"text":redact(c.get("text"))} for c in item.get("comments",[])],
                 "facts":facts,"feature_id":item.get("feature_id"),
                 "features":{"items":[{"id":f["id"],"name":redact(f.get("name"))} for f in features.get("items",[])],
                             "details":[{"id":f["id"],"name":redact(f.get("name")),"description":redact(f.get("description"))} for f in features.get("details",[])],"status":features.get("status")}}
        root=Path(__file__).parent
        instructions=(root/"prompts"/"semantic-review.md").read_text(encoding="utf-8")+"\n"+(root/"rules"/(RULE_VERSION+".md")).read_text(encoding="utf-8")
        feature_issue = None
        validation_features = features
        if "submission.feature" not in payload["required_checks"]:
            payload["features"] = {"items":[],"details":[],"status":"not_requested"}
            validation_features = payload["features"]
        elif features.get("status") != "complete":
            feature_issue = "Feature 候选范围未完整获取"
            payload["features"] = {"items":[],"details":[],"status":"partial"}
            validation_features = payload["features"]
        user=json.dumps(payload,ensure_ascii=False,allow_nan=False)
        if len(user)+len(instructions)>cfg["max_input_chars"] and "submission.feature" in payload["required_checks"]:
            feature_issue = "Feature 索引超出上下文预算，未据此判断适用性"
            payload["features"] = {"items":[],"details":[],"status":"partial"}
            validation_features = payload["features"]
            user=json.dumps(payload,ensure_ascii=False,allow_nan=False)
        if len(user)+len(instructions)>cfg["max_input_chars"]:return pending(item,"上下文预算不足，未截断任何评论")
        if self.calls>=self.config.data["limits"]["max_model_calls"]:return pending(item,"模型调用预算已用完")
        messages=[{"role":"system","content":instructions},{"role":"user","content":user}]
        if cfg["api_style"]=="chat_completions":
            body={"model":cfg["name"],"messages":messages,"max_completion_tokens":cfg["max_output_tokens"]}
            body["response_format"]={"type":"json_schema","json_schema":{"name":"bug_audit_review","strict":True,"schema":response_schema()}} if cfg["structured_mode"]=="schema" else {"type":"json_object"}
            path="chat/completions"
        else:
            body={"model":cfg["name"],"input":messages,"max_output_tokens":cfg["max_output_tokens"],"store":False}
            body["text"]={"format":{"type":"json_schema","name":"bug_audit_review","strict":True,"schema":response_schema()}} if cfg["structured_mode"]=="schema" else {"format":{"type":"json_object"}}
            path="responses"
        try:
            key=self.config.secret("model");self.calls+=1
            result=self.http.json("POST",path,headers={"Authorization":"Bearer "+key},payload=body)
            if cfg["api_style"]=="chat_completions":
                choice=result["choices"][0];message=choice["message"]
                if choice.get("finish_reason")!="stop" or message.get("refusal") or message.get("tool_calls"):return pending(item,"模型拒绝、截断或返回工具调用")
                text=message["content"]
            else:
                if result.get("status")!="completed":return pending(item,"模型响应未完成")
                outputs=result.get("output",[])
                if any(o.get("type") not in {"message","reasoning"} for o in outputs):return pending(item,"模型返回了未允许的调用")
                content=[c for o in outputs if o.get("type")=="message" for c in o.get("content",[])]
                if any(c.get("type")!="output_text" for c in content):return pending(item,"模型拒绝或输出类型无效")
                text="".join(c["text"] for c in content)
            checks = validate_review(json.loads(text),item,validation_features,self.last_validation)
            if feature_issue:
                checks = [check(c["rule_id"],"pending",feature_issue) if c["rule_id"]=="submission.feature" else c for c in checks]
            return checks
        except (TransportError,ValueError,KeyError,IndexError,TypeError):
            return pending(item,"公司模型调用或响应校验失败")

    def probe(self):
        synthetic={"created_at":"2025-01-01T00:00:00+08:00","snapshot":{"state":"Open"},"body":"合成连通性测试，无真实业务资料", "fields":{},"comments":[],
                   "evidence":{"comments":"complete","description":"complete"}}
        checks=self.review(synthetic,{}, {"items":[],"details":[],"status":"complete"})
        return {"status":"ready" if self.last_validation["valid"] else "blocked","calls":self.calls,
                "semantic_status":"pending" if any(c["status"]=="pending" for c in checks) else "complete"}
