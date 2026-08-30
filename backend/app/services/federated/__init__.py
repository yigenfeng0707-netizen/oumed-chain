"""联邦学习领域服务：数据集缓存 + 任务执行 + 标准基准实验。"""

from __future__ import annotations

import threading

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from app.services.federated.data_generator import FEATURES, HOSPITALS, TARGET, generate_all
from app.services.federated.engine import FedScaler, local_model_auc, run_federated

# 全局数据集缓存（进程内一次生成，固定种子保证演示可复现）
_DATASETS: dict | None = None
_SPLIT: dict | None = None
_SCALER = None
_CLIENTS: list[dict] | None = None
_X_TEST = None
_Y_TEST = None
_BENCHMARK_CACHE: dict | None = None
_BENCHMARK_LOCK = threading.Lock()


def _build_split(data: dict[str, pd.DataFrame]):
    rng = np.random.default_rng(42)
    train, test = {}, {}
    for site, df in data.items():
        idx = rng.permutation(len(df))
        cut = int(len(df) * 0.75)
        train[site], test[site] = df.iloc[idx[:cut]], df.iloc[idx[cut:]]

    test_df = pd.concat([
        test["A_三甲医院"].iloc[:1400],
        test["B_县人民医院"].iloc[:800],
        test["C_社区卫生中心"].iloc[:400],
    ])
    X_test_raw = test_df[FEATURES].to_numpy(float)
    y_test = test_df[TARGET].to_numpy(float)

    moments = []
    for site, tr in train.items():
        X = tr[FEATURES].to_numpy(float)
        moments.append((len(X), X.sum(0), (X**2).sum(0)))
    scaler = FedScaler().fit(moments)

    clients = []
    for site, tr in train.items():
        X = scaler.transform(tr[FEATURES].to_numpy(float))
        clients.append({"X": X, "y": tr[TARGET].to_numpy(float), "n": len(X), "site": site})

    return train, test, scaler.transform(X_test_raw), y_test, clients, scaler


def _ensure_data():
    global _DATASETS, _SPLIT, _SCALER, _CLIENTS, _X_TEST, _Y_TEST
    if _DATASETS is None:
        _DATASETS = generate_all()
        train, test, X_test, y_test, clients, scaler = _build_split(_DATASETS)
        _SPLIT = {"train": train, "test": test}
        _X_TEST, _Y_TEST, _CLIENTS, _SCALER = X_test, y_test, clients, scaler


def get_overview() -> dict:
    """三家医院数据全景（联邦统计口径，不含任何个体记录）。"""
    _ensure_data()
    hospitals = []
    for site, df in _DATASETS.items():
        tr, te = _SPLIT["train"][site], _SPLIT["test"][site]
        hospitals.append({
            "site": site,
            "total": int(len(df)),
            "train": int(len(tr)),
            "test": int(len(te)),
            "prevalence": round(float(df[TARGET].mean()), 4),
            "mean_age": round(float(df["age"].mean()), 1),
            "missing_ef": HOSPITALS[site]["missing_ef"],
        })
    return {
        "task": "心衰 30 天再入院风险预测",
        "features": FEATURES,
        "n_features": len(FEATURES),
        "hospitals": hospitals,
        "global_test_n": int(len(_Y_TEST)),
    }


def run_job(rounds: int = 12, local_epochs: int = 3,
            dp_sigma: float = 0.0, clip_norm: float | None = None,
            seed: int = 7) -> dict:
    """执行一次联邦训练任务（同步，秒级）。返回 AUC 曲线、最终 AUC 与逐院公平性对比。"""
    _ensure_data()
    if clip_norm is None:
        clip_norm = 0.2 if dp_sigma > 0 else 1.0

    history, final_auc, fed_model = run_federated(
        _CLIENTS, _X_TEST, _Y_TEST, rounds=rounds, local_epochs=local_epochs,
        clip_norm=clip_norm, sigma=dp_sigma, seed=seed)

    # 联邦模型在三家医院各自人群上的公平性表现（同一全局模型 vs 各院本地模型）
    per_site = {}
    for site in _SPLIT["test"]:
        Xs = _SCALER.transform(_SPLIT["test"][site][FEATURES].to_numpy(float))
        ys = _SPLIT["test"][site][TARGET].to_numpy(float)
        local_site_auc = local_model_auc(
            _SCALER.transform(_SPLIT["train"][site][FEATURES].to_numpy(float)),
            _SPLIT["train"][site][TARGET].to_numpy(float), Xs, ys)
        per_site[site] = {
            "local": round(local_site_auc, 4),
            "federated": round(float(roc_auc_score(ys, fed_model.predict_proba(Xs))), 4),
        }

    return {
        "rounds": rounds,
        "local_epochs": local_epochs,
        "dp_sigma": dp_sigma,
        "clip_norm": clip_norm,
        "auc_curve": [round(a, 4) for a in history],
        "final_auc": round(final_auc, 4),
        "per_site": per_site,
    }


def get_benchmark(force: bool = False) -> dict:
    """标准基准实验：本地 vs 联邦 vs DP 分档 vs 集中上界（结果缓存）。"""
    global _BENCHMARK_CACHE
    if _BENCHMARK_CACHE is not None and not force:
        return _BENCHMARK_CACHE
    with _BENCHMARK_LOCK:
        if _BENCHMARK_CACHE is not None and not force:
            return _BENCHMARK_CACHE
        _ensure_data()
        report: dict = {"local_auc": {}, "per_site": {}, "dp": {}}

        for c in _CLIENTS:
            report["local_auc"][c["site"]] = round(
                local_model_auc(c["X"], c["y"], _X_TEST, _Y_TEST), 4)

        job = run_job(rounds=12, local_epochs=3, dp_sigma=0.0)
        report["fed_auc"] = job["final_auc"]
        report["fed_curve"] = job["auc_curve"]
        report["per_site"] = job["per_site"]

        for sigma, label in ((0.01, "轻噪声"), (0.03, "中噪声"), (0.08, "强噪声")):
            r = run_job(rounds=8, local_epochs=6, dp_sigma=sigma)
            report["dp"][str(sigma)] = {"auc": r["final_auc"], "label": label}

        X_pooled = np.vstack([c["X"] for c in _CLIENTS])
        y_pooled = np.concatenate([c["y"] for c in _CLIENTS])
        report["pooled_oracle_auc"] = round(
            local_model_auc(X_pooled, y_pooled, _X_TEST, _Y_TEST, epochs=800), 4)
        report["dataset_info"] = get_overview()
        _BENCHMARK_CACHE = report
        return report
