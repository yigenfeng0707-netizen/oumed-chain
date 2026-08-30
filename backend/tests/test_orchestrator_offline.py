"""编排器（Orchestrator）离线降级链路测试 — 无真实 LLM/KB：

- 意图识别：LLM 高/低置信度/异常 → 关键词降级
- 多意图识别与权重分配
- 复合查询：单意图直通 / 多意图并行 + 降级拼接融合 / 全部失败兜底
- 路由分发：8 类智能体的离线降级回答（mock / 结构化模板）
- 结果聚合：建议与 agent 标签映射
"""

import os
import sys

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import Base, User  # noqa: E402
from app.services.orchestrator import Orchestrator  # noqa: E402


class FakeLLM:
    """可控的 LLM 桩：按预设返回意图/对话结果，可注入异常。"""

    def __init__(self, intent=None, confidence=0.9, chat_reply="llm-answer",
                 intent_exc=None, chat_exc=None):
        self.intent = intent or {}
        self.confidence = confidence
        self.chat_reply = chat_reply
        self.intent_exc = intent_exc
        self.chat_exc = chat_exc

    async def extract_intent(self, message):
        if self.intent_exc:
            raise self.intent_exc
        return {"intent": self.intent, "confidence": self.confidence}

    async def chat(self, messages, temperature=0.5, **kwargs):
        if self.chat_exc:
            raise self.chat_exc
        return self.chat_reply


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
    o._llm = None
    o._kb = None
    return o


PROFILE = {
    "found": True, "name": "张阿姨", "age": 62,
    "insurance_type": "职工医保", "employee_status": "退休",
    "chronic_diseases": ["高血压"],
}


# ---------------- 意图识别 ----------------

class TestIntentRecognition:
    async def test_llm_high_confidence_used(self, orch):
        orch._llm = FakeLLM(intent="coverage", confidence=0.9)
        assert await orch.intent_recognition("随便什么") == "coverage"

    async def test_llm_low_confidence_falls_back_to_keywords(self, orch):
        orch._llm = FakeLLM(intent="coverage", confidence=0.3)
        assert await orch.intent_recognition("帮我看看医保报销比例") == "coverage"

    async def test_llm_unknown_intent_falls_back(self, orch):
        orch._llm = FakeLLM(intent="nonexistent_agent", confidence=0.99)
        assert await orch.intent_recognition("你好") == "general"

    async def test_llm_exception_falls_back_to_keywords(self, orch):
        orch._llm = FakeLLM(intent_exc=RuntimeError("gateway down"))
        assert await orch.intent_recognition("帮我做脑电健康评估") == "eeg"

    async def test_keyword_intent_no_hit_returns_general(self, orch):
        assert orch._keyword_intent("今天天气不错") == "general"


class TestMultiIntentRecognition:
    def test_single_intent_message(self, orch):
        intents = orch.multi_intent_recognition("帮我查看医保权益")
        assert [i for i, _ in intents] == ["coverage"]

    def test_no_hit_returns_general_only(self, orch):
        assert orch.multi_intent_recognition("今天天气不错") == [("general", 1.0)]

    def test_composite_question_ranks_intents(self, orch):
        # 复合问题：报销（coverage）+ 政策（policy）+ 流程（claims）
        intents = orch.multi_intent_recognition("心脏搭桥的报销流程和报销比例，有什么政策")
        names = [i for i, _ in intents]
        assert "coverage" in names and "policy" in names
        # 权重降序且和为 1（保留权重>=0.2 的意图）
        weights = [w for _, w in intents]
        assert weights == sorted(weights, reverse=True)
        assert sum(weights) <= 1.0 + 1e-9

    def test_weight_filter_drops_noise(self, orch):
        # coverage 命中多个关键词（医保卡/报销比例/参保/缴费/个人账户/待遇/报多少/报销）
        # policy 只命中 1 个（政策）→ 权重 < 0.2 被过滤
        intents = orch.multi_intent_recognition(
            "医保卡报销比例参保缴费个人账户待遇报多少政策"
        )
        names = [i for i, _ in intents]
        assert "coverage" in names
        assert "policy" not in names


# ---------------- 复合查询 ----------------

