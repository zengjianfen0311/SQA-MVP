"""仅导出必要审计结论，不导出原始评论、密钥或个人联系方式。"""
import csv
import io
from collections import Counter
from .model import redact
from .tp import FIELDS


def cell(value):
    text=redact(value)
    if text.lstrip().startswith(("=","+","-","@")):text="'"+text
    return text


def report_run(bundle,findings,receipts):
    items={item["bug_id"]:item for item in bundle["items"]}
    statuses=Counter()
    for intent in receipts:
        if intent.get("kind")=="tp":
            for field in intent.get("receipt",{}).get("fields",[]):statuses[field.get("status","unknown")]+=1
    pending=sum(not f["ready"] for item in findings for f in item["fields"])
    pending_bugs=sum(any(not f["ready"] for f in item["fields"]) for item in findings)
    summary={"candidate_count":bundle["candidate_count"],"selected_count":len(findings),"field_count":sum(len(i["fields"]) for i in findings),
             "pending_fields":pending,"pending_bugs":pending_bugs,"writeback":dict(statuses),"collection":bundle["completeness"]}
    lines=["固件 Bug 每日审计", "窗口："+bundle["window"]["start"]+"（含）至 "+bundle["window"]["end"]+"（不含）",
           "候选 "+str(summary["candidate_count"])+"；纳入 "+str(summary["selected_count"])+"；字段 "+str(summary["field_count"]),
           "完整审计 "+str(len(findings)-pending_bugs)+" 单；待确认 "+str(pending_bugs)+" 单 / "+str(pending)+" 字段。",
           "回填记录："+str(dict(statuses))+"；采集："+bundle["completeness"]]
    if not receipts:lines.append("仅分析或演示，未据此证明真实回填/通知完成。")
    stream=io.StringIO(newline="")
    writer=csv.writer(stream);writer.writerow(["Bug ID","标题","审计字段","原值","拟值","Ready","说明"])
    links=[]
    for finding in findings:
        bid=finding["bug_id"];item=items[bid]
        for field in finding["fields"]:
            writer.writerow([cell(value) for value in (bid,item.get("snapshot",{}).get("title",""),field["name"],field.get("original_value"),field.get("proposed_value"),str(field["ready"]),field.get("reason",""))])
            if field["name"]==FIELDS[2] and field.get("proposed_value") not in (None,"无异常"):
                links.append({"bug_id":bid,"issue":redact(field["proposed_value"])[:240]})
    return {"markdown":"\n".join(lines),"csv_bytes":stream.getvalue().encode("utf-8-sig"),"filename":"Bug审计明细.csv","summary":summary,"links":links[:10]}
