"""支付宝在线支付测试（app/routers/payments.py + app/services/payment.py）。

沙箱模式全链路：下单→模拟扫码→订单完结→市场分账存证；
回调验签（沙箱签名防伪）；严格模式会话鉴权；管理端对账；
live 电脑网站支付分支（收银台表单/主动查单，mock 客户端）。
内存 SQLite + dependency_overrides，不触发真实网关。
"""

import os
import sys

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, DataProduct, DataTransaction  # noqa: E402
from app.routers.admin import _admin_credentials, _issue_admin_token  # noqa: E402
from app.services.payment import _sandbox_sig  # noqa: E402


def _admin_token() -> str:
    username, _ = _admin_credentials()
    return _issue_admin_token(username)


@pytest_asyncio.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(DataProduct(id="prod_test", name="脱敏病历数据集",
                                provider="测试医院", data_type="数据集", price=199))
        await session.commit()

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(settings, "ALIPAY_MODE", "sandbox")
    monkeypatch.setattr("app.database.async_session", session_factory)  # 审计落库同用内存库
    yield TestClient(app), session_factory
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", False)


# ---------------- 下单 ----------------

class TestPrecreate:
    def test_marketplace_order(self, client):
        c, _ = client
        r = c.post("/api/payments/precreate",
                   json={"kind": "marketplace", "ref_id": "prod_test", "user_id": "user_001"})
        assert r.status_code == 200
        body = r.json()
        assert body["order_no"].startswith("OM")
        assert body["gateway"] == "sandbox"
        assert body["amount_cents"] == 19900
        assert body["qr_code"].startswith("oumedtrust://sandbox-pay")
        assert body["status"] == "pending"

    def test_agent_service_order(self, client):
        c, _ = client
        r = c.post("/api/payments/precreate",
                   json={"kind": "agent_service", "ref_id": "cancer_predict"})
        assert r.status_code == 200
        assert r.json()["amount_cents"] == 990  # ¥9.9

    def test_unknown_product_404(self, client):
        c, _ = client
        r = c.post("/api/payments/precreate",
                   json={"kind": "marketplace", "ref_id": "not-exist"})
        assert r.status_code == 404

    def test_unknown_agent_service_404(self, client):
        c, _ = client
        r = c.post("/api/payments/precreate",
                   json={"kind": "agent_service", "ref_id": "no_such"})
        assert r.status_code == 404

    def test_bad_kind_400(self, client):
        c, _ = client
        r = c.post("/api/payments/precreate", json={"kind": "weird", "ref_id": "x"})
        assert r.status_code == 400

    def test_requires_session_in_strict_mode(self, client, strict):
        c, _ = client
        r = c.post("/api/payments/precreate",
                   json={"kind": "agent_service", "ref_id": "cancer_predict"})
        assert r.status_code == 401


# ---------------- 沙箱全链路：模拟扫码 → 分账存证 ----------------

