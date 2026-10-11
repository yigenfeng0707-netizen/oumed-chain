"""P1.3 断点续训与容错测试：

- 恶意参数方向检测：翻转/投毒参数 cosine 低于阈值 → 422 拒绝、不落库、不参与聚合，
  拒绝后合法节点重新提交任务仍可正常完成；阈值可通过 FEDERATION_COS_THRESHOLD 调节；
- 任务暂停/恢复：pause→paused（assignment 可拉取、参数上行 409）→resume 后从
  current_round 断点续训，最终 final_auc 与不间断运行完全一致（核心验收）；
- 状态守卫：done 任务不可 pause、非 paused 任务不可 resume；
- 节点存活可见性：节点面认证请求刷新 last_seen_at，管理端列表/任务详情 progress
  可见各节点提交进度与最后活跃时间（拔线掉线可发现）。

复用 P1.1 的 client/strict 夹具与真实训练驱动器（test_federation_nodes）。
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "node_agent"))

from app.config import settings  # noqa: E402
from node_agent import crypto as agent_crypto  # noqa: E402
from node_agent import trainer as agent_trainer  # noqa: E402
from node_agent.synthdata import write_csv  # noqa: E402
from test_federation_nodes import (_admin_headers, _create_job,  # noqa: E402
                                   _local_train_update, _node_headers,
                                   _register_node, _run_nodes_interleaved,
                                   client, strict)

TEST_KEY = "Zx9vQ2FyZVRlYW1LZXlOb3RTb0xvbmdUcnVzdGVkMQ=="  # 32 字节 base64 测试密钥


def _seed(node_id: str) -> int:
    return sum(ord(c) for c in node_id) % 1000


def _pull(client, node_id: str, token: str, job_id: str) -> dict:
    r = client.get(f"/api/federation/nodes/jobs/{job_id}/assignment",
                   headers=_node_headers(node_id, token))
    assert r.status_code == 200, r.text
    return r.json()


def _submit_update(client, node_id: str, token: str, job_id: str, key: bytes,
                   asg: dict, csv_path: str):
    upd = _local_train_update(csv_path, asg, seed=_seed(node_id))
    payload = agent_crypto.seal_envelope(upd, key)
    return client.post(f"/api/federation/nodes/jobs/{job_id}/rounds/{asg['round']}/update",
                       json={"payload": payload}, headers=_node_headers(node_id, token))


def _both_moments(client, nodes, job_id: str, key: bytes):
    """两节点各交一份联邦统计（真实合成数据），任务进入 running 第 1 轮。"""
    for nid, tok, csv_path in nodes:
        payload = agent_crypto.seal_envelope(agent_trainer.moments(agent_trainer.load_csv(csv_path)[0]), key)
        r = client.post(f"/api/federation/nodes/jobs/{job_id}/moments",
                        json={"payload": payload}, headers=_node_headers(nid, tok))
        assert r.status_code == 200, r.text


# ------------------------------------------------------------------
# 恶意参数方向检测
# ------------------------------------------------------------------

def test_tampered_update_rejected_and_not_persisted(strict, client, tmp_path):
    """验收：注入篡改参数被拒——翻转全局参数方向 cosine≈-1 → 422，
    恶意记录不落库（progress 无推进）、不参与聚合；合法节点随后可完成任务。"""
    csv_a = str(tmp_path / "a.csv")
    csv_b = str(tmp_path / "b.csv")
    write_csv(csv_a, "hospital_a", seed=11)
    write_csv(csv_b, "hospital_b", seed=22)
    node_a, token_a = _register_node(client, "A院")
    node_b, token_b = _register_node(client, "B院")
    job_id = _create_job(client, [node_a, node_b], rounds=3)
    key = agent_crypto.parse_key(TEST_KEY)

    _both_moments(client, [(node_a, token_a, csv_a), (node_b, token_b, csv_b)], job_id, key)

    # 第 1 轮双方合法提交 → 进入第 2 轮（global_coef 非零，方向检测生效）
    for nid, tok, csv_path in ((node_a, token_a, csv_a), (node_b, token_b, csv_b)):
        asg = _pull(client, nid, tok, job_id)
        assert asg["status"] == "running"
        r = _submit_update(client, nid, tok, job_id, key, asg, csv_path)
        assert r.status_code == 200, r.text
    asg2 = _pull(client, node_a, token_a, job_id)
    assert asg2["round"] == 2

    # 注入篡改参数：直接翻转全局模型（投毒方向反向）
    evil_coef = [-c for c in asg2["global_coef"]]
    payload = agent_crypto.seal_envelope({"coef": evil_coef, "n": 100, "local_auc": 0.9}, key)
    r = client.post(f"/api/federation/nodes/jobs/{job_id}/rounds/2/update",
                    json={"payload": payload}, headers=_node_headers(node_a, token_a))
    assert r.status_code == 422 and "cosine" in r.json()["detail"]

    # 恶意参数未落库/未参与聚合：B 提交后任务仍停在等待 A（轮次不推进）
    asg_b = _pull(client, node_b, token_b, job_id)
    assert _submit_update(client, node_b, token_b, job_id, key, asg_b, csv_b).status_code == 200
    detail = client.get(f"/api/federation/nodes/jobs/{job_id}", headers=_admin_headers()).json()
    assert detail["status"] == "running" and detail["round"] == 2
    prog = {p["node_id"]: p for p in detail["progress"]}
    assert prog[node_a]["last_update_round"] == 1  # 恶意第 2 轮提交未留痕

    # A 重新提交合法参数 → 任务照常推进完成（合法节点不受恶意节点连坐）
    asg2b = _pull(client, node_a, token_a, job_id)
    assert _submit_update(client, node_a, token_a, job_id, key, asg2b, csv_a).status_code == 200
    for _ in range(50):
        asg = _pull(client, node_a, token_a, job_id)
        if asg["status"] == "done":
            break
        assert _submit_update(client, node_a, token_a, job_id, key, asg, csv_a).status_code == 200
        assert _submit_update(client, node_b, token_b, job_id, key,
                              _pull(client, node_b, token_b, job_id), csv_b).status_code == 200
    else:
        raise AssertionError("任务未在限定步数内完成")
    detail = client.get(f"/api/federation/nodes/jobs/{job_id}", headers=_admin_headers()).json()
    assert detail["status"] == "done" and 0.5 < detail["result"]["final_auc"] < 1.0


def test_cosine_threshold_configurable(strict, client, monkeypatch, tmp_path):
    """阈值调高（0.9）：与全局模型正交（cos=0）的参数也被拒；首轮零模型跳过检测。"""
    monkeypatch.setattr(settings, "FEDERATION_COS_THRESHOLD", 0.9)
    csv_a = str(tmp_path / "a.csv")
    csv_b = str(tmp_path / "b.csv")
    write_csv(csv_a, "hospital_a", seed=33)
    write_csv(csv_b, "hospital_b", seed=44)
    node_a, token_a = _register_node(client, "A")
    node_b, token_b = _register_node(client, "B")
    job_id = _create_job(client, [node_a, node_b], rounds=2)
    key = agent_crypto.parse_key(TEST_KEY)

    _both_moments(client, [(node_a, token_a, csv_a), (node_b, token_b, csv_b)], job_id, key)

    # 第 1 轮：A 合法提交（首轮 global 为零向量，方向无意义，放行）
    asg1 = _pull(client, node_a, token_a, job_id)
    assert _submit_update(client, node_a, token_a, job_id, key, asg1, csv_a).status_code == 200
    # B 提交后进入第 2 轮
    asg1b = _pull(client, node_b, token_b, job_id)
    assert _submit_update(client, node_b, token_b, job_id, key, asg1b, csv_b).status_code == 200
    asg2 = _pull(client, node_a, token_a, job_id)
    assert asg2["round"] == 2

    # 构造与全局模型正交的向量（dot=0 → cos=0 < 0.9）→ 422
    g = np.asarray(asg2["global_coef"], dtype=float)
    ortho = np.zeros_like(g)
    ortho[0], ortho[1] = g[1], -g[0]  # dot(g, ortho) = g0*g1 - g1*g0 = 0
    payload = agent_crypto.seal_envelope({"coef": ortho.tolist(), "n": 50, "local_auc": 0.8}, key)
    r = client.post(f"/api/federation/nodes/jobs/{job_id}/rounds/2/update",
                    json={"payload": payload}, headers=_node_headers(node_a, token_a))
    assert r.status_code == 422

    # 阈值关闭（负数）：同一参数放行（方向检测关闭语义）
    monkeypatch.setattr(settings, "FEDERATION_COS_THRESHOLD", -1.0)
    assert client.post(f"/api/federation/nodes/jobs/{job_id}/rounds/2/update",
                       json={"payload": payload}, headers=_node_headers(node_a, token_a)
                       ).status_code == 200


# ------------------------------------------------------------------
# 任务暂停 / 恢复（断点续训）
# ------------------------------------------------------------------

def test_pause_resume_final_auc_matches_uninterrupted(strict, client, tmp_path):
    """核心验收：任务在节点掉线期间暂停→恢复→补齐提交，final_auc 与
    不间断运行完全一致（聚合确定性：只依赖已提交参数）。"""
    csv_a = str(tmp_path / "a.csv")
    csv_b = str(tmp_path / "b.csv")
    write_csv(csv_a, "hospital_a", seed=2026)
    write_csv(csv_b, "hospital_b", seed=777)
    node_a, token_a = _register_node(client, "三甲医院")
    node_b, token_b = _register_node(client, "县医院")
    key = agent_crypto.parse_key(TEST_KEY)
    nodes = [(node_a, token_a, csv_a), (node_b, token_b, csv_b)]

    # 参照任务：不间断跑完
    ref_job = _create_job(client, [node_a, node_b], rounds=2)
    _run_nodes_interleaved(client, nodes, ref_job, key)
    ref_auc = client.get(f"/api/federation/nodes/jobs/{ref_job}",
                         headers=_admin_headers()).json()["result"]["final_auc"]

    # 中断任务：A 交完第 1 轮后暂停（模拟 B 拔线 30 分钟）
    outage_job = _create_job(client, [node_a, node_b], rounds=2)
    _both_moments(client, nodes, outage_job, key)
    asg_a = _pull(client, node_a, token_a, outage_job)
    assert _submit_update(client, node_a, token_a, outage_job, key, asg_a, csv_a).status_code == 200

    r = client.post(f"/api/federation/nodes/jobs/{outage_job}/pause", headers=_admin_headers())
    assert r.status_code == 200 and r.json()["status"] == "paused"

    # 暂停期间：assignment 可拉取（Agent 轮询等待语义），参数上行 409 拒绝
    asg_b = _pull(client, node_b, token_b, outage_job)
    assert asg_b["status"] == "paused" and asg_b["round"] == 1
    junk = agent_crypto.seal_envelope({"coef": [0.0] * 14, "n": 10, "local_auc": 0.5}, key)
    r = client.post(f"/api/federation/nodes/jobs/{outage_job}/rounds/1/update",
                    json={"payload": junk}, headers=_node_headers(node_b, token_b))
    assert r.status_code == 409

    # 恢复 → 从第 1 轮断点续训 → B 补提交 → 驱动到 done
    r = client.post(f"/api/federation/nodes/jobs/{outage_job}/resume", headers=_admin_headers())
    assert r.status_code == 200 and r.json()["status"] == "running"
    assert r.json()["round"] == 1

    asg_b = _pull(client, node_b, token_b, outage_job)
    assert asg_b["status"] == "running" and asg_b["round"] == 1
    # 全局模型暂停前后一致（断点状态保留）
    assert asg_b["global_coef"] == asg_a["global_coef"]
    assert _submit_update(client, node_b, token_b, outage_job, key, asg_b, csv_b).status_code == 200
    for _ in range(50):
        asg = _pull(client, node_a, token_a, outage_job)
        if asg["status"] == "done":
            break
        assert _submit_update(client, node_a, token_a, outage_job, key, asg, csv_a).status_code == 200
        nxt = _pull(client, node_b, token_b, outage_job)
        if nxt["status"] == "running":
            assert _submit_update(client, node_b, token_b, outage_job, key, nxt, csv_b).status_code == 200
    else:
        raise AssertionError("任务未在限定步数内完成")

    detail = client.get(f"/api/federation/nodes/jobs/{outage_job}", headers=_admin_headers()).json()
    assert detail["status"] == "done"
    assert detail["result"]["final_auc"] == ref_auc  # 结果一致性（核心验收）
    assert detail["prev_hash"] and detail["event_hash"]


def test_pause_resume_in_collecting_phase(strict, client, tmp_path):
    """collecting 阶段暂停→恢复：回到统计收集阶段（round=0），统计可继续提交。"""
    csv_a = str(tmp_path / "a.csv")
    csv_b = str(tmp_path / "b.csv")
    write_csv(csv_a, "hospital_a", seed=55)
    write_csv(csv_b, "hospital_b", seed=66)
    node_a, token_a = _register_node(client, "A")
    node_b, token_b = _register_node(client, "B")
    job_id = _create_job(client, [node_a, node_b], rounds=1)
    key = agent_crypto.parse_key(TEST_KEY)

    payload_a = agent_crypto.seal_envelope(agent_trainer.moments(agent_trainer.load_csv(csv_a)[0]), key)
    assert client.post(f"/api/federation/nodes/jobs/{job_id}/moments",
                       json={"payload": payload_a}, headers=_node_headers(node_a, token_a)).status_code == 200

    assert client.post(f"/api/federation/nodes/jobs/{job_id}/pause", headers=_admin_headers()).status_code == 200
    asg = _pull(client, node_b, token_b, job_id)
    assert asg["status"] == "paused"
    # 暂停期间统计上行也 409
    payload_b = agent_crypto.seal_envelope(agent_trainer.moments(agent_trainer.load_csv(csv_b)[0]), key)
    assert client.post(f"/api/federation/nodes/jobs/{job_id}/moments",
                       json={"payload": payload_b}, headers=_node_headers(node_b, token_b)).status_code == 409

    r = client.post(f"/api/federation/nodes/jobs/{job_id}/resume", headers=_admin_headers())
    assert r.status_code == 200 and r.json() == {"status": "collecting", "round": 0,
                                                 "detail": "任务已恢复，节点将从当前轮断点续训"}
    assert client.post(f"/api/federation/nodes/jobs/{job_id}/moments",
                       json={"payload": payload_b}, headers=_node_headers(node_b, token_b)).status_code == 200
    assert client.get(f"/api/federation/nodes/jobs/{job_id}",
                      headers=_admin_headers()).json()["status"] == "running"


def test_pause_resume_state_guards(strict, client, tmp_path):
    """状态守卫：done 不可暂停；未暂停不可恢复；重复暂停/恢复 409。"""
    csv_a = str(tmp_path / "a.csv")
    csv_b = str(tmp_path / "b.csv")
    write_csv(csv_a, "hospital_a", seed=1)
    write_csv(csv_b, "hospital_b", seed=2)
    node_a, token_a = _register_node(client, "A")
    node_b, token_b = _register_node(client, "B")
    job_id = _create_job(client, [node_a, node_b], rounds=1)
    _run_nodes_interleaved(client, [(node_a, token_a, csv_a), (node_b, token_b, csv_b)],
                           job_id, agent_crypto.parse_key(TEST_KEY))

    assert client.post(f"/api/federation/nodes/jobs/{job_id}/pause",
                       headers=_admin_headers()).status_code == 409
    assert client.post(f"/api/federation/nodes/jobs/{job_id}/resume",
                       headers=_admin_headers()).status_code == 409

    # 重复暂停 / 重复恢复
    job2 = _create_job(client, [node_a, node_b], rounds=1)
    assert client.post(f"/api/federation/nodes/jobs/{job2}/pause", headers=_admin_headers()).status_code == 200
    assert client.post(f"/api/federation/nodes/jobs/{job2}/pause", headers=_admin_headers()).status_code == 409
    assert client.post(f"/api/federation/nodes/jobs/{job2}/resume", headers=_admin_headers()).status_code == 200
    assert client.post(f"/api/federation/nodes/jobs/{job2}/resume", headers=_admin_headers()).status_code == 409


# ------------------------------------------------------------------
# 节点存活可见性（last_seen_at + 任务进度）
# ------------------------------------------------------------------

def test_last_seen_refresh_and_job_progress(strict, client, tmp_path):
    """节点面认证请求刷新 last_seen_at；节点列表与任务详情 progress 可见
    各节点提交进度/最后活跃时间（掉线节点可定位）。"""
    csv_a = str(tmp_path / "a.csv")
    csv_b = str(tmp_path / "b.csv")
    write_csv(csv_a, "hospital_a", seed=3)
    write_csv(csv_b, "hospital_b", seed=4)
    node_a, token_a = _register_node(client, "上线医院")
    node_b, token_b = _register_node(client, "拔线医院")
    job_id = _create_job(client, [node_a, node_b], rounds=1)

    # A 请求过节点面（last_seen 刷新）；B 从未出现（模拟拔线）
    assert client.get("/api/federation/nodes/my-jobs",
                      headers=_node_headers(node_a, token_a)).status_code == 200
    nodes = {n["name"]: n for n in
             client.get("/api/federation/nodes", headers=_admin_headers()).json()}
    assert nodes["上线医院"]["last_seen_at"] is not None
    assert nodes["拔线医院"]["last_seen_at"] is None

    # 任务详情 progress：提交状态 + 最后活跃
    detail = client.get(f"/api/federation/nodes/jobs/{job_id}", headers=_admin_headers()).json()
    prog = {p["name"]: p for p in detail["progress"]}
    assert prog["上线医院"]["moments_submitted"] is False
    assert prog["上线医院"]["last_update_round"] is None
    assert prog["上线医院"]["last_seen_at"] is not None
    assert prog["拔线医院"]["last_seen_at"] is None

    # A 提交统计后 progress 推进
    payload = agent_crypto.seal_envelope(agent_trainer.moments(agent_trainer.load_csv(csv_a)[0]),
                                         agent_crypto.parse_key(TEST_KEY))
    assert client.post(f"/api/federation/nodes/jobs/{job_id}/moments",
                       json={"payload": payload}, headers=_node_headers(node_a, token_a)).status_code == 200
    detail = client.get(f"/api/federation/nodes/jobs/{job_id}", headers=_admin_headers()).json()
    prog = {p["name"]: p for p in detail["progress"]}
    assert prog["上线医院"]["moments_submitted"] is True
    assert prog["拔线医院"]["moments_submitted"] is False  # 卡住的就是它
