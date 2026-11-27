"""版本化事实与字段汇总；语义检查缺失时不自动判通过。"""
import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path

from .history import SHANGHAI, timestamp
from .tp import FIELDS

RULE_VERSION = "2026-11-10"
SUBMISSION = ("submission.firmware", "submission.reproducibility", "submission.app_platform", "submission.app_version", "submission.feature", "submission.category_detail")
FLOW = ("flow.explanation", "flow.keywords", "flow.state_consistency")
MAPPING = {
    "submission.firmware": "Firmware version is missing", "submission.reproducibility": "Reproducibility is missing",
    "submission.app_platform": "Suunto app platform is missing", "submission.app_version": "Suunto app version is missing",
    "submission.feature": "Feature is missing", "submission.category": "Bug Category is missing",
    "submission.reg": "Previously passed version is missing", "flow.first_response": "Bug was not promptly transferred to developer",
    "flow.keyword_presence": "State transferred without required [Keywords]", "flow.keywords": "State transferred without required [Keywords]",
    "flow.comment_presence": "State transferred without comment",
}
LABELS = {"flow.first_response":"流转超时", "flow.relations":"关联关系异常", "flow.explanation":"流转说明不足",
          "flow.comment_presence":"流转说明不足", "flow.keyword_presence":"流转说明不足", "flow.keywords":"流转说明不足",
          "flow.state_consistency":"状态与结论不符", "submission.category_detail":"信息不一致"}


def rules_digest():
    text = (Path(__file__).parent / "rules" / (RULE_VERSION + ".md")).read_text(encoding="utf-8-sig")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def old_bug(item):
    try: return timestamp(item["created_at"]) < datetime(2026,1,1,tzinfo=SHANGHAI)
    except Exception: return None


def semantic_rules(item):
    return list(FLOW if old_bug(item) is True else SUBMISSION + FLOW)


def working_hours(start, end):
    start, end = timestamp(start), timestamp(end)
    if end < start: raise ValueError("计时结束早于开始")
    seconds = 0
    while start < end:
        next_day = datetime.combine(start.date()+timedelta(days=1),datetime.min.time(),SHANGHAI)
        stop = min(end,next_day)
        if start.weekday() < 5: seconds += (stop-start).total_seconds()
        start = stop
    return seconds/3600


def build_facts(item, window):
    result = {"old_bug":old_bug(item), "created_at":item.get("created_at"), "audit_at":window["end"],
              "first_severity":None, "severity_complete":False, "first_nonnew_at":None}
    try:
        history = sorted(item.get("lifecycle",[]),key=lambda h:(timestamp(h["date"]),int(h["id"])))
        adds = [h for h in history if h.get("modification","").lower()=="add"]
        complete = item.get("evidence",{}).get("history")=="complete" and len(adds)==1
        if not complete: return result
        first = adds[0]
        severity = first.get("severityName")
        result["severity_complete"] = isinstance(severity,str) and bool(severity)
        edited = False
        for h in history:
            if timestamp(h["date"]) < timestamp(first["date"]): continue
            value = h.get("severityName")
            if not isinstance(value,str) or not value:
                result["severity_complete"]=False
            elif not edited and value != severity:
                severity=value;edited=True
            if h.get("stateName") and h["stateName"]!="New" and result["first_nonnew_at"] is None:
                result["first_nonnew_at"]=h["date"]
        result["first_severity"]=severity
        last=history[-1] if history else {}
        if last.get("stateName") != item.get("snapshot",{}).get("state") or last.get("severityName") != item.get("snapshot",{}).get("severity"):
            result["severity_complete"]=False
    except (ValueError, KeyError, TypeError):
        result["severity_complete"]=False
    return result


def check(rule_id, status, reason, refs=None, option=None):
    result={"rule_id":rule_id,"status":status,"reason":reason,"evidence_refs":refs or []}
    if option:result["option"]=option
    return result


