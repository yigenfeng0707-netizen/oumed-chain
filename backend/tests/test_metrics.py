"""监控埋点测试（app/metrics.py + /metrics 端点 + 安全事件联动）。

指标模块为进程内全局状态，用例以增量（delta）断言，避免相互污染。
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import metrics  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", False)


class TestMetricsRendering:
    def test_observe_and_render(self):
        before = metrics.render_prometheus()
        metrics.observe_request("/test/route", 200, 0.12)
        metrics.observe_denial("cross_user_access")
        metrics.observe_admin_login_failure()
        text = metrics.render_prometheus(demo_mode=False)

        assert 'http_requests_total{route="/test/route",status="200"}' in text
        assert 'http_request_duration_seconds_bucket{route="/test/route",le="0.25"}' in text
        assert 'auth_denials_total{reason="cross_user_access"}' in text
        assert "admin_login_failures_total" in text
        assert 'demo_mode="false"' in text
        assert text != before

    def test_histogram_bucket_monotonic(self):
        metrics.observe_request("/test/mono", 200, 0.01)
        metrics.observe_request("/test/mono", 200, 10.0)
        text = metrics.render_prometheus()
        rows = {
            line.split("}")[0].split('le="')[1].rstrip('"'): int(line.rsplit(" ", 1)[1])
            for line in text.splitlines()
            if line.startswith('http_request_duration_seconds_bucket{route="/test/mono"')
        }
        vals = list(rows.values())
        assert vals == sorted(vals)  # 累计桶单调不减
        assert rows["+Inf"] >= 2


class TestMetricsEndpoint:
    def test_metrics_exposes_request_counters(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert 'route="/api/health"' in r.text
        # /metrics 自身不计数
        assert 'route="/metrics"' not in r.text

    def test_denial_hook_in_strict_mode(self, client, strict, monkeypatch):
        # 审计写库短路（避免污染本地库）：指标钩子在写库之前执行，不受影响
        class _BoomSession:
            def __call__(self):
                raise RuntimeError("audit disabled in test")

        monkeypatch.setattr("app.database.async_session", _BoomSession())
        text_before = metrics.render_prometheus()

        def _count(text, label):
            for line in text.splitlines():
                if line.startswith(label):
                    return int(line.rsplit(" ", 1)[1])
            return 0

        before = _count(text_before, 'auth_denials_total{reason="missing_or_invalid_user_token"}')
        r = client.get("/api/health/user_001/profile")  # 无 token → 401
        assert r.status_code == 401
        after = _count(metrics.render_prometheus(),
                       'auth_denials_total{reason="missing_or_invalid_user_token"}')
        assert after == before + 1

    def test_admin_login_failure_hook(self, client):
        def _count():
            for line in metrics.render_prometheus().splitlines():
                if line.startswith("admin_login_failures_total "):
                    return int(line.rsplit(" ", 1)[1])
            return 0

        before = _count()
        r = client.post("/api/admin/login",
                        json={"username": "admin", "password": "wrong-password"})
        assert r.status_code == 401
        assert _count() == before + 1
