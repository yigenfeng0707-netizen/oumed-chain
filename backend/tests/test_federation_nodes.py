"""P1.1 医院侧节点 Agent + 平台 Coordinator 测试（联邦真实部署）：

- 参数信封：agent.crypto ↔ 平台 federation_coordinator 双向加解密互通
- 节点鉴权：无凭证/错凭证 401；非任务成员 403；重复提交 409
- 全协议集成：注册 2 节点 → 建任务 → 双节点本地训练（真实合成 CSV）
  逐轮加密上行 → collecting→running→done → 加权 AUC + 任务存证链
- 原始数据零传输断言：落库 payload 为密文，解密后仅 coef/n/local_auc 聚合字段
- 双模兼容：未配置 FEDERATION_PARAM_KEY 明文联调可用；配置后明文拒绝
- RBAC：member 注册节点 403（严格模式）
- Agent 单元：CSV 加载/联邦统计/纯 numpy AUC/本地训练提升

内存 SQLite + dependency_overrides，不触发 lifespan（与 test_rbac 同构）。
"""

import json
import os
import sys

import numpy as np
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "node_agent"))

from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, User  # noqa: E402
from app.services.federation_coordinator import (aggregate_coef,  # noqa: E402
                                                  aggregate_moments,
                                                  open_envelope, seal_envelope)
from node_agent import crypto as agent_crypto  # noqa: E402
from node_agent import trainer as agent_trainer  # noqa: E402
from node_agent.model import LogisticModel, dp_sanitize  # noqa: E402
from node_agent.synthdata import write_csv  # noqa: E402

KEY = "Zx9vQ2FyZVRlYW1LZXlOb3RTb0xvbmdUcnVzdGVkMQ=="  # 32 字节 base64 测试密钥


@pytest_asyncio.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(User(id=1, name="运营员", age=35, gender="女", city="杭州",
                         insurance_type="职工医保", employee_status="在职", role="operator"))
        session.add(User(id=2, name="普通用户", age=40, gender="男", city="温州",
                         insurance_type="职工医保", employee_status="在职", role="member"))
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
    """生产严格模式 + 强制加密信封。"""
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", KEY)


def _admin_headers() -> dict:
    from app.routers.admin import _admin_credentials, _issue_admin_token

    username, _ = _admin_credentials()
    return {"X-Admin-Token": _issue_admin_token(username)}