class TestComplexQuery:
    async def test_single_intent_passthrough(self, orch):
        result = await orch.handle_complex_query("你好", "1", PROFILE)
        assert result["multi_agent"] is False
        assert result["agents_invoked"] == ["general"]
        assert result["response"]

    async def test_multi_intent_parallel_and_fallback_fusion(self, orch):
        # coverage + policy 并行 → LLM=None → 降级拼接（【权益管家】【政策参谋】）
        result = await orch.handle_complex_query(
            "心脏搭桥的报销比例和报销政策", "1", PROFILE,
        )
        assert result["multi_agent"] is True
        assert set(result["agents_invoked"]) >= {"coverage", "policy"}
        assert "【权益管家】" in result["response"]
        assert "【政策参谋】" in result["response"]
        assert result["intent_weights"]

    async def test_all_agents_fail_returns_timeout_fallback(self, orch, monkeypatch):
        async def boom(*args, **kwargs):
            raise RuntimeError("agent crashed")

        monkeypatch.setattr(orch, "route_to_agent", boom)
        result = await orch.handle_complex_query("报销流程和报销政策", "1", None)
        assert result["multi_agent"] is True
        assert result["agents_invoked"] == []
        assert result["data"]["timeout_fallback"] is True
        assert "超时" in result["response"]

    async def test_fusion_timeout_uses_fuse_fallback(self, orch, monkeypatch):

        async def slow_fuse(message, intents, agent_results, **kwargs):
            # 融合预算 90s，直接抛超时异常确定性触发降级分支（真实超时等待过久）
            raise TimeoutError

        async def quick_route(agent_type, message, user_id=None, user_profile=None, **kwargs):
            return {"response": f"{agent_type}-resp", "data": {}}

        monkeypatch.setattr(orch, "_fuse_multi_agent_results", slow_fuse)
        monkeypatch.setattr(orch, "route_to_agent", quick_route)
        result = await orch.handle_complex_query("报销流程和报销政策", "1", None)
        assert result["multi_agent"] is True
        assert "【" in result["response"]  # 降级拼接格式

    async def test_llm_fusion_used_when_available(self, orch, monkeypatch):
        orch._llm = FakeLLM(chat_reply="融合后的综合回答")

        async def quick_route(agent_type, message, user_id=None, user_profile=None, **kwargs):
            return {"response": f"{agent_type}-resp", "data": {}}

        monkeypatch.setattr(orch, "route_to_agent", quick_route)
        result = await orch.handle_complex_query("报销流程和报销政策", "1", None)
        assert result["response"] == "融合后的综合回答"
        assert result["data"]["fused"] is True
        assert result["evidence"]


# ---------------- 路由分发（离线降级） ----------------