class TestSandboxFlow:
    def test_marketplace_full_flow(self, client):
        c, session_factory = client
        r = c.post("/api/payments/precreate",
                   json={"kind": "marketplace", "ref_id": "prod_test", "user_id": "user_001"})
        order_no = r.json()["order_no"]

        r = c.post("/api/payments/sandbox/complete", json={"order_no": order_no})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "paid"
        assert body["pay_proof"]
        settle = body["settle"]
        assert settle["revenue"]["provider"] + settle["revenue"]["platform"] \
            + settle["revenue"]["contributor"] == 19900
        assert settle["event_hash"]  # 分账已入存证链

        # 轮询接口可见已付
        r = c.get(f"/api/payments/order/{order_no}")
        assert r.json()["status"] == "paid"
        assert r.json()["trade_no"].startswith("SIM-")

    def test_complete_idempotent(self, client):
        c, _ = client
        r = c.post("/api/payments/precreate",
                   json={"kind": "agent_service", "ref_id": "imaging_report"})
        order_no = r.json()["order_no"]
        c.post("/api/payments/sandbox/complete", json={"order_no": order_no})
        r = c.post("/api/payments/sandbox/complete", json={"order_no": order_no})
        assert r.json()["already"] is True

    def test_complete_blocked_in_live_mode(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setattr(settings, "ALIPAY_MODE", "live")
        r = c.post("/api/payments/sandbox/complete", json={"order_no": "OM-ANY"})
        assert r.status_code == 403

    def test_complete_unknown_order_404(self, client):
        c, _ = client
        r = c.post("/api/payments/sandbox/complete", json={"order_no": "OM-NOPE"})
        assert r.status_code == 404


# ---------------- 回调验签（沙箱签名防伪） ----------------

class TestNotify:
    def test_notify_with_valid_sig(self, client):
        c, _ = client
        r = c.post("/api/payments/precreate",
                   json={"kind": "agent_service", "ref_id": "health_profile"})
        order_no = r.json()["order_no"]

        r = c.post("/api/payments/alipay/notify", data={
            "out_trade_no": order_no,
            "trade_no": "ALIPAY-TEST-001",
            "trade_status": "TRADE_SUCCESS",
            "sandbox_sig": _sandbox_sig(order_no),
        })
        assert r.text == "success"
        assert c.get(f"/api/payments/order/{order_no}").json()["status"] == "paid"

    def test_notify_with_bad_sig_rejected(self, client):
        c, _ = client
        r = c.post("/api/payments/precreate",
                   json={"kind": "agent_service", "ref_id": "health_profile"})
        order_no = r.json()["order_no"]
        r = c.post("/api/payments/alipay/notify", data={
            "out_trade_no": order_no, "trade_status": "TRADE_SUCCESS",
            "sandbox_sig": "forged-sig",
        })
        assert r.text == "fail"
        assert c.get(f"/api/payments/order/{order_no}").json()["status"] == "pending"

    def test_notify_unknown_order_fails(self, client):
        c, _ = client
        r = c.post("/api/payments/alipay/notify", data={
            "out_trade_no": "OM-UNKNOWN", "trade_status": "TRADE_SUCCESS",
            "sandbox_sig": _sandbox_sig("OM-UNKNOWN"),
        })
        assert r.text == "fail"

    def test_notify_non_success_event_ack(self, client):
        c, _ = client
        r = c.post("/api/payments/precreate",
                   json={"kind": "agent_service", "ref_id": "health_profile"})
        order_no = r.json()["order_no"]
        r = c.post("/api/payments/alipay/notify", data={
            "out_trade_no": order_no, "trade_status": "WAIT_BUYER_PAY",
            "sandbox_sig": _sandbox_sig(order_no),
        })
        assert r.text == "success"
        assert c.get(f"/api/payments/order/{order_no}").json()["status"] == "pending"


# ---------------- 管理端对账 ----------------

class TestAdminPayments:
    def test_requires_admin(self, client):
        c, _ = client
        assert c.get("/api/admin/payments").status_code == 401

    def test_revenue_summary(self, client):
        c, _ = client
        r = c.post("/api/payments/precreate",
                   json={"kind": "marketplace", "ref_id": "prod_test"})
        order_no = r.json()["order_no"]
        c.post("/api/payments/sandbox/complete", json={"order_no": order_no})

        r = c.get("/api/admin/payments", headers={"X-Admin-Token": _admin_token()})
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "sandbox"
        assert body["paid_count"] == 1
        assert body["revenue_cents"] == 19900


# ---------------- live 电脑网站支付（mock 客户端） ----------------

class _FakeAlipayClient:
    """模拟 python-alipay-sdk：收银台表单 + 查单（首次未付，第二次已付）。"""
    def __init__(self):
        self.query_calls = 0

    def api_alipay_trade_page_pay(self, **kwargs):
        return f"<form action='gateway'>{kwargs['out_trade_no']}</form>"

    def api_alipay_trade_query(self, out_trade_no):
        self.query_calls += 1
        if self.query_calls == 1:
            return {"code": "40004", "sub_code": "ACQ.TRADE_NOT_EXIST"}
        return {"code": "10000", "trade_status": "TRADE_SUCCESS", "trade_no": "2088T123"}


class TestLivePagePay:
    def test_precreate_returns_pay_form(self, client, monkeypatch):
        from app.services import payment
        c, _ = client
        monkeypatch.setattr(settings, "ALIPAY_MODE", "live")
        monkeypatch.setattr(settings, "ALIPAY_APP_ID", "2021006195651204")
        monkeypatch.setattr(payment, "_client", _FakeAlipayClient())
        r = c.post("/api/payments/precreate",
                   json={"kind": "marketplace", "ref_id": "prod_test"})
        assert r.status_code == 200
        body = r.json()
        assert body["gateway"] == "live"
        assert body["pay_form"].startswith("<form")
        assert not body["qr_code"]

    def test_order_polling_completes_via_query(self, client, monkeypatch):
        """回调未达时，轮询主动查单补偿完结（首次未付→第二次已付）。"""
        from app.services import payment
        c, _ = client
        monkeypatch.setattr(settings, "ALIPAY_MODE", "live")
        monkeypatch.setattr(settings, "ALIPAY_APP_ID", "2021006195651204")
        monkeypatch.setattr(payment, "_client", _FakeAlipayClient())
        order_no = c.post("/api/payments/precreate",
                          json={"kind": "marketplace", "ref_id": "prod_test"}).json()["order_no"]
        assert c.get(f"/api/payments/order/{order_no}").json()["status"] == "pending"
        r = c.get(f"/api/payments/order/{order_no}").json()
        assert r["status"] == "paid"
        assert r["trade_no"] == "2088T123"

    def test_query_returns_none_in_sandbox(self):
        from app.services import payment
        assert payment.query_trade_status("OM1") is None
