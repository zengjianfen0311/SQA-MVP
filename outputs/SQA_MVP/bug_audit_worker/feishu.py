"""机器人专用原生飞书适配器；摘要、上传、附件消息分别持久化。"""
import hashlib
import json
import re
import time
import uuid
from urllib.parse import quote

from .config import ConfigError
from .journal import digest
from .transport import HTTPClient,TransportError


class FeishuError(RuntimeError):
    def __init__(self,category,code=None):
        self.category,self.code=category,code
        super().__init__("飞书核验失败："+category+("（"+str(code)+"）" if code is not None else ""))


def content_value(value,mentions=None):
    if "zh_cn" in value:value=value["zh_cn"]
    if "file_key" in value:return {"file_key":value["file_key"]}
    mapped={m.get("key"):m.get("id") for m in (mentions or [])}
    rows=[]
    for row in value.get("content",[]):
        tags=[]
        for tag in row:
            kind=tag.get("tag")
            if kind=="at":tags.append({"tag":"at","user_id":mapped.get(tag.get("user_id"),tag.get("user_id"))})
            elif kind=="text":tags.append({"tag":"text","text":tag.get("text","")})
            elif kind=="a":tags.append({"tag":"a","text":tag.get("text", ""),"href":tag.get("href","")})
            else:raise FeishuError("unknown_message_tag")
        rows.append(tags)
    return {"title":value.get("title",""),"content":rows}


