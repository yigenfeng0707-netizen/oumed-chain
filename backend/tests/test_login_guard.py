"""P0-1.2 等保三级·入侵防范测试（app/services/login_guard.py + 登录端点接线）：

- 单元：连续失败计数 → 阈值触发锁定（30s）→ 指数翻倍 → 封顶 15min；
  成功登录清零；时钟快进后锁定自动解除
- 集成（生产模式 DEMO_MODE=false）：
  * 用户邮箱登录：错误密码 ×5 → 正确密码也 423；解锁后恢复
  * 管理员登录：同款锁定语义
- 演示模式（DEMO_MODE=true）：跳过锁定（零摩擦）

内存 SQLite + dependency_overrides（与 test_rbac 同构）；login_guard 为全局
内存态，用例前后 clear_all 防污染。
"""

import os
import sys

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import hash_password  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, User  # noqa: E402
from app.services import login_guard  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_guard():
    login_guard.clear_all()
    yield
    login_guard.clear_all()


@pytest_asyncio.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(id=1, name="张阿姨", age=62, gender="女", city="杭州",
                         insurance_type="职工医保", employee_status="退休",
                         email="aunt@hospital.cn",
                         password_hash=hash_password("RightPass123")))
        await session.commit()

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.database.async_session", session_factory)
    yield TestClient(app)
    app.dependency_overrides.clear()
    await engine.dispose()


# ------------------------------------------------------------------
# 单元：指数退避语义
# ------------------------------------------------------------------

def test_lock_after_threshold_with_exponential_backoff(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(login_guard, "_now", lambda: clock["t"])
    key = "user:brute@evil.cn"

    for i in range(login_guard.LOCK_THRESHOLD - 1):
        assert login_guard.record_failure(key) == 0  # 未达阈值不锁
    assert login_guard.is_locked(key) == 0

    # 第 5 次失败 → 锁 30s；期间即使 is_locked 也拒绝
    assert login_guard.record_failure(key) == 30
    assert login_guard.is_locked(key) == 30

    # 锁定期内继续失败 → 翻倍（60s/120s…）并顺延截止
    clock["t"] += 10
    assert login_guard.record_failure(key) == 60
    clock["t"] += 10
    assert login_guard.record_failure(key) == 120

    # 封顶 900s
    for _ in range(20):
        clock["t"] += 1
        last = login_guard.record_failure(key)
    assert last == login_guard.MAX_LOCK_SECONDS

    # 时钟快进越过失锁 → 自动解除
    clock["t"] += login_guard.MAX_LOCK_SECONDS + 1
    assert login_guard.is_locked(key) == 0
    assert login_guard.record_failure(key) == 0  # 计数已随过期清理重置


def test_success_resets_counter():
    key = "user:ok@hospital.cn"
    for _ in range(login_guard.LOCK_THRESHOLD - 1):
        login_guard.record_failure(key)
    login_guard.record_success(key)
    assert login_guard.is_locked(key) == 0
    assert login_guard.record_failure(key) == 0  # 计数从 0 重新累积


# ------------------------------------------------------------------
# 集成：登录端点
# ------------------------------------------------------------------

@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", False)


def test_user_login_lockout_returns_423(client, strict):
    """连续错误 5 次 → 正确密码也 423；解锁后（清状态）恢复 200。"""
    for _ in range(5):
        r = client.post("/api/auth/login", json={"email": "aunt@hospital.cn",
                                                 "password": "WrongPass999"})
        assert r.status_code == 401
    # 锁定期间正确密码也拒绝（423 Locked）
    r = client.post("/api/auth/login", json={"email": "aunt@hospital.cn",
                                             "password": "RightPass123"})
    assert r.status_code == 423
    assert "锁定" in r.json()["detail"]

    login_guard.clear_all()  # 模拟锁定窗口过期
    r = client.post("/api/auth/login", json={"email": "aunt@hospital.cn",
                                             "password": "RightPass123"})
    assert r.status_code == 200
    assert r.json()["token"]


def test_user_login_lockout_cleared_by_success(client, strict):
    """失败 4 次后登录成功 → 计数清零（第 5 次失败不再触发锁定）。"""
    for _ in range(4):
        client.post("/api/auth/login", json={"email": "aunt@hospital.cn",
                                             "password": "WrongPass999"})
    assert client.post("/api/auth/login", json={"email": "aunt@hospital.cn",
                                                "password": "RightPass123"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "aunt@hospital.cn",
                                                "password": "WrongPass999"}).status_code == 401
    assert login_guard.is_locked("user:aunt@hospital.cn") == 0


def test_admin_login_lockout(client, strict):
    """管理员登录：连续失败 5 次 → 正确口令也 423。"""
    for _ in range(5):
        r = client.post("/api/admin/login", json={"username": "admin", "password": "错误口令"})
        assert r.status_code == 401
    r = client.post("/api/admin/login", json={"username": "admin",
                                              "password": settings.ADMIN_PASSWORD})
    assert r.status_code == 423

    login_guard.clear_all()
    r = client.post("/api/admin/login", json={"username": "admin",
                                              "password": settings.ADMIN_PASSWORD})
    assert r.status_code == 200


def test_demo_mode_no_lockout(client):
    """演示模式：登录失败不计数、不锁定（零摩擦）。"""
    for _ in range(10):
        r = client.post("/api/auth/login", json={"email": "aunt@hospital.cn",
                                                 "password": "WrongPass999"})
        assert r.status_code == 401
    assert login_guard.is_locked("user:aunt@hospital.cn") == 0
