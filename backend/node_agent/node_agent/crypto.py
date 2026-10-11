"""节点参数上行加密：AES-256-GCM 信封（与平台 federation_coordinator 同款线格式）。

- 当前线格式：`enc2:<key_id>:<b64(nonce)>:<b64(ct|tag)>`，key_id =
  sha256(key)[:8] 十六进制——信封自带密钥版本号，平台多版本密钥环按 key_id 寻钥；
  明文为 UTF-8 JSON；
- 旧格式 `enc1:<b64(nonce)>:<b64(ct|tag)>` 解密兼容（过渡期）；
- 密钥解析与平台一致：base64(44 字符) → hex(64 字符) → 口令 PBKDF2 派生；
- 密钥版本：默认自动推导 key_id；环境变量 NODE_KEY_ID 可显式指定（与平台
  FEDERATION_PARAM_KEYS 中该密钥的 key_id 不一致时平台会拒绝，便于发现配置漂移）；
- 双模兼容：密钥未配置时 seal 原样返回 JSON 明文（联调模式），
  open 同样兼容三种输入（生产模式平台会强制拒绝明文）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX_V2 = "enc2:"
_PREFIX_V1 = "enc1:"


def parse_key(raw: str) -> bytes:
    """解析 32 字节密钥（与平台 crypto._parse_key 同规则）。空串返回 b""。"""
    raw = (raw or "").strip()
    if not raw:
        return b""
    try:
        key = base64.b64decode(raw, validate=True)
        if len(key) == 32:
            return key
    except Exception:  # noqa: BLE001 —— 非 base64 继续尝试 hex
        pass
    try:
        key = bytes.fromhex(raw)
        if len(key) == 32:
            return key
    except ValueError:
        pass
    return hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), b"oumed-chain-data-key", 200_000)


def envelope_key_id(key: bytes) -> str:
    """密钥版本号：sha256(key)[:8]（平台/节点侧对同一密钥推导结果一致）。"""
    return hashlib.sha256(key).hexdigest()[:8]


def _key() -> bytes:
    """从环境变量取信封密钥；未配置返回 b""（明文兼容联调模式）。"""
    return parse_key(os.environ.get("FEDERATION_PARAM_KEY", ""))


def _key_id(key: bytes) -> str:
    return os.environ.get("NODE_KEY_ID", "").strip() or envelope_key_id(key)


def seal_envelope(obj: dict, key: bytes | None = None) -> str:
    """把 dict 封装为加密信封（enc2 格式）；未配置密钥时返回明文 JSON（联调兼容模式）。"""
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if key is None:
        key = _key()
    if not key:
        return raw.decode("utf-8")
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, raw, None)
    return (_PREFIX_V2 + _key_id(key) + ":" + base64.b64encode(nonce).decode("ascii")
            + ":" + base64.b64encode(ct).decode("ascii"))


def open_envelope(payload: str, key: bytes | None = None) -> dict:
    """解开信封为 dict；兼容 enc2/enc1 密文与明文 JSON。解密失败抛 ValueError。

    本节点只持有一把密钥：enc2 信封 key_id 与本节点密钥不匹配时直接报错
    （平台已轮换密钥而本节点未同步，提示更新 FEDERATION_PARAM_KEY）。
    """
    if key is None:
        key = _key()

    if payload.startswith(_PREFIX_V2):
        if not key:
            raise ValueError("收到加密信封但本节点未配置 FEDERATION_PARAM_KEY")
        body = payload[len(_PREFIX_V2):]
        if body.count(":") != 2:
            raise ValueError("enc2 信封格式非法（应为 enc2:key_id:nonce:ct）")
        kid, nonce_b64, ct_b64 = body.split(":", 2)
        if kid != envelope_key_id(key):
            raise ValueError(
                f"信封 key_id={kid} 与本节点密钥不匹配（平台已轮换密钥？请同步更新 "
                "FEDERATION_PARAM_KEY）")
        return _decrypt(nonce_b64, ct_b64, key)

    if payload.startswith(_PREFIX_V1):
        if not key:
            raise ValueError("收到加密信封但本节点未配置 FEDERATION_PARAM_KEY")
        _, nonce_b64, ct_b64 = payload.split(":", 2)
        return _decrypt(nonce_b64, ct_b64, key)

    return json.loads(payload)


def _decrypt(nonce_b64: str, ct_b64: str, key: bytes) -> dict:
    try:
        raw = AESGCM(key).decrypt(base64.b64decode(nonce_b64),
                                  base64.b64decode(ct_b64), None)
        return json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"信封解密失败（密钥不匹配或数据损坏）: {e}") from e
