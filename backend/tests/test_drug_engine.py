"""药品卫士（Drug Scan / 用药安全）单元测试 — 全部离线（无 LLM），覆盖：

结果规范化 / JSON 解析 / 识别降级 / 归类 / 有效期核验 / 相互作用 /
扫描结果组装 / 用户确认登记 / 展示层 / 意图路由
"""

import os
import sys
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Base, MedicationRecord, User  # noqa: E402
from app.services.drug_scan import engine as de  # noqa: E402
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
        session.add(MedicationRecord(user_id=1, date=datetime(2025, 3, 2),
                                     medication_name="阿司匹林", category="抗血小板",
                                     quantity=2, unit_price=12.0, is_chronic=True))
        await session.commit()
        yield session
    await engine.dispose()


# ---------------- 结果规范化 ----------------

class TestNormalize:
    def test_valid_approval_number_kept(self):
        drug = de.normalize_drug_fields({
            "generic_name": "二甲双胍片",
            "approval_number": "国药准字H20023370",
            "confidence": 0.9,
        })
        assert drug["approval_number"] == "国药准字H20023370"
        assert drug["confidence"] == 0.9

    def test_invalid_approval_number_cleared_with_note(self):
        drug = de.normalize_drug_fields({
            "generic_name": "某药",
            "approval_number": "卫药准字123",
        })
        assert drug["approval_number"] == ""
        assert "批准文号格式不符" in drug["notes"]

    def test_placeholders_cleared(self):
        drug = de.normalize_drug_fields({
            "generic_name": "布洛芬缓释胶囊",
            "brand_name": "null",
            "spec": "无",
            "batch_number": "None",
        })
        assert drug["brand_name"] == ""
        assert drug["spec"] == ""
        assert drug["batch_number"] == ""

    def test_confidence_clamped(self):
        assert de.normalize_drug_fields({"confidence": 5})["confidence"] == 1.0
        assert de.normalize_drug_fields({"confidence": -1})["confidence"] == 0.0
        assert de.normalize_drug_fields({"confidence": "abc"})["confidence"] == 0.0


# ---------------- JSON 解析 ----------------

class TestParseDrugJson:
    def test_plain_json(self):
        data = de.parse_drug_json('{"generic_name": "二甲双胍片"}')
        assert data and data["generic_name"] == "二甲双胍片"

    def test_fenced_json(self):
        data = de.parse_drug_json('好的，结果如下：\n```json\n{"generic_name": "阿司匹林"}\n```')
        assert data and data["generic_name"] == "阿司匹林"

    def test_json_embedded_in_text(self):
        data = de.parse_drug_json('识别结果 {"generic_name": "硝苯地平"} 完毕')
        assert data and data["generic_name"] == "硝苯地平"

    def test_invalid_returns_none(self):
        assert de.parse_drug_json("") is None
        assert de.parse_drug_json("完全不是 JSON") is None
        assert de.parse_drug_json("{broken") is None


# ---------------- 识别降级 ----------------

class TestRecognizeFallback:
    async def test_no_llm_returns_mock(self):
        drug, source = await de.recognize_drug(b"fake-image-bytes", llm=None)
        assert source == "mock"
        assert drug["generic_name"] == "二甲双胍片"


# ---------------- 归类与有效期 ----------------

class TestCategorize:
    def test_known_drug_categorized(self):
        assert de.categorize_drug("二甲双胍片") == "降糖药"
        assert de.categorize_drug("阿司匹林肠溶片") == "抗凝抗血小板"

    def test_unknown_drug_empty(self):
        assert de.categorize_drug("维生素C片") == ""
        assert de.categorize_drug("") == ""


class TestExpiry:
    def test_future_expiry_ok(self):
        result = de.check_expiry("2099-12")
        assert result["status"] == "ok"

    def test_past_expiry_expired(self):
        result = de.check_expiry("2020-01")
        assert result["status"] == "expired"
        assert "已过期" in result["message"]

    def test_expiring_soon(self):
        soon = (datetime.now(UTC) + timedelta(days=45)).strftime("%Y-%m")
        result = de.check_expiry(soon)
        assert result["status"] == "expiring"

    def test_unparseable(self):
        assert de.check_expiry("")["status"] == "unknown"
        assert de.check_expiry("明年夏天")["status"] == "unknown"


# ---------------- 相互作用 ----------------

class TestInteractions:
    def test_warning_involving_scanned_drug(self):
        # 华法林（抗凝）+ 阿司匹林 → high 风险规则
        warnings = de.check_interactions("华法林", ["阿司匹林"])
        assert len(warnings) == 1
        assert warnings[0]["severity"] == "high"

    def test_no_warning_without_overlap(self):
        assert de.check_interactions("维生素C片", ["阿司匹林"]) == []

    def test_empty_name_no_warnings(self):
        assert de.check_interactions("", ["阿司匹林"]) == []


# ---------------- 扫描结果组装 ----------------

class TestBuildScanResult:
    def test_not_a_drug_passthrough(self):
        result = de.build_scan_result({"not_a_drug": True, "detected": "发票"}, [], "vision")
        assert result["not_a_drug"] is True
        assert result["detected"] == "发票"

    def test_full_result_shape(self):
        drug = de.mock_drug_result()
        result = de.build_scan_result(drug, ["华法林"], "mock")
        assert result["not_a_drug"] is False
        assert result["category"] == "降糖药"
        assert result["expiry"]["status"] in ("ok", "expiring", "expired", "unknown")
        assert "confirm_prompt" in result
        assert result["registered"] is False


# ---------------- 用户确认登记 ----------------

class TestRegister:
    async def test_register_creates_record(self, db):
        drug = {"generic_name": "二甲双胍片", "brand_name": "格华止"}
        info = await de.register_drug(db, "user_001", drug)
        assert info["medication_name"] == "二甲双胍片"
        assert info["category"] == "降糖药"

        rows = (await db.execute(select(MedicationRecord))).scalars().all()
        assert any(r.medication_name == "二甲双胍片" for r in rows)

    async def test_register_unknown_user_raises(self, db):
        with pytest.raises(ValueError):
            await de.register_drug(db, "user_999", {"generic_name": "某药"})


# ---------------- 展示层 ----------------

class TestFormat:
    def test_drug_card_markdown(self):
        drug = de.mock_drug_result()
        result = de.build_scan_result(drug, [], "vision")
        text = de.format_chat_response(result)
        assert "二甲双胍片" in text
        assert "有效期" in text
        assert "加入您的用药记录" in text

    def test_mock_source_flagged(self):
        drug = de.mock_drug_result()
        result = de.build_scan_result(drug, [], "mock")
        assert "示例数据" in de.format_chat_response(result)

    def test_not_a_drug_message(self):
        text = de.format_chat_response({"not_a_drug": True, "detected": "发票"})
        assert "不是药品包装" in text


# ---------------- 意图路由 ----------------

class TestDrugRouting:
    def test_keywords_route_to_drug(self):
        o = Orchestrator()
        assert o._keyword_intent("帮我识别一下这个药盒") == "drug"
        assert o._keyword_intent("这个药过期了吗") == "drug"

    async def test_handle_drug_agent_offline_returns_mock(self):
        o = Orchestrator()
        result = await o._handle_drug_agent("怎么识别药品？")
        assert "药品卫士" in result["response"]
        assert result["data"]["scan_entry"] == "/api/drugs/scan"
