"""路由层离线测试（agents 聊天 + eeg 端点）：

- 内存 SQLite + dependency_overrides，不触发 lifespan（LLM 保持离线降级）
- POST /api/agents/chat：意图路由 / 会话持久化 / 404 / 409 / 档案管家归档
- GET /api/agents/conversations/{id}：历史回放 / 404
- POST /api/agents/complex-chat：多智能体并行 + 降级拼接
- GET /api/eeg/*：states / real 数据集清单与详情 / 会话评估 / 历史与政策联动
"""

import os
import sys

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, User  # noqa: E402


@pytest_asyncio.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(id=1, name="张阿姨", age=62, gender="女", city="杭州",
                         insurance_type="职工医保", employee_status="退休"))
        session.add(User(id=2, name="李先生", age=45, gender="男", city="杭州",
                         insurance_type="居民医保", employee_status="在职"))
        await session.commit()

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    # body 智能体等内部自开 session 走 app.database.async_session，
    # 同样重定向到内存库，避免测试写入本地开发库 yibao.db
    monkeypatch.setattr("app.database.async_session", session_factory)
    # TestClient 不以 context manager 使用 → 不触发 lifespan → LLM 保持离线降级
    yield TestClient(app)
    app.dependency_overrides.clear()
    await engine.dispose()


# ---------------- /api/agents/chat ----------------

class TestChatEndpoint:
    def test_chat_general_offline(self, client):
        r = client.post("/api/agents/chat", json={"message": "你好"})
        assert r.status_code == 200
        data = r.json()
        assert data["agent_type"] == "assistant_agent"
        assert data["response"]
        assert data["conversation_id"]
        assert data["user_profile"]["name"] == "张阿姨"
        assert data["suggestions"]

    def test_chat_user_not_found(self, client):
        r = client.post("/api/agents/chat",
                        json={"message": "你好", "user_id": "user_999"})
        assert r.status_code == 404

    def test_chat_creates_and_reuses_conversation(self, client):
        r1 = client.post("/api/agents/chat", json={"message": "你好"})
        conv_id = r1.json()["conversation_id"]
        r2 = client.post("/api/agents/chat",
                         json={"message": "谢谢", "conversation_id": conv_id})
        assert r2.status_code == 200

        history = client.get(f"/api/agents/conversations/{conv_id}")
        assert history.status_code == 200
        messages = history.json()["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]

    def test_chat_conversation_ownership_conflict(self, client):
        r1 = client.post("/api/agents/chat",
                         json={"message": "你好", "user_id": "user_001"})
        conv_id = r1.json()["conversation_id"]
        r2 = client.post("/api/agents/chat",
                         json={"message": "你好", "user_id": "user_002",
                               "conversation_id": conv_id})
        assert r2.status_code == 409

    def test_chat_body_intent_archives(self, client):
        r = client.post("/api/agents/chat",
                        json={"message": "我2026年2月查出肺部小结节"})
        assert r.status_code == 200
        data = r.json()
        assert data["agent_type"] == "body_agent"
        assert data["body_focus"] == "lungs"
        assert data["body_updates"]

    def test_chat_eeg_intent_offline(self, client):
        r = client.post("/api/agents/chat", json={"message": "帮我做脑电健康评估"})
        data = r.json()
        assert data["agent_type"] == "eeg_agent"
        assert "脑电健康评估完成" in data["response"]
        assert data["data"]["metrics"]["stress_index"] >= 0

    def test_conversation_not_found(self, client):
        r = client.get("/api/agents/conversations/nonexistent")
        assert r.status_code == 404


# ---------------- /api/agents/complex-chat ----------------

class TestComplexChatEndpoint:
    def test_complex_chat_multi_agent_offline(self, client):
        r = client.post("/api/agents/complex-chat",
                        json={"message": "心脏搭桥的报销比例和报销政策"})
        assert r.status_code == 200
        data = r.json()
        assert data["multi_agent"] is True
        assert set(data["agents_invoked"]) >= {"coverage", "policy"}
        assert "【权益管家】" in data["response"] or data["response"]

    def test_complex_chat_single_intent(self, client):
        r = client.post("/api/agents/complex-chat", json={"message": "你好"})
        data = r.json()
        assert data["multi_agent"] is False
        assert data["agents_invoked"] == ["general"]

    def test_complex_chat_user_not_found(self, client):
        r = client.post("/api/agents/complex-chat",
                        json={"message": "你好", "user_id": "user_999"})
        assert r.status_code == 404


# ---------------- /api/eeg/* ----------------

class TestEegEndpoints:
    def test_states(self, client):
        r = client.get("/api/eeg/states")
        assert r.status_code == 200
        data = r.json()
        assert len(data["states"]) >= 5
        assert data["sample_rate"] == 256

    def test_real_list(self, client):
        r = client.get("/api/eeg/real/list")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 1
        # 至少一个真实公开数据集（eegmmidb / eegemotions27）
        sources = {s["source"] for s in data["sessions"]}
        assert sources & {"eegmmidb", "eegemotions27"}

    def test_real_list_source_filter(self, client):
        r = client.get("/api/eeg/real/list?source=eegmmidb")
        data = r.json()
        assert all(s["source"] == "eegmmidb" for s in data["sessions"])

    def test_real_detail_and_404(self, client):
        listing = client.get("/api/eeg/real/list").json()
        if not listing["sessions"]:
            pytest.skip("manifest 为空（未接入真实数据集）")
        record_id = listing["sessions"][0]["record_id"]
        r = client.get(f"/api/eeg/real/{record_id}")
        assert r.status_code == 200
        assert r.json()["record_id"] == record_id
        assert r.status_code == 200
        assert client.get("/api/eeg/real/nonexistent").status_code == 404

    def test_create_session(self, client):
        r = client.post("/api/eeg/user_001/session?duration_seconds=4")
        assert r.status_code == 200
        data = r.json()
        assert data["metrics"]["stress_index"] >= 0
        assert data["session_id"]

    def test_create_session_invalid_state(self, client):
        r = client.post("/api/eeg/user_001/session?mental_state=nonexistent")
        assert r.status_code == 400

    def test_create_session_user_not_found(self, client):
        r = client.post("/api/eeg/user_999/session")
        assert r.status_code == 404

    def test_latest_generates_when_no_history(self, client):
        r = client.get("/api/eeg/user_001/latest")
        assert r.status_code == 200
        data = r.json()
        assert data["from_history"] is False
        assert data["waveform"]

    def test_history_empty(self, client):
        r = client.get("/api/eeg/user_001/history")
        assert r.status_code == 200
        assert r.json()["total_sessions"] == 0

    def test_policy_links(self, client):
        r = client.get("/api/eeg/user_001/policy-links")
        assert r.status_code == 200
        assert "policy_links" in r.json()
