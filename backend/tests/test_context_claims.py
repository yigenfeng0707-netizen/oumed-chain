"""对话上下文感知 + 上传资料联合预审 单元测试 — 全部离线（无 LLM），覆盖：

上下文意图消歧 / 追问连续性路由 / 历史压缩块 / 资料二次分类 / 金额提取 /
联合预审组装 / 预审分步明细 / 最近上传资料查询（含时间窗）
"""

import os
import sys
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import crud  # noqa: E402
from app.models import Base, BodyDocument, User  # noqa: E402
from app.services import claims_engine  # noqa: E402
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


# ---------------- 上下文意图消歧 ----------------

class TestContextIntent:
    def setup_method(self):
        self.o = Orchestrator()

    def test_zero_hit_with_upload_history_and_flow_routes_claims(self):
        self.o.note_intent("user_001", "claims")
        hist = [{"role": "user", "content": "[上传了《发票.jpg》]"}]
        got = self.o.context_intent("你可以读到啥信息", hist, has_recent_docs=True, user_id="user_001")
        assert got == "claims"

    def test_zero_hit_without_flow_routes_body(self):
        hist = [{"role": "user", "content": "[上传了《报告.jpg》]"}]
        got = self.o.context_intent("里面写了啥", hist, has_recent_docs=False, user_id="user_002")
        assert got == "body"

    def test_explicit_intent_not_overridden(self):
        self.o.note_intent("user_001", "claims")
        hist = [{"role": "user", "content": "[上传了《发票.jpg》]"}]
        got = self.o.context_intent("门诊报销比例是多少", hist, True, "user_001")
        assert got is None

    def test_no_upload_context_returns_none(self):
        got = self.o.context_intent("你可以读到啥信息", [], has_recent_docs=False, user_id="user_001")
        assert got is None

    def test_reading_verb_required(self):
        hist = [{"role": "user", "content": "[上传了《发票.jpg》]"}]
        got = self.o.context_intent("今天天气不错", hist, has_recent_docs=True, user_id="user_001")
        assert got is None

    def test_note_intent_only_claims(self):
        self.o.note_intent("u1", "coverage")
        assert "u1" not in self.o._active_flows
        self.o.note_intent("u1", "claims")
        assert self.o._active_flows.get("u1") == "claims_prereview"

    def test_signature_fallback_after_restart(self):
        # 服务重启后内存流程状态丢失，靠最近助手回复署名路由回报销助手
        hist = [{"role": "assistant", "content": "**【报销助手】**\n预审测算（发票合计 222.36 元）"}]
        got = self.o.context_intent("刚刚图片里面说了啥", hist, has_recent_docs=True, user_id="u1")
        assert got == "claims"


# ---------------- 追问/细节连续性路由 ----------------

class TestFollowupIntent:
    def setup_method(self):
        self.o = Orchestrator()

    def test_detail_followup_routes_back_by_signature(self):
        hist = [
            {"role": "user", "content": "[上传了《发票.jpg》]"},
            {"role": "assistant", "content": "**【报销助手】**\n预审测算：统筹预计支付约 300.00 元"},
        ]
        assert self.o.followup_intent("说说具体的细节", hist, user_id="u1") == "claims"

    def test_skips_trailing_user_turn(self):
        hist = [
            {"role": "assistant", "content": "**【权益管家】**您的参保状态正常"},
            {"role": "user", "content": "[上传了《清单.jpg》]"},
        ]
        assert self.o.followup_intent("再详细说说", hist, user_id="u1") == "coverage"

    def test_explicit_keyword_not_overridden(self):
        hist = [{"role": "assistant", "content": "**【报销助手】**预审测算…"}]
        assert self.o.followup_intent("门诊报销比例具体是多少", hist, user_id="u1") is None

    def test_no_marker_returns_none(self):
        hist = [{"role": "assistant", "content": "**【报销助手】**预审测算…"}]
        assert self.o.followup_intent("你好", hist, user_id="u1") is None

    def test_no_signature_falls_back_to_last_agent(self):
        self.o.note_intent("u1", "claims")
        hist = [{"role": "assistant", "content": "已为您测算完成，无署名文本"}]
        assert self.o.followup_intent("为什么是这个数", hist, user_id="u1") == "claims"

    def test_empty_history_no_last_agent_returns_none(self):
        assert self.o.followup_intent("说说细节", [], user_id="u1") is None

    def test_note_intent_records_last_agent(self):
        self.o.note_intent("u1", "body")
        assert self.o._last_agent["u1"] == "body"
        self.o.note_intent("u1", "claims")
        assert self.o._last_agent["u1"] == "claims"


