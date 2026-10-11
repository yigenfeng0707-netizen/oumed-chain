"""P0-1.1 多角色 RBAC 测试（app/auth.py 角色 token + app/deps.require_roles/get_current_user）：

- token：角色写入签名 payload；旧 3 段格式兼容（member）；篡改角色/非法角色拒绝
- RBAC 守卫（严格模式 DEMO_MODE=false）：
  * regulator/operator 角色可查越权审计（/api/admin/security/denials）
  * member 角色 → 403（role_forbidden）+ 审计落库
  * 无 token → 401；超管 X-Admin-Token → 200（allow_admin）
- 角色管理：PUT /api/admin/users/{id}/role（仅超管）→ 重新登录后新角色生效
- 演示模式（DEMO_MODE=true）：全部放行（零摩擦）

内存 SQLite + dependency_overrides，不触发 lifespan（与 test_deps 同构）。
"""

import os
import sys

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import ROLES, issue_user_token, parse_token_identity, parse_user_token  # noqa: E402
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
                         insurance_type="职工医保", employee_status="退休", role="member"))
        session.add(User(id=2, name="监管员", age=40, gender="男", city="温州",
                         insurance_type="职工医保", employee_status="在职", role="regulator"))
        session.add(User(id=3, name="运营员", age=35, gender="女", city="杭州",
                         insurance_type="职工医保", employee_status="在职", role="operator"))
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


# ------------------------------------------------------------------
# token 层：角色签发/解析/兼容
# ------------------------------------------------------------------

def test_token_carries_role():
    token = issue_user_token(2, role="regulator")
    ident = parse_token_identity(token)
    assert ident == (2, "regulator", None)
    # parse_user_token 兼容旧签名（只取 uid）
    assert parse_user_token(token) == 2

    # P2.3：hospital_id 写入签名 payload 并可解析
    token2 = issue_user_token(2, role="hospital_admin", hospital_id=7)
    assert parse_token_identity(token2) == (2, "hospital_admin", 7)


def test_token_default_member_and_legacy_compat():
    ident = parse_token_identity(issue_user_token(1))
    assert ident == (1, "member", None)
    # 旧 3 段格式 uid|exp|sig → member（issue 后手工转旧格式）
    uid, exp = 1, 9999999999
    import hashlib
    import hmac as hmac_mod
    import os as os_mod

    secret = os_mod.getenv("YIBAO_SESSION_SECRET", "demo").encode()
    payload = f"{uid}|{exp}"
    sig = hmac_mod.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:32]
    assert parse_token_identity(f"{payload}|{sig}") == (1, "member", None)
    # 旧 4 段格式 uid|exp|role|sig（P2.3 之前）→ hospital_id=None
    payload4 = f"{uid}|{exp}|operator"
    sig4 = hmac_mod.new(secret, payload4.encode(), hashlib.sha256).hexdigest()[:32]
    assert parse_token_identity(f"{payload4}|{sig4}") == (1, "operator", None)


def test_token_role_tamper_rejected():
    token = issue_user_token(2, role="regulator")
    forged = token.replace("regulator", "operator")  # 改角色不改签名
    assert parse_token_identity(forged) is None


def test_token_invalid_role_rejected_at_issue():
    with pytest.raises(ValueError):
        issue_user_token(1, role="root")


# ------------------------------------------------------------------
# RBAC 守卫：越权审计端点（监管示范接线）
# ------------------------------------------------------------------

def test_rbac_regulator_can_query_denials(client, strict):
    r = client.get("/api/admin/security/denials",
                   headers={"X-User-Token": issue_user_token(2, role="regulator")})
    assert r.status_code == 200
    assert "logs" in r.json()


def test_rbac_operator_can_query_denials(client, strict):
    r = client.get("/api/admin/security/denials",
                   headers={"X-User-Token": issue_user_token(3, role="operator")})
    assert r.status_code == 200


