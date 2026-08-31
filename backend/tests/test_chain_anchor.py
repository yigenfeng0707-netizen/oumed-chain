"""存证链外部锚定测试（P2-3.4，app/services/chain_anchor.py）：

- RFC 3161 TimeStampReq 最小 DER 编码正确性
- TimeStampResp 状态解析（短/长形式长度、异常输入）
- 链尖计算与锚定落库：TSA 成功 / 不可达降级 / 未配置降级
- 管理员端点鉴权 + 公开锚定视图

内存 SQLite + dependency_overrides + monkeypatch TSA（不发起真实网络请求）。
"""

import base64
import hashlib
import os
import sys
import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Base,
    ChainAnchor,
    DataProduct,
    DataTransaction,
    FederationJob,
)
from app.routers.admin import _admin_credentials, _issue_admin_token  # noqa: E402
from app.services import chain_anchor  # noqa: E402


def _fake_tsr(status: int = 0, padding: int = 200) -> bytes:
    """构造合法外形的 TimeStampResp：SEQUENCE { SEQUENCE { INTEGER status }, <token> }"""
    status_info = bytes([0x30, 0x03, 0x02, 0x01, status])
    token = bytes(range(1, 1 + padding))  # 撑长形式长度分支
    body = status_info + token
    # 外层长度 > 127 → 长形式（0x81）
    return bytes([0x30, 0x81, len(body)]) + body


def _admin_token() -> str:
    username, _ = _admin_credentials()
    return _issue_admin_token(username)


@pytest_asyncio.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(settings, "CHAIN_ANCHOR_TSA_URL", "https://tsa.test/tsr")
    yield TestClient(app), session_factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_chain_events(session_factory):
    """种两条存证事件：联邦任务链 + 交易链。"""
    async with session_factory() as session:
        session.add(FederationJob(
            id=str(uuid.uuid4()), task="hf_readmission", rounds=3,
            status="done", prev_hash="0" * 64, event_hash="f" * 64,
        ))
        session.add(DataProduct(id="p1", name="脱敏数据集", provider="测试医院",
                                data_type="数据集"))
        session.add(DataTransaction(
            id=str(uuid.uuid4()), product_id="p1", product_name="脱敏数据集",
            buyer="应用A", amount=100, status="已成交",
            prev_hash="0" * 64, event_hash="a" * 64,
        ))
        await session.commit()


# ---------------- DER 编码与响应解析 ----------------

class TestDerCodec:
    def test_timestamp_request_structure(self):
        digest = hashlib.sha256(b"oumed-chain").digest()
        req = chain_anchor.build_timestamp_request(digest, nonce=12345)
        assert req[0] == 0x30
        assert req[1] == len(req) - 2  # 短形式长度自洽
        assert chain_anchor._OID_SHA256 in req
        assert digest in req

    def test_rejects_non_sha256_digest(self):
        with pytest.raises(ValueError):
            chain_anchor.build_timestamp_request(b"short")

    def test_parse_status_granted(self):
        assert chain_anchor.parse_response_status(_fake_tsr(0)) == 0
        assert chain_anchor.parse_response_status(_fake_tsr(1)) == 1

    def test_parse_status_rejection_and_malformed(self):
        assert chain_anchor.parse_response_status(_fake_tsr(2)) == 2
        assert chain_anchor.parse_response_status(b"") is None
        assert chain_anchor.parse_response_status(b"\x03\x01\x00") is None


# ---------------- 锚定服务（TSA monkeypatch，零真实网络） ----------------

class TestAnchorService:
    @pytest.mark.asyncio
    async def test_anchored_with_tsa_success(self, client, monkeypatch):
        _, session_factory = client
        await _seed_chain_events(session_factory)
        captured = {}

        def fake_post(url, body, timeout=15.0):
            captured["url"], captured["body"] = url, body
            return _fake_tsr(0)

        monkeypatch.setattr(chain_anchor, "tsa_post", fake_post)
        async with session_factory() as session:
            anchor = await chain_anchor.create_anchor(session, tsa_url="https://tsa.test/tsr")

        assert anchor.status == "anchored"
        assert anchor.event_count == 2
        assert anchor.fed_tip == "f" * 64
        assert anchor.market_tip == "a" * 64
        expected_tip = hashlib.sha256(f"{'f' * 64}|{'a' * 64}|2".encode()).hexdigest()
        assert anchor.tip_hash == expected_tip
        assert base64.b64decode(anchor.ts_token_b64) == _fake_tsr(0)
        assert captured["url"] == "https://tsa.test/tsr"
        # 提交给 TSA 的请求体包含链尖摘要
        assert bytes.fromhex(expected_tip) in captured["body"]

    @pytest.mark.asyncio
    async def test_offline_when_tsa_unreachable(self, client, monkeypatch):
        _, session_factory = client

        def boom(url, body, timeout=15.0):
            raise OSError("network unreachable")

        monkeypatch.setattr(chain_anchor, "tsa_post", boom)
        async with session_factory() as session:
            anchor = await chain_anchor.create_anchor(session, tsa_url="https://tsa.test/tsr")

        assert anchor.status == "offline"
        assert "network unreachable" in anchor.error
        assert len(anchor.tip_hash) == 64  # 链尖仍留痕

    @pytest.mark.asyncio
    async def test_offline_when_no_tsa_url(self, client):
        _, session_factory = client
        async with session_factory() as session:
            anchor = await chain_anchor.create_anchor(session, tsa_url=None)
        assert anchor.status == "offline"
        assert "CHAIN_ANCHOR_TSA_URL" in anchor.error
        assert anchor.event_count == 0  # 空链

    @pytest.mark.asyncio
    async def test_tsa_rejection_degrades(self, client, monkeypatch):
        _, session_factory = client
        monkeypatch.setattr(chain_anchor, "tsa_post",
                            lambda url, body, timeout=15.0: _fake_tsr(2))
        async with session_factory() as session:
            anchor = await chain_anchor.create_anchor(session, tsa_url="https://tsa.test/tsr")
        assert anchor.status == "offline"
        assert "PKIStatus=2" in anchor.error


# ---------------- 端点：管理员锚定 + 公开视图 ----------------

class TestAnchorEndpoints:
    def test_post_anchor_requires_admin(self, client):
        c, _ = client
        r = c.post("/api/admin/security/anchor")
        assert r.status_code == 401

    def test_post_and_list_anchors(self, client, monkeypatch):
        c, session_factory = client
        monkeypatch.setattr(chain_anchor, "tsa_post",
                            lambda url, body, timeout=15.0: _fake_tsr(0))
        headers = {"X-Admin-Token": _admin_token()}

        r = c.post("/api/admin/security/anchor", headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "anchored"
        assert r.json()["ts_token_b64"]

        r = c.get("/api/admin/security/anchors", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["tsa_url"] == "https://tsa.test/tsr"

    def test_public_anchor_view_hides_token(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(chain_anchor, "tsa_post",
                            lambda url, body, timeout=15.0: _fake_tsr(0))
        headers = {"X-Admin-Token": _admin_token()}
        c.post("/api/admin/security/anchor", headers=headers)

        r = c.get("/api/security/chain-anchors")
        assert r.status_code == 200
        body = r.json()
        assert body["anchored_count"] == 1
        assert body["latest"]["status"] == "anchored"
        # 公开视图不泄露时间戳令牌原文
        assert "ts_token_b64" not in body["anchors"][0]

    def test_public_anchor_view_empty_chain(self, client):
        c, _ = client
        r = c.get("/api/security/chain-anchors")
        assert r.status_code == 200
        assert r.json()["latest"] is None
