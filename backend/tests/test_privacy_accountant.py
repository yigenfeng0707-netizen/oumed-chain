"""P1.4 正式隐私预算核算测试：

- RDP 矩会计已知值对照：σ=1,T=1 → ε≈5.3；σ=8,T=1 → ε≈0.61（手工推导区间）；
- 单调性：噪声越大 ε 越小、轮数越多 ε 越大；
- 未启用 DP（σ=0）：ε=inf / 报告 enabled=False；
- 集成：真实联邦任务（dp_sigma=0.5）完成后 result.privacy 含形式化 (ε, δ)
  且与独立计算一致；无 DP 任务 privacy.enabled=False。

复用 P1.1 的 client/strict 夹具与真实训练驱动器（test_federation_nodes）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "node_agent"))

import math  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.privacy_accountant import (epsilon_from_rdp,  # noqa: E402
                                             privacy_report, rdp_gaussian)
from node_agent import crypto as agent_crypto  # noqa: E402
from test_federation_nodes import (_admin_headers, _create_job,  # noqa: E402
                                   _register_node, _run_nodes_interleaved,
                                   client, strict)

TEST_KEY = "Zx9vQ2FyZVRlYW1LZXlOb3RTb0xvbmdUcnVzdGVkMQ=="  # 32 字节 base64 测试密钥


# ------------------------------------------------------------------
# RDP 矩会计数学正确性
# ------------------------------------------------------------------

def test_rdp_gaussian_formula():
    """单次高斯机制 RDP = α/(2σ²)（基础公式对照）。"""
    assert rdp_gaussian(2.0, 1.0) == 1.0
    assert rdp_gaussian(4.0, 2.0) == 0.5
    assert rdp_gaussian(10.0, 5.0) == 0.2


def test_epsilon_known_values():
    """与手工推导对照（Mironov RDP→DP 转换）：
    ε(α) = T·α/(2σ²) + ln(1/δ)/(α-1)，最优 α* = 1 + sqrt(2·T·ln(1/δ)·σ²)/1…（对 T=1 闭式）。
    σ=1, T=1, δ=1e-5：α*≈5.8 → ε≈5.3；σ=8, T=1：α*≈39.3 → ε≈0.61。
    """
    eps1 = epsilon_from_rdp(1.0, 1, 1e-5)
    opt1 = min((a / 2.0 + math.log(1e5) / (a - 1), a)
               for a in (1 + 0.0005 * i for i in range(1, 40000)))[0]
    assert abs(eps1 - opt1) < 5e-3  # 与细网格独立搜索一致（凸最小值附近平坦）
    assert 5.0 < eps1 < 6.0

    eps8 = epsilon_from_rdp(8.0, 1, 1e-5)
    assert 0.5 < eps8 < 0.8  # 高噪声单轮 → 弱泄漏保证


def test_epsilon_monotonicity():
    """噪声越大 ε 越小；轮数越多 ε 越大；δ 放宽 ε 略降。"""
    assert epsilon_from_rdp(4.0, 1) < epsilon_from_rdp(2.0, 1)
    assert epsilon_from_rdp(2.0, 10) > epsilon_from_rdp(2.0, 5) > epsilon_from_rdp(2.0, 1)
    assert epsilon_from_rdp(1.0, 1, 1e-3) < epsilon_from_rdp(1.0, 1, 1e-5)
    assert math.isinf(epsilon_from_rdp(0.0, 10))
    assert math.isinf(epsilon_from_rdp(-1.0, 10))


def test_privacy_report_disabled():
    """σ=0：报告 enabled=False，ε/δ 不定义。"""
    r = privacy_report(0.0, 1.0, 12)
    assert r["enabled"] is False and r["epsilon"] is None and r["delta"] is None
    assert "未启用" in r["note"]


def test_privacy_report_shape():
    """启用 DP：报告含形式化 (ε, δ) 与机制/会计/视角口径字段，ε 与独立计算一致。"""
    r = privacy_report(sigma=0.5, clip_norm=0.2, rounds=12, delta=1e-5, local_epochs=3)
    assert r["enabled"] is True
    assert r["epsilon"] == round(epsilon_from_rdp(0.5, 12, 1e-5), 4)
    assert r["delta"] == 1e-5
    assert r["sigma"] == 0.5 and r["clip_norm"] == 0.2 and r["rounds"] == 12
    assert r["accountant"] == "RDP (Mironov 2017)"
    assert "per-node" in r["perspective"]
    assert r["local_epochs"] == 3


def test_privacy_report_uses_settings_delta(strict, monkeypatch):
    """δ 来自 DP_ACCOUNTANT_DELTA 配置（报告口径跟随设置）。"""
    monkeypatch.setattr(settings, "DP_ACCOUNTANT_DELTA", 1e-4)
    r = privacy_report(0.5, 1.0, 2, delta=settings.DP_ACCOUNTANT_DELTA)
    assert r["delta"] == 1e-4
    assert r["epsilon"] == round(epsilon_from_rdp(0.5, 2, 1e-4), 4)


# ------------------------------------------------------------------
# 集成：真实联邦任务报告携带隐私保证
# ------------------------------------------------------------------

def test_done_job_result_contains_privacy_report(strict, client, tmp_path):
    """dp_sigma=0.5 任务完成后 result.privacy 含 (ε, δ)，与独立计算一致。"""
    from node_agent.synthdata import write_csv

    csv_a = str(tmp_path / "a.csv")
    csv_b = str(tmp_path / "b.csv")
    write_csv(csv_a, "hospital_a", seed=88)
    write_csv(csv_b, "hospital_b", seed=99)
    node_a, token_a = _register_node(client, "A")
    node_b, token_b = _register_node(client, "B")
    job_id = _create_job(client, [node_a, node_b], rounds=2, dp_sigma=0.5)
    _run_nodes_interleaved(client, [(node_a, token_a, csv_a), (node_b, token_b, csv_b)],
                           job_id, agent_crypto.parse_key(TEST_KEY))

    result = client.get(f"/api/federation/nodes/jobs/{job_id}",
                        headers=_admin_headers()).json()["result"]
    assert result["privacy"]["enabled"] is True
    assert result["privacy"]["delta"] == settings.DP_ACCOUNTANT_DELTA
    assert result["privacy"]["sigma"] == 0.5
    assert result["privacy"]["epsilon"] == round(
        epsilon_from_rdp(0.5, 2, settings.DP_ACCOUNTANT_DELTA), 4)
    assert 0 < result["privacy"]["epsilon"] < 100


def test_done_job_without_dp_marks_privacy_disabled(strict, client, tmp_path):
    """dp_sigma=0 任务：result.privacy.enabled=False（无隐私保证口径）。"""
    from node_agent.synthdata import write_csv

    csv_a = str(tmp_path / "a.csv")
    csv_b = str(tmp_path / "b.csv")
    write_csv(csv_a, "hospital_a", seed=7)
    write_csv(csv_b, "hospital_b", seed=8)
    node_a, token_a = _register_node(client, "A")
    node_b, token_b = _register_node(client, "B")
    job_id = _create_job(client, [node_a, node_b], rounds=1, dp_sigma=0.0)
    _run_nodes_interleaved(client, [(node_a, token_a, csv_a), (node_b, token_b, csv_b)],
                           job_id, agent_crypto.parse_key(TEST_KEY))

    result = client.get(f"/api/federation/nodes/jobs/{job_id}",
                        headers=_admin_headers()).json()["result"]
    assert result["privacy"]["enabled"] is False
