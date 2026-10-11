"""P0-1.2 等保三级·安全响应头测试（app/main.py security_headers_middleware）：

- 全局：nosniff / X-Frame-Options DENY / Referrer-Policy / Permissions-Policy
- CSP：API 响应 default-src 'none'；/docs（Swagger UI）豁免
- HSTS：仅生产模式（DEMO_MODE=false）下发
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


def test_global_security_headers(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert "geolocation=()" in r.headers["Permissions-Policy"]


def test_csp_for_api_and_docs_exempt(client):
    assert "default-src 'none'" in client.get("/api/health").headers["Content-Security-Policy"]
    assert "Content-Security-Policy" not in client.get("/docs").headers  # Swagger UI 豁免


def test_hsts_only_in_strict_mode(client, monkeypatch):
    # 演示模式（默认）：无 HSTS（演示环境为 HTTP）
    assert "Strict-Transport-Security" not in client.get("/api/health").headers
    # 生产模式：下发 HSTS
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    hsts = client.get("/api/health").headers["Strict-Transport-Security"]
    assert "max-age=63072000" in hsts and "includeSubDomains" in hsts