def deterministic_checks(item, facts):
    fields=item.get("fields",{}); old=facts["old_bug"]; checks=[]
    if old is True:
        checks += [check("submission.category","not_applicable","旧单提单豁免"),check("submission.reg","not_applicable","旧单提单豁免")]
    else:
        value=fields.get("Bug Category")
        checks.append(check("submission.category","pass" if isinstance(value,str) and value.strip() else "fail","Bug Category 缺失" if not value else "已填写分类",["field:Bug Category"]))
        reg=fields.get("REG issue")
        if isinstance(reg,str) and reg.casefold()=="yes":
            value=fields.get("Previously passed version")
            checks.append(check("submission.reg","pass" if isinstance(value,str) and value.strip() else "fail","此前正常版本字段缺失" if not value else "回归版本已填写",["field:REG issue","field:Previously passed version"]))
        elif isinstance(reg,str) and reg.casefold()=="no":
            checks.append(check("submission.reg","not_applicable","非 REG issue",["field:REG issue"]))
        else: checks.append(check("submission.reg","pending","REG issue 尚未明确",["field:REG issue"]))
    severity=facts.get("first_severity")
    if not facts.get("severity_complete"):
        checks.append(check("flow.first_response","pending","首次 Severity 修改或完整历史未核实"))
    elif severity not in {"Critical","Blocking"}:
        checks.append(check("flow.first_response","not_applicable","该首次严重度未设置响应时限",["history"]))
    else:
        try:
            hours=working_hours(facts["created_at"],facts["first_nonnew_at"] or facts["audit_at"])
            limit=72 if severity=="Critical" else 48
            checks.append(check("flow.first_response","fail" if hours>limit else "pass",severity+" 首次响应"+("超" if hours>limit else "未超")+str(limit)+"小时",["history"]))
        except (ValueError,KeyError,TypeError): checks.append(check("flow.first_response","pending","首次响应计时依据无效"))
    issues,unknown=[],False
    rel=item.get("relations",{})
    if item.get("evidence",{}).get("relations")!="complete": unknown=True
    for direction in ("inbound","outbound"):
        entries=rel.get(direction)
        if not isinstance(entries,list): unknown=True; continue
        for target in entries:
            entity=str(target.get("targetEntity","")).casefold();tid=target.get("targetId")
            if not entity or entity=="general" or type(tid) is not int: unknown=True; continue
            if entity!="bug":continue
            if (direction=="inbound" and tid>=int(item["bug_id"])) or (direction=="outbound" and tid<=int(item["bug_id"])):
                issues.append(direction+" "+str(tid))
    option="Relations is wrong"
    if item.get("snapshot",{}).get("state")=="Duplicate" and not unknown and not rel.get("inbound") and not rel.get("outbound"):
        issues.append("Duplicate 无实际 Relations");option="Duplicate state is wrong"
    checks.append(check("flow.relations","pending" if unknown else "fail" if issues else "pass","目标类型/方向未核实" if unknown else "、".join(issues) or "关联方向已核验",["relations"],option))
    state=item.get("snapshot",{}).get("state")
    needed=state in {"Invalid","Reproduce","Needs info","Duplicate","Wont fix","Later"}
    comments=item.get("comments",[])
    if needed and item.get("evidence",{}).get("comments")!="complete":
        checks.append(check("flow.comment_presence","pending","评论未完整读取"))
    else:
        checks.append(check("flow.comment_presence","fail" if needed and not comments else "pass","无必要说明评论" if needed and not comments else "评论集合已核验",["comments"]))
    if state in {"Invalid","Reproduce","Needs info"}:
        if item.get("evidence",{}).get("comments")!="complete":
            checks.append(check("flow.keyword_presence","pending","关键词证据未完整读取"))
        else:
            text="\n".join(c.get("text","") for c in comments)
            pattern=r"(?<![A-Za-z])invalid(?![A-Za-z])" if state=="Invalid" else r"复现|复测|测试|(?<![A-Za-z])(?:reproduc(?:e|ed|ing|tion)|retest(?:ed|ing)?|test(?:ed|ing)?)(?![A-Za-z])"
            checks.append(check("flow.keyword_presence","pass" if re.search(pattern,text,re.I) else "fail","所需关键词存在" if re.search(pattern,text,re.I) else "缺少所需关键词",["comments"]))
    else: checks.append(check("flow.keyword_presence","not_applicable","当前状态无已定义关键词要求"))
    return checks


