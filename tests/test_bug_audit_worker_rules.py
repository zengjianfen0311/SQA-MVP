import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"outputs"/"SQA_MVP"))
from bug_audit_worker.rules import build_facts, deterministic_checks, assemble_findings, working_hours, semantic_rules
from bug_audit_worker.tp import FIELDS

OPTIONS = {FIELDS[0]: ["OK","No need","Bug Category is missing","Previously passed version is missing","Bug was not promptly transferred to developer","Firmware version is missing","Feature is missing"],
           FIELDS[1]: ["OK","Relations is wrong","Duplicate state is wrong","Invalid evidence is missing","State transferred without required [Keywords]"]}

def item(created="2026-11-23T00:00:00+08:00", severity="Critical"):
    return {"bug_id":"10","created_at":created,"snapshot":{"state":"Open","severity":severity},"original":{n:None for n in FIELDS},
            "fields":{"Bug Category":"Functional","REG issue":"No"},"feature_id":None,"comments":[],
            "evidence":{"history":"complete","relations":"complete","comments":"complete","description":"complete"},
            "relations":{"inbound":[],"outbound":[]},
            "lifecycle":[{"id":1,"date":created,"modification":"Add","stateName":"New","severityName":severity},
                         {"id":2,"date":"2026-11-24T00:00:00+08:00","modification":"Update","stateName":"Open","severityName":severity}]}

def semantic_passes(record):
    return [{"rule_id":rid,"status":"pass","reason":"证据齐全","evidence_refs":["body"]} for rid in semantic_rules(record)]

