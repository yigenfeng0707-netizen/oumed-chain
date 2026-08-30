"""联邦学习引擎单元测试（瓯医数链核心，种子固定全确定性）。"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "services" / "federated"))

from data_generator import FEATURES, TARGET, HOSPITALS, generate_all  # noqa: E402
from engine import (  # noqa: E402
    FedScaler,
    LogisticModel,
    dp_sanitize,
    fed_average,
    local_model_auc,
    run_federated,
)


@pytest.fixture(scope="module")
def setup():
    data = generate_all()
    import pandas as pd

    rng = np.random.default_rng(42)
    clients, scaler = [], None
    from sklearn.metrics import roc_auc_score

    moments = []
    for site, df in data.items():
        tr = df.iloc[: int(len(df) * 0.75)]  # 与生产逻辑一致：矩信息取自训练切片
        X = tr[FEATURES].to_numpy(float)
        moments.append((len(X), X.sum(0), (X**2).sum(0)))
    scaler = FedScaler().fit(moments)

    test_df = pd.concat([data["A_三甲医院"].iloc[:1400], data["B_县人民医院"].iloc[:800]])
    X_test = scaler.transform(test_df[FEATURES].to_numpy(float))
    y_test = test_df[TARGET].to_numpy(float)

    for site, df in data.items():
        cut = int(len(df) * 0.75)
        tr = df.iloc[:cut]
        clients.append({
            "X": scaler.transform(tr[FEATURES].to_numpy(float)),
            "y": tr[TARGET].to_numpy(float),
            "n": len(tr),
            "site": site,
        })
    return clients, X_test, y_test, scaler, data


def test_data_quality(setup):
    _, _, _, _, data = setup
    for site, df in data.items():
        assert len(df) == HOSPITALS[site]["n"]
        assert df[FEATURES].isna().sum().sum() == 0, "缺失值必须已被填充"
        assert 0.10 < df[TARGET].mean() < 0.35, "阳性率应贴近真实流行病学"
        assert set(df[TARGET].unique()) <= {0, 1}


def test_scaler_zero_center(setup):
    clients, _, _, scaler, _ = setup
    pooled = np.vstack([c["X"] for c in clients])
    assert np.abs(pooled.mean(0)).max() < 1e-6
    assert np.allclose(pooled.std(0), 1.0, atol=0.05)


def test_fed_average_weighted(setup):
    s1, s2 = np.array([1.0, 2.0]), np.array([3.0, 6.0])
    avg = fed_average([s1, s2], [3, 1])
    assert np.allclose(avg, [1.5, 3.0])


def test_dp_noise_changes_update(setup):
    rng = np.random.default_rng(1)
    u = np.zeros(10)
    noisy = dp_sanitize(u, clip_norm=1.0, sigma=0.5, rng=rng)
    assert not np.allclose(u, noisy)
    big = np.ones(10) * 100
    clipped = dp_sanitize(big, clip_norm=1.0, sigma=0.0, rng=rng)
    assert np.linalg.norm(clipped) <= 1.0 + 1e-9


def test_federated_beats_all_locals(setup):
    clients, X_test, y_test, _, _ = setup
    _, fed_auc, _ = run_federated(clients, X_test, y_test, rounds=12, sigma=0.0)
    local_aucs = [local_model_auc(c["X"], c["y"], X_test, y_test) for c in clients]
    assert fed_auc >= max(local_aucs) - 0.005


def test_dp_monotonic_degradation(setup):
    clients, X_test, y_test, _, _ = setup
    _, auc_low, _ = run_federated(clients, X_test, y_test, rounds=8, local_epochs=6,
                                  clip_norm=0.2, sigma=0.01, seed=7)
    _, auc_high, _ = run_federated(clients, X_test, y_test, rounds=8, local_epochs=6,
                                   clip_norm=0.2, sigma=0.08, seed=7)
    assert auc_low > auc_high, "更强噪声应造成更大效用损失"


def test_model_load_roundtrip(setup):
    m = LogisticModel(5)
    m.coef = np.arange(6.0)
    m2 = LogisticModel(5)
    m2.load(m.state())
    assert np.allclose(m2.state(), m.coef)