def test_rbac_member_forbidden_403(client, strict):
    r = client.get("/api/admin/security/denials",
                   headers={"X-User-Token": issue_user_token(1, role="member")})
    assert r.status_code == 403
    assert "需要角色" in r.json()["detail"]
    # 403 落审计（role_forbidden）
    r2 = client.get("/api/admin/security/denials",
                    headers={"X-Admin-Token": _admin_token()})
    reasons = [log["reason"] for log in r2.json()["logs"]]
    assert "role_forbidden" in reasons


def test_rbac_missing_token_401(client, strict):
    r = client.get("/api/admin/security/denials")
    assert r.status_code == 401


def test_rbac_super_admin_allow(client, strict):
    r = client.get("/api/admin/security/denials",
                   headers={"X-Admin-Token": _admin_token()})
    assert r.status_code == 200


def test_rbac_demo_mode_pass_through(client):
    """演示模式：无 token 也放行（零摩擦）。"""
    r = client.get("/api/admin/security/denials")
    assert r.status_code == 200
    assert r.json()["demo_mode"] is True


# ------------------------------------------------------------------
# 角色管理：用户列表 + 设置角色（仅超管）
# ------------------------------------------------------------------

def test_admin_users_list_has_role(client, strict):
    r = client.get("/api/admin/users", headers={"X-Admin-Token": _admin_token()})
    assert r.status_code == 200
    body = r.json()
    assert body["roles"] == list(ROLES)
    roles = {u["public_id"]: u["role"] for u in body["users"]}
    assert roles["user_001"] == "member"
    assert roles["user_002"] == "regulator"


def test_admin_set_role_then_relogin(client, strict):
    # 1. member → hospital_admin（超管操作）
    r = client.put("/api/admin/users/1/role",
                   json={"role": "hospital_admin"},
                   headers={"X-Admin-Token": _admin_token()})
    assert r.status_code == 200
    assert r.json()["old_role"] == "member" and r.json()["role"] == "hospital_admin"

    # 2. 旧 token（member）仍被拒 —— 无状态 token 权衡：重新登录后生效
    old_token = issue_user_token(1, role="member")
    r2 = client.get("/api/admin/security/denials", headers={"X-User-Token": old_token})
    assert r2.status_code == 403

    # 3. 重新登录 → 新 token 携带 hospital_admin → 通过（regulator/operator 守卫不含
    #    hospital_admin，此处验证角色确实生效：用 403 detail 区分"角色不符"而非"member"）
    r3 = client.get("/api/admin/security/denials",
                    headers={"X-User-Token": issue_user_token(1, role="hospital_admin")})
    assert r3.status_code == 403
    assert "hospital_admin" in r3.json()["detail"]


def test_admin_set_role_requires_admin(client, strict):
    r = client.put("/api/admin/users/1/role",
                   json={"role": "operator"},
                   headers={"X-User-Token": issue_user_token(1, role="member")})
    assert r.status_code == 401  # 无超管凭证


def test_admin_set_role_invalid_rejected(client, strict):
    r = client.put("/api/admin/users/1/role",
                   json={"role": "root"},
                   headers={"X-Admin-Token": _admin_token()})
    assert r.status_code == 422


# ------------------------------------------------------------------
# 登录接口签发带角色 token
# ------------------------------------------------------------------

def test_login_token_has_role(client, strict):
    # 演示用户无邮箱密码 → 注册一个
    reg = client.post("/api/auth/register",
                      json={"email": "rbac@test.cn", "password": "password123"})
    assert reg.status_code == 201
    assert reg.json()["user"]["role"] == "member"

    login = client.post("/api/auth/login",
                        json={"email": "rbac@test.cn", "password": "password123"})
    assert login.status_code == 200
    token = login.json()["token"]
    assert parse_token_identity(token)[1] == "member"

    # /me 返回 role 字段
    me = client.get("/api/auth/me", headers={"X-User-Token": token})
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "member"