class RuleTests(unittest.TestCase):
    def test_missing_category_is_not_relabelled_as_inconsistency(self):
        record=item(severity="Major");record["fields"]["Bug Category"]=""
        checks=semantic_passes(record)+deterministic_checks(record,build_facts(record,{"end":"2026-11-24T19:30:00+08:00"}))
        next(c for c in checks if c["rule_id"]=="submission.category_detail").update(status="fail",reason="信息缺失（Bug Category）：分类字段为空，应补充分类。")
        fields={f["name"]:f for f in assemble_findings(record,checks,OPTIONS)}
        self.assertEqual(fields[FIELDS[2]]["proposed_value"],"信息缺失（Bug Category 缺失）")
        self.assertEqual(fields[FIELDS[0]]["proposed_value"],"Bug Category is missing")

    def test_submission_issue_text_uses_short_detail_without_nested_analysis(self):
        record=item(severity="Major")
        checks=semantic_passes(record)+deterministic_checks(record,build_facts(record,{"end":"2026-11-24T19:30:00+08:00"}))
        next(c for c in checks if c["rule_id"]=="submission.feature").update(status="fail",reason="信息缺失（Feature关联）：已核实该候选覆盖产品及用户功能，当前未关联。"*3)
        fields={f["name"]:f for f in assemble_findings(record,checks,OPTIONS)}
        self.assertEqual(fields[FIELDS[2]]["proposed_value"],"信息缺失（Feature关联）")
        self.assertTrue(fields[FIELDS[2]]["ready"])

    def test_text_only_pending_does_not_block_dropdown_fields(self):
        record=item(severity="Major")
        checks=semantic_passes(record)+deterministic_checks(record,build_facts(record,{"end":"2026-11-24T19:30:00+08:00"}))
        for check in checks:
            if check["rule_id"] in {"submission.category_detail","flow.state_consistency"}:
                check.update(status="pending",reason="仅文字审计仍待确认")
        fields={f["name"]:f for f in assemble_findings(record,checks,OPTIONS)}
        self.assertTrue(fields[FIELDS[0]]["ready"])
        self.assertTrue(fields[FIELDS[1]]["ready"])
        self.assertFalse(fields[FIELDS[2]]["ready"])

    def test_linked_feature_matching_pending_only_blocks_text(self):
        record=item(severity="Major");record["feature_id"]=99
        checks=semantic_passes(record)+deterministic_checks(record,build_facts(record,{"end":"2026-11-24T19:30:00+08:00"}))
        next(c for c in checks if c["rule_id"]=="submission.feature").update(status="pending",reason="已关联但适用性待确认")
        fields={f["name"]:f for f in assemble_findings(record,checks,OPTIONS)}
        self.assertTrue(fields[FIELDS[0]]["ready"])
        self.assertFalse(fields[FIELDS[2]]["ready"])
        record["feature_id"]=None
        fields={f["name"]:f for f in assemble_findings(record,checks,OPTIONS)}
        self.assertFalse(fields[FIELDS[0]]["ready"])

    def test_reproduce_explanation_pending_keeps_defined_development_checks(self):
        record=item(severity="Major");record["snapshot"]["state"]="Reproduce"
        record["lifecycle"][-1]["stateName"]="Reproduce"
        record["comments"]=[{"id":"20","text":"retest navigation","at":"2026-11-24T12:00:00+08:00"}]
        checks=semantic_passes(record)+deterministic_checks(record,build_facts(record,{"end":"2026-11-24T19:30:00+08:00"}))
        next(c for c in checks if c["rule_id"]=="flow.explanation").update(status="pending",reason="说明是否充分待确认")
        fields={f["name"]:f for f in assemble_findings(record,checks,OPTIONS)}
        self.assertEqual(fields[FIELDS[1]]["proposed_value"],"OK")
        self.assertFalse(fields[FIELDS[2]]["ready"])
    def test_weekend_and_exact_response_threshold(self):
        self.assertEqual(working_hours("2026-11-20T00:00:00+08:00","2026-11-24T00:00:00+08:00"),48)
        record=item("2026-11-20T00:00:00+08:00","Blocking")
        facts=build_facts(record,{"end":"2026-11-24T00:00:00+08:00"})
        check=next(c for c in deterministic_checks(record,facts) if c["rule_id"]=="flow.first_response")
        self.assertEqual(check["status"],"pass")
        record["lifecycle"][-1]["date"]="2026-11-24T00:00:01+08:00"
        facts=build_facts(record,{"end":"2026-11-24T01:00:00+08:00"})
        self.assertEqual(next(c for c in deterministic_checks(record,facts) if c["rule_id"]=="flow.first_response")["status"],"fail")

    def test_first_actual_severity_change_controls_response_not_current(self):
        record=item("2026-11-19T00:00:00+08:00","Normal")
        record["lifecycle"].insert(1,{"id":3,"date":"2026-11-19T01:00:00+08:00","modification":"Update","stateName":"New","severityName":"Blocking"})
        facts=build_facts(record,{"end":"2026-11-24T12:00:00+08:00"})
        self.assertEqual(facts["first_severity"],"Blocking")
        self.assertEqual(next(c for c in deterministic_checks(record,facts) if c["rule_id"]=="flow.first_response")["status"],"fail")

    def test_old_testing_no_need_survives_other_pending_checks(self):
        record=item("2025-12-31T23:59:59+08:00")
        # Invalid 的说明/关键词仍需语义审计，不能以旧单豁免跳过。
        record["snapshot"]["state"]="Invalid"
        record["lifecycle"][-1]["stateName"]="Invalid"
        checks=deterministic_checks(record,build_facts(record,{"end":"2026-11-24T12:00:00+08:00"}))
        fields={f["name"]:f for f in assemble_findings(record,checks,OPTIONS)}
        self.assertTrue(fields[FIELDS[0]]["ready"])
        self.assertEqual(fields[FIELDS[0]]["proposed_value"],"No need")
        self.assertFalse(fields[FIELDS[1]]["ready"])
        self.assertFalse(fields[FIELDS[2]]["ready"])

    def test_general_relation_is_pending_and_missing_reg_version_is_independent(self):
        record=item();record["relations"]["inbound"]=[{"targetId":2,"targetEntity":"General"}]
        record["fields"]["REG issue"]="Yes"
        checks=deterministic_checks(record,build_facts(record,{"end":"2026-11-24T12:00:00+08:00"}))+semantic_passes(record)
        fields={f["name"]:f for f in assemble_findings(record,checks,OPTIONS)}
        self.assertTrue(fields[FIELDS[0]]["ready"])
        self.assertIn("Previously passed version is missing",fields[FIELDS[0]]["proposed_value"])
        self.assertFalse(fields[FIELDS[1]]["ready"])
        self.assertIsNone(fields[FIELDS[1]]["proposed_value"])

    def test_unknown_option_does_not_invent_dropdown_value_and_no_developer_rule(self):
        record=item();record["fields"]["Bug Category"]=""
        checks=deterministic_checks(record,build_facts(record,{"end":"2026-11-24T12:00:00+08:00"}))+semantic_passes(record)
        self.assertFalse(any("handoff" in c["rule_id"] for c in checks))
        options={**OPTIONS,FIELDS[0]:["OK","No need"]}
        fields={f["name"]:f for f in assemble_findings(record,checks,options)}
        self.assertFalse(fields[FIELDS[0]]["ready"])
        self.assertEqual(fields[FIELDS[1]]["proposed_value"],"OK")
        self.assertIn("信息缺失",fields[FIELDS[2]]["proposed_value"])

if __name__=="__main__":unittest.main()
