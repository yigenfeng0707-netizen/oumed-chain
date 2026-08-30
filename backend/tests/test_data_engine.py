"""数据管家（Data Agent / 湖仓引擎）单元测试 — 全部离线（无 LLM），覆盖：

SQL 安全校验 / 模板匹配 / LLM SQL 提取 / 数据目录 / 智能查询 / 质量报告 / 意图路由
"""

import os
import sys
from datetime import datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import (  # noqa: E402
    Base,
    InsuranceRecord,
    MedicalRecord,
    MedicationRecord,
    User,
)
from app.services.data_lake import engine as de  # noqa: E402
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
        session.add(MedicalRecord(user_id=1, date=datetime(2025, 3, 1), hospital="市一医院",
                                  department="内分泌科", diagnosis="糖尿病", visit_type="门诊",
                                  total_cost=300.0, reimbursed_amount=180.0))
        session.add(MedicalRecord(user_id=1, date=datetime(2025, 5, 1), hospital="市一医院",
                                  department="心内科", diagnosis="高血压", visit_type="住院",
                                  total_cost=10000.0, reimbursed_amount=8200.0))
        # 口径异常：报销额 > 总费用（供质量报告检出）
        session.add(MedicalRecord(user_id=1, date=datetime(2025, 6, 1), hospital="社区医院",
                                  department="全科", diagnosis="感冒", visit_type="门诊",
                                  total_cost=100.0, reimbursed_amount=120.0))
        session.add(MedicationRecord(user_id=1, date=datetime(2025, 3, 2),
                                     medication_name="二甲双胍", category="慢病用药",
                                     quantity=3, unit_price=38.5, is_chronic=True))
        session.add(InsuranceRecord(user_id=1, year=2025, month=1,
                                    base_amount=5000.0, personal_amount=100.0, company_amount=400.0))
        await session.commit()
        yield session
    await engine.dispose()


# ---------------- SQL 安全校验 ----------------

class TestValidateSql:
    def test_allows_plain_select_and_appends_limit(self):
        ok, sql, reason = de.validate_sql("SELECT * FROM users")
        assert ok and reason == ""
        assert sql.endswith("LIMIT 1000")

    def test_rejects_write_operations(self):
        for bad in [
            "INSERT INTO users(name) VALUES('x')",
            "UPDATE users SET name='x'",
            "DELETE FROM users",
            "DROP TABLE users",
            "CREATE TABLE evil(id int)",
        ]:
            ok, _, reason = de.validate_sql(bad)
            assert not ok, bad
            assert reason

    def test_rejects_multi_statement(self):
        ok, _, reason = de.validate_sql("SELECT * FROM users; DROP TABLE users")
        assert not ok
        assert "多条语句" in reason

    def test_rejects_unknown_table(self):
        ok, _, reason = de.validate_sql("SELECT * FROM secret_table")
        assert not ok
        assert "未知数据表" in reason

    def test_caps_large_limit(self):
        ok, sql, _ = de.validate_sql("SELECT * FROM users LIMIT 99999")
        assert ok
        assert "LIMIT 1000" in sql
        assert "99999" not in sql

    def test_keeps_small_limit(self):
        ok, sql, _ = de.validate_sql("SELECT * FROM users LIMIT 10")
        assert ok
        assert sql.endswith("LIMIT 10")


# ---------------- 模板匹配 / LLM SQL 提取 ----------------

class TestTemplateMatching:
    def test_cost_summary(self):
        tpl, score = de.match_template("帮我汇总就医费用")
        assert tpl is not None and tpl["id"] == "cost_summary"
        assert score > 0

    def test_medication_top(self):
        tpl, _ = de.match_template("哪些药买得最多")
        assert tpl["id"] == "medication_top"

    def test_insurance_payments(self):
        tpl, _ = de.match_template("缴费统计")
        assert tpl["id"] == "insurance_payments"

    def test_no_match_for_unrelated(self):
        tpl, score = de.match_template("今天天气怎么样")
        assert tpl is None and score == 0

    def test_all_template_sqls_are_safe(self):
        """所有预置模板 SQL 必须通过安全校验"""
        for tpl in de.QUERY_TEMPLATES:
            ok, _, reason = de.validate_sql(tpl["sql"])
            assert ok, f"{tpl['id']}: {reason}"

    def test_extract_sql_from_llm_fenced(self):
        assert de.extract_sql_from_llm("```sql\nSELECT 1\n```") == "SELECT 1"

    def test_extract_sql_from_llm_bare(self):
        assert de.extract_sql_from_llm("SELECT * FROM users LIMIT 5") == "SELECT * FROM users LIMIT 5"

    def test_extract_sql_unsupported(self):
        assert de.extract_sql_from_llm("UNSUPPORTED") is None
        assert de.extract_sql_from_llm("") is None


# ---------------- 数据目录 / 智能查询 ----------------

class TestCatalogAndQuery:
    async def test_catalog_counts_warehouse_rows(self, db):
        cat = await de.catalog(db)
        s = cat["summary"]
        assert s["warehouse_tables"] == len(de.WAREHOUSE_TABLES)
        assert s["warehouse_total_rows"] >= 6  # 1 user + 3 medical + 1 med + 1 insurance
        tables = {t["table"]: t["row_count"] for t in cat["warehouse"]}
        assert tables["users"] == 1
        assert tables["medical_records"] == 3
        # 每张仓层表带血缘说明
        assert all(t["lineage"] for t in cat["warehouse"])

    async def test_smart_query_template_executes(self, db):
        r = await de.smart_query(db, "帮我汇总就医费用")
        assert r["query"]["source"] == "template"
        assert r["query"]["sql"]
        assert r["result_table"]["row_count"] == 2  # 门诊/住院两类
        assert r["data_source"]["datasets"] == ["medical_records"]

    async def test_smart_query_fallback_without_llm(self, db):
        """无模板命中且无 LLM → 目录摘要兜底"""
        r = await de.smart_query(db, "今天天气怎么样")
        assert r["query"]["source"] == "fallback"
        assert r["query"]["sql"] is None
        assert "catalog" in r

    async def test_format_chat_response_shows_sql(self, db):
        r = await de.smart_query(db, "帮我汇总就医费用")
        text = de.format_chat_response(r)
        assert "```sql" in text
        assert "口径说明" in text


# ---------------- 数据质量报告 ----------------

class TestQualityReport:
    async def test_detects_reimbursed_over_total(self, db):
        report = await de.quality_report(db)
        hit = [c for c in report["checks"]
               if c["table"] == "medical_records" and "报销额" in c["check"]]
        assert hit and hit[0]["hits"] == 1
        assert report["issue_count"] >= 1
        assert report["lineage_note"]


# ---------------- 编排器意图路由 ----------------

class TestDataAgentRouting:
    def test_keyword_intent_routes_to_data(self):
        o = Orchestrator()
        assert o._keyword_intent("湖仓里有哪些数据资产") == "data"
        assert o._keyword_intent("帮我查数据，统计一下缴费情况") == "data"

    def test_other_intents_unaffected(self):
        o = Orchestrator()
        assert o._keyword_intent("帮我做脑电健康评估") == "eeg"
        assert o._keyword_intent("帮我查看我的医保权益") == "coverage"

    async def test_handle_data_agent_offline(self, db):
        o = Orchestrator()
        o._llm = None
        r = await o._handle_data_agent("帮我汇总就医费用", "1", None, db=db)
        assert "查询完成" in r["response"]
        assert r["data"]["query"]["sql"]
        assert r["evidence"][0]["type"] == "data_query"
