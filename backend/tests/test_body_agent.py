"""档案管家（Body Agent）测试 — 全部离线（无 LLM），覆盖：

规则抽取 / 门控 / 资料分类 / 规则规划 / 只增不删 / 对比 / 上传归档 / 交接
"""

import os
import sys

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import crud  # noqa: E402
from app.models import Base, User  # noqa: E402
from app.services.body import extractor  # noqa: E402
from app.services.body import taxonomy as tx
from app.services.orchestrator import Orchestrator  # noqa: E402


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(id=1, name="张阿姨", age=62, gender="女", city="杭州",
                         insurance_type="职工医保", employee_status="退休"))
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
def orch():
    o = Orchestrator()
    o._llm = None  # 离线：规则规划 + 规则抽取
    o._kb = None
    return o


# ---------------- 抽取规则 ----------------

class TestExtractor:
    def test_rule_extract_lung_nodule_with_date(self):
        recs = extractor.rule_extract("我2026年2月查出肺部小结节")
        assert len(recs) == 1
        assert recs[0]["organ"] == "lungs"
        assert recs[0]["event_date"] == "2026-02"
        assert "肺部小结节" in recs[0]["raw_excerpt"]

    def test_date_carries_forward_within_report(self):
        recs = extractor.rule_extract("2026年7月15日复查。肺部结节较前相仿。")
        assert recs[0]["organ"] == "lungs"
        assert recs[0]["event_date"] == "2026-07-15"

    def test_side_resolution_never_guesses(self):
        assert tx.match_organs("右肩疼") == ["shoulder_right"]
        assert tx.match_organs("左膝不舒服") == ["knee_left"]
        assert tx.match_organs("肩膀疼") == ["shoulder"]

    def test_gate_rejects_non_health_message(self):
        assert not tx.has_health_signal("医保能报多少")
        assert tx.has_health_signal("胃痛了两天")

    def test_third_party_statements_are_not_archived(self):
        assert extractor.rule_extract("我父亲做心脏搭桥能报多少") == []
        assert extractor.rule_extract("我妈查出肺结节。我自己右膝疼。")[0]["organ"] == "knee_right"

    def test_classify_doc_kind(self):
        assert extractor.classify_doc_kind("chest_CT.pdf", "") == "CT报告"
        assert extractor.classify_doc_kind("report.jpg", "肩关节磁共振检查") == "MRI报告"
        assert extractor.classify_doc_kind("note.txt", "门诊病历") == "病历文本"
        assert extractor.classify_doc_kind("scan.pdf", "") == "其他"

    def test_llm_validation_drops_hallucinated_items(self):
        source = "我右肩疼了一周"
        items = [
            {"organ": "shoulder_right", "description": "右肩疼一周", "raw_excerpt": "右肩疼了一周", "event_date": ""},
            {"organ": "lungs", "description": "肺结节", "raw_excerpt": "肺部发现结节", "event_date": ""},  # 原文没有
            {"organ": "unicorn", "description": "x", "raw_excerpt": "右肩疼", "event_date": ""},  # 未知器官
        ]
        out = extractor._validate(items, source)
        assert [r["organ"] for r in out] == ["shoulder_right"]

    def test_missing_info_questions(self):
        qs = extractor.missing_info([{"organ": "shoulder", "event_date": ""}])
        assert any("左侧还是右侧" in q for q in qs)
        assert any("时间" in q for q in qs)


# ---------------- 规则规划 ----------------

