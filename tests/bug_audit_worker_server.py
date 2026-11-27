"""受控 HTTP 业务系统，断言客户端真实请求而非仅模拟函数调用。"""
import copy
import json
import tempfile
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch

from bug_audit_worker.config import load_config
from tests.test_bug_audit_worker_config import EXAMPLE

FIELDS=("Bug Standard Review of Testing","Bug Standard Review of Development","Bug quality audit")

class WorkerServer:
    def __enter__(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.counts=Counter();self.requests=[];self.fields={name:None for name in FIELDS}
        self.fields.update({"Bug Category":"Functional","REG issue":"No"})
        self.state="Open";self.lose_tp_response=False;self.messages={};self.fail_file_send=False
        self.csv=b"";self.members_allowed=True;self.uploaded_file=None;self.lose_summary_response=False
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def handle_request(self):
                parsed=urlsplit(self.path);query={k:v[0] for k,v in parse_qs(parsed.query).items()}
                body=self.rfile.read(int(self.headers.get("Content-Length",0)))
                owner.counts[(self.command,parsed.path)]+=1
                owner.requests.append((self.command,parsed.path,query,body,dict(self.headers)))
                status,payload=owner.response(self.command,parsed.path,query,body,dict(self.headers))
                if status==0:
                    self.connection.close();self.close_connection=True;return
                self.send_response(status);self.end_headers()
                self.wfile.write(payload if isinstance(payload,bytes) else json.dumps(payload,ensure_ascii=False).encode())
            do_GET=handle_request
            do_POST=handle_request
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url="http://127.0.0.1:"+str(self.server.server_port)
        text=EXAMPLE.replace("https://tp.example.invalid",self.url).replace("https://model.example.invalid/v1",self.url+"/v1").replace("https://open.feishu.cn",self.url)
        text=text.replace("allow_apply = false",'allow_apply = true\nfirst_apply_date = "2026-01-01"\nlegacy_quiesced = true\nlegacy_last_run_date = "2025-12-31"')
        text+='\n[transport]\nallow_loopback_http=true\n'
        path=self.root/"worker.toml";path.write_text(text,encoding="utf-8");self.config=load_config(path)
        self.env=patch.dict("os.environ",{"TEST_TP_TOKEN":"synthetic-token","TEST_MODEL_KEY":"synthetic-model-key","TEST_FEISHU_SECRET":"synthetic-feishu-secret"});self.env.start()
        return self

    def __exit__(self,*_):
        self.env.stop();self.server.shutdown();self.server.server_close();self.thread.join(3);self.temp.cleanup()

    def bug(self):
        return {"id":1,"name":"Synthetic cross-feature reset","description":"Firmware 1.0, cross-feature reset; reproduce using navigation; expected no reset."+getattr(self,"extra_body",""),
                "createDate":"2026-11-23T12:00:00+08:00","stateName":self.state,"severityName":"Major", "projectId":1,
                "assignedTeamIds":[2],"assignedTeams":["A"],"featureId":None,
                "customValues":{"customFields":[{"name":name,"value":value} for name,value in self.fields.items()]}}

    def response(self,method,path,q,body,headers):
        intercept = getattr(self, "intercept", None)
        if intercept:
            answer = intercept(method, path, q, body, headers)
            if answer is not None: return answer
        if path=="/api/v1/Bugs/bulk":
            payload=json.loads(body)
            for bug in payload:
                if set(bug)!={"Id","CustomFields"} or bug["Id"]!=1:return 400,{}
                for field in bug["CustomFields"]:
                    if set(field)!={"Name","Value"} or field["Name"] not in FIELDS:return 400,{}
                    self.fields[field["Name"]]=field["Value"]
            return (0,{}) if self.lose_tp_response else (200,{"success":True})
        if path.endswith("/Projects"):return 200,{"items":[{"id":1,"name":"Synthetic"}],"next":None}
        if path.endswith("/Teams"):return 200,{"items":[{"id":2,"name":"A"},{"id":3,"name":"B"}],"next":None}
        if "/Context/" in path:return 200,{"customFields":{"items":[{"name":n,"value":"OK\nNo need\nRelations is wrong\nBug Category is missing\nPreviously passed version is missing"} for n in FIELDS]}}
        if path.endswith("/Bugs"):
            if "inboundRelations" in q["select"]:return 200,{"items":[{"id":1,"inbound":[],"outbound":[],"inboundCount":0,"outboundCount":0}],"next":None}
            return 200,{"items":[self.bug()],"next":None}
        if path.endswith("/BugHistories"):
            return 200,{"items":[{"id":10,"sourceEntityId":1,"date":"2026-11-23T12:00:00+08:00","modification":"Add","stateName":"New","severityName":"Major"},
                                {"id":11,"sourceEntityId":1,"date":"2026-11-24T12:00:00+08:00","modification":"Update","changes":"EntityState","stateName":"Open","severityName":"Major"}],"next":None}
        if path.endswith("/Comments"):return 200,{"items":[{"id":20,"generalId":1,"createDate":"2026-11-24T12:00:00+08:00","description":"Retest navigation; reset crosses different features."}],"next":None}
        if path.endswith("/Features"):return 200,{"items":getattr(self,"features",[]),"next":None}
        if path.startswith("/v1/"):
            request=json.loads(body);messages=request.get("input",request.get("messages"))
            user=json.loads(messages[-1]["content"])
            checks=[{"rule_id":rid,"status":"not_applicable" if rid in {"submission.feature","submission.app_platform","submission.app_version","flow.keywords","flow.explanation"} else "pass",
                     "reason":"合成证据已核实","evidence_refs":["body"]} for rid in user["required_checks"]]
            answer=json.dumps({"schema_version":1,"checks":checks,"coverage":{"body":True,"comment_ids":[c["id"] for c in user["comments"]],"field_names":list(user["fields"])}})
            if path.endswith("responses"):return 200,{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":answer}]}]}
            return 200,{"choices":[{"finish_reason":"stop","message":{"content":answer}}]}
        if path=="/open-apis/auth/v3/tenant_access_token/internal":
            return 200,{"code":0,"tenant_access_token":"synthetic-tenant-token","expire":7200}
        if path=="/open-apis/bot/v3/info":return 200,{"code":0,"bot":{"open_id":"configured-bot","activate_status":2,"app_name":"Synthetic bot"}}
        if path=="/open-apis/im/v1/chats/configured-chat":return 200,{"code":0,"data":{"name":"Synthetic"}}
        if path.endswith("/members"):
            return (200,{"code":0,"data":{"items":[{"member_id":"configured-user","name":"Reviewer"}],"has_more":False}}) if self.members_allowed else (403,{"code":99991672})
        if path=="/open-apis/im/v1/files" and method=="POST":
            marker=b'name="file"; filename="'
            part=body[body.index(marker):];self.uploaded_file=part.split(b'\r\n\r\n',1)[1].rsplit(b'\r\n--',1)[0]
            self.csv=self.uploaded_file
            return 200,{"code":0,"data":{"file_key":"file_synthetic"}}
        if path=="/open-apis/im/v1/messages" and method=="POST":
            value=json.loads(body)
            if self.fail_file_send and value["msg_type"]=="file":return 403,{"code":230006}
            mid="om_synthetic_"+str(len(self.messages)+1)
            content=json.loads(value["content"])
            mentions=[]
            if value["msg_type"]=="post":
                mentions=[{"id":tag["user_id"],"key":"@_user_"+str(index),"id_type":"open_id"} for index,tag in enumerate(tag for row in content["zh_cn"]["content"] for tag in row if tag["tag"]=="at")]
            else:content["file_name"]=value.get("file_name","Bug审计明细.csv")
            self.messages[mid]={"message_id":mid,"chat_id":value["receive_id"],"msg_type":value["msg_type"],"deleted":False,
                                "sender":{"id":"configured-app","id_type":"app_id","sender_type":"app"},"mentions":mentions,
                                "body":{"content":json.dumps(content,ensure_ascii=False)}}
            if self.lose_summary_response and value["msg_type"]=="post":return 0,{}
            return 200,{"code":0,"data":{"message_id":mid}}
        if "/resources/" in path:return 200,self.csv
        if path.startswith("/open-apis/im/v1/messages/"):
            mid=path.rsplit("/",1)[-1]
            return 200,{"code":0,"data":{"items":[copy.deepcopy(self.messages[mid])] if mid in self.messages else []}}
        return 404,{"code":404}
