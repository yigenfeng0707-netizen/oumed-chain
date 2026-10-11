"""联邦学习差分隐私预算核算（P1.4 正式 (ε, δ) 会计，零第三方依赖）。

口径与依据：
- 机制：客户端 DP-FedAvg——节点本地更新先 L2 裁剪到 clip_norm（敏感度上界 Δ=clip_norm），
  再逐坐标加高斯噪声 N(0, (sigma·clip_norm)²)（node_agent.model.dp_sanitize 与平台
  engine.dp_sanitize 同构实现）→ 相对敏感度的噪声乘数 σ_m = sigma，即标准高斯机制；
- 会计：Rényi 差分隐私（RDP，Mironov 2017）矩会计——高斯机制每轮
  RDP_α = α/(2σ_m²)（全量参与，采样率 q=1）；T 轮训练线性累积；
- 转换：任意 (α, ε_α)-RDP → (ε, δ)-DP：ε = min_{α>1} [ε_α + ln(1/δ)/(α-1)]，
  α 在对数网格上搜索取最优；
- 视角：任务级 per-node 中心 DP——每个节点数据在本任务内被 T 次高斯机制访问，
  报告的 (ε, δ) 为单节点视角的最坏保证（节点间数据互不共享，无需合并）。

σ_m = 0（未启用 DP）时输出 enabled=False，ε 不定义。
"""

from __future__ import annotations

import math

import numpy as np

# RDP→DP 转换的 α 搜索网格：严格 (1, 1e6]，近 1 段加密（小 σ 最优 α 靠近 1），
# 主段对数均匀 800 点（凸目标最小值附近平坦，该密度足够 ≤1e-3 精度）
_ALPHA_GRID = np.concatenate([
    1 + np.logspace(-8, -1, 40),   # α ∈ (1, 1.1]
    np.logspace(0.01, 6, 800),     # α ∈ [1.023, 1e6]（不含 1，防除零）
])


def rdp_gaussian(alpha: float, sigma: float) -> float:
    """高斯机制单次释放的 RDP：α/(2σ_m²)（σ_m 为相对 L2 敏感度的噪声乘数）。"""
    return alpha / (2.0 * sigma * sigma)


def epsilon_from_rdp(sigma: float, rounds: int, delta: float = 1e-5) -> float:
    """T 轮全量参与高斯机制的 (ε, δ)-DP 保证（RDP 转换，α 取网格最优）。

    σ_m ≤ 0 视为未启用 DP，返回 float("inf")。
    """
    if sigma <= 0 or rounds <= 0:
        return float("inf")
    log_term = math.log(1.0 / delta)
    best = min(rounds * rdp_gaussian(a, sigma) + log_term / (a - 1.0)
               for a in _ALPHA_GRID)
    return float(best)


def privacy_report(sigma: float, clip_norm: float, rounds: int,
                   delta: float = 1e-5, local_epochs: int | None = None) -> dict:
    """任务报告用的隐私保证字段：机制参数 + 形式化 (ε, δ) + 口径说明。"""
    if sigma <= 0:
        return {
            "enabled": False,
            "epsilon": None, "delta": None,
            "note": "未启用差分隐私（dp_sigma=0），参数上行不携带隐私保证",
        }
    eps = epsilon_from_rdp(sigma, rounds, delta)
    report = {
        "enabled": True,
        "epsilon": round(eps, 4),
        "delta": delta,
        "sigma": sigma,
        "clip_norm": clip_norm,
        "rounds": rounds,
        "accountant": "RDP (Mironov 2017)",
        "sampling": "q=1 全量参与",
        "perspective": "per-node 中心 DP（单节点视角，T 次高斯机制组合）",
        "note": "节点本地更新 L2 裁剪至 clip_norm 后加逐坐标高斯噪声，"
                "(ε, δ) 由 RDP 矩会计转换，任务内 per-node 最坏保证",
    }
    if local_epochs is not None:
        report["local_epochs"] = local_epochs
    return report
