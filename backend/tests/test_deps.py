"""P0 身份作用域鉴权测试（app/deps.py 双模）：

- DEMO_MODE=true（默认）：全部放行（演示/路演零摩擦）
- DEMO_MODE=false（生产）：
  * {user_id} 端点：无/无效 token → 401；token 与路径用户不匹配 → 403；匹配或管理员代查 → 200
  * 会话级端点：任意有效会话放行，无会话 → 401
  * 公共端点（如 /api/body/organs）不受影响

内存 SQLite + dependency_overrides，不触发 lifespan（与 test_routers_offline 同构）。
"""

import os
import sys

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import issue_user_token  # noqa: E402
from app.config import settings  # noqa: E402
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
    monkeypatch.setattr("app.database.async_session", session_factory)
    yield TestClient(app)
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
def strict(monkeypatch):
    """切换到生产严格模式（测试结束自动还原）。"""
    monkeypatch.setattr(settings, "DEMO_MODE", False)


def _admin_token() -> str:
    from app.routers.admin import _admin_credentials, _issue_admin_token

    username, _ = _admin_credentials()
    return _issue_admin_token(username)


# ---------------- 演示模式（默认）：零摩擦放行 ----------------

class TestDemoMode:
    def test_user_scoped_open_without_token(self, client):
        r = client.get("/api/health/user_001/profile")
        assert r.status_code == 200

    def test_session_endpoint_open_without_token(self, client):
        r = client.get("/api/federation/jobs")
        assert r.status_code == 200


# ---------------- 生产严格模式：用户作用域端点 ----------------

class TestStrictScope:
    def test_401_without_token(self, client, strict):
        r = client.get("/api/health/user_001/profile")
        assert r.status_code == 401

    def test_401_with_invalid_token(self, client, strict):
        r = client.get("/api/health/user_001/profile",
                       headers={"X-User-Token": "not-a-valid-token"})
        assert r.status_code == 401

    def test_403_cross_user_access(self, client, strict):
        # user_002 的 token 访问 user_001 的数据 → 越权
        r = client.get("/api/health/user_001/profile",
                       headers={"X-User-Token": issue_user_token(2)})
        assert r.status_code == 403

    def test_200_matching_token(self, client, strict):
        r = client.get("/api/health/user_001/profile",
                       headers={"X-User-Token": issue_user_token(1)})
        assert r.status_code == 200

    def test_200_admin_proxy(self, client, strict):
        # 管理员可代查任意用户
        r = client.get("/api/health/user_002/alerts",
                       headers={"X-Admin-Token": _admin_token()})
        assert r.status_code == 200

    def test_401_invalid_admin_token(self, client, strict):
        r = client.get("/api/health/user_002/alerts",
                       headers={"X-Admin-Token": "forged-admin-token"})
        assert r.status_code == 401

    def test_router_level_scope_coverage(self, client, strict):
        # 路由级 ScopeDep（coverage）：无 token 同样被拦
        assert client.get("/api/coverage/user_001").status_code == 401
        ok = client.get("/api/coverage/user_001",
                        headers={"X-User-Token": issue_user_token(1)})
        assert ok.status_code == 200


# ---------------- 生产严格模式：会话级端点 ----------------

class TestStrictSession:
    def test_401_without_session(self, client, strict):
        assert client.get("/api/federation/jobs").status_code == 401
        assert client.get("/api/users").status_code == 401

    def test_200_with_user_session(self, client, strict):
        r = client.get("/api/federation/jobs",
                       headers={"X-User-Token": issue_user_token(1)})
        assert r.status_code == 200

    def test_200_with_admin_session(self, client, strict):
        r = client.get("/api/federation/jobs",
                       headers={"X-Admin-Token": _admin_token()})
        assert r.status_code == 200


# ---------------- 生产严格模式：公共端点不受影响 ----------------

class TestStrictPublic:
    def test_public_endpoints_stay_open(self, client, strict):
        assert client.get("/api/body/organs").status_code == 200
        assert client.get("/api/federation/overview").status_code == 200
        assert client.get("/api/marketplace/products").status_code == 200


# ---------------- 生产严格模式：越权拒绝审计落库 ----------------

class TestDenialAudit:
    def test_denials_logged_and_admin_queryable(self, client, strict):
        # 制造三类拒绝：无 token(401) / 跨用户(403) / 列表端点无会话(401)
        assert client.get("/api/health/user_001/profile").status_code == 401
        assert client.get("/api/health/user_001/profile",
                          headers={"X-User-Token": issue_user_token(2)}).status_code == 403
        assert client.get("/api/users").status_code == 401

        r = client.get("/api/admin/security/denials",
                       headers={"X-Admin-Token": _admin_token()})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 3
        reasons = {log["reason"] for log in data["logs"]}
        assert {"missing_or_invalid_user_token", "cross_user_access", "missing_session"} <= reasons

        # 按目标用户过滤：仅 user_001 相关拒绝，无会话型（空目标）不混入
        r2 = client.get("/api/admin/security/denials?user_id=user_001",
                        headers={"X-Admin-Token": _admin_token()})
        assert r2.status_code == 200
        assert all(log["target_user_id"] == "user_001" for log in r2.json()["logs"])

    def test_denials_visible_in_user_audit_log(self, client, strict):
        client.get("/api/health/user_001/profile",
                   headers={"X-User-Token": issue_user_token(2)})  # 403 越权
        r = client.get("/api/security/audit-log/user_001",
                       headers={"X-User-Token": issue_user_token(1)})
        assert r.status_code == 200
        denied = [log for log in r.json()["logs"] if log["action"] == "拒绝访问"]
        assert len(denied) >= 1
        assert "403" in denied[0]["detail"]

    def test_admin_denials_endpoint_requires_admin_token(self, client, strict):
        assert client.get("/api/admin/security/denials").status_code == 401
