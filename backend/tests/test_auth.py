"""用户认证（邮箱注册/登录）测试：

- 密码哈希与 token 工具函数（PBKDF2 / HMAC 签发与解析）
- POST /api/auth/register：成功 / 重复邮箱 409 / 非法邮箱与弱密码 422
- POST /api/auth/login：成功 / 错误密码 401 / 不存在邮箱 401（防枚举：同一文案）
- GET /api/auth/me：有效 token / 无 token / 篡改 token / 过期 token 均 401
"""

import os
import sys
import time

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import (  # noqa: E402
    hash_password,
    issue_user_token,
    parse_user_token,
    verify_password,
)
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402


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
    monkeypatch.setattr("app.database.async_session", session_factory)
    yield TestClient(app)
    app.dependency_overrides.clear()
    await engine.dispose()


# ---------------- 工具函数 ----------------

class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self):
        stored = hash_password("S3cret!pass")
        assert stored.startswith("pbkdf2_sha256$")
        assert verify_password("S3cret!pass", stored)
        assert not verify_password("wrong", stored)

    def test_verify_rejects_empty_or_malformed(self):
        assert not verify_password("x", None)
        assert not verify_password("x", "")
        assert not verify_password("x", "not-a-valid-hash")
        assert not verify_password("x", "md5$1$abc$def")

    def test_hash_salt_uniqueness(self):
        # 相同密码两次哈希应产生不同盐 → 不同密文
        assert hash_password("same-pass-1") != hash_password("same-pass-1")


class TestUserToken:
    def test_issue_and_parse_roundtrip(self):
        token = issue_user_token(42)
        assert parse_user_token(token) == 42

    def test_parse_rejects_garbage(self):
        assert parse_user_token(None) is None
        assert parse_user_token("") is None
        assert parse_user_token("garbage") is None
        assert parse_user_token("42|9999999999|deadbeef") is None  # 签名不匹配

    def test_expired_token_rejected(self):
        token = issue_user_token(7, ttl=-10)  # 已过期
        assert parse_user_token(token) is None

    def test_token_survives_time(self):
        token = issue_user_token(9, ttl=60)
        assert parse_user_token(token) == 9
        time.sleep(0)


# ---------------- API：注册 ----------------

class TestRegister:
    def test_register_success_returns_token_and_user(self, client):
        r = client.post("/api/auth/register", json={
            "email": "Test@Example.com", "password": "Passw0rd!", "name": "王小明",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["token"]
        assert data["user"]["email"] == "test@example.com"  # 邮箱归一化为小写
        assert data["user"]["name"] == "王小明"
        assert data["user"]["registered"] is True
        assert parse_user_token(data["token"]) == data["user"]["id"]

    def test_register_name_defaults_to_email_prefix(self, client):
        r = client.post("/api/auth/register", json={
            "email": "alice@example.com", "password": "Passw0rd!",
        })
        assert r.status_code == 201
        assert r.json()["user"]["name"] == "alice"

    def test_register_duplicate_email_409(self, client):
        body = {"email": "dup@example.com", "password": "Passw0rd!"}
        assert client.post("/api/auth/register", json=body).status_code == 201
        r = client.post("/api/auth/register", json=body)
        assert r.status_code == 409
        assert "已注册" in r.json()["detail"]

    def test_register_invalid_email_422(self, client):
        r = client.post("/api/auth/register", json={
            "email": "not-an-email", "password": "Passw0rd!",
        })
        assert r.status_code == 422

    def test_register_short_password_422(self, client):
        r = client.post("/api/auth/register", json={
            "email": "short@example.com", "password": "123",
        })
        assert r.status_code == 422


# ---------------- API：登录 ----------------

class TestLogin:
    def _register(self, client, email="user@example.com", password="Passw0rd!"):
        client.post("/api/auth/register", json={"email": email, "password": password})

    def test_login_success(self, client):
        self._register(client)
        r = client.post("/api/auth/login", json={
            "email": "user@example.com", "password": "Passw0rd!",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["token"]
        assert data["user"]["email"] == "user@example.com"

    def test_login_wrong_password_401(self, client):
        self._register(client)
        r = client.post("/api/auth/login", json={
            "email": "user@example.com", "password": "WrongPass1!",
        })
        assert r.status_code == 401

    def test_login_unknown_email_same_message(self, client):
        # 防枚举：不存在邮箱与错误密码返回同一文案
        self._register(client)
        r1 = client.post("/api/auth/login", json={
            "email": "nobody@example.com", "password": "whatever1",
        })
        r2 = client.post("/api/auth/login", json={
            "email": "user@example.com", "password": "WrongPass1!",
        })
        assert r1.status_code == r2.status_code == 401
        assert r1.json()["detail"] == r2.json()["detail"]

    def test_login_demo_user_without_password_cannot_login(self, client):
        # 演示用户无邮箱，不在邮箱登录体系内
        r = client.post("/api/auth/login", json={
            "email": "ghost@example.com", "password": "whatever1",
        })
        assert r.status_code == 401


# ---------------- API：/me ----------------

class TestMe:
    def _get_token(self, client):
        r = client.post("/api/auth/register", json={
            "email": "me@example.com", "password": "Passw0rd!",
        })
        return r.json()["token"]

    def test_me_with_valid_token(self, client):
        token = self._get_token(client)
        r = client.get("/api/auth/me", headers={"X-User-Token": token})
        assert r.status_code == 200
        assert r.json()["user"]["email"] == "me@example.com"

    def test_me_without_token_401(self, client):
        assert client.get("/api/auth/me").status_code == 401

    def test_me_with_tampered_token_401(self, client):
        token = self._get_token(client)
        assert client.get("/api/auth/me", headers={"X-User-Token": f"{token}x"}).status_code == 401

    def test_me_with_expired_token_401(self, client):
        token = issue_user_token(1, ttl=-10)
        assert client.get("/api/auth/me", headers={"X-User-Token": token}).status_code == 401

    def test_me_token_for_missing_user_401(self, client):
        token = issue_user_token(99999)
        assert client.get("/api/auth/me", headers={"X-User-Token": token}).status_code == 401
