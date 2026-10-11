"""节点本地训练器：CSV 数据加载 → 联邦统计 → 标准化 → 本地训练 → AUC 评估。

特征工程契约与平台 data_generator.FEATURES 严格一致（13 维），
节点 CSV 必须包含全部特征列 + 标签列 readmit_30d。
原始数据只在本模块内存中出现，任何函数都不返回行级数据。
"""

from __future__ import annotations

import csv

import numpy as np

from node_agent.model import LogisticModel

# 与平台 data_generator.FEATURES 保持一致（特征工程契约，勿单方修改）
FEATURES = [
    "age", "sex_male", "bmi", "systolic_bp", "hba1c", "creatinine",
    "ejection_fraction", "sodium", "hemoglobin", "prior_admissions",
    "comorbidity_count", "polypharmacy", "followup_planned",
]
TARGET = "readmit_30d"


def load_csv(path: str) -> tuple[np.ndarray, np.ndarray]:
    """加载本院 EHR CSV（UTF-8，含表头），返回 (X, y)。缺失列直接报错 fail-fast。"""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        missing = [c for c in (*FEATURES, TARGET) if c not in cols]
        if missing:
            raise ValueError(f"CSV 缺少特征列: {missing}（现有列: {cols}）")
        rows = [(
            [float(r[c]) for c in FEATURES],
            float(r[TARGET]),
        ) for r in reader]
    if not rows:
        raise ValueError("CSV 无数据行")
    X = np.array([r[0] for r in rows], dtype=float)
    y = np.array([r[1] for r in rows], dtype=float)
    return X, y


def moments(X: np.ndarray) -> dict:
    """联邦统计三元组 (n, sum, sum_sq)：仅聚合量，可安全上行供全局标准化。"""
    return {"n": int(len(X)), "sum": X.sum(0).tolist(), "sum_sq": (X ** 2).sum(0).tolist()}


def standardize(X: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    return (X - mean) / std


def split_train_test(X: np.ndarray, y: np.ndarray, test_ratio: float = 0.25,
                     seed: int = 42) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(y))
    cut = int(len(y) * (1 - test_ratio))
    tr, te = idx[:cut], idx[cut:]
    return X[tr], y[tr], X[te], y[te]


def train_round(X_local: np.ndarray, y_local: np.ndarray, global_coef: np.ndarray,
                epochs: int = 3) -> np.ndarray:
    """一轮本地训练：从全局参数出发 fit，返回本地新参数（完整状态）。"""
    m = LogisticModel(X_local.shape[1])
    m.load(global_coef)
    m.fit_local(X_local, y_local, epochs=epochs)
    return m.state()


def _rankdata(scores: np.ndarray) -> np.ndarray:
    """平均秩（并列值取平均），Mann-Whitney U 的 AUC 基础。"""
    order = np.argsort(scores, kind="mergesort")
    sorted_scores = scores[order]
    ranks = np.empty(len(scores), dtype=float)
    i = 0
    while i < len(scores):
        j = i
        while j + 1 < len(scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def auc_score(y_true: np.ndarray, scores: np.ndarray) -> float:
    """纯 numpy AUC（U 统计量口径，与 sklearn roc_auc_score 数值一致）。

    Agent 刻意不依赖 sklearn：节点容器最小化（numpy + cryptography 即可运行）。
    """
    pos = y_true == 1
    n_pos, n_neg = int(pos.sum()), int(len(y_true) - pos.sum())
    if n_pos == 0 or n_neg == 0:
        raise ValueError("验证集正/负样本缺失，无法计算 AUC")
    ranks = _rankdata(scores)
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def local_auc(X_test: np.ndarray, y_test: np.ndarray, coef: np.ndarray) -> float:
    """本地验证集 AUC（聚合指标，可安全上行；原始数据不出节点）。"""
    m = LogisticModel(X_test.shape[1])
    m.load(coef)
    return auc_score(y_test, m.predict_proba(X_test))