class TestRouteToAgentOffline:
    async def test_policy_agent_falls_back_to_mock(self, orch):
        result = await orch.route_to_agent("policy", "门诊慢病有什么政策", "1", PROFILE)
        assert result["response"]
        assert result["data"]["matched_count"] >= 1

    async def test_policy_agent_no_profile(self, orch):
        result = await orch.route_to_agent("policy", "异地就医政策", "1", None)
        assert result["response"]

    async def test_health_agent_falls_back_to_mock(self, orch):
        result = await orch.route_to_agent("health_profile", "我最近身体怎么样", "1", PROFILE)
        assert "健康" in result["response"]
        assert result["data"]["health_score"] == 70

    async def test_health_agent_uses_profile_data(self, orch):
        result = await orch.route_to_agent(
            "health_profile", "我最近身体怎么样", "1",
            {**PROFILE, "medications": ["降压药"], "recent_visits": 2},
        )
        assert result["data"]["chronic_diseases"] == ["高血压"]

    async def test_coverage_agent_falls_back_to_mock(self, orch):
        result = await orch.route_to_agent("coverage", "我的报销比例是多少", "1", PROFILE)
        assert "报销" in result["response"]
        assert result["data"]["reimbursement_rate"] == 0.70

    async def test_eeg_agent_structured_response(self, orch):
        result = await orch.route_to_agent("eeg", "帮我做脑电健康评估", "1", PROFILE)
        assert "脑电健康评估完成" in result["response"]
        assert "张阿姨" in result["response"]
        assert result["data"]["metrics"]["stress_index"] >= 0
        assert any(e["type"] == "eeg_metric" for e in result["evidence"])

    async def test_imaging_agent_detects_study_types(self, orch):
        for msg, expected in [
            ("帮我分析肺部CT影像", "lung_ct"),
            ("看看这个核磁片子", "brain_mri"),
            ("分析一下胸片", "chest_xray"),
        ]:
            result = await orch.route_to_agent("imaging", msg, "1", None)
            assert result["data"]["study_type"] == expected
            assert "医师复核" in result["response"]
            assert result["data"]["finding_count"] >= 0

    async def test_generic_claims_and_security_mock(self, orch):
        claims = await orch.route_to_agent("claims", "报销流程怎么走", "1", None)
        assert "理赔" in claims["response"] or "报销" in claims["response"]
        security = await orch.route_to_agent("security", "我的数据安全吗", "1", None)
        assert "授权" in security["response"]

    async def test_generic_agent_llm_used_when_available(self, orch):
        orch._llm = FakeLLM(chat_reply="claims-llm-answer")
        result = await orch.route_to_agent("claims", "报销流程怎么走", "1", PROFILE)
        assert result["response"] == "claims-llm-answer"
        assert result["data"]["agent_type"] == "claims"

    async def test_generic_agent_llm_failure_falls_back_to_mock(self, orch):
        orch._llm = FakeLLM(chat_exc=RuntimeError("llm down"))
        result = await orch.route_to_agent("claims", "报销流程怎么走", "1", None)
        assert result["response"]  # mock 兜底

    async def test_general_agent_offline_variants(self, orch):
        greetings = await orch.route_to_agent("general", "你好", "1", PROFILE)
        assert "您好" in greetings["response"]
        identity = await orch.route_to_agent("general", "你是谁能做什么", "1", None)
        assert "MedSignal" in identity["response"]
        thanks = await orch.route_to_agent("general", "谢谢", "1", None)
        assert "不客气" in thanks["response"]
        unknown = await orch.route_to_agent("general", "随便聊聊天气", "1", None)
        assert "离线" in unknown["response"]

    async def test_general_agent_llm_used_when_available(self, orch):
        orch._llm = FakeLLM(chat_reply="自然对话回答")
        result = await orch.route_to_agent("general", "你好", "1", PROFILE)
        assert result["response"] == "自然对话回答"

    async def test_general_agent_llm_failure_falls_back_offline(self, orch):
        """LLM 网关 401/超时等异常时，降级到离线回答而非透传错误。"""
        orch._llm = FakeLLM(chat_exc=RuntimeError("LLM 服务暂不可用"))
        result = await orch.route_to_agent("general", "你好", "1", PROFILE)
        assert "AI 服务" not in result["response"]
        assert "Error" not in result["response"]
        assert result["response"]  # 有离线兑底内容

    async def test_body_agent_with_db(self, orch, db):
        result = await orch._handle_body_agent(
            "我2026年2月查出肺部小结节", "1", None, db=db,
        )
        assert result["data"]["body_updates"]
        assert result["data"]["body_focus"] == "lungs"


# ---------------- 结果聚合 ----------------

class TestAggregateResults:
    def test_suggestions_per_agent_type(self, orch):
        for agent in ["coverage", "claims", "health_profile", "policy",
                      "security", "eeg", "body", "general", "unknown"]:
            out = orch.aggregate_results({"response": "r", "data": {}, "evidence": []}, agent)
            assert out["suggestions"]
            assert out["response"] == "r"

    def test_agent_type_label_mapping(self, orch):
        assert orch.aggregate_results({}, "coverage")["agent_type"] == "coverage_agent"
        assert orch.aggregate_results({}, "eeg")["agent_type"] == "eeg_agent"
        assert orch.aggregate_results({}, "body")["agent_type"] == "body_agent"
        assert orch.aggregate_results({}, "general")["agent_type"] == "assistant_agent"
        assert orch.aggregate_results({}, "whatever")["agent_type"] == "orchestrator_agent"

    def test_evidence_passthrough(self, orch):
        evidence = [{"type": "eeg_metric", "metric": "stress_index", "value": 30}]
        out = orch.aggregate_results({"response": "r", "evidence": evidence}, "eeg")
        assert out["evidence"] == evidence


# ---------------- 融合降级拼接 ----------------

class TestFuseFallback:
    def test_fuse_fallback_joins_agent_answers(self, orch):
        fused = orch._fuse_fallback(
            [("coverage", 0.6), ("policy", 0.4)],
            {"coverage": {"response": "权益回答"}, "policy": {"response": "政策回答"}},
        )
        assert "【权益管家】" in fused["response"]
        assert "【政策参谋】" in fused["response"]
        assert fused["data"]["fallback"] is True

    def test_fuse_fallback_empty_results(self, orch):
        fused = orch._fuse_fallback([], {})
        assert fused["response"] == "暂无法处理该复合问题"