class TestHistoryBlock:
    def test_empty(self):
        assert Orchestrator.format_history_block(None) == ""
        assert Orchestrator.format_history_block([]) == ""

    def test_lines_and_truncation(self):
        block = Orchestrator.format_history_block([
            {"role": "user", "content": "帮我预审报销材料"},
            {"role": "assistant", "content": "X" * 500},
        ])
        assert "用户: 帮我预审报销材料" in block
        assert "助手:" in block
        assert "X" * 201 not in block


# ---------------- 资料二次分类 / 金额提取 ----------------

class TestClassifyAndAmount:
    def test_invoice_by_text(self):
        assert claims_engine.classify_uploaded_doc(
            "weixin.jpg", "病历文本", "医疗收费票据 合计金额 328.50 元",
        ) == "发票/票据"

    def test_invoice_by_name(self):
        assert claims_engine.classify_uploaded_doc(
            "receipt_001.png", "其他", "",
        ) == "发票/票据"

    def test_list(self):
        assert claims_engine.classify_uploaded_doc(
            "清单.jpg", "其他", "费用清单 明细列表 西药费 120.00",
        ) == "费用清单"

    def test_report_and_record(self):
        assert claims_engine.classify_uploaded_doc("mri.jpg", "MRI报告", "") == "检查报告"
        assert claims_engine.classify_uploaded_doc("bl.jpg", "病历文本", "") == "病历文本"
        assert claims_engine.classify_uploaded_doc("x.jpg", "其他", "") == "其他"

    def test_extract_amount_max_candidate(self):
        assert claims_engine.extract_invoice_amount("合计 100.00 金额：328.50") == 328.5
        assert claims_engine.extract_invoice_amount("无金额文本") is None

    def test_extract_amount_xiaoxie_and_gap(self):
        # 电子票据常见格式：大写金额在前，（小写）才是数字合计，中间隔大写文本
        text = "金额合计（大写）贰佰贰拾贰元叁角陆分 （小写）222.36"
        assert claims_engine.extract_invoice_amount(text) == 222.36

    def test_classify_fee_items_without_qingdan(self):
        # OCR 文本缺“清单”字样，靠费用项目词识别为费用清单
        text = "床位费（多人间） 护理费 II级护理 治疗费 磁共振（MR）平扫"
        assert claims_engine.classify_uploaded_doc("wx.jpg", "其他", text) == "费用清单"

    def test_list_with_invoice_watermark_not_invoice(self):
        # 费用清单常带“发票”水印字样，但含 分类小计/清单 应判清单；带票据号码的才是发票
        text = "抗甲状腺过氧化物酶抗体测定 分类小计 878.10 发票下载"
        assert claims_engine.classify_uploaded_doc("wx.jpg", "病历文本", text) == "费用清单"
        inv = "医疗收费票据 票据号码：12345 合计 100.00"
        assert claims_engine.classify_uploaded_doc("wx.jpg", "其他", inv) == "发票/票据"


# ---------------- 联合预审组装 ----------------

class TestReviewUploadedDocuments:
    def test_invoice_amount_and_estimate(self):
        docs = [
            {"filename": "发票.jpg", "doc_kind": "病历文本",
             "extracted_text": "医疗收费票据 合计金额：3280.50元 个人自付 40.00"},
            {"filename": "mri.jpg", "doc_kind": "MRI报告", "extracted_text": "腰椎MRI未见异常"},
        ]
        r = claims_engine.review_uploaded_documents(docs)
        kinds = {d["filename"]: d["claim_kind"] for d in r["documents"]}
        assert kinds["发票.jpg"] == "发票/票据"
        assert kinds["mri.jpg"] == "检查报告"
        assert r["total_amount"] == 3280.5
        assert r["estimate"] is not None
        assert r["estimate"]["estimated_reimbursement"] > 0
        statuses = {c["name"]: c["status"] for c in r["completeness"]}
        assert statuses["发票/票据"] == "uploaded"
        assert statuses["费用清单"] == "missing"
        assert "3280.50" in r["response"]
        assert "15–30 个工作日" in r["response"]

    def test_response_quotes_content(self):
        r = claims_engine.review_uploaded_documents([
            {"filename": "发票.jpg", "doc_kind": "病历文本",
             "extracted_text": "浙江省医疗收费票据 西药费 玛巴洛沙韦片 合计金额：1200.00元"},
        ])
        assert "内容摘要" in r["response"]
        assert "玛巴洛沙韦片" in r["response"]
        detail = claims_engine.build_prereview_detail_text(r, "职工医保")
        assert "识别到的内容" in detail
        assert "玛巴洛沙韦片" in detail

    def test_no_amount_no_estimate(self):
        r = claims_engine.review_uploaded_documents([
            {"filename": "病历.jpg", "doc_kind": "病历文本", "extracted_text": "门诊病历 高血压复诊"},
        ])
        assert r["total_amount"] is None
        assert r["estimate"] is None
        assert "未从发票中读取到金额" in r["response"]


