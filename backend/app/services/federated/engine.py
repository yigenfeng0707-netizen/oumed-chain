"""联邦学习核心引擎（瓯医数链底座 · 数据要素协作的真实实现）

三个模拟医院节点（三甲/县医院/社区卫生中心）在数据不出院的前提下
联邦训练心衰 30 天再入院风险模型，支持差分隐私（DP-FedAvg 风格：
裁剪 + 高斯噪声）。全部 CPU 计算，单任务秒级完成。
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import roc_auc_score

from app.services.federated.data_generator import FEATURES


class FedScaler:
    """全局标准化器：由各院 (n, sum, sum_sq) 联邦统计聚合而成，原始数据不出院。"""

    def fit(self, moments: list[tuple[int, np.ndarray, np.ndarray]]):
        n = sum(m[0] for m in moments)
        mean = sum(m[1] for m in moments) / n
        var = sum(m[2] for m in moments) / n - mean**2
        self.mean_, self.std_ = mean, np.sqrt(np.maximum(var, 1e-9))
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mean_) / self.std_


class LogisticModel:
    def __init__(self, n_features: int, l2: float = 1e-3, lr: float = 0.01):
        self.coef = np.zeros(n_features + 1)  # 末位为截距
        self.l2, self.lr = l2, lr

    @staticmethod
    def _add_intercept(X: np.ndarray) -> np.ndarray:
        return np.hstack([X, np.ones((X.shape[0], 1))])

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        z = self._add_intercept(X) @ self.coef
        return 1.0 / (1.0 + np.exp(-z))

    def fit_local(self, X: np.ndarray, y: np.ndarray, epochs: int = 3):
        Xb = self._add_intercept(X)
        n = len(y)
        for _ in range(epochs):
            p = 1.0 / (1.0 + np.exp(-Xb @ self.coef))
            grad = Xb.T @ (p - y) / n
            grad[:-1] += self.l2 * self.coef[:-1]
            self.coef -= self.lr * grad
        return self

    def state(self) -> np.ndarray:
        return self.coef.copy()

    def load(self, coef: np.ndarray):
        self.coef = coef.copy()


def fed_average(states: list[np.ndarray], weights: list[int]) -> np.ndarray:
    total = float(sum(weights))
    return sum(w * s for w, s in zip(weights, states)) / total


def dp_sanitize(update: np.ndarray, clip_norm: float, sigma: float,
                rng: np.random.Generator) -> np.ndarray:
    """客户端侧差分隐私：更新裁剪 + 高斯噪声注入。"""
    norm = np.linalg.norm(update)
    if norm > clip_norm:
        update = update * (clip_norm / norm)
    return update + rng.normal(0, sigma * clip_norm, size=update.shape)


def run_federated(clients: list[dict], X_test: np.ndarray, y_test: np.ndarray,
                  rounds: int = 12, local_epochs: int = 3,
                  clip_norm: float = 1.0, sigma: float = 0.0, seed: int = 7):
    """clients: [{"X": 已标准化特征, "y": 标签, "n": 样本数}, ...]

    返回 (每轮全局AUC历史, 最终AUC, 最终模型)。
    """
    rng = np.random.default_rng(seed)
    n_features = clients[0]["X"].shape[1]
    global_model = LogisticModel(n_features)

    history = [roc_auc_score(y_test, global_model.predict_proba(X_test))]

    for _ in range(rounds):
        states, weights = [], []
        for c in clients:
            m = LogisticModel(n_features)
            m.load(global_model.state())
            m.fit_local(c["X"], c["y"], epochs=local_epochs)
            update = m.state() - global_model.state()
            if sigma > 0:
                update = dp_sanitize(update, clip_norm, sigma, rng)
            states.append(global_model.state() + update)
            weights.append(c["n"])
        global_model.load(fed_average(states, weights))
        history.append(roc_auc_score(y_test, global_model.predict_proba(X_test)))

    return history, history[-1], global_model


def local_model_auc(X_local, y_local, X_test, y_test, epochs: int = 300) -> float:
    m = LogisticModel(X_local.shape[1], lr=0.3)
    Xb = np.hstack([X_local, np.ones((len(X_local), 1))])
    for _ in range(epochs):
        p = 1.0 / (1.0 + np.exp(-Xb @ m.coef))
        grad = Xb.T @ (p - y_local) / len(y_local)
        grad[:-1] += m.l2 * m.coef[:-1]
        m.coef -= m.lr * grad
    return roc_auc_score(y_test, m.predict_proba(X_test))