class TestRulePlan:
    def _tools(self, orch, msg):
        plan = orch._rule_body_plan(msg, {})
        return [a["tool"] for a in plan["actions"]], plan

    def test_describe_symptom_archives(self, orch):
        tools, plan = self._tools(orch, "我2026年2月查出肺部小结节")
        assert tools == ["archive"]
        assert plan["focus"] == "lungs"

    def test_query_retrieves(self, orch):
        tools, plan = self._tools(orch, "帮我看看肺部的记录")
        assert tools == ["retrieve"]
        assert plan["actions"][0]["args"]["organ"] == "lungs"

    def test_compare(self, orch):
        tools, _ = self._tools(orch, "对比一下两次肺部检查")
        assert tools == ["compare"]

    def test_reimbursement_question_archives_and_hands_off(self, orch):
        tools, _ = self._tools(orch, "肺结节复查能报销吗")
        assert "archive" in tools
        assert {"tool": "handoff", "args": {"agent": "policy"}} in orch._rule_body_plan("肺结节复查能报销吗", {})["actions"]

    def test_memory_question_retrieves_not_archives(self, orch):
        tools, _ = self._tools(orch, "我上次说的肺结节怎么样了")
        assert tools == ["retrieve"]

    def test_how_much_question_hands_off_to_coverage(self, orch):
        plan = orch._rule_body_plan("我2月肺结节，医保能报多少", {})
        assert {"tool": "handoff", "args": {"agent": "coverage"}} in plan["actions"]

    def test_keyword_intent_routing(self, orch):
        assert orch._keyword_intent("我2026年2月查出肺部小结节") == "body"
        assert orch._keyword_intent("帮我看看肺部的记录") == "body"
        assert orch._keyword_intent("帮我做脑电健康评估") == "eeg"
        assert orch._keyword_intent("帮我分析肺部CT影像") == "imaging"
        assert orch._keyword_intent("帮我查看我的医保权益") == "coverage"


# ---------------- 端到端（离线） ----------------

class TestBodyAgentFlow:
    async def test_archive_then_retrieve_then_compare_is_append_only(self, orch, db):
        r1 = await orch._handle_body_agent("我2026年2月查出肺部小结节", "user_001", None, db=db)
        assert r1["data"]["body_updates"][0]["organ"] == "lungs"
        assert r1["data"]["body_focus"] == "lungs"
        assert "已为您记录" in r1["response"]
        assert extractor.DISCLAIMER in r1["response"]

        # 上传复查报告 → 追加，不覆盖；同部位自动并列对比
        up = await orch.handle_body_document(
            "user_001", "2026年7月复查CT：肺部结节较前相仿。", "CT报告", "ct_0715.txt", "text/plain",
            user_profile=None, db=db,
        )
        assert up["records_added"] == 1
        assert up["doc_kind"] == "CT报告"
        assert up["comparison"]["organ"] == "lungs"
        assert "2026-02" in up["agent_response"] and "2026-07" in up["agent_response"]

        rows = await crud.get_body_records(db, "user_001", organ="lungs")
        assert len(rows) == 2
        assert rows[0].event_date == "2026-07" and rows[0].source_label == "CT报告"
        assert rows[1].event_date == "2026-02" and rows[1].source_label == "对话输入"

        r2 = await orch._handle_body_agent("帮我看看肺部的记录", "user_001", None, db=db)
        assert "[2026-07][CT报告]" in r2["response"]
        assert "[2026-02][对话输入]" in r2["response"]
        assert r2["data"]["body_focus"] == "lungs"

        r3 = await orch._handle_body_agent("对比一下两次肺部检查", "user_001", None, db=db)
        assert "以医生判断为准" in r3["response"]
        assert r3["data"]["comparison"]["earlier"]["event_date"] == "2026-02"

    async def test_empty_organ_reports_no_records(self, orch, db):
        r = await orch._handle_body_agent("帮我看看肝脏的记录", "user_001", None, db=db)
        assert "暂无相关医疗记录" in r["response"]
        assert r["data"]["records"] == []

    async def test_ask_missing_for_unsided_shoulder(self, orch, db):
        r = await orch._handle_body_agent("肩膀疼", "user_001", None, db=db)
        assert r["data"]["body_updates"][0]["organ"] == "shoulder"
        assert any("左侧还是右侧" in q for q in r["data"]["missing_info"])
        assert "请补充" in r["response"]

    async def test_handoff_to_policy_agent(self, orch, db):
        r = await orch._handle_body_agent("肺结节复查能报销吗", "user_001", None, db=db)
        assert "policy" in r["data"]["handoff"]
        assert "【政策参谋】" in r["response"]

    async def test_profile_shares_archive_upstream(self, orch, db):
        await orch._handle_body_agent("我2026年2月查出肺部小结节", "user_001", None, db=db)
        profile = await crud.get_user_health_profile(db, "user_001")
        assert profile["body_record_count"] == 1
        assert profile["body_organs"] == ["肺部"]
        assert "[2026-02][对话输入] 肺部" in profile["body_recent"][0]
