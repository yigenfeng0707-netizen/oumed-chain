"""联邦 Coordinator 聚合服务（P1.1 医院侧节点真实部署的平台侧对端）。

职责边界（与进程内仿真 services/federated.py 区分）：
- 不持有任何医院原始数据，只处理节点上行参数信封与聚合量；
- 参数信封：AES-256-GCM，P1.2 起线格式 `enc2:<key_id>:<b64(nonce)>:<b64(ct|tag)>`，
  key_id = sha256(key)[:8] 十六进制——信封自带密钥版本号，支持多版本密钥环轮换；
  旧格式 `enc1:<b64(nonce)>:<b64(ct|tag)>` 保留解密兼容（过渡期）；
  密钥未配置 = 明文兼容联调模式；
- 密钥环：FEDERATION_PARAM_KEYS（"key_id:key" 逗号分隔，最后一个为活跃写入密钥），
  留空回退单密钥 FEDERATION_PARAM_KEY（自动计算 key_id）。密钥泄露轮换后，旧密钥
  移出密钥环 → 其 key_id 不在环中，历史信封永久不可解密（P1.2 验收语义）；
- 聚合算法与 services/federated/engine.py 完全同构（FedScaler + FedAvg），
  保证"真实部署"与"演示仿真"数值语义一致。
"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings
from app.crypto import parse_key
from app.services.federated.engine import FedScaler, fed_average

# 信封线格式前缀：enc2 = 当前（带 key_id），enc1 = 旧版过渡兼容（不带 key_id）
ENVELOPE_PREFIX_V2 = "enc2:"
ENVELOPE_PREFIX = "enc1:"


def envelope_key_id(key: bytes) -> str:
    """密钥版本号：sha256(key)[:8] 十六进制（同一密钥在平台/节点侧推导结果一致）。"""
    return hashlib.sha256(key).hexdigest()[:8]


def keyring() -> dict[str, bytes]:
    """解析联邦参数密钥环：{key_id: key_bytes}，插入序即优先级（最后一个为活跃写入密钥）。

    - FEDERATION_PARAM_KEYS 已配置：按 "key_id:key" 逗号分隔解析，格式非法直接报错
      （静默跳过会导致轮换后莫名解密失败，生产宁可启动即暴露问题）；
    - 未配置：回退单密钥 FEDERATION_PARAM_KEY，自动计算 key_id；
    - 均未配置：空环 = 明文兼容联调模式。
    """
    raw = (settings.FEDERATION_PARAM_KEYS or "").strip()
    if raw:
        ring: dict[str, bytes] = {}
        for part in raw.split(","):
            part = part.strip()
            if ":" not in part:
                raise ValueError(
                    f"FEDERATION_PARAM_KEYS 条目格式非法（应为 key_id:key）: {part[:8]}…")
            kid, k = part.split(":", 1)
            key = parse_key(k.strip())
            if not key:
                raise ValueError("FEDERATION_PARAM_KEYS 条目密钥为空")
            ring[kid.strip()] = key
        if not ring:
            raise ValueError("FEDERATION_PARAM_KEYS 配置为空环")
        return ring
    single = parse_key(settings.FEDERATION_PARAM_KEY)
    return {envelope_key_id(single): single} if single else {}


def active_key() -> tuple[str, bytes]:
    """当前活跃写入密钥 (key_id, key)；未配置返回 ("", b"")（明文兼容联调模式）。"""
    ring = keyring()
    if not ring:
        return "", b""
    kid = next(reversed(ring))
    return kid, ring[kid]


def federation_key() -> bytes:
    """联邦参数信封活跃密钥（未配置返回 b"" = 明文兼容联调模式）。"""
    return active_key()[1]


def seal_envelope(obj: dict, key: bytes | None = None) -> str:
    """封装参数信封（平台侧集成测试/双向互通验证用；节点侧同算法独立实现）。

    已配置密钥 → `enc2:<key_id>:<b64(nonce)>:<b64(ct|tag)>`；未配置 → 明文 JSON。
    """
    if key is None:
        _, key = active_key()
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if not key:
        return raw.decode("utf-8")
    import os

    kid = envelope_key_id(key)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, raw, None)
    return (ENVELOPE_PREFIX_V2 + kid + ":" + base64.b64encode(nonce).decode("ascii")
            + ":" + base64.b64encode(ct).decode("ascii"))


def open_envelope(payload: str, key: bytes | None = None) -> dict:
    """解开参数信封为 dict。

    双模 + 多版本规则：
    - enc2 信封：按信封内 key_id 在密钥环寻钥；key_id 不在环中（密钥已轮换销毁）
      → 直接拒绝，历史参数永久不可解密；显式传 key 时仅该密钥可解；
    - enc1 旧信封（无 key_id）：过渡兼容，逐密钥尝试解密；
    - 明文 JSON：平台已配置任何密钥时一律拒绝（生产强制加密）。
    """
    ring = {envelope_key_id(key): key} if key else keyring()

    if payload.startswith(ENVELOPE_PREFIX_V2):
        if not ring:
            raise ValueError("收到加密信封但平台未配置 FEDERATION_PARAM_KEY(S)")
        body = payload[len(ENVELOPE_PREFIX_V2):]
        if body.count(":") != 2:
            raise ValueError("enc2 信封格式非法（应为 enc2:key_id:nonce:ct）")
        kid, nonce_b64, ct_b64 = body.split(":", 2)
        k = ring.get(kid)
        if k is None:
            raise ValueError(
                f"信封 key_id={kid} 不在平台密钥环中（密钥已轮换且旧密钥已销毁，"
                "历史参数永久不可解密）")
        try:
            raw = AESGCM(k).decrypt(base64.b64decode(nonce_b64),
                                    base64.b64decode(ct_b64), None)
            return json.loads(raw.decode("utf-8"))
        except Exception as e:  # noqa: BLE001 —— 解密失败不透传密文
            raise ValueError(f"参数信封解密失败（密钥不匹配或数据损坏）: {e}") from e

    if payload.startswith(ENVELOPE_PREFIX):
        # enc1 旧格式过渡兼容：无 key_id，逐密钥尝试（密文自带认证标签，错钥必失败）
        if not ring:
            raise ValueError("收到加密信封但平台未配置 FEDERATION_PARAM_KEY(S)")
        for k in ring.values():
            try:
                _, nonce_b64, ct_b64 = payload.split(":", 2)
                raw = AESGCM(k).decrypt(base64.b64decode(nonce_b64),
                                        base64.b64decode(ct_b64), None)
                return json.loads(raw.decode("utf-8"))
            except Exception:  # noqa: BLE001 —— 换下一把密钥
                continue
        raise ValueError("enc1 旧格式信封解密失败（密钥不在当前密钥环中）")

    if ring:
        raise ValueError("平台已配置联邦密钥，参数上行必须为加密信封（enc2:…）")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        raise ValueError(f"参数信封不是合法 JSON: {e}") from e


def aggregate_moments(entries: list[dict]) -> tuple[list[float], list[float]]:
    """聚合各节点联邦统计 (n, sum, sum_sq) → 全局标准化器 (mean, std)。

    复用 engine.FedScaler（与进程内仿真同构）；返回 JSON 友好 list。
    """
    import numpy as np

    moments = [(e["n"], np.asarray(e["sum"], float), np.asarray(e["sum_sq"], float))
               for e in entries]
    scaler = FedScaler().fit(moments)
    return scaler.mean_.tolist(), scaler.std_.tolist()


def aggregate_coef(states: list[list[float]], weights: list[int]) -> list[float]:
    """按节点样本量加权平均模型参数（FedAvg，复用 engine.fed_average）。"""
    import numpy as np

    return fed_average([np.asarray(s, dtype=float) for s in states],
                       [int(w) for w in weights]).tolist()


def weighted_auc(entries: list[dict]) -> float:
    """按节点验证集样本量加权平均 AUC（最终联邦结果指标）。"""
    total = sum(e["n"] for e in entries)
    if total <= 0:
        raise ValueError("节点样本总量为零")
    return sum(e["n"] * e["local_auc"] for e in entries) / total