# ---------------- 预审分步推导明细 ----------------

class TestPrereviewDetail:
    def _review_with_invoice(self):
        return claims_engine.review_uploaded_documents([
            {"filename": "发票.jpg", "doc_kind": "病历文本",
             "extracted_text": "医疗收费票据 合计金额：1200.00元"},
        ])

    def test_detail_text_contains_derivation(self):
        r = self._review_with_invoice()
        text = claims_engine.build_prereview_detail_text(r, "职工医保")
        assert "测算推导过程" in text
        assert "发票合计 1200.00 元" in text
        assert "起付线" in text
        assert "报销比例" in text
        assert "规则依据" in text
        # 个人负担构成 = 起付线 + 比例自付，二者相加应等于个人负担
        e = r["estimate"]
        assert abs(e["out_of_pocket"] - (e["effective_deductible"] + (1200.0 - e["effective_deductible"] - e["reimbursed_basic"]))) < 0.01

    def test_detail_text_no_amount(self):
        r = claims_engine.review_uploaded_documents([
            {"filename": "病历.jpg", "doc_kind": "病历文本", "extracted_text": "门诊病历"},
        ])
        text = claims_engine.build_prereview_detail_text(r, "职工医保")
        assert "暂无法展开测算" in text
        assert "测算推导过程" not in text

    async def test_build_uploaded_prereview_detail(self, db):
        db.add(BodyDocument(user_id=1, filename="发票.png", mime_type="image/png",
                            doc_kind="病历文本", extracted_text="医疗收费票据 合计 3280.50"))
        await db.commit()
        detail = await claims_engine.build_uploaded_prereview_detail(db, "user_001")
        assert detail is not None
        assert detail["response"].startswith("**【报销助手】**")
        assert "测算推导过程" in detail["response"]

    async def test_build_detail_no_docs(self, db):
        assert await claims_engine.build_uploaded_prereview_detail(db, "user_001") is None


# ---------------- 最近上传资料查询 ----------------

class TestRecentDocuments:
    async def test_window_and_context_block(self, db):
        now = datetime.now(UTC)
        db.add(BodyDocument(user_id=1, filename="old.jpg", mime_type="image/jpeg",
                            doc_kind="病历文本", extracted_text="旧资料",
                            uploaded_at=now - timedelta(hours=5)))
        db.add(BodyDocument(user_id=1, filename="new.jpg", mime_type="image/jpeg",
                            doc_kind="MRI报告", extracted_text="新资料 合计 12.00",
                            uploaded_at=now - timedelta(minutes=5)))
        await db.commit()

        docs = await crud.list_recent_body_documents(db, "user_001", within_minutes=120)
        assert [d.filename for d in docs] == ["new.jpg"]

        ctx = await Orchestrator().recent_documents_context(db, "user_001")
        assert "new.jpg" in ctx and "新资料" in ctx
        assert "old.jpg" not in ctx

    async def test_no_docs_empty_context(self, db):
        ctx = await Orchestrator().recent_documents_context(db, "user_001")
        assert ctx == ""

    async def test_duplicate_filename_keeps_latest(self, db):
        now = datetime.now(UTC)
        db.add(BodyDocument(user_id=1, filename="dup.jpg", mime_type="image/jpeg",
                            doc_kind="病历文本", extracted_text="旧版",
                            uploaded_at=now - timedelta(minutes=30)))
        db.add(BodyDocument(user_id=1, filename="dup.jpg", mime_type="image/jpeg",
                            doc_kind="MRI报告", extracted_text="新版",
                            uploaded_at=now - timedelta(minutes=2)))
        await db.commit()

        docs = await crud.list_recent_body_documents(db, "user_001", within_minutes=120)
        assert len(docs) == 1
        assert docs[0].extracted_text == "新版"

    async def test_content_row_preferred_over_empty_latest(self, db):
        now = datetime.now(UTC)
        db.add(BodyDocument(user_id=1, filename="dup2.jpg", mime_type="image/jpeg",
                            doc_kind="病历文本", extracted_text="有效识别内容",
                            uploaded_at=now - timedelta(minutes=30)))
        db.add(BodyDocument(user_id=1, filename="dup2.jpg", mime_type="image/jpeg",
                            doc_kind="其他", extracted_text="",
                            uploaded_at=now - timedelta(minutes=1)))
        await db.commit()

        docs = await crud.list_recent_body_documents(db, "user_001", within_minutes=120)
        assert len(docs) == 1
        assert docs[0].extracted_text == "有效识别内容"

    async def test_create_inherits_text_when_ocr_empty(self, db):
        await crud.create_body_document(db, "user_001", "inh.jpg", "image/jpeg", "病历文本", "旧次识别文本")
        doc = await crud.create_body_document(db, "user_001", "inh.jpg", "image/jpeg", "其他", "")
        assert doc.extracted_text == "旧次识别文本"
        assert doc.doc_kind == "病历文本"
        # 有文本时正常覆盖，不继承
        doc2 = await crud.create_body_document(db, "user_001", "inh.jpg", "image/jpeg", "MRI报告", "新识别")
        assert doc2.extracted_text == "新识别"


