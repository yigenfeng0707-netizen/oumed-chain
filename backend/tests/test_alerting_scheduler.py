"""告警触达 + 定时锚定调度测试（alerting.py + anchor_scheduler.py）。

覆盖：Webhook 报文格式/冷却防刷屏/撞库滑窗阈值、调度循环容错与取消、
管理端登录失败联动告警（HTTP 集成）。
"""

import asyncio
import io
import json
import os
import sys

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import alerting, anchor_scheduler, chain_anchor  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_alert_state():
    alerting.reset_state()
    yield
    alerting.reset_state()


class _FakeResp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestAlertSend:
    def test_no_webhook_only_logs(self, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", "")
        assert alerting.send_alert("k1", "标题", "详情") is False

    def test_webhook_payload_format(self, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", "https://hook.test/robot")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp()

        monkeypatch.setattr(alerting.urllib.request, "urlopen", fake_urlopen)
        assert alerting.send_alert("k2", "测试告警", "详情内容") is True
        assert captured["url"] == "https://hook.test/robot"
        assert captured["body"]["msgtype"] == "text"
        assert "测试告警" in captured["body"]["text"]["content"]

    def test_cooldown_suppresses_repeat(self, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", "https://hook.test/robot")
        posts = []

        def fake_urlopen(req, timeout=None):
            posts.append(1)
            return _FakeResp()

        monkeypatch.setattr(alerting.urllib.request, "urlopen", fake_urlopen)
        assert alerting.send_alert("k3", "t", "d") is True
        assert alerting.send_alert("k3", "t", "d") is False  # 冷却中
        assert len(posts) == 1
        # 不同 key 不受冷却影响
        assert alerting.send_alert("k4", "t", "d") is True
        assert len(posts) == 2


class TestBruteForceWindow:
    def test_threshold_triggers_alert(self, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_LOGIN_FAILURE_THRESHOLD", 3)
        hits = []
        monkeypatch.setattr(
            alerting, "send_alert",
            lambda key, title, detail, cooldown=alerting.COOLDOWN_SECONDS: hits.append(key) or True,
        )
        assert alerting.record_admin_login_failure("hacker") is False
        assert alerting.record_admin_login_failure("hacker") is False
        assert alerting.record_admin_login_failure("hacker") is True
        assert hits == ["admin_brute_force"]

    def test_expired_failures_pruned(self, monkeypatch):
        import time
        monkeypatch.setattr(settings, "ALERT_LOGIN_FAILURE_THRESHOLD", 2)
        # 植入窗口外的旧失败记录（应被修剪）
        alerting._login_failures.append(time.time() - 700)
        assert alerting.record_admin_login_failure("x") is False


class TestAnchorScheduler:
    def test_disabled_when_interval_zero(self, monkeypatch):
        monkeypatch.setattr(settings, "CHAIN_ANCHOR_INTERVAL_HOURS", 0)
        assert anchor_scheduler.start_anchor_task() is None

    async def test_start_task_runs(self, monkeypatch):
        monkeypatch.setattr(settings, "CHAIN_ANCHOR_INTERVAL_HOURS", 1)

        async def noop(_interval):
            return None

        monkeypatch.setattr(anchor_scheduler, "anchor_loop", noop)
        task = anchor_scheduler.start_anchor_task()
        assert task is not None
        await task

    async def test_loop_period_and_cancel(self, monkeypatch):
        runs = {"n": 0}

        async def fake_once():
            runs["n"] += 1
            return {"status": "anchored", "tip_hash": "ab" * 32}

        monkeypatch.setattr(anchor_scheduler, "scheduled_anchor_once", fake_once)
        sleeps = []

        async def fake_sleep(sec):
            sleeps.append(sec)
            if len(sleeps) >= 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(anchor_scheduler.asyncio, "sleep", fake_sleep)
        with pytest.raises(asyncio.CancelledError):
            await anchor_scheduler.anchor_loop(24)
        assert runs["n"] == 2
        assert sleeps == [86400.0, 86400.0]

    async def test_loop_survives_anchor_error(self, monkeypatch):
        runs = {"n": 0}

        async def flaky_once():
            runs["n"] += 1
            if runs["n"] == 1:
                raise RuntimeError("TSA 不可达")
            return {"status": "anchored", "tip_hash": "cd" * 32}

        monkeypatch.setattr(anchor_scheduler, "scheduled_anchor_once", flaky_once)
        sleeps = []

        async def fake_sleep(sec):
            sleeps.append(sec)
            if len(sleeps) >= 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(anchor_scheduler.asyncio, "sleep", fake_sleep)
        with pytest.raises(asyncio.CancelledError):
            await anchor_scheduler.anchor_loop(1)
        assert runs["n"] == 2  # 首次失败未杀死循环

    async def test_scheduled_anchor_once_session_wiring(self, monkeypatch):
        """内存库会话装配：create_anchor 收到可用会话并落库产物可序列化。"""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        from app.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr("app.database.async_session", factory)

        created = {}

        async def fake_create(db, tsa_url=None, timeout=15.0):
            created["tsa_url"] = tsa_url
            from app.models import ChainAnchor
            a = ChainAnchor(tip_hash="ef" * 32, fed_tip="0" * 64, market_tip="0" * 64,
                            event_count=0, tsa_url="", status="offline")
            db.add(a)
            await db.commit()
            await db.refresh(a)
            return a

        monkeypatch.setattr(chain_anchor, "create_anchor", fake_create)
        monkeypatch.setattr(settings, "CHAIN_ANCHOR_TSA_URL", "https://tsa.test/tsr")
        info = await anchor_scheduler.scheduled_anchor_once()
        assert info["status"] == "offline"
        assert created["tsa_url"] == "https://tsa.test/tsr"


class TestAdminLoginAlertHook:
    def test_login_failures_trigger_webhook_alert(self, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_LOGIN_FAILURE_THRESHOLD", 2)
        hits = []
        monkeypatch.setattr(
            alerting, "send_alert",
            lambda key, title, detail, cooldown=alerting.COOLDOWN_SECONDS: hits.append(key) or True,
        )
        with TestClient(app) as client:
            for _ in range(2):
                r = client.post("/api/admin/login",
                                json={"username": "admin", "password": "wrong-pass"})
                assert r.status_code == 401
        assert "admin_brute_force" in hits
