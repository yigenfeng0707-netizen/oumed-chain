"""P2.3 多租户测试（医院级租户隔离 + 计费计量）：

- token 绑定租户：hospital_id 写入签名 payload（篡改/未绑定场景见 test_rbac）
- 隔离验收（核心）：租户 A 无法访问租户 B 任何资源
  * 节点：注册自动归属本院；列表 scope 过滤；跨租户指派 403
  * 任务：非本院任务对租户管理员 404 隐藏（列表/详情/暂停/恢复）
  * 用户列表：hospital_admin 仅见本院用户
  * 未绑定租户的 hospital_admin → 403（不落全局视图）
- operator/超管：全局视图（跨租户可见）
- 计费计量：federated_jobs / node_updates 记录点与报表数字精确一致；
  hospital_admin 报表强制本院 scope
- 医院租户 CRUD：超管创建/停用；code 唯一 409；operator 无权创建

内存 SQLite + dependency_overrides，不触发 lifespan（与 test_rbac 同构）。
"""

import os
import sys

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "node_agent"))

from app.auth import issue_user_token  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Hospital, User  # noqa: E402
from node_agent import crypto as agent_crypto  # noqa: E402

KEY = "Zx9vQ2FyZVRlYW1LZXlOb3RTb0xvbmdUcnVzdGVkMQ=="  # 32 字节 base64 测试密钥


@pytest_asyncio.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(Hospital(id=1, name="附一院", code="wz-fy"))
        session.add(Hospital(id=2, name="县医院", code="wz-x"))
        session.add(User(id=1, name="A院管理员", age=40, gender="女", city="温州",
                         insurance_type="职工医保", employee_status="在职",
                         role="hospital_admin", hospital_id=1))
        session.add(User(id=2, name="B院管理员", age=42, gender="男", city="温州",
                         insurance_type="职工医保", employee_status="在职",
                         role="hospital_admin", hospital_id=2))
        session.add(User(id=3, name="运营员", age=35, gender="女", city="杭州",
                         insurance_type="职工医保", employee_status="在职",
                         role="operator"))
        session.add(User(id=4, name="未绑定管理员", age=38, gender="男", city="杭州",
                         insurance_type="职工医保", employee_status="在职",
                         role="hospital_admin"))
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


def _user_headers(uid: int, role: str, hospital_id: int | None = None) -> dict:
    return {"X-User-Token": issue_user_token(uid, role=role, hospital_id=hospital_id)}


def _a_admin() -> dict:
    return _user_headers(1, "hospital_admin", hospital_id=1)


def _b_admin() -> dict:
    return _user_headers(2, "hospital_admin", hospital_id=2)


def _operator() -> dict:
    return _user_headers(3, "operator")