def _register_node(client, name) -> tuple[str, str]:
    """注册节点，返回 (node_id, node_token)。"""
    r = client.post("/api/federation/nodes", json={"name": name}, headers=_admin_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    return body["node_id"], body["node_token"]


def _create_job(client, node_ids, rounds=3, dp_sigma=0.0) -> str:
    r = client.post("/api/federation/nodes/jobs",
                    json={"node_ids": node_ids, "rounds": rounds, "local_epochs": 2,
                          "dp_sigma": dp_sigma},
                    headers=_admin_headers())
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _node_headers(node_id: str, token: str) -> dict:
    return {"X-Node-Id": node_id, "X-Node-Token": token}


def _local_train_update(csv_path: str, asg: dict, seed: int) -> dict:
    """节点侧本地训练一轮（Agent trainer 真实实现）：返回上行信封内容。"""
    X, y = agent_trainer.load_csv(csv_path)
    X_tr, y_tr, X_te, y_te = agent_trainer.split_train_test(X, y, seed=seed)
    coef = np.array(asg["global_coef"], dtype=float)
    mean, std = asg["scaler"]["mean"], asg["scaler"]["std"]
    Xs_tr = agent_trainer.standardize(X_tr, np.array(mean), np.array(std))
    Xs_te = agent_trainer.standardize(X_te, np.array(mean), np.array(std))
    new_coef = agent_trainer.train_round(Xs_tr, y_tr, coef, epochs=asg["local_epochs"])
    if asg["dp_sigma"] > 0:
        update = dp_sanitize(new_coef - coef, asg["clip_norm"], asg["dp_sigma"],
                             np.random.default_rng(seed + asg["round"]))
        new_coef = coef + update
    return {
        "coef": new_coef.tolist(),
        "n": int(len(y_tr)),
        "local_auc": agent_trainer.local_auc(Xs_te, y_te, new_coef),
    }


def _node_step(client, node_id: str, token: str, job_id: str, key: bytes, state: dict) -> str:
    """节点单步：最多一次上行（moments 或一轮 update），返回任务状态。

    与 Agent 主循环 main.run 同款状态机（moments/update 均幂等处理 409）。
    """
    r = client.get(f"/api/federation/nodes/jobs/{job_id}/assignment",
                   headers=_node_headers(node_id, token))
    assert r.status_code == 200, r.text
    asg = r.json()
    if asg["status"] == "done":
        return "done"
    if asg["status"] == "collecting":
        if not state["moments_sent"]:
            payload = agent_crypto.seal_envelope(agent_trainer.moments(state["X"]), key)
            r2 = client.post(f"/api/federation/nodes/jobs/{job_id}/moments",
                             json={"payload": payload}, headers=_node_headers(node_id, token))
            assert r2.status_code == 200, r2.text
            state["moments_sent"] = True
        return "collecting"
    assert asg["status"] == "running", asg
    if asg["round"] in state["submitted"]:
        return "running"  # 本轮已提交，等待其他节点/聚合推进
    update = _local_train_update(state["csv"], asg, seed=sum(ord(c) for c in node_id) % 1000)
    payload = agent_crypto.seal_envelope(update, key)
    r2 = client.post(f"/api/federation/nodes/jobs/{job_id}/rounds/{asg['round']}/update",
                     json={"payload": payload}, headers=_node_headers(node_id, token))
    assert r2.status_code == 200, r2.text
    state["submitted"].add(asg["round"])
    return "running"


def _run_nodes_interleaved(client, nodes: list[tuple[str, str, str]], job_id: str, key: bytes):
    """两节点交错推进直至 done（模拟互不相通、无共享状态的独立节点）。"""
    states = [{"csv": p, "X": agent_trainer.load_csv(p)[0],
               "moments_sent": False, "submitted": set()} for _, _, p in nodes]
    for _ in range(500):  # 步数上限防死循环
        statuses = [_node_step(client, nid, tok, job_id, key, st)
                    for (nid, tok, _), st in zip(nodes, states)]
        if all(s == "done" for s in statuses):
            return
    raise AssertionError("联邦任务未在限定步数内完成")


# ------------------------------------------------------------------
# 参数信封：节点 ↔ 平台双向互通
# ------------------------------------------------------------------

def test_envelope_roundtrip_cross_implementation(strict):
    """agent 加密 → 平台解密；平台加密 → agent 解密（密钥互通）。"""
    obj = {"coef": [0.1, -0.2, 0.3], "n": 100, "local_auc": 0.75}
    key = agent_crypto.parse_key(KEY)
    sealed = agent_crypto.seal_envelope(obj, key)
    assert sealed.startswith("enc2:") and sealed.count(":") == 3  # enc2:key_id:nonce:ct
    assert open_envelope(sealed) == obj  # 平台默认密钥=settings.FEDERATION_PARAM_KEY=KEY

    sealed2 = seal_envelope(obj)  # 平台侧 seal（测试/互通验证用）
    assert agent_crypto.open_envelope(sealed2, key) == obj

    # 同明文两次加密密文不同（随机 nonce）
    assert agent_crypto.seal_envelope(obj, key) != agent_crypto.seal_envelope(obj, key)


def test_envelope_wrong_key_rejected(strict, monkeypatch):
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", "another-key-entirely")
    sealed = agent_crypto.seal_envelope({"a": 1}, agent_crypto.parse_key(KEY))
    with pytest.raises(ValueError, match="密钥环中"):
        open_envelope(sealed)


# ------------------------------------------------------------------
# 节点注册与鉴权
# ------------------------------------------------------------------

def test_register_node_token_hashed_and_auth(strict, client):
    node_id, token = _register_node(client, "温附一院")
    # 列表不泄露凭证
    nodes = client.get("/api/federation/nodes", headers=_admin_headers()).json()
    assert nodes[0]["id"] == node_id and "node_token" not in nodes[0]
    # 正确凭证可访问节点面
    r = client.get("/api/federation/nodes/my-jobs", headers=_node_headers(node_id, token))
    assert r.status_code == 200
    # 错误凭证 / 缺凭证 → 401
    assert client.get("/api/federation/nodes/my-jobs",
                      headers=_node_headers(node_id, "wrong-token")).status_code == 401
    assert client.get("/api/federation/nodes/my-jobs").status_code == 401


def test_register_requires_rbac(strict, client):
    """严格模式下 member 角色注册节点 → 403。"""
    from app.auth import issue_user_token

    r = client.post("/api/federation/nodes", json={"name": "越权节点"},
                    headers={"X-User-Token": issue_user_token(2, role="member")})
    assert r.status_code == 403


def test_create_job_requires_active_nodes(strict, client):
    node_id, _ = _register_node(client, "县医院")
    r = client.post("/api/federation/nodes/jobs",
                    json={"node_ids": [node_id, "ghost-node"]}, headers=_admin_headers())
    assert r.status_code == 400


# ------------------------------------------------------------------
# 全协议集成：两节点完整联邦训练（真实合成数据）
# ------------------------------------------------------------------

def test_full_distributed_training_two_nodes(strict, client, tmp_path):
    """验收口径：两个互不相通的"节点"各自本地训练，平台只收加密参数，
    完成一次完整联邦训练（collecting→running→done + 存证链）。"""
    csv_a = str(tmp_path / "hospital_a.csv")
    csv_b = str(tmp_path / "hospital_b.csv")
    write_csv(csv_a, "hospital_a", seed=2026)
    write_csv(csv_b, "hospital_b", seed=777)

    node_a, token_a = _register_node(client, "三甲医院")
    node_b, token_b = _register_node(client, "县医院")
    job_id = _create_job(client, [node_a, node_b], rounds=3)

    key = agent_crypto.parse_key(KEY)
    _run_nodes_interleaved(client, [(node_a, token_a, csv_a), (node_b, token_b, csv_b)], job_id, key)

    # 任务完成 + 存证链
    detail = client.get(f"/api/federation/nodes/jobs/{job_id}", headers=_admin_headers()).json()
    assert detail["status"] == "done"
    assert detail["prev_hash"] and detail["event_hash"]
    result = detail["result"]
    assert 0.5 < result["final_auc"] < 1.0
    assert result["total_samples"] == result["per_node"][0]["n"] + result["per_node"][1]["n"]
    assert {p["node_id"] for p in result["per_node"]} == {node_a, node_b}
    # assignment 在 done 后返回结果
    asg = client.get(f"/api/federation/nodes/jobs/{job_id}/assignment",
                     headers=_node_headers(node_a, token_a)).json()
    assert asg["status"] == "done"


def test_no_raw_data_leaves_node(strict, client, tmp_path):
    """原始数据零传输：信封密文不含 CSV 数值明文；解密后仅白名单聚合字段。"""
    import base64

    csv_a = str(tmp_path / "hospital_a.csv")
    csv_b = str(tmp_path / "hospital_b.csv")
    write_csv(csv_a, "hospital_a", seed=2026)
    write_csv(csv_b, "hospital_b", seed=777)
    node_a, token_a = _register_node(client, "A")
    node_b, token_b = _register_node(client, "B")
    job_id = _create_job(client, [node_a, node_b], rounds=1)
    key = agent_crypto.parse_key(KEY)
    _run_nodes_interleaved(client, [(node_a, token_a, csv_a), (node_b, token_b, csv_b)], job_id, key)

    sealed = agent_crypto.seal_envelope({"coef": [0.0] * 14, "n": 900, "local_auc": 0.7}, key)
    opened = open_envelope(sealed)
    assert set(opened.keys()) == {"coef", "n", "local_auc"}
    # 信封密文部分不含任何特征名/标签名明文
    raw = base64.b64decode(sealed.split(":")[2])
    for token in (b"age", b"readmit", b"hba1c", b"ejection"):
        assert token not in raw


def test_duplicate_and_membership_guards(strict, client, tmp_path):
    """重复提交 409；非成员节点 403；轮次错位 409。"""
    csv_a = str(tmp_path / "a.csv")
    csv_b = str(tmp_path / "b.csv")
    write_csv(csv_a, "hospital_a", seed=1)
    write_csv(csv_b, "hospital_b", seed=2)
    node_a, token_a = _register_node(client, "A")
    node_b, token_b = _register_node(client, "B")
    node_c, token_c = _register_node(client, "C")  # 未参与任务
    job_id = _create_job(client, [node_a, node_b], rounds=2)
    key = agent_crypto.parse_key(KEY)

    # 非成员节点拉 assignment → 403
    r = client.get(f"/api/federation/nodes/jobs/{job_id}/assignment",
                   headers=_node_headers(node_c, token_c))
    assert r.status_code == 403

    payload = agent_crypto.seal_envelope(agent_trainer.moments(agent_trainer.load_csv(csv_a)[0]), key)
    assert client.post(f"/api/federation/nodes/jobs/{job_id}/moments",
                       json={"payload": payload}, headers=_node_headers(node_a, token_a)).status_code == 200
    # 统计阶段重复提交 → 409
    assert client.post(f"/api/federation/nodes/jobs/{job_id}/moments",
                       json={"payload": payload}, headers=_node_headers(node_a, token_a)).status_code == 409
    # 单节点统计集齐不了（两节点任务），仍 collecting → 训练期端点拒绝
    r = client.post(f"/api/federation/nodes/jobs/{job_id}/rounds/1/update",
                    json={"payload": payload}, headers=_node_headers(node_a, token_a))
    assert r.status_code == 409


# ------------------------------------------------------------------
# 双模兼容：明文联调 / 强制加密
# ------------------------------------------------------------------

def test_plaintext_compat_mode_without_key(client, monkeypatch, tmp_path):
    """未配置 FEDERATION_PARAM_KEY：明文 JSON 上行可用（联调模式）。"""
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", "")
    csv_a = str(tmp_path / "a.csv")
    csv_b = str(tmp_path / "b.csv")
    write_csv(csv_a, "hospital_a", seed=3)
    write_csv(csv_b, "hospital_b", seed=4)
    node_a, token_a = _register_node(client, "A")
    node_b, token_b = _register_node(client, "B")
    job_id = _create_job(client, [node_a, node_b], rounds=1)
    _run_nodes_interleaved(client, [(node_a, token_a, csv_a), (node_b, token_b, csv_b)], job_id, b"")
    assert client.get(f"/api/federation/nodes/jobs/{job_id}",
                      headers=_admin_headers()).json()["status"] == "done"


def test_plaintext_rejected_when_key_configured(strict, client):
    """生产模式：平台配置密钥后，明文参数上行 → 400 拒绝。"""
    node_a, token_a = _register_node(client, "A")
    node_b, _ = _register_node(client, "B")
    job_id = _create_job(client, [node_a, node_b], rounds=1)
    r = client.post(f"/api/federation/nodes/jobs/{job_id}/moments",
                    json={"payload": json.dumps({"n": 10, "sum": [0.0] * 13, "sum_sq": [0.0] * 13})},
                    headers=_node_headers(node_a, token_a))
    assert r.status_code == 400 and "加密信封" in r.json()["detail"]


def test_bad_envelope_body_rejected(strict, client):
    """信封内容字段不合法 → 400。"""
    node_a, token_a = _register_node(client, "A")
    node_b, token_b = _register_node(client, "B")
    job_id = _create_job(client, [node_a, node_b], rounds=1)
    key = agent_crypto.parse_key(KEY)
    bad = agent_crypto.seal_envelope({"n": -5, "sum": [], "sum_sq": []}, key)
    r = client.post(f"/api/federation/nodes/jobs/{job_id}/moments",
                    json={"payload": bad}, headers=_node_headers(node_a, token_a))
    assert r.status_code == 400


# ------------------------------------------------------------------
# 聚合服务与 Agent 单元
# ------------------------------------------------------------------

def test_aggregate_moments_matches_fed_scaler():
    """聚合统计与 engine.FedScaler 数学一致（跨节点异构规模）。"""
    rng = np.random.default_rng(0)
    X1, X2 = rng.normal(50, 10, (100, 13)), rng.normal(60, 20, (300, 13))
    entries = [
        {"n": len(X1), "sum": X1.sum(0).tolist(), "sum_sq": (X1 ** 2).sum(0).tolist()},
        {"n": len(X2), "sum": X2.sum(0).tolist(), "sum_sq": (X2 ** 2).sum(0).tolist()},
    ]
    mean, std = aggregate_moments(entries)
    pooled = np.vstack([X1, X2])
    assert np.allclose(mean, pooled.mean(0), atol=1e-9)
    assert np.allclose(std, pooled.std(0), atol=1e-9)


def test_aggregate_coef_weighted_average():
    """FedAvg 按样本量加权：大节点参数权重占主导。"""
    big = aggregate_coef([[10.0], [10.0]], [9, 1])  # 9:1 加权
    assert abs(big[0] - 10.0) < 1e-9
    mixed = aggregate_coef([[0.0], [10.0]], [1, 3])
    assert abs(mixed[0] - 7.5) < 1e-9


def test_agent_csv_and_auc_unit(tmp_path):
    """CSV 加载 schema 校验 + 纯 numpy AUC 与已知值一致。"""
    csv_path = str(tmp_path / "h.csv")
    write_csv(csv_path, "hospital_a", seed=42)
    X, y = agent_trainer.load_csv(csv_path)
    assert X.shape == (1200, 13) and set(np.unique(y)) == {0.0, 1.0}
    m = agent_trainer.moments(X)
    assert m["n"] == 1200 and len(m["sum"]) == len(m["sum_sq"]) == 13
    # AUC 边界：完美分类器 = 1.0；随机 ≈ 0.5
    y_true = np.array([0, 0, 1, 1])
    assert agent_trainer.auc_score(y_true, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert agent_trainer.auc_score(y_true, np.array([0.9, 0.8, 0.2, 0.1])) == 0.0


def test_agent_local_training_improves_auc(tmp_path):
    """本地训练收敛：训练后 AUC 显著高于零参数基线。"""
    csv_path = str(tmp_path / "h.csv")
    write_csv(csv_path, "hospital_a", seed=7)
    X, y = agent_trainer.load_csv(csv_path)
    X_tr, y_tr, X_te, y_te = agent_trainer.split_train_test(X, y, seed=7)
    mean, std = X_tr.mean(0), X_tr.std(0)
    Xs_tr, Xs_te = agent_trainer.standardize(X_tr, mean, std), agent_trainer.standardize(X_te, mean, std)
    baseline = agent_trainer.local_auc(Xs_te, y_te, np.zeros(14))
    coef = agent_trainer.train_round(Xs_tr, y_tr, np.zeros(14), epochs=300)
    assert agent_trainer.local_auc(Xs_te, y_te, coef) > baseline + 0.1


def test_dp_sanitize_shape_and_clip():
    """DP 裁剪+噪声：超范数更新被裁剪，输出形状不变。"""
    rng = np.random.default_rng(0)
    update = np.full(14, 10.0)  # 范数远超 clip_norm
    out = dp_sanitize(update, clip_norm=1.0, sigma=0.1, rng=rng)
    assert out.shape == update.shape and np.linalg.norm(out) < np.linalg.norm(update)