class FeishuClient:
    def __init__(self,config,http=None):
        self.config=config;self.cfg=config.data["feishu"]
        self.http=http or HTTPClient(self.cfg["base_url"],**config.section("transport"))
        self._access=None;self._until=0

    def token(self):
        if self._access and time.monotonic()<self._until:return self._access
        result=self.http.json("POST","open-apis/auth/v3/tenant_access_token/internal",payload={"app_id":self.cfg["app_id"],"app_secret":self.config.secret("feishu")})
        if result.get("code")!=0 or not isinstance(result.get("tenant_access_token"),str):raise FeishuError("authentication",result.get("code"))
        self._access=result["tenant_access_token"];self._until=time.monotonic()+max(0,int(result.get("expire",0))-60)
        return self._access

    def api(self,method,path,query=None,payload=None):
        result=self.http.json(method,"open-apis/"+path,query=query,headers={"Authorization":"Bearer "+self.token()},payload=payload)
        if not isinstance(result,dict) or result.get("code")!=0:raise FeishuError("api_rejected",result.get("code") if isinstance(result,dict) else None)
        return result

    def probe(self):
        try:
            bot=self.api("GET","bot/v3/info")["bot"]
            if bot.get("open_id")!=self.cfg["bot_open_id"] or bot.get("activate_status")!=2:raise FeishuError("bot_identity")
            chat=quote(self.cfg["chat_id"],safe="")
            group=self.api("GET","im/v1/chats/"+chat)["data"]
            if group.get("name")!=self.cfg["chat_name"]:raise FeishuError("chat_identity")
            members=set();page=None;seen=set()
            for _ in range(self.config.data["limits"]["max_pages"]):
                params={"member_id_type":"open_id","page_size":100}
                if page:params["page_token"]=page
                result=self.api("GET","im/v1/chats/"+chat+"/members",query=params)["data"]
                members.update(m["member_id"] for m in result["items"])
                if result.get("has_more") is False:break
                page=result.get("page_token")
                if not page or page in seen:raise FeishuError("member_pagination")
                seen.add(page)
            else:raise FeishuError("member_page_limit")
            if not {m["open_id"] for m in self.cfg["mentions"]}<=members:raise FeishuError("mention_members_missing")
            return {"status":"ready","target":self.cfg["chat_name"]}
        except (TransportError,FeishuError,ConfigError,KeyError,TypeError) as exc:
            return {"status":"blocked","issues":[str(exc) if isinstance(exc,(TransportError,FeishuError,ConfigError)) else "飞书响应结构无效"]}

    def _summary_content(self,report):
        rows=[[{"tag":"text","text":report["markdown"]}]]
        rows.append([{"tag":"at","user_id":m["open_id"]} for m in self.cfg["mentions"]])
        for link in report.get("links",[]):
            bid=str(link["bug_id"])
            if not bid.isdigit():raise ConfigError("通知 Bug ID 无效")
            rows.append([{"tag":"a","text":"Bug #"+bid,"href":self.config.data["tp"]["base_url"].rstrip("/")+"/entity/"+bid},
                         {"tag":"text","text":" "+link["issue"]}])
        return {"zh_cn":{"title":"固件Bug每日审计","content":rows}}

    def _read_message(self,message_id):
        if not re.fullmatch(r"om_[A-Za-z0-9_-]+",message_id):raise FeishuError("invalid_message_id")
        data=self.api("GET","im/v1/messages/"+message_id,query={"user_id_type":"open_id"})["data"]
        items=data.get("items",[])
        if len(items)!=1:raise FeishuError("message_unavailable")
        return items[0]

    def _verify(self,intent,message_id):
        message=self._read_message(message_id);details=intent["details"]
        if (message.get("chat_id")!=self.cfg["chat_id"] or message.get("sender",{}).get("id")!=self.cfg["app_id"]
                or message.get("sender",{}).get("id_type")!="app_id" or message.get("deleted") is not False
                or message.get("msg_type")!=details["msg_type"]):raise FeishuError("message_identity_or_state")
        content=json.loads(message["body"]["content"])
        if details["msg_type"]=="post":
            if digest(content_value(content,message.get("mentions")))!=details["content_hash"]:raise FeishuError("message_content_mismatch")
            if {m.get("id") for m in message.get("mentions",[])}!={m["open_id"] for m in self.cfg["mentions"]}:raise FeishuError("mentions_mismatch")
        else:
            if content.get("file_name",details["filename"])!=details["filename"]:raise FeishuError("attachment_name")
            # 飞书回读可能返回消息作用域的资源 Key；以已核验消息的实际 Key 下载，仍严格比对原文件哈希。
            resource_key=content.get("file_key")
            if not isinstance(resource_key,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}",resource_key):raise FeishuError("attachment_key")
            resource=self.http.request("GET","open-apis/im/v1/messages/"+message_id+"/resources/"+quote(resource_key,safe=""),query={"type":"file"},headers={"Authorization":"Bearer "+self.token()})
            if hashlib.sha256(resource.body).hexdigest()!=details["file_sha256"]:raise FeishuError("attachment_hash_mismatch")
        return {"message_id":message_id,"target":self.cfg["chat_name"],"idempotency_key":details["uuid"],"summary_hash":details["summary_hash"]}

    def _message(self,run_id,key,kind,content,details,journal,retry_failed=False):
        uid=str(uuid.uuid5(uuid.NAMESPACE_URL,key))
        details={**details,"action":"message","msg_type":kind,"content_hash":digest(content_value(content)),"uuid":uid}
        payload={"receive_id":self.cfg["chat_id"],"msg_type":kind,"content":json.dumps(content,ensure_ascii=False),"uuid":uid}
        intent=journal.prepare(run_id,key,"feishu_summary" if kind=="post" else "feishu_file",digest(payload),details)
        if intent["status"]=="verified":return intent
        if intent["status"]=="failed" and retry_failed and intent["receipt"].get("safe_retry"):
            journal.retry_failed(key);intent=journal.prepare(run_id,key,intent["kind"],digest(payload),details)
        if intent["status"]=="failed":return intent
        mid=intent["receipt"].get("message_id")
        if intent["status"]=="prepared":
            journal.inflight(key)
            try:
                mid=self.api("POST","im/v1/messages",query={"receive_id_type":"chat_id"},payload=payload)["data"]["message_id"]
            except (TransportError,FeishuError,KeyError) as exc:
                rejected=isinstance(exc,FeishuError) or isinstance(exc,TransportError) and not exc.uncertain and exc.status in {400,401,403,404,422}
                return journal.settle(key,"failed" if rejected else "unknown",{"safe_retry":rejected,"reason":"发送被拒绝" if rejected else "发送结果未知，需消息回执核验"})
        if not mid:return journal.settle(key,"unknown",{"reason":"缺少可在线核验的消息 ID"})
        try:
            receipt=self._verify(intent,mid)
            return journal.settle(key,"verified",receipt)
        except (TransportError,FeishuError,ValueError,KeyError,TypeError):
            return journal.settle(key,"unknown",{"message_id":mid,"reason":"消息或附件回读未通过"})

    def _upload(self,run_id,key,report,journal,retry_failed=False):
        sha=hashlib.sha256(report["csv_bytes"]).hexdigest()
        details={"action":"upload","filename":report["filename"],"file_sha256":sha}
        intent=journal.prepare(run_id,key,"feishu_file",digest(details),details)
        if intent["status"]=="verified":return intent
        if intent["status"]=="failed" and retry_failed and intent["receipt"].get("safe_retry"):
            journal.retry_failed(key);intent=journal.prepare(run_id,key,"feishu_file",digest(details),details)
        if intent["status"]!="prepared":return intent
        boundary="audit"+uuid.uuid4().hex
        prefix=("--"+boundary+'\r\nContent-Disposition: form-data; name="file_type"\r\n\r\nstream\r\n--'+boundary+'\r\nContent-Disposition: form-data; name="file_name"\r\n\r\n'+report["filename"]+'\r\n--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="'+report["filename"]+'"\r\nContent-Type: text/csv\r\n\r\n').encode("utf-8")
        body=prefix+report["csv_bytes"]+("\r\n--"+boundary+"--\r\n").encode()
        journal.inflight(key)
        try:
            response=self.http.request("POST","open-apis/im/v1/files",headers={"Authorization":"Bearer "+self.token(),"Content-Type":"multipart/form-data; boundary="+boundary},body=body)
            result=json.loads(response.body)
            if result.get("code")!=0:raise FeishuError("upload_rejected",result.get("code"))
            return journal.settle(key,"verified",{"file_key":result["data"]["file_key"],"file_sha256":sha})
        except (TransportError,FeishuError,ValueError,KeyError) as exc:
            rejected=isinstance(exc,FeishuError) or isinstance(exc,TransportError) and not exc.uncertain and exc.status in {400,401,403,404,422}
            return journal.settle(key,"failed" if rejected else "unknown",{"safe_retry":rejected,"reason":"附件上传未完成"})

    def notify(self,run_id,report,journal,retry_failed=False):
        probe=self.probe()
        if probe["status"]!="ready":return probe
        prefix=str(run_id)+":feishu:"
        summary_hash=hashlib.sha256(report["markdown"].encode()).hexdigest()
        summary=self._message(run_id,prefix+"summary","post",self._summary_content(report),{"summary_hash":summary_hash},journal,retry_failed)
        if summary["status"]!="verified":return {"status":summary["status"],"receipts":[],"issues":[summary["receipt"].get("reason","")]}
        upload=self._upload(run_id,prefix+"upload",report,journal,retry_failed)
        if upload["status"]!="verified":return {"status":"partial","receipts":[summary["receipt"]],"issues":["附件上传未核验"]}
        key=upload["receipt"]["file_key"]
        attachment=self._message(run_id,prefix+"file","file",{"file_key":key},{"summary_hash":summary_hash,"file_key":key,"filename":report["filename"],"file_sha256":upload["receipt"]["file_sha256"]},journal,retry_failed)
        return {"status":"verified" if attachment["status"]=="verified" else "partial",
                "receipts":[i["receipt"] for i in (summary,attachment) if i["status"]=="verified"],
                "issues":[] if attachment["status"]=="verified" else ["附件消息未完成核验"]}

    def recover(self,run_id,journal,receipts=None):
        intents={i["operation_key"]:i for i in journal.intents(run_id) if i["details"].get("action")=="message"}
        for key,mid in (receipts or {}).items():
            if key not in intents:raise ConfigError("回执不是本次运行的消息操作")
            intent=intents[key]
            receipt=self._verify(intent,mid)
            journal.settle(key,"verified",receipt)
        for intent in intents.values():
            if intent["status"] in {"in_flight","unknown"} and intent["receipt"].get("message_id"):
                try:journal.settle(intent["operation_key"],"verified",self._verify(intent,intent["receipt"]["message_id"]))
                except (TransportError,FeishuError,ValueError,KeyError):pass
        return self.check_delivery(run_id,journal)

    def check_delivery(self,run_id,journal):
        intents=[i for i in journal.intents(run_id) if i["details"].get("action")=="message"]
        verified=[]
        for intent in intents:
            mid=intent["receipt"].get("message_id")
            if not mid:continue
            try:verified.append(self._verify(intent,mid))
            except (TransportError,FeishuError,ValueError,KeyError,TypeError):pass
        return {"status":"verified" if len(verified)==2 and len(intents)==2 else "partial","receipts":verified}
