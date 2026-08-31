"""泛癌卫士（Oncoformer）离线测试：

- visit_synthesizer：17 列 schema / 分箱范围 / 时间轴 / 确定性
- orchestrator：癌种关键词意图路由 + mock 降级
- 路由层：/api/cancer/status、/{user_id}/predict（无 torch 时走队列基线）、
  /cohort/patients（无预计算 JSON 时返回空列表）
- 真模型 forward：skipif 无 torch/权重（仅本地开发环境执行）
"""

import os
import sys

import numpy as np
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, User  # noqa: E402
from app.services.cancer.visit_synthesizer import synthesize_visits  # noqa: E402

from app.services.orchestrator import Orchestrator  # noqa: E402


# ---------------------------------------------------------------------------
# 合成器
# ---------------------------------------------------------------------------

PROFILE = {
    "name": "张阿姨", "age": 62, "gender": "女",
    "chronic_diseases": ["高血压", "糖尿病"], "visit_count_6m": 9,
}


def test_synthesize_schema():
    df = synthesize_visits(PROFILE, user_id="u1")
    row = df.iloc[0]
    n = len(row["valid_mask"])
    assert 6 <= n <= 16
    assert df["tokenized_category_feats"].iloc[0].shape == (1, n)
    assert df["tokenized_float_feats"].iloc[0].shape == (28, n)
    assert df["float_feats"].iloc[0].shape == (28, n)
    assert df["c_cls_labels"].iloc[0].shape == (7, n)
    assert df["f_cls_labels"].iloc[0].shape == (7, n)
    assert df["c_reg_labels"].iloc[0].shape == (1, n)
    tok = df["tokenized_float_feats"].iloc[0]
    valid = tok != -1
    assert ((tok[valid] >= 0) & (tok[valid] <= 255)).all()
    assert (np.asarray(df["valid_mask"].iloc[0]) == True).all()  # noqa: E712
    ti = np.asarray(df["time_index"].iloc[0])
    assert ((ti >= 0) & (ti <= 3650)).all() and (np.diff(ti) > 0).all()
    assert len(df["demo_visit_id"].iloc[0]) == n
    assert df["xray_path"].iloc[0] == ""


def test_synthesize_deterministic_and_profile_aware():
    a = synthesize_visits(PROFILE, user_id="u1")
    b = synthesize_visits(PROFILE, user_id="u1")
    assert np.array_equal(a["tokenized_float_feats"].iloc[0], b["tokenized_float_feats"].iloc[0])
    male = synthesize_visits({**PROFILE, "gender": "男"}, user_id="u1")
    assert male["tokenized_category_feats"].iloc[0][0][0] != a["tokenized_category_feats"].iloc[0][0][0]


# ---------------------------------------------------------------------------
# 意图路由
# ---------------------------------------------------------------------------

def test_cancer_keyword_routing():
    orch = Orchestrator()
    assert orch._keyword_intent("帮我评估一下患癌风险") == "cancer"
    assert orch._keyword_intent("泛癌卫士用的是什么模型") == "cancer"
    # 不误伤：普通健康问题仍归 health_profile
    assert orch._keyword_intent("我最近血压有点高") == "health_profile"


@pytest.mark.asyncio
async def test_cancer_agent_fallback_shape():
    orch = Orchestrator()
    result = await orch.route_to_agent("cancer", "帮我评估患癌风险", user_id="1")
    assert result["agent_type"] if "agent_type" in result else True
    assert "response" in result
    # 无 torch/权重环境走 mock 或队列基线，均不抛异常
    assert isinstance(result.get("data", {}), dict)


# ---------------------------------------------------------------------------
# 路由层（内存库，无 torch → 队列基线降级）
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(id=1, name="张阿姨", age=62, gender="女", city="杭州",
                         insurance_type="职工医保", employee_status="退休"))
        await session.commit()

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.database.async_session", session_factory)
    yield TestClient(app)
    app.dependency_overrides.clear()
    await engine.dispose()


def test_cancer_status(client):
    resp = client.get("/api/cancer/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent"] == "泛癌卫士"
    assert body["engine"] in ("oncoformer", "precomputed")
    assert "disclaimer" in body


def test_cancer_predict_offline_fallback(client):
    resp = client.post("/api/cancer/1/predict", json={"mode": "ehr_only"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["engine"] in ("oncoformer", "cohort_fallback")
    assert "risks" in body and "disclaimer" in body
    # 存档
    hist = client.get("/api/cancer/records/1").json()
    assert any(h["id"] == body["record_id"] for h in hist)


def test_cancer_predict_unknown_user(client):
    resp = client.post("/api/cancer/999/predict", json={})
    assert resp.status_code == 404


def test_cancer_cohort_empty_ok(client):
    resp = client.get("/api/cancer/cohort/patients")
    assert resp.status_code == 200
    assert "patients" in resp.json()


def test_cancer_health_probe(client):
    resp = client.get("/api/health/detailed")
    assert resp.status_code == 200
    assert "cancer_engine" in resp.json()["dependencies"]


# ---------------------------------------------------------------------------
# 真模型 forward（仅本地有 torch + 权重时执行）
# ---------------------------------------------------------------------------

def _torch_ready() -> bool:
    from app.config import settings
    if not settings.ONCOFORMER_CKPT_PATH:
        return False
    try:
        import torch  # noqa: F401
        import timm  # noqa: F401
        import einops  # noqa: F401
        import pytorch_lightning  # noqa: F401
    except ImportError:
        return False
    return os.path.exists(settings.ONCOFORMER_CKPT_PATH)


@pytest.mark.skipif(not _torch_ready(), reason="需要 torch 依赖与 Oncoformer 权重")
def test_oncoformer_real_forward():
    from app.services.cancer.model_provider import CANCER_COLS, get_cancer_model

    df = synthesize_visits(PROFILE, user_id="test-real")
    provider = get_cancer_model()
    r1 = provider.predict_df(df, mode="ehr_only")
    assert set(r1["scores"]["concurrent"]) == set(CANCER_COLS)
    assert all(0.0 <= v <= 1.0 for v in r1["scores"]["concurrent"].values())
    assert r1["pred_age"] is not None and 30 < r1["pred_age"] < 110
    r2 = provider.predict_df(df, mode="ehr_only")
    assert r1["scores"] == r2["scores"], "VAE 随机性应被固定种子抑制"