def _register_node(client, headers: dict, name: str, hospital_id: int | None = None) -> tuple[str, str]:
    body = {"name": name}
    if hospital_id is not None:
        body["hospital_id"] = hospital_id
    r = client.post("/api/federation/nodes", json=body, headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    return data["node_id"], data["node_token"]


def _create_job(client, headers: dict, node_ids: list[str], rounds: int = 1) -> dict:
    r = client.post("/api/federation/nodes/jobs",
                    json={"node_ids": node_ids, "rounds": rounds, "local_epochs": 1},
                    headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _node_headers(node_id: str, token: str) -> dict:
    return {"X-Node-Id": node_id, "X-Node-Token": token}


# ------------------------------------------------------------------
# 医院租户 CRUD（超管）
# ------------------------------------------------------------------

def test_hospital_crud_by_super_admin(client, strict):
    # 创建
    r = client.post("/api/admin/hospitals", json={"name": "中医院", "code": "wz-zy"},
                    headers=_admin_headers())
    assert r.status_code == 200
    hid = r.json()["id"]
    # code 唯一 → 409
    r2 = client.post("/api/admin/hospitals", json={"name": "重复", "code": "wz-zy"},
                     headers=_admin_headers())
    assert r2.status_code == 409
    # 状态变更
    r3 = client.put(f"/api/admin/hospitals/{hid}/status", json={"status": "disabled"},
                    headers=_admin_headers())
    assert r3.status_code == 200 and r3.json()["status"] == "disabled"
    # 不存在 → 404
    assert client.put("/api/admin/hospitals/999/status", json={"status": "active"},
                      headers=_admin_headers()).status_code == 404


def test_hospital_create_requires_admin(client, strict):
    r = client.post("/api/admin/hospitals", json={"name": "X", "code": "x"},
                    headers=_operator())
    assert r.status_code == 401  # 无超管凭证


# ------------------------------------------------------------------
# 节点隔离：注册归属 + 列表 scope
# ------------------------------------------------------------------

def test_node_registration_scoped_to_own_hospital(strict, client):
    node_a, _ = _register_node(client, _a_admin(), "A院节点1")
    node_b, _ = _register_node(client, _b_admin(), "B院节点1")
    assert node_a != node_b

    # A 只见本院节点
    nodes_a = client.get("/api/federation/nodes", headers=_a_admin()).json()
    assert {n["id"] for n in nodes_a} == {node_a}
    assert all(n["hospital_id"] == 1 for n in nodes_a)
    # B 只见本院节点
    nodes_b = client.get("/api/federation/nodes", headers=_b_admin()).json()
    assert {n["id"] for n in nodes_b} == {node_b}
    # operator 全局可见
    nodes_op = client.get("/api/federation/nodes", headers=_operator()).json()
    assert {n["id"] for n in nodes_op} == {node_a, node_b}


def test_operator_registers_node_with_explicit_tenant(strict, client):
    # operator 指定租户 → 绑定；不指定 → 平台直管（None）
    n1, _ = _register_node(client, _operator(), "绑定节点", hospital_id=1)
    n2, _ = _register_node(client, _operator(), "平台直管节点")
    nodes = {n["id"]: n for n in client.get("/api/federation/nodes",
                                            headers=_operator()).json()}
    assert nodes[n1]["hospital_id"] == 1
    assert nodes[n2]["hospital_id"] is None
    # 指定不存在的租户 → 400
    r = client.post("/api/federation/nodes", json={"name": "X", "hospital_id": 999},
                    headers=_operator())
    assert r.status_code == 400
    # A 管理员看不到平台直管节点
    ids_a = {n["id"] for n in client.get("/api/federation/nodes",
                                         headers=_a_admin()).json()}
    assert ids_a == {n1}


# ------------------------------------------------------------------
# 任务隔离：创建归属 + 跨租户全拒（验收核心）
# ------------------------------------------------------------------

def test_tenant_a_cannot_access_tenant_b_resources(strict, client):
    """验收口径：租户 A 无法访问租户 B 任何资源。"""
    node_a, _ = _register_node(client, _a_admin(), "A1")
    node_a2, _ = _register_node(client, _a_admin(), "A2")
    node_b, _ = _register_node(client, _b_admin(), "B1")

    job_a = _create_job(client, _a_admin(), [node_a, node_a2])
    assert job_a["hospital_id"] == 1

    # B 查详情/列表/暂停/恢复 → 404（隐藏存在性）
    assert client.get(f"/api/federation/nodes/jobs/{job_a['id']}",
                      headers=_b_admin()).status_code == 404
    assert client.get("/api/federation/nodes/jobs", headers=_b_admin()).json() == []
    assert client.post(f"/api/federation/nodes/jobs/{job_a['id']}/pause",
                       headers=_b_admin()).status_code == 404
    assert client.post(f"/api/federation/nodes/jobs/{job_a['id']}/resume",
                       headers=_b_admin()).status_code == 404

    # B 建任务指派 A 的节点 → 403（跨租户写拒绝）
    r = client.post("/api/federation/nodes/jobs",
                    json={"node_ids": [node_b, node_a], "rounds": 1},
                    headers=_b_admin())
    assert r.status_code == 403

    # A 详情正常；operator 全局可见
    assert client.get(f"/api/federation/nodes/jobs/{job_a['id']}",
                      headers=_a_admin()).json()["id"] == job_a["id"]
    jobs_op = client.get("/api/federation/nodes/jobs", headers=_operator()).json()
    assert [j["id"] for j in jobs_op] == [job_a["id"]]


def test_unbound_hospital_admin_rejected_everywhere(strict, client):
    """hospital_admin 未绑定租户 → 租户端点一律 403（不落全局视图）。"""
    unbound = _user_headers(4, "hospital_admin")
    assert client.get("/api/federation/nodes", headers=unbound).status_code == 403
    assert client.get("/api/federation/nodes/jobs", headers=unbound).status_code == 403
    assert client.get("/api/admin/users", headers=unbound).status_code == 403
    assert client.get("/api/admin/tenants/usage", headers=unbound).status_code == 403
    assert client.get("/api/admin/hospitals", headers=unbound).status_code == 403


def test_admin_users_scoped_to_own_hospital(strict, client):
    # A 管理员仅见本院用户
    r = client.get("/api/admin/users", headers=_a_admin())
    assert r.status_code == 200
    body = r.json()
    assert body["scope_hospital_id"] == 1
    assert {u["id"] for u in body["users"]} == {1}  # 只有 A 院管理员本人
    # operator 全局
    r2 = client.get("/api/admin/users", headers=_operator())
    body2 = r2.json()
    assert body2["scope_hospital_id"] is None
    assert {u["id"] for u in body2["users"]} == {1, 2, 3, 4}


def test_role_assignment_binds_hospital_tenant(strict, client):
    """超管设角色时可同时绑定租户；绑定不存在的租户 → 400。"""
    r = client.put("/api/admin/users/4/role",
                   json={"role": "hospital_admin", "hospital_id": 2},
                   headers=_admin_headers())
    assert r.status_code == 200 and r.json()["hospital_id"] == 2
    r2 = client.put("/api/admin/users/3/role",
                    json={"role": "hospital_admin", "hospital_id": 999},
                    headers=_admin_headers())
    assert r2.status_code == 400


# ------------------------------------------------------------------
# 计费计量：记录点 + 报表精确一致（验收核心）
# ------------------------------------------------------------------

def test_usage_metering_report_accurate(strict, client):
    """1 个任务（rounds=1）+ 2 节点全流程：federated_jobs=1、node_updates=4
    （每节点 1 次 moments + 1 次 update）。"""
    node_a, token_a = _register_node(client, _a_admin(), "A1")
    node_a2, token_a2 = _register_node(client, _a_admin(), "A2")
    job = _create_job(client, _a_admin(), [node_a, node_a2], rounds=1)

    key = agent_crypto.parse_key(KEY)
    moments = agent_crypto.seal_envelope(
        {"n": 100, "sum": [0.0] * 13, "sum_sq": [0.0] * 13}, key)
    for nid, tok in ((node_a, token_a), (node_a2, token_a2)):
        r = client.post(f"/api/federation/nodes/jobs/{job['id']}/moments",
                        json={"payload": moments}, headers=_node_headers(nid, tok))
        assert r.status_code == 200, r.text
    update = agent_crypto.seal_envelope(
        {"coef": [0.0] * 14, "n": 100, "local_auc": 0.7}, key)
    for nid, tok in ((node_a, token_a), (node_a2, token_a2)):
        r = client.post(f"/api/federation/nodes/jobs/{job['id']}/rounds/1/update",
                        json={"payload": update}, headers=_node_headers(nid, tok))
        assert r.status_code == 200, r.text

    # operator 报表：数字与写入点精确一致
    report = client.get("/api/admin/tenants/usage", headers=_operator()).json()
    assert report["totals"] == {"federated_jobs": 1, "node_updates": 4}
    rows = report["rows"]
    assert all(r["hospital_id"] == 1 for r in rows)

    # hospital_admin 报表强制本院：B 看到的是自己的空报表，参数传 A 也被忽略
    report_b = client.get("/api/admin/tenants/usage?hospital_id=1",
                          headers=_b_admin()).json()
    assert report_b["scope_hospital_id"] == 2 and report_b["rows"] == []

    # operator 按租户过滤
    report_a = client.get("/api/admin/tenants/usage?hospital_id=1",
                          headers=_operator()).json()
    assert report_a["scope_hospital_id"] == 1
    assert report_a["totals"] == {"federated_jobs": 1, "node_updates": 4}


def test_metering_skipped_for_platform_scope(strict, client):
    """operator 建任务/平台直管节点上行：hospital_id=None → 不计租户计量。"""
    n1, tok1 = _register_node(client, _operator(), "P1")
    n2, tok2 = _register_node(client, _operator(), "P2")
    job = _create_job(client, _operator(), [n1, n2], rounds=1)
    assert job["hospital_id"] is None

    key = agent_crypto.parse_key(KEY)
    moments = agent_crypto.seal_envelope(
        {"n": 50, "sum": [0.0] * 13, "sum_sq": [0.0] * 13}, key)
    for nid, tok in ((n1, tok1), (n2, tok2)):
        assert client.post(f"/api/federation/nodes/jobs/{job['id']}/moments",
                           json={"payload": moments},
                           headers=_node_headers(nid, tok)).status_code == 200

    report = client.get("/api/admin/tenants/usage", headers=_operator()).json()
    assert report["rows"] == [] and report["totals"] == {}
