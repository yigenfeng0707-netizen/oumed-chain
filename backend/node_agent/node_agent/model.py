"""节点本地模型：逻辑回归 + 客户端侧差分隐私。

算法与平台 engine.py 完全同构（特征数 13+截距、L2=1e-3、lr=0.01、
批量梯度 epochs 轮），保证节点与 coordinator 的数值语义一致；
独立实现（不 import 平台包）以保持 Agent 单容器自包含。
"""

from __future__ import annotations

import numpy as np

L2 = 1e-3
LR = 0.01


def add_intercept(X: np.ndarray) -> np.ndarray:
    return np.hstack([X, np.ones((X.shape[0], 1))])


class LogisticModel:
    def __init__(self, n_features: int, l2: float = L2, lr: float = LR):
        self.coef = np.zeros(n_features + 1)  # 末位为截距
        self.l2, self.lr = l2, lr

    def load(self, coef: np.ndarray):
        self.coef = coef.copy()

    def state(self) -> np.ndarray:
        return self.coef.copy()

    def fit_local(self, X: np.ndarray, y: np.ndarray, epochs: int = 3):
        """批量梯度下降若干轮（与平台 engine.LogisticModel.fit_local 同步）。"""
        Xb = add_intercept(X)
        n = len(y)
        for _ in range(epochs):
            p = 1.0 / (1.0 + np.exp(-Xb @ self.coef))
            grad = Xb.T @ (p - y) / n
            grad[:-1] += self.l2 * self.coef[:-1]
            self.coef -= self.lr * grad
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        z = add_intercept(X) @ self.coef
        return 1.0 / (1.0 + np.exp(-z))


def dp_sanitize(update: np.ndarray, clip_norm: float, sigma: float,
                rng: np.random.Generator) -> np.ndarray:
    """客户端侧差分隐私：更新裁剪 + 高斯噪声（与平台 engine.dp_sanitize 一致）。"""
    norm = np.linalg.norm(update)
    if norm > clip_norm:
        update = update * (clip_norm / norm)
    return update + rng.normal(0, sigma * clip_norm, size=update.shape)