# ---------------- 编排智能体（复合对话）上下文与离线预审注入 ----------------

class TestComplexQueryContext:
    def setup_method(self):
        self.o = Orchestrator()

    async def test_offline_claims_injection_replaces_mock(self, monkeypatch):
        """离线模式 + 近期资料：报销助手直接给真实预审，不再输出 claims mock。

        消息与前端快捷入口「帮我预审报销材料」一致（命中 claims+coverage+policy）。
        """
        async def fake_route(intent, message, user_id=None, user_profile=None, extra_context=""):
            return {"response": f"MOCK-{intent}", "agent_type": f"{intent}_agent"}
        monkeypatch.setattr(self.o, "route_to_agent", fake_route)

        result = await self.o.handle_complex_query(
            "帮我预审报销材料，查一下政策",
            "user_001",
            offline_claims_response="真实预审：基于您上传的发票合计 ¥222.36",
        )
        assert "真实预审" in result["response"]
        assert "MOCK-claims" not in result["response"]
        assert "claims" in result["agents_invoked"]
        assert result["multi_agent"] is True

    async def test_extra_context_reaches_dispatched_agents(self, monkeypatch):
        """extra_context（历史+资料）贯穿到各并行调度的 Agent。"""
        captured: dict[str, str] = {}

        async def fake_route(intent, message, user_id=None, user_profile=None, extra_context=""):
            captured[intent] = extra_context
            return {"response": f"OK-{intent}", "agent_type": f"{intent}_agent"}
        monkeypatch.setattr(self.o, "route_to_agent", fake_route)

        await self.o.handle_complex_query(
            "帮我预审报销材料，查一下政策",
            "user_001",
            extra_context="最近上传资料：《发票.jpg》",
            offline_claims_response="真实预审文本",
        )
        # claims 被离线注入替代，不参与调度；其余意图都拿到了上下文
        assert "claims" not in captured
        assert captured, "应有其余意图参与并行调度"
        assert all(v == "最近上传资料：《发票.jpg》" for v in captured.values())

    async def test_single_intent_passes_extra_context(self, monkeypatch):
        """单意图分支同样携带 extra_context。"""
        captured: dict[str, str] = {}

        async def fake_route(intent, message, user_id=None, user_profile=None, extra_context=""):
            captured["intent"] = intent
            captured["ctx"] = extra_context
            return {"response": "OK", "agent_type": f"{intent}_agent"}
        monkeypatch.setattr(self.o, "route_to_agent", fake_route)

        result = await self.o.handle_complex_query(
            "药盒上的批准文号怎么查",  # 仅命中 drug 关键词 → 单意图
            "user_001",
            extra_context="CTX-BLOCK",
        )
        assert captured["intent"] == "drug"
        assert captured["ctx"] == "CTX-BLOCK"
        assert result["multi_agent"] is False

    async def test_no_offline_injection_without_claims_intent(self, monkeypatch):
        """未命中 claims 意图时，离线预审注入不生效。"""
        async def fake_route(intent, message, user_id=None, user_profile=None, extra_context=""):
            return {"response": f"OK-{intent}", "agent_type": f"{intent}_agent"}
        monkeypatch.setattr(self.o, "route_to_agent", fake_route)

        result = await self.o.handle_complex_query(
            "脑电压力评估和心理情绪分析",  # eeg 双关键词 → 单意图 eeg
            "user_001",
            offline_claims_response="不应出现的内容",
        )
        assert "不应出现的内容" not in result["response"]