def assemble_findings(item, checks, options):
    by_id={c["rule_id"]:c for c in checks}
    old=old_bug(item)
    state=item.get("snapshot",{}).get("state","")
    testing=[rid for rid in SUBMISSION if rid!="submission.category_detail" and not (rid=="submission.feature" and item.get("feature_id"))]+["submission.category","submission.reg","flow.first_response"] if old is not True else []
    development=["flow.relations","flow.comment_presence","flow.keyword_presence"]
    if state in {"Invalid","Duplicate","Wont fix","Later"}:development.append("flow.explanation")
    if state in {"Invalid","Reproduce","Needs info"}:development.append("flow.keywords")
    if old is True:
        for rid in SUBMISSION: by_id[rid]=check(rid,"not_applicable","旧单提单豁免")
    # 文字专属检查继续参与质量结论，不能连带阻塞没有对应选项的多选字段。
    required=set(testing+development+["flow.first_response"]+list(FLOW))
    if old is not True:required.update(SUBMISSION)
    for rid in required:
        by_id.setdefault(rid,check(rid,"pending","该项语义审计尚未完成"))
    findings=[]
    for name,ids in ((FIELDS[0],testing),(FIELDS[1],development)):
        chosen=[];pending=[]
        if name==FIELDS[0] and old is True: chosen=["No need"]
        elif name==FIELDS[0] and old is None: pending.append("创建日期未核实")
        for rid in ids:
            result=by_id[rid]
            if result["status"]=="pending":pending.append(result["reason"])
            elif result["status"]=="fail":
                option=result.get("option") or MAPPING.get(rid)
                if rid=="flow.explanation": option={"Invalid":"Invalid evidence is missing","Duplicate":"Duplicate evidence is missing","Wont fix":"Wont fix evidence is missing","Later":"Later evidence is missing"}.get(state)
                if rid=="submission.feature" and item.get("feature_id"): option=None
                if option: chosen.append(option)
        chosen=sorted(set(chosen)) or ["OK"]
        if any(value not in options.get(name,[]) for value in chosen):pending.append("目标系统缺少准确选项映射")
        findings.append({"name":name,"original_value":item.get("original",{}).get(name),"proposed_value":None if pending else ",".join(chosen),
                         "ready":not pending,"reason":"；".join(dict.fromkeys(pending))[:1000]})
    applicable=[by_id[rid] for rid in sorted(required)]
    pending=[c["reason"] for c in applicable if c["status"]=="pending"]
    short_details={"submission.firmware":"固件版本", "submission.reproducibility":"复现情况",
                   "submission.app_platform":"Suunto App平台", "submission.app_version":"Suunto App版本",
                   "submission.feature":"Feature关联", "submission.reg":"此前正常版本"}
    issues=[]
    for c in applicable:
        if c["status"]!="fail":continue
        rid=c["rule_id"]
        # 分类为空已有确定性缺失结论，不将同一缺失再归成分类不一致。
        if rid=="submission.category_detail" and by_id.get("submission.category",{}).get("status")=="fail":continue
        detail=short_details.get(rid,str(c["reason"])[:70])
        issues.append(LABELS.get(rid,"信息缺失")+"（"+detail+"）")
    findings.append({"name":FIELDS[2],"original_value":item.get("original",{}).get(FIELDS[2]),
                     "proposed_value":None if pending else "；".join(issues) or "无异常","ready":not pending,
                     "reason":"；".join(dict.fromkeys(pending))[:1000]})
    return findings
